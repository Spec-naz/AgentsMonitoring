"""End-to-end fixtures for `agentops verify`: real files, the real CLI as a new process, a temporary ledger."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agentops import postcheck as p
from agentops.ledger import Conflict, Ledger

ROOT = Path(__file__).parents[1]
GOOD = b'schvaleny obsah\n'
SHA = hashlib.sha256(GOOD).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.target = self.root / 'cil'
        self.target.mkdir()
        (self.target / 'vystup.md').write_bytes(GOOD)
        self.db = self.root / 'state/tasks.sqlite'
        self.count = 0

    def spec(self, **change):
        return {'schema': p.SPEC_SCHEMA, 'target_dir': str(self.target), 'exact_directory': True,
                'files': [{'path': 'vystup.md', 'sha256': SHA}], 'must_not_exist': [], 'unresolved_effects': [], **change}

    def cli(self, spec, *extra):
        self.count += 1
        spec_path = self.root / f'spec-{self.count}.json'
        spec_path.write_text(json.dumps(spec))
        report = self.root / 'dukazy' / f'report-{self.count}.json'
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        done = subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), 'verify', '--spec', str(spec_path),
                               '--output', str(report), *extra], cwd=self.root, env=env, capture_output=True, text=True, timeout=60)
        return done, report

    def verdict(self, spec, expected_verdict, reason=None):
        done, report = self.cli(spec)
        self.assertEqual(done.returncode, 0 if expected_verdict == 'pass' else 2, done.stderr)
        data = json.loads(report.read_text())
        self.assertEqual(data['verdict'], expected_verdict)
        if reason:
            self.assertIn(reason, [row['reason'] for row in data['reasons']])
        return data


class Scenarios(Base):
    def test_correct_files_pass_and_report_real_observation(self):
        data = self.verdict(self.spec(), 'pass')
        self.assertEqual(data['files'][0]['found_sha256'], SHA)
        self.assertEqual(data['files'][0]['size'], len(GOOD))
        self.assertTrue(data['data_readable'])

    def test_correct_bytes_in_another_folder_fail(self):
        other = self.root / 'jina'
        other.mkdir()
        (self.target / 'vystup.md').rename(other / 'vystup.md')
        self.verdict(self.spec(), 'fail', 'file-missing')

    def test_target_folder_missing_fails_even_when_bytes_exist_elsewhere(self):
        data = self.verdict(self.spec(target_dir=str(self.root / 'neni')), 'fail', 'target-missing')
        self.assertFalse(data['data_readable'])
        self.assertEqual(data['files'][0]['verdict'], 'not-checked')

    def test_target_reached_through_symlink_is_not_the_agreed_folder(self):
        link = self.root / 'odkaz'
        link.symlink_to(self.target)
        self.verdict(self.spec(target_dir=str(link)), 'fail', 'target-not-exact-directory')

    def test_symlink_inside_the_path_is_not_followed(self):
        link = self.root / 'odkaz'
        link.symlink_to(self.root)
        self.verdict(self.spec(target_dir=str(link / 'cil')), 'fail', 'target-not-exact-directory')

    def test_target_that_is_a_file_fails(self):
        self.verdict(self.spec(target_dir=str(self.target / 'vystup.md')), 'fail', 'target-not-exact-directory')

    def test_missing_file_fails(self):
        (self.target / 'vystup.md').unlink()
        self.verdict(self.spec(), 'fail', 'file-missing')

    def test_changed_content_fails_and_reports_found_hash(self):
        (self.target / 'vystup.md').write_bytes(b'jiny obsah\n')
        data = self.verdict(self.spec(), 'fail', 'file-content-mismatch')
        self.assertEqual(data['files'][0]['found_sha256'], hashlib.sha256(b'jiny obsah\n').hexdigest())

    def test_claim_in_spec_cannot_replace_reading_the_file(self):
        """The same expected hash with different bytes on disk: the claim alone must not pass."""
        (self.target / 'vystup.md').write_bytes(b'')
        self.verdict(self.spec(), 'fail', 'file-content-mismatch')

    def test_unknown_effect_is_not_a_pass_and_forbids_repeat(self):
        data = self.verdict(self.spec(unresolved_effects=[{'id': 'upload-1', 'note': 'timeout, vzdáleny stav neznamy'}]),
                            'unverified', 'unresolved-effects')
        self.assertIs(data['repeat_action_allowed'], False)
        self.assertEqual(data['actions_performed'], [])

    def test_fail_wins_over_unverified(self):
        (self.target / 'vystup.md').unlink()
        self.verdict(self.spec(unresolved_effects=[{'id': 'a', 'note': 'n'}]), 'fail')

    def test_unexpected_entry_fails_only_when_exact_is_requested(self):
        (self.target / 'navic.txt').write_text('x')
        data = self.verdict(self.spec(), 'fail', 'unexpected-entries')
        self.assertEqual(data['unexpected_entries']['first'], ['navic.txt'])
        self.assertFalse(self.verdict(self.spec(exact_directory=False), 'pass')['unexpected_entries']['checked'])

    def test_nested_files_and_unexpected_entry_beside_them(self):
        (self.target / 'a/b').mkdir(parents=True)
        (self.target / 'vystup.md').rename(self.target / 'a/b/vystup.md')
        spec = self.spec(files=[{'path': 'a/b/vystup.md', 'sha256': SHA}])
        self.verdict(spec, 'pass')
        (self.target / 'a/navic').write_text('x')
        self.assertEqual(self.verdict(spec, 'fail')['unexpected_entries']['first'], ['a/navic'])

    def test_symlinked_file_fails_even_with_right_bytes(self):
        real = self.root / 'jinde.md'
        real.write_bytes(GOOD)
        (self.target / 'vystup.md').unlink()
        (self.target / 'vystup.md').symlink_to(real)
        self.verdict(self.spec(), 'fail', 'file-not-regular-file')

    def test_symlinked_parent_of_a_file_fails(self):
        other = self.root / 'jina'
        other.mkdir()
        (other / 'vystup.md').write_bytes(GOOD)
        (self.target / 'podslozka').symlink_to(other)
        self.verdict(self.spec(exact_directory=False, files=[{'path': 'podslozka/vystup.md', 'sha256': SHA}]),
                     'fail', 'file-not-regular-file')

    def test_fifo_does_not_hang_and_fails(self):
        (self.target / 'vystup.md').unlink()
        os.mkfifo(self.target / 'vystup.md')
        self.verdict(self.spec(), 'fail', 'file-not-regular-file')

    def test_directory_in_place_of_a_file_fails(self):
        (self.target / 'vystup.md').unlink()
        (self.target / 'vystup.md').mkdir()
        self.verdict(self.spec(), 'fail', 'file-not-regular-file')

    @unittest.skipIf(os.geteuid() == 0, 'root ignores permissions')
    def test_unreadable_file_is_unverified_not_fail_or_pass(self):
        (self.target / 'vystup.md').chmod(0)
        self.addCleanup((self.target / 'vystup.md').chmod, 0o600)
        data = self.verdict(self.spec(), 'unverified', 'file-unreadable')
        self.assertFalse(data['data_readable'])

    @unittest.skipIf(os.geteuid() == 0, 'root ignores permissions')
    def test_unreadable_target_is_unverified(self):
        self.target.chmod(0)
        self.addCleanup(self.target.chmod, 0o700)
        self.verdict(self.spec(), 'unverified', 'target-unreadable')

    def test_must_not_exist(self):
        wrong = self.root / 'spatne/vystup.md'
        spec = self.spec(must_not_exist=[str(wrong)])
        self.verdict(spec, 'pass')
        wrong.parent.mkdir()
        wrong.write_bytes(GOOD)
        self.verdict(spec, 'fail', 'must-not-exist-exists')

    def test_file_changing_during_read_is_unverified(self):
        """A real append between two reads of the file."""
        real = os.read
        appended = []
        def appending(descriptor, size):
            chunk = real(descriptor, size)
            if not appended:
                appended.append(1)
                with open(self.target / 'vystup.md', 'ab') as handle:
                    handle.write(b'x')
            return chunk
        with mock.patch.object(p.os, 'read', appending):
            result = p.run(self.spec())
        self.assertEqual(result['verdict'], 'unverified')
        self.assertIn('file-changed-during-read', [row['reason'] for row in result['reasons']])

    def test_too_large_is_unverified(self):
        with mock.patch.object(p, 'MAX_BYTES', 1):
            result = p.run(self.spec())
        self.assertEqual(result['verdict'], 'unverified')
        self.assertIsNone(result['files'][0]['found_sha256'])

    def test_listing_limit_is_unverified(self):
        (self.target / 'navic').write_text('x')
        with mock.patch.object(p, 'MAX_LISTED', 1):
            result = p.run(self.spec())
        self.assertEqual(result['verdict'], 'unverified')
        self.assertIn('directory-listing-incomplete', [row['reason'] for row in result['reasons']])


class ReplacedDuringCheck(Base):
    """Real rename/replace on disk, timed with a hook right after the file is hashed."""

    def after_hash(self, action, spec=None):
        real = p._hash_file
        done = []
        def hooked(descriptor, budget):
            result = real(descriptor, budget)
            if not done:
                done.append(1)
                action()
            return result
        with mock.patch.object(p, '_hash_file', hooked):
            return p.run(spec or self.spec())

    def assert_not_pass(self, result, reason):
        self.assertEqual(result['verdict'], 'unverified')
        self.assertIn(reason, [row['reason'] for row in result['reasons']])
        self.assertFalse(result['data_readable'])
        self.assertIsNone(result['files'][0]['found_sha256'])

    def test_file_atomically_replaced(self):
        other = self.root / 'nove'
        other.write_bytes(b'jine bajty')
        self.assert_not_pass(self.after_hash(lambda: os.replace(other, self.target / 'vystup.md')), 'file-replaced-during-check')

    def test_file_replaced_by_identical_bytes_is_still_not_the_checked_file(self):
        other = self.root / 'nove'
        other.write_bytes(GOOD)
        self.assert_not_pass(self.after_hash(lambda: os.replace(other, self.target / 'vystup.md')), 'file-replaced-during-check')

    def test_file_removed(self):
        self.assert_not_pass(self.after_hash((self.target / 'vystup.md').unlink), 'file-replaced-during-check')

    def test_file_rewritten_in_place_with_same_size(self):
        def rewrite():
            time.sleep(0.05)  # beyond the filesystem's timestamp granularity
            (self.target / 'vystup.md').write_bytes(b'x' * len(GOOD))
        self.assert_not_pass(self.after_hash(rewrite), 'file-replaced-during-check')

    def test_target_directory_swapped(self):
        def swap():
            self.target.rename(self.root / 'stara')
            self.target.mkdir()
            (self.target / 'vystup.md').write_bytes(b'jine bajty')
        result = self.after_hash(swap)
        self.assert_not_pass(result, 'target-replaced-during-check')
        self.assertEqual(result['target_status'], 'replaced-during-check')

    def test_target_directory_swapped_with_identical_bytes(self):
        def swap():
            self.target.rename(self.root / 'stara')
            self.target.mkdir()
            (self.target / 'vystup.md').write_bytes(GOOD)
        self.assert_not_pass(self.after_hash(swap), 'target-replaced-during-check')

    def test_target_directory_removed(self):
        self.assert_not_pass(self.after_hash(lambda: self.target.rename(self.root / 'pryc')), 'target-replaced-during-check')

    def test_parent_of_target_swapped(self):
        nested = self.root / 'rodic/cil'
        nested.mkdir(parents=True)
        (nested / 'vystup.md').write_bytes(GOOD)
        def swap():
            (self.root / 'rodic').rename(self.root / 'rodic-stary')
            nested.mkdir(parents=True)
            (nested / 'vystup.md').write_bytes(GOOD)
        self.assert_not_pass(self.after_hash(swap, self.spec(target_dir=str(nested))), 'target-replaced-during-check')

    def test_subfolder_swapped(self):
        (self.target / 'a').mkdir()
        (self.target / 'vystup.md').rename(self.target / 'a/vystup.md')
        def swap():
            (self.target / 'a').rename(self.root / 'stare-a')
            (self.target / 'a').mkdir()
            (self.target / 'a/vystup.md').write_bytes(GOOD)
        spec = self.spec(files=[{'path': 'a/vystup.md', 'sha256': SHA}])
        self.assert_not_pass(self.after_hash(swap, spec), 'file-replaced-during-check')

    def test_replacing_a_later_file_does_not_hide_an_earlier_one(self):
        (self.target / 'druhy.md').write_bytes(GOOD)
        other = self.root / 'nove'
        other.write_bytes(GOOD)
        spec = self.spec(files=[{'path': 'vystup.md', 'sha256': SHA}, {'path': 'druhy.md', 'sha256': SHA}])
        result = self.after_hash(lambda: os.replace(other, self.target / 'vystup.md'), spec)
        self.assertEqual(result['verdict'], 'unverified')
        self.assertEqual([row['status'] for row in result['files']], ['replaced-during-check', 'match'])

    def test_a_quiet_check_still_passes_and_internal_identity_stays_out_of_the_report(self):
        result = self.after_hash(lambda: None)
        self.assertEqual(result['verdict'], 'pass')
        self.assertNotIn('_identity', result['files'][0])
        json.dumps(result)


class DataReadable(Base):
    def test_meaning(self):
        self.assertTrue(p.run(self.spec())['data_readable'])
        (self.target / 'vystup.md').write_bytes(b'jine')
        result = p.run(self.spec())
        self.assertEqual(result['verdict'], 'fail')
        self.assertTrue(result['data_readable'])  # read fine, the bytes are wrong
        (self.target / 'vystup.md').unlink()
        result = p.run(self.spec())
        self.assertEqual(result['verdict'], 'fail')
        self.assertFalse(result['data_readable'])
        (self.target / 'vystup.md').symlink_to(self.root)
        self.assertFalse(p.run(self.spec())['data_readable'])
        (self.target / 'vystup.md').unlink()
        os.mkfifo(self.target / 'vystup.md')
        self.assertFalse(p.run(self.spec())['data_readable'])

    def test_one_missing_file_makes_the_whole_result_not_readable(self):
        spec = self.spec(files=[{'path': 'vystup.md', 'sha256': SHA}, {'path': 'chybi.md', 'sha256': SHA}])
        self.assertFalse(p.run(spec)['data_readable'])

    def test_application_health_stays_not_checked(self):
        for spec in (self.spec(), self.spec(target_dir=str(self.root / 'neni'))):
            self.assertEqual(p.run(spec)['application_health'], 'not_checked')


class OwnEffects(Base):
    """The real CLI: a refused run exits 1 and leaves no report, no parents and no ledger."""

    def run_cli(self, spec, output, *extra, db=None):
        spec_path = self.root / f'spec-{self.count}.json'
        self.count += 1
        spec_path.write_text(json.dumps(spec))
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        return subprocess.run([sys.executable, '-m', 'agentops', '--db', str(db or self.db), 'verify', '--spec', str(spec_path),
                               '--output', str(output), *extra], cwd=self.root, env=env, capture_output=True, text=True, timeout=60)

    def refused(self, spec, output, *extra, db=None):
        before = sorted(str(path) for path in self.target.rglob('*'))
        done = self.run_cli(spec, output, *extra, db=db)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertEqual(done.stdout, '')
        self.assertEqual(before, sorted(str(path) for path in self.target.rglob('*')))
        return done

    def test_report_inside_target(self):
        self.refused(self.spec(), self.target / 'receipt.json')
        self.assertFalse((self.target / 'receipt.json').exists())

    def test_report_inside_target_without_exact_directory(self):
        self.refused(self.spec(exact_directory=False), self.target / 'receipt.json')

    def test_report_in_a_new_folder_inside_target(self):
        self.refused(self.spec(), self.target / 'nova/slozka/receipt.json')
        self.assertFalse((self.target / 'nova').exists())

    def test_report_through_a_symlink_alias_of_target(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.target)
        self.refused(self.spec(), alias / 'receipt.json')
        self.assertFalse((self.target / 'receipt.json').exists())

    def test_report_alias_when_target_itself_is_written_through_a_symlink(self):
        link = self.root / 'odkaz'
        link.symlink_to(self.target)
        self.refused(self.spec(target_dir=str(link)), self.target / 'receipt.json')

    def test_report_on_forbidden_path(self):
        forbidden = self.root / 'zakazano.json'
        self.refused(self.spec(must_not_exist=[str(forbidden)]), forbidden)
        self.assertFalse(forbidden.exists())

    def test_report_inside_target_and_forbidden_as_in_the_review_reproduction(self):
        receipt = self.target / 'receipt.json'
        self.refused(self.spec(must_not_exist=[str(receipt)]), receipt)
        self.assertFalse(receipt.exists())

    def test_report_would_create_a_forbidden_parent(self):
        forbidden = self.root / 'zakazana-slozka'
        self.refused(self.spec(must_not_exist=[str(forbidden)]), forbidden / 'podslozka/receipt.json')
        self.assertFalse(forbidden.exists())

    def test_report_through_alias_onto_forbidden_path(self):
        (self.root / 'dukazy').mkdir()
        alias = self.root / 'alias'
        alias.symlink_to(self.root / 'dukazy')
        forbidden = self.root / 'dukazy/receipt.json'
        self.refused(self.spec(must_not_exist=[str(forbidden)]), alias / 'receipt.json')
        self.assertFalse(forbidden.exists())

    def test_ledger_inside_target(self):
        db = self.target / 'tasks.sqlite'
        self.refused(self.spec(), self.root / 'r.json', '--task', 'x', '--actor', 'tvurce', db=db)
        self.assertFalse(db.exists())
        self.assertFalse((self.root / 'r.json').exists())

    def test_ledger_in_a_new_folder_inside_target(self):
        db = self.target / 'stav/tasks.sqlite'
        self.refused(self.spec(), self.root / 'r.json', '--task', 'x', '--actor', 'tvurce', db=db)
        self.assertFalse(db.parent.exists())

    def test_ledger_through_a_symlink_alias_of_target(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.target)
        self.refused(self.spec(), self.root / 'r.json', '--task', 'x', '--actor', 'tvurce', db=alias / 'tasks.sqlite')

    def test_ledger_side_files_on_a_forbidden_path(self):
        db = self.root / 'state2/tasks.sqlite'
        for suffix in ('', '-wal', '-shm', '-journal'):
            with self.subTest(suffix):
                self.refused(self.spec(must_not_exist=[str(db) + suffix]), self.root / 'r.json', '--task', 'x', '--actor', 'tvurce', db=db)
                self.assertFalse(db.exists())

    def test_ledger_is_not_judged_when_no_task_is_recorded(self):
        """Without --task the ledger is never opened, so a ledger path there is harmless."""
        db = self.target / 'nepouzito.sqlite'
        done = self.run_cli(self.spec(), self.root / 'r.json', db=db)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(db.exists())

    def test_report_beside_target_is_fine(self):
        done = self.run_cli(self.spec(), self.root / 'r.json')
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_similar_folder_name_is_not_inside_target(self):
        done = self.run_cli(self.spec(), self.root / 'cil-dukazy/r.json')
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_real_task_run_leaves_target_and_forbidden_paths_clean(self):
        ledger = Ledger(self.db)
        self.addCleanup(ledger.close)
        task = ledger.create('ukol', 'tvurce', 'tvurce')['id']
        ledger.change(task, 'tvurce', 'start')
        forbidden = self.root / 'zakazano'
        done = self.run_cli(self.spec(must_not_exist=[str(forbidden)]), self.root / 'r.json', '--task', task, '--actor', 'tvurce')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(os.listdir(self.target), ['vystup.md'])
        self.assertFalse(forbidden.exists())
        self.assertEqual(ledger.latest_postcheck(task)['verdict'], 'pass')


class Evidence(Base):
    def test_report_has_version_date_and_real_code_hash(self):
        data = self.verdict(self.spec(), 'pass')
        self.assertEqual(data['code']['agentops_version'], p.__version__)
        self.assertEqual(data['code']['postcheck_sha256'], hashlib.sha256((ROOT / 'agentops/postcheck.py').read_bytes()).hexdigest())
        self.assertRegex(data['checked_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(data['evaluated_by'], 'rules, not a model')

    def test_report_says_what_was_not_checked_and_authorizes_nothing(self):
        data = self.verdict(self.spec(), 'pass')
        self.assertEqual(data['application_health'], 'not_checked')
        self.assertIn('application_health', data['not_verified'])
        self.assertIs(data['deployment_authorized'], False)

    def test_report_contains_no_file_content(self):
        done, report = self.cli(self.spec())
        self.assertNotIn(GOOD.decode().strip(), report.read_text())
        self.assertNotIn(GOOD.decode().strip(), done.stdout)

    def test_existing_report_is_not_overwritten(self):
        spec_path = self.root / 's.json'
        spec_path.write_text(json.dumps(self.spec()))
        report = self.root / 'r.json'
        report.write_text('puvodni')
        env = {**os.environ, 'PYTHONPATH': str(ROOT)}
        done = subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), 'verify', '--spec', str(spec_path),
                               '--output', str(report)], cwd=self.root, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(report.read_text(), 'puvodni')

    def test_report_is_private(self):
        _, report = self.cli(self.spec())
        self.assertEqual(report.stat().st_mode & 0o077, 0)

    def test_checked_data_is_left_untouched(self):
        path = self.target / 'vystup.md'
        before = (path.read_bytes(), path.stat().st_mtime_ns, sorted(os.listdir(self.target)))
        self.cli(self.spec())
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns, sorted(os.listdir(self.target))))

    def test_module_cannot_execute_or_reach_the_network(self):
        tree = ast.parse((ROOT / 'agentops/postcheck.py').read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split('.')[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add(node.module.split('.')[0])
        self.assertFalse(imported & {'subprocess', 'socket', 'urllib', 'http', 'ctypes', 'importlib', 'shutil'})
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(calls & {'system', 'popen', 'execv', 'spawnv', 'remove', 'unlink', 'rename', 'replace'})


class Validation(Base):
    def rejected(self, **change):
        done, report = self.cli(self.spec(**change))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertFalse(report.exists())

    def test_invalid_specs_give_no_report(self):
        for change in ({'target_dir': 'relativni'}, {'target_dir': '/a/../b'}, {'target_dir': '/'}, {'target_dir': '//a'},
                       {'target_dir': '/a/'}, {'exact_directory': 'ano'}, {'files': []}, {'schema': 'jine'},
                       {'files': [{'path': '../utek', 'sha256': SHA}]}, {'files': [{'path': '/abs', 'sha256': SHA}]},
                       {'files': [{'path': 'a/./b', 'sha256': SHA}]}, {'files': [{'path': 'x', 'sha256': 'abc'}]},
                       {'files': [{'path': 'x', 'sha256': SHA.upper()}]}, {'files': [{'path': 'x', 'sha256': SHA}] * 2},
                       {'must_not_exist': ['relativni']}, {'unresolved_effects': [{'id': 'a', 'note': ' '}]},
                       {'unresolved_effects': ['x']}, {'navic': 1}):
            with self.subTest(change=change):
                self.rejected(**change)

    def test_excluded_private_locations_are_refused_before_opening(self):
        for change in ({'target_dir': '/home/x/DANĚ/2026'}, {'target_dir': '/home/x/dane'}, {'target_dir': '/home/x/.ssh'},
                       {'files': [{'path': 'DANĚ/a.pdf', 'sha256': SHA}]}, {'files': [{'path': '.credentials.json', 'sha256': SHA}]},
                       {'files': [{'path': 'auth.json', 'sha256': SHA}]}, {'must_not_exist': ['/home/x/Daně/a']}):
            with self.subTest(change=change), mock.patch.object(p.os, 'open', side_effect=AssertionError('otevřeno')):
                with self.assertRaises(ValueError):
                    p.run(self.spec(**change))

    def test_decomposed_unicode_spelling_is_refused_too(self):
        with self.assertRaises(ValueError):
            p.validate(self.spec(target_dir='/home/x/DANE\u030c'))


class LedgerLink(Base):
    def setUp(self):
        super().setUp()
        self.db.parent.mkdir()
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)
        self.task = self.ledger.create('ukol', 'tvurce', 'tvurce')['id']
        self.ledger.change(self.task, 'tvurce', 'start')

    def review(self):
        self.ledger.change(self.task, 'tvurce', 'offer', target='kontrolor', purpose='review', artifact='a')
        self.ledger.change(self.task, 'kontrolor', 'accept')

    def done(self):
        return self.ledger.change(self.task, 'kontrolor', 'done', artifact='a')

    def test_task_without_check_completes_as_before(self):
        self.review()
        self.assertEqual(self.done()['state'], 'done')

    def test_failed_and_unverified_check_block_done(self):
        for verdict in ('fail', 'unverified'):
            self.ledger.record_postcheck(self.task, 'tvurce', verdict, '/r', 'x' * 64)
        self.review()
        with self.assertRaises(Conflict) as caught:
            self.done()
        self.assertIn('unverified', str(caught.exception))
        self.assertEqual(self.ledger.get(self.task)['state'], 'review')

    def test_newer_pass_allows_done_and_newer_fail_blocks_again(self):
        self.ledger.record_postcheck(self.task, 'tvurce', 'fail', '/r1', 'x' * 64)
        self.review()
        self.ledger.record_postcheck(self.task, 'kontrolor', 'pass', '/r2', 'y' * 64)
        self.ledger.record_postcheck(self.task, 'kontrolor', 'fail', '/r3', 'z' * 64)
        with self.assertRaises(Conflict):
            self.done()
        self.ledger.record_postcheck(self.task, 'kontrolor', 'pass', '/r4', 'w' * 64)
        self.assertEqual(self.done()['state'], 'done')

    def test_only_owner_records_and_version_is_compared(self):
        with self.assertRaises(Conflict):
            self.ledger.record_postcheck(self.task, 'cizi', 'pass', '/r', 'x' * 64)
        with self.assertRaises(Conflict):
            self.ledger.record_postcheck(self.task, 'tvurce', 'pass', '/r', 'x' * 64, expected=99)
        self.assertIsNone(self.ledger.latest_postcheck(self.task))

    def test_state_is_not_changed_and_request_id_replays_once(self):
        before = self.ledger.get(self.task)
        first = self.ledger.record_postcheck(self.task, 'tvurce', 'pass', '/r', 'x' * 64, request_id='r1')
        second = self.ledger.record_postcheck(self.task, 'tvurce', 'pass', '/r', 'x' * 64, request_id='r1')
        self.assertEqual(first['state'], before['state'])
        self.assertEqual(first['version'], second['version'])
        self.assertEqual(len([e for e in self.ledger.events(self.task) if e['action'] == 'postcheck']), 1)

    def test_invalid_verdict_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.record_postcheck(self.task, 'tvurce', 'schvaleno', '/r', 'x' * 64)

    def test_cli_links_report_and_failed_check_blocks_done_end_to_end(self):
        (self.target / 'vystup.md').write_bytes(b'zmeneno')
        done, report = self.cli(self.spec(), '--task', self.task, '--actor', 'tvurce')
        self.assertEqual(done.returncode, 2, done.stderr)
        latest = self.ledger.latest_postcheck(self.task)
        self.assertEqual(latest['verdict'], 'fail')
        self.assertEqual(latest['report'], str(report))
        self.assertEqual(latest['report_sha256'], hashlib.sha256(report.read_bytes()).hexdigest())
        self.review()
        with self.assertRaises(Conflict):
            self.done()
        (self.target / 'vystup.md').write_bytes(GOOD)
        done, _ = self.cli(self.spec(), '--task', self.task, '--actor', 'kontrolor')
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.done()['state'], 'done')

    def test_cli_needs_both_task_and_actor(self):
        done, report = self.cli(self.spec(), '--task', self.task)
        self.assertEqual(done.returncode, 1)
        self.assertFalse(report.exists())

    def test_invalid_spec_records_nothing(self):
        done, _ = self.cli(self.spec(files=[]), '--task', self.task, '--actor', 'tvurce')
        self.assertEqual(done.returncode, 1)
        self.assertIsNone(self.ledger.latest_postcheck(self.task))


if __name__ == '__main__':
    unittest.main()
