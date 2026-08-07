#!/usr/bin/env python3
"""FULL test: the cockpit "Start Raves + Qud" flow, end to end.

Drives the daemon through the SAME steps the cockpit button runs — classify the
open windows (the daemon-stamped ``role``), launch the ``raves`` pair if absent,
wait for exactly one Raves and one Qud window, arrange via the machine "pair"
layout — then verifies the result with readbacks instead of trust:

  1. exactly one Raves and one Qud window (duplicates fail);
  2. every "pair" placement landed: each window's live rect EQUALS the rect the
     layout said it applied (catches the QudLauncher portrait-quadrant residue,
     a move that silently missed, a layout aimed at a rearranged monitor);
  3. the two windows do not overlap and Raves sits above-or-left of Qud
     (the pair stage invariant on both the Mac slots and the Lumpy column);
  4. both apps' first-party state files are FRESH (the mod heartbeat and Raves'
     UiState) — windows alone can be zombies.

This is FULL-tier per raves docs/testing.md: it launches real apps and takes up
to ~2 minutes on a cold start. Run it after touching the cockpit start/arrange
path, the launchers, the layouts, or the window classifier (whose static half
is tools/test_cockpit_classify.py).

    python tools/test_pair_start.py [--fresh]

--fresh kills any running pair first (cold-start test). Exit 0 all green;
exit 1 prints each failed check.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from highvisor import protocol as P  # noqa: E402


def call(request, timeout=30.0):
    with socket.create_connection((P.HOST, P.PORT), timeout=timeout) as s:
        P.send_frame(s, request)
        resp = P.recv_frame(s)
    if resp is None:
        raise SystemExit("daemon closed the connection without replying")
    return resp


def pair_windows():
    """(raves[], qud[]) from live list_targets, via the daemon's role field."""
    t = call({"op": P.OP_LIST}).get("targets", [])
    return ([w for w in t if w.get("role") == "raves"],
            [w for w in t if w.get("role") == "qud"])


def state_file(name):
    path = os.path.expanduser(
        os.path.join("~", "Library", "Application Support", "RavesOfQud", name))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), os.path.getmtime(path)
    except (OSError, ValueError):
        return None, 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true",
                    help="kill any running pair first (cold-start test)")
    ap.add_argument("--wait", type=float, default=90.0,
                    help="seconds to wait for both windows (default 90)")
    args = ap.parse_args()
    failures = []

    def check(ok, label):
        print("%s %s" % ("ok  " if ok else "FAIL", label))
        if not ok:
            failures.append(label)
        return ok

    if args.fresh:
        for image in ("CoQ.exe", "Godot_v4.7.1-stable_win64.exe"):
            subprocess.run(["taskkill", "/IM", image, "/F"],
                           capture_output=True)
        time.sleep(3)

    # 1. launch the pair if Raves is absent (the cockpit's idempotent-ish rule).
    raves, qud = pair_windows()
    check(len(raves) <= 1 and len(qud) <= 1, "no duplicates before start")
    if not raves:
        r = call({"op": P.OP_LAUNCH, "name": "raves"})
        check(r.get("ok"), "launch raves (%s)" % r.get("error", "ok"))

    # 2. wait for exactly one of each (Qud boots ~20-40s after spawn).
    deadline = time.time() + args.wait
    while time.time() < deadline:
        raves, qud = pair_windows()
        if len(raves) == 1 and len(qud) == 1:
            break
        time.sleep(2)
    if not check(len(raves) == 1 and len(qud) == 1,
                 "one Raves + one Qud window (got %d/%d)" % (len(raves), len(qud))):
        print("\n%d failure(s)" % len(failures))
        return 1

    # 3. arrange via the machine "pair" layout and verify every placement by
    # READBACK — the live rect must equal what the layout says it applied.
    lay = call({"op": P.OP_LAYOUT_APPLY, "name": "pair"})
    if lay.get("ok"):
        placed = [r for r in lay.get("results", []) if r.get("ok")]
        check(len(placed) == 2, "pair layout placed 2 (%s)" % lay.get("detail"))
        time.sleep(1.0)
        raves, qud = pair_windows()
        live = {w["id"]: (w["x"], w["y"], w["w"], w["h"]) for w in raves + qud}
        for r in placed:
            want = tuple(r.get("rect", ()))
            got = live.get(r.get("target"))
            check(got == want, "%s rect %s == applied %s" % (r.get("title"), got, want))
    else:
        print("note: no machine 'pair' layout (%s) — skipping arrange asserts"
              % lay.get("error"))

    # 4. stage invariants that hold on every machine's pair arrangement.
    rv, qd = raves[0], qud[0]
    no_overlap = (rv["x"] + rv["w"] <= qd["x"] or qd["x"] + qd["w"] <= rv["x"]
                  or rv["y"] + rv["h"] <= qd["y"] or qd["y"] + qd["h"] <= rv["y"])
    check(no_overlap, "windows do not overlap")
    check((rv["y"], rv["x"]) <= (qd["y"], qd["x"]), "Raves above-or-left of Qud")
    check((rv["w"], rv["h"]) == (qd["w"], qd["h"]),
          "equal sizes (%dx%d vs %dx%d)" % (rv["w"], rv["h"], qd["w"], qd["h"]))

    # 5. first-party liveness: both apps' state files fresh (windows can be zombies).
    now = time.time()
    qs, qm = state_file("qud_state.json")
    rs, rm = state_file("raves_state.json")
    check(qs is not None and now - qm < 10,
          "qud_state.json fresh (scene=%s)" % (qs or {}).get("scene"))
    check(rs is not None and now - rm < 10,
          "raves_state.json fresh (scene=%s)" % (rs or {}).get("scene"))

    print("\n%s — %d failure(s)" % ("PASS" if not failures else "FAIL", len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
