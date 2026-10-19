"""Accounts shown on the strip: each is one sign-in to a provider."""
from __future__ import annotations
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Account:
    id: str        # Names credential, lock and snapshot files, and HA entity keys.
    provider: str  # "claude" or "codex": selects endpoints, parser and logo.
    title: str
    badge: str     # Static plan label, not detected metadata.


ACCOUNTS = {a.id: a for a in (
    Account("claude", "claude", "CLAUDE", "MAX 20×"),
    Account("claude2", "claude", "CLAUDE2", "MAX 20×"),
    Account("codex", "codex", "CODEX", "CHATGPT PRO"),
)}
DEFAULT = "claude,codex"
MAX_SHOWN = 3  # The 1920 × 480 layouts hold at most three panels.


def selected(value=None):
    """Accounts to show, from --accounts or QUOTA_ACCOUNTS, in screen order."""
    value = value if value is not None else os.environ.get("QUOTA_ACCOUNTS") or DEFAULT
    names = [name.strip() for name in value.split(",") if name.strip()]
    unknown = [name for name in names if name not in ACCOUNTS]
    if unknown:
        raise ValueError(f"Unknown account {unknown[0]!r}; choose from {', '.join(ACCOUNTS)}")
    if not names or len(names) > MAX_SHOWN or len(set(names)) != len(names):
        raise ValueError(f"Choose one to {MAX_SHOWN} different accounts")
    return tuple(ACCOUNTS[name] for name in names)
