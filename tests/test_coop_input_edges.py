"""Host tests for the per-consumer edge cursors at the input seam (no game run, nothing deployed).

The input service ticks at 125 Hz and its snapshot keeps each tick's edges for that tick only. These tests check
that every consumer with its own cursor sees each press exactly once however slowly it reads: through the history
directly, from several real threads at different paces, and end to end through the manual input service.
"""
from __future__ import annotations

import ctypes
import unittest

from coop_session_support import EdgeCursor, EdgeDelta, native_test, session_dll, u32
from test_coop_input_service import Service, seat_xinput

SEATS = 4


class EdgeCase(unittest.TestCase):
    def setUp(self):
        self.d = session_dll()
        self.d.ILCT_EdgeTestReset()
        self.seq = 0

    def tick(self, seat=None, pad=(0, 0, 0), act=(0, 0, 0, 0)):
        masks = (u32 * (SEATS * 7))()
        if seat is not None:
            base = (seat - 1) * 7
            masks[base:base + 3] = pad
            masks[base + 3:base + 7] = act
        self.seq += 1
        self.d.ILCT_EdgeRecord(self.seq, masks)

    def since(self, cursor, seat=2):
        d = EdgeDelta()
        ok = self.d.ILCT_EdgeSince(ctypes.byref(cursor), seat, ctypes.byref(d))
        return d if ok else None


@native_test
class EdgeCursorTest(EdgeCase):
    def test_nothing_recorded_yet(self):
        self.assertIsNone(self.since(EdgeCursor()))
        self.d.ILCT_EdgeReset(7)
        self.assertIsNone(self.since(EdgeCursor()))

    def test_a_fresh_cursor_seeds_without_replaying_history(self):
        self.d.ILCT_EdgeReset(7)
        self.tick(2, pad=(0x1, 0, 0x1), act=(0x4, 0, 0, 0x4))
        cur = EdgeCursor()
        d = self.since(cur)
        self.assertEqual((d.reseeded, d.ticks, d.pad_pressed, d.act_pressed), (1, 0, 0, 0))
        self.assertEqual((d.pad_held, d.act_held, d.to_seq), (0x1, 0x4, 1))
        d = self.since(cur)
        self.assertEqual((d.reseeded, d.ticks), (0, 0))

    def test_a_tap_between_two_reads_is_seen_once(self):
        self.d.ILCT_EdgeReset(7)
        self.tick()
        cur = EdgeCursor()
        self.since(cur)
        self.tick(2, pad=(0x10, 0, 0x10), act=(0x2, 0, 0, 0x2))   # pressed
        self.tick(2, pad=(0, 0x10, 0), act=(0, 0x2, 0, 0))        # released on the next tick
        self.tick()
        d = self.since(cur)
        self.assertEqual((d.ticks, d.from_seq, d.to_seq), (3, 2, 4))
        self.assertEqual((d.pad_pressed, d.pad_released, d.act_pressed, d.act_released), (0x10, 0x10, 0x2, 0x2))
        self.assertEqual((d.pad_held, d.act_held), (0, 0))
        d = self.since(cur)
        self.assertEqual((d.ticks, d.pad_pressed, d.act_pressed), (0, 0, 0))

    def test_consumers_never_affect_each_other(self):
        self.d.ILCT_EdgeReset(7)
        self.tick()
        fast, slow = EdgeCursor(), EdgeCursor()
        self.since(fast)
        self.since(slow)
        seen_fast = seen_slow = 0
        for t in range(40):
            if t % 10 == 3:
                self.tick(2, act=(0x1, 0, 0, 0x1))
            elif t % 10 == 4:
                self.tick(2, act=(0, 0x1, 0, 0))
            else:
                self.tick()
            seen_fast += self.since(fast).act_pressed & 1
            if t % 7 == 6:
                seen_slow += self.since(slow).act_pressed & 1
        seen_slow += self.since(slow).act_pressed & 1
        self.assertEqual((seen_fast, seen_slow), (4, 4))

    def test_seats_are_separate(self):
        self.d.ILCT_EdgeReset(7)
        self.tick()
        cur = EdgeCursor()
        for seat in (1, 2, 3):
            self.since(cur, seat)
        self.tick(3, act=(0x8, 0, 0, 0x8))
        self.assertEqual(self.since(cur, 2).act_pressed, 0)
        self.assertEqual(self.since(cur, 3).act_pressed, 0x8)
        self.assertEqual(self.since(cur, 1).act_pressed, 0)
        self.assertIsNone(self.since(cur, 0))
        self.assertIsNone(self.since(cur, SEATS + 1))

    def test_repeat_pulses_are_ored_too(self):
        self.d.ILCT_EdgeReset(7)
        self.tick()
        cur = EdgeCursor()
        self.since(cur)
        self.tick(2, act=(0, 0, 0x20, 0x20))
        self.tick(2, act=(0, 0, 0, 0x20))
        d = self.since(cur)
        self.assertEqual((d.act_repeated, d.act_held), (0x20, 0x20))

    def test_a_cursor_that_fell_too_far_behind_is_told_so(self):
        self.d.ILCT_EdgeReset(7)
        self.tick()
        cur = EdgeCursor()
        self.since(cur)
        self.tick(2, act=(0x1, 0, 0, 0x1))      # this press falls out of the history
        for _ in range(300):
            self.tick()
        self.tick(2, act=(0x2, 0, 0, 0x2))      # this one is still held
        d = self.since(cur)
        self.assertEqual((d.overrun, d.ticks, d.act_pressed), (1, 256, 0x2))
        d = self.since(cur)
        self.assertEqual((d.overrun, d.ticks), (0, 0))

    def test_a_new_service_run_reseeds_every_cursor(self):
        self.d.ILCT_EdgeReset(7)
        self.tick()
        cur = EdgeCursor()
        self.since(cur)
        self.tick(2, act=(0x1, 0, 0, 0x1))
        self.d.ILCT_EdgeReset(8)
        self.seq = 0
        self.tick(2, act=(0x4, 0, 0, 0x4))
        d = self.since(cur)
        self.assertEqual((d.reseeded, d.act_pressed, d.act_held, d.to_seq), (1, 0, 0x4, 1))
        self.tick(2, act=(0x8, 0, 0, 0xC))
        self.assertEqual(self.since(cur).act_pressed, 0x8)

    def test_gaps_in_tick_numbers_are_not_losses(self):
        self.d.ILCT_EdgeReset(7)
        self.tick()
        cur = EdgeCursor()
        self.since(cur)
        self.seq += 5
        self.tick(2, act=(0x1, 0, 0, 0x1))
        d = self.since(cur)
        self.assertEqual((d.overrun, d.ticks, d.act_pressed), (0, 1, 0x1))


@native_test
class EdgeThreadsTest(EdgeCase):
    def stress(self, readers, ticks=6000, period=9, hold=3, pause_every=40):
        sleeps = (u32 * len(readers))(*readers)
        out = (u32 * (6 * len(readers)))()
        presses = self.d.ILCT_EdgeStress(2, ticks, period, hold, len(readers), sleeps, pause_every, out)
        rows = [dict(zip(('press_deltas', 'mismatches', 'gaps', 'overruns', 'reads', 'covered'),
                         out[6 * i:6 * i + 6])) for i in range(len(readers))]
        return presses, rows

    def test_readers_at_different_paces_lose_nothing(self):
        # A spinning reader, readers that nap every few reads, and a writer that pauses now and then.
        presses, rows = self.stress([0, 3, 50, 1])
        self.assertGreater(presses, 600)
        for i, r in enumerate(rows):
            with self.subTest(reader=i, row=r):
                self.assertEqual((r['mismatches'], r['gaps']), (0, 0))
                self.assertGreater(r['press_deltas'], 0)
                self.assertGreaterEqual(r['covered'] + 256 * r['overruns'], 6000 - 1)
        # Presses closer together than a reader's (preempted) pace arrive ORed in one delta, never dropped: the
        # spinning reader still sees most of them separately.
        self.assertGreater(rows[0]['press_deltas'], presses // 2)


@native_test
class EdgeServiceTest(unittest.TestCase):
    """End to end through the manual input service: a tap shorter than the consumer's read interval."""

    def test_a_one_tick_tap_reaches_a_reader_that_reads_every_seventh_tick(self):
        s = Service(session_dll())
        try:
            s.begin()
            record = s.settings()
            seat_xinput(record, 2, 1)
            s.apply(record)
            s.xinput(1)
            t = 0.0
            s.step(t)
            cur = EdgeCursor()
            d = EdgeDelta()
            self.assertEqual(s.dll.ILCT_EdgeSince(ctypes.byref(cur), 2, ctypes.byref(d)), 1)
            self.assertEqual(d.reseeded, 1)
            taps = seen = 0
            for k in range(1, 43):
                t += 8
                tap = k % 10 == 3          # A down for exactly one tick
                taps += tap
                s.xinput(1, buttons=('A',) if tap else (), packet=k + 1)
                s.step(t)
                if k % 7 == 0:
                    frame = s.frame(2)
                    self.assertEqual(frame.pressed, 0, 'the snapshot alone shows the tap only on its own tick')
                    self.assertEqual(s.dll.ILCT_EdgeSince(ctypes.byref(cur), 2, ctypes.byref(d)), 1)
                    self.assertEqual(d.ticks, 7)
                    self.assertEqual(bool(d.pad_pressed), bool(d.pad_released), 'press and release travel together')
                    seen += 1 if d.pad_pressed else 0
            self.assertEqual((taps, seen), (4, 4))
        finally:
            s.end()


if __name__ == '__main__':
    unittest.main()
