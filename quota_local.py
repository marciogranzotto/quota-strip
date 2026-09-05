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

from quota_api import QuotaError, request_json, PROVIDERS
from quota_model import parse_claude, parse_codex


class LocalClaude:
    def __init__(self, monotonic=time.monotonic):
        self.monotonic = monotonic
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
            response = request_json(PROVIDERS["claude"][0], headers={
                "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20",
            })
            return parse_claude(response, time.time())
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            raise QuotaError("Claude local sign-in unavailable") from None


class LocalCodex:
    def fetch(self):
        binary = os.environ.get("QUOTA_CODEX_BIN") or shutil.which("codex")
        env = os.environ.copy()
        if not binary:
            # setup-mac.sh records the selected CLI, avoiding assumptions about
            # Finder/Terminal inheriting an interactive shell's Node PATH.
            try:
                config = json.loads(Path(__file__).with_name(".local-config.json").read_text())
                binary = config["codex_bin"]
                env["PATH"] = str(Path(binary).parent) + os.pathsep + env.get("PATH", "")
            except (OSError, ValueError, KeyError):
                pass
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


def local_provider(name):
    return LocalClaude() if name == "claude" else LocalCodex()
