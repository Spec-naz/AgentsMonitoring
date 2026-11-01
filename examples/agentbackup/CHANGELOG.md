# Changelog

## 0.1.2 — 2026-11-01

- Use explicit AgentOps-Backups folder path at the drive root instead of relying on root_folder_id. Refuse absolute paths and join file names without an unintended leading slash.
- Require the uploaded name/size in an independent destination listing before readback and offsite success.
- Daily04:00Europe/Prague timer replaces hourly scheduling; on-demand runs remain available.
- Existing local last48 verified-copy retention remains; remote automatic deletion remains disabled pending owner policy.
- Live folder placement must be checked independently through drive metadata, in addition to content SHA256.

## 0.1.1 — 2026-10-31

- Fail closed on unreadable source subtrees instead of silently skipping data.
- Preserve complete reviewed code modules, consistent SQLite and wiki Postgres dump.
- Hourly local encrypted capsule with bounded resource use; remote mode requires OAuth
  plus a real upload/download SHA256 check before activation.
- Restore validator and explicit recovery procedure; offline same-host rehearsal restored
  code/data and started dashboard; wiki dump imported into a new isolated database.
- No live credentials/Telegram delivery state export; full host failover remains unverified.

## 0.1.0 — 2026-10-31

Initial locally deployed encrypted recovery capsule and staged OneDrive setup.
