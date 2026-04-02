"""Settings window — built with custom Canvas widgets."""

import tkinter as tk
from tkinter import colorchooser

from .config import Config, THEME as T
from .widgets import Card, ToggleRow, Slider, RoundButton


class SettingsWindow:
    """Settings panel Toplevel with all controls."""

    def __init__(self, root: tk.Tk, config: Config, on_save, on_change):
        self._config = config
        self._on_save = on_save
        self._on_change = on_change
        self._win = None
        self.open(root)

    @property
    def is_open(self) -> bool:
        return self._win is not None and self._win.winfo_exists()

    def lift(self):
        if self.is_open:
            self._win.lift()

    def open(self, root: tk.Tk):
        if self.is_open:
            self.lift()
            return

        win = tk.Toplevel(root)
        win.title("Claude Usage Bar")
        win.geometry("340x740")
        win.resizable(False, False)
        win.configure(bg=T["bg"])
        self._win = win

        # Header
        hdr = tk.Frame(win, bg=T["bg"])
        hdr.pack(fill="x", padx=20, pady=(20, 4))
        tk.Label(hdr, text="Claude Usage Bar", bg=T["bg"], fg=T["text"],
                 font=("Segoe UI Semibold", 14), anchor="w").pack(side="left")

        tk.Frame(win, bg=T["border"], height=1).pack(fill="x", padx=20, pady=(8, 12))

        body = tk.Frame(win, bg=T["bg"])
        body.pack(fill="both", expand=True)

        # Display
        mode_card = Card(body, "Display")
        self._drain_var = tk.BooleanVar(value=self._config.get("drain_mode", False))
        ToggleRow(mode_card, "Drain mode", self._drain_var,
                  command=lambda: self._set("drain_mode", self._drain_var.get()),
                  hint="Starts full, empties as you use tokens")

        # Background
        bg_card = Card(body, "Background fill")
        self._bg_var = tk.BooleanVar(value=self._config["bg_enabled"])

        bg_row = tk.Frame(bg_card, bg=T["surface"])
        bg_row.pack(fill="x", pady=2)
        tk.Label(bg_row, text="Enabled", bg=T["surface"], fg=T["text"],
                 font=("Segoe UI", 10)).pack(side="left")

        self._swatch = tk.Canvas(bg_row, width=22, height=22, bg=T["surface"],
                                  highlightthickness=0, bd=0, cursor="hand2")
        self._swatch.create_oval(2, 2, 20, 20,
                                  fill=self._config["bg_color"], outline=T["border"])
        self._swatch.bind("<Button-1>", lambda e: self._pick_color())
        self._swatch.pack(side="right", padx=(6, 0))

        from .widgets import Toggle
        Toggle(bg_row, self._bg_var,
               command=lambda: self._set("bg_enabled", self._bg_var.get()),
               bg=T["surface"]).pack(side="right")

        self._bg_opacity_var = tk.IntVar(value=self._config["bg_opacity"])
        Slider(bg_card, "Opacity", self._bg_opacity_var, 5, 255,
               lambda v: self._set("bg_opacity", int(v)))

        self._bar_h_var = tk.IntVar(value=self._config.get("bar_height", 14))
        Slider(bg_card, "Bar height (0 = full)", self._bar_h_var, 0, 48,
               lambda v: self._set("bar_height", int(v)))

        # Bubbles
        bub_card = Card(body, "Bubbles")
        self._bub_opacity_var = tk.IntVar(value=self._config["bubble_opacity"])
        Slider(bub_card, "Opacity", self._bub_opacity_var, 5, 120,
               lambda v: self._set("bubble_opacity", int(v)))

        self._bub_count_var = tk.IntVar(value=self._config["bubble_count"])
        Slider(bub_card, "Count", self._bub_count_var, 0, 40,
               lambda v: self._set("bubble_count", int(v)))

        self._speed_var = tk.DoubleVar(value=self._config["bubble_speed"])
        Slider(bub_card, "Speed", self._speed_var, 0.1, 2.0,
               lambda v: self._set("bubble_speed", float(v)), resolution=0.1)

        # Footer
        foot = tk.Frame(win, bg=T["bg"])
        foot.pack(fill="x", padx=20, pady=(4, 20))
        RoundButton(foot, "Save & Close", self._save)

    def _set(self, key, value):
        self._config[key] = value
        self._on_change(key, value)

    def _pick_color(self):
        color = colorchooser.askcolor(
            initialcolor=self._config["bg_color"], title="Background Color")
        if color and color[1]:
            self._config["bg_color"] = color[1]
            self._swatch.delete("all")
            self._swatch.create_oval(2, 2, 20, 20,
                                      fill=color[1], outline=T["border"])
            self._on_change("bg_color", color[1])

    def _save(self):
        self._on_save()
        if self.is_open:
            self._win.destroy()
            self._win = None
