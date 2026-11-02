# Routines without a model (LC-08)

Rule: if a step has one right answer from known data, a script does it. A model starts only where judgment begins.

## `agentops brief`

What used to be repeated by hand at each takeover: read the task, read the audit trail for the return reason, check the linked
output exists, work out the revision. It reads the ledger, one linked file and the continuity guard. It changes nothing.

| Exit | Ending | Who continues | When |
|---|---|---|---|
| 0 | `no_action` | nobody | Task closed, waiting for someone else, or queued but never handed over. No model needs to start. |
| 10 | `start_agent` | agent | A handover to accept, an accepted task to start, or work already in progress, with what to read first. Accepting a review enters it, so no start is printed. |
| 20 | `person` | person | Uncertain outside effect or a saved pause or wait in the guard, explicit decision, your own declared block, or a linked output that is missing. |
| 30 | `unknown` | person | Ledger or guard unreadable, task not found, unreadable audit details, changed during the read, or over the time limit. |

Every ending has `retry: false`. The routine never tries again by itself and never guesses. The 10 second limit covers hashing the linked output.
Each SQLite read has its own 2 second lock timeout, so the whole run is bounded but not to an exact figure.

`start_agent` is not permission to dispatch. It says the state of this one task allows it. Dependencies and a global pause stay with the
foreman and relay. A decision handed to the owner stays with a person after it is accepted, until the owner hands it on.
Output contains no clock, so an unchanged state gives identical bytes, which suits the existing monitor gate.

## What it deliberately does not do

- **It chooses no work.** `projects/agent-foreman` decides which task is next; this looks at one task it is told about.
- **It takes nothing over.** The printed commands are text for the worker. Delivery to an agent stays with the coordinator or relay.
- **It does not know whether an agent is idle**, or whether someone just typed a stop message.
- **It does not read the output.** Only existence and a hash. Whether the handover makes sense is the agent's judgment.
- **A readable file is not a correct file.**
