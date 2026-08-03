"""EventBus — a tiny thread-safe pub/sub with a bounded history ring.

The engine, the web layer, and the cross-machine bridge all publish short
JSON-able event dicts here; subscribers (the web SSE stream, the bridge's
log-mirror) drain them from per-subscriber queues. The history ring lets a
late-joining web client replay the last N events so the onscreen log is never
blank on load.

Deliberately dependency-free and paradigm-neutral (plain threads + queues), to
match the threaded engine rather than dragging in asyncio.
"""
import itertools
import queue
import threading
import time


class EventBus:
    def __init__(self, history: int = 500):
        self._lock = threading.Lock()
        self._subs: "set[queue.Queue]" = set()
        self._history: "list[dict]" = []
        self._max = history
        self._seq = itertools.count(1)

    def publish(self, kind: str, **fields) -> dict:
        """Record an event and fan it out to every live subscriber. Never blocks
        on a slow subscriber — a full queue just drops the event for that one."""
        ev = {"seq": next(self._seq), "t": time.time(), "kind": kind}
        ev.update(fields)
        with self._lock:
            self._history.append(ev)
            if len(self._history) > self._max:
                del self._history[:-self._max]
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass
        return ev

    def subscribe(self):
        """Return (queue, history_snapshot). Feed the snapshot first, then drain
        the queue for live events. Call :meth:`unsubscribe` when done."""
        q: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
            hist = list(self._history)
        return q, hist

    def unsubscribe(self, q) -> None:
        with self._lock:
            self._subs.discard(q)
