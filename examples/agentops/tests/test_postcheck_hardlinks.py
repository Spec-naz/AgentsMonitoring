"""A report must not pass and then change checked data through a hard-linked ledger."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from agentops.ledger import Ledger
from agentops.postcheck import SPEC_SCHEMA

ROOT = Path(__file__).resolve().parent.parent


class HardLinkedEvidence(unittest.TestCase):
    def scenario(self, side=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'checked'
            target.mkdir()
            database = root / 'tasks.sqlite'
            ledger = Ledger(database)
            task = ledger.create('synthetic', 'maker', 'maker')['id']
            ledger.change(task, 'maker', 'start')
            ledger.close()
            writable = Path(str(database) + side) if side else database
            if side:
                writable.write_bytes(b'synthetic side file')
            os.link(writable, target / 'data')
            before = (target / 'data').read_bytes()
            spec = {'schema': SPEC_SCHEMA, 'target_dir': str(target), 'exact_directory': True,
                    'files': [{'path': 'data', 'sha256': hashlib.sha256(before).hexdigest()}],
                    'must_not_exist': [], 'unresolved_effects': []}
            spec_path = root / 'spec.json'
            spec_path.write_text(json.dumps(spec))
            report = root / 'report.json'
            run = subprocess.run([sys.executable, '-m', 'agentops', '--db', str(database), 'verify',
                                  '--spec', str(spec_path), '--output', str(report), '--task', task,
                                  '--actor', 'maker'], env={**os.environ, 'PYTHONPATH': str(ROOT)},
                                 capture_output=True, text=True, timeout=10)
            self.assertNotEqual(run.returncode, 0, run.stdout)
            self.assertIn('hard links', run.stderr)
            self.assertFalse(report.exists())
            self.assertEqual((target / 'data').read_bytes(), before)

    def test_hard_linked_ledger_cannot_modify_the_verified_file(self):
        self.scenario()

    def test_hard_linked_side_files_are_refused_before_writing(self):
        for side in ('-wal', '-shm', '-journal'):
            with self.subTest(side=side):
                self.scenario(side)
