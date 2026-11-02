#!/usr/bin/env python3
"""Durable, non-inference relay from approved metadata to an existing Codex thread.

The external queue CLI may resume inference in that already approved thread. This
script never selects a model/provider, calls an inference API or inputs a TUI.
Queue exit zero is not a turn ACK: only explicit ack records observed evidence.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3
import subprocess
import tempfile

from foreman import DEFAULT_CATALOGUE, DEFAULT_DB, DEFAULT_PAUSE, monitor, task_id


DEFAULT_STATE = Path.home() / '.local/state/agent-foreman'


def now():
    return datetime.now(timezone.utc).isoformat()


def paused(path):
    return Path(path).exists() or Path(path).is_symlink()


def read_receipts(state):
    path = state / 'relay.json'
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Invalid receipts')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Invalid receipts')
    return value


def save_receipts(state, data):
    fd, name = tempfile.mkstemp(prefix='.relay-', dir=state)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle, sort_keys=True)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, state / 'relay.json')
        directory = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def event_identity(thread, event):
    payload = json.dumps({'thread': thread, 'event': event}, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def message(event_id, event):
    return ('[TEAM Předák] FOREMAN RECEIPT ' + event_id + '\n'
            'Schválená provozní událost: ' + json.dumps(event, ensure_ascii=False, sort_keys=True) + '\n'
            'Zkontroluj aktuální AgentOps a PAUSED v ~/.local/state/agent-foreman, '
            'pokračuj v původní práci, případně převezmi review nebo konkrétní připravené předání. '
            'Žádný nový API/PAYG, poskytovatel, slepý retry ani TUI vstup. '
            'Po skutečném zpracování ulož explicitní ACK přes instalovaný příkaz '
            'agent-foreman ack --event ' + event_id + ' --evidence "konkrétní skutečně ověřený krok"; '
            'samotné doručení zprávy není dokončení úkolu.')


def locked_state(state):
    state = Path(state)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink() or state.stat().st_uid != os.getuid():
        raise ValueError('Invalid state directory')
    # Append mode is deliberate: opening the same live lock must not truncate it.
    descriptor = os.open(state / 'relay.lock', os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    lock = os.fdopen(descriptor, 'a')
    info = os.fstat(lock.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        lock.close()
        raise ValueError('Invalid lock')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def poll(thread, *, db=DEFAULT_DB, catalogue=DEFAULT_CATALOGUE,
         pause=DEFAULT_PAUSE, state=DEFAULT_STATE, codex='codex', timeout=20):
    task_id(thread)
    if paused(pause):
        return {'status': 'paused'}
    lock = locked_state(state)
    if lock is None:
        return {'status': 'busy'}
    with lock:
        if paused(pause):
            return {'status': 'paused'}
        data = monitor(db, catalogue, pause)
        events = data['events']
        if not events:
            return {'status': 'no_events'}
        receipts = read_receipts(Path(state))
        # One queue operation per tick. Previously attempted events, including
        # uncertain reservations, are never automatically retried.
        chosen = next(((event_identity(thread, event), event) for event in events
                       if event_identity(thread, event) not in receipts), None)
        if chosen is None:
            return {'status': 'unchanged'}
        event_id, event = chosen
        receipts[event_id] = {'state': 'reserved', 'thread': thread, 'event': event, 'reserved_at': now()}
        save_receipts(Path(state), receipts)
        # A stop arriving before queueing gets the last possible local check.
        # This is not an atomic transaction with a user's native provider stop.
        if paused(pause):
            receipts[event_id].update(state='paused_before_enqueue', updated_at=now())
            save_receipts(Path(state), receipts)
            return {'status': 'paused', 'event_id': event_id}
        try:
            result = subprocess.run([codex, 'queue', '--thread', thread, '--message',
                                     message(event_id, event), '--resume'],
                                    capture_output=True, text=True, timeout=timeout)
            warning = 'warning' in result.stderr.lower()
            disposition = ('queued_resume_unverified' if result.returncode == 0 and not warning
                           else 'resume_or_queue_unverified')
            receipts[event_id].update(state=disposition, exit_code=result.returncode,
                                     resume_warning=warning, updated_at=now())
        except subprocess.TimeoutExpired:
            receipts[event_id].update(state='timeout_unverified', updated_at=now())
        except OSError:
            receipts[event_id].update(state='queue_unavailable_unverified', updated_at=now())
        # CLI stdout/stderr may contain private session details; never persist them.
        save_receipts(Path(state), receipts)
        return {'status': receipts[event_id]['state'], 'event_id': event_id}


def acknowledge(state, event_id, evidence):
    if (len(event_id) != 64 or any(c not in '0123456789abcdef' for c in event_id)
            or not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 500
            or any(ord(c) < 32 for c in evidence)):
        raise ValueError('Explicit bounded evidence required')
    lock = locked_state(state)
    if lock is None:
        return {'status': 'busy'}
    with lock:
        receipts = read_receipts(Path(state))
        if event_id not in receipts:
            raise ValueError('Unknown receipt')
        receipts[event_id].update(state='acknowledged', evidence=evidence.strip(), acknowledged_at=now())
        save_receipts(Path(state), receipts)
        return {'status': 'acknowledged', 'event_id': event_id}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['poll', 'status', 'ack'])
    parser.add_argument('--thread')
    parser.add_argument('--state', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--db', type=Path, default=DEFAULT_DB)
    parser.add_argument('--catalogue', type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument('--pause', type=Path, default=DEFAULT_PAUSE)
    parser.add_argument('--event')
    parser.add_argument('--evidence')
    args = parser.parse_args()
    try:
        if args.action == 'poll':
            if not args.thread:
                raise ValueError('Configured thread required')
            output = poll(args.thread, db=args.db, catalogue=args.catalogue, pause=args.pause, state=args.state)
        elif args.action == 'ack':
            output = acknowledge(args.state, args.event or '', args.evidence)
        else:
            output = {'receipts': read_receipts(args.state)}
        print(json.dumps(output, sort_keys=True, separators=(',', ':')))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        print(json.dumps({'status': 'relay_unavailable'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
