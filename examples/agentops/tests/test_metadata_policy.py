"""LC-07: allowlists, export and retention. Real ledger and CLI on temporary folders; all secrets here are synthetic."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

from agentops import metadata as m
from agentops import provider_signals as ps
from agentops.ledger import Ledger
from agentops.observe import snapshot

ROOT = Path(__file__).parents[1]
SECRET = 'FAKE-SECRET-7f3a9c'
DAY = 86400
NOW = 1_800_000_000.0


def payload(event='Stop', session='sess-1', **extra):
    return json.dumps({'hook_event_name': event, 'session_id': session, **extra}).encode()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.db = self.root / 'state/tasks.sqlite'
        self.signals = self.root / 'signals'
        self.config = self.root / 'config.json'
        self.ledger = Ledger(self.db)
        self.addCleanup(self.ledger.close)

    def cli(self, *args):
        env = {**os.environ, 'PYTHONPATH': str(ROOT), 'HOME': str(self.root)}
        return subprocess.run([sys.executable, '-m', 'agentops', '--db', str(self.db), '--config', str(self.config), *args],
                              env=env, capture_output=True, text=True, timeout=60, cwd=self.root)

    def seeded_task(self):
        """A full task life with the fake secret in every free-text place the ledger has."""
        task = self.ledger.create('nazev ' + SECRET, 'autor', 'autor')['id']
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'progress', note='poznamka ' + SECRET)
        self.ledger.change(task, 'autor', 'block', note='duvod ' + SECRET)
        self.ledger.change(task, 'autor', 'start')
        self.ledger.cost(task, 'autor', 1500, 'CZK', 'actual', 'zdroj ' + SECRET)
        self.ledger.record_postcheck(task, 'autor', 'fail', '/dukazy/report-' + SECRET + '.json', 'a' * 64)
        self.ledger.change(task, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/vystupy/' + SECRET + '.md')
        return task

    def export(self, *extra):
        output = self.root / f'export-{uuid.uuid4()}.json'
        run = self.cli('metadata-export', '--signals-dir', str(self.signals), '--output', str(output), *extra)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn(SECRET, run.stdout + run.stderr)
        return output, json.loads(output.read_text()), run


class SnapshotsOnDisk(Base):
    def stored(self, session='sess-1', provider='claude'):
        return ps._session_file(self.signals, provider, session)

    def test_secret_in_any_hook_field_is_not_stored(self):
        ps.ingest('claude', payload('UserPromptSubmit', prompt=SECRET, cwd='/' + SECRET, transcript_path=SECRET, message=SECRET,
                                    nested={'a': {'b': SECRET}}, tool_input={'x': [SECRET]}, error=SECRET), self.signals, NOW, 'autor')
        text = self.stored().read_text()
        self.assertNotIn(SECRET, text)
        self.assertEqual(set(json.loads(text)) - set(ps.SNAPSHOT_FIELDS), set())
        self.assertEqual(json.loads(text)['last_event'], 'UserPromptSubmit')

    def test_rejected_payload_leaves_only_a_counter(self):
        for raw in (b'{"session_id": "' + SECRET.encode() + b' x", "hook_event_name": "Stop"}', SECRET.encode(),
                    payload('Neznamy-' + SECRET), payload('Stop', agent_id=SECRET)):
            self.assertIsNone(ps.ingest('claude', raw, self.signals, NOW))
        for path in self.signals.rglob('*'):
            if path.is_file():
                self.assertNotIn(SECRET, path.read_text())
        self.assertEqual(set(json.loads((self.signals / 'rejected.json').read_text())) - set(ps.REJECT_REASONS), {'updated_at'})

    def write_tampered(self, **extra):
        ps.ingest('claude', payload('Stop'), self.signals, NOW, 'autor')
        path = self.stored()
        value = json.loads(path.read_text())
        value.update(extra)
        path.write_text(json.dumps(value))
        return path

    def test_foreign_field_in_a_stored_file_is_not_carried_on_by_the_next_write(self):
        path = self.write_tampered(prompt=SECRET, nested={'a': SECRET})
        ps.ingest('claude', payload('Stop'), self.signals, NOW + 1, 'autor')
        self.assertNotIn(SECRET, path.read_text())
        self.assertEqual(json.loads(path.read_text())['events'], 2)

    def test_free_text_in_an_allowed_field_is_not_carried_on(self):
        path = self.write_tampered(last_event=SECRET, actor=SECRET, turn=SECRET, waiting_kind=SECRET)
        ps.ingest('claude', payload('SessionEnd'), self.signals, NOW + 1)
        self.assertNotIn(SECRET, path.read_text())

    def test_reading_a_tampered_file_returns_no_foreign_value(self):
        self.write_tampered(prompt=SECRET, actor=SECRET, last_event=SECRET, first_seen_at=float('nan'), events=True)
        session = ps.read_sessions(self.signals)[0]
        self.assertNotIn(SECRET, json.dumps(session))
        self.assertNotIn('events', session)
        self.assertNotIn('first_seen_at', session)
        self.assertNotIn(SECRET, json.dumps(ps.classify(session, NOW)))

    def test_clean_snapshot_refuses_what_cannot_identify_a_session(self):
        good = {'schema_version': ps.SCHEMA_VERSION, 'provider': 'claude', 'session_id': 's'}
        self.assertEqual(ps.clean_snapshot(good), good)
        for bad in (None, [], 'x', {}, {**good, 'schema_version': 99}, {**good, 'provider': 'jiny'}, {**good, 'session_id': 'a b'},
                    {**good, 'session_id': {'x': 1}}, {**good, 'schema_version': True}):
            self.assertIsNone(ps.clean_snapshot(bad))

    def test_existing_hook_behaviour_is_preserved(self):
        ps.ingest('claude', payload('UserPromptSubmit'), self.signals, NOW, 'autor')
        ps.ingest('claude', payload('Notification', notification_type='permission_prompt'), self.signals, NOW + 1, 'autor')
        session = ps.read_sessions(self.signals)[0]
        self.assertEqual((session['turn'], session['waiting_kind'], session['actor'], session['events']), ('running', 'confirmed', 'autor', 2))
        self.assertEqual(ps.classify(session, NOW + 2)['state'], 'waiting_input')
        self.assertIs(ps.classify(session, NOW + 2)['proves_task_progress'], False)

    def test_hook_entry_point_still_runs_isolated_and_silent(self):
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'agentops/provider_signals.py'), 'ingest', '--provider', 'claude',
                              '--state-dir', str(self.signals)], input=payload('UserPromptSubmit', prompt=SECRET),
                             capture_output=True, timeout=30, cwd=self.root)
        self.assertEqual((run.returncode, run.stdout, run.stderr), (0, b'', b''))
        self.assertNotIn(SECRET, self.stored().read_text())


class Records(Base):
    def run_event(self, **change):
        return {'task_id': str(uuid.uuid4()), 'run_id': str(uuid.uuid4()), 'parent_id': None, 'at': NOW, 'source': 'agentops',
                'evidence': 'observed', 'actor': 'autor', 'action': 'start', 'outcome': 'unknown', 'error_code': None,
                'usage_tokens': None, **change}

    def test_run_event_keeps_task_run_and_parent_apart(self):
        parent = str(uuid.uuid4())
        record = m.validate_record('run_event', self.run_event(parent_id=parent))
        self.assertEqual(record['parent_id'], parent)
        self.assertNotEqual(record['task_id'], record['run_id'])

    def test_unknown_usage_is_null_and_must_be_stated(self):
        self.assertIsNone(m.validate_record('run_event', self.run_event())['usage_tokens'])
        self.assertEqual(m.validate_record('run_event', self.run_event(usage_tokens=0))['usage_tokens'], 0)
        missing = self.run_event()
        del missing['usage_tokens']
        with self.assertRaises(ValueError):
            m.validate_record('run_event', missing)

    def test_invalid_records_are_refused_without_repeating_the_value(self):
        for change in ({'prompt': SECRET}, {'error_code': SECRET + ' text'}, {'action': SECRET}, {'actor': SECRET},
                       {'details': {'a': SECRET}}, {'usage_tokens': {'input': 1}}, {'usage_tokens': [1]}, {'usage_tokens': True},
                       {'usage_tokens': -1}, {'usage_tokens': '5'}, {'at': float('nan')}, {'at': float('inf')}, {'outcome': 'ok ' + SECRET},
                       {'evidence': 'proved'}, {'source': SECRET}, {'task_id': 'neni-uuid'}, {'parent_id': SECRET}):
            with self.subTest(change=str(change)[:40]), self.assertRaises(ValueError) as caught:
                m.validate_record('run_event', self.run_event(**change))
            self.assertNotIn(SECRET, str(caught.exception))

    def test_every_field_is_documented_with_a_purpose(self):
        document = (ROOT / 'docs/metadata-policy.md').read_text()
        for name, fields in m.RECORDS.items():
            self.assertIn(f'`{name}`', document)
            section = document.split(f'(`{name}`')[1].split('\n### ')[0]
            for field, (_, purpose) in fields.items():
                self.assertIn(f'| `{field}` | {purpose} |', section)


class Export(Base):
    def setUp(self):
        super().setUp()
        self.task = self.seeded_task()
        ps.ingest('codex', payload('UserPromptSubmit', session='sessionid-' + SECRET, prompt=SECRET), self.signals, NOW, 'autor')

    def test_no_secret_in_the_file_or_output(self):
        output, _, _ = self.export()
        self.assertNotIn(SECRET, output.read_text())

    def test_expected_metadata_is_kept(self):
        _, data, _ = self.export()
        task = data['tasks'][0]
        self.assertEqual((task['id'], task['owner'], task['state'], task['target'], task['purpose'], task['has_artifact']),
                         (self.task, 'autor', 'handoff_pending', 'kontrolor', 'review', True))
        self.assertNotIn('title', task)
        self.assertNotIn('artifact', task)
        actions = [event['action'] for event in data['events']]
        self.assertEqual(actions, ['create', 'start', 'progress', 'block', 'start', 'cost', 'postcheck', 'offer'])
        check = data['events'][6]
        self.assertEqual((check['verdict'], check['report_sha256']), ('fail', 'a' * 64))
        block = data['events'][3]
        self.assertEqual((block['from_state'], block['to_state']), ('running', 'blocked'))
        self.assertEqual(data['events'][7]['target'], 'kontrolor')

    def test_session_id_becomes_a_reference(self):
        _, data, _ = self.export()
        session = data['sessions'][0]
        self.assertEqual(session['session_ref'], ps._session_file(self.signals, 'codex', 'sessionid-' + SECRET).stem)
        self.assertEqual((session['provider'], session['actor'], session['turn']), ('codex', 'autor', 'running'))
        self.assertNotIn('session_id', session)

    def test_only_allowed_fields_appear_anywhere(self):
        _, data, _ = self.export('--include-references')
        for name, rows in (('export_task', data['tasks']), ('export_event', data['events']), ('export_session', data['sessions']),
                           ('export_cost', data['recorded_costs'])):
            self.assertTrue(rows)
            for row in rows:
                self.assertLessEqual(set(row), set(m.RECORDS[name]))
                self.assertFalse(any(isinstance(value, (dict, list)) for value in row.values()))

    def test_foreign_nested_details_in_the_audit_trail_do_not_pass(self):
        connection = sqlite3.connect(self.db)
        connection.execute('INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)', (self.task, NOW, 'autor', 'jina-akce', json.dumps({
            'prompt': SECRET, 'nested': {'x': {'y': SECRET}}, 'target': SECRET, 'purpose': SECRET, 'verdict': 'pass ' + SECRET,
            'changes': {'version': SECRET, 'title': SECRET}, 'from_state': {'a': SECRET}, 'to_state': ['x', SECRET], 'report_sha256': SECRET})))
        connection.commit()
        connection.close()
        output, data, _ = self.export()
        self.assertNotIn(SECRET, output.read_text())
        event = data['events'][-1]
        self.assertEqual(event['action'], 'jina-akce')  # the audit step stays visible
        self.assertGreater(data['counts']['dropped_values'], 0)

    def test_unreadable_details_are_named_and_not_guessed(self):
        connection = sqlite3.connect(self.db)
        connection.execute('INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)', (self.task, NOW, 'autor', 'x', 'neni json ' + SECRET))
        connection.execute('INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)', (self.task, NOW, 'autor', 'y', json.dumps([SECRET])))
        connection.commit()
        connection.close()
        output, data, _ = self.export()
        self.assertNotIn(SECRET, output.read_text())
        self.assertIs(data['events'][-1]['details_unreadable'], True)
        self.assertIs(data['events'][-2]['details_unreadable'], True)

    def test_free_text_as_action_or_actor_is_dropped_but_the_event_stays_counted(self):
        connection = sqlite3.connect(self.db)
        connection.execute('INSERT INTO events(task_id,at,actor,action,details) VALUES(?,?,?,?,?)', (self.task, NOW, 'ACTOR ' + SECRET, 'ACTION ' + SECRET, '{}'))
        connection.commit()
        total = connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        connection.close()
        output, data, _ = self.export()
        self.assertNotIn(SECRET, output.read_text())
        self.assertEqual(len(data['events']), total)
        self.assertNotIn('actor', data['events'][-1])

    def test_audit_is_complete_and_not_sampled(self):
        connection = sqlite3.connect(self.db)
        total = connection.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        seqs = [row[0] for row in connection.execute('SELECT seq FROM events ORDER BY seq')]
        connection.close()
        _, data, _ = self.export()
        self.assertEqual([event['seq'] for event in data['events']], seqs)
        self.assertEqual(data['counts']['events'], total)
        self.assertEqual(data['sampling']['critical_audit'], 'never')

    def test_references_only_on_request_and_only_plain_local_paths(self):
        _, plain, _ = self.export()
        self.assertFalse(any('report' in event for event in plain['events']))
        output, data, _ = self.export('--include-references')
        self.assertEqual(data['tasks'][0]['artifact'], f'/vystupy/{SECRET}.md')  # explicit: a path is shown as it is
        self.assertIs(data['references_included'], True)
        self.assertIn(SECRET, output.read_text())

    def test_reference_that_is_not_a_plain_path_is_withheld(self):
        for bad in ('https://uzivatel:' + SECRET + '@server/x', '/cesta?token=' + SECRET, '/a=' + SECRET, '/s mezerou/' + SECRET,
                    'relativni/' + SECRET, '/DANĚ/' + SECRET, '/home/x/.ssh/' + SECRET, '/x/auth.json'):
            self.assertFalse(m._reference(bad), bad)
        task = self.ledger.create('t', 'autor', 'autor')['id']
        self.ledger.change(task, 'autor', 'start')
        self.ledger.change(task, 'autor', 'offer', target='k', purpose='review', artifact='/cesta?token=' + SECRET)
        output, data, _ = self.export('--include-references')
        self.assertNotIn('token=' + SECRET, output.read_text())
        self.assertEqual(data['counts']['withheld_references'], 1)

    def test_unrecorded_cost_is_absent_not_zero(self):
        _, data, _ = self.export()
        self.assertEqual(data['recorded_costs'], [{'currency': 'CZK', 'kind': 'actual', 'micros': 1500, 'records': 1}])
        self.assertIn('unknown, not zero', ' '.join(data['limits']))
        self.assertFalse(any('cost' in key or 'usage' in key for task in data['tasks'] for key in task))

    def test_export_file_is_private_and_never_overwritten(self):
        output, _, _ = self.export()
        self.assertEqual(output.stat().st_mode & 0o077, 0)
        before = output.read_bytes()
        run = self.cli('metadata-export', '--signals-dir', str(self.signals), '--output', str(output))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(output.read_bytes(), before)

    def test_export_changes_nothing_it_read(self):
        def state():
            connection = sqlite3.connect(self.db)
            rows = [connection.execute(f'SELECT * FROM {name} ORDER BY 1').fetchall() for name in ('tasks', 'events', 'commands', 'costs')]
            connection.close()
            files = {str(path): path.read_bytes() for path in self.signals.rglob('*.json')}
            return rows, files
        before = state()
        self.export()
        self.assertEqual(before, state())

    def test_version_and_date_are_recorded(self):
        _, data, _ = self.export()
        self.assertEqual(data['agentops_version'], m.__version__)
        self.assertRegex(data['generated_at'], r'^\d{4}-\d\d-\d\dT')


class ErrorPaths(Base):
    def test_broken_ledger_message_has_no_content(self):
        self.ledger.close()
        self.db.write_bytes(SECRET.encode() * 100)
        output = self.root / 'x.json'
        run = self.cli('metadata-export', '--signals-dir', str(self.signals), '--output', str(output))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stderr.strip(), 'AgentOps: Ledger cannot be read')
        self.assertNotIn(SECRET, run.stdout)
        self.assertFalse(output.exists())

    def test_missing_ledger_is_not_created_by_an_export(self):
        missing = self.root / 'neni/tasks.sqlite'
        with self.assertRaises(ValueError):
            m.build_export(missing, self.signals)
        self.assertFalse(missing.parent.exists())

    def test_invalid_retention_config_does_not_echo_the_value(self):
        self.config.write_text(json.dumps({'retention': {'provider_signal_days': SECRET}}))
        run = self.cli('retention', 'plan', '--signals-dir', str(self.signals))
        self.assertEqual(run.returncode, 1)
        self.assertNotIn(SECRET, run.stdout + run.stderr)

    def test_unreadable_config_does_not_echo_its_content(self):
        self.config.write_text('{"a": "' + SECRET)
        run = self.cli('retention', 'plan', '--signals-dir', str(self.signals))
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stderr.strip(), 'AgentOps: Configuration cannot be read')
        self.assertNotIn(SECRET, run.stdout)

    def test_invalid_record_in_the_export_stops_instead_of_passing(self):
        with self.assertRaises(ValueError):
            m.validate_record('export_task', {'id': str(uuid.uuid4()), 'title': SECRET})


class SnapshotContract(Base):
    def test_snapshot_task_rows_have_a_fixed_field_list(self):
        self.seeded_task()
        result = snapshot(self.ledger, {}, signals={})
        self.assertEqual(set(result['tasks'][0]), {'id', 'title', 'owner', 'state', 'target', 'version', 'created', 'updated',
                                                   'progress_age_seconds', 'attention'})
        # Notes and cost sources stay out; a title is declared text and is documented as such.
        without_title = json.dumps({**result, 'tasks': [{**row, 'title': ''} for row in result['tasks']],
                                    'queue': {**result['queue'], 'rows': []}})
        self.assertNotIn(SECRET, without_title)

    def test_provider_observation_in_a_snapshot_holds_no_foreign_value(self):
        session = 'sess-x'
        ps.ingest('claude', payload('Stop', session=session), self.signals, NOW, 'autor')
        path = ps._session_file(self.signals, 'claude', session)
        value = json.loads(path.read_text())
        value['prompt'] = SECRET
        path.write_text(json.dumps(value))
        config = {'provider_bindings': {'autor': {'provider': 'claude', 'session_id': session, 'state_dir': str(self.signals), 'max_age_seconds': 900}}}
        result = snapshot(self.ledger, config, now=NOW + 1, signals={})
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertEqual(result['signals']['autor']['provider_observation']['state'], 'idle')


class Retention(Base):
    """A synthetic state tree: old and new sessions next to everything that must survive."""

    def session(self, name, age_seconds, provider='claude'):
        ps.ingest(provider, payload('Stop', session=name), self.signals, NOW - age_seconds, 'autor')
        return ps._session_file(self.signals, provider, name)

    def config_days(self, days):
        return {'retention': {'provider_signal_days': days}}

    def tree(self, root):
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(root.rglob('*')) if path.is_file() and path.name not in ('.lock', '.rejected.lock')}

    def test_default_plan_deletes_nothing(self):
        old = self.session('stara', 400 * DAY)
        before = self.tree(self.signals)
        result = m.plan({}, self.signals, self.db, now=NOW)
        self.assertIs(result['deletes_nothing'], True)
        self.assertEqual([row['file'] for row in result['auxiliary']['provider_signals']['expired']], [old.name])
        self.assertEqual(before, self.tree(self.signals))

    def test_cli_plan_deletes_nothing_and_there_is_no_implicit_apply(self):
        self.session('stara', 4000 * DAY)
        before = self.tree(self.signals)
        run = self.cli('retention', 'plan', '--signals-dir', str(self.signals))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['mode'], 'plan')
        self.assertEqual(before, self.tree(self.signals))
        self.assertEqual(self.cli('retention', '--signals-dir', str(self.signals)).returncode, 2)

    def test_time_boundary(self):
        limit = 14 * DAY
        exact = self.session('presne', limit)
        over = self.session('o-vterinu', limit + 1)
        under = self.session('pod', limit - 1)
        review = m.plan({}, self.signals, now=NOW)['auxiliary']['provider_signals']
        self.assertEqual([row['file'] for row in review['expired']], [over.name])
        self.assertEqual(review['kept'], 2)
        self.assertTrue(exact.exists() and under.exists())

    def test_configured_limit_changes_the_boundary(self):
        self.session('s', 10 * DAY)
        self.assertEqual(len(m.plan({}, self.signals, now=NOW)['auxiliary']['provider_signals']['expired']), 0)
        self.assertEqual(len(m.plan(self.config_days(7), self.signals, now=NOW)['auxiliary']['provider_signals']['expired']), 1)

    def test_invalid_limits_are_refused(self):
        for bad in ({'retention': {'provider_signal_days': value}} for value in (0, 6, 366, -1, True, 14.5, '14', None)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.retention_policy(bad)
        for bad in ({'retention': []}, {'retention': {'jine': 1}}, []):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                m.retention_policy(bad)

    def test_only_valid_old_sessions_can_expire(self):
        folder = self.session('stara', 100 * DAY).parent
        (folder / 'cizi.json').write_text(json.dumps({'cokoli': 1}))
        (folder / 'rozbity.json').write_text('{nope')
        target = self.root / 'mimo.json'
        target.write_text('{}')
        (folder / 'odkaz.json').symlink_to(target)
        future = self.session('budoucnost', -3 * DAY)
        renamed = folder / ('a' * 32 + '.json')
        renamed.write_text(self.session('prejmenovana', 100 * DAY).read_text())
        ps._session_file(self.signals, 'claude', 'prejmenovana').unlink()
        review = m.plan({}, self.signals, now=NOW)['auxiliary']['provider_signals']
        self.assertEqual(len(review['expired']), 1)
        reasons = {row['file']: row['reason'] for row in review['unknown']}
        self.assertEqual(reasons, {'cizi.json': 'foreign', 'rozbity.json': 'unreadable', 'odkaz.json': 'not_a_regular_file',
                                   future.name: 'dated_in_future', renamed.name: 'foreign'})

    def test_apply_on_staging_removes_exactly_the_plan_and_no_critical_trace(self):
        old_a, old_b = self.session('a', 100 * DAY), self.session('b', 100 * DAY, 'codex')
        new = self.session('c', DAY)
        task = self.seeded_task()
        reports = self.root / 'state/regression-evidence'
        reports.mkdir()
        (reports / 'report.json').write_text('{"stare": true}')
        os.utime(reports / 'report.json', (0, 0))
        (self.signals / 'claude' / 'cizi.json').write_text('{}')
        before_events = self.ledger.events(task)
        before_state = self.tree(self.root / 'state')
        result = m.plan({}, self.signals, self.db, now=NOW)
        outcome = m.apply_plan({}, self.signals, result['plan_sha256'], self.root / 'r.json', now=NOW, production_dir=self.root / 'produkce')
        self.assertEqual({row['file'] for row in outcome['removed']}, {old_a.name, old_b.name})
        self.assertEqual(outcome['skipped'], [])
        self.assertFalse(old_a.exists() or old_b.exists())
        self.assertTrue(new.exists() and (self.signals / 'claude/cizi.json').exists())
        self.assertEqual(before_events, self.ledger.events(task))
        self.assertTrue((reports / 'report.json').exists())
        after = self.tree(self.root / 'state')
        for name in before_state:
            if 'tasks.sqlite' not in name:
                self.assertEqual(before_state[name], after[name], name)

    def test_ledger_is_only_reported_never_a_candidate(self):
        task = self.seeded_task()
        count = len(self.ledger.events(task))
        result = m.plan({}, self.signals, self.db, now=NOW + 10000 * DAY)
        self.assertEqual(result['ledger']['events'], count)
        self.assertIn('ledger tasks, events, commands and costs', result['never_expires'])
        self.assertEqual(result['auxiliary']['provider_signals']['expired'], [])

    def test_append_only_audit_still_holds(self):
        task = self.seeded_task()
        for sql in ('DELETE FROM events', "UPDATE events SET actor='x'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.ledger.db.execute(sql)
        self.assertTrue(self.ledger.events(task))

    def test_apply_refuses_production_even_through_a_symlink(self):
        production = self.root / 'produkce'
        old = production / 'claude'
        ps.ingest('claude', payload('Stop', session='p'), production, NOW - 100 * DAY)
        alias = self.root / 'alias'
        alias.symlink_to(production)
        plan_hash = m.plan({}, production, now=NOW)['plan_sha256']
        for folder in (production, alias):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                m.apply_plan({}, folder, plan_hash, self.root / 'r.json', now=NOW, production_dir=production)
        self.assertEqual(len(list(old.glob('*.json'))), 1)

    def test_cli_apply_refuses_the_default_folder(self):
        default = self.root / '.local/state/agentops/provider-signals'
        ps.ingest('claude', payload('Stop', session='p'), default, 1000.0)
        run = self.cli('retention', 'plan')
        self.assertEqual(run.returncode, 0, run.stderr)
        plan_hash = json.loads(run.stdout)['plan_sha256']
        self.assertEqual(json.loads(run.stdout)['auxiliary']['provider_signals']['expired'][0]['last_event_at'], 1000.0)
        receipt = self.root / 'r.json'
        run = self.cli('retention', 'apply', '--signals-dir', str(default), '--plan-sha256', plan_hash, '--output', str(receipt))
        self.assertEqual(run.returncode, 1, run.stdout)
        self.assertIn('production', run.stderr)
        self.assertEqual(len(list((default / 'claude').glob('*.json'))), 1)
        self.assertFalse(receipt.exists())

    def test_apply_needs_the_exact_plan(self):
        old = self.session('a', 100 * DAY)
        current = m.plan({}, self.signals, now=NOW)['plan_sha256']
        production = self.root / 'produkce'
        with self.assertRaises(ValueError):
            m.apply_plan({}, self.signals, 'b' * 64, self.root / 'r.json', now=NOW, production_dir=production)
        self.assertTrue(old.exists())
        self.session('novy-kandidat', 100 * DAY)  # the plan no longer describes what would go
        with self.assertRaises(ValueError):
            m.apply_plan({}, self.signals, current, self.root / 'r.json', now=NOW, production_dir=production)
        self.assertTrue(old.exists())
        with self.assertRaises(ValueError):
            m.apply_plan(self.config_days(7), self.signals, current, self.root / 'r.json', now=NOW, production_dir=production)

    def test_session_refreshed_after_the_review_is_skipped(self):
        old = self.session('a', 100 * DAY)
        row = {'provider': 'claude', 'file': old.name, 'last_event_at': NOW - 100 * DAY}
        ps.ingest('claude', payload('Stop', session='a'), self.signals, NOW, 'autor')
        removed, skipped = ps.remove_expired(self.signals, [row], NOW, 14 * DAY)
        self.assertEqual((removed, skipped), ([], [{**row, 'reason': 'changed_since_review'}]))
        self.assertTrue(old.exists())

    def test_cli_apply_end_to_end_writes_a_private_receipt_once(self):
        old = self.session('a', 4000 * DAY)
        plan_run = self.cli('retention', 'plan', '--signals-dir', str(self.signals))
        plan_hash = json.loads(plan_run.stdout)['plan_sha256']
        receipt = self.root / 'dukazy/receipt.json'
        args = ('retention', 'apply', '--signals-dir', str(self.signals), '--plan-sha256', plan_hash, '--output', str(receipt))
        run = self.cli(*args)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(old.exists())
        self.assertEqual(receipt.stat().st_mode & 0o077, 0)
        self.assertEqual(json.loads(receipt.read_text())['removed'][0]['file'], old.name)
        self.assertEqual(self.cli(*args).returncode, 1)  # the receipt exists: nothing runs a second time

    def test_existing_receipt_stops_apply_before_anything_is_removed(self):
        old = self.session('a', 4000 * DAY)
        plan_hash = json.loads(self.cli('retention', 'plan', '--signals-dir', str(self.signals)).stdout)['plan_sha256']
        receipt = self.root / 'receipt.json'
        receipt.write_text('puvodni')
        run = self.cli('retention', 'apply', '--signals-dir', str(self.signals), '--plan-sha256', plan_hash, '--output', str(receipt))
        self.assertEqual(run.returncode, 1)
        self.assertTrue(old.exists())
        self.assertEqual(receipt.read_text(), 'puvodni')

    def apply_cli(self, receipt):
        plan_hash = json.loads(self.cli('retention', 'plan', '--signals-dir', str(self.signals)).stdout)['plan_sha256']
        return self.cli('retention', 'apply', '--signals-dir', str(self.signals), '--plan-sha256', plan_hash, '--output', str(receipt))

    def test_unusable_receipt_path_removes_nothing_and_leaks_no_path(self):
        parent = self.root / SECRET
        parent.write_text('neni slozka')
        dangling = self.root / 'odkaz.json'
        dangling.symlink_to(self.root / 'neexistuje' / SECRET)
        locked = self.root / 'zamcena'
        locked.mkdir()
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        old = self.session('a', 4000 * DAY)
        for receipt in (parent / 'r.json', parent / 'hloubeji/r.json', dangling, locked / 'r.json', locked / 'nova/r.json'):
            with self.subTest(receipt=receipt.name):
                run = self.apply_cli(receipt)
                self.assertEqual(run.returncode, 1, run.stdout)
                self.assertEqual(run.stderr.strip(), 'AgentOps: Receipt could not be prepared; nothing was removed')
                self.assertNotIn(SECRET, run.stdout)
                self.assertTrue(old.exists())
        self.assertFalse((self.root / 'neexistuje').exists())

    def test_receipt_exists_before_the_first_removal(self):
        old = self.session('a', 100 * DAY)
        receipt = self.root / 'r.json'
        seen = []
        real = ps.remove_expired
        def watching(*args):
            seen.append((old.exists(), json.loads(receipt.read_text())['mode'], json.loads(receipt.read_text())['planned'][0]['file']))
            return real(*args)
        plan_hash = m.plan({}, self.signals, now=NOW)['plan_sha256']
        with mock.patch.object(m.provider_signals, 'remove_expired', watching):
            m.apply_plan({}, self.signals, plan_hash, receipt, now=NOW, production_dir=self.root / 'p')
        self.assertEqual(seen, [(True, 'started', old.name)])
        self.assertEqual(json.loads(receipt.read_text())['mode'], 'applied')
        self.assertEqual(receipt.stat().st_mode & 0o077, 0)

    def test_failed_plan_check_leaves_no_receipt(self):
        self.session('a', 100 * DAY)
        receipt = self.root / 'r.json'
        with self.assertRaises(ValueError):
            m.apply_plan({}, self.signals, 'b' * 64, receipt, now=NOW, production_dir=self.root / 'p')
        self.assertFalse(receipt.exists())

    def test_one_file_that_cannot_be_removed_is_recorded_and_the_rest_go(self):
        first, second = self.session('a', 100 * DAY), self.session('b', 100 * DAY)
        stuck = sorted([first, second])[0]
        receipt = self.root / 'r.json'
        real = Path.unlink
        def failing(path, *args, **kwargs):
            if path == stuck:
                raise PermissionError(13, SECRET, str(path))
            return real(path, *args, **kwargs)
        plan_hash = m.plan({}, self.signals, now=NOW)['plan_sha256']
        with mock.patch.object(Path, 'unlink', failing):
            result = m.apply_plan({}, self.signals, plan_hash, receipt, now=NOW, production_dir=self.root / 'p')
        self.assertEqual([row['file'] for row in result['skipped']], [stuck.name])
        self.assertEqual(result['skipped'][0]['reason'], 'could_not_remove')
        self.assertEqual(len(result['removed']), 1)
        self.assertTrue(stuck.exists())
        written = receipt.read_text()
        self.assertEqual(json.loads(written)['skipped'][0]['file'], stuck.name)
        self.assertNotIn(SECRET, written)

    def test_failure_at_the_end_keeps_the_started_record(self):
        old = self.session('a', 100 * DAY)
        receipt = self.root / 'r.json'
        plan_hash = m.plan({}, self.signals, now=NOW)['plan_sha256']
        with mock.patch.object(m.os, 'replace', side_effect=OSError(5, SECRET, SECRET)), self.assertRaises(ValueError) as caught:
            m.apply_plan({}, self.signals, plan_hash, receipt, now=NOW, production_dir=self.root / 'p')
        self.assertNotIn(SECRET, str(caught.exception))
        record = json.loads(receipt.read_text())
        self.assertEqual((record['mode'], record['planned'][0]['file']), ('started', old.name))
        self.assertEqual([name for name in os.listdir(self.root) if name.startswith('.receipt-')], [])

    def test_export_output_error_leaks_no_path(self):
        parent = self.root / SECRET
        parent.write_text('neni slozka')
        run = self.cli('metadata-export', '--signals-dir', str(self.signals), '--output', str(parent / 'x.json'))
        self.assertEqual((run.returncode, run.stderr.strip()), (1, 'AgentOps: A file for this command could not be read or written'))
        self.assertNotIn(SECRET, run.stdout)

    def test_unreadable_config_path_leaks_no_path(self):
        self.config.mkdir()
        run = self.cli('retention', 'plan', '--signals-dir', str(self.signals))
        self.assertEqual((run.returncode, run.stderr.strip()), (1, 'AgentOps: Configuration cannot be read'))

    def test_prune_keeps_foreign_files_and_stays_compatible(self):
        old = self.session('a', 100 * DAY)
        foreign = old.parent / 'cizi.json'
        foreign.write_text('{"x": 1}')
        self.assertEqual(ps.prune(self.signals, now=NOW), 1)
        self.assertTrue(foreign.exists())

    def test_plan_output_has_no_session_id_or_foreign_value(self):
        path = self.session('sess-' + SECRET, 100 * DAY)
        value = json.loads(path.read_text())
        value['prompt'] = SECRET
        path.write_text(json.dumps(value))
        (path.parent / (SECRET + '.json')).write_text('{}')
        text = json.dumps(m.plan({}, self.signals, now=NOW)['auxiliary']['provider_signals']['expired'])
        self.assertNotIn(SECRET, text)
        self.assertTrue(re.search('[a-f0-9]{32}', text))


if __name__ == '__main__':
    unittest.main()
