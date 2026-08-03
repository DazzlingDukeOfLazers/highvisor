"""docks — standing "this window sits relative to that one" rules.

A dock rule pins one window directly ABOVE another (same column: matched x + width,
stacked vertically) whenever highvisor (re)positions it. Unlike a layout (absolute
rects, frozen to one arrangement) a dock is RELATIVE to a live anchor window, so it
follows the anchor wherever it is. This is what makes "Raves always sits above Caves
of Qud" defacto: launch or re-place Raves and highvisor stacks it over Qud on its own.

Rules are keyed by a window-title substring and live in
``~/.config/highvisor/docks.json`` merged over the built-in defaults (user wins):

    {"Raves of Qud": {"above": "CavesOfQud", "gap": 8}}
"""
import json
import os

# The 1:1 stage default: the Raves viewer stacks directly above Caves of Qud, so the
# adaptation and the game it mirrors share a column with Raves on top. Relative to
# Qud's live window, so it holds wherever Qud is.
BUILTIN = {
    "Raves of Qud": {"above": "CavesOfQud", "gap": 8},
}


def _path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "highvisor", "docks.json")


def load_docks() -> dict:
    """Built-in dock rules merged with the user's docks.json (user wins on key clash)."""
    out = {k: dict(v) for k, v in BUILTIN.items()}
    try:
        with open(_path()) as f:
            user = json.load(f)
        if isinstance(user, dict):
            out.update(user)
    except (OSError, ValueError):
        pass
    return out


def rule_for(label: str) -> dict:
    """The dock rule whose key is a substring of ``label`` (case-insensitive), or {}."""
    low = (label or "").lower()
    for key, rule in load_docks().items():
        if key.lower() in low:
            return dict(rule, _key=key)
    return {}
