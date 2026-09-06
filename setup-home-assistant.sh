#!/bin/bash
# Optional MQTT control service for an already-installed Pi appliance.
set -euo pipefail
cd "$(dirname "$0")"
if [[ $EUID -ne 0 || ! -f /proc/device-tree/model ]] ||
   ! grep -q 'Raspberry Pi' /proc/device-tree/model; then
    echo 'Run on the Pi with sudo: sudo ./setup-home-assistant.sh' >&2
    exit 1
fi
quota_user=${1:-${SUDO_USER:-}}
if [[ -z $quota_user || $quota_user == root ]] || ! id "$quota_user" >/dev/null 2>&1; then
    echo 'Supply the existing non-root appliance user.' >&2
    exit 1
fi
quota_user_home=$(getent passwd "$quota_user" | cut -d: -f6)
if [[ ! -f /opt/quota-strip/quota_api.py || ! -f $quota_user_home/.config/quota-strip/home-assistant.json ]]; then
    echo 'Install Quota Strip and run python3 quota_ha.py --configure as the appliance user first.' >&2
    exit 1
fi
# Query permission only; never execute power actions during installation.
runuser -u "$quota_user" -- sudo -n -l /usr/bin/systemctl reboot >/dev/null
runuser -u "$quota_user" -- sudo -n -l /usr/bin/systemctl poweroff >/dev/null
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3-paho-mqtt
install -m 0644 quota_ha.py quota_ha_sensors.py /opt/quota-strip/
install -m 0644 deploy/pi/quota-strip-ha.service /etc/systemd/user/quota-strip-ha.service
loginctl enable-linger "$quota_user"
quota_uid=$(id -u "$quota_user")
systemctl start "user@$quota_uid.service"
runuser -u "$quota_user" -- env XDG_RUNTIME_DIR="/run/user/$quota_uid" systemctl --user daemon-reload
runuser -u "$quota_user" -- env XDG_RUNTIME_DIR="/run/user/$quota_uid" systemctl --user enable quota-strip-ha.service
runuser -u "$quota_user" -- env XDG_RUNTIME_DIR="/run/user/$quota_uid" systemctl --user restart quota-strip-ha.service
echo 'Home Assistant controls enabled through MQTT discovery.'
