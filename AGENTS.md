# AGENTS.md

This file guides Codex, Claude Code, and other coding agents working in this repository; it is the single source of project guidance (`CLAUDE.md` imports it). It records the project overview together with decisions established during development. Verify changeable deployment details against current code, documentation, and the device; conversation history is not proof of current state.

## Working conventions

- Work in the actual `quota-strip` repository. Some sessions start in a separate `usage-meter` directory; check the working directory and Git root before editing or running commands.
- Fix root causes, not symptoms. Before non-trivial implementation, inspect existing dependency capabilities and research established approaches in primary documentation. State the proposed approach and its source before a large change.
- Preserve unrelated work, including untracked files. Never delete remote branches unless the user explicitly requests it.
- Use the monitor MCP for asynchronous work when available, preferably `monitor_wait`. Do not poll with sleeps or repeated shell/terminal calls unless the monitor cannot observe the condition. For SD-card flashing, the user specifically prefers to report completion themselves rather than have the agent watch it.
- Run checks appropriate to the change, and distinguish automated checks, live integration checks, and physical hardware verification. Do not run broad tests for documentation-only edits.

## What this is

A native Pygame dashboard for a fixed 1920 × 480 strip display that shows Claude and Codex subscription quotas (five-hour, weekly, model-specific limits such as Fable, Codex Spark, banked Codex resets) with pacing markers. It runs on macOS and on a Raspberry Pi 3. It is a flat set of scripts, not an installable package (`[tool.uv] package = false`). Runtime dependencies: stdlib plus `pygame`. The optional Home Assistant controller also uses `paho-mqtt`. The code supports Python 3.9+ (CI uses 3.11).

## Commands

```sh
./setup-mac.sh                                   # create .venv (prefers a framework Python 3.13)
./run.sh --demo --windowed                       # synthetic data, no account or network needed
./run.sh --source local --windowed               # reuse existing Claude Code / Codex CLI sign-ins
./run.sh --source standalone --windowed          # use credentials from quota_auth.py
./run.sh --json --source local                   # one normalized read, no GUI; exit 1 if any provider failed

.venv/bin/python -m unittest discover -s tests -v                             # all tests
.venv/bin/python -m unittest tests.test_quota.BudgetTests -v                  # one class
.venv/bin/python -m unittest tests.test_quota.BudgetTests.test_matches_status_bar_end_of_today -v  # one test

# Regenerate the README image deterministically (headless, no credentials)
./run.sh --demo --timezone UTC --at 2026-01-07T18:00:00+00:00 --screenshot docs/preview.png
```

CI (`.github/workflows/ci.yml`) also runs `compileall` on every module, `shellcheck` on `setup-home-assistant.sh setup-pi.sh install.sh deploy/pi/quota-strip-session`, `systemd-analyze --user verify` on the `deploy/pi/*.service`/`.target` units, and a headless demo render (`SDL_VIDEODRIVER=dummy`). When you add a module, add it to the `compileall` list. When you add a shell script, add it to the `shellcheck` list.

## Architecture

Data flows **provider → `Snapshot` → `State` → display / HA sensors**:

- **`quota_model.py`**: the provider-neutral core. `parse_claude` / `parse_codex` normalize raw account responses into a `Snapshot` of `Window`s (percent used, reset time, duration, bucket, `observed_at`, `stale`) plus an optional `ResetBank`. `Window.budget()` holds the pacing math. Weekly windows use the allowance at the next local midnight in the configured zone, so DST matters. Five-hour windows use a moving allowance based on the time left until reset. The README's "Reading the weekly/five-hour meter" sections are the spec for this math. Claude model quotas (Fable) come from `limits[]`, not `seven_day_*`. Codex prefers the multi-bucket representation, and Spark is detected by name or the `codex_bengalfox` bucket.
- **Two interchangeable provider sources.** Each exposes `.fetch() -> Snapshot` and raises `QuotaError`:
  - `quota_local.py` (`--source local`, default on macOS): reads the Claude Code credentials or Keychain entry read-only and never refreshes them. It also launches `codex app-server` for one `account/rateLimits/read` call. `LocalClaude` can fall back to `~/.claude/.debug/statusline-input.json` and backs off the account endpoint on its own.
  - `quota_api.py` `Provider` (`--source standalone`, default elsewhere): uses the appliance's own OAuth credentials in `CredentialStore` under `QUOTA_HOME` (default `~/.config/quota-strip`). It refreshes tokens under a file lock and writes JSON atomically. Both Claude sources read quotas through `ClaudeResetGrants`, which adds optional limit-reset grants, and Codex standalone adds optional reset-credit details; failures there set a snapshot warning without affecting quotas. `quota_auth.py` and `quota_callback.py` run the PKCE sign-in.
- **`QuotaError.fallback`** carries a partial `Snapshot`. `quota_state.State.partial()` merges it with the previous snapshot and marks the retained windows `stale` while keeping their original `observed_at`. This is how the Fable reading stays visible with a "Last known" label when the account endpoint fails but the status line still works.
- **`quota_state.py`**: one polling thread per provider with exponential backoff (capped at 1800 s, honoring `retry_after`). On success it persists `<provider>-snapshot.json` to `QUOTA_HOME` and reloads that file on startup as "Cached - reconnecting". Arbitrary exceptions are replaced with a generic message on purpose, so provider bodies and credentials never reach the screen.
- **`quota_display.py`**: CLI entry point and renderer. It redraws on a 15-second tick or when the readings change, independently of the poll interval. `--demo`, `--snapshot`, `--screenshot` and `--at` make no network calls. `--at` is only allowed with demo or snapshot renders.
- **`quota_ha.py` + `quota_ha_sensors.py`**: a **separate process** (its own systemd unit) for MQTT discovery. It provides a display on/off switch plus reboot and shutdown buttons (via `sudo`), and quota sensors. The sensors read the `*-snapshot.json` files that the dashboard writes, so they never make provider requests. They publish non-retained state with a staleness check.

## Invariants to preserve

- Missing data is never zero usage. Unknown values render as unavailable, and a banked-reset count that is missing shows "Count unavailable".
- Never log, print or display tokens or raw provider response bodies. Snapshots store only normalized values. Reset-credit IDs and descriptions are discarded.
- Local mode must not modify the CLI tools' credentials. Standalone mode must never touch the CLI stores.
- The consumer usage endpoints are unofficial. Parse defensively. A parser failure becomes `QuotaError("Quota response changed; update collector")`.
- The `MAX 20×` and `CHATGPT PRO` header badges are static labels, not detected metadata.

## Product decisions from this project

- Keep the existing pacing rule unless the user explicitly asks to change it. The user considered distributing remaining quota over remaining days and chose to retain the original rule.
- A display such as `43% / 3%` means **used / pacing allowance**, not used / total or used / remaining. Provider UIs may instead show remaining quota: 43% used equals 57% remaining. Verify the reported reset time and window duration before diagnosing a mismatch.
- Weekly pacing targets the next local midnight. Five-hour pacing moves with elapsed time in the reported window. These are guides, not provider-enforced spending caps. Do not change either calculation merely because usage exceeds the marker.
- Weekly bars have seven equal segments; five-hour bars have five. Partial segments show exact usage. UI headroom and over-budget labels use `%` of the full quota, not `pp`.
- Keep `FABLE / WEEKLY` visually distinct with its purple accent. Preserve provider logos to the left of their titles.
- Banked resets are read-only information. Never redeem a reset as part of collection, verification, or testing. Claude limit-reset grants come from the `cedar_ember` block of `/api/oauth/usage?cedar_ember=1&skip_spend=1`, read by `quota_api.ClaudeResetGrants` with a Claude Code user agent (the server reports grants only to that surface); see `docs/RESEARCH.md`. Never call the claim endpoint `POST /api/organizations/{org}/reset_rate_limits`. Never add a second usage request: the endpoint returns HTTP 429 for back-to-back requests from one token, so grants ride on the ordinary quota read (the grant query every 10 minutes, otherwise plain).
- Do not infer model identity from undocumented bucket codenames or treat every similarly named JSON field as a quota. In particular, Claude `seven_day_breakdown` is metadata; model-specific windows come from supported structures such as `limits[].weekly_scoped`.

## Public repository and private runtime data

- This repository is public. Before committing or pushing, inspect the exact files being published and check for secrets and personal data. `.gitignore` alone is not a privacy review.
- Keep OAuth credentials, MQTT passwords, SSH private keys, Wi-Fi settings, private provisioning files, device/account identifiers, and live snapshots outside the repository. Do not copy private Home Assistant configuration or dashboard backups into public docs.
- Use synthetic `--demo` data for committed screenshots. Photos of the user's desk or browser may contain unrelated personal or work information and must not become public assets.
- Read secrets only as needed for authorized configuration, keep them within the intended service boundary, and never echo them into command output or paste them into chat. Publish sanitized examples rather than real configuration.

## Home Assistant conventions

- Reuse the separate MQTT bridge. Quota sensors consume the dashboard's saved snapshots; they must not introduce additional provider polling or require the Mac to be running.
- Preserve stable discovery IDs and the existing Quota Strip device, so dashboard entity references and history survive updates. Retain discovery, but keep quota state non-retained with expiration and freshness checks. Missing readings must not become zero.
- Keep actual display-state feedback and separate display availability from controller availability. Display off uses DPMS while collection continues.
- Keep reboot/shutdown commands fixed and allowlisted; ignore replayed, duplicate, or stale control messages. Do not execute destructive power actions merely to test their wiring.
- The existing System dashboard has a Quota Strip section with gauges, quota details, banked resets, and controls. Preserve unrelated cards and views when editing it. Use the supported dashboard editor/API rather than overwriting live storage files. Keep confirmation prompts on reboot/shutdown dashboard actions.
- Keep the controller's pacing timezone aligned with the dashboard's timezone.

## Deployment

- Pi: `setup-pi.sh` installs to `/opt/quota-strip`. The dashboard runs under the systemd user unit `deploy/pi/quota-strip.service` (`--source standalone`, `NoNewPrivileges=yes`). `quota-strip-session.target` is started from the LightDM session script so that app crashes don't end the X session. `install.sh` picks the Mac or Pi installer.
- See `docs/RASPBERRY_PI.md` (setup and the hardware validation record), `docs/HOME_ASSISTANT.md`, and `docs/RESEARCH.md` (endpoint references).
- README's status and roadmap record what has been verified on hardware. Keep it accurate: don't mark something as verified unless it was actually exercised on a device.

### Appliance operations

- The actual appliance is a **Raspberry Pi 3 Model B v1.2**, not the original B+ initially assumed. It must run independently of the Mac. The strip's native panel mode was verified as 480 × 1920, rotated right to provide the physical 1920 × 480 landscape display; check the current output name before changing Xorg configuration.
- Use the installers and documented service boundaries. Restart only the component changed; an MQTT bridge update does not require restarting Home Assistant, its broker, the graphical session, or the Pi.
- A momentary button between GPIO3 and GND (physical pins 5 and 6) is configured with `dtoverlay=gpio-shutdown` and was physically tested working on 2026-09-25 (clean shutdown and wake from halt, as reported by the user). It is in service on the appliance; do not remove or rewire it, or change the overlay, without the user's request. If you change boot configuration, re-verify the button afterward. Do not add a competing GPIO polling daemon or the conflicting `gpio-poweroff` overlay.
- Native Ethernet Wake-on-LAN cannot start this Pi 3B after shutdown. A connected power supply plus the GPIO button can wake it; remote power-on needs additional hardware. Do not add a software-only wake control that cannot work.
- Undervoltage was observed during the initial deployment. Check current evidence rather than assuming it is either still present or resolved. Avoid unnecessary reboots, shutdowns, or power interruption during routine validation; describe any remaining physical test limitations accurately.
