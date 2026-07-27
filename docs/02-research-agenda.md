# highvisor — research agenda

These are the questions research must answer *before* we commit to a stack. The
architecture (`01`) is deliberately tooling-agnostic; the hardest requirement
(**background/unfocused control**) should decide the tooling. Findings land in
`03-research-findings.md`.

## Q1 — Background control feasibility, per OS (the decider)

For each OS, which delivery tiers actually work against an unfocused window, and
for which app classes do they fail?

- **Windows:**
  - `PostMessage`/`SendMessage` keyboard+mouse to a background HWND — which apps
    honor it, which ignore it (games/DirectInput, Chromium, hardware-accel)?
  - UI Automation control patterns (`InvokePattern`, `ValuePattern`,
    `LegacyIAccessible`) — do they act without focus? Coverage across app kinds
    (Win32, WPF, WinUI, Electron)?
  - UIPI/UAC: can a non-elevated daemon touch an elevated window at all?
- **macOS:**
  - AXUIElement actions (`AXPress`, `AXSetValue`, `kAXConfirmAction`) on a
    background app — reliability by app type (Cocoa vs Catalyst vs Electron)?
  - `CGEventPostToPid` vs `CGEventPost` — does pid-targeting deliver without
    activation? Known failure modes?
  - TCC: exact permissions (Accessibility, Screen Recording, Input Monitoring)
    and how to detect a missing grant programmatically.

## Q2 — Cross-platform automation libraries — build on, or DIY?

Evaluate each for: background support, accessibility-tree access, screenshot of a
specific window, cross-platform reach, maintenance/health, license, and how much
we'd have to drop to native FFI anyway.

- **DIY native** — Win32 + UIAutomation via FFI; macOS AX + CoreGraphics via FFI.
  Max control, max effort.
- **Python:** `pywinauto`, `uiautomation` (win), `pyobjc`/`atomacos`/`pyax`
  (mac), `pyautogui`/`pynput` (global input only), `mss` (screens).
- **.NET:** FlaUI / UIAutomation (excellent on Windows; weak/none on mac).
- **Node:** `nut.js`, `robotjs` (cross-platform input+screen, but *global* input;
  accessibility?).
- **Rust:** `enigo` (input), `xcap`/`screenshots` (capture), accessibility crates
  (immature?). Single-binary appeal for a daemon.
- **Java:** SikuliX / Robot Framework — image-based, heavy JVM, mostly global.
- **Browser tools** (`selenium`, `puppeteer`, `playwright`): confirm they are
  **web-only** and belong solely to the `chatgpt-web` brain adapter, not target
  control. Which is most robust for driving the ChatGPT web UI, and what are the
  ToS/stability risks of automating it?

## Q3 — Language / runtime for the daemon

Given Q1/Q2, which runtime lets us reach the native background APIs on *both* OSes
with the least glue?
- Python (fastest to prototype, matches existing `control.py` tooling, rich
  bindings both OSes — but packaging a daemon + native deps is fiddly).
- Rust (clean single binary, great FFI, weaker accessibility ecosystem).
- Node/TS (good if the browser brain dominates; global-input libs only).
- C#/.NET (best-in-class Windows, poor macOS parity).
- Split: native helper per OS + a common daemon core? Worth the seam?

## Q4 — Accessibility tree: is there any unified abstraction?

- Any lib that exposes a *single* tree API over UIA + AX? (Chromium's internal
  a11y, `AccessKit`, etc.) Or do we hand-roll a common `Element` model over two
  native trees?
- Cost/latency of walking a large tree; can we query by role/name without a full
  walk?

## Q5 — Screenshot of a specific / background / occluded window

- Windows: `PrintWindow` (incl. `PW_RENDERFULLCONTENT`), DWM thumbnails,
  Windows.Graphics.Capture — which handles GPU-accelerated + occluded windows?
- macOS: `CGWindowListCreateImage` vs ScreenCaptureKit (Sonoma+), by-window
  capture while unfocused; permission + performance.

## Q6 — Multi-brain arbitration ("both")

When Claude *and* OpenAI drive one session, how do proposed actions reconcile?
- Options to sketch: strict turn-taking, one proposes / one reviews, priority
  lanes, or last-writer-wins with a lock. (Design question, light research —
  prior art in pair-programming/agent-ensemble tooling.)

## Q7 — Prior art / competitors

Skim existing "AI controls the desktop" projects for architecture lessons and
pitfalls (not to copy, to avoid known dead-ends):
- OpenAI/Anthropic *computer use*, Open Interpreter OS mode, `self-operating-
  computer`, Microsoft UFO/OmniParser, `cua`/agent-computer frameworks, Sikuli.
- Specifically: how do they handle background windows (most don't — they take
  over the screen), and what's their permission story?

## Exit criteria

Research is done when `03-research-findings.md` can state, with citations:
1. A ranked recommendation for **language + core libraries**, justified primarily
   by Q1 (background control), secondarily by effort/maintenance.
2. A concrete per-OS plan for each PlatformBackend capability (observe + act),
   naming the specific API/lib for each tier.
3. A clear verdict on browser automation's role (brain transport only).
4. A shortlist of risks (permissions, app-class gaps) with mitigations, feeding a
   thin **Slice 0** spike: "screenshot + one key into ONE unfocused window on
   both OSes" — the smallest proof the whole premise holds.
