# Claude Usage Bar

A Windows taskbar overlay that shows your Claude Max 5-hour session usage as an animated progress bar at the bottom of your taskbar.

## How it works

- Polls the Anthropic OAuth usage API every 30 seconds using your Claude Code credentials
- Renders a thin colored bar at the bottom of the Windows taskbar that fills left-to-right as you use tokens
- Animated bubbles drift along the bar for visual flair
- System tray icon shows live usage % on hover (5-hour + 7-day)
- **Drain mode**: bar starts full and empties as you consume your allocation

## Setup

### Requirements

- Windows 10/11
- Python 3.10+
- Claude Code authenticated (creates `~/.claude/.credentials.json`)

### Install

```
pip install pystray Pillow
```

### Run

Double-click `Claude Usage Bar.pyw` or:

```
pythonw "Claude Usage Bar.pyw"
```

### Launch on boot

Copy `launch_silent.vbs` to your Startup folder:

```
copy launch_silent.vbs "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\"
```

> **Note**: Edit the Python path in `launch_silent.vbs` to match your system.

## Settings

Right-click the system tray icon (orange circle) and select **Settings**.

| Setting | Description |
|---|---|
| **Drain mode** | Inverts the bar: starts full, empties as usage grows |
| **Background fill** | Enable/disable, pick color, adjust opacity |
| **Bar height** | Thickness of the progress bar (2-12px) |
| **Bubbles** | Opacity, count, and speed of animated dots |

## Architecture

| File | Purpose |
|---|---|
| `claude_usage_bar.py` | Main app: overlay rendering, API polling, settings UI |
| `Claude Usage Bar.pyw` | Double-click launcher (no console window) |
| `statusline_hook.py` | Claude Code statusLine hook (backup data source) |
| `launch_silent.vbs` | Silent boot launcher for Windows Startup |

### Data flow

1. App reads OAuth refresh token from `~/.claude/.credentials.json`
2. Refreshes access token via `POST api.anthropic.com/v1/oauth/token`
3. Polls `GET api.anthropic.com/api/oauth/usage` every 30s
4. Writes state to `~/.claude/usage-bar-state.json`
5. Overlay reads state and renders the bar + bubbles

### Why not use the statusLine hook?

The Claude Code [statusLine feature is broken on Windows](https://github.com/anthropics/claude-code/issues/27161) as of early 2026. This app bypasses it entirely by calling the API directly.

## License

MIT
