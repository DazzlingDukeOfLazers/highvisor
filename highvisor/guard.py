"""The TIMESHARE GUARD: this machine is shared — highvisor borrows the keyboard/mouse,
it doesn't own them. Around any focus- or mouse-stealing op the guard:

  1. remembers the frontmost app + mouse position (only when the user is in a NON-game
     app — if a game window is already frontmost, nobody is being interrupted),
  2. plays an audio countdown (three pings, ~1.2s) so the user can lift their hands,
  3. lets the ops run as one SESSION (ops within GRACE_IDLE share it; MAX_SESSION hard cap),
  4. restores focus + mouse position and plays a return cue when the session ends.

Abort channels (any of): the cockpit ABORT button / `hv abort` (the abort_control op),
touching ~/.config/highvisor/ABORT, or the global panic hotkey Ctrl+Opt+Cmd+H (best-effort
NSEvent monitor; needs Accessibility, fails harmless). An abort restores immediately and
refuses further control ops for 30s. Disable the whole guard (e.g. for unattended runs)
by touching ~/.config/highvisor/guard_off.
"""
import os
import subprocess
import threading
import time

ABORT_FILE = os.path.expanduser("~/.config/highvisor/ABORT")
DISABLE_FILE = os.path.expanduser("~/.config/highvisor/guard_off")
SND_COUNT = "/System/Library/Sounds/Tink.aiff"
SND_RETURN = "/System/Library/Sounds/Glass.aiff"
# Frontmost apps that count as "ours" — no interruption, so no countdown/restore.
OUR_APPS = ("cavesofqud", "coq", "raves of qud", "ravesofqud", "godot")


class ControlGuard:
    GRACE_IDLE = 8.0     # ops within this window share one session (one countdown, one restore)
    MAX_SESSION = 20.0   # hard cap: control force-releases after this, mid-work or not

    def __init__(self, bus=None):
        self.bus = bus
        self._lock = threading.Lock()
        self._sess = None
        self._block_until = 0.0
        threading.Thread(target=self._reaper, daemon=True, name="hv-guard").start()
        self._start_hotkey()

    # ------------------------------------------------------------------ public
    def begin(self):
        """Call before a focus/mouse op. Returns None to proceed, or an error string."""
        if os.path.exists(DISABLE_FILE):
            return None
        if os.path.exists(ABORT_FILE):
            try:
                os.unlink(ABORT_FILE)
            except OSError:
                pass
            self.abort("abort file")
        now = time.time()
        if now < self._block_until:
            return "control aborted by user (%.0fs cooldown remaining)" % (self._block_until - now)
        with self._lock:
            if self._sess and now - self._sess["t0"] > self.MAX_SESSION:
                self._restore_locked("20s max")
            if self._sess is None:
                name, pid = "", 0
                try:
                    name, pid = self._front()
                except Exception:
                    pass
                ours = (not name) or any(a in name.lower() for a in OUR_APPS)
                sess = {"t0": now, "last": now, "pid": pid, "name": name, "ours": ours, "mouse": None}
                if not ours:
                    try:
                        sess["mouse"] = self._mouse()
                    except Exception:
                        pass
                    for _ in range(3):        # the countdown — user lifts their hands
                        self._play(SND_COUNT)
                        time.sleep(0.4)
                self._sess = sess
                if self.bus:
                    try:
                        self.bus.publish("guard", taking_control=True, was_front=name, interrupting=not ours)
                    except Exception:
                        pass
            self._sess["last"] = now
        return None

    def abort(self, why="user"):
        """Immediate release + 30s refusal of further control ops."""
        with self._lock:
            self._block_until = time.time() + 30.0
            self._restore_locked("ABORT: " + why)
        return {"ok": True, "blocked_for_s": 30, "why": why}

    def status(self):
        with self._lock:
            s = self._sess
            return {"ok": True,
                    "in_session": s is not None,
                    "interrupting": bool(s and not s.get("ours")),
                    "cooldown_s": max(0.0, self._block_until - time.time())}

    # ------------------------------------------------------------------ internals
    def _reaper(self):
        while True:
            time.sleep(1.0)
            with self._lock:
                s = self._sess
                if not s:
                    continue
                now = time.time()
                if now - s["last"] > self.GRACE_IDLE:
                    self._restore_locked("idle")
                elif now - s["t0"] > self.MAX_SESSION:
                    self._restore_locked("20s max")

    def _restore_locked(self, why):
        s, self._sess = self._sess, None
        if not s or s.get("ours"):
            return
        try:
            if s.get("pid"):
                from AppKit import NSRunningApplication
                app = NSRunningApplication.runningApplicationWithProcessIdentifier_(s["pid"])
                if app:
                    app.activateWithOptions_(0)
        except Exception:
            pass
        try:
            if s.get("mouse"):
                import Quartz
                Quartz.CGWarpMouseCursorPosition(s["mouse"])
        except Exception:
            pass
        self._play(SND_RETURN)   # "your machine is back"
        if self.bus:
            try:
                self.bus.publish("guard", released=why, returned_to=s.get("name", ""))
            except Exception:
                pass

    def _front(self):
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return "", 0
        return str(app.localizedName() or ""), int(app.processIdentifier())

    def _mouse(self):
        import Quartz
        loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
        return (float(loc.x), float(loc.y))

    def _play(self, snd):
        try:
            subprocess.Popen(["afplay", snd])
        except Exception:
            pass

    def _start_hotkey(self):
        """Global panic hotkey Ctrl+Opt+Cmd+H — best effort (needs Accessibility +
        a pumped runloop on this thread; any failure leaves file/button/CLI channels)."""
        def run():
            try:
                from AppKit import NSEvent, NSEventMaskKeyDown
                from Foundation import NSDate, NSRunLoop

                def handler(ev):
                    try:
                        mods = int(ev.modifierFlags())
                        if (ev.keyCode() == 4                      # 'h'
                                and mods & (1 << 18)               # control
                                and mods & (1 << 19)               # option
                                and mods & (1 << 20)):             # command
                            self.abort("hotkey")
                    except Exception:
                        pass

                NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, handler)
                while True:
                    NSRunLoop.currentRunLoop().runUntilDate_(
                        NSDate.dateWithTimeIntervalSinceNow_(0.5))
            except Exception:
                pass

        threading.Thread(target=run, daemon=True, name="hv-guard-hotkey").start()
