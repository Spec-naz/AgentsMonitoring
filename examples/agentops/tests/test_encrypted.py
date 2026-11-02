"""age adapter acceptance. Only ephemeral fixture identities; never production keys.

Set explicit AGENTOPS_TEST_AGE and AGENTOPS_TEST_AGE_KEYGEN paths to independently
verified official binaries for the real encrypted roundtrip tests. No downloads occur.
"""
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from agentops import continuity
from agentops import encrypted as enc

PUBLIC_FIXTURE = 'age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p'
AGE = os.environ.get('AGENTOPS_TEST_AGE')
KEYGEN = os.environ.get('AGENTOPS_TEST_AGE_KEYGEN')


class FixtureBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.project = self.base / 'reviewed-project'
        self.project.mkdir(mode=0o700)
        (self.project / 'main.py').write_text('fixture code\n')
        self.output = self.base / 'backup.age'
        self.stage = self.base / 'rehearsal'
        self.stage.mkdir(mode=0o700)
        self.identity = self.base / 'fixture-identity'
        self.identity.write_text('PRIVATE-FIXTURE-NOT-A-REAL-KEY')
        self.identity.chmod(0o600)

    def tool_stub(self):
        return patch.object(enc, '_tool', return_value=(Path('/verified/age'), 'v1.3.2', {'PATH': '/bin'}))

    def assert_no_scratch(self):
        self.assertFalse(list(self.base.glob('.agentops-encrypt-*')))
        self.assertFalse(list(self.base.glob('.agentops-decrypt-*')))


class AdapterSafetyTests(FixtureBase):
    def test_default_inventory_creates_no_output_or_plaintext(self):
        with patch.object(enc, 'capabilities', return_value={'available': False}), \
             patch.object(enc.continuity, 'export_backup', side_effect=AssertionError('plaintext created')):
            evidence = enc.export_encrypted({'project': self.project}, self.output, PUBLIC_FIXTURE)
        self.assertTrue(evidence['dry_run'])
        self.assertFalse(evidence['capabilities']['available'])
        self.assertFalse(self.output.exists())
        self.assert_no_scratch()

    def test_recipient_is_explicit_and_secret_export_stays_disabled(self):
        for recipient in ('', None, '--passphrase', 'AGE-SECRET-KEY-1FIXTURE', 'age1plugin-something'):
            with self.subTest(recipient=recipient), self.assertRaises(enc.EncryptedError):
                enc.export_encrypted({'project': self.project}, self.output, recipient)
        with self.assertRaises(continuity.ContinuityError):
            enc.export_encrypted({'project': self.project}, self.output, PUBLIC_FIXTURE, include_secrets=True)
        self.assertFalse(self.output.exists())

    def test_missing_or_unusable_tool_fails_before_plaintext_creation(self):
        with patch.object(enc.continuity, 'export_backup') as export:
            with self.assertRaises(enc.EncryptedError):
                enc.export_encrypted({'project': self.project}, self.output, PUBLIC_FIXTURE,
                                     age_executable=self.base / 'no-such-age', dry_run=False)
            export.assert_not_called()
        self.assert_no_scratch()
        self.assertFalse(enc.capabilities(self.base / 'no-such-age')['available'])

    def test_encrypt_failure_cleans_private_plaintext_and_never_publishes(self):
        seen = []
        def fail(_tool, _env, args, _timeout):
            plaintext = Path(args[-1])
            self.assertTrue(plaintext.is_file())
            self.assertEqual(stat.S_IMODE(plaintext.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(plaintext.stat().st_mode), 0o600)
            seen.append(plaintext.parent)
            raise enc.EncryptedError('External tool failed; diagnostics hidden.')
        with self.tool_stub(), patch.object(enc, '_run', side_effect=fail):
            with self.assertRaises(enc.EncryptedError):
                enc.export_encrypted({'project': self.project}, self.output, PUBLIC_FIXTURE, dry_run=False)
        self.assertTrue(seen)
        self.assertFalse(any(path.exists() for path in seen))
        self.assertFalse(self.output.exists())
        self.assert_no_scratch()

    def test_decrypt_failure_cleans_partial_plaintext_and_leaves_staging_empty(self):
        self.output.write_bytes(enc.AGE_HEADER + b'fixture ciphertext')
        seen = []
        def fail(_tool, _env, args, _timeout):
            plain = Path(args[args.index('--output') + 1])
            plain.write_text('partial decrypted FIXTURE')
            self.assertEqual(stat.S_IMODE(plain.parent.stat().st_mode), 0o700)
            seen.append(plain)
            raise enc.EncryptedError('Authentication failed; diagnostics hidden.')
        with self.tool_stub(), patch.object(enc, '_run', side_effect=fail):
            with self.assertRaises(enc.EncryptedError) as error:
                enc.rehearse_encrypted(self.output, self.stage, self.identity)
        self.assertNotIn('PRIVATE-FIXTURE', str(error.exception))
        self.assertFalse(any(path.exists() for path in seen))
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assert_no_scratch()

    def test_overwrite_inside_root_and_symlink_destinations_are_rejected(self):
        self.output.write_bytes(b'keep existing')
        with self.tool_stub(), self.assertRaises(enc.EncryptedError):
            enc.export_encrypted({'project': self.project}, self.output, PUBLIC_FIXTURE, dry_run=False)
        self.assertEqual(self.output.read_bytes(), b'keep existing')
        self.output.unlink()
        with self.tool_stub(), self.assertRaises(enc.EncryptedError):
            enc.export_encrypted({'project': self.project}, self.project / 'inside.age', PUBLIC_FIXTURE, dry_run=False)
        self.output.symlink_to(self.project / 'does-not-exist')
        with self.tool_stub(), self.assertRaises(enc.EncryptedError):
            enc.export_encrypted({'project': self.project}, self.output, PUBLIC_FIXTURE, dry_run=False)
        self.assertFalse((self.project / 'does-not-exist').exists())
        self.assert_no_scratch()

    def test_rehearsal_rejects_nonempty_public_or_symlink_stage_and_bad_identity(self):
        self.output.write_bytes(enc.AGE_HEADER)
        (self.stage / 'live-data').write_bytes(b'never replace')
        with self.assertRaises(enc.EncryptedError):
            enc.rehearse_encrypted(self.output, self.stage, self.identity)
        self.assertEqual((self.stage / 'live-data').read_bytes(), b'never replace')
        (self.stage / 'live-data').unlink()
        self.stage.chmod(0o755)
        with self.assertRaises(enc.EncryptedError):
            enc.rehearse_encrypted(self.output, self.stage, self.identity)
        self.stage.chmod(0o700)
        link = self.base / 'stage-link'
        link.symlink_to(self.stage, target_is_directory=True)
        with self.assertRaises(enc.EncryptedError):
            enc.rehearse_encrypted(self.output, link, self.identity)
        self.identity.chmod(0o644)
        with self.assertRaises(enc.EncryptedError):
            enc.rehearse_encrypted(self.output, self.stage, self.identity)

    def test_external_diagnostics_are_never_exposed(self):
        for response in (subprocess.CompletedProcess([], 1),):
            with patch.object(enc.subprocess, 'run', return_value=response):
                with self.assertRaises(enc.EncryptedError) as error:
                    enc._run(Path('/verified/age'), {}, ['--identity', self.identity], 10)
            self.assertNotIn('PRIVATE-FIXTURE', str(error.exception))
        with patch.object(enc.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['age', 'PRIVATE-FIXTURE'], 1)):
            with self.assertRaises(enc.EncryptedError) as error:
                enc._run(Path('/verified/age'), {}, [], 10)
        self.assertNotIn('PRIVATE-FIXTURE', str(error.exception))


@unittest.skipUnless(AGE and KEYGEN, 'Explicit independently verified age/age-keygen fixture paths required')
class RealAgeTests(FixtureBase):
    def setUp(self):
        super().setUp()
        self.identity.unlink()
        result = subprocess.run([KEYGEN, '--output', str(self.identity)],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0)  # Never include tool output/private identity in assertions.
        self.identity.chmod(0o600)
        derived = subprocess.run([KEYGEN, '-y', str(self.identity)],
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
        self.assertEqual(derived.returncode, 0)
        self.recipient = derived.stdout.decode('ascii').strip()

    def export_real(self):
        (self.project / '.env').write_text('FIXTURE_OMITTED_SECRET=not-for-export')
        return enc.export_encrypted({'project': self.project}, self.output, self.recipient,
                                    dry_run=False, age_executable=AGE)

    def test_real_roundtrip_sqlite_and_default_exclusions(self):
        ledger = self.project / 'ledger.sqlite3'
        with sqlite3.connect(ledger) as db:
            db.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY, title TEXT)')
            db.execute("INSERT INTO tasks(title) VALUES ('fixture only')")
        (self.project / '.env').write_text('FIXTURE_OMITTED_SECRET=not-for-export')
        evidence = enc.export_encrypted({'project': self.project}, self.output, self.recipient,
                                        ledger_path=ledger, dry_run=False, age_executable=AGE)
        self.assertFalse(evidence['encryption']['secrets_included'])
        self.assertEqual(evidence['encryption']['tool'], 'age')
        self.assertNotEqual(evidence['archive_sha256'], evidence['plaintext_archive_sha256'])
        self.assertTrue(self.output.read_bytes().startswith(enc.AGE_HEADER))
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        restored = enc.rehearse_encrypted(self.output, self.stage, self.identity, age_executable=AGE,
                                         expected_sha256=evidence['archive_sha256'])
        self.assertTrue(restored['decryption']['authenticated_decryption'])
        self.assertTrue(restored['expected_archive_hash_verified'])
        self.assertEqual(restored['sqlite_integrity'], 'passed')
        self.assertEqual((self.stage / 'files/project/main.py').read_text(), 'fixture code\n')
        self.assertFalse((self.stage / 'files/project/.env').exists())
        self.assertFalse((self.stage / 'files/project/ledger.sqlite3').exists())
        self.assertTrue((self.stage / 'files/ledger/ledger.sqlite3').is_file())
        self.assert_no_scratch()

    def test_real_ciphertext_tamper_fails_without_restoring_partial_data(self):
        self.export_real()
        content = bytearray(self.output.read_bytes())
        content[-1] ^= 1
        self.output.write_bytes(content)
        with self.assertRaises(enc.EncryptedError):
            enc.rehearse_encrypted(self.output, self.stage, self.identity, age_executable=AGE)
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assert_no_scratch()

    def test_real_wrong_identity_fails_without_revealing_key(self):
        self.export_real()
        wrong = self.base / 'wrong-identity'
        result = subprocess.run([KEYGEN, '--output', str(wrong)], stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0)
        wrong.chmod(0o600)
        with self.assertRaises(enc.EncryptedError):
            enc.rehearse_encrypted(self.output, self.stage, wrong, age_executable=AGE)
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assert_no_scratch()

    def test_real_atomic_publication_never_overwrites_raced_destination(self):
        original = enc._publish
        def race(source, destination):
            Path(destination).write_bytes(b'concurrent owner file')
            original(source, destination)
        with patch.object(enc, '_publish', side_effect=race):
            with self.assertRaises(FileExistsError):
                self.export_real()
        self.assertEqual(self.output.read_bytes(), b'concurrent owner file')
        self.assert_no_scratch()

    def test_real_encrypted_traversal_archive_is_rejected_and_plaintext_cleaned(self):
        malicious = self.base / 'malicious-fixture.zip'
        with zipfile.ZipFile(malicious, 'w') as z:
            z.writestr('../escape', b'fixture only')
        result = subprocess.run([AGE, '--encrypt', '--recipient', self.recipient,
                                 '--output', str(self.output), str(malicious)],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0)
        with self.assertRaises(continuity.ContinuityError):
            enc.rehearse_encrypted(self.output, self.stage, self.identity, age_executable=AGE)
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assertFalse((self.base / 'escape').exists())
        self.assert_no_scratch()

    def test_real_digest_mismatch_does_not_decrypt(self):
        self.export_real()
        with patch.object(enc, '_run') as run:
            with self.assertRaises(enc.EncryptedError):
                enc.rehearse_encrypted(self.output, self.stage, self.identity,
                                      age_executable=AGE, expected_sha256='0' * 64)
            run.assert_not_called()
        self.assertEqual(list(self.stage.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
