"""launch — named launchers for the programs highvisor should be able to start.

A launcher maps a short name to an OS-interpreted spec: a URL scheme
(``steam://rungameid/<id>``), an app path / ``.app`` bundle, or an app name. Presets
live in ``~/.config/highvisor/launch.json`` so the reusable flow is just
``hv launch qud`` — highvisor stays the thing that loads programs. ``hv ls`` reports
each running app's ``path``, so a spec can be discovered without leaving highvisor.
"""
import json
import os


def _path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "highvisor", "launch.json")


def load_launchers() -> dict:
    try:
        with open(_path()) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_launcher(name: str, spec: str) -> str:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = load_launchers()
    data[name] = spec
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path


def resolve(name_or_spec: str) -> str:
    """The open-target of a saved launcher (or the arg itself). Kept for callers
    that only need the target; use :func:`resolve_launch` to also get its args."""
    return resolve_launch(name_or_spec)[0]


def resolve_launch(name_or_spec: str):
    """``(open_target, args)`` for a launcher name. An entry may be a bare spec
    string (no args) or an object ``{"open": <spec>, "args": [...]}`` — the object
    form lets a launcher pass extra argv to the program (e.g. Raves telling Caves
    of Qud to open borderless). An unknown name is treated as a literal spec."""
    entry = load_launchers().get(name_or_spec, name_or_spec)
    if isinstance(entry, dict):
        return str(entry.get("open", "")), [str(a) for a in entry.get("args", [])]
    return str(entry), []
