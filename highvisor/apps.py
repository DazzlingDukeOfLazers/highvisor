"""apps — profiles for probing whether a known app is up, and in what STATE.

A profile names the app's window (title substring) and, optionally, a localhost
port that is only open in a particular state. For Caves of Qud the Raves mod's
bridge (127.0.0.1:48710) starts lazily on the first IN-GAME tick, so:
  • no window                    -> "off"
  • window present, port closed  -> "menu"  (launched, not in a game yet)
  • window present, port open    -> "in-game"
which lets highvisor answer "is Qud up, and are we in a game yet?" with no OCR.

Caveat: the bridge keeps listening once started, so returning to the menu AFTER a
game still reads "in-game" for that session — the signal is "has a game begun",
which is exactly what a turn-driven export (or reconnect) waits on.
"""
PROFILES = {
    "qud": {
        "window": "CavesOfQud",
        "proc": "CoQ",           # pkill -f pattern for a CLEAN restart (kills duplicates too)
        "launcher": "qud_solo",
        "port": 48710,           # Raves mod bridge — opens on the first in-game tick
        "port_state": "in-game",
        "window_state": "menu",
        "off_state": "off",
    },
    "raves": {
        "window": "Raves of Qud",
        "proc": "RavesOfQud",
        "launcher": "raves_solo",
    },
}
