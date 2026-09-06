#!/usr/bin/env python3
"""Optional Home Assistant MQTT controls; independent of quota collection."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time

from quota_api import atomic_json, data_home
from quota_ha_sensors import QuotaSensors

LOG = logging.getLogger("quota-strip-ha")


def load_config(path):
    if path.stat().st_mode & 0o077:
        raise ValueError("MQTT configuration must have permissions 0600")
    return validate_config(json.loads(path.read_text()))


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Invalid MQTT configuration")
    if not isinstance(config.get("host"), str) or not config["host"].strip():
        raise ValueError("MQTT host is required")
    port = config.get("port", 1883)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid MQTT port")
    for key in ("username", "password", "ca_file", "ha_online_payload", "timezone"):
        if key in config and not isinstance(config[key], str):
            raise ValueError("Invalid MQTT configuration")
    if type(config.get("tls", False)) is not bool:
        raise ValueError("Invalid MQTT TLS setting")
    for key, default in (("discovery_prefix", "homeassistant"),
                         ("ha_status_topic", "homeassistant/status")):
        value = config.get(key, default)
        if not isinstance(value, str) or not value or any(c in value for c in "+#\x00"):
            raise ValueError("Invalid MQTT topic")
    return config


class PiControls:
    """Use the active X session's authorization and fixed system commands."""

    def __init__(self, run=subprocess.run):
        self.run = run

    def command(self, args, **kwargs):
        return self.run(args, check=True, capture_output=True, text=True,
                        timeout=10, **kwargs).stdout

    def display_env(self):
        values = self.command(["systemctl", "--user", "show-environment"])
        env = os.environ.copy()
        # Do not use stale values inherited before a graphical session restart.
        env.pop("DISPLAY", None)
        env.pop("XAUTHORITY", None)
        for line in values.splitlines():
            key, _, value = line.partition("=")
            if key in ("DISPLAY", "XAUTHORITY"):
                env[key] = value
        if not env.get("DISPLAY") or not env.get("XAUTHORITY"):
            raise ValueError("Graphical session unavailable")
        return env

    def display_state(self):
        output = self.command(["xset", "q"], env=self.display_env())
        if "DPMS is Disabled" in output:
            return "ON"
        state = re.search(r"Monitor is (On|Off|Standby|Suspend)\b", output)
        if not state:
            raise ValueError("Display power status unavailable")
        return "ON" if state[1] == "On" else "OFF"

    def display(self, state):
        if state not in ("ON", "OFF"):
            raise ValueError("Invalid display command")
        env = self.display_env()
        self.command(["xset", "+dpms"], env=env)
        self.command(["xset", "dpms", "0", "0", "0"], env=env)
        self.command(["xset", "dpms", "force", state.lower()], env=env)

    def power(self, action):
        if action not in ("reboot", "shutdown"):
            raise ValueError("Invalid power command")
        self.command(["sudo", "-n", "/usr/bin/systemctl",
                      "reboot" if action == "reboot" else "poweroff"])


def discovery(device_id, root):
    device = {"identifiers": [f"quota_strip_{device_id}"], "name": "Quota Strip",
              "manufacturer": "Quota Strip", "model": "Raspberry Pi appliance"}
    common = {"device": device, "qos": 0, "retain": False,
              "availability_topic": f"{root}/availability"}
    display = {**common, "name": "Display", "unique_id": f"{device_id}_display",
               "icon": "mdi:monitor", "command_topic": f"{root}/display/set",
               "state_topic": f"{root}/display/state", "optimistic": False}
    del display["availability_topic"]
    display["availability"] = [{"topic": f"{root}/availability"},
                               {"topic": f"{root}/display/availability"}]
    display["availability_mode"] = "all"
    result = {("switch", "display"): display}
    for action in ("reboot", "shutdown"):
        button = {**common, "name": action.title(), "unique_id": f"{device_id}_{action}",
                  "command_topic": f"{root}/{action}/set", "payload_press": "PRESS",
                  "entity_category": "config", "icon": "mdi:restart" if action == "reboot" else "mdi:power"}
        if action == "reboot":
            button["device_class"] = "restart"
        result[("button", action)] = button
    return result


class Bridge:
    def __init__(self, config, device_id, client, controls=None):
        self.config, self.device_id, self.client = config, device_id, client
        self.controls = controls or PiControls()
        self.root = f"quota_strip/{device_id}"
        self.commands = queue.Queue(maxsize=8)
        self.announce = threading.Event()
        self.stop = threading.Event()
        self.published = {}
        self.sensors = QuotaSensors(client, device_id, self.root,
            config.get("discovery_prefix", "homeassistant"), data_home(),
            config.get("timezone", os.environ.get("QUOTA_TIMEZONE", "America/Sao_Paulo")))
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        client.will_set(f"{self.root}/availability", "offline", qos=1, retain=True)

    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            LOG.warning("MQTT connection rejected")
            return
        # Clean sessions and QoS 0 prevent queued power commands across reconnects.
        for control in ("display", "reboot", "shutdown"):
            client.subscribe(f"{self.root}/{control}/set", qos=0)
        client.subscribe(self.config.get("ha_status_topic", "homeassistant/status"), qos=0)
        self.announce.set()
        LOG.info("MQTT connected")

    def on_disconnect(self, client, userdata, flags, reason_code, properties):
        while True:
            try:
                self.commands.get_nowait()
            except queue.Empty:
                break

    def on_message(self, client, userdata, message):
        if message.topic == self.config.get("ha_status_topic", "homeassistant/status"):
            if message.payload == self.config.get("ha_online_payload", "online").encode():
                self.announce.set()
            return
        if message.retain or message.dup:
            return
        accepted = {f"{self.root}/display/set": (b"ON", b"OFF"),
                    f"{self.root}/reboot/set": (b"PRESS",),
                    f"{self.root}/shutdown/set": (b"PRESS",)}
        if message.payload not in accepted.get(message.topic, ()):
            return
        action = message.topic.split("/")[-2]
        try:
            self.commands.put_nowait((time.monotonic(), action, message.payload.decode("ascii")))
        except queue.Full:
            LOG.warning("Control queue full; command ignored")

    def publish(self, suffix, value, force=False):
        if not self.client.is_connected():
            return
        if force or self.published.get(suffix) != value:
            info = self.client.publish(f"{self.root}/{suffix}", value, qos=1, retain=True)
            if info.rc == 0:
                self.published[suffix] = value

    def announce_device(self):
        prefix = self.config.get("discovery_prefix", "homeassistant")
        for (kind, name), payload in discovery(self.device_id, self.root).items():
            self.client.publish(f"{prefix}/{kind}/quota_strip_{self.device_id}/{name}/config",
                                json.dumps(payload), qos=1, retain=True)
        self.sensors.reset_discovery()
        self.published.clear()
        self.refresh_display()
        self.publish("availability", "online")

    def refresh_display(self):
        try:
            state = self.controls.display_state()
        except (OSError, ValueError, subprocess.SubprocessError):
            self.publish("display/availability", "offline")
        else:
            self.publish("display/state", state)
            self.publish("display/availability", "online")

    def execute(self, item):
        received, action, value = item
        if not self.client.is_connected() or time.monotonic() - received > 5:
            return
        try:
            if action == "display":
                self.controls.display(value)
            else:
                self.controls.power(action)
            LOG.info("Control completed: %s", action)
        except (OSError, ValueError, subprocess.SubprocessError):
            # Never log command output or broker credentials.
            LOG.warning("Control failed: %s", action)
        self.refresh_display()

    def run(self):
        self.client.connect_async(self.config["host"], self.config.get("port", 1883), keepalive=30)
        self.client.loop_start()
        next_state = 0
        next_quotas = 0
        try:
            while not self.stop.is_set():
                if self.announce.is_set():
                    self.announce.clear()
                    self.announce_device()
                    next_quotas = 0
                if time.monotonic() >= next_state:
                    if self.client.is_connected():
                        self.refresh_display()
                    next_state = time.monotonic() + 5
                if time.monotonic() >= next_quotas:
                    self.sensors.refresh(time.time())
                    next_quotas = time.monotonic() + 30
                try:
                    self.execute(self.commands.get(timeout=0.5))
                except queue.Empty:
                    pass
        finally:
            if self.client.is_connected():
                info = self.client.publish(f"{self.root}/availability", "offline", qos=1, retain=True)
                try:
                    info.wait_for_publish(timeout=2)
                except (RuntimeError, ValueError):
                    pass
            self.client.disconnect()
            self.client.loop_stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configure", action="store_true", help="Save private MQTT settings interactively")
    args = parser.parse_args()
    path = data_home() / "home-assistant.json"
    if args.configure:
        config = {"host": input("MQTT broker host: ").strip(),
                  "port": int(input("MQTT port [1883]: ") or "1883"),
                  "username": input("MQTT username: "), "password": getpass.getpass("MQTT password: "),
                  "tls": input("Use TLS? [y/N]: ").strip().lower() == "y"}
        atomic_json(path, validate_config(config))
        print("Saved private MQTT settings. Run sudo ./setup-home-assistant.sh to enable controls.")
        return 0
    import paho.mqtt.client as mqtt
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = load_config(path)
        device_id = hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()[:16]
    except (OSError, ValueError):
        LOG.error("MQTT configuration unavailable or invalid; run quota_ha.py --configure")
        return 1
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"quota-strip-{device_id}",
                         clean_session=True, protocol=mqtt.MQTTv311)
    if config.get("username"):
        client.username_pw_set(config["username"], config.get("password"))
    if config.get("tls"):
        client.tls_set(ca_certs=config.get("ca_file") or None)
    client.reconnect_delay_set(min_delay=1, max_delay=60)
    client.max_queued_messages_set(32)
    bridge = Bridge(config, device_id, client)
    signal.signal(signal.SIGTERM, lambda *_: bridge.stop.set())
    signal.signal(signal.SIGINT, lambda *_: bridge.stop.set())
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
