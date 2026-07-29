# 08 — Visual parity & regression kit

How highvisor drives two apps to the same screen, compares them, and catches layout
regressions — the toolchain built to bring **Raves of Qud** 1:1 with **Caves of Qud**.
It automates the mechanical halves of a "reconstruct this UI" loop: **drive both →
capture → localize where they differ → lock a golden → catch regressions.** It does
*not* interpret *what* is wrong (serif vs sans, wrong gold, box too tall) — that reading
stays with the human/brain. Dated 2026-07-28, verified on macOS (Qud Unity build
2.0.211.59, Raves in Godot 4.7).

## The commands

| command | does | side |
|---|---|---|
| `probe --app qud` | is Qud `off` / `menu` / `in-game` (window + bridge port) | daemon |
| `dock <win>` / `stack <top> <bottom>` | put one window in another's column (Raves above Qud) | daemon |
| `click <win> <x> <y> --hover` | drive a UI that reads `Input.mousePosition` (Qud's legacy popups) | daemon |
| `diff <a.png> <b.png> --regions` | match % **and** a ranked "where do they diverge" punch-list + annotated image | client |
| `text-diff <ref> <cur>` | OCR both windows, word-level content gaps (labels/wording) | daemon |
| `parity <a> <b> [--size]` | resize both, capture, screenshot-diff once | daemon+client |
| `parity-sweep <a> <b> --regions` | the above across a set of window sizes, per-size punch-list | daemon+client |
| `scene <name> [--all]` | drive to a named screen, diff vs a **golden** (regression) | daemon+client |
| `scene <name> --parity [--text]` | also drive the scene's **reference** window (Qud) and diff the two live | daemon+client |
| `scene <name> --bless` | write the capture *as* the golden | daemon+client |

"client" ops (`diff`/`regions`/`sidebyside`) are pure Pillow — no daemon. Everything else
uses the daemon's `move`/`screenshot`/`click`/`ocr` ops.

## Workflow A — reconstruct a screen 1:1 with a reference

1. **Drive the reference to the screen.** Qud's title menu + in-game popups need
   `--hover` (see gotchas); e.g. from Qud's title, `click CavesOfQud 905 679 --hover`
   opens Mods.
2. **Build the Raves screen** against a capture of that.
3. **Compare live:** `scene mods --parity --text` drives *both* apps to the screen,
   captures each, and emits: a **parity match %**, a **region punch-list** (`_parity.png`),
   a labelled **side-by-side** (`_sbs.png`), and (`--text`) the **word gaps**.
4. **Iterate:** `--regions` says *where* to look, `text-diff` says *what words* are
   missing (e.g. Raves' Mods lacked Qud's description + Disable/Undo). Fix, re-run.

## Workflow B — regression

1. From a known-good state, **bless goldens:** `scene --all --bless`.
2. After any change, **re-check:** `scene --all` re-drives + diffs vs the goldens →
   a pass/fail table + per-scene punch-list. A layout break shows as a match drop, not
   by eye.
3. After an *intended* visual change, **re-bless** the affected scenes.

## The scenes file

JSON mapping a name → how to reach the screen + where its golden lives (paths resolve
relative to the file). Non-object keys (e.g. a `_note`) are ignored by `--all`.

```json
{
  "mods": {
    "window": "Raves of Qud", "size": "1793x997", "crop_top": 58,
    "threshold": 98, "parity_threshold": 82,
    "steps": [ {"click": [907, 689]}, {"wait": 1.4} ],
    "golden": "golden/mods.png",
    "reference": {
      "window": "CavesOfQud", "size": "1793x997",
      "steps": [ {"click": [905, 679], "hover": true}, {"wait": 2.0} ]
    }
  }
}
```

Steps (one action each): `{"move":[x,y,w,h]}`, `{"click":[x,y], "hover":bool, "double":bool,
"button":"left|right"}`, `{"key":"Escape", "focus":true}`, `{"wait":seconds}`, and
`{"shell":[argv…], "timeout":secs}`. An optional per-scene/-reference `"reset":[…]` runs before
`steps` to normalize state.

A **`shell`** step runs a command before the capture continues — the hook for deterministic **data
setup**. Its cwd is the scene config's directory (so `../capture/presets.py` resolves from
`tools/regression/`); a non-zero exit aborts the scene with the captured stderr. The canonical use is
loading a Raves option preset so a scene captures a **known configuration**:

```json
"reset": [
  { "shell": ["python3", "../capture/presets.py", "load", "some-qud-preset"] },
  { "click": [140, 952] }, { "wait": 0.6 }
]
```

Caveat: a preset's *Qud* options apply live over the bridge (fine in-scene), but its *Raves* settings
(camera/full_info) only take effect on a Raves **launch** — for those, relaunch at the script level
(`presets.py load X` → `hv launch raves` → `hv scene …`) rather than an in-scene `shell` step.

Raves' suite lives in the *Raves* repo (`tools/regression/scenes.json` + `golden/`), run as:

```bash
PYTHONPATH=<highvisor> python -m highvisor.cli scene --all \
  --config tools/regression/scenes.json           # regression
PYTHONPATH=<highvisor> python -m highvisor.cli scene --all --parity --text \
  --config tools/regression/scenes.json           # live vs Qud
```

## Gotchas (the hard-won bits)

- **Qud's legacy popups need `--hover`.** A bare warp+click drives Unity UI buttons
  (Qud's toolbar, its title menu) but its *console* popups (the in-game ☰ menu, "press
  [Space]" prompts) activate the item under `Input.mousePosition`, which
  `CGWarpMouseCursorPosition` doesn't update — so `--hover` posts a real `mouseMoved`
  first. **Do NOT hover for world-cell clicks** — a pre-move makes Qud hover-but-never-
  select the tile. See [`05-driving-input.md`](05-driving-input.md).
- **The reference must start from a known state.** Reference/parity scenes assume Qud is
  at its **title** (and Raves freshly at its menu); the steps navigate from there. There's
  no reliable "back to title" for Qud yet, so re-runs want Qud reset to the title first.
  Same for the current side: run the suite from a freshly-launched app, in config order.
- **Diffs need matched sizes.** `parity`/`parity-sweep`/`scene` resize both windows before
  capturing so the diff is 1:1; `parity-sweep` restores the originals when done.
- **The region diff is mechanical.** It localizes pixel divergence — *where*, not *what*.
  A flagged cell might be a font, a colour, or a layout shift; the reader decides.
- **`text-diff` is OCR-rough.** Vision garbles stylized game fonts and segments the same
  text into different boxes across two captures, so it works **word-level** with fuzzy
  matching and reports a *candidate checklist* (`missing` = reference words absent from
  current), not a precise score. Trust the standout content words, ignore the garble.
- **New daemon ops go live on the next daemon restart.** `probe`, `dock`, `stack`, and
  `click --hover` are daemon-side; a running daemon started before them won't have them
  (so a `--parity` scene whose Qud steps use `"hover": true` needs the restarted daemon,
  or drive Qud with the standalone hover helper meanwhile). The pure client-side image
  ops and the pre-existing `move`/`screenshot`/`ocr` ops work against any running daemon.

## What it does NOT do

It won't tell you the font is wrong or the gold is off — only that a region diverges or a
word is missing. Diagnosis + the fix stay with you; the kit removes the manual
capture-align-eyeball toil and turns "did I regress?" into one command.
