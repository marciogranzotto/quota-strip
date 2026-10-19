import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from quota_accounts import ACCOUNTS, selected
from quota_api import PROVIDERS, CredentialStore, Provider, QuotaError
from quota_ha_sensors import QuotaSensors, sensor_groups
import quota_local
from quota_local import ClaudeCodeProfile, CswapAccounts, CswapClaude, Unavailable, local_provider
from quota_model import WEEK, FIVE_HOURS, parse_claude, parse_cswap
from quota_state import State


NOW = 1792178000.0


def cswap_row(alias="claude2", **overrides):
    row = {
        "number": 2, "alias": alias, "active": False, "usageStatus": "ok",
        "usageFetchedAt": "2026-10-16T19:00:00Z", "usageAgeSeconds": 60.0,
        "usage": {
            "fiveHour": {"pct": 71.0, "resetsAt": "2026-10-16T21:45:00Z"},
            "sevenDay": {"pct": 48.0, "resetsAt": "2026-10-20T21:40:00Z", "expectedPct": 40.0},
            "scoped": [{"pct": 3.0, "resetsAt": "2026-10-20T21:40:00Z", "name": "Fable"}],
        },
    }
    row.update(overrides)
    return row


class SelectionTests(unittest.TestCase):
    def test_default_keeps_two_panels(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual([a.id for a in selected()], ["claude", "codex"])

    def test_environment_and_argument_choose_order(self):
        with patch.dict(os.environ, {"QUOTA_ACCOUNTS": "claude, claude2 ,codex"}):
            self.assertEqual([a.id for a in selected()], ["claude", "claude2", "codex"])
            self.assertEqual([a.id for a in selected("codex,claude2")], ["codex", "claude2"])

    def test_rejects_unknown_duplicate_empty_and_too_many(self):
        for value in ("claude,claude3", "claude,claude", " , ", "claude,claude2,codex,claude"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                selected(value)

    def test_second_account_is_a_claude_account(self):
        self.assertEqual(ACCOUNTS["claude2"].provider, "claude")


class StandaloneAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def test_each_account_has_its_own_credentials_and_snapshot(self):
        self.assertEqual(CredentialStore("claude2", self.home).path.name, "claude2-auth.json")
        self.assertEqual(State("claude2", self.home).path.name, "claude2-snapshot.json")
        with self.assertRaises(ValueError):
            CredentialStore("claude3", self.home)

    def test_second_account_uses_claude_endpoint_with_its_own_token(self):
        CredentialStore("claude", self.home).save({"access_token": "first"})
        CredentialStore("claude2", self.home).save({"access_token": "second"})
        calls = []

        def request(url, **kw):
            calls.append((url, kw["headers"]["Authorization"]))
            return {"five_hour": {"utilization": 9}}

        snapshot = Provider("claude2", self.home, request).fetch()
        self.assertEqual(snapshot.provider, "claude")
        self.assertTrue(calls[0][0].startswith(PROVIDERS["claude"][0]))
        self.assertEqual(calls[0][1], "Bearer second")

    def test_missing_sign_in_names_the_account(self):
        with self.assertRaisesRegex(QuotaError, "quota_auth.py claude2"):
            Provider("claude2", self.home).fetch()

    def test_state_restores_only_matching_provider(self):
        snap = parse_claude({"five_hour": {"utilization": 4}}, NOW)
        State("claude2", self.home).success(snap)
        self.assertEqual(State("claude2", self.home).get().snapshot, snap)
        (self.home / "codex-snapshot.json").write_text(json.dumps(snap.to_dict()))
        self.assertIsNone(State("codex", self.home).get().snapshot)


class CswapParserTests(unittest.TestCase):
    def test_current_reading_maps_to_claude_windows(self):
        snap = parse_cswap(cswap_row(), NOW)
        self.assertEqual([w.key for w in snap.windows], ["five_hour", "seven_day", "seven_day_fable"])
        self.assertEqual([w.used for w in snap.windows], [71, 48, 3])
        self.assertEqual(snap.windows[0].seconds, FIVE_HOURS)
        self.assertEqual(snap.windows[2].seconds, WEEK)
        self.assertEqual(snap.observed_at, 1792177200.0)
        self.assertEqual((snap.source, snap.warning, snap.reset_bank), ("cswap", None, None))

    def test_last_good_reading_keeps_its_age_and_reason(self):
        row = cswap_row(usageStatus="unavailable", usage=None,
                        usageStatusText="usage unavailable — network problem — retrying",
                        lastGoodUsage=cswap_row()["usage"], lastGoodFetchedAt="2026-10-16T18:00:00Z")
        snap = parse_cswap(row, NOW)
        self.assertEqual(snap.observed_at, 1792173600.0)
        self.assertIn("network problem", snap.warning)

    def test_age_is_used_without_a_timestamp(self):
        row = cswap_row()
        del row["usageFetchedAt"]
        self.assertEqual(parse_cswap(row, NOW).observed_at, NOW - 60)

    def test_no_reading_is_not_zero_usage(self):
        self.assertIsNone(parse_cswap(cswap_row(usageStatus="relogin_required", usage=None), NOW))

    def test_invalid_usage_is_rejected(self):
        bad = cswap_row()
        bad["usage"]["fiveHour"]["pct"] = None
        for row in (bad, cswap_row(usage={"scoped": "x"}), cswap_row(usage={})):
            with self.subTest(row=row), self.assertRaises(ValueError):
                parse_cswap(row, NOW)


class CswapSourceTests(unittest.TestCase):
    def reader(self, payload, returncode=0):
        self.clock = 0
        run = Mock(return_value=subprocess.CompletedProcess([], returncode, json.dumps(payload), ""))
        return CswapAccounts("cswap", run=run, monotonic=lambda: self.clock), run

    def test_matches_account_by_alias(self):
        reader, _ = self.reader({"schemaVersion": 1, "accounts": [cswap_row("claude"), cswap_row("claude2")]})
        self.assertEqual(CswapClaude("claude2", reader).fetch().windows[0].used, 71)

    def test_missing_or_duplicate_alias_explains_the_fix(self):
        reader, _ = self.reader({"schemaVersion": 1, "accounts": [cswap_row(None)]})
        with self.assertRaisesRegex(QuotaError, "cswap alias <number> claude2"):
            CswapClaude("claude2", reader).fetch()
        reader, _ = self.reader({"schemaVersion": 1, "accounts": [cswap_row(), cswap_row()]})
        with self.assertRaisesRegex(QuotaError, "Two cswap accounts"):
            CswapClaude("claude2", reader).fetch()

    def test_status_note_is_shown_without_a_reading(self):
        row = cswap_row(usageStatus="relogin_required", usage=None, usageStatusText="re-login needed")
        reader, _ = self.reader({"schemaVersion": 1, "accounts": [row]})
        with self.assertRaisesRegex(QuotaError, "re-login needed"):
            CswapClaude("claude2", reader).fetch()

    def test_error_envelope_is_not_relayed(self):
        reader, _ = self.reader({"schemaVersion": 1, "error": {"message": "person@example.com"}}, 1)
        with self.assertRaises(QuotaError) as caught:
            reader.accounts()
        self.assertNotIn("example.com", str(caught.exception))

    def test_schema_change_is_reported(self):
        reader, _ = self.reader({"schemaVersion": 2, "accounts": []})
        with self.assertRaisesRegex(QuotaError, "update collector"):
            reader.accounts()

    def test_accounts_share_one_read_per_interval(self):
        reader, run = self.reader({"schemaVersion": 1, "accounts": [cswap_row("claude"), cswap_row()]})
        CswapClaude("claude", reader).fetch()
        CswapClaude("claude2", reader).fetch()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["cswap", "list", "--json"])
        self.clock = CswapAccounts.ttl
        reader.accounts()
        self.assertEqual(run.call_count, 2)

    def test_local_provider_choice(self):
        with patch.object(quota_local, "cswap_binary", return_value=None):
            self.assertIsInstance(local_provider("claude"), quota_local.LocalClaude)
            missing = local_provider("claude2")
            self.assertIsInstance(missing, Unavailable)
            with self.assertRaisesRegex(QuotaError, "Install cswap"):
                missing.fetch()
            self.assertIsInstance(local_provider("codex"), quota_local.LocalCodex)
        with patch.object(quota_local, "cswap_binary", return_value="cswap"), \
                patch.object(quota_local, "_cswap", None):
            first, second = local_provider("claude"), local_provider("claude2")
            self.assertIsInstance(first, CswapClaude)
            self.assertIs(first.accounts, second.accounts)


class ClaudeCodeProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        patcher = patch("quota_local.Path.home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, account, stamp):
        path = self.home / ".claude.json"
        path.write_text(json.dumps({"projects": {}, "oauthAccount": account}))
        os.utime(path, ns=(stamp, stamp))

    def test_reads_claude_code_profile_copy_only_when_it_changes(self):
        self.write({"organizationUuid": "org-2", "organizationType": "claude_max",
                    "organizationRateLimitTier": "default_claude_max_20x"}, 1)
        profile = ClaudeCodeProfile()
        self.assertEqual(profile.plan(), "MAX 20×")
        self.assertEqual(profile.plan("org-2"), "MAX 20×")
        self.assertIsNone(profile.plan("org-1"))  # Another account's login is active.
        with patch("quota_local.json.loads") as loads:
            profile.plan()
            loads.assert_not_called()
        self.write({"organizationUuid": "org-1", "organizationType": "claude_pro"}, 2)
        self.assertEqual(profile.plan("org-1"), "PRO")

    def test_missing_or_malformed_profile_is_unknown(self):
        profile = ClaudeCodeProfile()
        self.assertIsNone(profile.plan())
        (self.home / ".claude.json").write_text("[]")
        self.assertIsNone(profile.plan())
        self.write("x", 3)
        self.assertIsNone(profile.plan())

    def test_cswap_account_takes_plan_from_matching_login(self):
        self.write({"organizationUuid": "org-2", "organizationType": "claude_max",
                    "organizationRateLimitTier": "default_claude_max_20x"}, 1)
        run = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps({"schemaVersion": 1, "accounts": [
            cswap_row("claude", organizationUuid="org-1"), cswap_row("claude2", organizationUuid="org-2")]}), ""))
        reader, profile = CswapAccounts("cswap", run=run), ClaudeCodeProfile()
        self.assertEqual(CswapClaude("claude2", reader, profile).fetch().plan, "MAX 20×")
        self.assertIsNone(CswapClaude("claude", reader, profile).fetch().plan)


class AccountSensorTests(unittest.TestCase):
    def test_first_account_keys_unchanged_and_second_account_separate(self):
        snap = parse_claude({"five_hour": {"utilization": 9, "resets_at": NOW + 3600}}, NOW)
        zone = ZoneInfo("UTC")
        original = sensor_groups(snap, NOW, zone)
        self.assertEqual(original, sensor_groups(snap, NOW, zone, "claude"))
        second = sensor_groups(snap, NOW, zone, "claude2")
        self.assertTrue(all(key.startswith("claude2_") for key in second))
        self.assertTrue(all(name.startswith("Claude2 ") for name, _, _ in second.values()))
        self.assertFalse(original.keys() & second.keys())

    def test_refresh_publishes_second_account_snapshot(self):
        snap = parse_claude({"five_hour": {"utilization": 9, "resets_at": NOW + 3600}}, NOW)
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            (home / "claude2-snapshot.json").write_text(json.dumps(snap.to_dict()))
            client = Mock()
            client.is_connected.return_value = True
            client.publish.return_value.rc = 0
            QuotaSensors(client, "test", "root", "ha", home, "UTC").refresh(NOW)
            topics = [c.args[0] for c in client.publish.call_args_list]
            self.assertTrue(topics)
            self.assertTrue(all("claude2_" in topic for topic in topics))


if __name__ == "__main__":
    unittest.main()
