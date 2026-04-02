#!/usr/bin/env python3
"""
Claude Usage Poller — polls the Anthropic OAuth usage API directly.

Replaces the broken statusLine hook on Windows by:
1. Reading the refresh token from ~/.claude/.credentials.json
2. Refreshing the OAuth access token via Anthropic's API
3. Fetching usage data from the OAuth usage endpoint
4. Writing to ~/.claude/usage-bar-state.json for the overlay to read

Runs as a background daemon, polling every 60 seconds.
"""

import json
import os
import sys
import time
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ── Paths ──────────────────────────────────────────────────────────────
CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"
STATE_FILE = Path.home() / ".claude" / "usage-bar-state.json"
LOG_FILE = Path.home() / ".claude" / "usage-poller.log"

# ── Constants ──────────────────────────────────────────────────────────
CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_ENDPOINT = "https://api.anthropic.com/v1/oauth/token"
USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
POLL_INTERVAL = 60  # seconds
TOKEN_REFRESH_BUFFER = 300  # refresh 5 min before expiry

# ── Logging ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(str(LOG_FILE), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("usage_poller")

# ── Token state (in-memory) ───────────────────────────────────────────
_access_token: str | None = None
_refresh_token: str | None = None
_token_expires_at: float = 0  # unix timestamp


def load_credentials() -> bool:
    """Load refresh token from credentials file."""
    global _refresh_token, _access_token, _token_expires_at
    try:
        data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        oauth = data.get("claudeAiOauth", {})
        _refresh_token = oauth.get("refreshToken")
        # Also load access token in case it's still valid
        _access_token = oauth.get("accessToken")
        expires_at_ms = oauth.get("expiresAt", 0)
        _token_expires_at = expires_at_ms / 1000 if expires_at_ms else 0
        if not _refresh_token:
            log.error("No refreshToken found in credentials file")
            return False
        log.info("Loaded credentials (refresh token present)")
        return True
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to load credentials: %s", e)
        return False


def refresh_access_token() -> bool:
    """Refresh the OAuth access token using the refresh token."""
    global _access_token, _refresh_token, _token_expires_at
    if not _refresh_token:
        log.error("No refresh token available")
        return False

    payload = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": _refresh_token,
        "client_id": CLAUDE_CODE_CLIENT_ID,
    }).encode("utf-8")

    req = Request(
        TOKEN_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        _access_token = data["access_token"]
        _token_expires_at = time.time() + data.get("expires_in", 28800)
        # Save the new refresh token (it rotates on each refresh)
        new_refresh = data.get("refresh_token")
        if new_refresh:
            _refresh_token = new_refresh
            _save_credentials(data)
        log.info("Token refreshed, expires in %ds", data.get("expires_in", 0))
        return True
    except (HTTPError, URLError, json.JSONDecodeError, KeyError) as e:
        log.error("Token refresh failed: %s", e)
        return False


def _save_credentials(token_data: dict):
    """Update the credentials file with fresh tokens."""
    try:
        existing = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        existing = {}

    oauth = existing.setdefault("claudeAiOauth", {})
    oauth["accessToken"] = token_data["access_token"]
    oauth["refreshToken"] = token_data.get("refresh_token", _refresh_token)
    oauth["expiresAt"] = int(_token_expires_at * 1000)

    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(CREDENTIALS_FILE.parent), suffix=".tmp"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(existing, f)
        if sys.platform == "win32" and CREDENTIALS_FILE.exists():
            CREDENTIALS_FILE.unlink()
        os.rename(tmp_path, str(CREDENTIALS_FILE))
        log.info("Saved updated credentials to disk")
    except OSError as e:
        log.warning("Failed to save credentials: %s", e)


def ensure_valid_token() -> bool:
    """Make sure we have a non-expired access token."""
    now = time.time()
    if _access_token and now < (_token_expires_at - TOKEN_REFRESH_BUFFER):
        return True  # still valid
    log.info("Access token expired or expiring soon, refreshing...")
    return refresh_access_token()


def fetch_usage() -> dict | None:
    """Fetch usage data from the OAuth usage endpoint."""
    if not ensure_valid_token():
        return None

    req = Request(
        USAGE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {_access_token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        if e.code == 401:
            log.warning("401 — forcing token refresh")
            if refresh_access_token():
                return fetch_usage()  # retry once
        log.error("Usage fetch failed: %s %s", e.code, e.reason)
        return None
    except (URLError, json.JSONDecodeError) as e:
        log.error("Usage fetch failed: %s", e)
        return None


def write_state(usage: dict):
    """Write usage state for the overlay app."""
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
            dir=str(STATE_FILE.parent), suffix=".tmp"
        )
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        if sys.platform == "win32" and STATE_FILE.exists():
            STATE_FILE.unlink()
        os.rename(tmp_path, str(STATE_FILE))
    except OSError:
        try:
            STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        except OSError as e:
            log.error("Failed to write state file: %s", e)


def main():
    log.info("=== Claude Usage Poller starting ===")

    if not load_credentials():
        log.error("Cannot start without credentials. Run 'claude' to authenticate first.")
        sys.exit(1)

    log.info("Polling every %ds, writing to %s", POLL_INTERVAL, STATE_FILE)

    consecutive_failures = 0
    while True:
        try:
            usage = fetch_usage()
            if usage:
                write_state(usage)
                pct = usage.get("five_hour", {}).get("utilization", "?")
                log.info("Usage: %s%% (5h)", pct)
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    log.warning("5 consecutive failures, reloading credentials...")
                    load_credentials()
                    consecutive_failures = 0
        except Exception:
            log.exception("Unexpected error in poll loop")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
