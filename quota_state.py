"""Independent provider polling and last-known-good snapshots."""
from __future__ import annotations
from dataclasses import dataclass
import json
import threading
import time
from quota_api import Provider, QuotaError, atomic_json, data_home
from quota_model import Snapshot


@dataclass(frozen=True)
class Reading:
    snapshot: Snapshot | None = None
    error: str | None = None

    def stale(self, now, max_age):
        return (self.error is not None or self.snapshot is None or
                now - self.snapshot.observed_at > max_age or self.snapshot.observed_at > now + 60)


class State:
    def __init__(self, provider, home=None):
        self.provider = provider
        self.path = (home or data_home()) / f"{provider}-snapshot.json"
        self.lock = threading.Lock()
        self.reading = Reading()
        try:
            snapshot = Snapshot.from_dict(json.loads(self.path.read_text()))
            if snapshot.provider == provider:
                self.reading = Reading(snapshot, "Cached - reconnecting")
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def get(self):
        with self.lock:
            return self.reading

    def success(self, snapshot):
        try:
            atomic_json(self.path, snapshot.to_dict())
            error = None
        except OSError:
            error = "Storage unavailable"
        with self.lock:
            self.reading = Reading(snapshot, error)

    def failure(self, error):
        with self.lock:
            self.reading = Reading(self.reading.snapshot, error)


def poll(state, stop, interval=120, home=None, source="standalone"):
    if source == "local":
        from quota_local import local_provider
        provider = local_provider(state.provider)
    else:
        provider = Provider(state.provider, home)
    backoff = interval
    while not stop.is_set():
        try:
            state.success(provider.fetch())
            backoff = interval
            wait = interval
        except QuotaError as exc:
            state.failure(str(exc))
            backoff = min(backoff * 2, 1800)
            wait = max(backoff, exc.retry_after)
        except Exception:
            # Keep the screen alive, but never expose arbitrary exception data
            # that may contain credentials or a provider's response body.
            state.failure("Collector error - check installation")
            backoff = min(backoff * 2, 1800)
            wait = backoff
        stop.wait(wait)
