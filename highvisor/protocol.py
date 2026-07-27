"""Wire protocol for the highvisor daemon.

Same framing as the raves-of-qud bridge (deliberately — it is simple, language
agnostic, and already battle-tested): each message is

    [4-byte big-endian length][UTF-8 JSON body]

Requests are ``{"op": "<name>", ...}``; responses are ``{"ok": bool, ...}``.
Screenshots ride back as base64 in ``png_b64`` (JSON has no bytes). Keep this
module dependency-free so any client language can reimplement it in a few lines.
"""
import json
import struct

HOST = "127.0.0.1"          # localhost only, single machine — never bind public
PORT = 48720                # arbitrary high port (raves used 48710; avoid clash)

# Op names the engine understands. Clients send op=<one of these>.
OP_PING = "ping"            # -> {ok, backend, version}
OP_LIST = "list_targets"    # -> {ok, targets:[Target]}
OP_SHOT = "screenshot"      # target -> {ok, png_b64, bytes, w, h}
OP_ACTIVATE = "activate"    # target -> ActionResult
OP_TEXT = "text"            # target, text -> ActionResult
OP_KEY = "key"              # target, keys -> ActionResult
OP_INSPECT = "inspect"      # target, depth -> {ok, tree}
OP_MOVE = "move"            # target, (zone | x,y,w,h), [topmost] -> ActionResult
OP_SCREEN = "screen_size"   # -> {ok, w, h}  (physical pixels of primary display)

MAX_FRAME = 64 * 1024 * 1024  # 64 MiB guard (a 4k screenshot fits easily)


def send_frame(sock, obj):
    """Serialize obj to JSON and write one length-prefixed frame."""
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_frame(sock):
    """Read one framed message; return the parsed dict, or None on clean EOF."""
    hdr = _recvn(sock, 4)
    if hdr is None:
        return None
    (n,) = struct.unpack(">I", hdr)
    if n > MAX_FRAME:
        raise ValueError("frame too large: %d bytes" % n)
    body = _recvn(sock, n)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))


def _recvn(sock, n):
    """Read exactly n bytes; None if the peer closed before n arrived."""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
