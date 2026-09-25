"""Account-only requests, adapted from fuziontech/claude-quota-display.

No inference calls or coding CLI dependencies. Unofficial endpoint contracts
are isolated here; credentials belong exclusively to this appliance.
"""
from __future__ import annotations

from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import fcntl
import json
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from quota_model import (CLAUDE_RESET_UNKNOWN, claude_reset_bank, number, parse_claude,
                         parse_codex, parse_reset_bank)

CLAUDE_CLIENT = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CODEX_CLIENT = "app_EMoamEEZ73f0CkXaXp7hrann"
PROVIDERS = {
    "claude": ("https://api.anthropic.com/api/oauth/usage", "https://platform.claude.com/v1/oauth/token", CLAUDE_CLIENT),
    "codex": ("https://chatgpt.com/backend-api/wham/usage", "https://auth.openai.com/oauth/token", CODEX_CLIENT),
}
# Claude limit-reset grants (cedar_ember) are reported only on this query, and
# only to Claude Code's CLI surface at or above a minimum version: other
# user agents receive ineligible_reason "surface" or "cli_version". Bump the
# version if the reset count starts reporting "cli_version".
CLAUDE_RESET_URL = PROVIDERS["claude"][0] + "?cedar_ember=1&skip_spend=1"
CLAUDE_CLI_AGENT = "claude-cli/2.1.282 (external, cli)"


class QuotaError(Exception):
    def __init__(self, message, status=None, retry_after=0, fallback=None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after
        self.fallback = fallback


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward account credentials through a redirect.
        return None


def retry_seconds(value, now=None):
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now))
        except (ValueError, TypeError, OverflowError):
            return 0


def request_json(url, *, payload=None, form=False, headers=None):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise QuotaError("HTTPS is required")
    hdr = {"Accept": "application/json", "User-Agent": "quota-strip/0.1"}
    hdr.update(headers or {})
    body = None
    if payload is not None:
        body = (urllib.parse.urlencode(payload) if form else json.dumps(payload)).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=body, headers=hdr)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=20) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise QuotaError("Unexpectedly large provider response")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise QuotaError("Unexpected provider response")
            return result
    except urllib.error.HTTPError as exc:
        delay = retry_seconds(exc.headers.get("Retry-After"))
        exc.close()
        label = {401: "Sign-in expired", 403: "Access denied", 429: "Provider rate limited"}.get(exc.code, "Provider request failed")
        raise QuotaError(f"{label} (HTTP {exc.code})", exc.code, delay) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise QuotaError("Network unavailable") from None
    except (ValueError, UnicodeError):
        raise QuotaError("Provider returned invalid JSON") from None


def data_home():
    return Path(os.environ.get("QUOTA_HOME", str(Path.home() / ".config/quota-strip"))).expanduser()


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".quota-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class CredentialStore:
    def __init__(self, provider, home=None):
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider")
        self.provider = provider
        self.home = Path(home) if home else data_home()
        self.path = self.home / f"{provider}-auth.json"

    @contextmanager
    def locked(self):
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.home / f"{self.provider}.lock", os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def read(self):
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
                raise ValueError()
            return data
        except (OSError, ValueError):
            raise QuotaError(f"Sign in: python3 quota_auth.py {self.provider}") from None

    def save(self, data):
        atomic_json(self.path, data)


def token_record(response, previous=None):
    record = dict(previous or {})
    access = response.get("access_token")
    if not isinstance(access, str) or not access:
        raise QuotaError("Token response has no access token")
    record["access_token"] = access
    for name in ("refresh_token", "account_id"):
        if isinstance(response.get(name), str) and response[name]:
            record[name] = response[name]
    expires = number(response.get("expires_in"))
    record["expires_at"] = time.time() + expires if expires is not None else None
    return record


class ClaudeResetGrants:
    """Claude limit-reset grants, read within the ordinary usage request.

    The usage endpoint rejects a second request from the same token in quick
    succession (HTTP 429), so grants are never fetched separately. At most every
    ten minutes the quota read itself uses the grant query, which returns the
    same quota fields plus cedar_ember. A failed grant-bearing read backs off
    the grant query, so the next quota attempt uses the plain request. Only
    status is read; resets are never claimed.
    """
    interval = 600

    def __init__(self, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.status = None
        self.retry_at = 0
        self.backoff = self.interval
        self.warning = None

    def read(self, request, token):
        headers = {"Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20"}
        if self.monotonic() < self.retry_at:
            data = request(PROVIDERS["claude"][0], headers=headers)
        else:
            try:
                data = request(CLAUDE_RESET_URL, headers={**headers, "User-Agent": CLAUDE_CLI_AGENT})
            except QuotaError as exc:
                if exc.status != 401:  # An expired token is not the grant query's fault.
                    self.failed(str(exc), exc.retry_after)
                raise
            self.record(data.get("cedar_ember"))
        if self.status is not None:
            data["cedar_ember"] = self.status
        if self.warning:
            data["_quota_strip_warning"] = self.warning
        return data

    def record(self, status):
        if isinstance(status, dict) and status.get("eligible") is False:
            reason = status.get("ineligible_reason")
            if not isinstance(reason, str) or reason in CLAUDE_RESET_UNKNOWN:
                return self.failed(f"not reported ({reason if isinstance(reason, str) else 'unknown'})")
        if claude_reset_bank(status, time.time()) is None:
            return self.failed("response changed")
        self.status, self.warning = status, None
        self.backoff = self.interval
        self.retry_at = self.monotonic() + self.interval

    def failed(self, reason, retry_after=0):
        self.status = None
        self.warning = f"Reset count unavailable: {reason}"
        self.backoff = min(self.backoff * 2, 3600)
        self.retry_at = self.monotonic() + max(self.backoff, retry_after)


class Provider:
    def __init__(self, name, home=None, request=request_json):
        self.name, self.store, self.request = name, CredentialStore(name, home), request
        self.reset_grants = ClaudeResetGrants() if name == "claude" else None
        self.reset_retry_at = 0
        self.reset_backoff = 120
        self.reset_warning = None

    def refresh(self, creds):
        if not creds.get("refresh_token"):
            raise QuotaError("Sign-in needs renewal; run quota_auth.py")
        _, token_url, client = PROVIDERS[self.name]
        payload = {"grant_type": "refresh_token", "refresh_token": creds["refresh_token"], "client_id": client}
        if self.name == "claude":
            payload["scope"] = "user:profile"
        response = self.request(token_url, payload=payload, form=self.name == "codex")
        updated = token_record(response, creds)
        self.store.save(updated)
        return updated

    def fetch(self):
        # Locks cover refresh + publication. CLI stores are never accessed.
        with self.store.locked():
            creds = self.store.read()
            expiry = number(creds.get("expires_at"))
            refreshed = expiry is not None and expiry <= time.time() + 60
            if refreshed:
                creds = self.refresh(creds)
            try:
                data = self.get_usage(creds)
            except QuotaError as exc:
                if exc.status != 401 or refreshed:
                    raise
                data = self.get_usage(self.refresh(creds))
        try:
            return (parse_claude if self.name == "claude" else parse_codex)(data, time.time())
        except (ValueError, TypeError, KeyError):
            raise QuotaError("Quota response changed; update collector") from None

    def get_usage(self, creds):
        if self.name == "claude":
            return self.reset_grants.read(self.request, creds["access_token"])
        headers = {"Authorization": "Bearer " + creds["access_token"]}
        if creds.get("account_id"):
            headers["ChatGPT-Account-Id"] = creds["account_id"]
        data = self.request(PROVIDERS[self.name][0], headers=headers)
        self.add_reset_details(data, headers)
        return data

    def add_reset_details(self, data, headers):
        """Read optional expiry details without delaying ordinary quota recovery."""
        bank = parse_reset_bank(data.get("rate_limit_reset_credits"))
        if bank is None:
            return
        if bank.available_count == 0:
            data["rate_limit_reset_credits"]["credits"] = []
            return
        if time.monotonic() < self.reset_retry_at:
            data["_quota_strip_warning"] = self.reset_warning
            return
        try:
            details = self.request("https://chatgpt.com/backend-api/wham/rate-limit-reset-credits", headers=headers)
            if parse_reset_bank(details) is None:
                raise QuotaError("Reset-bank response changed")
        except QuotaError as exc:
            self.reset_backoff = min(self.reset_backoff * 2, 1800)
            self.reset_retry_at = time.monotonic() + max(self.reset_backoff, exc.retry_after)
            self.reset_warning = f"Reset expiry unavailable: {exc}"
            data["_quota_strip_warning"] = self.reset_warning
        else:
            data["rate_limit_reset_credits"] = details
            self.reset_retry_at = 0
            self.reset_backoff = 120
            self.reset_warning = None
