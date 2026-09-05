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
from quota_model import Snapshot, Window, WEEK, countdown, rounded
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


def demo(now):
    # Synthetic examples only: demo mode never reads credentials or account data.
    from quota_model import parse_claude, parse_codex
    claude = parse_claude({
        "five_hour": {"utilization": 32, "resets_at": now + 2*3600},
        "seven_day": {"utilization": 24, "resets_at": now + 2*86400 + 15*3600 + 11*60},
        "limits": [{"kind": "weekly_scoped", "percent": 12,
                    "resets_at": now + 2*86400 + 15*3600 + 11*60,
                    "scope": {"model": {"display_name": "Fable"}}}],
    }, now)
    codex = parse_codex({"rateLimitsByLimitId": {
        "codex": {"primary": {"usedPercent": 54, "windowDurationMins": 10080, "resetsAt": now + 2*86400}},
        "spark": {"limitName": "Spark", "primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": now + 12000},
                  "secondary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": now + 5*86400}},
    }, "rateLimitResetCredits": {"availableCount": 2, "credits": [
        {"status": "available", "expiresAt": now + 3*86400},
        {"status": "available", "expiresAt": now + 12*86400},
    ]}}, now)
    return {"claude": Reading(claude), "codex": Reading(codex)}


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

    def bar(self, x, y, width, used, budget=None, unavailable=False, accent=BLUE):
        self.pg.draw.rect(self.surface, TRACK, (x, y, width, 16), border_radius=4)
        fill = int(width * max(0, min(100, used)) / 100)
        color = MUTED if unavailable else accent if budget is not None else rate_color(used)
        if fill:
            self.pg.draw.rect(self.surface, color, (x, y, fill, 16), border_radius=min(4, fill // 2))
        if budget is not None and not unavailable:
            mark = int(width * budget / 100)
            if used > budget and fill > mark:
                self.pg.draw.rect(self.surface, RED, (x + mark, y, fill - mark, 16))
            self.pg.draw.line(self.surface, RED if used > budget else GREEN,
                              (x + min(width - 1, mark), y - 5), (x + min(width - 1, mark), y + 21), 3)

    def window(self, w, x, y, width, now, stale):
        stale = stale or w.stale or (w.observed_at is not None and
                (now - w.observed_at > self.stale_after or w.observed_at > now + 60))
        expired = w.expired(now)
        budget = None if stale else w.budget(now, self.zone)
        unavailable = stale or expired
        accent = PURPLE if w.key == "seven_day_fable" else BLUE
        status = MUTED if unavailable else rate_color(w.used)
        if budget is not None and not unavailable:
            status = GREEN if w.used <= budget else RED
        label = (w.bucket + " / " if w.bucket else "") + w.label
        label_color = PURPLE if w.key == "seven_day_fable" else MUTED
        self.text(label.upper(), x, y + 5, 20, label_color, True, max_width=width-350)
        value = f"{rounded(w.used)}%"
        if budget is not None:
            value += f" / {budget}%"
        self.text(value, x + width, y - 6, 38, status, True, right=True)
        self.bar(x, y + 45, width, w.used, budget, unavailable, accent)
        self.text(countdown(w.resets_at, now), x, y + 72, 18, MUTED, max_width=width/2)
        if expired:
            note = "Awaiting fresh quota"
        elif stale:
            note = (f"Last known · {max(0, int((now-w.observed_at)//60))}m ago"
                    if w.observed_at is not None else "Last known usage")
        elif budget is not None:
            delta = budget - w.used
            note = f"{abs(delta):.0f}% left today" if delta >= 0 else f"{abs(delta):.0f} pp over today's budget"
        else:
            note = f"{max(0, 100-w.used):.0f}% remaining"
        self.text(note, x + width, y + 72, 18, status, right=True, max_width=width/2-10)

    def reset_bank(self, bank, x, now, stale):
        if bank is None:
            self.text("BANKED RESETS —", x, 38, 19, MUTED, True)
            self.text("Count unavailable", x, 63, 15, MUTED)
            return
        old = stale or bank.needs_refresh(now)
        color = MUTED if old or not bank.available_count else GREEN
        self.text(f"{bank.available_count} BANKED RESET" + ("S" if bank.available_count != 1 else ""),
                  x, 38, 19, color, True, max_width=360)
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
        self.text(note, x, 63, 15, color, max_width=360)

    def panel(self, name, reading, x, now):
        self.pg.draw.rect(self.surface, PANEL, (x, 20, 928, 414), border_radius=14)
        self.surface.blit(self.logos[name], (x + 26, 34))
        self.text("CLAUDE" if name == "claude" else "CODEX", x + 86, 36, 30, FG, True)
        self.text("MAX 20×" if name == "claude" else "CHATGPT PRO", x + 904, 44, 19, MUTED, right=True)
        snapshot = reading.snapshot
        stale = reading.stale(now, self.stale_after)
        if name == "codex":
            self.reset_bank(snapshot.reset_bank if snapshot else None, x + 330, now, stale)
        if snapshot is None:
            self.text("Waiting for quota", x + 30, 159, 35, FG, True)
            self.text(reading.error or "Connecting…", x + 30, 218, 23, MUTED, max_width=865)
            return
        windows = list(snapshot.windows)
        missing_models = (snapshot.source == "status line" and snapshot.warning and
                          not any(w.key.startswith("seven_day_") for w in windows))
        if not windows:
            self.text("No quota windows reported", x + 30, 184, 30, MUTED)
        # Keep the first two windows visible; rotate only overflow in slot three.
        if len(windows) > 3:
            slot = int(now // 15) % (len(windows) - 2)
            windows = windows[:2] + [windows[2 + slot]]
            self.text(f"More limits {slot+1}/{len(snapshot.windows)-2}", x + 904, 85, 14, MUTED, right=True)
        slots = len(windows) + bool(missing_models)
        gap = 146 if slots <= 2 else 106
        start = 115 if slots <= 2 else 93
        for index, w in enumerate(windows):
            self.window(w, x + 30, start + index * gap, 868, now, stale)
        if missing_models:
            y = start + len(windows) * gap
            self.text("MODEL WEEKLY LIMITS", x + 30, y + 5, 20, MUTED, True)
            self.text("Unavailable until account data returns", x + 30, y + 50, 20, MUTED)
        age = max(0, int((now - snapshot.observed_at) // 60))
        text = f"{'STALE' if stale else 'PARTIAL' if snapshot.warning else 'UPDATED'}  {age}m ago  ·  {snapshot.source}"
        issue = reading.error or snapshot.warning
        if issue:
            text += "  ·  " + issue
        self.text(text, x + 30, 407, 14, ORANGE if stale or issue else MUTED, max_width=868)

    def render(self, readings, now, sample=False):
        self.surface.fill(BG)
        self.panel("claude", readings.get("claude", Reading()), 20, now)
        self.panel("codex", readings.get("codex", Reading()), 972, now)
        self.text("QUOTA STRIP", 30, 449, 16, MUTED, True)
        self.text("SAMPLE DATA" if sample else "Weekly marker = allowance by local midnight", 210, 449, 16, ORANGE if sample else MUTED)
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
    args = parser.parse_args()
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
        for name in ("claude", "codex"):
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
        saved = demo(now)
    elif not args.screenshot:
        for name in ("claude", "codex"):
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
                display.render(readings, now, args.demo)
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
