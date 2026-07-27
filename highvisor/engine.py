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
    def __init__(self, backend):
        self.backend = backend
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
            return b.key(req["target"], req.get("keys", "")).to_dict()

        if op == P.OP_INSPECT:
            tree = b.inspect(req["target"], int(req.get("depth", 3)))
            return {"ok": True, "tree": tree.to_dict()}

        return {"ok": False, "error": "unknown op: %r" % op}

    def stop(self):
        job = _Job(None)
        self._q.put(job)
        job.event.wait(timeout=2.0)
