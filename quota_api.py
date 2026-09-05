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
from quota_model import number, parse_claude, parse_codex

CLAUDE_CLIENT = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
CODEX_CLIENT = "app_EMoamEEZ73f0CkXaXp7hrann"
PROVIDERS = {
    "claude": ("https://api.anthropic.com/api/oauth/usage", "https://platform.claude.com/v1/oauth/token", CLAUDE_CLIENT),
    "codex": ("https://chatgpt.com/backend-api/wham/usage", "https://auth.openai.com/oauth/token", CODEX_CLIENT),
}


class QuotaError(Exception):
    def __init__(self, message, status=None, retry_after=0):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


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


class Provider:
    def __init__(self, name, home=None, request=request_json):
        self.name, self.store, self.request = name, CredentialStore(name, home), request

    def refresh(self, creds):
        if not creds.get("refresh_token"):
            raise QuotaError("Sign-in needs renewal; run quota_auth.py")
        _, token_url, client = PROVIDERS[self.name]
        response = self.request(token_url, payload={
            "grant_type": "refresh_token", "refresh_token": creds["refresh_token"], "client_id": client,
        }, form=self.name == "codex")
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
        headers = {"Authorization": "Bearer " + creds["access_token"]}
        if self.name == "claude":
            headers["anthropic-beta"] = "oauth-2025-04-20"
        elif creds.get("account_id"):
            headers["ChatGPT-Account-Id"] = creds["account_id"]
        return self.request(PROVIDERS[self.name][0], headers=headers)
