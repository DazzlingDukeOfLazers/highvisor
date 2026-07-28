"""hv — the command-line client for the highvisor daemon.

A thin, dependency-free wrapper over the framed-JSON protocol. It never imports a
backend; it just opens a socket to the daemon and prints what comes back. Any
other language could reimplement this in a few lines (that's the point).

    hv ping
    hv ls
    hv shot <target> [out.png]
    hv text <target> <string...>
    hv key <target> <keys> [--focus]
    hv click <target> <x> <y> [--right] [--double]
    hv activate <target>
    hv inspect <target> [depth]
    hv move <target> <zone | x y w h> [--topmost | --no-topmost]
    hv screen
    hv layouts
    hv layout <name>
    hv layout-save <name> [description...]
    hv diff <a.png> <b.png> [--out heat.png]
    hv zones <img.png> [--top N]
    hv peers
    hv parity <a> <b> [--peer-a NAME] [--peer-b NAME] [--out heat.png] [--size WxH]
    hv launch <name | spec>
    hv launchers
    hv launch-save <name> <spec>
    hv tunnel <host> [--user U] [--bridge] [--print]   (drive a remote highvisor over SSH)
    hv responsive <golem> <source> [--threshold P] [--out-dir DIR]
    hv raw '{"op":"ping"}'

<target> is a window ref: "hwnd:0x1a2b", "pid:1234", or a title substring.
"""
import argparse
import base64
import json
import os
import socket
import sys

from . import protocol as P


_HOST, _PORT = P.HOST, P.PORT  # set once from CLI args in main()


def _call(request: dict) -> dict:
    with socket.create_connection((_HOST, _PORT), timeout=30) as s:
        P.send_frame(s, request)
        resp = P.recv_frame(s)
    if resp is None:
        raise SystemExit("daemon closed the connection without replying")
    return resp


def _print_json(obj, strip=()):
    if isinstance(obj, dict) and strip:
        obj = {k: v for k, v in obj.items() if k not in strip}
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def _cmd_ping(a):
    _print_json(_call({"op": P.OP_PING}))


def _cmd_ls(a):
    resp = _call({"op": P.OP_LIST})
    if not resp.get("ok"):
        _print_json(resp)
        return 1
    for t in resp.get("targets", []):
        mark = "*" if t.get("focused") else " "
        print("%s %-16s pid=%-6d %4dx%-4d  %s"
              % (mark, t["id"], t["pid"], t["w"], t["h"], t["title"]))


def _cmd_shot(a):
    resp = _call({"op": P.OP_SHOT, "target": a.target})
    if not resp.get("ok"):
        _print_json(resp)
        return 1
    out = a.out or "shot.png"
    with open(out, "wb") as f:
        f.write(base64.b64decode(resp["png_b64"]))
    print("wrote %s (%d bytes)" % (out, resp.get("bytes", 0)))


def _cmd_text(a):
    _print_json(_call({"op": P.OP_TEXT, "target": a.target,
                       "text": " ".join(a.text)}))


def _cmd_key(a):
    _print_json(_call({"op": P.OP_KEY, "target": a.target, "keys": a.keys,
                       "focus": a.focus}))


def _cmd_click(a):
    _print_json(_call({"op": P.OP_CLICK, "target": a.target, "x": a.x, "y": a.y,
                       "button": "right" if a.right else "left", "double": a.double,
                       "hover": a.hover}))


def _cmd_activate(a):
    _print_json(_call({"op": P.OP_ACTIVATE, "target": a.target}))


def _cmd_inspect(a):
    _print_json(_call({"op": P.OP_INSPECT, "target": a.target, "depth": a.depth}))


def _cmd_move(a):
    req = {"op": P.OP_MOVE, "target": a.target, "topmost": a.topmost}
    if len(a.rect) == 1:
        req["zone"] = a.rect[0]
    elif len(a.rect) == 4:
        req["x"], req["y"], req["w"], req["h"] = (int(v) for v in a.rect)
    else:
        raise SystemExit("move needs either a zone name or x y w h")
    _print_json(_call(req))


def _cmd_stack(a):
    _print_json(_call({"op": P.OP_STACK, "top": a.top, "bottom": a.bottom, "gap": a.gap}))


def _cmd_dock(a):
    _print_json(_call({"op": P.OP_DOCK, "target": a.target}))


def _cmd_probe(a):
    req = {"op": P.OP_PROBE}
    if a.app:
        req["app"] = a.app
    if a.window:
        req["window"] = a.window
    if a.port is not None:
        req["port"] = a.port
    _print_json(_call(req))


def _cmd_screen(a):
    _print_json(_call({"op": P.OP_SCREEN}))


def _cmd_diff(a):
    # Local image analysis — no daemon round-trip.
    from . import imageops
    _print_json(imageops.diff(a.a, a.b, crop_top=a.crop_top, out=a.out))


def _cmd_zones(a):
    from . import imageops
    z = imageops.detect_zones(a.img, top=a.top)
    _print_json({"count": len(z), "zones": z})


def _cmd_responsive(a):
    # Orchestrates the daemon (screen/move/shot) + local diff; see responsive.py.
    from . import responsive
    report = responsive.run(_call, a.golem, a.source,
                            threshold=a.threshold, out_dir=a.out_dir)
    _print_json(report)
    return 0 if report["verdict"] == "PASS" else 1


def _cmd_layouts(a):
    resp = _call({"op": P.OP_LAYOUT_LIST})
    if not resp.get("ok"):
        _print_json(resp)
        return 1
    for l in resp.get("layouts", []):
        print("%-14s %2d  %s" % (l["name"], l["placements"], l.get("description", "")))


def _cmd_layout(a):
    resp = _call({"op": P.OP_LAYOUT_APPLY, "name": a.name})
    if not resp.get("ok") and "results" not in resp:
        _print_json(resp)
        return 1
    for r in resp.get("results", []):
        mark = "ok " if r.get("ok") else "MISS"
        print("  %s %-12s %s %s" % (mark, r.get("match", ""),
                                    r.get("title", r.get("target", "")),
                                    r.get("error") or ""))
    print("%d/%d placed" % (resp.get("applied", 0), len(resp.get("results", []))))
    return 0 if resp.get("ok") else 1


def _cmd_layout_save(a):
    _print_json(_call({"op": P.OP_LAYOUT_SAVE, "name": a.name,
                       "description": " ".join(a.description) if a.description else ""}))


def _cmd_launch(a):
    _print_json(_call({"op": P.OP_LAUNCH, "name": a.name}))


def _cmd_launchers(a):
    resp = _call({"op": P.OP_LAUNCH_LIST})
    if not resp.get("ok"):
        _print_json(resp)
        return 1
    launchers = resp.get("launchers") or {}
    for n, s in launchers.items():
        print("%-14s %s" % (n, s))
    if not launchers:
        print("(no launchers saved — hv launch-save <name> <spec>)")


def _cmd_launch_save(a):
    _print_json(_call({"op": P.OP_LAUNCH_SAVE, "name": a.name, "spec": a.spec}))


def _cmd_ocr(a):
    resp = _call({"op": P.OP_OCR, "target": a.target})
    if not resp.get("ok"):
        _print_json(resp)
        return 1
    if a.boxes:
        _print_json(resp)
    else:
        print(resp.get("text", ""))


def _cmd_peers(a):
    resp = _call({"op": P.OP_PEERS})
    if not resp.get("ok"):
        _print_json(resp)
        return 1
    me = resp.get("self") or {}
    print("self: %s@%s:%s" % (me.get("name"), me.get("host"), me.get("port")))
    for p in resp.get("peers", []):
        print("  %-16s %s:%s" % (p["name"], p["host"], p["port"]))
    if not resp.get("peers"):
        print("  (no peers discovered yet)")


def _capture(target, peer, path):
    """Write a PNG of ``target`` — from a peer over the bridge if ``peer`` set,
    else from the local daemon."""
    if peer:
        r = _call({"op": P.OP_PEER_SHOT, "peer": peer, "target": target})
    else:
        r = _call({"op": P.OP_SHOT, "target": target})
    if not r.get("ok") or not r.get("png_b64"):
        raise SystemExit("capture %s%s failed: %s"
                         % (peer + ":" if peer else "", target, r.get("error")))
    with open(path, "wb") as f:
        f.write(base64.b64decode(r["png_b64"]))
    return r.get("bytes", 0)


def _find_target(ref):
    """The current Target dict for a ref (id, or title/owner substring), or None."""
    low = ref.lower()
    for t in _call({"op": P.OP_LIST}).get("targets", []):
        if (t["id"] == ref or low in (t.get("title") or "").lower()
                or low in (t.get("class_name") or "").lower()):
            return t
    return None


def _resize_local(ref, w, h):
    """Resize a local window to w x h in place (keeps its current origin)."""
    t = _find_target(ref)
    if not t:
        raise SystemExit("resize: no local window matching %r" % ref)
    _call({"op": P.OP_MOVE, "target": t["id"], "x": t["x"], "y": t["y"],
           "w": w, "h": h, "topmost": False})


def _cmd_parity(a):
    """One-shot visual parity: capture A and B (each local or from a peer) and
    screenshot-diff them with imageops — no LLM in the loop."""
    import tempfile
    import time
    from . import imageops
    if a.size:
        try:
            sw, sh = (int(v) for v in a.size.lower().split("x"))
        except ValueError:
            raise SystemExit("--size must be WxH, e.g. 1920x1080")
        if not a.peer_a:
            _resize_local(a.a, sw, sh)
        if not a.peer_b:
            _resize_local(a.b, sw, sh)
        time.sleep(a.settle)   # let both apps repaint at the new size
    d = tempfile.mkdtemp(prefix="hv-parity-")
    ap, bp = os.path.join(d, "a.png"), os.path.join(d, "b.png")
    _capture(a.a, a.peer_a, ap)
    _capture(a.b, a.peer_b, bp)
    heat = a.out or os.path.join(d, "heat.png")
    res = imageops.diff(ap, bp, crop_top=a.crop_top, out=heat)
    res["a"] = (a.peer_a + ":" if a.peer_a else "") + a.a
    res["b"] = (a.peer_b + ":" if a.peer_b else "") + a.b
    res["captures"] = [ap, bp]
    _print_json(res)


def _parse_sizes(spec):
    out = []
    for tok in spec.split(","):
        tok = tok.strip().lower()
        if not tok:
            continue
        try:
            w, h = (int(v) for v in tok.split("x"))
        except ValueError:
            raise SystemExit("--sizes items must be WxH, got %r" % tok)
        out.append((w, h))
    if not out:
        raise SystemExit("--sizes is empty")
    return out


def _cmd_parity_sweep(a):
    """Resize A and B through several window sizes TOGETHER, capturing + diffing each,
    to see how a reconstruction tracks the source across shapes — e.g. Raves' menu vs
    Qud's own at 16:9 / square / portrait / ultrawide. Both sides should be showing
    the comparable screen (put Qud at its title). Writes per-size side-by-sides, diff
    heatmaps + scores, and one stacked sweep.png. Restores original sizes when done."""
    import os
    import time
    import tempfile
    from . import imageops
    sizes = _parse_sizes(a.sizes)
    outdir = a.out or tempfile.mkdtemp(prefix="hv-sweep-")
    os.makedirs(outdir, exist_ok=True)
    orig = {}
    if not a.peer_a:
        orig["a"] = _find_target(a.a)
    if not a.peer_b:
        orig["b"] = _find_target(a.b)
    results, cmps = [], []
    for (w, h) in sizes:
        if not a.peer_a:
            _resize_local(a.a, w, h)
        if not a.peer_b:
            _resize_local(a.b, w, h)
        time.sleep(a.settle)                       # let both apps relayout + repaint
        tag = "%dx%d" % (w, h)
        ap = os.path.join(outdir, tag + "_a.png")
        bp = os.path.join(outdir, tag + "_b.png")
        _capture(a.a, a.peer_a, ap)
        _capture(a.b, a.peer_b, bp)
        heat = os.path.join(outdir, tag + "_heat.png")
        d = imageops.diff(ap, bp, crop_top=a.crop_top, out=heat)
        cmp_path = os.path.join(outdir, tag + "_cmp.png")
        imageops.sidebyside(ap, bp, cmp_path, label_a="%s  %s" % (a.a, tag),
                            label_b="%s  %s" % (a.b, tag), height=a.height)
        cmps.append(cmp_path)
        results.append({"size": [w, h], "content_match": d["content_match"],
                        "full_match": d["full_match"], "compare": cmp_path, "heatmap": heat})
    sheet = os.path.join(outdir, "sweep.png")
    imageops.stack_vertical(cmps, sheet)
    if not a.no_restore:                           # put both windows back as they were
        for side, ref in (("a", a.a), ("b", a.b)):
            t = orig.get(side)
            if t:
                _call({"op": P.OP_MOVE, "target": t["id"], "x": t["x"], "y": t["y"],
                       "w": t["w"], "h": t["h"], "topmost": False})
    _print_json({"out": outdir, "sheet": sheet, "sizes": results})


def _cmd_tunnel(a):
    """SSH-tunnel a remote highvisor to this machine: forward the remote daemon +
    cockpit (+ optional bridge) to local ports over an encrypted SSH connection.
    Reuses the whole daemon/CLI unchanged — only the wire becomes SSH."""
    host = "%s@%s" % (a.user, a.host) if a.user else a.host
    fwds = [(a.control, P.PORT), (a.web, 48721)]          # remote 48721 = cockpit
    if a.bridge:
        fwds.append((a.bridge_port, P.BRIDGE_PORT))
    ssh = ["ssh", "-N"]
    for lp, rp in fwds:
        ssh += ["-L", "127.0.0.1:%d:127.0.0.1:%d" % (lp, rp)]
    ssh.append(host)
    print("→ tunnelling %s's highvisor here (encrypted). Needs sshd + key auth on it." % a.host)
    print("    control : hv --port %d <cmd>   (e.g. hv --port %d ls)" % (a.control, a.control))
    print("    cockpit : http://127.0.0.1:%d" % a.web)
    if a.bridge:
        print("    bridge  : 127.0.0.1:%d" % a.bridge_port)
    print("    ssh     : %s" % " ".join(ssh))
    if a.print_only:
        return
    print("  (holds the tunnel open until Ctrl-C)")
    os.execvp("ssh", ssh)


def _cmd_raw(a):
    _print_json(_call(json.loads(a.json)), strip=("png_b64",))


def build_parser():
    p = argparse.ArgumentParser(prog="hv", description="highvisor CLI client")
    p.add_argument("--host", default=P.HOST)
    p.add_argument("--port", type=int, default=P.PORT)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping").set_defaults(fn=_cmd_ping)
    sub.add_parser("ls").set_defaults(fn=_cmd_ls)

    s = sub.add_parser("shot")
    s.add_argument("target")
    s.add_argument("out", nargs="?")
    s.set_defaults(fn=_cmd_shot)

    s = sub.add_parser("text")
    s.add_argument("target")
    s.add_argument("text", nargs="+")
    s.set_defaults(fn=_cmd_text)

    s = sub.add_parser("key")
    s.add_argument("target")
    s.add_argument("keys")
    s.add_argument("--focus", action="store_true",
                   help="activate + HID-tap delivery (for Unity/games that ignore background keys)")
    s.set_defaults(fn=_cmd_key)

    s = sub.add_parser("click", help="click at window-relative x y (points)")
    s.add_argument("target")
    s.add_argument("x", type=int)
    s.add_argument("y", type=int)
    s.add_argument("--right", action="store_true", help="right-click")
    s.add_argument("--double", action="store_true", help="double-click")
    s.add_argument("--hover", action="store_true",
                   help="post a real mouseMoved first (needed for Qud's legacy popups)")
    s.set_defaults(fn=_cmd_click)

    s = sub.add_parser("activate")
    s.add_argument("target")
    s.set_defaults(fn=_cmd_activate)

    s = sub.add_parser("inspect")
    s.add_argument("target")
    s.add_argument("depth", nargs="?", type=int, default=3)
    s.set_defaults(fn=_cmd_inspect)

    s = sub.add_parser("move", help="reposition a window to a zone or x y w h")
    s.add_argument("target")
    s.add_argument("rect", nargs="+",
                   help="zone name (e.g. top-right) or four ints: x y w h")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--topmost", dest="topmost", action="store_true", default=None,
                   help="pin the window above non-topmost windows")
    g.add_argument("--no-topmost", dest="topmost", action="store_false",
                   help="clear the window's topmost bit")
    s.set_defaults(fn=_cmd_move)

    s = sub.add_parser("stack", help="stack one window directly above another (same column)")
    s.add_argument("top", help="window to place on top (title substring)")
    s.add_argument("bottom", help="anchor window it sits above (title substring)")
    s.add_argument("--gap", type=int, default=8, help="pixels between them (default 8)")
    s.set_defaults(fn=_cmd_stack)

    s = sub.add_parser("dock", help="apply a window's standing dock rule (see docks.py)")
    s.add_argument("target", help="window id or title substring")
    s.set_defaults(fn=_cmd_dock)

    s = sub.add_parser("probe", help="is an app up, and in what state? (e.g. hv probe --app qud)")
    s.add_argument("--app", help="known app profile (see apps.py): qud")
    s.add_argument("--window", help="window title substring (if not using --app)")
    s.add_argument("--port", type=int, default=None, help="state-indicating localhost port")
    s.set_defaults(fn=_cmd_probe)

    sub.add_parser("screen").set_defaults(fn=_cmd_screen)

    s = sub.add_parser("diff", help="score two captures + write a heatmap")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--out", default=None, help="write amplified diff heatmap here")
    s.add_argument("--crop-top", type=int, default=58, dest="crop_top",
                   help="px of OS chrome to skip for the content score")
    s.set_defaults(fn=_cmd_diff)

    s = sub.add_parser("zones", help="detect saturated colour rectangles")
    s.add_argument("img")
    s.add_argument("--top", type=int, default=0, help="px of OS chrome to skip")
    s.set_defaults(fn=_cmd_zones)

    s = sub.add_parser("responsive",
                       help="deterministic responsive-parity test: golem vs source")
    s.add_argument("golem", help="window ref for the generated/candidate window")
    s.add_argument("source", help="window ref for the reference window")
    s.add_argument("--threshold", type=float, default=99.0,
                   help="min content-match %% to pass a frame (default 99.0)")
    s.add_argument("--out-dir", default=None, dest="out_dir",
                   help="where to write per-frame captures + heatmaps")
    s.set_defaults(fn=_cmd_responsive)

    sub.add_parser("layouts", help="list known window layouts").set_defaults(fn=_cmd_layouts)

    s = sub.add_parser("layout", help="apply a named window layout")
    s.add_argument("name")
    s.set_defaults(fn=_cmd_layout)

    s = sub.add_parser("layout-save", help="snapshot the current arrangement as a layout")
    s.add_argument("name")
    s.add_argument("description", nargs="*")
    s.set_defaults(fn=_cmd_layout_save)

    s = sub.add_parser("launch", help="start a program by launcher name or raw spec")
    s.add_argument("name", help="a saved launcher name, or an OS spec (steam://…, /path.app, App Name)")
    s.set_defaults(fn=_cmd_launch)

    sub.add_parser("launchers", help="list saved launchers").set_defaults(fn=_cmd_launchers)

    s = sub.add_parser("launch-save", help="save a named launcher (name -> spec)")
    s.add_argument("name")
    s.add_argument("spec")
    s.set_defaults(fn=_cmd_launch_save)

    s = sub.add_parser("ocr", help="recognize text in a window (Vision) — read AX-opaque apps")
    s.add_argument("target")
    s.add_argument("--boxes", action="store_true", help="include bounding boxes as JSON")
    s.set_defaults(fn=_cmd_ocr)

    sub.add_parser("peers", help="list discovered bridge peers").set_defaults(fn=_cmd_peers)

    s = sub.add_parser("parity",
                       help="capture two windows (local or --peer) and screenshot-diff them")
    s.add_argument("a", help="window ref for side A")
    s.add_argument("b", help="window ref for side B")
    s.add_argument("--peer-a", dest="peer_a", default=None,
                   help="capture A from this bridge peer instead of locally")
    s.add_argument("--peer-b", dest="peer_b", default=None,
                   help="capture B from this bridge peer instead of locally")
    s.add_argument("--out", default=None, help="write the diff heatmap here")
    s.add_argument("--crop-top", type=int, default=58, dest="crop_top",
                   help="px of chrome to skip for the content score")
    s.add_argument("--size", default=None,
                   help="resize both local sides to WxH before capturing (e.g. 1920x1080)")
    s.add_argument("--settle", type=float, default=0.4,
                   help="seconds to wait after resizing for repaint (default 0.4)")
    s.set_defaults(fn=_cmd_parity)

    s = sub.add_parser("parity-sweep",
                       help="resize two windows through several sizes together, diffing each")
    s.add_argument("a", help="window ref for side A (e.g. 'Raves of Qud')")
    s.add_argument("b", help="window ref for side B (e.g. 'CavesOfQud')")
    s.add_argument("--sizes", default="1920x1080,1280x720,2560x1080,1000x1000,1080x1350",
                   help="comma list of WxH to sweep (default covers 16:9/wide/square/portrait)")
    s.add_argument("--peer-a", dest="peer_a", default=None, help="capture A from this bridge peer")
    s.add_argument("--peer-b", dest="peer_b", default=None, help="capture B from this bridge peer")
    s.add_argument("--out", default=None, help="output dir for captures + sweep.png")
    s.add_argument("--crop-top", type=int, default=58, dest="crop_top",
                   help="px of chrome to skip for the content score")
    s.add_argument("--settle", type=float, default=1.2,
                   help="seconds to wait after each resize (default 1.2)")
    s.add_argument("--height", type=int, default=520, help="per-row height in the sheet")
    s.add_argument("--no-restore", action="store_true", dest="no_restore",
                   help="leave windows at the last size instead of restoring originals")
    s.set_defaults(fn=_cmd_parity_sweep)

    s = sub.add_parser("tunnel",
                       help="SSH-tunnel a remote highvisor to this machine (encrypted)")
    s.add_argument("host", help="ssh host or user@host (remote needs sshd + key auth)")
    s.add_argument("--user", default=None, help="ssh user (if not in the host)")
    s.add_argument("--control", type=int, default=48730,
                   help="local port for the remote control daemon (default 48730)")
    s.add_argument("--web", type=int, default=48731,
                   help="local port for the remote cockpit (default 48731)")
    s.add_argument("--bridge", action="store_true", help="also forward the bridge port")
    s.add_argument("--bridge-port", type=int, default=48732, dest="bridge_port")
    s.add_argument("--print", dest="print_only", action="store_true",
                   help="show the ssh command instead of running it")
    s.set_defaults(fn=_cmd_tunnel)

    s = sub.add_parser("raw")
    s.add_argument("json")
    s.set_defaults(fn=_cmd_raw)

    return p


def main(argv=None):
    # Window titles are arbitrary Unicode; the Windows console is often cp1252.
    # Reconfigure to UTF-8 with replacement so printing never crashes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    global _HOST, _PORT
    _HOST, _PORT = args.host, args.port
    try:
        return args.fn(args) or 0
    except ConnectionRefusedError:
        print("cannot reach daemon on %s:%d — is it running? (python -m highvisor.server)"
              % (args.host, args.port), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
