# Encrypted data continuity — age adapter

This adapter wraps the established age binary; it implements no cryptography and
generates no production key. Use an independently verified release and an explicit
native public recipient beginning `age1`. Keep the private recovery identity outside
the primary server. The server needs only the public recipient to export data.

```bash
agentops age-capabilities --age-executable /verified/path/age
# Inventory only by default; no archive or source contents read.
agentops backup-age --root code=/reviewed/project --output /private/backup.zip.age --recipient age1PUBLIC_RECIPIENT --age-executable /verified/path/age
# Explicit encrypted export, preserving existing secret/auth exclusions:
agentops backup-age --root code=/reviewed/project --output /private/backup.zip.age --recipient age1PUBLIC_RECIPIENT --age-executable /verified/path/age --execute
# Existing EMPTY mode0700 staging and an owned mode0600 private identity file:
agentops rehearse-age --archive /private/backup.zip.age --staging /private/empty-stage --identity-file /offline-recovery/identity.txt --sha256 CIPHERTEXT_SHA256 --age-executable /verified/path/age
```

These commands do not upload files, register a scheduler, change provider credentials
or start restored services. A real offsite backup additionally needs a chosen remote
destination, a recovery recipient controlled outside this server, retention policy
and a successful restore on another host. A local encrypted file is not offsite DR.

Source roots must still be reviewed. Filename filtering cannot prove code/history
contains no sensitive data. Explicit secret/auth identity export remains disabled.
An encrypted identity bundle requires a separately reviewed workflow.

The plaintext ZIP exists briefly in a private temporary directory. Normal cleanup
is tested on success and failure, but does not promise secure erasure or cleanup
after SIGKILL, power loss or a compromised shared Unix account. No external stderr
or private identity content is included in results. Identity file PATH is passed
to age; its contents are never command arguments.

Ciphertext is published atomically without overwrite and independently hashed.
Rehearsal pins the exact encrypted input whose hash is checked, decrypts with age,
then uses the existing manifest/path/type/hash and SQLite validation before restore.
Recipient encryption authenticates ciphertext integrity, not the sender; compare
a separately recorded ciphertext SHA256 and trust the source of the backup.

Validation uses disposable fixtures and ephemeral identities only: real age
roundtrip including SQLite WAL, wrong-key and tamper failures, encrypted traversal
rejection, overwrite races and temporary plaintext cleanup. No production recovery
identity was generated or uploaded as part of those tests.

The staged age v1.3.2 Linux release was downloaded from the
[official release](https://github.com/FiloSottile/age/releases/tag/v1.3.2) and its
GitHub-published SHA256 asset digest checked. CI can run the same real fixtures by
setting `AGENTOPS_TEST_AGE` and `AGENTOPS_TEST_AGE_KEYGEN` to independently verified
executables. Without them six E2E tests are explicitly skipped, not claimed passed.
