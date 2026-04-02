"""Wave edge renderer — draws an undulating polygon on a tkinter Canvas."""

import math
import tkinter as tk


class WaveRenderer:
    """Draws an animated wave edge at the fill boundary."""

    def __init__(self, amplitude: int = 8):
        self.amplitude = amplitude
        self.phase = 0.0

    def advance(self, dt: float = 0.025):
        """Advance the wave phase."""
        self.phase += dt

    def draw(self, canvas: tk.Canvas, fill_w: float, bar_h: float, color: str):
        """Draw the solid fill + wavy right edge on the canvas."""
        amp = self.amplitude
        solid_end = max(0, fill_w - amp)

        if solid_end > 0:
            canvas.create_rectangle(0, 0, solid_end, bar_h,
                                    fill=color, outline="")

        pts = [(solid_end, 0)]
        steps = max(20, int(bar_h) * 2)
        for i in range(steps + 1):
            y = (i / steps) * bar_h
            wx = (fill_w
                  + math.sin(self.phase + y * 0.06) * amp * 0.5
                  + math.sin(self.phase * 0.6 + y * 0.12) * amp * 0.25)
            pts.append((wx, y))
        pts.append((solid_end, bar_h))

        flat = [c for p in pts for c in p]
        canvas.create_polygon(flat, fill=color, outline="")
