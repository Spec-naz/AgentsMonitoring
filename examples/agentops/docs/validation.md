# 0.2.0 validation and limits

Independent review examined the ledger and observation/plugin implementation.
It found a review-forwarding loophole: forwarding an unchanged artifact could
replace its producer and permit self-review. The fix preserves original provenance
through review forwards and rejects a return to that producer. A regression covers
the loop. Actor labels remain declarations, not authenticated identities.

Seventy-eight isolated tests pass, including transactions/replay/CAS, reviewed completion,
append-only history, separate actual/estimate/currency accounting, non-reassigning
stale hints, terminal-content omission, stale snapshot expiry and eighteen backup
acceptance cases (including SQLite WAL, corruption, path traversal, links/devices,
collisions, concurrent destination creation and protected empty staging).

Pyflakes passes. Fixture browser acceptance normalizes the real plugin contract
through AgentsMonitoring and checks the existing renderer, an unaccepted handoff,
escaping, dark/light themes and 1280/390/320 px layouts. No core visual files changed.
Repeated modal hints are exposed even when no task is registered for that agent;
otherwise the original blocked-dialog incident could be missed outside the ledger.

A real local data rehearsal exported selected code and the active task ledger,
verified a separately recorded archive SHA256, restored into empty private staging
and passed SQLite integrity. The initial snapshot contained three tasks and twelve
events. No services, network clients, credentials or paid operations were restored
or started. Private evidence and archives remain outside Git.

The new age adapter passed six real ephemeral-identity E2E cases against an independently
SHA256-verified official age v1.3.2 release: data/SQLite roundtrip, wrong key, tamper,
encrypted traversal, overwrite race and digest mismatch. Plaintext cleanup and failure
paths are covered. These are fixture identities, never production recovery identities.

One isolated native Codex0.166 invocation delivered UserPromptSubmit and Stop through
the exact isolated-script hook command; the resulting bound fixture session was idle.
The vetted one-off fixture used the documented per-invocation trust bypass; it did
not modify global trust policy or an agent launcher. Persistent hooks still require
their normal provider registration/review. Hook events never complete ledger tasks.

Independent provider review fixed late-event actor rebinding, child activity incorrectly
clearing main prompts, unvalidated shell provider labels, direct-script plan idempotency,
full-settings output and expiry of SessionEnd. Exact-session and all-state freshness
integration tests prevent stale/other-session observations from appearing confirmed.

This does not validate a second server, identity recovery, production encrypted/off-host
backup, all applications or real RPO/RTO. Those remain explicitly pending in the continuity
runbook. There is no automatic task executor, lease takeover or alert sender.
