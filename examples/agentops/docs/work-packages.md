# Work packages (LC-04)

Available in AgentOps 0.2.8, alongside the metadata export and retention controls from 0.2.7.

A package gives one worker what one task needs: goal, sources with their origin, constraints, the financial frame, owner and
reviewer, output and end condition. It is a file. **It is not a task manager or an agent framework:** it runs nothing, assigns
nothing and holds no state. AgentOps stays the authority, and a handover can point to a package with `offer --artifact`.

```sh
agentops package build --draft draft.json --output package.json [--base FOLDER]
agentops package check package.json [--with-task]    # exit 0 ready, 2 not ready, 1 invalid format
agentops package render package.json                 # the text a worker gets
agentops package compare package.json --broad FILE... [--facts facts.json]
```

A synthetic draft is in `examples/work-packages/`.

## Format (`agentops-work-package-v1`)

| Field | Meaning |
|---|---|
| `package_id`, `version` | Identity. A changed source means a new version, not an edited file. |
| `task.task_id` | The AgentOps task. |
| `goal` | What should be true at the end. |
| `roles.owner`, `roles.reviewer` | Must differ. |
| `financial` | `scope`, `new_paid_usage` and `on_limit: stop_and_report`. Paid use needs `approval_ref` and still reads as not ready, for a person to decide. |
| `constraints` | At least one, each with an id. |
| `sources[]` | `path`, `origin` (kind and reference), `why`, `load`, optional `lines`, and the real `sha256` and `bytes`. |
| `output` | What to deliver and where. |
| `done_when[]` | Conditions, each with the evidence that proves it. At least one. |
| `out_of_scope`, `open_questions` | What stays out, and what is unclear. A blocking question means not ready. |
| `budget_bytes` | Upper limit of what is handed over. |

`load: required` is read by the worker and counted. `load: on_demand` is named but not handed over, which is how expert instructions
are loaded only when a task needs them.

## Not ready is never smoothed over

`build` keeps a source it cannot read as visibly missing, with its reason. `check` rereads every source and reports codes such as
`source_missing`, `source_changed`, `source_unreadable`, `source_lines_out_of_range`, `open_question`, `over_budget` or
`paid_usage_needs_person`. A problem in an on-demand source is reported but does not block. Invalid format gives exit 1, and no package.

Excluded locations are refused before anything is read: `DANĚ`, `.ssh`, credential file names, the DATA HUB cache and OneDrive. A draft
naming one directly is invalid. One reached through a folder alias reads as `source_excluded`, and every read checks where the path really
leads, including `--broad` files in `compare`, which are counted as `excluded` and not named. This is a guard against mistakes,
not a classifier of content, and a link swapped between the check and the read is not covered.

## Limits

- **Separate context is not isolation.** The worker can still read everything its account can. No OS or credential boundary exists here.
- **Bytes are not tokens.** `compare` measures what is handed over, not what is billed. It does not count what a worker reads later,
  or the work of writing the package.
- **Facts in `compare` are chosen by the author**, and they check literal presence. That is not an evaluation of a model or of quality.
- **Text in a package is written by a person or agent.** It is not scanned for secrets, so do not put them there.
- **A `git` origin is a label.** Only the file at `path` is hashed; nothing runs git.
