# Raspberry Pi appliance

Target: **Raspberry Pi 3 Model B v1.2**, a 1920 × 480 HDMI strip, and Raspberry Pi OS Lite **64-bit** (Trixie). The Python/Pygame application talks directly to provider account endpoints. A Mac, browser, or coding CLI is not needed during normal operation.

First boot, standalone quota collection, the rotated 1920 × 480 display, app crash recovery, and graphical session shutdown have been verified on a Pi 3B and a physical strip panel. Reboot, network recovery, and long-running operation remain part of the acceptance checks below; reliable power is required.

## 1. Prepare the microSD

Use **Raspberry Pi Imager 2.x or newer** from the [official download page](https://www.raspberrypi.com/software/). Imager 1.x cannot apply customization to current Trixie images: it writes a valid OS image but uses the wrong initialization format, leaving the user, Wi-Fi, and SSH settings unapplied. See the [official compatibility documentation](https://github.com/raspberrypi/rpi-imager/blob/main/doc/os_customisation_formats.md). An image verification success alone does not verify first-boot customization.

Select Raspberry Pi 3, Raspberry Pi OS Lite (64-bit), and the intended microSD. Writing an image erases that entire card; identify its label and capacity first.

Configure:

- Hostname: `quota-strip`.
- Username: `quota`, with a password you choose.
- SSH with public-key authentication; add your computer's public key.
- Your actual timezone and keyboard layout.
- Ethernet, or your Wi-Fi credentials with the correct country setting.

Wi-Fi is sufficient for both setup and normal operation; Ethernet is optional. The Pi 3B supports **2.4 GHz Wi-Fi only**, so enable that band on the configured network. See Raspberry Pi's [wireless compatibility table](https://www.raspberrypi.com/documentation/computers/getting-started.html).

Write and verify the image, then insert the card into the Pi. Connect HDMI, networking, and a suitable micro-USB power supply. Allow several minutes for the first boot. Connect using the key configured in Imager:

```sh
ssh quota@quota-strip.local
```

If `.local` discovery is unavailable, use the Pi's address from your router. Keep the Mac's private SSH key on the Mac.

## 2. Install the dashboard

On the Pi:

```sh
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/marciogranzotto/quota-strip.git
cd quota-strip
sudo ./setup-pi.sh
```

The installer uses distribution packages for Python, Pygame, fonts, Xorg, LightDM, and the systemd user session. It installs an explicit list of application files under `/opt/quota-strip`, configures LightDM to sign the appliance user into the dashboard, and installs a systemd user service to restart the app after an exit or crash. A separate session target stays active while the app restarts, so a crash does not return to the login screen; stopping the graphical session also stops the app. It does not copy checkout credentials or change HDMI timings. It stops the graphical session while updating and leaves it stopped until sign-in is ready.

## 3. Sign in as the appliance user

Run these **without sudo**:

```sh
python3 /opt/quota-strip/quota_auth.py claude
python3 /opt/quota-strip/quota_auth.py codex
```

Follow the browser links from your phone or computer. Claude's terminal flow asks for the returned `code#state` with hidden input; Codex uses a short device code. Credentials are stored with mode `0600` under the appliance user's `~/.config/quota-strip`. Never put them in Git or share the token files. Token renewal is automatic, but revoked access requires sign-in again.

To move an existing Quota Strip installation between devices, transfer **only its dedicated** `claude-auth.json` and `codex-auth.json` privately over SSH, with both displays stopped. Do not copy Claude Code or Codex CLI credentials. Treat this as a move: do not run two collectors with the same rotating refresh token. Alternatively, sign in separately on the Pi and leave the Mac's session independent.

## 4. Start and inspect

```sh
sudo systemctl restart lightdm
systemctl --user status quota-strip.service
journalctl --user -u quota-strip.service -n 50 --no-pager
```

The dashboard should appear on HDMI and start automatically after reboot. It polls providers independently. During network failures, it retains visibly stale readings and backs off; after reconnection, it replaces them with fresh data. It can boot offline and show cached data while retrying.

Settings are in `~/.config/quota-strip/display.env`, initially populated from the Pi's system timezone. For example:

```ini
QUOTA_TIMEZONE=Europe/London
```

After changing settings:

```sh
systemctl --user restart quota-strip.service
```

To pause the display, use `systemctl --user stop quota-strip.service`. To resume its graphical session, use `sudo systemctl restart lightdm`. In appliance mode, closing the app or pressing Q restarts it after five seconds.

## 5. Verify the HDMI mode

Start with the panel's advertised EDID mode. In the display session, `xrandr --query` lists modes. From SSH, obtain the display and authority paths with `systemctl --user show-environment`, and supply those values to `xrandr`; do not change X access controls.

If the panel advertises 1920 × 480 but another mode is selected, select that existing mode with `xrandr --output <connector> --mode 1920x480`, then make the setting persistent once confirmed. If it does not advertise the mode, obtain the manufacturer's timing specifications before forcing custom timings. Current Raspberry Pi OS uses KMS configuration; legacy `hdmi_cvt` recipes should not be applied blindly. See [Raspberry Pi display configuration](https://www.raspberrypi.com/documentation/computers/configuration.html).

Some strip panels advertise their native mode as **480 × 1920**, even when mounted horizontally. Use that native mode and rotate it instead of creating a custom timing:

```sh
xrandr --output <connector> --mode 480x1920 --rotate right
```

Choose `right` for clockwise rotation or `left` for counterclockwise rotation. Confirm the picture is upright and `xrandr --query` reports a 1920 × 480 desktop. On this dedicated, single-display appliance, save the confirmed orientation in `/etc/X11/xorg.conf.d/90-quota-strip-monitor.conf`:

```conf
Section "Monitor"
    Identifier "HDMI-1"
    Option "PreferredMode" "480x1920"
    Option "Rotate" "right"
EndSection
```

The `Identifier` must match the connector reported by `xrandr` (`HDMI-1` in this example), so Xorg applies the section to that output before the dashboard starts. Restart LightDM to apply changes. This rotates the graphical session; the text boot console retains its own orientation. See the [Xorg monitor configuration reference](https://www.x.org/releases/current/doc/man/man5/xorg.conf.5.xhtml).

## Power checks

Repeated `Undervoltage detected!` console messages indicate a power problem, even if Wi-Fi and SSH work. Inspect `vcgencmd get_throttled`: bit 0 means undervoltage is active and bit 2 means throttling is active; bits 16 and 18 retain the corresponding history since boot. Check both the adapter and the micro-USB cable, and shut down cleanly before changing the power connection. See [Raspberry Pi power supply guidance](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#power-supply).

## Acceptance checks on the Pi

1. Confirm `uname -m` is `aarch64` and `/proc/device-tree/model` identifies the expected Pi 3B.
2. Confirm Claude's five-hour, weekly, and Fable meters; Codex's returned windows; and the reset-bank count/expiry against account usage pages.
3. Reboot with the Mac disconnected. The native dashboard must return by itself at 1920 × 480.
4. Interrupt the Pi's network. Readings must become stale without disappearing or becoming zero. Reconnect and verify automatic recovery.
5. Kill the dashboard process and confirm systemd restarts it. Inspect `NRestarts` using `systemctl --user show quota-strip.service -p NRestarts`.
6. Leave it running across quota resets and token expiry. Inspect memory/CPU use and logs, and confirm no repeated sign-in prompts or tight request loops.

## Updates

```sh
cd ~/quota-strip
git pull --ff-only
sudo ./setup-pi.sh
sudo systemctl restart lightdm
```

Credentials and display settings remain in the appliance user's configuration directory.
