"""MacBackend — observe + control via the Accessibility API (AX) and CoreGraphics.

Mirrors WindowsBackend behind the same PlatformBackend seam (docs/01-architecture):
  - ``CGWindowListCopyWindowInfo`` enumerates on-screen windows; the target id is
    the CGWindowNumber (``win:<n>``).
  - ``CGWindowListCreateImage`` captures a SPECIFIC window even when it is
    unfocused/occluded — the macOS analogue of PrintWindow (tier-3 observation).
  - ``AXUIElement`` actions (``AXSetValue`` / ``AXPress`` / set ``AXPosition``)
    act on a background window WITHOUT bringing it frontmost — the reliable
    background path (tier 1). Hammerspoon's hs.axuielement proves this.
  - ``CGEventPostToPid`` delivers a keystroke to a pid (tier 2); ``activate`` +
    ``CGEventPost`` is the focus-stealing last resort (tier 4).

Coordinates: everything here is in **points** (the top-left-origin global display
coordinate space that AX and CGWindow bounds use) — uniform across displays and
what window positioning needs, so no per-monitor scale juggling. Only the captured
PNG is in native pixels (its own resolution). NB: on the Windows backend the unit
is physical pixels; a client that mixes the two across OSes must account for that.

TCC: AX needs the *Accessibility* grant (``AXIsProcessTrusted``); window capture
needs *Screen Recording*. We detect and raise a precise BackendError rather than
failing opaque (docs/03 risk table).
"""
import time
from io import BytesIO
from typing import List, Optional

import Quartz
from AppKit import NSBitmapImageRep, NSRunningApplication, NSWorkspace
from ApplicationServices import (
    AXIsProcessTrusted, AXUIElementCopyActionNames,
    AXUIElementCopyAttributeValue, AXUIElementCreateApplication,
    AXUIElementPerformAction, AXUIElementSetAttributeValue, AXValueCreate,
    AXValueGetValue)
try:  # the CGPoint/CGSize AXValue-type constants were renamed across pyobjc versions
    from ApplicationServices import kAXValueCGPointType, kAXValueCGSizeType
except ImportError:  # newer pyobjc
    from ApplicationServices import (
        kAXValueTypeCGPoint as kAXValueCGPointType,
        kAXValueTypeCGSize as kAXValueCGSizeType)

from ..backend import ActionResult, BackendError, Element, PlatformBackend, Target

# NSBitmapImageFileTypePNG is 4; the symbol moved across pyobjc versions, so pin it.
_PNG = 4

# AX attribute / action names as plain strings — robust across pyobjc versions.
_ROLE, _TITLE, _DESC = "AXRole", "AXTitle", "AXDescription"
_VALUE, _POS, _SIZE = "AXValue", "AXPosition", "AXSize"
_CHILDREN, _WINDOWS = "AXChildren", "AXWindows"
_FOCUSED_ELEM = "AXFocusedUIElement"
_PRESS, _RAISE = "AXPress", "AXRaise"
_EDITABLE_ROLES = ("AXTextArea", "AXTextField", "AXComboBox")

# key name -> macOS virtual keycode (for CGEventCreateKeyboardEvent).
KEYCODE = {
    "RETURN": 0x24, "ENTER": 0x24, "TAB": 0x30, "SPACE": 0x31,
    "DELETE": 0x33, "BACKSPACE": 0x33, "BACK": 0x33,
    "ESC": 0x35, "ESCAPE": 0x35, "FORWARDDELETE": 0x75, "DEL": 0x75,
    "LEFT": 0x7B, "RIGHT": 0x7C, "DOWN": 0x7D, "UP": 0x7E,
    "HOME": 0x73, "END": 0x77, "PAGEUP": 0x74, "PAGEDOWN": 0x79,
    "F1": 0x7A, "F2": 0x78, "F3": 0x63, "F4": 0x76, "F5": 0x60, "F6": 0x61,
    "F7": 0x62, "F8": 0x64, "F9": 0x65, "F10": 0x6D, "F11": 0x67, "F12": 0x6F,
}


class MacBackend(PlatformBackend):
    name = "macos"

    def thread_init(self):
        pass  # no COM apartment analogue; AX/CG are fine on our single worker thread

    def _require_ax(self):
        if not AXIsProcessTrusted():
            raise BackendError(
                "Accessibility permission is not granted to this process. Grant it in "
                "System Settings > Privacy & Security > Accessibility (add the terminal / "
                "python running highvisor), then retry.")

    # -------------------------------------------------------- window enumeration
    def _windows(self):
        """On-screen normal app windows (layer 0), front-to-back. Raw CGWindow dicts."""
        opts = (Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements)
        info = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
        out = []
        for w in info:
            if int(w.get("kCGWindowLayer", 0)) != 0:      # 0 == the normal window layer
                continue
            if not w.get("kCGWindowBounds"):
                continue
            out.append(w)
        return out

    def _resolve(self, ref: Optional[str]):
        """None/"screen" -> None. Else the raw CGWindow dict for the target.
        Accepts "win:<CGWindowNumber>", "pid:<n>" (that pid's frontmost window), or
        a case-insensitive title/owner substring."""
        if ref is None or ref == "screen":
            return None
        wins = self._windows()
        if ref.startswith("win:"):
            wid = int(ref.split(":", 1)[1])
            for w in wins:
                if int(w.get("kCGWindowNumber", -1)) == wid:
                    return w
            raise BackendError("no window with id %d" % wid)
        if ref.startswith("pid:"):
            pid = int(ref.split(":", 1)[1])
            for w in wins:                                # _windows is front-to-back
                if int(w.get("kCGWindowOwnerPID", -1)) == pid:
                    return w
            raise BackendError("no on-screen window for pid %d" % pid)
        low = ref.lower()
        for w in wins:
            if (low in (w.get("kCGWindowName") or "").lower()
                    or low in (w.get("kCGWindowOwnerName") or "").lower()):
                return w
        raise BackendError("no window matching ~ %r" % ref)

    def _bounds(self, w) -> tuple:
        """(x, y, w, h) of a CGWindow dict, in points."""
        b = w["kCGWindowBounds"]
        return (int(b["X"]), int(b["Y"]), int(b["Width"]), int(b["Height"]))

    # -------------------------------------------------------------- AX plumbing
    def _ax_app(self, pid: int):
        return AXUIElementCreateApplication(pid)

    def _ax_get(self, el, attr):
        if el is None:
            return None
        err, val = AXUIElementCopyAttributeValue(el, attr, None)
        return val if err == 0 else None

    def _ax_window(self, w):
        """The AXWindow element for a CGWindow dict: the app window whose title
        matches, else its main/first window. Needs the Accessibility grant."""
        self._require_ax()
        pid = int(w["kCGWindowOwnerPID"])
        app = self._ax_app(pid)
        windows = self._ax_get(app, _WINDOWS) or []
        want = (w.get("kCGWindowName") or "").strip()
        if want:
            for ax in windows:
                if (self._ax_get(ax, _TITLE) or "").strip() == want:
                    return ax, pid
        return (windows[0] if windows else app), pid

    @staticmethod
    def _unwrap_axvalue(v, point: bool) -> tuple:
        """Pull (x, y) out of an AXPosition or (w, h) out of an AXSize AXValue box.
        Returns (0, 0) when v is None or the extraction fails."""
        if v is None:
            return (0, 0)
        if point:
            ok, pt = AXValueGetValue(v, kAXValueCGPointType, None)
            return (int(pt.x), int(pt.y)) if ok else (0, 0)
        ok, sz = AXValueGetValue(v, kAXValueCGSizeType, None)
        return (int(sz.width), int(sz.height)) if ok else (0, 0)

    def _find_editable(self, el, depth=8):
        """DFS for a text/editable descendant (for text delivery)."""
        if el is None or depth < 0:
            return None
        role = self._ax_get(el, _ROLE)
        if role in _EDITABLE_ROLES:
            return el
        for k in (self._ax_get(el, _CHILDREN) or []):
            hit = self._find_editable(k, depth - 1)
            if hit is not None:
                return hit
        return None

    # ----------------------------------------------------------------- observe
    def list_targets(self) -> List[Target]:
        front_pid = -1
        fa = NSWorkspace.sharedWorkspace().frontmostApplication()
        if fa is not None:
            front_pid = int(fa.processIdentifier())
        out = []
        for w in self._windows():
            pid = int(w.get("kCGWindowOwnerPID", -1))
            x, y, ww, hh = self._bounds(w)
            wid = int(w.get("kCGWindowNumber", 0))
            out.append(Target(
                id="win:%d" % wid, kind="window", pid=pid,
                title=w.get("kCGWindowName") or "",
                class_name=w.get("kCGWindowOwnerName") or "",
                x=x, y=y, w=ww, h=hh,
                focused=(pid == front_pid),
                visible=bool(w.get("kCGWindowIsOnscreen", True)),
                path=self._app_path(pid)))
        return out

    def _app_path(self, pid: int) -> str:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return ""
        url = app.bundleURL() or app.executableURL()
        return url.path() if url is not None else ""

    def launch(self, spec: str) -> ActionResult:
        import subprocess
        spec = (spec or "").strip()
        if not spec:
            return ActionResult.fail("empty launch spec")
        if "://" in spec:                              # URL scheme (steam://, …)
            args = ["open", spec]
        elif spec.startswith("/") or spec.endswith(".app"):
            args = ["open", spec]                      # a path / .app bundle
        else:
            args = ["open", "-a", spec]                # an app name
        try:
            subprocess.Popen(args)
        except Exception as e:
            return ActionResult.fail("launch failed: %s" % e)
        return ActionResult(ok=True, detail="open %s" % " ".join(args[1:]))

    def screen_size(self):
        d = Quartz.CGMainDisplayID()
        return (int(Quartz.CGDisplayPixelsWide(d)), int(Quartz.CGDisplayPixelsHigh(d)))

    def screenshot(self, target: Optional[str]) -> bytes:
        w = self._resolve(target)
        if w is None:
            img = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())
        else:
            wid = int(w["kCGWindowNumber"])
            # BestResolution captures at the window's native backing scale (2x on a
            # Retina display) instead of point size, so a 1913x1062-pt window comes
            # back as ~3826x2124 px — full fidelity for the parity diff.
            opts = (Quartz.kCGWindowImageBoundsIgnoreFraming
                    | getattr(Quartz, "kCGWindowImageBestResolution", 0))
            img = Quartz.CGWindowListCreateImage(
                Quartz.CGRectNull, Quartz.kCGWindowListOptionIncludingWindow,
                wid, opts)
        if img is None:
            raise BackendError(
                "capture returned nothing — is Screen Recording granted? "
                "(System Settings > Privacy & Security > Screen Recording)")
        return self._png(img)

    def _png(self, cgimage) -> bytes:
        rep = NSBitmapImageRep.alloc().initWithCGImage_(cgimage)
        data = rep.representationUsingType_properties_(_PNG, None)
        return bytes(data)

    def click(self, target: str, x: int, y: int, button: str = "left",
              double: bool = False) -> ActionResult:
        w = self._resolve(target)
        if w is None:
            return ActionResult.fail("click needs a window target")
        wx, wy, ww, wh = self._bounds(w)
        gx, gy = wx + int(x), wy + int(y)          # window-relative -> global points
        self.activate(target)                       # a click on a bg app should focus it
        time.sleep(0.06)
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        pt = Quartz.CGPointMake(gx, gy)
        right = button == "right"
        b = Quartz.kCGMouseButtonRight if right else Quartz.kCGMouseButtonLeft
        down = Quartz.kCGEventRightMouseDown if right else Quartz.kCGEventLeftMouseDown
        up = Quartz.kCGEventRightMouseUp if right else Quartz.kCGEventLeftMouseUp
        # WARP the real OS cursor to the point so hover/position is correct (Unity
        # reads the actual cursor). Then post down/up exactly like a known-good
        # auto-clicker (othyn/macos-auto-clicker): HID source, HID tap, no click-state
        # field, no pre-move — just the button pair at the cursor position.
        Quartz.CGWarpMouseCursorPosition(pt)
        time.sleep(0.03)
        for _ in range(2 if double else 1):
            ed = Quartz.CGEventCreateMouseEvent(src, down, pt, b)
            eu = Quartz.CGEventCreateMouseEvent(src, up, pt, b)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ed)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, eu)
            time.sleep(0.02)
        return ActionResult(ok=True, tier=4,
                            detail="%s%s click @ global (%d,%d)"
                                   % ("double " if double else "", button, gx, gy))

    def inspect(self, target: str, depth: int = 3) -> Element:
        w = self._resolve(target)
        if w is None:
            raise BackendError("inspect needs a window target")
        ax, _pid = self._ax_window(w)
        return self._to_element(ax, depth)

    def ocr(self, target: str) -> dict:
        """Vision text recognition over the window capture. On-device, no network.
        The reader for AX-opaque apps (e.g. the ChatGPT desktop app)."""
        try:
            import Vision
            from Foundation import NSData
        except Exception as e:
            raise BackendError("OCR needs pyobjc Vision (pip install pyobjc-framework-Vision): %s" % e)
        png = self.screenshot(target)
        nsdata = NSData.dataWithBytes_length_(png, len(png))
        src = Quartz.CGImageSourceCreateWithData(nsdata, None)
        cg = None if src is None else Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
        if cg is None:
            raise BackendError("OCR: could not decode the capture")
        w, h = Quartz.CGImageGetWidth(cg), Quartz.CGImageGetHeight(cg)
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(1)          # 1 = accurate
        req.setUsesLanguageCorrection_(True)
        handler.performRequests_error_([req], None)
        boxes = []
        for r in (req.results() or []):
            cand = r.topCandidates_(1)
            if not cand:
                continue
            s = cand[0].string()
            bb = r.boundingBox()             # normalized, bottom-left origin
            x = int(bb.origin.x * w)
            y = int((1.0 - bb.origin.y - bb.size.height) * h)   # -> top-left px
            boxes.append({"text": str(s),
                          "bbox": [x, y, int(bb.size.width * w), int(bb.size.height * h)]})
        return {"w": int(w), "h": int(h), "boxes": boxes}

    def _to_element(self, el, depth) -> Element:
        if el is None:
            return Element(role="Unknown")
        role = self._ax_get(el, _ROLE) or "AXUnknown"
        name = self._ax_get(el, _TITLE) or self._ax_get(el, _DESC) or ""
        val = self._ax_get(el, _VALUE)
        val = "" if val is None else str(val)
        x, y = self._unwrap_axvalue(self._ax_get(el, _POS), point=True)
        ww, hh = self._unwrap_axvalue(self._ax_get(el, _SIZE), point=False)
        err, acts = AXUIElementCopyActionNames(el, None)
        el_out = Element(role=str(role), name=str(name), value=val,
                         x=x, y=y, w=ww, h=hh,
                         actions=[str(a) for a in (acts or [])] if err == 0 else [])
        if depth > 0:
            for k in (self._ax_get(el, _CHILDREN) or []):
                el_out.children.append(self._to_element(k, depth - 1))
        return el_out

    # ------------------------------------------------------------------- act
    def _running(self, pid: int):
        return NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)

    def activate(self, target: str) -> ActionResult:
        w = self._resolve(target)
        if w is None:
            return ActionResult.fail("activate needs a window target")
        app = self._running(int(w["kCGWindowOwnerPID"]))
        if app is None:
            return ActionResult.fail("no running application for target")
        # NSApplicationActivateAllWindows(1) | IgnoringOtherApps(2). The latter is
        # deprecated on macOS 14 but still the reliable "bring me forward" nudge.
        ok = bool(app.activateWithOptions_(1 | 2))
        return ActionResult(ok=ok, tier=4, detail="NSRunningApplication.activate")

    def move(self, target: str, x: int, y: int, w: int, h: int,
             topmost: Optional[bool] = None) -> ActionResult:
        win = self._resolve(target)
        if win is None:
            return ActionResult.fail("move needs a window target")
        ax, _pid = self._ax_window(win)
        if ax is None:
            return ActionResult.fail("no AX window to move")
        pt = AXValueCreate(kAXValueCGPointType, Quartz.CGPoint(x, y))
        sz = AXValueCreate(kAXValueCGSizeType, Quartz.CGSize(w, h))
        e1 = AXUIElementSetAttributeValue(ax, _POS, pt)
        e2 = AXUIElementSetAttributeValue(ax, _SIZE, sz)
        # topmost: AX has no persistent always-on-top for another app's window.
        # True -> raise it now (best-effort, NOT sticky); False/None -> leave z-order.
        if topmost is True:
            AXUIElementPerformAction(ax, _RAISE)
        ok = (e1 == 0 and e2 == 0)
        note = "" if topmost is not True else " (topmost=raise-once; AX can't pin sticky)"
        return ActionResult(ok=ok, tier=1,
                            detail="AXPosition/AXSize %d,%d %dx%d%s" % (x, y, w, h, note),
                            error=None if ok else "AX set failed (pos=%s size=%s)" % (e1, e2))

    def text(self, target: str, text: str) -> ActionResult:
        w = self._resolve(target)
        if w is None:
            return ActionResult.fail("text needs a window target")
        ax, pid = self._ax_window(w)
        edit = self._find_editable(ax) or self._ax_get(ax, _FOCUSED_ELEM)
        # Tier 1: AX set the value on the editable element (focus-free), verify by readback.
        if edit is not None:
            err = AXUIElementSetAttributeValue(edit, _VALUE, text)
            if err == 0:
                time.sleep(0.03)
                got = self._ax_get(edit, _VALUE)
                if got is not None and text in str(got):
                    return ActionResult(ok=True, tier=1, detail="AXSetValue")
            tier1_err = "AXSetValue err=%s / readback mismatch" % err
        else:
            tier1_err = "no editable AX element found"
        # Tier 4: activate + type the characters via CGEvent (steals focus).
        self.activate(target)
        time.sleep(0.06)
        for ch in text:
            self._post_char(ch, pid)
        return ActionResult(ok=True, tier=4,
                            detail="activate + CGEvent typing (tier1: %s)" % tier1_err)

    def key(self, target: str, keys: str, focus: bool = False) -> ActionResult:
        w = self._resolve(target)
        if w is None:
            return ActionResult.fail("key needs a window target")
        pid = int(w["kCGWindowOwnerPID"])
        name = keys.strip()
        parts = [p for p in name.replace("-", "+").split("+") if p]
        mods, base = parts[:-1], (parts[-1] if parts else "")
        flags = self._mod_flags(mods)
        code = self._keycode_for(base)
        if code is None:
            return ActionResult.fail("unknown key %r" % keys)
        # Tier 4: activate + post to the HID event tap (global). Needed for apps that
        # read from the focused HID stream and ignore per-pid posts — Unity games
        # (Caves of Qud) and other engines. Steals focus, so it's opt-in.
        if focus:
            self.activate(target)
            time.sleep(0.06)
            self._post_key(code, flags, pid=None)
            return ActionResult(ok=True, tier=4,
                                detail="activate + CGEvent(HID) keycode=0x%02X flags=0x%X" % (code, flags))
        # Tier 2: post the key to the target pid without stealing focus. Effectiveness
        # varies by app; if it no-ops the caller can retry with focus=true.
        self._post_key(code, flags, pid=pid)
        return ActionResult(ok=True, tier=2,
                            detail="CGEventPostToPid keycode=0x%02X flags=0x%X" % (code, flags))

    # ---- CGEvent helpers ----
    def _mod_flags(self, mods) -> int:
        m = 0
        for name in mods:
            u = name.upper()
            if u in ("CMD", "COMMAND", "META"): m |= Quartz.kCGEventFlagMaskCommand
            elif u in ("SHIFT",): m |= Quartz.kCGEventFlagMaskShift
            elif u in ("ALT", "OPTION", "OPT"): m |= Quartz.kCGEventFlagMaskAlternate
            elif u in ("CTRL", "CONTROL"): m |= Quartz.kCGEventFlagMaskControl
        return m

    def _keycode_for(self, base: str):
        u = base.upper()
        if u in KEYCODE:
            return KEYCODE[u]
        if len(base) == 1:
            return self._char_keycode(base)
        return None

    def _char_keycode(self, ch: str):
        return _US_KEYCODES.get(ch.lower())

    def _post_key(self, keycode: int, flags: int, pid: Optional[int] = None):
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, keycode, down)
            if flags:
                Quartz.CGEventSetFlags(ev, flags)
            if pid:
                Quartz.CGEventPostToPid(pid, ev)
            else:
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)

    def _post_char(self, ch: str, pid: Optional[int] = None):
        """Type one character by its unicode (keycode 0 + a unicode payload) so any
        printable char works regardless of keyboard layout."""
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(ch), ch)
            if pid:
                Quartz.CGEventPostToPid(pid, ev)
            else:
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)


# US ANSI keyboard: char -> virtual keycode, for named keys / combos. Plain text
# uses unicode typing (_post_char) instead, so this only needs the common set.
_US_KEYCODES = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06,
    "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E,
    "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15,
    "6": 0x16, "5": 0x17, "9": 0x19, "7": 0x1A, "8": 0x1C, "0": 0x1D, "o": 0x1F,
    "u": 0x20, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "k": 0x28, "n": 0x2D,
    "m": 0x2E, "=": 0x18, "-": 0x1B, "]": 0x1E, "[": 0x21, "'": 0x27, ";": 0x29,
    "\\": 0x2A, ",": 0x2B, "/": 0x2C, ".": 0x2F, "`": 0x32,
}
