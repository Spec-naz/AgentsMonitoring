"""LC-02: a real create, start, handover, return, review and done, read back from one timeline, through the real CLI."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
import uuid

from agentops import metadata, provider_signals as ps, timeline as t
from agentops.ledger import Conflict, Ledger
from agentops.review_queue import build as queue

ROOT = Path(__file__).parents[1]
SECRET = 'FAKE-SECRET-7f3a9c'


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.db = self.root / 'tasks.sqlite'
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), '--config', str(self.root / 'config.json'), *args],
                              env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True, text=True, timeout=60)

    def view(self, task, config=None, now=None):
        return t.build(self.db, task, config, now)

    def run_event(self, task, run_id=None, **change):
        record = {'task_id': task, 'run_id': run_id or str(uuid.uuid4()), 'parent_id': None, 'at': time.time(), 'source': 'operator',
                  'evidence': 'declared', 'actor': 'autor', 'action': 'start', 'outcome': 'unknown', 'error_code': None, 'usage_tokens': None, **change}
        return self.ledger.record_run_event(record)

    def running(self, owner='autor'):
        task = self.ledger.create('nazev ' + SECRET, owner, owner)['id']
        self.ledger.change(task, owner, 'start')
        return task

    def code_list(self, view):
        return [row['code'] for row in view['problems']]


class FullPass(Base):
    """create → start → handover → return → repair → review → done, all through the CLI."""

    def step(self, *args):
        run = self.cli(*args)
        self.assertEqual(run.returncode, 0, args[0] + run.stderr)
        return json.loads(run.stdout)

    def test_whole_life_is_in_one_timeline(self):
        evidence = self.root / 'vystup.md'
        evidence.write_text('obsah')
        task = self.step('create', '--title', 'Ukol ' + SECRET, '--owner', 'autor', '--actor', 'koordinator')['id']
        self.step('start', task, '--actor', 'autor')
        self.step('progress', task, '--actor', 'autor', '--note', 'poznamka ' + SECRET)
        self.step('offer', task, '--actor', 'autor', '--to', 'kontrolor', '--purpose', 'review', '--artifact', str(evidence))
        self.step('accept', task, '--actor', 'kontrolor')
        self.step('offer', task, '--actor', 'kontrolor', '--to', 'autor', '--purpose', 'work', '--artifact', str(self.root / 'nalezy.md'))
        self.step('accept', task, '--actor', 'autor')
        self.step('start', task, '--actor', 'autor')
        self.step('offer', task, '--actor', 'autor', '--to', 'kontrolor', '--purpose', 'review', '--artifact', str(evidence))
        self.step('accept', task, '--actor', 'kontrolor')
        self.step('done', task, '--actor', 'kontrolor', '--artifact', str(evidence))
        run = self.cli('timeline', task)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn(SECRET, run.stdout + run.stderr)
        view = json.loads(run.stdout)
        actions = [entry['action'] for entry in view['entries']]
        self.assertEqual(actions, ['create', 'start', 'progress', 'offer', 'accept', 'offer', 'accept', 'start', 'offer', 'accept', 'done'])
        self.assertEqual(view['milestones']['first_start']['actor'], 'autor')
        self.assertEqual(view['milestones']['first_handover']['seq'], view['entries'][3]['seq'])
        self.assertEqual(view['milestones']['returns'], 1)
        self.assertEqual(view['milestones']['done']['actor'], 'kontrolor')
        self.assertEqual(view['belongs_to'], 'history')
        self.assertEqual(self.code_list(view), ['returned_after_review'])
        self.assertEqual(view['problems'][0]['by'], 'kontrolor')
        self.assertTrue(all(entry['evidence'] == 'declared' for entry in view['entries']))
        self.assertIn(str(self.root / 'nalezy.md'), [entry.get('artifact') for entry in view['entries']])
        self.assertEqual(queue(self.ledger)['rows'], [])  # a finished task belongs to history

    def test_nothing_claims_execution_from_assignment_or_a_start(self):
        task = self.running()
        view = self.view(task)
        self.assertEqual(view['execution']['observed_runs'], 0)
        self.assertEqual(view['runs'], [])
        self.assertIsNone(view['usage']['known_tokens'])
        self.assertEqual(view['entries'][1]['evidence'], 'declared')
        self.assertIn('declaration', view['execution']['note'])


class Runs(Base):
    def test_child_run_is_linked_and_separate_from_the_task(self):
        task = self.running()
        root_run, child = str(uuid.uuid4()), str(uuid.uuid4())
        self.run_event(task, root_run, evidence='observed', source='agentops', outcome='ok', usage_tokens=100)
        self.run_event(task, child, parent_id=root_run, evidence='observed', source='agentops', outcome='ok', usage_tokens=50)
        runs = {run['run_id']: run for run in self.view(task)['runs']}
        self.assertEqual((runs[root_run]['relation'], runs[child]['relation'], runs[child]['parent_id']), ('root', 'child', root_run))
        self.assertNotIn(task, runs)
        self.assertEqual(self.view(task)['execution']['observed_runs'], 2)

    def test_parent_missing_is_named_not_assumed(self):
        task = self.running()
        run = self.run_event(task, parent_id=str(uuid.uuid4()))['run_id']
        view = self.view(task)
        self.assertEqual(view['runs'][0]['relation'], 'parent_not_recorded')
        self.assertIn({'code': 'parent_run_not_recorded', 'run_id': run}, view['problems'])

    def test_unknown_usage_is_not_zero(self):
        task = self.running()
        self.run_event(task, usage_tokens=None, outcome='ok')
        usage = self.view(task)['usage']
        self.assertIsNone(usage['known_tokens'])
        self.assertEqual((usage['runs_with_known_usage'], usage['runs_with_unknown_usage']), (0, 1))

    def test_known_zero_stays_a_known_zero_and_mixes_honestly(self):
        task = self.running()
        self.run_event(task, usage_tokens=0, outcome='ok')
        self.assertEqual(self.view(task)['usage']['known_tokens'], 0)
        self.run_event(task, usage_tokens=None, outcome='ok')
        self.run_event(task, usage_tokens=7, outcome='ok')
        usage = self.view(task)['usage']
        self.assertEqual((usage['known_tokens'], usage['runs_with_known_usage'], usage['runs_with_unknown_usage']), (7, 2, 1))

    def test_outcome_is_the_last_stated_and_unknown_offers_no_repeat(self):
        task = self.running()
        run = str(uuid.uuid4())
        self.run_event(task, run, action='start', outcome='unknown')
        self.assertEqual(self.view(task)['problems'], [{'code': 'run_outcome_unknown', 'run_id': run, 'repeat_offered': False}])
        self.run_event(task, run, action='end', outcome='ok')
        view = self.view(task)
        self.assertEqual((view['runs'][0]['events'], view['runs'][0]['outcome'], view['problems']), (2, 'ok', []))

    def test_failed_run_has_a_code_and_offers_no_repeat(self):
        task = self.running()
        self.run_event(task, outcome='failed', error_code='timeout')
        problem = self.view(task)['problems'][0]
        self.assertEqual((problem['code'], problem['error_code'], problem['repeat_offered']), ('run_failed', 'timeout', False))

    def test_a_declared_step_makes_the_run_declared(self):
        task = self.running()
        run = str(uuid.uuid4())
        self.run_event(task, run, evidence='observed')
        self.run_event(task, run, evidence='declared', action='end', outcome='ok')
        view = self.view(task)
        self.assertEqual(view['runs'][0]['evidence'], 'declared')
        self.assertEqual(view['execution']['observed_runs'], 0)

    def test_run_events_are_ordered_into_the_timeline(self):
        task = self.running()
        self.run_event(task, at=time.time() + 100, outcome='ok')
        entries = self.view(task)['entries']
        self.assertEqual([entry['kind'] for entry in entries], ['ledger', 'ledger', 'run'])
        self.assertEqual(entries[-1]['outcome'], 'ok')

    def test_run_events_do_not_change_the_task(self):
        task = self.running()
        before = self.ledger.get(task), self.ledger.events(task)
        self.run_event(task)
        self.assertEqual(before, (self.ledger.get(task), self.ledger.events(task)))

    def test_run_events_are_append_only(self):
        self.run_event(self.running())
        for sql in ('DELETE FROM run_events', "UPDATE run_events SET actor='x'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.db.execute(sql)

    def test_invalid_records_are_refused_without_repeating_the_value(self):
        task = self.running()
        for change in ({'prompt': SECRET}, {'action': SECRET}, {'error_code': SECRET + ' text'}, {'usage_tokens': -1}, {'usage_tokens': '5'},
                       {'evidence': 'proved'}, {'parent_id': SECRET}, {'run_id': SECRET}):
            with self.subTest(change=str(change)[:30]), self.assertRaises(ValueError) as caught:
                self.run_event(task, **change)
            self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(self.view(task)['runs'], [])

    def test_unknown_task_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_event(str(uuid.uuid4()))

    def test_request_id_records_once(self):
        task = self.running()
        record = {'task_id': task, 'run_id': str(uuid.uuid4()), 'parent_id': None, 'at': 5.0, 'source': 'operator', 'evidence': 'declared', 'actor': 'autor',
                  'action': 'start', 'outcome': 'unknown', 'error_code': None, 'usage_tokens': None}
        self.ledger.record_run_event(record, 'r1')
        self.ledger.record_run_event(record, 'r1')
        self.assertEqual(self.view(task)['runs'][0]['events'], 1)

    def test_cli_new_run_and_child(self):
        task = self.running()
        first = json.loads(self.cli('run-event', task, '--actor', 'autor', '--action', 'start', '--source', 'operator', '--evidence', 'declared').stdout)
        run = self.cli('run-event', task, '--actor', 'autor', '--action', 'start', '--source', 'operator', '--evidence', 'declared', '--parent-id', first['run_id'], '--usage-tokens', '9', '--outcome', 'ok')
        self.assertEqual(run.returncode, 0, run.stderr)
        view = json.loads(self.cli('timeline', task).stdout)
        self.assertEqual(sorted(row['relation'] for row in view['runs']), ['child', 'root'])
        self.assertIsNone(first['usage_tokens'])

    def test_metadata_export_keeps_to_the_contract(self):
        task = self.running()
        self.run_event(task, error_code='timeout', outcome='failed')
        export = metadata.build_export(self.db, self.root / 'signaly')
        self.assertEqual(export['counts']['runs'], 1)
        self.assertEqual(set(export['runs'][0]), set(metadata.RECORDS['run_event']))
        self.assertNotIn(SECRET, json.dumps(export))

    def test_old_ledger_without_the_table_reads_as_not_available(self):
        task = self.running()
        self.ledger.close()
        connection = sqlite3.connect(self.db)
        connection.executescript('DROP TRIGGER run_events_no_update; DROP TRIGGER run_events_no_delete; DROP TABLE run_events;')
        connection.commit()
        connection.close()
        view = self.view(task)
        self.assertEqual((view['execution']['run_records'], view['runs']), ('not_available', []))
        self.assertEqual(metadata.build_export(self.db, self.root / 'signaly')['counts']['runs'], 0)
        self.ledger = Ledger(self.db)

    def test_opening_an_old_ledger_adds_the_table_and_keeps_history(self):
        task = self.running()
        before = self.ledger.events(task)
        self.ledger.close()
        connection = sqlite3.connect(self.db)
        connection.executescript('DROP TRIGGER run_events_no_update; DROP TRIGGER run_events_no_delete; DROP TABLE run_events;')
        connection.commit()
        connection.close()
        self.ledger = Ledger(self.db)
        self.assertEqual(before, self.ledger.events(task))
        self.run_event(task)
        self.assertEqual(self.view(task)['execution']['run_records'], 'available')


class RunIdentity(Base):
    def test_self_parent_is_refused(self):
        task = self.running()
        run = str(uuid.uuid4())
        with self.assertRaises(Conflict):
            self.run_event(task, run, parent_id=run)
        self.assertEqual(self.view(task)['runs'], [])

    def test_parent_cannot_change_and_a_repeat_of_the_same_parent_is_fine(self):
        task = self.running()
        run, parent = str(uuid.uuid4()), str(uuid.uuid4())
        self.run_event(task, run, parent_id=parent)
        self.run_event(task, run, parent_id=parent, action='end', outcome='ok')
        for other in (str(uuid.uuid4()), None):
            with self.assertRaises(Conflict):
                self.run_event(task, run, parent_id=other, action='end', outcome='ok')
        self.assertEqual(self.view(task)['runs'][0]['events'], 2)

    def test_root_cannot_gain_a_parent_later(self):
        task = self.running()
        run = str(uuid.uuid4())
        self.run_event(task, run)
        with self.assertRaises(Conflict):
            self.run_event(task, run, parent_id=str(uuid.uuid4()))

    def test_run_id_cannot_move_to_another_task(self):
        first, second = self.running(), self.running()
        run = str(uuid.uuid4())
        self.run_event(first, run)
        with self.assertRaises(Conflict):
            self.run_event(second, run)
        self.assertEqual(self.view(second)['runs'], [])

    def test_cycle_is_refused_also_through_a_not_yet_recorded_parent(self):
        task = self.running()
        one, two, three = (str(uuid.uuid4()) for _ in range(3))
        self.run_event(task, one, parent_id=two)
        with self.assertRaises(Conflict):
            self.run_event(task, two, parent_id=one)
        self.run_event(task, two, parent_id=three)
        with self.assertRaises(Conflict):
            self.run_event(task, three, parent_id=one)
        self.run_event(task, three)
        self.assertEqual({row['run_id']: row['relation'] for row in self.view(task)['runs']}, {one: 'child', two: 'child', three: 'root'})

    def test_parent_not_recorded_yet_stays_possible(self):
        task = self.running()
        run, parent = str(uuid.uuid4()), str(uuid.uuid4())
        self.run_event(task, run, parent_id=parent)
        self.assertEqual(self.view(task)['runs'][0]['relation'], 'parent_not_recorded')
        self.run_event(task, parent, outcome='ok')
        self.assertEqual({row['run_id']: row['relation'] for row in self.view(task)['runs']}, {run: 'child', parent: 'root'})

    def test_parent_in_another_task_is_named(self):
        first, second = self.running(), self.running()
        parent = str(uuid.uuid4())
        self.run_event(first, parent, outcome='ok')
        self.run_event(second, parent_id=parent, outcome='ok')
        view = self.view(second)
        self.assertEqual((view['runs'][0]['relation'], view['problems']), ('parent_in_other_task', []))

    def insert(self, task, run, parent):
        connection = sqlite3.connect(self.db)
        connection.execute('INSERT INTO run_events(task_id,run_id,parent_id,at,source,evidence,actor,action,outcome) VALUES(?,?,?,?,?,?,?,?,?)',
                           (task, run, parent, 1.0, 'operator', 'declared', 'autor', 'start', 'ok'))
        connection.commit()
        connection.close()

    def test_records_written_past_the_rules_read_as_invalid(self):
        task = self.running()
        selfish, changing = str(uuid.uuid4()), str(uuid.uuid4())
        self.insert(task, selfish, selfish)
        self.insert(task, changing, str(uuid.uuid4()))
        self.insert(task, changing, str(uuid.uuid4()))
        view = self.view(task)
        runs = {row['run_id']: row for row in view['runs']}
        self.assertEqual((runs[selfish]['relation'], runs[selfish]['parent_id']), ('invalid', None))
        self.assertEqual((runs[changing]['relation'], runs[changing]['parent_id']), ('invalid', None))
        self.assertLessEqual({'run_parent_is_itself', 'run_parent_changed'}, set(self.code_list(view)))

    def test_same_run_under_two_tasks_written_past_the_rules_is_a_problem(self):
        first, second = self.running(), self.running()
        run = str(uuid.uuid4())
        self.insert(first, run, None)
        self.insert(second, run, None)
        self.assertIn('run_id_in_other_task', self.code_list(self.view(first)))


class RunEventCli(Base):
    def event(self, task, *extra):
        return self.cli('run-event', task, '--actor', 'autor', '--action', 'start', '--source', 'operator', '--evidence', 'declared', *extra)

    def test_identical_request_with_a_given_run_id_replays_the_original(self):
        task = self.running()
        args = ('--run-id', str(uuid.uuid4()), '--request-id', 'pozadavek-1')
        first = self.event(task, *args)
        time.sleep(0.01)
        second = self.event(task, *args)
        self.assertEqual((first.returncode, second.returncode), (0, 0), second.stderr)
        self.assertEqual(json.loads(first.stdout), json.loads(second.stdout))
        self.assertEqual(self.view(task)['runs'][0]['events'], 1)

    def test_identical_request_without_a_run_id_returns_the_same_run(self):
        task = self.running()
        first, second = self.event(task, '--request-id', 'pozadavek-2'), self.event(task, '--request-id', 'pozadavek-2')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(first.stdout)['run_id'], json.loads(second.stdout)['run_id'])
        self.assertEqual(json.loads(first.stdout)['at'], json.loads(second.stdout)['at'])
        self.assertEqual(len(self.view(task)['runs']), 1)

    def test_different_content_under_the_same_request_id_fails(self):
        task = self.running()
        run = str(uuid.uuid4())
        self.assertEqual(self.event(task, '--run-id', run, '--request-id', 'r').returncode, 0)
        for extra, other in ((('--run-id', run, '--outcome', 'ok'), task), (('--run-id', str(uuid.uuid4())), task), (('--run-id', run), self.running()), ((), task)):
            result = self.event(other, *extra, '--request-id', 'r')
            self.assertEqual(result.returncode, 1, extra)
            self.assertIn('Request id reused', result.stderr)
        self.assertEqual(self.view(task)['runs'][0]['events'], 1)

    def test_without_a_request_id_each_call_is_its_own_event(self):
        task = self.running()
        run = str(uuid.uuid4())
        self.event(task, '--run-id', run)
        self.event(task, '--run-id', run)
        self.assertEqual(self.view(task)['runs'][0]['events'], 2)

    def test_identity_errors_are_short_and_keep_the_exit_code(self):
        task = self.running()
        run = str(uuid.uuid4())
        result = self.event(task, '--run-id', run, '--parent-id', run)
        self.assertEqual((result.returncode, result.stderr.strip()), (1, 'AgentOps: A run cannot be its own parent'))


class ExistingEmptyTable(Base):
    """The live ledger already holds the empty table and its triggers from before this was deployed."""

    def test_reopening_with_the_empty_table_keeps_it_and_it_works(self):
        task = self.running()
        self.ledger.close()
        self.ledger = Ledger(self.db)
        schema = sqlite3.connect(self.db).execute("SELECT type,name FROM sqlite_master WHERE name LIKE 'run_events%' ORDER BY name").fetchall()
        self.assertEqual(schema, [('table', 'run_events'), ('trigger', 'run_events_no_delete'), ('trigger', 'run_events_no_update')])
        self.run_event(task, outcome='ok')
        self.assertEqual(len(self.view(task)['runs']), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.db.execute('DELETE FROM run_events')


class ReportLocation(Base):
    def setUp(self):
        super().setUp()
        self.private = self.root / '.ssh'
        self.private.mkdir()
        self.data = b'{}'
        self.sha = hashlib.sha256(self.data).hexdigest()
        (self.private / 'report.json').write_bytes(self.data)
        self.alias = self.root / 'alias'
        self.alias.symlink_to(self.private, target_is_directory=True)

    def test_folder_alias_to_an_excluded_place_is_not_opened(self):
        real = os.open
        def watching(path, *args, **kwargs):
            self.assertNotIn('report.json', str(path))
            return real(path, *args, **kwargs)
        with mock.patch.object(t.os, 'open', watching):
            self.assertEqual(t._report_check(str(self.alias / 'report.json'), self.sha), 'not_checked')

    def test_direct_excluded_path_and_alias_chain(self):
        second = self.root / 'druhy'
        second.symlink_to(self.alias, target_is_directory=True)
        self.assertEqual(t._report_check(str(self.private / 'report.json'), self.sha), 'not_checked')
        self.assertEqual(t._report_check(str(second / 'report.json'), self.sha), 'not_checked')

    def test_path_written_through_an_excluded_name_is_not_read_even_if_it_leads_out(self):
        public = self.root / 'bezna'
        public.mkdir()
        (public / 'r.json').write_bytes(self.data)
        (self.private / 'ven').symlink_to(public, target_is_directory=True)
        self.assertEqual(t._report_check(str(self.private / 'ven/r.json'), self.sha), 'not_checked')

    def test_ordinary_alias_and_honest_statuses_stay(self):
        public = self.root / 'bezna'
        public.mkdir()
        (public / 'r.json').write_bytes(self.data)
        link = self.root / 'odkaz'
        link.symlink_to(public, target_is_directory=True)
        self.assertEqual(t._report_check(str(link / 'r.json'), self.sha), 'matches')
        self.assertEqual(t._report_check(str(link / 'r.json'), 'a' * 64), 'differs')
        self.assertEqual(t._report_check(str(link / 'neni.json'), self.sha), 'missing')

    def test_excluded_alias_does_not_surface_a_problem_as_if_read(self):
        task = self.running()
        self.ledger.record_postcheck(task, 'autor', 'pass', str(self.alias / 'report.json'), self.sha)
        view = self.view(task)
        entry = next(entry for entry in view['entries'] if entry['action'] == 'postcheck')
        self.assertEqual((entry['report_check'], view['problems']), ('not_checked', []))


class Problems(Base):
    def test_each_problem_points_to_its_entry(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'block', note='duvod ' + SECRET)
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'offer', target='k', purpose='review', artifact='/a.md')
        self.ledger.change(task, 'autor', 'withdraw', note='stazeno ' + SECRET)
        view = self.view(task)
        seq = {entry['action']: entry['seq'] for entry in view['entries']}
        self.assertEqual(view['problems'], [{'code': 'declared_blocked', 'seq': seq['block']}, {'code': 'handover_withdrawn', 'seq': seq['withdraw']}])
        self.assertNotIn(SECRET, json.dumps(view))

    def test_first_assignment_is_not_a_return(self):
        """Found on the real ledger: a coordinator assigning the work to its later author counted as a return."""
        task = self.running('koordinator')
        self.ledger.change(task, 'koordinator', 'offer', target='autor', purpose='work', artifact='/zadani.md')
        self.ledger.change(task, 'autor', 'accept')
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'offer', target='koordinator', purpose='review', artifact='/vystup.md')
        self.ledger.change(task, 'koordinator', 'accept')
        view = self.view(task)
        self.assertEqual((view['milestones']['returns'], view['problems']), (0, []))
        self.ledger.change(task, 'koordinator', 'offer', target='autor', purpose='work', artifact='/nalezy.md')
        view = self.view(task)
        self.assertEqual((view['milestones']['returns'], self.code_list(view)), (1, ['returned_after_review']))

    def test_own_handover_is_not_a_return(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='k', purpose='review', artifact='/a.md')
        self.ledger.change(task, 'k', 'accept')
        self.ledger.change(task, 'k', 'offer', target='treti', purpose='work', artifact='/b.md')
        view = self.view(task)
        self.assertEqual((view['milestones']['returns'], view['problems']), (0, []))

    def report(self, content=b'{}'):
        path = self.root / 'report.json'
        path.write_bytes(content)
        return path, hashlib.sha256(content).hexdigest()

    def check_entry(self, view):
        return next(entry for entry in view['entries'] if entry['action'] == 'postcheck')

    def test_report_check_matches_differs_and_missing(self):
        task = self.running()
        path, sha = self.report()
        self.ledger.record_postcheck(task, 'autor', 'pass', str(path), sha)
        view = self.view(task)
        self.assertEqual((self.check_entry(view)['report_check'], view['problems']), ('matches', []))
        path.write_bytes(b'zmeneno')
        view = self.view(task)
        self.assertEqual((self.check_entry(view)['report_check'], self.code_list(view)), ('differs', ['report_differs']))
        path.unlink()
        self.assertEqual(self.code_list(self.view(task)), ['report_missing'])

    def test_failed_and_unverified_result_checks_are_problems(self):
        task = self.running()
        path, sha = self.report()
        self.ledger.record_postcheck(task, 'autor', 'fail', str(path), sha)
        self.ledger.record_postcheck(task, 'autor', 'unverified', str(path), sha)
        self.assertEqual(self.code_list(self.view(task)), ['result_check_fail', 'result_check_unverified'])

    def test_report_in_an_excluded_place_or_symlink_is_not_read(self):
        task = self.running()
        real = self.root / 'real.json'
        real.write_text('{}')
        link = self.root / 'odkaz.json'
        link.symlink_to(real)
        self.assertEqual(t._report_check(str(link), hashlib.sha256(b'{}').hexdigest()), 'not_checked')
        self.assertEqual(t._report_check('/home/x/DANĚ/r.json', 'a' * 64), 'not_checked')
        self.assertEqual(t._report_check('/x/r?token=1', 'a' * 64), 'not_checked')
        self.assertTrue(task)

    def test_unreadable_audit_details_are_named(self):
        task = self.running()
        connection = sqlite3.connect(self.db)
        connection.execute('INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)', (task, 9e9, 'autor', 'x', 'neni json ' + SECRET))
        connection.commit()
        connection.close()
        view = self.view(task)
        self.assertEqual(self.code_list(view), ['audit_details_unreadable'])
        self.assertNotIn(SECRET, json.dumps(view))


class Filter(Base):
    def test_no_free_text_in_any_entry(self):
        task = self.ledger.create('nazev ' + SECRET, 'autor', 'autor')['id']
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'progress', note='p ' + SECRET)
        self.ledger.cost(task, 'autor', 100, 'CZK', 'actual', 'zdroj ' + SECRET)
        self.ledger.change(task, 'autor', 'block', note='b ' + SECRET)
        connection = sqlite3.connect(self.db)
        connection.execute('INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)', (task, 9e9, 'autor', 'jina', json.dumps({
            'prompt': SECRET, 'nested': {'x': SECRET}, 'target': SECRET, 'purpose': SECRET, 'verdict': SECRET, 'changes': {'version': SECRET}, 'from_state': [SECRET]})))
        connection.commit()
        connection.close()
        self.assertNotIn(SECRET, json.dumps(self.view(task)))

    def test_reference_that_is_not_a_plain_path_is_withheld(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='k', purpose='review', artifact='/cesta?token=' + SECRET)
        view = self.view(task)
        self.assertNotIn(SECRET, json.dumps(view))
        self.assertIs(view['task']['reference_withheld'], True)
        self.assertIs(view['entries'][-1]['reference_withheld'], True)

    def test_excluded_location_is_withheld(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='k', purpose='review', artifact='/home/x/DANĚ/a.pdf')
        self.assertNotIn('DANĚ', json.dumps(self.view(task), ensure_ascii=False))

    def test_timeline_reads_only(self):
        task = self.running()
        self.run_event(task)
        def state():
            connection = sqlite3.connect(self.db)
            rows = [connection.execute(f'SELECT * FROM {name} ORDER BY 1').fetchall() for name in ('tasks', 'events', 'run_events', 'commands')]
            connection.close()
            return rows
        before = state()
        self.assertEqual(self.cli('timeline', task).returncode, 0)
        self.assertEqual(before, state())

    def test_safe_errors(self):
        marker = 'TAJNA_CESTA'
        (self.root / marker).write_text('neni slozka')
        task = self.running()
        for args, expected in ((['--db', str(self.root / marker / 'x.sqlite'), 'timeline', task], 'AgentOps: Ledger cannot be read'),
                               (['--db', str(self.root / marker / 'x.sqlite'), 'run-event', task, '--actor', 'a', '--action', 'b', '--source', 'operator', '--evidence', 'declared'],
                                'AgentOps: A file for this command could not be read or written')):
            run = subprocess.run([sys.executable, '-m', 'agentops', *args], env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True, text=True, timeout=30)
            self.assertEqual((run.returncode, run.stderr.strip()), (1, expected))
            self.assertNotIn(marker, run.stdout)
        broken = self.root / 'rozbity.sqlite'
        broken.write_bytes(SECRET.encode() * 200)
        with self.assertRaises(ValueError) as caught:
            t.build(broken, task)
        self.assertEqual(str(caught.exception), 'Ledger cannot be read')
        with self.assertRaises(ValueError):
            t.build(self.db, str(uuid.uuid4()))


class ProviderSignal(Base):
    def bind(self, session='sess-1', **extra):
        return {'provider_bindings': {'autor': {'provider': 'claude', 'session_id': session, 'state_dir': str(self.root / 'signaly'), 'max_age_seconds': 900, **extra}}}

    def feed(self, at, session='sess-1', event='UserPromptSubmit', actor='autor'):
        ps.ingest('claude', json.dumps({'hook_event_name': event, 'session_id': session, 'prompt': SECRET}).encode(), self.root / 'signaly', at, actor)

    def test_no_binding_is_not_assigned(self):
        signal = self.view(self.running())['provider_signal']
        self.assertEqual((signal['status'], signal['proves_task_progress'], signal['about']), ('not_assigned', False, 'actor_turn_not_task'))

    def test_bound_fresh_signal_is_current_and_still_no_proof_of_progress(self):
        self.feed(1000.0)
        view = self.view(self.running(), self.bind(), now=1010.0)
        signal = view['provider_signal']
        self.assertEqual((signal['status'], signal['state'], signal['evidence'], signal['proves_task_progress']), ('current', 'turn_running', 'observed', False))
        self.assertEqual(view['execution']['observed_runs'], 0)
        self.assertNotIn(SECRET, json.dumps(view))

    def test_stale_signal_is_named(self):
        self.feed(1000.0)
        signal = self.view(self.running(), self.bind(), now=1000.0 + 5000)['provider_signal']
        self.assertEqual((signal['status'], signal['reason'], signal['evidence']), ('stale', 'running_without_end_signal', None))

    def test_signal_of_another_actor_or_session_is_not_attributed(self):
        self.feed(1000.0, actor='jiny')
        self.assertEqual(self.view(self.running(), self.bind(), now=1010.0)['provider_signal']['status'], 'not_assigned')
        self.feed(1000.0, session='jina-session')
        self.assertEqual(self.view(self.running(), self.bind(), now=1010.0)['provider_signal']['status'], 'not_assigned')

    def test_broken_binding_is_not_assigned(self):
        for config in ({'provider_bindings': []}, {'provider_bindings': {'autor': 'x'}}, {'provider_bindings': {'autor': {'provider': 'jiny', 'session_id': 's'}}},
                       {'provider_bindings': {'autor': {'provider': 'claude', 'session_id': 5}}}):
            self.assertEqual(self.view(self.running(), config)['provider_signal']['status'], 'not_assigned')

    def test_signal_never_changes_the_task(self):
        task = self.running()
        self.feed(1000.0, event='Stop')
        view = self.view(task, self.bind(), now=1010.0)
        self.assertEqual((view['task']['state'], view['milestones']['done']), ('running', None))


class QueueLink(Base):
    def row(self, task):
        return next(row for row in queue(self.ledger)['rows'] if row['id'] == task)

    def in_review(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='k', purpose='review', artifact='/a.md')
        return task

    def test_queue_links_the_timeline_and_summarises_runs(self):
        task = self.in_review()
        self.assertEqual(self.row(task)['timeline'], 'agentops timeline ' + task)
        self.assertEqual(self.row(task)['runs'], {'runs': 0, 'unclear': 0, 'unknown_usage': 0})
        self.run_event(task, outcome='unknown')
        self.run_event(task, outcome='ok', usage_tokens=5)
        self.assertEqual(self.row(task)['runs'], {'runs': 2, 'unclear': 1, 'unknown_usage': 1})


if __name__ == '__main__':
    unittest.main()
