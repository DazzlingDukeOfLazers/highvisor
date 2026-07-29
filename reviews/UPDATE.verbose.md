# Documentation review update — 2026-07-28 (verbose)

## What was reviewed

The active documentation surfaces in:

- `/Users/homefolder/personal-git/highvisor`: root README, numbered docs, loop startup context, and review convention.
- `/Users/homefolder/personal-git/raves-of-qud`: root README, `CLAUDE.md`, subsystem docs, merge runbook, and regression-preset README.

No source documentation was edited. New review tickets were written under each repository’s `reviews/`.

## Editorial direction

The recommended information order is:

1. State what the tool/product does in the reader’s language.
2. Give the smallest verified path to a useful result.
3. State prerequisites, status, limitations, and safety boundaries.
4. Offer a task-based documentation map.
5. Link to detailed architecture, implementation, and historical reasoning.

This preserves the hard-won engineering record without forcing a first-time reader to consume it before succeeding.

## Highest-impact highvisor changes

- Turn README/overview into a task-oriented entry point with one canonical first-success sequence.
- Add a current capability matrix and distinguish implemented, partial, planned, and historical material.
- Scope absolute background-control claims by OS/app class and treat event posting as best-effort until verified.
- Reconcile Qud click guidance: bare click, hover/pre-move behavior, and UI surface must be described as a matrix.
- Put LAN bridge bind/authentication/encryption boundaries before feature detail.
- Add explicit executable-config warning for parity scene `shell` steps.
- Keep agent startup cards minimal and link to one canonical protocol contract.

## Highest-impact raves-of-qud changes

- Reduce the 590-line README to product description, prerequisites, quickstart, current capabilities, limitations, and documentation map.
- Reduce `CLAUDE.md` to behavioral instructions and daily commands; move debugging history to durable subsystem/decision docs.
- Declare canonical owners for duplicated facts (camera controls, protocol, rendering, tool behavior).
- Fix known drift:
  - camera modes 1–6 vs 1–7;
  - Escape resets to COMPASS vs keeps current mode;
  - synthetic mouse pre-move/click-state requirements vs rejection;
  - old “Claude cannot capture” claims after highvisor window capture;
  - protocol color parsing by trailing letter vs current foreground-before-`^` rule;
  - “unshaded world” absolutes vs optional shaded geometry.
- Make the protocol a normative wire contract: limits, types, required fields, lifecycle, compatibility, errors, thread/execution semantics, and localhost-only security.
- Separate roadmap status from historical design, and label multiplayer clearly as an unimplemented proposal.
- Add the missing raves-of-qud `reviews/README.md` convention.

## SEO approach

Use plain search phrases in page titles, first paragraphs, and task headings—never as a keyword dump. Lead pages should name the category and outcome:

- highvisor: desktop automation, background window control, macOS Accessibility automation, Windows UI Automation, screenshot diff, golden image testing.
- raves-of-qud: Caves of Qud 3D viewer, Godot front end, Caves of Qud mod, 2.5D roguelike viewer, legacy game integration.

Detailed keyword-to-page recommendations are in `reviews/seo-keywords.md`.

## Suggested integration order

1. Fix factual contradictions and overbroad security/accuracy claims.
2. Establish canonical source pages and replace duplicates with links.
3. Rewrite README first screens and verified quickstarts.
4. Add status labels to research, roadmap, multiplayer, and historical runbooks.
5. Split encyclopedic material from README/`CLAUDE.md`.
6. Apply heading/title/lede SEO improvements after information architecture stabilizes.

## Review coverage note

Some documents did not receive a standalone ticket because their issues are covered by a combined ticket or cross-project rule. The audit still included them. Highvisor’s `docs/02` + `docs/03` and all `loop/*` are intentionally grouped. Raves’ missing review convention has a proposed-target ticket rather than a source-file review.
