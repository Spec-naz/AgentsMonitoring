"""Use the existing dashboard renderer; no HTML, controls, auth or credential access."""
import json
from pathlib import Path
import time


QUEUE_LABELS = {'rework': 'Oprava po kontrole', 'peer_review': 'Kontrola kolegou',
                'takeover': 'Čeká na převzetí', 'blocked': 'Zablokováno'}
MAX_TABLE_ROWS = 200  # the dashboard refuses a table with more
DETAILS_LABEL = 'Odkaz ke kopírování'
QUEUE_NOTE = (f'Cesty a příkazy jsou reference: otevři „{DETAILS_LABEL}“ a klepni na text. '
              'Soubory se odtud neotevírají. Deklarovaný stav, ne ověřený běh.')


def _plain(value, limit):
    """The dashboard refuses control characters and long text, and one bad row would hide the whole plugin."""
    text = ''.join(' ' if ord(char) < 32 else char for char in str(value)).strip()
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _short(reference):
    """What fits on a phone: the file name of a local path, the full reference stays in the copy field."""
    return reference.rsplit('/', 1)[-1] or reference


def _references(row):
    """Evidence as visible rows: the title says what it is, and the full reference can be copied."""
    found = []
    check = row.get('postcheck')
    if check:
        extra = ', done se odmítne, dokud není novější pass' if row.get('postcheck_blocks_done') else ''
        found.append((f"Kontrola výsledku: {check['verdict']}{extra}, {_short(check['report'])}", check['report']))
    returned = row.get('returned')
    if returned and returned.get('reason_artifact'):
        found.append((f"Vrátil {returned['by']}, důvod: {_short(returned['reason_artifact'])}", returned['reason_artifact']))
    if row.get('blocked_reason'):
        how = 'Stažené předání' if row.get('blocked_action') == 'withdraw' else 'Zablokováno'
        found.append((f"{how}, důvod: {row['blocked_reason']}", row['blocked_reason']))
    if row.get('artifact') and row['artifact'] != (returned or {}).get('reason_artifact'):
        found.append((f"Výstup: {_short(row['artifact'])}", row['artifact']))
    runs = row.get('runs') or {}
    if runs.get('runs'):
        unclear = f", nejasné: {runs['unclear']}" if runs.get('unclear') else ''
        usage = f", spotřeba neznámá u {runs['unknown_usage']}" if runs.get('unknown_usage') else ''
        found.append((f"Běhy: {runs['runs']}{unclear}{usage}", row.get('timeline')))
    if row.get('reviewer_must_not_be'):
        found.append((f"Kontrola nezávisle, ne {row['reviewer_must_not_be']}", None))
    return found


def _queue_row(row_id, name, kind, **fields):
    # alive stays true: a declared state is not process health. The workflow kind decides how a row looks.
    return {'id': row_id, 'name': _plain(name, 240), 'kind': kind, 'vendor': 'workflow', 'alive': True, 'actions_locked': True, **fields}


def _tasks(count):
    return f"{count} úkol" if count == 1 else f"{count} úkoly" if 2 <= count <= 4 else f"{count} úkolů"


def queue_table(queue):
    if not isinstance(queue, dict) or not queue.get('rows'):
        return None
    owner = str(queue.get('owner_actor') or 'vlastník')
    labels = {'owner_decision': f'Rozhodnutí: {owner}', **QUEUE_LABELS}
    rows, shown, attention = [], 0, 0
    for row in queue['rows']:
        references = _references(row)
        if len(rows) + 1 + len(references) > MAX_TABLE_ROWS:
            break
        shown += 1
        detail = _plain(f"Čeká na: {row['waiting_on']}. Průběh: {row['timeline']}.", 500)
        needs_look = row['category'] in ('owner_decision', 'blocked') or row.get('postcheck_blocks_done') or (row.get('runs') or {}).get('unclear')
        attention += bool(needs_look)
        rows.append(_queue_row('q-' + row['id'], row['title'], 'workflow-attention' if needs_look else 'workflow-task',
                               label=_plain(row['waiting_on'], 240),
                               status=labels.get(row['category'], 'Neznámé'), status_detail=detail,
                               session_id=_plain(row['timeline'], 500), age=row.get('waiting_seconds', 0)))
        for number, (name, reference) in enumerate(references):
            fields = {'session_id': _plain(reference, 500)} if reference else {}
            rows.append(_queue_row(f"q-{row['id']}-{number}", '↳ ' + name, 'workflow-evidence', label='podklad',
                                   status='Podklad', status_detail=_plain(reference or name, 500), **fields))
    counts = queue.get('counts', {})
    note = ', '.join(f"{labels[name]}: {counts[name]}" for name in labels if counts.get(name))
    if queue.get('truncated') or shown < len(queue['rows']):
        note += '. Zobrazena jen část fronty'
    summary = _tasks(shown) + (f' · {attention} k pozornosti' if attention else '')
    return {'id': 'queue', 'title': 'Co čeká a na koho', 'rows': rows, 'note': _plain(f'{note}. {QUEUE_NOTE}', 500),
            # Understood by agentsmon 0.4.0.dev8 and newer; older versions ignore it.
            'presentation': {'type': 'workflow', 'count': _tasks(shown), 'summary': summary, 'details_label': DETAILS_LABEL,
                             'headers': ['Úkol', 'Čeká na', 'Odkaz', 'Čeká', 'Stav']}}


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
    queue = queue_table(data.get('queue'))
    if queue:
        tables.append(queue)
    return {'tables': tables}
