import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from quota_ha import Bridge, PiControls, discovery, load_config, validate_config


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.is_connected.return_value = True
        self.client.publish.return_value.rc = 0
        self.controls = Mock()
        self.controls.display_state.return_value = 'ON'
        self.bridge = Bridge({'host': 'broker'}, 'test', self.client, self.controls)

    def message(self, action, payload=b'PRESS', **flags):
        self.bridge.on_message(self.client, None, SimpleNamespace(
            topic=f'quota_strip/test/{action}/set', payload=payload,
            retain=flags.get('retain', False), dup=flags.get('dup', False)))

    def test_replayed_and_invalid_commands_ignored(self):
        self.message('reboot', retain=True)
        self.message('shutdown', dup=True)
        self.message('display', b'OFF; reboot')
        self.message('unknown')
        self.assertTrue(self.bridge.commands.empty())
        self.controls.power.assert_not_called()

    def test_live_command_and_expiration(self):
        self.message('display', b'OFF')
        self.bridge.execute(self.bridge.commands.get_nowait())
        self.controls.display.assert_called_once_with('OFF')
        self.bridge.execute((time.monotonic() - 6, 'shutdown', 'PRESS'))
        self.controls.power.assert_not_called()
        self.message('reboot')
        self.bridge.execute(self.bridge.commands.get_nowait())
        self.controls.power.assert_called_once_with('reboot')

    def test_disconnect_drops_pending_commands(self):
        self.message('shutdown')
        self.bridge.on_disconnect(self.client, None, None, None, None)
        self.assertTrue(self.bridge.commands.empty())
        self.client.is_connected.return_value = False
        self.bridge.execute((time.monotonic(), 'shutdown', 'PRESS'))
        self.controls.power.assert_not_called()

    def test_discovery_state_feedback_and_separate_availability(self):
        entries = discovery('test', 'quota_strip/test')
        display = entries[('switch', 'display')]
        self.assertFalse(display['optimistic'])
        self.assertEqual(display['availability_mode'], 'all')
        self.assertEqual(len(display['availability']), 2)
        for entry in entries.values():
            self.assertEqual(entry['qos'], 0)
            self.assertFalse(entry['retain'])
        self.assertEqual(entries[('button', 'shutdown')]['payload_press'], 'PRESS')
        self.assertIn('availability_topic', entries[('button', 'reboot')])

    def test_reconnect_and_ha_birth_announce_discovery(self):
        self.bridge.on_connect(self.client, None, None, SimpleNamespace(is_failure=False), None)
        self.assertTrue(self.bridge.announce.is_set())
        self.client.subscribe.assert_any_call('quota_strip/test/shutdown/set', qos=0)
        self.bridge.announce.clear()
        self.bridge.on_message(self.client, None, SimpleNamespace(
            topic='homeassistant/status', payload=b'online', retain=True, dup=False))
        self.assertTrue(self.bridge.announce.is_set())
        self.bridge.announce_device()
        calls = self.client.publish.call_args_list
        self.assertEqual(sum(c.args[0].endswith('/config') for c in calls), 3)
        self.client.publish.assert_any_call('quota_strip/test/availability', 'online', qos=1, retain=True)

    def test_unavailable_display_does_not_publish_false_off(self):
        self.controls.display_state.side_effect = subprocess.TimeoutExpired('xset', 10)
        self.bridge.refresh_display()
        self.client.publish.assert_called_once_with(
            'quota_strip/test/display/availability', 'offline', qos=1, retain=True)

    def test_queue_is_bounded(self):
        with self.assertLogs('quota-strip-ha', level='WARNING'):
            for _ in range(20):
                self.message('display', b'ON')
        self.assertEqual(self.bridge.commands.qsize(), 8)


class SystemCommandTests(unittest.TestCase):
    def test_display_queries_fresh_session_environment(self):
        run = Mock(side_effect=[SimpleNamespace(stdout='DISPLAY=:2\nXAUTHORITY=/new/auth\n'),
                               SimpleNamespace(stdout='DPMS is Enabled\n  Monitor is Off\n')])
        with patch.dict(os.environ, {'DISPLAY': ':old', 'XAUTHORITY': '/old'}):
            self.assertEqual(PiControls(run).display_state(), 'OFF')
        env = run.call_args.kwargs['env']
        self.assertEqual(env['DISPLAY'], ':2')
        self.assertEqual(env['XAUTHORITY'], '/new/auth')
        self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_fixed_display_and_power_commands(self):
        run = Mock(return_value=SimpleNamespace(stdout='DISPLAY=:0\nXAUTHORITY=/auth'))
        controls = PiControls(run)
        controls.display('OFF')
        self.assertEqual(run.call_args.args[0], ['xset', 'dpms', 'force', 'off'])
        controls.power('shutdown')
        self.assertEqual(run.call_args.args[0], ['sudo', '-n', '/usr/bin/systemctl', 'poweroff'])
        with self.assertRaises(ValueError):
            controls.power('reboot; anything')
        with self.assertRaises(ValueError):
            controls.display('anything')

    def test_missing_session_is_unavailable(self):
        controls = PiControls(Mock(return_value=SimpleNamespace(stdout='')))
        with self.assertRaises(ValueError):
            controls.display_state()


class ConfigTests(unittest.TestCase):
    def test_rejects_invalid_config_and_insecure_file_permissions(self):
        for config in ([], {'host': ''}, {'host': 'broker', 'port': True},
                       {'host': 'broker', 'ha_online_payload': 42},
                       {'host': 'broker', 'discovery_prefix': 'homeassistant/#'}):
            with self.subTest(config=config), self.assertRaises(ValueError):
                validate_config(config)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'mqtt.json'
            path.write_text(json.dumps({'host': 'broker', 'tls': True}))
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                load_config(path)
            path.chmod(0o600)
            self.assertTrue(load_config(path)['tls'])


if __name__ == '__main__':
    unittest.main()
