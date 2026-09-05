"""Appliance-specific sign-in, using existing consumer client flows.

See docs/RESEARCH.md for contracts. Tokens are never printed. These flows are
not a supported third-party API; sign-in may need changes as providers evolve.
"""
from __future__ import annotations
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode
from quota_api import (CLAUDE_CLIENT, CODEX_CLIENT, CredentialStore, PROVIDERS,
                       QuotaError, request_json, token_record)


def pkce_challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()


def claude_login(request=request_json, prompt=getpass.getpass):
    verifier, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    redirect = "https://platform.claude.com/oauth/code/callback"
    query = urlencode({
        "code": "true", "client_id": CLAUDE_CLIENT, "response_type": "code",
        "redirect_uri": redirect, "scope": "user:profile",
        "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256", "state": state,
    })
    print("Open this link in a browser on your phone or computer:\n")
    print("https://claude.ai/oauth/authorize?" + query)
    supplied = prompt("\nPaste the returned code#state here (hidden): ").strip()
    code, separator, returned_state = supplied.partition("#")
    if not separator or not code or not hmac.compare_digest(state, returned_state):
        raise QuotaError("Authorization state mismatch; start sign-in again")
    response = request(PROVIDERS["claude"][1], payload={
        "grant_type": "authorization_code", "client_id": CLAUDE_CLIENT,
        "redirect_uri": redirect, "code": code, "state": state, "code_verifier": verifier,
    })
    if "scope" in response and "user:profile" not in str(response["scope"]).split():
        raise QuotaError("Sign-in did not grant quota/profile access")
    return token_record(response)


def account_id_from_token(token):
    """Routing metadata from HTTPS token response; provider validates bearer."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        value = data.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        return value if isinstance(value, str) else None
    except (ValueError, IndexError, AttributeError):
        return None


def codex_login(request=request_json, sleep=time.sleep, monotonic=time.monotonic):
    base = "https://auth.openai.com"
    device = request(base + "/api/accounts/deviceauth/usercode", payload={"client_id": CODEX_CLIENT})
    user_code = device.get("user_code", device.get("usercode"))
    if not user_code or not device.get("device_auth_id"):
        raise QuotaError("Device sign-in response changed")
    print("Open https://auth.openai.com/codex/device on your phone or computer.")
    print(f"Enter this one-time code: {user_code}\nWaiting for approval (up to 15 minutes)...")
    try:
        interval = max(5, int(device.get("interval", 5)))
    except (ValueError, TypeError):
        interval = 5
    deadline = monotonic() + 900
    while monotonic() < deadline:
        try:
            result = request(base + "/api/accounts/deviceauth/token", payload={
                "device_auth_id": device["device_auth_id"], "user_code": user_code,
            })
            break
        except QuotaError as exc:
            if exc.status not in (403, 404, 429):
                raise
            sleep(min(max(interval, exc.retry_after), max(0, deadline - monotonic())))
    else:
        raise QuotaError("Device sign-in expired; start again")
    if not result.get("authorization_code") or not result.get("code_verifier"):
        raise QuotaError("Device token response changed")
    response = request(PROVIDERS["codex"][1], form=True, payload={
        "grant_type": "authorization_code", "client_id": CODEX_CLIENT,
        "code": result["authorization_code"], "code_verifier": result["code_verifier"],
        "redirect_uri": base + "/deviceauth/callback",
    })
    record = token_record(response)
    account = account_id_from_token(response.get("id_token", ""))
    if account:
        record["account_id"] = account
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("provider", choices=["claude", "codex"])
    args = parser.parse_args()
    store = CredentialStore(args.provider)
    try:
        with store.locked():
            record = claude_login() if args.provider == "claude" else codex_login()
            if not record.get("refresh_token"):
                raise QuotaError("No refresh token returned; unattended sign-in is unavailable")
            store.save(record)
        print("Sign-in saved for Quota Strip. Restart the display to fetch immediately.")
    except (QuotaError, OSError) as exc:
        print(str(exc) if isinstance(exc, QuotaError) else "Could not save sign-in state")
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nSign-in cancelled")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
