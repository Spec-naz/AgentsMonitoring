"""Isolated continuity acceptance: no live configuration, credentials or services."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import warnings
import zipfile

from agentops import continuity as c


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'reviewed-project'
        self.root.mkdir()
        (self.root / 'main.py').write_text('print("fixture")\n')
        self.archive = self.base / 'backup.zip'
        self.stage = self.base / 'restore-rehearsal'
        self.stage.mkdir()

    def export(self, **kwargs):
        return c.export_backup({'project': self.root}, self.archive, dry_run=False, **kwargs)

    def crafted(self, paths, *, sqlite=False):
        files = []
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name, content, kind in paths:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (kind | 0o600) << 16
                z.writestr(info, content)
                files.append({'path': name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
                              'mode': 0o600, 'sqlite': sqlite})
            z.writestr('manifest.json', json.dumps({'schema': 1, 'files': files, 'includes_secrets': False}))

    def test_default_is_metadata_only_and_does_not_create_backup(self):
        with patch.object(c, '_safe_open_source', side_effect=AssertionError('content read')), \
             patch.object(c.sqlite3, 'connect', side_effect=AssertionError('database opened')):
            report = c.export_backup({'project': self.root}, self.archive)
        self.assertTrue(report['dry_run'])
        self.assertFalse(self.archive.exists())
        self.assertEqual(report['selected'][0]['path'], 'files/project/main.py')
        self.assertNotIn('sha256', report['selected'][0])

    def test_credentials_symlinks_devices_and_other_databases_are_excluded(self):
        for name in ('.env', '.env.production', 'auth.json', 'api-token', 'config.json', 'id_ed25519', 'key.pem', 'state.db', 'state.db-wal'):
            (self.root / name).write_text('FIXTURE_SECRET_DO_NOT_COPY')
        (self.root / '.git').mkdir()
        (self.root / '.git/config').write_text('FIXTURE_SECRET_DO_NOT_COPY')
        (self.root / '.venv').mkdir()
        (self.root / '.venv/native-binary').write_text('REBUILD_FOR_TARGET_ARCHITECTURE')
        (self.root / 'outside-link').symlink_to(self.base)
        os.mkfifo(self.root / 'pipe')
        report = self.export()
        self.assertGreaterEqual(len(report['excluded']), 12)
        with zipfile.ZipFile(self.archive) as z:
            self.assertEqual(set(z.namelist()), {'manifest.json', 'files/project/main.py'})
            for name in z.namelist():
                self.assertNotIn(b'FIXTURE_SECRET_DO_NOT_COPY', z.read(name))

    def test_unsafe_roots_and_secret_opt_in_fail_closed(self):
        link = self.base / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(c.ContinuityError):
            c.inventory({'project': link})
        fifo = self.base / 'fifo'
        os.mkfifo(fifo)
        with self.assertRaises(c.ContinuityError):
            c.inventory({'project': fifo})
        with self.assertRaises(c.ContinuityError):
            c.inventory({'home': Path.home()})
        with self.assertRaises(c.ContinuityError):
            self.export(include_secrets=True)
        self.assertFalse(self.archive.exists())

    def test_destination_cannot_overlap_sources_or_replace_existing_file(self):
        with self.assertRaises(c.ContinuityError):
            c.export_backup({'project': self.root}, self.root / 'bad.zip', dry_run=False)
        self.archive.write_bytes(b'leave-existing-file-alone')
        with self.assertRaises(c.ContinuityError):
            self.export()
        self.assertEqual(self.archive.read_bytes(), b'leave-existing-file-alone')

    def test_roundtrip_evidence_and_private_permissions(self):
        report = self.export()
        restored = c.restore_backup(self.archive, self.stage, expected_sha256=report['archive_sha256'])
        self.assertEqual((self.stage / 'files/project/main.py').read_bytes(), (self.root / 'main.py').read_bytes())
        self.assertTrue(restored['expected_archive_hash_verified'])
        self.assertEqual(restored['sqlite_integrity'], 'not configured')
        self.assertEqual(stat.S_IMODE(self.archive.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.stage / 'files/project/main.py').stat().st_mode), 0o600)
        self.assertTrue(any('not authenticity' in text for text in restored['limits']))

    def test_missing_roots_and_ledger_are_reported(self):
        report = c.inventory({'missing': self.base / 'absent'}, self.base / 'absent-ledger.sqlite3')
        self.assertEqual(len(report['missing']), 2)
        self.assertEqual(report['selected'], [])

    def test_restore_requires_empty_non_symlink_staging_and_preserves_live_file(self):
        self.export()
        live = self.stage / 'live-data'
        live.write_bytes(b'never replace')
        with self.assertRaises(c.ContinuityError):
            c.restore_backup(self.archive, self.stage)
        self.assertEqual(live.read_bytes(), b'never replace')
        link = self.base / 'staging-link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(c.ContinuityError):
            c.restore_backup(self.archive, link)
        self.assertEqual((self.root / 'main.py').read_text(), 'print("fixture")\n')

    def test_traversal_absolute_and_windows_paths_are_rejected_before_writes(self):
        for name in ('../escape', '/tmp/escape', 'files/project/../../escape', 'files\\project\\escape', 'C:/escape', 'files/project/CON', 'files/project/name.'):
            with self.subTest(name=name):
                self.crafted([(name, b'x', stat.S_IFREG)])
                with self.assertRaises(c.ContinuityError):
                    c.restore_backup(self.archive, self.stage)
                self.assertEqual(list(self.stage.iterdir()), [])
        self.assertFalse((self.base / 'escape').exists())

    def test_symlink_and_device_archive_entries_are_rejected(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFDIR):
            with self.subTest(kind=kind):
                self.crafted([('files/project/danger', b'outside', kind)])
                with self.assertRaises(c.ContinuityError):
                    c.restore_backup(self.archive, self.stage)
                self.assertEqual(list(self.stage.iterdir()), [])

    def test_duplicate_case_and_parent_child_collisions_are_rejected(self):
        for pair in [('files/project/a', 'files/project/a'), ('files/project/a', 'files/project/A'), ('files/project/a', 'files/project/a/b')]:
            with self.subTest(pair=pair), warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                self.crafted([(name, b'x', stat.S_IFREG) for name in pair])
                with self.assertRaises(c.ContinuityError):
                    c.restore_backup(self.archive, self.stage)
                self.assertEqual(list(self.stage.iterdir()), [])

    def test_tampered_content_hash_and_external_archive_digest_are_rejected(self):
        self.export()
        with self.assertRaises(c.ContinuityError):
            c.restore_backup(self.archive, self.stage, expected_sha256='0' * 64)
        with zipfile.ZipFile(self.archive) as z:
            manifest = z.read('manifest.json')
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('manifest.json', manifest)
            z.writestr('files/project/main.py', b'changed bytes')
        with self.assertRaises(c.ContinuityError):
            c.restore_backup(self.archive, self.stage)
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_same_size_tamper_is_caught_by_hash_not_just_length(self):
        self.export()
        with zipfile.ZipFile(self.archive) as z:
            manifest = z.read('manifest.json')
            original = z.read('files/project/main.py')
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('manifest.json', manifest)
            z.writestr('files/project/main.py', b'X' * len(original))
        with self.assertRaisesRegex(c.ContinuityError, 'hash'):
            c.restore_backup(self.archive, self.stage)
        self.assertEqual(list(self.stage.iterdir()), [])

    def test_export_rejects_case_collisions_before_creating_archive(self):
        (self.root / 'README').write_text('a')
        (self.root / 'readme').write_text('b')
        with self.assertRaises(c.ContinuityError):
            self.export()
        self.assertFalse(self.archive.exists())

    def test_atomic_publish_never_deletes_concurrently_created_destination(self):
        original_link = c.os.link
        def concurrent_destination(source, destination):
            Path(destination).write_bytes(b'concurrent-owner-file')
            return original_link(source, destination)
        with patch.object(c.os, 'link', side_effect=concurrent_destination):
            with self.assertRaises(FileExistsError):
                self.export()
        self.assertEqual(self.archive.read_bytes(), b'concurrent-owner-file')
        self.assertEqual(list(self.base.glob('.agentops-backup-*')), [])

    def test_restore_does_not_follow_a_parent_symlink_inserted_after_validation(self):
        self.export()
        outside = self.base / 'outside-live-directory'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'live state stays untouched')
        original = c._stage_parent
        inserted = []
        def inject(root_fd, parts, created_dirs=None):
            if not inserted:
                (self.stage / 'files').symlink_to(outside, target_is_directory=True)
                inserted.append(True)
            return original(root_fd, parts, created_dirs)
        with patch.object(c, '_stage_parent', side_effect=inject):
            with self.assertRaises(OSError):
                c.restore_backup(self.archive, self.stage)
        self.assertEqual(sentinel.read_bytes(), b'live state stays untouched')
        self.assertEqual(sorted(p.name for p in outside.iterdir()), ['sentinel'])

    def test_sqlite_online_backup_includes_wal_commits_excludes_uncommitted_changes(self):
        ledger = self.root / 'tasks.sqlite3'
        writer = sqlite3.connect(ledger)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY, title TEXT)')
        writer.execute("INSERT INTO tasks(title) VALUES ('committed fixture')")
        writer.commit()
        writer.execute("INSERT INTO tasks(title) VALUES ('uncommitted fixture')")
        report = self.export(ledger_path=ledger)
        self.assertTrue(any(e.get('sqlite') for e in report['files']))
        restored = c.restore_backup(self.archive, self.stage)
        self.assertEqual(restored['sqlite_integrity'], 'passed')
        with sqlite3.connect(self.stage / 'files/ledger/ledger.sqlite3') as copy:
            self.assertEqual(copy.execute('SELECT title FROM tasks').fetchall(), [('committed fixture',)])
        self.assertTrue(writer.in_transaction)
        writer.rollback()

    def test_invalid_sqlite_causes_cleanup_without_touching_live_sources(self):
        self.crafted([('files/ledger/ledger.sqlite3', b'not sqlite', stat.S_IFREG)], sqlite=True)
        with self.assertRaises(c.ContinuityError):
            c.restore_backup(self.archive, self.stage)
        self.assertEqual(list(self.stage.iterdir()), [])
        self.assertTrue((self.root / 'main.py').exists())

    def test_archive_extra_entries_are_not_restored(self):
        self.export()
        with zipfile.ZipFile(self.archive, 'a') as z:
            z.writestr('files/project/undeclared', b'x')
        with self.assertRaises(c.ContinuityError):
            c.restore_backup(self.archive, self.stage)
        self.assertEqual(list(self.stage.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
