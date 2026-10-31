import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest

from agentops import provider_signals as ps

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'PRIVATE-FIXTURE-DO-NOT-STORE'


def claude(event, session='sess-1', **extra):
    payload = {'session_id': session, 'transcript_path': f'/home/x/{SECRET}.jsonl', 'cwd': f'/srv/{SECRET}',
               'permission_mode': 'bypassPermissions', 'hook_event_name': event}
    payload.update(extra)
    return json.dumps(payload).encode()


def codex(event, session='019a-thread', **extra):
    payload = {'session_id': session, 'turn_id': 'turn-7', 'transcript_path': None, 'cwd': f'/srv/{SECRET}',
               'hook_event_name': event, 'model': 'gpt-x'}
    payload.update(extra)
    return json.dumps(payload).encode()


class SignalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / 'signals'

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, provider, raw, now, actor='klodak'):
        return ps.ingest(provider, raw, self.dir, now=now, actor=actor)

    def stored_text(self):
        return ''.join(p.read_text() for p in self.dir.rglob('*.json'))

    def test_older_event_cannot_rebind_newest_turn_to_another_actor(self):
        self.feed('claude', claude('UserPromptSubmit'), 100, actor='kodak')
        result = self.feed('claude', claude('Stop'), 90, actor='klodak')
        self.assertEqual(result['actor'], 'kodak')
        self.assertEqual(result['turn'], 'running')

    # --- privacy -----------------------------------------------------------
    def test_only_allowlisted_metadata_is_stored(self):
        self.feed('claude', claude('UserPromptSubmit', prompt=SECRET + ' token=sk-abc'), 100)
        self.feed('claude', claude('Stop', last_assistant_message=SECRET, stop_hook_active=False,
                                   background_tasks=[{'command': SECRET}]), 110)
        self.feed('claude', claude('Notification', notification_type='permission_prompt', message=SECRET,
                                   title=SECRET), 120)
        self.feed('codex', codex('PermissionRequest', tool_name='Bash', tool_input={'command': SECRET}), 130)
        text = self.stored_text()
        self.assertNotIn(SECRET, text)
        self.assertNotIn('sk-abc', text)
        self.assertNotIn('transcript', text)
        self.assertNotIn('turn-7', text)
        for path in self.dir.rglob('*.json'):
            value = json.loads(path.read_text())
            if path.name == 'rejected.json':
                continue
            self.assertLessEqual(set(value), {'schema_version', 'provider', 'session_id', 'actor', 'first_seen_at',
                                              'turn', 'turn_since', 'waiting_since', 'waiting_kind', 'ended_at',
                                              'last_event', 'last_event_at', 'events'})

    def test_files_are_private_and_names_are_hashed(self):
        self.feed('claude', claude('UserPromptSubmit', session='../../etc/passwd'), 1)
        self.feed('claude', claude('UserPromptSubmit', session='abc-123'), 1)
        self.assertEqual(stat.S_IMODE(self.dir.stat().st_mode), 0o700)
        files = [p for p in (self.dir / 'claude').glob('*.json')]
        self.assertEqual(len(files), 1)
        self.assertNotIn('abc-123', files[0].name)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        self.assertFalse(Path('/etc/passwd.json').exists())
        rejected = json.loads((self.dir / 'rejected.json').read_text())
        self.assertEqual(rejected['bad_session'], 1)

    def test_garbage_is_rejected_and_counted(self):
        for raw in (b'not json', b'[1]', claude('PreToolUse'), json.dumps({'hook_event_name': 'Stop'}).encode(),
                    b'x' * (ps.MAX_STDIN_BYTES + 1)):
            self.assertIsNone(self.feed('claude', raw, 1))
        counts = json.loads((self.dir / 'rejected.json').read_text())
        self.assertEqual((counts['not_json'], counts['not_object'], counts['unknown_event'], counts['bad_session'],
                          counts['oversize']), (1, 1, 1, 1, 1))
        self.assertEqual(ps.read_sessions(self.dir), [])

    # --- Claude turn states -----------------------------------------------
    def test_claude_turn_lifecycle(self):
        self.feed('claude', claude('SessionStart', source='startup'), 10)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=11)['state'], 'idle')
        self.feed('claude', claude('UserPromptSubmit', prompt='x'), 20)
        sig = ps.agent_signal('klodak', 'claude', self.dir, now=30)
        self.assertEqual((sig['state'], sig['since'], sig['proves_task_progress']), ('turn_running', 20, False))
        self.feed('claude', claude('Notification', notification_type='permission_prompt'), 40)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=41)['state'], 'waiting_input')
        self.feed('claude', claude('PostToolUse', tool_name='Bash', tool_input={'command': SECRET}), 50)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=51)['state'], 'turn_running')
        self.feed('claude', claude('Stop'), 60)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=61)['state'], 'idle')
        self.feed('claude', claude('Notification', notification_type='idle_prompt'), 120)
        idle = ps.agent_signal('klodak', 'claude', self.dir, now=121)
        self.assertEqual((idle['state'], idle['since']), ('idle', 60))
        self.feed('claude', claude('SessionEnd', reason='other'), 130)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=131)['state'], 'ended')

    def test_claude_irrelevant_notifications_are_ignored(self):
        self.feed('claude', claude('UserPromptSubmit'), 10)
        self.assertIsNone(self.feed('claude', claude('Notification', notification_type='auth_success'), 11))
        self.assertIsNone(self.feed('claude', claude('Notification'), 12))
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=13)['state'], 'turn_running')

    def test_stop_failure_is_failed_not_idle(self):
        self.feed('claude', claude('UserPromptSubmit'), 10)
        self.feed('claude', claude('StopFailure', error='rate_limit', last_assistant_message=SECRET), 20)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=21)['state'], 'failed')

    def test_subagent_turn_events_do_not_touch_main_turn(self):
        self.feed('claude', claude('UserPromptSubmit'), 10)
        self.assertIsNone(self.feed('claude', claude('Stop', agent_id='def456', agent_type='Explore'), 20))
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=21)['state'], 'turn_running')
        self.assertIsNone(self.feed('claude', claude('Notification', notification_type='permission_prompt', agent_id='def456'), 22))
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=23)['state'], 'turn_running')

    def test_subagent_activity_cannot_clear_main_permission_request(self):
        self.feed('claude', claude('UserPromptSubmit'), 10)
        self.feed('claude', claude('Notification', notification_type='permission_prompt'), 20)
        self.assertIsNone(self.feed('claude', claude('PostToolUse', agent_id='child'), 30))
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=31)['state'], 'waiting_input')

    # --- staleness ---------------------------------------------------------
    def test_running_without_end_becomes_unknown(self):
        # Claude never fires Stop after an Esc interrupt.
        self.feed('claude', claude('UserPromptSubmit'), 0)
        sig = ps.agent_signal('klodak', 'claude', self.dir, now=ps.DEFAULTS['running_max_age'] + 1)
        self.assertEqual((sig['state'], sig['reason']), ('unknown', 'running_without_end_signal'))

    def test_old_waiting_and_future_timestamps_are_unknown(self):
        self.feed('claude', claude('Notification', notification_type='permission_prompt'), 0)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=ps.DEFAULTS['waiting_max_age'] + 5)['state'],
                         'unknown')
        self.feed('claude', claude('Stop', session='s2'), 10_000)
        [snap] = [s for s in ps.read_sessions(self.dir, 'claude') if s['session_id'] == 's2']
        sig = ps.classify(snap, now=100)
        self.assertEqual((sig['state'], sig['reason']), ('unknown', 'clock_skew'))

    def test_out_of_order_event_does_not_regress(self):
        self.feed('claude', claude('Stop'), 50)
        self.feed('claude', claude('UserPromptSubmit'), 40)  # delivered late
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=51)['state'], 'idle')

    # --- binding -----------------------------------------------------------
    def test_binding_rules(self):
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=1)['reason'], 'no_bound_session')
        self.feed('claude', claude('UserPromptSubmit', session='a'), 10, actor=None)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=11)['state'], 'unknown')
        self.feed('claude', claude('UserPromptSubmit', session='b'), 10, actor='Evil;rm')
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=11)['state'], 'unknown')
        self.feed('claude', claude('UserPromptSubmit', session='c'), 10)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=11)['session_id'], 'c')
        self.feed('claude', claude('UserPromptSubmit', session='d'), 12)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=13)['reason'], 'ambiguous_binding')
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=13, session_id='d')['session_id'], 'd')
        self.assertEqual(ps.agent_signal('klodak', 'codex', self.dir, now=13)['state'], 'unknown')
        self.assertEqual(ps.agent_signal('../x', 'claude', self.dir, now=13)['reason'], 'invalid_binding')
        self.feed('claude', claude('SessionEnd', session='d'), 14)
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir, now=15)['session_id'], 'c')

    def test_tampered_or_foreign_files_are_skipped(self):
        self.feed('claude', claude('Stop', session='ok'), 10)
        folder = self.dir / 'claude'
        (folder / 'junk.json').write_text('{"schema_version":1,"provider":"claude","session_id":"x","last_event_at":1}')
        (folder / 'broken.json').write_text('{')
        os.symlink(next(folder.glob('*.json')), folder / 'link.json')
        self.assertEqual([s['session_id'] for s in ps.read_sessions(self.dir)], ['ok'])

    # --- Codex -------------------------------------------------------------
    def test_codex_lifecycle(self):
        self.feed('codex', codex('SessionStart', source='startup'), 1, actor='kodak')
        self.feed('codex', codex('UserPromptSubmit', prompt=SECRET), 2, actor='kodak')
        self.assertEqual(ps.agent_signal('kodak', 'codex', self.dir, now=3)['state'], 'turn_running')
        self.feed('codex', codex('PermissionRequest', tool_name='Bash'), 4, actor='kodak')
        sig = ps.agent_signal('kodak', 'codex', self.dir, now=5)
        self.assertEqual((sig['state'], sig['confidence']), ('waiting_input', 'probable'))
        self.feed('codex', codex('Interrupt', permission_mode='default'), 6, actor='kodak')
        self.assertEqual(ps.agent_signal('kodak', 'codex', self.dir, now=7)['state'], 'idle')
        self.assertIsNone(self.feed('codex', codex('Notification'), 8, actor='kodak'))
        self.feed('codex', codex('SessionEnd', reason='other'), 9, actor='kodak')
        self.assertEqual(ps.agent_signal('kodak', 'codex', self.dir, now=10)['state'], 'ended')

    # --- concurrency -------------------------------------------------------
    def test_parallel_ingest_keeps_valid_snapshot(self):
        threads = [threading.Thread(target=self.feed, args=('claude', claude(ev), 100 + i))
                   for i, ev in enumerate(['UserPromptSubmit', 'Stop'] * 20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        [snap] = ps.read_sessions(self.dir, 'claude')
        self.assertEqual(snap['events'], 40)
        self.assertEqual(snap['last_event_at'], 139)
        self.assertEqual(list((self.dir / 'claude').glob('.sig-*')), [])

    def test_prune(self):
        self.feed('claude', claude('Stop', session='old'), 0)
        self.feed('claude', claude('Stop', session='new'), 1000)
        self.assertEqual(ps.prune(self.dir, now=1000, max_age=500), 1)
        self.assertEqual([s['session_id'] for s in ps.read_sessions(self.dir)], ['new'])

    # --- CLI entry point ---------------------------------------------------
    def run_cli(self, args, stdin=b'', env=None):
        return subprocess.run([sys.executable, '-I', str(ROOT / 'agentops/provider_signals.py')] + args, input=stdin,
                              capture_output=True, cwd=ROOT, env={**os.environ, **(env or {})}, timeout=20)

    def test_hook_cli_is_silent_and_never_fails(self):
        run = self.run_cli(['ingest', '--provider', 'claude', '--state-dir', str(self.dir)],
                           claude('UserPromptSubmit', prompt=SECRET), {'AGENTOPS_ACTOR': 'klodak'})
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, b'', b''))
        self.assertEqual(ps.agent_signal('klodak', 'claude', self.dir)['state'], 'turn_running')
        for raw in (b'garbage', b'x' * (ps.MAX_STDIN_BYTES + 10)):
            run = self.run_cli(['ingest', '--provider', 'claude', '--state-dir', str(self.dir)], raw)
            self.assertEqual((run.returncode, run.stdout), (0, b''))
        blocked = Path(self.tmp.name) / 'file-not-dir'
        blocked.write_text('')
        run = self.run_cli(['ingest', '--provider', 'claude', '--state-dir', str(blocked / 'x')], claude('Stop'))
        self.assertEqual((run.returncode, run.stdout), (0, b''))

    def test_codex_stdout_is_json_only_for_stop(self):
        args = ['ingest', '--provider', 'codex', '--state-dir', str(self.dir)]
        self.assertEqual(self.run_cli(args, codex('Stop')).stdout, b'{}')
        for event in ('UserPromptSubmit', 'SessionStart', 'Interrupt'):
            self.assertEqual(self.run_cli(args, codex(event)).stdout, b'')


class InstallPlanTests(unittest.TestCase):
    TELEGRAM = {'type': 'command', 'command': 'env AGENT2TELEGRAM_CONFIG=/x/claude.json python3 -m agent2telegram hook'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'settings.json'
        self.original = {'theme': 'dark', 'hooks': {'Stop': [{'matcher': '', 'hooks': [self.TELEGRAM]}],
                                                    'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command',
                                                                                                  'command': 'guard'}]}]}}
        self.path.write_text(json.dumps(self.original))

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_preserves_every_existing_hook_and_never_writes(self):
        before = self.path.read_bytes()
        mtime = self.path.stat().st_mtime_ns
        config, plan = ps.plan_install('claude', self.path, 'python3 -m agentops.provider_signals ingest --provider claude')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.path.stat().st_mtime_ns, mtime)
        self.assertFalse(plan['writes'])
        self.assertEqual(config['theme'], 'dark')
        self.assertEqual(config['hooks']['Stop'][0], self.original['hooks']['Stop'][0])
        self.assertEqual(config['hooks']['PreToolUse'], self.original['hooks']['PreToolUse'])
        self.assertEqual(len(config['hooks']['Stop']), 2)
        self.assertEqual(plan['preserved'], {'Stop': 1, 'PreToolUse': 1})
        self.assertEqual({a['event'] for a in plan['add']},
                         {'SessionStart', 'UserPromptSubmit', 'Stop', 'StopFailure', 'Notification', 'SessionEnd'})
        self.assertNotIn('PostToolUse', config['hooks'])

    def test_plan_is_idempotent(self):
        config, _ = ps.plan_install('claude', self.path, 'python3 -m agentops.provider_signals ingest --provider claude')
        self.path.write_text(json.dumps(config))
        again, plan = ps.plan_install('claude', self.path, 'python3 -m agentops.provider_signals ingest --provider claude')
        self.assertEqual(plan['add'], [])
        self.assertEqual(again, config)

    def test_actual_isolated_script_plan_is_idempotent(self):
        command = ps.hook_command('claude', package_root=ROOT)
        config, _ = ps.plan_install('claude', self.path, command)
        again, plan = ps.merge_hooks(config, 'claude', command)
        self.assertEqual(plan['add'], [])
        self.assertEqual(again, config)

    def test_shell_generator_rejects_untrusted_provider(self):
        with self.assertRaises(ValueError):
            ps.hook_command('claude; echo injected')

    def test_codex_plan_for_missing_file_and_timeouts(self):
        config, plan = ps.plan_install('codex', Path(self.tmp.name) / 'hooks.json', 'cmd agentops.provider_signals')
        self.assertFalse(plan['config_exists'])
        self.assertFalse((Path(self.tmp.name) / 'hooks.json').exists())
        timeouts = {e: g[0]['hooks'][0]['timeout'] for e, g in config['hooks'].items()}
        self.assertLessEqual(timeouts['Interrupt'], 3)
        self.assertLessEqual(timeouts['SessionEnd'], 3)
        self.assertNotIn('Notification', config['hooks'])
        self.assertTrue(any('/hooks' in n for n in plan['notes']))

    def test_refuses_unexpected_shapes(self):
        self.path.write_text(json.dumps({'hooks': []}))
        with self.assertRaises(ValueError):
            ps.plan_install('claude', self.path, 'x agentops.provider_signals')
        self.path.write_text(json.dumps({'hooks': {'Stop': {}}}))
        with self.assertRaises(ValueError):
            ps.plan_install('claude', self.path, 'x agentops.provider_signals')

    def test_cli_plan_does_not_write(self):
        before = self.path.read_bytes()
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'agentops/provider_signals.py'), 'plan-install', '--provider', 'claude',
                              '--config', str(self.path)], capture_output=True, cwd=ROOT, timeout=20)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(json.loads(run.stdout)['writes'])
        self.assertNotIn(b'AGENT2TELEGRAM', run.stdout)  # plan summary does not echo other hooks
        self.assertEqual(self.path.read_bytes(), before)

    def test_command_is_isolated_and_quoted(self):
        cmd = ps.hook_command('claude', package_root='/opt/a b', state_dir='/s')
        self.assertEqual(cmd, "/usr/bin/python3 -I '/opt/a b/agentops/provider_signals.py' ingest --provider claude "
                              "--state-dir /s")

    def test_planted_package_in_agent_cwd_is_not_executed(self):
        cwd = Path(self.tmp.name) / 'work'
        (cwd / 'agentops').mkdir(parents=True)
        marker = Path(self.tmp.name) / 'pwned'
        (cwd / 'agentops' / '__init__.py').write_text(f'open({str(marker)!r}, "w").close()')
        (cwd / 'json.py').write_text(f'open({str(marker)!r}, "w").close()')
        cmd = ps.hook_command('claude', python=sys.executable, package_root=ROOT,
                              state_dir=Path(self.tmp.name) / 'sig')
        run = subprocess.run(cmd, shell=True, cwd=cwd, input=b'{"session_id":"s","hook_event_name":"Stop"}',
                             capture_output=True, timeout=20, env={**os.environ, 'PYTHONPATH': str(cwd)})
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(len(ps.read_sessions(Path(self.tmp.name) / 'sig')), 1)

if __name__ == '__main__':
    unittest.main()
