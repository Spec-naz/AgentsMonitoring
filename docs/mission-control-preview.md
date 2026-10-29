# Mission Control preview: 0.4.0.dev1

This candidate changes presentation and browser snapshot handling. It does not add an agent
orchestrator, infer whether an agent is thinking, send Telegram probes, or change keepalive policy.

## Behavior

- Responsive cards wrap full agent names. Telegram links reserve their own space. They open
  the configured bot; their presence is not a bridge health check.
- Session identifiers/ports are available in Connection details. Expanded details survive polling.
- Dark is the initial theme. Light/dark preference is stored only in this browser. Keyboard
  focus and section toggling work without a mouse. Reduced-motion preferences suppress transitions.
- The top counts describe native agent observations, excluding separate provider tables. The process count includes tmux agents only. HTTP-checked daemons report endpoint
  responding/unavailable, independently of process liveness. Attention counts stopped/login/incomplete-data
  rows; it is not an exhaustive diagnosis of every possible provider error.
- State fetches reject HTTP errors, time out after a configurable timeout (120 seconds by default) and never overlap.
  Set `dashboard.request_timeout_seconds` higher for large/slow configurations (minimum 5 seconds).
  A failed fetch
  or snapshot older than max(30 seconds, 3 polling intervals) marks the retained view stale.
  A successful rendered snapshot clears that warning. The banner reports browser receipt age;
  it is not proof of fresh provider data or an end-to-end Telegram response.
- Plugin columns, provider actions, service metrics and availability history remain supported.

## Validation

Run the dependency-free suite:

```sh
python3 -m unittest discover -s tests
```

Optional browser acceptance (fixture data only; no live actions):

```sh
python3 -m pip install playwright
python3 -m playwright install --with-deps chromium
python3 tests/verify_mission_browser.py --out /tmp/mission-evidence
python3 tests/verify_plugins_browser.py --browser /path/to/chrome --out /tmp/plugin-evidence
```

The mission checks cover 1280/768/390/320 px layouts, non-overlapping Telegram controls,
long unbroken identities, HTML escaping, preserved details, persisted themes, failed fetches,
age-based staleness, recovery, keyboard toggles and reduced motion. Plugin checks cover
provider actions, health details, metrics, extra columns, loading/failure isolation and escaping.
Browser evidence is synthetic and does not represent a live system health claim.

## Review and deployment

Work belongs to `feat/mission-control-dashboard`, based on upstream/fork commit
`f9b74eec38a4d04867883e1af5951466c85f2e3c`. The production installation lives separately in
`~/.agentsmon-src`; creating or reviewing this candidate does not replace it.

Before deploying, record the currently installed version, source commit, launch command and
service manager. Back up `~/.config/agentsmon` and the dashboard state directory specified in
that config with their existing permissions. Never commit credentials or those backups.
Use the real configured paths if they differ; verify them before executing any restore.

Install the reviewed candidate into the same controlled environment used for the dashboard,
then restart **only the dashboard process** through its current manager. Do not restart tmux
agents or Telegram bridges to activate a UI change. Verify authenticated HTTP access, agent
cards, Telegram links, plugin actions and the candidate version/integrity footer.

## Rollback

There is no database/config schema migration in this preview. To undo a deployment, reinstall
the previously recorded package/source revision in the same environment and restart only the
dashboard through its manager. Verify the previous version and integrity footer and existing
agents/bridges. Restore backed-up config/state only if those were separately changed; this UI
release itself does not require restoring data. Keep the Git history: use a revert commit for
source rollback, not force-pushing or deleting commits.

Do not merge or tag a stable release while checks fail. `0.4.0.dev1` is a development version,
not a claim of a production rollout. Cloudflare Tunnel/Access and shared task handoff are
separate follow-up work.
