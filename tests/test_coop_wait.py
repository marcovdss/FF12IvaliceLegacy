"""Host tests for the Wait engine (native/coop/src/wait). No game run, nothing deployed; the exe is only read
(IVALICE_LEGACY_GAME, a copy) for the image twin and the unicorn classes.

* the rule: the installed reference's 216-case Wait matrix (three seats, modes CLOSED/LIST/TARGET, participating or
  not) restated, then the same rule over four seats with a benched role (9^4 cases): a pause only when at least one
  seat participates and every participating seat's menu is open; benched and absent seats never count;
* the frontend id -> pause reason table, restated from the decoded jump table and checked against the stock function
  0x255290 itself, run in unicorn for every id in both battle modes;
* the game-thread decision: named cases (acquire, borrow, revalidate and re-acquire, hand over, release, another
  frontend's reason, Active mode, not in play, the WAIT bit off) and invariants over every input combination;
* the engine on a synthetic image with doubles of the four game functions: acquire/aggregate, release/lock clear/
  aggregate in that order, the request's epoch and age (stale = released), Active never pauses and a reason taken in
  Wait is released with the lock clear after a switch to Active, hand-over to a stock reason-0 frontend, borrowing
  a stock-held reason, revalidation after a stock release, the kill switch (WAIT off) and a non-play game mode;
  attach fails closed on foreign bytes;
* unicorn on the hooked twin: the UT-01 tick calls 0x226BE0 with ECX/EDX/R8D/R9D = 0 and both stack arguments -1 on
  a Win64-aligned stack, then 0x226280, then the stock UI tick; a release calls 0x225CD0(0, -1), 0x182DA0, 0x226280;
  hand-over and borrow call nothing but the stock tick.
"""
from __future__ import annotations

import ctypes
import itertools
import random
import re
import struct
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_patch_engine as pe
from coop_native_support import native_test
from coop_session_support import Image
from test_coop_movement import MoveEmulator
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, untagged_constants

u8, i8, u16, u32, u64, i32 = (ctypes.c_uint8, ctypes.c_int8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64,
                              ctypes.c_int32)
P = ctypes.POINTER
ROOT = Path(__file__).resolve().parents[1]
WAIT_SRC = ROOT / 'native' / 'coop' / 'src' / 'wait'
B32_PROPOSAL_IDS = ('WT-V1', 'WT-V2', 'WT-V3', 'WT-V4', 'WT-V5')


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


SeatView = S('SeatView', [('role', u8), ('open', u8)])
EngineIn = S('EngineIn', [('owned', u8), ('readable', u8), ('held', u8), ('requested', u8), ('live', u8),
                          ('active_mode', u8), ('in_play', u8), ('frontend_on', u8), ('frontend_reason', i8),
                          ('reserved', u8 * 3)])
EngineOut = S('EngineOut', [('action', u8), ('owned', u8), ('wanted', u8), ('revalidated', u8), ('borrowed', u8),
                            ('reserved', u8 * 3)])
RecordHead = S('RecordHead', [('epoch', u32), ('flags', u32), ('generation', u64)])
RequestRecord = S('RequestRecord', [('head', RecordHead), ('published_ms', u64)])
COUNTERS = ('steps', 'acquired', 'released', 'handed_over', 'borrowed', 'revalidated', 'unreadable', 'stale',
            'dry_owned', 'prologue_mismatch', 'published', 'stale_released')
Counters = S('Counters', [(n, u64) for n in COUNTERS])
F_ACQUIRE = ctypes.CFUNCTYPE(None, i32, i32, i32, i32, i32, i32)
F_RELEASE = ctypes.CFUNCTYPE(None, i32, i32)
F_VOID = ctypes.CFUNCTYPE(None)
GameFns = S('GameFns', [('acquire', F_ACQUIRE), ('release', F_RELEASE), ('aggregate', F_VOID),
                        ('clear_lock', F_VOID)])
LAYOUTS = [SeatView, EngineIn, EngineOut, RequestRecord, Counters, GameFns]

ABSENT, PARTICIPANT, BENCHED = 0, 1, 2
CLOSED, LIST, TARGET = 0, 1, 2
ACT_NONE, ACT_ACQUIRE, ACT_RELEASE, ACT_HANDOVER = 0, 1, 2, 3
FRESH_MS = 100
WAIT_BIT = 0x20

# Game facts (VAs, pinned base 0x120000).
FN_ACQUIRE, FN_RELEASE, FN_AGGREGATE, FN_CLEAR_LOCK, FN_REASON_OF = 0x226BE0, 0x225CD0, 0x226280, 0x182DA0, 0x255290
FN_ACTIVE_MODE = 0x2DDC30
REASON_OF_CODE = 0x6C  # 0x255290..0x2552FC: the code; the dword and byte jump tables follow it
RETURN_TO = TWIN_BASE + 0x1000  # a mapped address outside the function: the emulation stops when it returns there
REASONS_HELD, SETTINGS_PTR, FRONTEND_ID, FRONTEND_FLAG, GAME_MODE = 0x1FD46D4, 0x215E6E0, 0x2092730, 0x2092734, 0x2064AD3
UI_TICK = 0x290CF0
EPOCH, GEN, NOW = gt.EPOCH, gt.GEN, gt.NOW

# The decoded jump table of 0x255290 (frontend id -> pause reason, -1 none); ids 1 and 4 depend on the mode.
REASONS = {0x02: 0xD, 0x03: 2, 0x06: 0xD, 0x0F: 0xD, 0x11: 0xD, 0x12: 0xD, 0x13: 0xD, 0x14: 0, 0x16: 0xD, 0x18: 2}


def reason(id_, active):
    if id_ == 1:
        return -1 if active else 0
    if id_ == 4:
        return 0 if active else -1
    return REASONS.get(id_, -1)


SIGNATURES = {
    'ILCT_WaitSizes': ([P(u32), u32], u32), 'ILCT_WaitPauseWanted': ([P(SeatView)], i32),
    'ILCT_WaitReasonOf': ([u32, u32], i32), 'ILCT_WaitDecide': ([P(EngineIn), P(EngineOut)], None),
    'ILCT_WaitPublish': ([u32, u64, u32, u64], None), 'ILCT_WaitWithdraw': ([], None),
    'ILCT_WaitRecord': ([P(RequestRecord)], i32), 'ILCT_WaitStep': ([u32], None), 'ILCT_WaitStepDry': ([], None),
    'ILCT_WaitAttach': ([u64], i32), 'ILCT_WaitOwned': ([], i32), 'ILCT_WaitEnabled': ([], i32),
    'ILCT_WaitSetOwned': ([i32], None), 'ILCT_WaitForceEnabled': ([i32], None),
    'ILCT_WaitSetImageBase': ([u64], None), 'ILCT_WaitSetFns': ([P(GameFns)], None), 'ILCT_WaitReset': ([], None),
    'ILCT_WaitLast': ([P(EngineOut)], None), 'ILCT_WaitStats': ([P(Counters)], None),
    'ILCT_WaitOwnedAddress': ([], u64),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


class WaitWorld:
    """The patch layer installed on the twin (subsystem_live needs it), the UT-01 tick attached."""

    def __init__(self):
        self.w = gt.world()
        self.d = declare(self.w.d)
        self.twin = self.w.twin

    def setup(self):
        d = self.d
        self.twin.reset()
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_WaitReset()
        report = self.twin.call('ILCT_PatchInstall')
        assert report.result == OK, report.result
        d.ILCT_SetFixedClock(NOW)
        assert d.ILCT_TickAttach() == 1
        d.ILCT_GtqSeal()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_GtqSetGeneration(GEN)


_ww = None


def wait_world() -> WaitWorld:
    global _ww
    if _ww is None:
        _ww = WaitWorld()
    return _ww


def views(*seats):
    arr = (SeatView * 4)()
    for k, (role, open_) in enumerate(seats):
        arr[k].role, arr[k].open = role, open_
    return arr


def engine_in(**kw) -> EngineIn:
    e = EngineIn()
    base = dict(readable=1, live=1, in_play=1, frontend_reason=-1)
    base.update(kw)
    for k, v in base.items():
        setattr(e, k, v)
    return e


# ---- static -----------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_every_address_constant_is_tagged(self):
        problems = []
        for path in sorted(WAIT_SRC.glob('*.[ch]*')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                problems += [f'{path.name}:{number}: {lit}' for lit in untagged_constants(line)]
        self.assertEqual(problems, [])

    def test_the_prologue_pins_are_the_verify_only_rows(self):
        import json
        rows = {r['id']: r for r in json.loads((ROOT / 'native' / 'coop' / 'tools' / 'sites_legacy.json')
                                               .read_text(encoding='utf-8'))['sites']}
        facts = (WAIT_SRC / 'wait_facts.h').read_text(encoding='utf-8')
        pins = re.findall(r'\{(FN_\w+), (\d+), \{([^}]*)\}\}', facts)
        self.assertEqual(len(pins), 5)
        va_of = {name: int(v, 16) for name, v in re.findall(r'constexpr uintptr_t (FN_\w+) = (0x[0-9A-Fa-f]+);', facts)}
        for sid, (name, length, data) in zip(B32_PROPOSAL_IDS, pins):
            row = rows[sid]
            self.assertEqual((row['group'], row['kind'], row['legacy']), ('wait', 'verify-only', 'verify-only'), sid)
            self.assertEqual(int(row['va'], 16), va_of[name], sid)
            self.assertEqual(int(length), row['length'], sid)
            got = bytes(int(b, 16) for b in data.replace('\n', ' ').split(','))
            self.assertEqual(got, bytes.fromhex(row['expected']), sid)

    def test_nothing_blocks_or_writes_game_memory(self):
        text = (WAIT_SRC / 'wait.cpp').read_text(encoding='utf-8')
        for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new '):
            self.assertNotIn(forbidden, text)


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class LayoutTest(unittest.TestCase):
    def test_struct_mirrors(self):
        d = wait_world().d
        out = (u32 * 16)()
        self.assertEqual(d.ILCT_WaitSizes(out, 16), len(LAYOUTS))
        for k, cls in enumerate(LAYOUTS):
            self.assertEqual(out[k], ctypes.sizeof(cls), cls.__name__)


# ---- the rule ---------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class WaitMatrixTest(unittest.TestCase):
    def setUp(self):
        self.d = wait_world().d

    def wanted(self, *seats):
        return bool(self.d.ILCT_WaitPauseWanted(views(*seats)))

    def test_the_216_case_reference_matrix(self):
        # The installed reference's offline table: three seats, each CLOSED/LIST/TARGET, participating or not.
        # Restated: a pause exactly when some seat participates and every participating menu is not CLOSED.
        cases, paused = 0, 0
        for modes in itertools.product((CLOSED, LIST, TARGET), repeat=3):
            for enabled in itertools.product((False, True), repeat=3):
                seats = [(PARTICIPANT if e else ABSENT, int(m != CLOSED)) for m, e in zip(modes, enabled)]
                expected = any(enabled) and all(m != CLOSED for m, e in zip(modes, enabled) if e)
                with self.subTest(modes=modes, enabled=enabled):
                    self.assertEqual(self.wanted(*seats), expected)
                cases += 1
                paused += expected
        self.assertEqual((cases, paused), (216, 98), 'the reference table: 216 cases, 98 of them pause')

    def test_four_seats_with_benched_seats_excluded(self):
        roles = (ABSENT, PARTICIPANT, BENCHED)
        cases = 0
        for combo in itertools.product(itertools.product(roles, (CLOSED, LIST, TARGET)), repeat=4):
            seats = [(r, int(m != CLOSED)) for r, m in combo]
            part = [m for r, m in combo if r == PARTICIPANT]
            expected = bool(part) and all(m != CLOSED for m in part)
            got = self.wanted(*seats)
            if got != expected:
                self.fail(f'{combo}: {got} != {expected}')
            cases += 1
        self.assertEqual(cases, 9 ** 4)

    def test_named_cases(self):
        P_, B_ = PARTICIPANT, BENCHED
        self.assertFalse(self.wanted(), 'nobody participates')
        self.assertTrue(self.wanted((P_, 1)), 'P1 alone with its menu open: stock behaviour')
        self.assertFalse(self.wanted((P_, 1), (P_, 0)), 'one guest browsing never freezes the others')
        self.assertTrue(self.wanted((P_, 1), (P_, 1), (P_, 1), (P_, 1)), 'four open menus')
        self.assertTrue(self.wanted((P_, 1), (B_, 0)), 'a benched seat never holds it back')
        self.assertFalse(self.wanted((B_, 1), (B_, 1)), 'benched seats alone never pause')
        self.assertTrue(self.wanted((ABSENT, 0), (P_, 1)), 'P1 out (no character): the guest alone decides')


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class ReasonTableTest(unittest.TestCase):
    def test_the_table_restated(self):
        d = wait_world().d
        for active in (0, 1):
            for id_ in range(0, 0x40):
                with self.subTest(id=id_, active=active):
                    self.assertEqual(d.ILCT_WaitReasonOf(id_, active), reason(id_, active))


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class ReasonOracleTest(unittest.TestCase):
    """0x255290 itself, run in unicorn on the twin for ids 0..0x1A in both modes, agrees with our copy."""

    def test_the_stock_function_agrees(self):
        ww = wait_world()
        ww.setup()
        emu = MoveEmulator(ww.twin)
        X = pe.X
        mode = {'active': 0}

        def battle_mode(uc):
            uc.reg_write(X.UC_X86_REG_RAX, mode['active'])
        emu.answers[TWIN_BASE + FN_ACTIVE_MODE - 0x120000] = battle_mode
        try:
            for active in (0, 1):
                mode['active'] = active
                for id_ in range(0, 0x1B):
                    state = pe.random_state(random.Random(id_ * 2 + active))
                    state['gpr']['RCX'] = id_
                    rsp = state['gpr']['RSP']
                    emu.refresh()
                    emu.uc.mem_write(rsp, struct.pack('<Q', RETURN_TO))
                    start = TWIN_BASE + FN_REASON_OF - 0x120000
                    b = emu.run(start, lambda x: start <= x < start + REASON_OF_CODE, state)
                    got = struct.unpack('<i', struct.pack('<I', b['gpr']['RAX'] & 0xFFFFFFFF))[0]
                    with self.subTest(id=id_, active=active):
                        self.assertEqual(b['exit'], RETURN_TO)
                        self.assertEqual(got, reason(id_, active))
                        self.assertEqual(ww.d.ILCT_WaitReasonOf(id_, active), got)
        finally:
            emu.answers.pop(TWIN_BASE + FN_ACTIVE_MODE - 0x120000, None)
            ww.twin.reset()


# ---- the decision ------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class DecisionTest(unittest.TestCase):
    def setUp(self):
        self.d = wait_world().d

    def decide(self, **kw) -> EngineOut:
        out = EngineOut()
        self.d.ILCT_WaitDecide(ctypes.byref(engine_in(**kw)), ctypes.byref(out))
        return out

    def test_named_cases(self):
        rows = [
            ('wanted, free: acquire', dict(requested=1), ACT_ACQUIRE, 1),
            ('wanted, ours already: nothing', dict(requested=1, owned=1, held=1), ACT_NONE, 1),
            ('wanted, held by stock: borrow', dict(requested=1, held=1), ACT_NONE, 0),
            ('ours but cleared by stock, still wanted: re-acquire', dict(requested=1, owned=1), ACT_ACQUIRE, 1),
            ('ours but cleared by stock, not wanted: just forget', dict(owned=1), ACT_NONE, 0),
            ('not wanted, ours: release', dict(owned=1, held=1), ACT_RELEASE, 0),
            ('not wanted, ours, a stock reason-0 frontend up: hand over',
             dict(owned=1, held=1, frontend_on=1, frontend_reason=0), ACT_HANDOVER, 0),
            ('wanted, but the main menu (reason 0xD) is up: release ours',
             dict(requested=1, owned=1, held=1, frontend_on=1, frontend_reason=0xD), ACT_RELEASE, 0),
            ('wanted, a frontend without a reason is up: keep', dict(requested=1, owned=1, held=1, frontend_on=1,
                                                                     frontend_reason=-1), ACT_NONE, 1),
            ('Active mode: release', dict(requested=1, owned=1, held=1, active_mode=1), ACT_RELEASE, 0),
            ('Active mode never acquires', dict(requested=1, active_mode=1), ACT_NONE, 0),
            ('not in play (teardown): release', dict(requested=1, owned=1, held=1, in_play=0), ACT_RELEASE, 0),
            ('WAIT bit off (kill switch): release', dict(requested=1, owned=1, held=1, live=0), ACT_RELEASE, 0),
            ('unreadable: nothing, ownership kept', dict(requested=1, owned=1, held=0, readable=0), ACT_NONE, 1),
        ]
        for label, kw, action, owned in rows:
            with self.subTest(label):
                out = self.decide(**kw)
                self.assertEqual((out.action, out.owned), (action, owned))
        self.assertTrue(self.decide(requested=1, held=1).borrowed)
        self.assertTrue(self.decide(requested=1, owned=1).revalidated)

    def test_invariants_over_every_input(self):
        for bits in itertools.product((0, 1), repeat=8):
            for fr in (-1, 0, 2, 0xD):
                owned, readable, held, requested, live, active, in_play, fe_on = bits
                out = self.decide(owned=owned, readable=readable, held=held, requested=requested, live=live,
                                  active_mode=active, in_play=in_play, frontend_on=fe_on, frontend_reason=fr)
                label = f'{bits} {fr}'
                if not readable:
                    self.assertEqual((out.action, out.owned), (ACT_NONE, owned), label)
                    continue
                if out.action == ACT_ACQUIRE:
                    self.assertFalse(held, f'{label}: acquired a held reason')
                    self.assertFalse(active, f'{label}: acquired in Active mode')
                    self.assertTrue(requested and live and in_play, label)
                    self.assertFalse(fe_on and fr not in (-1, 0), f'{label}: acquired next to another reason')
                if out.action == ACT_RELEASE:
                    self.assertTrue(owned and held, f'{label}: released what it does not own')
                    self.assertFalse(fe_on and fr == 0, f'{label}: stole a stock reason-0 frontend\'s pause')
                if out.action == ACT_HANDOVER:
                    self.assertTrue(owned and held and fe_on and fr == 0, label)
                if out.owned:
                    self.assertTrue(held or out.action == ACT_ACQUIRE, f'{label}: owns a clear bit')
                    self.assertTrue(out.wanted, f'{label}: owns what nobody wants')
                if active:
                    self.assertFalse(out.owned, f'{label}: holds a pause in Active mode')


# ---- the engine with doubles ---------------------------------------------------------------------------------------------
class FakeGame:
    """The four functions, recorded; acquire/release set and clear reason 0's bit in the synthetic image."""

    def __init__(self, img):
        self.img, self.calls = img, []
        self.fns = GameFns(F_ACQUIRE(self.acquire), F_RELEASE(self.release), F_VOID(self.aggregate),
                           F_VOID(self.clear_lock))

    def held(self):
        return struct.unpack('<I', ctypes.string_at(self.img.va(REASONS_HELD), 4))[0]

    def set_held(self, value):
        self.img.put_global(REASONS_HELD, struct.pack('<I', value))

    def acquire(self, r, a, b, c, d_, e):
        self.calls.append(('acquire', r, a, b, c, d_, e))
        self.set_held(self.held() | (1 << r))

    def release(self, r, all_):
        self.calls.append(('release', r, all_))
        self.set_held(self.held() & ~(1 << r))

    def aggregate(self):
        self.calls.append(('aggregate',))

    def clear_lock(self):
        self.calls.append(('clear_lock',))


ACQUIRED = [('acquire', 0, 0, 0, 0, -1, -1), ('aggregate',)]
RELEASED = [('release', 0, -1), ('clear_lock',), ('aggregate',)]


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class EngineTest(unittest.TestCase):
    def setUp(self):
        self.ww = wait_world()
        self.ww.setup()
        self.d = self.ww.d
        self.img = Image()
        self.settings = self.img.alloc(16)
        self.img.put_global(SETTINGS_PTR, struct.pack('<Q', self.settings))
        self.mode(active=False)
        self.game = FakeGame(self.img)
        self.game.set_held(0)
        self.frontend(0, 0)
        self.img.put_global(GAME_MODE, b'\x02')
        self.d.ILCT_WaitSetImageBase(self.img.base)
        self.d.ILCT_WaitSetFns(ctypes.byref(self.game.fns))
        self.d.ILCT_WaitForceEnabled(1)
        self.now = NOW

    def tearDown(self):
        self.d.ILCT_WaitReset()
        self.ww.twin.reset()
        self.img.close()

    def mode(self, active):
        ctypes.memmove(self.settings, struct.pack('<I', 0x08 if active else 0), 4)

    def frontend(self, id_, on):
        self.img.put_global(FRONTEND_ID, struct.pack('<I', id_))
        self.img.put_global(FRONTEND_FLAG, struct.pack('<I', on))

    def request(self, on=True, epoch=EPOCH, age=0):
        self.d.ILCT_WaitPublish(epoch, GEN, int(on), self.now - age)

    def step(self, dt=16):
        self.now += dt
        self.d.ILCT_SetFixedClock(self.now)
        self.game.calls.clear()
        self.d.ILCT_WaitStep(EPOCH)
        return list(self.game.calls)

    def owned(self):
        return bool(self.d.ILCT_WaitOwned())

    def stats(self):
        c = Counters()
        self.d.ILCT_WaitStats(ctypes.byref(c))
        return c

    def test_acquire_once_then_release_in_order(self):
        self.request()
        self.assertEqual(self.step(0), ACQUIRED)
        self.assertTrue(self.owned())
        self.request()
        self.assertEqual(self.step(0), [], 'held and ours: no second acquire')
        self.request(False)
        self.assertEqual(self.step(0), RELEASED, 'release, then the Wait lock clear, then the aggregate')
        self.assertFalse(self.owned())
        self.assertEqual(self.game.held() & 1, 0)

    def test_a_stale_request_is_no_request(self):
        self.request()
        self.step(0)
        self.request(epoch=EPOCH + 1)
        self.assertEqual(self.step(0), RELEASED, 'another epoch')
        self.request()
        self.assertEqual(self.step(0), ACQUIRED)
        self.request(age=FRESH_MS + 1)
        self.assertEqual(self.step(0), RELEASED, 'published too long ago (the co-op thread stalled)')
        self.request()
        self.step(0)
        self.d.ILCT_WaitWithdraw()
        self.assertEqual(self.step(0), RELEASED, 'withdrawn')
        self.assertGreaterEqual(self.stats().stale, 2)

    def test_active_mode_never_pauses_and_a_switch_releases_with_the_lock_clear(self):
        self.mode(active=True)
        self.request()
        self.assertEqual(self.step(0), [], 'Active: never')
        self.mode(active=False)
        self.request()
        self.assertEqual(self.step(0), ACQUIRED)
        self.mode(active=True)  # the player switched to Active while the pause was held
        self.request()
        self.assertEqual(self.step(0), RELEASED, 'the lock clear runs in Active too')
        self.img.put_global(SETTINGS_PTR, struct.pack('<Q', 0))
        self.mode(active=False)
        self.request()
        self.assertEqual(self.step(0), [], 'unreadable battle setting: read as Active')

    def test_handover_to_a_stock_reason_0_frontend(self):
        self.request()
        self.step(0)
        self.frontend(0x14, 1)  # a stock frontend that holds reason 0
        self.request(False)
        self.assertEqual(self.step(0), [], 'never steal its pause')
        self.assertFalse(self.owned())
        self.assertEqual(self.game.held() & 1, 1, 'still paused: the frontend holds it now')
        self.frontend(0, 0)
        self.game.set_held(0)  # its close released it
        self.assertEqual(self.step(0), [])
        self.assertEqual(self.stats().handed_over, 1)

    def test_borrow_then_acquire_once_stock_lets_go(self):
        self.game.set_held(1)  # stock holds reason 0 (not ours)
        self.request()
        self.assertEqual(self.step(0), [], 'borrow: own nothing')
        self.assertFalse(self.owned())
        self.request(False)
        self.assertEqual(self.step(0), [], 'never release what it did not take')
        self.game.set_held(0)
        self.request()
        self.assertEqual(self.step(0), ACQUIRED)
        self.assertGreaterEqual(self.stats().borrowed, 1)

    def test_revalidation_after_a_stock_release(self):
        self.request()
        self.step(0)
        self.game.set_held(0)  # the area reset released every reason
        self.request()
        self.assertEqual(self.step(0), ACQUIRED, 'still wanted: taken again on the same call')
        self.game.set_held(0)
        self.request(False)
        self.assertEqual(self.step(0), [], 'not wanted any more: forgotten, nothing released')
        self.assertFalse(self.owned())
        self.assertEqual(self.stats().revalidated, 2)

    def test_another_frontends_reason_releases_ours(self):
        self.request()
        self.step(0)
        self.frontend(0x02, 1)  # the main menu, reason 0xD
        self.request()
        self.assertEqual(self.step(0), RELEASED)
        self.request()
        self.assertEqual(self.step(0), [], 'and does not take it back while that frontend is up')
        self.frontend(0, 0)
        self.request()
        self.assertEqual(self.step(0), ACQUIRED)

    def test_the_kill_switch_and_teardown_release(self):
        self.request()
        self.step(0)
        self.d.ILCT_PatchApplySubsystems(0xFF & ~WAIT_BIT)
        self.request()
        self.assertEqual(self.step(0), RELEASED, 'the WAIT bit off: released although still requested')
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.request()
        self.step(0)
        self.img.put_global(GAME_MODE, b'\x05')
        self.request()
        self.assertEqual(self.step(0), RELEASED, 'game mode 5 (teardown)')

    def test_disabled_never_calls_and_dry_counts(self):
        self.d.ILCT_WaitForceEnabled(0)
        self.request()
        self.assertEqual(self.step(0), [])
        self.assertFalse(self.owned())
        self.d.ILCT_WaitSetOwned(1)
        self.d.ILCT_WaitStepDry()
        self.assertEqual(self.stats().dry_owned, 1)

    def test_attach_fails_closed_on_foreign_bytes(self):
        self.d.ILCT_WaitForceEnabled(-1)
        self.assertEqual(self.d.ILCT_WaitAttach(self.img.base), 0, 'the synthetic image has no such functions')
        self.assertEqual(self.d.ILCT_WaitEnabled(), 0)
        self.assertEqual(self.stats().prologue_mismatch, 1)
        self.assertEqual(self.d.ILCT_WaitAttach(TWIN_BASE), 1, 'the pinned exe')
        self.assertEqual(self.d.ILCT_WaitEnabled(), 1)


# ---- unicorn: the UT-01 tick on the hooked twin ------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class Ut01WaitEmulationTest(unittest.TestCase):
    """The engine runs first in the UT-01 tick: the pause-reason calls, their arguments, the stack alignment, and the
    hand-over and borrow rules, on the hooked twin."""

    @classmethod
    def setUpClass(cls):
        cls.ww = wait_world()
        cls.ww.setup()  # the emulator maps the installed hooks' pages: install once, before it is built
        cls.emu = MoveEmulator(cls.ww.twin)
        cls.seen = []
        X = pe.X
        held = TWIN_BASE + REASONS_HELD - 0x120000

        def acquire(uc):
            rsp = uc.reg_read(X.UC_X86_REG_RSP)
            stack = struct.unpack('<ii', bytes(uc.mem_read(rsp + 0x28, 4)) + bytes(uc.mem_read(rsp + 0x30, 4)))
            regs = tuple(uc.reg_read(r) & 0xFFFFFFFF for r in (X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8,
                                                               X.UC_X86_REG_R9))
            cls.seen.append(('acquire', regs, stack, rsp & 0xF))
            value = struct.unpack('<I', uc.mem_read(held, 4))[0]
            uc.mem_write(held, struct.pack('<I', value | 1))

        def release(uc):
            cls.seen.append(('release', uc.reg_read(X.UC_X86_REG_RCX) & 0xFFFFFFFF,
                             uc.reg_read(X.UC_X86_REG_RDX) & 0xFFFFFFFF, uc.reg_read(X.UC_X86_REG_RSP) & 0xF))
            value = struct.unpack('<I', uc.mem_read(held, 4))[0]
            uc.mem_write(held, struct.pack('<I', value & ~1))

        cls.answers = {FN_ACQUIRE: acquire, FN_RELEASE: release}
        for va, fn in cls.answers.items():
            cls.emu.answers[va] = fn

    @classmethod
    def tearDownClass(cls):
        for va in cls.answers:
            cls.emu.answers.pop(va, None)
        cls.ww.d.ILCT_WaitReset()  # an attached engine must not run in other suites' UT-01 emulations
        cls.ww.twin.reset()

    def prepare(self, requested, owned):
        d = self.ww.d
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_WaitReset()
        assert d.ILCT_TickAttach() == 1
        d.ILCT_GtqSeal()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_SetFixedClock(NOW)
        d.ILCT_PatchHeartbeat(EPOCH)
        assert d.ILCT_WaitAttach(TWIN_BASE) == 1
        d.ILCT_WaitSetFns(None)
        d.ILCT_WaitSetImageBase(TWIN_BASE)
        d.ILCT_WaitSetOwned(1 if owned else 0)
        d.ILCT_WaitPublish(EPOCH, GEN, 1 if requested else 0, NOW)

    def run_tick(self, held=0, frontend=(0, 0), active=False, clock=NOW + 5):
        emu, uc, twin = self.emu, self.emu.uc, self.ww.twin
        row = self.ww.w.ut01
        site = TWIN_BASE + twin.rows[row]['rva']
        emu.refresh()
        emu.tick_clock(clock)
        settings = pe.SCRATCH + 0x40
        uc.mem_write(settings, struct.pack('<I', 0x08 if active else 0))
        rows = [(SETTINGS_PTR, struct.pack('<Q', settings)), (REASONS_HELD, struct.pack('<I', held)),
                (FRONTEND_ID, struct.pack('<I', frontend[0])), (FRONTEND_FLAG, struct.pack('<I', frontend[1])),
                (GAME_MODE, b'\x02')]
        for va, data in rows:
            uc.mem_write(TWIN_BASE + va - 0x120000, data)
        self.seen.clear()
        owned_at = self.ww.d.ILCT_WaitOwnedAddress()
        state = pe.random_state(random.Random(0x3636))
        b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5, state,
                    probe=lambda u: (struct.unpack('<I', u.mem_read(owned_at, 4))[0],
                                     struct.unpack('<I', u.mem_read(TWIN_BASE + REASONS_HELD - 0x120000, 4))[0]))
        self.assertEqual(emu.unexpected, [])
        return b

    def test_acquire_calls_and_arguments(self):
        self.prepare(requested=True, owned=False)
        b = self.run_tick()
        self.assertEqual(b['calls'], [FN_ACQUIRE, FN_AGGREGATE, UI_TICK])
        self.assertEqual(self.seen, [('acquire', (0, 0, 0, 0), (-1, -1), 8)],
                         'ECX..R9D = 0, [rsp+0x20] = [rsp+0x28] = -1 as seen by the callee, RSP = 8 mod 16 at entry')
        self.assertEqual(b['probe'], (1, 1), 'owned, and the bit is set')

    def test_release_calls_and_arguments(self):
        self.prepare(requested=False, owned=True)
        b = self.run_tick(held=1)
        self.assertEqual(b['calls'], [FN_RELEASE, FN_CLEAR_LOCK, FN_AGGREGATE, UI_TICK])
        self.assertEqual(self.seen, [('release', 0, 0xFFFFFFFF, 8)])
        self.assertEqual(b['probe'], (0, 0))

    def test_handover_calls_nothing(self):
        self.prepare(requested=False, owned=True)
        b = self.run_tick(held=1, frontend=(0x14, 1))
        self.assertEqual(b['calls'], [UI_TICK])
        self.assertEqual(b['probe'], (0, 1), 'ownership dropped, the stock frontend keeps the pause')

    def test_borrow_calls_nothing(self):
        self.prepare(requested=True, owned=False)
        b = self.run_tick(held=1)
        self.assertEqual(b['calls'], [UI_TICK])
        self.assertEqual(b['probe'], (0, 1))

    def test_revalidate_then_reacquire(self):
        self.prepare(requested=True, owned=True)
        b = self.run_tick(held=0)
        self.assertEqual(b['calls'], [FN_ACQUIRE, FN_AGGREGATE, UI_TICK])
        self.assertEqual(b['probe'], (1, 1))

    def test_active_mode_calls_nothing(self):
        self.prepare(requested=True, owned=False)
        b = self.run_tick(active=True)
        self.assertEqual(b['calls'], [UI_TICK])

    # ---- a stalled co-op thread: the liveness record is stale, the handler does not run, the stale handler does ----
    STALE = NOW + 2001  # one millisecond past the liveness window (heartbeat at NOW)

    def test_stale_liveness_gives_back_an_owned_pause(self):
        self.prepare(requested=True, owned=True)
        # A request published at the stale moment would keep the pause if the normal handler ran (see the instrument
        # below); with the liveness record stale only the stale handler runs, and it trusts no request.
        self.ww.d.ILCT_WaitPublish(EPOCH, GEN, 1, self.STALE)
        b = self.run_tick(held=1, clock=self.STALE)
        self.assertEqual(b['calls'], [FN_RELEASE, FN_CLEAR_LOCK, FN_AGGREGATE, UI_TICK])
        self.assertEqual(self.seen, [('release', 0, 0xFFFFFFFF, 8)], 'release(0, -1), RSP = 8 mod 16 at entry')
        self.assertEqual(b['probe'], (0, 0), 'not owned, the bit is clear')

    def test_stale_liveness_hands_over_to_a_stock_reason_0_frontend(self):
        self.prepare(requested=False, owned=True)
        b = self.run_tick(held=1, frontend=(0x14, 1), clock=self.STALE)
        self.assertEqual(b['calls'], [UI_TICK])
        self.assertEqual(b['probe'], (0, 1))

    def test_stale_liveness_never_acquires_and_drops_a_cleared_reason(self):
        self.prepare(requested=True, owned=False)
        self.ww.d.ILCT_WaitPublish(EPOCH, GEN, 1, self.STALE)  # fresh for the engine: the normal handler would acquire
        b = self.run_tick(held=0, clock=self.STALE)
        self.assertEqual(b['calls'], [UI_TICK], 'nothing acquired without a fresh beat')
        self.assertEqual(b['probe'], (0, 0))
        self.prepare(requested=True, owned=True)
        b = self.run_tick(held=0, clock=self.STALE)
        self.assertEqual(b['calls'], [UI_TICK])
        self.assertEqual(b['probe'], (0, 0), 'someone else cleared it: ownership dropped, nothing called')

    def test_fresh_liveness_instrument(self):
        self.prepare(requested=True, owned=True)
        self.ww.d.ILCT_WaitPublish(EPOCH, GEN, 1, NOW + 2000)
        b = self.run_tick(held=1, clock=NOW + 2000)
        self.assertEqual(b['calls'], [UI_TICK], 'instrument: inside the window the normal handler keeps a wanted pause')
        self.assertEqual(b['probe'], (1, 1))
        self.prepare(requested=True, owned=False)
        self.ww.d.ILCT_WaitPublish(EPOCH, GEN, 1, NOW + 2000)
        b = self.run_tick(held=0, clock=NOW + 2000)
        self.assertEqual(b['calls'], [FN_ACQUIRE, FN_AGGREGATE, UI_TICK], 'instrument: and acquires one')


if __name__ == '__main__':
    unittest.main()
