"""
Claude Code Usage Bar v5 — Taskbar Bubble Overlay
Animated orange bubbles float across the Windows taskbar, filling left-to-right
proportional to your Claude Max 5-hour session usage window.

Two child windows of the taskbar (behind icons via HWND_BOTTOM):
  - bg_window:     solid background fill, independent opacity
  - bubble_window: chroma-keyed canvas with animated bubbles, independent opacity

v5: Now polls Anthropic's OAuth usage API directly (no statusLine hook needed).
Falls back to reading ~/.claude/usage-bar-state.json if API is unavailable.
"""

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import math
import os
import random
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import colorchooser
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from PIL import Image, ImageDraw
import pystray

log = logging.getLogger("claude_usage_bar")

# ─── Paths ───────────────────────────────────────────────────────────────────

STATE_FILE = Path.home() / ".claude" / "usage-bar-state.json"
CONFIG_FILE = Path.home() / ".claude" / "usage-bar-config.json"

# ─── Default Config ──────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "enabled": True,
    "drain_mode": False,
    "bar_height": 14,       # px from bottom (0 = full taskbar height)
    "bubble_opacity": 80,
    "bubble_count": 20,
    "bubble_speed": 0.5,
    "bg_enabled": True,
    "bg_color": "#E3713F",
    "bg_opacity": 18,
    "test_percent": -1,     # legacy, kept for config compat
}

CHROMA_KEY = "#010101"
CHROMA_KEY_RGB = 0x00010101
BUBBLE_COLORS = ["#E3713F", "#F0A882", "#E8946A", "#D4623A", "#F5B899"]
FPS = 30
FRAME_MS = 1000 // FPS
POLL_INTERVAL_SECONDS = 2

# ─── Windows API ─────────────────────────────────────────────────────────────

GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
LWA_ALPHA = 0x00000002
LWA_COLORKEY = 0x00000001
HWND_BOTTOM = 1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

user32 = ctypes.windll.user32

FindWindowW = user32.FindWindowW
FindWindowW.restype = wintypes.HWND
FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]

GetWindowRect = user32.GetWindowRect
GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

SetWindowLongW = user32.SetWindowLongW
SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
SetWindowLongW.restype = ctypes.c_long

GetWindowLongW = user32.GetWindowLongW
GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongW.restype = ctypes.c_long

SetLayeredWindowAttributes = user32.SetLayeredWindowAttributes
SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD,
]

SetWindowPos = user32.SetWindowPos
SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]

SetParent = user32.SetParent
SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
SetParent.restype = wintypes.HWND


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r") as f:
            saved = json.load(f)
        # Migrate v2 keys to v3
        if "opacity" in saved and "bubble_opacity" not in saved:
            saved["bubble_opacity"] = saved.pop("opacity")
        if "color" in saved:
            saved.pop("color")  # unused in v3+
        cfg.update({k: saved[k] for k in DEFAULT_CONFIG if k in saved})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg):
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ─── Taskbar ─────────────────────────────────────────────────────────────────

def get_taskbar_info():
    hwnd = FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return None, None
    rect = wintypes.RECT()
    GetWindowRect(hwnd, ctypes.byref(rect))
    return hwnd, (rect.left, rect.top, rect.right, rect.bottom)


# ─── Child Window Helper ────────────────────────────────────────────────────

def make_child_of_taskbar(tk_toplevel, taskbar_hwnd):
    """Reparent a tkinter Toplevel as a child of the taskbar, at the bottom z-order."""
    tk_toplevel.update_idletasks()
    tk_toplevel.update()
    frame = ctypes.c_long(tk_toplevel.winfo_id())
    hwnd = user32.GetParent(frame)
    if not hwnd or not taskbar_hwnd:
        return hwnd

    # Reparent into the taskbar
    SetParent(hwnd, taskbar_hwnd)

    # Set WS_CHILD | WS_VISIBLE
    style = GetWindowLongW(hwnd, GWL_STYLE)
    style = (style | WS_CHILD | WS_VISIBLE) & ~0x80000000  # remove WS_POPUP
    SetWindowLongW(hwnd, GWL_STYLE, style)

    # Set layered + click-through + tool window
    ex = GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    SetWindowLongW(hwnd, GWL_EXSTYLE, ex)

    return hwnd


SWP_NOZORDER = 0x0004

def position_child(hwnd, x, y, w, h):
    """Position a child window without changing z-order."""
    SetWindowPos(hwnd, 0, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)


# ─── Bubble ──────────────────────────────────────────────────────────────────

class Bubble:
    def __init__(self, max_x, max_y, speed):
        self.thin = max_y < 30  # thin bar mode
        if self.thin:
            r_max = max_y * 0.35
            self.radius = random.uniform(max(1, r_max * 0.4), max(1.5, r_max))
        else:
            self.radius = random.uniform(2, 5)
        self.x = random.uniform(0, max(1, max_x))
        self.y = random.uniform(0, max(1, max_y))
        self.speed = random.uniform(speed * 0.5, speed * 1.5)
        self.drift_x = random.uniform(-0.15, 0.15)
        self.color = random.choice(BUBBLE_COLORS)
        self.max_y = max_y
        self.max_x = max_x
        self.phase = random.uniform(0, math.pi * 2)
        self.osc_amp = random.uniform(0.2, 0.6)

    def update(self, fill_width):
        self.phase += 0.05
        if self.thin:
            self.x += self.drift_x * 2 + math.sin(self.phase) * 0.3
            self.y += math.cos(self.phase * 1.3) * 0.15
            if self.x < -self.radius or self.x > fill_width + self.radius:
                self.x = random.uniform(0, max(1, fill_width))
                self.y = random.uniform(0, max(1, self.max_y))
                self.color = random.choice(BUBBLE_COLORS)
        else:
            # Float rightward — gentle vertical wobble, no despawn on y
            self.x += self.speed
            self.y += math.sin(self.phase) * 0.2  # gentle bob only
            # Clamp y to stay in bounds instead of despawning
            self.y = max(self.radius, min(self.max_y - self.radius, self.y))
            # Only respawn when exiting right edge
            if self.x > fill_width + self.radius:
                self.x = -self.radius
                self.y = random.uniform(self.radius, max(self.radius + 1, self.max_y - self.radius))
                self.color = random.choice(BUBBLE_COLORS)
                self.radius = random.uniform(2, 5)


# ─── OAuth Usage Poller ──────────────────────────────────────────────

CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_ENDPOINT = "https://api.anthropic.com/v1/oauth/token"
USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
API_POLL_INTERVAL = 30  # seconds — fast updates


class OAuthPoller:
    """Polls Anthropic's OAuth usage API and writes state for the overlay."""

    def __init__(self):
        self._access_token = None
        self._refresh_token = None
        self._token_expires_at = 0.0
        self._rate_limit_until = 0.0
        self._consecutive_429s = 0
        self._running = True
        self.last_usage = None
        self._load_credentials()

    def _load_credentials(self) -> bool:
        try:
            data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            oauth = data.get("claudeAiOauth", {})
            self._refresh_token = oauth.get("refreshToken")
            self._access_token = oauth.get("accessToken")
            expires_ms = oauth.get("expiresAt", 0)
            self._token_expires_at = expires_ms / 1000 if expires_ms else 0
            return bool(self._refresh_token)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load credentials: %s", e)
            return False

    def _refresh_access_token(self) -> bool:
        if not self._refresh_token:
            return False
        payload = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": CLAUDE_CODE_CLIENT_ID,
        }).encode("utf-8")
        req = Request(TOKEN_ENDPOINT, data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 28800)
            new_refresh = data.get("refresh_token")
            if new_refresh:
                self._refresh_token = new_refresh
                self._save_credentials(data)
            self._consecutive_429s = 0  # reset on fresh token
            self._rate_limit_until = 0
            log.info("OAuth token refreshed (new token)")
            return True
        except (HTTPError, URLError, json.JSONDecodeError, KeyError) as e:
            log.warning("Token refresh failed: %s", e)
            return False

    def _save_credentials(self, token_data):
        try:
            existing = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        oauth = existing.setdefault("claudeAiOauth", {})
        oauth["accessToken"] = token_data["access_token"]
        oauth["refreshToken"] = token_data.get("refresh_token", self._refresh_token)
        oauth["expiresAt"] = int(self._token_expires_at * 1000)
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(CREDENTIALS_FILE.parent), suffix=".tmp")
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(existing, f)
            if sys.platform == "win32" and CREDENTIALS_FILE.exists():
                CREDENTIALS_FILE.unlink()
            os.rename(tmp_path, str(CREDENTIALS_FILE))
        except OSError:
            pass

    def _ensure_valid_token(self) -> bool:
        if self._access_token and time.time() < (self._token_expires_at - 300):
            return True
        return self._refresh_access_token()

    def fetch_usage(self) -> dict | None:
        if time.time() < self._rate_limit_until:
            return None  # still in backoff
        if not self._ensure_valid_token():
            return None
        req = Request(USAGE_ENDPOINT, headers={
            "Authorization": f"Bearer {self._access_token}",
            "anthropic-beta": "oauth-2025-04-20",
        }, method="GET")
        try:
            with urlopen(req, timeout=15) as resp:
                self._consecutive_429s = 0
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 401:
                if self._refresh_access_token():
                    return self.fetch_usage()
            if e.code == 429:
                self._consecutive_429s += 1
                # Rate limits are per-token. Refresh to get a new token.
                if self._consecutive_429s <= 2:
                    log.info("429 — refreshing token to escape rate limit")
                    if self._refresh_access_token():
                        return self.fetch_usage()
                # If refresh didn't help, exponential backoff
                backoff = min(600, 60 * (2 ** (self._consecutive_429s - 2)))
                log.warning("Rate limited (attempt %d), backoff %ds",
                            self._consecutive_429s, backoff)
                self._rate_limit_until = time.time() + backoff
                return None
            log.warning("Usage fetch failed: %s", e)
            return None
        except (URLError, json.JSONDecodeError) as e:
            log.warning("Usage fetch failed: %s", e)
            return None

    def write_state(self, usage):
        five_hour = usage.get("five_hour", {})
        seven_day = usage.get("seven_day", {})
        state = {
            "used_percentage": five_hour.get("utilization", 0),
            "resets_at": five_hour.get("resets_at"),
            "seven_day_percentage": seven_day.get("utilization", 0),
            "seven_day_resets_at": seven_day.get("resets_at"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "source": "oauth_api",
        }
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(STATE_FILE.parent), suffix=".tmp")
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            if sys.platform == "win32" and STATE_FILE.exists():
                STATE_FILE.unlink()
            os.rename(tmp_path, str(STATE_FILE))
        except OSError:
            try:
                STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
            except OSError:
                pass
        self.last_usage = state

    def poll_once(self) -> dict | None:
        usage = self.fetch_usage()
        if usage:
            self.write_state(usage)
        return usage

    def stop(self):
        self._running = False


# ─── Main App ────────────────────────────────────────────────────────────────

class UsageOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.usage_percent = 0.0
        self.resets_at = None
        self.seven_day_percent = 0.0
        self.running = True
        self.config = load_config()
        # Save immediately to persist migrated keys
        save_config(self.config)
        self.enabled = self.config["enabled"]
        self.taskbar_hwnd = None
        self.taskbar_rect = None
        self.bg_hwnd = None
        self.bubble_hwnd = None
        self.bubbles = []
        self.settings_win = None
        self.oauth_poller = OAuthPoller()
        self._wave_phase = 0.0
        self._setup()
        self._setup_tray()
        self._spawn_bubbles()

    def _setup(self):
        self.taskbar_hwnd, self.taskbar_rect = get_taskbar_info()
        if not self.taskbar_rect:
            print("[ERROR] Could not find taskbar")
            sys.exit(1)

        left, top, right, bottom = self.taskbar_rect
        tb_w = right - left
        tb_h = bottom - top
        cfg_h = self.config.get("bar_height", 0)
        bar_h = tb_h if cfg_h <= 0 else max(2, cfg_h)
        bar_y = tb_h - bar_h  # 0 when full height

        # ── Background window ──
        self.bg_win = tk.Toplevel(self.root)
        self.bg_win.overrideredirect(True)
        self.bg_win.configure(bg=self.config["bg_color"])
        self.bg_win.geometry(f"1x{bar_h}+{left}+{top + bar_y}")

        self.bg_hwnd = make_child_of_taskbar(self.bg_win, self.taskbar_hwnd)
        if self.bg_hwnd:
            SetLayeredWindowAttributes(self.bg_hwnd, 0, self.config["bg_opacity"], LWA_ALPHA)
            position_child(self.bg_hwnd, 0, bar_y, 1, bar_h)

        # ── Bubble window ──
        self.bubble_win = tk.Toplevel(self.root)
        self.bubble_win.overrideredirect(True)
        self.bubble_win.configure(bg=CHROMA_KEY)
        self.bubble_win.geometry(f"{tb_w}x{bar_h}+{left}+{top + bar_y}")

        self.canvas = tk.Canvas(
            self.bubble_win, width=tb_w, height=bar_h,
            bg=CHROMA_KEY, highlightthickness=0, bd=0,
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.bubble_hwnd = make_child_of_taskbar(self.bubble_win, self.taskbar_hwnd)
        if self.bubble_hwnd:
            SetLayeredWindowAttributes(
                self.bubble_hwnd, CHROMA_KEY_RGB,
                self.config["bubble_opacity"], LWA_COLORKEY | LWA_ALPHA,
            )
            position_child(self.bubble_hwnd, 0, bar_y, tb_w, bar_h)

    def _spawn_bubbles(self):
        if not self.taskbar_rect:
            return
        left, top, right, bottom = self.taskbar_rect
        tb_w = right - left
        tb_h = bottom - top
        cfg_h = self.config.get("bar_height", 0)
        bar_h = tb_h if cfg_h <= 0 else max(2, cfg_h)
        pct = self._get_display_percent()
        fill_w = max(1, tb_w * (pct / 100.0))
        self.bubbles = [
            Bubble(fill_w, bar_h, self.config["bubble_speed"])
            for _ in range(self.config["bubble_count"])
        ]

    # ── Tray ─────────────────────────────────────────────────────────────

    def _setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Claude Usage Bar", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Disable" if self.enabled else "Enable",
                self._on_toggle,
            ),
            pystray.MenuItem("Settings...", self._on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )
        self.tray_icon = pystray.Icon(
            "claude_usage", self._create_tray_icon(0),
            "Claude: waiting for data...", menu,
        )

    def _create_tray_icon(self, percent=0):
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, size - 4, size - 4], fill=(227, 113, 63, 255))
        if percent > 0:
            draw.pieslice(
                [8, 8, size - 8, size - 8],
                start=-90, end=-90 + int(3.6 * percent),
                fill=(255, 255, 255, 200),
            )
        return img

    def _on_toggle(self, icon, item):
        self.enabled = not self.enabled
        self.config["enabled"] = self.enabled
        save_config(self.config)
        if not self.enabled:
            self.root.after(0, self._hide_all)
        else:
            self.root.after(0, self._show_all)

    def _on_settings(self, icon, item):
        self.root.after(0, self._open_settings)

    def _on_quit(self, icon, item):
        self.running = False
        icon.stop()
        self.root.after(100, self.root.destroy)

    # ── Settings ─────────────────────────────────────────────────────────
    #
    # Design tokens derived from HeroUI dark mode + pixel-police 4px grid.
    # All custom widgets drawn on Canvas to avoid ugly native tk widgets.

    # Surfaces  (HeroUI: --background, --surface, --surface-secondary)
    _BG        = "#0f0f0f"
    _SURFACE   = "#191919"
    _SURFACE2  = "#222222"
    _BORDER    = "#2a2a2a"

    # Text  (HeroUI: --foreground, --muted)
    _TEXT      = "#f0f0f0"
    _TEXT2     = "#a0a0a0"
    _TEXT3     = "#555555"

    # Accent  (brand orange)
    _ACCENT    = "#E3713F"
    _ACCENT_HI = "#F08A55"
    _ACCENT_LO = "#C05A2D"

    # Toggle / Slider
    _TOG_OFF   = "#333333"
    _TOG_ON    = "#E3713F"
    _KNOB      = "#ffffff"
    _TRACK_BG  = "#2a2a2a"
    _TRACK_FG  = "#E3713F"

    # ── Canvas Toggle ────────────────────────────────────────────────

    def _make_toggle(self, parent, var, command=None, bg=None):
        bg = bg or self._SURFACE
        W, H = 40, 22
        PAD = 3
        c = tk.Canvas(parent, width=W, height=H, bg=bg,
                      highlightthickness=0, bd=0, cursor="hand2")

        def _round_rect(x1, y1, x2, y2, r, **kw):
            pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
                   x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
            return c.create_polygon(pts, smooth=True, **kw)

        def draw():
            c.delete("all")
            on = var.get()
            fill = self._TOG_ON if on else self._TOG_OFF
            _round_rect(0, 0, W, H, H // 2, fill=fill, outline="")
            kr = (H - PAD * 2) // 2
            kx = W - PAD - kr if on else PAD + kr
            ky = H // 2
            c.create_oval(kx - kr, ky - kr, kx + kr, ky + kr,
                          fill=self._KNOB, outline="")

        def toggle(e=None):
            var.set(not var.get())
            draw()
            if command:
                command()

        c.bind("<Button-1>", toggle)
        draw()
        return c

    # ── Canvas Slider ────────────────────────────────────────────────

    def _make_slider(self, parent, label, var, lo, hi, command,
                     resolution=1, bg=None):
        """Full Canvas-drawn slider — no tk.Scale."""
        bg = bg or self._SURFACE
        W, TRACK_H, THUMB_R = 248, 6, 7
        H = THUMB_R * 2 + 4
        PAD_X = THUMB_R + 2  # keep thumb inside canvas

        row = tk.Frame(parent, bg=bg)
        row.pack(fill="x", pady=(6, 2))

        # Label + value
        head = tk.Frame(row, bg=bg)
        head.pack(fill="x")
        tk.Label(head, text=label, bg=bg, fg=self._TEXT2,
                 font=("Segoe UI", 9), anchor="w").pack(side="left")
        fmt = (lambda v: f"{v:.1f}") if resolution < 1 else (lambda v: str(int(v)))
        val_lbl = tk.Label(head, text=fmt(var.get()), bg=bg,
                           fg=self._TEXT, font=("Segoe UI", 9, "bold"))
        val_lbl.pack(side="right")

        c = tk.Canvas(row, width=W, height=H, bg=bg,
                      highlightthickness=0, bd=0, cursor="hand2")
        c.pack(fill="x", pady=(2, 0))
        dragging = [False]

        def val_to_x(v):
            frac = (v - lo) / max(1e-9, hi - lo)
            return PAD_X + frac * (W - 2 * PAD_X)

        def x_to_val(x):
            frac = max(0, min(1, (x - PAD_X) / max(1, W - 2 * PAD_X)))
            raw = lo + frac * (hi - lo)
            if resolution >= 1:
                return int(round(raw))
            return round(raw / resolution) * resolution

        def draw():
            c.delete("all")
            cy = H // 2
            # track bg
            c.create_line(PAD_X, cy, W - PAD_X, cy, fill=self._TRACK_BG,
                          width=TRACK_H, capstyle="round")
            # track fill
            tx = val_to_x(var.get())
            c.create_line(PAD_X, cy, tx, cy, fill=self._TRACK_FG,
                          width=TRACK_H, capstyle="round")
            # thumb shadow
            c.create_oval(tx - THUMB_R - 1, cy - THUMB_R - 1,
                          tx + THUMB_R + 1, cy + THUMB_R + 1,
                          fill="#000000", outline="", stipple="gray25")
            # thumb
            c.create_oval(tx - THUMB_R, cy - THUMB_R,
                          tx + THUMB_R, cy + THUMB_R,
                          fill=self._KNOB, outline=self._BORDER)
            val_lbl.config(text=fmt(var.get()))

        def on_press(e):
            dragging[0] = True
            update(e)

        def on_drag(e):
            if dragging[0]:
                update(e)

        def on_release(e):
            dragging[0] = False

        def update(e):
            v = x_to_val(e.x)
            v = max(lo, min(hi, v))
            if resolution >= 1:
                var.set(int(v))
            else:
                var.set(round(v, 1))
            draw()
            command(v)

        c.bind("<Button-1>", on_press)
        c.bind("<B1-Motion>", on_drag)
        c.bind("<ButtonRelease-1>", on_release)
        draw()
        c._draw = draw  # keep ref for external redraws
        return c

    # ── Card & Row helpers ───────────────────────────────────────────

    def _make_card(self, parent, title):
        outer = tk.Frame(parent, bg=self._BORDER, padx=1, pady=1)
        outer.pack(fill="x", padx=16, pady=(0, 10))
        card = tk.Frame(outer, bg=self._SURFACE, padx=14, pady=10)
        card.pack(fill="both")
        tk.Label(card, text=title.upper(), bg=self._SURFACE, fg=self._TEXT3,
                 font=("Segoe UI", 8), anchor="w",
                 ).pack(fill="x", pady=(0, 6))
        return card

    def _make_toggle_row(self, parent, label_text, var, command=None,
                         hint=None):
        row = tk.Frame(parent, bg=self._SURFACE)
        row.pack(fill="x", pady=2)
        lbl_frame = tk.Frame(row, bg=self._SURFACE)
        lbl_frame.pack(side="left", fill="y")
        tk.Label(lbl_frame, text=label_text, bg=self._SURFACE, fg=self._TEXT,
                 font=("Segoe UI", 10), anchor="w").pack(anchor="w")
        if hint:
            tk.Label(lbl_frame, text=hint, bg=self._SURFACE, fg=self._TEXT3,
                     font=("Segoe UI", 8), anchor="w", wraplength=190,
                     ).pack(anchor="w")
        self._make_toggle(row, var, command, bg=self._SURFACE).pack(side="right")

    # ── Open Settings ────────────────────────────────────────────────

    def _open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Claude Usage Bar")
        win.geometry("340x740")
        win.resizable(False, False)
        win.configure(bg=self._BG)
        self.settings_win = win

        # ── Header with live usage ──
        hdr = tk.Frame(win, bg=self._BG)
        hdr.pack(fill="x", padx=20, pady=(20, 4))
        tk.Label(hdr, text="Claude Usage Bar", bg=self._BG, fg=self._TEXT,
                 font=("Segoe UI Semibold", 14), anchor="w").pack(side="left")

        # Live usage badge
        pct = self._get_display_percent()
        badge_text = f"{pct:.0f}%"
        badge = tk.Label(hdr, text=badge_text, bg=self._ACCENT, fg="white",
                         font=("Segoe UI", 9, "bold"), padx=8, pady=2)
        badge.pack(side="right")

        # Separator
        tk.Frame(win, bg=self._BORDER, height=1).pack(fill="x", padx=20, pady=(8, 12))

        # ── Body ──
        body = tk.Frame(win, bg=self._BG)
        body.pack(fill="both", expand=True)

        # ── Mode ──
        mode_card = self._make_card(body, "Display")
        self._drain_var = tk.BooleanVar(value=self.config.get("drain_mode", False))
        self._make_toggle_row(mode_card, "Drain mode", self._drain_var,
                              self._on_drain_toggle,
                              hint="Starts full, empties as you use tokens")

        # ── Background ──
        bg_card = self._make_card(body, "Background fill")
        self._bg_var = tk.BooleanVar(value=self.config["bg_enabled"])

        bg_row = tk.Frame(bg_card, bg=self._SURFACE)
        bg_row.pack(fill="x", pady=2)
        tk.Label(bg_row, text="Enabled", bg=self._SURFACE, fg=self._TEXT,
                 font=("Segoe UI", 10)).pack(side="left")
        # Color swatch
        self._bg_color_btn = tk.Canvas(bg_row, width=22, height=22,
                                        bg=self._SURFACE, highlightthickness=0,
                                        bd=0, cursor="hand2")
        self._bg_color_btn.create_oval(2, 2, 20, 20,
                                        fill=self.config["bg_color"], outline=self._BORDER)
        self._bg_color_btn.bind("<Button-1>", lambda e: self._pick_bg_color())
        self._bg_color_btn.pack(side="right", padx=(6, 0))
        self._make_toggle(bg_row, self._bg_var, self._on_bg_toggle,
                          bg=self._SURFACE).pack(side="right")

        self._bg_opacity_var = tk.IntVar(value=self.config["bg_opacity"])
        self._make_slider(bg_card, "Opacity", self._bg_opacity_var, 5, 255,
                          self._on_bg_opacity)

        self._bar_height_var = tk.IntVar(value=self.config.get("bar_height", 0))
        self._make_slider(bg_card, "Bar height (0 = full)", self._bar_height_var, 0, 48,
                          self._on_bar_height)

        # ── Bubbles ──
        bub_card = self._make_card(body, "Bubbles")
        self._bubble_opacity_var = tk.IntVar(value=self.config["bubble_opacity"])
        self._make_slider(bub_card, "Opacity", self._bubble_opacity_var, 5, 120,
                          self._on_bubble_opacity)

        self._bubble_count_var = tk.IntVar(value=self.config["bubble_count"])
        self._make_slider(bub_card, "Count", self._bubble_count_var, 0, 40,
                          self._on_bubble_count)

        self._speed_var = tk.DoubleVar(value=self.config["bubble_speed"])
        self._make_slider(bub_card, "Speed", self._speed_var, 0.1, 2.0,
                          self._on_speed, resolution=0.1)

        # ── Footer ──
        foot = tk.Frame(win, bg=self._BG)
        foot.pack(fill="x", padx=20, pady=(4, 20))

        btn_frame = tk.Frame(foot, bg=self._BG)
        btn_frame.pack(fill="x")

        save_btn = tk.Canvas(btn_frame, height=40, bg=self._BG,
                             highlightthickness=0, bd=0, cursor="hand2")
        save_btn.pack(fill="x")

        def _draw_btn(hover=False):
            save_btn.delete("all")
            w = save_btn.winfo_width() or 290
            fill = self._ACCENT_HI if hover else self._ACCENT
            # Rounded rect button
            r = 8
            save_btn.create_polygon(
                r, 0, w - r, 0, w, 0, w, r, w, 40 - r, w, 40,
                w - r, 40, r, 40, 0, 40, 0, 40 - r, 0, r, 0, 0,
                smooth=True, fill=fill, outline="")
            save_btn.create_text(w // 2, 20, text="Save & Close",
                                 fill="white", font=("Segoe UI Semibold", 10))

        save_btn.bind("<Configure>", lambda e: _draw_btn())
        save_btn.bind("<Enter>", lambda e: _draw_btn(True))
        save_btn.bind("<Leave>", lambda e: _draw_btn(False))
        save_btn.bind("<Button-1>", lambda e: self._save_settings())
        win.after(50, _draw_btn)

    # ── Settings handlers ────────────────────────────────────────────────

    def _on_drain_toggle(self):
        self.config["drain_mode"] = self._drain_var.get()
        self._spawn_bubbles()

    def _on_bg_toggle(self):
        self.config["bg_enabled"] = self._bg_var.get()

    def _pick_bg_color(self):
        color = colorchooser.askcolor(
            initialcolor=self.config["bg_color"], title="Background Color",
        )
        if color and color[1]:
            self.config["bg_color"] = color[1]
            self._bg_color_btn.delete("all")
            self._bg_color_btn.create_oval(2, 2, 20, 20,
                                            fill=color[1], outline=self._BORDER)
            self.bg_win.configure(bg=color[1])

    def _on_bar_height(self, val):
        self.config["bar_height"] = int(val)
        self._spawn_bubbles()

    def _on_bg_opacity(self, val):
        self.config["bg_opacity"] = int(val)
        if self.bg_hwnd:
            SetLayeredWindowAttributes(self.bg_hwnd, 0, int(val), LWA_ALPHA)

    def _on_bubble_opacity(self, val):
        self.config["bubble_opacity"] = int(val)
        if self.bubble_hwnd:
            SetLayeredWindowAttributes(
                self.bubble_hwnd, CHROMA_KEY_RGB, int(val), LWA_COLORKEY | LWA_ALPHA,
            )

    def _on_bubble_count(self, val):
        self.config["bubble_count"] = int(val)
        self._spawn_bubbles()

    def _on_speed(self, val):
        self.config["bubble_speed"] = float(val)
        for b in self.bubbles:
            b.speed = random.uniform(float(val) * 0.5, float(val) * 1.5)

    def _save_settings(self):
        save_config(self.config)
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.destroy()
            self.settings_win = None

    # ── Display helpers ──────────────────────────────────────────────────

    def _get_display_percent(self):
        raw = min(100, max(0, self.usage_percent))
        if self.config.get("drain_mode", False):
            return 100 - raw
        return raw

    def _hide_all(self):
        self.canvas.delete("all")
        if self.bg_hwnd:
            SetLayeredWindowAttributes(self.bg_hwnd, 0, 0, LWA_ALPHA)
        if self.bubble_hwnd:
            SetLayeredWindowAttributes(
                self.bubble_hwnd, CHROMA_KEY_RGB, 0, LWA_COLORKEY | LWA_ALPHA,
            )

    def _show_all(self):
        if self.bg_hwnd:
            SetLayeredWindowAttributes(
                self.bg_hwnd, 0, self.config["bg_opacity"], LWA_ALPHA,
            )
        if self.bubble_hwnd:
            SetLayeredWindowAttributes(
                self.bubble_hwnd, CHROMA_KEY_RGB,
                self.config["bubble_opacity"], LWA_COLORKEY | LWA_ALPHA,
            )

    # ── Animation ────────────────────────────────────────────────────────

    def _animate(self):
        if not self.running:
            return

        pct = self._get_display_percent()

        if self.enabled and pct > 0:
            left, top, right, bottom = self.taskbar_rect
            tb_w = right - left
            tb_h = bottom - top
            cfg_h = self.config.get("bar_height", 0)
            bar_h = tb_h if cfg_h <= 0 else max(2, cfg_h)
            bar_y = tb_h - bar_h
            fill_w = max(1, int(tb_w * (pct / 100.0)))
            wave_amp = 8

            # Hide bg window — everything drawn on single bubble canvas
            if self.bg_hwnd:
                position_child(self.bg_hwnd, 0, 0, 1, 1)

            # Set bubble canvas opacity to bg_opacity (single layer = no overlap)
            if self.bubble_hwnd:
                position_child(self.bubble_hwnd, 0, bar_y, tb_w, bar_h)
                opacity = self.config["bg_opacity"] if self.config["bg_enabled"] else self.config["bubble_opacity"]
                SetLayeredWindowAttributes(
                    self.bubble_hwnd, CHROMA_KEY_RGB, opacity, LWA_COLORKEY | LWA_ALPHA)

            self._wave_phase += 0.025
            self.canvas.delete("all")
            bg_color = self.config["bg_color"]

            # Draw entire fill + wave as one shape on canvas
            if self.config["bg_enabled"]:
                # Solid rectangle up to near the wave edge
                solid_end = max(0, fill_w - wave_amp)
                if solid_end > 0:
                    self.canvas.create_rectangle(
                        0, 0, solid_end, bar_h,
                        fill=bg_color, outline="")

                # Wavy right edge polygon (no smooth — avoids tkinter bulge)
                pts = [(solid_end, 0)]
                steps = max(20, bar_h * 2)
                for i in range(steps + 1):
                    y = (i / steps) * bar_h
                    wx = (fill_w
                          + math.sin(self._wave_phase + y * 0.06) * wave_amp * 0.5
                          + math.sin(self._wave_phase * 0.6 + y * 0.12) * wave_amp * 0.25)
                    pts.append((wx, y))
                pts.append((solid_end, bar_h))
                flat = [c for p in pts for c in p]
                self.canvas.create_polygon(flat, fill=bg_color, outline="")

            for bubble in self.bubbles:
                bubble.update(fill_w)
                x, y, r = bubble.x, bubble.y, bubble.radius
                if 0 <= x <= fill_w + wave_amp and -r <= y <= bar_h + r:
                    self.canvas.create_oval(
                        x - r, y - r, x + r, y + r,
                        fill=bubble.color, outline="",
                    )

        elif not self.enabled or pct <= 0:
            self.canvas.delete("all")
            if self.bg_hwnd:
                position_child(self.bg_hwnd, 0, 0, 1, 1)

        self.root.after(FRAME_MS, self._animate)

    # ── Polling ──────────────────────────────────────────────────────────

    def _read_state(self):
        try:
            data = json.loads(STATE_FILE.read_text())
            new_pct = min(100, max(0, data.get("used_percentage", 0)))
            if abs(new_pct - self.usage_percent) > 2:
                self.usage_percent = new_pct
                self._respawn_for_new_width()
            else:
                self.usage_percent = new_pct
            self.resets_at = data.get("resets_at")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def _respawn_for_new_width(self):
        if not self.taskbar_rect:
            return
        left, _, right, _ = self.taskbar_rect
        tb_w = right - left
        fill_w = max(1, tb_w * (self.usage_percent / 100.0))
        for b in self.bubbles:
            b.max_x = fill_w
            if b.x > fill_w:
                b.x = random.uniform(0, fill_w)

    def _update_tray(self):
        reset_str = ""
        if self.resets_at:
            try:
                if isinstance(self.resets_at, str):
                    reset_dt = datetime.fromisoformat(self.resets_at)
                else:
                    reset_dt = datetime.fromtimestamp(self.resets_at, tz=timezone.utc)
                delta = reset_dt - datetime.now(timezone.utc)
                secs = max(0, int(delta.total_seconds()))
                h, rem = divmod(secs, 3600)
                m = rem // 60
                reset_str = f" \u2014 resets {h}h{m}m"
            except (ValueError, TypeError, OSError):
                pass

        pct = self._get_display_percent()
        if not self.enabled:
            status = "off"
        else:
            drain = self.config.get("drain_mode", False)
            label = f"{pct:.0f}% remaining" if drain else f"{pct:.0f}% used"
            weekly = f" | 7d: {self.seven_day_percent:.0f}%" if self.seven_day_percent else ""
            status = f"5h: {label}{reset_str}{weekly}"
        self.tray_icon.title = f"Claude: {status}"
        self.tray_icon.icon = self._create_tray_icon(pct if self.enabled else 0)

    def _poll_loop(self):
        """Main poll loop: API every 30s, state file every 2s as fallback."""
        api_counter = 0
        while self.running:
            # Try OAuth API every 60 seconds
            if api_counter <= 0:
                try:
                    usage = self.oauth_poller.poll_once()
                    if usage:
                        five = usage.get("five_hour", {})
                        seven = usage.get("seven_day", {})
                        new_pct = min(100, max(0, five.get("utilization", 0)))
                        if abs(new_pct - self.usage_percent) > 2:
                            self.usage_percent = new_pct
                            self._respawn_for_new_width()
                        else:
                            self.usage_percent = new_pct
                        self.seven_day_percent = seven.get("utilization", 0)
                        self.resets_at = five.get("resets_at")
                        api_counter = API_POLL_INTERVAL // POLL_INTERVAL_SECONDS
                    else:
                        # API failed — fall back to state file, wait full interval
                        self._read_state()
                        api_counter = API_POLL_INTERVAL // POLL_INTERVAL_SECONDS
                except Exception:
                    self._read_state()
                    api_counter = API_POLL_INTERVAL // POLL_INTERVAL_SECONDS
            else:
                api_counter -= 1

            _, new_rect = get_taskbar_info()
            if new_rect:
                self.taskbar_rect = new_rect
            if self.running:
                self.root.after(0, self._update_tray)
            time.sleep(POLL_INTERVAL_SECONDS)

    # ── Run ──────────────────────────────────────────────────────────────

    def run(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        self._animate()
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False


def main():
    if sys.platform != "win32":
        print("This app only works on Windows.")
        sys.exit(1)

    log_file = Path.home() / ".claude" / "usage-bar.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
        ],
    )

    log.info("Starting Claude Usage Bar v5")
    log.info("Polling OAuth API every %ds, state file: %s", API_POLL_INTERVAL, STATE_FILE)

    app = UsageOverlay()
    app.run()


if __name__ == "__main__":
    main()
