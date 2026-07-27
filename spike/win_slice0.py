#!/usr/bin/env python3
"""highvisor — Slice 0 spike (Windows).

Goal (docs/03-research-findings.md): prove the premise before building the
engine — SCREENSHOT one specific window AND deliver input to it WHILE IT IS
UNFOCUSED, via the native/accessibility path, and verify the input landed.

Why a self-hosted target? Modern Win11 `notepad.exe` is a WinUI/Store app: the
launch pid hands off to a broker (so pid!=window owner) and its editor is a XAML
RichEditBox with NO child HWND — exactly the UWP/WinUI gap the research flagged
(UIA needs foreground, no HWND for WM_SETTEXT). To prove the *native background
pipeline* deterministically we spawn our own classic Win32 window with a real
EDIT child, which supports both UIA ValuePattern and WM_SETTEXT. The WinUI gap is
recorded in the spike README, not swept under the rug.

Flow:
  --serve   create a classic Win32 window + EDIT child, shown WITHOUT activation
            (SW_SHOWNOACTIVATE), run a message loop. Unique title for lookup.
  (driver)  spawn --serve, keep OUR console focused, then against the unfocused
            target:
              TIER 1  UIA ValuePattern.SetValue  (semantic, no focus, no coords)
              TIER 2  SendMessage(WM_SETTEXT)     (classic background technique)
            capture the window via PrintWindow(PW_RENDERFULLCONTENT) -> PNG,
            read the text back (WM_GETTEXT / UIA) to confirm, print a report.

Exit 0 iff the target was unfocused, at least one write tier verifiably landed,
and the screenshot captured. Deps: pip install uiautomation pillow
"""
import ctypes
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(HERE, "slice0_win_shot.png")
MARKER1 = "highvisor slice0 UIA-ValuePattern unfocused-write OK"
MARKER2 = "highvisor slice0 WM_SETTEXT unfocused-write OK"

WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH, WM_DESTROY = 0x000C, 0x000D, 0x000E, 0x0002
WS_OVERLAPPEDWINDOW, WS_CHILD, WS_VISIBLE = 0x00CF0000, 0x40000000, 0x10000000
ES_MULTILINE, WS_VSCROLL = 0x0004, 0x00200000
SW_SHOWNOACTIVATE, PW_RENDERFULLCONTENT = 4, 0x00000002
CW_USEDEFAULT = -0x80000000
WHITE_BRUSH = 0

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _setsig():
    sig = [
        (user32.GetWindowDC, ctypes.c_void_p, [wintypes.HWND]),
        (user32.PrintWindow, ctypes.c_int, [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]),
        (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, ctypes.c_void_p]),
        (user32.GetForegroundWindow, wintypes.HWND, []),
        (user32.FindWindowW, wintypes.HWND, [wintypes.LPCWSTR, wintypes.LPCWSTR]),
        (user32.FindWindowExW, wintypes.HWND,
         [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]),
        (user32.CreateWindowExW, wintypes.HWND,
         [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
          wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]),
        (user32.DefWindowProcW, ctypes.c_long,
         [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]),
        (kernel32.GetConsoleWindow, wintypes.HWND, []),
        (kernel32.GetModuleHandleW, wintypes.HINSTANCE, [wintypes.LPCWSTR]),
        (gdi32.CreateCompatibleDC, ctypes.c_void_p, [ctypes.c_void_p]),
        (gdi32.CreateCompatibleBitmap, ctypes.c_void_p,
         [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
        (gdi32.SelectObject, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
        (gdi32.DeleteObject, ctypes.c_int, [ctypes.c_void_p]),
        (gdi32.DeleteDC, ctypes.c_int, [ctypes.c_void_p]),
        (gdi32.GetDIBits, ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                         wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.UINT]),
    ]
    for fn, res, args in sig:
        fn.restype, fn.argtypes = res, args


# ---------------------------------------------------------------- target (serve)
def serve(title):
    hInst = kernel32.GetModuleHandleW(None)
    cls_name = "HighvisorSlice0Target"

    def wndproc(hwnd, msg, wp, lp):
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wp, lp)

    proc = WNDPROC(wndproc)
    wc = WNDCLASS()
    wc.lpfnWndProc = proc
    wc.hInstance = hInst
    wc.hCursor = user32.LoadCursorW(None, ctypes.c_wchar_p(0x7F00))  # IDC_ARROW
    wc.hbrBackground = gdi32.GetStockObject(WHITE_BRUSH)
    wc.lpszClassName = cls_name
    if not user32.RegisterClassW(ctypes.byref(wc)):
        raise ctypes.WinError()

    win = user32.CreateWindowExW(0, cls_name, title, WS_OVERLAPPEDWINDOW,
                                 CW_USEDEFAULT, CW_USEDEFAULT, 640, 400,
                                 None, None, hInst, None)
    if not win:
        raise ctypes.WinError()
    # A real classic EDIT child: exposes UIA ValuePattern AND a child HWND.
    user32.CreateWindowExW(0, "EDIT", "",
                           WS_CHILD | WS_VISIBLE | ES_MULTILINE | WS_VSCROLL,
                           0, 0, 624, 360, win, None, hInst, None)
    user32.ShowWindow(win, SW_SHOWNOACTIVATE)  # appear WITHOUT stealing focus
    user32.UpdateWindow(win)

    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
    return 0


# ---------------------------------------------------------------- capture helper
def capture_window(hwnd, path):
    from PIL import Image
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return False, "no window area"
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    rc = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth, bmi.bmiHeader.biHeight = w, -h
    bmi.bmiHeader.biPlanes, bmi.bmiHeader.biBitCount = 1, 32
    bmi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).save(path)
    return bool(rc), "PrintWindow rc=%s size=%dx%d" % (rc, w, h)


def wm_get_text(hwnd):
    n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(hwnd, WM_GETTEXT, n + 1, buf)
    return buf.value


# ------------------------------------------------------------------------ driver
def driver():
    import uiautomation as auto
    report = {}
    title = "highvisor-slice0-%s" % uuid.uuid4().hex[:8]
    child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--serve", title])
    try:
        # find our target window by its unique title
        win = None
        for _ in range(40):
            h = user32.FindWindowW(None, title)
            if h:
                win = h
                break
            time.sleep(0.15)
        if not win:
            print("FAIL: target window never appeared")
            return 1
        edit = user32.FindWindowExW(win, None, "EDIT", None)
        print("target hwnd=0x%X  edit hwnd=0x%X  title=%s" % (win, edit or 0, title))

        # keep OUR console in the foreground so the target is unfocused
        console = kernel32.GetConsoleWindow()
        if console:
            user32.SetForegroundWindow(console)
        time.sleep(0.3)
        fg = user32.GetForegroundWindow()
        report["unfocused_before_write"] = (fg != win)
        print("foreground=0x%X  target unfocused? %s" % (fg or 0, fg != win))

        # TIER 1 — UIA ValuePattern on the EDIT (semantic, focus-free)
        try:
            ctl = auto.ControlFromHandle(edit)
            ctl.GetValuePattern().SetValue(MARKER1)
            time.sleep(0.2)
            got = wm_get_text(edit)
            report["tier1_uia_valuepattern"] = "OK" if MARKER1 in got else "readback='%s'" % got
        except Exception as e:
            report["tier1_uia_valuepattern"] = "FAILED: %s" % e
        print("TIER 1  UIA ValuePattern.SetValue :", report["tier1_uia_valuepattern"])

        # TIER 2 — SendMessage(WM_SETTEXT) straight to the child HWND
        try:
            user32.SendMessageW(edit, WM_SETTEXT, 0, ctypes.c_wchar_p(MARKER2))
            time.sleep(0.2)
            got = wm_get_text(edit)
            report["tier2_wm_settext"] = "OK" if MARKER2 in got else "readback='%s'" % got
        except Exception as e:
            report["tier2_wm_settext"] = "FAILED: %s" % e
        print("TIER 2  SendMessage(WM_SETTEXT)   :", report["tier2_wm_settext"])

        report["unfocused_at_capture"] = (user32.GetForegroundWindow() != win)
        ok, note = capture_window(win, SHOT)
        report["screenshot"] = ("OK -> %s (%s)" % (SHOT, note)) if ok else "FAILED (%s)" % note
        print("SHOT    PrintWindow -> PNG        :", report["screenshot"])

        wrote = report.get("tier1_uia_valuepattern") == "OK" or \
                report.get("tier2_wm_settext") == "OK"
        success = wrote and ok and report["unfocused_before_write"]
        print("\n=== Slice 0 (Windows) report ===")
        for k, v in report.items():
            print("  %-26s %s" % (k, v))
        print("  %-26s %s" % ("VERDICT", "PASS" if success else "FAIL"))
        return 0 if success else 1
    finally:
        try:
            child.terminate()
        except Exception:
            pass


def _dpi_aware():
    # PER_MONITOR_AWARE_V2 (-4): keeps GetWindowRect and PrintWindow in the same
    # (physical) coordinate space so the capture isn't offset/scaled on HiDPI.
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        except Exception:
            pass


def main():
    _dpi_aware()
    _setsig()
    if len(sys.argv) >= 3 and sys.argv[1] == "--serve":
        return serve(sys.argv[2])
    return driver()


if __name__ == "__main__":
    sys.exit(main())
