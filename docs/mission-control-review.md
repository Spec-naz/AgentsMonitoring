# Mission Control candidate review record

Candidate: 0.4.0.dev2, branch `feat/mission-control-dashboard`.

## Independent reviews

Claude reviewed supplied source excerpts in a separate CLI session, without modifying the
running Telegram conversation. A second independent code reviewer inspected the repository
and reproduced concrete findings in Chromium. The implementer checked each claim against
actual code. Neither review is a production deployment or a claim about live agent health.

## Resolved findings

- **Mixed liveness signals:** HTTP-checked daemons now say Endpoint responding/unavailable.
  The process count includes tmux agents only; the mixed native group says available.
- **Long status overflow at 320 px:** status and model text wrap inside the card. Browser
  acceptance includes a realistic long authentication status and an unbroken long identity.
- **Pastel name highlight contrast:** existing red/gold/green/blue name backgrounds use dark
  foreground text in both themes. All four variants are covered in browser acceptance.
- **Overly short fetch cutoff:** use a configurable `dashboard.request_timeout_seconds`,
  default 120, minimum 5, rather than a fixed 20-second limit. Slow multi-service setups can
  raise it without changing application code. Timed-out client requests do not cancel
  server collection; this is not a new server-side collection scheduler.
- **Initial screen-reader announcement:** do not announce an outage while the first request
  is still legitimately pending. Announce receipt/failure transitions, not the seconds counter.
- **Keyboard and focus:** keep the prior focused card control during refresh; support
  keyboard copy and restore focus after the HTTP clipboard fallback. Focus outlines no
  longer change element rounding.
- **Table accessibility:** column headers are visually clipped rather than display:none.
  Browser acceptance checks that column headers remain in the accessibility snapshot.
- **Stale readability:** stale warnings use explicit text and dashed headers without lowering
  the contrast of the retained data.
- **Port escaping:** render the port through the existing HTML escaping helper.

## Considered but not accepted as defects

- A failed fetch deliberately marks the retained snapshot unverified immediately, even
  before the age threshold. The threshold separately detects delayed/missing refreshes.
  The banner explicitly gives receipt age and connection failure; it does not claim the
  agent has stopped. Documentation explains that receipt age is not provider measurement age.
- Claude's supplied excerpt omitted the delegated keyboard handler. The actual code does
  handle Enter/Space for focusable copy controls; it was checked rather than removed.
- Collapse state is stored in data attributes/hidden/aria-expanded, not the overwritten
  style class. Duplicate table-local row IDs are outside the validated provider contract;
  native tmux session names are unique.

## Verification

- Dependency-free unit suite: 239 tests.
- Pyflakes over `agentsmon` and `tests`.
- Browser acceptance for responsive identity/Telegram controls, themes, stale/failure/recovery,
  escaped content, status changes, keyboard focus, reduced motion and highlighted names.
- Existing plugin browser acceptance: provider columns/actions, health metrics, loading/failure
  isolation, escaped content and observed uptime.
- Isolated package installation includes the local CSS and reports clean content integrity.

CI repeats unit/lint/package checks on the configured Python matrix and both browser checks
on Python 3.12. Until CI completes, local results do not constitute a CI pass. The existing
production installation remains untouched.


## Flight Deck design revision

Claude implemented the visual design in a separate worktree, commits ec0e8f3 and c397ca9.
Codex integrated the design and added gauge/clock/poll regression checks. An independent
reviewer verified layout at 1280/1024/768/390/320 px and checked gauge and poll semantics.
The review found stale light-theme small-text contrast around 3.55–3.60:1 because whole
cards were dimmed. Claude fixed the issue by keeping text at full opacity/contrast and
applying stale feedback to decorative lights. A Chromium recheck measured station labels
at 6.45:1 and session-age text at 6.63:1, with card opacity 1 and no card filter.
The gauge subtitle now says availability,
which includes endpoint-backed daemons.

The ring uses native agent observations, with `alive && !needs_login`; separate plugin
tables are not folded into that gauge. The tmux-process count excludes daemons. The clock
uses browser-local time, and the countdown follows the next scheduled polling callback;
a manual refresh does not pretend to reschedule it. No new backend actions or auth changes
were introduced by the cockpit design. The preview remains unmerged and undeployed.
