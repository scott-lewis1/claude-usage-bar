"""Bubble particle with physics for the overlay animation."""

import math
import random

from .config import BUBBLE_COLORS


class Bubble:
    """A single animated bubble that drifts across the fill area."""

    def __init__(self, max_x: float, max_y: float, speed: float):
        self.max_x = max_x
        self.max_y = max_y
        self.thin = max_y < 30

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
        self.phase = random.uniform(0, math.pi * 2)
        self.osc_amp = random.uniform(0.2, 0.6)

    def update(self, fill_width: float):
        """Advance one frame of physics."""
        self.phase += 0.05

        if self.thin:
            self.x += self.drift_x * 2 + math.sin(self.phase) * 0.3
            self.y += math.cos(self.phase * 1.3) * 0.15
            if self.x < -self.radius or self.x > fill_width + self.radius:
                self.x = random.uniform(0, max(1, fill_width))
                self.y = random.uniform(0, max(1, self.max_y))
                self.color = random.choice(BUBBLE_COLORS)
        else:
            self.x += self.speed
            self.y += math.sin(self.phase) * 0.2
            self.y = max(self.radius, min(self.max_y - self.radius, self.y))
            if self.x > fill_width + self.radius:
                self.x = -self.radius
                self.y = random.uniform(
                    self.radius, max(self.radius + 1, self.max_y - self.radius))
                self.color = random.choice(BUBBLE_COLORS)
                self.radius = random.uniform(2, 5)
