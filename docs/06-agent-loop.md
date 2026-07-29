# 06 — The agent loop (shortcode orchestrator)

highvisor as a **coordinator** between desktop AI agents: it reads one agent's
output, routes a human-readable opcode to another, and — gated by you — drives that
app. Built on the input/read primitives in [`05-driving-input.md`](./05-driving-input.md)
(click, `key --focus`) plus `hv ocr`. Dated 2026-07-28.

*(This page's grammar examples are deliberately written with an uppercase `hv: END`
so they are **not** live opcodes if this doc is ever read inside a watched window —
see §Try it.)*

> **Use this when** you want to relay short, **human-approved** tasks between two visible desktop agents
> (e.g. Claude ↔ ChatGPT on one screen). It is **not** a secure message bus or a delivery guarantee.
> Cross-*machine* agent routing is **planned over SSH** ([`07-ssh-transport.md`](./07-ssh-transport.md)),
> not the plaintext LAN bridge.

## The loop

    read → parse → gate → drive → reply → repeat

A highvisor **watcher** (the `Orchestrator`, a daemon thread) does read+parse; the
**cockpit** (`http://127.0.0.1:48721`) is its human-control surface where detected
operations are approved or denied; after approval highvisor **drives** the target.

1. **read** — watch each registered agent's on-screen output: Claude Code via the
   AX tree (`inspect`), the ChatGPT desktop app via `ocr` (no usable AX). ~3s poll.
2. **parse** — extract `hv:` opcodes (see grammar).
3. **gate** — each new opcode waits **pending** in the cockpit; nothing runs until
   you approve it (or its lane is pre-approved).
4. **drive** — type a message into the target's composer + submit (`ask`), or click
   Claude's Approve/Deny buttons (`approve`/`deny`).
5. the target replies; its reply is read next cycle. **Approved ≠ delivered** — see
   §Delivery is best-effort.

## Opcode grammar

    hv: <verb> <machine>/<agent>
    <payload / reason>
    hv: end

**Parser contract** (`orchestrator.parse_opcodes`):
- The `hv:` header and the `hv: end` terminator each occupy their own line. A line
  may have leading whitespace or `>` before `hv:` (quote-tolerant); it need not be
  column 1. The header's `hv` is case-insensitive; **verbs and targets are
  lowercase**.
- The body is every line between the header and the **first** following `hv: end`.
  An empty body is allowed (e.g. a bare `approve`).
- **A terminator is mandatory.** An `hv:` with no following `hv: end` is not an
  opcode — this is what keeps unterminated *mentions* (prose, this doc) from parsing
  as commands. It does **not** make a complete, well-formed block inert: any visible
  well-formed block is input (see §Trust model).
- Multiple opcodes per read are fine (each header pairs with the next unused
  `hv: end`). Malformed/partial blocks are silently ignored, never queued.

**verbs**
- `ask` — type the body into the target's composer and submit.
- `approve` / `deny` — click Claude's permission button matching the verb (body =
  optional reason, logged). `approve-all` clicks Claude's *"always allow"* control.
  **Do not confuse the `approve-all` opcode with the cockpit "Approve lane" verdict**
  (§Gating) — different operations.
- `key` — send a key/chord to the target after focusing it (`OP_KEY --focus`). Body
  is one key spec, e.g. `return`, `cmd+a`, `esc`. Broader effect than `ask`, so gate
  it carefully. Unknown specs are rejected, not typed literally.

**target** — `<machine>/<agent>`. `mac` is the local machine; the agent maps to a
window + I/O strategy in the `AGENTS` registry ([`highvisor/orchestrator.py`](../highvisor/orchestrator.py)).
Remote machines would route over the bridge ([`04-web-and-bridge.md`](./04-web-and-bridge.md));
only the local `mac/*` route is implemented/tested today.

## Reading: AX vs OCR

- **Claude Code** exposes a rich AX tree — conversation text and buttons
  (Approve/Deny appear when a tool prompt is up) are readable and clickable via AX.
- **ChatGPT desktop** is AX-opaque, so it's read with `hv ocr` (on-device Vision).
  Its composer is located from the current screen image each call rather than a fixed
  coordinate, which tolerates layout/font changes — but OCR can still fail when the
  composer is obscured, off-screen, visually ambiguous, or rendered unexpectedly.

## Gating

- Newly detected opcodes wait in the **pending** queue (cockpit PENDING panel:
  **Approve / Approve lane / Deny**). **By default nothing executes unapproved.**
- **Approve lane** promotes a `verb target` lane (e.g. `ask mac/chatgpt`) to auto-run
  future matches *without* another per-command approval. Lanes live in the running
  process only — a daemon restart clears them (no persistence yet), and there is no
  UI to revoke a lane mid-session short of restart. Grant a lane only when **both**
  verb and target are safe for unattended execution.
- Every detection, verdict, and execution is published to the event bus and shown in
  the log — a live, interruptible audit trail. `HIGHVISOR_ORCH=0` disables the loop.

## Trust model

**Treat all visible agent output as untrusted protocol input** — not just prose you
type, but any agent reply, pasted document, web page, tool output, or quoted message
in a watched window may contain a well-formed opcode. Two layers contain this:

1. the `hv: end` terminator (stops incomplete mentions from parsing), and
2. **the gate is the actual authorization boundary** — a well-formed block still
   reaches pending; you decide.

Approve-lane materially raises the stakes: matching commands bypass per-command
review. This is why lanes should be narrow and obviously-safe.

Two live-learned corollaries:
- **Watching your own window means your prose is input.** A complete `hv: … hv: end`
  written while *explaining* the protocol is a real opcode; the gate caught exactly
  this during development (denied). Rule of thumb: examples use a fake agent or
  `hv: END` (uppercase) so they don't arm.
- **The watcher reads the VISIBLE tree.** An opcode scrolled below the fold or hidden
  behind a prompt can be missed; nudge the window to the bottom (a scroll-to-bottom
  before read is planned hardening).

## Dedup

Fingerprint = verb + target + whitespace-normalized body. Scope is the running
process (no persistence, no expiry); a restart forgets all fingerprints, and the
watcher **primes** on start (marks everything on-screen as already-seen) so
scrollback isn't replayed. Consequence: two *intentionally identical* asks dedup to
one within a session — vary the body to repeat.

## Delivery is best-effort

An approved operation is not a confirmed delivery. `deliver`/`press` return
`{ok, error}` and publish an `exec …` line to the log; a missing window, an
un-found composer, or (for `approve`/`deny`) no on-screen prompt returns `ok:false`.
There is **no automatic retry**, so a failed op will not silently look like a closed
loop — but re-issuing an `ask` after a partial failure can duplicate it. Watch the
log line, not just the approval.

## Try it

Emit this in a watched window — **but first lowercase the terminator**: this block
ships with an uppercase `hv: END` so it's inert; change it to `hv: end` only when you
actually want to submit it.

    hv: ask mac/chatgpt
    Reply with only: PONG
    hv: END

→ it appears in the cockpit PENDING panel → approve → highvisor types it into ChatGPT
and submits → ChatGPT's reply is read next cycle.

## Verified

2026-07-28, local `mac/claude` ⇄ `mac/chatgpt` `ask` round-trip with manual gating
(watcher detect → pending → approve → deliver → reply). Other verbs (`approve`/`deny`
against a live Claude prompt), lane persistence, and bridge routing are **not** yet
exercised end-to-end.
