# Provider signals (Claude Code and Codex hooks)

`agentops/provider_signals.py` turns provider lifecycle hooks into a small,
private per-session snapshot and classifies the **provider turn** of an agent.
It never says anything about task progress (`proves_task_progress: false`).

## Verified contracts (2026-10-31)

| | Claude Code 2.1.316 | Codex CLI 0.166.0 |
|---|---|---|
| Source | code.claude.com/docs/en/hooks | learn.chatgpt.com/docs/hooks (redirect from developers.openai.com/codex/hooks), event names cross-checked in the 0.166.0 binary |
| Config | `~/.claude/settings.json` → `hooks.<Event>[] = {matcher?, hooks:[{type:"command", command, timeout, async?}]}`; all settings levels merge | `~/.codex/hooks.json` (same shape) or inline `[[hooks.<Event>]]` in `config.toml`; all layers merge |
| Enabled | always | by default (`[features] hooks = false` disables) |
| Trust | none | **every non-managed hook must be trusted in `/hooks`**; trust is bound to the hook hash, a changed hook is skipped until re-trusted |
| stdin | one JSON object | one JSON object |
| Common fields used | `session_id`, `hook_event_name`, `agent_id` (subagent marker), `notification_type` | `session_id`, `hook_event_name` |
| Events used | `SessionStart`, `UserPromptSubmit`, `Stop`, `StopFailure`, `Notification` (`permission_prompt`, `idle_prompt`, `elicitation_dialog`, `elicitation_url_dialog`, `agent_needs_input`), `SessionEnd`, optional `PostToolUse` | `SessionStart`, `UserPromptSubmit`, `Stop`, `Interrupt`, `PermissionRequest`, `SessionEnd`, optional `PostToolUse` |
| Gaps | `Stop` **does not fire on an Esc interrupt**; API errors fire `StopFailure` instead | **no Notification/idle event**; `PermissionRequest` fires before an approval prompt but another hook may still decide it, and it never fires when approvals are bypassed; `SessionEnd.reason` is always `other`; `Interrupt`/`SessionEnd` timeout 1–3 s |
| stdout | plain text on `UserPromptSubmit`/`SessionStart` is injected into context → we print nothing | `Stop` expects JSON → we print `{}`; elsewhere nothing (plain text would become context or be invalid) |

Not used on purpose: `PreToolUse`, Claude `PermissionRequest` (decision hooks), Codex legacy `notify`
(not configured on this host; its payload carries the last assistant message).

## What is stored

Allowlist only: `provider`, `event` (Notification as `Notification:<type>` from a fixed list),
`session_id` (validated `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`), declared `actor` from
`AGENTOPS_ACTOR` (validated, **not authenticated**), observation times and counters.
Never stored: prompt, transcript or its path, `last_assistant_message`, tool names/args,
messages/titles, `cwd`, `turn_id`, model, background tasks, error text.

Files: `~/.local/state/agentops/provider-signals/<provider>/<sha256(provider,session)[:32]>.json`,
directories `0700`, files `0600`, atomic write (tempfile + fsync + rename) under a per-provider
`flock`. Rejected payloads only increment counters in `rejected.json`
(`oversize`, `not_json`, `not_object`, `bad_session`, `unknown_event`, `subagent_turn`).

## States

| state | meaning | confidence |
|---|---|---|
| `turn_running` | prompt submitted, no end signal yet | confirmed start; end may be missing |
| `waiting_input` | permission/elicitation prompt (Claude) or approval request (Codex) after the last turn start | `confirmed` (Claude) / `probable` (Codex) |
| `idle` | turn ended (`Stop`, Codex `Interrupt`, Claude `idle_prompt`) or session started | confirmed |
| `failed` | Claude `StopFailure` (API error) | confirmed |
| `ended` | `SessionEnd` | confirmed |
| `unknown` | everything else, with a `reason` | — |

`unknown` reasons: `no_signal`, `no_bound_session`, `invalid_binding`, `ambiguous_binding`
(several live sessions for one agent), `running_without_end_signal` (older than
`running_max_age`, default 2 h — covers Esc), `waiting_signal_stale` (> 24 h),
`signal_stale` (> 7 d), `clock_skew` (event > 60 s in the future), `no_turn_signal`.

Out-of-order delivery (parallel hooks) never regresses state: an event older than the last
applied one only increments the counter. Subagent `Stop`/`UserPromptSubmit` (with `agent_id`)
are ignored; subagent permission prompts still count as waiting.

Without the optional `PostToolUse` hook, a Claude `waiting_input` stays until the turn ends,
even after the user approved. With it (`async: true`), the next tool run clears waiting.
Under bypass permissions both providers rarely ask at all.

## Python API

```python
from agentops import provider_signals as ps
ps.ingest(provider, raw_bytes, state_dir=..., now=None, actor=None) -> snapshot | None
ps.read_sessions(state_dir=..., provider=None) -> [snapshot]
ps.classify(snapshot, now=None, limits=None) -> signal
ps.agent_signal(agent, provider, state_dir=..., now=None, session_id=None, limits=None) -> signal
ps.prune(state_dir=..., now=None, max_age=14 days) -> int
ps.plan_install(provider, config_path=None, command=None, include_activity=False) -> (merged_config, plan)
ps.hook_command(provider, python='/usr/bin/python3', package_root=None, state_dir=None) -> str
```

`signal` keys: `state`, `reason`, `source='provider-hook'`, `proves_task_progress=False`,
`provider`, `session_id`, `actor`, `last_event`, `last_event_at`, `since`, `age_seconds`,
`confidence`. `limits` overrides `running_max_age`, `waiting_max_age`, `idle_max_age`, `future_skew`.

## CLI (standalone, not wired into `agentops` yet)

```
python3 -I agentops/provider_signals.py ingest --provider claude|codex [--state-dir D]   # hook entry, stdin
python3 -I agentops/provider_signals.py status [--agent A --provider P] [--state-dir D]
python3 -I agentops/provider_signals.py plan-install --provider P [--config F] [--package-root R]
                                                     [--state-dir D] [--include-activity]
```

With valid hook arguments, `ingest` converts observation failures into exit 0 and emits no diagnostic content.
The provider enforces the configured timeout; metadata I/O may still delay a callback within that bound.
`plan-install` is read-only: it prints which handlers would be appended and how many existing
handlers per event are preserved. Full existing settings are deliberately never printed,
because they may include environment credentials. There is no `--apply`.

The hook command runs the file as an isolated script (`python3 -I <root>/agentops/provider_signals.py`),
so the agent's cwd, `PYTHONPATH` and user site are not searched. Pass `--package-root` of the
**production** release; the default is the checkout the plan was generated from.

## Install (manual, after review)

1. Add `AGENTOPS_ACTOR=<agent>` to each agent launcher (otherwise sessions stay unbound → `unknown`).
2. `plan-install --provider claude --package-root <release>`, review the metadata plan, then edit
   `~/.claude/settings.json` by hand. The existing Telegram `Stop` hook stays untouched next to ours.
3. Codex: same for `~/.codex/hooks.json` (file does not exist yet), then trust the hooks in `/hooks`.
4. Check with `status`. Remove by deleting handlers whose command contains `provider_signals`.

## Limits

Child-agent events are deliberately excluded from the main-session observer. Otherwise
a child tool call could incorrectly clear a main approval prompt. Separate child-session
coverage remains future work. Hook events never complete tasks or refresh declared progress.
Integration requires an exact session binding and defaults to 15-minute freshness; stale,
ambiguous or mismatched observations remain unknown. A declared actor is not authentication.

- The actor is a declaration from the environment; under one Unix account anything can write the
  state directory. These are hints for a dashboard, not evidence.
- A long turn over `running_max_age` is reported as `unknown`, not as stuck.
- `turn_running` means a provider turn is open, not that the task advances.
- Codex behaviour was verified from docs and binary strings, not by a live trusted hook run.
