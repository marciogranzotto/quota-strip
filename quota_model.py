"""Provider-neutral quota windows and the user's end-of-today budget rule."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time as day_time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

WEEK = 604800


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def timestamp(value):
    if number(value) is not None:
        return float(value)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.timestamp() if dt.tzinfo else None
        except ValueError:
            pass
    return None


def rounded(value):
    """Nonnegative round-half-up, matching the shell budget calculation."""
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class Window:
    key: str
    label: str
    used: float
    resets_at: float | None
    seconds: float | None
    bucket: str = ""
    observed_at: float | None = None
    stale: bool = False

    def budget(self, now: float, zone: ZoneInfo):
        if self.seconds != WEEK or self.resets_at is None or self.resets_at <= now:
            return None
        local = datetime.fromtimestamp(now, zone)
        midnight = datetime.combine(local.date() + timedelta(days=1), day_time(), zone)
        start = self.resets_at - WEEK
        return max(0, min(100, rounded((midnight.timestamp() - start) * 100 / WEEK)))

    def expired(self, now):
        return self.resets_at is not None and self.resets_at <= now


@dataclass(frozen=True)
class ResetBank:
    available_count: int
    next_expiry: float | None = None
    details_complete: bool = False

    def needs_refresh(self, now):
        return self.next_expiry is not None and self.next_expiry <= now

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            return None
        count = data.get("available_count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            return None
        expiry = timestamp(data.get("next_expiry"))
        if data.get("next_expiry") is not None and expiry is None:
            return cls(count)
        if expiry is not None:
            try:
                datetime.fromtimestamp(expiry, timezone.utc)
            except (ValueError, OverflowError, OSError):
                return cls(count)
        return cls(count, expiry, data.get("details_complete") is True)


def parse_reset_bank(data):
    """Optional app-server or HTTP metadata; discard credit IDs and descriptions."""
    if not isinstance(data, dict):
        return None
    bank = ResetBank.from_dict({"available_count": data.get("availableCount", data.get("available_count"))})
    if bank is None:
        return None
    rows = data.get("credits")
    if not isinstance(rows, list):
        return bank
    complete = len(rows) == bank.available_count
    expiries = []
    for row in rows:
        if not isinstance(row, dict) or row.get("status") != "available":
            complete = False
            continue
        if "expiresAt" not in row and "expires_at" not in row:
            complete = False
            continue
        raw = row.get("expiresAt", row.get("expires_at"))
        if raw is None:  # The protocol explicitly defines null as no expiry.
            continue
        expiry = timestamp(raw)
        try:
            if expiry is None:
                raise ValueError()
            datetime.fromtimestamp(expiry, timezone.utc)
        except (ValueError, OverflowError, OSError):
            complete = False
            continue
        expiries.append(expiry)
    return ResetBank(bank.available_count, min(expiries) if expiries else None, complete)


@dataclass(frozen=True)
class Snapshot:
    provider: str
    windows: tuple[Window, ...]
    observed_at: float
    source: str = "account"
    reset_bank: ResetBank | None = None
    warning: str | None = None

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        if data.get("provider") not in ("claude", "codex"):
            raise ValueError("Invalid provider")
        observed = number(data.get("observed_at"))
        if observed is None:
            raise ValueError("Invalid observation time")
        windows = []
        for w in data["windows"]:
            used = number(w.get("used"))
            if used is None or used < 0:
                raise ValueError("Invalid usage")
            windows.append(Window(str(w["key"]), str(w["label"]), used,
                                  timestamp(w.get("resets_at")), number(w.get("seconds")),
                                  str(w.get("bucket", "")), timestamp(w.get("observed_at")),
                                  w.get("stale") is True))
        return cls(data["provider"], tuple(windows), observed, str(data.get("source", "account")),
                   ResetBank.from_dict(data.get("reset_bank")),
                   data.get("warning") if isinstance(data.get("warning"), str) else None)


def duration_label(seconds):
    if seconds == WEEK:
        return "Weekly"
    if seconds is None:
        return "Window"
    if seconds % 86400 == 0:
        return f"{seconds / 86400:g}-day"
    if seconds % 3600 == 0:
        return f"{seconds / 3600:g}-hour"
    return f"{seconds / 60:g}-minute"


def parse_claude(data, now):
    if not isinstance(data, dict):
        raise ValueError("Invalid Claude quota response")
    windows = []
    recognized = False
    for key, value in data.items():
        if key != "five_hour" and not key.startswith("seven_day"):
            continue
        recognized = True
        if not isinstance(value, dict):
            continue
        used = number(value.get("utilization"))
        if used is None or used < 0:
            raise ValueError("Invalid Claude utilization")
        seconds = 18000 if key == "five_hour" else WEEK
        suffix = key.removeprefix("seven_day").strip("_").replace("_", " ").title() if key.startswith("seven_day_") else ""
        label = duration_label(seconds) + (f" / {suffix}" if suffix else "")
        windows.append(Window(key, label, used, timestamp(value.get("resets_at")), seconds))
    # Model quotas such as Fable are reported in limits[], not seven_day_*.
    # Prefer the explicit scoped meter if a legacy field also names that model.
    limits = data.get("limits")
    if limits is not None and not isinstance(limits, list):
        raise ValueError("Invalid Claude limits")
    for limit in limits or []:
        if not isinstance(limit, dict) or limit.get("kind") != "weekly_scoped":
            continue
        scope = limit.get("scope")
        if not isinstance(scope, dict) or scope.get("surface") is not None:
            continue
        model = scope.get("model")
        name = model.get("display_name") if isinstance(model, dict) else None
        if not isinstance(name, str) or not name.strip():
            continue
        recognized = True
        used = number(limit.get("percent"))
        if used is None or used < 0:
            raise ValueError("Invalid Claude scoped utilization")
        name = name.strip()
        key = "seven_day_" + "_".join(name.casefold().split())
        windows = [w for w in windows if w.key != key]
        windows.append(Window(key, f"{name} / Weekly", used,
                              timestamp(limit.get("resets_at")), WEEK))
    if not recognized:
        raise ValueError("Claude response has no recognized quota fields")
    windows.sort(key=lambda w: (w.key != "five_hour", w.key != "seven_day", w.key))
    return Snapshot("claude", tuple(windows), now)


def parse_codex(data, now):
    """Accept direct usage HTTP responses and app-server snapshots.

    Read durations from the server. Primary is NOT necessarily a 5-hour window.
    Prefer the multi-bucket representation when it is supplied.
    """
    if not isinstance(data, dict):
        raise ValueError("Invalid Codex quota response")
    windows = []
    mapped = data.get("rateLimitsByLimitId")
    buckets = []
    if isinstance(mapped, dict):
        buckets = [(key, value) for key, value in mapped.items() if isinstance(value, dict)]
    elif "rateLimits" in data:
        if isinstance(data["rateLimits"], dict):
            buckets = [("codex", data["rateLimits"])]
    elif "rate_limit" in data:
        if isinstance(data["rate_limit"], dict):
            buckets = [("codex", data["rate_limit"])]
        for item in data.get("additional_rate_limits") or []:
            if isinstance(item, dict) and isinstance(item.get("rate_limit"), dict):
                key = item.get("limit_name") or item.get("metered_feature") or "Additional"
                buckets.append((str(key), item["rate_limit"]))
    else:
        raise ValueError("Codex response has no recognized quota fields")
    for bucket, limits in buckets:
        name = limits.get("limitName") or bucket
        if bucket == "codex":
            name = ""
        elif "spark" in str(name).lower() or bucket == "codex_bengalfox":
            name = "Spark"
        for slot in ("primary", "secondary"):
            value = limits.get(slot, limits.get(slot + "_window"))
            if not isinstance(value, dict):
                continue
            used = number(value.get("usedPercent", value.get("used_percent")))
            if used is None or used < 0:
                raise ValueError("Invalid Codex utilization")
            seconds = number(value.get("limit_window_seconds"))
            if seconds is None:
                minutes = number(value.get("windowDurationMins"))
                seconds = minutes * 60 if minutes is not None else None
            if seconds is not None and seconds <= 0:
                seconds = None
            reset = timestamp(value.get("resetsAt", value.get("reset_at")))
            windows.append(Window(f"{bucket}:{slot}", duration_label(seconds), used,
                                  reset, seconds, str(name)))
    return Snapshot("codex", tuple(windows), now,
                    reset_bank=parse_reset_bank(data.get("rateLimitResetCredits", data.get("rate_limit_reset_credits"))),
                    warning=data.get("_quota_strip_warning"))


def countdown(reset, now):
    if reset is None:
        return "Reset time unavailable"
    if reset <= now:
        return "Reset reached - awaiting update"
    minutes = int((reset - now) // 60)
    if minutes < 1:
        return "Resets in <1m"
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return "Resets in " + (f"{days}d " if days else "") + (f"{hours}h " if hours else "") + f"{minutes}m"
