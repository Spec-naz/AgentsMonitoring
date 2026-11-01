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

    def test_automatic_plugin_discovery_and_cache_refresh_on_provider_onboarding(self):
        cfg = {'modules_dir': str(self.root / 'modules'), 'usage_plugin': None, 'agents': []}
        self.assertFalse(any(p.id == 'usage' for p in plugins.discover(cfg)))
        first = plugins.manager(cfg)
        cfg['agents'] = [{'name': 'new-agent', 'kind': 'antigravity'}]
        discovered = plugins.discover(cfg)
        self.assertTrue(any(p.id == 'usage' for p in discovered))
        self.assertIsNot(plugins.manager(cfg), first)
        with patch('agentsmon.__main__.sys.stdout'):
            from agentsmon.__main__ import main
            self.assertEqual(main(['antigravity-usage', 'install', '--profile-home', str(self.root / 'other'),
                                   '--profile', 'gcp', '--pool', 'GCP trial', '--snapshot-dir', str(self.directory)]), 0)


if __name__ == '__main__':
    unittest.main()
