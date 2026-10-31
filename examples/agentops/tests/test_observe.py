import importlib.util
import json
from pathlib import Path
import subprocess
import time
import unittest
from unittest.mock import patch

from agentops.observe import classify_terminal, observe_sessions


class ObserveTests(unittest.TestCase):
    def test_known_modal_is_hint_but_idle_is_unclassified(self):
        self.assertEqual(classify_terminal('We recommend compacting earlier\n enter confirm · esc not now'), 'waiting_input_hint')
        self.assertEqual(classify_terminal('old discussion of compaction\n❯ '), 'unclassified')
        self.assertEqual(classify_terminal('We recommend compacting earlier\n enter confirm\n' + 'working\n' * 35), 'unclassified')

    def test_terminal_contents_are_never_returned_and_no_keys_are_sent(self):
        secret = 'PRIVATE-FIXTURE-DO-NOT-PUBLISH'
        text = secret + '\nWe recommend compacting earlier\n enter confirm'
        with patch('agentops.observe.subprocess.run', return_value=subprocess.CompletedProcess([], 0, text, '')) as run:
            result = observe_sessions({'kodak': 'CEO_Master'})
        self.assertNotIn(secret, json.dumps(result))
        self.assertEqual(run.call_args.args[0][:2], ['tmux', 'capture-pane'])
        self.assertEqual(result['kodak']['samples'], 1)
        with patch('agentops.observe.subprocess.run', return_value=subprocess.CompletedProcess([], 0, text, '')):
            second = observe_sessions({'kodak': 'CEO_Master'}, result)
        self.assertEqual(second['kodak']['samples'], 2)

    def test_expired_plugin_snapshot_hides_rows(self):
        spec = importlib.util.spec_from_file_location('plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        with patch.object(plugin.Path, 'stat') as stat, patch.object(plugin.Path, 'read_text', return_value=json.dumps({'schema_version': 1, 'generated_at': time.time() - 200, 'tasks': [{'title': 'stale'}]})):
            stat.return_value.st_size = 100
            result = plugin.collect({}, {})
        self.assertEqual(result['tables'][0]['rows'], [])
        self.assertIn('error', result['tables'][0])

    def test_repeated_modal_is_visible_even_without_a_registered_task(self):
        spec = importlib.util.spec_from_file_location('plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        data = {'schema_version': 1, 'generated_at': time.time(), 'tasks': [], 'signals': {
            'klodak': {'signal': 'waiting_input_hint', 'samples': 2}}}
        with patch.object(plugin.Path, 'stat') as stat, patch.object(plugin.Path, 'read_text', return_value=json.dumps(data)):
            stat.return_value.st_size = 200
            tables = plugin.collect({}, {})['tables']
        self.assertEqual(tables[1]['id'], 'signals')
        self.assertEqual(tables[1]['rows'][0]['status'], 'Possible input dialog')
        self.assertTrue(tables[1]['rows'][0]['actions_locked'])


if __name__ == '__main__':
    unittest.main()
