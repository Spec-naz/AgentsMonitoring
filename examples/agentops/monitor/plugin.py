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
              'handoff_unacknowledged': 'Handoff not accepted', 'declared_blocked': 'Blocked by owner'}
    tasks = sorted(data['tasks'], key=lambda t: (t['state'] in ('done', 'cancelled'), -t['updated']))[:100]
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
    note = 'Declared workflow; actor labels are not authenticated. '
    if costs:
        note += 'Recorded costs: ' + '; '.join(f"{entry['micros']/1000000:.6f} {entry['currency']} ({entry['kind']})" for entry in costs[:6]) + '. '
    else:
        note += 'Costs unknown (no entries). '
    note += 'Unrecorded costs unknown; shared account permissions are not isolated.'
    return {'tables': [{'id': 'tasks', 'title': 'Work queue (declared state)', 'rows': rows, 'note': note[:500]}]}
