# Working notes for Claude (and future humans)

highvisor = the localhost desktop-UI supervisor: observe + drive native apps (focused or NOT)
through one daemon. The raves-of-qud parity work is its first consumer; the design goal is to be
reusable for ANY app pair. Cockpit web UI on **:48721**, daemon RPC on **48720**.

## THE PRIME RULE — drive apps through highvisor, never by hand

Manual `open`, raw AppleScript window moves, and hand-rolled click seances are how sessions
thrash. The `hv` commands are the interface; when one is missing or broken, **fix highvisor**
(that's this repo) rather than working around it — the workaround dies with the session, the
fix compounds. Concretely:

| instead of… | use |
|---|---|
| `open <app>.app` / steam URL | `hv launch raves` (pair), `hv launch raves_solo` / `qud_solo`, or the cockpit ▶ buttons |
| AppleScript / manual window moves | `hv move` (readback-verified), cockpit slot buttons |
| screenshot-then-guess "what screen is it on?" | `hv state` (first-party scene reports), `hv probe` |
| click-drive to a known screen by hand | `hv goto <app> <node>` (planned route over the transition graph) |
| guessing what a goto will do | `hv plan <app> <node> [--from <state>]` — the route, driving nothing |
| sleep-and-hope waiting for a state | `hv assert --app qud --node in_game --timeout 20` |
| pkill/osascript restart seances | `hv restart qud` / `hv restart raves` (kills ALL instances incl. duplicates, relaunches, waits for the app to REPORT) |
| blind top-row Continue clicks | `hv loadsave <name>` (row computed from DISK metadata — `hv saves` lists them, no game launch) |

The daemon **self-restarts** (2026-08-03): a source watcher re-execs it when any highvisor `.py`
changes (edit → it picks itself up in ~2s), and the launchd KeepAlive agent is INSTALLED
(`com.highvisor.daemon`) so a crash respawns it — verified by `kill -9`: new pid, port listening
~5s later. Logs: `~/Library/Logs/highvisor.log`.

**The agent runs Highvisor.app, and that is not cosmetic.** macOS attributes Screen Recording to
a *responsible process*: a daemon started from a terminal inherits the terminal's grant, the
identical binary started by launchd does not. Measured A/B, same venv interpreter: launchd-spawned
saw every window title as blank, shell-spawned read them fine — and the symptom surfaces three
layers away as `no window for app 'raves'` or `text 'continue' not on screen` while looking
straight at Continue. `tools/make_app.sh` builds `build/Highvisor.app`: an ad-hoc-signed bundle
whose tiny C stub **forks** the venv python and stays alive as its parent (an `execv` stub would
defeat the point — the process image would become the interpreter again, and responsibility flows
parent→child). Grant "Highvisor" Screen Recording ONCE and it survives venv rebuilds, Python
upgrades and every source edit. `hv install-daemon` builds it if missing, kills any manually-run
daemon (port clash), bootstraps, then **verifies capture works** and prints the remedy if not.
`hv ls` warns too: several windows and not one title is the signature. **You cannot check that by PID or uptime** — `os.execv` replaces the
process image in place, so both are PRESERVED across a re-exec. A daemon showing hours of uptime
may well be running code you saved a minute ago. To tell, grep the daemon log for
`source changed: … — re-exec`, or call an op and look for behaviour only the new code has. (Cost
of learning this the other way: a wrong "the watcher is broken" conclusion, and a pointless manual
daemon restart.) If `nc -z 127.0.0.1 48720` fails now, the AGENT is down, not just the process —
`launchctl print gui/$(id -u)/com.highvisor.daemon` first, and check the log before
restarting anything by hand.
`webui/` static files still only need a browser reload.

## THE TIMESHARE GUARD — this machine is shared

Every focus/mouse-stealing op (activate, key --focus, click, text) runs inside a guard session
(`highvisor/guard.py`): it remembers the frontmost app + mouse position, plays a 3-ping audio
countdown before taking control (only when interrupting a NON-game app), restores focus + mouse
and plays a return cue when the session idles out (~8s) or hits the **20s hard cap**. Panic
channels: the cockpit 🛑 ABORT button, `hv abort`, `touch ~/.config/highvisor/ABORT`, or
**Ctrl+Opt+Cmd+H** anywhere — all release immediately and refuse control ops for 30s. Disable
for unattended runs: `touch ~/.config/highvisor/guard_off`.

## Map

| file | what |
|---|---|
| `highvisor/engine.py` | op dispatch + gamestate/gamego/assert logic |
| `highvisor/backends/darwin.py` | macOS backend (AX move/click/key, ScreenCaptureKit shots, OCR) |
| `highvisor/gametree.json` | THE canonical game state tree: per-node `detect` (how we know we're there) and `done` (1:1 score), plus the `transitions` graph, `costs` and `preflight`. Hot-reloads. |
| `highvisor/gametree.py` | tree loader + state evaluator (deepest match wins; detect OR-lists) |
| `highvisor/plan.py` | the route planner over `transitions` — pure data + search, no backend |
| `highvisor/cli.py` | the `hv` CLI (`~/bin/hv` wrapper runs it from any cwd) |
| `highvisor/webui/` | the cockpit (vanilla JS; served from disk, reload to pick up) |
| `~/.config/highvisor/launch.json` | machine-local launchers (`qud`, `qud_solo`, `raves`, `raves_solo`) |

## State detection — first-party beats OCR

The apps REPORT their own UI state into `~/Library/Application Support/RavesOfQud/`:
`raves_state.json` (Raves' UiState autoload) and `qud_state.json` (the mod's heartbeat thread),
each `{scene, …, ts}`, rewritten ~1-2s. The engine trusts a file while its mtime is fresh
(`STATE_FILE_TTL`), evaluates it as the `scene` signal in gametree detect conditions, and falls
back to OCR/port signals when stale. **When detection is wrong, teach the app to report the
scene** (add a `UiState.set_scene` call / extend the mod heartbeat) rather than piling on OCR
substrings.

**Reports are PER-PROCESS.** A shared path has one writer per running instance: three live Raves
had `raves_state.json` cycling `in_game → status_tinkering → title` every 2s, so every read was a
coin flip — that, not a reporter bug, is why the tree "lied" and `hv goto raves in_game` needed
retries (with two windows up, `_find_win` can hand a recipe a different window than the one being
read). Raves now stamps `pid` and also writes `raves_state.<pid>.json`; the engine reads the
sidecar for the pid owning the window it is evaluating and REFUSES a shared file stamped with a
foreign pid — None (fall back to OCR/port) beats a confident wrong answer. Unstamped reports
(`qud_state.json`) read as before. `hv state` prints `!! N INSTANCES` and `hv goto` refuses to
drive while duplicates exist; `hv restart <app>` is the cure. Guarded by
`python3 tools/selftest_state_read.py` (stdlib only, no daemon, no apps — run it with any change
to the reader).

## gametree TRANSITIONS — routes are planned, not scripted (2026-08-06)

Nodes no longer store how to reach them. `gametree.json` carries a `transitions` list —
`{app, from, to, steps, cost?, verify?, timeout?}` — and `hv goto` SEARCHES a route from the
state the app is actually detected in (`plan.py`; A* with a zero heuristic, i.e. Dijkstra —
the obvious tree-distance heuristic is inadmissible, see the module docstring).

- `from` = a node id · a list · `{"within": node}` (that node **or any descendant**) · `"*"`.
  Two non-node states exist so "get me out of here" always has a start: **`off`** (no window)
  and **`unknown`** (window up, nothing matched).
- `verify` (default `{"node": to}`) runs after every edge. Non-negotiable: an edge that does
  not check its own arrival lets the route continue from a state it only assumes.
- `costs` prices a step by how much we want to AVOID it — bridge ~free, `click_text` dear
  (OCR is the flaky class, and a miss clicks the WRONG thing rather than failing), launch
  slow, **restart 120**. A `"*" → title` restart edge guarantees there is always a route; the
  planner PICKING it is the signal that a real exit edge is missing.
- **`preflight`** clears ghost modals before planning. A pooled `PopupMessage` is a condition
  on top of a state, not a state — as a node it doubles the graph, as an edge it needs a
  self-loop. One declaration replaced sixteen copy-pasted dismiss steps.
- On a failed edge the engine re-reads and **re-plans if the app moved** (twice, then gives
  up — if it did not move, the same route comes back and loops).

Steps (one vocabulary, shared with the legacy recipes): `{"launch": name}`, `{"restart":
app}`, `{"wait_window": label}`, `{"activate": label}`, `{"click_hover": [x,y], "window":
label}` (Unity menus need hover), `{"click_text": label, "window": label}`, `{"key": keys}`,
`{"command": name, "answers": [btn]}`, `{"bridge": name, "args": {...}}`, `{"dock": label}`,
`{"dismiss": {...}}`, `{"sleep": s}`, `{"assert": {...}}`. Coordinates are window points at
the standard 1920×1080 slots; re-measure if the layout changes.

- `hv plan <app> <node> [--from <state>]` — the route `hv goto` would take, driving NOTHING.
- `python3 tools/selftest_plan.py` — stdlib only, no daemon, no apps. Proves every target is
  reachable from every state we might be found in. Run it with any transition edit.
- The 20 legacy `goto[app]` recipes are GONE (2026-08-06) — the graph covers every node that
  had one. The fallback code stays, so adding a recipe for an unmodelled node still works,
  but `selftest_plan.py` fails if one reappears for a node the graph can already reach.
- `click_text` POLLS the OCR to a deadline: Qud does not repaint unfocused, so a single
  snapshot read the previous screen and "Continue is not on screen" meant "I am looking at
  a stale frame". Never load a save by clicking a row — `{"load_save": {"row": n}}` goes
  through the mod's `loadsave {id}` with the id resolved from DISK.
- Full write-up: `docs/05-driving-input.md`.

## hv assert — the TDD primitive

`hv assert --app raves --popup message --timeout 10` blocks until Raves reports a message
popup (exit 0) or dumps the actual state (exit 1). Conditions: `--node`, `--scene`, `--popup
[kind]`, `--present yes|no`, `--ocr-contains`. Use it to pin state before AND after a driven
action instead of screenshot-guess loops:
`hv goto qud in_game && hv assert --app qud --node in_game && <the actual test>`.

## Gotchas

- `hv move` verifies by READBACK (CG window frame) — raw AX error codes lie for Godot's
  borderless window (kAXErrorFailure from sets that landed, and vice versa).
- A `[Errno 49] Can't assign requested address` from any hv call is TRANSIENT — just retry.
- Qud's window FREEZES when unfocused (Unity doesn't repaint) — `hv activate` + ~2s before a
  shot, or you'll diff a stale frame. The mod/bridge still runs unfocused; only pixels freeze.
- Shot scale = the display's backing (Retina 2× / 4K 1×) — see the ops quickref memory.
- Qud's MODERN menu screens (Records/Options/Mods) ignore ALL OS-synthesized keys —
  HID-sourced or not. Exit them with the mod's first-party `uiback` bridge command
  (gametree `{"bridge": "uiback"}` step / `{"dismiss": {..., "bridge": "uiback"}}`),
  never key injection. Clicks DO land (warp + HID button pair).
- Menu recipes click by LABEL, not coords: `{"click_text": "Records", "window": ...}` —
  fixed coords started stray games twice when the menu reflowed / the window sat
  off-slot. OCR matching is space-insensitive (Vision reads 'Opti ons' on Raves'
  Source Code Pro); optional `"offset": [dx,dy]` when the hit-area sits away from
  the caption (Qud Records' Back chevron is 40px above its "[Esc] Back" label).
- A dismiss step FAILS the recipe if the affordance is missing or the scene doesn't
  change — silent "dismissed" was how a stray Cancel reached the main menu (where
  Cancel == quit confirm). Fire ONE cancel and verify; never a fallback shotgun.
- A daemon re-exec mid key-combo orphans a modifier DOWN in the OS HID state: every later
  synthetic key/click arrives Cmd-modified and silently no-ops ("intermittent" app-side
  symptoms, survives app restarts). `_clear_stuck_mods` in darwin.py self-heals on every
  key/click op — if scripted keys ever go dead again, check `CGEventSourceFlagsState` first.
- Commit + push after each verified round; author guard before push:
  `git log --all --format='%ae' | grep -i allspice` must print nothing.
