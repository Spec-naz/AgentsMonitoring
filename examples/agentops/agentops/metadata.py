"""What operational records may contain, a safe metadata export, and a retention plan (LC-07).

Rules are allowlists: an unknown field, a nested object or a free-text value is refused or dropped, never
passed along. This is not recognition of sensitive data. It works because free text is left out, and a field
that is allowed can still contain whatever its format permits, such as an identifier someone chose badly.
It is rule-based and local: no model, network or cloud export. Nothing here runs on a schedule.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time
from urllib.parse import quote
import uuid

from . import __version__, provider_signals
from .postcheck import _denied
from .regression import canonical, digest

EXPORT_SCHEMA = 'agentops-metadata-export-v1'
PLAN_SCHEMA = 'agentops-retention-plan-v1'
ACTOR = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
TOKEN = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
HEX64 = re.compile(r'[a-f0-9]{64}\Z')
SESSION_REF = re.compile(r'[a-f0-9]{32}\Z')
# A local path only. A query, credentials in a URL or an assignment are withheld, not cleaned. This is a guardrail.
LOCAL_PATH = re.compile(r'/[^\s?=@:]{1,499}\Z')
STATES = ('queued', 'running', 'blocked', 'handoff_pending', 'review', 'done', 'cancelled')
PHASES = ('work', 'review')
OUTCOMES = ('ok', 'failed', 'unknown')
VERDICTS = ('pass', 'fail', 'unverified')
EVIDENCE = ('declared', 'observed')
RUN_SOURCES = ('agentops', 'provider-hook', 'operator')
DEFAULT_SIGNAL_DIR = provider_signals.DEFAULT_STATE_DIR
# Audit is never sampled: the export carries every event, and there is no sampling code path.
SAMPLING = {'critical_audit': 'never', 'auxiliary': 'none implemented'}
# Must not be shorter than the 7 days after which a session already reads as stale.
SIGNAL_DAYS = (7, 365, 14)
NEVER_EXPIRES = ('ledger tasks, events, commands and costs', 'result check and regression reports', 'backups and rehearsals',
                 'deployment records')


def _is_uuid(value):
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _optional(check):
    return lambda value: value is None or check(value)


def _actor(value):
    return isinstance(value, str) and ACTOR.fullmatch(value) is not None


def _token(value):
    return isinstance(value, str) and TOKEN.fullmatch(value) is not None


def _one_of(*options):
    return lambda value: isinstance(value, str) and value in options


def _count(value):
    return type(value) is int and value >= 0


def _flag(value):
    return type(value) is bool


def _reference(value):
    return isinstance(value, str) and LOCAL_PATH.fullmatch(value) is not None and not _denied(value.split('/')[1:])


def _sha(value):
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


# record -> field -> (check, purpose). Purposes are the documentation; docs/metadata-policy.md must list each field.
RECORDS = {
    'export_task': {
        'id': (_is_uuid, 'Link to the task and its events; a random identifier.'),
        'owner': (_actor, 'Who holds the task; a declared label.'),
        'state': (_one_of(*STATES), 'Declared workflow state.'),
        'phase': (_one_of(*PHASES), 'Work or review.'),
        'target': (_optional(_actor), 'Who a handover waits for.'),
        'purpose': (_optional(_one_of(*PHASES)), 'Why it was handed over.'),
        'producer': (_optional(_actor), 'Author, for the independent review rule.'),
        'created': (_time, 'Timing.'),
        'updated': (_time, 'Timing.'),
        'progress': (_optional(_time), 'Last declared progress; null means none was recorded.'),
        'version': (_count, 'Revision, to detect changes.'),
        'has_artifact': (_flag, 'Whether a result is linked, without saying where.'),
        'artifact': (_reference, 'Only with --include-references: local path of the result.'),
    },
    'export_event': {
        'seq': (_count, 'Complete, gap-checkable audit order.'),
        'task_id': (_is_uuid, 'Link to the task.'),
        'at': (_time, 'Timing.'),
        'actor': (_actor, 'Who recorded it; a declared label.'),
        'action': (_token, 'What kind of step it was.'),
        'from_state': (_one_of(*STATES), 'State before.'),
        'to_state': (_one_of(*STATES), 'State after.'),
        'target': (_actor, 'Addressee of a handover.'),
        'purpose': (_one_of(*PHASES), 'Purpose of a handover.'),
        'version': (_count, 'Task revision after the step.'),
        'verdict': (_one_of(*VERDICTS), 'Result check outcome.'),
        'report_sha256': (_sha, 'Identifies the report without its content.'),
        'report': (_reference, 'Only with --include-references: local path of the report.'),
        'details_unreadable': (_flag, 'The stored details could not be read; nothing was guessed.'),
    },
    'export_session': {
        'provider': (_one_of(*provider_signals.PROVIDERS), 'Which CLI.'),
        'session_ref': (lambda value: isinstance(value, str) and SESSION_REF.fullmatch(value) is not None,
                        'Stable reference, a hash of the session id.'),
        'actor': (_optional(_actor), 'Declared agent; not authenticated.'),
        'turn': (_one_of(*provider_signals.TURNS), 'Turn state, not task progress.'),
        'last_event': (_optional(_one_of(*(event for events in provider_signals.PROVIDERS.values() for event in events))), 'Last lifecycle event.'),
        'last_event_at': (_optional(_time), 'Timing.'),
        'events': (_count, 'Number of hook events seen.'),
    },
    'export_cost': {
        'currency': (lambda value: isinstance(value, str) and re.fullmatch('[A-Z]{3}', value) is not None, 'Currency of recorded amounts.'),
        'kind': (_one_of('actual', 'estimate'), 'Whether it was measured.'),
        'micros': (_count, 'Sum of recorded entries only.'),
        'records': (_count, 'How many entries; everything else is unknown, not zero.'),
    },
    # Contract for the future LC-02 timeline. Nothing writes these yet.
    'run_event': {
        'task_id': (_is_uuid, 'The declared task. A task is not a run.'),
        'run_id': (_is_uuid, 'One concrete execution.'),
        'parent_id': (_optional(_is_uuid), 'The run that started this one; null for a root run.'),
        'at': (_time, 'Timing.'),
        'source': (_one_of(*RUN_SOURCES), 'What wrote the record.'),
        'evidence': (_one_of(*EVIDENCE), 'Declared by an actor, or observed by a mechanism.'),
        'actor': (_actor, 'Declared label; not authenticated.'),
        'action': (_token, 'Kind of step.'),
        'outcome': (_one_of(*OUTCOMES), 'Result; unknown stays unknown.'),
        'error_code': (_optional(_token), 'A code, never error text.'),
        'usage_tokens': (_optional(_count), 'Null is unknown usage, never zero.'),
    },
}
RUN_EVENT_REQUIRED = ('task_id', 'run_id', 'parent_id', 'at', 'source', 'evidence', 'actor', 'action', 'outcome', 'error_code', 'usage_tokens')


def validate_record(name, record):
    """Strict: unknown fields and invalid values are refused. The message never repeats a value."""
    fields = RECORDS[name]
    if not isinstance(record, dict):
        raise ValueError('Record must be an object')
    for key, value in record.items():
        if key not in fields:
            raise ValueError('Field not allowed in %s' % name)
        if isinstance(value, (dict, list)) or not fields[key][0](value):
            raise ValueError('Invalid value for %s.%s' % (name, key))
    if name == 'run_event' and any(key not in record for key in RUN_EVENT_REQUIRED):
        raise ValueError('run_event must state every field; unknown is an explicit null')
    return record


def _pick(name, source, keys):
    """Copy only allowed values. A stored value that does not fit is dropped and counted, not repaired."""
    record, dropped = {}, 0
    for key in keys:
        if key in source and source[key] is not None:
            value = source[key]
            if not isinstance(value, (dict, list)) and RECORDS[name][key][0](value):
                record[key] = value
            else:
                dropped += 1
    return record, dropped


def _connect(path):
    resolved = Path(path).expanduser().absolute()
    if not resolved.is_file():
        raise ValueError('Ledger cannot be read')
    return sqlite3.connect('file:%s?mode=ro' % quote(str(resolved)), uri=True, timeout=5)


def _task(row, include_references):
    record, dropped = _pick('export_task', row, ('id', 'owner', 'state', 'phase', 'target', 'purpose', 'producer',
                                                  'created', 'updated', 'progress', 'version'))
    for key in ('target', 'purpose', 'producer', 'progress'):
        record.setdefault(key, None)
    record['has_artifact'] = row['artifact'] is not None
    withheld = 0
    if include_references and row['artifact'] is not None:
        if _reference(row['artifact']):
            record['artifact'] = row['artifact']
        else:
            withheld = 1
    return validate_record('export_task', record), dropped, withheld


def _event(row, include_references):
    base, dropped = _pick('export_event', row, ('seq', 'task_id', 'at', 'actor', 'action'))
    try:
        details = json.loads(row['details'])
        if not isinstance(details, dict):
            raise ValueError
    except ValueError:
        return validate_record('export_event', {**base, 'details_unreadable': True}), dropped, 0
    changes = details.get('changes') if isinstance(details.get('changes'), dict) else {}
    flat = {'from_state': details.get('from_state'), 'to_state': details.get('to_state'), 'target': details.get('target'),
            'purpose': details.get('purpose'), 'version': changes.get('version'), 'verdict': details.get('verdict'),
            'report_sha256': details.get('report_sha256')}
    picked, more = _pick('export_event', flat, flat)
    record, withheld = {**base, **picked}, 0
    if include_references and details.get('report') is not None:
        if _reference(details['report']):
            record['report'] = details['report']
        else:
            withheld = 1
    return validate_record('export_event', record), dropped + more, withheld


def _session(snapshot):
    source = {**snapshot, 'session_ref': hashlib.sha256(
        ('%s\0%s' % (snapshot['provider'], snapshot['session_id'])).encode()).hexdigest()[:32]}
    record, dropped = _pick('export_session', source, ('provider', 'session_ref', 'actor', 'turn', 'last_event', 'last_event_at', 'events'))
    return validate_record('export_session', record), dropped


def build_export(ledger_path, signals_dir=DEFAULT_SIGNAL_DIR, include_references=False, now=None):
    """Metadata only. Titles, notes, cost sources, session ids, prompts and error text are never read into it."""
    connection = _connect(ledger_path)
    connection.row_factory = sqlite3.Row
    try:
        tasks = [dict(row) for row in connection.execute(
            'SELECT id,owner,state,phase,target,purpose,producer,created,updated,progress,version,artifact FROM tasks ORDER BY created,id')]
        events = [dict(row) for row in connection.execute('SELECT seq,task_id,at,actor,action,details FROM events ORDER BY seq')]
        costs = [dict(row) for row in connection.execute(
            'SELECT currency,kind,SUM(micros) AS micros,COUNT(*) AS records FROM costs GROUP BY currency,kind')]
    except sqlite3.Error:
        raise ValueError('Ledger cannot be read') from None
    finally:
        connection.close()
    dropped = withheld = 0
    out_tasks, out_events = [], []
    for row in tasks:
        record, lost, held = _task(row, include_references)
        out_tasks.append(record)
        dropped += lost
        withheld += held
    for row in events:
        if not _count(row['seq']):
            raise ValueError('Ledger cannot be read')
        record, lost, held = _event(row, include_references)
        out_events.append(record)
        dropped += lost
        withheld += held
    if len(out_events) != len(events):
        raise ValueError('Audit events must not be lost from an export')
    sessions = []
    for snapshot in provider_signals.read_sessions(signals_dir):
        record, lost = _session(snapshot)
        sessions.append(record)
        dropped += lost
    out_costs = [validate_record('export_cost', {key: row[key] for key in RECORDS['export_cost']}) for row in costs]
    return {'schema': EXPORT_SCHEMA, 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
            'agentops_version': __version__, 'references_included': include_references, 'sampling': SAMPLING,
            'counts': {'tasks': len(out_tasks), 'events': len(out_events), 'sessions': len(sessions),
                       'dropped_values': dropped, 'withheld_references': withheld},
            'tasks': out_tasks, 'events': out_events, 'sessions': sessions, 'recorded_costs': out_costs,
            'not_included': ['task titles', 'notes and block reasons', 'cost sources', 'session ids', 'prompts and responses',
                             'terminal content', 'credentials', 'error text'],
            'limits': ['Allowlist of formats, not recognition of sensitive data.',
                       'Costs and usage not recorded here are unknown, not zero.',
                       'Task states are declared, not observed execution.']}


def retention_policy(config):
    """Configured limits, strictly. The default is today's prune default of 14 days."""
    raw = config.get('retention', {}) if isinstance(config, dict) else None
    if not isinstance(raw, dict) or set(raw) - {'provider_signal_days'}:
        raise ValueError('Invalid retention settings')
    low, high, default = SIGNAL_DAYS
    days = raw.get('provider_signal_days', default)
    if type(days) is not int or not low <= days <= high:
        raise ValueError('provider_signal_days must be a whole number from %d to %d' % (low, high))
    return {'provider_signal_days': days}


def _plan_hash(signals_dir, days, expired):
    return digest({'dir': os.path.realpath(signals_dir), 'days': days, 'expired': expired})


def _ledger_facts(ledger_path):
    """Sizes and counts to watch growth. Never candidates for deletion."""
    try:
        connection = _connect(ledger_path)
    except (ValueError, sqlite3.Error):
        return {'readable': False}
    try:
        counts = {name: connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in ('tasks', 'events', 'commands', 'costs')}
        return {'readable': True, 'bytes': Path(ledger_path).expanduser().stat().st_size, **counts}
    except (sqlite3.Error, OSError):
        return {'readable': False}
    finally:
        connection.close()


def plan(config, signals_dir=DEFAULT_SIGNAL_DIR, ledger_path=None, now=None):
    """Read-only. Deletes nothing and is the default of the CLI."""
    now = time.time() if now is None else now
    policy = retention_policy(config)
    review = provider_signals.retention_review(signals_dir, now, policy['provider_signal_days'] * 86400)
    return {'schema': PLAN_SCHEMA, 'mode': 'plan', 'deletes_nothing': True, 'generated_at': now, 'policy': policy,
            'auxiliary': {'provider_signals': review}, 'plan_sha256': _plan_hash(signals_dir, policy['provider_signal_days'], review['expired']),
            'never_expires': list(NEVER_EXPIRES), 'ledger': _ledger_facts(ledger_path) if ledger_path else {'readable': False},
            'sampling': SAMPLING}


def _reserve_receipt(path, record):
    """Create the receipt exclusively before anything is removed. A path it cannot use stops everything here."""
    path = Path(path).expanduser().absolute()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(canonical(record) + b'\n')
            target.flush()
            os.fsync(target.fileno())
    except OSError:
        raise ValueError('Receipt could not be prepared; nothing was removed') from None
    return path


def _finish_receipt(path, record):
    """Replace the started record in one step. If this fails, the started record still lists what was planned."""
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(canonical(record) + b'\n')
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    except OSError:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
        raise ValueError('Receipt could not be finished; its started record lists what was planned') from None


def apply_plan(config, signals_dir, plan_sha256, receipt, now=None, production_dir=DEFAULT_SIGNAL_DIR):
    """Remove exactly what a reviewed plan listed, from an explicit folder. Production is refused in this version.

    The receipt exists before the first removal, so a failure at any later point still leaves a trace.
    """
    now = time.time() if now is None else now
    if os.path.realpath(signals_dir) == os.path.realpath(production_dir):
        raise ValueError('Retention apply refuses the production folder; that needs a separate decision')
    policy = retention_policy(config)
    max_age = policy['provider_signal_days'] * 86400
    review = provider_signals.retention_review(signals_dir, now, max_age)
    if _plan_hash(signals_dir, policy['provider_signal_days'], review['expired']) != plan_sha256:
        raise ValueError('The plan no longer matches; review a new plan')
    base = {'schema': PLAN_SCHEMA, 'generated_at': now, 'policy': policy, 'plan_sha256': plan_sha256, 'never_expires': list(NEVER_EXPIRES)}
    path = _reserve_receipt(receipt, {**base, 'mode': 'started', 'planned': review['expired']})
    removed, skipped = provider_signals.remove_expired(signals_dir, review['expired'], now, max_age)
    result = {**base, 'mode': 'applied', 'removed': removed, 'skipped': skipped, 'kept_unknown': review['unknown']}
    _finish_receipt(path, result)
    return result
