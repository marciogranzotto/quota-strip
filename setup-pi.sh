#!/bin/bash
# Install onto a dedicated Raspberry Pi OS Lite appliance, from its checkout.
set -euo pipefail
cd "$(dirname "$0")"
if [[ $(uname -s) != Linux ]] || [[ ! -f /proc/device-tree/model ]] ||
   ! grep -q 'Raspberry Pi' /proc/device-tree/model; then
    echo 'Run this installer on the Raspberry Pi, not on the development computer.' >&2
    exit 1
fi
if [[ $EUID -ne 0 ]]; then
    echo 'Run with sudo: sudo ./setup-pi.sh' >&2
    exit 1
fi
quota_user=${1:-${SUDO_USER:-}}
if [[ -z $quota_user || $quota_user == root ]] || ! id "$quota_user" >/dev/null 2>&1; then
    echo 'Supply the existing non-root appliance user: sudo ./setup-pi.sh quota' >&2
    exit 1
fi
quota_user_home=$(getent passwd "$quota_user" | cut -d: -f6)
quota_group=$(id -gn "$quota_user")
if [[ ! -d $quota_user_home ]]; then
    echo 'The appliance user must have a home directory.' >&2
    exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    python3 python3-pygame ca-certificates tzdata fontconfig fonts-dejavu-core \
    xserver-xorg x11-xserver-utils lightdm dbus-user-session libpam-systemd

# Stop the dedicated graphical session before replacing its code.
systemctl stop lightdm
# SSH may keep the user manager alive after the graphical session ends.
quota_uid=$(id -u "$quota_user")
if [[ -S /run/user/$quota_uid/bus && -f /etc/systemd/user/quota-strip.service ]]; then
    runuser -u "$quota_user" -- env XDG_RUNTIME_DIR="/run/user/$quota_uid" \
        systemctl --user stop quota-strip.service
fi
install -d -m 0755 /opt/quota-strip /opt/quota-strip/assets
# Explicit allowlist: never copy a checkout's credentials, logs, or snapshots.
install -m 0644 quota_api.py quota_auth.py quota_callback.py quota_model.py \
    quota_state.py quota_local.py quota_display.py LICENSE /opt/quota-strip/
install -m 0644 assets/*.png /opt/quota-strip/assets/
install -m 0644 assets/README.md assets/LICENSE* /opt/quota-strip/assets/
install -D -m 0755 deploy/pi/quota-strip-session /usr/local/bin/quota-strip-session
install -D -m 0644 deploy/pi/quota-strip.desktop /usr/share/xsessions/quota-strip.desktop
install -D -m 0644 deploy/pi/quota-strip.service /etc/systemd/user/quota-strip.service
install -d -m 0755 /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/90-quota-strip.conf <<CONFIG
[Seat:*]
autologin-user=$quota_user
autologin-user-timeout=0
autologin-session=quota-strip
user-session=quota-strip
allow-guest=false
xserver-allow-tcp=false
CONFIG
install -d -o "$quota_user" -g "$quota_group" -m 0700 "$quota_user_home/.config/quota-strip"
if [[ ! -e $quota_user_home/.config/quota-strip/display.env ]]; then
    quota_zone=$(timedatectl show -p Timezone --value)
    printf 'QUOTA_TIMEZONE=%s\n' "$quota_zone" > "$quota_user_home/.config/quota-strip/display.env"
    chown "$quota_user:$quota_group" "$quota_user_home/.config/quota-strip/display.env"
    chmod 0600 "$quota_user_home/.config/quota-strip/display.env"
fi
systemctl enable lightdm
systemctl set-default graphical.target
echo 'Installed. Sign in as the appliance user using /opt/quota-strip/quota_auth.py.'
echo 'Then run: sudo systemctl restart lightdm'
