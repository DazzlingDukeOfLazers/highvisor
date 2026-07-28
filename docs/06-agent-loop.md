# 06 — The agent loop (shortcode orchestrator)

highvisor as a **coordinator** between desktop AI agents: it reads one agent's
output, routes a human-readable opcode to another, and — gated by you — drives that
app. Built on the input/read primitives from docs/05 (click, `key --focus`) plus
`hv ocr`. Dated 2026-07-28; verified live (Claude ⇄ ChatGPT round-trip).

## The loop

    read → parse → gate → drive → reply → repeat

1. **read** — the orchestrator watches each registered agent's on-screen output:
   Claude Code via the AX tree (`inspect`), the ChatGPT desktop app via `ocr`
   (it exposes no usable AX). Polled ~3s.
2. **parse** — pull `hv:` opcodes out of that text (see grammar).
3. **gate** — each new opcode is held **pending** in the cockpit; nothing runs
   until you approve it (or approve its lane).
4. **drive** — on approval, act on the target: type a message into its composer +
   submit (`ask`), or click Claude's Approve/Deny buttons (`approve`/`deny`).
5. the target replies; its reply is read on the next cycle, and round it goes.

## Opcode grammar

A VISIBLE header line, a body, and a REQUIRED `hv: end` terminator:

    hv: <verb> <machine>/<agent>
    <payload / reason>
    hv: end

- **verbs:** `ask` (type payload + submit) · `approve` / `approve-all` / `deny`
  (click Claude's permission buttons; body = optional reason) · `key`.
- **target:** `<machine>/<agent>`, e.g. `mac/claude`, `mac/chatgpt`. Machine routes
  local vs. a bridge peer; agent maps to a window + I/O strategy (`AGENTS` registry).
- **`hv: end` is mandatory.** It is the one delimiter that survives rendering —
  code-fence ``` marks do NOT (AX and OCR both drop them when reading rendered
  text). More importantly, it is what makes the protocol safe to watch in a window
  that also *discusses* it: an `hv:` written in prose (a grammar example, this very
  doc) has no terminator, so it is never mistaken for a command.

## Reading: AX vs OCR

- **Claude Code** exposes a rich AX tree — its conversation text and its buttons
  (Approve/Deny appear when a tool prompt is up) are readable and clickable via
  accessibility.
- **ChatGPT desktop** is AX-opaque (just `AXWindow`/`AXGroup`), so it's read with
  `hv ocr` (on-device Vision). Its composer is OCR-located each call, so delivery
  survives font/layout changes.

## Gating

- New opcodes go to a **pending** queue, surfaced in the cockpit's PENDING panel
  with **Approve / Approve-lane / Deny**. Nothing executes un-approved.
- **Approve-all** promotes a `verb target` *lane* to auto-run future matches (use
  it once you trust a lane, e.g. `ask mac/chatgpt`).
- Everything — detections, verdicts, executions — is published to the event bus and
  shown in the log: a live, interruptible audit trail. `HIGHVISOR_ORCH=0` disables.

## Caveats learned live

- **Watching your own window means your prose is input.** A complete `hv: … hv: end`
  block written while *explaining* the protocol fires as a real opcode. `hv: end`
  keeps unterminated mentions safe; the gate catches the rest (deny). Rule of thumb:
  when writing example opcodes in a watched window, use a fake agent or omit `hv: end`.
- **The watcher reads the VISIBLE tree.** An opcode scrolled below the fold (or
  hidden behind a prompt) can be missed; nudge the window to the bottom. (A
  scroll-to-bottom before read is a planned hardening.)
- **Dedup is fingerprint-based** (verb+target+whitespace-normalized body) and the
  watcher **primes** on start (marks everything on-screen as already-seen), so
  scrollback isn't replayed.

## Files: lanes, not a mutex

Both agents write files through their *own* tools, which bypass highvisor — so it
**cannot enforce a write mutex** between them. Instead of a compliance-dependent
advisory lock, the lanes are separated: **ChatGPT reviews into `reviews/*.md`;
Claude edits source.** No shared file, no contention. See `reviews/README.md`.

## Try it

Emit (in a watched window):

    hv: ask mac/chatgpt
    Reply with only: PONG
    hv: end

→ it appears in the cockpit PENDING panel → approve → highvisor types it into
ChatGPT and submits → ChatGPT's reply is read next cycle.
