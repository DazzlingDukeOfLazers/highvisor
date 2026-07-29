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

### Coordinates are points — but a screenshot may not be

`click` x/y are window **points** (logical), added to the window origin. A `shot` PNG,
though, comes back at the window's **backing scale**, which is not always 1×: a full-size
Raves/Qud window returned pixel-for-point (1:1) this session, but a *smaller* window came
back at **2×** (shot px = 2× points). So don't read coordinates straight off a screenshot
assuming px == points — compare the `shot` dimensions to the window's `ls` W×H and divide
by the ratio before clicking. Driving targets at **full window size** kept it 1:1 and clicks
landed first try; a mis-scaled coordinate silently hits the wrong control.

## Practical guidance (which tier for which target)

| target | keyboard | mouse | notes |
|---|---|---|---|
| Native AppKit / standard apps | tier 1–2, or `--focus` | click works | a11y usually present |
| Qud **Unity UI / title menu** | **none works** | **bare first, escalate to `--hover`** | no AX tree; keys dropped. Bare has worked, but the title-menu items have also needed `--hover` when a bare click didn't move the highlight — verify per session |
| Qud **legacy console popup** (Load-game picker, in-game ☰, "[Space]" prompt) | none works | **`--hover`** | reads `Input.mousePosition`; needs the pre-move a bare warp doesn't provide |
| Qud **in-game world cell** | none works | **bare click, NEVER `--hover`** | a pre-move makes Qud hover-but-never-*select* the tile |
| Godot apps (our own) | prefer the mod/command channel (tier 3) | click works | cooperative hook is cleanest |

The tension between "no pre-move" (above) and "`--hover` was required" is real and **surface-specific**, not
a contradiction: bare wins for plain Unity buttons and world cells (a pre-move gets rejected), while
`--hover` — a real `mouseMoved` before the bare click — is what the legacy popups (and sometimes the title
menu) need. When unsure, try bare, screenshot, and escalate to `--hover` only if the highlight didn't move.

**Rule of thumb for closed engines: drive them by mouse, not keys.** Position with a
cursor warp, click with a bare down/up, and never decorate the event.

## Recipe: Caves of Qud, title → in-game

Qud's bridge (the mod socket on **48710**) only opens once a save is loaded, so an
automated loop has to walk the pre-game menus by mouse first:

1. `shot` the Qud window and confirm the title menu is up.
2. `click --hover` **Continue**. Bare clicks drive Unity *buttons*, but the title-menu
   *items* did not reliably select on a bare click this session (the highlight never moved
   to the clicked item) — so **escalate to `--hover`** if a bare click leaves the selection
   unchanged.
3. The **Load Game** picker (a legacy console popup) appears — `click --hover` the save row.
4. Poll `nc -z 127.0.0.1 48710` until it opens = in-game. From here, prefer the mod's
   command channel (tier 3) over more clicking.

Verified end-to-end this session: title → Continue → Load picker → save row → bridge up,
entirely via `hv click --hover`.
