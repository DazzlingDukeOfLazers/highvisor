"""server — the localhost TCP daemon.

Binds 127.0.0.1:PORT (localhost only, never public), speaks the framed-JSON
protocol, and forwards every request to the single-threaded :class:`Engine`.
One thread per connection; the engine serializes the actual work, so the threads
only do socket I/O and block on ``engine.submit``.

Run it with ``python -m highvisor.server`` (or the ``hvd`` console script).
"""
import datetime
import faulthandler
import os
import signal
import socket
import sys
import threading
import traceback

from . import protocol as P
from .backends import make_backend
from .engine import Engine

CRASH_LOG = os.path.expanduser("~/.highvisor/daemon.log")


def _install_crash_guards():
    """Make the daemon survivable and DIAGNOSABLE. Every Python socket path is already guarded, so a
    full process death is almost certainly a NATIVE crash in the macOS backend (pyobjc / Quartz /
    ScreenCaptureKit) — which no try/except can catch. So: ignore SIGPIPE; enable faulthandler to dump
    a C-level traceback to the crash log on a fatal signal; and log any uncaught Python exception
    (main or worker thread) there too. Returns the open log file (kept alive for faulthandler)."""
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)   # writing to a dead socket → EPIPE, not death
    except (ValueError, OSError, AttributeError):
        pass
    fp = None
    try:
        os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
        fp = open(CRASH_LOG, "a", buffering=1)
        fp.write("\n=== daemon start %s (pid %d) ===\n"
                 % (datetime.datetime.now().isoformat(timespec="seconds"), os.getpid()))
        faulthandler.enable(file=fp, all_threads=True)   # native crashes → traceback in the log
    except Exception:
        fp = None

    def _log(kind, exc_type, exc, tb):
        line = "\n[%s] UNCAUGHT %s\n%s\n" % (
            datetime.datetime.now().isoformat(timespec="seconds"), kind,
            "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            if fp is not None:
                fp.write(line)
        except Exception:
            pass
        try:
            sys.stderr.write(line)
        except Exception:
            pass

    sys.excepthook = lambda et, e, tb: _log("main thread", et, e, tb)
    threading.excepthook = lambda a: _log("thread:%s" % a.thread.name,
                                          a.exc_type, a.exc_value, a.exc_traceback)
    return fp


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


def _write_version_file():
    """Publish the daemon's version where Raves can read it (the reverse of Raves'
    state files): the 1:1 title version corner shows raves + hv versions."""
    try:
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        m = re.search(r'^version = "([^"]+)"', open(os.path.join(root, "pyproject.toml")).read(), re.M)
        v = m.group(1) if m else "?"
        p = os.path.expanduser("~/Library/Application Support/RavesOfQud")
        os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, "hv_version.txt"), "w") as f:
            f.write(v)
    except Exception:
        pass


def serve_forever(host=P.HOST, port=P.PORT, backend=None, web=True):
    from .events import EventBus
    _crash_fp = _install_crash_guards()
    _write_version_file()
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
        import time
        from .web import make_web_server, WEB_PORT
        web_srv = make_web_server(engine, bus, bridge=bridge,
                                  orchestrator=orchestrator, host=host)

        def _web_forever():
            # If the web server loop ever throws, log + restart it rather than losing the cockpit.
            while True:
                try:
                    web_srv.serve_forever()
                except Exception as e:
                    bus.publish("boot", msg="web server restart: %s" % e)
                    time.sleep(0.5)

        threading.Thread(target=_web_forever, name="hv-web", daemon=True).start()
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
            try:
                conn, addr = srv.accept()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # A transient accept() error (reset during handshake, fd churn) must NEVER end the
                # accept loop — that's the whole daemon. Log it and keep serving.
                bus.publish("boot", msg="accept error (continuing): %s" % e)
                continue
            try:
                threading.Thread(target=_serve_conn, args=(conn, addr, engine),
                                 name="hv-conn", daemon=True).start()
            except Exception as e:
                bus.publish("boot", msg="conn spawn error: %s" % e)
                try:
                    conn.close()
                except Exception:
                    pass
    except KeyboardInterrupt:
        print("shutting down", flush=True)
    finally:
        srv.close()
        engine.stop()


def _watch_sources_and_reexec():
    """Self-restart on code change: when any highvisor .py changes on disk, re-exec
    the daemon in place (sockets close; clients are one-shot and reconnect). Ends the
    'edit engine.py, ask Daniel to restart the daemon' loop. Crash-restart is the
    launchd KeepAlive plist's job (`hv install-daemon`)."""
    import glob
    import os
    import sys
    import time
    root = os.path.dirname(os.path.abspath(__file__))
    paths = (glob.glob(os.path.join(root, "*.py"))
             + glob.glob(os.path.join(root, "backends", "*.py")))
    baseline = {p: os.path.getmtime(p) for p in paths}
    while True:
        time.sleep(2.0)
        for p, m0 in list(baseline.items()):
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if m != m0:
                print("[hv] source changed: %s — re-exec" % os.path.basename(p), flush=True)
                os.chdir(os.path.dirname(root))   # repo root, so -m resolves
                os.execv(sys.executable, [sys.executable, "-m", "highvisor.server"] + sys.argv[1:])


def main():
    import threading
    threading.Thread(target=_watch_sources_and_reexec, daemon=True,
                     name="hv-source-watch").start()
    serve_forever()


if __name__ == "__main__":
    main()
