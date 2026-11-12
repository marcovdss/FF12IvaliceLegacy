"""Host tests: no game-thread job stays PENDING forever (no game run, nothing deployed).

When the game thread is not draining (liveness stale, module disarmed, faulted or dry), a reaper on any other thread
gives each job that can no longer run its terminal result without ever running it, and a requester that waits past
ttl + margin reaps by itself. Drain, flush and reap share one consumer flag; nobody waits for it.
"""
from __future__ import annotations

import ctypes
import unittest

import test_coop_game_thread as gt
from test_coop_game_thread import Completion, GtqCounters
from test_coop_patch_engine import GAME, NATIVE

DONE, PENDING, STALE_EPOCH, EXPIRED, DISARMED, NO_WORLD = 1, 0, -2, -4, -5, -8
PUSH_OK, PUSH_NO_WORLD = 0, 6
OP_ANY_WORLD = 20
OP_RECORD, OP_MOVEMENT = gt.OP_RECORD, gt.OP_MOVEMENT   # the test DLL's CORE and MOVEMENT test operations
LIVE, DRY = 1, 0
MARGIN = 250
T0, EPOCH, GEN = gt.NOW, gt.EPOCH, gt.GEN


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class JobExpiryTest(unittest.TestCase):
    """The patch layer installed on the image twin (as the game-thread queue tests do), LIVE and armed."""

    def setUp(self):
        self.w = gt.world()
        self.w.setup(bump_to=0)
        d = self.d = self.w.d
        d.ILCT_GtqReap.restype = ctypes.c_uint32
        d.ILCT_GtqAwait.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p]
        d.ILCT_GtqAwait.restype = ctypes.c_int32
        d.ILCT_GtqHoldConsumer.argtypes = [ctypes.c_int32]
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_GtqSetWorld.argtypes = [ctypes.c_uint32, ctypes.c_int32]
        d.ILCT_GtqRegisterAnyWorld.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_GtqAnyWorldRuns.restype = ctypes.c_uint32
        d.ILCT_GtqSetWorld(0, 0)
        self.now = T0

    def tearDown(self):
        self.d.ILCT_GtqSetWorld(0, 0)
        self.d.ILCT_GtqHoldConsumer(0)
        self.d.ILCT_PatchFault(0)
        self.w.twin.reset()

    def clock(self, now):
        self.now = now
        self.d.ILCT_SetFixedClock(now)

    def push(self, op=OP_RECORD, ttl=500, epoch=EPOCH):
        ticket = ctypes.c_uint32()
        self.assertEqual(self.d.ILCT_GtqPush(op, 1, epoch, GEN, ttl, 0, ctypes.byref(ticket)), 0)
        return ticket.value, self.now, ttl

    def result(self, ticket):
        return self.d.ILCT_GtqResult(ticket, ctypes.byref(Completion()))

    def wait(self, job):
        ticket, enqueued, ttl = job
        return self.d.ILCT_GtqAwait(ticket, enqueued, ttl, ctypes.byref(Completion()))

    def stats(self):
        c = GtqCounters()
        self.d.ILCT_GtqStats(ctypes.byref(c))
        return c

    def test_a_job_waiting_out_a_stalled_frame_loop_expires(self):
        job = self.push(ttl=500)
        self.clock(T0 + 2500)                       # the co-op heartbeat is stale: the drain stops
        self.assertEqual(self.d.ILCT_GtqDrain(), 0)
        self.assertEqual(self.result(job[0]), PENDING)
        self.assertEqual(self.d.ILCT_GtqReap(), 1)
        self.assertEqual(self.result(job[0]), EXPIRED)
        self.assertEqual(self.d.ILCT_LogCount(), 0, 'a reaped job never runs')
        self.assertEqual(self.stats().reaped, 1)

    def test_disarming_gives_every_queued_job_its_result(self):
        jobs = [self.push(ttl=2000) for _ in range(5)]
        self.d.ILCT_PatchSetArmed(0)
        self.assertEqual(self.d.ILCT_GtqReap(), 5)
        self.assertEqual([self.result(j[0]) for j in jobs], [DISARMED] * 5)

    def test_dry_mode_and_faulted_subsystems_are_disarmed(self):
        a = self.push(op=OP_MOVEMENT, ttl=2000)
        self.d.ILCT_PatchFault(0x02)                # MOVEMENT faulted
        self.assertEqual(self.d.ILCT_GtqReap(), 1)
        self.assertEqual(self.result(a[0]), DISARMED)
        b = self.push(ttl=2000)
        self.d.ILCT_PatchSetMode(DRY)
        self.assertEqual(self.d.ILCT_GtqReap(), 1)
        self.assertEqual(self.result(b[0]), DISARMED)

    def test_a_job_that_can_still_run_is_never_reaped(self):
        job = self.push(ttl=500)
        self.clock(T0 + 100)
        self.assertEqual(self.d.ILCT_GtqReap(), 0)
        self.assertEqual(self.result(job[0]), PENDING)
        self.assertEqual(self.d.ILCT_GtqDrain(), 1)  # the frame loop is back: it runs
        self.assertEqual(self.result(job[0]), DONE)

    def test_fifo_head_holds_the_rest_for_at_most_its_own_ttl(self):
        head = self.push(ttl=2000)
        self.clock(T0 + 10)
        short = self.push(ttl=100)
        self.clock(T0 + 600)
        self.assertEqual(self.d.ILCT_GtqReap(), 0)
        self.assertEqual(self.result(short[0]), PENDING)
        self.clock(T0 + 2001)
        self.assertEqual(self.d.ILCT_GtqReap(), 2)
        self.assertEqual((self.result(head[0]), self.result(short[0])), (EXPIRED, EXPIRED))

    def test_a_requester_reaps_by_itself_after_ttl_plus_margin(self):
        job = self.push(ttl=500)
        self.clock(T0 + 2500)
        self.d.ILCT_GtqDrain()                      # stale: nothing taken
        self.clock(T0 + 500 + MARGIN)
        self.assertEqual(self.wait(job), PENDING)   # not yet past the margin
        self.clock(T0 + 500 + MARGIN + 1)
        self.assertEqual(self.wait(job), EXPIRED)
        self.assertEqual(self.wait(job), EXPIRED, 'a terminal result stays')

    def test_one_consumer_at_a_time_and_nobody_waits(self):
        job = self.push(ttl=500)
        self.assertEqual(self.d.ILCT_GtqHoldConsumer(1), 1)
        self.assertEqual(self.d.ILCT_GtqDrain(), 0, 'the drain ran while another consumer held the queue')
        self.clock(T0 + 3000)
        self.assertEqual(self.d.ILCT_GtqReap(), 0)
        self.assertEqual(self.wait(job), PENDING)
        self.assertGreaterEqual(self.stats().consumer_busy, 3)
        self.d.ILCT_GtqHoldConsumer(0)
        self.assertEqual(self.wait(job), EXPIRED)

    def raw_push(self, op=OP_RECORD, epoch=EPOCH, ttl=500):
        ticket = ctypes.c_uint32()
        status = self.d.ILCT_GtqPush(op, 1, epoch, GEN, ttl, 0, ctypes.byref(ticket))
        return status, ticket.value

    def test_world_requiring_ops_need_a_valid_world_in_their_own_epoch(self):
        self.d.ILCT_GtqSetWorld(EPOCH, 0)               # an invalid stretch (load, title, cutscene)
        self.assertEqual(self.raw_push()[0], PUSH_NO_WORLD)
        self.d.ILCT_GtqSetWorld(EPOCH, 1)
        status, ticket = self.raw_push()
        self.assertEqual(status, PUSH_OK)
        self.d.ILCT_GtqSetWorld(EPOCH, 0)               # the world stopped being valid before the frame ran it
        self.assertEqual(self.d.ILCT_GtqDrain(), 1)
        self.assertEqual(self.result(ticket), NO_WORLD)
        self.assertEqual(self.d.ILCT_LogCount(), 0)
        self.d.ILCT_GtqSetWorld(EPOCH, 1)
        status, ticket = self.raw_push()
        self.assertEqual(self.d.ILCT_GtqDrain(), 1)
        self.assertEqual(self.result(ticket), DONE)
        self.d.ILCT_GtqSetWorld(0, 0)                   # no session core: no restriction
        self.assertEqual(self.raw_push()[0], PUSH_OK)

    def test_reap_retires_jobs_that_can_never_run(self):
        self.d.ILCT_GtqSetWorld(EPOCH, 1)
        _, a = self.raw_push(ttl=2000)
        self.d.ILCT_GtqSetWorld(EPOCH, 0)               # its epoch became an invalid stretch: it can never run
        self.assertEqual(self.d.ILCT_GtqReap(), 1)
        self.assertEqual(self.result(a), NO_WORLD)
        self.d.ILCT_GtqSetWorld(EPOCH, 1)
        _, b = self.raw_push(ttl=2000)
        self.d.ILCT_GtqSetWorld(EPOCH + 1, 1)           # the session moved on
        self.assertEqual(self.d.ILCT_GtqReap(), 1)
        self.assertEqual(self.result(b), STALE_EPOCH)

    def test_ops_registered_for_any_world_run_in_an_invalid_stretch(self):
        d = self.d
        d.ILCT_GtqReset()
        self.assertEqual(d.ILCT_GtqSetupTestOps(0), 1)
        self.assertEqual(d.ILCT_GtqRegisterAnyWorld(OP_ANY_WORLD, 0), 1)
        d.ILCT_GtqSeal()
        d.ILCT_GtqSetGeneration(GEN)
        d.ILCT_GtqSetWorld(EPOCH, 0)
        runs = d.ILCT_GtqAnyWorldRuns()
        status, ticket = self.raw_push(op=OP_ANY_WORLD)
        self.assertEqual(status, PUSH_OK)
        self.assertEqual(self.raw_push()[0], PUSH_NO_WORLD)
        self.assertEqual(d.ILCT_GtqDrain(), 1)
        self.assertEqual(self.result(ticket), DONE)
        self.assertEqual(d.ILCT_GtqAnyWorldRuns(), runs + 1)

    def test_stale_epoch_jobs_are_still_decided_by_the_drain(self):
        job = self.push(ttl=500)
        self.d.ILCT_PatchHeartbeat(EPOCH + 1)       # a boundary: the session moved to a new epoch
        self.assertEqual(self.d.ILCT_GtqDrain(), 1)
        self.assertEqual(self.result(job[0]), STALE_EPOCH)


if __name__ == '__main__':
    unittest.main()
