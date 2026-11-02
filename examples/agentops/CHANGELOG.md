# Changelog

## 0.2.9 — 2026-11-02

- Add `agentops brief` (LC-08): a read-only, rule-based preparation of one task's start, with a linked output check and a defined
  ending for agent, person, nobody or unknown. It runs and changes nothing. Retain the independently accepted metadata/retention controls from 0.2.7 and work packages from 0.2.8.

## 0.2.8 — 2026-11-02

- Add versioned work packages (LC-04): `agentops package build/check/render/compare`. A package names the goal, the needed
  sources with origin and hash, constraints, the financial frame, owner and reviewer, output and end condition.
  A missing or changed required source means not ready. It runs nothing and holds no task state.
  Retain the independently accepted metadata export, retention safeguards and provider filters from 0.2.7.

## 0.2.7 — 2026-11-02

- Add allowlists per record type with documented purposes, including the contract of a future
  task/run/parent event (LC-07). Unknown fields, nested objects and free text are refused.
- Add `agentops metadata-export`: no titles, notes, prompts, cost sources, session ids or error text.
  Local paths appear only with `--include-references`, and every audit event is exported.
- Add `agentops retention plan`, which deletes nothing, and a staging-only `apply` bound to a plan hash.
  The ledger and reports never expire. Production pruning is not enabled.
- A stored session snapshot now keeps only allowlisted fields when read, so a foreign value is not
  carried on by the next write. `prune` no longer deletes malformed or foreign files.

## 0.2.6 — 2026-11-02

- Add `agentops queue` and a dashboard table (LC-03): one read-only list of owner decisions,
  repairs after review, peer reviews, takeovers and blocks, with copyable references to outputs,
  the reason for a return or block and the newest result check. It adds no approval step, and an owner decision
  appears only after an explicit handover to that actor.
- The table declares itself as workflow, so agentsmon 0.4.0.dev8 or newer counts tasks without
  evidence rows and shows no agent health for them. Deploy that agentsmon version first.

## 0.2.5 — 2026-11-02

- Add `agentops verify` (LC-01): a read-only, rule-based check of the exact target folder,
  files found there and their real SHA-256. Wrong folder, missing or changed file fails;
  unreadable data or an unresolved effect stays unverified. The report records code version
  and date, and links to the task; `done` refuses while the latest check is not a pass.

## 0.2.4 — 2026-11-01

- Add offline synthetic regression template/run/compare commands with full suite
  contract hashes, per-case verdicts, missing-evidence gates and private no-overwrite
  reports. Include backup destination, restored application and timeout reconciliation
  fixtures; successful checks still require independent review.

## 0.2.3 — 2026-11-01

- Fail closed on missing, empty or relative worker registration paths and empty provider
  metadata. Marker lookup errors now require intervention instead of appearing ready.
  Preserve the shared probe lock through marker inspection. Both edge cases were found
  by Antigraviťák in his actual onboarding review of 0.2.2.

## 0.2.2 — 2026-11-01

- Add an optional explicitly registered Task workers table independent of task snapshots.
  Metadata-only profile checks and existing wrapper lock observations distinguish ready,
  locally working and intervention-required workers; no model calls or credential reads.
  Shared quota labels do not duplicate usage percentages.

## 0.2.1 — 2026-11-01

- Show only active ledger tasks in the Work queue; exclude reviewed/completed and
  cancelled tasks before the 100-row limit. Full task and event history remains in AgentOps.

- Hide missing or expired provider observations instead of showing them as failed agents;
  retain real hook failures and repeated input hints. Empty signals table is omitted.

## 0.2.0 — 2026-10-31

- Observe allowlisted Claude Code and native Codex lifecycle hook metadata with
  private per-session storage, exact actor/session bindings and freshness checks.
  Source labels distinguish provider turn state from terminal hints and task progress.
- Provide metadata-only hook plans preserving existing handlers; no full settings
  output or blanket trust changes. Hook processes use isolated Python to avoid cwd shadowing.
- Add explicit-recipient age encryption and isolated authenticated decryption of
  data-only exports. Offsite transport and secret-bearing identity bundles remain separate.
- Real age fixtures and one isolated native Codex start/stop callback smoke passed.

## 0.1.1 — 2026-10-31

- Expose repeated terminal modal hints and unavailable observations independently
  of task registration, so an agent without an active ledger task is still visible.

## 0.1.0 — 2026-10-31

- SQLite task ledger with explicit acceptance, independent review, append-only events,
  command idempotency, revision conflicts and manual cost observations.
- Bounded fallback modal hints, declared-progress overdue signals and an existing-renderer
  monitoring plugin; no automated keystrokes, task execution or paid retries.
- Explicit-root export, online SQLite backup, hashes and isolated empty-staging restore.
  Secrets fail closed; cross-host identities and full-system migration are not implemented.
- Joint continuity concept based on actual Claude and Hermes reviews, with cold-first
  recovery, single active consumer, provider-side fencing and clear portability limits.
