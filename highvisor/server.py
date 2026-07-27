"""server — the localhost TCP daemon.

Binds 127.0.0.1:PORT (localhost only, never public), speaks the framed-JSON
protocol, and forwards every request to the single-threaded :class:`Engine`.
One thread per connection; the engine serializes the actual work, so the threads
only do socket I/O and block on ``engine.submit``.

Run it with ``python -m highvisor.server`` (or the ``hvd`` console script).
"""
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


def serve_forever(host=P.HOST, port=P.PORT, backend=None):
    backend = backend or make_backend()
    engine = Engine(backend)
    engine.start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)
    print("highvisor daemon: backend=%s listening on %s:%d"
          % (backend.name, host, port), flush=True)
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
