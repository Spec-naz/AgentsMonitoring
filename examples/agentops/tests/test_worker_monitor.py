import errno
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location('worker_monitor', Path(__file__).parents[1] / 'monitor-worker/plugin.py')
plugin = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin)


class WorkerMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'config.yaml'
        self.profile.write_text('model:\n  provider: claude-subscription-directsdk-experimental\n  default: claude-haiku-5-5\nagent:\n  max_turns: 4\n')
        self.launcher = self.root / 'run.py'
        self.launcher.write_text('PRIVATE-SOURCE-DO-NOT-READ')
        self.state = self.root / 'state'
        self.state.mkdir()
        self.config = {'id': 'hermik2', 'name': 'Hermík dvojka', 'model': 'claude-haiku-5-5',
                       'provider': 'claude-subscription-directsdk-experimental',
                       'quota_label': 'Claude Max sdílené s Klóďákem', 'config_path': str(self.profile),
                       'launcher_path': str(self.launcher), 'state_dir': str(self.state)}

    def row(self):
        return plugin.collect({}, {'workers': [self.config]})['tables'][0]['rows'][0]

    def test_unregistered_worker_is_not_discovered(self):
        self.assertEqual(plugin.collect({}, {}), {'tables': []})

    def test_ready_has_no_persistent_process_or_duplicate_usage(self):
        row = self.row()
        self.assertEqual(row['status'], 'Připraven')
        self.assertTrue(row['alive'])
        self.assertTrue(row['actions_locked'])
        self.assertIn('Claude Max', row['account'])
        self.assertNotIn('cells', row)
        self.assertNotIn('percent', json.dumps(row))
        self.assertFalse((self.state / 'run.lock').exists())

    def test_actual_held_existing_lock_reports_activity_without_modifying_it(self):
        lock = self.state / 'run.lock'
        lock.write_text('LOCK-CONTENTS-DO-NOT-READ')
        before = lock.stat()
        with lock.open('r') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            (self.state / 'unresolved.json').write_text('PRIVATE-PROMPT-DO-NOT-READ')
            self.assertEqual(self.row()['status'], 'Pracuje')
        self.assertEqual(lock.read_text(), 'LOCK-CONTENTS-DO-NOT-READ')
        self.assertEqual(lock.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')

    def test_unresolved_invalid_or_dangling_marker_requires_intervention(self):
        marker = self.state / 'unresolved.json'
        marker.write_text('not valid json, still must block')
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        marker.unlink()
        marker.symlink_to(self.root / 'nonexistent')
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')

    def test_missing_wrong_or_oversized_config_is_attention(self):
        for text in ('', 'model:\n  default: OTHER\n', 'x' * 65537, self.profile.read_text() + 'model: {}\n', self.profile.read_text().replace('  default:', '  default: OTHER\n  default:')):
            self.profile.write_text(text)
            self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        self.profile.unlink()
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')

    def test_metadata_output_never_contains_paths_job_or_source_contents(self):
        (self.state / 'job-private').mkdir()
        (self.state / 'job-private/reply.md').write_text('PRIVATE-REPLY')
        (self.state / 'unresolved.json').write_text('PRIVATE-PROMPT')
        # Explicitly fail if code starts reading marker/job/source files via Path helpers.
        with patch.object(Path, 'read_text', side_effect=AssertionError('No Path content reads')):
            output = json.dumps(self.row())
        for value in ('PRIVATE', str(self.root), 'job-private', 'percent'):
            self.assertNotIn(value, output)

    def test_non_regular_metadata_does_not_block_or_report_ready(self):
        lock = self.state / 'run.lock'
        os.mkfifo(lock)
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        lock.unlink()
        self.profile.unlink()
        self.profile.symlink_to(self.launcher)
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')

    def test_parallel_shared_monitor_probe_is_not_mistaken_for_worker(self):
        lock = self.state / 'run.lock'
        lock.write_text('')
        with lock.open('r') as other_monitor:
            fcntl.flock(other_monitor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.assertEqual(self.row()['status'], 'Připraven')

    def test_directory_metadata_does_not_leak_descriptors(self):
        fd_count = lambda: len(list(Path('/proc/self/fd').iterdir()))
        self.profile.unlink()
        self.profile.mkdir()
        before = fd_count()
        for _ in range(10):
            self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        self.assertEqual(fd_count(), before)
        self.profile.rmdir()
        self.profile.write_text('model:\n  provider: claude-subscription-directsdk-experimental\n  default: claude-haiku-5-5\n')
        (self.state / 'run.lock').mkdir()
        for _ in range(10):
            self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        self.assertEqual(fd_count(), before)

    def test_bad_public_metadata_preserves_other_registered_worker(self):
        bad = dict(self.config, id='invalid-public', name=None, model=42, quota_label='bad\nlabel')
        table = plugin.collect({}, {'workers': [self.config, bad]})['tables'][0]
        self.assertEqual([r['status'] for r in table['rows']], ['Připraven', 'Vyžaduje zásah'])
        self.assertEqual(table['rows'][1]['name'], 'invalid-public')
        self.assertEqual(table['rows'][1]['label'], 'Model neověřen')
        self.assertNotIn('bad\nlabel', json.dumps(table))

    def test_unknown_home_expansion_is_attention_and_invalid_id_is_explicit_error(self):
        with patch.object(Path, 'expanduser', side_effect=RuntimeError('unknown user')):
            self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        with self.assertRaises(ValueError):
            plugin.collect({}, {'workers': [dict(self.config, id=42)]})

    def test_missing_empty_or_relative_registration_paths_require_intervention(self):
        original = dict(self.config)
        for key in ('config_path', 'launcher_path', 'state_dir'):
            for value in (None, '', '   ', 'relative/path', '.', 42):
                with self.subTest(key=key, value=value):
                    self.config = dict(original, **{key: value})
                    self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
            self.config = dict(original)
            del self.config[key]
            with self.subTest(key=key, missing=True):
                self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        self.config = original

    def test_empty_missing_or_nonstring_provider_requires_intervention(self):
        for value in (None, '', '   ', 42):
            with self.subTest(value=value):
                self.config['provider'] = value
                self.assertEqual(self.row()['status'], 'Vyžaduje zásah')
        del self.config['provider']
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')

    def test_marker_permission_and_io_errors_require_intervention_with_or_without_lock(self):
        marker = self.state / 'unresolved.json'
        original_lstat = Path.lstat
        for with_lock in (False, True):
            if with_lock:
                (self.state / 'run.lock').write_text('')
            for error in (PermissionError(errno.EACCES, 'fixture'), OSError(errno.EIO, 'fixture')):
                def checked_lstat(path, *args, **kwargs):
                    if path == marker:
                        raise error
                    return original_lstat(path, *args, **kwargs)
                with self.subTest(lock=with_lock, error=error.errno):
                    with patch.object(Path, 'lstat', checked_lstat):
                        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')

    def test_shared_lock_is_held_until_marker_check_finishes(self):
        lock = self.state / 'run.lock'
        lock.write_text('')
        original_exists = plugin.marker_exists
        checked = []
        def check_marker(path):
            with lock.open('r') as contender:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            checked.append(True)
            return original_exists(path)
        with patch.object(plugin, 'marker_exists', side_effect=check_marker):
            self.assertEqual(self.row()['status'], 'Připraven')
        self.assertEqual(checked, [True])
        with lock.open('r') as contender:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_dangling_marker_also_requires_intervention_with_existing_unheld_lock(self):
        (self.state / 'run.lock').write_text('')
        (self.state / 'unresolved.json').symlink_to(self.root / 'missing-marker-target')
        self.assertEqual(self.row()['status'], 'Vyžaduje zásah')

    def test_worker_table_is_independent_of_missing_task_snapshot(self):
        # No snapshot path is configured or accessed by this optional plugin.
        table = plugin.collect({}, {'snapshot_path': str(self.root / 'missing'), 'workers': [self.config]})['tables'][0]
        self.assertEqual(table['id'], 'workers')
        self.assertEqual(len(table['rows']), 1)


if __name__ == '__main__':
    unittest.main()
