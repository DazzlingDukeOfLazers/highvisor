#!/usr/bin/env python3
"""SPOT self-test for the pid-keyed state-file reader (engine._read_state_file).

Why this exists: the shared report path has one writer per running app instance. With
three Raves alive, raves_state.json cycled in_game -> status_tinkering -> title every two
seconds and every read was a coin flip, so `hv state` reported screens the window in front
of us was not on and `hv goto` "needed retries". The fix reads the per-process sidecar
(raves_state.<pid>.json) for the pid that owns the window being evaluated.

That is a pure function of (files on disk, pid), so it is decided STATICALLY here rather
than by driving two copies of a game. Stdlib only; no daemon, no apps.

    python3 tools/selftest_state_read.py      # exit 0 clean, 1 with the failures named
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from highvisor.engine import Engine  # noqa: E402

FAILS = []


def check(name, got, want):
    if got != want:
        FAILS.append("%s: got %r, want %r" % (name, got, want))


def write(path, scene, pid=None, age=0.0):
    d = {"scene": scene, "ts": int(time.time())}
    if pid is not None:
        d["pid"] = pid
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    if age:
        t = time.time() - age
        os.utime(path, (t, t))


def main():
    eng = Engine.__new__(Engine)          # the reader touches no instance state
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "raves_state.json")
        side = os.path.join(d, "raves_state.101.json")

        # 1. no pid asked for -> shared file, exactly as before this change (Qud's mod)
        write(base, "title")
        check("shared read", eng._read_scene(base), "title")

        # 2. THE BUG: another instance owns the shared file. Reading it would report a
        #    screen belonging to a window we are not looking at -> refuse, report nothing.
        write(base, "status_tinkering", pid=999)
        check("foreign shared report rejected", eng._read_scene(base, 101), None)

        # 3. our own sidecar answers, and it WINS over a foreign shared file
        write(side, "in_game", pid=101)
        check("sidecar preferred", eng._read_scene(base, 101), "in_game")

        # 4. a stale sidecar is not a fallback to a foreign shared file
        write(side, "in_game", pid=101, age=eng.STATE_FILE_TTL + 5)
        check("stale sidecar + foreign shared", eng._read_scene(base, 101), None)

        # 5. ...but a shared file WE wrote is still good when the sidecar is stale
        write(base, "options", pid=101)
        check("own shared report", eng._read_scene(base, 101), "options")

        # 6. an unstamped report (older app build) stays readable — no forced upgrade
        write(base, "title")
        check("unstamped report accepted", eng._read_scene(base, 101), "title")

        # 7. extras come from the same resolved report, not a second guess
        write(side, "in_game", pid=101)
        check("extra follows sidecar",
              (eng._read_state_extra(base, 101) or {}).get("pid"), 101)

    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("ok — pid-keyed state reads (7 cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
