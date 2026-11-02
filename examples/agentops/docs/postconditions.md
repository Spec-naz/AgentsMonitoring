# Result check on the real location (LC-01)

`agentops verify` answers one narrow question: are the agreed bytes at the agreed path right now?
It reads the filesystem instead of trusting a statement from a candidate document.

```sh
agentops verify --spec spec.json --output "$HOME/.local/state/agentops/postcheck/run-1.json" \
  --task TASK_ID --actor klodak
```

```json
{"schema": "agentops-postcheck-spec-v1",
 "target_dir": "/exact/absolute/folder",
 "exact_directory": true,
 "files": [{"path": "report.md", "sha256": "<64 lowercase hex>"}],
 "must_not_exist": ["/exact/absolute/wrong-place/report.md"],
 "unresolved_effects": []}
```

## Verdicts and exit codes

| Situation | Verdict |
|---|---|
| Exact folder, all files present with the expected hash, nothing unexpected | `pass`, exit 0 |
| Folder missing or reached through a symlink; same bytes only in another folder | `fail`, exit 2 |
| File missing, changed, a symlink or not a regular file; unexpected entry with `exact_directory`; a `must_not_exist` path exists | `fail`, exit 2 |
| No permission, file changing during the read, over the size limit | `unverified`, exit 2 |
| Any `unresolved_effects` entry, such as an upload that timed out | `unverified`, exit 2 |
| A folder or file replaced during the check, even with identical bytes | `unverified`, exit 2 |
| Invalid spec, or an excluded private location | no report, exit 1 |
| The report or, with `--task`, the ledger and its side files would land in the target or on a `must_not_exist` path, also through a symlink or a folder they would create | no report, nothing recorded, exit 1 |

`fail` wins over `unverified`. An unverified result is never promoted, and the report always says
`repeat_action_allowed: false`: deciding about an uncertain effect is a person's reconciliation step.

## Link to the task

With `--task` and `--actor`, the saved report's path and SHA-256 are recorded as a `postcheck` event by the
current owner. `agentops history ID` shows it, and `Ledger.latest_postcheck(id)` gives the newest one, for
example for the LC-03 queue. It changes no state. `done` is refused while the newest check is not a pass, so
a new check is the way forward. Tasks without a check behave as before: this is no new gate for ordinary work.

## What it does not prove

- **Application health:** `data_readable` means every listed file was really found and read at its path (a wrong
  hash is readable, a missing file is not). The report always has `application_health: not_checked`, because
  readable restored data is not a working service. Checking health stays a separate, explicit step per host.
- **Exact directory contents:** when `exact_directory` is true, the target and every nested parent of an expected file have their entry names, identity, size and modification/change times compared before and after the check. An observed change is `unverified`, even if the checked file hashes still match. Extra files in unrelated directories remain allowed with `exact_directory: false`.
- **Later changes:** the last step re-walks the path and compares the folder and each file (device, inode, size,
  times). A replacement seen there is `unverified`, even with identical bytes. A change after that step, or an
  in-place rewrite that leaves the same size within one timestamp tick, is not caught. A result is a statement
  about one moment, so the reviewer should run the check again before `done`.
- **Truth of the expected hash:** it comes from the spec, so the spec author decides what is correct.
- **What `done` reads:** the newest recorded verdict for the task, not a fresh look at the disk, and not all checks
  across several specs. Anyone with direct ledger access can declare a verdict, and actors are accountability
  labels. This guards against mistakes; it is neither verified identity nor authorization.
- **`must_not_exist`:** this is a plain lookup of the path, so symlinks in its parent folders are followed.
- **Remote state:** cloud copies, e-mails and messages are outside its reach; list them as `unresolved_effects`.

## Boundaries

No model, network, shell or execution of checked data; nothing is repaired, moved or repeated. File content
never enters the report, only hashes, sizes and names. Excluded locations (`DANĚ`, `.ssh`, `.gnupg`, `.aws`,
credential file names) are refused before anything is opened. This is a mistake guard, not a data classifier.
Relation to LC-06: `regress` evaluates supplied synthetic observations; `verify` produces its own real
observation of one location. It does not replace that oracle. Linux is the tested platform.

Report/ledger/side files that already have multiple hard links are refused before checking or writing: an external path can still alias checked bytes through the same inode. Use independent backup copies rather than hard links for writable ledgers. This is not a claim of transactional protection against all later filesystem changes.
