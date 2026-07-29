"""orchestrator — the shortcode/opcode router for the agent feedback loop.

Watches each registered agent's ON-SCREEN output (Claude Code via the AX tree; the
ChatGPT desktop app via OCR — it exposes no usable AX), extracts ``hv:`` opcodes,
and — GATED — drives the target agent: types a message into its input, or clicks
Claude's Approve / Approve-all / Deny buttons. Everything detected/done is published
to the EventBus, so the cockpit shows a live, interruptible audit trail.

Opcode grammar — human-readable and OCR-safe. A VISIBLE header line, the payload,
and a REQUIRED ``hv: end`` terminator:

    hv: <verb> <machine>/<agent>
    <payload / reason>
    hv: end

verbs: ``ask`` (type payload + submit), ``approve`` / ``approve-all`` / ``deny``
(click Claude's buttons; body = optional reason), ``key`` (send a key). The
``hv: end`` is mandatory and is what makes the protocol safe to watch in a window
that also DISCUSSES it: an ``hv:`` written in prose (a grammar example, this
docstring) has no terminator, so it is never mistaken for a command. Code-fence
``` marks are NOT a delimiter — AX and OCR both drop them when reading rendered
text — so ``hv: end`` is the one thing that survives.
"""
import hashlib
import re
import threading
import time

from . import protocol as P

VERBS = {"ask", "approve", "approve-all", "deny", "key"}

# A header line: "hv: ask mac/claude". Tolerant of OCR — allow ':' or whitespace
# after hv, and spaces around the '/'. Verb is validated against VERBS below.
_HEADER = re.compile(
    r"(?im)^[ \t>]*hv[:\s][ \t]*(?P<verb>[a-z][a-z-]*)[ \t]+"
    r"(?P<machine>[a-z0-9]+)[ \t]*/[ \t]*(?P<agent>[a-z0-9]+)[ \t]*$")
_END = re.compile(r"(?i)^[ \t>]*hv[:\s]\s*end\s*$")


class Opcode:
    def __init__(self, verb, machine, agent, body, source=""):
        self.verb = verb
        self.machine = machine
        self.agent = agent
        self.body = body
        self.source = source

    @property
    def target(self):
        return "%s/%s" % (self.machine, self.agent)

    @property
    def fp(self):
        norm = re.sub(r"\s+", " ", self.body).strip()[:300]  # whitespace-stable
        raw = "%s|%s|%s" % (self.verb, self.target, norm)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def as_event(self):
        # Send enough body for the cockpit to render choice buttons from `(x)` option lines
        # (the pending panel scrolls, so a long body is fine); bounded to stay lightweight.
        return {"verb": self.verb, "target": self.target,
                "body": self.body[:2000], "src": self.source, "fp": self.fp}


def parse_opcodes(text, source=""):
    """Extract opcodes from a blob of on-screen text (AX-flattened or OCR).

    EVERY opcode must be closed by an ``hv: end`` line. This is the one delimiter
    that survives rendering — code-fence ``` marks do NOT (AX and OCR both drop
    them) — and, crucially, it means an ``hv:`` MENTIONED in prose (a grammar
    example, this very docstring, a discussion of the protocol) is NOT a command:
    no terminator, no opcode. The body is everything between the header and the
    ``hv: end``.
    """
    lines = text.splitlines()
    headers = []
    ends = []
    for idx, ln in enumerate(lines):
        if _END.match(ln):
            ends.append(idx)
            continue
        m = _HEADER.match(ln)
        if m and m.group("verb") in VERBS:
            headers.append((idx, m))
    ops = []
    used_ends = set()
    for idx, m in headers:
        end_i = next((e for e in ends if e > idx and e not in used_ends), None)
        if end_i is None:
            continue                      # unterminated -> prose, not a command
        used_ends.add(end_i)
        body_lines = [ln for ln in lines[idx + 1:end_i] if ln.strip() != "```"]
        body = "\n".join(body_lines).strip()
        ops.append(Opcode(m.group("verb"), m.group("machine"),
                          m.group("agent"), body, source))
    return ops


def flatten_ax_text(tree):
    """Depth-first concatenation of an inspect() tree's node text (value|name)."""
    out = []

    def walk(n):
        if not isinstance(n, dict):
            return
        for k in ("value", "name"):
            v = (n.get(k) or "").strip()
            if v:
                out.append(v)
                break
        for c in n.get("children", []) or []:
            walk(c)

    walk(tree or {})
    return "\n".join(out)


# Per-agent I/O: which window, how to read it, how to find its composer, and (for
# Claude) how to recognize its permission buttons. Composer hint is matched against
# OCR/AX text; the fallback is a window fraction if the hint isn't found.
AGENTS = {
    "chatgpt": {
        "window": "ChatGPT", "read": "ocr", "submit": "return",
        "composer_hint": r"(?i)do anything|ask anything|message chatgpt|send a message",
        "composer_fallback": (0.25, 0.90),
    },
    "claude": {
        "window": "Claude", "read": "ax", "submit": "return",
        "composer_hint": r"(?i)reply to claude|how can i help|message|write",
        "composer_fallback": (0.5, 0.95),
        "approve": [r"(?i)^\s*yes\b", r"(?i)\ballow\b", r"(?i)\bapprove\b", r"(?i)^\s*proceed"],
        "approve-all": [r"(?i)don.?t ask", r"(?i)always allow", r"(?i)approve all", r"(?i)yes.*all"],
        "deny": [r"(?i)^\s*no\b", r"(?i)\bdeny\b", r"(?i)\breject\b", r"(?i)don.?t allow", r"(?i)^\s*cancel"],
    },
}


class Source:
    """One agent to watch. ``name`` is the routing id ("mac/claude"); ``target`` is
    the highvisor window ref; ``mode`` is "ax" (rich a11y) or "ocr" (opaque apps)."""

    def __init__(self, name, target, mode):
        self.name = name
        self.target = target
        self.mode = mode


class Orchestrator:
    def __init__(self, engine, bus, sources=None):
        self.engine = engine
        self.bus = bus
        self.sources = sources or []
        self._seen = set()          # opcode fingerprints already emitted (dedup)
        self._run = False
        self.pending = {}           # fp -> Opcode awaiting the user's approval
        self.auto_lanes = set()     # "verb target" lanes the user approved wholesale
        self._lock = threading.Lock()

    def read_source(self, s: Source) -> str:
        if s.mode == "ocr":
            r = self.engine.submit({"op": P.OP_OCR, "target": s.target})
            return r.get("text", "") if r.get("ok") else ""
        r = self.engine.submit({"op": P.OP_INSPECT, "target": s.target, "depth": 45})
        return flatten_ax_text(r.get("tree", {})) if r.get("ok") else ""

    def scan_once(self):
        """Read every source; queue any NEW opcodes (gated). Returns the new ones."""
        found = []
        for s in self.sources:
            for op in parse_opcodes(self.read_source(s), s.name):
                if op.fp in self._seen:
                    continue
                self._seen.add(op.fp)
                self._ingest(op)
                found.append(op)
        return found

    # ---- gated queue ------------------------------------------------------------
    def _lane(self, op):
        return "%s %s" % (op.verb, op.target)

    def _ingest(self, op):
        """A new opcode: auto-run it if its lane is pre-approved, else hold it
        pending for the user."""
        if self._lane(op) in self.auto_lanes:
            self.bus.publish("opcode", status="auto", **op.as_event())
            self.execute(op)
            return
        with self._lock:
            self.pending[op.fp] = op
        self.bus.publish("opcode", status="pending", **op.as_event())

    def pending_list(self):
        with self._lock:
            ops = list(self.pending.values())
        return [dict(op.as_event(), lane=self._lane(op)) for op in ops]

    def act(self, fp, action):
        """User verdict on a pending opcode: approve / approve-all / deny."""
        with self._lock:
            op = self.pending.pop(fp, None)
        if op is None:
            return {"ok": False, "error": "no pending opcode %s" % fp}
        if action == "deny":
            self.bus.publish("opcode", status="denied", **op.as_event())
            return {"ok": True, "action": "denied", "fp": fp}
        if action == "approve-all":
            self.auto_lanes.add(self._lane(op))
            self.bus.publish("orch", msg="lane auto-approved: %s" % self._lane(op))
        res = self.execute(op)
        # approve-all: also drain any other pending opcodes already in this lane
        if action == "approve-all":
            lane = self._lane(op)
            with self._lock:
                drain = [o for o in self.pending.values() if self._lane(o) == lane]
                for o in drain:
                    self.pending.pop(o.fp, None)
            for o in drain:
                self.execute(o)
        return {"ok": bool(res.get("ok", True)), "action": action, "fp": fp}

    def prime(self):
        """Mark every opcode currently on screen as already-seen WITHOUT acting on
        it — so the loop only fires on opcodes that appear AFTER watching starts.
        Without this, scrollback and any discussion of the protocol itself (grammar
        examples, this very sentence) would be replayed as commands."""
        n = 0
        for s in self.sources:
            for op in parse_opcodes(self.read_source(s), s.name):
                self._seen.add(op.fp)
                n += 1
        self.bus.publish("orch", msg="primed: %d existing opcode(s) ignored" % n)
        return n

    # ---- drivers: turn an opcode into an action on the target agent -------------
    def _window_info(self, win):
        r = self.engine.submit({"op": P.OP_LIST})
        low = win.lower()
        for t in r.get("targets", []):
            if low in (t.get("title") or "").lower() or low in (t.get("class_name") or "").lower():
                return t
        return None

    def _click_win(self, win, x, y):
        return self.engine.submit({"op": P.OP_CLICK, "target": win, "x": int(x), "y": int(y)})

    def _find_ax(self, win, roles=None, text_patterns=None):
        """First AX node matching a role and/or a text pattern. Returns its bounds
        [x,y,w,h] (global points), or None."""
        r = self.engine.submit({"op": P.OP_INSPECT, "target": win, "depth": 50})
        if not r.get("ok"):
            return None
        hit = [None]

        def walk(n):
            if hit[0]:
                return
            role = n.get("role", "")
            txt = "%s %s" % (n.get("name", ""), n.get("value", ""))
            role_ok = (roles is None) or any(role.endswith(x) for x in roles)
            text_ok = (text_patterns is None) or any(re.search(p, txt) for p in text_patterns)
            if role_ok and text_ok and n.get("bounds"):
                hit[0] = n.get("bounds")
            for c in n.get("children", []) or []:
                walk(c)

        walk(r.get("tree", {}))
        return hit[0]

    def _focus_composer(self, win, a):
        """Click the target's text input so typing lands there."""
        if a["read"] == "ocr":
            r = self.engine.submit({"op": P.OP_OCR, "target": win})
            if not r.get("ok"):
                return False
            ow, oh = r["w"] or 1, r["h"] or 1
            info = self._window_info(win)
            scale = (info["w"] / ow) if info else 1.0
            cands = [b for b in r["boxes"] if re.search(a["composer_hint"], b["text"])]
            if cands:
                bb = max(cands, key=lambda b: b["bbox"][1])["bbox"]
                cx, cy = bb[0] + bb[2] // 2, bb[1] + bb[3] // 2
            else:
                cx, cy = a["composer_fallback"][0] * ow, a["composer_fallback"][1] * oh
            self._click_win(win, cx * scale, cy * scale)
            return True
        # ax: find an editable text area, click its centre; else fall back
        info = self._window_info(win)
        if info is None:
            return False
        b = self._find_ax(win, roles=("TextArea", "TextField", "ComboBox"))
        if b:
            self._click_win(win, b[0] - info["x"] + b[2] // 2, b[1] - info["y"] + b[3] // 2)
        else:
            self._click_win(win, a["composer_fallback"][0] * info["w"],
                            a["composer_fallback"][1] * info["h"])
        return True

    def deliver(self, agent, body, submit=True, focus=True):
        """`ask`: put ``body`` into the agent's composer, and (optionally) submit.

        ``focus=True`` clicks the composer first (needed when submitting, or when the
        tier-4 typing fallback is required). ``focus=False`` skips that click and relies
        on the tier-1 focus-free ``AXSetValue`` path (used by the pasted-pick relay so a
        cockpit click doesn't bounce focus to the agent window). Best-effort: if the
        editable can't be set focus-free, backend.text still falls back to tier-4."""
        a = AGENTS.get(agent)
        if a is None:
            return {"ok": False, "error": "unknown agent: %s" % agent}
        win = a["window"]
        if focus:
            if not self._focus_composer(win, a):
                return {"ok": False, "error": "composer not found for %s" % agent}
            time.sleep(0.15)
        # NB: no cmd+a clear — its tier-4 activate disrupts composer focus so the
        # following type doesn't land. The composer is empty after every submit, so
        # in the normal loop there's nothing to clear.
        self.engine.submit({"op": P.OP_TEXT, "target": win, "text": body})
        time.sleep(0.1)
        if submit:
            self.engine.submit({"op": P.OP_KEY, "target": win, "keys": a["submit"], "focus": True})
        return {"ok": True, "agent": agent, "submitted": submit}

    def press_submit(self, agent):
        """Press the agent's submit key (Return) in its composer — used to SEND text that was
        pasted earlier focus-free. Focuses the composer first so the key lands (a one-time focus
        steal at send time, unlike the focus-free paste)."""
        a = AGENTS.get(agent)
        if a is None:
            return {"ok": False, "error": "unknown agent: %s" % agent}
        win = a["window"]
        self._focus_composer(win, a)
        time.sleep(0.1)
        return self.engine.submit({"op": P.OP_KEY, "target": win, "keys": a["submit"], "focus": True})

    def press(self, agent, which):
        """`approve` / `approve-all` / `deny`: find the target's matching button in
        the AX tree and click it. Only meaningful for AX agents (Claude)."""
        a = AGENTS.get(agent)
        if a is None or which not in a:
            return {"ok": False, "error": "no %s button for %s" % (which, agent)}
        win = a["window"]
        info = self._window_info(win)
        if info is None:
            return {"ok": False, "error": "%s window not found" % win}
        b = self._find_ax(win, roles=("Button",), text_patterns=a[which])
        if b is None:
            return {"ok": False, "error": "no '%s' button on screen (is a prompt showing?)" % which}
        self._click_win(win, b[0] - info["x"] + b[2] // 2, b[1] - info["y"] + b[3] // 2)
        return {"ok": True, "agent": agent, "pressed": which}

    def execute(self, op: "Opcode"):
        """Run a (gated-approved) opcode against its target agent."""
        agent = op.agent
        if op.verb == "ask":
            res = self.deliver(agent, op.body, submit=True)
        elif op.verb in ("approve", "approve-all", "deny"):
            res = self.press(agent, op.verb)
        elif op.verb == "key":
            res = self.engine.submit({"op": P.OP_KEY, "target": AGENTS.get(agent, {}).get("window", agent),
                                      "keys": op.body, "focus": True})
        else:
            res = {"ok": False, "error": "verb %s not executable" % op.verb}
        self.bus.publish("orch", msg="exec %s %s -> %s" % (op.verb, op.target,
                                                           "ok" if res.get("ok") else res.get("error")))
        return res

    def start(self, interval=2.0):
        self.prime()
        self._run = True
        threading.Thread(target=self._loop, args=(interval,),
                         name="hv-orchestrator", daemon=True).start()

    def stop(self):
        self._run = False

    def _loop(self, interval):
        while self._run:
            try:
                self.scan_once()
            except Exception:
                pass
            time.sleep(interval)
