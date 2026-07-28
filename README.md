# highvisor

A local supervisor for the desktop UI layer. highvisor observes and controls
native desktop applications on Windows and macOS over a small, brain-agnostic
RPC — so a Claude/OpenAI/scripted "brain" can drive real apps to speed up and
focus debug loops.

Named after "hypervisor": it sits above the desktop and coordinates what runs on
it. See [`docs/`](docs/) for the full design.

## Status

Both backends built. Windows verified end-to-end; the **macOS backend** is
implemented and heavily exercised — window capture/move/dock, AX inspect, Vision
OCR, and the synthetic-input tier ladder incl. the Unity/Qud click (`--hover`) —
driving Caves of Qud + Godot apps for the visual-parity kit (`docs/08-parity-kit.md`).
See `docs/03-research-findings.md`.

## Architecture (one paragraph)

Four layers: **brain adapters** → **CLI/clients** → **core daemon** (RPC server +
single-threaded action queue) → **per-OS `PlatformBackend`** (observe + act).
Everything OS-specific lives behind the `PlatformBackend` seam. The daemon speaks
framed JSON over localhost TCP (`127.0.0.1:48720`). See
[`docs/01-architecture.md`](docs/01-architecture.md).

### Background control (the hard part)

highvisor tries to act on **unfocused** windows via a tier ladder, reporting which
tier actually delivered each action:

| tier | mechanism | focus? |
|------|-----------|--------|
| 1 | accessibility action (UIA pattern / AX action) | background, semantic |
| 2 | window message post (`WM_SETTEXT` / `PostMessage`) | background, syntactic |
| 3 | cooperative hook (target polls our channel) | background, opt-in |
| 4 | activate + global input (`SendInput` / `CGEvent`) | steals focus |

## Install

```
pip install -e .
```

Dependencies: `pillow`, plus `uiautomation` on Windows.

## Usage

Start the daemon:

```
python -m highvisor.server      # or: hvd
```

Drive it with the CLI client (`<target>` is a window ref — `hwnd:0x1a2b`,
`pid:1234`, or a title substring):

```
hv ping
hv ls
hv shot <target> [out.png]
hv text <target> <string...>
hv key <target> <keys>
hv activate <target>
hv inspect <target> [depth]
hv raw '{"op":"ping"}'
```

The protocol is dependency-free framed JSON (`highvisor/protocol.py`), so any
language can speak it in a few lines.

### Visual parity & regression

Drive two apps to the same screen, localize where they differ, and catch layout
regressions — the toolchain built to bring a reconstruction 1:1 with its source
(Raves of Qud vs Caves of Qud):

```
hv probe --app qud                     # off / menu / in-game
hv diff a.png b.png --regions          # match % + WHERE they diverge (+ annotated)
hv text-diff CavesOfQud "Raves of Qud" # OCR word-level content gaps (rough)
hv parity-sweep A B --regions          # compare across window sizes
hv scene mods --parity --text          # drive BOTH + diff live vs the reference
hv scene --all --bless                 # lock goldens;  hv scene --all  to regress
```

See [`docs/08-parity-kit.md`](docs/08-parity-kit.md) for the workflows, the scenes
file format, and the gotchas (the `--hover` click, matched sizes, OCR limits).

## Notepad golem (structural reconstruction demo)

`tools/gen_notepad_depth.py` turns a captured window into a runnable **golem** —
a Godot reconstruction that reflows like the source. It reads a UIA tree (`hv
inspect`) plus a capture (`hv shot`) and emits a small Godot 4.7 project whose
layout, fills, menus, and flyouts mirror Windows 11 Notepad. Fixtures for the
demo ship in `tools/fixtures/`, so it regenerates the same golem on macOS or
Windows with no capture step:

```
python tools/gen_notepad_depth.py                 # -> ./notepad_golem
python tools/gen_notepad_depth.py --out /tmp/golem # custom output dir
```

Flags: `--tree`, `--png`, `--out`, `--scale` (physical/logical DPI ratio; the
bundled capture is 200 % → `2`). Requires `pillow` (already a dependency).

Open the generated project in Godot 4.7:

```
# macOS
/Applications/Godot.app/Contents/MacOS/Godot --path notepad_golem

# Windows
Godot_v4.7.1-stable_win64.exe --path notepad_golem
```

The golem's popups are Godot Controls drawn *inside* the window, so `hv shot`
(PrintWindow-backed) captures them — you can verify hover/click states over the
same RPC, without stealing focus.
