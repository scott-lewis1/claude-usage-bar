"""Main application — orchestrates all components."""

import json
import logging
import random
import sys
import threading
import time
import tkinter as tk

from .config import (
    Config, STATE_FILE, LOG_FILE, FRAME_MS, POLL_INTERVAL_SECONDS,
    API_POLL_INTERVAL,
)
from .win32 import Taskbar
from .overlay import OverlayWindow
from .poller import OAuthPoller
from .tray import TrayIcon
from .settings_ui import SettingsWindow

log = logging.getLogger("claude_usage_bar")


class UsageBarApp:
    """Top-level application that wires everything together."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self.config = Config()
        self.config.save()

        self.usage_percent = 0.0
        self.seven_day_percent = 0.0
        self.resets_at = None
        self.running = True
        self.enabled = self.config["enabled"]

        self.taskbar = Taskbar()
        if not self.taskbar.rect:
            log.error("Could not find taskbar")
            sys.exit(1)

        self.poller = OAuthPoller()
        self.overlay = OverlayWindow(self.root, self.config, self.taskbar)
        self.tray = TrayIcon(
            on_toggle=self._on_toggle,
            on_settings=lambda: self.root.after(0, self._open_settings),
            on_quit=self._on_quit,
        )

        self._settings_win = None

    # ── Display ──────────────────────────────────────────────────────

    def _get_display_percent(self) -> float:
        raw = min(100, max(0, self.usage_percent))
        if self.config.get("drain_mode", False):
            return 100 - raw
        return raw

    # ── Tray callbacks ───────────────────────────────────────────────

    def _on_toggle(self):
        self.enabled = not self.enabled
        self.config["enabled"] = self.enabled
        self.config.save()
        if self.enabled:
            self.root.after(0, self.overlay.show)
        else:
            self.root.after(0, self.overlay.hide)

    def _open_settings(self):
        if self._settings_win and self._settings_win.is_open:
            self._settings_win.lift()
            return
        self._settings_win = SettingsWindow(
            self.root, self.config,
            on_save=self.config.save,
            on_change=self._on_setting_changed,
        )

    def _on_setting_changed(self, key, value):
        if key in ("bubble_count", "bar_height", "drain_mode"):
            pct = self._get_display_percent()
            fill_w = max(1, self.taskbar.width * (pct / 100.0))
            self.overlay.spawn_bubbles(fill_w)
        elif key == "bubble_speed":
            for b in self.overlay.bubbles:
                b.speed = random.uniform(float(value) * 0.5, float(value) * 1.5)

    def _on_quit(self):
        self.running = False
        self.tray.stop()
        self.root.after(100, self.root.destroy)

    # ── Animation ────────────────────────────────────────────────────

    def _animate(self):
        if not self.running:
            return

        pct = self._get_display_percent()
        if self.enabled and pct > 0:
            self.overlay.animate(pct)
        elif not self.enabled or pct <= 0:
            self.overlay.hide()

        self.root.after(FRAME_MS, self._animate)

    # ── Polling ──────────────────────────────────────────────────────

    def _read_state(self):
        try:
            data = json.loads(STATE_FILE.read_text())
            new_pct = min(100, max(0, data.get("used_percentage", 0)))
            if abs(new_pct - self.usage_percent) > 2:
                self.usage_percent = new_pct
                self.overlay.respawn_for_new_width(new_pct)
            else:
                self.usage_percent = new_pct
            self.resets_at = data.get("resets_at")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def _poll_loop(self):
        api_counter = 0
        while self.running:
            if api_counter <= 0:
                try:
                    usage = self.poller.poll_once()
                    if usage:
                        five = usage.get("five_hour", {})
                        seven = usage.get("seven_day", {})
                        new_pct = min(100, max(0, five.get("utilization", 0)))
                        if abs(new_pct - self.usage_percent) > 2:
                            self.usage_percent = new_pct
                            self.overlay.respawn_for_new_width(new_pct)
                        else:
                            self.usage_percent = new_pct
                        self.seven_day_percent = seven.get("utilization", 0)
                        self.resets_at = five.get("resets_at")
                        api_counter = API_POLL_INTERVAL // POLL_INTERVAL_SECONDS
                    else:
                        self._read_state()
                        api_counter = API_POLL_INTERVAL // POLL_INTERVAL_SECONDS
                except Exception:
                    self._read_state()
                    api_counter = API_POLL_INTERVAL // POLL_INTERVAL_SECONDS
            else:
                api_counter -= 1

            self.taskbar.refresh()
            if self.running:
                pct = self._get_display_percent()
                self.root.after(0, lambda: self.tray.update(
                    pct, self.enabled,
                    self.config.get("drain_mode", False),
                    self.seven_day_percent, self.resets_at))
            time.sleep(POLL_INTERVAL_SECONDS)

    # ── Run ──────────────────────────────────────────────────────────

    def run(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.tray.start()
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

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(str(LOG_FILE), encoding="utf-8")],
    )
    log.info("Starting Claude Usage Bar v6 (OOP)")
    log.info("Polling OAuth API every %ds", API_POLL_INTERVAL)

    app = UsageBarApp()
    app.run()
