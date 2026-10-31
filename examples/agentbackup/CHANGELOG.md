# Changelog

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
