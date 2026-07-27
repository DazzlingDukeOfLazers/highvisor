# highvisor

A local supervisor for the desktop UI layer. highvisor observes and controls
native desktop applications on Windows and macOS over a small, brain-agnostic
RPC — so a Claude/OpenAI/scripted "brain" can drive real apps to speed up and
focus debug loops.

Named after "hypervisor": it sits above the desktop and coordinates what runs on
it. See [`docs/`](docs/) for the full design.

## Status

Early. Windows backend is built and verified end-to-end; macOS is spec'd but not
yet implemented. See `docs/03-research-findings.md`.

## Architecture (one paragraph)

Four layers: **brain adapters** → **CLI/clients** → **core daemon** (RPC server +
single-threaded action queue) → **per-OS `PlatformBackend`** (observe + act).
Everything OS-specific lives behind the `PlatformBackend` seam. The daemon speaks
framed JSON over localhost TCP (`127.0.0.1:48720`). See
[`docs/01-architecture.md`](docs/01-architecture.md).

### Background control (the hard part)

highvisor tries to act on **unfocused** windows via a tier ladder, reporting which
tier actually delivered each action:

| tier | mechanism | focus? |
|------|-----------|--------|
| 1 | accessibility action (UIA pattern / AX action) | background, semantic |
| 2 | window message post (`WM_SETTEXT` / `PostMessage`) | background, syntactic |
| 3 | cooperative hook (target polls our channel) | background, opt-in |
| 4 | activate + global input (`SendInput` / `CGEvent`) | steals focus |

## Install

```
pip install -e .
```

Dependencies: `pillow`, plus `uiautomation` on Windows.

## Usage

Start the daemon:

```
python -m highvisor.server      # or: hvd
```

Drive it with the CLI client (`<target>` is a window ref — `hwnd:0x1a2b`,
`pid:1234`, or a title substring):

```
hv ping
hv ls
hv shot <target> [out.png]
hv text <target> <string...>
hv key <target> <keys>
hv activate <target>
hv inspect <target> [depth]
hv raw '{"op":"ping"}'
```

The protocol is dependency-free framed JSON (`highvisor/protocol.py`), so any
language can speak it in a few lines.
