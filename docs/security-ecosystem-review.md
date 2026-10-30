# Independent review — 0.4.0.dev4

Claude implemented the read-only Security & ecosystem section in local candidate
`3a07684`, based on the deployed compact dashboard `f3dcbbe`. Codex reviewed and
integrated it in a separate worktree; an independent reviewer examined the collector
using isolated fixtures, without reading real credential files.

Findings and resolutions:

- P1: Regex-redacting full cron commands exposed ordinary short credentials in
  `curl -u`, URL userinfo and Basic headers. The inventory now omits all arguments
  and environment values, showing only schedule and executable name.
- P2: Git remote display removed only userinfo. Queries, fragments and helper
  commands are now omitted as well.
- P2: A failed or missing `ss` probe looked like a successful empty result.
  Collection now carries command success explicitly and marks failures unverified.
- P2: Unreadable SSH includes or inaccessible include search paths could falsely
  appear complete. They now make the SSH observation unverified. Empty include
  globs are conservatively unverified too, even when they may be harmless.
- Wording now distinguishes parsed global SSH defaults from effective runtime or
  per-user policy, and non-loopback listeners from external reachability. Config
  parsing no longer claims secret values are never read into process memory;
  they are excluded from responses.

Verification covers endpoint authentication/no-store, secret omission, unknown
observations, caching and frontend escaping. All four browser suites passed with
fixture data: mission behavior, plugin compatibility, compact rosters and ecosystem
tabs/rescan/errors. The collector has no network or privileged operations. Runtime
config, host snapshots and real screenshots are not committed.

Deployment preserves the preceding immutable release and backs up runtime config
and SQLite separately. Only the dashboard process is replaced. GitHub workflow
status must be checked separately from local test evidence.
