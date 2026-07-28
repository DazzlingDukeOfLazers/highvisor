"""orchestrator — the shortcode/opcode router for the agent feedback loop.

Watches each registered agent's ON-SCREEN output (Claude Code via the AX tree; the
ChatGPT desktop app via OCR — it exposes no usable AX), extracts ``hv:`` opcodes,
and — GATED — drives the target agent: types a message into its input, or clicks
Claude's Approve / Approve-all / Deny buttons. Everything detected/done is published
to the EventBus, so the cockpit shows a live, interruptible audit trail.

Opcode grammar — human-readable and OCR-safe. The marker is a VISIBLE header line,
NOT a code-fence info string (which renders invisibly, so OCR can't see it):

    hv: <verb> <machine>/<agent>
    ```
    <payload / reason>
    ```

verbs: ``ask`` (type payload + submit), ``approve`` / ``approve-all`` / ``deny``
(click Claude's buttons; body = optional reason), ``key`` (send a key), ``end``
(stop). The body runs to the next ``hv:`` header, an ``hv: end`` line, or end of
text; surrounding ``` fences are stripped so the SAME grammar parses from raw AX
text and from OCR (which drops the fence markers) alike.
"""
import hashlib
import re
import threading
import time

from . import protocol as P

VERBS = {"ask", "approve", "approve-all", "deny", "key", "end"}
# Button/control verbs carry at most a one-line reason (never a multi-line payload).
BUTTON_VERBS = {"approve", "approve-all", "deny", "end"}

# A header line: "hv: ask mac/claude". Tolerant of OCR — allow ':' or whitespace
# after hv, and spaces around the '/'. Verb is validated against VERBS below.
_HEADER = re.compile(
    r"(?im)^[ \t>]*hv[:\s][ \t]*(?P<verb>[a-z][a-z-]*)[ \t]+"
    r"(?P<machine>[a-z0-9]+)[ \t]*/[ \t]*(?P<agent>[a-z0-9]+)[ \t]*$")
_END = re.compile(r"(?i)^[ \t>]*hv[:\s]\s*end\s*$")
_FENCE = re.compile(r"^\s*```")


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
        raw = "%s|%s|%s" % (self.verb, self.target, self.body)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def as_event(self):
        return {"verb": self.verb, "target": self.target,
                "body": self.body[:400], "src": self.source, "fp": self.fp}


def _fence_body(region):
    """Content between the first ```…``` pair in ``region``, or None if no pair."""
    start = None
    for i, ln in enumerate(region):
        if _FENCE.match(ln):
            if start is None:
                start = i
            else:
                return "\n".join(region[start + 1:i]).strip()
    return None


def parse_opcodes(text, source=""):
    """Extract opcodes from a blob of on-screen text (AX-flattened or OCR).

    Body rules, so the same grammar survives AX (fenced) and OCR (fences dropped):
      - if a ```…``` pair is present, the body is exactly its content;
      - else a button/control verb takes the first non-empty line (a reason);
      - else (ask/key, no fence — the OCR case) the body runs to an ``hv: end``
        line or the next opcode.
    """
    lines = text.splitlines()
    headers = []
    for idx, ln in enumerate(lines):
        m = _HEADER.match(ln)
        if m and m.group("verb") in VERBS:
            headers.append((idx, m))
    ops = []
    for hi, (idx, m) in enumerate(headers):
        verb = m.group("verb")
        nxt = headers[hi + 1][0] if hi + 1 < len(headers) else len(lines)
        region = lines[idx + 1:nxt]
        fenced = _fence_body(region)
        if fenced is not None:
            body = fenced
        elif verb in BUTTON_VERBS:
            body = next((ln.strip() for ln in region if ln.strip()), "")
        else:
            keep = []
            for ln in region:
                if _END.match(ln):
                    break
                keep.append(ln)
            body = "\n".join(keep).strip()
        ops.append(Opcode(verb, m.group("machine"), m.group("agent"), body, source))
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

    def read_source(self, s: Source) -> str:
        if s.mode == "ocr":
            r = self.engine.submit({"op": P.OP_OCR, "target": s.target})
            return r.get("text", "") if r.get("ok") else ""
        r = self.engine.submit({"op": P.OP_INSPECT, "target": s.target, "depth": 45})
        return flatten_ax_text(r.get("tree", {})) if r.get("ok") else ""

    def scan_once(self):
        """Read every source, emit any NEW opcodes to the bus. Returns the new ones."""
        found = []
        for s in self.sources:
            for op in parse_opcodes(self.read_source(s), s.name):
                if op.fp in self._seen:
                    continue
                self._seen.add(op.fp)
                self.bus.publish("opcode", **op.as_event())
                found.append(op)
        return found

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

    def deliver(self, agent, body, submit=True):
        """`ask`: focus the agent's composer, replace its contents with ``body``,
        and (optionally) submit."""
        a = AGENTS.get(agent)
        if a is None:
            return {"ok": False, "error": "unknown agent: %s" % agent}
        win = a["window"]
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
