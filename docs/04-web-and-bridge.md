# 04 — Web cockpit & cross-machine bridge

Two surfaces added on top of the localhost daemon (docs/01). Both share the one
`Engine` + one `EventBus`; neither widens the control surface.

## Web cockpit (localhost only)

`highvisor/web.py` serves a static vanilla-JS SPA (`highvisor/webui/`) over
`http.server` on **:48721** — no framework, no build step, so it never collides
with the Godot toolchain.

- `GET /` and assets — the cockpit page.
- `POST /rpc` — one RPC request dict → engine reply (the same ops the CLI speaks).
- `GET /events` — Server-Sent Events: the live log/event stream.
- `GET /bridge/peers`, `POST /bridge/send`, `POST /bridge/shot` — bridge control
  (present only when the bridge is running).

The cockpit is: window list (click → unfocused screenshot preview), a streaming
**onscreen log** (every op + every cross-machine message), a peers panel, and a
context-handoff box. Open it at <http://127.0.0.1:48721>.

The **EventBus** (`highvisor/events.py`) is a thread-safe pub/sub with a bounded
history ring so a late-joining browser replays the last N events. The engine
publishes a compact event per op (`ping`/`list` filtered out as noise).

## Bridge (LAN-facing, token-gated, DATA ONLY)

`highvisor/bridge.py` is the automated replacement for copy/pasting context
between machines. It is **not** the control port — it never exposes the ops that
drive your apps. It binds **:48722** on the LAN and speaks the same framed-JSON.

- **Discovery** is zero-config: each instance advertises `_highvisor._tcp.local.`
  over mDNS/zeroconf and browses for the others, so the Mac and PC find each
  other automatically on the same network.
- **Message types:** `context` (handoff text), `log` (mirror this machine's op
  log onto the peer's onscreen log — op/boot only, so no echo loop), `shot_req`
  → `shot_resp` (a peer may pull a screenshot of one of your windows, opt-in).
- **Files are out of scope** — those go through git.

### Pairing / trust

Every message must carry the shared token or it is refused (`secrets.compare_digest`).
The token lives at `~/.config/highvisor/token` (auto-generated, `0600`), or
`$HIGHVISOR_TOKEN` overrides it.

> The token is a **secret — keep it out of the git repo.** To pair the PC, set the
> same `$HIGHVISOR_TOKEN` on both machines (or copy the token file by hand). Do
> not commit it.

Disable the bridge entirely with `HIGHVISOR_BRIDGE=0`.

### Why the split is safe

The LAN only ever sees the bridge, and the bridge only moves data + (opt-in)
screenshots between token-paired machines. Controlling apps stays on the
localhost-only ports. A machine on your network without the token sees nothing.
