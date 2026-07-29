# highvisor — architecture

This describes the *shape* of the system independent of language/library choice
(that's resolved in `03-research-findings.md`). The point of writing it first is
that the requirements — especially **background control** — should pick the
tooling, not the other way around.

## Layers

```
┌──────────────────────────────────────────────────────────────────────┐
│ 4. Brain adapters   claude / openai-api / chatgpt-web / script / repl  │
├──────────────────────────────────────────────────────────────────────┤
│ 3. Client surface   CLI  +  language clients  (talk RPC)               │
├──────────────────────────────────────────────────────────────────────┤
│ 2. Core engine      daemon: RPC server, target registry, session state,│
│                     action queue, observation capture, policy/limits   │
├──────────────────────────────────────────────────────────────────────┤
│ 1. Platform backend PlatformBackend interface  ─┬─ WindowsBackend      │
│                     (observe + act, per OS)      └─ MacBackend          │
└──────────────────────────────────────────────────────────────────────┘
```

Layers 1–2 are highvisor proper. Layer 3 is thin. Layer 4 is optional and
swappable — the whole reason the control surface must be brain-agnostic.

## 1. PlatformBackend — the interface that matters

Everything hard lives here. Two capability sets, each with a *focused* and a
*background* path:

**Observe**
- `list_targets()` → running apps + top-level windows (id, title, pid, bounds,
  focused?, visible?).
- `screenshot(target=screen|window|region)` → PNG bytes. Must be able to capture
  a **specific, occluded, or unfocused** window, not just the front screen.
- `inspect(target)` → semantic UI tree (role, name, value, bounds, actions) where
  the OS accessibility layer exposes it. Optional per-node; degrade gracefully.

**Act**
- `mouse(move|click|drag|scroll, at, button, target)`
- `key(down|up|press, keys, target)`
- `text(string, target)`
- `activate(target)` — bring to foreground (the fallback when background fails).

The `target` parameter is the crux. Two delivery modes per action:

| mode        | Windows                          | macOS                                   | works unfocused? |
|-------------|----------------------------------|-----------------------------------------|------------------|
| **global**  | `SendInput`                      | `CGEventPost`                           | no — needs focus |
| **targeted**| `PostMessage`/`SendMessage` → HWND, UIA `Invoke`/`Value` patterns | `AXUIElement` actions (`AXPress`, `AXSetValue`), `CGEventPostToPid` | often yes |

The backend picks **targeted** first when a target is named, falls back to
**global** (after `activate`) when targeted delivery is unsupported for that app.

### Background control — the tiered strategy (must-have)

For a given target+action, try in order and record which tier succeeded:

1. **Accessibility action** — semantic (`Invoke` a button, set a field value).
   Highest fidelity, most background-friendly, no coordinates. Win: UIA patterns.
   Mac: AXUIElement actions.
2. **Window message posting** — synthesize input to a specific window handle
   without focus. Win: `PostMessage(WM_KEYDOWN/CHAR/LBUTTONDOWN...)`. Mac:
   `CGEventPostToPid` (pid-targeted; effectiveness varies by app).
3. **Cooperative hook** — for targets whose source we control, the app polls a
   highvisor command channel (file/socket) and executes actions itself. This is
   the generalized `godot_cmd` pattern; 100% reliable but requires target buy-in.
4. **Activate + global input** — last resort: `activate(target)` then `SendInput`
   / `CGEventPost`. Steals focus and serializes the loop; **broadest compatibility but
   still target-dependent** (e.g. Unity/Qud ignores synthetic *keyboard* even after focus —
   see [`05-driving-input.md`](./05-driving-input.md)), so verify delivery, don't assume it.

Screenshots have their own background story: Win `PrintWindow`/DWM thumbnail or
Graphics.Capture; Mac `CGWindowListCreateImage` / ScreenCaptureKit can grab an
unfocused window. (The raves loop already proved Qud's own render-to-file works
while unfocused — that's tier-3 for observation.)

## 2. Core engine (the daemon)

> **Status:** this page began as a design sketch. **Framed JSON over TCP is implemented** (control
> daemon on `127.0.0.1:48720`), as is the core CLI vocabulary below. Items still described as design
> (sessions/replay, policy/limits, brain adapters, automatic tier fallback) are **planned** unless a
> later doc or the README capability table says otherwise.

- **Transport:** localhost RPC — **framed JSON over TCP, implemented** (exactly the raves bridge frame:
  `[4-byte BE len][UTF-8 JSON]`). The vocabulary below is transport-agnostic.
- **Target registry:** resolve stable target ids from `list_targets()`; cache
  handles; detect when a window dies.
- **Action queue:** serialize actions per target; timestamp; return an
  observation (or diff) after each so callers get a tight see→act→see loop.
- **Sessions:** a brain opens a session, scopes to one or more targets, gets a
  transcript of actions/observations (replayable — this is where "record/replay
  for humans" falls out for free).
- **Policy/limits:** rate limits, an allow-list of target apps, a dry-run mode.
  Safety rail so a runaway brain can't thrash the whole desktop.

### RPC vocabulary (the implemented `hv` verbs; abstract names map to the CLI, e.g. `screenshot`→`shot`, `list_targets`→`ls`, `mouse`→`click`)

```jsonc
// request:  {"op":"screenshot","target":{"window":"godot#1"},"region":null}
// response: {"ok":true,"png_b64":"...","meta":{"w":1600,"h":900,"tier":"capture"}}

// request:  {"op":"key","target":{"window":"godot#1"},"press":"Return","mode":"auto"}
// response: {"ok":true,"delivered":"post-message","tier":2}

// ops: list_targets, activate, screenshot, inspect, mouse, key, text,
//      session.open, session.close, session.replay
// every act response reports which delivery TIER actually worked — so the loop
// (and we) learn what a given app supports.
```

## 3. Client surface

- **CLI:** `hv shot godot#1`, `hv key godot#1 Return`, `hv ls`, `hv inspect …`.
  Thin wrapper over RPC; mirrors control.py's ergonomics.
- **Language clients:** a tiny lib per language as needed (Python first — matches
  existing tooling). Just frames requests; no logic.

## 4. Brain adapters (optional, swappable)

Each adapter turns high-level intent into RPC calls and feeds observations back:

- **claude** — Anthropic API (or wrap highvisor's RPC as an MCP server so Claude
  Code/desktop calls it natively).
- **openai-api** — OpenAI API with tool-calling bound to the RPC ops.
- **chatgpt-web** — drives the ChatGPT web UI via browser automation
  (selenium/puppeteer/playwright) as a *brain transport*. Note: this is the ONE
  place browser automation belongs; it does not control desktop targets.
- **script / repl** — deterministic sequences; also the test harness.

The switch ("Claude, OpenAI, both, or scripted") is just choosing which
adapter(s) hold the session. "Both" = two brains proposing actions into one
queue; needs an arbitration policy (open question — see research agenda).

## Data model (sketch)

- **Target** = { id, kind: app|window, pid, title, bounds, focused, visible }.
- **Element** = { role, name, value, bounds, actions[] } (from `inspect`).
- **Observation** = { ts, screenshot?, tree?, target, focused }.
- **Action** = { ts, op, target, params, result:{tier, ok, error?} }.
- **Session** = ordered list of (Action | Observation); replayable.

## Cross-cutting concerns to design for early

- **Permissions.** macOS TCC will demand *Accessibility* and *Screen Recording*
  grants; the daemon must detect missing grants and tell the caller precisely
  what to enable (not fail opaquely). Windows: UIPI/UAC integrity — a normal
  daemon can't send messages to an elevated window; document + detect.
- **Coordinate spaces.** DPI scaling, multi-monitor, Retina points-vs-pixels.
  Actions take window-relative coords where possible to survive moves.
- **Determinism/timing.** Expose explicit waits and "settled?" checks instead of
  `sleep`; the raves loop's read-back-until-file-updates pattern generalizes.
- **Failure transparency.** Every act reports the tier that worked (or that all
  tiers failed) so a brain can adapt and we can see per-app capability at a glance.
