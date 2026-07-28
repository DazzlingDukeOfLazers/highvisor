"""Engine — the single-threaded action queue that owns the backend.

Every op runs on ONE worker thread. That thread calls ``backend.thread_init()``
first and then processes jobs serially, so all UIA/COM calls live in one apartment
and no backend method ever runs concurrently with another. Server connection
threads hand work in via :meth:`submit` and block until the worker replies.

Why single-threaded: Windows UI Automation is COM, and COM objects are apartment
bound. Serializing on one thread is simpler and safer than marshalling across
threads, and desktop automation is not throughput-bound anyway.
"""
import base64
import queue
import threading
import traceback

from . import protocol as P
from .backend import BackendError

__version__ = "0.0.1"


class _Job:
    __slots__ = ("request", "event", "result")

    def __init__(self, request):
        self.request = request
        self.event = threading.Event()
        self.result = None


class Engine:
    def __init__(self, backend, bus=None):
        self.backend = backend
        self.bus = bus  # optional EventBus: each op is published for the onscreen log
        self.bridge = None  # optional Bridge: set by the server for peer_* ops
        self._q: "queue.Queue[_Job]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="hv-engine",
                                        daemon=True)
        self._started = threading.Event()
        self._init_error = None

    def start(self):
        self._thread.start()
        self._started.wait()
        if self._init_error is not None:
            raise RuntimeError("backend init failed: %s" % self._init_error)

    def submit(self, request: dict) -> dict:
        """Enqueue a request and block until the worker returns its response."""
        job = _Job(request)
        self._q.put(job)
        job.event.wait()
        return job.result

    # --------------------------------------------------------------- worker
    def _run(self):
        try:
            self.backend.thread_init()
        except Exception as e:  # backend unusable — surface via start()
            self._init_error = "%s\n%s" % (e, traceback.format_exc())
            self._started.set()
            return
        self._started.set()
        while True:
            job = self._q.get()
            if job.request is None:  # shutdown sentinel
                job.event.set()
                return
            try:
                job.result = self._dispatch(job.request)
            except BackendError as e:
                job.result = {"ok": False, "error": str(e)}
            except Exception as e:
                job.result = {"ok": False,
                              "error": "%s: %s" % (type(e).__name__, e)}
            finally:
                job.event.set()
            if self.bus is not None:
                self._publish_op(job.request, job.result)

    def _publish_op(self, req: dict, res: dict) -> None:
        """Emit a compact event for the onscreen log — never the screenshot bytes."""
        op = req.get("op")
        # polled constantly / internal reads (the orchestrator watches via these) —
        # would drown the log, so keep them out of the onscreen stream.
        if op in (P.OP_PING, P.OP_LIST, P.OP_INSPECT, P.OP_OCR):
            return
        f = {"op": op, "ok": bool(res.get("ok"))}
        if req.get("target"):
            f["target"] = req["target"]
        if res.get("tier") is not None:
            f["tier"] = res["tier"]
        if res.get("detail"):
            f["detail"] = res["detail"]
        if op == P.OP_SHOT and res.get("ok"):
            f["detail"] = "%d bytes" % res.get("bytes", 0)
        if res.get("error"):
            f["error"] = res["error"]
        try:
            self.bus.publish("op", **f)
        except Exception:
            pass

    def _dispatch(self, req: dict) -> dict:
        op = req.get("op")
        b = self.backend

        if op == P.OP_PING:
            return {"ok": True, "backend": b.name, "version": __version__}

        if op == P.OP_LIST:
            return {"ok": True, "targets": [t.to_dict() for t in b.list_targets()]}

        if op == P.OP_SHOT:
            png = b.screenshot(req.get("target"))
            return {"ok": True, "bytes": len(png),
                    "png_b64": base64.b64encode(png).decode("ascii")}

        # For actions, the response IS the ActionResult dict: its ``ok`` reports
        # whether the action landed (RPC-level failures come back as exceptions).
        if op == P.OP_ACTIVATE:
            return b.activate(req["target"]).to_dict()

        if op == P.OP_TEXT:
            return b.text(req["target"], req.get("text", "")).to_dict()

        if op == P.OP_KEY:
            return b.key(req["target"], req.get("keys", ""),
                         focus=bool(req.get("focus", False))).to_dict()

        if op == P.OP_CLICK:
            return b.click(req["target"], int(req.get("x", 0)), int(req.get("y", 0)),
                           button=req.get("button", "left"),
                           double=bool(req.get("double", False))).to_dict()

        if op == P.OP_INSPECT:
            tree = b.inspect(req["target"], int(req.get("depth", 3)))
            return {"ok": True, "tree": tree.to_dict()}

        if op == P.OP_OCR:
            res = b.ocr(req["target"])
            res["ok"] = True
            res["text"] = "\n".join(x["text"] for x in res.get("boxes", []))
            return res

        if op == P.OP_SCREEN:
            w, h = b.screen_size()
            return {"ok": True, "w": w, "h": h}

        if op == P.OP_MOVE:
            topmost = req.get("topmost")  # tri-state: True/False/None
            zone = req.get("zone")
            if zone:  # resolve the named zone against the physical display size
                from .backend import zone_rect
                sw, sh = b.screen_size()
                x, y, w, h = zone_rect(zone, sw, sh)
            else:
                x, y, w, h = (int(req["x"]), int(req["y"]),
                              int(req["w"]), int(req["h"]))
            return b.move(req["target"], x, y, w, h, topmost).to_dict()

        if op == P.OP_STACK:
            return self._stack_above(b, req.get("top"), req.get("bottom"),
                                     int(req.get("gap", 8)))

        if op == P.OP_DOCK:
            return self._dock(b, req.get("target"))

        if op == P.OP_LAYOUT_LIST:
            from .layouts import load_layouts
            return {"ok": True, "layouts": [
                {"name": n, "description": l.get("description", ""),
                 "placements": len(l.get("placements", []))}
                for n, l in load_layouts().items()]}

        if op == P.OP_LAYOUT_APPLY:
            return self._apply_layout(b, req.get("name"))

        if op == P.OP_PEERS:
            if self.bridge is None:
                return {"ok": False, "error": "bridge not running"}
            return {"ok": True, "peers": self.bridge.peers(),
                    "self": self.bridge.identity()}

        if op == P.OP_PEER_SHOT:
            if self.bridge is None:
                return {"ok": False, "error": "bridge not running"}
            return self.bridge.request_shot(req.get("peer"), req.get("target"))

        if op == P.OP_LAUNCH:
            from .launch import resolve
            spec = resolve(req.get("name", ""))
            if not spec:
                return {"ok": False, "error": "no launcher/spec %r" % req.get("name")}
            before = {t.id for t in b.list_targets()}
            d = b.launch(spec).to_dict()
            d["spec"] = spec
            # defacto: if the just-launched window carries a standing dock rule
            # (e.g. Raves -> above Caves of Qud), highvisor stacks it on its own.
            dock = self._autodock_new(b, before)
            if dock is not None:
                d["dock"] = dock
            return d

        if op == P.OP_LAUNCH_LIST:
            from .launch import load_launchers
            return {"ok": True, "launchers": load_launchers()}

        if op == P.OP_LAUNCH_SAVE:
            from .launch import save_launcher
            path = save_launcher(req["name"], req["spec"])
            return {"ok": True, "saved": req["name"], "spec": req["spec"], "path": path}

        if op == P.OP_LAYOUT_SAVE:
            from .layouts import save_layout
            placements = []
            for t in b.list_targets():
                label = t.title or t.class_name
                if not label:
                    continue  # skip the untitled desktop/wallpaper layer
                # Absolute rects: an exact freeze of the current arrangement, which
                # round-trips faithfully across displays (incl. negative-origin
                # secondary monitors). Hand-authored layouts use zone/frac instead.
                placements.append({"match": label,
                                   "rect": [t.x, t.y, t.w, t.h]})
            path = save_layout(req["name"], {
                "description": req.get("description", "saved arrangement"),
                "placements": placements})
            return {"ok": True, "saved": req["name"], "path": path,
                    "windows": len(placements),
                    "detail": "%d windows -> %s" % (len(placements), req["name"])}

        return {"ok": False, "error": "unknown op: %r" % op}

    def _find_win(self, wins, label):
        """First window whose title/owner contains ``label`` (case-insensitive)."""
        m = (label or "").lower()
        for t in wins:
            if m and (m in (t.title or "").lower() or m in (t.class_name or "").lower()):
                return t
        return None

    def _stack_above(self, b, top_label, bottom_label, gap=8):
        """Move ``top`` into the anchor's column (matched x + width), directly above it."""
        if not top_label or not bottom_label:
            return {"ok": False, "error": "stack needs top and bottom"}
        wins = b.list_targets()
        top = self._find_win(wins, top_label)
        bot = self._find_win(wins, bottom_label)
        if bot is None:
            return {"ok": False, "error": "anchor %r not found" % bottom_label}
        if top is None:
            return {"ok": False, "error": "%r not found" % top_label}
        x, w, h = bot.x, bot.w, bot.h            # same column + size as the anchor
        y = bot.y - gap - h                      # stacked directly above it
        r = b.move(top.id, int(x), int(y), int(w), int(h), None)
        return {"ok": r.ok, "top": top.id, "bottom": bot.id,
                "rect": [int(x), int(y), int(w), int(h)], "error": r.error}

    def _dock(self, b, target):
        """Apply the standing dock rule for ``target`` (id or title substring)."""
        wins = b.list_targets()
        win = next((t for t in wins if t.id == target), None) or self._find_win(wins, target)
        if win is None:
            return {"ok": False, "error": "no window %r" % target}
        from .docks import rule_for
        label = win.title or win.class_name or ""
        rule = rule_for(label)
        if not (rule and rule.get("above")):
            return {"ok": False, "error": "no dock rule for %r" % label}
        return self._stack_above(b, label, rule["above"], int(rule.get("gap", 8)))

    def _autodock_new(self, b, before_ids, deadline_s=5.0):
        """Poll briefly for a newly-appeared window with a dock rule; apply it once.
        Bounded — returns the dock result as soon as the new window shows, or None."""
        import time
        from .docks import rule_for
        end = time.monotonic() + deadline_s
        while time.monotonic() < end:
            for t in b.list_targets():
                if t.id in before_ids:
                    continue
                label = t.title or t.class_name or ""
                rule = rule_for(label)
                if rule and rule.get("above"):
                    return self._stack_above(b, label, rule["above"], int(rule.get("gap", 8)))
            time.sleep(0.4)
        return None

    def _apply_layout(self, b, name):
        from .layouts import load_layouts, placement_rect
        lay = load_layouts().get(name)
        if not lay:
            return {"ok": False, "error": "no layout %r" % name}
        sw, sh = b.screen_size()
        wins = b.list_targets()
        used = set()
        results = []
        for pl in lay.get("placements", []):
            m = (pl.get("match") or "").lower()
            win = None
            for t in wins:
                if t.id in used:
                    continue
                if (not m or m in (t.title or "").lower()
                        or m in (t.class_name or "").lower()):
                    win = t
                    break
            if win is None:
                results.append({"match": pl.get("match"), "ok": False,
                                "error": "no matching window"})
                continue
            used.add(win.id)
            try:
                x, y, w, h = placement_rect(pl, sw, sh)
            except Exception as e:
                results.append({"match": pl.get("match"), "target": win.id,
                                "ok": False, "error": str(e)})
                continue
            r = b.move(win.id, x, y, w, h, pl.get("topmost"))
            results.append({"match": pl.get("match"), "target": win.id,
                            "title": win.title, "ok": r.ok,
                            "rect": [x, y, w, h], "error": r.error})
        applied = sum(1 for x in results if x["ok"])
        return {"ok": applied > 0, "applied": applied, "results": results,
                "detail": "%s: %d/%d placed" % (name, applied, len(results))}

    def stop(self):
        job = _Job(None)
        self._q.put(job)
        job.event.wait(timeout=2.0)
