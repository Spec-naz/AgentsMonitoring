"""Official Antigravity statusLine metadata collector and explicit profile installer.

Collector persists only conversation UUID, model, quota/reset metadata and explicitly
configured profile/pool labels. No credentials, transcripts, context tokens or network.
"""
import argparse
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
import time

MAX_INPUT = 262144
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')
BUCKET = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,59}\Z')


def label(value):
    if not isinstance(value, str) or not value or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid metadata label')
    return value


def number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def reset_epoch(bucket, now):
    maximum = now + 366 * 86400
    value = bucket.get('reset_time')
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.tzinfo is not None and 0 <= dt.timestamp() <= maximum:
                return int(dt.timestamp())
        except (ValueError, OverflowError):
            pass
    if number(value) and 0 <= value <= maximum:
        return int(value)
    seconds = bucket.get('reset_in_seconds')
    if number(seconds) and 0 <= seconds <= 366 * 86400:
        return int(now + seconds)
    if 'reset_time' in bucket or 'reset_in_seconds' in bucket:
        raise ValueError('Invalid reset metadata')
    return None


def sanitize(payload, profile, pool, now=None):
    now = time.time() if now is None else now
    if not isinstance(payload, dict):
        raise ValueError('Invalid status metadata')
    sid, compat = payload.get('conversation_id'), payload.get('session_id')
    if sid and compat and sid != compat:
        raise ValueError('Ambiguous conversation identity')
    sid = sid or compat
    if not isinstance(sid, str) or not UUID.fullmatch(sid):
        raise ValueError('No active conversation UUID')
    model = payload.get('model')
    try:
        model_id = label(model['id']) if isinstance(model, dict) and model.get('id') else None
    except ValueError:
        model_id = None
    raw = payload.get('quota')
    quota = {}
    if isinstance(raw, dict):
        for key, bucket in list(raw.items())[:32]:
            if not isinstance(key, str) or not BUCKET.fullmatch(key) or not isinstance(bucket, dict):
                continue
            clean = {}
            if bucket.get('disabled') is True:
                clean['disabled'] = True
            fraction = bucket.get('remaining_fraction')
            amount = bucket.get('remaining_amount')
            if number(fraction) and 0 <= fraction <= 1:
                clean['remaining_fraction'] = fraction
            elif number(amount) and amount >= 0:
                clean['remaining_amount'] = amount
            try:
                reset = reset_epoch(bucket, now)
            except ValueError:
                continue
            if reset is not None:
                clean['reset_at'] = reset
            if clean:
                quota[key] = clean
            if len(quota) == 8:
                break
    return {'schema_version': 1, 'conversation_id': sid.lower(), 'profile': label(profile),
            'pool': label(pool), 'model': model_id, 'observed_at': now, 'quota': quota}


def private_directory(path):
    path = Path(path).expanduser().absolute()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('Invalid metadata directory')
    os.chmod(path, 0o700)
    return path


def atomic_write(path, contents):
    fd, temporary = tempfile.mkstemp(prefix='.status-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(contents)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_file(path, limit=MAX_INPUT):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('Invalid metadata file')
        raw = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if len(raw) > limit:
        raise ValueError('Oversized metadata file')
    return raw


def read_json(path, limit=MAX_INPUT):
    return json.loads(read_file(path, limit))


def store_snapshot(snapshot, directory):
    directory = private_directory(directory)
    path = directory / (snapshot['conversation_id'] + '.json')
    descriptor = os.open(path.with_suffix('.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('Invalid snapshot lock')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.path.lexists(path):
            old = read_json(path)
            if not isinstance(old, dict) or old.get('profile') != snapshot['profile'] or old.get('pool') != snapshot['pool']:
                raise ValueError('Conversation belongs to another configured financial pool')
        atomic_write(path, json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode())
    finally:
        os.close(descriptor)
    return path


WRAPPER_HEADER = '#!/bin/sh\n# AgentsMonitoring Antigravity quota collector\n'


def managed_wrapper(contents, expected_args):
    """Recognize our narrow generated format; marker/uid are not authentication."""
    try:
        source = contents.decode('utf-8')
        if not source.startswith(WRAPPER_HEADER) or len(source.splitlines()) != 3:
            return False
        command = source.splitlines()[2]
        if not command.startswith('exec '):
            return False
        words = shlex.split(command[5:])
        return (len(words) == 10 and words[1] == '-I' and words[3] == 'collect'
                and words[4:] == expected_args[4:] and Path(words[0]).is_absolute()
                and Path(words[2]).is_absolute() and Path(words[2]).name == 'antigravity_usage.py'
                and Path(words[2]).parent.name == 'agentsmon')
    except (ValueError, UnicodeError):
        return False


def install(profile_home, profile, pool, directory, apply=False):
    profile_home = Path(profile_home).expanduser().absolute()
    path = profile_home / '.gemini/antigravity-cli/settings.json'
    original = read_file(path) if os.path.lexists(path) else None
    previous = json.loads(original) if original is not None else {}
    if not isinstance(previous, dict):
        raise ValueError('Invalid settings object')
    arguments = [sys.executable, '-I', str(Path(__file__).absolute()), 'collect',
                 '--profile', label(profile), '--pool', label(pool),
                 '--snapshot-dir', str(Path(directory).expanduser().absolute())]
    wrapper = path.parent / 'agentsmon-statusline.sh'
    wrapper_contents = (WRAPPER_HEADER + 'exec ' + shlex.join(arguments) + '\n').encode()
    desired = {'type': 'command', 'command': shlex.quote(str(wrapper)), 'enabled': True, 'stack_with_default': True}
    configured = previous.get('statusLine') == desired
    if 'statusLine' in previous and not configured:
        raise ValueError('Existing statusLine preserved; compose explicitly before installing')
    old_wrapper = None
    if os.path.lexists(wrapper):
        info = wrapper.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError('Wrapper is not privately managed')
        old_wrapper = read_file(wrapper)
        if old_wrapper != wrapper_contents and not (configured and managed_wrapper(old_wrapper, arguments)):
            raise ValueError('Foreign or changed wrapper preserved; review before replacing')
    if configured and old_wrapper == wrapper_contents:
        return {'status': 'already-configured', 'command_path': str(wrapper)}
    operation = 'upgrade' if configured and old_wrapper is not None else 'recover' if old_wrapper is not None else 'install'
    if not apply:
        return {'status': 'ready-to-' + operation, 'changes': ['statusLine', 'managed-wrapper'],
                'applied': False, 'command_path': str(wrapper)}
    private_directory(path.parent)
    updated = dict(previous, statusLine=desired)
    stamp = str(time.time_ns())
    if original is not None:
        # Exact bytes preserve formatting, ordering and any unrelated settings.
        atomic_write(path.with_name('settings.before-agentsmon-' + stamp + '.json'), original)
    if old_wrapper is not None and old_wrapper != wrapper_contents:
        atomic_write(wrapper.with_name('agentsmon-statusline.before-agentsmon-' + stamp + '.sh'), old_wrapper)
    if old_wrapper != wrapper_contents:
        atomic_write(wrapper, wrapper_contents)
    wrapper.chmod(0o700)
    atomic_write(path, json.dumps(updated, ensure_ascii=False, indent=2).encode())
    return {'status': 'configured', 'operation': operation, 'applied': True, 'command_path': str(wrapper)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('collect', 'install'):
        p = commands.add_parser(name)
        p.add_argument('--profile', required=True)
        p.add_argument('--pool', required=True)
        p.add_argument('--snapshot-dir', type=Path, default=Path('~/.local/state/agentsmon/usage/antigravity'))
        if name == 'install':
            p.add_argument('--profile-home', type=Path, required=True)
            p.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'collect':
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(raw) > MAX_INPUT:
                raise ValueError('Oversized status input')
            store_snapshot(sanitize(json.loads(raw), args.profile, args.pool), args.snapshot_dir)
        else:
            print(json.dumps(install(args.profile_home, args.profile, args.pool, args.snapshot_dir, args.apply)))
        return 0
    except (ValueError, OSError, TypeError, KeyError, RuntimeError, RecursionError, OverflowError):
        if args.command == 'collect':
            # Empty pre-conversation payloads are normal; do not disable native statusLine.
            return 0
        print('StatusLine nebyla změněna; existující nastavení zachováno.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
