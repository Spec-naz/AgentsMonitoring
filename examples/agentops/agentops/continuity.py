"""Portable, opt-in backup of explicitly reviewed roots; never replaces live files.

inventory(roots, ledger_path=None) inspects names/stat only (no content reads).
export_backup(..., destination=..., dry_run=False) writes a private ZIP plus manifest.
restore_backup(archive, staging, expected_sha256=None) rehearses into EMPTY staging.
Secrets opt-ins deliberately fail closed: an independently verified encrypted identity
bundle (age recipients/key recovery) is a separate future operation, never plaintext here.
Filename exclusions are a guardrail, NOT proof that arbitrary files contain no secrets.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import tempfile
import time
import unicodedata
from urllib.parse import quote
import zipfile

SCHEMA = 1
MAX_FILES = 10000
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_SENSITIVE = re.compile(r"(?i)(auth|credential|token|secret|password|passwd|api[-_]?key|identity|config|cookies|session)")
_PRIVATE_DIRS = {'.ssh', '.aws', '.azure', '.kube', '.gnupg', '.config', '.git', '.claude', '.codex', '.docker'}
_PRIVATE_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.jks', '.keystore'}
_DATABASE_SUFFIXES = {'.db', '.sqlite', '.sqlite3'}
_RUNTIME_DIRS = {'__pycache__', '.cache', '.venv', 'venv', 'node_modules', '.pytest_cache'}
LIMITS = [
    'Only explicitly configured, operator-reviewed roots are inspected; HOME is never auto-discovered.',
    'Filename exclusions cannot prove that selected file contents contain no secrets. Review roots before export.',
    'Secrets/auth identities are omitted; encrypted identity/key recovery is not implemented.',
    'Regular files are copied independently, not an application-wide point-in-time filesystem snapshot.',
    'Only a configured SQLite ledger uses an online consistent backup; other databases are excluded.',
    'Restored files/directories are owner-only; production permissions and executable/service setup require separate review.',
    'Hashes detect corruption against the manifest, not authenticity; trust a separately recorded archive SHA256.',
    'Restore rehearsal proves archive/file hashes and SQLite integrity only, not service startup, OAuth validity, network reachability, or migration readiness.',
]


class ContinuityError(ValueError):
    """Safe, content-free failure; never includes a source file's contents."""


def _private_guard(include_secrets):
    if include_secrets:
        raise ContinuityError('Secret export is disabled: a verified established encryption/identity-bundle workflow is required.')


def _source(path):
    path = Path(path).expanduser().absolute()
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return path, None
    if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
        raise ContinuityError('Source roots must be ordinary directories or regular files, never symlinks or devices.')
    if path.resolve() in (Path.home().resolve(), Path(path.anchor)):
        raise ContinuityError('Choose reviewed subdirectories or files, never a whole HOME or filesystem root.')
    return path, mode


def _within(path, root):
    return path == root or root in path.parents


def _reason(relative):
    parts = PurePosixPath(relative).parts
    for part in parts:
        lower = part.lower()
        if lower in _RUNTIME_DIRS:
            return 'rebuildable runtime/cache excluded'
        if lower in _PRIVATE_DIRS or lower == '.env' or lower.startswith('.env.') or lower.startswith('id_'):
            return 'credential/auth path excluded'
        if _SENSITIVE.search(part) or Path(part).suffix.lower() in _PRIVATE_SUFFIXES:
            return 'possible credential/configuration excluded'
    name = parts[-1].lower()
    if Path(name).suffix in _DATABASE_SUFFIXES or name.endswith(('-wal', '-shm', '-journal')):
        return 'database requires explicit SQLite ledger configuration'
    return None


def _archive_name(name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ContinuityError('Unsafe archive path.')
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')) or str(p) != name:
        raise ContinuityError('Unsafe archive path.')
    for part in p.parts:
        stem = part.split('.')[0].upper()
        if part.endswith((' ', '.')) or stem in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', stem):
            raise ContinuityError('Archive contains a nonportable path.')
    return name


def inventory(roots, ledger_path=None, *, include_secrets=False):
    """Return selected/excluded/missing paths and limitations, without reading content.

    roots maps stable archive labels to explicit reviewed directories or files. Missing
    roots are reported. Symlink/special roots fail; nested symlinks/devices are excluded.
    """
    _private_guard(include_secrets)
    if not isinstance(roots, dict) or any(not isinstance(k, str) or not _LABEL.fullmatch(k) for k in roots):
        raise ContinuityError('Roots must map safe labels to explicit paths.')
    if ledger_path is not None and 'ledger' in roots:
        raise ContinuityError('The ledger label is reserved when a SQLite ledger is configured.')
    report = {'schema': SCHEMA, 'selected': [], 'excluded': [], 'missing': [], 'limits': list(LIMITS)}
    ledger, ledger_mode = _source(ledger_path) if ledger_path is not None else (None, None)
    if ledger_mode is not None and not stat.S_ISREG(ledger_mode):
        raise ContinuityError('The SQLite ledger must be a regular file.')
    ledger_related = {str(ledger) + suffix for suffix in ('', '-wal', '-shm', '-journal')} if ledger else set()
    total = 0
    for label, raw in roots.items():
        root, mode = _source(raw)
        if mode is None:
            report['missing'].append({'label': label, 'source': str(root)})
            continue
        root = root.resolve()
        candidates = []
        if stat.S_ISREG(mode):
            candidates = [(root, root.name)]
        else:
            def walk_error(_error):
                raise ContinuityError('A configured root cannot be fully inspected.')
            for directory, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
                for child in list(dirs):
                    path = Path(directory) / child
                    rel = path.relative_to(root).as_posix()
                    reason = 'symlink excluded' if path.is_symlink() else _reason(rel)
                    if reason:
                        dirs.remove(child)
                        report['excluded'].append({'path': f'files/{label}/{rel}', 'reason': reason})
                candidates.extend((Path(directory) / child, (Path(directory) / child).relative_to(root).as_posix()) for child in files)
        for path, relative in sorted(candidates):
            archive_path = _archive_name(f'files/{label}/{relative}')
            info = path.lstat()
            reason = ('configured ledger copied through SQLite backup' if str(path) in ledger_related else
                      'symlink or special file excluded' if not stat.S_ISREG(info.st_mode) else _reason(relative))
            if reason:
                report['excluded'].append({'path': archive_path, 'reason': reason})
                continue
            total += info.st_size
            if info.st_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(report['selected']) >= MAX_FILES:
                raise ContinuityError('Backup selection exceeds configured safety limits.')
            report['selected'].append({'path': archive_path, 'source': str(path), 'root': str(root),
                                       'size': info.st_size, 'mode': stat.S_IMODE(info.st_mode)})
    if ledger is not None:
        if ledger_mode is None:
            report['missing'].append({'label': 'ledger', 'source': str(ledger)})
        else:
            if ledger.stat().st_size > MAX_FILE_BYTES or total + ledger.stat().st_size > MAX_TOTAL_BYTES or len(report['selected']) >= MAX_FILES:
                raise ContinuityError('SQLite ledger exceeds the safety limit.')
            report['selected'].append({'path': 'files/ledger/ledger.sqlite3', 'source': str(ledger),
                                       'root': str(ledger), 'size': ledger.stat().st_size, 'mode': 0o600, 'sqlite': True})
    portable = [unicodedata.normalize('NFC', e['path']).casefold() for e in report['selected']]
    if len(portable) != len(set(portable)):
        raise ContinuityError('Selection has nonportable case-colliding paths.')
    return report


def _sha(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_open_source(entry):
    # Reject changed/symlink ancestors as well as a swapped leaf. Reviewed roots are
    # not a sandbox against a concurrent malicious process running as this user.
    source, root = Path(entry['source']), Path(entry['root'])
    current = source
    while True:
        if current.is_symlink():
            raise ContinuityError('Source changed to a symlink during export.')
        if current == root:
            break
        current = current.parent
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise ContinuityError('Source changed to a nonregular file.')
    return os.fdopen(fd, 'rb')


def _sqlite_copy(source, target):
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ContinuityError('SQLite ledger must remain a regular file.')
    try:
        deadline = time.monotonic() + 30
        def progress(_status, _remaining, _total):
            if time.monotonic() > deadline:
                raise ContinuityError('SQLite online backup exceeded its deadline.')
        with closing(sqlite3.connect('file:' + quote(str(source)) + '?mode=ro', uri=True, timeout=5)) as live:
            with closing(sqlite3.connect(target)) as copy:
                live.backup(copy, pages=256, progress=progress, sleep=0.01)
                if copy.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                    raise ContinuityError('SQLite backup integrity check failed.')
    except sqlite3.Error:
        raise ContinuityError('SQLite backup could not be verified.') from None


def export_backup(roots, destination=None, ledger_path=None, *, dry_run=True, include_secrets=False):
    """Default is metadata-only inventory. Actual export creates a NEW private ZIP.

    destination must not exist and must be outside every source root and ledger path.
    Returns evidence including per-file hashes and separately recordable archive_sha256.
    """
    plan = inventory(roots, ledger_path, include_secrets=include_secrets)
    if dry_run:
        return {**plan, 'dry_run': True}
    if destination is None:
        raise ContinuityError('An explicit backup destination is required.')
    destination = Path(destination).expanduser().absolute()
    for raw in list(roots.values()) + ([ledger_path] if ledger_path is not None else []):
        root = Path(raw).expanduser().resolve()
        if _within(destination.resolve(), root):
            raise ContinuityError('Backup destination must be outside configured source roots.')
    if destination.exists() or destination.is_symlink():
        raise ContinuityError('Backup destination already exists.')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.agentops-backup-', dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    published = False
    manifest = {'schema': SCHEMA, 'files': [], 'excluded': plan['excluded'], 'missing': plan['missing'],
                'limits': list(LIMITS), 'includes_secrets': False}
    try:
        with tempfile.TemporaryDirectory(prefix='agentops-export-') as temp:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                total = 0
                for entry in plan['selected']:
                    digest = hashlib.sha256()
                    info = zipfile.ZipInfo(entry['path'])
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o600) << 16
                    if entry.get('sqlite'):
                        copied = Path(temp) / 'ledger.sqlite3'
                        _sqlite_copy(entry['source'], copied)
                        stream = open(copied, 'rb')
                    else:
                        stream = _safe_open_source(entry)
                    size = 0
                    with stream, archive.open(info, 'w') as output:
                        before = os.fstat(stream.fileno())
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            size += len(chunk)
                            total += len(chunk)
                            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                                raise ContinuityError('Export exceeds safety limits.')
                            digest.update(chunk)
                            output.write(chunk)
                        after = os.fstat(stream.fileno())
                        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                            raise ContinuityError('A selected source file changed during export; retry when idle.')
                    manifest['files'].append({'path': entry['path'], 'size': size, 'sha256': digest.hexdigest(),
                                              'mode': entry['mode'], 'sqlite': bool(entry.get('sqlite'))})
                encoded = json.dumps(manifest, ensure_ascii=True, sort_keys=True).encode()
                if len(encoded) > MAX_MANIFEST_BYTES:
                    raise ContinuityError('Backup manifest exceeds safety limits.')
                archive.writestr('manifest.json', encoded)
        digest = _sha(temporary)
        with open(temporary, 'rb') as complete:
            os.fsync(complete.fileno())
        # Publish a complete archive atomically; a concurrently created destination wins.
        os.link(temporary, destination)
        published = True
        return {**plan, 'dry_run': False, 'archive': str(destination), 'archive_sha256': digest,
                'files': manifest['files']}
    except BaseException:
        if published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)


def _validated_manifest(archive):
    infos = archive.infolist()
    if len(infos) > MAX_FILES + 1:
        raise ContinuityError('Archive has too many entries.')
    canonical = set()
    members = {}
    for info in infos:
        name = _archive_name(info.filename)
        portable = unicodedata.normalize('NFC', name).casefold()
        if portable in canonical:
            raise ContinuityError('Archive has duplicate or case-colliding paths.')
        canonical.add(portable)
        mode = info.external_attr >> 16
        if info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.flag_bits & 1:
            raise ContinuityError('Archive members must be unencrypted regular files, never links/devices/directories.')
        if info.file_size > MAX_FILE_BYTES or info.file_size < 0:
            raise ContinuityError('Archive member exceeds safety limits.')
        members[name] = info
    if 'manifest.json' not in members or members['manifest.json'].file_size > MAX_MANIFEST_BYTES:
        raise ContinuityError('Archive manifest is missing or oversized.')
    try:
        manifest = json.loads(archive.read('manifest.json'))
    except (ValueError, UnicodeError):
        raise ContinuityError('Invalid archive manifest.') from None
    if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or manifest.get('includes_secrets') is not False:
        raise ContinuityError('Unsupported archive schema or secret-bearing archive.')
    files = manifest.get('files')
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ContinuityError('Invalid archive file manifest.')
    expected = set()
    total = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise ContinuityError('Invalid file record.')
        name = _archive_name(entry.get('path'))
        if not name.startswith('files/') or len(PurePosixPath(name).parts) < 3 or name in expected:
            raise ContinuityError('Invalid or duplicate manifest path.')
        if name not in members or type(entry.get('size')) is not int or entry['size'] != members[name].file_size:
            raise ContinuityError('Manifest file size does not match archive.')
        if not isinstance(entry.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']):
            raise ContinuityError('Invalid file hash.')
        if type(entry.get('mode')) is not int or entry['mode'] < 0 or entry['mode'] > 0o7777 or type(entry.get('sqlite')) is not bool:
            raise ContinuityError('Invalid file mode/type.')
        total += entry['size']
        expected.add(name)
    portable_names = {unicodedata.normalize('NFC', n).casefold() for n in expected}
    for name in portable_names:
        if any(str(parent) in portable_names for parent in PurePosixPath(name).parents if str(parent) != '.'):
            raise ContinuityError('Archive has a file/directory path collision.')
    if expected | {'manifest.json'} != set(members) or total > MAX_TOTAL_BYTES:
        raise ContinuityError('Unlisted entries or oversized archive.')
    # Verify every member before the first staging write. The manifest is not a signature.
    for entry in files:
        digest = hashlib.sha256()
        with archive.open(entry['path']) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != entry['sha256']:
            raise ContinuityError('Archive content hash mismatch.')
    return manifest


def _stage_parent(root_fd, parts, created_dirs=None):
    current = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            if created_dirs is not None:
                try:
                    os.mkdir(part, 0o700, dir_fd=current)
                except FileExistsError:
                    pass
                else:
                    created_dirs.append(parts[:index + 1])
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


def restore_backup(archive_path, staging, *, expected_sha256=None):
    """Validate then restore into an EXISTING EMPTY staging directory only.

    Refuses links/devices, traversal, duplicate/case-colliding paths, undeclared files,
    tampered hashes and SQLite corruption. Never starts services or replaces live files.
    Directory-fd/no-follow support is required for safe writes (POSIX); unsupported
    platforms fail closed. Rehearsal archives remain portable ZIP/JSON/SQLite files.
    """
    archive_path = Path(archive_path).expanduser().absolute()
    staging = Path(staging).expanduser().absolute()
    if staging.is_symlink() or not staging.is_dir() or any(staging.iterdir()):
        raise ContinuityError('Restore target must be an existing EMPTY ordinary staging directory.')
    staging = staging.resolve()
    if archive_path.is_symlink() or not archive_path.is_file() or _within(archive_path.resolve(), staging):
        raise ContinuityError('Archive must be an ordinary file outside staging.')
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY') or os.open not in os.supports_dir_fd:
        raise ContinuityError('Safe isolated restore requires directory-fd/no-follow filesystem support.')
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256):
            raise ContinuityError('Invalid expected archive hash.')
        if _sha(archive_path) != expected_sha256:
            raise ContinuityError('Archive SHA256 does not match separately recorded evidence.')
    created_files, created_dirs = [], []
    root_fd = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with zipfile.ZipFile(archive_path, 'r') as archive:
            manifest = _validated_manifest(archive)
            for entry in manifest['files']:
                parts = PurePosixPath(entry['path']).parts
                parent_fd = _stage_parent(root_fd, parts[:-1], created_dirs)
                try:
                    fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
                except BaseException:
                    os.close(parent_fd)
                    raise
                os.close(parent_fd)
                created_files.append(parts)
                digest = hashlib.sha256()
                with os.fdopen(fd, 'wb') as output, archive.open(entry['path']) as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b''):
                        digest.update(chunk)
                        output.write(chunk)
                    os.fchmod(output.fileno(), 0o700 if entry['mode'] & 0o100 else 0o600)
                if digest.hexdigest() != entry['sha256']:
                    raise ContinuityError('Archive changed during restore.')
                if entry['sqlite']:
                    target = staging.joinpath(*parts)
                    try:
                        with closing(sqlite3.connect('file:' + quote(str(target)) + '?mode=ro', uri=True)) as db:
                            if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
                                raise ContinuityError('Restored SQLite integrity check failed.')
                    except sqlite3.Error:
                        raise ContinuityError('Restored SQLite file is invalid.') from None
        return {'schema': SCHEMA, 'staging': str(staging), 'restored': manifest['files'],
                'excluded': manifest.get('excluded', []), 'missing': manifest.get('missing', []),
                'sqlite_integrity': 'passed' if any(e['sqlite'] for e in manifest['files']) else 'not configured',
                'expected_archive_hash_verified': expected_sha256 is not None, 'limits': list(LIMITS)}
    except BaseException:
        # Only remove entries created by this rehearsal, using held parent descriptors.
        for parts, is_dir in [(p, False) for p in reversed(created_files)] + [(p, True) for p in reversed(created_dirs)]:
            try:
                parent_fd = _stage_parent(root_fd, parts[:-1])
            except OSError:
                continue
            try:
                if is_dir:
                    os.rmdir(parts[-1], dir_fd=parent_fd)
                else:
                    os.unlink(parts[-1], dir_fd=parent_fd)
            except OSError:
                pass
            finally:
                os.close(parent_fd)
        raise
    finally:
        os.close(root_fd)
