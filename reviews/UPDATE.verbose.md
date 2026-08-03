# Documentation review update — 2026-07-28 (Round 2, verbose)

## Outcome

Round 1’s restructuring worked. Both projects now have usable front doors, clearer status labels, and better canonical subsystem pages. This pass therefore focused on a harder standard: whether normative prose agrees with the current implementation.

No source documentation was edited. Seventeen new dated tickets were written under the target repositories’ `reviews/` directories:

- raves-of-qud: 8 tickets;
- highvisor: 9 tickets.

A local Markdown path scan found no missing repository-relative target files. Remaining cross-link problems are semantic: a link reaches a page successfully, but that “canonical” page can still contain obsolete behavior.

## Highest-impact findings

### 1. Raves’ protocol is not currently normative

`docs/protocol.md` says frames can be 4 GiB, omits most current snapshot blocks, lists fewer than half the implemented commands, conflates Qud’s turn thread with Unity’s graphics main thread, repeats the rejected trailing-letter color rule, and calls already-shipped stats/messages “deferred.”

The implementation says:

- inbound commands are capped at 16 MiB;
- snapshots include game/build/timing, structured zone coordinates, world terrain, stats, target/context, abilities, messages, palette, and cells;
- six commands are consumed inline on the socket reader and seven more are queued;
- graphics work alone is marshalled through `uiQueue`.

Fix this before expanding the protocol further.

### 2. Raves’ roadmap opens with the opposite of current reality

The page says “No code yet” and “not started,” then later says Phase 0 is done and Phase 1 landed. `WorldStore.gd`, persistence tests, `gameId`, structured zone coordinates, remembered neighbors, and disk persistence confirm the latter.

Make the first screen a shipped/partial/planned status matrix. Cut completed “smallest first steps” and move old design prose into history.

### 3. Highvisor’s machine-qualified opcode boundary is unsafe in code and docs

The parser records `<machine>/<agent>`, but execution ignores `machine`. An approved `floorputer/claude` opcode can therefore resolve to the local Claude entry. Documentation must not imply remote routing until code rejects non-`mac` machines or implements an explicit route.

This is the most important highvisor finding.

### 4. Highvisor’s architecture examples do not match the RPC

`docs/01-architecture.md` labels its examples implemented but uses an object target, `press`, `mode:auto`, response metadata the engine does not return, and unimplemented session operations. Replace them with payloads accepted by `Engine._dispatch`.

### 5. “Delivered” and `ok:true` overstate raw input guarantees

For several key/click paths, highvisor knows the OS call/event post completed; it does not know the app reacted. The agent loop compounds this: `deliver()` ignores the text and submit action results and returns success.

Use three states consistently:

1. approved;
2. posted;
3. confirmed by readback.

Only the third is end-to-end delivery.

### 6. Bridge screenshot consent is broader than the docs say

Once the bridge is enabled and a peer has the shared token, that peer can issue `shot_req`; no local per-request approval occurs. “Opt-in” currently means bridge/token opt-in, not screenshot-by-screenshot consent.

### 7. Several implementation-status claims need provenance

- highvisor currently uses `CGWindowListCreateImage`, `PrintWindow`, and `BitBlt`, not the researched ScreenCaptureKit/DWM/Windows.Graphics.Capture alternatives.
- `ssh-doctor` is documented but not implemented.
- Windows capability checkmarks need an environment/date evidence record.
- Raves’ `Protocol.Build` has not kept up with later mod changes despite a mandatory-bump rule.

## What to restore versus cut

### Restore

- Raves `CLAUDE.md`: one row for `docs/qud-api.md`.
- Raves `CLAUDE.md`: the allspice author guard next to push instructions.
- Roadmap: concise acceptance criteria for the next unfinished Phase 1 slice.
- Research/capability claims: explicit tested OS/app/version/date evidence.

### Cut or rewrite

- fixed `dd/mac` / `dd/pc` branch claims from `CLAUDE.md`;
- “Claude cannot see/send keys” explanations now superseded by highvisor;
- raves protocol’s trailing-letter color rule and shipped-as-deferred items;
- roadmap’s “No code yet,” completed first step, and duplicate progress narratives;
- highvisor’s unsupported Codex benchmark/market paragraph;
- nonexistent `ssh-doctor` as a current command;
- universal synthetic-click recipes;
- lowercase live opcode examples in watched startup cards.

Do **not** restore the long README data-model dump or the 494-line `CLAUDE.md`. Round 1’s condensation is the right direction.

## Docs that are holding up

No standalone corrective ticket was needed for:

- raves `docs/cameras.md`: seven modes and Escape behavior are now canonical and consistent;
- raves `docs/rendering.md`: mental model, current shading flag, glossary, and rejected-approaches appendix are useful (appendix numbering is a low-priority cleanup only);
- raves `docs/multiplayer.md`: clearly labeled proposal;
- raves debugging-decisions and presets references: appropriate durable homes for detail;
- highvisor `docs/02-research-agenda.md`: historical status is clear enough;
- highvisor `docs/08-parity-kit.md`: fast path, executable-scene warning, and limits are clear;
- highvisor `docs/09-work-cycle.md`: strong overall, with only the routing/completion-gate corrections grouped into the startup-card ticket.

## Integration order

1. Enforce/document the orchestrator machine boundary and propagate delivery-stage failures.
2. Repair raves `docs/protocol.md`.
3. Rewrite raves roadmap status from current code.
4. Correct highvisor RPC examples and input-result terminology.
5. Correct bridge screenshot trust and nonexistent/current feature labels.
6. Fix README quickstart/rendering issues and evidence labels.
7. Apply small CLAUDE/tools/gotchas/legacy-playbook corrections.
8. Apply SEO title/lede changes after factual contracts are stable.

## SEO

Both root README titles are now substantially better. Remaining opportunity is in specialist pages: put the searchable category in H1/opening copy (“Qud thread model,” “human-in-the-loop agent coordination,” “remote desktop automation over SSH”) and keep project jargon as the secondary phrase. See `reviews/seo-keywords.md`.
