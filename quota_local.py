"""Read-only integration with this Mac's existing Claude and Codex sign-ins."""
from __future__ import annotations
from dataclasses import replace
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

from quota_accounts import ACCOUNTS
from quota_api import ClaudeResetGrants, QuotaError, request_json
from quota_model import parse_claude, parse_codex, parse_cswap


def local_config(key):
    """A CLI path that setup-mac.sh recorded, for launches without a shell PATH."""
    try:
        return json.loads(Path(__file__).with_name(".local-config.json").read_text()).get(key)
    except (OSError, ValueError, AttributeError):
        return None


class LocalClaude:
    def __init__(self, monotonic=time.monotonic):
        self.monotonic = monotonic
        self.reset_grants = ClaudeResetGrants(monotonic=monotonic)
        self.next_attempt = 0
        self.backoff = 120
        self.last_error = None

    def fetch(self):
        now = self.monotonic()
        if now >= self.next_attempt:
            try:
                snapshot = self.account_usage()
                self.backoff = 120
                self.last_error = None
                return snapshot
            except QuotaError as exc:
                self.last_error = exc
                self.backoff = min(max(self.backoff * 2, 300 if exc.status == 429 else 0), 1800)
                self.next_attempt = self.monotonic() + max(self.backoff, exc.retry_after)
        original = self.last_error
        if original is not None:
            # The user's existing status line already saves the official JSON.
            # Read that capture without changing settings or invoking inference.
            path = Path.home() / ".claude/.debug/statusline-input.json"
            try:
                captured = json.loads(path.read_text()).get("rate_limits") or {}
                data = {}
                for key in ("five_hour", "seven_day"):
                    if isinstance(captured.get(key), dict):
                        w = captured[key]
                        data[key] = {"utilization": w.get("used_percentage"), "resets_at": w.get("resets_at")}
                snap = parse_claude(data, path.stat().st_mtime)
                fallback = replace(snap, source="status line", warning=str(original))
            except (OSError, ValueError, TypeError):
                raise original
            # A partial reading is still an account failure. Keep that signal
            # while allowing the status line to refresh during account backoff.
            raise QuotaError(str(original), original.status,
                             max(0, self.next_attempt - self.monotonic()), fallback) from None

    def account_usage(self):
        path = Path.home() / ".claude/.credentials.json"
        try:
            if path.exists():
                data = json.loads(path.read_text())
            else:
                result = subprocess.run(["/usr/bin/security", "find-generic-password",
                                         "-s", "Claude Code-credentials", "-w"],
                                        capture_output=True, text=True, timeout=15)
                if result.returncode:
                    raise QuotaError("Claude sign-in unavailable")
                data = json.loads(result.stdout)
            creds = data.get("claudeAiOauth") or {}
            token = creds.get("accessToken")
            if not token:
                raise QuotaError("Claude sign-in unavailable")
            expiry = creds.get("expiresAt")
            if isinstance(expiry, (int, float)) and expiry <= (time.time() + 60) * 1000:
                raise QuotaError("Claude sign-in expired; open Claude Code")
            return parse_claude(self.reset_grants.read(request_json, token), time.time())
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            raise QuotaError("Claude local sign-in unavailable") from None


class LocalCodex:
    def fetch(self):
        binary = os.environ.get("QUOTA_CODEX_BIN") or shutil.which("codex")
        env = os.environ.copy()
        if not binary:
            # setup-mac.sh records the selected CLI, avoiding assumptions about
            # Finder/Terminal inheriting an interactive shell's Node PATH.
            binary = local_config("codex_bin")
            if binary:
                env["PATH"] = str(Path(binary).parent) + os.pathsep + env.get("PATH", "")
        if not binary:
            raise QuotaError("Codex CLI not found")
        process = subprocess.Popen([binary, "app-server"], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, bufsize=1, env=env)
        messages = queue.Queue()

        def read():
            try:
                for line in process.stdout:
                    try:
                        messages.put(json.loads(line))
                    except ValueError:
                        continue
            finally:
                messages.put(None)

        reader = threading.Thread(target=read, daemon=True)
        reader.start()

        def send(message):
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def response(identifier):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    msg = messages.get(timeout=max(0.01, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if msg is None:
                    raise QuotaError("Codex app-server stopped")
                if msg.get("id") == identifier:
                    if "error" in msg:
                        raise QuotaError("Codex quota unavailable; check Codex sign-in")
                    return msg.get("result")
            raise QuotaError("Codex quota request timed out")

        try:
            send({"method": "initialize", "id": 1, "params": {
                "clientInfo": {"name": "quota_strip", "title": "Quota Strip", "version": "0.1.0"},
            }})
            response(1)
            send({"method": "initialized", "params": {}})
            send({"method": "account/rateLimits/read", "id": 2})
            return replace(parse_codex(response(2), time.time()), source="Codex")
        except (BrokenPipeError, ValueError, TypeError):
            raise QuotaError("Codex quota response unavailable") from None
        finally:
            process.stdin.close()
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
            reader.join(timeout=1)
            process.stdout.close()


def cswap_binary():
    return os.environ.get("QUOTA_CSWAP_BIN") or shutil.which("cswap") or local_config("cswap_bin")


class CswapAccounts:
    """Shared `cswap list --json` reads for every Claude account on this Mac.

    cswap swaps which account Claude Code's own login belongs to, so the
    active login cannot identify an account. cswap also polls each account's
    usage on its own schedule and serves `list` from that store, so reading
    it adds no usage requests against the per-token rate limit.
    """
    ttl = 30

    def __init__(self, binary, run=subprocess.run, monotonic=time.monotonic):
        self.binary, self.run, self.monotonic = binary, run, monotonic
        self.lock = threading.Lock()
        self.rows, self.read_at = None, None

    def accounts(self):
        with self.lock:
            if self.read_at is None or self.monotonic() - self.read_at >= self.ttl:
                self.rows = self.read()
                self.read_at = self.monotonic()
            return self.rows

    def read(self):
        try:
            result = self.run([self.binary, "list", "--json"], capture_output=True,
                              text=True, timeout=60, stdin=subprocess.DEVNULL)
            data = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            raise QuotaError("cswap unavailable") from None
        if not isinstance(data, dict) or data.get("schemaVersion") != 1:
            raise QuotaError("cswap output changed; update collector")
        if result.returncode or "error" in data or not isinstance(data.get("accounts"), list):
            # The error envelope may name accounts; show only that it failed.
            raise QuotaError("cswap list failed")
        return data["accounts"]


class CswapClaude:
    """One Claude account from cswap, matched by its cswap alias."""

    def __init__(self, account, accounts):
        self.account, self.accounts = account, accounts

    def fetch(self):
        rows = [row for row in self.accounts.accounts()
                if isinstance(row, dict) and row.get("alias") == self.account]
        if len(rows) != 1:
            raise QuotaError(f"Run: cswap alias <number> {self.account}" if not rows
                             else f"Two cswap accounts use alias {self.account}")
        try:
            snapshot = parse_cswap(rows[0], time.time())
        except (ValueError, TypeError, KeyError, AttributeError):
            raise QuotaError("Quota response changed; update collector") from None
        if snapshot is None:
            note = rows[0].get("usageStatusText")
            raise QuotaError(note if isinstance(note, str) and note else "cswap usage unavailable")
        return snapshot


class Unavailable:
    def __init__(self, message):
        self.message = message

    def fetch(self):
        raise QuotaError(self.message)


_cswap = None
_cswap_lock = threading.Lock()


def local_provider(account):
    global _cswap
    if ACCOUNTS[account].provider == "codex":
        return LocalCodex()
    binary = cswap_binary()
    if binary:
        with _cswap_lock:
            if _cswap is None:
                _cswap = CswapAccounts(binary)
        return CswapClaude(account, _cswap)
    if account == "claude":
        return LocalClaude()
    # Claude Code's own login holds one account; others need cswap's store.
    return Unavailable(f"Install cswap to read {account} locally")
