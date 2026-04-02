"""Configuration management — defaults, load, save, paths, theme tokens."""

import json
from pathlib import Path

STATE_FILE = Path.home() / ".claude" / "usage-bar-state.json"
CONFIG_FILE = Path.home() / ".claude" / "usage-bar-config.json"
CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
LOG_FILE = Path.home() / ".claude" / "usage-bar.log"

DEFAULTS = {
    "enabled": True,
    "drain_mode": False,
    "bar_height": 14,
    "bubble_opacity": 80,
    "bubble_count": 20,
    "bubble_speed": 0.5,
    "bg_enabled": True,
    "bg_color": "#E3713F",
    "bg_opacity": 18,
    "test_percent": -1,
}

CHROMA_KEY = "#010101"
CHROMA_KEY_RGB = 0x00010101
BUBBLE_COLORS = ["#FFFFFF", "#FFD4C0", "#FFE0D0", "#FFC8A8", "#FFFAF5"]
FPS = 30
FRAME_MS = 1000 // FPS
POLL_INTERVAL_SECONDS = 2
API_POLL_INTERVAL = 30

# Design tokens (HeroUI dark mode + pixel-police 4px grid)
THEME = {
    "bg": "#0f0f0f",
    "surface": "#191919",
    "surface2": "#222222",
    "border": "#2a2a2a",
    "text": "#f0f0f0",
    "text2": "#a0a0a0",
    "text3": "#555555",
    "accent": "#E3713F",
    "accent_hi": "#F08A55",
    "accent_lo": "#C05A2D",
    "tog_off": "#333333",
    "tog_on": "#E3713F",
    "knob": "#ffffff",
    "track_bg": "#2a2a2a",
    "track_fg": "#E3713F",
}


class Config:
    """Manages app configuration with load/save and migration."""

    def __init__(self):
        self._data = dict(DEFAULTS)
        self.load()

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self._data[key] = value

    def get(self, key, default=None):
        return self._data.get(key, default)

    def load(self):
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            if "opacity" in saved and "bubble_opacity" not in saved:
                saved["bubble_opacity"] = saved.pop("opacity")
            if "color" in saved:
                saved.pop("color")
            self._data.update({k: saved[k] for k in DEFAULTS if k in saved})
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def save(self):
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
        except OSError:
            pass
