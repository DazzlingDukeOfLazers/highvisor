"""hv — the command-line client for the highvisor daemon.

A thin, dependency-free wrapper over the framed-JSON protocol. It never imports a
backend; it just opens a socket to the daemon and prints what comes back. Any
other language could reimplement this in a few lines (that's the point).

    hv ping
    hv ls
    hv shot <target> [out.png]
    hv text <target> <string...>
    hv key <target> <keys>
    hv activate <target>
    hv inspect <target> [depth]
    hv move <target> <zone | x y w h> [--topmost]
    hv screen
    hv raw '{"op":"ping"}'

<target> is a window ref: "hwnd:0x1a2b", "pid:1234", or a title substring.
"""
import argparse
import base64
import json
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
    _print_json(_call({"op": P.OP_KEY, "target": a.target, "keys": a.keys}))


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


def _cmd_screen(a):
    _print_json(_call({"op": P.OP_SCREEN}))


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
    s.set_defaults(fn=_cmd_key)

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
    s.add_argument("--topmost", action="store_true",
                   help="keep the window above non-topmost windows")
    s.set_defaults(fn=_cmd_move)

    sub.add_parser("screen").set_defaults(fn=_cmd_screen)

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
