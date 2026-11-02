"""Explicit age wrapper for data-only continuity exports and isolated rehearsals.

No cryptographic implementation, key generation, implicit recipient, uploads or service
activation. Secret-bearing exports remain disabled. Plaintext exists briefly in an
owner-only temporary directory; cleanup is not a secure-erasure or SIGKILL guarantee.
An operator must independently verify the age executable and keep recovery keys off-host.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile

from . import continuity

MAX_ENVELOPE_BYTES = continuity.MAX_TOTAL_BYTES + continuity.MAX_MANIFEST_BYTES + 16 * 1024 * 1024
AGE_HEADER = b'age-encryption.org/v1\n'
LIMITS = [
    'Only reviewed data roots and the configured SQLite ledger are exported; secret/auth opt-ins remain disabled.',
    'age version is reported, not proof of binary authenticity; independently verify the selected executable.',
    'Recipient encryption does not authenticate the sender; compare a separately recorded ciphertext SHA256.',
    'Plaintext temporarily exists in private local storage. Normal cleanup is not secure erasure or a crash/power-loss guarantee.',
    'No off-host copy, provider upload, production identity migration, service activation or cross-host failover is performed.',
]


class EncryptedError(continuity.ContinuityError):
    """Content-free wrapper failure. External stderr/key material is never reported."""


def _tool(age_executable=None):
    selected = str(age_executable) if age_executable is not None else shutil.which('age')
    if not selected:
        raise EncryptedError('age is unavailable; supply an independently verified age executable.')
    try:
        path = Path(selected).expanduser().absolute().resolve()
    except (OSError, RuntimeError):
        raise EncryptedError('age executable path is invalid or unsupported.') from None
    if not path.is_file() or not os.access(path, os.X_OK):
        raise EncryptedError('age executable is unavailable or not executable.')
    env = {'PATH': os.pathsep.join((str(path.parent), os.defpath)), 'LANG': 'C', 'LC_ALL': 'C'}
    try:
        version = subprocess.run([str(path), '--version'], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=5, check=False, env=env, start_new_session=True)
    except (OSError, subprocess.SubprocessError):
        raise EncryptedError('age capability check failed; no backup was attempted.') from None
    text = version.stdout.decode('ascii', errors='replace').strip()
    if version.returncode or not re.fullmatch(r'v?[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', text):
        raise EncryptedError('Unsupported age version output; no backup was attempted.')
    return path, text, env


def capabilities(age_executable=None):
    """Check only the selected binary/version; read no identities or source content."""
    try:
        path, version, _env = _tool(age_executable)
        return {'available': True, 'tool': 'age', 'executable': str(path), 'version': version,
                'secrets_supported': False, 'binary_authenticity_verified': False}
    except EncryptedError:
        return {'available': False, 'tool': 'age', 'secrets_supported': False,
                'reason': 'Established age tool unavailable or unsupported; no encryption attempted.'}


def _recipient(value):
    # Shape guard only: the established tool validates the native key/checksum.
    # Plugin/SSH/passphrase recipients are intentionally outside this small adapter.
    if not isinstance(value, str) or not re.fullmatch(r'age1[0-9a-z]{50,80}', value):
        raise EncryptedError('An explicit native age public recipient is required.')
    return value


def _regular(path, *, private=False):
    path = Path(path).expanduser().absolute()
    try:
        info = path.lstat()
    except OSError:
        raise EncryptedError('A required regular file is unavailable.') from None
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise EncryptedError('Required files must be ordinary regular files, never symlinks or devices.')
    if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise EncryptedError('Identity file must be owner-only and owned by the current user.')
    return path


def _private_stage(staging):
    path = Path(staging).expanduser().absolute()
    try:
        info = path.lstat()
    except OSError:
        raise EncryptedError('Rehearsal requires an existing empty private staging directory.') from None
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700 or any(path.iterdir()):
        raise EncryptedError('Rehearsal requires an existing empty owned directory with mode 0700, never a symlink.')
    return path.resolve()


def _digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _run(path, env, arguments, timeout):
    if type(timeout) is not int or not 1 <= timeout <= 900:
        raise EncryptedError('age timeout must be between 1 and 900 seconds.')
    try:
        result = subprocess.run([str(path), *map(str, arguments)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=timeout, check=False, env=env, start_new_session=True)
    except (OSError, subprocess.SubprocessError):
        raise EncryptedError('age operation failed or timed out; external diagnostics are intentionally hidden.') from None
    if result.returncode:
        raise EncryptedError('age rejected the operation or could not authenticate/decrypt the input; no external diagnostics are disclosed.')


def _publish(source, destination):
    _regular(source, private=True)
    with open(source, 'rb') as completed:
        os.fsync(completed.fileno())
    os.link(source, destination)  # Atomic and EXCL-like: never overwrite another file.


def export_encrypted(roots, destination, recipient, ledger_path=None, *, age_executable=None,
                     dry_run=True, include_secrets=False, timeout=300):
    """Inventory by default; encrypt a NEW continuity ZIP only when dry_run=False.

    recipient is an explicit public native age recipient. destination must be outside
    configured source roots. Plaintext ZIP and temporary ciphertext live in a 0700
    directory next to destination; only complete ciphertext is published, mode 0600.
    No identity file/key generation is needed for export. include_secrets fails closed.
    """
    recipient = _recipient(recipient)
    plan = continuity.inventory(roots, ledger_path, include_secrets=include_secrets)
    if dry_run:
        return {**plan, 'dry_run': True, 'recipient': recipient, 'encryption': 'planned, not performed',
                'capabilities': capabilities(age_executable), 'encryption_limits': list(LIMITS)}
    tool, version, env = _tool(age_executable)
    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise EncryptedError('Encrypted backup destination already exists; it will never be overwritten.')
    for raw in list(roots.values()) + ([ledger_path] if ledger_path is not None else []):
        root = Path(raw).expanduser().resolve()
        if continuity._within(destination.resolve(), root):
            raise EncryptedError('Encrypted backup must be outside configured source roots.')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.agentops-encrypt-', dir=destination.parent) as scratch:
        private = Path(scratch)
        plain, cipher = private / 'reviewed-data.zip', private / 'reviewed-data.zip.age'
        evidence = continuity.export_backup(roots, plain, ledger_path, dry_run=False)
        fd = os.open(cipher, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        _run(tool, env, ['--encrypt', '--recipient', recipient, '--output', cipher, plain], timeout)
        with open(cipher, 'rb') as source:
            if source.read(len(AGE_HEADER)) != AGE_HEADER:
                raise EncryptedError('age did not produce the supported native encrypted format.')
        if cipher.stat().st_size > MAX_ENVELOPE_BYTES:
            raise EncryptedError('Encrypted backup exceeds the safety limit.')
        digest = _digest(cipher)
        _publish(cipher, destination)
    return {**evidence, 'archive': str(destination), 'archive_sha256': digest,
            'plaintext_archive_sha256': evidence['archive_sha256'], 'recipient': recipient,
            'encryption': {'tool': 'age', 'version': version, 'secrets_included': False,
                           'decryption_rehearsed': False}, 'encryption_limits': list(LIMITS)}


def _copy_ciphertext(source_path, destination):
    """Pin the exact ciphertext bytes whose hash is checked, without following leaf links."""
    fd = os.open(source_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise EncryptedError('Encrypted input changed to a nonregular file.')
    try:
        out_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except BaseException:
        os.close(fd)
        raise
    digest, size = hashlib.sha256(), 0
    with os.fdopen(fd, 'rb') as source, os.fdopen(out_fd, 'wb') as output:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            size += len(chunk)
            if size > MAX_ENVELOPE_BYTES:
                raise EncryptedError('Encrypted input exceeds the safety limit.')
            digest.update(chunk)
            output.write(chunk)
        after = os.fstat(source.fileno())
        if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise EncryptedError('Encrypted input changed while being staged; no restore was attempted.')
    return digest.hexdigest()


def rehearse_encrypted(archive_path, staging, identity_file, *, age_executable=None,
                       expected_sha256=None, timeout=300):
    """Decrypt with explicit age identity PATH, then validate/rehearse in EMPTY 0700 staging.

    Identity contents never enter Python arguments, results or logs. Plaintext is confined
    to a sibling private temporary directory and removed on success/failure; restored
    reviewed data remain in staging. No services or external actions are started.
    """
    stage = _private_stage(staging)
    archive = _regular(archive_path)
    identity = _regular(identity_file, private=True)
    if continuity._within(archive.resolve(), stage):
        raise EncryptedError('Encrypted input must be outside the empty staging directory.')
    if archive.stat().st_size > MAX_ENVELOPE_BYTES:
        raise EncryptedError('Encrypted input exceeds the safety limit.')
    if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_sha256)):
        raise EncryptedError('Invalid separately recorded ciphertext SHA256.')
    tool, version, env = _tool(age_executable)
    with tempfile.TemporaryDirectory(prefix='.agentops-decrypt-', dir=stage.parent) as scratch:
        private = Path(scratch)
        cipher, plain = private / 'checked-input.age', private / 'decrypted-data.zip'
        digest = _copy_ciphertext(archive, cipher)
        if expected_sha256 is not None and digest != expected_sha256:
            raise EncryptedError('Ciphertext SHA256 differs from separately recorded evidence.')
        fd = os.open(plain, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        _run(tool, env, ['--decrypt', '--identity', identity, '--output', plain, cipher], timeout)
        _regular(plain, private=True)
        if plain.stat().st_size > MAX_ENVELOPE_BYTES:
            raise EncryptedError('Decrypted archive exceeds the safety limit.')
        restored = continuity.restore_backup(plain, stage)
    return {**restored, 'archive_sha256': digest, 'expected_archive_hash_verified': expected_sha256 is not None,
            'decryption': {'tool': 'age', 'version': version, 'authenticated_decryption': True},
            'encryption_limits': list(LIMITS)}
