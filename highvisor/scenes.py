"""scenes — named UI states for capture + golden-image regression testing.

A scenes file (JSON) maps a name to how to REACH a screen and where its golden capture
lives, so a layout regression shows up as a match-% drop + a region punch-list instead of
having to be caught by eye. ``hv scene <name>`` resizes the window, runs the steps, captures,
and diffs the capture against the golden (imageops.diff + regions). ``--bless`` writes the
capture AS the golden (establish/update the reference). Paths resolve relative to the file.

    { "mods": {
        "window": "Raves of Qud", "size": "1793x997", "crop_top": 58, "threshold": 97,
        "steps": [ {"click": [907, 689]}, {"wait": 1.2} ],
        "golden": "golden/mods.png" } }

Each step is one action:
    {"move": [x, y, w, h]}                         reposition/resize the window
    {"click": [x, y], "hover": bool, "double": bool, "button": "left"|"right"}
    {"key": "Escape", "focus": true}               synthetic key (Godot etc.; not Qud)
    {"wait": seconds}
An optional per-scene "reset": [...] runs before "steps" to return to a known state.
"""
import json
import os


def load(path: str) -> dict:
    with open(path) as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def rel(config_path: str, p: str) -> str:
    """Resolve a scene path relative to the scenes file's directory."""
    if os.path.isabs(p):
        return p
    return os.path.join(os.path.dirname(os.path.abspath(config_path)), p)
