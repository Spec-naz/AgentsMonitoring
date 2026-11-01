# AgentBackup 0.1.3

Daily age-encrypted application recovery capsules and optional rclone OneDrive
copy with SHA256 full-download verification. Runtime: Python stdlib, verified age
and rclone; optional pg_dump/pg_restore for the wiki database. No sudo.

`backup.py --config /private/settings.json --local-only` creates a local capsule.
Without --local-only, remote authorization is required; failure is nonzero and
recorded separately from local success. No sync or credential export. Narrow verified-capsule retention is opt-in. Full source modules are preserved; .env, links, caches, dependency/build
directories and live DB files are omitted. SQLite uses online backup; Postgres
uses consistent pg_dump custom format. All output/temporary files owner-only.
Capsule ZIP is encrypted before publication. Normal plaintext cleanup is not
secure erase and cannot guarantee cleanup after power loss/SIGKILL.

Restore uses the previously reviewed AgentOps continuity validator copied to
`continuity_restore.py` with its original safety rules unchanged. No scripts from
restored code are executed by the restore tool. `restore.py --help` and RECOVERY.md
explain recovery. The private recovery identity never belongs on the production VPS.

No unattended offsite success until server OAuth authorization AND an actual
upload/download hash check pass. Chat connector tokens are not extracted/reused.
The OneDrive remote targets the dedicated existing folder by an explicit path, but OAuth scopes
may permit access elsewhere in the user's OneDrive: path scoping is not an OAuth
security boundary. Request only Files.ReadWrite offline_access; no SharePoint search.

Snapshots of different applications do not form a global transaction. Sources
changing during file copy fail the run. Missing required roots/databases fail
rather than silently produce an apparently complete backup. Ciphertext SHA256
needs a trusted independently recorded source; same-storage hashes are not signatures.

Limits: max512MiB uncompressed capsule, max128MiB per file, max10000files;
5GiB minimum free disk, 4GiB ciphertext backlog guard. Remote deletion is limited to recognized verified capsules past the configured age.
Local retention keeps the configured count of verified archives (production7); unverified copies are preserved.
An unuploaded backlog is preserved and eventually stops growing at the guard.
User service uses low CPU/IO priority, 768MiB memory ceiling, 25% CPU quota and
a bounded execution timeout. A failed transfer remains visibly failed; no fake success.

Tests: WAL SQLite snapshot and complete source recovery, credential/symlink exclusion,
tampered archive rejection before extraction; actual local capsule sandbox rehearsal
recorded privately. No test is claimed for unattended OneDrive until authorization.

Version0.1.2 requires an explicit destination folder path; do not rely on root_folder_id for write placement or prepend an absolute object path. Schedule is daily04:00Europe/Prague plus up to120seconds jitter. Owner approved retention on2026-11-01:30days remote and7verified local copies.

Destination verification: list the configured folder after each upload and require archive name/size before readback and receipt publication. Do not rely on root_folder_id for write placement. Production remote is agentbackup:AgentOps-Backups with no root_folder_id override.

Retention0.1.3: after a newly verified upload+receipt, remove matching capsule/receipt pairs older than30days in AgentOps-Backups. Keep current/newest remote archive regardless of age. Remote candidates need valid verified receipts with matching name/size and SHA256 shape. Local deletion also rechecks the actual SHA256. Preserve recovery kits, legacy manual archives, malformed/unknown entries and unverified copies. Age is UTC archive timestamp; cleanup occurs on successful daily runs, not during a failed backup.
