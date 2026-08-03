"""Deterministic responsive-parity test: does the golem reflow like the source?

Given two window refs — the generated/candidate "golem" and the reference
"source" — this drives them through a FIXED set of window frames and scores
their rendered output at every size. A faithful reconstruction is responsive-
*equivalent*, not merely pixel-equivalent at one size: if the golem captured the
source's layout rules (anchors, or a Godot stretch config), it reflows the same
way on every frame and passes with zero per-frame tuning.

It is deterministic by construction — same windows in, same report out:
  * ``FRAMES`` is a fixed, ordered list — no sampling, no randomness.
  * ``place`` is a pure function of (screen_size, frame): the golem takes the
    top-right quadrant and the source the bottom-right, both right-edge aligned,
    so every window lands in the RIGHT HALF of the screen.
  * Capture uses the daemon's per-window path (PrintWindow on Windows), which
    renders a window from its own backing store — independent of z-order or
    occlusion — so the result never depends on what happens to be on top.
  * The moves clear the topmost bit (``topmost=False``) so a prior run can't
    leave windows floating; placement is therefore idempotent.
  * The verdict is a threshold on content-match; identical renders yield
    identical numbers yield an identical PASS/FAIL.

This module is transport-agnostic: it takes a ``call(request) -> response``
callable (the CLI injects its socket client) rather than opening its own socket,
so the algorithm stays testable and free of wire details.
"""
import base64
import os
import tempfile
import time
from typing import Callable, List, Optional, Tuple

from . import imageops
from . import protocol as P

# Fixed, deterministic inputs. Every frame must satisfy w <= screen_w//2 and
# h <= screen_h//2 so the two quadrants never overlap (see ``place``).
FRAMES: List[Tuple[int, int]] = [
    (1920, 1080),   # 16:9 base — exact fit, no letterbox
    (1600, 900),    # 16:9 smaller — uniform downscale
    (1280, 1000),   # tall (1.28) — expects top/bottom bars
    (1600, 700),    # wide (2.29) — expects left/right bars
    (1000, 1040),   # very tall (0.96) — heavy top/bottom bars
]
DEFAULT_THRESHOLD = 99.0    # content-match %% required to pass a frame
SETTLE_S = 0.35             # fixed dwell after a resize so the app repaints


def place(screen_w: int, screen_h: int, w: int, h: int,
          origin_x: int = 0, origin_y: int = 0):
    """Pure placement. Golem -> top-right quadrant, source -> bottom-right,
    both right-edge aligned so x >= screen_w//2 (strictly the right half).
    Requires w <= screen_w//2 and h <= screen_h//2. ``origin_x/origin_y`` shift
    both rects onto a chosen display, so a secondary monitor's global origin
    (e.g. a 4K stacked above the built-in, at a negative y) is honored."""
    x0, y_mid = screen_w // 2, screen_h // 2
    if w > screen_w - x0 or h > y_mid:
        raise ValueError(
            "frame %dx%d does not fit the right-half quadrants of %dx%d"
            % (w, h, screen_w, screen_h))
    x = screen_w - w                        # right edge aligned; >= x0
    return ((origin_x + x, origin_y + 0, w, h),
            (origin_x + x, origin_y + y_mid, w, h))


def _window_bounds(call, ref):
    """(x, y, w, h) of the window matching ``ref`` via OP_LIST, or None. Mirrors
    the backend's ref rules: win:<id>, pid:<n>, else title/owner substring."""
    r = call({"op": P.OP_LIST})
    if not r.get("ok"):
        return None
    targets = r.get("targets", [])
    for t in targets:                                    # exact win:/pid: first
        if ref.startswith("win:") and str(t.get("id", "")) == ref:
            return (t["x"], t["y"], t["w"], t["h"])
        if ref.startswith("pid:") and str(t.get("pid")) == ref.split(":", 1)[1]:
            return (t["x"], t["y"], t["w"], t["h"])
    low = ref.lower()
    for t in targets:
        if (low in (t.get("title") or "").lower()
                or low in (t.get("class_name") or "").lower()):
            return (t["x"], t["y"], t["w"], t["h"])
    return None


def _resolve_display(call, golem, display):
    """Pick the display to sweep on and return (origin_x, origin_y, w, h, info).

    ``display`` selects it: ``None``/"auto" -> the display the golem window sits
    on (so moving the windows to a monitor is enough — no need to make it the OS
    main display); "main" -> the primary; an int/digit-string -> that index into
    the display list. Falls back to OP_SCREEN at origin (0,0) on an older daemon
    that doesn't know OP_DISPLAYS."""
    dr = call({"op": P.OP_DISPLAYS})
    displays = dr.get("displays") if dr.get("ok") else None
    if not displays:                                     # older daemon / no info
        sr = call({"op": P.OP_SCREEN})
        if not sr.get("ok"):
            raise RuntimeError("screen_size failed: %r" % sr)
        return (0, 0, sr["w"], sr["h"], {"main": True, "fallback": "screen"})

    def as_tuple(d):
        return (d["x"], d["y"], d["w"], d["h"], d)

    if isinstance(display, str) and display.isdigit():
        display = int(display)
    if isinstance(display, int):
        if not (0 <= display < len(displays)):
            raise ValueError("no display index %d (have %d)" % (display, len(displays)))
        return as_tuple(displays[display])
    if display == "main":
        return as_tuple(next(d for d in displays if d.get("main")))

    # auto: the display whose bounds contain the golem window's center.
    gb = _window_bounds(call, golem)
    if gb:
        cx, cy = gb[0] + gb[2] // 2, gb[1] + gb[3] // 2
        for d in displays:
            if d["x"] <= cx < d["x"] + d["w"] and d["y"] <= cy < d["y"] + d["h"]:
                return as_tuple(d)
    return as_tuple(next(d for d in displays if d.get("main")))  # fall back to main


def run(call: Callable[[dict], dict], golem: str, source: str,
        frames: Optional[List[Tuple[int, int]]] = None,
        threshold: float = DEFAULT_THRESHOLD,
        out_dir: Optional[str] = None,
        settle: float = SETTLE_S,
        display=None) -> dict:
    """Execute the parity sweep. ``call`` is a request->response callable
    speaking the framed-JSON protocol. ``display`` chooses which monitor to sweep
    on (see :func:`_resolve_display`; default auto-detects the golem's display).
    Returns a report dict with per-frame scores and an overall PASS/FAIL verdict."""
    frames = list(FRAMES if frames is None else frames)
    out_dir = out_dir or tempfile.mkdtemp(prefix="hv-responsive-")
    os.makedirs(out_dir, exist_ok=True)

    origin_x, origin_y, screen_w, screen_h, disp = _resolve_display(call, golem, display)

    def move(ref, rect):
        x, y, w, h = rect
        call({"op": P.OP_MOVE, "target": ref,
              "x": x, "y": y, "w": w, "h": h, "topmost": False})

    def shot(ref, path):
        r = call({"op": P.OP_SHOT, "target": ref})
        if not r.get("ok"):
            raise RuntimeError("shot %s failed: %r" % (ref, r))
        with open(path, "wb") as f:
            f.write(base64.b64decode(r["png_b64"]))

    results = []
    for (w, h) in frames:
        g_rect, s_rect = place(screen_w, screen_h, w, h, origin_x, origin_y)
        move(golem, g_rect)
        move(source, s_rect)
        time.sleep(settle)                  # fixed, deterministic settle
        gp = os.path.join(out_dir, "gol_%dx%d.png" % (w, h))
        sp = os.path.join(out_dir, "src_%dx%d.png" % (w, h))
        heat = os.path.join(out_dir, "heat_%dx%d.png" % (w, h))
        shot(golem, gp)
        shot(source, sp)
        d = imageops.diff(gp, sp, out=heat)
        results.append({"frame": [w, h],
                        "golem_rect": list(g_rect),
                        "source_rect": list(s_rect),
                        "content_match": d["content_match"],
                        "full_match": d["full_match"],
                        "pass": d["content_match"] >= threshold})

    worst = min(r["content_match"] for r in results) if results else None
    return {"screen": [screen_w, screen_h],
            "display": disp,
            "origin": [origin_x, origin_y],
            "threshold": threshold,
            "out_dir": out_dir,
            "frames": results,
            "worst_content_match": worst,
            "verdict": "PASS" if all(r["pass"] for r in results) else "FAIL"}
