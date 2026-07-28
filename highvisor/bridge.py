"""bridge — the cross-machine data channel between highvisor instances on a LAN.

This is deliberately NOT the control port. The TCP daemon (48720) and web cockpit
(48721) stay bound to localhost; only this bridge (48722) faces the LAN, and it
carries *data*, never remote control of your apps:

  - context   handoff text/blobs between sessions (the automated copy/paste)
  - log       mirror this machine's op log onto the peer's onscreen log
  - shot_req  a peer may request a screenshot of one of your windows (opt-in,
              token-gated) and gets shot_resp back on the same connection

Discovery is zero-config: each instance advertises ``_highvisor._tcp.local.`` over
mDNS and browses for the others, so the Mac and PC find each other automatically
on the same network. Every message must carry the shared token (``~/.config/
highvisor/token``, or $HIGHVISOR_TOKEN) or it is refused — so only *your* machines
talk. Files are intentionally out of scope: those go through git.

Wire format is the same framed-JSON as the rest of highvisor (protocol.py): one
request frame in, one reply frame out, connection closed.
"""
import os
import secrets
import socket
import threading

from . import protocol as P

_SERVICE = "_highvisor._tcp.local."


# ------------------------------------------------------------------ helpers
def _lan_ip() -> str:
    """Best-effort primary LAN IPv4 (no packets sent — just picks the route)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _token_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "highvisor", "token")


def load_or_make_token() -> str:
    """The shared pairing secret. $HIGHVISOR_TOKEN wins; else read/create the file.
    Copy the value to your other machine (git is fine) to pair them."""
    env = os.environ.get("HIGHVISOR_TOKEN")
    if env:
        return env.strip()
    path = _token_path()
    try:
        with open(path) as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    tok = secrets.token_hex(16)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(tok + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return tok


class Bridge:
    def __init__(self, engine, bus, name=None, host=None,
                 port=P.BRIDGE_PORT, token=None):
        self.engine = engine
        self.bus = bus
        self.name = name or socket.gethostname().split(".")[0]
        self.host = host or _lan_ip()
        self.port = port
        self.token = token or load_or_make_token()
        self._peers = {}                       # key "host:port" -> {name,host,port}
        self._pending = {}                     # req_id -> [Event, result]
        self._lock = threading.Lock()
        self._zc = None
        self._info = None
        self._srv = None

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("0.0.0.0", self.port))   # LAN-facing (token-gated below)
        self._srv.listen(16)
        threading.Thread(target=self._accept_loop, name="hv-bridge-acc",
                         daemon=True).start()
        threading.Thread(target=self._mirror_loop, name="hv-bridge-mirror",
                         daemon=True).start()
        self._register_zeroconf()
        self.bus.publish("peer", event="bridge up", name=self.name,
                         host="%s:%d" % (self.host, self.port))

    def _register_zeroconf(self):
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except Exception as e:      # bridge still works for explicit peers
            self.bus.publish("peer", event="mDNS unavailable", name=str(e), host="")
            return
        self._zc = Zeroconf()
        self._info = ServiceInfo(
            _SERVICE, "%s.%s" % (self.name, _SERVICE),
            addresses=[socket.inet_aton(self.host)], port=self.port,
            properties={"name": self.name})
        self._zc.register_service(self._info)
        from zeroconf import ServiceBrowser
        ServiceBrowser(self._zc, _SERVICE, handlers=[self._on_service])

    def _on_service(self, zeroconf, service_type, name, state_change):
        from zeroconf import ServiceStateChange
        if state_change is ServiceStateChange.Removed:
            info = None
        else:
            info = zeroconf.get_service_info(service_type, name)
        short = name.split(".")[0]
        if state_change is ServiceStateChange.Removed:
            with self._lock:
                gone = [k for k, v in self._peers.items() if v["name"] == short]
                for k in gone:
                    del self._peers[k]
            if gone:
                self.bus.publish("peer", event="left", name=short, host="")
            return
        if not info or not info.addresses:
            return
        host = socket.inet_ntoa(info.addresses[0])
        port = info.port
        if host == self.host and port == self.port:
            return                                    # that's us
        key = "%s:%d" % (host, port)
        with self._lock:
            new = key not in self._peers
            self._peers[key] = {"name": short, "host": host, "port": port}
        if new:
            self.bus.publish("peer", event="joined", name=short,
                             host="%s:%d" % (host, port))

    # --------------------------------------------------------------- inbound
    def _accept_loop(self):
        while True:
            try:
                conn, addr = self._srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            try:
                msg = P.recv_frame(conn)
            except Exception:
                return
            if not isinstance(msg, dict):
                return
            reply = self._handle(msg)
            try:
                P.send_frame(conn, reply)
            except Exception:
                pass

    def _handle(self, msg: dict) -> dict:
        if not secrets.compare_digest(str(msg.get("token", "")), self.token):
            return {"ok": False, "error": "bad token"}
        typ = msg.get("type")
        frm = msg.get("from", "peer")
        if typ == "context":
            self.bus.publish("context", **{"from": frm, "text": msg.get("text", "")})
            return {"ok": True}
        if typ == "log":
            ev = msg.get("event", {})
            self.bus.publish("log", **{"from": frm, "msg": _one_line(ev)})
            return {"ok": True}
        if typ == "shot_req":
            res = self.engine.submit({"op": P.OP_SHOT, "target": msg.get("target")})
            return {"ok": bool(res.get("ok")), "png_b64": res.get("png_b64"),
                    "error": res.get("error"), "bytes": res.get("bytes")}
        if typ == "ping":
            return {"ok": True, "name": self.name}
        return {"ok": False, "error": "unknown type %r" % typ}

    # -------------------------------------------------------------- outbound
    def identity(self):
        return {"name": self.name, "host": self.host, "port": self.port}

    def peers(self):
        with self._lock:
            return list(self._peers.values())

    def _send_to(self, peer, msg, timeout=10):
        msg = dict(msg, token=self.token, **{"from": self.name})
        with socket.create_connection((peer["host"], peer["port"]), timeout=timeout) as s:
            P.send_frame(s, msg)
            return P.recv_frame(s)

    def send_context(self, text, to=None):
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        targets = [p for p in self.peers() if to in (None, p["name"])]
        if not targets:
            return {"ok": False, "error": "no peers"}
        sent = 0
        for p in targets:
            try:
                r = self._send_to(p, {"type": "context", "text": text})
                if r and r.get("ok"):
                    sent += 1
            except Exception:
                pass
        # echo into our own log so the sender sees what went out
        self.bus.publish("context", **{"from": "→ %d peer(s)" % sent, "text": text})
        return {"ok": sent > 0, "sent": sent}

    def request_shot(self, peer_name, target):
        peer = next((p for p in self.peers() if p["name"] == peer_name), None)
        if peer is None:
            return {"ok": False, "error": "no such peer %r" % peer_name}
        try:
            r = self._send_to(peer, {"type": "shot_req", "target": target}, timeout=20)
        except Exception as e:
            return {"ok": False, "error": "peer unreachable: %s" % e}
        return r or {"ok": False, "error": "no reply"}

    # ------------------------------------------------------------ log mirror
    def _mirror_loop(self):
        """Forward this machine's own op/boot events to peers as ``log`` messages
        so they interleave in the peer's onscreen log. Received log/context/peer
        events are never re-forwarded, so there is no echo loop."""
        q, _hist = self.bus.subscribe()          # live only; skip history
        while True:
            ev = q.get()
            if ev.get("kind") not in ("op", "boot"):
                continue
            for p in self.peers():
                try:
                    self._send_to(p, {"type": "log", "event": ev}, timeout=5)
                except Exception:
                    pass


def _one_line(ev: dict) -> str:
    if ev.get("kind") == "op":
        s = "%s %s" % (ev.get("op"), "ok" if ev.get("ok") else "fail")
        if ev.get("target"):
            s += " " + str(ev["target"])
        if ev.get("detail"):
            s += " " + str(ev["detail"])
        return s
    return ev.get("msg") or ev.get("kind", "?")
