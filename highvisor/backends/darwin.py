"""MacBackend — observe + control via Accessibility (AX) and Quartz.

*** WRITTEN ON WINDOWS — UNTESTED ON A REAL MAC. ***
Every pyobjc call here is a hypothesis until it goes green on macOS. It mirrors
the Windows backend's tier ladder using the APIs the Slice 0 mac spike
(spike/mac_slice0.py) and docs/03-research-findings.md identified:

  - tier 1 (accessibility): AXUIElementSetAttributeValue(kAXValueAttribute) — a
    semantic, focus-free write, the mac analog of UIA ValuePattern.SetValue.
  - tier 4 (global input): activate the app + synthesize CGEvent keystrokes.
  - capture: Quartz CGWindowListCreateImage grabs a SPECIFIC window even when it
    is not frontmost.

TCC permissions the daemon must have (surfaced as BackendError with guidance):
  - Accessibility (AXIsProcessTrusted) — required for all AX read/write.
  - Screen Recording — required for CGWindowListCreateImage to return pixels
    (else a black/desktop-only image on macOS 10.15+).

pyobjc is imported lazily so this module only needs the frameworks on macOS.

Deps: pyobjc-framework-Cocoa, pyobjc-framework-Quartz,
      pyobjc-framework-ApplicationServices
"""
import time
from typing import List, Optional

from ..backend import ActionResult, BackendError, Element, PlatformBackend, Target

# Editable AX roles we target for text/key delivery, in preference order.
_EDIT_ROLES = ("AXTextArea", "AXTextField", "AXComboBox")

# mac virtual keycodes for named keys (ANSI layout).
_KEYCODE_NAMED = {
    "RETURN": 0x24, "ENTER": 0x24, "TAB": 0x30, "SPACE": 0x31,
    "BACKSPACE": 0x33, "BACK": 0x33, "DELETE": 0x75, "DEL": 0x75,
    "ESC": 0x35, "ESCAPE": 0x35, "LEFT": 0x7B, "RIGHT": 0x7C,
    "DOWN": 0x7D, "UP": 0x7E, "HOME": 0x73, "END": 0x77,
    "PAGEUP": 0x74, "PAGEDOWN": 0x79,
    "F1": 0x7A, "F2": 0x78, "F3": 0x63, "F4": 0x76, "F5": 0x60,
    "F6": 0x61, "F7": 0x62, "F8": 0x64, "F9": 0x65, "F10": 0x6D,
    "F11": 0x67, "F12": 0x6F,
}

# mac virtual keycodes for printable chars (ANSI), for combos like cmd+s.
_KEYCODE_CHAR = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8,
    "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17,
    "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33,
    "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50, " ": 49,
}

_MODIFIER_ALIASES = {
    "CMD": "cmd", "COMMAND": "cmd", "WIN": "cmd", "META": "cmd", "SUPER": "cmd",
    "CTRL": "ctrl", "CONTROL": "ctrl", "ALT": "alt", "OPT": "alt", "OPTION": "alt",
    "SHIFT": "shift",
}


class MacBackend(PlatformBackend):
    name = "darwin"

    # --------------------------------------------------------------- lifecycle
    def thread_init(self):
        # Soft preflight: don't hard-fail here (that would kill the engine).
        # Per-op helpers raise a clear BackendError if a permission is missing.
        try:
            import AppKit  # noqa: F401  (import cost paid once on the worker thread)
            import Quartz  # noqa: F401
            import ApplicationServices  # noqa: F401
        except Exception as e:  # pragma: no cover - only meaningful on mac
            raise BackendError(
                "pyobjc frameworks missing: %s\n"
                "  pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz "
                "pyobjc-framework-ApplicationServices" % e)

    # ------------------------------------------------------------------ AX glue
    @staticmethod
    def _require_ax():
        from ApplicationServices import AXIsProcessTrusted
        if not AXIsProcessTrusted():
            raise BackendError(
                "Accessibility permission not granted. Grant it in "
                "System Settings > Privacy & Security > Accessibility for the "
                "process running the highvisor daemon, then restart it.")

    @staticmethod
    def _ax_raw(el, attr):
        """Copy an AX attribute; return the raw value (may be an AXValue) or None."""
        from ApplicationServices import AXUIElementCopyAttributeValue
        err, val = AXUIElementCopyAttributeValue(el, attr, None)
        return val if err == 0 else None

    @classmethod
    def _ax_str(cls, el, attr):
        v = cls._ax_raw(el, attr)
        return "" if v is None else str(v)

    @classmethod
    def _ax_children(cls, el):
        from ApplicationServices import kAXChildrenAttribute
        kids = cls._ax_raw(el, kAXChildrenAttribute)
        return list(kids) if kids else []

    @classmethod
    def _ax_role(cls, el):
        from ApplicationServices import kAXRoleAttribute
        return cls._ax_str(el, kAXRoleAttribute)

    @classmethod
    def _ax_point(cls, el):
        """Unwrap kAXPositionAttribute (an AXValue wrapping CGPoint)."""
        from ApplicationServices import kAXPositionAttribute
        v = cls._ax_raw(el, kAXPositionAttribute)
        return cls._unwrap_axvalue(v, point=True)

    @classmethod
    def _ax_size(cls, el):
        """Unwrap kAXSizeAttribute (an AXValue wrapping CGSize)."""
        from ApplicationServices import kAXSizeAttribute
        v = cls._ax_raw(el, kAXSizeAttribute)
        return cls._unwrap_axvalue(v, point=False)

    @staticmethod
    def _unwrap_axvalue(v, point):
        if v is None:
            return (0, 0)
        try:
            from ApplicationServices import AXValueGetValue
            try:  # constant name differs across pyobjc versions
                from ApplicationServices import kAXValueCGPointType, kAXValueCGSizeType
            except Exception:
                from ApplicationServices import (
                    kAXValueTypeCGPoint as kAXValueCGPointType,
                    kAXValueTypeCGSize as kAXValueCGSizeType)
            import Quartz
            if point:
                ok, out = AXValueGetValue(v, kAXValueCGPointType, Quartz.CGPoint())
                return (int(out.x), int(out.y)) if ok else (0, 0)
            ok, out = AXValueGetValue(v, kAXValueCGSizeType, Quartz.CGSize())
            return (int(out.width), int(out.height)) if ok else (0, 0)
        except Exception:
            return (0, 0)

    @classmethod
    def _find_role(cls, el, roles, depth=8):
        """DFS for the first descendant whose AX role is in ``roles``."""
        if cls._ax_role(el) in roles:
            return el
        if depth <= 0:
            return None
        for kid in cls._ax_children(el):
            hit = cls._find_role(kid, roles, depth - 1)
            if hit is not None:
                return hit
        return None

    # ------------------------------------------------------------ window model
    @staticmethod
    def _window_infos():
        """On-screen, real application windows (layer 0), newest first."""
        import Quartz
        opts = (Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements)
        out = []
        for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
            if w.get("kCGWindowLayer", 0) != 0:
                continue
            b = w.get("kCGWindowBounds") or {}
            out.append({
                "wid": int(w.get("kCGWindowNumber", 0)),
                "pid": int(w.get("kCGWindowOwnerPID", 0)),
                "owner": w.get("kCGWindowOwnerName") or "",
                "title": w.get("kCGWindowName") or "",
                "x": int(b.get("X", 0)), "y": int(b.get("Y", 0)),
                "w": int(b.get("Width", 0)), "h": int(b.get("Height", 0)),
            })
        return out

    def _resolve_info(self, ref: Optional[str]):
        """Turn a target ref into a window-info dict (or None for the screen).
        Accepts: None/"screen"; "win:1234"; "pid:1234"; else title substring."""
        if ref is None or ref == "screen":
            return None
        infos = self._window_infos()
        if ref.startswith("win:"):
            wid = int(ref.split(":", 1)[1])
            for info in infos:
                if info["wid"] == wid:
                    return info
            raise BackendError("no window with id %d" % wid)
        if ref.startswith("pid:"):
            pid = int(ref.split(":", 1)[1])
            for info in infos:
                if info["pid"] == pid:
                    return info
            raise BackendError("no window for pid %d" % pid)
        low = ref.lower()
        for info in infos:
            if low in info["title"].lower() or low in info["owner"].lower():
                return info
        raise BackendError("no window matching title ~ %r" % ref)

    def _ax_window_for(self, info):
        """Best-effort AXUIElement for the window described by ``info``.
        AX has no direct CGWindowNumber lookup, so match by title, else by
        position/size, else fall back to the app's first window."""
        self._require_ax()
        from ApplicationServices import (
            AXUIElementCreateApplication, kAXWindowsAttribute, kAXTitleAttribute)
        ax_app = AXUIElementCreateApplication(info["pid"])
        windows = self._ax_raw(ax_app, kAXWindowsAttribute)
        windows = list(windows) if windows else []
        if not windows:
            raise BackendError("no AX windows for pid %d (permission?)" % info["pid"])
        if info["title"]:
            for win in windows:
                if self._ax_str(win, kAXTitleAttribute) == info["title"]:
                    return win
        for win in windows:
            px, py = self._ax_point(win)
            if abs(px - info["x"]) <= 2 and abs(py - info["y"]) <= 2:
                return win
        return windows[0]

    @staticmethod
    def _frontmost_pid():
        from AppKit import NSWorkspace
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.processIdentifier() if app else -1

    # ----------------------------------------------------------------- observe
    def list_targets(self) -> List[Target]:
        fg = self._frontmost_pid()
        out = []
        for info in self._window_infos():
            title = info["title"] or info["owner"]
            if not title and info["w"] <= 0:
                continue
            out.append(Target(
                id="win:%d" % info["wid"], kind="window", pid=info["pid"],
                title=title, class_name=info["owner"],
                x=info["x"], y=info["y"], w=info["w"], h=info["h"],
                focused=(info["pid"] == fg), visible=True))
        return out

    def screenshot(self, target: Optional[str]) -> bytes:
        import Quartz
        from Cocoa import NSBitmapImageRep, NSPNGFileType
        info = self._resolve_info(target)
        if info is None:  # whole screen
            img = Quartz.CGWindowListCreateImage(
                Quartz.CGRectInfinite, Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID, Quartz.kCGWindowImageDefault)
        else:
            img = Quartz.CGWindowListCreateImage(
                Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
                info["wid"], Quartz.kCGWindowImageBoundsIgnoreFraming)
        if img is None:
            raise BackendError(
                "CGWindowListCreateImage returned no pixels. Grant Screen "
                "Recording in System Settings > Privacy & Security > Screen "
                "Recording for the daemon process, then restart it.")
        rep = NSBitmapImageRep.alloc().initWithCGImage_(img)
        data = rep.representationUsingType_properties_(NSPNGFileType, None)
        if data is None:
            raise BackendError("failed to encode PNG from window image")
        return bytes(data)  # NSData supports the buffer protocol

    def inspect(self, target: str, depth: int = 3) -> Element:
        info = self._resolve_info(target)
        if info is None:
            raise BackendError("inspect needs a window target")
        win = self._ax_window_for(info)
        return self._to_element(win, depth)

    def _to_element(self, el, depth) -> Element:
        from ApplicationServices import (
            kAXTitleAttribute, kAXDescriptionAttribute, kAXValueAttribute,
            AXUIElementIsAttributeSettable, AXUIElementCopyActionNames)
        role = self._ax_role(el) or "Unknown"
        name = self._ax_str(el, kAXTitleAttribute) or self._ax_str(el, kAXDescriptionAttribute)
        value = self._ax_str(el, kAXValueAttribute)
        x, y = self._ax_point(el)
        w, h = self._ax_size(el)
        out = Element(role=role, name=name, value=value, x=x, y=y, w=w, h=h)
        try:
            err, settable = AXUIElementIsAttributeSettable(el, kAXValueAttribute, None)
            if err == 0 and settable:
                out.actions.append("SetValue")
        except Exception:
            pass
        try:
            err, actions = AXUIElementCopyActionNames(el, None)
            if err == 0 and actions:
                out.actions.extend(str(a) for a in actions)
        except Exception:
            pass
        if depth > 0:
            for kid in self._ax_children(el):
                out.children.append(self._to_element(kid, depth - 1))
        return out

    # --------------------------------------------------------------------- act
    def activate(self, target: str) -> ActionResult:
        info = self._resolve_info(target)
        if info is None:
            return ActionResult.fail("activate needs a window target")
        from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(info["pid"])
        if app is None:
            return ActionResult.fail("no running app for pid %d" % info["pid"])
        ok = app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        # Best-effort raise of the specific window.
        try:
            win = self._ax_window_for(info)
            from ApplicationServices import AXUIElementPerformAction
            AXUIElementPerformAction(win, "AXRaise")
        except Exception:
            pass
        return ActionResult(ok=bool(ok), tier=4, detail="activate pid %d" % info["pid"])

    def text(self, target: str, text: str) -> ActionResult:
        info = self._resolve_info(target)
        if info is None:
            return ActionResult.fail("text needs a window target")
        win = self._ax_window_for(info)
        edit = self._find_role(win, _EDIT_ROLES)
        if edit is None:
            return ActionResult.fail("no editable (AXTextArea/Field) element found")

        # Tier 1: AX SetAttribute(kAXValue) — semantic, focus-free; verify readback.
        from ApplicationServices import (
            AXUIElementSetAttributeValue, kAXValueAttribute)
        try:
            err = AXUIElementSetAttributeValue(edit, kAXValueAttribute, text)
            time.sleep(0.05)
            got = self._ax_str(edit, kAXValueAttribute)
            if err == 0 and text in got:
                return ActionResult(ok=True, tier=1, detail="AX SetAttribute(kAXValue)")
            tier1_err = "err=%s readback mismatch" % err
        except Exception as e:
            tier1_err = str(e)

        # Tier 4: activate then type the text as a unicode CGEvent stream.
        try:
            self.activate(target)
            time.sleep(0.05)
            self._cg_type_unicode(text)
            return ActionResult(ok=True, tier=4, detail="activate + CGEvent unicode")
        except Exception as e:
            return ActionResult(ok=False, tier=None,
                                error="tier1(%s) tier4(%s)" % (tier1_err, e))

    def key(self, target: str, keys: str) -> ActionResult:
        info = self._resolve_info(target)
        if info is None:
            return ActionResult.fail("key needs a window target")
        # macOS has no reliable background key post (no PostMessage analog); the
        # dependable path is tier 4: focus the app, then synthesize CGEvents.
        try:
            self.activate(target)
            time.sleep(0.05)
            mods, keyname = self._parse_combo(keys)
            up = keyname.upper()
            if up in _KEYCODE_NAMED:
                self._cg_key(_KEYCODE_NAMED[up], mods)
            elif len(keyname) == 1 and keyname.lower() in _KEYCODE_CHAR:
                self._cg_key(_KEYCODE_CHAR[keyname.lower()], mods)
            elif len(keyname) == 1:
                self._cg_type_unicode(keyname)
            else:
                return ActionResult.fail("unknown key spec %r" % keys)
            return ActionResult(ok=True, tier=4, detail="CGEvent key %r" % keys)
        except Exception as e:
            return ActionResult.fail("no tier could deliver keys %r: %s" % (keys, e))

    # --------------------------------------------------------------- CGEvent I/O
    @staticmethod
    def _parse_combo(keys):
        """Split 'cmd+shift+s' -> (['cmd','shift'], 's'). Bare keys -> ([], key)."""
        parts = [p for p in keys.strip().replace("-", "+").split("+") if p]
        if not parts:
            return [], ""
        mods, key = [], parts[-1]
        for p in parts[:-1]:
            m = _MODIFIER_ALIASES.get(p.upper())
            if m:
                mods.append(m)
        return mods, key

    @staticmethod
    def _mod_flags(mods):
        import Quartz
        flags = 0
        for m in mods:
            if m == "cmd":
                flags |= Quartz.kCGEventFlagMaskCommand
            elif m == "ctrl":
                flags |= Quartz.kCGEventFlagMaskControl
            elif m == "alt":
                flags |= Quartz.kCGEventFlagMaskAlternate
            elif m == "shift":
                flags |= Quartz.kCGEventFlagMaskShift
        return flags

    def _cg_key(self, keycode, mods):
        import Quartz
        flags = self._mod_flags(mods)
        for is_down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, is_down)
            if flags:
                Quartz.CGEventSetFlags(ev, flags)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.005)

    @staticmethod
    def _cg_type_unicode(text):
        import Quartz
        for ch in text:
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            up = Quartz.CGEventCreateKeyboardEvent(None, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(up, len(ch), ch)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.005)
