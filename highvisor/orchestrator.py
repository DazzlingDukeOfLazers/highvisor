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
