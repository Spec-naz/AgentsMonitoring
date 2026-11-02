import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import relay


THREAD = '01a184a2-f8d5-71f0-86d3-6cda9460a488'
TASK = '11111111-1111-4111-8111-111111111111'


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.pause = self.root / 'PAUSED'
        self.event = {'kind': 'review_pending', 'task_id': TASK, 'to': 'kodak', 'transition_seq': 10}
        self.events = [self.event]
        self.monitor = mock.patch.object(relay, 'monitor', side_effect=lambda *a: {'events': self.events})
        self.monitor.start()
        self.addCleanup(self.monitor.stop)
        self.process = mock.patch.object(relay.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'queued', ''))
        self.run = self.process.start()
        self.addCleanup(self.process.stop)

    def poll(self):
        return relay.poll(THREAD, state=self.state, pause=self.pause, timeout=.1)

    def receipts(self):
        return relay.read_receipts(self.state)

    def test_no_events_never_invokes_codex(self):
        self.events = []
        self.assertEqual(self.poll(), {'status': 'no_events'})
        self.run.assert_not_called()

    def test_queue_uses_existing_thread_resume_and_receipt_without_tui(self):
        result = self.poll()
        self.assertEqual(result['status'], 'queued_resume_unverified')
        args = self.run.call_args.args[0]
        self.assertEqual(args[:5], ['codex', 'queue', '--thread', THREAD, '--message'])
        self.assertEqual(args[-1], '--resume')
        self.assertIn(result['event_id'], args[5])
        self.assertNotIn('tmux', args)
        self.assertNotIn('--model', args)

    def test_unchanged_event_never_requeued_even_before_ack(self):
        self.poll()
        self.assertEqual(self.poll(), {'status': 'unchanged'})
        self.run.assert_called_once()

    def test_pause_prevents_even_state_creation(self):
        self.pause.touch()
        self.assertEqual(self.poll(), {'status': 'paused'})
        self.assertFalse(self.state.exists())
        self.run.assert_not_called()

    def test_pause_between_reservation_and_delivery_blocks_queue(self):
        original = relay.save_receipts
        def save(*args):
            original(*args)
            self.pause.touch()
        with mock.patch.object(relay, 'save_receipts', side_effect=save):
            result = self.poll()
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(self.receipts()[result['event_id']]['state'], 'paused_before_enqueue')
        self.run.assert_not_called()

    def test_exit_zero_resume_warning_is_unverified_not_acknowledged(self):
        self.run.return_value = subprocess.CompletedProcess([], 0, 'queued', 'Warning: resume failed FAKE_SECRET')
        result = self.poll()
        self.assertEqual(result['status'], 'resume_or_queue_unverified')
        self.assertTrue(self.receipts()[result['event_id']]['resume_warning'])
        self.assertNotIn('FAKE_SECRET', (self.state / 'relay.json').read_text())
        self.assertEqual(self.poll()['status'], 'unchanged')
        self.run.assert_called_once()

    def test_nonzero_failure_is_never_automatically_retried(self):
        self.run.return_value = subprocess.CompletedProcess([], 1, 'FAKE_SECRET', 'FAKE_SECRET')
        result = self.poll()
        self.assertEqual(result['status'], 'resume_or_queue_unverified')
        self.assertEqual(self.poll()['status'], 'unchanged')
        self.run.assert_called_once()
        self.assertNotIn('FAKE_SECRET', (self.state / 'relay.json').read_text())

    def test_timeout_is_durable_unknown_and_not_replayed(self):
        self.run.side_effect = subprocess.TimeoutExpired('codex', .1, stderr='FAKE_SECRET')
        result = self.poll()
        self.assertEqual(result['status'], 'timeout_unverified')
        self.assertEqual(self.poll()['status'], 'unchanged')
        self.run.assert_called_once()
        self.assertNotIn('FAKE_SECRET', (self.state / 'relay.json').read_text())

    def test_missing_queue_executable_is_recorded_without_sensitive_error(self):
        self.run.side_effect = OSError('FAKE_SECRET')
        result = self.poll()
        self.assertEqual(result['status'], 'queue_unavailable_unverified')
        self.assertEqual(self.poll()['status'], 'unchanged')
        self.assertNotIn('FAKE_SECRET', (self.state / 'relay.json').read_text())

    def test_reserved_before_process_start_and_crash_cannot_requeue(self):
        def die(*args, **kwargs):
            self.assertEqual(next(iter(self.receipts().values()))['state'], 'reserved')
            raise KeyboardInterrupt()
        self.run.side_effect = die
        with self.assertRaises(KeyboardInterrupt):
            self.poll()
        self.run.side_effect = None
        self.assertEqual(self.poll()['status'], 'unchanged')
        self.run.assert_called_once()

    def test_concurrent_lock_prevents_queue_and_does_not_truncate_live_lock(self):
        self.state.mkdir()
        lock = self.state / 'relay.lock'
        lock.write_text('sentinel-lock-content')
        with lock.open('a') as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.poll(), {'status': 'busy'})
        self.assertEqual(lock.read_text(), 'sentinel-lock-content')
        self.run.assert_not_called()

    def test_fifo_lock_cli_fails_quickly_and_never_queues_with_or_without_reader(self):
        self.state.mkdir()
        fifo = self.state / 'relay.lock'
        os.mkfifo(fifo)
        binary_dir = self.root / 'bin'
        binary_dir.mkdir()
        marker = self.root / 'codex-was-called'
        fake = binary_dir / 'codex'
        fake.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("called")\n')
        fake.chmod(0o755)
        env = {**os.environ, 'PATH': str(binary_dir) + os.pathsep + os.environ.get('PATH', '')}
        command = [sys.executable, str(Path(relay.__file__)), 'poll', '--thread', THREAD,
                   '--state', str(self.state), '--pause', str(self.pause)]
        for reader_present in [False, True]:
            with self.subTest(reader_present=reader_present):
                reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK) if reader_present else None
                child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                try:
                    stdout, stderr = child.communicate(timeout=2)
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate()
                    if reader is not None:
                        os.close(reader)
                self.assertEqual(child.returncode, 1)
                self.assertEqual(stdout.strip(), '{"status": "relay_unavailable"}')
                self.assertEqual(stderr, '')
                self.assertFalse(marker.exists())

    def test_second_attempt_while_first_process_running_is_busy(self):
        def nested(*args, **kwargs):
            self.assertEqual(self.poll(), {'status': 'busy'})
            return subprocess.CompletedProcess([], 0, '', '')
        self.run.side_effect = nested
        self.assertEqual(self.poll()['status'], 'queued_resume_unverified')
        self.run.assert_called_once()

    def test_new_actual_transition_gets_new_receipt(self):
        first = self.poll()
        self.event['transition_seq'] = 20
        second = self.poll()
        self.assertNotEqual(first['event_id'], second['event_id'])
        self.assertEqual(self.run.call_count, 2)

    def test_one_new_event_per_poll_no_infinite_loop(self):
        self.events.append({**self.event, 'task_id': '22222222-2222-4222-8222-222222222222'})
        self.poll()
        self.assertEqual(self.run.call_count, 1)
        self.poll()
        self.assertEqual(self.run.call_count, 2)
        self.assertEqual(self.poll()['status'], 'unchanged')

    def test_ack_requires_explicit_evidence_and_known_receipt(self):
        result = self.poll()
        with self.assertRaises(ValueError):
            relay.acknowledge(self.state, result['event_id'], '')
        with self.assertRaises(ValueError):
            relay.acknowledge(self.state, 'a' * 64, 'actual native turn abc')
        ack = relay.acknowledge(self.state, result['event_id'], 'observed task review accepted v17')
        self.assertEqual(ack['status'], 'acknowledged')
        self.assertEqual(self.receipts()[result['event_id']]['evidence'], 'observed task review accepted v17')
        self.assertEqual(self.poll()['status'], 'unchanged')

    def test_different_target_thread_has_distinct_receipt(self):
        one = relay.event_identity(THREAD, self.event)
        two = relay.event_identity('22222222-2222-4222-8222-222222222222', self.event)
        self.assertNotEqual(one, two)


if __name__ == '__main__':
    unittest.main()
