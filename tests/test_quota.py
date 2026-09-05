import base64
from dataclasses import replace
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from quota_api import (CredentialStore, NoRedirect, Provider, QuotaError,
                       atomic_json, retry_seconds, token_record)
from quota_auth import account_id_from_token, claude_login, codex_login, pkce_challenge
from quota_model import Snapshot, Window, WEEK, countdown, parse_claude, parse_codex
from quota_state import Reading, State


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


class BudgetTests(unittest.TestCase):
    zone = ZoneInfo("America/Sao_Paulo")

    def test_matches_status_bar_end_of_today(self):
        now = epoch("2026-09-05T18:00:00-03:00")
        reset = now + 2*86400 + 15*3600 + 11*60
        window = Window("w", "Weekly", 16, reset, WEEK)
        self.assertEqual(window.budget(now, self.zone), 66)

    def test_budget_changes_at_local_midnight(self):
        reset = epoch("2026-09-08T09:00:00-03:00")
        window = Window("w", "Weekly", 16, reset, WEEK)
        before = epoch("2026-09-05T23:59:59-03:00")
        self.assertEqual(window.budget(before, self.zone), 66)
        self.assertEqual(window.budget(before + 1, self.zone), 80)

    def test_reset_today_caps_at_100(self):
        now = epoch("2026-09-05T12:00:00-03:00")
        w = Window("w", "Weekly", 80, now+3600, WEEK)
        self.assertEqual(w.budget(now, self.zone), 100)
        self.assertIsNone(w.budget(now+3600, self.zone))

    def test_dst_uses_next_calendar_midnight(self):
        zone = ZoneInfo("America/New_York")
        # This local calendar day is 23 hours long.
        now = epoch("2026-03-08T00:00:00-05:00")
        w = Window("w", "Weekly", 1, now+WEEK, WEEK)
        self.assertEqual(w.budget(now, zone), 14)  # 23/168, rounded
        midnight = epoch("2026-03-09T00:00:00-04:00")
        self.assertEqual(midnight - now, 23*3600)

    def test_no_budget_without_weekly_duration_and_reset(self):
        now = epoch("2026-09-05T12:00:00Z")
        self.assertIsNone(Window("w", "Unknown", 3, now+3600, None).budget(now, self.zone))
        self.assertIsNone(Window("w", "Weekly", 3, None, WEEK).budget(now, self.zone))
        self.assertIn("awaiting", countdown(now, now))


class ParserTests(unittest.TestCase):
    def test_claude_fractional_percent_is_not_fraction(self):
        snapshot = parse_claude({"five_hour": {"utilization": .5, "resets_at": "2026-09-05T20:00:00Z"}}, 10)
        self.assertEqual(snapshot.windows[0].used, .5)
        self.assertEqual(snapshot.windows[0].label, "5-hour")

    def test_missing_is_not_zero(self):
        self.assertEqual(parse_claude({"five_hour": None}, 1).windows, ())
        with self.assertRaises(ValueError):
            parse_claude({"five_hour": {}}, 1)
        with self.assertRaises(ValueError):
            parse_claude({"error": "not a quota"}, 1)

    def test_claude_model_specific_weekly(self):
        snap = parse_claude({"seven_day_sonnet": {"utilization": 1, "resets_at": None}}, 1)
        self.assertEqual(snap.windows[0].seconds, WEEK)
        self.assertEqual(snap.windows[0].label, "Weekly / Sonnet")

    def test_codex_weekly_primary_and_all_buckets(self):
        snap = parse_codex({"rateLimitsByLimitId": {
            "codex": {"primary": {"usedPercent": 57, "windowDurationMins": 10080, "resetsAt": 123}},
            "codex_bengalfox": {"primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": 123}},
        }, "rateLimits": {"primary": {"usedPercent": 99}}}, 10)
        self.assertEqual(len(snap.windows), 2)
        self.assertEqual(snap.windows[0].label, "Weekly")
        self.assertEqual(snap.windows[1].bucket, "Spark")
        self.assertEqual(snap.windows[1].used, 0)

    def test_claude_fable_scoped_weekly(self):
        now = epoch("2026-09-05T18:00:00-03:00")
        reset = "2026-09-08T12:00:00Z"
        snap = parse_claude({
            "five_hour": {"utilization": 10},
            "seven_day": {"utilization": 18, "resets_at": reset},
            "limits": [{"kind": "weekly_scoped", "percent": 5,
                        "resets_at": reset, "scope": {
                            "model": {"display_name": "Fable", "id": None},
                            "surface": None}}],
        }, now)
        self.assertEqual(len(snap.windows), 3)
        fable = snap.windows[2]
        self.assertEqual((fable.key, fable.label, fable.used),
                         ("seven_day_fable", "Fable / Weekly", 5))
        self.assertEqual(fable.resets_at, epoch(reset))
        self.assertEqual(fable.budget(now, BudgetTests.zone), 66)

    def test_claude_scoped_prefers_explicit_meter_without_duplicate(self):
        snap = parse_claude({
            "seven_day_fable": {"utilization": 9},
            "limits": [{"kind": "weekly_scoped", "percent": 0.5,
                        "scope": {"model": {"display_name": "Fable"}}}],
        }, 1)
        self.assertEqual(len(snap.windows), 1)
        self.assertEqual(snap.windows[0].used, 0.5)
        self.assertIsNone(snap.windows[0].budget(1, BudgetTests.zone))

    def test_claude_scoped_missing_percent_is_not_zero(self):
        for value in (None, -1, float("nan"), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_claude({"limits": [{"kind": "weekly_scoped", "percent": value,
                    "scope": {"model": {"display_name": "Fable"}}}]}, 1)

    def test_codex_http_duration_and_extra_buckets(self):
        value = {"primary_window": {"used_percent": 0, "limit_window_seconds": WEEK, "reset_at": 123}}
        snap = parse_codex({"rate_limit": value, "additional_rate_limits": [{"limit_name": "Spark", "rate_limit": value}]}, 1)
        self.assertEqual([w.seconds for w in snap.windows], [WEEK, WEEK])

    def test_no_guessed_duration(self):
        snap = parse_codex({"rate_limit": {"primary_window": {"used_percent": 4, "reset_at": 123}}}, 1)
        self.assertEqual(snap.windows[0].label, "Window")
        self.assertIsNone(snap.windows[0].seconds)

    def test_reject_non_finite_usage(self):
        for used in [float("nan"), float("inf"), -1, True, "3"]:
            with self.subTest(used=used), self.assertRaises(ValueError):
                parse_claude({"five_hour": {"utilization": used}}, 1)


class StorageAndAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_private_atomic_save_and_cache_restore(self):
        store = CredentialStore("claude", self.home)
        with store.locked():
            store.save({"access_token": "test-only"})
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
        state = State("claude", self.home)
        snap = parse_claude({"five_hour": {"utilization": 4}}, 100)
        state.success(snap)
        state.failure("offline")
        self.assertEqual(state.get().snapshot, snap)
        self.assertTrue(state.get().stale(101, 600))
        self.assertTrue(State("claude", self.home).get().stale(101, 600))

    def test_invalid_cache_is_ignored(self):
        (self.home / "claude-snapshot.json").write_text('{"provider":"claude"}')
        self.assertIsNone(State("claude", self.home).get().snapshot)

    def test_expired_token_refresh_persisted_once(self):
        calls = []
        def request(url, **kw):
            calls.append((url, kw))
            if "oauth/token" in url:
                return {"access_token": "new", "refresh_token": "new-refresh", "expires_in": 3600}
            self.assertEqual(kw["headers"]["Authorization"], "Bearer new")
            return {"five_hour": {"utilization": 7}}
        provider = Provider("claude", self.home, request)
        provider.store.save({"access_token": "old", "refresh_token": "old-refresh", "expires_at": 1})
        self.assertEqual(provider.fetch().windows[0].used, 7)
        self.assertEqual(provider.store.read()["refresh_token"], "new-refresh")
        self.assertEqual(len(calls), 2)

    def test_403_does_not_refresh(self):
        def request(url, **kw):
            raise QuotaError("denied", 403)
        provider = Provider("codex", self.home, request)
        provider.store.save({"access_token": "old", "refresh_token": "refresh"})
        with patch.object(provider, "refresh") as refresh:
            with self.assertRaises(QuotaError):
                provider.fetch()
            refresh.assert_not_called()

    def test_401_retries_only_once(self):
        provider = Provider("codex", self.home, lambda *a, **kw: (_ for _ in ()).throw(QuotaError("bad", 401)))
        provider.store.save({"access_token": "old", "refresh_token": "refresh"})
        with patch.object(provider, "refresh", return_value={"access_token": "new"}) as refresh:
            with self.assertRaises(QuotaError):
                provider.fetch()
            self.assertEqual(refresh.call_count, 1)

    def test_retry_after_both_formats(self):
        self.assertEqual(retry_seconds("120", 0), 120)
        self.assertEqual(retry_seconds("Thu, 01 Jan 1970 00:02:00 GMT", 0), 120)
        self.assertEqual(retry_seconds("invalid", 0), 0)

    def test_pkce_rfc7636_vector(self):
        self.assertEqual(pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_state_mismatch_never_exchanges_code(self):
        with patch("sys.stdout", new=io.StringIO()), patch("quota_auth.request_json") as request:
            with self.assertRaises(QuotaError):
                claude_login(request=request, prompt=lambda _: "code#wrong")
            request.assert_not_called()

    def test_device_login_pending_then_exchange(self):
        calls = []
        def request(url, **kw):
            calls.append(url)
            if url.endswith("usercode"):
                return {"device_auth_id": "test", "user_code": "TEST", "interval": "5"}
            if url.endswith("deviceauth/token"):
                if len(calls) == 2:
                    raise QuotaError("pending", 403)
                return {"authorization_code": "code", "code_verifier": "verifier"}
            self.assertTrue(kw["form"])
            return {"access_token": "token", "refresh_token": "refresh", "expires_in": 3600}
        with patch("sys.stdout", new=io.StringIO()):
            result = codex_login(request=request, sleep=lambda _: None)
        self.assertEqual(result["refresh_token"], "refresh")
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
