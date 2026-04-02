"""Reusable Canvas-drawn UI widgets for the settings panel."""

import tkinter as tk

from .config import THEME as T


class Toggle(tk.Canvas):
    """Canvas-based toggle switch."""

    WIDTH, HEIGHT, PAD = 40, 22, 3

    def __init__(self, parent, var: tk.BooleanVar, command=None, bg=None):
        bg = bg or T["surface"]
        super().__init__(parent, width=self.WIDTH, height=self.HEIGHT,
                         bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._var = var
        self._command = command
        self.bind("<Button-1>", self._toggle)
        self._draw()

    def _draw(self):
        self.delete("all")
        W, H, PAD = self.WIDTH, self.HEIGHT, self.PAD
        on = self._var.get()
        fill = T["tog_on"] if on else T["tog_off"]
        # Rounded track
        pts = [H//2, 0, W-H//2, 0, W, 0, W, H//2, W, H-H//2, W, H,
               W-H//2, H, H//2, H, 0, H, 0, H-H//2, 0, H//2, 0, 0]
        self.create_polygon(pts, smooth=True, fill=fill, outline="")
        # Knob
        kr = (H - PAD * 2) // 2
        kx = W - PAD - kr if on else PAD + kr
        self.create_oval(kx - kr, H // 2 - kr, kx + kr, H // 2 + kr,
                         fill=T["knob"], outline="")

    def _toggle(self, _event=None):
        self._var.set(not self._var.get())
        self._draw()
        if self._command:
            self._command()


class Slider(tk.Frame):
    """Canvas-drawn slider with label and value display."""

    TRACK_W, TRACK_H, THUMB_R = 248, 6, 7

    def __init__(self, parent, label: str, var, lo, hi, command,
                 resolution=1, bg=None):
        bg = bg or T["surface"]
        super().__init__(parent, bg=bg)
        self.pack(fill="x", pady=(6, 2))

        self._var = var
        self._lo = lo
        self._hi = hi
        self._res = resolution
        self._command = command
        self._dragging = False

        PAD_X = self.THUMB_R + 2
        H = self.THUMB_R * 2 + 4
        fmt = (lambda v: f"{v:.1f}") if resolution < 1 else (lambda v: str(int(v)))
        self._fmt = fmt

        head = tk.Frame(self, bg=bg)
        head.pack(fill="x")
        tk.Label(head, text=label, bg=bg, fg=T["text2"],
                 font=("Segoe UI", 9), anchor="w").pack(side="left")
        self._val_lbl = tk.Label(head, text=fmt(var.get()), bg=bg,
                                  fg=T["text"], font=("Segoe UI", 9, "bold"))
        self._val_lbl.pack(side="right")

        self._canvas = tk.Canvas(self, width=self.TRACK_W, height=H, bg=bg,
                                  highlightthickness=0, bd=0, cursor="hand2")
        self._canvas.pack(fill="x", pady=(2, 0))
        self._canvas.bind("<Button-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _val_to_x(self, v):
        PAD_X = self.THUMB_R + 2
        W = self.TRACK_W
        frac = (v - self._lo) / max(1e-9, self._hi - self._lo)
        return PAD_X + frac * (W - 2 * PAD_X)

    def _x_to_val(self, x):
        PAD_X = self.THUMB_R + 2
        W = self.TRACK_W
        frac = max(0, min(1, (x - PAD_X) / max(1, W - 2 * PAD_X)))
        raw = self._lo + frac * (self._hi - self._lo)
        if self._res >= 1:
            return int(round(raw))
        return round(raw / self._res) * self._res

    def _draw(self):
        c = self._canvas
        c.delete("all")
        W = self.TRACK_W
        H = self.THUMB_R * 2 + 4
        PAD_X = self.THUMB_R + 2
        cy = H // 2

        c.create_line(PAD_X, cy, W - PAD_X, cy, fill=T["track_bg"],
                      width=self.TRACK_H, capstyle="round")
        tx = self._val_to_x(self._var.get())
        c.create_line(PAD_X, cy, tx, cy, fill=T["track_fg"],
                      width=self.TRACK_H, capstyle="round")
        R = self.THUMB_R
        c.create_oval(tx - R - 1, cy - R - 1, tx + R + 1, cy + R + 1,
                      fill="#000000", outline="", stipple="gray25")
        c.create_oval(tx - R, cy - R, tx + R, cy + R,
                      fill=T["knob"], outline=T["border"])
        self._val_lbl.config(text=self._fmt(self._var.get()))

    def _update(self, event):
        v = self._x_to_val(event.x)
        v = max(self._lo, min(self._hi, v))
        if self._res >= 1:
            self._var.set(int(v))
        else:
            self._var.set(round(v, 1))
        self._draw()
        self._command(v)

    def _on_press(self, e):
        self._dragging = True
        self._update(e)

    def _on_drag(self, e):
        if self._dragging:
            self._update(e)

    def _on_release(self, _e):
        self._dragging = False


class Card(tk.Frame):
    """Card container with border and section title."""

    def __init__(self, parent, title: str):
        outer = tk.Frame(parent, bg=T["border"], padx=1, pady=1)
        outer.pack(fill="x", padx=16, pady=(0, 10))
        super().__init__(outer, bg=T["surface"], padx=14, pady=10)
        self.pack(fill="both")
        tk.Label(self, text=title.upper(), bg=T["surface"], fg=T["text3"],
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(0, 6))


class ToggleRow(tk.Frame):
    """Row with label + optional hint on left, toggle on right."""

    def __init__(self, parent, label: str, var: tk.BooleanVar,
                 command=None, hint=None):
        super().__init__(parent, bg=T["surface"])
        self.pack(fill="x", pady=2)

        lbl_frame = tk.Frame(self, bg=T["surface"])
        lbl_frame.pack(side="left", fill="y")
        tk.Label(lbl_frame, text=label, bg=T["surface"], fg=T["text"],
                 font=("Segoe UI", 10), anchor="w").pack(anchor="w")
        if hint:
            tk.Label(lbl_frame, text=hint, bg=T["surface"], fg=T["text3"],
                     font=("Segoe UI", 8), anchor="w", wraplength=190,
                     ).pack(anchor="w")

        self.toggle = Toggle(self, var, command, bg=T["surface"])
        self.toggle.pack(side="right")


class RoundButton(tk.Canvas):
    """Canvas-drawn rounded button with hover effect."""

    def __init__(self, parent, text: str, command, height=40):
        super().__init__(parent, height=height, bg=T["bg"],
                         highlightthickness=0, bd=0, cursor="hand2")
        self.pack(fill="x")
        self._text = text
        self._command = command
        self._height = height
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._draw(hover=True))
        self.bind("<Leave>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self._command())
        self.after(50, self._draw)

    def _draw(self, hover=False):
        self.delete("all")
        w = self.winfo_width() or 290
        h = self._height
        fill = T["accent_hi"] if hover else T["accent"]
        r = 8
        self.create_polygon(
            r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h,
            w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0,
            smooth=True, fill=fill, outline="")
        self.create_text(w // 2, h // 2, text=self._text,
                         fill="white", font=("Segoe UI Semibold", 10))
