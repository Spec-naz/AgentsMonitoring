# AgentSandbox 0.1 — offline experiments on the existing VPS

Created 2026-10-31. Uses existing bubblewrap and unprivileged Linux namespaces;
no root installation, production service restart or external subscription.

## Run an experiment

Create a new owned mode0700 directory under `~/.local/state/agent-sandbox/`.
Use `python3 sandbox.py --workspace /absolute/private/experiment --code /reviewed/code -- /usr/bin/python3 script.py`.
Inside, `/workspace` is writable and `/app` is the selected read-only code.
HOME is empty `/home/sandbox`. Internet, host HOME, provider credentials,
Telegram/Cloudflare sockets and host process namespace are not exposed.
Only `/usr` and its library symlinks are shared read-only. `/tmp` is temporary.
No production bots, agents, timers or paid API calls are configured.

Limits: 90s default wall timeout (max300), 60 CPU seconds per process,
768MiB address space per process, 128MiB per regular file, 128 open descriptors,
no core dumps. These are NOT aggregate cgroup CPU/memory/process/disk quotas.
Workspaces persist and consume the production host disk. Use for reviewed
experiments, not hostile code. Shared kernel/hardware remains a common failure
point; this is not a VM, another host, disaster recovery or a full security audit.
No browser port is published. Do not add production tokens or network to defaults.

## Restore rehearsal observed

Private evidence: `~/.local/state/agent-sandbox/rehearsal/result.json`.
`check_restore.py` is the reproducible in-sandbox check, expecting `input.zip`,
`expected.json`, separate `runtime/agentsmon` and a nonexistent `restored` target.
Fresh online SQLite snapshot plus reviewed release data: 110 files restored,
archive hash verified, SQLite integrity passed, internet denied, host HOME and
sockets hidden. Dashboard fresh-config HTTP200 inside private network namespace.
No production consumers started and no external login/failover tested.

The complete dashboard runtime was staged separately from its immutable release:
current data filename exclusions omit source modules such as config.py and
identity.py. Thus this does not claim the selected-data ZIP independently restores
a complete runnable installation. Code release + data + separately recovered
identities are distinct required inputs. Existing OneDrive ciphertext was restored
on the user's Mac; server rehearsal uses a fresh local data snapshot and does
not access the user's private recovery key.

Rollback: no service changes to roll back. Stop a running experiment (Ctrl-C),
retain evidence, then remove only its dedicated workspace when no longer needed.
Never commit production archives, SQLite data, restored content or credentials.
