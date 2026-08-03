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
| click-drive to a known screen by hand | `hv goto <app> <node>` (gametree recipes) |
| sleep-and-hope waiting for a state | `hv assert --app qud --node in_game --timeout 20` |
| pkill/osascript restart seances | `hv restart qud` / `hv restart raves` (kills ALL instances incl. duplicates, relaunches, waits) |
| blind top-row Continue clicks | `hv loadsave <name>` (row computed from DISK metadata — `hv saves` lists them, no game launch) |

The daemon **self-restarts** (2026-08-03): a source watcher re-execs it when any highvisor `.py`
changes (edit → it picks itself up in ~2s), and `hv install-daemon` adds a launchd KeepAlive agent
for crash restart. If `nc -z 127.0.0.1 48720` fails AND the agent isn't installed, ask Daniel.
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
| `highvisor/gametree.json` | THE canonical game state tree: per-node `detect` (how we know we're there), `goto` (how to get there), `done` (1:1 score). Hot-reloads. |
| `highvisor/gametree.py` | tree loader + state evaluator (deepest match wins; detect OR-lists) |
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

## gametree `goto` recipes (hv goto / cockpit click-to-state)

A node's `goto[app]` is a step list: `{"goto": node}` (chain), `{"launch": name,
"unless_running": true}`, `{"wait_window": label}`, `{"activate": label}`,
`{"click_hover": [x,y], "window": label}` (Unity menus need hover), `{"key": keys}`,
`{"sleep": s}`, `{"assert": {...}, "timeout": s}`. First failure stops the run; progress
streams to the cockpit log. Idempotent — already-there returns immediately. Coordinates are
window points at the standard 1920×1080 slots; re-measure if the layout changes.

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
- Commit + push after each verified round; author guard before push:
  `git log --all --format='%ae' | grep -i allspice` must print nothing.
