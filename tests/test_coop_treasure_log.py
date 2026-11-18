"""Host tests for chests to the combat log without a pause (native/coop/src/interaction/treasure_log.*: TL-01, TL-02).
No game run, nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin.

* the rows: TL-01 (the notice's 6-byte presentation store) and TL-02 (the treasure begin call) are optional MOVEMENT
  PATCH rows with the pinned stock bytes, TL-01 hooked whole, TL-02 a call row on 0x269090; TL-03 stays INFO;
* the patch bytes: TL-01's handler stores 1 where the stock code stores 0x10000 (Together's c7 06 01 00 00 00) and
  resumes after the store; TL-02 writes the treasure record as 0x328640 would ({1, actor, chest, data}, pending 0),
  leaves the result word alone and skips the call;
* fail closed: TL-02 stays stock with a stock open in flight, no actor, an actor whose entity is gone, outside a world
  or in another epoch; with MOVEMENT disarmed or co-op off (the patch layer's liveness) both rows give the stock modal
  back; a row another mod rewrote arms neither (all or nothing) and the one-time hook.log line says why.
"""
from __future__ import annotations

import ctypes
import struct
import unittest

import test_coop_frontend as tfe
import test_coop_game_thread as gt
import test_coop_hud as th
import test_coop_overlay as tov
import test_coop_patch_engine as pe
from coop_native_support import native_test, quiet_faulthandler
from test_coop_patch_engine import GAME

u8, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER

PINNED = 0x120000
TL01_SITE, TL02_SITE, TL03_SITE = 0x50FB3A, 0x35588D, 0x50FBA8
FN_BEGIN = 0x269090
RECORD = 0x208CCF8
PENDING, RESULT = RECORD + 0x20, RECORD + 0x24
RUN_ORIGINAL, RESUME = 0, 1
MOVEMENT = 0x02
MODAL, COMBAT_LOG = 0x10000, 1


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


RecordImage = S('RecordImage', [('active', u64), ('actor', u64), ('chest', u64), ('data', u64), ('pending', u32),
                                ('pad', u32)])
TlStatus = S('TlStatus', [('attached', u8), ('presentation', u8), ('begin', u8), ('logged', u8)])
TlCounters = S('TlCounters', [(n, u64) for n in ('log_notices', 'no_pause_opens', 'stock_in_flight', 'stock_other',
                                                 'sounds', 'sounds_retried')])
SOUND_CHEST = 0x27
EXIT_TAIL, EXIT_RETURN = 0, 1

SIG = {
    'ILCT_TlReset': ([], None), 'ILCT_TlAttach': ([], i32), 'ILCT_TlStatus': ([P(TlStatus)], None),
    'ILCT_TlStats': ([P(TlCounters)], None), 'ILCT_TlCall01': ([ctypes.c_void_p, u32], i32),
    'ILCT_TlCall02': ([ctypes.c_void_p, u32], i32), 'ILCT_TlRecordFor': ([u64, u64, u64, P(RecordImage)], None),
    'ILCT_TlRows': ([P(i32)], None), 'ILCT_TlStep': ([u64], None), 'ILCT_TlDispatch01': ([u64, P(i32)], u64),
    'ILCT_TlDispatch02': ([u64, u64, u64, P(i32)], u64), 'ILCT_TlSoundDue': ([], u32),
}


def declare(d):
    for name, (args, res) in SIG.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


# ---- the rows -------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
class SiteRowsTest(unittest.TestCase):
    def test_the_two_rows_and_the_info_row(self):
        rows = {r['id']: r for r in pe.site_rows()}
        a, b, c = rows['TL-01'], rows['TL-02'], rows['TL-03']
        self.assertEqual((a['kind'], a['policy'], a['subsystem'], a['flags']), ('IMM', 'PATCH', 'MOVEMENT', 0x08))
        self.assertEqual((a['rva'], a['expected'], a['hook_len']), (TL01_SITE - PINNED, bytes.fromhex('c7 06 00 00 01 00'), 6))
        self.assertEqual((b['kind'], b['policy'], b['subsystem'], b['flags']), ('CALL', 'PATCH', 'MOVEMENT', 0x08))
        self.assertEqual((b['rva'], b['expected']), (TL02_SITE - PINNED, bytes.fromhex('e8 fe 37 f1 ff')))
        self.assertEqual(pe.call_offset(b), 0, 'a call row (an island)')
        self.assertEqual(TL02_SITE + 5 + struct.unpack('<i', b['expected'][1:5])[0], FN_BEGIN)
        self.assertEqual((c['policy'], c['rva']), ('INFO', TL03_SITE - PINNED), 'TL-03 is never written')

    def test_the_stored_mode_is_togethers_combat_log_presentation(self):
        stock, together = bytes.fromhex('c7 06 00 00 01 00'), bytes.fromhex('c7 06 01 00 00 00')
        self.assertEqual(stock[:2], together[:2], 'the same mov dword [rsi], imm32')
        self.assertEqual(struct.unpack('<I', stock[2:])[0], MODAL)
        self.assertEqual(struct.unpack('<I', together[2:])[0], COMBAT_LOG)


@native_test
class RecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(tov.fresh_test_dll())

    def test_the_record_as_0x328640_writes_it(self):
        r = RecordImage()
        self.d.ILCT_TlRecordFor(0x1111, 0x2222, 0x3333, ctypes.byref(r))
        raw = bytes(r)[:0x24]
        self.assertEqual(raw, struct.pack('<QQQQI', 1, 0x1111, 0x2222, 0x3333, 0),
                         '{1, actor, chest, data} then pending 0; the result word after it is not part of the write')


# ---- the handlers on the twin ---------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(tov.declare(tfe.declare(cls.hw.d)))
        rows = (i32 * 3)()
        cls.d.ILCT_TlRows(rows)
        cls.rows = list(rows)

    def setUp(self):
        self.hw.setup(attach=False)
        d = self.d
        d.ILCT_TlReset()
        self.assertEqual(d.ILCT_TlAttach(), 1, 'both rows hooked by the install: armed')
        self.publish()
        self.twin.poke(PENDING, struct.pack('<I', 0))
        self.twin.poke(RESULT, struct.pack('<i', -1))
        self.actor = ctypes.create_string_buffer(0x100)
        self.entity = ctypes.create_string_buffer(0x100)
        struct.pack_into('<Q', self.actor, 0x30, ctypes.addressof(self.entity))   # 0x263e30: the actor's entity

    def tearDown(self):
        self.d.ILCT_OvLogCapture(0)
        self.d.ILCT_TlReset()
        self.hw.twin.reset()

    @property
    def twin(self):
        return self.hw.twin

    def publish(self, epoch=gt.EPOCH, world=True):
        rec = tfe.Record()
        rec.head.epoch, rec.head.generation = epoch, 1
        rec.head.flags = tfe.GATE['WORLD'] if world else 0
        rec.facts, rec.phase = tfe.FACT['CONTROL'], tfe.PHASE['FIELD']
        self.d.ILCT_TownSessionPublish(ctypes.byref(rec))

    def stats(self) -> TlCounters:
        c = TlCounters()
        self.d.ILCT_TlStats(ctypes.byref(c))
        return c

    def status(self) -> TlStatus:
        s = TlStatus()
        self.d.ILCT_TlStatus(ctypes.byref(s))
        return s

    def record(self) -> bytes:
        return ctypes.string_at(RECORD, 0x28)

    def begin(self, actor=None, chest=0xC0FFEE, data=0xDA7A, epoch=gt.EPOCH):
        c = th.HookCtx()
        c.rcx = ctypes.addressof(self.actor) if actor is None else actor
        c.rdx, c.r8 = chest, data
        c.index = self.rows[1]
        with quiet_faulthandler():
            r = self.d.ILCT_TlCall02(ctypes.byref(c), epoch)
        return r, c

    def test_tl01_stores_the_combat_log_presentation_and_resumes_after_the_store(self):
        notice = ctypes.create_string_buffer(struct.pack('<I', 0xDEADBEEF), 8)
        c = th.HookCtx()
        c.rsi, c.index = ctypes.addressof(notice), self.rows[0]
        self.assertEqual(self.d.ILCT_TlCall01(ctypes.byref(c), gt.EPOCH), RESUME)
        self.assertEqual(struct.unpack_from('<I', notice, 0)[0], COMBAT_LOG, 'mode 1: the reward is a log line')
        self.assertEqual(c.resume, TL01_SITE + 6, 'the 6-byte stock store is not run')
        self.assertEqual(self.stats().log_notices, 1)

    def test_tl02_writes_the_record_with_pending_0_and_skips_the_call(self):
        before = self.record()
        r, c = self.begin()
        self.assertEqual((r, c.resume), (RESUME, TL02_SITE + 5), 'the begin call is skipped (no open animation)')
        self.assertEqual(self.record()[:0x24],
                         struct.pack('<QQQQI', 1, ctypes.addressof(self.actor), 0xC0FFEE, 0xDA7A, 0))
        self.assertEqual(ctypes.string_at(RESULT, 4), struct.pack('<i', -1), 'the result word is left alone: the end '
                         'step releases nothing')
        self.assertNotEqual(before, self.record())
        self.assertEqual(self.stats().no_pause_opens, 1)

    def test_tl02_leaves_the_begin_call_stock(self):
        cases = {
            'a stock open in flight (pending 1)': (lambda: self.twin.poke(PENDING, struct.pack('<I', 1)), {}, 'stock_in_flight'),
            'no actor': (lambda: None, {'actor': 0}, 'stock_other'),
            'the actor\'s entity is gone': (lambda: struct.pack_into('<Q', self.actor, 0x30, 0), {}, 'stock_other'),
            'no valid world': (lambda: self.publish(world=False), {}, 'stock_other'),
            'another epoch': (lambda: None, {'epoch': gt.EPOCH + 1}, 'stock_other'),
        }
        for name, (prepare, kw, counter) in cases.items():
            with self.subTest(name):
                self.setUp()
                prepare()
                before = self.record()
                r, _ = self.begin(**kw)
                self.assertEqual(r, RUN_ORIGINAL, name)
                self.assertEqual(self.record(), before, 'nothing written')
                self.assertEqual(getattr(self.stats(), counter), 1)
                self.tearDown()

    def test_movement_disarmed_gives_the_stock_modal_back(self):
        d = self.d
        d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        notice = ctypes.create_string_buffer(struct.pack('<I', 0xDEADBEEF), 8)
        c = th.HookCtx()
        c.rsi, c.index = ctypes.addressof(notice), self.rows[0]
        self.assertEqual(d.ILCT_TlCall01(ctypes.byref(c), gt.EPOCH), RUN_ORIGINAL)
        self.assertEqual(struct.unpack_from('<I', notice, 0)[0], 0xDEADBEEF, 'the stock store runs (modal)')
        before = self.record()
        self.assertEqual(self.begin()[0], RUN_ORIGINAL, 'the stock begin call runs (the open animation)')
        self.assertEqual(self.record(), before)

    def test_through_the_patch_layer_co_op_off_is_stock(self):
        d = self.d
        notice = ctypes.create_string_buffer(struct.pack('<I', 0xDEADBEEF), 8)
        stock = i32()
        nxt = d.ILCT_TlDispatch01(ctypes.addressof(notice), ctypes.byref(stock))
        self.assertEqual((stock.value, nxt), (0, TL01_SITE + 6), 'live: the handler resumes after the store')
        self.assertEqual(struct.unpack_from('<I', notice, 0)[0], COMBAT_LOG)
        for name, off in (('co-op off', lambda: d.ILCT_PatchSetArmed(0)),
                          ('MOVEMENT disarmed', lambda: d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT))):
            with self.subTest(name):
                self.setUp()
                off()
                struct.pack_into('<I', notice, 0, 0xDEADBEEF)
                d.ILCT_TlDispatch01(ctypes.addressof(notice), ctypes.byref(stock))
                self.assertEqual(stock.value, 1, 'the trampoline: the stock modal store')
                self.assertEqual(struct.unpack_from('<I', notice, 0)[0], 0xDEADBEEF)
                self.tearDown()

    def test_tl02_through_the_call_island_skips_the_call(self):
        mode = i32()
        nxt = self.d.ILCT_TlDispatch02(ctypes.addressof(self.actor), 0xC0FFEE, 0xDA7A, ctypes.byref(mode))
        self.assertEqual((mode.value, nxt), (EXIT_RETURN, TL02_SITE + 5), 'RESUME -> the stub pops and continues')
        self.assertEqual(self.record()[:0x24], struct.pack('<QQQQI', 1, ctypes.addressof(self.actor), 0xC0FFEE,
                                                           0xDA7A, 0))
        self.d.ILCT_PatchSetArmed(0)
        nxt = self.d.ILCT_TlDispatch02(ctypes.addressof(self.actor), 0xC0FFEE, 0xDA7A, ctypes.byref(mode))
        self.assertEqual((mode.value, nxt), (EXIT_TAIL, FN_BEGIN), 'co-op off: the stock begin call')

    def test_a_chest_taken_in_log_mode_queues_the_chest_sound(self):
        """Together's TL-03 wrapper played UI sound 0x27 on every chest: ours queues it for the game thread."""
        d = self.d
        self.assertEqual(self.begin()[0], RESUME)
        self.assertEqual(d.ILCT_TlSoundDue(), 1)
        d.ILCT_TlStep(gt.NOW)
        self.assertEqual((d.ILCT_TlSoundDue(), self.stats().sounds), (0, 1), 'queued through submit::play_system')
        self.assertEqual(self.begin()[0], RESUME)
        d.ILCT_TlStep(gt.NOW + 20)
        self.assertEqual(self.stats().sounds + self.stats().sounds_retried, 2,
                         'the second waits while the first holds the sound slot, or is queued')

    def test_at_most_two_chest_sounds_wait(self):
        """Review 16: a burst of chests queues at most two sounds (never a long tail)."""
        for _ in range(5):
            self.assertEqual(self.begin()[0], RESUME)
        self.assertEqual(self.d.ILCT_TlSoundDue(), 2)

    def test_a_stock_begin_call_plays_no_extra_sound(self):
        self.twin.poke(PENDING, struct.pack('<I', 1))
        self.assertEqual(self.begin()[0], RUN_ORIGINAL)
        self.assertEqual(self.d.ILCT_TlSoundDue(), 0, 'only chests taken in log mode')

    def test_the_line_names_the_real_reason_when_attach_never_ran(self):
        """interaction::attach failed, so treasure_log::attach never ran: the line must not blame another mod."""
        d = self.d
        d.ILCT_TlReset()
        d.ILCT_OvLogCapture(1)
        d.ILCT_TlStep(gt.NOW)
        log = d.ILCT_OvLogText().decode()
        self.assertIn('the interaction rows did not attach', log)
        self.assertNotIn('another mod', log)

    def test_the_one_time_line_when_on(self):
        d = self.d
        d.ILCT_OvLogCapture(1)
        d.ILCT_TlStep(gt.NOW)
        d.ILCT_TlStep(gt.NOW + 20)
        log = d.ILCT_OvLogText().decode()
        self.assertEqual(log.count('treasure log: on'), 1, log)


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class AllOrNothingTest(unittest.TestCase):
    """A row another mod rewrote is skipped by the install (OPTIONAL); then neither row is armed and chests stay stock."""

    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(tov.declare(cls.hw.d))
        rows = (i32 * 3)()
        cls.d.ILCT_TlRows(rows)
        cls.rows = list(rows)

    def tearDown(self):
        self.d.ILCT_OvLogCapture(0)
        self.d.ILCT_TlReset()
        self.hw.twin.reset()

    def test_either_row_rewritten_arms_neither(self):
        d = self.d
        for site, name, other in ((TL01_SITE, 'TL-01', 'TL-02 hooked'), (TL02_SITE + 1, 'TL-02', 'TL-01 hooked')):
            with self.subTest(name):
                self.hw.twin.reset()
                self.hw.twin.poke(site, b'\x90')                   # another mod's byte in the window
                report = self.hw.twin.call('ILCT_PatchInstall')
                self.assertEqual(report.result, pe.OK, 'an optional row that differs is skipped, not refused')
                d.ILCT_SetFixedClock(gt.NOW)
                d.ILCT_PatchSetArmed(1)
                d.ILCT_PatchApplySubsystems(0xFF)
                d.ILCT_PatchSetMode(gt.LIVE)
                d.ILCT_PatchHeartbeat(gt.EPOCH)
                d.ILCT_TlReset()
                self.assertEqual(d.ILCT_TlAttach(), 0)
                s = TlStatus()
                d.ILCT_TlStatus(ctypes.byref(s))
                self.assertEqual(s.attached, 0)
                self.assertEqual((s.presentation, s.begin), (0, 1) if name == 'TL-01' else (1, 0))
                d.ILCT_OvLogCapture(1)
                d.ILCT_TlStep(gt.NOW)
                log = d.ILCT_OvLogText().decode()
                self.assertIn('treasure log: off, chests stay stock', log)
                self.assertIn(f'{name} not hooked', log)
                self.assertIn(other, log)
                d.ILCT_OvLogCapture(0)
                notice = ctypes.create_string_buffer(struct.pack('<I', 0xDEADBEEF), 8)
                c = th.HookCtx()
                c.rsi, c.index = ctypes.addressof(notice), self.rows[0]
                self.assertEqual(d.ILCT_TlCall01(ctypes.byref(c), gt.EPOCH), RUN_ORIGINAL, 'not armed: stock')


if __name__ == '__main__':
    unittest.main()
