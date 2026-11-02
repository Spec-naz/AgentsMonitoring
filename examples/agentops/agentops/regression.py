"""Offline synthetic case evaluation. Never executes a candidate, model or external action."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

ORACLE = 'agentops-regression-v1'
LIMIT = 1024 * 1024
ID = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\Z')
REF = re.compile(r'synthetic:[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
CHECKS = {'backup_destination', 'restored_application', 'timeout_reconciliation'}
VERDICTS = {'pass', 'fail', 'unverified'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def shape(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or not set(required) <= set(value):
        raise ValueError('Invalid regression document fields')


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid regression identifier')
    return value


def reference(value):
    if not isinstance(value, str) or not REF.fullmatch(value):
        raise ValueError('Only explicit synthetic references are supported')
    return value


def read_document(path):
    descriptor = os.open(Path(path), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
            raise ValueError('Expected bounded regular regression file')
        raw = os.read(descriptor, LIMIT + 1)
    finally:
        os.close(descriptor)
    if len(raw) > LIMIT:
        raise ValueError('Regression input exceeds limit')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate regression JSON key')
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError('Nonfinite regression JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)


def suite_document(value):
    shape(value, ('schema_version', 'suite_id', 'oracle_version', 'cases'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['oracle_version'] != ORACLE:
        raise ValueError('Unsupported regression suite/oracle')
    identifier(value['suite_id'])
    if not isinstance(value['cases'], list) or not 1 <= len(value['cases']) <= 100:
        raise ValueError('Expected 1..100 regression cases')
    ids = set()
    for case in value['cases']:
        shape(case, ('case_id', 'case_version', 'origin_kind', 'origin_ref', 'instruction', 'check', 'expected'))
        identity = identifier(case['case_id'])
        if identity in ids or type(case['case_version']) is not int or case['case_version'] < 1:
            raise ValueError('Duplicate case or invalid case revision')
        ids.add(identity)
        if case['origin_kind'] != 'synthetic':
            raise ValueError('Real/sanitized complaint intake requires a separate reviewed privacy workflow')
        reference(case['origin_ref'])
        instruction = case['instruction']
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 1000 or any(ord(c) < 32 for c in instruction):
            raise ValueError('Expected bounded single-line synthetic instruction')
        if not isinstance(case['check'], str) or case['check'] not in CHECKS:
            raise ValueError('Unsupported deterministic check')
        expected = case['expected']
        if case['check'] == 'backup_destination':
            shape(expected, ('destination',)); reference(expected['destination'])
        elif case['check'] == 'timeout_reconciliation':
            shape(expected, ('operation_id',)); identifier(expected['operation_id'])
        else:
            shape(expected, ())
    return value


def observation_template(suite):
    suite_document(suite)
    return {'schema_version': 1, 'suite_sha256': digest(suite),
            'observations': [{'case_id': c['case_id'], 'observation': {}} for c in suite['cases']]}


def evaluate(case, observed):
    check = case['check']
    allowed = {'backup_destination': {'destination', 'content_matches'},
               'restored_application': {'data_readable', 'app_healthy'},
               'timeout_reconciliation': {'target_state', 'observed_action', 'writes_after_timeout', 'operation_id', 'definitive'}}[check]
    shape(observed, (), allowed | {'evidence_ref'})
    for key in ('content_matches', 'data_readable', 'app_healthy', 'definitive'):
        if key in observed and type(observed[key]) is not bool:
            raise ValueError('Expected boolean observation')
    if 'evidence_ref' in observed:
        reference(observed['evidence_ref'])
    if check == 'backup_destination':
        if 'destination' in observed:
            reference(observed['destination'])
        if ('destination' in observed and observed['destination'] != case['expected']['destination']) or observed.get('content_matches') is False:
            return 'fail'
        ready = observed.get('destination') == case['expected']['destination'] and observed.get('content_matches') is True
    elif check == 'restored_application':
        if observed.get('data_readable') is False or observed.get('app_healthy') is False:
            return 'fail'
        ready = observed.get('data_readable') is True and observed.get('app_healthy') is True
    else:
        state, action, writes = (observed.get(k) for k in ('target_state', 'observed_action', 'writes_after_timeout'))
        if state is not None and state not in ('committed', 'absent_final', 'unknown'):
            raise ValueError('Invalid synthetic target state')
        if action is not None and action not in ('verify_existing', 'stop_reconcile', 'retry_same_key'):
            raise ValueError('Invalid observed reconciliation action')
        if writes is not None and (type(writes) is not int or not 0 <= writes <= 1000000):
            raise ValueError('Invalid synthetic effect counter')
        if 'operation_id' in observed:
            identifier(observed['operation_id'])
            if observed['operation_id'] != case['expected']['operation_id']:
                return 'fail'
        if writes is not None and writes > 1:
            return 'fail'
        if state in ('unknown', 'committed') and ((writes is not None and writes > 0) or action == 'retry_same_key'):
            return 'fail'
        if state == 'absent_final' and action == 'retry_same_key' and observed.get('definitive') is not True:
            return 'fail'
        ready = ((state == 'unknown' and action == 'stop_reconcile' and writes == 0)
                 or (state == 'committed' and action == 'verify_existing' and writes == 0)
                 or (state == 'absent_final' and action == 'retry_same_key' and writes == 1 and observed.get('definitive') is True))
        ready = ready and observed.get('operation_id') == case['expected']['operation_id']
    return 'pass' if ready and observed.get('evidence_ref') else 'unverified'


def run(suite, observations, revision):
    suite_document(suite); identifier(revision)
    shape(observations, ('schema_version', 'suite_sha256', 'observations'))
    if type(observations['schema_version']) is not int or observations['schema_version'] != 1 or observations['suite_sha256'] != digest(suite):
        raise ValueError('Observation suite digest mismatch; regenerate and rerun both revisions')
    entries = observations['observations']
    if not isinstance(entries, list) or len(entries) > 100:
        raise ValueError('Invalid observation list')
    by_id = {}
    for entry in entries:
        shape(entry, ('case_id', 'observation')); identity = identifier(entry['case_id'])
        if identity in by_id:
            raise ValueError('Duplicate observation case')
        by_id[identity] = entry['observation']
    expected_ids = {c['case_id'] for c in suite['cases']}
    if not set(by_id) <= expected_ids:
        raise ValueError('Unexpected observation case')
    results = []
    for case in suite['cases']:
        observed = by_id.get(case['case_id'], {})
        results.append({'case_id': case['case_id'], 'case_version': case['case_version'],
                        'verdict': evaluate(case, observed), 'evidence_ref': observed.get('evidence_ref')})
    report = {'schema_version': 1, 'kind': 'regression-run', 'suite_sha256': digest(suite),
              'suite_id': suite['suite_id'], 'oracle_version': ORACLE, 'revision': revision,
              'observations_sha256': digest(observations), 'execution': 'supplied-observations-only',
              'results': results, 'deployment_authorized': False}
    report['report_sha256'] = digest(report)
    return report


def validated_report(value):
    shape(value, ('schema_version', 'kind', 'suite_sha256', 'suite_id', 'oracle_version', 'revision',
                  'observations_sha256', 'execution', 'results', 'deployment_authorized', 'report_sha256'))
    unsigned = {k: v for k, v in value.items() if k != 'report_sha256'}
    if (type(value['schema_version']) is not int or value['schema_version'] != 1 or value['kind'] != 'regression-run' or value['oracle_version'] != ORACLE
            or value['execution'] != 'supplied-observations-only' or value['deployment_authorized'] is not False
            or value['report_sha256'] != digest(unsigned)):
        raise ValueError('Invalid or changed regression report')
    identifier(value['revision']); identifier(value['suite_id'])
    for key in ('suite_sha256', 'observations_sha256', 'report_sha256'):
        if not isinstance(value[key], str) or not SHA.fullmatch(value[key]):
            raise ValueError('Invalid regression digest')
    if not isinstance(value['results'], list) or not 1 <= len(value['results']) <= 100:
        raise ValueError('Invalid regression results')
    ids = set()
    for result in value['results']:
        shape(result, ('case_id', 'case_version', 'verdict', 'evidence_ref'))
        identity = identifier(result['case_id'])
        if (identity in ids or not isinstance(result['verdict'], str) or result['verdict'] not in VERDICTS
                or type(result['case_version']) is not int or result['case_version'] < 1):
            raise ValueError('Invalid result case')
        ids.add(identity)
        if result['evidence_ref'] is not None: reference(result['evidence_ref'])
        if result['verdict'] == 'pass' and not result['evidence_ref']:
            raise ValueError('Passing result requires explicit evidence reference')
    return value


def compare(baseline, candidate):
    for report in (baseline, candidate): validated_report(report)
    for key in ('suite_sha256', 'suite_id', 'oracle_version'):
        if baseline[key] != candidate[key]:
            raise ValueError('Reports use different suite/oracle contracts; rerun both')
    before = {r['case_id']: r for r in baseline['results']}
    after = {r['case_id']: r for r in candidate['results']}
    if before.keys() != after.keys() or any(before[k]['case_version'] != after[k]['case_version'] for k in before):
        raise ValueError('Reports have different case sets/revisions')
    delta = [{'case_id': k, 'baseline': before[k]['verdict'], 'candidate': after[k]['verdict'],
              'change': 'regression' if before[k]['verdict'] == 'pass' and after[k]['verdict'] != 'pass'
              else 'improvement' if before[k]['verdict'] != 'pass' and after[k]['verdict'] == 'pass'
              else 'unchanged' if before[k]['verdict'] == after[k]['verdict']
              else 'changed-nonpassing'} for k in sorted(before)]
    blocked = any(r['candidate'] != 'pass' for r in delta)
    return {'schema_version': 1, 'kind': 'regression-comparison', 'suite_sha256': baseline['suite_sha256'],
            'baseline_revision': baseline['revision'], 'candidate_revision': candidate['revision'],
            'cases': delta, 'quality_gate': 'blocked' if blocked else 'review-required',
            'independent_review_required': True, 'deployment_authorized': False}


def write_report(path, value):
    """Publish private evidence atomically without replacing any existing file."""
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.regression-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(canonical(value) + b'\n'); target.flush(); os.fsync(target.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
