"""Prepare the start of one task without a model: what to read, which revision, and where to stop (LC-08).

The steps every takeover repeats are rules: read the task, find the handover and the reason for a return, check that the
linked output is really there. This does only that. It reads the ledger, one linked file and the continuity guard, and
changes nothing. It takes, starts and assigns nothing: choosing what is next belongs to the foreman monitor, doing the
work to an agent, and decisions to a person. The commands it prints are suggestions, and it runs none of them.

Anything unclear ends in `unknown`, which is handed to a person and not retried. The output has no clock in it, so
identical state gives identical bytes for the existing monitor gate.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from urllib.parse import quote
import uuid

SCHEMA = 'agentops-handoff-brief-v1'
DEFAULT_GUARD = Path.home() / '.local/state/task-continuity-guard/checkpoints.sqlite'
ACTOR = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
TIME_LIMIT_SECONDS = 10
GUARD_ALLOWS_WORK = ('ready', 'not_registered', 'not_available')
# Exit code for each ending, so a caller can stop without reading anything further.
EXIT = {'no_action': 0, 'start_agent': 10, 'person': 20, 'unknown': 30}
HANDED_TO = {'no_action': 'nobody', 'start_agent': 'agent', 'person': 'person', 'unknown': 'person'}


class Unknown(Exception):
    """The state cannot be established. The reason is a code, never text from the data."""


def _connect(path):
    resolved = Path(path).expanduser().absolute()
    if not resolved.is_file():
        raise Unknown('ledger_unavailable')
    return sqlite3.connect('file:%s?mode=ro' % quote(str(resolved)), uri=True, timeout=2)


def _read_task(path, task_id):
    """One consistent read of the task and its events."""
    try:
        connection = _connect(path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('BEGIN')
            task = connection.execute('SELECT id,owner,state,phase,producer,target,purpose,version,artifact FROM tasks WHERE id=?', (task_id,)).fetchone()
            events = [dict(row) for row in connection.execute("SELECT seq,actor,action,details FROM events WHERE task_id=? ORDER BY seq", (task_id,))]
            return dict(task) if task else None, events
        finally:
            connection.close()
    except sqlite3.Error:
        raise Unknown('ledger_unavailable') from None


def _details(event):
    try:
        value = json.loads(event['details'])
    except ValueError:
        raise Unknown('audit_details_unreadable') from None
    if not isinstance(value, dict):
        raise Unknown('audit_details_unreadable')
    return value


def _artifact(path, deadline):
    """Exists, is a plain file, and has this hash. Content is never copied or interpreted."""
    if not isinstance(path, str) or not path.startswith('/') or '\0' in path:
        return {'status': 'not_a_local_file', 'reference': path}
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return {'status': 'missing', 'reference': path}
    except OSError:
        return {'status': 'unreadable', 'reference': path}
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return {'status': 'not_a_file', 'reference': path}
        if info.st_size > MAX_ARTIFACT_BYTES:
            return {'status': 'too_large', 'reference': path}
        digest = hashlib.sha256()
        while True:
            if time.monotonic() > deadline:
                raise Unknown('time_limit')
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        return {'status': 'readable', 'reference': path, 'bytes': info.st_size, 'sha256': digest.hexdigest()}
    except OSError:
        return {'status': 'unreadable', 'reference': path}
    finally:
        os.close(descriptor)


def _guard(path, task_id):
    """An uncertain outside effect, as recorded by the continuity guard, stops the routine."""
    resolved = Path(path).expanduser().absolute()
    if not resolved.exists():
        return 'not_available'
    try:
        connection = sqlite3.connect('file:%s?mode=ro' % quote(str(resolved)), uri=True, timeout=2)
        try:
            row = connection.execute('SELECT mode FROM checkpoints WHERE task_id=? AND retired=0', (task_id,)).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        raise Unknown('guard_unavailable') from None
    return row[0] if row and isinstance(row[0], str) else 'not_registered'


def _returned(task, events):
    producer = task['producer']
    if not producer:
        return None
    for event in reversed(events):
        if event['action'] == 'offer':
            details = _details(event)
            if details.get('purpose') == 'work' and details.get('target') == producer and event['actor'] != producer:
                return {'by': event['actor'], 'reason_artifact': details.get('artifact')}
    return None


def _commands(task, actor, ending_kind):
    """Text for the worker. Nothing here runs it."""
    base = '--actor %s --expected-version %d' % (actor, task['version'])
    if ending_kind == 'accept':
        steps = ['agentops accept %s %s' % (task['id'], base)]
        if task['purpose'] != 'review':  # accepting a review already enters it; only work has a separate start
            steps.append('agentops start %s --actor %s --expected-version %d' % (task['id'], actor, task['version'] + 1))
        return steps
    if ending_kind == 'start':
        return ['agentops start %s %s' % (task['id'], base)]
    return []


def _handed_to_owner(task, events, owner_actor):
    """Explicit only, and it stays a decision after accepting. Merely owning a task asks nobody for anything."""
    if task['target'] == owner_actor:
        return True
    if task['owner'] != owner_actor:
        return False
    for event in reversed(events):
        if event['action'] == 'offer':
            return _details(event).get('target') == owner_actor
    return False


def _decide(task, events, actor, artifact, guard, owner_actor):
    """Rules in a fixed order. The first that applies ends it."""
    state = task['state']
    if state in ('done', 'cancelled'):
        return 'no_action', 'task_closed', None
    if guard == 'needs_reconciliation':
        return 'person', 'uncertain_outside_effect', None
    # A saved pause or wait must not be overridden by a routine. An unrecognised mode is treated the same way.
    if guard not in GUARD_ALLOWS_WORK:
        return 'person', 'guard_says_wait', None
    if _handed_to_owner(task, events, owner_actor):
        return 'person', 'decision_handed_to_owner', None
    is_mine = task['owner'] == actor if state != 'handoff_pending' else task['target'] == actor
    if not is_mine:
        return 'no_action', 'waits_for_someone_else', None
    if state == 'blocked':
        return 'person', 'declared_blocked', None
    if artifact is not None and artifact['status'] != 'readable':
        return 'person', 'linked_output_' + artifact['status'], None
    if state == 'handoff_pending':
        return 'start_agent', 'handover_to_accept', 'accept'
    if state == 'queued':
        # Queued alone is not a start signal: most backlog is queued and was never handed to anyone.
        last = next((event for event in reversed(events) if event['action'] not in ('progress', 'cost', 'postcheck')), None)
        if not last or last['action'] != 'accept':
            return 'no_action', 'queued_not_handed_over', None
        return 'start_agent', 'accepted_not_started', 'start'
    if state == 'review' or state == 'running':
        return 'start_agent', 'already_in_progress', None
    raise Unknown('state_not_recognised')


def prepare(ledger_path, task_id, actor, guard_path=DEFAULT_GUARD, owner_actor='michal', time_limit=TIME_LIMIT_SECONDS):
    """Always returns a result with one ending. It does not raise for a state it cannot establish."""
    if not isinstance(actor, str) or not ACTOR.fullmatch(actor):
        raise ValueError('Invalid actor')
    try:
        if str(uuid.UUID(task_id)) != task_id:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        raise ValueError('Invalid task id') from None
    deadline = time.monotonic() + time_limit
    result = {'schema': SCHEMA, 'task_id': task_id, 'for': actor, 'executed_nothing': True, 'content_copied': False}
    try:
        task, events = _read_task(ledger_path, task_id)
        if task is None:
            raise Unknown('task_not_found')
        artifact = _artifact(task['artifact'], deadline) if task['artifact'] else None
        guard = _guard(guard_path, task_id)
        again, _ = _read_task(ledger_path, task_id)
        if again != task:
            raise Unknown('task_changed_during_read')
        ending, reason, step = _decide(task, events, actor, artifact, guard, owner_actor)
        returned = _returned(task, events)
        check = None
        for event in reversed(events):
            if event['action'] == 'postcheck':
                details = _details(event)
                check = {'verdict': details.get('verdict'), 'report': details.get('report')}
                break
    except Unknown as stop:
        return {**result, 'ending': 'unknown', 'reason': str(stop), 'handed_to': HANDED_TO['unknown'], 'retry': False}
    result.update(ending=ending, reason=reason, handed_to=HANDED_TO[ending], retry=False, state=task['state'], version=task['version'],
                  owner=task['owner'], target=task['target'], purpose=task['purpose'], producer=task['producer'], guard=guard)
    if ending == 'start_agent':
        result.update(read_first=artifact, returned=returned, result_check=check, commands=_commands(task, actor, step),
                      must_not_review_own_work=task['producer'] == actor and task['phase'] == 'review')
    elif ending == 'person':
        result.update(read_first=artifact)
    return result


def stable_json(result):
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)
