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

    # A TORN READ must not take the daemon down. The tree hot-reloads on mtime, so any
    # non-atomic writer (an editor, a script that dumps then appends) leaves a window where the
    # file is half a document — and every op goes through the tree, so a raise there answered
    # JSONDecodeError to everything until someone touched the file again.
    import os, shutil, tempfile, time
    good = gametree.load_tree(force=True)
    n = len(good.get("transitions") or [])
    path = gametree._PATH
    backup = tempfile.mktemp(suffix=".json")
    shutil.copy(path, backup)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"root": {"id": "root"')      # a half-written save
        os.utime(path, None)
        time.sleep(0.01)
        served = gametree.load_tree()
        check("a torn read keeps serving the last good tree",
              len(served.get("transitions") or []) == n)
    finally:
        shutil.copy(backup, path)
        os.remove(backup)
        gametree.load_tree(force=True)

    assert_tolerance()

    print("\n%s (%d checks failed)" % ("all good" if not FAILED else "FAILED", len(FAILED)))
    return 1 if FAILED else 0


def assert_tolerance():
    """`assert node=X` tolerates landing DEEPER than X, and must not tolerate not moving.

    THE BUG (measured live 2026-08-07). A `me_menu_file -> map_editor` edge — steps
    `[{key: escape}]`, verify `{node: map_editor}` — passed while the File dropdown was
    still open and the state file still read `tab='File'`, because map_editor is on
    me_menu_file's path. The route continued, the next click landed on the menu bar with a
    dropdown down (which cancels it and opens nothing), and `hv goto` returned ok=True
    having arrived nowhere.

    The tolerance itself is RIGHT and must survive: detection reports the deepest match, so
    an edge aiming at a container legitimately lands on a child (raves title->new_game
    arrives on game_mode). What was missing is that the tolerance is DIRECTIONAL — it may
    not swallow "I never left". _drive_route supplies `not_within` for any climbing edge;
    `exact` is the manual form.
    """
    from highvisor.engine import Engine
    holds = Engine._assert_holds        # pure over (want, state); no backend, no daemon

    IN_MENU = {"node": "me_menu_file", "path": ["title", "modding_toolkit", "map_editor",
                                                "me_menu_file"]}
    IN_EDITOR = {"node": "map_editor", "path": ["title", "modding_toolkit", "map_editor"]}
    IN_CHARGEN = {"node": "game_mode", "path": ["title", "new_game", "game_mode"]}

    print("\nassert tolerance (directional)")
    check("landing DEEPER than the asked-for node still passes",
          holds(None, {"node": "new_game"}, IN_CHARGEN))
    check("the exact node passes",
          holds(None, {"node": "map_editor"}, IN_EDITOR))

    check("an ancestor alone still passes without a direction",
          holds(None, {"node": "map_editor"}, IN_MENU))
    check("`exact` REJECTS the still-open dropdown",
          not holds(None, {"node": "map_editor", "exact": True}, IN_MENU))
    check("`exact` accepts the editor itself",
          holds(None, {"node": "map_editor", "exact": True}, IN_EDITOR))

    # what _drive_route now attaches to a climbing edge
    climb = {"node": "map_editor", "not_within": "me_menu_file"}
    check("a CLIMBING edge's verify fails while we are still where we started",
          not holds(None, climb, IN_MENU))
    check("the same verify passes once the dropdown is gone",
          holds(None, climb, IN_EDITOR))
    check("not_within rejects DESCENDANTS of the named node too",
          not holds(None, {"node": "title", "not_within": "map_editor"}, IN_MENU))
    check("not_within leaves an unrelated branch alone",
          holds(None, {"node": "new_game", "not_within": "map_editor"}, IN_CHARGEN))

    # `exact` must not be mistaken for a condition — asserting it ALONE is not an assertion
    eng = Engine.__new__(Engine)
    r = Engine._assert_state(eng, None, {"app": "qud", "exact": True})
    check("`exact` alone is not a condition", r.get("ok") is False, str(r))


if __name__ == "__main__":
    sys.exit(main())
