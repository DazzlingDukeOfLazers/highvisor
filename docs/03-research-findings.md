# highvisor research — desktop automation & background window control (macOS & Windows)

Answers to `02-research-agenda.md`, with the reasoning that leads to a stack.
Sources are listed at the bottom. Dated 2026-07-27.

## Decisions (conclusion)

| decision | choice | why | current status |
|---|---|---|---|
| Runtime | **Python** | best verified coverage for V1 — one runtime reaches Win UIA + macOS AX; matches `control.py` | implemented (both backends) |
| Transport | **framed JSON / TCP** | dependency-free localhost RPC any language can speak | implemented |
| Background action | **AX / UIA first**, then message-post → cooperative hook → global input | semantic + target-specific; degrade only when needed | **partial by app** — verify delivery per target |
| Capture | per-window OS capture | grabs a specific **unfocused** window | **implemented:** macOS `CGWindowListCreateImage` (verified); Windows `PrintWindow(PW_RENDERFULLCONTENT)` (window) + `BitBlt` (full screen). ScreenCaptureKit / DWM thumbnails / `Windows.Graphics.Capture` are **researched alternatives, not wired** |

**The evidence below varies in strength** — official API docs, third-party reports, and highvisor's own
live verification. Treat fast-moving product claims (e.g. Codex / computer-use tooling) as **industry
signal, not implementation evidence**; the load-bearing conclusions are the ones we verified.

## TL;DR

- **Background/unfocused control is only reliable through the OS *accessibility*
  layer** (Windows UI Automation patterns; macOS AXUIElement actions). Every
  *global* input library (nut.js, robotjs, enigo, pyautogui, pynput) synthesizes
  input to whatever has focus — they are **foreground-only** and can only ever be
  the last-resort tier.
- **No single library unifies the accessibility *client* side across OSes.**
  (AccessKit looks like it would, but it's the *provider* side — it helps a UI
  toolkit *expose* accessibility, not drive *other* apps.) We hand-roll one
  `Element` model over two native trees.
- **Python is the recommended runtime for the V1 daemon** — it gave the best verified coverage here: one
  runtime reaches *native accessibility* on **both** OSes (`pywinauto`/`uiautomation` → Windows UIA;
  `pyobjc`/`atomacos` → macOS AX), and it matches the existing `control.py` tooling. Drop to a native
  helper (Swift / C#) per-OS later only if perf or permissions force it.
- *(Market signal, not implementation evidence — see the market-notes below.)* Contemporary
  computer-use agents also moved from pixel screenshots toward window-focused accessibility trees, which
  is consistent with our accessibility-first, background-capable design being the right
  frontier — and Windows background will be the harder half.

## Q1 — Background control feasibility (the decider)

**Windows**
- **UI Automation patterns** (`InvokePattern.Invoke`, `ValuePattern.SetValue`,
  `TogglePattern`, `LegacyIAccessible`) are the background-friendly path.
  Critically: pattern methods may auto-set focus, but "setting focus does **not**
  bring the element to the foreground or make it visible," and
  `IUIAutomation2::AutoSetFocus` can be turned **off**. So UIA can act on a
  background window semantically, without stealing the screen. → **Tier 1.**
- **`PostMessage`/`SendMessage`** to a background HWND works for *some* apps but
  is flaky and app-dependent: naked `WM_KEYDOWN` loses modifier state, ALT/accel
  routes through `WM_SYSKEYDOWN`, and GPU/DirectInput apps ignore it. Sending
  `WM_COMMAND` for a known command id is the reliable sub-case. → **Tier 2, use
  sparingly.**
- **Gaps:** UWP/"Metro" apps require foreground for UIA; Electron/Chromium expose
  a11y only when enabled (`--force-renderer-accessibility`); a non-elevated
  daemon **cannot** message an elevated window (UIPI/UAC). Games = tier 3/4.

**macOS**
- **AXUIElement actions** (`performAction("AXPress")`, `AXSetValue`, confirm
  actions) act on **another app's** elements **without** bringing it frontmost —
  proven by Hammerspoon's `hs.axuielement` and usable directly from Python via
  `pyobjc` / `atomacos`. → **Tier 1, and cleaner than Windows here.**
- **`CGEventPostToPid`** targets a pid; effectiveness varies and often still
  wants activation. → **Tier 2.**
- **Caveats:** TCC gates everything — **Accessibility** gates inspection **and** synthetic control (detect
  via `AXIsProcessTrusted()`); **Screen Recording** gates capture. (Input Monitoring governs *observing*
  input, which highvisor doesn't do — don't add it as a requirement unless a current backend op proves it
  necessary.) Some app queries fail with `.cannotComplete` even when trusted; `activate` semantics changed
  in macOS 14 (`activate(options:)` deprecated → yield-activation model).

**Verdict:** accessibility-first on both OSes; message-posting as a per-app
tier-2; `activate + global input` as tier-4; **cooperative hook** (tier 3) for
apps we own the source of.

## Q5 — Screenshot of a specific / background window

- **Windows:** `PrintWindow(hwnd, …, PW_RENDERFULLCONTENT)` or
  `Windows.Graphics.Capture` (handles GPU-accelerated windows); DWM thumbnails
  for occluded windows.
- **macOS:** **ScreenCaptureKit** `SCContentFilter(desktopIndependentWindow:)`
  captures a single window even if not frontmost (needs Screen Recording
  permission; restart after first grant); legacy `CGWindowListCreateImage`
  as fallback on older OSes.
- Both can capture unfocused/occluded windows — observation is *easier* than
  action for the background case.
- **Shipped choice:** highvisor implements the *legacy/simplest* per-window path — macOS
  `CGWindowListCreateImage`, Windows `PrintWindow(PW_RENDERFULLCONTENT)` (+ `BitBlt` for full screen). The
  modern options above (ScreenCaptureKit, `Windows.Graphics.Capture`, DWM thumbnails) are researched
  swap-ins behind the backend seam, not yet wired.

## Q2/Q3 — Libraries and language

| Option | Native a11y (background) | Both OSes | Notes |
|---|---|---|---|
| **Python:** pywinauto/uiautomation + pyobjc/atomacos | ✅ UIA **and** AX | ✅ | Best coverage; matches control.py; packaging a daemon w/ native deps is the pain. **Recommended.** |
| .NET / FlaUI | ✅ UIA only | ❌ (Win) | Best-in-class Windows; no macOS. |
| Node / nut.js, robotjs | ❌ global input only | ✅ | Foreground only; good for tier-4 + cross-platform capture. |
| Rust / enigo + xcap | ❌ global input only | ✅ | Single binary, but a11y = hand-write UIA+AX FFI now (AccessKit is provider-side). High effort. |
| Java / SikuliX, Robot Framework | image-based, global | ✅ | Heavy JVM; no semantic background. |

**Recommendation:** **Python** core daemon for V1 — the strongest verified fit here: it reaches *native
accessibility* (the most reliable background path) on **both** Windows and macOS without writing FFI from
scratch. Accept the packaging cost;
keep a clean `PlatformBackend` seam so a per-OS native helper (Swift for AX +
ScreenCaptureKit, C#/FlaUI for UIA) can replace the Python backend later if
latency or permissions demand it — without touching the RPC vocabulary.

## Q4 — Unified accessibility tree?

No client-side unifier exists. **AccessKit** is the *server/provider* side
(toolkits expose a tree). So highvisor defines its own `Element` model and maps
it from UIA (`AutomationElement`, control patterns) and AX (`AXUIElement`
attributes/actions). Query by role/name to avoid full-tree walks (both APIs
support conditional/scoped search).

## Q7 — Prior art (what to copy / avoid)

- **OpenAI Codex computer use (2026):** mac = **background, parallel agents via
  a11y tree**; windows = **foreground takeover**. Validates a11y-over-pixels and
  shows background-on-Windows is the unsolved-hard part. Also: hybrid "background
  mode" uses CDP for web + terminal for system tasks, and **"can't interact with
  native GUI apps"** in background — exactly the gap highvisor targets.
- **Most open agents** (self-operating-computer, Open Interpreter OS mode,
  UFO/OmniParser) **take over the foreground** and drive by pixels/global input.
  highvisor's differentiator is background + accessibility + cooperative hook.

## Browser automation verdict (selenium / puppeteer / playwright)

**Web-only. Not for desktop targets.** Its sole role in highvisor is the
`chatgpt-web` **brain transport** (driving the ChatGPT web UI when using OpenAI
without the API). Prefer **Playwright** over selenium/puppeteer for robustness
and cross-browser support. Flag: automating a logged-in ChatGPT web session is
brittle and likely against ToS — treat as a convenience adapter, not a
dependency; the OpenAI API adapter should be the real path.

## Q6 — Multi-brain arbitration ("both")

V1: simplest safe thing — a **per-session turn-taking lock**; one brain holds the
action token at a time, the other observes. Defer proposer/reviewer or priority
lanes until there's a real use case.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| macOS TCC blocks AX/capture silently | Detect `AXIsProcessTrusted()` + screen-recording grant on startup; emit a precise "enable X in Settings" error, never fail opaque. |
| Windows UAC: can't touch elevated windows | Detect integrity mismatch; document; offer "run daemon elevated" opt-in. |
| App class ignores UIA + PostMessage (games, DirectInput) | Fall to tier-3 cooperative hook (generalized `godot_cmd`) or tier-4 activate+global. Report the tier so the brain adapts. |
| Electron/Chromium a11y off | Detect empty tree; document `--force-renderer-accessibility`; fall back to pixels+global. |
| Python daemon packaging (native deps, two OSes) | Pin to a venv/PyInstaller build per OS; keep backend behind an interface so a native helper can replace it. |

## Spike result & the next research question

The Slice-0 spike ("screenshot one specific window + deliver one input to it while UNFOCUSED, both OSes,
via the native path") is **complete** — the daemon, backends, and per-app tier matrix were built on it. What
this doc should now carry is the *evidence of record*, not the plan:

> **Record the actual apps + OS versions and which capture/input tiers were verified.** macOS was verified
> on this machine (2026-07-28) against Caves of Qud (Unity) and Godot — per-window `CGWindowListCreateImage`
> capture unfocused, AX actions tier 1, and the bare-click path into Unity. Windows code paths
> (`PrintWindow`/`BitBlt`, UIA) are **implemented**; mark each capability "verified" only with the tested
> Windows version + app + date.

The next research question is therefore **the first still-unverified capability** (e.g. Windows background
input against a real app, or a GPU-accelerated-window capture that `PrintWindow` returns black for) — not
rebuilding Slice 0. Where Windows background input proves as hard as the foreground-only prior art suggests,
lean on the **cooperative hook** for owned targets (Godot) and accept activate+global for the rest — and say
so explicitly in scope.

---

## Sources

- [SendMessage/PostMessage to non-focus window (Cheat Engine forum)](https://www.cheatengine.org/forum/viewtopic.php?t=459358)
- [Send keystroke to another app without focus (Microsoft Q&A)](https://learn.microsoft.com/en-us/answers/questions/496305/send-keystroke-to-another-app-without-focus)
- [Send inputs to background/minimized windows (LearnCodeByGaming)](https://learncodebygaming.com/blog/how-to-send-inputs-to-multiple-windows-and-minimized-windows-with-python)
- [InvokePattern Class (Microsoft Learn)](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.invokepattern?view=windowsdesktop-8.0)
- [ValuePattern.SetValue (Microsoft Learn)](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.valuepattern.setvalue?view=windowsdesktop-9.0)
- [IUIAutomation2::get_AutoSetFocus (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-get_autosetfocus)
- [Python-UIAutomation-for-Windows (yinkaisheng)](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows)
- [pywinauto (GitHub)](https://github.com/pywinauto/pywinauto)
- [Hammerspoon hs.axuielement docs](https://www.hammerspoon.org/docs/hs.axuielement.html)
- [PyObjC Accessibility API notes](https://pyobjc.readthedocs.io/en/latest/apinotes/Accessibility.html)
- [atomacos (oa-atomacos, PyPI)](https://pypi.org/project/oa-atomacos)
- [Quick Tip: Controlling macOS with Python (SitePoint)](https://www.sitepoint.com/quick-tip-controlling-macos-with-python/)
- [Capturing screen content in macOS — ScreenCaptureKit (Apple)](https://developer.apple.com/documentation/ScreenCaptureKit/capturing-screen-content-in-macos)
- [NSRunningApplication activate behavior change, Big Sur+ (Apple Forums)](https://developer.apple.com/forums/thread/668913)
- [AccessKit — how it works (provider side)](https://accesskit.dev/how-it-works/)
- [nut.js](https://nutjs.dev/) / [nut.js philosophy](https://nutjs.dev/docs/philosophy)
- [Codex Computer Use on Windows: foreground takeover (TechTimes)](https://www.techtimes.com/articles/317531/20260601/openai-codex-computer-use-now-windows-foreground-takeover-europe-excluded.htm)
- [AI agent runs silently in the background — pixels→a11y tree, 62%→80% (BigGo)](https://finance.biggo.com/news/3f5df03ea8db45b7)
- [Codex background computer use: how desktop agents work (BuildMVPFast)](https://www.buildmvpfast.com/blog/openai-codex-background-computer-use-desktop-agent-2026)
