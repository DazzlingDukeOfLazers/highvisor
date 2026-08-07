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

A BARE load/quit loop survives 40 cycles (measured 2026-08-07, ~9 min, one failure-free run),
so the plain cycle count is NOT the variable. The flags below add the things a real session does
between quits, one axis at a time — keep them separable, because "I turned on three things and
it broke" is exactly the reasoning that produced the three wrong diagnoses already on this edge:

    --menus     tour the TITLE-side modern screens between quits (records/options/mods).
                Those are the screens that ignore synthesized keys and close over the mod's
                `uiback`, i.e. the ones that leave async continuations behind.
    --status    tour the IN-GAME status screens before each quit (journal/quests/…). Same
                shape, but the window is opened while a game is live.
    --raves     drive the quit FROM RAVES (`hv goto raves title`) instead of from Qud. Raves'
                exit runs the same CmdQuit + popup-answer path, with Raves' own mirrored modal
                chain riding on top of it.

Writes one JSON line per cycle to tools/../age_qud_quit.jsonl next to the repo, so a run that
outlives the session it was started in is still readable. Each line carries the FULL Qud state
report after the quit attempt (scene/live/view/window), because the failure signature is not
"nothing happened": the harness has seen scene=Stage with live=false, which is a game that ENDED
while the view never left the stage — a different bug from a CmdQuit that never ran, and the
per-cycle record is the only place that distinction survives.
"""
import argparse
import json
import os
import subprocess
import sys
import time

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "age_qud_quit.jsonl")
STATE = os.path.expanduser("~/Library/Application Support/RavesOfQud/qud_state.json")
PLAYER_LOG = os.path.expanduser("~/Library/Logs/Freehold Games/CavesOfQud/Player.log")

# Title-side modern screens, and the in-game status tabs. Both tours return to the node they
# started from, so a cycle always ends where the next one expects to begin.
TITLE_MENUS = ["records", "options", "mods"]
STATUS_TABS = ["status_journal", "status_quests", "status_skills"]


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


def log_size():
    """Byte offset into Qud's Player.log, so each cycle can quote only its OWN mod lines."""
    try:
        return os.path.getsize(PLAYER_LOG)
    except OSError:
        return 0


def log_since(off, keep=("[popup]", "quit", "sync pump")):
    """The mod lines written since `off` that bear on the quit, newest last."""
    try:
        with open(PLAYER_LOG, errors="replace") as fh:
            fh.seek(off)
            lines = fh.read().splitlines()
    except OSError:
        return []
    return [l for l in lines if any(k in l for k in keep)][-12:]


def tour(app, nodes, home):
    """Visit each node and come back. Returns the ids that did NOT arrive.

    Deliberately NOT fatal: a modern screen that refuses to open is its own known flake
    (Records has an explicit note in the tree), and aborting the ageing run on one would
    throw away the accumulation we are trying to build.
    """
    missed = []
    for node in nodes:
        if not hv("goto", app, node).get("ok"):
            missed.append(node)
        if not hv("goto", app, home).get("ok"):
            missed.append("%s->%s" % (node, home))
    return missed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--no-restart", action="store_true",
                    help="age the process that is already up instead of starting clean")
    ap.add_argument("--menus", action="store_true",
                    help="tour the title-side modern screens between quits")
    ap.add_argument("--status", action="store_true",
                    help="tour the in-game status screens before each quit")
    ap.add_argument("--raves", action="store_true",
                    help="quit FROM RAVES (hv goto raves title) instead of from Qud")
    a = ap.parse_args()

    if not a.no_restart:
        hv("restart", "qud")
    if a.raves:
        # raves_solo, not the pair: the pair launcher spawns its own Qud, and two Quds is the
        # one condition `hv goto` refuses to drive through.
        hv("launch", "raves_solo")
    t0 = time.time()
    log = open(OUT, "a", buffering=1)
    driver = "raves" if a.raves else "qud"
    print("driver=%s menus=%s status=%s" % (driver, a.menus, a.status))

    for cycle in range(1, a.max + 1):
        load = hv("goto", driver, "in_game")
        if not load.get("ok"):
            rec = {"cycle": cycle, "phase": "load", "ok": False, "driver": driver,
                   "error": load.get("error"), "mins": round((time.time() - t0) / 60, 1)}
            log.write(json.dumps(rec) + "\n")
            print("cycle %d: LOAD failed — %s" % (cycle, rec["error"]))
            return 2

        missed = []
        if a.status:
            missed += tour(driver, STATUS_TABS, "in_game")

        st = qud_state()
        off = log_size()
        quit_ = hv("goto", driver, "title")
        after = qud_state()
        bad = [s for s in (quit_.get("steps") or []) if s.get("ok") is False]
        rec = {"cycle": cycle,
               "driver": driver,
               "quit_ok": bool(quit_.get("ok")),
               "failed_steps": len(bad),
               "error": (bad[0].get("error") if bad else None),
               "mins": round((time.time() - t0) / 60, 1),
               # the co-varying quantities, so the culprit can be told apart from the count
               "unity_scene": st.get("unity_scene"),
               "qud_ts": st.get("ts"),
               "after": (quit_.get("state") or {}).get("node"),
               # the WHOLE report after the attempt: scene=Stage with live=false is a game that
               # ended without the view following, and only these fields tell that apart from a
               # CmdQuit that never ran at all
               "after_scene": after.get("scene"),
               "after_live": after.get("live"),
               "after_view": after.get("view"),
               # THE FIELDS THAT SPLIT THE STRAND IN HALF (mod build 2026-08-07).
               # Qud keeps two view names: cur_view is the LOGICAL one XRLCore's menu loop
               # sets and tests, view is what GameManager.UpdateView last APPLIED. At a
               # strand, cur_view=MainMenu means the menu loop ran and UpdateView did not;
               # cur_view=Stage means control never got back to the loop. running/player
               # split the collapsed `live` flag, so "the game ended" can be told from
               # "The.Player went null while RunGame is still looping".
               "after_cur_view": after.get("cur_view"),
               "after_running": after.get("running"),
               "after_player": after.get("player"),
               "after_window": after.get("window"),
               "missed": missed or None,
               "mod_log": log_since(off) or None}
        log.write(json.dumps(rec) + "\n")
        print("cycle %2d  quit_ok=%-5s  %4.1f min  after=%s  scene=%s live=%s%s"
              % (cycle, rec["quit_ok"], rec["mins"], rec["after"],
                 rec["after_scene"], rec["after_live"],
                 ("  missed=%s" % missed) if missed else ""))
        sys.stdout.flush()

        if not quit_.get("ok"):
            print("\nBROKE at cycle %d after %.1f minutes: %s" % (cycle, rec["mins"], rec["error"]))
            for l in rec["mod_log"] or []:
                print("   mod| " + l)
            # PHOTOGRAPH the failure. The state file says which screen Qud THINKS it is on;
            # only the window says which one it is DRAWING, and the whole point of this edge's
            # history is that those two have disagreed.
            # ACTIVATE before the shot. Qud does not repaint unfocused, so a capture taken
            # as-is can be the last frame drawn before the game ended -- which is exactly the
            # ambiguity that made the first strand photograph unreadable (a rendered stage
            # that might have been a stale frame). Focus it, let it draw, then capture.
            shot = os.path.join(os.path.dirname(OUT), "age_fail_c%d.png" % cycle)
            hv("activate", "CavesOfQud", timeout=30)
            time.sleep(2.5)
            hv("shot", "CavesOfQud", shot, timeout=60)
            print("   shot| %s  (activated first, so this is a LIVE frame)" % shot)
            print("   state| " + json.dumps(qud_state()))
            print("Re-run to confirm the number repeats — if it does not, the count is not the "
                  "variable and the minutes/zone loads are worth a second look.")
            return 1

        if a.menus:
            missed = tour(driver, TITLE_MENUS, "title")
            if missed:
                print("     menu tour missed: %s" % missed)

    print("\nsurvived %d cycles (%.1f min) without failing — raise --max, or add an axis "
          "(--menus / --status / --raves)." % (a.max, (time.time() - t0) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
