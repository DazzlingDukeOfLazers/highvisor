"""Per-OS PlatformBackend implementations."""
import sys

from ..backend import PlatformBackend


def make_backend() -> PlatformBackend:
    """Return the backend for the current OS. Backends are imported lazily so the
    package still imports on an unsupported OS (you just can't serve)."""
    if sys.platform == "win32":
        from .windows import WindowsBackend
        return WindowsBackend()
    if sys.platform == "darwin":
        raise NotImplementedError(
            "macOS backend not built yet (see spike/mac_slice0.py for the plan)")
    raise NotImplementedError("unsupported platform: %s" % sys.platform)
