"""layouts — named window arrangements, applied through the move/zone ops.

A layout is an ordered list of *placements*; applying it walks the placements in
order and drops the first still-unused window whose title/owner matches into a
rect. A rect is either a named ``zone`` (backend.ZONES — halves/quadrants) or a
``frac`` [x, y, w, h] in 0..1 of the primary display, so a layout can carve finer
than quadrants (e.g. a left column of four). Order makes ``apply`` deterministic,
which is the point: the 1:1 loop wants a stable, repeatable stage.

Built-ins live here; user layouts live in ``~/.config/highvisor/layouts.json`` and
are merged on top (same name overrides). ``save`` snapshots the current window
positions into that file so you can arrange by hand and freeze it.
"""
import json
import os

from .backend import ZONES, zone_rect

BUILTIN = {
    "loop": {
        "description": "1:1 feedback-loop stage — left column: cockpit / Claude / "
                       "ChatGPT / Finder; right column: golem over source",
        "placements": [
            {"match": "highvisor", "frac": [0.0, 0.00, 0.5, 0.25]},
            {"match": "Claude",    "frac": [0.0, 0.25, 0.5, 0.25]},
            {"match": "ChatGPT",   "frac": [0.0, 0.50, 0.5, 0.25]},
            {"match": "Finder",    "frac": [0.0, 0.75, 0.5, 0.25]},
            {"match": "golem",     "frac": [0.5, 0.00, 0.5, 0.50]},
            {"match": "source",    "frac": [0.5, 0.50, 0.5, 0.50]},
        ],
    },
    "halves": {
        "description": "First two visible windows, side by side",
        "placements": [
            {"match": "", "zone": "left"},
            {"match": "", "zone": "right"},
        ],
    },
    "quads": {
        "description": "First four visible windows into the quadrants",
        "placements": [
            {"match": "", "zone": "top-left"},
            {"match": "", "zone": "top-right"},
            {"match": "", "zone": "bottom-left"},
            {"match": "", "zone": "bottom-right"},
        ],
    },
}


def _path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "highvisor", "layouts.json")


def load_layouts() -> dict:
    """Built-ins merged with the user's layouts.json (user wins on name clash)."""
    out = {k: dict(v) for k, v in BUILTIN.items()}
    try:
        with open(_path()) as f:
            user = json.load(f)
        if isinstance(user, dict):
            out.update(user)
    except (OSError, ValueError):
        pass
    return out


def save_layout(name: str, layout: dict) -> str:
    """Write/overwrite one named layout in the user file; return its path."""
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[name] = layout
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def placement_rect(pl: dict, sw: int, sh: int, displays=None):
    """Resolve one placement to an (x, y, w, h) pixel/point rect.

    ``monitor`` fills a whole display and survives the user rearranging their
    monitors (unlike a frozen ``rect``): ``"main"`` = the primary, ``"other"`` =
    the first non-primary, or an integer index into the backend's display list.
    Falls back to the primary-display rect when the machine has no such monitor.
    """
    if "monitor" in pl:
        mons = list(displays or [])
        main = next((m for m in mons if m.get("main")), mons[0] if mons else None)
        which = pl["monitor"]
        if which == "main":
            mon = main
        elif which == "other":
            mon = next((m for m in mons if m is not main), main)
        else:
            i = int(which)
            mon = mons[i] if 0 <= i < len(mons) else None
        if mon is None:
            return (0, 0, sw, sh)
        return (int(mon["x"]), int(mon["y"]), int(mon["w"]), int(mon["h"]))
    if "zone" in pl:
        return zone_rect(pl["zone"], sw, sh)
    if "frac" in pl:
        fx, fy, fw, fh = pl["frac"]
        return (int(fx * sw), int(fy * sh), int(fw * sw), int(fh * sh))
    if "rect" in pl:
        return tuple(int(v) for v in pl["rect"])
    raise ValueError("placement needs one of: monitor, zone, frac, rect")
