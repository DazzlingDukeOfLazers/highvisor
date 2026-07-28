# 05 — Driving input (keyboard & mouse), and the synthetic-click gotcha

How highvisor delivers input to a target, the tier ladder it climbs, and the
hard-won detail that makes clicks land in engines (Unity/Qud) that drop everything
else. Dated 2026-07-28, verified on macOS against Caves of Qud (Unity, build
2.0.211.59) and Godot apps.

## The tier ladder (recap)

Every act reports which delivery path actually worked (`ActionResult.tier`), so we
learn each app's real capabilities:

1. **accessibility action** — UIA pattern / AX `AXPress`/`AXSetValue`. Background,
   semantic, the only *reliable* unfocused path. Requires the app to expose an a11y
   tree.
2. **window message post** — `PostMessage` (Win) / `CGEventPostToPid` (mac).
   Background, syntactic, per-app-flaky.
3. **cooperative hook** — the app polls our command channel (the generalized
   `godot_cmd` trick). Background, opt-in, for targets we own the source of.
4. **activate + global input** — focus-stealing synthetic keyboard/mouse. Last
   resort, but the only thing that works for many closed-source engines.

## Keyboard

- `key(target, keys)` posts at **tier 2** (`CGEventPostToPid` / `PostMessage`).
- `key(target, keys, focus=True)` — `hv key … --focus` — is **tier 4**: activate,
  then post to the HID event tap (`kCGHIDEventTap`) / `SendKeys`. Use it for apps
  that ignore background key posts.

**Finding: Unity (Caves of Qud) ignores synthetic keyboard entirely** — even tier
4 with a `kCGEventSourceStateHIDSystemState` source. Qud also exposes **no
accessibility tree** for its menu (only window-control buttons), so there is *no*
key or AX path into its menus. Qud only takes keys via the **mod's in-game command
injection** (`Keyboard.PushCommand`), which does not exist pre-game (menus /
character creation).

## Mouse — and the click-state gotcha

`click(target, x, y, button, double)` — `hv click <target> <x> <y>` — coordinates
are **window-relative** (added to the window's screen origin). It:

1. **Warps the real OS cursor** to the point (`CGWarpMouseCursorPosition`). Unity
   reads the *actual* cursor position for hover, so the warp is what makes the
   hover highlight follow — a synthetic move event alone does not.
2. Posts a **bare `LeftMouseDown` / `LeftMouseUp` pair** from a
   `kCGEventSourceStateHIDSystemState` source to `kCGHIDEventTap`.

**The gotcha (this is the whole point of the doc):** the click only registers as a
real selection in Unity if the event is *minimal*:

- **Do NOT set `kCGMouseEventClickState`.** Setting it (even to 1) makes Qud drop
  the click — it highlights on hover but never activates.
- **Do NOT post a pre-move `kCGEventMouseMoved`.** The warp already positions the
  cursor; an extra move event also causes Qud to reject the click.

With the field set / pre-move posted: hover works, selection silently fails. With a
bare down/up: the click selects. This matches a known-good auto-clicker exactly —
[othyn/macos-auto-clicker](https://github.com/othyn/macos-auto-clicker)
(`AutoClickSimulator.swift`): HID-system-state source, `.cghidEventTap`, plain
`CGEvent(mouseEventSource:mouseType:mouseCursorPosition:mouseButton:)` down then up,
no click-state, no move. Verified: warp to a menu item + bare click **opened Qud's
Options screen** — a genuine activation.

Windows equivalent: `SetCursorPos` + `mouse_event(down|up)` after `activate`.

## Practical guidance (which tier for which target)

| target | keyboard | mouse | notes |
|---|---|---|---|
| Native AppKit / standard apps | tier 1–2, or `--focus` | click works | a11y usually present |
| **Unity (Caves of Qud) menus** | **none works** | **bare click (this doc)** | no AX tree; keys dropped |
| Godot apps (our own) | prefer the mod/command channel (tier 3) | click works | cooperative hook is cleanest |
| In-game Qud | the mod's `PushCommand` (tier 3) | — | menus/char-creation are pre-game, mouse-only |

**Rule of thumb for closed engines: drive them by mouse, not keys.** Position with a
cursor warp, click with a bare down/up, and never decorate the event.
