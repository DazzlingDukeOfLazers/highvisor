# highvisor — overview

> A local **supervisor for the desktop UI layer**: one service that can *observe*
> and *control* native applications on Windows and macOS, driven by a swappable
> "brain" (Claude, OpenAI, both, or a plain script). Named for *hypervisor* — it
> sits above the apps, watches them, and moves their hands.

## Why this exists

Automated debug loops keep re-inventing the same wheel, badly. In the
`raves-of-qud` project the loop looks like this:

- an outside agent needs to **see** the app (screenshot / UI state),
- **act** on it (move, click, type),
- and do so **while the app is unfocused** — because a human is not sitting at
  the keyboard and other windows have focus.

We solved it there with a pile of one-offs: a mod that streams state over a
socket, a `control.py` that pokes the game, a `godot_cmd` file that Godot *polls*
because it ignores background keystrokes, and the platform `computer-use` MCP
(which needs per-action human approval and can't reach a background window).
Every project rebuilds this. **highvisor is the reusable version.**

## What it is

A single-machine, localhost-only service that exposes a small, stable vocabulary
of **observe** and **act** primitives over a CLI + local RPC, plus a **backend
abstraction** so the same primitives work on Windows and macOS, plus **brain
adapters** so the caller can be an AI or a script.

```
  brain (swappable)                highvisor                     targets
  ┌───────────────┐   RPC     ┌───────────────────────┐      ┌────────────┐
  │ Claude (API)  │──────────▶│  core engine          │─────▶│ any native │
  │ OpenAI (API)  │           │  - targets/sessions   │ obs  │   app      │
  │ ChatGPT (web) │◀──────────│  - action queue       │◀─────│ (focused OR│
  │ script / CLI  │  observ.  │  - platform backend ──┼──────│  background)│
  └───────────────┘           └───────────────────────┘      └────────────┘
```

## Decided (2026-07-27)

- **Interface:** CLI + a local RPC daemon (same spirit as the raves bridge —
  language-agnostic, scriptable, no hard MCP dependency). MCP can wrap the RPC
  later as one more caller.
- **Driver = pluggable brain.** Control can be switched to Claude, OpenAI, both,
  or scripted. The first OpenAI hookup may drive the **ChatGPT web interface**
  rather than the API — so a browser-automation adapter is in scope as a *brain
  transport*, distinct from controlling desktop *targets*.
- **Background / unfocused control is a first-class, must-have requirement**, not
  a stretch goal. This is the single hardest constraint and it drives most of the
  architecture (see `01-architecture.md`). It rules out "just synthesize global
  input and hope the right window has focus."

## Goals

1. **Observe** any app: screenshot (whole screen, a window, or a region) and,
   where available, a semantic UI/accessibility tree.
2. **Act** on any app: mouse (move/click/drag/scroll), keyboard (keys/text),
   and — critically — do so against a **specific, possibly unfocused** window.
3. **Cross-platform** Windows + macOS behind one API; per-OS backends hidden.
4. **Brain-agnostic:** the control surface is identical whether an AI or a shell
   script is calling it.
5. **Debug-loop ergonomics:** fast round-trips, structured output, deterministic
   enough to script; the loop is the product.

## Non-goals (for now)

- Not a general-purpose RPA / macro product for end users.
- Not a cloud or multi-machine service — localhost, single box.
- Not tied to one target app (the raves bridge was; highvisor is the opposite).
- Not trying to defeat anti-automation (games with DirectInput, DRM, CAPTCHAs).
  When a target refuses injected input, we fall back to a **cooperative hook**
  (see architecture) rather than fighting the app.

## The one hard truth to keep in view

"Control an unfocused window" is not uniformly possible. Native message-posting
(Win32 `PostMessage`, macOS Accessibility actions) reaches *many* apps without
focus, but some (fullscreen games, DirectInput, elevated processes) ignore it.
highvisor's answer is a **tiered strategy**: try the cheapest method that works
for the target, and expose a **cooperative-target** path for apps we control the
source of (like the Godot viewer) so they can poll a highvisor command channel —
generalizing the `godot_cmd` trick instead of leaving it per-project.

## Reading order

1. `00-overview.md` — this file.
2. `01-architecture.md` — components, RPC vocabulary, per-OS backend strategy.
3. `02-research-agenda.md` — the open tooling questions to resolve before we pick
   a stack.
4. `03-research-findings.md` — filled in after research; the stack recommendation.
5. `04-web-and-bridge.md` — the web cockpit + the cross-machine LAN bridge.
6. `05-driving-input.md` — the tier ladder + the synthetic-click gotcha (Unity/Qud).
7. `06-agent-loop.md` — the human-gated Claude↔ChatGPT opcode loop.
8. `07-ssh-transport.md` — tunnelling a remote highvisor over SSH.
9. `08-parity-kit.md` — visual parity & regression tooling (drive both, diff, golden).
