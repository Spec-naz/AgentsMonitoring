"""One read-only timeline of a task: who did what, on what evidence, and where a problem is on record (LC-02).

It joins the ledger with run events and shows what each entry rests on. A ledger entry is declared by an actor. A run is a
run_event, which says itself whether it was declared or observed. Being assigned, or a process being alive, is never
reported as work done. A task from before run events has no runs, and none are invented for it.

No titles, notes, prompts, responses or error text are shown. Plain local paths are references to copy; files are not served.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
from urllib.parse import quote

from . import metadata, provider_signals

SCHEMA = 'agentops-timeline-v1'
STALE_REASONS = ('signal_stale', 'waiting_signal_stale', 'running_without_end_signal', 'clock_skew')
UNASSIGNED_REASONS = ('no_bound_session', 'no_signal', 'no_turn_signal', 'invalid_binding')
MAX_REPORT_BYTES = 64 * 1024 * 1024


def _connect(path):
    resolved = Path(path).expanduser().absolute()
    if not resolved.is_file():
        raise ValueError('Ledger cannot be read')
    return sqlite3.connect('file:%s?mode=ro' % quote(str(resolved)), uri=True, timeout=5)


def _read(path, task_id):
    try:
        connection = _connect(path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN')
            task = connection.execute('SELECT id,owner,state,phase,target,purpose,producer,created,updated,version,artifact FROM tasks WHERE id=?', (task_id,)).fetchone()
            if task is None:
                raise ValueError('Task not found')
            events = [dict(row) for row in connection.execute('SELECT seq,task_id,at,actor,action,details FROM events WHERE task_id=? ORDER BY seq', (task_id,))]
            has_runs = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_events'").fetchone() is not None
            runs = [dict(row) for row in connection.execute('SELECT * FROM run_events WHERE task_id=? ORDER BY seq', (task_id,))] if has_runs else []
            elsewhere = {row[0] for row in connection.execute('SELECT DISTINCT run_id FROM run_events WHERE task_id!=?', (task_id,))} if has_runs else set()
            return dict(task), events, runs, has_runs, elsewhere
        finally:
            connection.close()
    except sqlite3.Error:
        raise ValueError('Ledger cannot be read') from None


def _report_check(path, expected):
    """Does the linked report still have the recorded hash? Evidence about that file only, not about who made it."""
    # Where it really leads counts too: a folder alias must not reach an excluded place. This guards against mistakes.
    if not metadata._reference(path) or not metadata._reference(os.path.realpath(path)):
        return 'not_checked'
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return 'missing'
    except OSError:
        return 'not_checked'
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_REPORT_BYTES:
            return 'not_checked'
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    except OSError:
        return 'not_checked'
    finally:
        os.close(descriptor)
    return 'matches' if digest.hexdigest() == expected else 'differs'


def _ledger_entry(event, problems, producer, reviewed):
    record, _, _ = metadata._event(event, True)
    entry = {'kind': 'ledger', 'source': 'agentops', 'evidence': 'declared', **{key: value for key, value in record.items() if key != 'task_id'}}
    if record.get('details_unreadable'):
        problems.append({'code': 'audit_details_unreadable', 'seq': event['seq']})
        return entry
    details = json.loads(event['details'])
    artifact = details.get('artifact')
    if artifact is not None:
        if metadata._reference(artifact):
            entry['artifact'] = artifact
        else:
            entry['reference_withheld'] = True
    action = record.get('action')
    if action == 'postcheck':
        check = _report_check(details.get('report'), details.get('report_sha256'))
        entry['report_check'] = check
        if record.get('verdict') != 'pass':
            problems.append({'code': 'result_check_' + str(record.get('verdict')), 'seq': event['seq']})
        if check in ('missing', 'differs'):
            problems.append({'code': 'report_' + check, 'seq': event['seq']})
    # A return needs an earlier review. The first assignment to the later author looks the same and is not one.
    elif action == 'offer' and reviewed and details.get('purpose') == 'work' and producer and details.get('target') == producer and event['actor'] != producer:
        entry['is_return'] = True
        problems.append({'code': 'returned_after_review', 'seq': event['seq'], 'by': record.get('actor')})
    elif action == 'block':
        problems.append({'code': 'declared_blocked', 'seq': event['seq']})
    elif action == 'withdraw':
        problems.append({'code': 'handover_withdrawn', 'seq': event['seq']})
    return entry


def _run_view(rows, problems, elsewhere=()):
    """One line per run. The last event states its outcome, and unknown stays unknown.

    Records that break the identity rules can exist from before the writer checked them or from direct edits. They
    are shown as invalid, never as a coherent relation. `elsewhere` holds run ids also recorded under another task.
    """
    grouped = {}
    for row in rows:
        grouped.setdefault(row['run_id'], []).append(row)
    views = []
    for run_id, events in grouped.items():
        last = events[-1]
        # Across a run's events the last stated value counts: a later null means no longer known.
        parents = {row['parent_id'] for row in events}
        parent = events[0]['parent_id'] if len(parents) == 1 else None
        usage = last['usage_tokens']
        if len(parents) > 1:
            relation = 'invalid'
            problems.append({'code': 'run_parent_changed', 'run_id': run_id})
        elif parent == run_id:
            relation, parent = 'invalid', None
            problems.append({'code': 'run_parent_is_itself', 'run_id': run_id})
        else:
            relation = 'root' if parent is None else 'child' if parent in grouped else 'parent_in_other_task' if parent in elsewhere else 'parent_not_recorded'
        if run_id in elsewhere:
            problems.append({'code': 'run_id_in_other_task', 'run_id': run_id})
        view = {'run_id': run_id, 'parent_id': parent, 'relation': relation, 'actor': last['actor'], 'source': last['source'],
                'evidence': 'observed' if all(row['evidence'] == 'observed' for row in events) else 'declared',
                'first_at': events[0]['at'], 'last_at': last['at'], 'events': len(events), 'outcome': last['outcome'],
                'error_code': last['error_code'], 'usage_tokens': usage}
        views.append(view)
        if relation == 'parent_not_recorded':
            problems.append({'code': 'parent_run_not_recorded', 'run_id': run_id})
        if last['outcome'] == 'unknown':
            problems.append({'code': 'run_outcome_unknown', 'run_id': run_id, 'repeat_offered': False})
        elif last['outcome'] == 'failed':
            problems.append({'code': 'run_failed', 'run_id': run_id, 'error_code': last['error_code'], 'repeat_offered': False})
    return views


def _usage(runs):
    known = [run['usage_tokens'] for run in runs if run['usage_tokens'] is not None]
    return {'known_tokens': sum(known) if known else None, 'runs_with_known_usage': len(known), 'runs_with_unknown_usage': len(runs) - len(known),
            'note': 'Sum of recorded values only. Unknown is not zero.'}


def _signal(actor, config, now):
    """The actor's provider session, if one is bound. It is about a turn of an actor, never about this task."""
    base = {'actor': actor, 'about': 'actor_turn_not_task', 'proves_task_progress': False}
    bindings = config.get('provider_bindings') if isinstance(config, dict) else None
    binding = bindings.get(actor) if isinstance(bindings, dict) else None
    if not isinstance(binding, dict):
        return {**base, 'status': 'not_assigned'}
    provider, session = binding.get('provider'), binding.get('session_id')
    max_age = binding.get('max_age_seconds', 900)
    if provider not in provider_signals.PROVIDERS or not isinstance(session, str) or type(max_age) is not int:
        return {**base, 'status': 'not_assigned'}
    state_dir = binding.get('state_dir', provider_signals.DEFAULT_STATE_DIR)
    seen = provider_signals.agent_signal(actor, provider, state_dir=state_dir, now=now, session_id=session,
                                         limits={'running_max_age': max_age, 'waiting_max_age': max_age, 'idle_max_age': max_age})
    reason = seen.get('reason')
    status = 'stale' if reason in STALE_REASONS else 'not_assigned' if reason in UNASSIGNED_REASONS else 'ambiguous' if reason == 'ambiguous_binding' else 'current'
    return {**base, 'status': status, 'state': seen.get('state'), 'reason': reason, 'last_event': seen.get('last_event'),
            'last_event_at': seen.get('last_event_at'), 'evidence': 'observed' if status == 'current' else None}


def _first(entries, action):
    for entry in entries:
        if entry.get('action') == action:
            return {'at': entry.get('at'), 'actor': entry.get('actor'), 'seq': entry.get('seq'), 'evidence': entry['evidence']}
    return None


def build(ledger_path, task_id, config=None, now=None):
    task, events, rows, has_runs, elsewhere = _read(ledger_path, task_id)
    problems = []
    entries, reviewed = [], False
    for event in events:
        entry = _ledger_entry(event, problems, task['producer'], reviewed)
        entries.append(entry)
        reviewed = reviewed or (entry.get('action') == 'offer' and entry.get('purpose') == 'review')
    runs = _run_view(rows, problems, elsewhere)
    for row in rows:
        entries.append({'kind': 'run', 'run_seq': row['seq'], 'run_id': row['run_id'], 'parent_id': row['parent_id'], 'at': row['at'], 'source': row['source'],
                        'evidence': row['evidence'], 'actor': row['actor'], 'action': row['action'], 'outcome': row['outcome'],
                        'error_code': row['error_code'], 'usage_tokens': row['usage_tokens']})
    entries.sort(key=lambda entry: (entry.get('at', 0), 0 if entry['kind'] == 'ledger' else 1, entry.get('seq', entry.get('run_seq', 0))))
    closed = task['state'] in ('done', 'cancelled')
    observed = [run for run in runs if run['evidence'] == 'observed']
    ledger_only = [entry for entry in entries if entry['kind'] == 'ledger']
    returns = [entry for entry in ledger_only if entry.get('is_return')]
    artifact = task['artifact']
    summary = {'id': task['id'], 'owner': task['owner'], 'state': task['state'], 'phase': task['phase'], 'target': task['target'],
               'purpose': task['purpose'], 'producer': task['producer'], 'version': task['version'], 'created': task['created'], 'updated': task['updated']}
    if artifact is not None:
        summary['artifact' if metadata._reference(artifact) else 'reference_withheld'] = artifact if metadata._reference(artifact) else True
    return {'schema': SCHEMA, 'task': summary, 'belongs_to': 'history' if closed else 'active',
            'milestones': {'created': _first(ledger_only, 'create'), 'first_start': _first(ledger_only, 'start'),
                           'first_handover': _first(ledger_only, 'offer'), 'returns': len(returns), 'done': _first(ledger_only, 'done'),
                           'cancelled': _first(ledger_only, 'cancel')},
            'execution': {'observed_runs': len(observed), 'run_records': 'available' if has_runs else 'not_available', 'runs_recorded': len(runs),
                          'note': 'A start in the ledger is a declaration. Execution is shown only by a run marked observed.'},
            'runs': runs, 'usage': _usage(runs), 'problems': problems,
            'provider_signal': _signal(task['owner'], config or {}, now), 'entries': entries,
            'limits': ['Actors are labels, not verified identities.', 'A task state is declared, not observed.',
                       'A provider signal describes a turn of an actor, not progress on this task.',
                       'A problem stays on record after it is resolved; it is history, not current state.']}


def run_summary(ledger, task_id):
    """For the queue: how many runs, and whether any is unclear. Empty when nothing is recorded."""
    rows = [dict(row) for row in ledger.db.execute('SELECT * FROM run_events WHERE task_id=? ORDER BY seq', (task_id,))]
    problems = []
    runs = _run_view(rows, problems)
    usage = _usage(runs)
    return {'runs': len(runs), 'unclear': sum(run['outcome'] != 'ok' for run in runs), 'unknown_usage': usage['runs_with_unknown_usage']}
