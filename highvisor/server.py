"""server — the localhost TCP daemon.

Binds 127.0.0.1:PORT (localhost only, never public), speaks the framed-JSON
protocol, and forwards every request to the single-threaded :class:`Engine`.
One thread per connection; the engine serializes the actual work, so the threads
only do socket I/O and block on ``engine.submit``.

Run it with ``python -m highvisor.server`` (or the ``hvd`` console script).
"""
import os
import socket
import threading

from . import protocol as P
from .backends import make_backend
from .engine import Engine


def _serve_conn(conn, addr, engine):
    with conn:
        while True:
            try:
                req = P.recv_frame(conn)
            except Exception:
                break
            if req is None:  # clean EOF
                break
            if not isinstance(req, dict):
                P.send_frame(conn, {"ok": False, "error": "request must be a JSON object"})
                continue
            resp = engine.submit(req)
            try:
                P.send_frame(conn, resp)
            except Exception:
                break


def serve_forever(host=P.HOST, port=P.PORT, backend=None, web=True):
    from .events import EventBus
    backend = backend or make_backend()
    bus = EventBus()
    engine = Engine(backend, bus=bus)
    engine.start()

    bridge = None
    # The plaintext LAN bridge is OFF by default (fail-closed) — cross-machine goes
    # over SSH (docs/07). Opt in explicitly with HIGHVISOR_BRIDGE=1 for a same-LAN peer.
    if os.environ.get("HIGHVISOR_BRIDGE", "0") == "1":
        try:
            from .bridge import Bridge
            bridge = Bridge(engine, bus)
            engine.bridge = bridge   # enable peer_* ops over the TCP protocol
            bridge.start()
            print("highvisor bridge: LAN peer channel on %s:%d (token-gated)"
                  % (bridge.host, bridge.port), flush=True)
        except Exception as e:
            print("highvisor bridge: disabled (%s)" % e, flush=True)
            bridge = None

    orchestrator = None
    if os.environ.get("HIGHVISOR_ORCH", "1") != "0":
        try:
            from .orchestrator import Orchestrator, Source
            orchestrator = Orchestrator(engine, bus, [
                Source("mac/claude", "Claude", "ax"),
                Source("mac/chatgpt", "ChatGPT", "ocr"),
            ])
            orchestrator.start(interval=3.0)
            print("highvisor orchestrator: watching Claude(AX)+ChatGPT(OCR), GATED",
                  flush=True)
        except Exception as e:
            print("highvisor orchestrator: disabled (%s)" % e, flush=True)
            orchestrator = None

    if web:
        from .web import make_web_server, WEB_PORT
        web_srv = make_web_server(engine, bus, bridge=bridge,
                                  orchestrator=orchestrator, host=host)
        threading.Thread(target=web_srv.serve_forever, name="hv-web",
                         daemon=True).start()
        print("highvisor web cockpit: http://%s:%d" % (host, WEB_PORT), flush=True)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    print("highvisor daemon: backend=%s listening on %s:%d"
          % (backend.name, host, port), flush=True)
    bus.publish("boot", backend=backend.name, msg="daemon up on %d" % port)
    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(target=_serve_conn, args=(conn, addr, engine),
                                 name="hv-conn", daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("shutting down", flush=True)
    finally:
        srv.close()
        engine.stop()


def main():
    serve_forever()


if __name__ == "__main__":
    main()
