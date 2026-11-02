# Metadata, export and retention (LC-07)

## Why this is an allowlist

Records keep only named fields in a fixed format. Free text is left out, not cleaned. **This is not recognition of
sensitive data:** an allowed field can still hold whatever its format permits, and a person can put a secret into a task
title or note, which is why those stay out of telemetry and exports. Prompts, responses, terminal content, credentials
and error text are not stored by default. There is no switch that turns them on. AgentOps has no debug log.

## What exists and where

| Record | Where | Content | Class |
|---|---|---|---|
| Provider session | `provider-signals/<provider>/<hash>.json` | `SNAPSHOT_FIELDS` in `provider_signals.py`; reading drops anything else | auxiliary, can expire |
| Rejection counters | `provider-signals/rejected.json` | Fixed reasons and counts; one bounded file | auxiliary, kept |
| Dashboard snapshot | `snapshot.json` | Overwritten each run. **Holds declared text:** titles, block reasons, output paths | auxiliary, replaced |
| Ledger | `tasks.sqlite` | Tasks, events and run events, append-only. **Holds declared text:** titles, notes, paths | critical, never expires |
| Result check and regression reports | path chosen by the caller | Paths, hashes, names of unexpected files, effect notes; no file content | critical evidence, never expires |
| Backups | path chosen by the caller | Selected files and the ledger; excludes are filename-based | out of scope, unchanged |

Declared text in the ledger and snapshot is written by people and agents on purpose. It is private to the account and
unchanged here. It is kept out of new telemetry and exports, not scanned.

## Allowed fields


### Exported task (`export_task`)

| Field | Purpose |
|---|---|
| `id` | Link to the task and its events; a random identifier. |
| `owner` | Who holds the task; a declared label. |
| `state` | Declared workflow state. |
| `phase` | Work or review. |
| `target` | Who a handover waits for. |
| `purpose` | Why it was handed over. |
| `producer` | Author, for the independent review rule. |
| `created` | Timing. |
| `updated` | Timing. |
| `progress` | Last declared progress; null means none was recorded. |
| `version` | Revision, to detect changes. |
| `has_artifact` | Whether a result is linked, without saying where. |
| `artifact` | Only with --include-references: local path of the result. |

### Exported audit event (`export_event`)

| Field | Purpose |
|---|---|
| `seq` | Complete, gap-checkable audit order. |
| `task_id` | Link to the task. |
| `at` | Timing. |
| `actor` | Who recorded it; a declared label. |
| `action` | What kind of step it was. |
| `from_state` | State before. |
| `to_state` | State after. |
| `target` | Addressee of a handover. |
| `purpose` | Purpose of a handover. |
| `version` | Task revision after the step. |
| `verdict` | Result check outcome. |
| `report_sha256` | Identifies the report without its content. |
| `report` | Only with --include-references: local path of the report. |
| `details_unreadable` | The stored details could not be read; nothing was guessed. |

### Exported session (`export_session`)

| Field | Purpose |
|---|---|
| `provider` | Which CLI. |
| `session_ref` | Stable reference, a hash of the session id. |
| `actor` | Declared agent; not authenticated. |
| `turn` | Turn state, not task progress. |
| `last_event` | Last lifecycle event. |
| `last_event_at` | Timing. |
| `events` | Number of hook events seen. |

### Exported recorded cost (`export_cost`)

| Field | Purpose |
|---|---|
| `currency` | Currency of recorded amounts. |
| `kind` | Whether it was measured. |
| `micros` | Sum of recorded entries only. |
| `records` | How many entries; everything else is unknown, not zero. |

### Run event (written by `agentops run-event`) (`run_event`)

| Field | Purpose |
|---|---|
| `task_id` | The declared task. A task is not a run. |
| `run_id` | One concrete execution. |
| `parent_id` | The run that started this one; null for a root run. |
| `at` | Timing. |
| `source` | What wrote the record. |
| `evidence` | Declared by an actor, or observed by a mechanism. |
| `actor` | Declared label; not authenticated. |
| `action` | Kind of step. |
| `outcome` | Result; unknown stays unknown. |
| `error_code` | A code, never error text. |
| `usage_tokens` | Null is unknown usage, never zero. |

A task is the declared piece of work and a run is one concrete execution, so `run_event` keeps them apart. It must state
every field, and unknown is an explicit `null`: `usage_tokens: null` is unknown usage, never zero. `evidence` separates
what someone declared from what a mechanism observed. `error_code` is a code, never text.

## Export

```sh
agentops metadata-export --output "$HOME/.local/state/agentops/exports/run-1.json"
```

The file is private (0600) and is never overwritten. Every audit event is exported, because audit is never sampled. A
stored value that does not fit is dropped and counted in `dropped_values`, not repaired. `--include-references` adds local
paths of results and reports; a value that does not look like a plain local path, or lies in an excluded location, is
withheld and counted. **Identifiers it keeps:** task ids, actor labels, a hash of each session id. Times and the pattern of
work are visible, so treat the file as private.

## Retention

| Class | Limit | Why |
|---|---|---|
| Provider sessions | 14 days by default; `retention.provider_signal_days` in the config, 7 to 365 | Useful only for the current state. A session already reads as stale after 7 days. |
| Ledger, reports, backups, deployment records | never expire here | Evidence of critical actions; ledger events are append-only |

```sh
agentops retention plan                                    # read-only, the default
agentops retention apply --signals-dir DIR --plan-sha256 H --output RECEIPT
```

`apply` removes exactly what the plan listed, rechecks each file under a lock, and fails if the plan no longer matches.
The receipt is created exclusively **before the first removal**. A receipt path it cannot use stops everything. A file that cannot
be removed is recorded with its reason. If the final receipt cannot be written, the `started` record still lists what was planned,
though not what was already removed, so compare it with the folder. These commands print fixed messages, never a raw file error.
**In this version it refuses the production folder and nothing schedules it.** Pruning production needs a separate decision.
A malformed, foreign, symlinked or future-dated file is reported and kept. An age exactly at the limit is kept. The ledger is
reported only as sizes and counts, so growth stays visible.

## Limits

- Nothing removes old declared text, and the ledger only grows. Its growth is reported, not limited.
- `session_ref` is a hash, not anonymisation: anyone with a session id can compute it.
- The plan hash binds the expired list, so a file expiring after the plan was made means making a new plan.
- Filename excludes in backups remain a guardrail, not proof.

