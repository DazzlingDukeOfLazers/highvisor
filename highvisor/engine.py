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


def _png_dims(png: bytes):
    """(width, height) from a PNG's IHDR, or None. Cheap header read — avoids a
    full decode just to report the capture's pixel size."""
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and png[12:16] == b"IHDR":
        return (int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big"))
    return None


def _slim_state(st):
    """A gamestate entry reduced to what an assert/goto caller needs to read."""
    if not st:
        return None
    return {"node": st.get("node"), "label": st.get("label"), "off": st.get("off"),
            "path": st.get("path"), "via": st.get("via"),
            "scene": (st.get("signals") or {}).get("scene"),
            "extra": st.get("extra")}


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
            png = b.screenshot(req.get("target"), native=bool(req.get("native")))
            resp = {"ok": True, "bytes": len(png),
                    "png_b64": base64.b64encode(png).decode("ascii")}
            dims = _png_dims(png)
            if dims:
                resp["w"], resp["h"] = dims
            return resp

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
            kw = {"button": req.get("button", "left"),
                  "double": bool(req.get("double", False))}
            if req.get("hover"):   # only forward when asked — backends without the arg won't see it
                kw["hover"] = True
            return b.click(req["target"], int(req.get("x", 0)), int(req.get("y", 0)),
                           **kw).to_dict()

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

        if op == P.OP_DISPLAYS:
            return {"ok": True, "displays": b.displays()}

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

        if op == P.OP_PROBE:
            return self._probe(b, req.get("app"), req.get("window"), req.get("port"))

        if op == P.OP_GAMETREE:
            from . import gametree
            return {"ok": True, "tree": gametree.load_tree(force=bool(req.get("reload")))}

        if op == P.OP_GAMESTATE:
            return self._gamestate(b, ocr=bool(req.get("ocr", False)))

        if op == P.OP_GAMEGO:
            return self._gamego(b, req.get("app"), req.get("node"))

        if op == P.OP_ASSERT:
            return self._assert_state(b, req)

        if op == P.OP_WRITE_TEXT:
            return self._write_text(req.get("path"), req.get("content", ""))

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
            from .launch import resolve_launch
            spec, largs = resolve_launch(req.get("name", ""))
            if not spec:
                return {"ok": False, "error": "no launcher/spec %r" % req.get("name")}
            before = {t.id for t in b.list_targets()}
            d = b.launch(spec, largs).to_dict()
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

    def _probe(self, b, app=None, window=None, port=None):
        """Report whether an app is up and in what state, from its window + a
        state-indicating localhost port. Takes an app profile name (see apps.py)
        or an explicit window label (+ optional port)."""
        prof = {}
        if app:
            from .apps import PROFILES
            prof = PROFILES.get(app)
            if prof is None:
                return {"ok": False, "error": "no app profile %r" % app}
            window = window or prof.get("window")
            if port is None:
                port = prof.get("port")
        win = self._find_win(b.list_targets(), window) if window else None
        port_open = False
        if port:
            import socket
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=0.5):
                    port_open = True
            except OSError:
                port_open = False
        if win is None:
            state = prof.get("off_state", "off")
        elif port_open:
            state = prof.get("port_state", "up")
        else:
            state = prof.get("window_state", "up")
        return {"ok": True, "app": app, "running": win is not None, "state": state,
                "port": port, "port_open": port_open,
                "window": win.to_dict() if win else None}

    def _write_text(self, path, content):
        """Write a small text/JSON config file. Restricted to under $HOME so the cockpit's live
        tuning tools (e.g. the title-bg nudge) can write app config a game hot-reloads, without a
        general filesystem-write capability."""
        import os
        if not path:
            return {"ok": False, "error": "no path"}
        home = os.path.realpath(os.path.expanduser("~"))
        full = os.path.realpath(os.path.expanduser(str(path)))
        if full != home and not full.startswith(home + os.sep):
            return {"ok": False, "error": "path must be under $HOME"}
        try:
            d = os.path.dirname(full)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(full, "w", encoding="utf-8") as fh:
                fh.write(str(content))
            return {"ok": True, "path": full, "bytes": len(str(content))}
        except OSError as e:
            return {"ok": False, "error": str(e)}

    def _gamestate(self, b, ocr=False):
        """Evaluate the game state-machine tree against live signals for each app.

        Cheap by default — window presence + Qud's 48710 bridge port. Pass ocr=True to
        also OCR each present window so menu-side screens (title / load / chargen) can be
        told apart; that path is heavier, so the cockpit polls it on a slower cadence.
        See gametree.py for the matching rules."""
        from . import gametree
        import socket
        tree = gametree.load_tree()
        wins = b.list_targets()
        states = {}
        for app, cfg in gametree.apps(tree).items():
            win = self._find_win(wins, cfg.get("window"))
            signals = {"present": win is not None, "port_open": None,
                       "game_live": None, "ocr_text": None,
                       "scene": self._read_scene(cfg.get("state_file"))}
            port = cfg.get("port")
            if port:
                try:
                    with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4) as s:
                        signals["port_open"] = True
                        # The mod's bridge listener is open even at Qud's MAIN MENU (it starts at
                        # load), so port-open alone can't tell menu from in-game. But the mod
                        # force-publishes a snapshot to every client on connect ONLY when a game is
                        # actually live (the server is multi-client, so this is harmless to Raves's
                        # own connection). So a brief read is the true liveness signal: bytes -> a
                        # game is running; silence -> a menu screen. Mirrors MainMenu's own probe.
                        s.settimeout(0.35)
                        try:
                            signals["game_live"] = len(s.recv(1)) > 0
                        except (socket.timeout, OSError):
                            signals["game_live"] = False
                except OSError:
                    signals["port_open"] = False
                    signals["game_live"] = False
            if ocr and win is not None:
                try:
                    res = b.ocr(win.id)
                    signals["ocr_text"] = "\n".join(x.get("text", "") for x in res.get("boxes", []))
                except Exception:
                    signals["ocr_text"] = None
            st = gametree.evaluate(tree, app, signals)
            st["window"] = win.to_dict() if win else None
            st["signals"] = {"present": signals["present"], "port_open": signals["port_open"],
                             "game_live": signals["game_live"],
                             "scene": signals["scene"],
                             "ocr_used": signals["ocr_text"] is not None}
            st["extra"] = self._read_state_extra(cfg.get("state_file"))
            states[app] = st
        return {"ok": True, "ocr": bool(ocr), "states": states}

    # App-authored state files — first-party scene reports (the Qud mod's qud_state.json,
    # Raves' raves_state.json). Far more accurate than OCR and cheap enough for every poll.
    # A report is trusted only while FRESH (mtime within STATE_FILE_TTL): a crashed app's
    # last write must not pin the tree to a stale screen.
    STATE_FILE_TTL = 6.0

    def _read_state_file(self, path):
        """Parsed JSON dict of a fresh state file, else None."""
        import json as _json
        import os as _os
        import time as _time
        if not path:
            return None
        p = _os.path.expanduser(path)
        try:
            if _time.time() - _os.path.getmtime(p) > self.STATE_FILE_TTL:
                return None
            with open(p, "r", encoding="utf-8") as fh:
                d = _json.load(fh)
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def _read_scene(self, path):
        d = self._read_state_file(path)
        return d.get("scene") if d else None

    def _read_state_extra(self, path):
        """The full fresh report minus the scene key (mode, popup, zone, …) for the UI."""
        d = self._read_state_file(path)
        if not d:
            return None
        return {k: v for k, v in d.items() if k != "scene"}

    # ----------------------------------------------------------- assert (TDD)
    def _assert_state(self, b, req):
        """Poll the live state until the requested condition holds, or time out.

        The TDD primitive for state-dependent work: ``hv assert --app qud --node in_game
        --timeout 20`` blocks until Qud reports in-game (exit 0) or dumps the actual
        state (exit 1). Conditions (all supplied must hold):
          app + node:     the app's current node == node, or node is on its path
          scene:          the app's self-reported scene equals this
          popup:          true = any popup up (state-file ``popup`` key), or a popup type
          present:        window presence equals this bool
          ocr_contains:   the app window's OCR contains this substring (heavy — forces OCR)
        ``ok`` = the op ran; ``passed`` = the assertion's verdict."""
        import time
        app = req.get("app")
        want = {k: req[k] for k in ("node", "scene", "popup", "present", "ocr_contains")
                if k in req and req[k] is not None}
        if not app or not want:
            return {"ok": False, "error": "assert needs app and at least one condition"}
        timeout = float(req.get("timeout", 10.0))
        interval = max(0.2, float(req.get("interval", 0.8)))
        need_ocr = "ocr_contains" in want
        t0 = time.monotonic()
        actual = None
        while True:
            st = self._gamestate(b, ocr=need_ocr).get("states", {}).get(app)
            actual = st
            if st is not None and self._assert_holds(want, st):
                return {"ok": True, "passed": True, "app": app, "want": want,
                        "elapsed": round(time.monotonic() - t0, 2), "actual": _slim_state(st)}
            if time.monotonic() - t0 >= timeout:
                return {"ok": True, "passed": False, "app": app, "want": want,
                        "elapsed": round(time.monotonic() - t0, 2), "actual": _slim_state(st),
                        "error": "assert timed out"}
            time.sleep(interval)

    def _assert_holds(self, want, st):
        if "present" in want:
            if bool(st.get("signals", {}).get("present")) != bool(want["present"]):
                return False
        if "node" in want:
            node = want["node"]
            if st.get("node") != node and node not in (st.get("path") or []):
                return False
        if "scene" in want:
            if (st.get("signals", {}).get("scene") or "") != want["scene"]:
                return False
        if "popup" in want:
            popup = (st.get("extra") or {}).get("popup")
            if want["popup"] is True:
                if not popup:
                    return False
            elif str(popup or "") != str(want["popup"]):
                return False
        if "ocr_contains" in want:
            # _gamestate stored no raw text; re-derive from the evaluate input is overkill —
            # OCR the window directly (need_ocr already made the poll heavy anyway).
            win = st.get("window")
            if not win:
                return False
            try:
                res = self.backend.ocr(win["id"])
                text = "\n".join(x.get("text", "") for x in res.get("boxes", [])).lower()
            except Exception:
                return False
            if str(want["ocr_contains"]).lower() not in text:
                return False
        return True

    # ------------------------------------------------------- gamego (drive-to-state)
    def _gamego(self, b, app, node_id, _depth=0):
        """Drive ``app`` to tree state ``node_id`` via the node's goto[app] recipe.

        Idempotent: if the app is already at (or inside) the target node, no steps run.
        Steps (executed in order; the first failure stops the run):
          {"goto": node}                  run that node's recipe first (recursion, depth-capped)
          {"launch": name, "unless_running": bool}   launch unless the app's window is up
          {"wait_window": label, "timeout": s}       poll for the window
          {"activate": label}             front the window
          {"click_hover": [x,y], "window": label}    hover-click (menus need the hover)
          {"click": [x,y], "window": label}          plain click
          {"key": keys, "window": label}  focused key injection
          {"sleep": s}                    settle pause
          {"assert": {...}, "timeout": s} inline _assert_state (app defaults to this app)
        """
        import time
        from . import gametree
        if _depth > 4:
            return {"ok": False, "error": "goto recursion too deep (recipe cycle?)"}
        tree = gametree.load_tree()
        if app not in gametree.apps(tree):
            return {"ok": False, "error": "unknown app %r" % app}
        node = gametree.find_node(tree, node_id)
        if node is None:
            return {"ok": False, "error": "no tree node %r" % node_id}
        recipe = (node.get("goto") or {}).get(app)
        if not recipe:
            return {"ok": False, "error": "node %r has no goto recipe for %s" % (node_id, app)}
        # already there? (node current or an ancestor of the current node)
        st = self._gamestate(b).get("states", {}).get(app) or {}
        if st.get("node") == node_id or node_id in (st.get("path") or []):
            return {"ok": True, "app": app, "node": node_id, "steps": [],
                    "detail": "already at %s" % node_id, "state": _slim_state(st)}
        steps = []

        def fail(step, why):
            steps.append({"step": step, "ok": False, "error": why})
            return {"ok": False, "app": app, "node": node_id, "steps": steps, "error": why}

        for step in recipe:
            self.bus.publish("gamego", app=app, node=node_id, step=step)
            if "goto" in step:
                r = self._gamego(b, app, step["goto"], _depth + 1)
                steps.append({"step": step, "ok": r.get("ok"), "detail": r.get("detail", "")})
                if not r.get("ok"):
                    return fail(step, r.get("error", "goto failed"))
            elif "launch" in step:
                cfg = gametree.apps(tree).get(app, {})
                if step.get("unless_running") and self._find_win(b.list_targets(), cfg.get("window")):
                    steps.append({"step": step, "ok": True, "detail": "already running"})
                    continue
                from .launch import resolve_launch
                spec, largs = resolve_launch(step["launch"])
                if not spec:
                    return fail(step, "no launcher %r" % step["launch"])
                r = b.launch(spec, largs)
                steps.append({"step": step, "ok": r.ok, "detail": r.detail})
                if not r.ok:
                    return fail(step, r.error or "launch failed")
            elif "wait_window" in step:
                deadline = time.monotonic() + float(step.get("timeout", 30))
                while self._find_win(b.list_targets(), step["wait_window"]) is None:
                    if time.monotonic() > deadline:
                        return fail(step, "window %r never appeared" % step["wait_window"])
                    time.sleep(1.0)
                steps.append({"step": step, "ok": True})
            elif "activate" in step:
                win = self._find_win(b.list_targets(), step["activate"])
                if win is None:
                    return fail(step, "no window %r" % step["activate"])
                r = b.activate(win.id)
                steps.append({"step": step, "ok": r.ok})
                time.sleep(0.6)
            elif "click_hover" in step or "click" in step:
                key = "click_hover" if "click_hover" in step else "click"
                win = self._find_win(b.list_targets(), step.get("window", ""))
                if win is None:
                    return fail(step, "no window %r" % step.get("window"))
                x, y = step[key]
                kw = {"hover": True} if key == "click_hover" else {}
                r = b.click(win.id, int(x), int(y), **kw)
                steps.append({"step": step, "ok": r.ok})
                if not r.ok:
                    return fail(step, r.error or "click failed")
                time.sleep(0.5)
            elif "key" in step:
                win = self._find_win(b.list_targets(), step.get("window", ""))
                if win is None:
                    return fail(step, "no window %r" % step.get("window"))
                r = b.key(win.id, step["key"], focus=True)
                steps.append({"step": step, "ok": r.ok})
                time.sleep(0.4)
            elif "sleep" in step:
                time.sleep(float(step["sleep"]))
                steps.append({"step": step, "ok": True})
            elif "dismiss" in step:
                # Conditional dismissal: if the app currently reports this scene (e.g. a quit
                # dialog left open by a stray Escape), press the key to clear it; otherwise
                # no-op. Keeps recipes self-healing without a full conditional language.
                cond = step["dismiss"]
                cur = (self._gamestate(b).get("states", {}).get(app) or {})
                scene = (cur.get("signals") or {}).get("scene") or ""
                want_scene = cond.get("scene", "")
                if str(scene).lower() == str(want_scene).lower():
                    cfg = gametree.apps(tree).get(app, {})
                    win = self._find_win(b.list_targets(), cfg.get("window"))
                    if win is not None:
                        b.key(win.id, cond.get("key", "Escape"), focus=True)
                        time.sleep(0.6)
                    steps.append({"step": step, "ok": True, "detail": "dismissed %s" % scene})
                else:
                    steps.append({"step": step, "ok": True, "detail": "not present"})
            elif "assert" in step:
                a = dict(step["assert"])
                a.setdefault("app", app)
                a["timeout"] = step.get("timeout", a.get("timeout", 15))
                r = self._assert_state(b, a)
                steps.append({"step": step, "ok": bool(r.get("passed")),
                              "actual": r.get("actual")})
                if not r.get("passed"):
                    return fail(step, "assert failed: wanted %s, got %s"
                                % (a, (r.get("actual") or {}).get("label")))
            else:
                return fail(step, "unknown step %r" % step)
        st = self._gamestate(b).get("states", {}).get(app)
        return {"ok": True, "app": app, "node": node_id, "steps": steps,
                "state": _slim_state(st)}

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
