"""Versioned work packages: what one worker gets, instead of the whole team history (LC-04).

A package names the goal, the sources a worker really needs with their origin, the constraints, the financial
frame, who owns and who reviews it, the expected output and when it is finished. It runs nothing, assigns nothing and
holds no state: AgentOps stays the task authority, and a package is a file an offer can point to.

A missing or changed required source is reported as not ready, never smoothed over.
Limits: separating context is not an OS or credential boundary, because the worker can still read what its account can.
Bytes and characters are not billed tokens, and a coverage check is not an evaluation of a model.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .ledger import Ledger
from .postcheck import _denied
from .regression import canonical, digest, read_document

SCHEMA = 'agentops-work-package-v1'
DRAFT_SCHEMA = 'agentops-work-package-draft-v1'
MAX_PACKAGE_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 64
ID = re.compile(r'[a-z0-9][a-z0-9_.-]{0,79}\Z')
ACTOR = re.compile(r'[a-z][a-z0-9_-]{0,47}\Z')
TASK = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
ORIGINS = ('file', 'git', 'review', 'assignment', 'synthetic')
LOADS = ('required', 'on_demand')
# Out of bounds for packages whatever a draft says, on top of the private locations the result check refuses.
PACKAGE_DENIED = {'datahub-context-cache', 'data hub', 'datahub', 'onedrive'}
NOT_TOKENS = 'Bytes and characters as handed over; not billed tokens.'


class Problem(ValueError):
    pass


def _text(value, name, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise Problem('Invalid text in %s' % name)
    return value


def _shape(value, name, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or not set(required) <= set(value):
        raise Problem('Invalid fields in %s' % name)


def _items(value, name, least=0):
    if not isinstance(value, list) or not least <= len(value) <= MAX_ITEMS:
        raise Problem('%s needs between %d and %d items' % (name, least, MAX_ITEMS))
    return value


def _id(value, name):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Problem('Invalid identifier in %s' % name)
    return value


def _unique(rows, name):
    ids = [_id(row.get('id') if isinstance(row, dict) else None, name) for row in rows]
    if len(set(ids)) != len(ids):
        raise Problem('Duplicate id in %s' % name)


def _excluded(path):
    """Excluded by the name of any folder or the file. Works on one spelling of a path."""
    parts = str(path).split('/')[1:]
    return _denied(parts) or any(part.casefold() in PACKAGE_DENIED for part in parts)


def _out_of_scope(path):
    """The path as written and where it really leads, so a folder alias cannot reach an excluded place."""
    absolute = os.path.abspath(path)
    return _excluded(absolute) or _excluded(os.path.realpath(absolute))


def _path(value, name):
    if not isinstance(value, str) or not value.startswith('/') or value != os.path.normpath(value) or value.startswith('//') or '\0' in value:
        raise Problem('Source path must be exact and absolute in %s' % name)
    if _excluded(value):
        raise Problem('Source is in an excluded private location: %s' % name)
    return value


def _lines(value, name):
    if not (isinstance(value, list) and len(value) == 2 and all(type(n) is int for n in value) and 1 <= value[0] <= value[1]):
        raise Problem('Lines must be [first, last] in %s' % name)
    return value


def _common(package, schema, name):
    if package.get('schema') != schema:
        raise Problem('Unknown %s schema' % name)
    if type(package['version']) is not int or package['version'] < 1:
        raise Problem('Version must be a positive whole number')
    _id(package['package_id'], 'package_id')
    task = package['task']
    _shape(task, 'task', ('task_id',))
    if not isinstance(task['task_id'], str) or not TASK.fullmatch(task['task_id']):
        raise Problem('Task must be an AgentOps task id')
    _text(package['goal'], 'goal')
    roles = package['roles']
    _shape(roles, 'roles', ('owner', 'reviewer'))
    for role in roles.values():
        if not isinstance(role, str) or not ACTOR.fullmatch(role):
            raise Problem('Invalid role')
    if roles['owner'] == roles['reviewer']:
        raise Problem('Owner cannot review their own work')
    money = package['financial']
    _shape(money, 'financial', ('scope', 'new_paid_usage', 'on_limit'), ('approval_ref',))
    _text(money['scope'], 'financial.scope', 500)
    if type(money['new_paid_usage']) is not bool or money['on_limit'] != 'stop_and_report':
        raise Problem('Financial frame must state new paid usage and stop at a limit')
    if money['new_paid_usage']:
        _text(money.get('approval_ref'), 'financial.approval_ref', 500)
    rows = _items(package['constraints'], 'constraints', 1)
    _unique(rows, 'constraints')
    for row in rows:
        _shape(row, 'constraints', ('id', 'text'))
        _text(row['text'], 'constraints.text', 1000)
    output = package['output']
    _shape(output, 'output', ('description', 'location'), ('review_by_offer',))
    _text(output['description'], 'output.description', 1000)
    _text(output['location'], 'output.location', 500)
    rows = _items(package['done_when'], 'done_when', 1)
    _unique(rows, 'done_when')
    for row in rows:
        _shape(row, 'done_when', ('id', 'text', 'evidence'))
        _text(row['text'], 'done_when.text', 1000)
        _text(row['evidence'], 'done_when.evidence', 500)
    rows = _items(package['out_of_scope'], 'out_of_scope')
    for row in rows:
        _text(row, 'out_of_scope', 500)
    rows = _items(package['open_questions'], 'open_questions')
    _unique(rows, 'open_questions')
    for row in rows:
        _shape(row, 'open_questions', ('id', 'question', 'blocking'))
        _text(row['question'], 'open_questions.question', 500)
        if type(row['blocking']) is not bool:
            raise Problem('open_questions.blocking must be true or false')
    if type(package['budget_bytes']) is not int or package['budget_bytes'] < 1:
        raise Problem('Budget must be a positive whole number')


FIELDS = ('schema', 'package_id', 'version', 'task', 'goal', 'roles', 'financial', 'constraints', 'sources', 'output', 'done_when',
          'out_of_scope', 'open_questions', 'budget_bytes')


def validate(package):
    """Format only. Whether the sources are really there is `check`."""
    _shape(package, 'package', FIELDS)
    _common(package, SCHEMA, 'package')
    rows = _items(package['sources'], 'sources')
    _unique(rows, 'sources')
    if not any(row.get('load') == 'required' for row in rows if isinstance(row, dict)) and not package['open_questions']:
        raise Problem('A package without a required source must say why in open_questions')
    for row in rows:
        _shape(row, 'sources', ('id', 'path', 'origin', 'why', 'load', 'sha256', 'bytes'), ('lines', 'missing_reason'))
        _path(row['path'], 'sources.path')
        _origin(row['origin'])
        _text(row['why'], 'sources.why', 500)
        if row['load'] not in LOADS:
            raise Problem('Source load must be required or on_demand')
        if 'lines' in row:
            _lines(row['lines'], 'sources.lines')
        missing = row['sha256'] is None
        if missing:
            _text(row.get('missing_reason'), 'sources.missing_reason', 500)
            if row['bytes'] is not None:
                raise Problem('A missing source has no size')
        else:
            if not isinstance(row['sha256'], str) or not SHA.fullmatch(row['sha256']) or 'missing_reason' in row:
                raise Problem('A source needs a full SHA-256 or a missing_reason')
            if type(row['bytes']) is not int or row['bytes'] < 0:
                raise Problem('A source needs its size')
    if len(canonical(package)) > MAX_PACKAGE_BYTES:
        raise Problem('Package is too large to be a summary; select less')
    return package


def _origin(origin):
    _shape(origin, 'origin', ('kind', 'ref'))
    if origin['kind'] not in ORIGINS:
        raise Problem('Unknown origin kind')
    _text(origin['ref'], 'origin.ref', 300)


def _read_source(path, lines):
    """The selected bytes, or a reason. Every read passes here, and nothing excluded is opened.

    It reads one named file, never a folder, a symlink as the file itself, or a device. This guards against
    mistakes: a link swapped between this check and the open is not covered.
    """
    if _out_of_scope(path):
        return None, 'source_excluded'
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None, 'source_missing'
    except OSError:
        return None, 'source_unreadable'
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return None, 'source_not_a_file'
        if info.st_size > MAX_SOURCE_BYTES:
            return None, 'source_too_large'
        data = b''
        while len(data) <= MAX_SOURCE_BYTES:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data += chunk
    except OSError:
        return None, 'source_unreadable'
    finally:
        os.close(descriptor)
    if lines:
        selected = data.splitlines(keepends=True)
        if lines[1] > len(selected):
            return None, 'source_lines_out_of_range'
        data = b''.join(selected[lines[0] - 1:lines[1]])
    return data, None


def build(draft, base=None):
    """Turn a draft into a package with real hashes and sizes. What cannot be read stays visibly missing."""
    _shape(draft, 'draft', [field for field in FIELDS if field != 'schema'] + ['schema'])
    _common(draft, DRAFT_SCHEMA, 'draft')
    rows = _items(draft['sources'], 'sources')
    _unique(rows, 'sources')
    sources = []
    for row in rows:
        _shape(row, 'sources', ('id', 'path', 'origin', 'why', 'load'), ('lines',))
        raw = row['path']
        if isinstance(raw, str) and not raw.startswith('/') and base is not None:
            raw = os.path.normpath(os.path.join(os.path.abspath(base), raw))
        path = _path(raw, 'sources.path')
        if 'lines' in row:
            _lines(row['lines'], 'sources.lines')
        data, reason = _read_source(path, row.get('lines'))
        entry = {**row, 'path': path}
        if reason:
            entry.update(sha256=None, bytes=None, missing_reason=reason)
        else:
            entry.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        sources.append(entry)
    return validate({**draft, 'schema': SCHEMA, 'sources': sources})


def check(package, ledger_path=None):
    """Is it usable now? Rereads every source. Reports problems by code, and never repairs anything."""
    validate(package)
    problems = []
    required = on_demand = 0
    for row in package['sources']:
        strict = row['load'] == 'required'
        data, reason = _read_source(row['path'], row.get('lines'))
        if row['sha256'] is None or reason:
            problems.append({'code': reason or row['missing_reason'], 'source': row['id'], 'blocking': strict})
            continue
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            problems.append({'code': 'source_changed', 'source': row['id'], 'blocking': strict})
            continue
        if strict:
            required += len(data)
        else:
            on_demand += len(data)
    for row in package['open_questions']:
        if row['blocking']:
            problems.append({'code': 'open_question', 'question': row['id'], 'blocking': True})
    if package['financial']['new_paid_usage']:
        problems.append({'code': 'paid_usage_needs_person', 'blocking': True})
    package_bytes = len(canonical(package))
    if package_bytes + required > package['budget_bytes']:
        problems.append({'code': 'over_budget', 'blocking': True})
    task_state = None
    if ledger_path is not None:
        task_state, more = _task_state(package, ledger_path)
        problems += more
    return {'ready': not any(row['blocking'] for row in problems), 'package_sha256': digest(package), 'problems': problems,
            'task': task_state, 'volume': {'package_bytes': package_bytes, 'required_source_bytes': required,
                                           'on_demand_source_bytes': on_demand, 'handed_over_bytes': package_bytes + required,
                                           'budget_bytes': package['budget_bytes'], 'unit_note': NOT_TOKENS}}


def _task_state(package, ledger_path):
    """Read-only comparison with the authority. A package does not change or assign anything."""
    if not Path(ledger_path).expanduser().is_file():
        return None, [{'code': 'ledger_unavailable', 'blocking': True}]
    ledger = Ledger(ledger_path)
    try:
        task = ledger.get(package['task']['task_id'])
    except (ValueError, KeyError):
        return None, [{'code': 'task_not_found', 'blocking': True}]
    finally:
        ledger.close()
    problems = []
    if task['state'] in ('done', 'cancelled'):
        problems.append({'code': 'task_closed', 'blocking': True})
    if package['roles']['owner'] not in (task['owner'], task['target']):
        problems.append({'code': 'owner_differs_from_task', 'blocking': True})
    return {'state': task['state'], 'owner': task['owner'], 'target': task['target'], 'version': task['version']}, problems


def render(package):
    """The text a worker gets. On-demand sources are named, not included, so they are read only when needed."""
    validate(package)
    lines = ['# Pracovní balíček %s v%d' % (package['package_id'], package['version']),
             'Hash: %s' % digest(package), 'Úkol AgentOps: %s' % package['task']['task_id'],
             'Vlastník: %s. Kontrola: %s. Vlastní výsledek si neschvaluješ.' % (package['roles']['owner'], package['roles']['reviewer']),
             '', '## Cíl', package['goal'], '', '## Omezení']
    lines += ['- %s' % row['text'] for row in package['constraints']]
    money = package['financial']
    lines += ['', '## Finanční rámec', money['scope'],
              'Nové placené využití: %s. Při limitu: zastav a ozvi se.' % ('ano, ' + money['approval_ref'] if money['new_paid_usage'] else 'ne')]
    if package['out_of_scope']:
        lines += ['', '## Mimo rozsah'] + ['- %s' % row for row in package['out_of_scope']]
    for load, title in (('required', '## Povinné zdroje'), ('on_demand', '## Zdroje na vyžádání (čti jen když je potřebuješ)')):
        rows = [row for row in package['sources'] if row['load'] == load]
        if rows:
            lines += ['', title]
        for row in rows:
            where = row['path'] + (' řádky %d-%d' % tuple(row['lines']) if 'lines' in row else '')
            state = 'CHYBÍ: ' + row['missing_reason'] if row['sha256'] is None else 'sha256 ' + row['sha256'][:16]
            lines.append('- %s: %s (%s). %s. Původ: %s %s.' % (row['id'], where, state, row['why'], row['origin']['kind'], row['origin']['ref']))
    if package['open_questions']:
        lines += ['', '## Otevřené otázky']
        lines += ['- %s%s' % ('BLOKUJE: ' if row['blocking'] else '', row['question']) for row in package['open_questions']]
    lines += ['', '## Výstup', package['output']['description'], 'Kam: %s' % package['output']['location'], '', '## Hotovo, když']
    lines += ['- %s Důkaz: %s' % (row['text'], row['evidence']) for row in package['done_when']]
    return '\n'.join(lines) + '\n'


def _measure(paths):
    total = {'files': 0, 'bytes': 0, 'characters': 0, 'lines': 0, 'unreadable': [], 'excluded': 0}
    texts = []
    for path in paths:
        data, reason = _read_source(str(path), None)
        if reason == 'source_excluded':
            total['excluded'] += 1  # counted, and not named: the name itself may be what should stay out
            continue
        if reason:
            total['unreadable'].append(str(path))
            continue
        text = data.decode('utf-8', 'replace')
        texts.append(text)
        total['files'] += 1
        total['bytes'] += len(data)
        total['characters'] += len(text)
        total['lines'] += text.count('\n')
    return total, '\n'.join(texts)


def compare(package, broad_paths, facts=None):
    """Same assignment, two handovers: volume, and which listed facts each one really contains. No model is run."""
    validate(package)
    rendered = render(package)
    texts = [rendered]
    measured = {'files': 1, 'bytes': len(rendered.encode()), 'characters': len(rendered), 'lines': rendered.count('\n'), 'unreadable': []}
    for row in package['sources']:
        if row['load'] != 'required':
            continue
        data, reason = _read_source(row['path'], row.get('lines'))
        if reason:
            measured['unreadable'].append(row['id'])
            continue
        text = data.decode('utf-8', 'replace')
        texts.append(text)
        measured['files'] += 1
        measured['bytes'] += len(data)
        measured['characters'] += len(text)
        measured['lines'] += text.count('\n')
    package_text = '\n'.join(texts)
    broad, broad_text = _measure(broad_paths)
    result = {'package_sha256': digest(package), 'package': measured, 'broad': broad,
              'ratio_bytes': round(measured['bytes'] / broad['bytes'], 4) if broad['bytes'] else None,
              'not_measured': ['billed tokens', 'model quality', 'time', 'cost of writing the package'], 'unit_note': NOT_TOKENS}
    if facts is not None:
        if not isinstance(facts, list) or not all(isinstance(row, dict) and set(row) == {'id', 'text'} for row in facts):
            raise Problem('Facts must be a list of id and text')
        result['facts'] = {'total': len(facts),
                           'missing_from_package': [row['id'] for row in facts if row['text'] not in package_text],
                           'missing_from_broad': [row['id'] for row in facts if row['text'] not in broad_text],
                           'note': 'Literal presence of facts chosen in advance by the author; presence is not understanding.'}
    return result


def load_package(path):
    return read_document(path)


def package_json(package):
    return json.dumps(package, ensure_ascii=False, indent=2) + '\n'
