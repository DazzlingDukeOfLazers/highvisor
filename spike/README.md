# Slice 0 spike — "control an unfocused window, natively"

Smallest proof that highvisor's premise holds before building the engine:
**screenshot one specific window AND write into it while it is UNFOCUSED, via the
native/accessibility path, and verify the write landed.** (See
`../docs/03-research-findings.md` → "Recommended next step".)

## Results

| OS | status | tiers proven | capture |
|----|--------|--------------|---------|
| **Windows** | ✅ **PASS** (verified 2026-07-27, Win11, Python 3.11) | Tier 1 UIA `ValuePattern.SetValue` **OK**; Tier 2 `SendMessage(WM_SETTEXT)` **OK** | `PrintWindow(PW_RENDERFULLCONTENT)` 640×400, DPI-correct |
| **macOS** | ⏳ **untested** (written on Windows) | Tier 1 AX `SetAttributeValue(kAXValue)` | `CGWindowListCreateImage(IncludingWindow)` |

Windows evidence: the captured window shows the injected text with an **inactive**
title bar — the write happened while our console held the foreground
(`unfocused_before_write=True`, `unfocused_at_capture=True`).

## Run it

```bash
pip install uiautomation pillow            # Windows
python spike/win_slice0.py                 # -> spike/slice0_win_shot.png, prints a report, exit 0 = PASS

# macOS (needs a real Mac + TCC grants: Accessibility + Screen Recording)
pip install pyobjc-framework-Cocoa pyobjc-framework-Quartz pyobjc-framework-ApplicationServices
python spike/mac_slice0.py                 # -> spike/slice0_mac_shot.png
```

Each script spawns/opens its own target, moves focus away, drives the target
unfocused, captures it, reads the value back, and prints a per-tier report. Exit
0 iff (target was unfocused) AND (a write tier verifiably landed) AND (capture
succeeded).

## What we learned (feeds the daemon design)

- **Native accessibility is the real background path.** On Windows, UIA
  `ValuePattern.SetValue` writes to an unfocused window with no focus and no
  coordinates — tier 1 as predicted. `WM_SETTEXT` (tier 2) also works for a
  classic control with a child HWND.
- **The target choice exposed the WinUI/UWP gap immediately.** Modern Win11
  `notepad.exe` is a Store/WinUI app: the launch pid hands off to a broker (so
  `pid != window owner`) and the editor is a XAML `RichEditBox` with **no child
  HWND** and UIA that wants foreground. So the spike hosts its **own classic
  Win32 window** with a real `EDIT` child for a deterministic proof. *The daemon
  must expect per-app-class gaps* — the `03-findings` tier ladder + cooperative
  hook exist precisely for apps like modern Notepad, games, and Electron.
- **DPI awareness matters for capture.** Without `PER_MONITOR_AWARE_V2`,
  `GetWindowRect`/`PrintWindow` disagree on HiDPI and the capture is offset into a
  scaled buffer. The daemon's Windows backend must set DPI awareness at startup.
- **64-bit ctypes discipline:** every handle-returning / handle-taking Win32 call
  needs explicit `restype`/`argtypes` (`c_void_p`), or handles truncate to
  `c_int` and you get `OverflowError` / silent corruption. (Hit both while
  writing this.)
- **macOS caveats to verify on-device:** `CGWindowListCreateImage` returns black
  without the **Screen Recording** grant; AX read/write needs **Accessibility**;
  detect both via `AXIsProcessTrusted()` and a capture sanity check, and surface
  a precise "enable X" message instead of failing opaque.

## Not in scope for Slice 0 (deliberately)

Occluded (not just unfocused) capture on macOS via ScreenCaptureKit; global-input
tier-4 fallback; the cooperative-hook tier-3; multi-monitor coordinate mapping.
These land when the daemon + `PlatformBackend` interface get built.
