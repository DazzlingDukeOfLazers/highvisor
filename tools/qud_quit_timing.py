#!/usr/bin/env python3
"""Time Qud's quit-confirm chain from the bridge, so the `in_game -> title` edge stops guessing.

THE EDGE SENDS ITS ANSWERS BLIND ON A 1.2s TIMER. `{"command":"CmdQuit","answers":["Yes","No"]}`
pushes the command, sleeps 1.2s, answers Yes, sleeps 1.2s, answers No — with no check that either
confirm was ever on screen. Both sends report ok because a TCP write succeeded, which is why this
edge has produced three wrong diagnoses: the steps cannot fail, so the only thing that ever fails
is the verify, 45 seconds later, with no record of what happened in between.

The mod already knows: PopupBridge publishes {"type":"popup","active":true,…} to every connected
client the moment a modal goes up, and active:false when it goes away. So hold a client socket
open, send CmdQuit down the same socket, and timestamp the frames. That measures the ONE quantity
the fixed sleep assumes and never checks — how long Qud takes to raise each confirm — on a fresh
process and on an aged one, with no mod change and no screenshots.

    python3 tools/qud_quit_timing.py            # observe only: report popups as they appear
    python3 tools/qud_quit_timing.py --quit     # send CmdQuit, then time both confirms
    python3 tools/qud_quit_timing.py --quit --answer   # …and answer them AS THEY APPEAR

`--answer` is the experiment, not just a convenience: if answering on arrival succeeds where the
1.2s timer fails, the bug is the timer and nothing else. Answers go Yes (to "are you sure") then
No (to "save first?") — NEVER Yes to the save prompt, which would overwrite the fixture save every
parity capture is measured against.
"""
import argparse
import json
import socket
import struct
import sys
import time

PORT = 48710


def send(sock, obj):
    payload = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def frames(sock, deadline):
    """Yield decoded frames until `deadline`. Partial reads are buffered across recv calls."""
    buf = b""
    while time.time() < deadline:
        sock.settimeout(max(0.05, deadline - time.time()))
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            return
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        while len(buf) >= 4:
            n = struct.unpack(">I", buf[:4])[0]
            if len(buf) < 4 + n:
                break
            body, buf = buf[4:4 + n], buf[4 + n:]
            try:
                yield json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quit", action="store_true", help="send CmdQuit and time the confirms")
    ap.add_argument("--answer", action="store_true",
                    help="answer each confirm WHEN IT ARRIVES instead of on a timer")
    ap.add_argument("--seconds", type=float, default=30.0)
    a = ap.parse_args()

    # The answers, in order. Yes = "are you sure you want to quit"; No = "save first?".
    pending = ["Yes", "No"]

    with socket.create_connection(("127.0.0.1", PORT), timeout=5) as s:
        t0 = time.time()
        if a.quit:
            send(s, {"type": "command", "name": "command", "command": "CmdQuit"})
            print("%6.2fs  sent CmdQuit" % 0.0)
        seen = 0
        for f in frames(s, t0 + a.seconds):
            if f.get("type") != "popup":
                continue
            dt = time.time() - t0
            if not f.get("active"):
                print("%6.2fs  popup CLEARED (id=%s)" % (dt, f.get("id")))
                continue
            seen += 1
            btns = ",".join(b.get("command", "") for b in f.get("buttons") or [])
            print("%6.2fs  popup #%d %r  buttons=[%s]"
                  % (dt, seen, (f.get("message") or "")[:60], btns))
            if a.answer and pending:
                btn = pending.pop(0)
                send(s, {"type": "command", "name": "popup", "action": "button", "btn": btn})
                print("%6.2fs  answered %s" % (time.time() - t0, btn))
        if a.quit and seen == 0:
            print("\nNO POPUP EVER ARRIVED in %.0fs — CmdQuit did not reach a turn thread that "
                  "could raise the confirm. That is a different failure from answering too "
                  "early, and the edge's fixed sleep cannot tell them apart." % a.seconds)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
