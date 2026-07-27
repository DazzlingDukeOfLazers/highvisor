"""WindowsBackend — observe + control via UI Automation (UIA) and Win32.

Built on what the Slice 0 spike proved (see spike/README.md):
  - UIA ValuePattern.SetValue writes to an UNFOCUSED window (tier 1)
  - SendMessage(WM_SETTEXT) to a child EDIT hwnd (tier 2)
  - DPI-aware PrintWindow(PW_RENDERFULLCONTENT) captures a specific window
  - 64-bit ctypes needs explicit argtypes/restype or handles truncate

UIA (via the ``uiautomation`` package) does enumeration, inspection, and the
semantic tier-1 actions. Raw Win32 (ctypes) does capture, message posting, and
activation. All calls happen on the engine's single worker thread, which owns the
COM apartment — so no cross-thread COM headaches.
"""
import ctypes
import time
from ctypes import wintypes
from typing import List, Optional

import uiautomation as auto

from ..backend import ActionResult, BackendError, Element, PlatformBackend, Target

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# --- Win32 constants -------------------------------------------------------
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH = 0x000C, 0x000D, 0x000E
WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
PW_RENDERFULLCONTENT = 0x00000002
SW_RESTORE = 9

# BitBlt raster op + SetWindowPos flags / special HWNDs (window-ops).
SRCCOPY = 0x00CC0020
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER = 0x0001, 0x0002, 0x0004
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
HWND_TOP, HWND_TOPMOST, HWND_NOTOPMOST = 0, -1, -2
SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001

VK = {
    "RETURN": 0x0D, "ENTER": 0x0D, "TAB": 0x09, "ESC": 0x1B, "ESCAPE": 0x1B,
    "SPACE": 0x20, "BACKSPACE": 0x08, "BACK": 0x08, "DELETE": 0x2E, "DEL": 0x2E,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22, "INSERT": 0x2D,
}
for _i in range(1, 13):
    VK["F%d" % _i] = 0x70 + (_i - 1)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _configure_win32():
    """Set restype/argtypes for every handle-bearing call (64-bit correctness)."""
    sig = [
        (user32.GetWindowDC, ctypes.c_void_p, [wintypes.HWND]),
        (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, ctypes.c_void_p]),
        (user32.PrintWindow, ctypes.c_int,
         [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]),
        (user32.GetForegroundWindow, wintypes.HWND, []),
        (user32.SetForegroundWindow, ctypes.c_int, [wintypes.HWND]),
        (user32.GetWindowRect, ctypes.c_int, [wintypes.HWND, ctypes.c_void_p]),
        (user32.IsIconic, ctypes.c_int, [wintypes.HWND]),
        (user32.ShowWindow, ctypes.c_int, [wintypes.HWND, ctypes.c_int]),
        (user32.SendMessageW, ctypes.c_long,
         [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
        (user32.PostMessageW, ctypes.c_int,
         [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
        (gdi32.CreateCompatibleDC, ctypes.c_void_p, [ctypes.c_void_p]),
        (gdi32.CreateCompatibleBitmap, ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
        (gdi32.SelectObject, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
        (gdi32.DeleteObject, ctypes.c_int, [ctypes.c_void_p]),
        (gdi32.DeleteDC, ctypes.c_int, [ctypes.c_void_p]),
        (gdi32.GetDIBits, ctypes.c_int,
         [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
          ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]),
        # window-ops: screen capture (BitBlt), screen size, move, robust activate
        (user32.GetDC, ctypes.c_void_p, [wintypes.HWND]),
        (user32.GetSystemMetrics, ctypes.c_int, [ctypes.c_int]),
        (user32.SetWindowPos, ctypes.c_int,
         [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
          ctypes.c_int, ctypes.c_int, wintypes.UINT]),
        (user32.GetWindowThreadProcessId, wintypes.DWORD,
         [wintypes.HWND, ctypes.c_void_p]),
        (user32.AttachThreadInput, ctypes.c_int,
         [wintypes.DWORD, wintypes.DWORD, ctypes.c_int]),
        (user32.BringWindowToTop, ctypes.c_int, [wintypes.HWND]),
        (user32.SystemParametersInfoW, ctypes.c_int,
         [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]),
        (gdi32.BitBlt, ctypes.c_int,
         [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
          ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
          wintypes.DWORD]),
    ]
    for fn, res, args in sig:
        fn.restype, fn.argtypes = res, args


def _dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_V2
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


class WindowsBackend(PlatformBackend):
    name = "windows"

    def thread_init(self):
        # Must run before creating windows/UIA on this thread.
        _dpi_aware()
        _configure_win32()
        # Touch UIA once so comtypes initializes COM on THIS (worker) thread.
        auto.GetRootControl()

    # ---------------------------------------------------------------- helpers
    def _toplevels(self):
        """Top-level window controls that are real, on-screen, and named."""
        out = []
        for w in auto.GetRootControl().GetChildren():
            try:
                if w.ControlTypeName not in ("WindowControl", "PaneControl"):
                    continue
                if not w.NativeWindowHandle:
                    continue
                out.append(w)
            except Exception:
                continue
        return out

    def _resolve(self, ref: Optional[str]):
        """Turn a target ref into a top-level HWND (int).
        Accepts: None/"screen" -> None; "hwnd:0x1a2b"/"hwnd:123"; "pid:123";
        else a case-insensitive title substring."""
        if ref is None or ref == "screen":
            return None
        if ref.startswith("hwnd:"):
            v = ref.split(":", 1)[1]
            return int(v, 16) if v.lower().startswith("0x") else int(v)
        if ref.startswith("pid:"):
            pid = int(ref.split(":", 1)[1])
            for w in self._toplevels():
                if w.ProcessId == pid:
                    return w.NativeWindowHandle
            raise BackendError("no window for pid %d" % pid)
        low = ref.lower()
        for w in self._toplevels():
            if low in (w.Name or "").lower():
                return w.NativeWindowHandle
        raise BackendError("no window matching title ~ %r" % ref)

    def _find_editable(self, ctrl, depth=8):
        """DFS for an edit/document descendant (for text/key delivery)."""
        try:
            if ctrl.ControlTypeName in ("EditControl", "DocumentControl"):
                return ctrl
        except Exception:
            pass
        if depth <= 0:
            return None
        try:
            kids = ctrl.GetChildren()
        except Exception:
            return None
        for k in kids:
            hit = self._find_editable(k, depth - 1)
            if hit is not None:
                return hit
        return None

    # ----------------------------------------------------------------- observe
    def list_targets(self) -> List[Target]:
        fg = user32.GetForegroundWindow()
        out = []
        for w in self._toplevels():
            try:
                r = w.BoundingRectangle
                hwnd = w.NativeWindowHandle
                title = w.Name or ""
                if not title and (r.width() <= 0 or r.height() <= 0):
                    continue
                out.append(Target(
                    id="hwnd:0x%X" % hwnd, kind="window", pid=w.ProcessId,
                    title=title, class_name=w.ClassName or "",
                    x=r.left, y=r.top, w=r.width(), h=r.height(),
                    focused=(hwnd == fg), visible=not w.IsOffscreen))
            except Exception:
                continue
        return out

    def screenshot(self, target: Optional[str]) -> bytes:
        from io import BytesIO
        from PIL import Image
        hwnd = self._resolve(target)
        if hwnd is None:
            # Full-screen: PrintWindow on the desktop returns black, so BitBlt
            # from the screen DC instead. Physical pixels (we're DPI-aware).
            w, h = self.screen_size()
            src = user32.GetDC(None)
            mem = gdi32.CreateCompatibleDC(src)
            bmp = gdi32.CreateCompatibleBitmap(src, w, h)
            gdi32.SelectObject(mem, bmp)
            gdi32.BitBlt(mem, 0, 0, w, h, src, 0, 0, SRCCOPY)
            buf = self._dib_bytes(mem, bmp, w, h)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, src)
            out = BytesIO()
            Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).save(out, "PNG")
            return out.getvalue()
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w <= 0 or h <= 0:
            raise BackendError("window has no area (minimized?)")
        hdc = user32.GetWindowDC(hwnd)
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        gdi32.SelectObject(mem, bmp)
        user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
        buf = self._dib_bytes(mem, bmp, w, h)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, hdc)
        out = BytesIO()
        Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).save(out, "PNG")
        return out.getvalue()

    def _dib_bytes(self, mem, bmp, w, h):
        """Pull top-down 32-bit BGRX pixels out of a bitmap via GetDIBits."""
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h
        bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
        bmi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        return buf

    def screen_size(self):
        return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))

    def move(self, target: str, x: int, y: int, w: int, h: int,
             topmost: bool = False) -> ActionResult:
        hwnd = self._resolve(target)
        if hwnd is None:
            return ActionResult.fail("move needs a window target")
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        insert_after = HWND_TOPMOST if topmost else HWND_TOP
        flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
        ok = user32.SetWindowPos(hwnd, insert_after, x, y, w, h, flags)
        return ActionResult(ok=bool(ok), tier=4,
                            detail="SetWindowPos %d,%d %dx%d topmost=%s"
                                   % (x, y, w, h, topmost))

    def inspect(self, target: str, depth: int = 3) -> Element:
        hwnd = self._resolve(target)
        if hwnd is None:
            raise BackendError("inspect needs a window target")
        return self._to_element(auto.ControlFromHandle(hwnd), depth)

    def _to_element(self, ctrl, depth) -> Element:
        try:
            r = ctrl.BoundingRectangle
            el = Element(role=ctrl.ControlTypeName, name=ctrl.Name or "",
                         x=r.left, y=r.top, w=r.width(), h=r.height())
        except Exception:
            el = Element(role="Unknown")
        try:
            vp = ctrl.GetValuePattern()
            if vp is not None:
                el.value = vp.Value or ""
                el.actions.append("SetValue")
        except Exception:
            pass
        if depth > 0:
            try:
                for k in ctrl.GetChildren():
                    el.children.append(self._to_element(k, depth - 1))
            except Exception:
                pass
        return el

    # ------------------------------------------------------------------ act
    def activate(self, target: str) -> ActionResult:
        hwnd = self._resolve(target)
        if hwnd is None:
            return ActionResult.fail("activate needs a window target")
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        # A bare SetForegroundWindow from a background process is refused by the
        # foreground lock (returns 0). Defeat it: zero the lock timeout, then
        # attach our input queue to the current foreground thread's so Windows
        # treats the call as coming from the active app.
        user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0,
                                     ctypes.c_void_p(0), 0)
        fg = user32.GetForegroundWindow()
        tgt_t = user32.GetWindowThreadProcessId(hwnd, None)
        fg_t = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = bool(fg_t) and fg_t != tgt_t
        if attached:
            user32.AttachThreadInput(fg_t, tgt_t, True)
        try:
            user32.BringWindowToTop(hwnd)
            ok = user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(fg_t, tgt_t, False)
        return ActionResult(ok=bool(ok), tier=4,
                            detail="SetForegroundWindow=%s (attach=%s)"
                                   % (ok, attached))

    def _wm_get_text(self, hwnd):
        n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
        b = ctypes.create_unicode_buffer(n + 1)
        user32.SendMessageW(hwnd, WM_GETTEXT, n + 1, ctypes.addressof(b))
        return b.value

    def text(self, target: str, text: str) -> ActionResult:
        hwnd = self._resolve(target)
        if hwnd is None:
            return ActionResult.fail("text needs a window target")
        edit = self._find_editable(auto.ControlFromHandle(hwnd))
        if edit is None:
            return ActionResult.fail("no editable element found in target")
        edit_hwnd = edit.NativeWindowHandle

        # Tier 1: UIA ValuePattern (semantic, focus-free) — verify by readback.
        try:
            vp = edit.GetValuePattern()
            vp.SetValue(text)
            time.sleep(0.05)
            got = ""
            try:
                got = edit.GetValuePattern().Value or ""
            except Exception:
                got = self._wm_get_text(edit_hwnd) if edit_hwnd else ""
            if text in got:
                return ActionResult(ok=True, tier=1, detail="UIA ValuePattern.SetValue")
        except Exception as e:
            tier1_err = str(e)
        else:
            tier1_err = "readback mismatch"

        # Tier 2: WM_SETTEXT to the child edit hwnd.
        if edit_hwnd:
            try:
                user32.SendMessageW(edit_hwnd, WM_SETTEXT, 0, ctypes.c_wchar_p(text))
                time.sleep(0.05)
                if text in (self._wm_get_text(edit_hwnd) or ""):
                    return ActionResult(ok=True, tier=2, detail="WM_SETTEXT")
            except Exception as e:
                return ActionResult(ok=False, tier=None,
                                    error="tier1(%s) tier2(%s)" % (tier1_err, e))
        return ActionResult(ok=False, tier=None,
                            error="tier1(%s); no child hwnd for tier2" % tier1_err)

    def key(self, target: str, keys: str) -> ActionResult:
        hwnd = self._resolve(target)
        if hwnd is None:
            return ActionResult.fail("key needs a window target")
        # Deliver to the editable child if present, else the top-level window.
        edit = self._find_editable(auto.ControlFromHandle(hwnd))
        dest = (edit.NativeWindowHandle if edit and edit.NativeWindowHandle
                else hwnd)
        name = keys.strip()
        upper = name.upper()

        # Tier 2: post a named virtual key, or a single printable char.
        if upper in VK:
            vk = VK[upper]
            user32.PostMessageW(dest, WM_KEYDOWN, vk, 0)
            user32.PostMessageW(dest, WM_KEYUP, vk, 0)
            return ActionResult(ok=True, tier=2, detail="PostMessage VK 0x%02X" % vk)
        if len(name) == 1:
            user32.PostMessageW(dest, WM_CHAR, ord(name), 0)
            return ActionResult(ok=True, tier=2, detail="PostMessage WM_CHAR %r" % name)

        # Tier 4: combos / long sequences — activate then send globally.
        # (PostMessage can't carry modifier state reliably; see research findings.)
        try:
            self.activate(target)
            time.sleep(0.05)
            auto.SendKeys(keys, waitTime=0)
            return ActionResult(ok=True, tier=4, detail="activate + SendKeys")
        except Exception as e:
            return ActionResult.fail("no tier could deliver keys %r: %s" % (keys, e))
