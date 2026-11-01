# Changelog

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
