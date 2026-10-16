#!/usr/bin/env python3
"""1920x480 Claude / Codex quota strip. Pygame UI; stdlib collectors."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
from zoneinfo import ZoneInfo

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
from dataclasses import dataclass
from quota_accounts import ACCOUNTS, selected
from quota_model import Snapshot, Window, WEEK, FIVE_HOURS, countdown, rounded
from quota_state import Reading, State, poll

WIDTH, HEIGHT = 1920, 480
BG = (11, 16, 22)
PANEL = (18, 25, 33)
TRACK = (39, 49, 59)
FG = (231, 238, 242)
MUTED = (142, 158, 170)
BLUE = (120, 205, 239)
PURPLE = (194, 157, 245)
GREEN = (148, 213, 160)
RED = (248, 115, 114)
ORANGE = (238, 171, 103)
YELLOW = (234, 210, 128)


def rate_color(used):
    return RED if used >= 95 else ORANGE if used >= 75 else YELLOW if used >= 50 else GREEN


@dataclass(frozen=True)
class Style:
    """Panel geometry. Offsets are from the panel's left edge or the row's top."""
    pad: int
    logo: int
    title: tuple            # x, y, size
    badge: tuple            # right inset, y, size
    bank: tuple             # x, y of the count; its note sits below at y + bank_gap
    bank_sizes: tuple
    bank_gap: int
    rows: tuple             # first row y and row gap, for up to two and for three rows
    label: tuple            # y, size
    value: tuple            # y, size
    bar: tuple              # y, height
    meta: tuple             # y, size
    label_room: int         # Width kept for the value beside the label.
    waiting: tuple          # title size, detail y, detail size


# Today's two-panel style, and option A's narrower three-panel style.
FULL = Style(30, 44, (86, 36, 30), (24, 44, 19), (330, 38), (19, 15), 25, ((115, 146), (93, 106)),
             (5, 20), (-6, 38), (45, 16), (72, 18), 350, (35, 218, 23))
COMPACT = Style(28, 40, (78, 36, 28), (22, 44, 17), (28, 90), (16, 14), 22, ((150, 110), (150, 86)),
                (3, 17), (-4, 30), (36, 14), (58, 15), 260, (30, 210, 19))
# (x, width) per panel for one to three accounts; two keeps today's geometry.
PANELS = {1: ((20, 1880),), 2: ((20, 928), (972, 928)), 3: ((20, 613), (653, 613), (1286, 614))}


def demo(now, accounts):
    # Synthetic examples only: demo mode never reads credentials or account data.
    from quota_model import parse_claude, parse_codex
    claude = parse_claude({
        "cedar_ember": {"eligible": True, "grants": [
            {"resets_left": 1, "ends_at": now + 20*86400, "clears": ["five_hour", "seven_day"]},
        ]},
        "five_hour": {"utilization": 32, "resets_at": now + 2*3600},
        "seven_day": {"utilization": 24, "resets_at": now + 2*86400 + 15*3600 + 11*60},
        "limits": [{"kind": "weekly_scoped", "percent": 12,
                    "resets_at": now + 2*86400 + 15*3600 + 11*60,
                    "scope": {"model": {"display_name": "Fable"}}}],
    }, now)
    codex = parse_codex({"rateLimitsByLimitId": {
        "codex": {"primary": {"usedPercent": 54, "windowDurationMins": 10080, "resetsAt": now + 2*86400}},
    }, "rateLimitResetCredits": {"availableCount": 2, "credits": [
        {"status": "available", "expiresAt": now + 3*86400},
        {"status": "available", "expiresAt": now + 12*86400},
    ]}}, now)
    claude2 = parse_claude({
        "cedar_ember": {"eligible": True, "grants": []},
        "five_hour": {"utilization": 71, "resets_at": now + 2*3600 + 45*60},
        "seven_day": {"utilization": 48, "resets_at": now + 4*86400 + 2*3600 + 40*60},
        "limits": [{"kind": "weekly_scoped", "percent": 3,
                    "resets_at": now + 4*86400 + 2*3600 + 40*60,
                    "scope": {"model": {"display_name": "Fable"}}}],
    }, now)
    samples = {"claude": claude, "claude2": claude2, "codex": codex}
    return {a.id: Reading(samples[a.id]) for a in accounts}


class Display:
    def __init__(self, surface, zone, stale_after=600):
        import pygame
        self.pg, self.surface, self.zone = pygame, surface, zone
        self.stale_after = stale_after
        self.fonts = {}
        assets = Path(__file__).resolve().parent / "assets"
        self.logos = {
            name: pygame.image.load(str(assets / filename)).convert_alpha()
            for name, filename in (("claude", "claude.png"), ("codex", "chatgpt.png"))
        }
        self.scaled = {}

    def text(self, value, x, y, size=22, color=FG, bold=False, right=False, max_width=None):
        key = (size, bold)
        if key not in self.fonts:
            self.fonts[key] = self.pg.font.SysFont("dejavusans", size, bold=bold)
        font = self.fonts[key]
        value = str(value)
        if max_width is not None:
            while value and font.size(value)[0] > max_width:
                value = value[:-2] + "…" if len(value) > 1 else ""
        image = font.render(value, True, color)
        self.surface.blit(image, image.get_rect(topright=(x, y)) if right else (x, y))

    def logo(self, provider, size):
        if (provider, size) not in self.scaled:
            image = self.logos[provider]
            self.scaled[provider, size] = (image if image.get_width() == size else
                                           self.pg.transform.smoothscale(image, (size, size)))
        return self.scaled[provider, size]

    def bar(self, x, y, width, used, budget=None, unavailable=False, accent=BLUE, segments=1, height=16):
        fill = self.pg.Rect(x, y, int(width * max(0, min(100, used)) / 100), height)
        color = MUTED if unavailable else accent if budget is not None else rate_color(used)
        mark = int(width * budget / 100) if budget is not None else None
        clip = self.surface.get_clip()
        for index in range(segments):
            # Center six-pixel gaps on time boundaries, keeping the percentage
            # scale and daily allowance marker aligned across the whole bar.
            left = round(width * index / segments) + (3 if index else 0)
            right = round(width * (index + 1) / segments) - (3 if index < segments - 1 else 0)
            segment = self.pg.Rect(x + left, y, right - left, height)
            self.pg.draw.rect(self.surface, TRACK, segment, border_radius=4)
            filled = segment.clip(fill)
            if filled.width:
                radius = min(4, filled.width // 2)
                self.pg.draw.rect(self.surface, color, filled, border_radius=radius)
                if mark is not None and not unavailable and used > budget:
                    over_budget = self.pg.Rect(x + mark, y, width - mark, height)
                    self.surface.set_clip(clip.clip(over_budget))
                    self.pg.draw.rect(self.surface, RED, filled, border_radius=radius)
                    self.surface.set_clip(clip)
        if mark is not None and not unavailable:
            self.pg.draw.line(self.surface, RED if used > budget else GREEN,
                              (x + min(width - 1, mark), y - 5), (x + min(width - 1, mark), y + height + 5), 3)

    def window(self, w, x, y, width, now, stale, s=FULL):
        stale = stale or w.stale or (w.observed_at is not None and
                (now - w.observed_at > self.stale_after or w.observed_at > now + 60))
        expired = w.expired(now)
        budget = None if stale else w.budget(now, self.zone)
        unavailable = stale or expired
        accent = PURPLE if w.key == "seven_day_fable" else GREEN if w.seconds == FIVE_HOURS else BLUE
        status = MUTED if unavailable else rate_color(w.used)
        if budget is not None and not unavailable:
            status = GREEN if w.used <= budget else RED
        label = (w.bucket + " / " if w.bucket else "") + w.label
        label_color = PURPLE if w.key == "seven_day_fable" else MUTED
        self.text(label.upper(), x, y + s.label[0], s.label[1], label_color, True,
                  max_width=max(100, width - s.label_room))
        value = f"{rounded(w.used)}%"
        if budget is not None:
            value += f" / {rounded(budget)}%"
        self.text(value, x + width, y + s.value[0], s.value[1], status, True, right=True)
        segments = {WEEK: 7, FIVE_HOURS: 5}.get(w.seconds, 1)
        self.bar(x, y + s.bar[0], width, w.used, budget, unavailable, accent, segments, s.bar[1])
        self.text(countdown(w.resets_at, now), x, y + s.meta[0], s.meta[1], MUTED, max_width=width/2)
        if expired:
            note = "Awaiting fresh quota"
        elif stale:
            note = (f"Last known · {max(0, int((now-w.observed_at)//60))}m ago"
                    if w.observed_at is not None else "Last known usage")
        elif budget is not None:
            delta = budget - w.used
            if w.seconds == FIVE_HOURS:
                note = f"{abs(delta):.0f}% left now" if delta >= 0 else f"{abs(delta):.0f}% over current budget"
            else:
                note = f"{abs(delta):.0f}% left today" if delta >= 0 else f"{abs(delta):.0f}% over today's budget"
        else:
            note = f"{max(0, 100-w.used):.0f}% remaining"
        self.text(note, x + width, y + s.meta[0], s.meta[1], status, right=True, max_width=width/2-10)

    def reset_bank(self, bank, x, y, width, now, stale, noun, s=FULL):
        big, small = s.bank_sizes
        if bank is None:
            self.text(f"{noun}S —", x, y, big, MUTED, True)
            self.text("Count unavailable", x, y + s.bank_gap, small, MUTED)
            return
        old = stale or bank.needs_refresh(now)
        color = MUTED if old or not bank.available_count else GREEN
        clears = " + ".join({"five_hour": "5H", "seven_day": "WEEKLY"}[c] for c in bank.clears)
        self.text(f"{bank.available_count} {noun}" + ("S" if bank.available_count != 1 else "")
                  + (f" · {clears}" if clears and bank.available_count else ""),
                  x, y, big, color, True, max_width=width)
        if old:
            note = "Last known · awaiting update"
        elif bank.available_count == 0:
            note = "None available"
        elif bank.next_expiry is not None:
            expiry = datetime.fromtimestamp(bank.next_expiry, self.zone)
            prefix = "Next expires" if bank.details_complete else "Listed expiry"
            note = f"{prefix} {expiry:%d %b · %H:%M}"
            if bank.next_expiry - now <= 86400:
                color = ORANGE
        else:
            note = "No expiry" if bank.details_complete else "Expiry unavailable"
        self.text(note, x, y + s.bank_gap, small, color, max_width=width)

    def panel(self, account, reading, x, width, now):
        s = FULL if width >= 900 else COMPACT
        inner = width - 2 * s.pad
        self.pg.draw.rect(self.surface, PANEL, (x, 20, width, 414), border_radius=14)
        self.surface.blit(self.logo(account.provider, s.logo), (x + s.pad - 4, 34 - (s.logo - 44) // 2))
        self.text(account.title, x + s.title[0], s.title[1], s.title[2], FG, True)
        self.text(account.badge, x + width - s.badge[0], s.badge[1], s.badge[2], MUTED, right=True)
        snapshot = reading.snapshot
        stale = reading.stale(now, self.stale_after)
        bank_x = x + s.bank[0]
        self.reset_bank(snapshot.reset_bank if snapshot else None, bank_x, s.bank[1],
                        min(360, x + width - 150 - bank_x) if s is FULL else inner, now, stale,
                        "BANKED RESET" if account.provider == "codex" else "LIMIT RESET", s)
        if snapshot is None:
            self.text("Waiting for quota", x + s.pad, 159, s.waiting[0], FG, True)
            self.text(reading.error or "Connecting…", x + s.pad, s.waiting[1], s.waiting[2], MUTED,
                      max_width=inner)
            return
        windows = list(snapshot.windows)
        missing_models = (snapshot.source == "status line" and snapshot.warning and
                          not any(w.key.startswith("seven_day_") for w in windows))
        if not windows:
            self.text("No quota windows reported", x + s.pad, 184, s.waiting[0] - 5, MUTED)
        # Keep the first two windows visible; rotate only overflow in slot three.
        if len(windows) > 3:
            slot = int(now // 15) % (len(windows) - 2)
            windows = windows[:2] + [windows[2 + slot]]
            more_y = 85 if s is FULL else s.bank[1] + s.bank_gap
            self.text(f"More limits {slot+1}/{len(snapshot.windows)-2}", x + width - s.badge[0],
                      more_y, 14, MUTED, right=True)
        slots = len(windows) + bool(missing_models)
        start, gap = s.rows[slots > 2]
        for index, w in enumerate(windows):
            self.window(w, x + s.pad, start + index * gap, inner, now, stale, s)
        if missing_models:
            y = start + len(windows) * gap
            self.text("MODEL WEEKLY LIMITS", x + s.pad, y + s.label[0], s.label[1], MUTED, True)
            self.text("Unavailable until account data returns", x + s.pad, y + s.bar[0],
                      s.meta[1] + 2, MUTED, max_width=inner)
        age = max(0, int((now - snapshot.observed_at) // 60))
        text = f"{'STALE' if stale else 'PARTIAL' if snapshot.warning else 'UPDATED'}  {age}m ago  ·  {snapshot.source}"
        issue = reading.error or snapshot.warning
        if issue:
            text += "  ·  " + issue
        self.text(text, x + s.pad, 407, 14, ORANGE if stale or issue else MUTED, max_width=inner)

    def render(self, accounts, readings, now, sample=False):
        self.surface.fill(BG)
        for account, (x, width) in zip(accounts, PANELS[len(accounts)]):
            self.panel(account, readings.get(account.id, Reading()), x, width, now)
        self.text("QUOTA STRIP", 30, 449, 16, MUTED, True)
        self.text("SAMPLE DATA" if sample else "Markers: weekly by midnight · 5-hour by elapsed time", 210, 449, 16, ORANGE if sample else MUTED)
        local = datetime.fromtimestamp(now, self.zone)
        self.text(f"{self.zone.key}   {local:%a %d %b  %H:%M}", 1890, 445, 21, MUTED, right=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--snapshot", type=Path, help="Render sanitized saved readings; no network")
    parser.add_argument("--screenshot", type=Path, help="Save PNG and exit; no network")
    parser.add_argument("--json", action="store_true", help="One live, normalized quota read; no GUI")
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--source", choices=["local", "standalone"], default="local" if sys.platform == "darwin" else "standalone")
    parser.add_argument("--timezone", default=os.environ.get("QUOTA_TIMEZONE", "America/Sao_Paulo"))
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--at", help="ISO time for deterministic previews")
    parser.add_argument("--accounts", help="Comma-separated accounts in screen order "
                        f"(default: QUOTA_ACCOUNTS or claude,codex); known: {', '.join(ACCOUNTS)}")
    args = parser.parse_args()
    try:
        accounts = selected(args.accounts)
    except ValueError as exc:
        parser.error(str(exc))
    zone = ZoneInfo(args.timezone)
    if args.interval < 60:
        parser.error("Polling interval must be at least 60 seconds")
    if args.at:
        specified = datetime.fromisoformat(args.at)
        if specified.tzinfo is None:
            parser.error("--at requires a timezone offset")
        if not (args.demo or args.snapshot):
            parser.error("--at is only for demo/snapshot previews")
    fixed_now = specified.timestamp() if args.at else None
    now = fixed_now if fixed_now is not None else time.time()
    if args.json:
        from quota_api import Provider, QuotaError
        result, failed = {}, False
        for name in (a.id for a in accounts):
            try:
                from quota_local import local_provider
                provider = local_provider(name) if args.source == "local" else Provider(name)
                result[name] = provider.fetch().to_dict()
            except QuotaError as exc:
                result[name] = exc.fallback.to_dict() if exc.fallback else {"error": str(exc)}
                failed = True
        print(json.dumps(result, indent=2))
        return int(failed)
    import pygame
    if args.screenshot:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.display.init()
    pygame.font.init()
    windowed = args.windowed or args.screenshot or sys.platform == "darwin"
    screen = pygame.display.set_mode((WIDTH, HEIGHT), 0 if windowed else pygame.FULLSCREEN)
    pygame.display.set_caption("Quota Strip")
    pygame.mouse.set_visible(bool(windowed))
    display = Display(screen, zone, max(600, args.interval * 3))
    stop = threading.Event()
    states = {}
    saved = None
    if args.snapshot:
        payload = json.loads(args.snapshot.read_text())
        saved = {name: Reading(Snapshot.from_dict(value)) for name, value in payload.items()}
    elif args.demo:
        saved = demo(now, accounts)
    elif not args.screenshot:
        for name in (a.id for a in accounts):
            states[name] = State(name)
            threading.Thread(target=poll, args=(states[name], stop, args.interval, None, args.source), daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    last = None
    clock = pygame.time.Clock()
    try:
        while not stop.is_set():
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q)):
                    stop.set()
            now = fixed_now if fixed_now is not None else time.time()
            readings = saved if saved is not None else {n: s.get() for n, s in states.items()}
            signature = (int(now // 15), tuple(readings.items()))
            if signature != last:
                display.render(accounts, readings, now, args.demo)
                pygame.display.flip()
                last = signature
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                pygame.image.save(screen, str(args.screenshot))
                break
            clock.tick(4)
    finally:
        stop.set()
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
