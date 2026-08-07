#!/usr/bin/env python3
"""SPOT test for the transition graph and its planner. Stdlib only; nothing running.

Two halves, and the split is the point:

  * **Planner logic** against a tiny synthetic tree — search, cost, `within`, `*`, and the
    three distinct "unreachable" diagnoses. Fixed input, so a failure here is a planner bug.
  * **The real gametree.json** — every state we ever drive to is reachable from every state
    we might be found in, nothing points at a node that does not exist, and the routes we
    care about have the shape we think they have.

The second half is the one that earns its keep. Under the old recipe model, "can this app
get from here to there?" was answerable only by driving both apps and watching, which is
how a broken route survived until a capture run tripped over it. It is now a property of
the data, decidable in milliseconds (docs/testing.md: decide statically what can be decided
statically).

    python3 tools/selftest_plan.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from highvisor import gametree, plan  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, (" — " + detail) if detail else ""))
        FAILED.append(name)


# --------------------------------------------------------------- synthetic tree
TOY = {
    "apps": {"a": {"label": "A"}},
    "root": {"id": "root", "children": [
        {"id": "home", "children": [
            {"id": "menu", "children": [
                {"id": "deep"},
            ]},
        ]},
        {"id": "island"},
    ]},
    "transitions": [
        {"app": "a", "from": "off", "to": "home", "steps": [{"launch": "x"}]},
        {"app": "a", "from": "home", "to": "menu", "steps": [{"click_text": "Menu"}]},
        {"app": "a", "from": "menu", "to": "deep", "steps": [{"key": "d"}]},
        {"app": "a", "from": {"within": "menu"}, "to": "home", "steps": [{"key": "Escape"}]},
        {"app": "a", "from": "*", "to": "home", "steps": [{"restart": "a"}]},
        {"app": "a", "from": "home", "to": "deep", "cost": 999,
         "steps": [{"key": "shortcut"}], "note": "a dear direct edge the planner must reject"},
    ],
}


def toy():
    print("planner logic (synthetic tree)")
    r = plan.route(TOY, "a", "home", "home")
    check("already-there routes to an empty list", r["ok"] and r["route"] == [])

    r = plan.route(TOY, "a", "home", "deep")
    check("prefers two cheap hops over one dear direct edge",
          r["ok"] and [e["to"] for e in r["route"]] == ["menu", "deep"],
          str(r.get("route")))

    r = plan.route(TOY, "a", "deep", "home")
    check("a `within` edge fires from a DESCENDANT",
          r["ok"] and len(r["route"]) == 1 and r["route"][0]["to"] == "home")

    r = plan.route(TOY, "a", plan.UNKNOWN, "home")
    check("`*` gives `unknown` a way out", r["ok"] and r["route"][0]["steps"][0] == {"restart": "a"})

    r = plan.route(TOY, "a", "off", "deep")
    check("cold start chains launch -> menu -> deep",
          r["ok"] and [e["to"] for e in r["route"]] == ["home", "menu", "deep"],
          str([e.get("to") for e in r.get("route", [])]))

    # every reported cost must be the sum of its edges — a planner that returns a route
    # whose cost does not add up cannot be reasoned about
    check("route cost == sum of edge costs",
          r["ok"] and r["cost"] == sum(e["cost"] for e in r["route"]))

    check("launch is dearer than a keypress",
          plan.derive_cost([{"launch": "x"}], plan.DEFAULT_COSTS)
          > plan.derive_cost([{"key": "d"}], plan.DEFAULT_COSTS))
    check("an unrecognised step is not free",
          plan.derive_cost([{"wiggle": 1}], plan.DEFAULT_COSTS) > 0)
    check("explicit cost overrides the derived one",
          [e for e in plan.transitions(TOY, "a") if e["cost"] == 999])

    # the three distinct unreachable diagnoses
    r = plan.route(TOY, "a", "home", "island")
    check("unreachable names the missing INBOUND edge",
          not r["ok"] and "ENTERS" in r["error"], r.get("error", ""))

    # a start with no OUTBOUND edge at all — distinct from "the goal has no inbound edge",
    # and it wants a different repair, so the two must not share a message
    dead = dict(TOY, transitions=[t for t in TOY["transitions"]
                                  if t["from"] not in ("*", {"within": "menu"})])
    r = plan.route(dead, "a", "deep", "home")
    check("a dead-end start is diagnosed as such",
          not r["ok"] and "LEAVES" in r["error"], r.get("error", ""))

    r = plan.route(TOY, "a", "home", "nowhere")
    check("an unknown target is rejected before searching",
          not r["ok"] and "unknown state" in r["error"], r.get("error", ""))

    # determinism: same tree, same answer, every time (heap ties broken by insertion order)
    runs = {plan.summarize(plan.route(TOY, "a", "off", "deep")) for _ in range(20)}
    check("planning is deterministic", len(runs) == 1)

    check("verify defaults to the destination node",
          all(e["verify"] for e in plan.transitions(TOY, "a")))


# ------------------------------------------------------------------- real tree
def _detectable(tree, app):
    """Every node id the tree can RECOGNISE for `app` — i.e. every state we can be found in.

    Distinct from the set of transition targets (the states we aim at), and the difference is
    where the interesting failures live: a state nothing drives to still has to be leavable.
    """
    found = set()

    def walk(n):
        if n.get("id") and app in (n.get("detect") or {}):
            found.add(n["id"])
        for c in n.get("children") or []:
            walk(c)

    walk(tree["root"])
    return found


def real():
    tree = gametree.load_tree(force=True)
    apps = sorted(gametree.apps(tree))
    ids = set(plan.node_ids(tree))
    print("\nreal gametree.json (%d states, %d transitions)"
          % (len(ids), len(tree.get("transitions") or [])))

    # 1. no transition names a state that does not exist — a typo here is otherwise
    #    invisible until a route silently omits the edge
    bad = []
    for tr in tree.get("transitions") or []:
        for who, spec in (("to", tr.get("to")), ("from", tr.get("from"))):
            for s in _spec_nodes(spec):
                if s not in ids and s not in (plan.OFF, plan.UNKNOWN, "*"):
                    bad.append("%s %s=%r" % (tr.get("app"), who, s))
    check("every transition endpoint is a real node", not bad, "; ".join(bad))

    # 2. every app declared in the tree has a graph at all
    for app in apps:
        check("%s has transitions" % app, len(plan.transitions(tree, app)) > 0)

    # 3. THE PROPERTY THE RECIPE MODEL COULD NOT GIVE US: from any state we can be
    #    DETECTED in, every state we drive to is reachable. This is the whole refactor
    #    in one assertion.
    for app in apps:
        targets = sorted({t["to"] for t in plan.transitions(tree, app)})
        # Starts are not just the places we DRIVE to. Anything the tree can DETECT is
        # somewhere we can be found, and several such nodes are never any edge's `to`:
        # states you fall into rather than aim for (`quit_dialog`, `summary`, and
        # `stranded_stage` -- a game that ended with the view stuck on the stage). Deriving
        # starts from targets alone left exactly those untested for "can we get out?", which
        # is the only question that matters about a state you cannot aim at.
        starts = sorted(set(targets) | _detectable(tree, app) | {plan.OFF, plan.UNKNOWN})
        misses = []
        for start in starts:
            for goal in targets:
                r = plan.route(tree, app, start, goal)
                if not r["ok"]:
                    misses.append("%s->%s" % (start, goal))
        check("%s: all %d targets reachable from all %d starts"
              % (app, len(targets), len(starts)), not misses,
              "%d gaps: %s" % (len(misses), ", ".join(misses[:6])))

    # 4. no route should need the restart hatch when a first-party path exists. Restart
    #    appearing in an ordinary route is the signal that a real exit edge is missing,
    #    so name the pairs that rely on it rather than asserting none do.
    for app in apps:
        targets = sorted({t["to"] for t in plan.transitions(tree, app)})
        via_restart = []
        for start in targets:
            for goal in targets:
                if start == goal:
                    continue
                r = plan.route(tree, app, start, goal)
                if r["ok"] and any("restart" in s for e in r["route"] for s in e["steps"]):
                    via_restart.append("%s->%s" % (start, goal))
        print("  note %s: %d of %d modelled pairs still route via RESTART%s"
              % (app, len(via_restart), len(targets) * (len(targets) - 1),
                 (" (" + ", ".join(via_restart[:8]) + ")") if via_restart else ""))

    # 5. the routes whose SHAPE we specifically claim in the docs
    r = plan.route(tree, "qud", "status_journal", "status_skills")
    check("qud tab->tab is one bridge call",
          r["ok"] and len(r["route"]) == 1
          and r["route"][0]["steps"][0].get("bridge") == "statustab",
          plan.summarize(r))

    r = plan.route(tree, "raves", "status_journal", "status_skills")
    check("raves tab->tab is escape-then-key (2 edges), not a trip through title",
          r["ok"] and [e["to"] for e in r["route"]] == ["in_game", "status_skills"],
          plan.summarize(r))

    r = plan.route(tree, "qud", "status_equipment", "title")
    check("qud leaves the status screens before quitting",
          r["ok"] and [e["to"] for e in r["route"]] == ["in_game", "title"],
          plan.summarize(r))

    r = plan.route(tree, "raves", "off", "status_skills")
    check("raves cold start reaches a status tab",
          r["ok"] and [e["to"] for e in r["route"]] == ["title", "in_game", "status_skills"],
          plan.summarize(r))

    # 6. no node should carry a legacy `goto` recipe the graph can already reach. The
    #    recipes were all removed once the transitions covered them; one reappearing means
    #    two descriptions of the same move, which is the drift this refactor existed to end.
    #    (A recipe for a node the graph CANNOT reach is legitimate — that is the fallback.)
    dupes = []

    def walk(n):
        for app in (n.get("goto") or {}):
            if plan.route(tree, app, plan.UNKNOWN, n["id"]).get("ok"):
                dupes.append("%s:%s" % (app, n["id"]))
        for c in n.get("children") or []:
            walk(c)

    walk(tree["root"])
    check("no legacy recipe duplicates a route the graph already has", not dupes,
          ", ".join(dupes))

    # 7. preflight rules are well-formed (they run before every driven route)
    for app in apps:
        for rule in plan.preflight(tree, app):
            check("%s preflight rule has when+steps" % app,
                  bool(rule.get("when")) and bool(rule.get("steps")))


def _spec_nodes(spec):
    if isinstance(spec, dict):
        return [spec[k] for k in ("within",) if k in spec]
    if isinstance(spec, list):
        out = []
        for s in spec:
            out.extend(_spec_nodes(s))
        return out
    return [spec] if spec else []


if __name__ == "__main__":
    toy()
    real()
    print("\n%s (%d checks failed)" % ("FAILED" if FAILED else "all good", len(FAILED)))
    sys.exit(1 if FAILED else 0)
