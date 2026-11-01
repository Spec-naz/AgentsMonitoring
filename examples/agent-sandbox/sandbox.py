#!/usr/bin/python3
"""Offline same-host experiment runner. No production HOME, network or sockets."""
import argparse
import os
from pathlib import Path
import resource
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--workspace', required=True, type=Path)
p.add_argument('--code', required=True, type=Path)
p.add_argument('--timeout', type=int, default=90)
p.add_argument('command', nargs=argparse.REMAINDER)
a = p.parse_args()
workspace = a.workspace.expanduser().absolute()
code = a.code.expanduser().resolve(strict=True)
base = (Path.home() / '.local/state/agent-sandbox').resolve()
if (workspace.is_symlink() or not workspace.is_dir()
        or base not in workspace.resolve().parents):
    p.error('Use an existing dedicated directory under ~/.local/state/agent-sandbox/.')
if workspace.stat().st_uid != os.getuid() or workspace.stat().st_mode & 0o077:
    p.error('Workspace must be owned by this user and mode 0700.')
if not 1 <= a.timeout <= 300 or not a.command:
    p.error('Specify command and timeout 1..300 seconds.')
command = a.command[1:] if a.command[0] == '--' else a.command
args = ['/usr/bin/bwrap', '--unshare-all', '--die-with-parent', '--new-session',
        '--cap-drop', 'ALL', '--clearenv', '--ro-bind', '/usr', '/usr',
        '--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
        '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--dir', '/etc', '--dir', '/home', '--dir', '/home/sandbox',
        '--ro-bind', str(code), '/app', '--bind', str(workspace.resolve()), '/workspace',
        '--setenv', 'HOME', '/home/sandbox', '--setenv', 'PATH', '/usr/bin:/bin',
        '--setenv', 'LANG', 'C.UTF-8', '--setenv', 'PYTHONDONTWRITEBYTECODE', '1',
        '--setenv', 'PYTHONPATH', '/app', '--chdir', '/workspace', '--', *command]
def limits():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024**2, 128 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
try:
    result = subprocess.run(args, env={'PATH': '/usr/bin:/bin'}, timeout=a.timeout,
                            preexec_fn=limits, check=False)
except subprocess.TimeoutExpired:
    p.exit(124, 'Sandbox timeout; parent terminated.\n')
p.exit(result.returncode)
