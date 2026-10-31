# 0.1.0 validation and limits

Independent review examined the ledger and observation/plugin implementation.
It found a review-forwarding loophole: forwarding an unchanged artifact could
replace its producer and permit self-review. The fix preserves original provenance
through review forwards and rejects a return to that producer. A regression covers
the loop. Actor labels remain declarations, not authenticated identities.

Thirty isolated tests pass, including transactions/replay/CAS, reviewed completion,
append-only history, separate actual/estimate/currency accounting, non-reassigning
stale hints, terminal-content omission, stale snapshot expiry and eighteen backup
acceptance cases (including SQLite WAL, corruption, path traversal, links/devices,
collisions, concurrent destination creation and protected empty staging).

Pyflakes passes. Fixture browser acceptance normalizes the real plugin contract
through AgentsMonitoring and checks the existing renderer, an unaccepted handoff,
escaping, dark/light themes and 1280/390/320 px layouts. No core visual files changed.

A real local data rehearsal exported selected code and the active task ledger,
verified a separately recorded archive SHA256, restored into empty private staging
and passed SQLite integrity. The initial snapshot contained three tasks and twelve
events. No services, network clients, credentials or paid operations were restored
or started. Private evidence and archives remain outside Git.

This does not validate a second server, identity recovery, encrypted/off-host backup,
all applications or real RPO/RTO. Those remain explicitly pending in the continuity
runbook. Live deploy is limited to the task snapshot producer and read-only plugin;
there is no automatic task executor, lease takeover or alert sender.
