"""Read-only check of a delivered result: exact directory, files found there, their real SHA-256.

It looks at the local filesystem and reports. It never runs anything from the checked data, repairs or repeats
an action, uses a model or the network, and never copies file content into the report.
A pass means these bytes were at this path at this moment. It does not prove application health, remote state
or that the content is right, and it never authorizes a deployment. The final recheck narrows the window for a
replacement during the check; a change right after it stays possible, so a result is a point-in-time statement.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import time
import unicodedata

from . import __version__
from .regression import digest, identifier, shape

SCHEMA = 'agentops-postcheck-v1'
SPEC_SCHEMA = 'agentops-postcheck-spec-v1'
MAX_FILES = 1000
MAX_LISTED = 20000
MAX_BYTES = 64 * 1024 * 1024 * 1024
MAX_REPORTED_UNEXPECTED = 50
# Never opened, whatever a spec says. DANĚ stays excluded by Michal's decision of 2026-11-02.
DENIED_COMPONENTS = {'daně', 'dane', '.ssh', '.gnupg', '.aws'}
DENIED_NAMES = {'.credentials.json', 'auth.json', 'id_rsa', 'id_ed25519'}
PATH_FLAG = getattr(os, 'O_PATH', os.O_RDONLY)
NOT_VERIFIED = ('application_health', 'remote_state', 'content_meaning')


def _denied(parts):
    folded = [unicodedata.normalize('NFC', part).casefold() for part in parts]
    return any(part in DENIED_COMPONENTS for part in folded) or (bool(folded) and folded[-1] in DENIED_NAMES)


def _absolute(value):
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//') or '\0' in value \
            or value != os.path.normpath(value) or value == '/':
        raise ValueError('Postcheck paths must be exact absolute paths')
    if _denied(value.split('/')[1:]):
        raise ValueError('Postcheck refuses an excluded private location')
    return value


def _relative(value):
    parts = value.split('/') if isinstance(value, str) else []
    if not parts or '\0' in value or any(part in ('', '.', '..') for part in parts):
        raise ValueError('Postcheck file paths must stay inside the target directory')
    if _denied(parts):
        raise ValueError('Postcheck refuses an excluded private location')
    return value


def validate(spec):
    shape(spec, ('schema', 'target_dir', 'exact_directory', 'files', 'must_not_exist', 'unresolved_effects'))
    if spec['schema'] != SPEC_SCHEMA or type(spec['exact_directory']) is not bool:
        raise ValueError('Invalid postcheck specification')
    _absolute(spec['target_dir'])
    files = spec['files']
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError('Postcheck needs between 1 and %d files' % MAX_FILES)
    names = set()
    for row in files:
        shape(row, ('path', 'sha256'))
        sha = row['sha256']
        if not isinstance(sha, str) or len(sha) != 64 or set(sha) - set('0123456789abcdef'):
            raise ValueError('Postcheck needs a full lowercase SHA-256')
        if _relative(row['path']) in names:
            raise ValueError('Duplicate postcheck file')
        names.add(row['path'])
    if not isinstance(spec['must_not_exist'], list) or not isinstance(spec['unresolved_effects'], list):
        raise ValueError('Invalid postcheck specification')
    for value in spec['must_not_exist']:
        _absolute(value)
    for row in spec['unresolved_effects']:
        shape(row, ('id', 'note'))
        identifier(row['id'])
        if not isinstance(row['note'], str) or not row['note'].strip() or len(row['note']) > 500:
            raise ValueError('Unresolved effect needs a short note')
    return spec


def _open_target(path):
    """Walk from / without following a symlink: a link to another folder is not the agreed folder."""
    current = os.open('/', PATH_FLAG | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.split('/')[1:]:
            following = os.open(part, PATH_FLAG | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current)
            os.close(current)
            current = following
        return os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=current)
    finally:
        os.close(current)


def _target_problem(error):
    if error.errno == errno.ENOENT:
        return 'fail', 'target-missing'
    if error.errno in (errno.ELOOP, errno.ENOTDIR):
        return 'fail', 'target-not-exact-directory'
    return 'unverified', 'target-unreadable'


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _hash_file(descriptor, budget):
    before = os.fstat(descriptor)
    if before.st_size > budget:
        return None, before, 'too-large'
    digest_value = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest_value.update(chunk)
    after = os.fstat(descriptor)
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        return None, after, 'changed-during-read'
    return digest_value.hexdigest(), after, None


def _check_file(target, name, expected, budget):
    row = {'path': name, 'expected_sha256': expected, 'found_sha256': None, 'size': None}
    def result(verdict, status):
        return {**row, 'verdict': verdict, 'status': status}
    parent = os.dup(target)
    descriptor = None
    try:
        parts = name.split('/')
        try:
            for part in parts[:-1]:
                following = os.open(part, PATH_FLAG | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
                os.close(parent)
                parent = following
            # O_NONBLOCK: a FIFO in the target must not hang the check.
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        except OSError as error:
            if error.errno == errno.ENOENT:
                return result('fail', 'missing')
            if error.errno in (errno.ELOOP, errno.ENOTDIR):
                return result('fail', 'not-regular-file')
            return result('unverified', 'unreadable')
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return result('fail', 'not-regular-file')
            found, info, problem = _hash_file(descriptor, budget)
        except OSError:
            return result('unverified', 'unreadable')
        row.update(found_sha256=found, size=info.st_size)
        if problem:
            return result('unverified', problem)
        row['_identity'] = _identity(info)
        return result('pass', 'match') if found == expected else result('fail', 'content-mismatch')
    finally:
        os.close(parent)
        if descriptor is not None:
            os.close(descriptor)


def _file_identity(target, name):
    """What is at this path now, or None. Same no-symlink walk as the read."""
    parent = os.dup(target)
    try:
        parts = name.split('/')
        for part in parts[:-1]:
            following = os.open(part, PATH_FLAG | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            os.close(parent)
            parent = following
        return _identity(os.stat(parts[-1], dir_fd=parent, follow_symlinks=False))
    except OSError:
        return None
    finally:
        os.close(parent)


def _recheck(spec_dir, target_identity, files):
    """Last step: does the path still lead to the same folder and files? A replacement is never a pass."""
    try:
        current = _open_target(spec_dir)
    except OSError:
        current = None
    try:
        same_target = current is not None and _folder_identity(current) == target_identity
        changed = []
        for row in files:
            identity = row.pop('_identity', None)
            if identity is None:
                continue
            if not same_target or _file_identity(current, row['path']) != identity:
                row.update(found_sha256=None, size=None, verdict='unverified', status='replaced-during-check')
                changed.append(row['path'])
        return same_target, changed
    finally:
        if current is not None:
            os.close(current)


def _folder_identity(descriptor):
    info = os.fstat(descriptor)
    return info.st_dev, info.st_ino


def _directory_snapshot(target, names):
    """Capture every directory whose entries exact_directory relies on, including nested parents."""
    parents = {''} | {'/'.join(name.split('/')[:n]) for name in names
                     for n in range(1, name.count('/') + 1)}
    snapshot = {}
    listed = 0
    for name in sorted(parents):
        descriptor = os.dup(target)
        try:
            for part in name.split('/') if name else []:
                following = os.open(part, PATH_FLAG | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                    dir_fd=descriptor)
                os.close(descriptor)
                descriptor = following
            # Entry names matter even when the filesystem's timestamps have not advanced a tick.
            listing = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=descriptor)
            entries = []
            try:
                with os.scandir(listing) as iterator:
                    for entry in iterator:
                        listed += 1
                        if listed > MAX_LISTED:
                            return None
                        entries.append(entry.name)
            finally:
                os.close(listing)
            snapshot[name] = (_identity(os.fstat(descriptor)), tuple(sorted(entries)))
        except OSError:
            snapshot[name] = None
        finally:
            os.close(descriptor)
    return snapshot


def _directories_unchanged(path, expected):
    try:
        descriptor = _open_target(path)
    except OSError:
        return False
    try:
        # A marker file path recreates exactly the same parent-directory set; it is never opened.
        names = [name + '/__snapshot_marker__' if name else '__snapshot_marker__' for name in expected]
        return _directory_snapshot(descriptor, names) == expected
    finally:
        os.close(descriptor)


def _unexpected(target, names):
    """Entries the spec does not know. Only directories that lead to an expected file are entered."""
    expected = set(names)
    ancestors = {'/'.join(name.split('/')[:depth]) for name in names for depth in range(1, name.count('/') + 1)}
    found, listed = [], 0
    pending = [('', os.dup(target))]
    try:
        while pending:
            prefix, descriptor = pending.pop()
            try:
                entries = os.listdir(descriptor)
                listed += len(entries)
                if listed > MAX_LISTED:
                    raise OverflowError
                for entry in sorted(entries):
                    path = prefix + entry
                    if path in expected:
                        continue
                    if path in ancestors:
                        try:
                            pending.append((path + '/', os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                                                dir_fd=descriptor)))
                        except OSError as error:
                            if error.errno != errno.ENOENT and error.errno not in (errno.ELOOP, errno.ENOTDIR):
                                raise
                    else:
                        found.append(path)
            finally:
                os.close(descriptor)
    finally:
        for _, descriptor in pending:
            os.close(descriptor)
    return found


def _absence(path):
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return {'path': path, 'verdict': 'pass', 'status': 'absent'}
    except OSError:
        return {'path': path, 'verdict': 'unverified', 'status': 'unreadable'}
    return {'path': path, 'verdict': 'fail', 'status': 'exists'}


def _source_digest():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def run(spec, now=None):
    validate(spec)
    reasons = []
    def note(verdict, reason):
        reasons.append({'verdict': verdict, 'reason': reason})
    files, unexpected = [], {'checked': False, 'count': None, 'first': []}
    target_status = 'exact-directory'
    directory_snapshot = None
    try:
        target = _open_target(spec['target_dir'])
        target_identity = _folder_identity(target)
    except OSError as error:
        verdict, target_status = _target_problem(error)
        note(verdict, target_status)
        target = target_identity = None
    if target is None:
        files = [{'path': row['path'], 'expected_sha256': row['sha256'], 'found_sha256': None, 'size': None,
                  'verdict': 'not-checked', 'status': 'target-not-usable'} for row in spec['files']]
    else:
        try:
            if spec['exact_directory']:
                directory_snapshot = _directory_snapshot(target, [row['path'] for row in spec['files']])
            budget = MAX_BYTES
            for row in spec['files']:
                checked = _check_file(target, row['path'], row['sha256'], budget)
                budget -= checked['size'] or 0
                files.append(checked)
                if checked['verdict'] != 'pass':
                    note(checked['verdict'], 'file-%s' % checked['status'])
            if spec['exact_directory']:
                try:
                    found = _unexpected(target, [row['path'] for row in spec['files']])
                except (OSError, OverflowError):
                    note('unverified', 'directory-listing-incomplete')
                else:
                    unexpected = {'checked': True, 'count': len(found), 'first': found[:MAX_REPORTED_UNEXPECTED]}
                    if found:
                        note('fail', 'unexpected-entries')
        finally:
            os.close(target)
    absences = [_absence(path) for path in spec['must_not_exist']]
    for row in absences:
        if row['verdict'] != 'pass':
            note(row['verdict'], 'must-not-exist-%s' % row['status'])
    if target_identity is not None:
        same_target, changed = _recheck(spec['target_dir'], target_identity, files)
        if not same_target:
            target_status = 'replaced-during-check'
            note('unverified', 'target-replaced-during-check')
        for _ in changed:
            note('unverified', 'file-replaced-during-check')
    if directory_snapshot is not None and not _directories_unchanged(spec['target_dir'], directory_snapshot):
        note('unverified', 'directory-changed-during-check')
    if spec['unresolved_effects']:
        note('unverified', 'unresolved-effects')
    kinds = {row['verdict'] for row in reasons}
    verdict = 'fail' if 'fail' in kinds else 'unverified' if 'unverified' in kinds else 'pass'
    return {'schema': SCHEMA, 'verdict': verdict, 'reasons': reasons,
            'checked_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now)),
            'code': {'agentops_version': __version__, 'postcheck_sha256': _source_digest()},
            'spec_sha256': digest(spec), 'target_dir': spec['target_dir'], 'target_status': target_status,
            # Only files really found and read at the path; a wrong hash is readable, a missing file is not.
            'data_readable': all(row['found_sha256'] is not None for row in files),
            'files': files, 'unexpected_entries': unexpected, 'must_not_exist': absences,
            'unresolved_effects': spec['unresolved_effects'], 'not_verified': list(NOT_VERIFIED),
            'application_health': 'not_checked', 'actions_performed': [], 'repeat_action_allowed': False,
            'deployment_authorized': False, 'evaluated_by': 'rules, not a model'}


def _inside(path, folder):
    return path == folder or path.startswith(folder + '/')


def _spellings(path):
    """The path as written and with symlinks resolved, so an alias cannot hide where a write lands."""
    return {os.path.abspath(path), os.path.realpath(path)}


def effect_paths(output, ledger=None):
    """Everything `verify` itself writes: the report and, with a task, the ledger and its side files."""
    paths = [output]
    if ledger is not None:
        paths += [str(ledger) + suffix for suffix in ('', '-wal', '-shm', '-journal')]
    return paths


def refuse_own_effects(spec, paths):
    """Evidence must not change what it describes. Runs before the check and before any write."""
    validate(spec)
    watched = _spellings(spec['target_dir'])
    forbidden = set().union(*(_spellings(path) for path in spec['must_not_exist'])) if spec['must_not_exist'] else set()
    for path in paths:
        try:
            info = os.stat(path)
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENOTDIR):
                raise ValueError('Cannot inspect evidence or ledger location') from None
        else:
            # realpath does not reveal a hard-link alias of a checked file.
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise ValueError('Evidence and ledger files must not have hard links')
        for written in _spellings(path):
            if any(_inside(written, folder) for folder in watched):
                raise ValueError('Report and ledger must stay outside the checked target')
            # _inside also covers a forbidden folder that writing here would have to create as a parent.
            if any(_inside(written, folder) for folder in forbidden):
                raise ValueError('Report and ledger must not create a path the check forbids')


def report_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
