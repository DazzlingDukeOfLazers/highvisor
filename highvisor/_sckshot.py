"""_sckshot — capture ONE window via ScreenCaptureKit and write it as a PNG.

Run as a short-lived subprocess:

    python -m highvisor._sckshot <cgWindowID> <backing:0|1> <out.png>

Why a subprocess: ScreenCaptureKit's only capture entry points are async
(``getShareableContentWithCompletionHandler:`` / ``captureImageWithFilter:...``),
and their completion blocks are delivered to the **main thread's** run loop. The
daemon runs ``screenshot()`` on the Engine's worker thread, where pumping a run
loop never receives the callbacks (measured: it times out). Here we ARE the
process main thread, so a plain ``CFRunLoopRunInMode`` pump delivers them.

``backing=1`` captures at the display's native backing scale (2x on a Retina
display — "true 2x"); ``backing=0`` captures at point size (1:1 px<->pt, so a
coordinate read off the PNG lands as a click without halving). Exit 0 on success;
non-zero with a one-line reason on stderr so the caller can fall back.
"""
import sys
import threading


def _fail(msg: str) -> "int":
    sys.stderr.write(msg.rstrip() + "\n")
    return 2


def capture(wid: int, backing: bool, out_path: str) -> int:
    import Quartz
    import ScreenCaptureKit as SCK
    from AppKit import NSBitmapImageRep

    # Touch the window server once before any ScreenCaptureKit call. SCK's async
    # path asserts (CGS_REQUIRE_INIT) if the CoreGraphics connection hasn't been
    # initialized yet, and a fresh subprocess that dives straight into SCK never
    # would; a throwaway CGWindowList query initializes it.
    Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)

    box: dict = {}
    done = threading.Event()

    def on_content(content, err):
        try:
            if err is not None:
                box["err"] = "SCShareableContent error: %s" % err
                done.set()
                return
            sc_win = next((w for w in content.windows()
                           if int(w.windowID()) == wid), None)
            if sc_win is None:
                box["err"] = "window %d not in shareable content" % wid
                done.set()
                return
            filt = SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(sc_win)
            scale = float(filt.pointPixelScale()) or 1.0
            cr = filt.contentRect()
            mul = scale if backing else 1.0
            cfg = SCK.SCStreamConfiguration.alloc().init()
            cfg.setWidth_(max(1, int(round(cr.size.width * mul))))
            cfg.setHeight_(max(1, int(round(cr.size.height * mul))))
            cfg.setShowsCursor_(False)

            def on_img(cgimg, err2):
                if err2 is not None:
                    box["err"] = "capture error: %s" % err2
                elif cgimg is None:
                    box["err"] = "capture returned no image"
                else:
                    box["img"] = cgimg
                done.set()

            SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                filt, cfg, on_img)
        except Exception as e:  # noqa: BLE001 — report to caller, never crash silently
            box["err"] = "exception: %r" % e
            done.set()

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(on_content)

    # Pump this (main) thread's run loop until the async chain resolves. SCK
    # delivers its completion blocks here; a plain Event.wait() would deadlock.
    deadline = 8.0
    while not done.is_set() and deadline > 0:
        Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, 0.05, True)
        deadline -= 0.05

    if "img" not in box:
        return _fail(box.get("err", "timed out waiting for ScreenCaptureKit"))

    rep = NSBitmapImageRep.alloc().initWithCGImage_(box["img"])
    data = rep.representationUsingType_properties_(4, None)  # 4 = PNG
    if data is None:
        return _fail("could not PNG-encode the capture")
    with open(out_path, "wb") as f:
        f.write(bytes(data))
    return 0


def main(argv) -> int:
    if len(argv) != 4:
        return _fail("usage: python -m highvisor._sckshot <wid> <backing:0|1> <out.png>")
    try:
        wid = int(argv[1])
        backing = argv[2] not in ("0", "", "false", "False")
    except ValueError:
        return _fail("bad arguments: %r" % (argv[1:],))
    return capture(wid, backing, argv[3])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
