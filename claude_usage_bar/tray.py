"""System tray icon with usage tooltip and menu."""

import threading
from datetime import datetime, timezone

from PIL import Image, ImageDraw
import pystray


class TrayIcon:
    """Manages the system tray icon, tooltip, and right-click menu."""

    def __init__(self, on_toggle, on_settings, on_quit):
        self._on_toggle = on_toggle
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._enabled = True

        menu = pystray.Menu(
            pystray.MenuItem("Claude Usage Bar", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Disable" if self._enabled else "Enable",
                self._handle_toggle,
            ),
            pystray.MenuItem("Settings...", self._handle_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._handle_quit),
        )
        self._icon = pystray.Icon(
            "claude_usage", self._render_icon(0),
            "Claude: waiting for data...", menu,
        )

    def start(self):
        """Run the tray icon in a daemon thread."""
        threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self):
        self._icon.stop()

    def update(self, pct: float, enabled: bool, drain: bool,
               seven_day: float, resets_at):
        """Update tooltip and icon."""
        self._enabled = enabled

        reset_str = ""
        if resets_at:
            try:
                if isinstance(resets_at, str):
                    reset_dt = datetime.fromisoformat(resets_at)
                else:
                    reset_dt = datetime.fromtimestamp(resets_at, tz=timezone.utc)
                delta = reset_dt - datetime.now(timezone.utc)
                secs = max(0, int(delta.total_seconds()))
                h, rem = divmod(secs, 3600)
                m = rem // 60
                reset_str = f" \u2014 resets {h}h{m}m"
            except (ValueError, TypeError, OSError):
                pass

        if not enabled:
            status = "off"
        else:
            label = f"{pct:.0f}% remaining" if drain else f"{pct:.0f}% used"
            weekly = f" | 7d: {seven_day:.0f}%" if seven_day else ""
            status = f"5h: {label}{reset_str}{weekly}"

        self._icon.title = f"Claude: {status}"
        self._icon.icon = self._render_icon(pct if enabled else 0)

    def _render_icon(self, percent: float) -> Image.Image:
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

    def _handle_toggle(self, icon, item):
        self._on_toggle()

    def _handle_settings(self, icon, item):
        self._on_settings()

    def _handle_quit(self, icon, item):
        self._on_quit()
