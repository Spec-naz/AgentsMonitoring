import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agentsmon import antigravity_usage as collector, plugins, usage_plugin

SID = '12345678-abcd-ef01-2345-6789abcdef01'
SID2 = 'abcdef01-1234-5678-abcd-123456789abc'


class AntigravityUsageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / 'snapshots'

    def payload(self, sid=SID):
        return {'conversation_id': sid, 'session_id': sid, 'model': {'id': 'gemini-test'},
                'quota': {'gemini-weekly': {'remaining_fraction': .75, 'reset_in_seconds': 1000}},
                'email': 'PRIVATE@example.test', 'cwd': '/PRIVATE', 'conversation_title': 'PRIVATE',
                'transcript_path': '/PRIVATE/transcript', 'subagents': [{'name': 'PRIVATE'}],
                'context_window': {'used_percentage': 99}, 'cost': {'total_usd': 999}}

    def snapshot(self, payload=None, profile='consumer', pool='Google AI Pro', now=None):
        now = time.time() if now is None else now
        data = collector.sanitize(payload or self.payload(), profile, pool, now)
        path = collector.store_snapshot(data, self.directory)
        return path

    def test_collector_redacts_all_non_allowlisted_fields_and_uses_private_uuid_file(self):
        path = self.snapshot(now=1000)
        contents = path.read_text()
        self.assertNotIn('PRIVATE', contents)
        self.assertNotIn('context_window', contents)
        self.assertNotIn('cost', contents)
        self.assertEqual(path.name, SID + '.json')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        data = json.loads(contents)
        self.assertEqual(data['quota']['gemini-weekly'], {'remaining_fraction': .75, 'reset_at': 2000})

    def test_new_sessions_are_automatic_without_per_agent_mapping_and_pools_are_distinct(self):
        self.snapshot(profile='consumer', pool='AI Pro')
        other = self.payload(SID2)
        other['quota'] = {'gcp-credits': {'remaining_amount': 25, 'reset_in_seconds': 5000}}
        self.snapshot(other, profile='gcp', pool='GCP trial credits')
        result = usage_plugin.collect({'agents': [
            {'id': 'new-auto-consumer', 'kind': 'antigravity', 'session_id': SID},
            {'id': 'new-auto-gcp', 'kind': 'antigravity', 'conversation_id': SID2},
        ]}, {'antigravity_directory': str(self.directory)})
        first, second = [p['cells']['limits'] for p in result['patches']]
        self.assertEqual(first['bars'][0]['percent'], 25)
        self.assertIn('used', first['bars'][0]['label'])
        self.assertIn('AI Pro', first['title'])
        self.assertIn('GCP trial credits', second['title'])
        self.assertIn('25 remaining', second['text'])
        self.assertNotIn('bars', second)
        self.assertIn('not dollars', second['title'])
        self.assertNotIn('999', json.dumps(result))

    def test_conflicting_session_identity_and_cross_pool_overwrite_are_rejected(self):
        payload = self.payload()
        payload['session_id'] = SID2
        with self.assertRaises(ValueError): collector.sanitize(payload, 'consumer', 'AI Pro')
        path = self.snapshot()
        before = path.read_bytes()
        with self.assertRaises(ValueError): self.snapshot(profile='gcp', pool='GCP trial')
        self.assertEqual(path.read_bytes(), before)

    def test_disabled_expired_unknown_and_stale_are_not_current_quota(self):
        payload = self.payload()
        payload['quota'] = {'disabled': {'disabled': True, 'remaining_fraction': .9},
                            'expired': {'remaining_fraction': .5, 'reset_time': 900}}
        self.snapshot(payload, now=1000)
        value = usage_plugin.antigravity_reading(SID, self.directory, 1100)
        self.assertEqual(value['text'], 'Disabled')
        self.assertNotIn('bars', value)
        payload['quota'] = {'weekly': {'remaining_fraction': .8, 'reset_time': 3000}}
        self.snapshot(payload, now=1000)
        value = usage_plugin.antigravity_reading(SID, self.directory, 1700)
        self.assertIn('Cached', value['text'])
        self.assertEqual(value['bars'][0]['percent'], 20)
        self.assertEqual(usage_plugin.antigravity_reading(SID, self.directory, 3100)['text'], 'No current reading')

    def test_bad_numbers_never_turn_context_tokens_into_quota(self):
        for fraction in (True, -1, 1.01, float('nan'), '0.9'):
            payload = self.payload()
            payload['quota'] = {'weekly': {'remaining_fraction': fraction}}
            self.snapshot(payload)
            value = usage_plugin.antigravity_reading(SID, self.directory, time.time())
            self.assertNotIn('bars', value)
        missing = usage_plugin.collect({'agents': [{'id': 'new', 'kind': 'antigravity'}]}, {})
        self.assertEqual(missing['patches'][0]['cells']['limits']['text'], 'Unavailable')

    def test_installer_preserves_other_settings_and_refuses_existing_statusline(self):
        home = self.root / 'profile home'
        settings = home / '.gemini/antigravity-cli/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'other': {'setting': 'keep'}}))
        plan = collector.install(home, 'consumer', 'AI Pro', self.directory)
        self.assertFalse(plan['applied'])
        self.assertNotIn('statusLine', json.loads(settings.read_text()))
        result = collector.install(home, 'consumer', 'AI Pro', self.directory, apply=True)
        self.assertTrue(result['applied'])
        actual = json.loads(settings.read_text())
        self.assertEqual(actual['other'], {'setting': 'keep'})
        self.assertTrue(actual['statusLine']['stack_with_default'])
        # Execute the generated native shell command under the new profile HOME.
        result = subprocess.run(actual['statusLine']['command'], shell=True, input=json.dumps(self.payload()).encode(),
                                env={'HOME': str(home), 'PATH': '/usr/bin:/bin'}, capture_output=True, check=True)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))
        self.assertTrue((self.directory / (SID + '.json')).exists())
        self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
        self.assertEqual(collector.install(home, 'consumer', 'AI Pro', self.directory, apply=True)['status'], 'already-configured')
        settings.write_text(json.dumps({'statusLine': {'command': 'custom command'}}))
        before = settings.read_bytes()
        with self.assertRaises(ValueError): collector.install(home, 'gcp', 'trial', self.directory, apply=True)
        self.assertEqual(settings.read_bytes(), before)

    def test_bounded_collector_process_emits_no_sensitive_stdout_or_stderr(self):
        command = [sys.executable, '-I', str(Path(collector.__file__)), 'collect', '--profile', 'consumer',
                   '--pool', 'AI Pro', '--snapshot-dir', str(self.directory)]
        result = subprocess.run(command, input=json.dumps(self.payload()).encode(), capture_output=True, check=True)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))
        self.assertTrue((self.directory / (SID + '.json')).exists())
        result = subprocess.run(command, input=b'PRIVATE' * 50000, capture_output=True, check=True)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))

    def test_managed_pinned_release_upgrade_and_rollback_preserve_original_backups(self):
        home = self.root / 'managed-profile'
        settings = home / '.gemini/antigravity-cli/settings.json'
        settings.parent.mkdir(parents=True)
        original = b'{  "other": {"order": 1}, "second": true }\n'
        settings.write_bytes(original)
        first = '/reviewed/releases/v1/agentsmon/antigravity_usage.py'
        second = '/reviewed/releases/v2/agentsmon/antigravity_usage.py'
        with patch.object(collector, '__file__', first):
            collector.install(home, 'consumer', 'AI Pro', self.directory, apply=True)
        wrapper = settings.parent / 'agentsmon-statusline.sh'
        first_bytes = wrapper.read_bytes()
        backups = list(settings.parent.glob('settings.before-agentsmon-*.json'))
        self.assertTrue(any(p.read_bytes() == original for p in backups))
        with patch.object(collector, '__file__', second):
            self.assertEqual(collector.install(home, 'consumer', 'AI Pro', self.directory)['status'], 'ready-to-upgrade')
            self.assertEqual(wrapper.read_bytes(), first_bytes)
            result = collector.install(home, 'consumer', 'AI Pro', self.directory, apply=True)
        self.assertEqual(result['operation'], 'upgrade')
        self.assertIn(second.encode(), wrapper.read_bytes())
        self.assertNotIn(b'/current/', wrapper.read_bytes())
        wrapper_backups = list(settings.parent.glob('agentsmon-statusline.before-agentsmon-*.sh'))
        self.assertTrue(any(p.read_bytes() == first_bytes for p in wrapper_backups))
        with patch.object(collector, '__file__', first):
            collector.install(home, 'consumer', 'AI Pro', self.directory, apply=True)
        self.assertEqual(wrapper.read_bytes(), first_bytes)
        with self.assertRaises(ValueError): collector.install(home, 'consumer', 'DIFFERENT POOL', self.directory, apply=True)

    def test_partial_install_recovers_identical_owned_wrapper_but_dry_run_rejects_foreign(self):
        home = self.root / 'partial-profile'
        settings = home / '.gemini/antigravity-cli/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text('{"untouched": true}')
        real_write = collector.atomic_write
        def fail_settings(path, contents):
            if path == settings:
                raise OSError('synthetic partial failure')
            return real_write(path, contents)
        with patch.object(collector, 'atomic_write', side_effect=fail_settings):
            with self.assertRaises(OSError): collector.install(home, 'consumer', 'AI Pro', self.directory, apply=True)
        wrapper = settings.parent / 'agentsmon-statusline.sh'
        self.assertTrue(wrapper.exists())
        self.assertNotIn('statusLine', json.loads(settings.read_text()))
        self.assertEqual(collector.install(home, 'consumer', 'AI Pro', self.directory)['status'], 'ready-to-recover')
        collector.install(home, 'consumer', 'AI Pro', self.directory, apply=True)
        self.assertTrue(json.loads(settings.read_text())['untouched'])
        other_home = self.root / 'foreign-profile'
        foreign = other_home / '.gemini/antigravity-cli/agentsmon-statusline.sh'
        foreign.parent.mkdir(parents=True)
        foreign.write_text('# foreign command, do not modify')
        before = foreign.read_bytes()
        with self.assertRaises(ValueError): collector.install(other_home, 'consumer', 'AI Pro', self.directory)
        self.assertEqual(foreign.read_bytes(), before)
        self.assertFalse(foreign.with_name('settings.json').exists())

    def test_large_integers_bad_resets_and_invalid_model_do_not_crash_or_invent_quota(self):
        self.assertFalse(collector.number(10 ** 4000))
        now = time.time()
        for reset in (1e300, now * 1000, 10 ** 4000, '9999-01-01T00:00:00Z'):
            payload = self.payload()
            payload['quota'] = {'weekly': {'remaining_fraction': .2, 'reset_time': reset}}
            self.snapshot(payload, now=now)
            self.assertEqual(usage_plugin.antigravity_reading(SID, self.directory, now)['text'], 'No current reading')
        payload = self.payload()
        payload['model'] = {'id': {'invalid': True}}
        data = collector.sanitize(payload, 'consumer', 'AI Pro', now)
        self.assertIsNone(data['model'])
        self.assertEqual(data['quota']['gemini-weekly']['remaining_fraction'], .75)
        payload['quota'] = {'huge': {'remaining_amount': 10 ** 4000}}
        command = [sys.executable, '-I', str(Path(collector.__file__)), 'collect', '--profile', 'consumer',
                   '--pool', 'AI Pro', '--snapshot-dir', str(self.directory)]
        result = subprocess.run(command, input=json.dumps(payload).encode(), capture_output=True, check=True)
        self.assertEqual((result.stdout, result.stderr), (b'', b''))

    def test_missing_empty_invalid_quota_clears_previous_reading_instead_of_fake_freshness(self):
        now = time.time()
        for quota in ('missing', {}, 'invalid'):
            self.snapshot(now=now)
            payload = self.payload()
            if quota == 'missing': payload.pop('quota')
            else: payload['quota'] = quota
            self.snapshot(payload, now=now + 1)
            value = usage_plugin.antigravity_reading(SID, self.directory, now + 2)
            self.assertEqual(value['text'], 'No current reading')
            self.assertNotIn('bars', value)
            self.assertIn('not monetary balance', value['title'])
        self.snapshot(now=now)
        value = usage_plugin.antigravity_reading(SID, self.directory, now + 700)
        self.assertEqual(value['text'], 'Cached')
        self.assertIn('not monetary balance', value['title'])

    def test_actual_plugin_manager_subprocess_consumes_automatic_uuid_source(self):
        self.snapshot()
        cfg = {'modules_dir': str(self.root / 'modules'), 'agents': [{'kind': 'antigravity'}],
               'usage_plugin': {'antigravity_directory': str(self.directory)}}
        result = plugins.Manager(cfg).collect([{'id': 'automatic', 'name': 'Automatic agent',
                                               'kind': 'antigravity', 'session_id': SID}])
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['patches'][0]['row_id'], 'automatic')
        self.assertEqual(result['patches'][0]['cells']['usage:limits']['bars'][0]['percent'], 25)

    def test_automatic_plugin_discovery_and_cache_refresh_on_provider_onboarding(self):
        cfg = {'modules_dir': str(self.root / 'modules'), 'usage_plugin': None, 'agents': []}
        self.assertFalse(any(p.id == 'usage' for p in plugins.discover(cfg)))
        first = plugins.manager(cfg)
        cfg['agents'] = [{'name': 'new-agent', 'kind': 'antigravity'}]
        discovered = plugins.discover(cfg)
        self.assertTrue(any(p.id == 'usage' for p in discovered))
        self.assertIsNot(plugins.manager(cfg), first)
        cfg['usage_plugin'] = False
        self.assertFalse(any(p.id == 'usage' for p in plugins.discover(cfg)))
        with patch('agentsmon.__main__.sys.stdout'):
            from agentsmon.__main__ import main
            self.assertEqual(main(['antigravity-usage', 'install', '--profile-home', str(self.root / 'other'),
                                   '--profile', 'gcp', '--pool', 'GCP trial', '--snapshot-dir', str(self.directory)]), 0)


    def test_install_through_current_symlink_pins_actual_reviewed_release(self):
        release = self.root / 'immutable-release'
        module = release / 'agentsmon/antigravity_usage.py'
        module.parent.mkdir(parents=True)
        module.write_text('synthetic module path only')
        current = self.root / 'current'
        current.symlink_to(release, target_is_directory=True)
        with patch.object(collector, '__file__', str(current / 'agentsmon/antigravity_usage.py')):
            result = collector.install(self.root / 'profile', 'consumer', 'AI Pro', self.directory, apply=True)
        import shlex
        words = shlex.split(Path(result['command_path']).read_text().splitlines()[2][5:])
        self.assertEqual(words[2], str(module))
        self.assertNotIn('/current/', words[2])


if __name__ == '__main__':
    unittest.main()
