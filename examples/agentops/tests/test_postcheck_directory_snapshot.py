"""Independent real-filesystem reproductions of mutation before the final recheck."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from agentops import postcheck


class DirectoryMutation(unittest.TestCase):
    def scenario(self, nested=False, exact=True, add=True):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'target'
            parent = target / 'a/b' if nested else target
            parent.mkdir(parents=True)
            (parent / 'data').write_bytes(b'expected')
            relative = 'a/b/data' if nested else 'data'
            spec = {'schema': postcheck.SPEC_SCHEMA, 'target_dir': str(target), 'exact_directory': exact,
                    'files': [{'path': relative, 'sha256': hashlib.sha256(b'expected').hexdigest()}],
                    'must_not_exist': [], 'unresolved_effects': []}
            original = postcheck._recheck
            def before_final(*args):
                if add:
                    (parent / 'unexpected').write_bytes(b'added')
                return original(*args)
            with mock.patch.object(postcheck, '_recheck', before_final):
                return postcheck.run(spec)

    def test_root_entry_added_before_final_recheck_is_not_pass(self):
        result = self.scenario()
        self.assertEqual(result['verdict'], 'unverified')
        self.assertIn('directory-changed-during-check', [r['reason'] for r in result['reasons']])

    def test_nested_entry_added_before_final_recheck_is_not_pass(self):
        result = self.scenario(nested=True)
        self.assertEqual(result['verdict'], 'unverified')
        self.assertIn('directory-changed-during-check', [r['reason'] for r in result['reasons']])

    def test_unchanged_nested_directory_passes(self):
        self.assertEqual(self.scenario(nested=True, add=False)['verdict'], 'pass')

    def test_unlisted_changes_allowed_when_exact_directory_is_false(self):
        self.assertEqual(self.scenario(nested=True, exact=False)['verdict'], 'pass')
