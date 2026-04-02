#!/usr/bin/env python3
"""
Claude Code Status Line Hook
Captures usage data from Claude Code's status line JSON and writes it
to a shared state file for the taskbar overlay app to read.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path.home() / ".claude" / "usage-bar-state.json"


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return

    # Debug: log raw input to diagnose hook issues
    debug_file = STATE_FILE.parent / "usage-bar-debug.json"
    try:
        debug_file.write_text(raw)
    except OSError:
        pass

    # Extract rate limit data
    rate_limits = data.get("rate_limits")
    if not rate_limits:
        # No rate limit data yet (before first API response) — print status only
        model = data.get("model", {}).get("display_name", "")
        ctx = data.get("context_window", {})
        ctx_pct = ctx.get("used_percentage") or 0
        print(f"{model} | ctx {ctx_pct}%")
        return

    five_hour = rate_limits.get("five_hour", {})
    used_pct = five_hour.get("used_percentage", 0)
    resets_at = five_hour.get("resets_at")

    # Write state file atomically (write to temp, then rename)
    state = {
        "used_percentage": used_pct,
        "resets_at": resets_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(STATE_FILE.parent), suffix=".tmp"
        )
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(state, f)
        # Atomic rename (Windows: need to remove target first)
        if sys.platform == "win32" and STATE_FILE.exists():
            STATE_FILE.unlink()
        os.rename(tmp_path, str(STATE_FILE))
    except OSError:
        # If atomic write fails, try direct write as fallback
        try:
            STATE_FILE.write_text(json.dumps(state))
        except OSError:
            pass

    # Print status line for Claude Code display
    model = data.get("model", {}).get("display_name", "")
    ctx_pct = data.get("context_window", {}).get("used_percentage") or 0
    reset_str = ""
    if resets_at:
        try:
            reset_dt = datetime.fromtimestamp(resets_at, tz=timezone.utc)
            delta = reset_dt - datetime.now(timezone.utc)
            secs = max(0, int(delta.total_seconds()))
            hours, remainder = divmod(secs, 3600)
            minutes = remainder // 60
            reset_str = f" | resets {hours}h{minutes}m"
        except (ValueError, TypeError, OSError):
            pass

    print(f"{model} | {used_pct:.0f}% used{reset_str} | ctx {ctx_pct}%")


if __name__ == "__main__":
    main()
