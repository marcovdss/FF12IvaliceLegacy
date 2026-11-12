"""Host tests for the game-thread queue, the UT-01 tick, the liveness seqlock, dry-arm mode and the fail-open
detour template. No game run, nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy).

* native (ctypes, the test DLL with the patch layer installed on the image twin, nothing executed in the twin):
  the bounded MPSC queue under four producer threads (no loss, no duplicate, per-producer order), capacity and
  drain budget, every rejection reason (stale epoch, stale generation, expired, disarmed, unknown op, bad result),
  epoch re-read before each job, DRY flush, the tick running the settings safe point and handing the subsystem mask
  to the arm gates (deferred while the layer lock is held), re-entry, the fail-open template;
* unicorn (the hooked twin + the test DLL + MinHook's buffers, imports answered by doubles): the full UT-01 path
  from the site bytes through the retargeted call, the island, our call stub, the dispatcher, the tick and the queue,
  then into the callee: stale or torn liveness -> exactly one stock call and nothing drained; fresh -> the queue drained and
  exactly one stock call; stale epoch/generation jobs rejected without running; and in DRY mode every one of the
  61 detours behaves exactly like the original bytes (registers, flags, XMM, memory outside our module, callees,
  continuation), even with every gate byte forced open and a handler that would wreck the state.
"""
from __future__ import annotations

import ctypes
import random
import struct
import threading
import unittest

import test_coop_patch_engine as pe
from coop_native_support import SCHEMA as S, Bridge
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, Twin, checked, regions

LIVE, DRY = 1, 0
PUSH_OK, PUSH_FULL, PUSH_NOT_LIVE, PUSH_BAD_OP, PUSH_BAD_TTL, PUSH_BAD_EPOCH = range(6)
DONE, PENDING = 1, 0
UNKNOWN_OP, STALE_EPOCH, STALE_GENERATION, EXPIRED, DISARMED, LOST, BAD_RESULT = -1, -2, -3, -4, -5, -6, -7
OP_RECORD, OP_REFUSE, OP_MOVEMENT, OP_BUMP_EPOCH, OP_REENTER, OP_BAD_RESULT = 1, 2, 3, 4, 5, 6
CAPACITY, BUDGET, COMPLETIONS = 64, 16, 128
NOW, EPOCH, GEN = 1_000_000, 7, 5
FAULT_ALL = 0xFFFFFFFF
MOVEMENT = 0x02


class Completion(ctypes.Structure):
    _fields_ = [('ticket', ctypes.c_uint32), ('result', ctypes.c_int32), ('op', ctypes.c_uint16),
                ('issuer', ctypes.c_uint8), ('reserved0', ctypes.c_uint8), ('epoch', ctypes.c_uint32),
                ('finished_ms', ctypes.c_uint64)]


class GtqCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ('pushed', 'push_full', 'push_refused', 'drained', 'ran', 'rejected',
                                               'flushed', 'drain_stopped', 'reaped', 'consumer_busy')]


class TickCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ('ticks', 'reentered', 'settings_applied', 'safe_point_busy',
                                               'mask_applied', 'mask_deferred', 'jobs_drained', 'jobs_flushed',
                                               'last_tick_ms')]


class LivenessView(ctypes.Structure):
    _fields_ = [('armed', ctypes.c_uint32), ('epoch', ctypes.c_uint32), ('heartbeat_ms', ctypes.c_uint64),
                ('now_ms', ctypes.c_uint64), ('coherent', ctypes.c_bool), ('fresh', ctypes.c_bool),
                ('_pad', ctypes.c_uint8 * 6)]


class GtAddresses(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ('log_count', 'log_arg0', 'gtq_stats', 'tick_stats', 'test_slot',
                                               'fixed_now', 'completions')]


def declare(d):
    d.ILCT_PatchSetMode.argtypes = [ctypes.c_uint32]
    d.ILCT_PatchSubsystems.restype = ctypes.c_uint32
    d.ILCT_PatchFault.argtypes = [ctypes.c_uint32]
    d.ILCT_PatchApplySubsystems.argtypes = [ctypes.c_uint32]
    d.ILCT_PatchSeqPoke.argtypes = [ctypes.c_uint32]
    d.ILCT_PatchHoldLock.argtypes = [ctypes.c_int32]
    d.ILCT_PatchHeartbeat.argtypes = [ctypes.c_uint32]
    d.ILCT_SetFixedClock.argtypes = [ctypes.c_uint64]
    d.ILCT_LivenessSample.argtypes = [ctypes.POINTER(LivenessView)]
    d.ILCT_GtqSetupTestOps.argtypes = [ctypes.c_uint32]
    d.ILCT_GtqRegister.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    d.ILCT_GtqPush.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32,
                               ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint32)]
    d.ILCT_GtqPushRaw.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint32,
                                  ctypes.c_uint64, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint32)]
    d.ILCT_GtqResult.argtypes = [ctypes.c_uint32, ctypes.POINTER(Completion)]
    d.ILCT_GtqSetGeneration.argtypes = [ctypes.c_uint64]
    d.ILCT_GtqDrain.restype = ctypes.c_uint32
    d.ILCT_GtqFlush.argtypes = [ctypes.c_int32]
    d.ILCT_GtqStats.argtypes = [ctypes.POINTER(GtqCounters)]
    d.ILCT_LogCount.restype = ctypes.c_uint32
    d.ILCT_LogArg.argtypes = [ctypes.c_uint32]
    d.ILCT_LogArg.restype = ctypes.c_uint64
    d.ILCT_TickRun.argtypes = [ctypes.c_uint32]
    d.ILCT_TickStats.argtypes = [ctypes.POINTER(TickCounters)]
    d.ILCT_RecordPublish.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_uint64]
    d.ILCT_RecordSeqPoke.argtypes = [ctypes.c_uint32]
    d.ILCT_RecordSetHandler.argtypes = [ctypes.c_uint32]
    d.ILCT_RecordInvoke.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64)]
    d.ILCT_PatchSetClobber.argtypes = [ctypes.c_uint32]
    d.ILCT_GtAddresses.argtypes = [ctypes.POINTER(GtAddresses)]
    d.ILCT_RecordSeq.restype = ctypes.c_uint32
    d.ILCT_RecordAbaDrill.argtypes = [ctypes.POINTER(ctypes.c_uint64 * 5)]
    d.ILCT_BigWriter.argtypes = [ctypes.c_uint32]
    d.ILCT_BigReader.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint64), ctypes.POINTER(ctypes.c_uint64)]


class World:
    """The test DLL with the patch layer installed on the twin and a fixed module clock."""

    def __init__(self):
        self.twin = Twin.get()
        self.d = self.twin.dll
        declare(self.d)
        self.addr = GtAddresses()
        self.d.ILCT_GtAddresses(ctypes.byref(self.addr))
        self.ut01 = self.d.ILCT_TickRow()
        self.attached = False

    def setup(self, mode=LIVE, bump_to=EPOCH + 1):
        d = self.d
        self.twin.reset()
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        report = self.twin.call('ILCT_PatchInstall')
        assert report.result == OK, report.result
        d.ILCT_SetFixedClock(NOW)
        assert d.ILCT_TickAttach() == 1
        self.attached = True
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchSetMode(mode)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_GtqSetGeneration(GEN)
        assert d.ILCT_GtqSetupTestOps(bump_to) == 1
        d.ILCT_GtqSeal()

    def push(self, op=OP_RECORD, arg=0, epoch=EPOCH, gen=GEN, ttl=500, issuer=1) -> tuple[int, int]:
        ticket = ctypes.c_uint32()
        status = self.d.ILCT_GtqPush(op, issuer, epoch, gen, ttl, arg, ctypes.byref(ticket))
        return status, ticket.value

    def result(self, ticket: int) -> int:
        return self.d.ILCT_GtqResult(ticket, None)

    def log(self) -> list[int]:
        return [self.d.ILCT_LogArg(k) for k in range(self.d.ILCT_LogCount())]

    def gtq(self) -> GtqCounters:
        c = GtqCounters()
        self.d.ILCT_GtqStats(ctypes.byref(c))
        return c

    def tick(self) -> TickCounters:
        c = TickCounters()
        self.d.ILCT_TickStats(ctypes.byref(c))
        return c


_world = None


def world() -> World:
    global _world
    if _world is None:
        _world = World()
    return _world


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class GameThreadQueueTest(unittest.TestCase):
    def setUp(self):
        self.w = world()
        self.w.setup()
        self.d = self.w.d

    def tearDown(self):
        self.d.ILCT_PatchInject(0, 0)
        self.w.twin.reset()

    def test_fifo_and_one_result_per_ticket(self):
        tickets = [self.w.push(arg=100 + k)[1] for k in range(5)]
        self.assertEqual([self.w.result(t) for t in tickets], [PENDING] * 5)
        self.assertEqual(self.d.ILCT_GtqDrain(), 5)
        self.assertEqual(self.w.log(), [100, 101, 102, 103, 104])
        for t in tickets:
            c = Completion()
            self.assertEqual(self.d.ILCT_GtqResult(t, ctypes.byref(c)), DONE)
            self.assertEqual((c.ticket, c.op, c.issuer, c.epoch), (t, OP_RECORD, 1, EPOCH))
        self.assertEqual(self.d.ILCT_GtqDrain(), 0)
        self.assertEqual(self.w.log(), [100, 101, 102, 103, 104], 'a job ran twice')

    def test_capacity_and_drain_budget(self):
        pushed = [self.w.push(arg=k) for k in range(CAPACITY)]
        self.assertTrue(all(status == PUSH_OK for status, _ in pushed))
        self.assertEqual(self.w.push(arg=999)[0], PUSH_FULL)
        self.assertEqual(self.d.ILCT_GtqDrain(), BUDGET)
        self.assertEqual(len(self.w.log()), BUDGET)
        for k in range(BUDGET):
            self.assertEqual(self.w.push(arg=1000 + k)[0], PUSH_OK)
        self.assertEqual(self.w.push(arg=2000)[0], PUSH_FULL)
        while self.d.ILCT_GtqDrain():
            pass
        self.assertEqual(self.w.log(), list(range(CAPACITY)) + [1000 + k for k in range(BUDGET)])

    def test_push_refusals(self):
        self.assertEqual(self.w.push(op=0)[0], PUSH_BAD_OP)
        self.assertEqual(self.w.push(op=31)[0], PUSH_BAD_OP)
        self.assertEqual(self.w.push(op=99)[0], PUSH_BAD_OP)
        self.assertEqual(self.w.push(ttl=0)[0], PUSH_BAD_TTL)
        self.assertEqual(self.w.push(ttl=2001)[0], PUSH_BAD_TTL)
        self.assertEqual(self.w.push(epoch=0)[0], PUSH_BAD_EPOCH)
        self.assertEqual(self.d.ILCT_GtqRegister(9, 0), 0, 'registered after seal')
        self.d.ILCT_PatchSetMode(DRY)
        self.assertEqual(self.w.push()[0], PUSH_NOT_LIVE)
        self.d.ILCT_PatchSetMode(LIVE)
        self.d.ILCT_PatchSetArmed(0)
        self.assertEqual(self.w.push()[0], PUSH_NOT_LIVE)
        self.d.ILCT_PatchSetArmed(1)
        self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        self.assertEqual(self.w.push(op=OP_MOVEMENT)[0], PUSH_NOT_LIVE)
        self.assertEqual(self.w.push(op=OP_RECORD)[0], PUSH_OK, 'a CORE op does not depend on subsystem bits')
        self.assertEqual(self.w.gtq().push_refused, 9)

    def test_stale_epoch_and_generation_are_rejected_without_running(self):
        good1 = self.w.push(arg=1)[1]
        stale_epoch = self.w.push(arg=2, epoch=EPOCH - 1)[1]
        stale_gen = self.w.push(arg=3, gen=GEN - 1)[1]
        good2 = self.w.push(arg=4)[1]
        self.assertEqual(self.d.ILCT_GtqDrain(), 4)
        self.assertEqual(self.w.log(), [1, 4])
        self.assertEqual([self.w.result(t) for t in (good1, stale_epoch, stale_gen, good2)],
                         [DONE, STALE_EPOCH, STALE_GENERATION, DONE])
        # The generation is read at drain time: a change after the push rejects.
        t = self.w.push(arg=5)[1]
        self.d.ILCT_GtqSetGeneration(GEN + 1)
        self.d.ILCT_GtqDrain()
        self.assertEqual(self.w.result(t), STALE_GENERATION)
        # And the epoch: a heartbeat with a new world rejects jobs made for the old one.
        self.d.ILCT_GtqSetGeneration(GEN)
        t = self.w.push(arg=6)[1]
        self.d.ILCT_PatchHeartbeat(EPOCH + 3)
        self.d.ILCT_GtqDrain()
        self.assertEqual(self.w.result(t), STALE_EPOCH)
        self.assertEqual(self.w.log(), [1, 4])

    def test_epoch_is_re_read_before_each_job(self):
        first = self.w.push(op=OP_BUMP_EPOCH, arg=1)[1]  # its operation moves the world to EPOCH + 1
        second = self.w.push(arg=2)[1]                  # made for EPOCH: must not run in EPOCH + 1
        self.assertEqual(self.d.ILCT_GtqDrain(), 2)
        self.assertEqual((self.w.result(first), self.w.result(second)), (DONE, STALE_EPOCH))
        self.assertEqual(self.w.log(), [1])

    def test_expiry_rejects_instead_of_running_late(self):
        t1 = self.w.push(arg=1, ttl=50)[1]
        t2 = self.w.push(arg=2, ttl=500)[1]
        self.d.ILCT_SetFixedClock(NOW + 51)
        self.d.ILCT_PatchHeartbeat(EPOCH)
        self.d.ILCT_GtqDrain()
        self.assertEqual((self.w.result(t1), self.w.result(t2)), (EXPIRED, DONE))
        self.assertEqual(self.w.log(), [2])

    def test_disarmed_and_faulted_subsystems_reject(self):
        t1 = self.w.push(op=OP_MOVEMENT, arg=1)[1]
        self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        t2 = self.w.push(op=OP_RECORD, arg=2)[1]
        self.d.ILCT_GtqDrain()
        self.assertEqual((self.w.result(t1), self.w.result(t2)), (DISARMED, DONE))
        self.d.ILCT_PatchApplySubsystems(0xFF)
        t3 = self.w.push(op=OP_MOVEMENT, arg=3)[1]
        self.d.ILCT_PatchFault(MOVEMENT)
        self.d.ILCT_GtqDrain()
        self.assertEqual(self.w.result(t3), DISARMED)
        self.assertEqual(self.w.log(), [2])

    def test_unknown_op_is_claimed_and_rejected_once_and_bad_results_are_coerced(self):
        unknown = ctypes.c_uint32()
        self.assertEqual(self.d.ILCT_GtqPushRaw(17, EPOCH, GEN, 500, NOW, 1, ctypes.byref(unknown)), PUSH_OK)
        refused = self.w.push(op=OP_REFUSE)[1]
        bad = self.w.push(op=OP_BAD_RESULT)[1]
        after = self.w.push(arg=9)[1]
        self.assertEqual(self.d.ILCT_GtqDrain(), 4)
        self.assertEqual([self.w.result(t) for t in (unknown.value, refused, bad, after)],
                         [UNKNOWN_OP, -16, BAD_RESULT, DONE])
        self.assertEqual(self.d.ILCT_GtqDrain(), 0)

    def test_drain_stops_on_a_torn_stale_or_disarmed_sample(self):
        t = self.w.push(arg=1)[1]
        self.d.ILCT_PatchSeqPoke(3)  # odd: the writer is "inside"
        self.assertEqual(self.d.ILCT_GtqDrain(), 0)
        self.d.ILCT_PatchSeqPoke(4)
        self.d.ILCT_SetFixedClock(NOW + 2001)  # stale
        self.assertEqual(self.d.ILCT_GtqDrain(), 0)
        self.d.ILCT_SetFixedClock(NOW + 10)
        self.d.ILCT_PatchSetArmed(0)
        self.assertEqual(self.d.ILCT_GtqDrain(), 0)
        self.d.ILCT_PatchSetArmed(1)
        self.assertEqual(self.w.result(t), PENDING, 'a stopped drain must leave the job queued')
        self.assertEqual(self.w.gtq().drain_stopped, 3)
        self.assertEqual(self.d.ILCT_GtqDrain(), 1)
        self.assertEqual(self.w.result(t), DONE)

    def test_lost_result_after_the_table_wraps(self):
        first = self.w.push(arg=0)[1]
        self.d.ILCT_GtqDrain()
        for k in range(COMPLETIONS):
            self.w.push(arg=k + 1)
            self.d.ILCT_GtqDrain()
        self.assertEqual(self.w.result(first), LOST)
        self.assertEqual(self.w.result(first + COMPLETIONS + 5), PENDING, 'a future ticket is pending')

    def test_four_producers_one_consumer(self):
        per, producers = 3000, 4
        stop = threading.Event()
        tickets = [[] for _ in range(producers)]

        def produce(p):
            for i in range(per):
                while True:
                    status, ticket = self.w.push(arg=(p << 32) | i, issuer=p)
                    if status == PUSH_OK:
                        tickets[p].append(ticket)
                        break
                    self.assertEqual(status, PUSH_FULL)

        def consume():
            while not stop.is_set():
                self.d.ILCT_GtqDrain()
            while self.d.ILCT_GtqDrain():
                pass

        consumer = threading.Thread(target=consume)
        consumer.start()
        threads = [threading.Thread(target=produce, args=(p,)) for p in range(producers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stop.set()
        consumer.join()
        log = self.w.log()
        self.assertEqual(len(log), per * producers, 'lost or duplicated jobs')
        self.assertEqual(len(set(log)), per * producers)
        for p in range(producers):
            mine = [v & 0xFFFFFFFF for v in log if v >> 32 == p]
            self.assertEqual(mine, list(range(per)), f'producer {p}: order not kept')
        everything = [t for ts in tickets for t in ts]
        self.assertEqual(len(set(everything)), per * producers, 'a ticket was issued twice')
        self.assertNotIn(0, everything)
        c = self.w.gtq()
        self.assertEqual((c.pushed, c.drained, c.ran, c.rejected), (per * producers,) * 3 + (0,))


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class TickTest(unittest.TestCase):
    generation = 100

    def setUp(self):
        self.w = world()
        self.w.setup()
        self.d = self.w.d
        self.bridge = Bridge(self.d)

    def tearDown(self):
        self.w.twin.reset()

    def stage(self, mask: int) -> int:
        TickTest.generation += 1
        record = bytearray(S.defaults())
        struct.pack_into('<I', record, S.locate('ilc_settings', ['header', 'generation'])[0], TickTest.generation)
        struct.pack_into('<I', record, S.locate('ilc_settings', ['subsystems_armed'])[0], mask)
        status, _ = self.bridge.call(S.e('op', 'SET_SETTINGS'), bytes(record))
        self.assertGreater(status, 0)
        return TickTest.generation

    def test_attached_tick_owns_the_safe_point(self):
        status, _ = self.bridge.call(S.e('op', 'TEST_SAFE_POINT'))
        self.assertEqual(status, S.e('error', 'NOT_AVAILABLE'))

    def test_tick_applies_settings_and_hands_the_mask_to_the_gates(self):
        mask = 0xFF & ~0x08 & ~0x40
        generation = self.stage(mask)
        self.assertEqual(self.d.ILCT_PatchSubsystems(), 0xFF, 'applied before the safe point')
        self.assertEqual(self.d.ILCT_TickRun(EPOCH), 0)  # RUN_ORIGINAL: the stock tick follows
        status, out = self.bridge.call(S.e('op', 'GET_STATUS'))
        st = S.decode('ilc_status', out)
        self.assertEqual((st['applied_generation'], st['applied_subsystems']), (generation, mask))
        self.assertEqual(self.d.ILCT_PatchSubsystems(), mask)
        t = self.w.tick()
        self.assertEqual((t.ticks, t.settings_applied, t.mask_applied, t.mask_deferred), (1, 1, 1, 0))

    def test_mask_is_deferred_while_the_layer_lock_is_held(self):
        self.stage(0x01)
        holder_ready, release = threading.Event(), threading.Event()

        def hold():
            self.d.ILCT_PatchHoldLock(1)
            holder_ready.set()
            release.wait()
            self.d.ILCT_PatchHoldLock(0)

        holder = threading.Thread(target=hold)
        holder.start()
        holder_ready.wait()
        try:
            self.assertEqual(self.d.ILCT_TickRun(EPOCH), 0)  # must not block
            self.assertEqual(self.d.ILCT_PatchSubsystems(), 0xFF)
            self.assertEqual((self.w.tick().mask_applied, self.w.tick().mask_deferred), (0, 1))
        finally:
            release.set()
            holder.join()
        self.d.ILCT_TickRun(EPOCH)
        self.assertEqual(self.d.ILCT_PatchSubsystems(), 0x01)
        self.assertEqual(self.w.tick().mask_applied, 1)

    def test_live_tick_drains_and_dry_tick_flushes(self):
        t1 = self.w.push(arg=11)[1]
        self.d.ILCT_TickRun(EPOCH)
        self.assertEqual((self.w.result(t1), self.w.log()), (DONE, [11]))
        t2 = self.w.push(arg=12)[1]
        self.d.ILCT_PatchSetMode(DRY)
        self.d.ILCT_TickRun(EPOCH)
        self.assertEqual((self.w.result(t2), self.w.log()), (DISARMED, [11]))
        t = self.w.tick()
        self.assertEqual((t.ticks, t.jobs_drained, t.jobs_flushed), (2, 1, 1))

    def test_reentry_runs_stock(self):
        t = self.w.push(op=OP_REENTER, arg=1)[1]
        self.d.ILCT_TickRun(EPOCH)
        self.assertEqual(self.w.result(t), DONE)
        c = self.w.tick()
        self.assertEqual((c.ticks, c.reentered), (1, 1))


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class FailOpenTemplateTest(unittest.TestCase):
    def setUp(self):
        self.w = world()
        self.w.setup()
        self.d = self.w.d
        self.row = self.w.twin.index['FW-01']  # FIREWALL_MENUS
        self.d.ILCT_RecordWithdraw()

    def tearDown(self):
        self.w.twin.reset()

    def invoke(self, epoch=EPOCH) -> tuple[int, int]:
        rax = ctypes.c_uint64(0x1111)
        result = self.d.ILCT_RecordInvoke(self.row, epoch, ctypes.byref(rax))
        return result, rax.value

    def declined(self) -> int:
        a = self.w.twin.addresses
        return struct.unpack('<4Q', ctypes.string_at(a.stats0 + self.row * a.stats_stride, 32))[3]

    def test_template(self):
        self.assertEqual(self.invoke(), (0, 0x1111), 'no record published: stock')
        self.d.ILCT_RecordPublish(EPOCH, GEN, 0xBEEF)
        self.assertEqual(self.invoke(), (0, 0xBEEF), 'fresh record: the body ran')
        self.assertEqual(self.invoke(epoch=EPOCH + 1), (0, 0x1111), 'record for another epoch: stock')
        self.d.ILCT_RecordSeqPoke(7)
        self.assertEqual(self.invoke(), (0, 0x1111), 'torn record: stock')
        self.d.ILCT_RecordPublish(EPOCH, GEN, 0xBEEF)
        self.d.ILCT_PatchApplySubsystems(0xFF & ~0x10)
        self.assertEqual(self.invoke(), (0, 0x1111), 'subsystem bit clear: stock')
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_PatchFault(0x10)
        self.assertEqual(self.invoke(), (0, 0x1111), 'subsystem faulted: stock')
        self.assertEqual(self.declined(), 5)

    def test_withdraw_never_resets_the_sequence(self):
        self.d.ILCT_RecordPublish(EPOCH, GEN, 0xBEEF)
        published = self.d.ILCT_RecordSeq()
        self.d.ILCT_RecordWithdraw()
        withdrawn = self.d.ILCT_RecordSeq()
        self.assertEqual(withdrawn, published + 2, 'withdraw must be a write (seq += 2), never a reset')
        self.assertEqual(self.invoke(), (0, 0x1111), 'withdrawn record: stock')
        self.d.ILCT_RecordPublish(EPOCH, GEN, 0xCAFE)
        self.assertEqual(self.d.ILCT_RecordSeq(), withdrawn + 2)
        self.assertEqual(self.invoke(), (0, 0xCAFE))

    def test_preempted_reader_across_withdraw_and_republish_rejects_its_copy(self):
        out = (ctypes.c_uint64 * 5)()
        self.d.ILCT_RecordAbaDrill(ctypes.byref(out))
        accepted, first, second, copied, fresh = list(out)
        self.assertEqual(accepted, 0, f'a copy made across withdraw + republish was accepted (rax 0x{copied:X})')
        self.assertEqual(second, first + 4)
        self.assertEqual(fresh, 0xBBBB, 'a fresh read must see the republished record')

    def test_concurrent_publish_withdraw_never_yields_a_torn_record(self):
        results = []

        def reader():
            accepted, torn = ctypes.c_uint64(), ctypes.c_uint64()
            self.d.ILCT_BigReader(3_000_000, ctypes.byref(accepted), ctypes.byref(torn))
            results.append((accepted.value, torn.value))

        writer = threading.Thread(target=self.d.ILCT_BigWriter, args=(3_000_000,))
        readers = [threading.Thread(target=reader) for _ in range(2)]
        writer.start()
        for t in readers:
            t.start()
        for t in readers + [writer]:
            t.join()
        self.assertEqual([torn for _, torn in results], [0, 0], f'torn copies accepted: {results}')
        self.assertGreater(sum(a for a, _ in results), 0, 'instrument: no copy was ever accepted')

    def test_dry_mode_declines(self):
        self.d.ILCT_RecordPublish(EPOCH, GEN, 0xBEEF)
        self.d.ILCT_PatchSetMode(DRY)
        self.assertEqual(self.invoke(), (0, 0x1111))

    def test_liveness_seqlock(self):
        v = LivenessView()
        self.assertEqual(self.d.ILCT_LivenessSample(ctypes.byref(v)), 1)
        self.assertEqual((v.armed, v.epoch, v.heartbeat_ms, v.coherent, v.fresh), (1, EPOCH, NOW, True, True))
        self.d.ILCT_PatchSeqPoke(5)
        self.assertEqual(self.d.ILCT_LivenessSample(ctypes.byref(v)), 0)
        self.assertFalse(v.coherent)
        self.d.ILCT_PatchHeartbeat(EPOCH)  # the writer leaves it even again
        self.assertEqual(self.d.ILCT_LivenessSample(ctypes.byref(v)), 1)
        self.d.ILCT_SetFixedClock(NOW + 2001)
        self.assertEqual(self.d.ILCT_LivenessSample(ctypes.byref(v)), 0)
        self.assertTrue(v.coherent)


# ---- unicorn: the hooked UT-01 path and dry-arm equivalence ----------------------------------------------------
TRAP_BASE = 0x40_0000_0000


class TickEmulator(pe.Emulator):
    """The patch-engine emulator plus: module data re-copied from the process on demand, and every import slot of the test
    DLL pointed at a trap that answers with a double (unknown imports are recorded and fail the test)."""

    budget = 400000  # the settings safe point hashes the 3,492-byte defaults once (about 21,000 instructions)

    def __init__(self, twin):
        super().__init__(twin)
        handle = twin.dll._handle
        lfanew = struct.unpack('<I', ctypes.string_at(handle + 0x3C, 4))[0]
        imp_rva = struct.unpack('<I', ctypes.string_at(handle + lfanew + 24 + 112 + 8, 4))[0]
        self.slots = []
        desc = handle + imp_rva
        while True:
            oft, _, _, name_rva, ft = struct.unpack('<5I', ctypes.string_at(desc, 20))
            if name_rva == 0:
                break
            k = 0
            while True:
                entry = struct.unpack('<Q', ctypes.string_at(handle + oft + 8 * k, 8))[0]
                if entry == 0:
                    break
                name = ctypes.string_at(handle + (entry & 0x7FFFFFFF) + 2).decode() if not entry >> 63 else f'#{entry}'
                self.slots.append((handle + ft + 8 * k, name))
                k += 1
            desc += 20
        self.trap = {TRAP_BASE + 16 * k: name for k, (_, name) in enumerate(self.slots)}
        size = (16 * len(self.slots) + pe.PAGE - 1) & ~(pe.PAGE - 1)
        self.uc.mem_map(TRAP_BASE, size)
        self.uc.mem_write(TRAP_BASE, b'\xC3' * size)
        self.uc.hook_add(pe.unicorn.UC_HOOK_CODE, self._trap, begin=TRAP_BASE, end=TRAP_BASE + size - 1)
        self.clock = NOW
        self.imports, self.unexpected = [], []

    def refresh(self):
        lo, hi = self.dll
        for base, length, state, protect, _ in regions(lo, hi):
            if state == 0x1000 and protect & 0xCC:  # committed, writable (RW, WC, RWX, WCX)
                self.uc.mem_write(base, ctypes.string_at(base, min(length, hi - base)))
        for k, (slot, _) in enumerate(self.slots):
            self.uc.mem_write(slot, struct.pack('<Q', TRAP_BASE + 16 * k))
        self.uc.mem_write(self.twin.addresses.clock_slot, struct.pack('<Q', pe.CLOCK_PAGE))
        self.set_clock(self.clock)
        self.imports, self.unexpected = [], []

    def tick_clock(self, value):
        self.clock = value
        self.set_clock(value)

    def _trap(self, uc, address, size, _):
        name = self.trap.get(address)
        if name is None:
            return
        self.imports.append(name)
        X = pe.X
        rcx, rdx, r8 = (uc.reg_read(r) for r in (X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8))
        if name.startswith('TryAcquireSRWLock'):
            uc.reg_write(X.UC_X86_REG_RAX, 1)
        elif name.startswith(('ReleaseSRWLock', 'AcquireSRWLock')):
            pass
        elif name == 'GetTickCount64':
            uc.reg_write(X.UC_X86_REG_RAX, self.clock)
        elif name in ('memcpy', 'memmove'):
            uc.mem_write(rcx, bytes(uc.mem_read(rdx, r8)))
            uc.reg_write(X.UC_X86_REG_RAX, rcx)
        elif name == 'memset':
            uc.mem_write(rcx, bytes([rdx & 0xFF]) * r8)
            uc.reg_write(X.UC_X86_REG_RAX, rcx)
        else:
            self.unexpected.append(name)


def compare(case: unittest.TestCase, emu, label, a, b, baseline, entry_rsp):
    case.assertEqual(a['exit'], b['exit'], f'{label}: continuation')
    case.assertEqual(a['calls'], b['calls'], f'{label}: callees')
    case.assertEqual(a['gpr'], b['gpr'], f'{label}: general registers')
    case.assertEqual(a['xmm'], b['xmm'], f'{label}: XMM registers')
    case.assertEqual(a['rflags'], b['rflags'], f'{label}: flags')
    rsp, (lo, hi), diffs = a['gpr']['RSP'], emu.dll, []
    for address in set(a['final']) | set(b['final']):
        if lo <= address < hi or pe.STACK <= address < rsp:
            continue
        if pe.STACK <= address < entry_rsp and address not in a['final']:
            continue
        if a['final'].get(address, baseline[address]) != b['final'].get(address, baseline[address]):
            diffs.append(hex(address))
    case.assertEqual(diffs, [], f'{label}: memory')


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class Ut01EmulationTest(unittest.TestCase):
    TRIALS = 6

    @classmethod
    def setUpClass(cls):
        cls.w = world()
        cls.w.setup()
        cls.emu = TickEmulator(cls.w.twin)
        cls.row = cls.w.ut01
        cls.site = TWIN_BASE + cls.w.twin.rows[cls.row]['rva']
        cls.stock = TWIN_BASE + 0x170CF0  # the UI manager tick (UT-01's call target)
        cls.cont = cls.site + 5

    @classmethod
    def tearDownClass(cls):
        cls.w.twin.reset()

    def setUp(self):
        self.d = self.w.d
        self.d.ILCT_GtqReset()
        self.d.ILCT_TickReset()
        self.d.ILCT_GtqSetupTestOps(EPOCH + 1)
        self.d.ILCT_GtqSeal()
        self.d.ILCT_PatchSetMode(LIVE)
        self.d.ILCT_SetFixedClock(NOW)
        self.d.ILCT_PatchHeartbeat(EPOCH)
        self.d.ILCT_GtqSetGeneration(GEN)

    def u32(self, address):
        return struct.unpack('<I', self.emu.uc.mem_read(address, 4))[0]

    def u64(self, address):
        return struct.unpack('<Q', self.emu.uc.mem_read(address, 8))[0]

    def emu_result(self, ticket):
        slot = self.w.addr.completions + (ticket & (COMPLETIONS - 1)) * 32
        got_ticket, result = struct.unpack('<Ii', self.emu.uc.mem_read(slot + 8, 8))
        return result if got_ticket == ticket else PENDING

    def run_case(self, clock, setup_uc=None, trials=None, tickets=()):
        """Original bytes vs the hooked UT-01 path from the same states; returns the hooked results."""
        emu, uc, r = self.emu, self.emu.uc, self.w.twin.rows[self.row]
        patched = bytes(uc.mem_read(self.site, 5))
        out = []
        for trial in range(trials or self.TRIALS):
            emu.refresh()
            emu.tick_clock(clock)
            if setup_uc:
                setup_uc(uc)
            state = pe.random_state(random.Random(0x1010 + trial))
            emu.write_code(self.site, checked(r)[:5])
            a = emu.run(self.site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                        self.site <= x < self.site + 5, state)
            emu.write_code(self.site, patched)
            before = emu.stats(self.row)(uc)
            b = emu.run(self.site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                        self.site <= x < self.site + 5, state, probe=lambda u: (
                            emu.stats(self.row)(u), self.u64(self.w.addr.tick_stats), self.u32(self.w.addr.log_count),
                            [self.u64(self.w.addr.log_arg0 + 8 * k) for k in range(self.u32(self.w.addr.log_count))],
                            [self.emu_result(t) for t in tickets]))  # read before run() undoes the writes
            touched = set(a['final']) | set(b['final'])
            baseline = {x: uc.mem_read(x, 1)[0] for x in touched}
            compare(self, emu, f'UT-01 trial {trial}', a, b, baseline, state['gpr']['RSP'])
            self.assertEqual(a['calls'], [self.stock])
            self.assertEqual(b['calls'], [self.stock], 'the stock tick must run exactly once')
            self.assertEqual(b['exit'], self.cont)
            self.assertEqual(emu.unexpected, [], f'unexpected imports: {emu.unexpected}')
            counters, ticks, logged, args, results = b['probe']
            out.append({'delta': tuple(x - y for x, y in zip(counters, before)), 'ticks': ticks, 'logged': logged,
                        'args': args, 'results': results, 'imports': list(emu.imports)})
        return out

    def test_stale_liveness_gives_exactly_one_stock_call_and_drains_nothing(self):
        tickets = [self.w.push(arg=1)[1], self.w.push(arg=2)[1]]
        for res in self.run_case(NOW + 2001, tickets=tickets):
            self.assertEqual(res['delta'][:3], (1, 1, 0))
            self.assertEqual((res['ticks'], res['logged'], res['results']), (0, 0, [PENDING, PENDING]))

    def test_torn_liveness_gives_exactly_one_stock_call(self):
        self.w.push(arg=1)
        live = self.w.twin.addresses.liveness
        for res in self.run_case(NOW + 5, setup_uc=lambda uc: uc.mem_write(live + 16, struct.pack('<I', 9))):
            self.assertEqual(res['delta'][:3], (1, 1, 0))
            self.assertEqual((res['ticks'], res['logged']), (0, 0))

    def test_fresh_liveness_drains_the_queue_then_calls_stock_once(self):
        tickets = [self.w.push(arg=10 + k)[1] for k in range(3)]
        for res in self.run_case(NOW + 5, tickets=tickets):
            self.assertEqual(res['delta'][:3], (1, 0, 1))
            self.assertEqual((res['ticks'], res['logged'], res['args']), (1, 3, [10, 11, 12]))
            self.assertEqual(res['results'], [DONE] * 3)

    def test_stale_epoch_and_generation_jobs_are_rejected_in_the_tick(self):
        good1 = self.w.push(arg=1)[1]
        stale_epoch = self.w.push(arg=2, epoch=EPOCH - 1)[1]
        stale_gen = self.w.push(arg=3, gen=GEN - 1)[1]
        good2 = self.w.push(arg=4)[1]
        for res in self.run_case(NOW + 5, tickets=(good1, stale_epoch, stale_gen, good2)):
            self.assertEqual(res['args'], [1, 4])
            self.assertEqual(res['results'], [DONE, STALE_EPOCH, STALE_GENERATION, DONE])

    def test_dry_tick_runs_no_job_and_behaves_like_stock(self):
        tickets = [self.w.push(arg=k)[1] for k in range(2)]
        self.d.ILCT_PatchSetMode(DRY)
        for res in self.run_case(NOW + 5, tickets=tickets):
            self.assertEqual(res['delta'][:3], (1, 0, 1))  # the tick ran (housekeeping)
            self.assertEqual((res['ticks'], res['logged']), (1, 0), 'a job ran in DRY mode')
            self.assertIn('TryAcquireSRWLockExclusive', res['imports'], 'the safe point did not run')
            self.assertEqual(res['results'], [DISARMED] * 2)


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class DryArmEquivalenceTest(unittest.TestCase):
    TRIALS = 6

    @classmethod
    def setUpClass(cls):
        w = cls.w = world()
        w.setup(mode=LIVE)
        d = w.d
        cls.twin = w.twin
        for row in cls.twin.patch:
            if row != w.ut01:
                d.ILCT_PatchSetClobber(row)
            d.ILCT_PatchSetSiteArmed(row, 1)
        w.push(arg=1)
        d.ILCT_PatchSetMode(DRY)
        gates = {cls.twin.rows[r]['id'] for r in cls.twin.patch if cls.twin.info(r).gate_open}
        if gates != {'UT-01'}:
            raise AssertionError(f'DRY gates: {sorted(gates)}')
        cls.emu = TickEmulator(cls.twin)

    @classmethod
    def tearDownClass(cls):
        cls.twin.reset()

    def test_every_detour_behaves_exactly_like_stock_in_dry_mode(self):
        emu, uc, twin = self.emu, self.emu.uc, self.twin
        passed = 0
        for row in twin.patch:
            r = twin.rows[row]
            site = TWIN_BASE + r['rva']
            window = (site, site + r['hook_len'])
            patched = bytes(uc.mem_read(site, r['hook_len']))
            core = row == self.w.ut01
            ok = True
            for forced in (False, True):
                for trial in range(self.TRIALS):
                    emu.refresh()
                    emu.tick_clock(NOW + 5)
                    if forced:  # every gate byte open, as if a gate lagged the mode change
                        for other in twin.patch:  # call rows: also their entry slot (pe.Emulator.force_gate)
                            emu.force_gate(other, True)
                    state = pe.random_state(random.Random(0xD12 * 100 + row * 10 + trial))
                    emu.write_code(site, checked(r)[:r['hook_len']])
                    a = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                                window[0] <= x < window[1], state)
                    emu.write_code(site, patched)
                    before = emu.stats(row)(uc)
                    b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or
                                site <= x < site + 5, state, probe=emu.stats(row))
                    touched = set(a['final']) | set(b['final'])
                    baseline = {x: uc.mem_read(x, 1)[0] for x in touched}
                    with self.subTest(site=r['id'], forced=forced, trial=trial):
                        try:
                            compare(self, emu, r['id'], a, b, baseline, state['gpr']['RSP'])
                            delta = tuple(x - y for x, y in zip(b['probe'], before))[:3]
                            if core:
                                self.assertEqual(delta, (1, 0, 1))
                            elif forced:
                                self.assertEqual(delta, (1, 1, 0), 'DRY: the dispatcher must send it to stock')
                            else:
                                self.assertEqual(delta, (0, 0, 0), 'DRY: the gate must be closed')
                            self.assertEqual(emu.unexpected, [])
                        except AssertionError:
                            ok = False
                            raise
            passed += ok
        print(f'\ndry-arm equivalence: {passed}/{len(twin.patch)} PATCH rows behave like stock '
              f'({self.TRIALS} states x gates as computed and forced open)')
        self.assertEqual(passed, len(twin.patch))


if __name__ == '__main__':
    unittest.main()
