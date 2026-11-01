"""Use the existing dashboard renderer; no HTML, controls, auth or credential access."""
import json
from pathlib import Path
import time


def collect(context, settings):
    try:
        path = Path(settings.get('snapshot_path', '~/.local/state/agentops/snapshot.json')).expanduser()
        if path.stat().st_size > 1024 * 1024:
            raise ValueError('Snapshot too large')
        data = json.loads(path.read_text())
        if data.get('schema_version') != 1 or not 0 <= time.time() - data['generated_at'] < 180:
            raise ValueError('Snapshot expired')
    except (OSError, ValueError, TypeError, KeyError):
        return {'tables': [{'id': 'tasks', 'title': 'Work queue (declared state)', 'rows': [],
                            'error': 'AgentOps snapshot unavailable or older than 3 minutes.'}]}
    states = {'queued': 'Awaiting start', 'running': 'In progress (declared)', 'blocked': 'Blocked',
              'handoff_pending': 'Awaiting acceptance', 'review': 'In review', 'done': 'Reviewed', 'cancelled': 'Cancelled'}
    alerts = {'waiting_input_hint': 'Possible input dialog', 'progress_overdue_hint': 'Progress update overdue',
              'handoff_unacknowledged': 'Handoff not accepted', 'declared_blocked': 'Blocked by owner',
              'provider_waiting_input': 'Input requested (hook)', 'provider_waiting_hint': 'Approval may be pending',
              'provider_turn_failed': 'Turn failed (hook)'}
    active_tasks = [task for task in data['tasks'] if task['state'] not in ('done', 'cancelled')]
    tasks = sorted(active_tasks, key=lambda task: -task['updated'])[:100]
    rows = []
    for task in tasks:
        state = task['state']
        attention = task.get('attention')
        detail = f"Declared owner: {task['owner']}; revision {task['version']}. "
        if task.get('target'):
            detail += f"Awaiting explicit acceptance by {task['target']}. "
        age = task.get('progress_age_seconds')
        detail += 'No progress evidence recorded. ' if age is None else f'Last declared progress {int(age)}s ago. '
        detail += 'Workflow state, not process health. Actors share one Unix account.'
        rows.append({'id': 't-' + task['id'], 'name': task['title'][:240],
                     'label': task['owner'], 'vendor': 'workflow', 'alive': not bool(attention),
                     'status': alerts.get(attention, states.get(state, 'Unknown')),
                     'status_detail': detail[:500], 'actions_locked': True,
                     'age': max(0, time.time() - task['created'])})
    costs = data.get('costs', [])
    note = f'Showing {len(tasks)} of {len(active_tasks)} active tasks; history retained. Declared workflow; actors not authenticated. '
    if costs:
        note += 'Recorded costs: ' + '; '.join(f"{entry['micros']/1000000:.6f} {entry['currency']} ({entry['kind']})" for entry in costs[:6]) + '. '
    else:
        note += 'Costs unknown (no entries). '
    note += 'Unrecorded costs unknown; shared account permissions are not isolated.'
    tables = [{'id': 'tasks', 'title': 'Work queue (declared state)', 'rows': rows, 'note': note[:500]}]
    signals = []
    for name, observation in data.get('signals', {}).items():
        signal = observation.get('signal')
        provider = observation.get('provider_observation', {})
        label = 'Terminal heuristic'
        detail = 'Configured terminal observation only; not proof of a hang. No keystrokes are sent.'
        healthy = False
        repeated_hint = signal == 'waiting_input_hint' and observation.get('samples', 0) >= 2
        state = provider.get('state')
        if state in ('waiting_input', 'idle', 'failed') or (state == 'turn_running' and not repeated_hint):
            statuses = {'turn_running': 'Turn started (hook)', 'idle': 'Turn ended (hook)',
                        'waiting_input': 'Input requested (hook)', 'failed': 'Turn failed (hook)'}
            status = statuses[provider['state']]
            if provider['state'] == 'idle' and provider.get('last_event') != 'Stop':
                status = 'Idle reported (hook)'
            if provider['state'] == 'waiting_input' and provider.get('confidence') != 'confirmed':
                status = 'Approval may be pending'
            label = str(provider.get('provider') or 'Provider') + ' hook'
            healthy = provider['state'] in ('turn_running', 'idle')
            detail = 'Last bound provider event: ' + str(provider.get('last_event') or 'none') + '. '
            if provider.get('age_seconds') is not None:
                detail += str(int(provider['age_seconds'])) + 's ago. '
            detail += 'Provider turn state, not task progress or process-health proof. No task is completed automatically.'
        elif repeated_hint:
            status = 'Possible input dialog'
        else:
            continue
        signals.append({'id': name, 'name': name, 'label': label, 'alive': healthy,
                        'status': status, 'actions_locked': True,
                        'status_detail': detail[:500]})
    total_observed = len(data.get('signals', {}))
    tables[0]['note'] = (f'Current signals: {len(signals)}/{total_observed} configured agents; missing data is not a failure or health proof. ' + tables[0]['note'])[:500]
    if signals:
        tables.append({'id': 'signals', 'title': 'Agent signals (hooks / hints)', 'rows': signals,
                       'note': 'Fresh bound hook events and repeated terminal hints only. Missing or stale observations are omitted, not healthy. Turn state does not prove task progress. No automatic intervention.'})
    return {'tables': tables}
