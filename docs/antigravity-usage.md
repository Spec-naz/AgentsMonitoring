# Automatic Antigravity quota readings

The official [Antigravity statusLine contract](https://antigravity.google/docs/cli/statusline/)
provides conversation identity, model and quota buckets. AgentsMonitoring collects
only those fields plus its observation time and explicitly configured profile/pool
labels. It never saves email, cwd, title, transcript path, subagents, context-window
usage, signed-in credentials or estimated costs. Input is capped at256KiB, snapshots
at8buckets, files0600 and their directory0700. Collection performs no network/model
requests. StatusLine stdout stays empty; the native status line is preserved.

Install once for each independently configured CLI profile home, using the reviewed
installation's command. First inspect the plan, then apply:

```sh
agentsmon antigravity-usage install --profile-home /absolute/profile-home \
  --profile consumer --pool 'Google AI Pro'
agentsmon antigravity-usage install --profile-home /absolute/profile-home \
  --profile consumer --pool 'Google AI Pro' --apply
```

For a different financial source, choose a different explicit label, e.g. profile
`gcp`, pool `GCP trial — eligibility not yet verified`. The installer does not check
billing entitlement and this label is never inferred from vendor, model or email.
It writes `.gemini/antigravity-cli/settings.json` under the supplied home, adds
`statusLine.enabled=true` and `stack_with_default=true`, and generates a private
`agentsmon-statusline.sh` invoking the pinned reviewed collector with a central
snapshot directory. Existing unrelated statusLine settings or wrappers are refused;
other settings are preserved and an existing settings file is backed up as exact original bytes privately.
An explicit `--apply` can upgrade ONLY a recognized privately owned generated
wrapper attached to the same configured statusLine and the same profile/pool/destination.
The old wrapper is backed up too. Foreign hooks/wrappers remain refused, including
during dry-run. An identical wrapper left by a partial install can be recovered.
The collector stays pinned to the reviewed immutable release; retain that release
until hooks are explicitly upgraded. For rollback restore the backed-up hook/wrapper
before removing a release. A marker and file owner are local conventions, not authentication.
The installer starts/reloads nothing and does not read authentication files.

The command prints the wrapper `command_path`; in an already-running idle CLI,
the operator can use the official native `/statusline /absolute/wrapper/path`
command to reload it. Actual native refresh and sign-in remain operator tasks.
A profile with no authenticated quota report stays Unavailable; a label is not
proof that GCP credits exist or fund that CLI session.

Once that provider profile is configured, **new conversations are automatic**:
statusLine writes `UUID.json` in the central directory, and the existing Usage
column selects the reading for each `kind=antigravity` row's exact public
`session_id` or `conversation_id`. No new per-agent `usage_plugin.sources` mapping
is required. Registering the first configured Antigravity agent enables the built-in
Usage adapter; dynamic manager discovery refreshes when that provider is added.
The agent/session identity itself must already be registered/resolved. An explicit
`usage_plugin: false` disables automatic discovery too. Existing
explicit source mappings take precedence. Headless paths that never emit statusLine
cannot provide this reading; no transcript scan or API fallback is attempted.

`remaining_fraction` becomes `(1-fraction)*100`, labelled **used**. A remaining
amount is shown as remaining with unspecified units, **never dollars or billed
spend**. Context-token percentages are never treated as quota. Expired windows
are omitted, disabled buckets stay disabled, and readings older than10minutes
are marked Cached. Missing/ambiguous UUIDs, missing snapshots and invalid values
remain Unavailable or No current reading. The timestamp is observation time, not
independent provider freshness proof. Profiles sharing the same actual account may
share limits, but collector labels do not establish financial independence.

Snapshot filenames contain UUIDs only. A UUID already associated with a different
explicit profile/pool cannot be overwritten by another configured collector.
Snapshots are local metadata under the same trusted Unix principal, not an
authenticated telemetry boundary. A future retention policy is separate; these
files contain no conversation content and collection creates one per conversation.

A later event with missing, empty or invalid quota deliberately replaces a prior
reading with No current reading; we do not refresh an older quota as if new. Native
metadata events may therefore transiently hide a prior quota. Invalid model IDs
become unknown model metadata while a valid quota remains usable. Reset epochs
outside0..observation+366days are rejected, including millisecond timestamps.
All percentages describe quota, not a monetary balance.


The optional Plan limits panel displays remaining fractions separately from the
roster's used percentages. It preserves explicit profile/pool labels, Cached-prefix
staleness and distinct conversation readings; matching percentages/profile names
do not merge accounts. Remaining amounts with unspecified units are not quota
percentages or monetary balances and cannot create rings without a fraction.
