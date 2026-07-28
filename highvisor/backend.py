"""PlatformBackend — the interface every OS backend implements, plus the small
data model the RPC speaks in.

Everything hard about highvisor lives behind this seam (see docs/01-architecture
.md). The engine only ever talks to a PlatformBackend; the Windows and (future)
macOS backends are the only OS-aware code. Keep this file OS-neutral.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Target:
    """A controllable thing — an app window. ``id`` is a stable-ish string the
    client passes back to address it (we use ``hwnd:0x....`` on Windows)."""
    id: str
    kind: str            # "window"
    pid: int
    title: str
    class_name: str
    x: int
    y: int
    w: int
    h: int
    focused: bool
    visible: bool
    path: str = ""        # app bundle/executable path, when the OS exposes it —
                          # so highvisor can report how to relaunch what it sees

    def to_dict(self):
        return self.__dict__.copy()


@dataclass
class Element:
    """A node from the accessibility tree (``inspect``)."""
    role: str
    name: str = ""
    value: str = ""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    actions: List[str] = field(default_factory=list)
    children: List["Element"] = field(default_factory=list)

    def to_dict(self):
        return {
            "role": self.role, "name": self.name, "value": self.value,
            "bounds": [self.x, self.y, self.w, self.h],
            "actions": self.actions,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class ActionResult:
    """Result of an act. ``tier`` records WHICH delivery path actually worked, so
    the caller (and we) learn each app's real capabilities:
        1 = accessibility action (UIA pattern / AX action)  — background, semantic
        2 = window message post (WM_SETTEXT / PostMessage)   — background, syntactic
        3 = cooperative hook (target polls our channel)      — background, opt-in
        4 = activate + global input (SendInput / CGEvent)    — steals focus
    """
    ok: bool
    tier: Optional[int] = None
    detail: str = ""
    error: Optional[str] = None

    def to_dict(self):
        return {"ok": self.ok, "tier": self.tier,
                "detail": self.detail, "error": self.error}

    @classmethod
    def fail(cls, error):
        return cls(ok=False, tier=None, error=str(error))


class BackendError(Exception):
    """Raised for addressable, client-facing failures (bad target, etc.)."""


# Named half/quadrant zones of the primary display, resolved against its physical
# size. This is the vocabulary the Ersatz layout uses ("Godot top-right over the
# slurped program bottom-right"). OS-neutral on purpose — the engine computes the
# rect from the backend's screen_size() and hands the backend plain pixels.
ZONES = ("full", "left", "right", "top", "bottom",
         "top-left", "top-right", "bottom-left", "bottom-right")


def zone_rect(zone: str, screen_w: int, screen_h: int):
    """Return (x, y, w, h) in physical pixels for a named ZONES entry."""
    hw, hh = screen_w // 2, screen_h // 2
    table = {
        "full":         (0, 0, screen_w, screen_h),
        "left":         (0, 0, hw, screen_h),
        "right":        (hw, 0, hw, screen_h),
        "top":          (0, 0, screen_w, hh),
        "bottom":       (0, hh, screen_w, hh),
        "top-left":     (0, 0, hw, hh),
        "top-right":    (hw, 0, hw, hh),
        "bottom-left":  (0, hh, hw, hh),
        "bottom-right": (hw, hh, hw, hh),
    }
    if zone not in table:
        raise BackendError("unknown zone %r (known: %s)" % (zone, ", ".join(ZONES)))
    return table[zone]


class PlatformBackend:
    """Abstract per-OS backend. Subclasses implement all methods. Every method
    runs on the engine's single worker thread (see engine.py), so implementations
    need not be thread-safe, but MUST be reasonably fast / non-blocking."""

    name = "abstract"

    def thread_init(self):
        """Called once on the worker thread before any op (e.g. COM init)."""

    def list_targets(self) -> List[Target]:
        raise NotImplementedError

    def launch(self, spec: str) -> "ActionResult":
        """Start a program. ``spec`` is OS-interpreted: a URL scheme
        (``steam://rungameid/...``), an app path/bundle, or an app name."""
        raise NotImplementedError

    def screenshot(self, target: Optional[str]) -> bytes:
        """PNG bytes of ``target`` (a window ref) or the screen if target is
        None/"screen". Must work for an UNFOCUSED window."""
        raise NotImplementedError

    def activate(self, target: str) -> ActionResult:
        raise NotImplementedError

    def text(self, target: str, text: str) -> ActionResult:
        """Set/insert text into the target's editable element, unfocused if
        possible (tier ladder)."""
        raise NotImplementedError

    def key(self, target: str, keys: str, focus: bool = False) -> ActionResult:
        """Deliver a keystroke (named key like 'Return', a single char, or a
        combo) to the target, unfocused if possible (tier ladder). ``focus=True``
        forces the focus-stealing path for apps that ignore background keys
        (Unity/other engines)."""
        raise NotImplementedError

    def click(self, target: str, x: int, y: int, button: str = "left",
              double: bool = False) -> ActionResult:
        """Click at (x, y) given RELATIVE to the target window's top-left, in the
        window's coordinate units. Synthetic mouse events reach many apps that
        drop synthetic keys (Unity games); this activates the window first."""
        raise NotImplementedError

    def inspect(self, target: str, depth: int = 3) -> Element:
        raise NotImplementedError

    def ocr(self, target: str) -> dict:
        """Recognize text in the target window's capture. Returns
        {w, h, boxes:[{text, bbox:[x,y,w,h] in capture pixels}]}. The escape hatch
        for AX-opaque apps (web/Electron/canvas UIs) — read what you can't inspect."""
        raise NotImplementedError

    def screen_size(self):
        """Return (width, height) of the primary display in physical pixels."""
        raise NotImplementedError

    def move(self, target: str, x: int, y: int, w: int, h: int,
             topmost: Optional[bool] = None) -> ActionResult:
        """Position + size ``target`` to a physical-pixel rect. ``topmost`` is
        tri-state: ``True`` pins it above non-topmost windows (used to pin an
        Ersatz overlay), ``False`` explicitly clears the topmost bit, ``None``
        (default) just raises without touching the topmost state."""
        raise NotImplementedError
