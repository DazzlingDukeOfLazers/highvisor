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
