"""gametree — the canonical game state-machine tree, plus a live-state evaluator.

ONE tree (``gametree.json``) is the source of truth; the cockpit renders it as three
views (master structure + a Raves column + a Qud column). Each node carries per-app
``detect`` signatures and a per-app ``done`` (0..1 completion). This module is pure
data + logic: it loads the JSON (reloading on file change) and, given already-gathered
signals for an app, decides which node that app is currently in. Gathering the signals
(window list, port check, OCR) lives in the engine, which has the backend.

State evaluation (``evaluate``):
  signals = {present: bool, port_open: bool|None, ocr_text: str|None, scene: str|None,
             tab: str|None}
  - present False                       -> {"off": True}  (no window)
  - else walk the tree; a node MATCHES when every condition in its detect[app] holds:
        "port": True/False   -> requires port_open to equal it (skip if port_open is None)
        "ocr_any": [subs]    -> requires ocr_text to contain any substring
                                (fails when ocr_text is None -> OCR-only nodes need an OCR poll)
        "scene": "name" | ["a","b"] -> requires the app-REPORTED scene to equal one of these.
        "tab":   "name" | ["a","b"] -> same, for a sub-screen WITHIN the scene (Qud's status
                                tabs). Pair it with "scene" so the tab name cannot match while
                                a different window happens to be up.
                                The apps author their own state files (the mod's qud_state.json;
                                Raves' raves_state.json) — first-party truth, so it beats OCR
                                guessing and works on every cheap poll. Fails when scene is None
                                (file missing/stale), so the OCR/port fallbacks still apply.
    Return the DEEPEST matching node. If only the root region matches (window up but
    nothing specific), return {"running": True, node None} = "unknown screen".

``goto`` recipes: a node may carry goto[app] = [step, ...] — the command sequence that
drives the app from a KNOWN BASE (its title screen unless the recipe starts with
``{"launch": ...}``) to this state. Executed by the engine's ``gamego`` op; steps are
documented there. The tree only STORES them (one canonical map: detection + navigation).
"""
import json
import os

#: How much a matching SIGNAL is worth when two nodes match at the same depth. The apps' own
#: reports are ground truth; OCR is a reading of pixels; game_live/port are inferences from a
#: timed socket probe and can be wrong simply because something was busy. Higher wins.
TRUST = {"tab": 5, "scene": 4, "ocr": 3, "live": 2, "port": 1, "window": 0}

_PATH = os.path.join(os.path.dirname(__file__), "gametree.json")
_cache = None
_cache_mtime = None


def load_tree(force=False):
    """Load (and cache) the tree JSON, reloading automatically when the file changes."""
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(_PATH)
    except OSError:
        mtime = None
    if force or _cache is None or mtime != _cache_mtime:
        with open(_PATH, "r", encoding="utf-8") as fh:
            _cache = json.load(fh)
        _cache_mtime = mtime
    return _cache


def apps(tree=None):
    """The app config block: {app: {label, window, port?}}."""
    return (tree or load_tree()).get("apps", {})


def find_node(tree, node_id):
    """The node dict with this id (depth-first), or None."""
    def walk(node):
        if node.get("id") == node_id:
            return node
        for ch in node.get("children", []) or []:
            hit = walk(ch)
            if hit is not None:
                return hit
        return None
    return walk((tree or load_tree())["root"])


def _matches(detect, app, signals):
    """Does this node's detect[app] hold under the signals? Unknown -> False.

    detect[app] is one signature dict (every condition must hold) or a LIST of
    signature dicts (any one matching signature suffices) — the list form lets a
    first-party ``scene`` report sit alongside an OCR fallback without requiring
    both at once (the state file only exists once the app ships its reporter)."""
    cond = (detect or {}).get(app)
    if cond is None:
        return False  # no detector for this app on this node
    if isinstance(cond, list):
        return any(_matches({app: c}, app, signals) for c in cond)
    if "port" in cond:
        po = signals.get("port_open")
        if po is None or bool(po) != bool(cond["port"]):
            return False
    if "game_live" in cond:
        gl = signals.get("game_live")
        if gl is None or bool(gl) != bool(cond["game_live"]):
            return False
    if "ocr_any" in cond:
        text = signals.get("ocr_text")
        if not text:
            return False
        low = text.lower()
        if not any(str(s).lower() in low for s in cond["ocr_any"]):
            return False
    if "scene" in cond:
        scene = signals.get("scene")
        if not scene:
            return False
        want = cond["scene"]
        want = want if isinstance(want, list) else [want]
        if str(scene).lower() not in [str(w).lower() for w in want]:
            return False
    if "tab" in cond:
        # A SUB-SCREEN within the reported scene — Qud's status screens are eight tabs of one
        # window, so `scene` alone bottoms out at status_screens and every tab below it was
        # undetectable for Qud. The app reports the active tab by name in its state file.
        tab = signals.get("tab")
        if not tab:
            return False
        want = cond["tab"]
        want = want if isinstance(want, list) else [want]
        if str(tab).lower() not in [str(w).lower() for w in want]:
            return False
    # matched every stated condition (a detector with only e.g. {"port": False} is valid)
    return True


def evaluate(tree, app, signals):
    """Return the current-state dict for ``app`` given ``signals``.

    {ok, off, running, node, label, path:[ids], via, ocr_used}
    ``path`` is the id chain root->node (excluding root) so the UI can light the branch.
    """
    ocr_used = signals.get("ocr_text") is not None
    if not signals.get("present"):
        return {"off": True, "running": False, "node": None, "label": "off",
                "path": [], "via": "no-window", "ocr_used": ocr_used}

    best = None  # (depth, trust, node, path, via)

    def walk(node, depth, path):
        nonlocal best
        nid = node.get("id")
        here_path = path if nid == "root" else path + [nid]
        if nid != "root" and _matches(node.get("detect"), app, signals):
            cond = node["detect"][app]
            if isinstance(cond, list):   # OR-list: report via the first signature that matched
                cond = next((c for c in cond if _matches({app: c}, app, signals)), {})
            via = ("tab" if "tab" in cond
                   else "scene" if "scene" in cond
                   else "ocr" if "ocr_any" in cond
                   else "live" if "game_live" in cond
                   else "port" if "port" in cond else "window")
            # Deepest wins, and on a TIE the more trustworthy SIGNAL wins — not tree order.
            #
            # Both `title` and `in_game` sit at depth 1. `title` carries a `{"game_live": false}`
            # fallback for reading the title when no scene file exists; `in_game` matches the
            # mod's first-party `scene: "play"`. The game_live probe is a 0.35s read on Qud's
            # bridge, so a busy or just-restarted Qud can miss it — and then BOTH matched at
            # depth 1 and `title` won purely by appearing first in the children array.
            #
            # Observed live 2026-08-06: `hv state` reported "Title Screen  scene=play  via=live"
            # while Qud was plainly in-game (confirmed by screenshot). Harmless when a human
            # reads it; not harmless now that `gamego` PLANS from the detected state — a stray
            # "title" makes it plan title->in_game, whose edge is `load_save`, i.e. reload the
            # save over a running game.
            #
            # The ranking is the project's own standing rule made mechanical: first-party
            # reports beat OCR, OCR beats inference from a port probe.
            trust = TRUST.get(via, 0)
            if best is None or (depth, trust) > (best[0], best[1]):
                best = (depth, trust, node, here_path, via)
        for ch in node.get("children", []) or []:
            walk(ch, depth + 1, here_path)

    walk(tree["root"], 0, [])

    if best is None:
        # window is up but nothing specific matched (e.g. a cheap no-OCR poll, or an
        # unmodelled screen). Honest "running, screen unknown".
        return {"off": False, "running": True, "node": None, "label": "running · unknown screen",
                "path": [], "via": "window", "ocr_used": ocr_used}
    _, _, node, path, via = best
    return {"off": False, "running": True, "node": node["id"], "label": node.get("label", node["id"]),
            "path": path, "via": via, "ocr_used": ocr_used}
