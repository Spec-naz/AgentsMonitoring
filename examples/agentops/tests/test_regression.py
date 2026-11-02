import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agentops import regression as r

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / 'examples/regression'


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.suite = r.read_document(EXAMPLES / 'suite.json')
        self.baseline = r.read_document(EXAMPLES / 'baseline.json')
        self.candidate = r.read_document(EXAMPLES / 'candidate.json')
        self.passing = r.read_document(EXAMPLES / 'passing.json')

    def case(self, check):
        return next(c for c in self.suite['cases'] if c['check'] == check)

    def test_same_score_still_exposes_individual_regression(self):
        before = r.run(self.suite, self.baseline, 'baseline-v1')
        after = r.run(self.suite, self.candidate, 'candidate-v2')
        self.assertEqual(sum(x['verdict'] == 'pass' for x in before['results']), 2)
        self.assertEqual(sum(x['verdict'] == 'pass' for x in after['results']), 2)
        report = r.compare(before, after)
        self.assertEqual([(x['case_id'], x['change']) for x in report['cases']],
                         [('backup-folder', 'regression'), ('restored-app', 'improvement'), ('timeout-unknown', 'unchanged')])
        self.assertEqual(report['quality_gate'], 'blocked')
        self.assertFalse(report['deployment_authorized'])

    def test_full_manifest_contract_change_rejects_comparison_even_with_same_case_ids(self):
        before = r.run(self.suite, self.passing, 'v1')
        changed = copy.deepcopy(self.suite)
        changed['cases'][0]['instruction'] += ' Changed instruction.'
        with self.assertRaises(ValueError): r.run(changed, self.passing, 'v2')
        observations = r.observation_template(changed)
        after = r.run(changed, observations, 'v2')
        with self.assertRaises(ValueError): r.compare(before, after)

    def test_changed_nonpassing_evidence_is_not_labelled_unchanged(self):
        before = r.run(self.suite, self.baseline, 'v1')
        observations = copy.deepcopy(self.baseline)
        observations['observations'][1]['observation'].pop('app_healthy')
        after = r.run(self.suite, observations, 'v2')
        change = next(row for row in r.compare(before, after)['cases'] if row['case_id'] == 'restored-app')
        self.assertEqual((change['baseline'], change['candidate'], change['change']),
                         ('fail', 'unverified', 'changed-nonpassing'))

    def test_missing_observation_or_evidence_is_unverified_not_pass(self):
        observations = r.observation_template(self.suite)
        self.assertEqual({x['verdict'] for x in r.run(self.suite, observations, 'v1')['results']}, {'unverified'})
        observations = copy.deepcopy(self.passing)
        observations['observations'][0]['observation'].pop('evidence_ref')
        observations['observations'].pop()
        results = r.run(self.suite, observations, 'v1')['results']
        self.assertEqual([x['verdict'] for x in results], ['unverified', 'pass', 'unverified'])

    def test_backup_and_restore_require_actual_destination_and_separate_health(self):
        backup = self.case('backup_destination')
        self.assertEqual(r.evaluate(backup, {'destination':'synthetic:wrong', 'content_matches':True}), 'fail')
        self.assertEqual(r.evaluate(backup, {'content_matches':True}), 'unverified')
        restore = self.case('restored_application')
        self.assertEqual(r.evaluate(restore, {'data_readable':True, 'app_healthy':False}), 'fail')
        self.assertEqual(r.evaluate(restore, {'data_readable':True}), 'unverified')
        with self.assertRaises(ValueError): r.evaluate(restore, {'data_readable':True, 'process_running':True})

    def test_timeout_never_accepts_unknown_or_committed_retry(self):
        case = self.case('timeout_reconciliation')
        base = {'operation_id':'synthetic-op-001','evidence_ref':'synthetic:target','writes_after_timeout':0}
        for state, action in [('unknown','stop_reconcile'),('committed','verify_existing')]:
            self.assertEqual(r.evaluate(case, dict(base,target_state=state,observed_action=action)), 'pass')
            self.assertEqual(r.evaluate(case, dict(base,target_state=state,observed_action='retry_same_key',writes_after_timeout=1)), 'fail')
        self.assertEqual(r.evaluate(case, dict(base,target_state='absent_final',observed_action='retry_same_key',writes_after_timeout=1,definitive=True)), 'pass')
        self.assertEqual(r.evaluate(case, dict(base,target_state='absent_final',observed_action='retry_same_key',writes_after_timeout=1)), 'fail')
        self.assertEqual(r.evaluate(case, dict(base,target_state='unknown',observed_action='stop_reconcile',operation_id='new-key')), 'fail')
        self.assertEqual(r.evaluate(case, dict(base,target_state='absent_final',observed_action='retry_same_key',writes_after_timeout=2,definitive=True)), 'fail')

    def test_unreviewed_real_cases_and_raw_content_are_rejected(self):
        for kind in ('sanitized','real'):
            suite = copy.deepcopy(self.suite);suite['cases'][0]['origin_kind'] = kind
            with self.assertRaises(ValueError): r.suite_document(suite)
        observations = copy.deepcopy(self.passing)
        observations['observations'][0]['observation']['raw_chat'] = 'DO-NOT-PRINT'
        with self.assertRaises(ValueError) as caught: r.run(self.suite, observations, 'v1')
        self.assertNotIn('DO-NOT-PRINT', str(caught.exception))

    def test_reports_detect_tamper_and_never_self_authorize(self):
        before = r.run(self.suite, self.baseline, 'v1')
        passing = r.run(self.suite, self.passing, 'v2')
        self.assertNotIn('observation', passing['results'][0])
        comparison = r.compare(before, passing)
        self.assertEqual(comparison['quality_gate'], 'review-required')
        self.assertTrue(comparison['independent_review_required'])
        self.assertFalse(comparison['deployment_authorized'])
        changed = copy.deepcopy(before);changed['results'][0]['verdict'] = 'unverified'
        with self.assertRaises(ValueError): r.compare(changed, passing)
        changed = copy.deepcopy(before);changed['results'].pop();changed['report_sha256'] = r.digest({k:v for k,v in changed.items() if k != 'report_sha256'})
        with self.assertRaises(ValueError): r.compare(changed, passing)

    def test_duplicate_keys_nonfinite_special_files_and_symlinks_fail_closed(self):
        path = self.root / 'input.json'
        for content in ('{"x":1,"x":2}', '{"x":NaN}'):
            path.write_text(content)
            with self.assertRaises(ValueError): r.read_document(path)
        path.unlink();path.symlink_to(EXAMPLES / 'suite.json')
        with self.assertRaises(OSError): r.read_document(path)
        path.unlink();os.mkfifo(path)
        with self.assertRaises(ValueError): r.read_document(path)
        path.unlink();path.write_bytes(b'x' * (r.LIMIT + 1))
        with self.assertRaises(ValueError): r.read_document(path)

    def test_schema_and_check_types_are_strict_and_observation_cases_unique(self):
        for change in ('schema', 'check'):
            suite = copy.deepcopy(self.suite)
            if change == 'schema': suite['schema_version'] = True
            else: suite['cases'][0]['check'] = []
            with self.assertRaises(ValueError): r.suite_document(suite)
        observations = copy.deepcopy(self.passing)
        observations['observations'].append(observations['observations'][0])
        with self.assertRaises(ValueError): r.run(self.suite, observations, 'v1')

    def test_private_atomic_output_no_overwrite_and_no_live_replacement(self):
        path = self.root / 'evidence.json'
        r.write_report(path, {'synthetic':True})
        before = path.read_bytes()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): r.write_report(path, {'other':True})
        self.assertEqual(path.read_bytes(), before)
        link = self.root / 'symlink';link.symlink_to(path)
        with self.assertRaises(FileExistsError): r.write_report(link, {})
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.regression-*')), [])

    def test_cli_usable_offline_workflow_has_truthful_exit_codes_and_never_opens_ledger(self):
        env = dict(os.environ, HOME=str(self.root), PYTHONDONTWRITEBYTECODE='1')
        missing = self.root / 'must-not-create.sqlite'
        def invoke(*args):
            return subprocess.run([sys.executable,'-m','agentops','--db',str(missing),'regress',*map(str,args)],
                                  cwd=ROOT,env=env,capture_output=True,text=True)
        template = self.root / 'template.json'
        self.assertEqual(invoke('template','--suite',EXAMPLES/'suite.json','--output',template).returncode,0)
        self.assertEqual(r.read_document(template)['suite_sha256'],r.digest(self.suite))
        reports = []
        for name,expected in [('baseline',2),('candidate',2),('passing',0)]:
            output = self.root / (name+'.json')
            result = invoke('run','--suite',EXAMPLES/'suite.json','--observations',EXAMPLES/(name+'.json'),'--revision',name,'--output',output)
            self.assertEqual(result.returncode,expected,result.stderr)
            reports.append(output)
        compared = self.root / 'comparison.json'
        self.assertEqual(invoke('compare','--baseline',reports[0],'--candidate',reports[1],'--output',compared).returncode,2)
        self.assertEqual(r.read_document(compared)['quality_gate'],'blocked')
        reviewed = self.root / 'review-required.json'
        self.assertEqual(invoke('compare','--baseline',reports[0],'--candidate',reports[2],'--output',reviewed).returncode,0)
        self.assertFalse(r.read_document(reviewed)['deployment_authorized'])
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
