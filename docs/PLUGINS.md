# Plugin API v1

The public package owns rendering, authentication, HTTP routing and process supervision.
Private integrations live in separate repositories. Never edit `agentsmon/` to add an
organization's agent, path, credential, detector or action.

## Installation and lifecycle

`modules_dir` defaults to `~/.config/agentsmon/modules`. Two manifest locations are supported:

- `<modules_dir>/<extension>/agentsmon-plugin.json` with Python files alongside it.
- `<modules_dir>/<extension>.agentsmon.json` with a module relative to that manifest.

A symlink from `modules_dir` to a private plugin **directory** is supported. Use directories
for new plugins: executable helpers placed at the top level are legacy service checks.
Restart the dashboard after adding/changing a manifest or entry-point installation. Python
code is loaded in a fresh worker for each check. API v1 intentionally has no hot installer.
Disabling a plugin means removing its directory link/config opt-in and restarting the
dashboard, without changing core. These are administrator operations, not agent actions.

```json
{
  "api_version": 1,
  "id": "example",
  "version": "1.0.0",
  "module": "plugin.py",
  "timeout_seconds": 3,
  "action_timeout_seconds": 210,
  "settings": {"snapshot_path": "~/.local/state/example/status.json"}
}
```

IDs use `[a-z][a-z0-9_-]{0,63}`. Versions are display strings (maximum 80 characters).
`collect` timeout range is 0.1–10 seconds; action timeout is 0.1–300 seconds.
Duplicate plugin IDs are rejected, including collisions with built-in plugin IDs.
At most 64 file manifests are loaded; excess manifests produce a visible discovery error.

## Contract

```python
def collect(context, settings):
    return {
        "tables": [{
            "id": "workers", "title": "Example workers",
            "rows": [{"id": "one", "name": "Example", "alive": True,
                      "label": "Model", "vendor": "openai", "age": 120,
                      "session_id": "example-session", "actions_locked": True}]
        }],
        "hidden_sessions": [],
        "hidden_services": []
    }
```

`context` contains `api_version` and `agents`, a public projection of core rows:
`id, name, kind, label, vendor, alive, session_id, age, port, latency_ms, tmux_session,
account`. Core row ID is its configured/discovered name. No restart commands, auth
configuration, full config or inherited credential environment variables are passed.
`settings` is only this plugin's manifest settings. It must not contain dashboard auth.

Every response is JSON-serializable. Optional response fields:

- `tables`: up to 16 tables, each with `id`, `title`, `rows` (up to 200).
  Optional `up` can make the table unhealthy even when its rows are alive; `error` also
  forces unhealthy status. `note` provides a concise explanation. Table IDs are namespaced
  to `plugin:table` by core. `rows` require unique `id`, `name` and boolean `alive`.
- Rows may add the public fields above plus `status`, `status_detail` (tooltip), `name_color`,
  `metadata_error`, `telegram_bot`, `telegram_url`, `cells`, `row_actions`, `actions_locked`.
  Telegram URLs must be `https://t.me/<bot>`. Metrics are finite, nonnegative numbers or null.
  `age` is seconds. Most text fields are limited to 500 characters and reject control codes.
- `columns`: up to eight `{id, title}` definitions; appended consistently to all agent tables
  before Actions. IDs become `plugin:column`.
- `patches`: up to 200 display-only `{row_id, label?, vendor?, account?, name_color?, cells?}`.
  Core target: exact row name. Plugin target: `plugin:table:row`. Used for model/account
  detectors or usage columns. `account` appears in the model's tooltip. Cannot alter liveness,
  session identity or core actions.
- `cells`: map of column ID to string or `{text, title?, tone?, bars?, quota_group?}`. Tone is `muted`,
  `ok`, `warning` or `error`; at most four bars `{label, percent, title?, resets_at?}`, percent 0–100.
- `hidden_sessions`: up to 200 exact tmux names to hide from the core table.
  Generic config `hidden_sessions` remains available and supports its existing patterns.
- `hidden_services`: exact service names whose duplicate display should be suppressed.
  The legacy check still runs and maintains its snapshot/history. Use cautiously.

Tables and rows share the persistent table renderer and column geometry. Arbitrary HTML,
JavaScript, CSS, URLs or client callbacks are not an extension mechanism. Text is escaped.
Patch ordering is deterministic (manifest discovery order, then opt-in entry points and
built-in Usage); for one display field, the last explicit patch wins.

## Actions

Advertise only actions the plugin implements:

```python
# In a row:
row_actions = [{"id": "reload", "label": "Reload when idle", "icon": "restart",
                "confirm": "Reload with the same session?"}]

def action(request, settings):
    # Validate registry, ownership, allowed instance and idleness again here.
    # Use a fixed argv list, never interpolate a browser-provided name into a shell.
    if request != {"table_id": "workers", "row_id": "one", "action_id": "reload"}:
        return {"ok": False, "message": "Unknown target"}
    return {"ok": False, "message": "Example only; nothing changed"}
```

Browser POST `/api/agent/action` carries `{table, row, action}`. Core enforces the existing
HTTP Basic auth (if configured), mandatory `X-Agentsmon` cross-site guard, bounded body,
namespace and a fresh `collect` result. Only an action advertised by that specific row can
run. No fallback to core restart/stop, no client-provided shell command. One action at a time
per plugin. Workers return `{ok: boolean, message: string}`. After failure/timeout, the UI
instructs verification before another attempt; timeouts are ambiguous and never auto-retried.
`icon` is `restart`, `stop` or `run`; `actions_locked` shows a lock for remaining restrictions.
Custom actions have exactly the dashboard's operator authority; no extra authorization is
inferred. Plugins must enforce their own lifecycle/idle checks server-side.

## Failure isolation and trust boundary

Collection runs off the HTTP thread, concurrently in up to four workers. A cached result
refreshes after ten seconds, and after thirty seconds unrefreshed rows and actions disappear
in favor of a neutral loading notice (`plugin_loading` in `/api/state`). Loading is separate
from `plugin_errors`; it does not report a failed check. A crashed/timed-out plugin creates
a red error card after its bounded check completes; other results
continue. Each worker is a subprocess with a separate process group, bounded time and
1 MiB stdin/stdout, with descendant cleanup. Arbitrary plugin output/stderr is not logged.
Each action uses another bounded worker. Unknown API versions fail visibly.

**Plugins are trusted locally installed Python code running as the same OS user.** The API
does not hand out core auth, but is not a filesystem/security sandbox against malicious
code: a plugin could independently read files accessible to that user. Install only reviewed
plugins. Protect settings, snapshots and action registries with filesystem permissions.

## Optional Python entry points

Nothing installed is auto-enabled. Configure `"plugin_entry_points": ["example"]` and
publish entry point group `agentsmon.plugins`, name `example`, value `my_package.plugin`
(or `my_package:provider` with `collect`/`action` methods). Include a distribution metadata
file `agentsmon-plugins.json`:

```json
{"example": {"api_version": 1, "id": "example", "version": "1.0.0", "settings": {}}}
```

Discovery reads distribution metadata, without importing third-party code into the server.
Missing, ambiguous or incompatible entry points produce visible errors. The enabled package
must be installed in the interpreter running the dashboard (not only a user-site directory,
since workers use isolated Python mode).

## Built-in opt-in Usage plugin

Inspired by TomasErnest's PR #8. Reads local rate-limit snapshots, **never** OAuth tokens.
No outbound network or account guessing. Configure explicit row IDs and source files:

```json
{
  "usage_plugin": {
    "sources": {
      "work-claude": {"format": "claude-statusline", "path": "/path/to/statusline.json", "account": "Work"},
      "example:workers:one": {"format": "codex-rollout", "path": "/path/to/rollout.jsonl", "account": "Team"}
    }
  }
}
```

Supported formats: Claude statusline JSON with `rate_limits.five_hour/seven_day`, Codex
rollout JSONL `payload.rate_limits.primary/secondary`, manually supplied ChatGPT-compatible
snapshot `rate_limit.primary_window/secondary_window`. Reads at most 2 MB (tail for rollout).
No producer is installed automatically: reuse an existing statusline/usage capture, or have
your reviewed integration atomically write its snapshot. A missing source displays
`Unavailable`; stale (over ten minutes) shows `Cached`; expired windows show no current
reading. Reported window lengths take precedence over fallback labels. Percent is quota
used, not dollars or token cost. Core includes no auto-fetch from private account APIs.

## Tests

`python3 -m unittest discover -s tests -v` includes subprocess isolation, slow/crashed workers,
API/version/entry-point failures, scoped actions, actual HTTP authentication/guard, usage
account separation, integrity and a clean-HOME `install.sh --no-setup --no-pip` launch.
Optional real browser: `python3 tests/verify_plugins_browser.py --browser /path/to/chrome
--out /tmp/browser-evidence` (Playwright installed separately). It uses local fixtures only.


### Remaining quota panel

The built-in `usage:limits` column alone feeds the remaining-capacity panel; arbitrary
plugin bars are not subscription quotas. Optional `resets_at` is finite nonnegative
epoch seconds. Optional `quota_group` must match `source-` plus64lowercase hex digits.
The usage adapter derives it only from an exact operator-declared source path,
format and account label. Only identical entire readings with the same key combine.
This is display deduplication, not account authentication or a financial ledger.
The hash does not expose the raw path, but is not an anonymization guarantee.
Antigravity pool labels never infer a shared account; distinct readings stay separate.
Cached-prefix metadata stays last-known, unknown readings never invent capacity,
and the panel labels100-used as remaining without summing shared limits.
