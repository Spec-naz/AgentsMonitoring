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

    def test_work_queue_filters_terminal_tasks_before_limit(self):
        spec = importlib.util.spec_from_file_location('plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        def task(number, state):
            return {'id': str(number), 'title': state, 'state': state, 'owner': 'fixture',
                    'version': 1, 'updated': number, 'created': 0}
        terminal = [task(1000 + i, 'done' if i % 2 else 'cancelled') for i in range(105)]
        active_states = ['queued', 'running', 'blocked', 'handoff_pending', 'review']
        active = [task(i, active_states[i % len(active_states)]) for i in range(105)]
        data = {'schema_version': 1, 'generated_at': time.time(), 'tasks': terminal + active}
        original = json.dumps(data)
        with patch.object(plugin.Path, 'stat') as stat, patch.object(plugin.Path, 'read_text', return_value=original):
            stat.return_value.st_size = len(original)
            queue = plugin.collect({}, {})['tables'][0]
        self.assertEqual(len(queue['rows']), 100)
        self.assertEqual([row['id'] for row in queue['rows']], ['t-' + str(i) for i in range(104, 4, -1)])
        self.assertEqual({row['name'] for row in queue['rows']}, set(active_states))
        self.assertTrue(all(row['actions_locked'] for row in queue['rows']))
        self.assertIn('history retained', queue['note'])
        self.assertIn('100 of 105 active', queue['note'])

    def test_work_queue_with_only_terminal_tasks_is_empty(self):
        spec = importlib.util.spec_from_file_location('plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        data = {'schema_version': 1, 'generated_at': time.time(),
                'tasks': [{'state': 'done'}, {'state': 'cancelled'}]}
        with patch.object(plugin.Path, 'stat') as stat, patch.object(plugin.Path, 'read_text', return_value=json.dumps(data)):
            stat.return_value.st_size = 200
            queue = plugin.collect({}, {})['tables'][0]
        self.assertEqual(queue['rows'], [])
        self.assertNotIn('error', queue)


    def test_unknown_and_unavailable_signals_do_not_create_failure_panel(self):
        spec = importlib.util.spec_from_file_location('plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        data = {'schema_version': 1, 'generated_at': time.time(), 'tasks': [], 'signals': {
            'missing': {'signal': 'unavailable'},
            'stale': {'signal': 'unclassified', 'provider_observation': {'state': 'unknown', 'reason': 'signal_stale'}},
            'unbound': {'provider_observation': {'state': 'unknown', 'reason': 'no_bound_session'}}}}
        with patch.object(plugin.Path, 'stat') as stat, patch.object(plugin.Path, 'read_text', return_value=json.dumps(data)):
            stat.return_value.st_size = 1000
            tables = plugin.collect({}, {})['tables']
        self.assertEqual([t['id'] for t in tables], ['tasks'])
        self.assertIn('Current signals: 0/3', tables[0]['note'])

    def test_actual_hook_failure_still_visible_but_stale_peer_is_omitted(self):
        spec = importlib.util.spec_from_file_location('plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        data = {'schema_version': 1, 'generated_at': time.time(), 'tasks': [], 'signals': {
            'failed': {'signal': 'waiting_input_hint', 'samples': 2, 'provider_observation': {'state': 'failed', 'provider': 'claude', 'last_event': 'StopFailure', 'age_seconds': 2}},
            'stale': {'provider_observation': {'state': 'unknown'}}}}
        with patch.object(plugin.Path, 'stat') as stat, patch.object(plugin.Path, 'read_text', return_value=json.dumps(data)):
            stat.return_value.st_size = 1000
            tables = plugin.collect({}, {})['tables']
        self.assertEqual([r['id'] for r in tables[1]['rows']], ['failed'])
        self.assertFalse(tables[1]['rows'][0]['alive'])
        self.assertEqual(tables[1]['rows'][0]['status'], 'Turn failed (hook)')

    def test_normal_turns_are_healthy_and_ended_session_is_omitted(self):
        spec = importlib.util.spec_from_file_location('plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        observations = {state: {'provider_observation': {'state': state, 'last_event': 'Stop'}}
                        for state in ('turn_running', 'idle', 'ended')}
        observations['running-modal'] = {'signal': 'waiting_input_hint', 'samples': 2, 'provider_observation': {'state': 'turn_running'}}
        observations['ended-modal'] = {'signal': 'waiting_input_hint', 'samples': 2, 'provider_observation': {'state': 'ended'}}
        observations['one-hint'] = {'signal': 'waiting_input_hint', 'samples': 1}
        observations['two-hints'] = {'signal': 'waiting_input_hint', 'samples': 2,
                                     'provider_observation': {'state': 'unknown'}}
        data = {'schema_version': 1, 'generated_at': time.time(), 'tasks': [], 'signals': observations}
        with patch.object(plugin.Path, 'stat') as stat, patch.object(plugin.Path, 'read_text', return_value=json.dumps(data)):
            stat.return_value.st_size = 1500
            tables = plugin.collect({}, {})['tables']
        rows = {row['id']: row for row in tables[1]['rows']}
        self.assertEqual(set(rows), {'turn_running', 'idle', 'two-hints', 'running-modal', 'ended-modal'})
        self.assertTrue(rows['turn_running']['alive'])
        self.assertTrue(rows['idle']['alive'])
        self.assertEqual(rows['running-modal']['status'], 'Possible input dialog')
        self.assertFalse(rows['running-modal']['alive'])
        self.assertIn('Current signals: 5/7', tables[0]['note'])


if __name__ == '__main__':
    unittest.main()
