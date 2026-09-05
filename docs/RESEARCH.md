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

Standalone mode keeps appliance-owned credentials separate from local CLI stores, serializes renewal, and saves tokens atomically. Real standalone login and refresh remain unverified. The Mac prototype uses existing Claude credentials and the installed Codex app-server.

## Validation boundaries

Live macOS reads have been verified. Unit tests cover quotas, pacing, storage, staleness, and mocked authentication. A headless demo checks the native drawing path without using account data.

Original Raspberry Pi B+ deployment, display timing, OS installation, unattended startup, and real standalone token rotation have not been validated. [Raspberry Pi operating systems](https://www.raspberrypi.com/software/operating-systems/) is the starting point for that later phase.

## Banked reset metadata

- [Official app-server account contract](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md): `account/rateLimits/read` includes optional `rateLimitResetCredits`, authoritative `availableCount`, and potentially capped `credits` rows. The installed CLI's generated JSON Schema was also checked. `expiresAt: null` means no expiry; a missing detail list means only the count is known.
- [How banked Codex resets work](https://help.openai.com/en/articles/20001498-how-banked-codex-resets-work): saved resets can expire and are distinct from purchased usage credits and automatic resets.
- [Claude Max limits](https://support.claude.com/en/articles/11049741-what-is-the-max-plan) and [Claude usage credits](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans): scheduled allowances and pay-as-you-go usage. As of 2026-09-05, the reviewed documentation and live Claude usage schema did not expose an equivalent saved-reset bank. This is a verification boundary, not a guarantee about every account or future rollout.

The dashboard reads metadata only; it does not implement reset redemption. Ordinary usage remains available when optional bank metadata is missing or malformed.

## Partial Claude readings

Claude Code's documented status-line payload reports only the main five-hour and weekly windows. A successful fallback therefore cannot replace a complete account snapshot or clear an account error. The collector retains missing windows with their original observation times, marks them stale, and retries the account endpoint with exponential backoff while reading the local capture at the normal interval. A complete account response replaces the partial state and may remove quotas that are no longer reported.
