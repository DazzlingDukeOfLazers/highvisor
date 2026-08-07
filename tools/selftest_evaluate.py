#!/usr/bin/env python3
"""SPOT test for gametree.evaluate — which node wins, and why. Stdlib only, nothing running.

    python3 tools/selftest_evaluate.py

THE BUG IT WAS WRITTEN FOR (observed live 2026-08-06). `hv state` reported

    qud     Title Screen  scene=play  via=live

while Qud was plainly in-game — confirmed by screenshot, which is the standing rule here.
Both `title` and `in_game` sit at depth 1 in the real tree. `in_game` matched the mod's
first-party `scene: "play"`; `title` matched its `{"game_live": false}` fallback, because the
game_live probe is a 0.35s read on Qud's bridge and a busy or just-restarted Qud can miss it.
Two matches at equal depth, and the winner was decided by which node appears FIRST in the
children array.

Harmless while a human read the line. Not harmless once `gamego` PLANS from the detected
state: a stray "title" makes it plan title->in_game, whose edge is `load_save` — reloading the
save over a running game. The refactor made a long-standing wobble consequential, which is
exactly the kind of thing that deserves a test rather than a comment.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from highvisor import gametree  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, (" — " + detail) if detail else ""))
        FAILED.append(name)


def sig(**kw):
    s = {"present": True, "port_open": None, "game_live": None, "ocr_text": None,
         "scene": None, "tab": None}
    s.update(kw)
    return s


# Same shape as the real tree's collision: two depth-1 siblings, the shallow-signal one first.
TOY = {
    "apps": {"a": {}},
    "root": {"id": "root", "children": [
        {"id": "title", "detect": {"a": [{"scene": "MainMenu"}, {"game_live": False}]}},
        {"id": "in_game", "detect": {"a": [{"scene": "play"}, {"game_live": True}]},
         "children": [
             {"id": "status", "detect": {"a": [{"scene": "StatusScreens"}]}, "children": [
                 {"id": "status_journal",
                  "detect": {"a": [{"scene": "StatusScreens", "tab": "Journal"}]}},
             ]},
         ]},
    ]},
}


def main():
    print("evaluate: signal trust vs tree order")

    # THE REGRESSION. Both match at depth 1; the first-party scene must win over the inference.
    r = gametree.evaluate(TOY, "a", sig(scene="play", game_live=False))
    check("a first-party scene beats a stale game_live at equal depth",
          r["node"] == "in_game" and r["via"] == "scene",
          "%s via %s" % (r["node"], r["via"]))

    # ...and the reverse ordering must not have been "fixed" by simply preferring the LAST node.
    r = gametree.evaluate(TOY, "a", sig(scene="MainMenu", game_live=True))
    check("the same rule picks title when the scene says MainMenu",
          r["node"] == "title" and r["via"] == "scene", "%s via %s" % (r["node"], r["via"]))

    # Depth still dominates trust — a deeper node with a WEAKER signal should still win, or the
    # ranking would flip the tab/scene hierarchy the status screens depend on.
    r = gametree.evaluate(TOY, "a", sig(scene="StatusScreens", tab="Journal"))
    check("depth beats trust (the deepest match still wins)",
          r["node"] == "status_journal" and r["via"] == "tab",
          "%s via %s" % (r["node"], r["via"]))

    # With no first-party report at all, the inference is still allowed to decide.
    r = gametree.evaluate(TOY, "a", sig(game_live=False))
    check("game_live alone still resolves the title", r["node"] == "title" and r["via"] == "live",
          "%s via %s" % (r["node"], r["via"]))
    r = gametree.evaluate(TOY, "a", sig(game_live=True))
    check("game_live alone still resolves in-game",
          r["node"] == "in_game" and r["via"] == "live", "%s via %s" % (r["node"], r["via"]))

    # Honest unknowns, both directions.
    r = gametree.evaluate(TOY, "a", sig())
    check("nothing matched -> running, screen unknown",
          r["node"] is None and r["running"] and not r["off"], str(r))
    r = gametree.evaluate(TOY, "a", sig(present=False))
    check("no window -> off", r["off"] and not r["running"], str(r))

    check("the trust table ranks first-party above inference",
          gametree.TRUST["tab"] > gametree.TRUST["scene"] > gametree.TRUST["ocr"]
          > gametree.TRUST["live"] >= gametree.TRUST["port"])

    # The real tree, against the exact signals that produced the bad reading.
    real = gametree.load_tree(force=True)
    r = gametree.evaluate(real, "qud", sig(scene="play", game_live=False, port_open=True))
    check("REAL tree: scene=play + game_live False reads as in_game, not title",
          r["node"] == "in_game", "%s via %s" % (r["node"], r["via"]))
    r = gametree.evaluate(real, "qud", sig(scene="MainMenu", game_live=False, port_open=True))
    check("REAL tree: the title still reads as the title", r["node"] == "title",
          "%s via %s" % (r["node"], r["via"]))
    r = gametree.evaluate(real, "raves", sig(scene="status_journal"))
    check("REAL tree: raves status tab resolves to its leaf", r["node"] == "status_journal",
          "%s via %s" % (r["node"], r["via"]))

    print("\n%s (%d checks failed)" % ("all good" if not FAILED else "FAILED", len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
