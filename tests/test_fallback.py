"""Partial Claude reads must not erase model quotas or defeat account backoff."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from quota_api import QuotaError
from quota_local import LocalClaude
from quota_model import parse_claude
from quota_state import State, poll


def full_snapshot(now=100):
    return parse_claude({
        'five_hour': {'utilization': 10},
        'seven_day': {'utilization': 20},
        'limits': [{'kind': 'weekly_scoped', 'percent': 7,
                    'resets_at': 1000000,
                    'scope': {'model': {'display_name': 'Fable'}}}],
    }, now)


class ClaudeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.path = self.home / '.claude/.debug/statusline-input.json'
        self.path.parent.mkdir(parents=True)
        self.capture(200)
        self.home_patch = patch('quota_local.Path.home', return_value=self.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)
        self.now = 0
        self.provider = LocalClaude(monotonic=lambda: self.now)
        self.account = Mock(side_effect=QuotaError('Provider rate limited (HTTP 429)', 429, 0))
        self.provider.account_usage = self.account

    def capture(self, when, used=30):
        self.path.write_text(json.dumps({'rate_limits': {
            'five_hour': {'used_percentage': used},
            'seven_day': {'used_percentage': 21},
        }}))
        os.utime(self.path, (when, when))

    def fallback(self):
        with self.assertRaises(QuotaError) as raised:
            self.provider.fetch()
        self.assertIsNotNone(raised.exception.fallback)
        return raised.exception

    def test_status_line_refreshes_during_account_cooldown(self):
        first = self.fallback()
        self.assertEqual(first.retry_after, 300)
        self.assertEqual(first.fallback.source, 'status line')
        self.assertIn('429', first.fallback.warning)
        self.now = 120
        self.capture(320, 40)
        second = self.fallback()
        self.assertEqual(second.retry_after, 180)
        self.assertEqual(second.fallback.windows[0].used, 40)
        self.assertEqual(second.fallback.observed_at, 320)
        self.assertEqual(self.account.call_count, 1)
        self.now = 300
        self.assertEqual(self.fallback().retry_after, 600)
        self.assertEqual(self.account.call_count, 2)

    def test_retry_after_is_honored(self):
        self.account.side_effect = QuotaError('Rate limited', 429, 2000)
        self.assertEqual(self.fallback().retry_after, 2000)
        self.now = 1800
        self.fallback()
        self.assertEqual(self.account.call_count, 1)

    def test_retained_model_age_survives_repeated_fallbacks_and_restart(self):
        state = State('claude', self.home)
        state.success(full_snapshot())
        state.partial(self.fallback().fallback)
        snap = state.get().snapshot
        self.assertEqual(len(snap.windows), 3)
        self.assertEqual(snap.windows[-1].observed_at, 100)
        self.assertTrue(snap.windows[-1].stale)
        self.assertEqual(snap.windows[-1].used, 7)
        self.assertFalse(snap.windows[0].stale)
        self.now = 120
        self.capture(320)
        restored = State('claude', self.home)
        restored.partial(self.fallback().fallback)
        self.assertEqual(restored.get().snapshot.windows[-1], snap.windows[-1])
        self.assertEqual(restored.get().snapshot.observed_at, 320)
        self.assertFalse(restored.get().stale(321, 600))

    def test_account_recovery_clears_partial_warning_and_stale_flags(self):
        state = State('claude', self.home)
        state.success(full_snapshot())
        state.partial(self.fallback().fallback)
        self.now = 300
        self.account.side_effect = None
        self.account.return_value = full_snapshot(400)
        fresh = self.provider.fetch()
        state.success(fresh)
        self.assertIsNone(fresh.warning)
        self.assertFalse(fresh.windows[-1].stale)
        self.assertEqual(state.get().snapshot, fresh)
        self.assertEqual(self.provider.backoff, 120)

    def test_complete_response_may_remove_a_model_quota(self):
        state = State('claude', self.home)
        state.success(full_snapshot())
        state.success(parse_claude({'five_hour': {'utilization': 10}}, 200))
        self.assertEqual(len(state.get().snapshot.windows), 1)

    def test_no_fabricated_model_value_without_previous_reading(self):
        state = State('claude', self.home)
        state.partial(self.fallback().fallback)
        snap = state.get().snapshot
        self.assertEqual(len(snap.windows), 2)
        self.assertIsNotNone(snap.warning)

    def test_bad_capture_preserves_original_error(self):
        self.path.write_text('not json')
        with self.assertRaises(QuotaError) as raised:
            self.provider.fetch()
        self.assertEqual(raised.exception.status, 429)
        self.assertIsNone(raised.exception.fallback)

    def test_poll_keeps_partial_readings_without_marking_fresh_windows_stale(self):
        state = State('claude', self.home)
        state.success(full_snapshot())
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with patch('quota_local.local_provider', return_value=self.provider):
            poll(state, stop, interval=120, source='local')
        stop.wait.assert_called_once_with(120)
        self.assertIsNone(state.get().error)
        self.assertTrue(state.get().snapshot.windows[-1].stale)
        self.assertIn('429', state.get().snapshot.warning)
