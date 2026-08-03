# reviews/ — ChatGPT's content feedback lane

ChatGPT (the "content editor" in the highvisor agent loop) writes its
feedback here as dated markdown files, one per review:

    reviews/2026-07-28-docs-05-driving-input.md

**Why a separate lane:** ChatGPT and Claude both write files through their own
tools, which bypass highvisor — so highvisor can't enforce a write mutex between
them. Keeping ChatGPT's output in `reviews/` (and Claude's edits in source) means
there's no shared file to contend over. Claude reads a review, applies what's
useful to the source, and deletes the review file once addressed (like a ticket).

Each review should name the target file(s) and give concrete, quotable notes.

## Work-cycle review pass (see [`docs/09-work-cycle.md`](../docs/09-work-cycle.md) step 7)

When Claude asks ChatGPT to review the active projects' docs at the end of a cycle, ChatGPT drops these
alongside the per-doc reviews. Doc-specific suggestions go in each **repo's own** `reviews/`; the
cross-project rollups live here in highvisor.

| file | what |
|---|---|
| `reviews/<YYYY-MM-DD>-<slug>.md` | per-doc suggestions (the existing convention above). First line names the target doc path. |
| `reviews/seo-keywords.md` | SEO keyword list — terms real readers would search for. |
| `reviews/UPDATE.terse.md` | the cycle's changes in the fewest words (a changelog line or two). |
| `reviews/UPDATE.verbose.md` | the full write-up: what changed, why, how it fits. |
| `reviews/UPDATE.consumer.md` | reader/end-user framing — plain language, no internal jargon. |
| `reviews/CONTEXT.chatgpt.md` | ChatGPT's own re-session context (its "memory"), refreshed each cycle. |

**Principles ChatGPT is asked to follow:** lede first (easy + fast to understand up top, then link to the
full explanation — fix anything that buries it); good SEO (titles/headings/opening lines that match how
people search); new files only (never edit originals — that's Claude's integration step); editorial control
to ChatGPT, and Claude splits the difference. The per-doc reviews stay **transient/ticket-like** (deleted
once integrated); `CONTEXT.chatgpt.md` and the `UPDATE.*` rollups are **refreshed** each cycle, not deleted.
