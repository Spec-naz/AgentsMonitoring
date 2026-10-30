"""Read-only host snapshot: security checks, agent access inventory and ecosystem map.

Runs as the dashboard user, never as root and never over the network. Secret VALUES are never
read into results: env files contribute key names only, JSON configs only whitelisted
non-secret fields, everything else is file metadata (mode, age). What cannot be checked
without root is reported as ``unknown`` instead of guessed. Results are cached because a
scan walks the home directory and runs a few system commands.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import socket
import stat
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

CACHE_SECONDS = 300
MIN_RESCAN_SECONDS = 30
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "data": None}

# Anything that looks like a credential inside free text (cron lines, process names).
_SECRETISH = re.compile(r"(?i)((?:token|key|secret|password|passwd|bearer)[=: ]+)\S{6,}|\b[A-Za-z0-9_\-]{32,}\b"
                        r"|\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b")


def redact(text: str) -> str:
    return _SECRETISH.sub(lambda m: (m.group(1) or "") + "***", text or "")


def cron_metadata(line: str) -> str:
    """Expose schedule and executable name only; arguments/env values may be secrets."""
    line = line.strip()
    assignment = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
    if assignment:
        return assignment.group(1) + '=[configured]'
    try:
        words = shlex.split(line)
    except ValueError:
        return '[unparseable scheduled job; command hidden]'
    if not words:
        return ''
    if words[0] in ('@reboot', '@yearly', '@annually', '@monthly', '@weekly', '@daily', '@midnight', '@hourly'):
        schedule, command = words[:1], words[1:]
    elif len(words) >= 6 and all(re.fullmatch(r'[A-Za-z0-9*?,/\-]+', w) for w in words[:5]):
        schedule, command = words[:5], words[5:]
    else:
        return '[scheduled job; command hidden]'
    while command and re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', command[0]):
        command.pop(0)
    executable = Path(command[0]).name if command else ''
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', executable):
        executable = '[command hidden]'
    return ' '.join(schedule) + ' ' + redact(executable) + ' [arguments hidden]'


def remote_metadata(remote: str) -> str:
    """Drop URL userinfo, query and fragment; never expose helper command strings."""
    try:
        parts = urlsplit(remote)
        if parts.scheme in ('http', 'https', 'ssh', 'git') and parts.hostname:
            host = '[' + parts.hostname + ']' if ':' in parts.hostname else parts.hostname
            return urlunsplit((parts.scheme, host, parts.path, '', ''))
    except ValueError:
        pass
    scp = re.fullmatch(r'(?:[^@/:\s]+@)?([A-Za-z0-9.-]+):([A-Za-z0-9_./-]+)', remote)
    if scp:
        return scp.group(1) + ':' + scp.group(2)
    return '[configured remote; details hidden]' if remote else ''


class CommandOutput(str):
    def __new__(cls, text, successful):
        value = super().__new__(cls, text)
        value.successful = successful
        return value


def _run(cmd: list[str], timeout: int = 10, env: dict | None = None) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return CommandOutput(result.stdout, result.returncode == 0)
    except (OSError, subprocess.SubprocessError):
        return CommandOutput('', False)


def _home() -> Path:
    return Path.home()


def _tilde(path: Path | str) -> str:
    p, h = str(path), str(_home())
    return "~" + p[len(h):] if p == h or p.startswith(h + os.sep) else p


def _meta(path: Path) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {"exists": False}
    return {"exists": True, "mode": f"{stat.S_IMODE(st.st_mode):03o}",
            "age_days": int((time.time() - st.st_mtime) // 86400),
            "exposed": bool(stat.S_IMODE(st.st_mode) & 0o077)}


def env_key_names(path: Path) -> list[str]:
    """Names of assigned variables in a dotenv file. Values are never returned."""
    names = []
    try:
        for line in path.read_text("utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\S?)", line)
            if m and m.group(2):
                names.append(m.group(1))
    except OSError:
        pass
    return sorted(set(names))


# ---- sshd ----------------------------------------------------------------------------------
_SSHD_KEYS = ("permitrootlogin", "passwordauthentication", "kbdinteractiveauthentication",
              "pubkeyauthentication", "port")


def sshd_effective(root: Path = Path("/")) -> dict:
    """Effective global sshd values from readable config files (first value wins, Includes
    expanded in place, Match blocks ignored). Returns {key: (value, source)} plus 'readable'."""
    found: dict = {}
    seen: set = set()
    complete, has_match = True, False

    def read(path: Path, in_match: bool = False):
        nonlocal complete, has_match
        if path in seen:
            return in_match
        seen.add(path)
        try:
            lines = path.read_text("utf-8", errors="ignore").splitlines()
        except OSError:
            complete = False
            return in_match
        for raw in lines:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split(None, 1)
            key, val = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
            if key == "match":
                in_match = True
                has_match = True
                continue
            if in_match:
                continue
            if key == "include":
                for pattern in val.split():
                    if not pattern.startswith("/"):
                        pattern = str(root / "etc/ssh" / pattern)
                    elif root != Path("/"):
                        pattern = str(root / pattern.lstrip("/"))
                    matches = sorted(glob.glob(pattern))
                    if not matches:
                        # glob hides inaccessible search directories too. An empty
                        # pattern may be harmless, but cannot prove completeness.
                        complete = False
                    for inc in matches:
                        in_match = read(Path(inc), in_match)
                continue
            if key in _SSHD_KEYS and key not in found:
                found[key] = (val.lower(), _tilde(path) if root == Path("/") else str(path.relative_to(root)))
        return in_match

    main = root / "etc/ssh/sshd_config"
    read(main)
    found["readable"] = os.access(main, os.R_OK)
    found["complete"] = complete
    found["has_match"] = has_match
    return found


# ---- network -------------------------------------------------------------------------------
def listeners() -> list[dict] | None:
    out = []
    observed = _run(["ss", "-ltnupH"])
    if not getattr(observed, 'successful', bool(observed)):
        return None
    for line in observed.splitlines():
        cols = line.split()
        if len(cols) < 5:
            continue
        proto, local = cols[0], cols[4]
        addr, _, port = local.rpartition(":")
        proc = ""
        m = re.search(r'users:\(\("([^"]+)"', line)
        if m:
            proc = m.group(1)
        bare = addr.strip("[]").split("%")[0]
        loopback = bare.startswith("127.") or bare in ("::1", "localhost") or "%lo" in addr
        out.append({"proto": proto, "address": addr, "port": port, "process": proc,
                    "public": not loopback})
    # Ephemeral wildcard UDP sockets of one process (e.g. cloudflared QUIC) collapse into one row.
    merged: dict = {}
    for item in [i for i in out if i["proto"] == "udp" and i["address"] in ("*", "0.0.0.0", "[::]") and i["process"]]:
        key = item["process"]
        if key in merged:
            merged[key]["port"] += ", " + item["port"]
            out.remove(item)
        else:
            merged[key] = item
    seen, unique = set(), []
    for item in out:
        key = (item["proto"], item["port"], item["public"], item["process"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return sorted(unique, key=lambda i: (not i["public"], i["proto"], int(i["port"]) if i["port"].isdigit() else 0))


def _active(unit: str, user: bool = False) -> str:
    cmd = ["systemctl"] + (["--user"] if user else []) + ["is-active", unit]
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return _run(cmd, env=env).strip() or "unknown"


# ---- security checks ------------------------------------------------------------------------
def _check(cid, title, status, detail, advice=""):
    return {"id": cid, "title": title, "status": status, "detail": detail, "advice": advice}


def security_checks(sshd: dict, listen: list[dict] | None, creds: list[dict], root: Path = Path("/")) -> list[dict]:
    checks = []
    if not sshd.get("readable") or not sshd.get('complete', True):
        checks.append(_check("ssh", "SSH configuration", "unknown", "sshd_config or an included file is not readable; global defaults cannot be verified."))
    else:
        pw, src = sshd.get("passwordauthentication", ("yes", "default"))
        checks.append(_check("ssh_password", "Parsed global SSH password default", "risk" if pw == "yes" else "ok",
                             f"PasswordAuthentication {pw} ({src})",
                             "Use keys only: set PasswordAuthentication no in a file read before cloud-init."
                             if pw == "yes" else ""))
        rl, src = sshd.get("permitrootlogin", ("prohibit-password", "default"))
        status = "risk" if rl == "yes" else "warn" if rl in ("prohibit-password", "without-password", "forced-commands-only") else "ok"
        checks.append(_check("ssh_root", "Parsed global SSH root default", status, f"PermitRootLogin {rl} ({src})",
                             "Disable direct root login; use a user account and sudo." if status != "ok" else ""))
    public = [l for l in (listen or []) if l["public"] and not (l["port"] == "22" and l["proto"] == "tcp")
              and not (l["proto"] == "udp" and l["process"] == "cloudflared")]
    checks.append(_check("public_ports", "Services listening on non-loopback addresses",
                         "unknown" if listen is None else "warn" if public else "ok",
                         'Listener scan failed or is unavailable.' if listen is None else
                         (", ".join(f'{l["proto"]} {l["address"]}:{l["port"]} {l["process"]}'.strip() for l in public)
                          or "No unexpected non-loopback listeners observed."),
                         "Bind internal services to 127.0.0.1 or put them behind the tunnel." if public else ""))
    ufw = ""
    try:
        ufw = (root / "etc/ufw/ufw.conf").read_text("utf-8", errors="ignore")
    except OSError:
        pass
    enabled = re.search(r"(?m)^\s*ENABLED\s*=\s*yes", ufw)
    checks.append(_check("firewall", "Firewall (ufw)", "ok" if enabled else ("warn" if ufw else "unknown"),
                         "enabled; rules need root to list" if enabled else ("installed but disabled" if ufw else "ufw not found"),
                         "" if enabled else "Enable ufw with SSH allowed."))
    f2b = _active("fail2ban")
    checks.append(_check("fail2ban", "Brute-force protection (fail2ban)", "ok" if f2b == "active" else "warn",
                         f"service {f2b}", "" if f2b == "active" else "Install and enable fail2ban for sshd."))
    reboot = (root / "var/run/reboot-required").exists()
    checks.append(_check("reboot", "Pending reboot", "warn" if reboot else "ok",
                         "a reboot is required to load updated packages (often the kernel)" if reboot else "none required",
                         "Schedule a reboot; agents and bridges come back on their own." if reboot else ""))
    notes = ""
    try:
        notes = (root / "var/lib/update-notifier/updates-available").read_text("utf-8", errors="ignore")
    except OSError:
        pass
    m_all = re.search(r"(\d+) updates? can be applied", notes)
    m_sec = re.search(r"(\d+) of these updates (?:is a|are) (?:standard )?security update", notes)
    auto = ""
    try:
        auto = (root / "etc/apt/apt.conf.d/20auto-upgrades").read_text("utf-8", errors="ignore")
    except OSError:
        pass
    unattended = bool(re.search(r'Unattended-Upgrade\s+"1"', auto))
    sec = int(m_sec.group(1)) if m_sec else 0
    checks.append(_check("updates", "Package updates", "warn" if sec or not unattended else "ok",
                         (f"{m_all.group(1)} updates available, {sec} security" if m_all else "no pending count available")
                         + ("; automatic security updates on" if unattended else "; automatic security updates OFF"),
                         "" if unattended and not sec else "Enable unattended-upgrades and apply security updates."))
    exposed = [c for c in creds if c.get("exposed")]
    checks.append(_check("secret_files", "Credential file permissions", "risk" if exposed else "ok",
                         ", ".join(c["path"] for c in exposed) or f"{sum(1 for c in creds if c.get('exists'))} known files, none readable by others",
                         "chmod 600 the listed files." if exposed else ""))
    try:
        home_mode = stat.S_IMODE(_home().stat().st_mode)
        checks.append(_check("home", "Home directory", "warn" if home_mode & 0o007 else "ok",
                             f"mode {home_mode:03o}", "chmod 750 (or 700) your home." if home_mode & 0o007 else ""))
    except OSError:
        pass
    return checks


# ---- access inventory -----------------------------------------------------------------------
def _json_fields(path: Path, fields: tuple) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return {f: data.get(f) for f in fields if isinstance(data, dict) and f in data}


def access_inventory() -> list[dict]:
    h = _home()
    items: list[dict] = []

    def add(owner, name, kind, path: Path, **extra):
        meta = _meta(path)
        if not meta["exists"] and not extra.pop("show_missing", False):
            return
        items.append({"owner": owner, "name": name, "kind": kind, "path": _tilde(path), **meta, **extra})

    add("Claude Code", "Anthropic sign-in", "OAuth session", h / ".claude/.credentials.json")
    add("Codex", "OpenAI sign-in", "OAuth session", h / ".codex/auth.json")
    for env in (h / ".hermes/.env",):
        add("Hermes", "Provider keys and bot token", "environment file", env, keys=env_key_names(env))
    add("Hermes", "Hermes sign-in", "OAuth session", h / ".hermes/auth.json")
    for cfg in sorted((h / ".config/agent2telegram").glob("*.json")):
        f = _json_fields(cfg, ("agent", "bot_username", "tmux_session", "allowed_user_ids", "allowed_group_ids"))
        try:
            raw = json.loads(cfg.read_text("utf-8"))
        except (OSError, ValueError):
            raw = {}
        extras = [k for k in ("elevenlabs_api_key",) if isinstance(raw, dict) and raw.get(k)]
        users = f.get("allowed_user_ids") or []
        add({"claude-code": "Claude Code", "codex": "Codex"}.get(f.get("agent"), f.get("agent") or "Telegram bridge"),
            f"Telegram bot @{f.get('bot_username') or '?'}", "bot token" + (" + " + ", ".join(extras) if extras else ""),
            cfg, note=f"tmux {f.get('tmux_session') or '?'} · {len(users)} allowed user(s) · "
                      f"{len(f.get('allowed_group_ids') or [])} group(s)")
    add("Agents Monitoring", "Dashboard login", "password hash", h / ".config/agentsmon/config.json")
    tun = h / ".config/cloudflare/tunnel.env"
    add("Cloudflare tunnel", "Tunnel connector", "tunnel token", tun, keys=env_key_names(tun))
    for cred in sorted((h / ".cloudflared").glob("*.json")):
        add("Cloudflare tunnel", "Tunnel credentials", "credentials file", cred)
    add("Base44 Superagent", "Base44 API", "personal token", h / ".config/agent-party-base44/personal-token")
    add("Git", "Stored git passwords", "credential store", h / ".git-credentials")
    ak = h / ".ssh/authorized_keys"
    if ak.exists():
        fps = []
        for line in _run(["ssh-keygen", "-lf", str(ak)]).splitlines():
            parts = line.split()
            if len(parts) >= 3:
                fps.append(f"{parts[-1].strip('()')} {parts[2] if len(parts) > 3 else ''}".strip())
        add("People", "SSH login keys", "authorized public keys", ak, note=" · ".join(fps) or "no keys")
    known = {i["path"] for i in items}
    for path in sorted(glob.glob(str(h / ".config/*/*"))):
        p = Path(path)
        if _tilde(p) in known or not p.is_file():
            continue
        if re.search(r"(?i)(token|secret|credential|\.env$|api[-_]?key|\.pem$|\.key$)", p.name):
            add("Unclassified", p.name, "possible credential", p,
                keys=env_key_names(p) if p.name.endswith(".env") else [])
    return items


# ---- ecosystem ------------------------------------------------------------------------------
_SKIP_DIRS = {".cache", "node_modules", ".npm", "__pycache__", ".local", "snap", "go", ".rustup", ".cargo",
              ".agentsmon-prod"}


def git_repos(max_depth: int = 3) -> list[dict]:
    h, repos = _home(), []
    for root, dirs, _files in os.walk(h):
        depth = Path(root).relative_to(h).parts
        if ".git" in dirs or ".git" in _files:          # a worktree has a .git file
            repos.append(Path(root))
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and d != ".git" and len(depth) < max_depth]
    out = []
    for r in sorted(repos):
        remote = _run(["git", "-C", str(r), "remote", "get-url", "origin"]).strip()
        remote = remote_metadata(remote)
        last = _run(["git", "-C", str(r), "log", "-1", "--format=%ct"]).strip()
        out.append({"path": _tilde(r), "remote": remote,
                    "branch": _run(["git", "-C", str(r), "rev-parse", "--abbrev-ref", "HEAD"]).strip(),
                    "changes": len(_run(["git", "-C", str(r), "status", "--porcelain"]).splitlines()),
                    "last_commit": int(last) if last.isdigit() else None})
    return out


def ecosystem(listen: list[dict]) -> dict:
    sessions = []
    for line in _run(["tmux", "list-panes", "-a", "-F", "#{session_name}\t#{pane_current_command}\t#{pane_current_path}"]).splitlines():
        name, _, rest = line.partition("\t")
        cmd, _, cwd = rest.partition("\t")
        if name and name not in {s["name"] for s in sessions}:
            sessions.append({"name": name, "command": cmd, "cwd": _tilde(cwd)})
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    units = []
    for line in _run(["systemctl", "--user", "list-units", "--type=service", "--all", "--no-legend", "--plain"], env=env).splitlines():
        cols = line.split(None, 4)
        if len(cols) >= 4 and not cols[0].startswith(("dbus", "systemd-", "dirmngr", "gpg-agent", "pk-debconf", "snapd.")):
            units.append({"name": cols[0], "active": cols[2], "sub": cols[3],
                          "description": cols[4] if len(cols) > 4 else ""})
    # A oneshot started by a timer is normally inactive between runs; say so instead of warning.
    timed = set()
    for line in _run(["systemctl", "--user", "list-timers", "--all", "--no-legend", "--plain"], env=env).splitlines():
        timed.update(w for w in line.split() if w.endswith(".service"))
    for u in units:
        u["timer"] = u["name"] in timed
    cron = [cron_metadata(l) for l in _run(["crontab", "-l"]).splitlines()
            if l.strip() and not l.lstrip().startswith("#")]
    return {"sessions": sessions, "services": units, "cron": cron, "listeners": listen,
            "repositories": git_repos()}


# ---- snapshot -------------------------------------------------------------------------------
def collect(root: Path = Path("/")) -> dict:
    started = time.time()
    listen = listeners()
    creds = access_inventory()
    checks = security_checks(sshd_effective(root), listen, creds, root)
    counts = {s: sum(1 for c in checks if c["status"] == s) for s in ("risk", "warn", "ok", "unknown")}
    return {"generated_at": int(time.time()), "duration_ms": int((time.time() - started) * 1000),
            "host": socket.gethostname(), "user": os.environ.get("USER") or str(os.getuid()),
            "security": {"checks": checks, "counts": counts},
            "access": creds, "ecosystem": ecosystem(listen or []),
            "limits": ["Runs without root: firewall rules, auth logs and root-owned services are not inspected.",
                       "Secret values are excluded from results; config files are parsed locally for selected metadata.",
                       "Non-loopback listeners are not proof of external reachability; firewall rules are not verified.",
                       "SSH checks parse global file defaults; Match rules and the running daemon configuration are not verified.",
                       "A running process proves liveness, not correct behaviour or delivery."]}


def snapshot(refresh: bool = False) -> dict:
    """Cached snapshot; a refresh is honoured at most every MIN_RESCAN_SECONDS."""
    with _lock:
        age = time.time() - _cache["at"]
        if _cache["data"] is None or age > CACHE_SECONDS or (refresh and age > MIN_RESCAN_SECONDS):
            _cache["data"] = collect()
            _cache["at"] = time.time()
        return _cache["data"]
