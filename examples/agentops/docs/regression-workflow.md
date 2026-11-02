# Offline regression workflow (LC-06)

This is a working, dependency-free CLI for reviewed **synthetic** cases and explicitly
supplied observations. It does not invoke an agent, application, network, model, cloud
account, backup, restore or retry. Revision labels describe the caller's observations;
they are not proof that the named software was executed. The initial suite covers the
three mechanisms from the independently reviewed Hermes LC-06 proposal.

## Run the included regression demonstration

From this checkout, choose a NEW evidence directory outside the repository. The output
files are private0600, published atomically and never overwritten, including symlinks.
Do not direct output into a source/configuration location.

```sh
LC06_EVIDENCE="$HOME/.local/state/agentops/regression/demo-$(date +%Y%m%d-%H%M%S)"
python3 -m agentops regress run --suite examples/regression/suite.json \
  --observations examples/regression/baseline.json --revision fixture-v1 \
  --output "$LC06_EVIDENCE/baseline.json"
# Exit2 expected: restored application is unhealthy.
python3 -m agentops regress run --suite examples/regression/suite.json \
  --observations examples/regression/candidate.json --revision fixture-v2 \
  --output "$LC06_EVIDENCE/candidate.json"
# Exit2 expected: content is correct but the backup destination is wrong.
python3 -m agentops regress compare --baseline "$LC06_EVIDENCE/baseline.json" \
  --candidate "$LC06_EVIDENCE/candidate.json" --output "$LC06_EVIDENCE/comparison.json"
# Exit2 expected: both versions pass2/3, but backup-folder regressed.
```

A fully passing observation fixture is `examples/regression/passing.json`. A passing
run/compare returns0. Validation/I/O errors return1; a valid report with any failed or
unverified case returns2 and still saves the evidence. All passing comparisons say
`review-required`, never `deployment_authorized:true`. No AgentOps task DB is opened
by the regression commands, even when the default ledger is unavailable.

## Record another version using the same cases

1. Inspect `suite.json`: fixed case IDs/revisions, origin, instruction, expected target
   contract and oracle version are all part of its canonical SHA256. Keep this exact
   suite for both versions. JSON whitespace/key ordering is deliberately immaterial;
   changing instructions, expected values, case revisions or oracle changes the contract.
2. Create an empty bound observation file:
   `python3 -m agentops regress template --suite examples/regression/suite.json --output /new/private/observations.json`.
   Missing observations remain unverified. Fill only the permitted synthetic fields,
   using evidence references such as `synthetic:readback-v2`; never raw conversations,
   private documents, credentials, accounts or real paths.
3. Run the candidate/fixture independently under the appropriate existing harness or
   fill synthetic observations explicitly. This CLI does not run it for you. Record
   truthful evidence and a revision identifier (a Git SHA is useful when applicable).
4. Evaluate with `regress run`, preserving suite and observation hashes. `run` reports
   only per-case verdicts and evidence references, not raw observations or instructions.
5. Compare run reports. Different suite/oracle hashes or case sets/revisions refuse
   comparison, even when case IDs alone match. Inspect every regression; an unchanged
   total score cannot hide a failing case. Missing evidence is not pass. Report hashes
   detect accidental edits, not forgery by the shared Unix principal.
6. Hand report paths and underlying evidence to an independent reviewer using the
   existing AgentOps offer/accept workflow. A passing score does not approve release,
   prove provider idleness, authenticate ownership or complete the parent task.

## Initial deterministic checks

- `backup_destination`: exact expected synthetic destination **and** content match.
  A wrong destination fails even with matching bytes. Missing evidence is unverified.
- `restored_application`: data readable **and** application healthy. A running process
  field is deliberately unsupported; it is not a successful healthcheck.
- `timeout_reconciliation`: committed result→verify existing with no new writes;
  unknown→stop/reconcile with no new writes; definitive absent→one synthetic retry
  under the same operation ID. A changed key, repeated writes or blind unknown retry
  fails. This evaluates observations, not real remote idempotency or exactly-once effects.

Schemas are strict, bounded (1MiB/file,100cases), reject duplicate JSON keys, nonfinite
numbers, unsupported check names, extra fields, input symlinks and special files.
The full suite and result hashes are local integrity checks, not signatures.

Real or sanitized complaint intake is deliberately rejected until LC-07 privacy rules
and a separately reviewed extraction workflow exist. Filename/schema allowlists do
not prove text is anonymized. LC-01 remains responsible for real acceptance contracts
and target-state evidence; this tool neither replaces that process nor authorizes
production retries. Extend the finite oracle only with reviewed code/tests, not
arbitrary shell/Python hooks from JSON. Examples are synthetic and not historical
production failures or evaluated model runs.
