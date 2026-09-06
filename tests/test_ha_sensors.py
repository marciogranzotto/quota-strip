import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from quota_ha_sensors import QuotaSensors, sensor_groups
from quota_model import Snapshot, Window, ResetBank, WEEK, FIVE_HOURS


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.now = 1788700000
        self.zone = ZoneInfo('UTC')
        self.snapshot = Snapshot('codex', (
            Window('weekly', 'Weekly', 43, self.now + WEEK - 3600, WEEK),
            Window('spark', '5-hour', 0, self.now + FIVE_HOURS, FIVE_HOURS, 'Spark'),
        ), self.now, reset_bank=ResetBank(2, self.now + 1000, False))

    def test_usage_remaining_and_budget_match_model(self):
        groups = sensor_groups(self.snapshot, self.now, self.zone)
        weekly = next(v for v in groups.values() if v[0] == 'Codex Weekly')
        self.assertEqual(weekly[2]['used'], 43)
        self.assertEqual(weekly[2]['remaining'], 57)
        self.assertEqual(weekly[2]['budget'], self.snapshot.windows[0].budget(self.now, self.zone))
        self.assertTrue(weekly[2]['available'])
        self.assertEqual(groups['codex_bank'][2]['count'], 2)
        self.assertFalse(groups['codex_bank'][2]['expiry_details_complete'])

    def test_stale_and_expired_data_are_unavailable(self):
        groups = sensor_groups(self.snapshot, self.now + 601, self.zone)
        self.assertTrue(all(not g[2]['available'] for g in groups.values()))
        stale = Snapshot('claude', (
            Window('retained', 'Fable weekly', 9, self.now + WEEK, WEEK, stale=True),
            Window('expired', '5-hour', 12, self.now, FIVE_HOURS),
            Window('old', 'Weekly', 20, self.now + WEEK, WEEK, observed_at=self.now - 601),
        ), self.now)
        self.assertTrue(all(not g[2]['available'] for g in sensor_groups(stale, self.now, self.zone).values()))
        future = Snapshot('codex', self.snapshot.windows, self.now + 61)
        self.assertTrue(all(not g[2]['available'] for g in sensor_groups(future, self.now, self.zone).values()))

    def test_stable_keys_do_not_depend_on_labels(self):
        changed = Snapshot('codex', (Window('weekly', 'Renamed', 43, self.now + WEEK, WEEK),), self.now)
        first = next(iter(sensor_groups(self.snapshot, self.now, self.zone)))
        self.assertEqual(first, next(iter(sensor_groups(changed, self.now, self.zone))))

    def test_discovery_retained_but_state_expires_and_is_not_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            path = home / 'codex-snapshot.json'
            path.write_text(json.dumps(self.snapshot.to_dict()))
            client = Mock()
            client.is_connected.return_value = True
            client.publish.return_value.rc = 0
            sensors = QuotaSensors(client, 'test', 'quota_strip/test', 'homeassistant', home, 'UTC')
            sensors.refresh(self.now)
            configs = [json.loads(c.args[1]) for c in client.publish.call_args_list if c.kwargs['retain']]
            self.assertEqual(len(configs), 10)
            self.assertTrue(all(c['expire_after'] == 90 for c in configs))
            self.assertTrue(all(c['availability_mode'] == 'all' for c in configs))
            self.assertEqual(sum(c.get('device_class') == 'timestamp' for c in configs), 3)
            client.publish.reset_mock()
            sensors.refresh(self.now + 30)
            self.assertEqual(client.publish.call_count, 3)
            self.assertTrue(all(not c.kwargs['retain'] for c in client.publish.call_args_list))
            sensors.reset_discovery()
            client.publish.reset_mock()
            sensors.refresh(self.now + 60)
            self.assertEqual(client.publish.call_count, 13)
            path.write_text('invalid JSON')
            client.publish.reset_mock()
            sensors.refresh(self.now + 90)
            self.assertEqual(client.publish.call_count, 3)
            self.assertTrue(all(not json.loads(c.args[1])['available'] for c in client.publish.call_args_list))

    def test_no_publication_when_disconnected(self):
        client = Mock()
        client.is_connected.return_value = False
        QuotaSensors(client, 'test', 'root', 'ha', Path('/nonexistent'), 'UTC').refresh(self.now)
        client.publish.assert_not_called()
