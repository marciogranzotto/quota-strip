"""Exercise standalone auth boundaries and recovery without real credentials."""
import http.client
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quota_api import Provider, QuotaError
from quota_callback import LoopbackCallback
from quota_model import parse_codex
from quota_state import State, poll


class CallbackTests(unittest.TestCase):
    def send(self, callback, path, host=None):
        conn = http.client.HTTPConnection('127.0.0.1', callback.server.server_port, timeout=2)
        try:
            conn.request('GET', path, headers={'Host': host or f'localhost:{callback.server.server_port}'})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_valid_callback_is_private_and_one_shot_even_after_consumption(self):
        with patch('sys.stderr', new=io.StringIO()) as logs, LoopbackCallback('expected') as callback:
            status, headers, body = self.send(callback, '/callback?code=test-only-code&state=expected')
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertNotIn(b'test-only-code', body)
            self.assertEqual(callback.wait(.1), 'test-only-code')
            self.assertEqual(self.send(callback, '/callback?code=second&state=expected')[0], 409)
        self.assertEqual(logs.getvalue(), '')
        self.assertFalse(callback.thread.is_alive())

    def test_invalid_callbacks_do_not_complete_login(self):
        with LoopbackCallback('expected') as callback:
            for path, host in [
                ('/callback?code=a&state=wrong', None),
                ('/callback?code=a&state=expected&state=wrong', None),
                ('/callback?code=a&code=b&state=expected', None),
                ('/callback?code=a&error=denied&state=expected', None),
                ('/callback?state=expected', None),
                ('/other?code=a&state=expected', None),
                ('/callback?code=a&state=expected', 'example.invalid'),
                ('/callback?code=a&state=%C3%A9', None),
            ]:
                with self.subTest(path=path, host=host):
                    self.assertEqual(self.send(callback, path, host)[0], 400)
            with self.assertRaisesRegex(QuotaError, 'timed out'):
                callback.wait(.01)

    def test_denial_is_reported_without_exchanging_a_code(self):
        with LoopbackCallback('expected') as callback:
            self.assertEqual(self.send(callback, '/callback?error=access_denied&state=expected')[0], 200)
            with self.assertRaisesRegex(QuotaError, 'declined'):
                callback.wait(.1)


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def test_http_reset_bank_normalizes_iso_dates_and_discards_private_fields(self):
        snap = parse_codex({'rate_limit': {}, 'rate_limit_reset_credits': {
            'available_count': 1, 'credits': [{'status': 'available',
            'expires_at': '2030-01-01T00:00:00Z', 'id': 'private', 'description': 'private'}]}}, 1)
        self.assertEqual(snap.reset_bank.available_count, 1)
        self.assertEqual(snap.reset_bank.next_expiry, 1893456000)
        self.assertTrue(snap.reset_bank.details_complete)
        self.assertNotIn('private', str(snap.to_dict()))

    def test_reset_details_failure_backs_off_without_losing_live_count(self):
        detail_calls = []
        def request(url, **kwargs):
            self.assertNotIn('payload', kwargs)  # Both operations are read-only.
            if url.endswith('/usage'):
                return {'rate_limit': {'primary_window': {'used_percent': 5}},
                        'rate_limit_reset_credits': {'available_count': 2}}
            detail_calls.append(url)
            if len(detail_calls) == 1:
                raise QuotaError('Provider rate limited', 429, 1000)
            return {'available_count': 1, 'credits': [{'status': 'available', 'expires_at': None}]}
        provider = Provider('codex', self.home, request)
        provider.store.save({'access_token': 'test-only'})
        with patch('quota_api.time.monotonic', return_value=100):
            first = provider.fetch()
        with patch('quota_api.time.monotonic', return_value=1099):
            second = provider.fetch()
        self.assertEqual(len(detail_calls), 1)
        self.assertEqual(second.windows[0].used, 5)
        self.assertEqual(first.reset_bank.available_count, 2)
        self.assertIsNotNone(second.warning)
        with patch('quota_api.time.monotonic', return_value=1100):
            recovered = provider.fetch()
        self.assertEqual(len(detail_calls), 2)
        self.assertEqual(recovered.reset_bank.available_count, 1)
        self.assertTrue(recovered.reset_bank.details_complete)
        self.assertIsNone(recovered.warning)

    def test_zero_bank_needs_no_detail_request(self):
        calls = []
        def request(url, **kwargs):
            calls.append(url)
            return {'rate_limit': {}, 'rate_limit_reset_credits': {'available_count': 0}}
        provider = Provider('codex', self.home, request)
        provider.store.save({'access_token': 'test-only'})
        self.assertTrue(provider.fetch().reset_bank.details_complete)
        self.assertEqual(len(calls), 1)

    def test_network_failure_then_token_rotation_recovers_cached_state(self):
        phase = [0]
        calls = []
        def request(url, **kwargs):
            calls.append(url)
            if phase[0] == 0:
                raise QuotaError('Network unavailable')
            if url.endswith('/oauth/token'):
                self.assertEqual(kwargs['payload']['scope'], 'user:profile')
                return {'access_token': 'rotated', 'refresh_token': 'rotated-refresh', 'expires_in': 3600}
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer rotated')
            return {'five_hour': {'utilization': 10}}
        provider = Provider('claude', self.home, request)
        provider.store.save({'access_token': 'expired', 'refresh_token': 'test-refresh', 'expires_at': 1})
        state = State('claude', self.home)
        waits = []
        class Stop:
            done = False
            def is_set(self):
                return self.done
            def wait(self, delay):
                waits.append(delay)
                if phase[0] == 0:
                    self_test.assertEqual(state.get().error, 'Network unavailable')
                    self_test.assertEqual(provider.store.read()['refresh_token'], 'test-refresh')
                    phase[0] = 1
                else:
                    self.done = True
        self_test = self
        with patch('quota_state.Provider', return_value=provider):
            poll(state, Stop(), 120, self.home, 'standalone')
        self.assertEqual(waits, [240, 120])
        self.assertEqual(len(calls), 3)
        self.assertIsNone(state.get().error)
        self.assertEqual(state.get().snapshot.windows[0].used, 10)
        self.assertEqual(provider.store.read()['refresh_token'], 'rotated-refresh')
        restored = State('claude', self.home).get()
        self.assertEqual(restored.snapshot, state.get().snapshot)
        self.assertIsNotNone(restored.error)  # Reboot does not make cached data fresh.


if __name__ == '__main__':
    unittest.main()
