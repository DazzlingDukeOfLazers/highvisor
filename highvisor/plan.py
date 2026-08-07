"""plan — routes between game states, derived from TRANSITIONS instead of per-node recipes.

WHY THIS EXISTS (the thing the recipe model did badly)
------------------------------------------------------
The original ``goto[app]`` model stores, on every node, a COMPLETE route from a known base
(the title screen). That works and it is what got us this far, but it has four costs that
show up as flaky runs rather than as obviously-bad data:

* **It only routes from the base.** Every recipe opens with ``{"goto": "title"}`` or
  ``{"goto": "in_game"}``. Start somewhere the recipe did not anticipate and it does not
  re-route — it drives the base's recipe from the wrong screen and fails a step that was
  never wrong. That is the entire "goto needs retries" folklore.
* **Prefixes are duplicated.** All eight status tabs repeat the same two ghost-modal
  self-heals plus a chain step; fixing one meant fixing eight, and they drifted.
* **Cost is invisible.** A restart and a `uiback` are both "a step", so nothing prefers the
  cheap first-party move over the expensive one. We know a Qud restart is ~2 minutes and
  `uiback` is instant; the data never said so.
* **An impossible target fails MIDWAY.** You learn it cannot be done after the app has been
  driven halfway there, in whatever state that left.

Here, an edge is a TRANSITION: *from* these states, doing these steps, you arrive at *this*
state, at *this* cost. Routes are then derived. Reaching a state from an unexpected screen is
an ordinary search, prefixes are shared because they are one edge, cost is a number the
planner minimises, and an unreachable target is a PLANNING failure — reported before anything
has been touched.

This module is pure data + search: no backend, no I/O beyond the tree the caller hands in.
That is deliberate, and it is what lets `tools/selftest_plan.py` check the whole graph in
milliseconds with nothing running (docs/testing.md: decide statically what can be decided
statically).

WHY A* WITH A ZERO HEURISTIC
----------------------------
``route()`` is A*; its default heuristic is zero, which makes it exactly Dijkstra. That is a
considered choice, not a stub. The obvious heuristic — hops in the CONTAINMENT tree — is
inadmissible on this graph: `status_skills` to `status_journal` is two tree hops but ONE
transition (a `statustab` bridge call), so the heuristic overestimates and A* would happily
return a costlier route while looking like it worked. With ~60 states and ~100 edges the
search is microseconds either way, so optimality is worth more than pruning. Pass
``heuristic=`` if a genuinely admissible one ever appears.
"""

import heapq

# What a step COSTS us, by kind. These are not runtimes; they are "how much do we want to
# avoid this", learned the hard way and editable in gametree.json's `costs` block:
#   * first-party bridge commands are near-free and never miss
#   * anything OCR-driven is expensive because it is the one class that goes FLAKY
#     (a missed `click_text` does not fail cleanly, it clicks the wrong thing)
#   * launching is slow; restarting is the last resort that always works
DEFAULT_COSTS = {
    "goto": 0,
    "sleep": 1,
    "bridge": 1,
    "activate": 1,
    "key": 2,
    "keys": 2,
    "dock": 2,
    "assert": 2,
    "click": 3,
    "click_hover": 3,
    "command": 4,
    "dismiss": 4,
    "wait_window": 5,
    "load_save": 6,
    "click_text": 10,
    "launch": 60,
    "restart": 120,
}
_UNKNOWN_STEP_COST = 3

#: The two states that are not tree nodes. ``off`` = no window at all; ``unknown`` = the
#: window is up but nothing matched (a cheap no-OCR poll, or a screen we have not modelled).
#: Both must be real planner states or "route me out of here" has nowhere to start — which
#: is precisely the case the old model could not express.
OFF = "off"
UNKNOWN = "unknown"


def costs(tree):
    """The cost table: defaults overlaid with the tree's own ``costs`` block."""
    t = dict(DEFAULT_COSTS)
    t.update((tree or {}).get("costs") or {})
    return t


def step_cost(step, table):
    """Cost of one step — the sum over the keys it uses that the table knows about.

    A step is a dict with one action key plus modifiers (``window``, ``note``, ``timeout``),
    so unknown keys are ignored rather than charged. A step whose action we do not recognise
    is charged a middling default: unknown should not be free, or a typo becomes the
    cheapest route in the graph.
    """
    hits = [table[k] for k in step if k in table]
    return sum(hits) if hits else _UNKNOWN_STEP_COST


def derive_cost(steps, table):
    """Cost of an edge from its steps. Never zero — a free edge makes the search
    indifferent between doing something and doing nothing."""
    return max(1, sum(step_cost(s, table) for s in (steps or [])))


def node_ids(tree):
    """Every node id in the containment tree (excluding the synthetic root)."""
    out = []

    def walk(n):
        if n.get("id") and n["id"] != "root":
            out.append(n["id"])
        for ch in n.get("children") or []:
            walk(ch)

    walk(tree["root"])
    return out


def subtree_ids(tree, node_id):
    """``node_id`` plus every descendant — the states an edge marked
    ``{"within": node_id}`` may legally start from.

    This is how "leave the status screens" is written ONCE instead of once per tab. The
    containment tree already knows that being on the Journal tab is being inside the status
    screens; the graph should not have to restate it eight times.
    """
    found = []

    def walk(n, inside):
        here = inside or n.get("id") == node_id
        if here and n.get("id") and n["id"] != "root":
            found.append(n["id"])
        for ch in n.get("children") or []:
            walk(ch, here)

    walk(tree["root"], False)
    return found


def _sources(spec, tree, universe):
    """Concrete planner states an edge's ``from`` spec covers."""
    if spec == "*":
        return list(universe)
    if isinstance(spec, dict):
        if "within" in spec:
            return subtree_ids(tree, spec["within"])
        return []
    if isinstance(spec, list):
        out = []
        for s in spec:
            out.extend(_sources(s, tree, universe))
        return out
    return [spec] if spec else []


def transitions(tree, app):
    """The app's transitions, normalized: cost filled in, verify defaulted, id stamped.

    ``verify`` defaults to ``{"node": to}`` because an edge that does not check its own
    arrival is worse than no edge: the route continues from a state it only ASSUMES it is
    in, and the failure surfaces several steps later somewhere unrelated. Every edge
    verifies. That invariant is what makes re-planning after a failure safe.
    """
    out = []
    table = costs(tree)
    for i, tr in enumerate(tree.get("transitions") or []):
        if tr.get("app") != app:
            continue
        e = dict(tr)
        e.setdefault("steps", [])
        if "cost" not in e:
            e["cost"] = derive_cost(e["steps"], table)
        if "verify" not in e:
            e["verify"] = {"node": e.get("to")}
        e["id"] = e.get("id") or "%s:%s->%s#%d" % (app, _spec_label(e.get("from")),
                                                   e.get("to"), i)
        out.append(e)
    return out


def _spec_label(spec):
    if isinstance(spec, dict) and "within" in spec:
        return "within(%s)" % spec["within"]
    if isinstance(spec, list):
        return "|".join(str(s) for s in spec)
    return str(spec)


def graph(tree, app, signals=None):
    """Adjacency: {state: [(cost, edge), ...]} over every planner state.

    ``signals`` (optional) filters out edges whose ``requires`` block does not hold right
    now — a precondition, not a runtime failure. An edge that needs the mod's bridge is not
    a route option while the port is shut, and the planner should route AROUND it rather
    than pick it and die on the first step.
    """
    universe = node_ids(tree) + [OFF, UNKNOWN]
    adj = {s: [] for s in universe}
    for e in transitions(tree, app):
        if not _requires_hold(e.get("requires"), signals):
            continue
        for src in _sources(e.get("from"), tree, universe):
            if src not in adj:
                adj[src] = []
            adj[src].append((e["cost"], e))
    return adj


def _requires_hold(req, signals):
    """Every key in ``requires`` must equal the live signal of the same name. An UNKNOWN
    signal (None) passes: refusing to plan because we did not poll something is worse than
    trying an edge that verifies its own arrival anyway."""
    if not req:
        return True
    sig = signals or {}
    for k, want in req.items():
        have = sig.get(k)
        if have is None:
            continue
        if bool(have) != bool(want):
            return False
    return True


def route(tree, app, start, goal, signals=None, heuristic=None):
    """Cheapest route of transitions from ``start`` to ``goal``.

    Returns ``{ok: True, route: [edge...], cost, from, to}`` — ``route`` is empty when we
    are already there — or ``{ok: False, error, ...}`` with a reason that says WHICH way the
    graph is broken, because "no route" alone sends you reading the whole JSON.
    """
    if start == goal:
        return {"ok": True, "route": [], "cost": 0, "from": start, "to": goal,
                "detail": "already at %s" % goal}
    adj = graph(tree, app, signals)
    if goal not in adj:
        return {"ok": False, "error": "unknown state %r for %s" % (goal, app)}
    h = heuristic or (lambda _s: 0)

    # A* — with the default zero heuristic this is Dijkstra; see the module docstring for
    # why the obvious tree-distance heuristic is inadmissible here.
    seen = set()
    best = {start: 0}
    # (f, seq, state, path) — seq breaks ties deterministically so the same tree always
    # plans the same route; without it heapq compares the dicts and raises.
    seq = 0
    pq = [(h(start), seq, start, [])]
    while pq:
        f, _, state, path = heapq.heappop(pq)
        if state in seen:
            continue
        seen.add(state)
        g = best[state]
        if state == goal:
            return {"ok": True, "route": path, "cost": g, "from": start, "to": goal,
                    "detail": "%d transition(s), cost %d" % (len(path), g)}
        for cost, edge in adj.get(state, []):
            nxt = edge.get("to")
            if nxt in seen:
                continue
            ng = g + cost
            if nxt in best and best[nxt] <= ng:
                continue
            best[nxt] = ng
            seq += 1
            heapq.heappush(pq, (ng + h(nxt), seq, nxt, path + [edge]))

    return {"ok": False, "from": start, "to": goal,
            "error": _why_unreachable(tree, app, start, goal, adj, seen),
            "reached": sorted(seen)}


def _why_unreachable(tree, app, start, goal, adj, reached):
    """Name the actual gap. Three different repairs hide behind "no route"."""
    entering = [e for st in adj for _, e in adj[st] if e.get("to") == goal]
    if not entering:
        return ("no transition ENTERS %r for %s — nothing in the graph can produce that "
                "state, so no starting point would help" % (goal, app))
    if not adj.get(start):
        return ("no transition LEAVES %r for %s — %s is a dead end (add an exit edge, or a "
                "'*' edge such as restart)" % (start, app, start))
    return ("no route %s -> %s for %s: %d state(s) are reachable from %s and %r is not one "
            "of them" % (start, goal, app, len(reached), start, goal))


def preflight(tree, app):
    """Self-heal rules that run BEFORE planning, not as graph edges.

    A ghost modal (Qud's pooled ``PopupMessage`` that reports live with nothing on screen)
    is not a STATE — it is a condition that can sit on top of any state and silently eat
    every key. Modelling it as a node would double the graph; modelling it as an edge would
    need self-loops, which a shortest-path search cannot use. So it is a guard: check the
    condition, clear it, then plan against what is actually there.

    This is where the sixteen copy-pasted ghost-modal dismisses in the old status-tab
    recipes went — one declaration, applied to every route.
    """
    return [r for r in (tree.get("preflight") or []) if r.get("app") == app]


def summarize(rt):
    """One line per transition — what `hv plan` prints and what the trace records."""
    if not rt.get("ok"):
        return rt.get("error", "no route")
    if not rt["route"]:
        return rt.get("detail", "already there")
    parts = ["%s (%d)" % (e.get("to"), e.get("cost", 0)) for e in rt["route"]]
    return "%s -> %s  [cost %d]" % (rt["from"], " -> ".join(parts), rt["cost"])
