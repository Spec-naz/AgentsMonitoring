import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from foreman import decide, load_catalogue, monitor, read_tasks


A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'
OTHER = '33333333-3333-4333-8333-333333333333'


def row(tid=A, **changes):
    return {'id': tid, 'owner': 'kodak', 'state': 'queued', 'phase': 'work',
            'target': None, 'purpose': None, **changes}


class ForemanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'ledger.sqlite'
        self.pause = self.root / 'PAUSED'
        self.path = self.root / 'catalogue.json'
        self.catalogue = {'schema': 'agent-foreman-catalogue-v1', 'coordinator': 'kodak',
                          'tasks': [{'id': A, 'worker': 'klodak', 'depends_on': []},
                                    {'id': B, 'worker': 'klodak', 'depends_on': [A]}]}
        self.path.write_text(json.dumps(self.catalogue))
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,owner TEXT,state TEXT,phase TEXT,target TEXT,purpose TEXT)')

    def write_rows(self, *rows):
        with sqlite3.connect(self.db) as con:
            con.execute('DELETE FROM tasks')
            con.executemany('INSERT INTO tasks VALUES(:id,:owner,:state,:phase,:target,:purpose)', rows)

    def call(self):
        return monitor(self.db, self.path, self.pause)

    def events(self, *rows):
        return decide(self.catalogue, {r['id']: r for r in rows})['events']

    def test_prepared_coordinator_task_requests_offer_not_self_claim(self):
        event = self.events(row())[0]
        self.assertEqual(event, {'kind': 'prepare_offer', 'task_id': A, 'from': 'kodak', 'to': 'klodak'})

    def test_work_offer_is_actionable_only_with_correct_route_and_purpose(self):
        self.assertEqual(self.events(row(state='handoff_pending', target='klodak', purpose='work'))[0]['kind'], 'work_handoff')
        self.assertEqual(self.events(row(state='handoff_pending', target='other', purpose='work')), [])
        self.assertEqual(self.events(row(state='handoff_pending', target='klodak', purpose='review')), [])

    def test_worker_owned_queued_work_can_start(self):
        self.assertEqual(self.events(row(owner='klodak'))[0]['kind'], 'work_owned')

    def test_worker_busy_running_or_review_even_outside_catalogue_suppresses_work(self):
        for state in ['running', 'review']:
            with self.subTest(state=state):
                self.assertEqual(self.events(row(state='handoff_pending', target='klodak', purpose='work'),
                                             row(OTHER, owner='klodak', state=state)), [])

    def test_while_busy_prepared_work_changes_emit_same_empty_output(self):
        busy = row(OTHER, owner='klodak', state='running')
        self.assertEqual(self.events(row(), busy), self.events(row(state='handoff_pending', target='klodak', purpose='work'), busy))

    def test_review_to_coordinator_remains_visible_when_worker_busy(self):
        events = self.events(row(owner='klodak', state='handoff_pending', target='kodak', purpose='review'),
                             row(OTHER, owner='klodak', state='running'))
        self.assertEqual(events, [{'kind': 'review_pending', 'task_id': A, 'to': 'kodak'}])

    def test_accepted_coordinator_review_event(self):
        self.assertEqual(self.events(row(state='review', phase='review'))[0]['kind'], 'review_owned')

    def test_other_review_destination_does_not_wake_coordinator(self):
        self.assertEqual(self.events(row(state='handoff_pending', target='michal', purpose='review')), [])

    def test_dependency_requires_done_not_offered_cancelled_missing(self):
        for state in ['queued', 'blocked', 'handoff_pending', 'review', 'cancelled']:
            events = self.events(row(state=state), row(B))
            self.assertFalse(any(event['task_id'] == B for event in events), state)
        self.assertEqual(self.events(row(B)), [])
        self.assertEqual(self.events(row(state='done'), row(B))[0]['task_id'], B)

    def test_terminal_blocked_tasks_not_dispatched(self):
        for state in ['done', 'cancelled', 'blocked']:
            self.assertEqual(self.events(row(state=state)), [])

    def test_unapproved_tasks_never_selected(self):
        self.assertEqual(self.events(row(OTHER)), [])

    def test_one_candidate_per_worker(self):
        self.catalogue['tasks'][1]['depends_on'] = []
        self.assertEqual([x['task_id'] for x in self.events(row(), row(B))], [A])

    def test_owned_queued_precedes_another_offer(self):
        self.catalogue['tasks'][1]['depends_on'] = []
        event = self.events(row(state='handoff_pending', target='klodak', purpose='work'), row(B, owner='klodak'))
        self.assertEqual(event[0]['task_id'], B)
        self.assertEqual(len(event), 1)

    def test_review_does_not_block_unrelated_next_task(self):
        self.catalogue['tasks'][1]['depends_on'] = []
        events = self.events(row(owner='klodak', state='handoff_pending', target='kodak', purpose='review'), row(B))
        self.assertEqual([e['kind'] for e in events], ['review_pending', 'prepare_offer'])

    def test_pause_and_dangling_pause_produce_no_events_even_missing_db(self):
        self.db.unlink()
        self.pause.touch()
        self.assertEqual(self.call()['events'], [])
        self.pause.unlink()
        self.pause.symlink_to(self.root / 'missing')
        self.assertEqual(self.call()['events'], [])

    def test_missing_db_not_created_and_error_generic(self):
        self.db.unlink()
        result = self.call()
        self.assertFalse(self.db.exists())
        self.assertEqual(result['events'], [{'kind': 'monitor_unavailable'}])
        self.assertNotIn(str(self.root), json.dumps(result))

    def test_readonly_repeat_preserves_database_bytes_and_schema(self):
        self.write_rows(row())
        before = self.db.read_bytes()
        first = self.call()
        self.assertEqual(self.call(), first)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(list(read_tasks(self.db)), [A])

    def test_output_excludes_extra_private_fields_versions_and_clock(self):
        original = row()
        noisy = {**original, 'version': 99, 'progress': 900, 'title': 'FAKE_PRIVATE',
                 'prompt': 'FAKE_SECRET', 'artifact': '/private/user/file'}
        self.assertEqual(self.events(original), self.events(noisy))
        output = json.dumps(self.events(noisy))
        for forbidden in ['FAKE', 'private', 'version', 'progress', 'prompt', 'artifact']:
            self.assertNotIn(forbidden, output)

    def test_database_only_queries_selected_metadata(self):
        with sqlite3.connect(self.db) as con:
            con.execute('ALTER TABLE tasks ADD COLUMN prompt TEXT')
            con.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?)', (A, 'kodak', 'queued', 'work', None, None, 'FAKE_SECRET'))
        self.assertNotIn('FAKE_SECRET', json.dumps(read_tasks(self.db)))
        self.assertNotIn('FAKE_SECRET', json.dumps(self.call()))

    def test_semantic_transition_sequence_changes_but_progress_sequence_does_not(self):
        self.write_rows(row())
        with sqlite3.connect(self.db) as con:
            con.execute('CREATE TABLE events(seq INTEGER PRIMARY KEY,task_id TEXT,action TEXT)')
            con.execute('INSERT INTO events VALUES(1,?,?)', (A, 'offer'))
        first = self.call()
        self.assertEqual(first['events'][0]['transition_seq'], 1)
        with sqlite3.connect(self.db) as con:
            con.execute('INSERT INTO events VALUES(2,?,?)', (A, 'progress'))
        self.assertEqual(self.call(), first)
        with sqlite3.connect(self.db) as con:
            con.execute('INSERT INTO events VALUES(3,?,?)', (A, 'offer'))
        self.assertEqual(self.call()['events'][0]['transition_seq'], 3)

    def test_strict_catalogue_rejects_duplicate_unknown_and_external_dependencies(self):
        cases = []
        duplicate = copy.deepcopy(self.catalogue)
        duplicate['tasks'].append(copy.deepcopy(duplicate['tasks'][0]))
        cases.append(duplicate)
        unknown = copy.deepcopy(self.catalogue)
        unknown['tasks'][0]['prompt'] = 'untrusted instruction'
        cases.append(unknown)
        external = copy.deepcopy(self.catalogue)
        external['tasks'][1]['depends_on'] = [OTHER]
        cases.append(external)
        for value in cases:
            self.path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                load_catalogue(self.path)
            self.assertEqual(self.call()['events'], [{'kind': 'monitor_unavailable'}])


if __name__ == '__main__':
    unittest.main()
