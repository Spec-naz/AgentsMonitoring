# AgentsMonitoring collaboration rules

- Work on the user's fork, Spec-naz/AgentsMonitoring. Keep changes on a feature branch and
  submit a PR with the problem, resulting behavior, validation and material limitations.
- Claude owns dashboard visual design and implementation; Codex handles technical integration,
  verification and GitHub publication. The user visual direction is a spaceship cockpit/control panel.
- UI/UX should use coherent softer shapes, natural text spacing and compact agent rosters.
  Evaluate larger fleets (12/24 agents) as well as two-agent examples, including mobile.
- Preserve the dependency-free runtime unless a concrete requirement justifies a change.
- The user requests collaboration with Claude: use an independent review of substantial
  changes. Record findings and their resolution. Do not claim review approval when it is
  pending, and verify review claims against code rather than accepting them automatically.
- One implementer owns a task; the other reviews. Use separate worktrees for concurrent
  edits. Handoffs identify the branch, files, results, checks and outstanding concerns.
- Keep CHANGELOG, version metadata, package integrity manifest and affected docs consistent.
  Regenerate the reviewed manifest with scripts/core_manifest.py; never bypass integrity
  checks to make a modified installation appear clean.
- Test the behavior appropriate to the change. For dashboard work, run unit tests, lint,
  and fixture-based browser checks covering mobile, errors and provider compatibility.
- Keep a recorded previous version and rollback plan before deployment. A Git revision does
  not back up runtime configuration or databases. Do not restart agents or Telegram bridges
  just to activate a dashboard UI update.
- Never commit secrets or raw production state. Keep auth checks, escaping and action
  confirmations intact. Preserve history; use revert commits rather than rewriting main.
- Report facts, assumptions and unavailable measurements separately. Process liveness is
  not proof of task progress, bridge delivery or successful collaboration.
