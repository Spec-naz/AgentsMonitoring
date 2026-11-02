"""LC-08: each ending from real ledger transitions and real files, in a temporary folder."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from agentops import brief as b
from agentops.ledger import Ledger

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
        self.guard = self.root / 'guard.sqlite'
        self.output = self.root / 'vystup.md'
        self.output.write_text('obsah ' + SECRET)

    def run_for(self, task, actor='klodak', **kwargs):
        return b.prepare(self.db, task, actor, kwargs.pop('guard', self.guard), **kwargs)

    def running(self, owner='kodak'):
        task = self.ledger.create('nazev ' + SECRET, owner, owner)['id']
        self.ledger.change(task, owner, 'start')
        return task

    def handed_over(self, artifact=None):
        task = self.running()
        self.ledger.change(task, 'kodak', 'offer', target='klodak', purpose='work', artifact=str(artifact or self.output))
        return task

    def set_guard(self, task, mode):
        connection = sqlite3.connect(self.guard)
        connection.execute('CREATE TABLE IF NOT EXISTS checkpoints(task_id TEXT, mode TEXT, retired INTEGER)')
        connection.execute('INSERT INTO checkpoints VALUES(?,?,0)', (task, mode))
        connection.commit()
        connection.close()


class StartAgent(Base):
    def test_handover_to_accept_gives_exact_commands_and_hash(self):
        task = self.handed_over()
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason'], result['handed_to']), ('start_agent', 'handover_to_accept', 'agent'))
        version = self.ledger.get(task)['version']
        self.assertEqual(result['commands'], [f'agentops accept {task} --actor klodak --expected-version {version}',
                                              f'agentops start {task} --actor klodak --expected-version {version + 1}'])
        self.assertEqual(result['read_first']['status'], 'readable')
        self.assertEqual(result['read_first']['bytes'], self.output.stat().st_size)

    def test_printed_commands_really_work_on_the_ledger(self):
        task = self.handed_over()
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        for command in self.run_for(task)['commands']:
            parts = command.split()
            self.assertEqual(parts[0], 'agentops')
            run = subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), *parts[1:]], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.ledger.get(task)['state'], 'running')

    def run_commands(self, commands):
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        for command in commands:
            parts = command.split()
            run = subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), *parts[1:]], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, command + run.stderr)

    def test_printed_commands_for_a_review_really_work(self):
        task = self.running()
        self.ledger.change(task, 'kodak', 'offer', target='klodak', purpose='review', artifact=str(self.output))
        commands = self.run_for(task)['commands']
        self.assertEqual(len(commands), 1)
        self.run_commands(commands)
        self.assertEqual(self.ledger.get(task)['state'], 'review')

    def test_printed_commands_after_a_return_really_work(self):
        task = self.running('klodak')
        self.ledger.change(task, 'klodak', 'offer', target='kodak', purpose='review', artifact='/h.md')
        self.ledger.change(task, 'kodak', 'accept')
        self.ledger.change(task, 'kodak', 'offer', target='klodak', purpose='work', artifact=str(self.output))
        self.run_commands(self.run_for(task)['commands'])
        self.assertEqual(self.ledger.get(task)['state'], 'running')

    def test_ordinary_work_of_the_owner_actor_is_not_a_decision(self):
        task = self.running('michal')
        result = self.run_for(task, 'michal')
        self.assertEqual((result['ending'], result['reason']), ('start_agent', 'already_in_progress'))
        queued = self.ledger.create('t', 'michal', 'michal')['id']
        self.assertEqual(self.run_for(queued, 'michal')['reason'], 'queued_not_handed_over')

    def test_return_after_review_names_the_reason(self):
        task = self.running('klodak')
        self.ledger.change(task, 'klodak', 'offer', target='kodak', purpose='review', artifact='/h.md')
        self.ledger.change(task, 'kodak', 'accept')
        self.ledger.change(task, 'kodak', 'offer', target='klodak', purpose='work', artifact=str(self.output))
        result = self.run_for(task)
        self.assertEqual(result['returned'], {'by': 'kodak', 'reason_artifact': str(self.output)})

    def test_accepted_not_started_and_already_running(self):
        task = self.handed_over()
        self.ledger.change(task, 'klodak', 'accept')
        result = self.run_for(task)
        self.assertEqual((result['reason'], len(result['commands'])), ('accepted_not_started', 1))
        self.ledger.change(task, 'klodak', 'start')
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason'], result['commands']), ('start_agent', 'already_in_progress', []))

    def test_review_handed_to_an_independent_reviewer_starts_an_agent(self):
        task = self.running()
        self.ledger.change(task, 'kodak', 'offer', target='klodak', purpose='review', artifact=str(self.output))
        result = self.run_for(task)
        self.assertEqual(result['ending'], 'start_agent')
        self.assertIs(result['must_not_review_own_work'], False)

    def test_latest_result_check_is_shown(self):
        task = self.running('klodak')
        self.ledger.record_postcheck(task, 'klodak', 'fail', '/r1.json', 'a' * 64)
        self.ledger.record_postcheck(task, 'klodak', 'unverified', '/r2.json', 'b' * 64)
        self.assertEqual(self.run_for(task)['result_check'], {'verdict': 'unverified', 'report': '/r2.json'})


class NoAction(Base):
    def test_closed_tasks(self):
        task = self.running()
        self.ledger.change(task, 'kodak', 'cancel', note='konec')
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason'], result['handed_to']), ('no_action', 'task_closed', 'nobody'))

    def test_waiting_for_someone_else(self):
        for task in (self.running(), self.ledger.create('t', 'kodak', 'kodak')['id']):
            self.assertEqual(self.run_for(task)['reason'], 'waits_for_someone_else')
        offered = self.running()
        self.ledger.change(offered, 'kodak', 'offer', target='treti', purpose='work', artifact='/x')
        self.assertEqual(self.run_for(offered)['ending'], 'no_action')

    def test_queued_alone_is_not_a_signal_to_start(self):
        """Found on the real ledger: backlog items are queued and owned, yet nobody handed them over."""
        task = self.ledger.create('backlog', 'klodak', 'kodak')['id']
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason']), ('no_action', 'queued_not_handed_over'))
        self.assertNotIn('commands', result)

    def test_someone_elses_block_is_not_my_person_alert(self):
        """Found on the real ledger: every actor was told to fetch a person for any blocked task."""
        task = self.running()
        self.ledger.change(task, 'kodak', 'block', note='x')
        self.assertEqual(self.run_for(task)['reason'], 'waits_for_someone_else')
        self.assertEqual(self.run_for(task, 'kodak')['reason'], 'declared_blocked')

    def test_owner_who_offered_away_has_nothing_to_do(self):
        task = self.running('klodak')
        self.ledger.change(task, 'klodak', 'offer', target='kodak', purpose='review', artifact=str(self.output))
        self.assertEqual(self.run_for(task)['reason'], 'waits_for_someone_else')


class Person(Base):
    def test_decision_handed_to_owner(self):
        task = self.running()
        self.ledger.change(task, 'kodak', 'offer', target='michal', purpose='work', artifact=str(self.output))
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason'], result['handed_to']), ('person', 'decision_handed_to_owner', 'person'))
        self.assertNotIn('commands', result)

    def test_decision_stays_with_a_person_after_it_is_accepted(self):
        task = self.running()
        self.ledger.change(task, 'kodak', 'offer', target='michal', purpose='work', artifact=str(self.output))
        self.ledger.change(task, 'michal', 'accept')
        for actor in ('michal', 'klodak'):
            result = self.run_for(task, actor)
            self.assertEqual((result['ending'], result['reason']), ('person', 'decision_handed_to_owner'))
            self.assertNotIn('commands', result)
        self.ledger.change(task, 'michal', 'start')
        self.assertEqual(self.run_for(task, 'michal')['reason'], 'decision_handed_to_owner')

    def test_decision_ends_when_the_owner_hands_it_on(self):
        task = self.running()
        self.ledger.change(task, 'kodak', 'offer', target='michal', purpose='work', artifact=str(self.output))
        self.ledger.change(task, 'michal', 'accept')
        self.ledger.change(task, 'michal', 'start')
        self.ledger.change(task, 'michal', 'offer', target='klodak', purpose='work', artifact=str(self.output))
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason']), ('start_agent', 'handover_to_accept'))

    def test_decision_follows_the_configured_owner(self):
        task = self.running()
        self.ledger.change(task, 'kodak', 'offer', target='vedouci', purpose='work', artifact=str(self.output))
        self.ledger.change(task, 'vedouci', 'accept')
        self.assertEqual(self.run_for(task, 'vedouci', owner_actor='vedouci')['reason'], 'decision_handed_to_owner')
        self.assertEqual(self.run_for(task, 'vedouci')['ending'], 'start_agent')

    def test_declared_block_goes_to_a_person_even_for_the_owner(self):
        task = self.running('klodak')
        self.ledger.change(task, 'klodak', 'block', note='duvod ' + SECRET)
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason']), ('person', 'declared_blocked'))
        self.assertNotIn(SECRET, b.stable_json(result))

    def test_uncertain_outside_effect_stops_before_anything_else(self):
        task = self.handed_over()
        self.set_guard(task, 'needs_reconciliation')
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['reason'], result['retry']), ('person', 'uncertain_outside_effect', False))
        self.assertNotIn('commands', result)

    def test_saved_pause_or_wait_is_not_overridden(self):
        for mode in ('paused', 'waiting_approval', 'rezim-z-budoucnosti'):
            with self.subTest(mode):
                task = self.handed_over()
                self.set_guard(task, mode)
                result = self.run_for(task)
                self.assertEqual((result['ending'], result['reason']), ('person', 'guard_says_wait'))
                self.assertNotIn('commands', result)

    def test_guard_does_not_alert_about_a_closed_task(self):
        task = self.handed_over()
        self.set_guard(task, 'needs_reconciliation')
        self.ledger.change(task, 'kodak', 'cancel', note='x')
        self.assertEqual(self.run_for(task)['ending'], 'no_action')

    def test_ready_guard_entry_does_not_stop(self):
        task = self.handed_over()
        self.set_guard(task, 'ready')
        result = self.run_for(task)
        self.assertEqual((result['ending'], result['guard']), ('start_agent', 'ready'))

    def test_missing_guard_database_is_named_not_treated_as_ok_or_failure(self):
        result = self.run_for(self.handed_over(), guard=self.root / 'neni.sqlite')
        self.assertEqual((result['ending'], result['guard']), ('start_agent', 'not_available'))

    def test_linked_output_problems_go_to_a_person_without_commands(self):
        link = self.root / 'odkaz.md'
        link.symlink_to(self.output)
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        cases = {self.root / 'neni.md': 'linked_output_missing', link: 'linked_output_unreadable', fifo: 'linked_output_not_a_file',
                 self.root: 'linked_output_not_a_file', 'relativni/cesta.md': 'linked_output_not_a_local_file'}
        for artifact, reason in cases.items():
            with self.subTest(reason=reason, artifact=str(artifact)):
                result = self.run_for(self.handed_over(artifact))
                self.assertEqual((result['ending'], result['reason']), ('person', reason))
                self.assertNotIn('commands', result)

    def test_oversized_output(self):
        task = self.handed_over()
        with mock.patch.object(b, 'MAX_ARTIFACT_BYTES', 1):
            self.assertEqual(self.run_for(task)['reason'], 'linked_output_too_large')


class Unknown(Base):
    def assert_unknown(self, result, reason):
        self.assertEqual((result['ending'], result['reason'], result['handed_to'], result['retry']), ('unknown', reason, 'person', False))
        self.assertNotIn('commands', result)
        self.assertNotIn(SECRET, b.stable_json(result))

    def test_missing_or_broken_ledger(self):
        task = str(uuid.uuid4())
        self.assert_unknown(b.prepare(self.root / 'neni.sqlite', task, 'klodak', self.guard), 'ledger_unavailable')
        broken = self.root / 'rozbity.sqlite'
        broken.write_bytes(SECRET.encode() * 200)
        self.assert_unknown(b.prepare(broken, task, 'klodak', self.guard), 'ledger_unavailable')
        self.assertFalse((self.root / 'neni.sqlite').exists())

    def test_task_not_found(self):
        self.assert_unknown(self.run_for(str(uuid.uuid4())), 'task_not_found')

    def test_broken_guard_is_not_read_as_all_clear(self):
        self.guard.write_bytes(SECRET.encode() * 200)
        self.assert_unknown(self.run_for(self.handed_over()), 'guard_unavailable')

    def test_guard_with_unknown_schema(self):
        connection = sqlite3.connect(self.guard)
        connection.execute('CREATE TABLE jina(a)')
        connection.commit()
        connection.close()
        self.assert_unknown(self.run_for(self.handed_over()), 'guard_unavailable')

    def test_unreadable_audit_details(self):
        task = self.running('klodak')
        connection = sqlite3.connect(self.db)
        connection.execute("INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)", (task, 1.0, 'klodak', 'postcheck', 'neni json ' + SECRET))
        connection.commit()
        connection.close()
        self.assert_unknown(self.run_for(task), 'audit_details_unreadable')

    def test_time_limit_on_a_slow_read_ends_as_unknown(self):
        self.assert_unknown(self.run_for(self.handed_over(), time_limit=-1), 'time_limit')

    def test_task_changed_during_the_read(self):
        task = self.handed_over()
        real = b._artifact
        def changing(path, deadline):
            self.ledger.change(task, 'kodak', 'withdraw', note='stazeno')
            return real(path, deadline)
        with mock.patch.object(b, '_artifact', changing):
            self.assert_unknown(self.run_for(task), 'task_changed_during_read')

    def test_unrecognised_state_is_not_guessed(self):
        task = self.running('klodak')
        connection = sqlite3.connect(self.db)
        connection.execute("UPDATE tasks SET state='novy-stav' WHERE id=?", (task,))
        connection.commit()
        connection.close()
        self.assert_unknown(self.run_for(task), 'state_not_recognised')


class Boundaries(Base):
    def state(self):
        connection = sqlite3.connect(self.db)
        rows = [connection.execute(f'SELECT * FROM {name} ORDER BY 1').fetchall() for name in ('tasks', 'events', 'commands')]
        connection.close()
        return rows

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), 'brief', *args], env={**os.environ, 'PYTHONPATH': str(ROOT)},
                              capture_output=True, text=True, timeout=30)

    def test_nothing_changes_in_the_ledger_or_the_files(self):
        task = self.handed_over()
        before = self.state(), self.output.read_bytes(), self.output.stat().st_mtime_ns
        self.run_for(task)
        self.cli(task, '--for', 'klodak', '--guard-db', str(self.guard))
        self.assertEqual(before, (self.state(), self.output.read_bytes(), self.output.stat().st_mtime_ns))
        self.assertFalse(self.guard.exists())

    def test_cli_exit_code_says_who_continues(self):
        start = self.handed_over()
        self.assertEqual(self.cli(start, '--for', 'klodak', '--guard-db', str(self.guard)).returncode, 10)
        self.assertEqual(self.cli(start, '--for', 'jiny', '--guard-db', str(self.guard)).returncode, 0)
        blocked = self.running('klodak')
        self.ledger.change(blocked, 'klodak', 'block', note='x')
        self.assertEqual(self.cli(blocked, '--for', 'klodak', '--guard-db', str(self.guard)).returncode, 20)
        self.assertEqual(self.cli(str(uuid.uuid4()), '--for', 'klodak', '--guard-db', str(self.guard)).returncode, 30)

    def test_invalid_input_is_exit_one_and_short(self):
        for args in (('neni-uuid', '--for', 'klodak'), (str(uuid.uuid4()), '--for', 'A B')):
            run = self.cli(*args)
            self.assertEqual(run.returncode, 1)
            self.assertNotIn('Traceback', run.stderr)

    def test_same_state_gives_identical_bytes_for_the_monitor_gate(self):
        task = self.handed_over()
        self.assertEqual(self.cli(task, '--for', 'klodak', '--guard-db', str(self.guard)).stdout,
                         self.cli(task, '--for', 'klodak', '--guard-db', str(self.guard)).stdout)
        before = self.cli(task, '--for', 'klodak', '--guard-db', str(self.guard)).stdout
        self.ledger.change(task, 'kodak', 'withdraw', note='x')
        self.assertNotEqual(before, self.cli(task, '--for', 'klodak', '--guard-db', str(self.guard)).stdout)

    def test_no_title_note_or_file_content_in_the_output(self):
        task = self.handed_over()
        self.assertNotIn(SECRET, self.cli(task, '--for', 'klodak', '--guard-db', str(self.guard)).stdout)

    def test_module_cannot_run_anything_or_reach_the_network(self):
        import ast
        tree = ast.parse((ROOT / 'agentops/brief.py').read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add(node.module.split('.')[0])
        self.assertFalse(imported & {'subprocess', 'socket', 'urllib.request', 'http', 'importlib'})
        self.assertNotIn('Ledger(', (ROOT / 'agentops/brief.py').read_text())

    def test_default_guard_path_is_the_one_the_guard_really_uses(self):
        source = Path.home() / 'projects/task-continuity-guard/guard.py'
        if not source.exists():
            self.skipTest('guard source not on this host')
        self.assertIn(str(b.DEFAULT_GUARD.relative_to(Path.home() / '.local/state')), source.read_text())

    def test_documents_say_what_it_does_not_decide(self):
        text = (ROOT / 'docs/routines.md').read_text()
        for phrase in ('It chooses no work', 'It takes nothing over', 'does not know whether an agent is idle'):
            self.assertIn(phrase, text)


if __name__ == '__main__':
    unittest.main()
