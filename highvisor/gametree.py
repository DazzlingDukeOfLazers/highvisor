"""gametree — the canonical game state-machine tree, plus a live-state evaluator.

ONE tree (``gametree.json``) is the source of truth; the cockpit renders it as three
views (master structure + a Raves column + a Qud column). Each node carries per-app
``detect`` signatures and a per-app ``done`` (0..1 completion). This module is pure
data + logic: it loads the JSON (reloading on file change) and, given already-gathered
signals for an app, decides which node that app is currently in. Gathering the signals
(window list, port check, OCR) lives in the engine, which has the backend.

State evaluation (``evaluate``):
  signals = {present: bool, port_open: bool|None, ocr_text: str|None}
  - present False                       -> {"off": True}  (no window)
  - else walk the tree; a node MATCHES when every condition in its detect[app] holds:
        "port": True/False   -> requires port_open to equal it (skip if port_open is None)
        "ocr_any": [subs]    -> requires ocr_text to contain any substring
                                (fails when ocr_text is None -> OCR-only nodes need an OCR poll)
    Return the DEEPEST matching node. If only the root region matches (window up but
    nothing specific), return {"running": True, node None} = "unknown screen".
"""
import json
import os

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


def _matches(detect, app, signals):
    """Does this node's detect[app] hold under the signals? Unknown -> False."""
    cond = (detect or {}).get(app)
    if cond is None:
        return False  # no detector for this app on this node
    if "port" in cond:
        po = signals.get("port_open")
        if po is None or bool(po) != bool(cond["port"]):
            return False
    if "ocr_any" in cond:
        text = signals.get("ocr_text")
        if not text:
            return False
        low = text.lower()
        if not any(str(s).lower() in low for s in cond["ocr_any"]):
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

    best = None  # (depth, node, path, via)

    def walk(node, depth, path):
        nonlocal best
        nid = node.get("id")
        here_path = path if nid == "root" else path + [nid]
        if nid != "root" and _matches(node.get("detect"), app, signals):
            cond = node["detect"][app]
            via = "ocr" if "ocr_any" in cond else ("port" if "port" in cond else "window")
            if best is None or depth > best[0]:
                best = (depth, node, here_path, via)
        for ch in node.get("children", []) or []:
            walk(ch, depth + 1, here_path)

    walk(tree["root"], 0, [])

    if best is None:
        # window is up but nothing specific matched (e.g. a cheap no-OCR poll, or an
        # unmodelled screen). Honest "running, screen unknown".
        return {"off": False, "running": True, "node": None, "label": "running · unknown screen",
                "path": [], "via": "window", "ocr_used": ocr_used}
    _, node, path, via = best
    return {"off": False, "running": True, "node": node["id"], "label": node.get("label", node["id"]),
            "path": path, "via": via, "ocr_used": ocr_used}
