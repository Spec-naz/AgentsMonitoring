# Security and ecosystem — 0.4.0.dev4

The collapsed Security & ecosystem section provides read-only host observations,
credential-file metadata and an ecosystem inventory. It preserves the existing
dashboard layout and auth. Expand it for Security, Access and Ecosystem tabs.

`GET /api/ecosystem` requires the same dashboard credentials as `/api/state` and
returns `Cache-Control: no-store`. Snapshots are cached for five minutes; explicit
refresh is limited to one collection per thirty seconds. No privileged commands,
network requests, package updates or configuration changes are performed by this
collector. It runs as the dashboard's OS user.

SSH checks parse readable global configuration defaults in include order. They
are not an effective-policy check of the running daemon or per-user Match rules.
Unreadable include files make the SSH result unverified. A failed listener probe
also yields an unverified result. Non-loopback listeners do not prove external
reachability: firewall rules and routing are outside the observation.

Access inventory displays selected key names and file metadata. Configuration
files are parsed locally; secret values are excluded from the response. Cron
inventory exposes schedules and executable names only: arguments and environment
values are omitted. Remote URLs omit userinfo, query strings and fragments;
custom Git helper strings are hidden. The authenticated endpoint still exposes
host metadata such as local paths and service names; keep dashboard auth enabled.

Independent review found and fixed credential exposure in cron arguments, incomplete
SSH observations being presented as safe, and failed listener probes looking healthy.
Regression fixtures cover those cases as well as URL credential removal, endpoint
authentication, caching and XSS-safe rendering. The browser suite checks both themes,
320–1280 px widths, tab keyboard access, rescan failure and default collapse.

Run `python3 -m unittest discover -s tests`, Pyflakes, and all four
`tests/verify_*_browser.py` checks (mission, plugins, compact and ecosystem) before
deployment. The browser checks use synthetic fixtures and do not inspect real secrets.

Deploy an integrity-checked immutable Git export after backing up runtime config
and SQLite separately. Preserve the prior release and restart only the dashboard;
agent sessions and Telegram bridges are not part of this release. Roll back by
selecting the prior immutable release and restoring its launcher path. The deployment
record belongs in private operator state, not in this public repository.
