"""Overlay window — renders the fill bar, wave edge, and bubbles on the taskbar."""

import random
import tkinter as tk

from .config import Config, CHROMA_KEY, CHROMA_KEY_RGB
from .win32 import Taskbar
from .bubble import Bubble
from .wave import WaveRenderer


class OverlayWindow:
    """Creates and manages the overlay windows reparented into the taskbar."""

    def __init__(self, root: tk.Tk, config: Config, taskbar: Taskbar):
        self.config = config
        self.taskbar = taskbar
        self.bubbles: list[Bubble] = []
        self.wave = WaveRenderer(amplitude=8)

        self._bg_hwnd = None
        self._bubble_hwnd = None
        self._canvas = None

        tb_w = taskbar.width
        tb_h = taskbar.height
        bar_h, bar_y = self._bar_geometry()
        left = taskbar.rect[0]
        top = taskbar.rect[1]

        # Background window
        self._bg_win = tk.Toplevel(root)
        self._bg_win.overrideredirect(True)
        self._bg_win.configure(bg=config["bg_color"])
        self._bg_win.geometry(f"1x{bar_h}+{left}+{top + bar_y}")
        self._bg_hwnd = taskbar.reparent_child(self._bg_win)
        if self._bg_hwnd:
            Taskbar.set_opacity(self._bg_hwnd, config["bg_opacity"])
            Taskbar.position_child(self._bg_hwnd, 0, bar_y, 1, bar_h)

        # Bubble/canvas window
        self._bubble_win = tk.Toplevel(root)
        self._bubble_win.overrideredirect(True)
        self._bubble_win.configure(bg=CHROMA_KEY)
        self._bubble_win.geometry(f"{tb_w}x{bar_h}+{left}+{top + bar_y}")

        self._canvas = tk.Canvas(
            self._bubble_win, width=tb_w, height=bar_h,
            bg=CHROMA_KEY, highlightthickness=0, bd=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._bubble_hwnd = taskbar.reparent_child(self._bubble_win)
        if self._bubble_hwnd:
            Taskbar.set_chroma_key(
                self._bubble_hwnd, CHROMA_KEY_RGB, config["bubble_opacity"])
            Taskbar.position_child(self._bubble_hwnd, 0, bar_y, tb_w, bar_h)

        self.spawn_bubbles(0)

    def _bar_geometry(self) -> tuple[int, int]:
        """Returns (bar_h, bar_y) based on config."""
        tb_h = self.taskbar.height
        cfg_h = self.config.get("bar_height", 0)
        bar_h = tb_h if cfg_h <= 0 else max(2, cfg_h)
        bar_y = tb_h - bar_h
        return bar_h, bar_y

    def spawn_bubbles(self, fill_w: float):
        bar_h, _ = self._bar_geometry()
        self.bubbles = [
            Bubble(max(1, fill_w), bar_h, self.config["bubble_speed"])
            for _ in range(self.config["bubble_count"])
        ]

    def animate(self, pct: float):
        """Render one frame of the overlay."""
        bar_h, bar_y = self._bar_geometry()
        tb_w = self.taskbar.width
        fill_w = max(1, int(tb_w * (pct / 100.0)))

        # Hide bg — everything on single canvas
        if self._bg_hwnd:
            Taskbar.position_child(self._bg_hwnd, 0, 0, 1, 1)

        if self._bubble_hwnd:
            Taskbar.position_child(self._bubble_hwnd, 0, bar_y, tb_w, bar_h)
            opacity = (self.config["bg_opacity"]
                       if self.config["bg_enabled"]
                       else self.config["bubble_opacity"])
            Taskbar.set_chroma_key(self._bubble_hwnd, CHROMA_KEY_RGB, opacity)

        self.wave.advance()
        self._canvas.delete("all")

        if self.config["bg_enabled"]:
            self.wave.draw(self._canvas, fill_w, bar_h, self.config["bg_color"])

        for bubble in self.bubbles:
            bubble.update(fill_w)
            x, y, r = bubble.x, bubble.y, bubble.radius
            if 0 <= x <= fill_w + self.wave.amplitude and -r <= y <= bar_h + r:
                self._canvas.create_oval(
                    x - r, y - r, x + r, y + r,
                    fill=bubble.color, outline="")

    def hide(self):
        self._canvas.delete("all")
        if self._bg_hwnd:
            Taskbar.set_opacity(self._bg_hwnd, 0)
        if self._bubble_hwnd:
            Taskbar.set_chroma_key(self._bubble_hwnd, CHROMA_KEY_RGB, 0)

    def show(self):
        if self._bg_hwnd:
            Taskbar.set_opacity(self._bg_hwnd, self.config["bg_opacity"])
        if self._bubble_hwnd:
            Taskbar.set_chroma_key(
                self._bubble_hwnd, CHROMA_KEY_RGB, self.config["bubble_opacity"])

    def respawn_for_new_width(self, usage_pct: float):
        tb_w = self.taskbar.width
        fill_w = max(1, tb_w * (usage_pct / 100.0))
        for b in self.bubbles:
            b.max_x = fill_w
            if b.x > fill_w:
                b.x = random.uniform(0, fill_w)
