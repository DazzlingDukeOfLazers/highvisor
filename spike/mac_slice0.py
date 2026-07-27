#!/usr/bin/env python3
"""highvisor — Slice 0 spike (macOS).  *** UNTESTED — written on Windows ***

Mirror of win_slice0.py for macOS, built from the researched APIs
(docs/03-research-findings.md). Must be run + verified on a real Mac; treat every
call here as a hypothesis until it goes green.

Goal: SCREENSHOT one specific window AND write into it WHILE IT IS UNFOCUSED,
via the native accessibility path, and verify the write landed.

Approach:
  - target: TextEdit (Cocoa app; exposes a clean AXTextArea)
  - TIER 1 (accessibility): AXUIElementSetAttributeValue(kAXValueAttribute) on the
    text area — a semantic, focus-free write, the Mac analog of UIA ValuePattern.
  - capture: Quartz CGWindowListCreateImage(kCGWindowListOptionIncludingWindow,
    windowID) grabs THAT window even when it is not frontmost.
  - readback: AXUIElementCopyAttributeValue(kAXValueAttribute).

Permissions (TCC) — the daemon must detect these and tell the user precisely:
  - Accessibility (AXIsProcessTrusted) — required for AX read/write.
  - Screen Recording — required for CGWindowListCreateImage to return pixels
    (otherwise you get a black/desktop-only image on macOS 10.15+).

Deps: pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz
      pyobjc-framework-ApplicationServices
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SHOT = os.path.join(HERE, "slice0_mac_shot.png")
MARKER = "highvisor slice0 AX unfocused-write OK"


def _preflight():
    try:
        from ApplicationServices import AXIsProcessTrusted
    except Exception as e:
        print("FAIL: pyobjc ApplicationServices missing:", e)
        print("  pip install pyobjc-framework-ApplicationServices pyobjc-framework-Quartz")
        return False
    if not AXIsProcessTrusted():
        print("FAIL: Accessibility permission not granted.")
        print("  System Settings > Privacy & Security > Accessibility -> enable your terminal/python.")
        return False
    return True


def _ax_children(el):
    from ApplicationServices import AXUIElementCopyAttributeValue, kAXChildrenAttribute
    err, kids = AXUIElementCopyAttributeValue(el, kAXChildrenAttribute, None)
    return list(kids) if (err == 0 and kids) else []


def _ax_role(el):
    from ApplicationServices import AXUIElementCopyAttributeValue, kAXRoleAttribute
    err, role = AXUIElementCopyAttributeValue(el, kAXRoleAttribute, None)
    return role if err == 0 else None


def _find_role(el, role, depth=6):
    """DFS for the first descendant with the given AX role."""
    if depth < 0:
        return None
    if _ax_role(el) == role:
        return el
    for kid in _ax_children(el):
        hit = _find_role(kid, role, depth - 1)
        if hit is not None:
            return hit
    return None


def _window_id_for_pid(pid, title_hint=None):
    """Find an on-screen window id owned by pid (via Quartz window list)."""
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
        if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 0) == 0:
            return w.get("kCGWindowNumber")
    return None


def _capture(window_id, path):
    import Quartz
    from Cocoa import NSBitmapImageRep, NSPNGFileType
    img = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming)
    if img is None:
        return False, "CGWindowListCreateImage returned None (Screen Recording perm?)"
    rep = NSBitmapImageRep.alloc().initWithCGImage_(img)
    data = rep.representationUsingType_properties_(NSPNGFileType, None)
    ok = data.writeToFile_atomically_(path, True)
    return bool(ok), "CGWindowListCreateImage %dx%d" % (
        Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img))


def main():
    if not _preflight():
        return 1
    from AppKit import NSWorkspace, NSApplicationActivateIgnoringOtherApps
    from ApplicationServices import (
        AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
        AXUIElementSetAttributeValue, kAXWindowsAttribute, kAXValueAttribute)

    report = {}
    # launch a fresh TextEdit doc
    subprocess.run(["osascript", "-e",
                    'tell application "TextEdit" to make new document'], check=False)
    subprocess.run(["open", "-a", "TextEdit"], check=False)
    time.sleep(2.0)

    # resolve TextEdit pid
    pid = None
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.localizedName() == "TextEdit":
            pid = app.processIdentifier()
            break
    if pid is None:
        print("FAIL: TextEdit not running")
        return 1

    # move focus AWAY: activate Finder so TextEdit is unfocused
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.localizedName() == "Finder":
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
            break
    time.sleep(0.6)
    frontmost = NSWorkspace.sharedWorkspace().frontmostApplication()
    report["unfocused_before_write"] = (frontmost.processIdentifier() != pid)
    print("frontmost=%s  TextEdit unfocused? %s"
          % (frontmost.localizedName(), report["unfocused_before_write"]))

    ax_app = AXUIElementCreateApplication(pid)
    err, windows = AXUIElementCopyAttributeValue(ax_app, kAXWindowsAttribute, None)
    if err != 0 or not windows:
        print("FAIL: no AX windows for TextEdit (err=%s)" % err)
        return 1
    win = windows[0]
    text_area = _find_role(win, "AXTextArea")
    if text_area is None:
        print("FAIL: no AXTextArea found")
        return 1

    # TIER 1 — AX set value (semantic, focus-free)
    try:
        set_err = AXUIElementSetAttributeValue(text_area, kAXValueAttribute, MARKER)
        time.sleep(0.3)
        _, got = AXUIElementCopyAttributeValue(text_area, kAXValueAttribute, None)
        report["tier1_ax_setvalue"] = "OK" if (set_err == 0 and got == MARKER) \
            else "err=%s readback='%s'" % (set_err, got)
    except Exception as e:
        report["tier1_ax_setvalue"] = "FAILED: %s" % e
    print("TIER 1  AX SetAttribute(kAXValue) :", report["tier1_ax_setvalue"])

    # screenshot the specific window while unfocused
    wid = _window_id_for_pid(pid)
    if wid is None:
        report["screenshot"] = "FAILED (no window id)"
        ok = False
    else:
        ok, note = _capture(wid, SHOT)
        report["screenshot"] = ("OK -> %s (%s)" % (SHOT, note)) if ok else "FAILED (%s)" % note
    print("SHOT    CGWindowListCreateImage   :", report["screenshot"])

    report["unfocused_at_capture"] = (
        NSWorkspace.sharedWorkspace().frontmostApplication().processIdentifier() != pid)
    success = report.get("tier1_ax_setvalue") == "OK" and ok \
        and report["unfocused_before_write"]
    print("\n=== Slice 0 (macOS) report ===")
    for k, v in report.items():
        print("  %-26s %s" % (k, v))
    print("  %-26s %s" % ("VERDICT", "PASS" if success else "FAIL"))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
