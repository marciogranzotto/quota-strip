import os
import unittest
from zoneinfo import ZoneInfo

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
except ImportError:  # The renderer is optional; collectors and sensors need only the stdlib.
    pygame = None

from quota_model import ResetBank, ResetKind

DEC_10 = 1796940000.0   # 2026-12-10T22:00:00Z
MAR_09 = 1804579140.0   # 2027-03-09T07:59:00Z
NOW = 1794585600.0      # 2026-11-13T18:00:00Z


@unittest.skipIf(pygame is None, "pygame is not installed")
class ResetHeaderTests(unittest.TestCase):
    """The reset header names each kind of Claude reset, as Claude Code does."""

    def setUp(self):
        from quota_display import COMPACT, MUTED, PURPLE, Display
        self.style, self.muted, self.purple = COMPACT, MUTED, PURPLE
        pygame.init()
        self.addCleanup(pygame.quit)
        self.display = Display(pygame.display.set_mode((1920, 480)), ZoneInfo("UTC"))
        self.drawn = []
        draw = self.display.text

        def record(value, x, y, *args, **kwargs):
            width = draw(value, x, y, *args, **kwargs)
            color = args[1] if len(args) > 1 else kwargs.get("color")
            self.drawn.append((str(value), x, y, color))
            return width
        self.display.text = record

    def header(self, bank, width=557, stale=False, noun="LIMIT RESET"):
        self.drawn.clear()
        self.display.reset_bank(bank, 28, 90, width, NOW, stale, noun, self.style)
        top = [d for d in self.drawn if d[2] == 90]
        notes = [d[0] for d in self.drawn if d[2] != 90]
        return top, notes

    def test_weekly_and_fable_resets_are_counted_separately(self):
        bank = ResetBank(3, DEC_10, True, (ResetKind("weekly", 1, DEC_10), ResetKind("fable", 2, MAR_09)))
        top, notes = self.header(bank)
        self.assertEqual("".join(t for t, *_ in top), "1 WEEKLY · 2 FABLE RESETS")
        self.assertEqual([t for t, *_ in top], ["1 WEEKLY", " · ", "2 FABLE", " RESETS"])
        self.assertEqual([c for t, _, _, c in top if t == "2 FABLE"], [self.purple])
        xs = [x for _, x, _, _ in top]
        self.assertEqual(xs, sorted(set(xs)))  # Each part starts where the previous one ended.
        self.assertEqual(notes, ["Weekly 10 Dec 22:00 · Fable 09 Mar 07:59"])

    def test_one_kind_reads_like_a_single_bank(self):
        top, notes = self.header(ResetBank(1, DEC_10, True, (ResetKind("weekly", 1, DEC_10),)))
        self.assertEqual("".join(t for t, *_ in top), "1 WEEKLY RESET")
        self.assertEqual(notes, ["Next expires 10 Dec · 22:00"])

    def test_narrow_header_falls_back_to_the_total(self):
        bank = ResetBank(3, DEC_10, True, (ResetKind("weekly", 1, DEC_10), ResetKind("fable", 2, MAR_09)))
        top, notes = self.header(bank, width=120)
        self.assertEqual([t for t, *_ in top], ["3 LIMIT RESETS"])
        self.assertEqual(notes, ["Next expires 10 Dec · 22:00"])

    def test_unknown_split_and_stale_readings(self):
        top, _ = self.header(ResetBank(2, DEC_10, True), noun="BANKED RESET")
        self.assertEqual([t for t, *_ in top], ["2 BANKED RESETS"])
        bank = ResetBank(3, DEC_10, True, (ResetKind("weekly", 1, DEC_10), ResetKind("fable", 2, MAR_09)))
        top, notes = self.header(bank, stale=True)
        self.assertTrue(all(c == self.muted for *_, c in top))
        self.assertEqual(notes, ["Last known · awaiting update"])
        top, notes = self.header(ResetBank(0, None, True))
        self.assertEqual(([t for t, *_ in top], notes), (["0 LIMIT RESETS"], ["None available"]))


if __name__ == "__main__":
    unittest.main()
