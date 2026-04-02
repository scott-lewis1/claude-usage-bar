"""OAuth usage API poller — fetches Claude Max usage data."""

import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .config import CREDENTIALS_FILE, STATE_FILE

log = logging.getLogger("claude_usage_bar.poller")

CLAUDE_CODE_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
TOKEN_ENDPOINT = "https://api.anthropic.com/v1/oauth/token"
USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"


class OAuthPoller:
    """Polls Anthropic's OAuth usage API and writes state for the overlay."""

    def __init__(self):
        self._access_token = None
        self._refresh_token = None
        self._token_expires_at = 0.0
        self._rate_limit_until = 0.0
        self._consecutive_429s = 0
        self.last_usage = None
        self._load_credentials()

    def _load_credentials(self) -> bool:
        try:
            data = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
            oauth = data.get("claudeAiOauth", {})
            self._refresh_token = oauth.get("refreshToken")
            self._access_token = oauth.get("accessToken")
            expires_ms = oauth.get("expiresAt", 0)
            self._token_expires_at = expires_ms / 1000 if expires_ms else 0
            return bool(self._refresh_token)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load credentials: %s", e)
            return False

    def _refresh_access_token(self) -> bool:
        if not self._refresh_token:
            return False
        payload = json.dumps({
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": CLAUDE_CODE_CLIENT_ID,
        }).encode("utf-8")
        req = Request(TOKEN_ENDPOINT, data=payload,
                      headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 28800)
            new_refresh = data.get("refresh_token")
            if new_refresh:
                self._refresh_token = new_refresh
                self._save_credentials(data)
            self._consecutive_429s = 0
            self._rate_limit_until = 0
            log.info("OAuth token refreshed (new token)")
            return True
        except (HTTPError, URLError, json.JSONDecodeError, KeyError) as e:
            log.warning("Token refresh failed: %s", e)
            return False

    def _save_credentials(self, token_data: dict):
        try:
            existing = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = {}
        oauth = existing.setdefault("claudeAiOauth", {})
        oauth["accessToken"] = token_data["access_token"]
        oauth["refreshToken"] = token_data.get("refresh_token", self._refresh_token)
        oauth["expiresAt"] = int(self._token_expires_at * 1000)
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(CREDENTIALS_FILE.parent), suffix=".tmp")
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(existing, f)
            if sys.platform == "win32" and CREDENTIALS_FILE.exists():
                CREDENTIALS_FILE.unlink()
            os.rename(tmp_path, str(CREDENTIALS_FILE))
        except OSError:
            pass

    def _ensure_valid_token(self) -> bool:
        if self._access_token and time.time() < (self._token_expires_at - 300):
            return True
        return self._refresh_access_token()

    def fetch_usage(self) -> dict | None:
        """Fetch usage data from the API. Returns raw API response or None."""
        if time.time() < self._rate_limit_until:
            return None
        if not self._ensure_valid_token():
            return None

        req = Request(USAGE_ENDPOINT, headers={
            "Authorization": f"Bearer {self._access_token}",
            "anthropic-beta": "oauth-2025-04-20",
        }, method="GET")

        try:
            with urlopen(req, timeout=15) as resp:
                self._consecutive_429s = 0
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 401:
                if self._refresh_access_token():
                    return self.fetch_usage()
            if e.code == 429:
                self._consecutive_429s += 1
                if self._consecutive_429s <= 2:
                    log.info("429 — refreshing token to escape rate limit")
                    if self._refresh_access_token():
                        return self.fetch_usage()
                backoff = min(600, 60 * (2 ** (self._consecutive_429s - 2)))
                log.warning("Rate limited (attempt %d), backoff %ds",
                            self._consecutive_429s, backoff)
                self._rate_limit_until = time.time() + backoff
                return None
            log.warning("Usage fetch failed: %s", e)
            return None
        except (URLError, json.JSONDecodeError) as e:
            log.warning("Usage fetch failed: %s", e)
            return None

    def write_state(self, usage: dict):
        """Write parsed usage to the state file."""
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
                dir=str(STATE_FILE.parent), suffix=".tmp")
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f)
            if sys.platform == "win32" and STATE_FILE.exists():
                STATE_FILE.unlink()
            os.rename(tmp_path, str(STATE_FILE))
        except OSError:
            try:
                STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
            except OSError:
                pass
        self.last_usage = state

    def poll_once(self) -> dict | None:
        """Fetch and write. Returns raw API response or None."""
        usage = self.fetch_usage()
        if usage:
            self.write_state(usage)
        return usage
