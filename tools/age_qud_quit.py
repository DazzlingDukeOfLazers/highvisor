#!/usr/bin/env python3
"""Age a Qud process until `qud in_game -> title` stops working, and say what it took.

WHY: that edge passes 3/3 on a fresh process and failed 3/3 on one that had been driven all
session (2026-08-07). Four hypotheses are already refuted — a `wish godmode`, a pre-clear step
on the edge, contention with Raves (it fails with Raves stopped), and a stale vs freshly loaded
save. What is left is accumulation inside the process.

The obvious experiment is "count the cycles", and on its own that produces a number that will
not reproduce: cycles are not necessarily the thing that accumulates. Elapsed time, zone loads
and answered popups all rise together in a loop like this, so record them ALL per iteration and
let whichever one predicts the failure be the answer.

    python3 tools/age_qud_quit.py            # restarts Qud first, runs until it breaks
    python3 tools/age_qud_quit.py --max 60   # give up after N cycles (default 40)

Writes one JSON line per cycle to tools/../age_qud_quit.jsonl next to the repo, so a run that
outlives the session it was started in is still readable.
"""
import argparse
import json
import os
import subprocess
import sys
import time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "age_qud_quit.jsonl")
STATE = os.path.expanduser("~/Library/Application Support/RavesOfQud/qud_state.json")


def hv(*args, timeout=400):
    p = subprocess.run(["hv"] + list(args), capture_output=True, text=True, timeout=timeout)
    i = p.stdout.find("{")
    try:
        return json.loads(p.stdout[i:]) if i >= 0 else {}
    except ValueError:
        return {}


def qud_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--no-restart", action="store_true",
                    help="age the process that is already up instead of starting clean")
    a = ap.parse_args()

    if not a.no_restart:
        hv("restart", "qud")
    t0 = time.time()
    log = open(OUT, "a", buffering=1)

    for cycle in range(1, a.max + 1):
        load = hv("goto", "qud", "in_game")
        if not load.get("ok"):
            rec = {"cycle": cycle, "phase": "load", "ok": False,
                   "error": load.get("error"), "mins": round((time.time() - t0) / 60, 1)}
            log.write(json.dumps(rec) + "\n")
            print("cycle %d: LOAD failed — %s" % (cycle, rec["error"]))
            return 2

        st = qud_state()
        quit_ = hv("goto", "qud", "title")
        bad = [s for s in (quit_.get("steps") or []) if s.get("ok") is False]
        rec = {"cycle": cycle,
               "quit_ok": bool(quit_.get("ok")),
               "failed_steps": len(bad),
               "error": (bad[0].get("error") if bad else None),
               "mins": round((time.time() - t0) / 60, 1),
               # the co-varying quantities, so the culprit can be told apart from the count
               "unity_scene": st.get("unity_scene"),
               "qud_ts": st.get("ts"),
               "after": (quit_.get("state") or {}).get("node")}
        log.write(json.dumps(rec) + "\n")
        print("cycle %2d  quit_ok=%-5s  %4.1f min  after=%s"
              % (cycle, rec["quit_ok"], rec["mins"], rec["after"]))
        sys.stdout.flush()

        if not quit_.get("ok"):
            print("\nBROKE at cycle %d after %.1f minutes: %s" % (cycle, rec["mins"], rec["error"]))
            print("Re-run to confirm the number repeats — if it does not, the count is not the "
                  "variable and the minutes/zone loads are worth a second look.")
            return 1

    print("\nsurvived %d cycles (%.1f min) without failing — raise --max, or the ageing needs "
          "something this loop does not do (menus, Raves-initiated quits)." % (a.max, (time.time() - t0) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
