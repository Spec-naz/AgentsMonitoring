"""Declared workflow state with transactional handoffs, review gates and audit events.

Actor names are accountability labels, NOT authentication or a security boundary.
All local agents currently share one Unix principal. This module executes no tasks.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid


class Conflict(ValueError):
    pass


def label(value, limit=240):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid text field')
    return value.strip()


def actor(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', value):
        raise ValueError('Invalid actor label')
    return value


class Ledger:
    def __init__(self, path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError('Ledger cannot be a symlink')
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError('Ledger must be an owned regular file with mode 0600')
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS tasks(
            id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL,
            state TEXT NOT NULL, phase TEXT NOT NULL DEFAULT 'work', producer TEXT,
            target TEXT, purpose TEXT, created REAL NOT NULL, updated REAL NOT NULL,
            progress REAL, version INTEGER NOT NULL DEFAULT 1, artifact TEXT);
          CREATE TABLE IF NOT EXISTS events(
            seq INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL);
          CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT,'Audit events are append-only'); END;
          CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT,'Audit events are append-only'); END;
          CREATE TABLE IF NOT EXISTS commands(
            request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS costs(
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
            actor TEXT NOT NULL, at REAL NOT NULL, micros INTEGER NOT NULL,
            currency TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    def get(self, task_id):
        row = self.db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown task')
        return dict(row)

    def list(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM tasks ORDER BY created,id')]

    def events(self, task_id):
        return [dict(row) for row in self.db.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (task_id,))]

    def _command(self, payload, request_id, work):
        request_id = label(request_id or str(uuid.uuid4()), 120)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old = self.db.execute('SELECT * FROM commands WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise Conflict('Request id reused with different content')
                result = json.loads(old['result'])
            else:
                result = work()
                self.db.execute('INSERT INTO commands VALUES(?,?,?)', (request_id, fingerprint, json.dumps(result)))
            self.db.execute('COMMIT')
            return result
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def _event(self, task_id, who, action, details):
        self.db.execute('INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)',
                        (task_id, time.time(), who, action, json.dumps(details)))

    def create(self, title, owner, by, request_id=None):
        title, owner, by = label(title), actor(owner), actor(by)
        def work():
            task_id, now = str(uuid.uuid4()), time.time()
            self.db.execute('INSERT INTO tasks(id,title,owner,state,created,updated) VALUES(?,?,?,?,?,?)',
                            (task_id, title, owner, 'queued', now, now))
            self._event(task_id, by, 'create', {'task': self.get(task_id)})
            return self.get(task_id)
        return self._command({'op': 'create', 'title': title, 'owner': owner, 'by': by}, request_id, work)

    def change(self, task_id, who, action, *, target=None, purpose=None, note=None,
               artifact=None, expected=None, request_id=None):
        who = actor(who)
        if target is not None:
            target = actor(target)
        if note is not None:
            note = label(note, 500)
        if artifact is not None:
            artifact = label(artifact, 500)
        payload = dict(op=action, id=task_id, who=who, target=target, purpose=purpose,
                       note=note, artifact=artifact, expected=expected)
        def work():
            task = self.get(task_id)
            if expected is not None and expected != task['version']:
                raise Conflict('Task changed; refresh before retrying')
            now = time.time()
            changes = {'updated': now, 'version': task['version'] + 1}
            if action == 'accept':
                if task['state'] != 'handoff_pending' or task['target'] != who:
                    raise Conflict('No handoff addressed to this actor')
                changes.update(owner=who, target=None, purpose=None,
                               state='review' if task['purpose'] == 'review' else 'queued',
                               phase=task['purpose'])
            else:
                if task['owner'] != who:
                    raise Conflict('Only the current owner may change this task')
                if action == 'start' and task['state'] in ('queued', 'blocked'):
                    changes.update(state='review' if task['phase'] == 'review' else 'running', progress=now)
                elif action == 'progress' and task['state'] in ('running', 'review') and note:
                    changes.update(progress=now)
                elif action == 'block' and task['state'] in ('running', 'review') and note:
                    changes.update(state='blocked')
                elif action == 'offer' and task['state'] in ('running', 'review', 'blocked'):
                    if not target or target == who or purpose not in ('work', 'review') or not artifact:
                        raise ValueError('Handoff requires another actor, purpose and artifact reference')
                    if purpose == 'review' and task['phase'] == 'review' and task['producer'] == target:
                        raise Conflict('Original producer cannot review their unchanged work')
                    changes.update(state='handoff_pending', target=target, purpose=purpose, artifact=artifact)
                    if purpose == 'review' and task['phase'] != 'review':
                        changes.update(producer=who)
                elif action == 'withdraw' and task['state'] == 'handoff_pending' and note:
                    changes.update(state='blocked', target=None, purpose=None)
                elif action == 'done' and task['state'] == 'review' and artifact:
                    if not task['producer'] or task['producer'] == who:
                        raise Conflict('Completion requires an independent reviewer')
                    changes.update(state='done', artifact=artifact)
                elif action == 'cancel' and task['state'] not in ('done', 'cancelled') and note:
                    changes.update(state='cancelled', target=None, purpose=None)
                else:
                    raise Conflict('Invalid transition or required evidence missing')
            fields = ','.join(key + '=?' for key in changes)
            self.db.execute('UPDATE tasks SET ' + fields + ' WHERE id=?', (*changes.values(), task_id))
            self._event(task_id, who, action, {'from_state': task['state'], 'to_state': changes.get('state', task['state']),
                                             'changes': changes, **{k: v for k, v in payload.items() if v is not None}})
            return self.get(task_id)
        return self._command(payload, request_id, work)

    def cost(self, task_id, who, micros, currency, kind, source, request_id=None):
        who, source = actor(who), label(source, 240)
        if type(micros) is not int or not 0 <= micros <= 10**15 or not re.fullmatch('[A-Z]{3}', currency) or kind not in ('actual', 'estimate'):
            raise ValueError('Invalid cost observation')
        payload = dict(op='cost', id=task_id, who=who, micros=micros, currency=currency, kind=kind, source=source)
        def work():
            self.get(task_id)
            cost_id = str(uuid.uuid4())
            self.db.execute('INSERT INTO costs VALUES(?,?,?,?,?,?,?,?)',
                            (cost_id, task_id, who, time.time(), micros, currency, kind, source))
            self._event(task_id, who, 'cost', payload)
            return {'id': cost_id, **payload}
        return self._command(payload, request_id, work)

    def cost_totals(self):
        return [dict(row) for row in self.db.execute(
            'SELECT currency,kind,SUM(micros) AS micros,COUNT(*) AS records FROM costs GROUP BY currency,kind')]
