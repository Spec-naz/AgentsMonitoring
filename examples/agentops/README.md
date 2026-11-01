# AgentOps reference — 0.2.2

A small, dependency-free operational layer alongside AgentsMonitoring. It records
explicit tasks and handoff acceptance, independent review, append-only events,
declared progress, recorded costs and shared-account access limitations. It does
not execute tasks, change permissions, press terminal keys or retry paid actions.

The first supported runtime is Python 3.10+ on Linux, with a local SQLite database.
Do not put a live WAL database on a shared network filesystem. Native Windows,
macOS, alternate CPU architectures and complete host failover are not certified.

```bash
python3 -m agentops init
python3 -m agentops create --title 'Review a change' --owner kodak --actor kodak --request-id request-123
python3 -m agentops start TASK_ID --actor kodak --expected-version 1
python3 -m agentops progress TASK_ID --actor kodak --note 'Tests passed; evidence in Git'
python3 -m agentops offer TASK_ID --actor kodak --to klodak --purpose review --artifact git:COMMIT_SHA
python3 -m agentops accept TASK_ID --actor klodak
python3 -m agentops done TASK_ID --actor klodak --artifact review:REPORT_REFERENCE
python3 -m agentops history TASK_ID
```

Until `accept`, the sender remains responsible and the receiver is only a pending
target. Nobody may self-handoff. `done` requires review state, another declared
actor and evidence. `withdraw` cancels an unaccepted offer into blocked state;
`offer --purpose work` sends corrections back for explicit acceptance and start.
State changes are serialized in `BEGIN IMMEDIATE`; `--expected-version` prevents
stale clients, and `--request-id` deduplicates repeated commands. Use stable request
IDs in automation. A successful CLI invocation is not delivery to a colleague;
the colleague must actually run `accept`.

Actor labels are NOT authenticated identities. Shared Unix users can impersonate
each other or replace the database. Append-only triggers support accident-resistant
history, not tamper-proof evidence. A genuine security boundary requires separate
identities and provider-scoped credentials; see [continuity design](docs/continuity-of-system.md).

`progress` is an explicit evidence-bearing report. The snapshot timer never sends
fake progress heartbeats. An overdue update is a hint, not proof of a hang. The
fallback modal detector captures only explicitly configured tmux panes in memory,
stores pattern IDs only, requires two observations before flagging input-waiting,
and never sends keystrokes. Unknown/idle-looking terminal content stays unclassified.
Provider hooks are a stronger, separately labelled source of turn events. Exact
session binding and freshness checks prevent another session or stale state from
being treated as the current actor. Hooks never refresh declared task progress or
complete tasks. The default plan does not edit provider configuration; see
[provider signals](docs/provider-signals.md) for reviewed installation/trust steps.

Runtime state defaults to `~/.local/state/agentops/` and declarations to
`~/.config/agentops/config.json`. Both remain outside Git. Avoid secrets, transcript
contents and private signed URLs in task titles, notes, evidence or cost sources.
The read-only plugin exposes titles, actor labels, state and summary timestamps;
it omits evidence and note contents. The ledger itself contains private evidence.

Costs are manual observations, with `actual` and `estimate` and each currency kept
separate. An absent record means unknown, never zero total spending. The CLI accepts
up to six decimal places. For example, in a disposable fixture database only:

```bash
python3 -m agentops --db /tmp/demo/tasks.sqlite create --title Demo --owner demo --actor demo
python3 -m agentops --db /tmp/demo/tasks.sqlite cost TASK_ID --actor demo --amount 0.012345 --currency USD --kind estimate --source fixture
```

## Backup and isolated restore rehearsal

```bash
# Default is inventory only: no source contents read or archive created.
python3 -m agentops backup --root code=/absolute/reviewed/project --output /private/backups/agentops.zip
# Explicit export: excludes credential/config/auth-like paths; online SQLite backup.
python3 -m agentops backup --root code=/absolute/reviewed/project --output /private/backups/agentops.zip --execute
# Writes only into an EMPTY staging directory; starts no process or network client.
python3 -m agentops rehearse --archive /private/backups/agentops.zip --staging /private/empty-restore
```

The source roots must be explicitly reviewed. Filename filtering cannot prove an
arbitrary source file contains no secrets. In particular, task history, code or
artifacts can contain sensitive data; treat every archive as private. Secret opt-in
fails closed. Optional [age-encrypted data export and rehearsal](docs/encrypted-continuity.md)
use an independently verified age executable and an explicit public recipient;
encrypted identity bundles and off-host storage remain a separate operator workflow.
The export returns an archive SHA256 for independent recording;
internal hashes alone do not authenticate an attacker-replaced archive. Missing
paths and excluded items are disclosed rather than counted as recovered.

The rehearsal verifies archive paths/types/hashes, restores SQLite and checks its
integrity. It does NOT recreate OAuth sessions, Cloudflare/Telegram ownership,
service startup or a functioning alternative server. See the cutover checklist.

## Existing dashboard plugin

`monitor/agentsmon-plugin.json` implements Plugin API v1. Install a directory link
under the configured AgentsMonitoring modules directory. Produce snapshots with
`python3 -m agentops snapshot` once a minute. Reload only the dashboard to discover
the manifest; no core or visual files are changed. Snapshots older than three
minutes hide old rows and display an error. No actions are exposed in the browser.

Work queue shows up to 100 active tasks, newest declared update first: queued,
running, blocked, awaiting handoff acceptance and in review. Reviewed/completed
(`done`) and cancelled tasks are omitted from this view. They remain in the
ledger, task history and snapshots. These are declared workflow states, not
measurements of whether an agent is currently executing work.

## Validation

```bash
python3 -m unittest discover -s tests -v
python3 -m pyflakes agentops monitor tests
```

Tests use isolated fixture directories, not production credentials or services.
Optional renderer acceptance: `tests/verify_monitor_browser.py --agentsmon PATH
--out ARTIFACT_DIRECTORY` (Playwright installed separately).

### On-demand worker visibility

The optional [Task workers plugin](docs/worker-monitoring.md) shows explicitly
registered workers independently of persistent sessions and the task snapshot.
It makes no model/auth calls and never starts or restarts a worker.

### Offline regression cases

`agentops regress template/run/compare` evaluates explicit synthetic observations and
compares per-case results using the same full suite/oracle contract. It never invokes
agents or external actions and never authorizes deployment. See the runnable
[LC-06 workflow](docs/regression-workflow.md) and `examples/regression/`.
