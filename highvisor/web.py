"""web — a localhost HTTP face for the daemon: a static single-page cockpit plus
the two endpoints that bridge a browser to the engine and the event bus.

    GET  /            the SPA (served from highvisor/webui/)
    GET  /<asset>     static app.js / style.css / etc.
    POST /rpc         body = one RPC request dict -> engine reply (JSON)
    GET  /events      Server-Sent Events: the live log / event stream

No websockets, no framework, no build step — just http.server + SSE — so the web
client never touches the Godot toolchain. Localhost only: same trust boundary as
the TCP control port (cross-machine data rides the separate bridge, not this).
"""
import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import protocol as P

WEB_PORT = 48721
_WEBUI = os.path.join(os.path.dirname(__file__), "webui")
_CTYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
           ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
           ".ico": "image/x-icon"}


def make_web_server(engine, bus, bridge=None, orchestrator=None,
                    host=P.HOST, port=WEB_PORT):
    """Build (but don't start) the ThreadingHTTPServer. ``bridge`` exposes /bridge/*
    (peer discovery + context handoff); ``orchestrator`` exposes /orch/* (the gated
    agent-loop pending queue)."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass  # keep the daemon's stdout for real events, not access logs

        def _send(self, code, body, ctype="application/json"):
            if isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        # ------------------------------------------------------------- GET
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/events":
                return self._sse()
            if path in ("/", ""):
                path = "/index.html"
            if path == "/bridge/peers":
                peers = bridge.peers() if bridge else []
                return self._send(200, json.dumps({"ok": True, "peers": peers,
                                                    "self": bridge.identity() if bridge else None}))
            if path == "/orch/pending":
                if orchestrator is None:
                    return self._send(200, json.dumps({"ok": True, "pending": [], "lanes": []}))
                return self._send(200, json.dumps({
                    "ok": True, "pending": orchestrator.pending_list(),
                    "lanes": sorted(orchestrator.auto_lanes)}))
            fp = os.path.normpath(os.path.join(_WEBUI, path.lstrip("/")))
            if not fp.startswith(_WEBUI) or not os.path.isfile(fp):
                return self._send(404, "not found", "text/plain")
            with open(fp, "rb") as f:
                data = f.read()
            ext = os.path.splitext(fp)[1]
            return self._send(200, data, _CTYPES.get(ext, "application/octet-stream"))

        # ------------------------------------------------------------ POST
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                req = json.loads(raw or b"{}")
            except Exception as e:
                return self._send(400, json.dumps({"ok": False, "error": "bad json: %s" % e}))
            if self.path == "/rpc":
                return self._send(200, json.dumps(engine.submit(req)))
            if self.path == "/bridge/send" and bridge is not None:
                return self._send(200, json.dumps(bridge.send_context(
                    req.get("text", ""), to=req.get("to"))))
            if self.path == "/bridge/shot" and bridge is not None:
                return self._send(200, json.dumps(bridge.request_shot(
                    req.get("peer"), req.get("target"))))
            if self.path == "/orch/act" and orchestrator is not None:
                return self._send(200, json.dumps(orchestrator.act(
                    req.get("fp"), req.get("action"))))
            if self.path == "/pick":
                # A human clicked a choice button the cockpit rendered from an ask's `(x)` options.
                # 1) Record it on the bus (visible in the log + SSE) so the decision is captured.
                bus.publish("pick", q=str(req.get("q", "")), opt=str(req.get("opt", "")),
                            label=str(req.get("label", "")), fp=req.get("fp"))
                # 2) Paste the running pick summary into the ASKER's composer (the ask's source),
                #    WITHOUT submitting — the human presses enter to send. Closes the manual-relay gap.
                pasted = None
                if orchestrator is not None:
                    agent = (str(req.get("src", "") or "").split("/")[-1]) or "claude"
                    summary = str(req.get("summary", "")) or ("%s=(%s)" % (req.get("q", ""), req.get("opt", "")))
                    try:
                        # focus=False: tier-1 AXSetValue is focus-free, so clicking a pick in the
                        # cockpit doesn't bounce focus to the agent window.
                        res = orchestrator.deliver(agent, summary, submit=False, focus=False)
                        if not res.get("ok") and agent != "claude":     # fall back to the primary asker
                            res = orchestrator.deliver("claude", summary, submit=False, focus=False)
                        pasted = res.get("ok")
                    except Exception as e:
                        bus.publish("orch", msg="pick paste failed: %s" % e)
                return self._send(200, json.dumps({"ok": True, "pasted": pasted}))
            if self.path == "/pick_submit":
                # Debounced: the cockpit calls this ~1.8s after the last pick to SEND the pasted
                # summary (press Return in the asker's composer). Separate from /pick so multi-question
                # asks accumulate all picks before one submit.
                submitted = None
                if orchestrator is not None:
                    agent = (str(req.get("src", "") or "").split("/")[-1]) or "claude"
                    try:
                        res = orchestrator.press_submit(agent)
                        if not res.get("ok") and agent != "claude":
                            res = orchestrator.press_submit("claude")
                        submitted = res.get("ok")
                    except Exception as e:
                        bus.publish("orch", msg="pick submit failed: %s" % e)
                    # The ask was answered directly (picks sent to the asker), so REMOVE it from pending —
                    # it should not linger or be forwardable to its target. `deny` drops it without delivering.
                    fp = req.get("fp")
                    if fp:
                        try:
                            orchestrator.act(fp, "deny")
                        except Exception:
                            pass
                return self._send(200, json.dumps({"ok": True, "submitted": submitted}))
            if self.path == "/shutdown":
                # localhost-only cockpit -> local off switch. Reply first, then exit
                # after a beat so the response reaches the browser.
                bus.publish("boot", msg="shutdown requested from cockpit")
                self._send(200, json.dumps({"ok": True, "msg": "highvisor shutting down"}))
                threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)),
                                 daemon=True).start()
                return
            return self._send(404, "not found", "text/plain")

        # -------------------------------------------------------------- SSE
        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q, hist = bus.subscribe()
            try:
                for ev in hist:
                    self._emit(ev)
                while True:
                    try:
                        ev = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")  # comment frame
                        self.wfile.flush()
                        continue
                    self._emit(ev)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                bus.unsubscribe(q)

        def _emit(self, ev):
            self.wfile.write(("data: %s\n\n" % json.dumps(ev)).encode("utf-8"))
            self.wfile.flush()

    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv
