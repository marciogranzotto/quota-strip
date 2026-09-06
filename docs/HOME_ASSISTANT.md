# Home Assistant controls

Quota Strip can appear as one MQTT device in Home Assistant with three entities:

- **Display** switch: turns the HDMI display on or off while quota collection continues.
- **Reboot** button: restarts the Raspberry Pi.
- **Shutdown** button: shuts down the operating system cleanly.

The optional controller runs independently of the graphical dashboard. It uses [Home Assistant MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) and [Eclipse Paho](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html); no custom Home Assistant integration is needed.

## Install

First install the Pi appliance using [the Pi guide](RASPBERRY_PI.md). Home Assistant must already have an MQTT integration connected to a broker reachable from the Pi, with discovery enabled. Use a broker account allowed to publish discovery and device state and subscribe to control and Home Assistant status topics.

On the Pi, as the existing appliance user:

```sh
cd ~/quota-strip
python3 quota_ha.py --configure
sudo ./setup-home-assistant.sh
```

Enter the broker's LAN hostname or address, port, and credentials. An add-on hostname such as `core-mosquitto` may only resolve inside Home Assistant; use its host's LAN address from the Pi. Enable TLS if the broker supports it. With TLS enabled, certificate and hostname verification remain active; a private CA can be supplied with `ca_file` in the configuration.

Settings are stored at `~/.config/quota-strip/home-assistant.json` with permissions `0600`, outside the repository. The password prompt is hidden. Never commit this file or paste its contents into an issue. The controller's supplied service uses this default location.

Optional JSON settings are `discovery_prefix` (default `homeassistant`), `ha_status_topic` (default `homeassistant/status`), and `ha_online_payload` (default `online`). Match these to Home Assistant if customized. `tls` defaults to false and `port` to 1883; set the port explicitly for your TLS broker.

The installer adds `python3-paho-mqtt`, installs a separate user service, and enables user lingering so controls can work without an interactive login. It requires the appliance user to already have passwordless sudo permission for exactly these invocations:

```text
/usr/bin/systemctl reboot
/usr/bin/systemctl poweroff
```

Installation only checks those permissions; it never executes a power action. It does not grant additional sudo permissions. If these permissions are absent, have the system administrator configure them before installing.

In Home Assistant, open **Settings → Devices & services → MQTT → Devices → Quota Strip**. The display switch reports actual X11 DPMS state. Reboot and Shutdown are configuration buttons on the same device. They can be added to dashboards or automations; consider confirmation prompts for dashboard power buttons.

## Behavior and limitations

Display off uses X11 DPMS, with automatic blanking timers disabled. It does not stop quota collection or power down the Pi. The display must support HDMI power management; whether its backlight fully turns off depends on its controller. The bridge reads the current graphical session's authorization again on each command, so a session restart does not leave it using an old X authority file. A newly started graphical session defaults to display on.

If the graphical session is unavailable, only the Display entity becomes unavailable; power buttons remain available while the controller is connected. State is checked every five seconds and after a command. A broker last will marks the device offline if the connection disappears. Discovery is republished on reconnect and when Home Assistant announces startup.

Control subscriptions use QoS 0 and a clean MQTT session. Retained messages delivered on subscription, duplicate-marked messages, invalid payloads, and commands older than five seconds in the local queue are ignored. Commands are never retained by the discovery entities. Do not publish retained commands yourself: MQTT can forward a new retained publish to an already subscribed client as an ordinary live message. MQTT access to these topics grants control of the appliance; keep the broker authenticated and restrict topic access appropriately.

**Shutdown cannot be undone through this integration:** the Pi's network and controller stop with the OS. A Raspberry Pi 3 requires a physical power cycle or suitable external wake hardware to start again. Turning the Display switch on is different from starting a shut-down Pi. There is no software-only power-on button here.

## Update, diagnose, remove

After updating your checkout, rerun `sudo ./setup-home-assistant.sh` to install controller changes. It restarts only the controller, not Home Assistant, the broker, or the dashboard.

```sh
systemctl --user status quota-strip-ha.service
sudo journalctl _SYSTEMD_USER_UNIT=quota-strip-ha.service -n 30 --no-pager
systemctl --user restart quota-strip-ha.service
```

Logs report connection and command outcomes without printing broker credentials or subprocess output. If MQTT connects but entities do not appear, check discovery settings and broker ACLs. If only Display is unavailable, check the graphical session and its `DISPLAY`/`XAUTHORITY` values in `systemctl --user show-environment`.

To stop controls:

```sh
systemctl --user disable --now quota-strip-ha.service
```

Retained discovery entries remain in the broker. To remove them permanently, publish an empty retained payload to the three discovery config topics for this device, then remove its entities in Home Assistant if still present. Do not clear other devices' topics. User lingering is left enabled because other services may use it.

## Validation

Automated tests cover discovery, state feedback, missing sessions, command allowlists, retained/duplicate replay handling, queue bounds, stale commands, disconnect handling, and private configuration permissions. Actual reboot and shutdown commands are mocked during tests. They are not executed during setup or display verification.

On the Pi 3 test appliance, all three entities were discovered by Home Assistant. Live MQTT OFF and ON commands produced matching retained state and X11 DPMS readings, with the display restored to ON. The controller service and all 59 tests passed on the Pi. Physical reboot, shutdown, and cold-boot recovery remain untested.
