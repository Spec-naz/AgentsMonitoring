"""LC-03: real ledger flows, the real CLI, and the dashboard table fed by a real snapshot."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

from agentops.ledger import Conflict, Ledger
from agentops.observe import snapshot
from agentops.review_queue import build

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('queue_plugin', ROOT / 'monitor/plugin.py')
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / 'tasks.sqlite'
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)

    def task(self, owner='autor'):
        return self.ledger.create('ukol', owner, owner)['id']

    def running(self, owner='autor'):
        task = self.task(owner)
        self.ledger.change(task, owner, 'start')
        return task

    def in_review(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/vystup.md')
        self.ledger.change(task, 'kontrolor', 'accept')
        return task

    def returned(self):
        task = self.in_review()
        self.ledger.change(task, 'kontrolor', 'offer', target='autor', purpose='work', artifact='/nalezy.md')
        return task

    def row(self, task):
        rows = [row for row in build(self.ledger)['rows'] if row['id'] == task]
        return rows[0] if rows else None


class Categories(Base):
    def test_ordinary_work_is_not_in_the_queue(self):
        self.assertIsNone(self.row(self.task()))
        self.assertIsNone(self.row(self.running()))

    def test_finished_and_cancelled_are_not_in_the_queue(self):
        task = self.in_review()
        self.ledger.change(task, 'kontrolor', 'done', artifact='/prijeti.md')
        self.assertIsNone(self.row(task))
        other = self.running()
        self.ledger.change(other, 'autor', 'cancel', note='zruseno')
        self.assertIsNone(self.row(other))
        handed = self.running()  # would read as a decision if closed tasks were not filtered first
        self.ledger.change(handed, 'autor', 'offer', target='michal', purpose='work', artifact='/a.md')
        self.ledger.change(handed, 'michal', 'accept')
        self.ledger.change(handed, 'michal', 'cancel', note='zruseno')
        self.assertIsNone(self.row(handed))

    def test_plain_work_handover_is_a_takeover(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kolega', purpose='work', artifact='/zadani.md')
        row = self.row(task)
        self.assertEqual((row['category'], row['waiting_on'], row['artifact']), ('takeover', 'kolega', '/zadani.md'))
        self.assertIsNone(row['returned'])

    def test_review_offered_and_in_progress_are_peer_review(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/vystup.md')
        row = self.row(task)
        self.assertEqual((row['category'], row['waiting_on'], row['reviewer_must_not_be']), ('peer_review', 'kontrolor', 'autor'))
        self.ledger.change(task, 'kontrolor', 'accept')
        self.assertEqual(self.row(task)['category'], 'peer_review')

    def test_return_is_rework_with_the_stored_reason_through_the_whole_repair(self):
        task = self.returned()
        row = self.row(task)
        self.assertEqual((row['category'], row['waiting_on']), ('rework', 'autor'))
        self.assertEqual((row['returned']['by'], row['returned']['reason_artifact']), ('kontrolor', '/nalezy.md'))
        self.ledger.change(task, 'autor', 'accept')
        self.assertEqual(self.row(task)['category'], 'rework')
        self.ledger.change(task, 'autor', 'start')
        self.assertEqual(self.row(task)['returned']['reason_artifact'], '/nalezy.md')

    def test_repaired_work_offered_again_is_review_not_rework(self):
        task = self.returned()
        self.ledger.change(task, 'autor', 'accept')
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/oprava.md')
        row = self.row(task)
        self.assertEqual(row['category'], 'peer_review')
        self.assertIsNone(row['returned'])

    def test_handing_work_to_a_third_colleague_after_a_return_is_a_takeover(self):
        task = self.returned()
        self.ledger.change(task, 'autor', 'accept')
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'offer', target='treti', purpose='work', artifact='/predani.md')
        self.assertEqual(self.row(task)['category'], 'takeover')

    def test_own_handover_is_not_a_return(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/v.md')
        self.ledger.change(task, 'kontrolor', 'accept')
        self.ledger.change(task, 'kontrolor', 'offer', target='kolega', purpose='work', artifact='/d.md')
        self.assertEqual(self.row(task)['category'], 'takeover')

    def test_blocked_shows_reason_and_is_not_assumed_to_be_a_decision(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'block', note='ceka to na Michala asi')
        row = self.row(task)
        self.assertEqual((row['category'], row['blocked_reason']), ('blocked', 'ceka to na Michala asi'))

    def test_withdrawn_handover_shows_its_own_reason_not_an_older_block(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'block', note='stary duvod')
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/v.md')
        self.ledger.change(task, 'autor', 'withdraw', note='novy duvod')
        row = self.row(task)
        self.assertEqual((row['category'], row['blocked_reason'], row['blocked_action']), ('blocked', 'novy duvod', 'withdraw'))

    def test_withdrawn_handover_without_earlier_block_has_a_reason(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/v.md')
        self.ledger.change(task, 'autor', 'withdraw', note='jen tento duvod')
        self.assertEqual(self.row(task)['blocked_reason'], 'jen tento duvod')

    def test_newer_block_replaces_withdraw_reason(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/v.md')
        self.ledger.change(task, 'autor', 'withdraw', note='stazeno')
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'block', note='novejsi blok')
        row = self.row(task)
        self.assertEqual((row['blocked_reason'], row['blocked_action']), ('novejsi blok', 'block'))

    def test_old_reason_is_not_shown_after_the_task_continues(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'block', note='stary')
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/v.md')
        self.assertIsNone(self.row(task)['blocked_reason'])

    def test_decision_needs_explicit_handover_to_the_owner_actor(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='michal', purpose='work', artifact='/moznosti.md')
        row = self.row(task)
        self.assertEqual((row['category'], row['waiting_on'], row['artifact']), ('owner_decision', 'michal', '/moznosti.md'))
        self.ledger.change(task, 'michal', 'accept')
        self.assertEqual(self.row(task)['category'], 'owner_decision')

    def test_task_merely_owned_by_the_owner_actor_asks_nothing(self):
        queued = self.task('michal')
        self.assertIsNone(self.row(queued))
        started = self.running('michal')
        self.assertIsNone(self.row(started))
        self.assertEqual(build(self.ledger)['counts']['owner_decision'], 0)

    def test_owner_actor_blocking_own_task_is_blocked_not_a_decision(self):
        task = self.running('michal')
        self.ledger.change(task, 'michal', 'block', note='pauza')
        self.assertEqual(self.row(task)['category'], 'blocked')

    def test_decision_ends_when_the_owner_hands_it_on(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='michal', purpose='work', artifact='/a.md')
        self.ledger.change(task, 'michal', 'accept')
        self.ledger.change(task, 'michal', 'start')
        self.ledger.change(task, 'michal', 'offer', target='autor', purpose='work', artifact='/rozhodnuti.md')
        self.assertNotEqual(self.row(task)['category'], 'owner_decision')

    def test_withdrawn_decision_is_no_longer_a_decision(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='michal', purpose='work', artifact='/a.md')
        self.ledger.change(task, 'autor', 'withdraw', note='vyreseno bez nej')
        row = self.row(task)
        self.assertEqual((row['category'], row['blocked_reason']), ('blocked', 'vyreseno bez nej'))

    def test_nothing_else_produces_a_decision(self):
        self.returned()
        self.in_review()
        blocked = self.running()
        self.ledger.change(blocked, 'autor', 'block', note='rozhodnuti Michala')
        self.assertEqual(build(self.ledger)['counts']['owner_decision'], 0)

    def test_owner_actor_is_configurable_and_validated(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='vedouci', purpose='work', artifact='/a.md')
        self.assertEqual(build(self.ledger, 'vedouci')['rows'][0]['category'], 'owner_decision')
        with self.assertRaises(ValueError):
            build(self.ledger, 'NE PLATNY!')

    def test_decisions_come_first_then_longest_waiting(self):
        now = time.time()
        rework = self.returned()
        decision = self.running()
        self.ledger.change(decision, 'autor', 'offer', target='michal', purpose='work', artifact='/a.md')
        older, newer = self.in_review(), self.in_review()
        self.ledger.db.execute('UPDATE tasks SET updated=? WHERE id=?', (now - 500, older))
        self.ledger.db.execute('UPDATE tasks SET updated=? WHERE id=?', (now - 5, newer))
        self.assertEqual([row['id'] for row in build(self.ledger, now=now)['rows']], [decision, rework, older, newer])


class AuthorRule(Base):
    def test_producer_named_in_the_queue_really_cannot_complete_it(self):
        task = self.in_review()
        self.assertEqual(self.row(task)['reviewer_must_not_be'], 'autor')
        with self.assertRaises(Conflict):
            self.ledger.change(task, 'autor', 'done', artifact='/x')
        with self.assertRaises(Conflict):  # nor can the review be handed back to the producer
            self.ledger.change(task, 'kontrolor', 'offer', target='autor', purpose='review', artifact='/x')


class ResultCheckLink(Base):
    def test_verdict_and_report_are_linked_and_failed_check_is_flagged(self):
        task = self.in_review()
        self.assertIsNone(self.row(task)['postcheck'])
        self.ledger.record_postcheck(task, 'kontrolor', 'fail', '/report1.json', 'a' * 64)
        row = self.row(task)
        self.assertEqual((row['postcheck']['verdict'], row['postcheck']['report']), ('fail', '/report1.json'))
        self.assertTrue(row['postcheck_blocks_done'])
        self.ledger.record_postcheck(task, 'kontrolor', 'pass', '/report2.json', 'b' * 64)
        row = self.row(task)
        self.assertEqual(row['postcheck']['report_sha256'], 'b' * 64)
        self.assertFalse(row['postcheck_blocks_done'])

    def test_flag_agrees_with_what_done_really_does(self):
        task = self.in_review()
        self.ledger.record_postcheck(task, 'kontrolor', 'unverified', '/r.json', 'a' * 64)
        self.assertTrue(self.row(task)['postcheck_blocks_done'])
        with self.assertRaises(Conflict):
            self.ledger.change(task, 'kontrolor', 'done', artifact='/x')


class ReadOnly(Base):
    def state(self):
        return ([dict(row) for row in self.ledger.db.execute('SELECT * FROM tasks ORDER BY id')],
                [dict(row) for row in self.ledger.db.execute('SELECT * FROM events ORDER BY seq')],
                [dict(row) for row in self.ledger.db.execute('SELECT * FROM commands ORDER BY request_id')])

    def test_building_changes_nothing_in_the_ledger(self):
        self.returned()
        before = self.state()
        build(self.ledger)
        self.assertEqual(before, self.state())

    def test_cli_changes_nothing_and_filters_by_who_acts(self):
        self.returned()
        other = self.in_review()
        before = self.state()
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        def cli(*extra):
            done = subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), 'queue', *extra], env=env,
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout)
        self.assertEqual(len(cli()['rows']), 2)
        self.assertEqual([row['id'] for row in cli('--for', 'kontrolor')['rows']], [other])
        self.assertEqual(before, self.state())

    def test_filter_applies_before_the_row_limit(self):
        from unittest import mock
        first = self.in_review()
        self.ledger.db.execute('UPDATE tasks SET updated=? WHERE id=?', (time.time() - 500, first))
        wanted = self.returned()
        with mock.patch('agentops.review_queue.MAX_ROWS', 1):
            result = build(self.ledger, waiting_on='autor')
        self.assertEqual([row['id'] for row in result['rows']], [wanted])
        self.assertFalse(result['truncated'])

    def test_row_limit_is_reported(self):
        from unittest import mock
        self.in_review(), self.in_review()
        with mock.patch('agentops.review_queue.MAX_ROWS', 1):
            result = build(self.ledger)
        self.assertEqual(len(result['rows']), 1)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['counts']['peer_review'], 2)


class Dashboard(Base):
    def tables(self, config=None):
        path = self.root / 'snapshot.json'
        path.write_text(json.dumps(snapshot(self.ledger, config or {}, signals={})))
        return plugin.collect({}, {'snapshot_path': str(path)})['tables']

    def queue(self, config=None):
        return [table for table in self.tables(config) if table['id'] == 'queue']

    def test_empty_queue_shows_no_table_and_old_table_stays_first(self):
        self.running()
        tables = self.tables()
        self.assertEqual(tables[0]['id'], 'tasks')
        self.assertEqual([table['id'] for table in tables if table['id'] == 'queue'], [])

    def test_snapshot_without_queue_still_renders(self):
        self.assertIsNone(plugin.queue_table(None))
        self.assertIsNone(plugin.queue_table({'rows': []}))

    def rows(self, config=None):
        return {row['id']: row for row in self.queue(config)[0]['rows']}

    def test_evidence_is_a_visible_row_with_a_copyable_full_reference(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/dlouha/cesta/vystup.md')
        self.ledger.change(task, 'kontrolor', 'accept')
        self.ledger.change(task, 'kontrolor', 'offer', target='autor', purpose='work', artifact='/slozka/nalezy.md')
        rows = self.rows()
        main = rows['q-' + task]
        self.assertEqual((main['status'], main['label']), ('Oprava po kontrole', 'autor'))
        self.assertEqual(main['session_id'], 'agentops timeline ' + task)
        evidence = rows[f'q-{task}-0']
        self.assertIn('nalezy.md', evidence['name'])  # visible without hover
        self.assertEqual(evidence['session_id'], '/slozka/nalezy.md')  # the copy field holds all of it

    def test_report_of_a_failed_check_is_visible_and_says_done_is_refused(self):
        task = self.in_review()
        self.ledger.record_postcheck(task, 'kontrolor', 'fail', '/dukazy/report.json', 'a' * 64)
        rows = self.rows()
        self.assertEqual(rows['q-' + task]['kind'], 'workflow-attention')
        names = [row['name'] for key, row in rows.items() if key.startswith(f'q-{task}-')]
        self.assertTrue(any('fail' in name and 'report.json' in name and 'done se odmítne' in name for name in names), names)
        self.assertIn('/dukazy/report.json', [row.get('session_id') for row in rows.values()])

    def test_blocked_reason_is_readable_in_the_row_name(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/v.md')
        self.ledger.change(task, 'autor', 'withdraw', note='chybi podklad')
        names = [row['name'] for row in self.rows().values()]
        self.assertTrue(any('Stažené předání' in name and 'chybi podklad' in name for name in names), names)

    def test_return_reason_is_not_repeated_as_the_output(self):
        self.returned()
        self.assertEqual(sum('nalezy.md' in row['name'] for row in self.rows().values()), 1)

    def test_all_rows_are_locked_and_no_state_pretends_to_be_health(self):
        self.returned()
        blocked = self.running()
        self.ledger.change(blocked, 'autor', 'block', note='duvod')
        for row in self.rows().values():
            self.assertIs(row['actions_locked'], True)
            self.assertIs(row['alive'], True)
            self.assertTrue(row['kind'].startswith('workflow-'))

    def test_row_kinds_separate_tasks_attention_and_evidence(self):
        rework = self.returned()
        blocked = self.running()
        self.ledger.change(blocked, 'autor', 'block', note='duvod')
        decision = self.running()
        self.ledger.change(decision, 'autor', 'offer', target='michal', purpose='work', artifact='/a.md')
        rows = self.rows()
        self.assertEqual(rows['q-' + rework]['kind'], 'workflow-task')
        self.assertEqual(rows['q-' + blocked]['kind'], 'workflow-attention')
        self.assertEqual(rows['q-' + decision]['kind'], 'workflow-attention')
        self.assertEqual({row['kind'] for key, row in rows.items() if key.count('-') > 5}, {'workflow-evidence'})

    def test_count_excludes_evidence_rows_and_names_attention(self):
        self.returned()
        self.returned()
        blocked = self.running()
        self.ledger.change(blocked, 'autor', 'block', note='duvod')
        table = self.queue()[0]
        self.assertGreater(len(table['rows']), 3)
        self.assertEqual(table['presentation']['count'], '3 úkoly')
        self.assertEqual(table['presentation']['summary'], '3 úkoly · 1 k pozornosti')

    def test_counts_read_naturally_and_without_attention(self):
        self.assertEqual([plugin._tasks(n) for n in (1, 2, 4, 5, 21)], ['1 úkol', '2 úkoly', '4 úkoly', '5 úkolů', '21 úkolů'])
        self.returned()
        self.assertEqual(self.queue()[0]['presentation']['summary'], '1 úkol')

    def test_presentation_satisfies_the_dashboard_contract(self):
        self.returned()
        shown = self.queue()[0]['presentation']
        self.assertEqual(shown['type'], 'workflow')
        self.assertEqual(len(shown['headers']), 5)
        self.assertLessEqual(len(shown['summary']), 120)

    def add_run(self, task, **change):
        self.ledger.record_run_event({'task_id': task, 'run_id': str(uuid.uuid4()), 'parent_id': None, 'at': 5.0, 'source': 'operator', 'evidence': 'declared',
                                      'actor': 'autor', 'action': 'start', 'outcome': 'ok', 'error_code': None, 'usage_tokens': 1, **change})

    def test_runs_appear_as_evidence_with_the_timeline_to_copy(self):
        task = self.returned()
        self.add_run(task)
        self.add_run(task, outcome='unknown', usage_tokens=None)
        rows = self.rows()
        row = next(row for key, row in rows.items() if 'Běhy: 2' in row['name'])
        self.assertIn('nejasné: 1', row['name'])
        self.assertIn('spotřeba neznámá u 1', row['name'])
        self.assertEqual(row['session_id'], 'agentops timeline ' + task)
        self.assertEqual(row['kind'], 'workflow-evidence')
        self.assertEqual(rows['q-' + task]['kind'], 'workflow-attention')

    def test_clean_runs_need_no_attention_and_no_runs_add_no_row(self):
        task = self.returned()
        self.assertFalse(any('Běhy' in row['name'] for row in self.rows().values()))
        self.add_run(task)
        rows = self.rows()
        self.assertEqual(rows['q-' + task]['kind'], 'workflow-task')
        self.assertEqual(next(row for row in rows.values() if 'Běhy' in row['name'])['name'], '↳ Běhy: 1')

    def test_note_says_files_cannot_be_opened_from_here(self):
        self.returned()
        self.assertIn('neotevírají', self.queue()[0]['note'])

    def test_decision_label_follows_the_declared_owner(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='vedouci', purpose='work', artifact='/a.md')
        table = self.queue({'decision_owner': 'vedouci'})[0]
        row = {row['id']: row for row in table['rows']}['q-' + task]
        self.assertEqual(row['status'], 'Rozhodnutí: vedouci')
        self.assertEqual(row['kind'], 'workflow-attention')
        self.assertIn('Rozhodnutí: vedouci', table['note'])
        self.assertNotIn('Michal', json.dumps(table, ensure_ascii=False))

    def test_decision_owner_comes_from_config_not_from_a_default(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'offer', target='michal', purpose='work', artifact='/a.md')
        self.assertEqual(self.rows({'decision_owner': 'vedouci'})['q-' + task]['kind'], 'workflow-task')

    def test_control_characters_and_long_text_stay_inside_dashboard_limits(self):
        task = self.running()
        self.ledger.change(task, 'autor', 'block', note='x' * 400)
        queue = build(self.ledger)
        queue['rows'][0].update(blocked_reason='radek\nnovy\t' + 'y' * 600, title='t\n' + 'z' * 300)
        for row in plugin.queue_table(queue)['rows']:
            self.assertLessEqual(len(row['name']), 240)
            self.assertLessEqual(len(row.get('session_id', '')), 500)
            self.assertLessEqual(len(row['status_detail']), 500)
            self.assertFalse(any(ord(char) < 32 for field in ('name', 'status_detail') for char in row[field]))

    def test_table_stays_under_the_dashboard_row_limit_and_says_so(self):
        for _ in range(80):
            self.in_review()
        table = self.queue()[0]
        self.assertLessEqual(len(table['rows']), plugin.MAX_TABLE_ROWS)
        self.assertIn('jen část', table['note'])


if __name__ == '__main__':
    unittest.main()
