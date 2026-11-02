#!/usr/bin/env python3
"""Read-only, stable metadata monitor for a small explicitly approved work catalogue.

No provider calls, task mutations, scheduling, notifications or terminal input.
Hermes' existing monitor gate deduplicates byte-identical output before any agent run.
"""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import uuid


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = Path.home() / '.local/state/agentops/tasks.sqlite'
DEFAULT_PAUSE = Path.home() / '.local/state/agent-foreman/PAUSED'
DEFAULT_CATALOGUE = Path.home() / '.config/agent-foreman/catalogue.json'
ACTOR = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
SCHEMA = 'agent-foreman-monitor-v1'


def text_actor(value):
    if not isinstance(value, str) or not ACTOR.fullmatch(value):
        raise ValueError('Invalid actor')
    return value


def task_id(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Invalid task id')
    return value


def load_catalogue(path):
    path = Path(path)
    if path.stat().st_size > 32768:
        raise ValueError('Catalogue too large')
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or set(value) != {'schema', 'coordinator', 'tasks'}:
        raise ValueError('Invalid catalogue')
    if value['schema'] != 'agent-foreman-catalogue-v1':
        raise ValueError('Invalid catalogue schema')
    text_actor(value['coordinator'])
    tasks = value['tasks']
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 32:
        raise ValueError('Invalid tasks')
    seen = set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != {'id', 'worker', 'depends_on'}:
            raise ValueError('Invalid task')
        tid = task_id(task['id'])
        if tid in seen:
            raise ValueError('Duplicate task')
        seen.add(tid)
        text_actor(task['worker'])
        deps = task['depends_on']
        if not isinstance(deps, list) or len(deps) > 16:
            raise ValueError('Invalid dependencies')
        for dependency in deps:
            if task_id(dependency) == tid:
                raise ValueError('Self dependency')
    if any(dep not in seen for task in tasks for dep in task['depends_on']):
        raise ValueError('Dependency outside catalogue')
    return value


def read_tasks(path):
    # Missing DB is never created; no Ledger constructor, WAL/schema setup or writes.
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True, timeout=3) as db:
        db.row_factory = sqlite3.Row
        tasks = {row['id']: dict(row) for row in db.execute(
            'SELECT id,owner,state,phase,target,purpose FROM tasks')}
        # Distinguish a genuinely new handoff after a return/rework loop from
        # an old identical-looking state. Progress/cost/postcheck noise is excluded.
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            for event in db.execute("SELECT task_id,max(seq) AS seq FROM events WHERE action IN "
                                    "('create','start','offer','accept','withdraw','block','done','cancel') GROUP BY task_id"):
                if event['task_id'] in tasks:
                    tasks[event['task_id']]['transition_seq'] = event['seq']
        return tasks


def decide(catalogue, tasks):
    """Return only actionable catalogue changes, never clock/progress/version noise.

    A lack of a declared active task is NOT proof of provider idleness. Caller must
    still obtain an actual runtime ACK before delivery/start. Only one candidate
    per worker is exposed, in the explicit catalogue order.
    """
    coordinator = catalogue['coordinator']
    events = []
    candidates = {}
    for approved in catalogue['tasks']:
        row = tasks.get(approved['id'])
        if row is None:
            continue
        state, owner = row['state'], row['owner']
        if (state == 'handoff_pending' and row['target'] == coordinator
                and row['purpose'] == 'review'):
            events.append({'kind': 'review_pending', 'task_id': approved['id'],
                           'to': coordinator})
            continue
        # Accepted but unfinished review remains actionable without fresh mutations.
        if state == 'review' and owner == coordinator and row['phase'] == 'review':
            events.append({'kind': 'review_owned', 'task_id': approved['id'],
                           'to': coordinator})
            continue
        if any(tasks.get(dep, {}).get('state') != 'done'
               for dep in approved['depends_on']):
            continue
        worker = approved['worker']
        # Consider all ledger work for a worker, even outside the approved catalogue.
        busy = any(task['owner'] == worker and task['state'] in {'running', 'review'}
                   for task in tasks.values())
        if busy:
            continue
        kind = None
        if (state == 'handoff_pending' and row['target'] == worker
                and row['purpose'] == 'work'):
            kind = 'work_handoff'
        elif state == 'queued' and owner == worker and row['phase'] == 'work':
            kind = 'work_owned'
        elif state == 'queued' and owner == coordinator and row['phase'] == 'work':
            # This is a request to the coordinator to prepare/offer, not self-claim.
            kind = 'prepare_offer'
        if kind:
            candidate = {'kind': kind, 'task_id': approved['id'],
                         'from': coordinator, 'to': worker}
            # Resume already-owned queued work before accepting another offer.
            if worker not in candidates or (kind == 'work_owned'
                    and candidates[worker]['kind'] != 'work_owned'):
                candidates[worker] = candidate
    events.extend(candidates.values())
    for event in events:
        sequence = tasks.get(event['task_id'], {}).get('transition_seq')
        if type(sequence) is int and sequence > 0:
            event['transition_seq'] = sequence
    return {'schema': SCHEMA, 'events': events}


def monitor(db, catalogue_path, pause_path):
    empty = {'schema': SCHEMA, 'events': []}
    try:
        # Dangling marker also pauses. Symlinks/owner labels are not auth boundaries.
        pause = Path(pause_path)
        if pause.exists() or pause.is_symlink():
            return empty
        return decide(load_catalogue(catalogue_path), read_tasks(db))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        return {'schema': SCHEMA, 'events': [{'kind': 'monitor_unavailable'}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--catalogue', type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument('--pause', type=Path, default=DEFAULT_PAUSE)
    args = parser.parse_args()
    print(json.dumps(monitor(args.db, args.catalogue, args.pause), sort_keys=True,
                     separators=(',', ':')))


if __name__ == '__main__':
    main()
