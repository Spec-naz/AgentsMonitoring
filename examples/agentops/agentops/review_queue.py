"""One read-only list of what waits for a person or colleague, with links to the evidence (LC-03).

It only reads the ledger. It adds no approval, and asks for no decision by itself: a task is an owner decision
only when it was explicitly handed to that actor. Everything else stays with the team.
States are declared in the ledger, so this shows what was recorded, not what really happened.
"""
import json
import time

from .ledger import actor
from .timeline import run_summary

CATEGORIES = ('owner_decision', 'rework', 'peer_review', 'takeover', 'blocked')
MAX_ROWS = 200


def _details(event):
    return json.loads(event['details'])


def _latest(events, action, test=lambda details: True):
    for event in reversed(events):
        if event['action'] == action and test(_details(event)):
            return event, _details(event)
    return None, None


def _return_of(task, events):
    """The newest handover from a reviewer back to the producer, with what it points to as the reason."""
    producer = task['producer']
    if not producer:
        return None
    event, details = _latest(events, 'offer', lambda row: row.get('purpose') == 'work' and row.get('target') == producer)
    if not event:
        return None
    return {'by': event['actor'], 'at': event['at'], 'reason_artifact': details.get('artifact')}


def _handed_to(task, events, owner_actor):
    """Explicit only: a pending offer, or an accepted one. Merely owning a task asks nobody for anything."""
    if task['target'] == owner_actor:
        return True
    if task['owner'] != owner_actor:
        return False
    event, _ = _latest(events, 'offer')
    return bool(event) and _details(event).get('target') == owner_actor


def _blocked_by(events):
    """The event that really moved the task to blocked: a block or a withdrawn handover, with its own note."""
    for event in reversed(events):
        details = _details(event)
        if details.get('to_state') == 'blocked':
            return {'reason': details.get('note'), 'action': event['action'], 'by': event['actor'], 'at': event['at']}
    return None


def _category(task, returned, owner_actor, handed):
    if handed:
        return 'owner_decision'
    state = task['state']
    if state == 'blocked':
        return 'blocked'
    if state == 'review' or (state == 'handoff_pending' and task['purpose'] == 'review'):
        return 'peer_review'
    if state == 'handoff_pending':
        return 'rework' if returned and task['target'] == task['producer'] else 'takeover'
    if state in ('queued', 'running') and returned and task['phase'] == 'work' and task['owner'] == task['producer']:
        return 'rework'
    return None  # ordinary work: nobody needs to be told


def build(ledger, owner_actor='michal', now=None, waiting_on=None):
    owner_actor = actor(owner_actor)
    waiting_on = None if waiting_on is None else actor(waiting_on)
    now = time.time() if now is None else now
    rows = []
    for task in ledger.list():
        if task['state'] in ('done', 'cancelled'):
            continue
        events = ledger.events(task['id'])
        returned = _return_of(task, events)
        category = _category(task, returned, owner_actor, _handed_to(task, events, owner_actor))
        if category is None or (waiting_on and (task['target'] or task['owner']) != waiting_on):
            continue
        check = ledger.latest_postcheck(task['id'])
        blocked = _blocked_by(events) if task['state'] == 'blocked' else None
        rows.append({
            'id': task['id'], 'title': task['title'], 'category': category, 'state': task['state'],
            'owner': task['owner'], 'waiting_on': task['target'] or task['owner'], 'version': task['version'],
            'waiting_seconds': max(0, now - task['updated']), 'artifact': task['artifact'],
            'returned': returned if category == 'rework' else None,
            'blocked_reason': blocked['reason'] if blocked else None,
            'blocked_action': blocked['action'] if blocked else None,
            # The ledger refuses done by the producer; shown so nobody plans a review that cannot count.
            'reviewer_must_not_be': task['producer'] if category == 'peer_review' else None,
            'postcheck': check, 'postcheck_blocks_done': bool(check) and check['verdict'] != 'pass',
            'runs': run_summary(ledger, task['id']),
            'timeline': 'agentops timeline ' + task['id']})
    rows.sort(key=lambda row: (CATEGORIES.index(row['category']), -row['waiting_seconds']))
    counts = {name: sum(row['category'] == name for row in rows) for name in CATEGORIES}
    return {'generated_at': now, 'owner_actor': owner_actor, 'counts': counts, 'rows': rows[:MAX_ROWS],
            'truncated': len(rows) > MAX_ROWS,
            'limitations': ['States are declared, not observed execution.',
                            'A decision appears only when explicitly handed to the owner actor; owning or blocking a task does not ask for one.',
                            'A verdict is the newest recorded result check, not a new look at the disk.']}
