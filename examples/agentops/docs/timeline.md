# Task timeline (LC-02)

```sh
agentops timeline TASK
agentops run-event TASK --actor klodak --action start --source operator --evidence declared [--run-id ID] [--parent-id ID] [--outcome ok|failed|unknown] [--error-code CODE] [--usage-tokens N]
```

`timeline` reads only. It uses the existing ledger, so there is no second task database. `run-event` adds one step of a concrete run and
changes no task state. Run events are append-only and keep to the `run_event` allowlist in [metadata policy](metadata-policy.md).

## What separates what

| Thing | Meaning |
|---|---|
| Task and run | A task is the declared work. A run is one concrete execution, with its own `run_id`. `parent_id` links a child run. |
| Declared and observed | Every ledger entry is `declared`. A run says which it is, and a run with any declared event reads as `declared`. |
| Assigned and executed | A start in the ledger is a statement. `execution.observed_runs` counts only runs marked observed. |
| Unknown usage | `known_tokens` sums recorded values and is `null` when none exist. `runs_with_unknown_usage` is counted separately. Unknown is never zero. |
| Provider signal | `current`, `stale`, `ambiguous` or `not_assigned`. It describes a turn of an actor, not this task, and it never completes anything. |
| Active and history | `belongs_to` is `history` for a done or cancelled task, which also leaves the queue. |

## Run identity

A run belongs to one task and has one parent. The writer refuses a run as its own parent, a changed parent, a run id already under another
task, and a parent chain that leads back to the run. A parent not recorded yet is allowed. Records that break this anyway, from a direct edit, read as
`relation: invalid` with a problem code, never as a coherent relation. Repeating `run-event` with the same `--request-id` and the same content
returns the original event, with its original time and run id, and stores one row. Different content under that id fails. This is about the record only.

## Problems on record

Codes point to an entry: `returned_after_review`, `declared_blocked`, `handover_withdrawn`, `result_check_fail`, `result_check_unverified`,
`report_missing`, `report_differs`, `run_failed`, `run_outcome_unknown`, `parent_run_not_recorded`, `run_parent_changed`, `run_parent_is_itself`,
`run_id_in_other_task`, `audit_details_unreadable`.
They stay after being resolved, because this is history and not current state. A run that failed or is unknown says `repeat_offered: false`:
the timeline never suggests repeating an outside action.

## What it does not do

- **It does not show titles, notes, prompts, responses or error text.** `agentops history` still shows notes to those with ledger access.
  A plain local path is shown as a reference to copy; anything else is withheld. Files are not served.
- **It does not invent run identity.** A task from before run events has none, and old tasks stay without runs.
- **It does not prove identity or truth.** Actors are labels, and anyone with ledger access can write an event marked observed. That mark is a
  statement about how it was recorded, and it guards against mistakes only.
- **Nothing writes run events automatically yet.** Until a caller records them, tasks show `runs_recorded: 0`.
- **A report check says only that the file still has the recorded hash**, not who produced it.

The dashboard queue links the timeline and shows runs as an evidence row. There is no new page and no new action.
