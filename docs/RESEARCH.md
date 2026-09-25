# Integration research

These references informed the implementation. Consumer account contracts may change; links are implementation references, not guarantees of third-party API support.

## Starting point

- [fuziontech/claude-quota-display](https://github.com/fuziontech/claude-quota-display): MIT Python/Pygame appliance and macOS development support. The original Pi installer targeted a Pi 3B+ and a different display layout.
- [CodexBar: Claude](https://github.com/steipete/CodexBar/blob/main/docs/claude.md) and [CodexBar: Codex](https://github.com/steipete/CodexBar/blob/main/docs/codex.md): account quota strategies, distinct from local token-cost estimates.
- [fuelcheck Codex provider](https://github.com/emanuelarcos/fuelcheck/blob/main/internal/providers/codex.go): direct account endpoint reference. Window duration must come from the response; the primary window is not necessarily five hours.

## Collection contracts

- [Codex app-server](https://learn.chatgpt.com/docs/app-server): initialization, `account/rateLimits/read`, multiple rate-limit buckets, and reported window durations.
- [Claude Code status lines](https://code.claude.com/docs/en/statusline): per-window utilization and reset fields; absent windows must not be interpreted as zero.
- [claude-fable-usage](https://github.com/T0mSIlver/claude-fable-usage/blob/main/statusline.py): model quotas in `limits[]`, with `kind: weekly_scoped`, `percent`, `resets_at`, and `scope.model.display_name`. Read these from the existing account request rather than making a separate Fable request.
- [Claude Fable models on your plan](https://support.claude.com/en/articles/15424964-claude-fable-models-on-your-plan): Fable's sublimit is part of the overall weekly allowance. Display the reported percentage directly; do not derive it from overall usage or multiply it.

## Standard-library and rendering choices

- [Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html): IANA timezone and daylight-saving handling for the next local calendar midnight.
- [Python urllib.request](https://docs.python.org/3/library/urllib.request.html): HTTPS requests. The collector rejects redirects to avoid forwarding bearer credentials.
- [Pygame display](https://www.pygame.org/docs/ref/display.html): native software surfaces, fullscreen operation, and headless rendering. Network workers are separate from rendering.

The weekly guide uses a fixed seven-day window ending at the reported reset. Its target is the elapsed fraction at the next local midnight, rounded half up and clamped to 0–100. It is a pacing convention, not a provider enforcement rule.

## Experimental standalone authentication

- [Codex device authentication](https://github.com/openai/codex/blob/main/codex-rs/login/src/device_code_auth.rs) and [login server](https://github.com/openai/codex/blob/main/codex-rs/login/src/server.rs): user-code flow, pending statuses, callback, and token exchange.
- [fuelcheck authentication](https://github.com/emanuelarcos/fuelcheck/blob/main/internal/auth/codex.go): existing consumer client ID and refresh contract.
- [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636): PKCE S256; the tests include its published verifier/challenge vector.

Standalone mode keeps appliance-owned credentials separate from local CLI stores, serializes renewal, and saves tokens atomically. Real standalone login, quota reads, and token refresh have been verified for both providers on macOS. Local mode remains available using existing Claude credentials and the installed Codex app-server.

## Validation boundaries

Live macOS and standalone Pi reads have been verified. Unit tests cover quotas, pacing, storage, staleness, and mocked authentication. A headless demo checks the native drawing path without using account data; CI also validates the installer scripts and systemd units.

The application is deployed on a Raspberry Pi 3 Model B v1.2 using Raspberry Pi OS Lite 64-bit (Trixie). Its panel's native 480 × 1920 mode runs at 60 Hz and is rotated into a physically verified 1920 × 480 display. App crash recovery and graphical session shutdown were tested on the hardware. An unresolved undervoltage issue leaves cold-boot recovery, physical network recovery, and sustained unattended operation unverified. See the [hardware validation record](RASPBERRY_PI.md#hardware-validation-record-2026-09-05) and [Raspberry Pi operating systems](https://www.raspberrypi.com/software/operating-systems/).

## Banked reset metadata

- [Official app-server account contract](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md): `account/rateLimits/read` includes optional `rateLimitResetCredits`, authoritative `availableCount`, and potentially capped `credits` rows. The installed CLI's generated JSON Schema was also checked. `expiresAt: null` means no expiry; a missing detail list means only the count is known.
- [How banked Codex resets work](https://help.openai.com/en/articles/20001498-how-banked-codex-resets-work): saved resets can expire and are distinct from purchased usage credits and automatic resets.
- [Claude Max limits](https://support.claude.com/en/articles/11049741-what-is-the-max-plan) and [Claude usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans): scheduled allowances and pay-as-you-go usage, which are distinct from limit-reset grants. On 2026-09-05 the reviewed documentation and plain usage schema exposed no Claude reset bank; that finding is superseded below.

### Claude limit-reset grants (2026-09-25)

Read from Claude Code 2.1.282's bundled client and confirmed with live read-only requests on 2026-09-25:

- `GET /api/oauth/usage?cedar_ember=1&skip_spend=1` adds a `cedar_ember` status block: `eligible`, `ineligible_reason`, `at_limit`, `exhausted`, `grants[]`, `next_grant_id`, `weekly_resets_at`, `cooldown_until`. Each grant has `id`, `label`, `resets_total`, `resets_left`, `starts_at`, `ends_at`, `clears` (meter keys such as `five_hour`, `seven_day`, `seven_day_overage_included`), `paused`, `usable_now`, `use_requires_limit`, `percent_used`, and `blocking`. The plain query returns `cedar_ember: null`.
- The server evaluates grants only for Claude Code's CLI surface. With a non-CLI user agent it returned `eligible: false, ineligible_reason: "surface"`; with `claude-cli/1.0.0 (external, cli)` it returned `cli_version`. `x-app: cli` alone had no effect. The client's reason enum is `config_off`, `tier`, `seat`, `mobile`, `surface`, `cli_version`, `no_grant`, `tenure`, `other_experiment`, `unavailable`, `unknown`. Quota Strip treats `surface`, `cli_version`, `mobile`, `unavailable`, `unknown`, and unrecognized reasons as an unknown count, and the rest as zero.
- The test account had one grant, "one usage-limit reset for Pro and Max" from the Claude Opus 5.5 launch, valid 2026-09-22 to 2026-10-22, clearing the five-hour and weekly meters.
- `?at_wall=1&skip_spend=1` adds `juniper_tide`, a separate once-a-week session-limit reset offered only at a limit (`not_at_wall` otherwise). It is not a bank and is not displayed.
- Claims are `POST /api/organizations/{org}/reset_rate_limits`. Quota Strip never calls it.
- Several rapid diagnostic requests produced HTTP 429 on the ordinary usage read, so the grant read is limited to every 10 minutes.

### Codex Spark (2026-09-25)

The account responses no longer report a Spark limit: `wham/usage` returned `additional_rate_limits: null`, and the app-server's `rateLimitsByLimitId` contained only `codex`. On 2026-09-05 the same account reported `GPT-5.3-Codex-Spark` five-hour and weekly windows. The parser still recognizes Spark if it returns. The usage response also gained `rate_limit_reset_credits.applicable_available_count` (0 while `available_count` was 1); its meaning is unverified and it is not used.

The dashboard reads metadata only; it does not implement reset redemption. Ordinary usage remains available when optional bank metadata is missing or malformed.

## Partial Claude readings

### Additive weekly metadata (2026-09-15)

A live request on the Pi returned valid `five_hour`, `seven_day`, and
`limits[].weekly_scoped` meters alongside a new `seven_day_breakdown` object.
The old parser treated every key starting with `seven_day` as a quota and
rejected the entire response because this metadata had no `utilization`.
Legacy windows are now selected by explicit field name, consistent with the
[CodexBar field mapping](https://github.com/steipete/CodexBar/blob/main/docs/claude.md).
Unknown metadata is ignored; recognized meters still require valid percentages.
Model names remain dynamically supported through `limits[].weekly_scoped`.
Regression tests cover metadata alongside valid meters, metadata-only responses,
malformed known meters, new scoped models, and successful snapshot publication.

### Local status-line fallback

Claude Code's documented status-line payload reports only the main five-hour and weekly windows. A successful fallback therefore cannot replace a complete account snapshot or clear an account error. The collector retains missing windows with their original observation times, marks them stale, and retries the account endpoint with exponential backoff while reading the local capture at the normal interval. A complete account response replaces the partial state and may remove quotas that are no longer reported.

## Standalone boot and callback

- [RFC 8252](https://www.rfc-editor.org/rfc/rfc8252): native OAuth loopback callbacks with PKCE. The listener binds IPv4 loopback, validates state and callback host/path, accepts one result, and suppresses request logs.
- [Python HTTP server](https://docs.python.org/3/library/http.server.html): `ThreadingHTTPServer` handles browser pre-opened sockets without blocking the callback.
- [Codex backend reset client](https://github.com/openai/codex/blob/main/codex-rs/backend-client/src/client/rate_limit_resets.rs) and [types](https://github.com/openai/codex/blob/main/codex-rs/backend-client/src/types.rs): HTTP usage includes `rate_limit_reset_credits.available_count`; GET `/wham/rate-limit-reset-credits` provides expiry details in snake_case with ISO timestamps. These fields are normalized without retaining identifiers. No redemption operation is implemented.
- [LightDM configuration](https://github.com/canonical/lightdm/blob/main/data/lightdm.conf): a dedicated auto-login X session supplies display authorization.
- [systemctl](https://manpages.debian.org/trixie/systemd/systemctl.1.en.html), [unit dependencies](https://github.com/systemd/systemd/blob/main/man/systemd.unit.xml), and [service restart policy](https://manpages.debian.org/trixie/systemd/systemd.service.5.en.html): import only display environment variables and wait on a persistent session target. The target `Wants=` the app; the app is `PartOf=` the target. An app failure does not end the target, while stopping the target stops the app. Waiting directly on the app service caused a real crash test to end the X session and return to LightDM's greeter.
- [Xorg output configuration](https://gitlab.freedesktop.org/xorg/xserver/-/blob/master/hw/xfree86/modes/xf86Crtc.c): without an explicit monitor mapping, the output name selects the monitor section. Matching the section's `Identifier` to `HDMI-1` made the native mode and `Rotate` setting apply at X startup.
- [Imager customization formats](https://github.com/raspberrypi/rpi-imager/blob/main/doc/os_customisation_formats.md): current Trixie customization needs Imager 2.x and cloud-init. A successful image verification alone does not prove the login and network configuration was applied.

The Pi installer uses distribution-provided Pygame and Xorg packages. The Mac remains independent from the Pi's operation; each device should have a separate provider login when both run simultaneously.
