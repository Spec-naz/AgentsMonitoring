"""Provider lifecycle signals from Claude Code and Codex hooks.

Only allowlisted metadata is kept: provider, event name (with an allowlisted
notification subtype), session ID, the declared actor (AGENTOPS_ACTOR) and the
time AgentOps observed the event. Prompts, transcripts, transcript paths, tool
names/arguments, messages, cwd and any tokens are parsed only to locate the
allowed fields and are never written.

A provider signal describes the provider turn, never task progress. Every
classification therefore carries ``proves_task_progress: False``.

Verified contracts (2026-10-31, see docs/provider-signals.md):
  * Claude Code 2.1.316 hooks: UserPromptSubmit, Stop, StopFailure,
    Notification (notification_type), SessionStart, SessionEnd. Stop does not
    fire on a user interrupt (Esc), so a running turn can go silent.
  * Codex CLI 0.166.0 hooks (hooks.json): UserPromptSubmit, Stop, Interrupt,
    PermissionRequest, SessionStart, SessionEnd. There is no Notification event.
    Hooks must be trusted via /hooks before they run. Stop and Interrupt reject
    plain-text stdout.
"""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import time

SCHEMA_VERSION = 1
DEFAULT_STATE_DIR = Path.home() / '.local/state/agentops/provider-signals'
MAX_STDIN_BYTES = 16 * 1024 * 1024
SESSION_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}')
ACTOR_RE = re.compile(r'[a-z][a-z0-9_-]{0,47}')
MARKER = 'provider_signals'

# event -> effect on the provider turn. Anything not listed is ignored.
CLAUDE_EVENTS = {
    'SessionStart': 'session_start',
    'UserPromptSubmit': 'turn_start',
    'Stop': 'turn_end',
    'StopFailure': 'turn_failed',
    'SessionEnd': 'session_end',
    'Notification:permission_prompt': 'waiting',
    'Notification:elicitation_dialog': 'waiting',
    'Notification:elicitation_url_dialog': 'waiting',
    'Notification:agent_needs_input': 'waiting',
    'Notification:idle_prompt': 'idle_confirmed',
    # Optional activity events; only the event name is kept (no tool name/args).
    'PostToolUse': 'activity',
}
CODEX_EVENTS = {
    'SessionStart': 'session_start',
    'UserPromptSubmit': 'turn_start',
    'Stop': 'turn_end',
    'Interrupt': 'turn_interrupted',
    'SessionEnd': 'session_end',
    # Fires right before an approval prompt; another hook may still decide it.
    'PermissionRequest': 'waiting_probable',
    'PostToolUse': 'activity',
}
PROVIDERS = {'claude': CLAUDE_EVENTS, 'codex': CODEX_EVENTS}
REJECT_REASONS = ('oversize', 'not_json', 'not_object', 'bad_session', 'unknown_event', 'subagent_turn')

# Classification thresholds (seconds).
DEFAULTS = {
    'running_max_age': 2 * 3600,   # no turn end within this -> unknown (Esc never fires Stop)
    'waiting_max_age': 24 * 3600,  # a prompt nobody answered for a day is no longer trustworthy
    'idle_max_age': 7 * 24 * 3600,
    'future_skew': 60,
}


class Rejected(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def _atomic_write(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.sig-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as target:
            json.dump(value, target, ensure_ascii=True, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _session_file(state_dir, provider, session_id):
    # Hashed name: no path tricks, fixed length, still one file per session.
    digest = hashlib.sha256(f'{provider}\0{session_id}'.encode()).hexdigest()[:32]
    return Path(state_dir) / provider / f'{digest}.json'


class _Lock:
    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def parse_event(provider, raw):
    """Return the allowlisted metadata of one hook payload or raise Rejected."""
    if provider not in PROVIDERS:
        raise ValueError('Unknown provider')
    if len(raw) > MAX_STDIN_BYTES:
        raise Rejected('oversize')
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise Rejected('not_json')
    if not isinstance(payload, dict):
        raise Rejected('not_object')
    session = payload.get('session_id')
    if not isinstance(session, str) or not SESSION_RE.fullmatch(session):
        raise Rejected('bad_session')
    event = payload.get('hook_event_name')
    if not isinstance(event, str):
        raise Rejected('unknown_event')
    if event == 'Notification' and provider == 'claude':
        kind = payload.get('notification_type')
        event = 'Notification:' + kind if isinstance(kind, str) else 'Notification:?'
    if event not in PROVIDERS[provider]:
        raise Rejected('unknown_event')
    effect = PROVIDERS[provider][event]
    # Main-session state must not be changed by child turns, activities or prompts.
    # Child approvals require a separate binding/observer; do not conflate them.
    if payload.get('agent_id') is not None:
        raise Rejected('subagent_turn')
    return {'provider': provider, 'event': event, 'effect': effect, 'session_id': session}


def apply_event(state, meta, observed_at, actor):
    """Pure state transition for one session snapshot."""
    state = dict(state) if state else {
        'schema_version': SCHEMA_VERSION, 'provider': meta['provider'], 'session_id': meta['session_id'],
        'actor': None, 'first_seen_at': observed_at, 'turn': 'unknown', 'turn_since': None,
        'waiting_since': None, 'ended_at': None, 'last_event': None, 'last_event_at': None, 'events': 0}
    state['events'] = state.get('events', 0) + 1
    last = state.get('last_event_at')
    if last is not None and observed_at < last:
        return state  # Parallel hooks raced; never let an older event regress state.
    if actor is not None:
        state['actor'] = actor
    effect = meta['effect']
    state['last_event'] = meta['event']
    state['last_event_at'] = observed_at
    if effect == 'session_start':
        state.update(turn='idle', turn_since=observed_at, waiting_since=None, ended_at=None)
    elif effect == 'turn_start':
        state.update(turn='running', turn_since=observed_at, waiting_since=None, ended_at=None)
    elif effect in ('turn_end', 'turn_interrupted'):
        state.update(turn='idle', turn_since=observed_at, waiting_since=None)
    elif effect == 'idle_confirmed':
        if state.get('turn') != 'idle':
            state.update(turn='idle', turn_since=observed_at)
        state['waiting_since'] = None
    elif effect == 'turn_failed':
        state.update(turn='failed', turn_since=observed_at, waiting_since=None)
    elif effect in ('waiting', 'waiting_probable'):
        state.update(waiting_since=state.get('waiting_since') or observed_at,
                     waiting_kind='confirmed' if effect == 'waiting' else 'probable')
    elif effect == 'activity':
        if state.get('turn') == 'running':
            state['waiting_since'] = None  # A tool ran, so a permission prompt was answered.
    elif effect == 'session_end':
        state.update(turn='ended', ended_at=observed_at, waiting_since=None)
    return state


def _count_reject(state_dir, reason):
    try:
        root = _private_dir(state_dir)
        path = root / 'rejected.json'
        with _Lock(root / '.rejected.lock'):
            try:
                counts = json.loads(path.read_text())
                if not isinstance(counts, dict):
                    counts = {}
            except (OSError, ValueError):
                counts = {}
            counts = {k: v for k, v in counts.items() if k in REJECT_REASONS and type(v) is int}
            counts[reason] = counts.get(reason, 0) + 1
            counts['updated_at'] = time.time()
            _atomic_write(path, counts)
    except OSError:
        pass


def ingest(provider, raw, state_dir=DEFAULT_STATE_DIR, now=None, actor=None):
    """Record one hook payload. Returns the new snapshot or None when ignored."""
    now = time.time() if now is None else now
    if actor is not None and not ACTOR_RE.fullmatch(actor):
        actor = None
    try:
        meta = parse_event(provider, raw)
    except Rejected as rejected:
        _count_reject(state_dir, rejected.reason)
        return None
    root = _private_dir(state_dir)
    folder = _private_dir(root / provider)
    path = _session_file(root, provider, meta['session_id'])
    with _Lock(folder / '.lock'):
        try:
            current = json.loads(path.read_text())
            if not isinstance(current, dict) or current.get('session_id') != meta['session_id']:
                current = None
        except FileNotFoundError:
            current = None
        except (OSError, ValueError):
            current = None
        state = apply_event(current, meta, now, actor)
        _atomic_write(path, state)
    return state


def _valid_snapshot(value, provider):
    return (isinstance(value, dict) and value.get('schema_version') == SCHEMA_VERSION
            and value.get('provider') == provider and isinstance(value.get('session_id'), str)
            and SESSION_RE.fullmatch(value['session_id']) is not None
            and isinstance(value.get('last_event_at'), (int, float)))


def read_sessions(state_dir=DEFAULT_STATE_DIR, provider=None):
    """All well-formed session snapshots; malformed or foreign files are skipped."""
    result = []
    for name in ([provider] if provider else sorted(PROVIDERS)):
        folder = Path(state_dir) / name
        try:
            entries = sorted(folder.glob('*.json'))
        except OSError:
            continue
        for path in entries:
            if path.is_symlink() or not path.is_file():
                continue
            try:
                value = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if _valid_snapshot(value, name) and path == _session_file(state_dir, name, value['session_id']):
                result.append(value)
    return result


def classify(snapshot, now=None, limits=None):
    """Turn state of one session: turn_running, waiting_input, idle, failed, ended or unknown."""
    now = time.time() if now is None else now
    limits = {**DEFAULTS, **(limits or {})}
    out = {'state': 'unknown', 'reason': None, 'source': 'provider-hook', 'proves_task_progress': False,
           'provider': None, 'session_id': None, 'actor': None, 'last_event': None,
           'last_event_at': None, 'since': None, 'age_seconds': None, 'confidence': None}
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('last_event_at'), (int, float)):
        out['reason'] = 'no_signal'
        return out
    out.update(provider=snapshot.get('provider'), session_id=snapshot.get('session_id'), actor=snapshot.get('actor'),
               last_event=snapshot.get('last_event'), last_event_at=snapshot['last_event_at'])
    last = snapshot['last_event_at']
    if last - now > limits['future_skew']:
        out['reason'] = 'clock_skew'
        return out
    age = max(0.0, now - last)
    out['age_seconds'] = age
    turn = snapshot.get('turn')
    waiting = snapshot.get('waiting_since')
    if turn == 'ended':
        if age > limits['idle_max_age']:
            out['reason'] = 'signal_stale'
            return out
        out.update(state='ended', since=snapshot.get('ended_at'), confidence='confirmed')
        return out
    if waiting is not None and turn in ('running', 'idle', 'unknown'):
        if now - waiting > limits['waiting_max_age']:
            out['reason'] = 'waiting_signal_stale'
            return out
        out.update(state='waiting_input', since=waiting, confidence=snapshot.get('waiting_kind', 'confirmed'))
        return out
    if turn == 'running':
        if age > limits['running_max_age']:
            out['reason'] = 'running_without_end_signal'  # Esc interrupt in Claude never fires Stop.
            return out
        out.update(state='turn_running', since=snapshot.get('turn_since'), confidence='confirmed')
        return out
    if turn in ('idle', 'failed'):
        if age > limits['idle_max_age']:
            out['reason'] = 'signal_stale'
            return out
        out.update(state=turn, since=snapshot.get('turn_since'), confidence='confirmed')
        return out
    out['reason'] = 'no_turn_signal'
    return out


def agent_signal(agent, provider, state_dir=DEFAULT_STATE_DIR, now=None, session_id=None, limits=None):
    """Bind an agent name to exactly one live provider session or return unknown.

    The binding uses the declared actor written at ingest (AGENTOPS_ACTOR) and,
    when given, an exact session_id. Zero matches, several live matches, another
    provider or an actor mismatch all return unknown rather than a guess.
    """
    now = time.time() if now is None else now
    base = classify(None, now, limits)
    if provider not in PROVIDERS or not isinstance(agent, str) or not ACTOR_RE.fullmatch(agent):
        base['reason'] = 'invalid_binding'
        return base
    sessions = [s for s in read_sessions(state_dir, provider) if s.get('actor') == agent]
    if session_id is not None:
        sessions = [s for s in sessions if s['session_id'] == session_id]
    classified = [classify(s, now, limits) for s in sessions]
    live = [c for c in classified if c['state'] not in ('ended', 'unknown')]
    if len(live) == 1:
        return live[0]
    if len(live) > 1:
        base.update(reason='ambiguous_binding', provider=provider, actor=agent)
        return base
    if classified:  # Only ended or unknown sessions remain; report the newest as is.
        return max(classified, key=lambda c: c['last_event_at'] or 0)
    base.update(reason='no_bound_session', provider=provider, actor=agent)
    return base


def prune(state_dir=DEFAULT_STATE_DIR, now=None, max_age=14 * 24 * 3600):
    """Delete snapshots whose last event is older than max_age. Returns the count."""
    now = time.time() if now is None else now
    removed = 0
    for provider in PROVIDERS:
        folder = Path(state_dir) / provider
        if not folder.is_dir():
            continue
        with _Lock(folder / '.lock'):
            for path in folder.glob('*.json'):
                try:
                    value = json.loads(path.read_text())
                    stale = not _valid_snapshot(value, provider) or now - value['last_event_at'] > max_age
                except (OSError, ValueError):
                    stale = True
                if stale and not path.is_symlink():
                    path.unlink(missing_ok=True)
                    removed += 1
    return removed


# ---------------------------------------------------------------- install plan

CLAUDE_PLAN = (
    ('SessionStart', None), ('UserPromptSubmit', None), ('Stop', None), ('StopFailure', None),
    ('Notification', 'permission_prompt|idle_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input'),
    ('SessionEnd', None),
)
CODEX_PLAN = (
    ('SessionStart', None), ('UserPromptSubmit', None), ('Stop', None), ('Interrupt', None),
    ('PermissionRequest', None), ('SessionEnd', None),
)


def hook_command(provider, python='/usr/bin/python3', package_root=None, state_dir=None):
    """Hook command line. Runs this file as an isolated script (-I): the agent's cwd,
    PYTHONPATH and user site are never searched, so a planted agentops/ package in a
    working directory cannot be executed by the hook."""
    if provider not in PROVIDERS:
        raise ValueError('Unknown provider')
    root = Path(package_root) if package_root else Path(__file__).resolve().parent.parent
    script = root / 'agentops' / 'provider_signals.py'
    parts = [_shell_quote(python), '-I', _shell_quote(str(script)), 'ingest', '--provider', provider]
    if state_dir:
        parts += ['--state-dir', _shell_quote(str(state_dir))]
    return ' '.join(parts)


def _shell_quote(value):
    return value if re.fullmatch(r'[A-Za-z0-9_./:=@%+-]+', value) else "'" + value.replace("'", "'\\''") + "'"


def _is_ours(handler):
    if not isinstance(handler, dict) or handler.get('type') != 'command' or not isinstance(handler.get('command'), str):
        return False
    try:
        words = shlex.split(handler['command'])
    except ValueError:
        return False
    return any(words[index:index + 2] == ['-m', 'agentops.provider_signals'] for index in range(len(words))) or any(
        Path(word).parts[-2:] == ('agentops', 'provider_signals.py') and index > 0 and words[index - 1] == '-I'
        for index, word in enumerate(words))


def merge_hooks(existing, provider, command, include_activity=False):
    """Return (new_config, plan). Never removes or edits an existing handler."""
    if provider not in PROVIDERS:
        raise ValueError('Unknown provider')
    if existing is None:
        existing = {}
    if not isinstance(existing, dict):
        raise ValueError('Config root is not a JSON object')
    config = json.loads(json.dumps(existing))
    hooks = config.setdefault('hooks', {})
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" is not an object; refusing to touch it')
    events = list(CLAUDE_PLAN if provider == 'claude' else CODEX_PLAN)
    if include_activity:
        events.append(('PostToolUse', None))
    plan = {'provider': provider, 'add': [], 'already_present': [], 'preserved': {}}
    for event, groups in hooks.items():
        if isinstance(groups, list):
            plan['preserved'][event] = sum(len(g.get('hooks', [])) for g in groups if isinstance(g, dict)
                                           and isinstance(g.get('hooks'), list))
    for event, matcher in events:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f'hooks.{event} is not a list; refusing to touch it')
        if any(_is_ours(h) for g in groups if isinstance(g, dict) for h in (g.get('hooks') or [])):
            plan['already_present'].append(event)
            continue
        handler = {'type': 'command', 'command': command, 'timeout': 5}
        if provider == 'codex' and event in ('Interrupt', 'SessionEnd'):
            handler['timeout'] = 2  # Codex caps these at 1-3 s.
        if provider == 'claude' and event == 'PostToolUse':
            handler['async'] = True
        group = {'hooks': [handler]}
        if matcher:
            group['matcher'] = matcher
        groups.append(group)
        plan['add'].append({'event': event, 'matcher': matcher, 'timeout': handler['timeout']})
    return config, plan


def plan_install(provider, config_path=None, command=None, include_activity=False):
    """Read-only: describe what would be added. Never writes the config."""
    if provider not in PROVIDERS:
        raise ValueError('Unknown provider')
    if config_path is None:
        config_path = Path.home() / ('.claude/settings.json' if provider == 'claude' else '.codex/hooks.json')
    config_path = Path(config_path)
    existing = None
    if config_path.exists():
        existing = json.loads(config_path.read_text())
    command = command or hook_command(provider)
    new_config, plan = merge_hooks(existing, provider, command, include_activity)
    plan.update(config_path=str(config_path), config_exists=existing is not None, command=command,
                writes=False, notes=[])
    if provider == 'codex':
        plan['notes'].append('Codex runs only trusted hooks: review and trust them with /hooks after the edit.')
        toml = config_path.parent / 'config.toml'
        try:
            if re.search(r'^\s*\[\[?hooks[.\]]', toml.read_text(), re.M):
                plan['notes'].append(f'{toml} also defines hooks; they keep running alongside hooks.json.')
            if re.search(r'^\s*hooks\s*=\s*false', toml.read_text(), re.M):
                plan['notes'].append('features.hooks = false in config.toml: hooks are disabled.')
        except OSError:
            pass
    else:
        plan['notes'].append('Claude Code merges hook settings from all levels; nothing existing is replaced.')
        plan['notes'].append('Stop does not fire on Esc interrupts; a running turn becomes unknown after running_max_age.')
    plan['notes'].append('Set AGENTOPS_ACTOR=<agent> in each agent launcher, otherwise sessions cannot be bound.')
    return new_config, plan


# ------------------------------------------------------------------------ CLI

def _read_stdin():
    data = bytearray()
    stream = sys.stdin.buffer
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        if len(data) <= MAX_STDIN_BYTES:
            data += chunk  # Keep draining past the cap so the writer never sees EPIPE.
    return bytes(data)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog='provider_signals.py')
    sub = parser.add_subparsers(dest='command', required=True)
    ing = sub.add_parser('ingest', help='hook entry point: read one payload from stdin')
    ing.add_argument('--provider', choices=sorted(PROVIDERS), required=True)
    ing.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    show = sub.add_parser('status', help='classified state of recorded sessions')
    show.add_argument('--state-dir', type=Path, default=DEFAULT_STATE_DIR)
    show.add_argument('--agent')
    show.add_argument('--provider', choices=sorted(PROVIDERS))
    plan = sub.add_parser('plan-install', help='print the merge plan; never writes')
    plan.add_argument('--provider', choices=sorted(PROVIDERS), required=True)
    plan.add_argument('--config', type=Path)
    plan.add_argument('--package-root', type=Path)
    plan.add_argument('--state-dir', type=Path)
    plan.add_argument('--include-activity', action='store_true')
    args = parser.parse_args(argv)

    if args.command == 'ingest':
        event = None
        try:
            raw = _read_stdin()
            state = ingest(args.provider, raw, args.state_dir, actor=os.environ.get('AGENTOPS_ACTOR'))
            event = parse_event(args.provider, raw)['event'] if state is not None else None
        except Exception:  # noqa: BLE001 - a signal hook must never break the agent.
            pass
        # Empty stdout means "no decision" for both providers, and plain stdout on
        # UserPromptSubmit/SessionStart would be injected as context. Codex documents
        # that Stop expects JSON on stdout, so only there an empty object is written.
        if args.provider == 'codex' and event == 'Stop':
            sys.stdout.write('{}')
        return 0
    if args.command == 'status':
        now = time.time()
        if args.agent:
            if not args.provider:
                parser.error('--agent requires --provider')
            rows = [agent_signal(args.agent, args.provider, args.state_dir, now)]
        else:
            rows = [classify(s, now) for s in read_sessions(args.state_dir, args.provider)]
        print(json.dumps(rows, indent=2, sort_keys=True))
        return 0
    command = hook_command(args.provider, package_root=args.package_root, state_dir=args.state_dir)
    _, result = plan_install(args.provider, args.config, command, args.include_activity)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
    except OSError as error:
        sys.exit(0 if error.errno == errno.EPIPE else 1)
