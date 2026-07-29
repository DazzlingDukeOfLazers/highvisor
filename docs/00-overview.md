# highvisor overview: background desktop automation, visual parity, and agent coordination

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

A service whose **control daemon is localhost-only**, exposing a small, stable vocabulary of **observe**
and **act** primitives over a CLI + local RPC, plus a **backend abstraction** so the same primitives work
on Windows and macOS. Cross-machine use is **optional and explicit**: an SSH tunnel exposes a remote
machine's *local* daemon securely, and a **disabled-by-default** LAN bridge carries token-gated
context/log/screenshot *data* — never control (see [`04`](04-web-and-bridge.md), [`07`](07-ssh-transport.md)).

**Runtime today** — the working path is a watched desktop **orchestrator**, not the API/web brain adapters
below:

```
  watched desktop agent  ──▶  parser / gate (cockpit)  ──▶  local Engine  ──▶  target window
  (Claude via AX tree,        human approves each          observe + act        (focused OR
   ChatGPT via OCR)           opcode / lane                 primitives            background)
```

The swappable **brain-adapter** architecture (Claude API, OpenAI API, ChatGPT web, sessions) is the
**planned** design — see [`01-architecture.md`](01-architecture.md):

```
  brain (PLANNED)                  highvisor                     targets
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
- **Driver = pluggable brain (planned).** The design allows switching control to Claude, OpenAI, both, or
  scripted. *Historical note (2026-07-27):* the first OpenAI hookup was expected to drive the ChatGPT **web**
  interface as a *brain transport*. The path that actually shipped is different: the desktop
  **orchestrator** reads ChatGPT through **OCR** and Claude through the **AX tree** (see
  [`06-agent-loop.md`](06-agent-loop.md)); the API/web adapters remain unbuilt.
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
- Not a **cloud-hosted** service — the control daemon is localhost. (Cross-machine use *is* supported, but
  only via an explicit SSH tunnel or the opt-in data bridge — see "What it is".)
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

## Operating it (quickstart)

The daemon runs on localhost; the CLI is the front door. From the repo:

```
python3 -m highvisor.cli <cmd>      # or: hv <cmd> if installed on PATH
```

Host/port default to **127.0.0.1:48720** — you never need `--host`/`--port` on this
machine. Confirm the daemon is up with `nc -z 127.0.0.1 48720` (someone has to have
started `python -m highvisor.server` first). Everyday commands:

- `ls` — list windows; each row is `win:<ID>  pid=…  W×H  <title>`. Target the other
  commands by `win:<ID>`.
- `launch <name>` — start a registered app (`launchers` lists them); prefer this over
  the OS launcher so highvisor owns the process.
- `activate win:ID`, `shot win:ID <out.png>`, `click [--hover] win:ID x y`.
- `probe --app qud` — is a known app off / at its menu / in-game.

If a call throws `OSError: [Errno 49] Can't assign requested address`: the default host is already
loopback, so it is **not** a `--host` config error. `Errno 49` (`EADDRNOTAVAIL`) has several possible
causes (ephemeral-port exhaustion under churn, a stale bind, a bad address) — **retry first**, and if it
persists, check that the daemon is up and bound to `127.0.0.1:48720` rather than assuming a single cause.

## Where to go next — by what you want to do

| I want to… | Read |
|---|---|
| Use it on **one app** | the [README](../README.md) quickstart → [`05-driving-input.md`](05-driving-input.md) |
| **Compare two apps** / catch UI regressions | [`08-parity-kit.md`](08-parity-kit.md) |
| **Coordinate AI agents** (Claude ↔ ChatGPT) | [`06-agent-loop.md`](06-agent-loop.md) → [`09-work-cycle.md`](09-work-cycle.md) |
| Reach **another machine** | [`07-ssh-transport.md`](07-ssh-transport.md) (SSH); [`04-web-and-bridge.md`](04-web-and-bridge.md) only for the optional LAN bridge |
| **Understand / extend** the design | [`01-architecture.md`](01-architecture.md) → [`03-research-findings.md`](03-research-findings.md) (`02` is the historical agenda) |

All pages, in order: `00` overview · `01` architecture · `02` research agenda · `03` findings · `04` web +
bridge · `05` driving input · `06` agent loop · `07` SSH · `08` parity kit · `09` work-cycle loop.
