"""Metadata-only on-demand worker visibility; no auth, model calls or job contents."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat

MAX_CONFIG = 65536


def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Expected regular metadata file')
    return info


@contextmanager
def metadata_file(path, max_size=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (max_size is not None and info.st_size > max_size):
            raise ValueError('Invalid metadata file')
        yield descriptor
    finally:
        os.close(descriptor)


def model_config(path, expected_model, expected_provider):
    """Read only bounded config metadata; parse this simple model section, not YAML."""
    with metadata_file(path, MAX_CONFIG) as descriptor:
        content = os.read(descriptor, MAX_CONFIG + 1)
    if not content or len(content) > MAX_CONFIG:
        raise ValueError('Invalid profile config')
    lines = content.decode('utf-8').splitlines()
    sections = [i for i, line in enumerate(lines) if re.match(r'^model\s*:', line)]
    if len(sections) != 1 or lines[sections[0]] != 'model:':
        raise ValueError('Missing or ambiguous model metadata')
    values = {}
    for line in lines[sections[0] + 1:]:
        if line and not line[0].isspace() and not line.startswith('#'):
            break
        match = re.fullmatch(r'  (provider|default):\s*([A-Za-z0-9_.-]+)\s*', line)
        if re.match(r'^  (provider|default)\s*:', line) and not match:
            raise ValueError('Unsupported model metadata syntax')
        if match:
            key, value = match.groups()
            if key in values:
                raise ValueError('Duplicate model metadata')
            values[key] = value
    if values != {'provider': expected_provider, 'default': expected_model}:
        raise ValueError('Unexpected profile model/provider')


def worker_state(config):
    """An observed held wrapper lock means local activity, never proven task progress."""
    profile = Path(config['config_path']).expanduser()
    launcher = Path(config['launcher_path']).expanduser()
    state = Path(config['state_dir']).expanduser()
    model_config(profile, config['model'], config['provider'])
    regular(launcher)
    try:
        info = state.lstat()
    except FileNotFoundError:
        return 'ready'
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Invalid state directory')
    marker = state / 'unresolved.json'
    lock = state / 'run.lock'
    try:
        with metadata_file(lock) as descriptor:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return 'working'
            # Shared probes coexist; an active wrapper holds an exclusive lock.
            return 'attention' if os.path.lexists(marker) else 'ready'
    except FileNotFoundError:
        # Any marker, even a dangling symlink or unreadable one, requires intervention.
        return 'attention' if os.path.lexists(marker) else 'ready'


def collect(context, settings):
    configs = settings.get('workers', [])
    if not isinstance(configs, list) or len(configs) > 8:
        raise ValueError('At most eight explicitly registered workers')
    rows = []
    ids = set()
    for config in configs:
        if (not isinstance(config, dict) or not isinstance(config.get('id'), str)
                or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', config['id'])):
            raise ValueError('Invalid worker registration')
        identity = config['id']
        if identity in ids:
            raise ValueError('Duplicate worker registration')
        ids.add(identity)
        public_valid = True
        public = {}
        for key, fallback in (('name', identity), ('model', 'Model neověřen'), ('quota_label', 'Kvóta neověřena')):
            value = config.get(key, identity if key == 'name' else '')
            if not isinstance(value, str) or not value or len(value) > 120 or any(ord(char) < 32 for char in value):
                value, public_valid = fallback, False
            public[key] = value
        name, model, quota = public['name'], public['model'], public['quota_label']
        try:
            state = worker_state(config) if public_valid else 'attention'
        except (OSError, ValueError, TypeError, KeyError, UnicodeError, RuntimeError):
            state = 'attention'
        statuses = {'ready': 'Připraven', 'working': 'Pracuje', 'attention': 'Vyžaduje zásah'}
        details = {
            'ready': 'Profil a model jsou nastavené; přihlášení a vypnuté extra usage ověří až spuštění úkolu.',
            'working': 'Místní wrapper drží zámek běhu. To není důkaz postupu ani dokončení úkolu.',
            'attention': 'Chybí platná konfigurace nebo předchozí běh vyžaduje vyjasnění. Automatický restart se neprovádí.',
        }
        rows.append({'id': identity, 'name': name, 'label': model, 'alive': state != 'attention',
                     'status': statuses[state], 'status_detail': details[state], 'account': quota,
                     'actions_locked': True})
    return {'tables': [{'id': 'workers', 'title': 'Task workers', 'rows': rows,
                       'note': 'Pracovníci na vyžádání; nemusí trvale běžet. Kvóta je sdílená, nevzniká další předplatné. Bez automatického spouštění a bez ověření účtování v dashboardu.'}]} if rows else {'tables': []}
