"""Bounded local observations; never store terminal contents or infer real progress."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as target:
            json.dump(value, target, ensure_ascii=False)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def classify_terminal(text):
    # Exact known modal with footer, not merely a historical mention of compaction.
    lower = '\n'.join(text[-16000:].splitlines()[-30:]).lower()
    if 'we recommend compacting earlier' in lower and 'enter confirm' in lower:
        return 'waiting_input_hint'
    if ('do you want to proceed?' in lower or 'do you trust the files in this folder?' in lower) and ('esc' in lower or 'enter' in lower):
        return 'waiting_input_hint'
    return 'unclassified'  # An idle prompt or running process proves no task progress.


def observe_sessions(sessions, previous=None):
    result = {}
    previous = previous or {}
    if not isinstance(sessions, dict) or len(sessions) > 32:
        raise ValueError('Invalid session mapping')
    for name, session in sessions.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', name) or not isinstance(session, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', session):
            raise ValueError('Invalid exact tmux target')
        try:
            run = subprocess.run(['tmux', 'capture-pane', '-t', '=' + session + ':0.0', '-p', '-S', '-45'],
                                 capture_output=True, text=True, timeout=3, check=False)
            signal = classify_terminal(run.stdout) if run.returncode == 0 else 'unavailable'
        except (OSError, subprocess.SubprocessError):
            signal = 'unavailable'
        now = time.time()
        old = previous.get(name, {})
        streak = min(2, old.get('samples', 0) + 1) if old.get('signal') == signal and 0 <= now - old.get('observed_at', 0) <= 180 else 1
        result[name] = {'signal': signal, 'samples': streak, 'observed_at': now, 'source': 'tmux-modal-heuristic'}
    return result


def snapshot(ledger, config, now=None, signals=None, previous=None):
    now = time.time() if now is None else now
    signals = observe_sessions(config.get('sessions', {}), previous) if signals is None else signals
    stale = config.get('stale_after_seconds', 900)
    handoff_after = config.get('handoff_after_seconds', 600)
    if type(stale) is not int or not 60 <= stale <= 86400 or type(handoff_after) is not int or not 60 <= handoff_after <= 86400:
        raise ValueError('Invalid observation thresholds')
    tasks = []
    for task in ledger.list():
        state = task['state']
        progress_age = max(0, now - task['progress']) if task['progress'] is not None else None
        observation = signals.get(task['owner'], {})
        hint = observation.get('signal') if observation.get('samples', 0) >= 2 else None
        attention = None
        if state in ('running', 'review') and hint == 'waiting_input_hint':
            attention = 'waiting_input_hint'
        elif state == 'handoff_pending' and now - task['updated'] > handoff_after:
            attention = 'handoff_unacknowledged'
        elif state in ('running', 'review') and (progress_age if progress_age is not None else max(0, now - task['updated'])) > stale:
            attention = 'progress_overdue_hint'
        elif state == 'blocked':
            attention = 'declared_blocked'
        tasks.append({'id': task['id'], 'title': task['title'], 'owner': task['owner'],
                      'state': state, 'target': task['target'], 'version': task['version'],
                      'created': task['created'], 'updated': task['updated'],
                      'progress_age_seconds': progress_age, 'attention': attention})
    return {'schema_version': 1, 'generated_at': now, 'tasks': tasks, 'signals': signals,
            'costs': ledger.cost_totals(), 'cost_coverage': 'recorded entries only; all other costs unknown',
            'access': {'principal_model': 'shared Unix account', 'actor_names_authenticated': False,
                       'declarations': config.get('access_declarations', [])},
            'limitations': ['No task execution or automatic retries.', 'Modal and overdue signals are hints, not proof of a hang.',
                            'A task state is declared, not observed execution.', 'Permissions listed here are not enforcement.']}
