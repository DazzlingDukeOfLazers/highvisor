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
    """A saved launcher's spec if the arg is a known name, else the arg itself."""
    return load_launchers().get(name_or_spec, name_or_spec)
