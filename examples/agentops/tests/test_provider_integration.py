import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentops.ledger import Ledger
from agentops.observe import snapshot
from agentops.provider_signals import ingest


class ProviderIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = Ledger(self.root / 'tasks.sqlite')
        self.addCleanup(self.ledger.close)
        self.task = self.ledger.create('Fixture task', 'demo', 'demo')
        self.ledger.change(self.task['id'], 'demo', 'start')
        self.config = {'provider_bindings': {'demo': {'provider': 'claude', 'session_id': 's1',
                         'state_dir': str(self.root / 'signals'), 'max_age_seconds': 60}}}

    def feed(self, event, at, session='s1', **kwargs):
        return ingest('claude', json.dumps({'session_id': session, 'hook_event_name': event, **kwargs}).encode(),
                      self.root / 'signals', now=at, actor='demo')

    def test_stop_never_completes_task_or_refreshes_declared_progress(self):
        before = self.ledger.get(self.task['id'])
        self.feed('UserPromptSubmit', 100)
        self.feed('Stop', 110)
        value = snapshot(self.ledger, self.config, now=115, signals={})
        self.assertEqual(value['signals']['demo']['provider_observation']['state'], 'idle')
        after = self.ledger.get(self.task['id'])
        self.assertEqual((after['state'], after['progress'], after['version']),
                         (before['state'], before['progress'], before['version']))

    def test_exact_session_is_required_and_old_bound_event_becomes_unknown(self):
        self.feed('UserPromptSubmit', 100, session='other-session')
        value = snapshot(self.ledger, self.config, now=105, signals={})
        self.assertEqual(value['signals']['demo']['provider_observation']['state'], 'unknown')
        self.feed('UserPromptSubmit', 100)
        value = snapshot(self.ledger, self.config, now=200, signals={})
        self.assertEqual(value['signals']['demo']['provider_observation']['state'], 'unknown')

    def test_session_end_also_expires_under_binding_freshness(self):
        self.feed('SessionEnd', 100)
        value = snapshot(self.ledger, self.config, now=500, signals={})
        self.assertEqual(value['signals']['demo']['provider_observation']['state'], 'unknown')

    def test_hook_waiting_is_separate_from_modal_heuristic(self):
        self.feed('UserPromptSubmit', 100)
        self.feed('Notification', 105, notification_type='permission_prompt', message='DO-NOT-PUBLISH')
        value = snapshot(self.ledger, self.config, now=110, signals={})
        self.assertEqual(value['tasks'][0]['attention'], 'provider_waiting_input')
        self.assertNotIn('DO-NOT-PUBLISH', json.dumps(value))

    def test_cli_plan_preserves_settings_and_excludes_secret_values(self):
        path = self.root / 'settings.json'
        contents = json.dumps({'env': {'API_KEY': 'DO-NOT-PUBLISH'}, 'hooks': {}})
        path.write_text(contents)
        run = subprocess.run([sys.executable, '-m', 'agentops', 'provider-plan', '--provider', 'claude',
                              '--actor', 'demo', '--settings', str(path)], capture_output=True, text=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('DO-NOT-PUBLISH', run.stdout)
        self.assertEqual(path.read_text(), contents)


if __name__ == '__main__':
    unittest.main()
