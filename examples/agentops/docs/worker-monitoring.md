# On-demand Task workers table

This optional plugin uses the existing dashboard renderer. It adds no core UI,
process supervisor, model inference, credentials access or automatic task execution.
It is independent of the AgentOps task snapshot; a missing task snapshot does not
hide registered workers. Without explicit `workers` registrations, it returns no table.

Install the `monitor-worker` directory as a separate module and supply at most eight
registrations in its own manifest settings. Example metadata (no credentials):

```json
{"workers": [{
  "id": "hermik2",
  "name": "Hermík dvojka",
  "model": "claude-haiku-5-5",
  "provider": "claude-subscription-directsdk-experimental",
  "quota_label": "Claude Max sdílené s Klóďákem",
  "config_path": "~/.hermes/profiles/hermik2/config.yaml",
  "launcher_path": "~/projects/hermik2-worker/run.py",
  "state_dir": "~/.local/state/hermik2"
}]}
```

The config check reads at most 64KiB of a regular non-symlink UTF-8 config and checks
the simple `model:` section's unquoted provider/default metadata. It does not parse
or validate all YAML settings. Unsupported formatting is intervention-required,
not silently accepted. The launcher must exist as a regular file; its contents are
not read. Registration paths are trusted local administrator configuration.

- **Připraven:** metadata matches and no active wrapper lock or unresolved marker
  was observed. The wrapper checks subscription login and extra usage only when
  an actual task starts. Ready is not proof of quota, login or model availability.
- **Pracuje:** the existing `run.lock` cannot be acquired non-blockingly. This is
  local wrapper activity, including preflight, not proof of model/task progress.
  A lock holder takes precedence over the unresolved marker created during a run.
- **Vyžaduje zásah:** unlocked unresolved marker (even invalid/dangling metadata),
  invalid/missing config or launcher, or inaccessible/nonregular state metadata.
  A crashed parent can leave a detached native request: dashboard never claims to
  cancel it. Follow the wrapper's explicit reconciliation procedure.

The probe opens existing lock files read-only, does not create/truncate them and
briefly acquires/relinquishes a shared flock if no exclusive wrapper lock is held.
Concurrent monitor probes coexist; a wrapper attempting its exclusive nonblocking lock
during the brief probe may safely refuse that start, requiring a later explicit retry. This observation is not an atomic
systemwide health guarantee. Concurrent jobs can begin after the observation.
No prompt, reply, runtime log, receipt, private project name, credential or marker
contents are read or displayed. Shared quota is labelled, never duplicated as a
second usage bar or independent allowance. Rows have no start/restart actions.
