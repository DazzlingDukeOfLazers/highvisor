# 09 — The work-cycle loop

The repeatable loop for each larger work cycle, so any session (Claude included) stays oriented and
nothing gets skipped. **This file is the editable source of truth — either Daniel or Claude may edit it.**
Change the loop by changing this list.

> Fast version: **goal → prototype → tools → test → goldens → docs+memory → ChatGPT doc-review → integrate.**
> Each step below says *how*, with this project's actual tools and hard-won rules.

## The loop

1. **Goal** — Daniel states the goal. Log it in a per-cycle entry (template at the bottom).

2. **Prototype (LLM + code).** Explore first, don't guess. Reflect Qud APIs against the shipped assembly
   (`dotnet build` / `ilspycmd`), and **prototype algorithms in Python before porting** (`tools/capture/*.py`
   — the Python-first rule). Measure before hypothesising; know which build is running.

3. **Build deterministic, reusable tools.** Factor throwaway steps into tools that re-run the same way every
   time (`control.py`, `presets.py`, `hv` commands), behind the platform seam where OS-specific. No silent
   caps — `log()` anything you bound (top-N, no-retry, sampling).

4. **Test the goal round with highvisor.** Drive live (`hv launch/activate/click[--hover]/shot/probe`), set
   deterministic state with **option presets** (`presets.py load <name>`; see
   [`08-parity-kit.md`](./08-parity-kit.md) and raves `tools/regression/presets/`), and read back the result
   (`shot.png` / `qud_shot.png` / `selection.txt` / snapshot JSON). Verify yourself; never ask Daniel to eyeball it.

5. **Create/refresh golden files.** `hv scene <name> --bless` to establish or update the golden; add the scene
   to `scenes.json`; wire preset-driven setup via a `shell` step (live Qud state) or the `load → launch → scene`
   pattern (Raves settings). Re-bless only on an *intended* visual change.

6. **Document + update memory.** Repo docs (`CLAUDE.md`, `docs/*`, READMEs) and memory files (project state,
   feedback, non-obvious facts — not what the repo already records). **Commit + push each round once it builds**,
   after the allspice author guard (`git log … | grep -i allspice` empty).

7. **Hand off to ChatGPT for a documentation review.** Emit the [opcode](#step-7-the-chatgpt-doc-review-handoff)
   so highvisor delivers the review request to ChatGPT. ChatGPT reviews the active projects' docs, returns
   suggestions under the [review conventions](../reviews/README.md), and maintains its own re-session context.

8. **Review + integrate.** Read ChatGPT's review files, take what improves the docs (ChatGPT has editorial
   control; you split the difference), fold it into the canonical docs, then clear the consumed review files.
   Loop back to step 1 for the next goal.

## Step 7: the ChatGPT doc-review handoff

Emit this block in your reply. The orchestrator ([`06-agent-loop.md`](./06-agent-loop.md)) types the body into
ChatGPT's composer and submits (gated — it waits in the cockpit until approved).

> **Arming note:** the terminator below is written `hv: END` (uppercase) on purpose so *documenting* it here
> doesn't arm the orchestrator. To actually fire it, emit the block with a lowercase `hv: end`.

```
hv: ask mac/chatgpt
Claude here. Please review all the docs across our active projects — raves-of-qud and highvisor — and
propose improvements. Assume editorial control; I'll read everything and split the difference.

- Put suggestions in NEW files (don't edit the originals) per each repo's reviews/README.md:
  reviews/<YYYY-MM-DD>-<slug>.md, first line = the target doc path.
- Optimise for "easy + fast to understand FIRST, then links to the full explanation." Fix anything that
  buries the lede.
- Use good SEO practice; if useful, create reviews/seo-keywords.md.
- Produce three update files at different depths: reviews/UPDATE.terse.md, reviews/UPDATE.verbose.md,
  reviews/UPDATE.consumer.md.
- When you finish, create/update reviews/CONTEXT.chatgpt.md so we can re-session with the same context.
hv: END
```

(Remote ChatGPT would be `floorputer/chatgpt` instead of `mac/chatgpt`. ChatGPT-web can't write disk directly,
so its output comes back as labeled blocks — Claude saves them to the paths above; the bridge context-handoff
automates the copy where available.)

## Per-cycle log (copy per goal)

```
### <cycle name> — <date>
- goal:
- prototype/tools built:
- tested (how) + goldens:
- docs/memory touched:
- ChatGPT review: requested? integrated?
- status: in-progress | done
```
