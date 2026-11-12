"""Host tests for town visibility (native/coop/src/towns). No game run, nothing deployed; the exe is only read
(IVALICE_LEGACY_GAME, a copy) for the image-twin and emulator classes.

* the rules on plain values: which party ids count as controlled (P1, manual guests; never a parked guest or a
  fourth seat), the engine's town mode, the slot filter (1, 2, 3 players in a party of 4, story allies controlled
  and uncontrolled, no leader -> nothing written), what a rebuild would change;
* the rebuild planner: world gate, settle times (show fast, hide after the identity settle), one push for a burst
  of changes, minimum interval, caps, degraded (stock town arm), a new world settles again;
* the TW-01 handler natively on saved machine states and synthetic memory: fail-open in every refused condition,
  the register/pointer contract, town mode only, and the exact slots it blanks;
* the rebuild operation on synthetic memory (the game's function replaced by a stub): every refusal calls nothing,
  pool full is reported, and through the queue the job is refused outside a valid world;
* the co-op-thread step: the record it publishes, the job it asks for, debounce and the result it collects;
* unicorn on the hooked twin: the game's layout builder 0x3266C0 run from its entry to its bind loop's end with the
  game's own field-mode and town predicate on synthetic party blocks: town/non-town layouts and bind calls for 1, 2
  and 3 players in a party of 4, story allies, identical to the original bytes whenever the filter must not act
  (dry-arm, record missing, PARTY bit clear, no Companions arm, scripted modes); and the UT-01 tick running the
  rebuild job calls 0x37CA80 once with a Win64-aligned stack before the stock tick.
"""
from __future__ import annotations

import ctypes
import random
import re
import struct
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_patch_engine as pe
from coop_native_support import native_test
from coop_session_support import GATE, PHASE, SEATF, GameWorld, Image, Record, RecordHead
from test_coop_input_service import fresh_test_dll
from test_coop_movement import MoveEmulator
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, checked, untagged_constants

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER
TOWNS_SRC = Path(__file__).resolve().parents[1] / 'native' / 'coop' / 'src' / 'towns'


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


TownRecord = S('TownRecord', [('head', RecordHead), ('allowed', u64)])
PlannerState = S('PlannerState', [('key', u64), ('key_since_ms', u64), ('last_push_ms', u64), ('world_epoch', u32),
                                  ('applied', u32), ('refused', u32), ('ticket', u32), ('has_key', u8),
                                  ('in_flight', u8), ('pushed_once', u8), ('reserved', u8), ('reserved2', u32)])
PlannerInputs = S('PlannerInputs', [('key', u64), ('now_ms', u64), ('world_epoch', u32), ('need', u32),
                                    ('in_town', u8), ('world_ok', u8), ('task_busy', u8), ('degraded', u8),
                                    ('reserved', u32)])
COUNTER_NAMES = ('steps', 'published', 'withdrawn', 'reads_failed', 'pushed', 'push_refused', 'applied', 'refused',
                 'gave_up', 'degraded', 'filtered', 'hidden_slots', 'no_leader', 'contract_failed')
Counters = S('Counters', [(n, u64) for n in COUNTER_NAMES])
DebugView = S('DebugView', [('planner', PlannerState), ('last_inputs', PlannerInputs), ('last_action', i32),
                            ('last_result', i32), ('allowed', u64)])
GPR = ('rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15')
Ctx = S('Ctx', [('xmm', u64 * 12), ('resume', u64), ('reserved', u64)] + [(n, u64) for n in GPR] +
        [('rflags', u64), ('index', u64)])
LAYOUTS = [TownRecord, PlannerState, PlannerInputs, Counters, DebugView]

U16x4 = u16 * 4
NONE = 0xFFFF
NO_LEADER = 0xFF
NEED_SHOW, NEED_HIDE = 1, 2
PUSH, NOTHING, NOT_TOWN, IN_FLIGHT, WAIT_SETTLE, WAIT_INTERVAL, WAIT_WORLD, WAIT_TASK, GAVE_UP, DEGRADED = \
    1, 0, -1, -2, -3, -4, -5, -6, -7, -8
APPLIED, NO_WORLD, NOT_FIELD, NOT_TOWN_R, UNREADABLE, BUSY, POOL_FULL = 1, -16, -17, -18, -19, -20, -21
PARTY = 0x04
OP_TOWN_REBUILD = 9
PUSH_NO_WORLD = 6
RUN_ORIGINAL = 0
EPOCH = gt.EPOCH

# Game facts the tests build (VAs of the pinned build).
PARTY_PTR, LOCATION_ID, AREA_TABLE, TASK_PTR, TOWN_ARM = 0x2EBF190, 0x21654C4, 0x2ADD100, 0x2EBF400, 0x326768
LAYOUTS_OFF = 0x5A48
STOCK_ARM = bytes.fromhex('83E8010F8482010000')
# The town arm rewritten the way Companions' Full Party behaves: the town case jumps straight to the join point.
FULL_PARTY_ARM = bytes.fromhex('83E8010F84') + struct.pack('<i', 0x326A34 - (TOWN_ARM + 9))
REGULAR_IN_SESSION = SEATF['IN_SESSION'] | SEATF['MANUAL']

SIGNATURES = {
    'ILCT_TownSizes': ([P(u32), u32], u32),
    'ILCT_TownControlled': ([P(Record)], u64),
    'ILCT_TownKeeps': ([u64, u32, u32], i32),
    'ILCT_TownMode': ([i32, u32, u32, u32, u32], i32),
    'ILCT_TownFilter': ([u64, u32, P(u16), P(u16)], i32),
    'ILCT_TownNeed': ([u64, u32, P(u16), P(u16)], u32),
    'ILCT_TownPlanReset': ([P(PlannerState)], None),
    'ILCT_TownPlan': ([P(PlannerState), P(PlannerInputs)], i32),
    'ILCT_TownPlanPushed': ([P(PlannerState), i32, u32, u64], None),
    'ILCT_TownPlanResult': ([P(PlannerState), i32], None),
    'ILCT_TownConstants': ([P(u64)], None),
    'ILCT_TownAttach': ([], i32),
    'ILCT_TownRow': ([], i32),
    'ILCT_TownInvoke': ([P(Ctx), u32], i32),
    'ILCT_TownPublish': ([P(TownRecord)], None),
    'ILCT_TownWithdraw': ([], None),
    'ILCT_TownRecord': ([P(TownRecord)], i32),
    'ILCT_TownSessionPublish': ([P(Record)], None),
    'ILCT_TownSessionWithdraw': ([], None),
    'ILCT_TownStart': ([u64, u64], None),
    'ILCT_TownStep': ([u64], None),
    'ILCT_TownStop': ([], None),
    'ILCT_TownReset': ([], None),
    'ILCT_TownSetImageBase': ([u64], None),
    'ILCT_TownSetRematerialize': ([u32, u64, u64], None),
    'ILCT_TownRematerializeCalls': ([], u32),
    'ILCT_TownOp': ([u32], i32),
    'ILCT_TownStats': ([P(Counters)], None),
    'ILCT_TownDebug': ([P(DebugView)], None),
    'ILCT_SesReset': ([], None),
    'ILCT_SesStart': ([u64, u64], None),
    'ILCT_SesStep': ([u64], None),
    'ILCT_SesStop': ([], None),
    'ILCT_GtqWorld': ([], u64),
    'ILCT_GtqDrain': ([], u32),
    'ILCT_GtqGeneration': ([], u64),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


_pure = None


def pure_dll():
    global _pure
    if _pure is None:
        _pure = declare(fresh_test_dll())
    return _pure


def session_record(seats=None, epoch=EPOCH, gates=None, phase=PHASE['FIELD'], facts=0x10, world_epoch=3,
                   generation=gt.GEN) -> Record:
    """seats = {s: (member, flags)}; facts 0x10 = player control on."""
    rec = Record()
    rec.head.epoch, rec.head.generation = epoch, generation
    rec.head.flags = GATE['WORLD'] if gates is None else gates
    rec.phase, rec.facts, rec.world_epoch = phase, facts, world_epoch
    for s in range(4):
        rec.seat[s].member, rec.seat[s].party_slot = NONE, 0xFF
    for s, (member, flags) in (seats or {}).items():
        rec.seat[s].member, rec.seat[s].flags = member, flags
    return rec


def players(p1=0, p2=None, p3=None, p4=None, **kw) -> Record:
    seats = {0: (p1, SEATF['IN_SESSION'])}
    for s, m in ((1, p2), (2, p3), (3, p4)):
        if m is not None:
            seats[s] = (m, REGULAR_IN_SESSION)
    return session_record(seats, **kw)


def bits(*members) -> int:
    return sum(1 << m for m in members)


def town_record(allowed, epoch=EPOCH, flags=1) -> TownRecord:
    r = TownRecord()
    r.head.epoch, r.head.flags, r.head.generation, r.allowed = epoch, flags, gt.GEN, allowed
    return r


# ---- layouts and rules ---------------------------------------------------------------------------------------
@native_test
class LayoutTest(unittest.TestCase):
    def test_struct_mirrors_match_the_module(self):
        d = pure_dll()
        out = (u32 * 16)()
        n = d.ILCT_TownSizes(out, 16)
        self.assertEqual(n, len(LAYOUTS))
        for k, cls in enumerate(LAYOUTS):
            self.assertEqual(out[k], ctypes.sizeof(cls), cls.__name__)
        c = (u64 * 7)()
        d.ILCT_TownConstants(c)
        self.assertEqual(list(c), [40, 300, 250, 1, 16, OP_TOWN_REBUILD, 200])

    def test_every_address_constant_is_tagged(self):
        found = [(p.name, k, lit) for p in sorted(TOWNS_SRC.glob('*.*'))
                 for k, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1)
                 for lit in untagged_constants(line)]
        self.assertEqual(found, [])
        self.assertEqual(untagged_constants('constexpr uintptr_t X = 0x2F76000;'), ['0x2F76000'], 'instrument')

    def test_vas_in_the_facts_are_tagged(self):
        text = (TOWNS_SRC / 'towns_facts.h').read_text(encoding='utf-8')
        for line in text.splitlines():
            if re.search(r'uintptr_t \w+ = 0x', line):
                self.assertRegex(line, r'//.*\bVA\b', line)


@native_test
class RuleTest(unittest.TestCase):
    def setUp(self):
        self.d = pure_dll()

    def test_controlled_members(self):
        d = self.d
        self.assertEqual(d.ILCT_TownControlled(players(0)), bits(0), 'solo: P1 only')
        self.assertEqual(d.ILCT_TownControlled(players(0, 2)), bits(0, 2))
        self.assertEqual(d.ILCT_TownControlled(players(0, 2, 3)), bits(0, 2, 3))
        self.assertEqual(d.ILCT_TownControlled(players(0, 8)), bits(0, 8), 'a story ally a guest drives')
        parked = session_record({0: (0, SEATF['IN_SESSION']), 1: (2, SEATF['IN_SESSION'])})
        self.assertEqual(d.ILCT_TownControlled(parked), bits(0), 'a parked guest controls nothing')
        self.assertEqual(d.ILCT_TownControlled(players(0, 2, 3, p4=4)), bits(0, 2, 3), 'no fourth seat')
        self.assertEqual(d.ILCT_TownControlled(players(0, 70)), bits(0), 'ids >= 64 never count')
        self.assertEqual(d.ILCT_TownControlled(session_record({1: (2, REGULAR_IN_SESSION)})), bits(2),
                         'P1 out of session (no record seat): only the guest')

    def test_town_mode_is_the_engine_rule(self):
        d = self.d
        cases = [  # (table_ok, location, count, byte, mode) -> town
            ((1, 5, 0x100, 0x80, 0), 1),
            ((1, 5, 0x100, 0x7F, 0), 0),          # other bits only
            ((1, 0x100, 0x100, 0x80, 0), 0),      # id out of range
            ((1, 0xFFFFFFFF, 0x100, 0x80, 0), 1),  # negative id reads as area 0
            ((0, 5, 0x100, 0x80, 0), 0),          # no table
            ((1, 5, 0x100, 0x80, 0x4), 0),        # scripted mode 4
            ((1, 5, 0x100, 0x80, 0x8), 0),        # scripted mode 5
            ((1, 5, 0x100, 0x80, 0x3), 1),        # chocobo/flag-0 bits do not hide a town (chocobo is not in towns)
        ]
        for args, want in cases:
            self.assertEqual(d.ILCT_TownMode(*args), want, args)

    def run_filter(self, allowed, leader, current, ui=None):
        cur = U16x4(*current)
        u = U16x4(*(ui if ui is not None else current))
        res = self.d.ILCT_TownFilter(allowed, leader, cur, u)
        return res, list(cur), list(u)

    def test_filter_for_one_two_three_players_in_a_party_of_four(self):
        party = [0, 1, 2, 3]
        self.assertEqual(self.run_filter(bits(0), 0, party), (0b1110, [0, NONE, NONE, NONE], [0, NONE, NONE, NONE]))
        self.assertEqual(self.run_filter(bits(0, 2), 0, party), (0b1010, [0, NONE, 2, NONE], [0, NONE, 2, NONE]))
        self.assertEqual(self.run_filter(bits(0, 2, 3), 0, party), (0b0010, [0, NONE, 2, 3], [0, NONE, 2, 3]))
        self.assertEqual(self.run_filter(bits(0, 1, 2, 3), 0, party), (0, party, party), 'all seated: Full Party')

    def test_leader_is_always_kept_and_p1s_character_too(self):
        # leader moved to a guest's character (B) while the session's swap settles: both keep their bodies
        self.assertEqual(self.run_filter(bits(0, 2), 2, [0, 1, 2, 3])[1], [0, NONE, 2, NONE])
        # leader cycled onto an uncontrolled member C: C is kept before P1's seat adopts it
        self.assertEqual(self.run_filter(bits(0, 2), 1, [0, 1, 2, 3])[1], [0, 1, 2, NONE])

    def test_story_allies_follow_the_same_rule(self):
        party = [0, 1, 2, 8]
        self.assertEqual(self.run_filter(bits(0, 8), 0, party)[1], [0, NONE, NONE, 8], 'controlled ally')
        self.assertEqual(self.run_filter(bits(0), 0, party)[1], [0, NONE, NONE, NONE], 'uncontrolled ally hidden')

    def test_nothing_is_written_without_the_leader_in_current(self):
        for current, leader in (([1, NONE, NONE, NONE], 0), ([0, 1, 2, 3], NO_LEADER), ([NONE] * 4, 0)):
            res, cur, u = self.run_filter(bits(0, 2), leader, current)
            self.assertEqual((res, cur, u), (-1, current, current), (current, leader))

    def test_empty_slots_and_ui_only_change_where_current_is_hidden(self):
        res, cur, u = self.run_filter(bits(0), 0, [0, NONE, 5, NONE], ui=[0, 9, 5, 9])
        self.assertEqual((res, cur, u), (0b0100, [0, NONE, NONE, NONE], [0, 9, NONE, 9]))

    def test_need(self):
        d = self.d

        def need(allowed, leader, current, roster):
            return d.ILCT_TownNeed(allowed, leader, U16x4(*current), U16x4(*roster))

        roster = [0, 1, 2, 3]
        self.assertEqual(need(bits(0, 2), 0, roster, roster), NEED_HIDE, 'Full Party bodies, two players')
        self.assertEqual(need(bits(0, 2), 0, [0, NONE, 2, NONE], roster), 0, 'already filtered')
        self.assertEqual(need(bits(0, 2, 3), 0, [0, NONE, 2, NONE], roster), NEED_SHOW, 'P3 joined')
        self.assertEqual(need(bits(0), 0, [0, NONE, 2, NONE], roster), NEED_HIDE, 'P2 left')
        self.assertEqual(need(bits(0, 2), 1, [0, NONE, 2, NONE], roster), NEED_SHOW, 'leader cycled to a hidden one')
        self.assertEqual(need(bits(1, 2), 1, [0, 1, 2, NONE], roster), NEED_HIDE, 'P1 adopted it: old one hides')
        self.assertEqual(need(bits(0, 2), 9, roster, roster), 0, 'leader not in the roster: nothing to fix')


@native_test
class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.d = pure_dll()
        self.s = PlannerState()
        self.d.ILCT_TownPlanReset(self.s)

    def plan(self, now, key=bits(0, 2), need=NEED_HIDE, in_town=1, world_ok=1, task_busy=0, degraded=0, world=3):
        i = PlannerInputs()
        i.key, i.now_ms, i.world_epoch, i.need = key, now, world, need
        i.in_town, i.world_ok, i.task_busy, i.degraded = in_town, world_ok, task_busy, degraded
        return self.d.ILCT_TownPlan(self.s, i)

    def pushed(self, now, ok=True, ticket=11):
        self.d.ILCT_TownPlanPushed(self.s, 1 if ok else 0, ticket, now)

    def test_hide_waits_out_the_identity_settle_and_show_is_fast(self):
        self.assertEqual(self.plan(1000), WAIT_SETTLE)
        self.assertEqual(self.plan(1299), WAIT_SETTLE)
        self.assertEqual(self.plan(1300), PUSH)
        self.d.ILCT_TownPlanReset(self.s)
        self.assertEqual(self.plan(1000, need=NEED_SHOW), WAIT_SETTLE)
        self.assertEqual(self.plan(1039, need=NEED_SHOW), WAIT_SETTLE)
        self.assertEqual(self.plan(1040, need=NEED_SHOW), PUSH)

    def test_a_burst_of_changes_gives_one_push_after_the_last(self):
        pushes = []
        now = 1000
        for k, key in enumerate([bits(0, 2), bits(0, 2, 3), bits(0, 3), bits(0, 2, 3)]):
            for _ in range(5):  # 5 beats of 20 ms each per key: 100 ms < hide settle
                if self.plan(now, key=key) == PUSH:
                    pushes.append(now)
                    self.pushed(now)
                now += 20
        self.assertEqual(pushes, [])
        while not pushes:
            if self.plan(now, key=bits(0, 2, 3)) == PUSH:
                pushes.append(now)
                self.pushed(now)
            now += 20
        self.assertEqual(len(pushes), 1)
        self.assertGreaterEqual(pushes[0] - (1000 + 3 * 100), 300, 'settled from the last key change')
        self.assertEqual(self.plan(now, key=bits(0, 2, 3)), IN_FLIGHT)

    def test_result_cap_interval_and_refusals(self):
        self.assertEqual(self.plan(1300 + 1000, need=NEED_HIDE), WAIT_SETTLE)  # first sight starts the settle
        self.assertEqual(self.plan(2600), PUSH)
        self.pushed(2600)
        self.d.ILCT_TownPlanResult(self.s, APPLIED)
        self.assertEqual(self.plan(2620), GAVE_UP, 'one applied rebuild per key and world')
        self.assertEqual(self.plan(2640, key=bits(0)), WAIT_SETTLE, 'a new key starts over')
        self.assertEqual(self.plan(2940, key=bits(0)), PUSH)
        self.pushed(2940)
        self.d.ILCT_TownPlanResult(self.s, BUSY)
        self.assertEqual(self.plan(2960, key=bits(0)), WAIT_INTERVAL, 'a refusal backs off')
        self.assertEqual(self.plan(3190, key=bits(0)), PUSH)
        for k in range(15):
            self.pushed(3190 + 250 * k, ok=False)
        self.assertEqual(self.plan(3190 + 250 * 16, key=bits(0)), GAVE_UP, 'refusals are capped (16)')

    def test_gates(self):
        self.plan(0)
        self.assertEqual(self.plan(400, in_town=0), NOT_TOWN)
        self.assertEqual(self.plan(400, degraded=1), DEGRADED)
        self.assertEqual(self.plan(400, need=0), NOTHING)
        self.assertEqual(self.plan(400, world_ok=0), WAIT_WORLD, 'menu, modal, cutscene, load or no world')
        self.assertEqual(self.plan(400, task_busy=1), WAIT_TASK)
        self.assertEqual(self.plan(400, world=4), WAIT_SETTLE, 'a new world settles again')
        self.assertEqual(self.plan(700, world=4), PUSH)

    def test_a_swap_never_rebuilds(self):
        # P1 (0) and P2 (2): the leader moves to 2 and, 200 ms later, the session swaps the seats. P1's character
        # stays controlled through the swap, so the key never changes and neither body needs a rebuild.
        d = self.d
        for now, (allowed, leader) in ((0, (bits(0, 2), 0)), (100, (bits(0, 2), 2)), (300, (bits(2, 0), 2))):
            key = allowed | bits(leader)
            need = d.ILCT_TownNeed(allowed, leader, U16x4(0, NONE, 2, NONE), U16x4(0, 1, 2, 3))
            self.assertEqual(need, 0)
            self.assertEqual(self.plan(now, key=key, need=need), NOTHING)
            self.assertEqual(self.s.key, bits(0, 2), 'the key never changed')


# ---- the patch layer around TW-01 (twin installed, LIVE) -----------------------------------------------------
class TownWorld:
    def __init__(self):
        self.w = gt.world()
        self.d = declare(self.w.d)
        self.twin = self.w.twin
        self.row = self.twin.index['TW-01']

    def setup(self, mode=gt.LIVE, subsystems=0xFF):
        d = self.d
        self.twin.reset()
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_TownReset()
        d.ILCT_SesReset()
        report = self.twin.call('ILCT_PatchInstall')
        assert report.result == OK, report.result
        d.ILCT_SetFixedClock(gt.NOW)
        assert d.ILCT_TickAttach() == 1
        assert d.ILCT_TownAttach() == 1
        d.ILCT_GtqSeal()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(subsystems)
        d.ILCT_PatchSetMode(mode)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_GtqSetGeneration(gt.GEN)

    def soft_reset(self):
        d = self.d
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_SetFixedClock(gt.NOW)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_TownSetImageBase(TWIN_BASE)

    def stats(self) -> Counters:
        c = Counters()
        self.d.ILCT_TownStats(c)
        return c


_tw = None


def town_world() -> TownWorld:
    global _tw
    if _tw is None:
        _tw = TownWorld()
    return _tw


class Party:
    """The party block, location, area table, task pointer and town arm in a synthetic image."""

    def __init__(self, img: Image, town=True, area=7, mode=0, leader=0, roster=(0, 1, 2, 3), current=None,
                 arm=FULL_PARTY_ARM):
        self.img = img
        self.block = img.alloc(0x6000, page=True)
        self.areas = img.alloc(0x1000, page=True)
        img.put_global(PARTY_PTR, struct.pack('<Q', self.block))
        img.put_global(AREA_TABLE, struct.pack('<Q', self.areas))
        img.put_global(TASK_PTR, struct.pack('<Q', 0))
        img.put_global(TOWN_ARM, arm)
        img.p32(self.areas + 4, 0x100)
        self.area = area
        self.place(area, town)
        img.p32(self.block + 0x5B04, mode)
        self.layouts(roster, current)
        self.leader(leader)

    @property
    def layouts_at(self):
        return self.block + LAYOUTS_OFF

    def place(self, area, town):
        self.img.put_global(LOCATION_ID, struct.pack('<I', area))
        self.img.p8(self.areas + 9 + 3 * area, 0x80 if town else 0)

    def layouts(self, roster, current=None):
        current = roster if current is None else current
        for k, members in enumerate((roster, current, current, roster, roster)):
            values = list(members) + [NONE] * (9 - len(members))
            self.img.put(self.layouts_at + 0x12 * k, struct.pack('<9H', *values))

    def leader(self, member):
        self.img.p32(self.block + 0x5AA4, member if member != NO_LEADER else 0xFF)

    def slots(self, k):
        return list(struct.unpack('<4H', ctypes.string_at(self.layouts_at + 0x12 * k, 8)))

    def raw(self):
        return ctypes.string_at(self.layouts_at, 0x60)


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class HandlerNativeTest(unittest.TestCase):
    def setUp(self):
        self.m = town_world()
        self.m.setup()
        self.d = self.m.d
        self.img = Image()
        self.party = Party(self.img)
        self.d.ILCT_TownSetImageBase(self.img.base)
        self.d.ILCT_TownPublish(town_record(bits(0, 2)))

    def tearDown(self):
        self.d.ILCT_TownWithdraw()
        self.d.ILCT_TownSetImageBase(TWIN_BASE)
        self.img.close()

    def ctx(self, **regs) -> Ctx:
        c = Ctx()
        rng = random.Random(1201)
        for n in GPR:
            setattr(c, n, rng.getrandbits(64))
        for k in range(12):
            c.xmm[k] = rng.getrandbits(64)
        self._stack = ctypes.create_string_buffer(0x200)
        c.rsp = ctypes.addressof(self._stack) + 0x80
        c.rdi = self.party.layouts_at
        c.r15 = c.rdi + 0x12
        c.r13 = 0x1234_0000_FFFF
        c.rbp = 0xABCD_0000_0000
        c.rflags = 0x246
        for n, v in regs.items():
            setattr(c, n, v)
        return c

    def invoke(self, c, epoch=EPOCH):
        return self.d.ILCT_TownInvoke(c, epoch)

    def assert_untouched(self, label, epoch=EPOCH, **regs):
        c = self.ctx(**regs)
        before, mem = bytes(c), self.party.raw()
        self.assertEqual(self.invoke(c, epoch), RUN_ORIGINAL, label)
        after = Ctx.from_buffer_copy(before)
        after.index = c.index
        self.assertEqual(bytes(c), bytes(after), f'{label}: registers changed')
        self.assertEqual(self.party.raw(), mem, f'{label}: layouts changed')

    def test_town_filter_blanks_only_uncontrolled_current_and_hud_slots(self):
        before = self.party.raw()
        c = self.ctx()
        regs = bytes(c)
        self.assertEqual(self.invoke(c), RUN_ORIGINAL, 'the original instruction always runs')
        after = Ctx.from_buffer_copy(regs)
        after.index = c.index
        self.assertEqual(bytes(c), bytes(after), 'no register changes')
        self.assertEqual(self.party.slots(1), [0, NONE, 2, NONE])
        self.assertEqual(self.party.slots(2), [0, NONE, 2, NONE])
        raw = self.party.raw()
        for k in (0, 3, 4):
            self.assertEqual(raw[0x12 * k:0x12 * k + 0x12], before[0x12 * k:0x12 * k + 0x12],
                             f'layout {k} (default/menu/fifth) untouched')
        self.assertEqual(raw[0x12 + 8:0x24], before[0x12 + 8:0x24], 'slots 4..8 untouched')
        s = self.m.stats()
        self.assertEqual((s.filtered, s.hidden_slots), (1, 2))

    def test_fail_open_in_every_refused_condition(self):
        self.assert_untouched('record of another epoch', epoch=EPOCH + 1)
        self.d.ILCT_TownPublish(town_record(bits(0, 2), epoch=0))
        self.assert_untouched('record epoch 0')
        self.d.ILCT_TownPublish(town_record(bits(0, 2), flags=0))
        self.assert_untouched('record not active')
        self.d.ILCT_TownWithdraw()
        self.assert_untouched('no record')
        self.d.ILCT_TownPublish(town_record(bits(0, 2)))
        for label, action in (('PARTY bit clear', lambda: self.d.ILCT_PatchApplySubsystems(0xFF & ~PARTY)),
                              ('DRY', lambda: self.d.ILCT_PatchSetMode(gt.DRY)),
                              ('PARTY faulted', lambda: self.d.ILCT_PatchFault(PARTY))):
            action()
            self.assert_untouched(label)
            self.m.setup()
            self.d.ILCT_TownSetImageBase(self.img.base)
            self.d.ILCT_TownPublish(town_record(bits(0, 2)))

    def test_contract_and_town_mode(self):
        self.assert_untouched('r15 is not current', r15=self.party.layouts_at + 0x24)
        self.assert_untouched('r13 is not 0xFFFF', r13=0x1234)
        self.assert_untouched('ebp is not 0', rbp=1)
        self.assert_untouched('rdi is not this party block', rdi=self.party.layouts_at + 0x100,
                              r15=self.party.layouts_at + 0x112)
        self.assertEqual(self.m.stats().contract_failed, 4)
        self.party.place(self.party.area, town=False)
        self.assert_untouched('not a town')
        self.party.place(self.party.area, town=True)
        self.img.p32(self.party.block + 0x5B04, 0x4)
        self.assert_untouched('scripted single-body mode')
        self.img.p32(self.party.block + 0x5B04, 0)
        self.party.leader(5)
        self.assert_untouched('leader not in current')
        self.assertEqual(self.m.stats().no_leader, 1)
        self.party.leader(0)
        self.img.put_global(PARTY_PTR, struct.pack('<Q', 0))
        self.assert_untouched('no party block')

    def test_unreadable_layouts_are_stock(self):
        c = self.ctx()
        regs, mem = bytes(c), self.party.raw()
        self.img.protect(self.party.block + 0x5000, 0x1000, 0x01)
        try:
            self.assertEqual(self.invoke(c), RUN_ORIGINAL)
        finally:
            self.img.protect(self.party.block + 0x5000, 0x1000, 0x04)
        after = Ctx.from_buffer_copy(regs)
        after.index = c.index
        self.assertEqual(bytes(c), bytes(after))
        self.assertEqual(self.party.raw(), mem)


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class RebuildOperationTest(unittest.TestCase):
    def setUp(self):
        self.m = town_world()
        self.m.setup()
        self.d = self.m.d
        self.img = Image()
        self.party = Party(self.img)
        self.d.ILCT_TownSetImageBase(self.img.base)
        self.task = self.img.va(TASK_PTR)
        self.d.ILCT_TownSetRematerialize(1, self.task, 0x5555_0000)
        self.d.ILCT_TownSessionPublish(players(0, 2))

    def tearDown(self):
        self.d.ILCT_TownSessionWithdraw()
        self.d.ILCT_TownSetRematerialize(0, 0, 0)
        self.d.ILCT_TownSetImageBase(TWIN_BASE)
        self.img.close()

    def refused(self, want, label, epoch=EPOCH):
        calls = self.d.ILCT_TownRematerializeCalls()
        self.assertEqual(self.d.ILCT_TownOp(epoch), want, label)
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), calls, f'{label}: the game function ran')

    def test_applied_once_then_busy(self):
        self.assertEqual(self.d.ILCT_TownOp(EPOCH), APPLIED)
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 1)
        self.refused(BUSY, 'the task it started is still running')

    def test_every_refusal_calls_nothing(self):
        self.refused(NO_WORLD, 'job of another epoch', epoch=EPOCH + 1)
        for label, rec, want in (('no valid world', players(0, 2, gates=0), NO_WORLD),
                                 ('menu', players(0, 2, phase=PHASE['MENU']), NOT_FIELD),
                                 ('cutscene', players(0, 2, phase=PHASE['CUTSCENE']), NOT_FIELD),
                                 ('control off', players(0, 2, facts=0), NOT_FIELD)):
            self.d.ILCT_TownSessionPublish(rec)
            self.refused(want, label)
        self.d.ILCT_TownSessionWithdraw()
        self.refused(NO_WORLD, 'no session record')
        self.d.ILCT_TownSessionPublish(players(0, 2))
        self.party.place(self.party.area, town=False)
        self.refused(NOT_TOWN_R, 'not a town')
        self.party.place(self.party.area, town=True)
        self.img.p32(self.party.block + 0x5B04, 0x8)
        self.refused(NOT_TOWN_R, 'scripted mode')
        self.img.p32(self.party.block + 0x5B04, 0)
        self.img.put_global(TASK_PTR, struct.pack('<Q', 0x77))
        self.refused(BUSY, 'a task in flight')
        self.img.put_global(TASK_PTR, struct.pack('<Q', 0))
        self.img.put_global(PARTY_PTR, struct.pack('<Q', 0))
        self.refused(UNREADABLE, 'no party block')

    def test_pool_full_is_reported(self):
        self.d.ILCT_TownSetRematerialize(2, 0, 0)
        self.assertEqual(self.d.ILCT_TownOp(EPOCH), POOL_FULL)
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 1)

    def test_the_job_is_world_gated_in_the_queue(self):
        """With the session core reporting no valid world, the push is refused; the op itself refuses too."""
        d = self.d
        world_img = Image()
        try:
            gw = GameWorld(world_img)
            gw.roots(False)
            d.ILCT_SesStart(world_img.base, gt.NOW)
            d.ILCT_SesStep(gt.NOW)
            epoch = d.ILCT_GtqWorld() >> 32
            self.assertEqual(d.ILCT_GtqWorld() & 1, 0, 'the session reports an invalid world')
            ticket = ctypes.c_uint32()
            status = d.ILCT_GtqPush(OP_TOWN_REBUILD, 0xFF, epoch, d.ILCT_GtqGeneration(), 200, 0, ticket)
            self.assertEqual(status, PUSH_NO_WORLD)
            d.ILCT_SesStop()
        finally:
            d.ILCT_SesReset()
            world_img.close()


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class StepTest(unittest.TestCase):
    """The co-op-thread step on synthetic memory, with the session record published directly."""

    def setUp(self):
        self.m = town_world()
        self.m.setup()
        self.d = self.m.d
        self.img = Image()
        self.party = Party(self.img)  # Full Party bodies: current = roster
        self.d.ILCT_TownSetRematerialize(1, self.img.va(TASK_PTR), 0x4242)
        self.d.ILCT_TownStart(self.img.base, gt.NOW)

    def tearDown(self):
        self.d.ILCT_TownStop()
        self.d.ILCT_TownSessionWithdraw()
        self.d.ILCT_TownSetRematerialize(0, 0, 0)
        self.d.ILCT_TownSetImageBase(TWIN_BASE)
        self.img.close()

    def debug(self) -> DebugView:
        v = DebugView()
        self.d.ILCT_TownDebug(v)
        return v

    def run_beats(self, start, count, rec, drain=True):
        """count beats of 20 ms; the UT-01 drain between beats when drain=True. Returns the beat times pushed at."""
        pushed = []
        for k in range(count):
            now = start + 20 * k
            self.d.ILCT_SetFixedClock(now)  # the module clock is the co-op thread's clock
            self.d.ILCT_PatchHeartbeat(EPOCH)
            self.d.ILCT_TownSessionPublish(rec)
            before = self.m.stats().pushed
            self.d.ILCT_TownStep(now)
            if self.m.stats().pushed != before:
                pushed.append(now)
            if drain:
                self.d.ILCT_GtqDrain()
        return pushed

    def test_publishes_the_controlled_set_and_withdraws_without_a_session(self):
        self.d.ILCT_TownSessionPublish(players(0, 2))
        self.d.ILCT_TownStep(gt.NOW)
        r = TownRecord()
        self.assertEqual(self.d.ILCT_TownRecord(r), 1)
        self.assertEqual((r.head.epoch, r.head.flags, r.allowed), (EPOCH, 1, bits(0, 2)))
        self.d.ILCT_TownSessionWithdraw()
        self.d.ILCT_TownStep(gt.NOW + 20)
        self.assertEqual(self.d.ILCT_TownRecord(r), 0)

    def test_guest_seated_in_town_hides_the_rest_once(self):
        pushed = self.run_beats(gt.NOW, 30, players(0, 2))
        self.assertEqual(len(pushed), 1, pushed)
        self.assertGreaterEqual(pushed[0] - gt.NOW, 300, 'hide waits out the identity settle')
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 1)
        v = self.debug()
        self.assertEqual((v.last_result, v.planner.applied), (APPLIED, 1))
        # the stub does not run the builder, so the layout still needs a hide: capped, no second rebuild
        self.img.put_global(TASK_PTR, struct.pack('<Q', 0))
        self.assertEqual(self.run_beats(gt.NOW + 600, 30, players(0, 2)), [])
        self.assertEqual(self.debug().last_action, GAVE_UP)

    def test_join_shows_fast(self):
        self.party.layouts((0, 1, 2, 3), (0, NONE, 2, NONE))  # P1 + P2 filtered
        self.assertEqual(self.run_beats(gt.NOW, 10, players(0, 2)), [], 'nothing to change')
        pushed = self.run_beats(gt.NOW + 200, 10, players(0, 2, 3))
        self.assertEqual(len(pushed), 1)
        self.assertLessEqual(pushed[0] - (gt.NOW + 200), 60, 'a show waits about two beats')

    def test_no_rebuild_outside_towns_in_menus_or_with_a_stock_town_arm(self):
        for label, prepare, rec in (
                ('field', lambda: self.party.place(self.party.area, town=False), players(0, 2)),
                ('menu', lambda: None, players(0, 2, phase=PHASE['MENU'])),
                ('loading', lambda: None, players(0, 2, gates=0, phase=PHASE['LOADING'])),
                ('stock town arm', lambda: self.img.put_global(TOWN_ARM, STOCK_ARM), players(0, 2)),
                ('PARTY bit clear', lambda: self.d.ILCT_PatchApplySubsystems(0xFF & ~PARTY), players(0, 2))):
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                prepare()
                self.assertEqual(self.run_beats(gt.NOW, 40, rec), [], label)
                self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 0, label)

    def test_rapid_changes_debounce_to_one_rebuild(self):
        recs = [players(0, 2), players(0, 2, 3), players(0, 3), players(0, 2)]
        pushed = []
        now = gt.NOW
        for rec in recs:
            pushed += self.run_beats(now, 6, rec)
            now += 120
        pushed += self.run_beats(now, 30, recs[-1])
        self.assertEqual(len(pushed), 1, pushed)
        self.assertGreaterEqual(pushed[0] - (gt.NOW + 3 * 120), 300)

    def test_job_result_is_collected_even_when_never_drained(self):
        pushed = self.run_beats(gt.NOW, 20, players(0, 2), drain=False)
        self.assertEqual(len(pushed), 1)
        # no drain: await_result reaps it once past ttl + margin, then the planner backs off and retries
        self.run_beats(pushed[0] + 20, 30, players(0, 2), drain=False)
        v = self.debug()
        self.assertEqual(v.last_result, gt.EXPIRED)
        self.assertGreaterEqual(v.planner.refused, 1)


# ---- unicorn: the game's layout builder on the hooked twin -------------------------------------------------------
SCR = pe.SCRATCH
BLOCK_E, TABLE_E, ENT_E, ACT_E, REC_E = SCR + 0x400000, SCR + 0x410000, SCR + 0x420000, SCR + 0x430000, SCR + 0x440000
BUILDER, BUILDER_END = 0x3266C0, 0x326B1E   # entry, and the call of 0x326500 right after the bind loop
CHAIN = [(0x30D9A0, 0x30DA09), (0x377C20, 0x377C6E), (0x3148F0, 0x31490B), (0x2EF2B0, 0x2EF2C4),
         (0x2EF640, 0x2EF648)]           # field mode -> is town -> location id: run as the game's own code
TOWN_MEMBER, SLOT_ENTITY, SLOT_ACTOR, BIND = 0x377C70, 0x263750, 0x263E30, 0x23A570
SITE = 0x326A34
AREA = 7


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class BuilderEmulationTest(unittest.TestCase):
    TRIALS = 3

    @classmethod
    def setUpClass(cls):
        cls.m = town_world()
        cls.m.setup()
        cls.emu = MoveEmulator(cls.m.twin)
        cls.binds = []
        cls.town_member = 0
        emu = cls.emu
        X = pe.X
        emu.answers[SLOT_ENTITY] = lambda uc: uc.reg_write(
            X.UC_X86_REG_RAX, ENT_E + 0x40 * (uc.reg_read(X.UC_X86_REG_RCX) & 0xFFFFFFFF))
        emu.answers[SLOT_ACTOR] = lambda uc: uc.reg_write(
            X.UC_X86_REG_RAX, ACT_E + 0x1000 * ((uc.reg_read(X.UC_X86_REG_RCX) - ENT_E) // 0x40))
        emu.answers[BIND] = lambda uc: cls.binds.append(
            ((uc.reg_read(X.UC_X86_REG_RCX) - ACT_E) // 0x1000, uc.reg_read(X.UC_X86_REG_RDX) & 0xFFFF))
        emu.answers[TOWN_MEMBER] = lambda uc: uc.reg_write(X.UC_X86_REG_RAX, cls.town_member)

    @classmethod
    def tearDownClass(cls):
        for a in (SLOT_ENTITY, SLOT_ACTOR, BIND, TOWN_MEMBER):
            cls.emu.answers.pop(a, None)
        cls.m.d.ILCT_TownWithdraw()
        cls.m.twin.reset()

    def setUp(self):
        self.m.soft_reset()
        self.m.d.ILCT_TownPublish(town_record(bits(0, 2)))

    def world(self, uc, roster=(0, 1, 2, 3), leader=0, town=True, mode=0, bound=None, arm=FULL_PARTY_ARM):
        """Party block and area table in scratch memory, the twin's globals pointing at them; slot actors bound to
        `bound` (default: the roster, as Full Party left them), each with its battle record."""
        bound = list(roster) if bound is None else list(bound)
        values = list(roster) + [NONE] * (9 - len(roster))
        for k in range(5):
            uc.mem_write(BLOCK_E + LAYOUTS_OFF + 0x12 * k, struct.pack('<9H', *values))
        uc.mem_write(BLOCK_E + 0x5AA4, struct.pack('<I', leader))
        uc.mem_write(BLOCK_E + 0x5B04, struct.pack('<I', mode))
        uc.mem_write(PARTY_PTR, struct.pack('<Q', BLOCK_E))
        uc.mem_write(LOCATION_ID, struct.pack('<I', AREA))
        uc.mem_write(AREA_TABLE, struct.pack('<Q', TABLE_E))
        uc.mem_write(TABLE_E + 4, struct.pack('<I', 0x100))
        uc.mem_write(TABLE_E + 8 + 3 * AREA, bytes([0, 0x80 if town else 0, 0]))
        for slot in range(4):
            actor, rec = ACT_E + 0x1000 * slot, REC_E + 0x100 * slot
            member = bound[slot]
            uc.mem_write(actor, struct.pack('<Q', 4 if member != NONE else 0))
            uc.mem_write(actor + 0x698, struct.pack('<Q', rec))
            uc.mem_write(rec + 4, struct.pack('<H', member if member != NONE else 0))
            uc.mem_write(rec + 0x3C, bytes(4))
            uc.mem_write(rec + 0x64, bytes(4))
        self.emu.write_code(TOWN_ARM, arm)

    @staticmethod
    def allowed(x):
        if not TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE:
            return True  # our module, MinHook's buffers, import traps
        return BUILDER <= x < BUILDER_END or any(lo <= x < hi for lo, hi in CHAIN)

    def state(self, trial):
        st = pe.random_state(random.Random(0x7701 + trial))
        st['gpr']['RSP'] = (st['gpr']['RSP'] & ~0xF) - 8  # just called
        return st

    def run_pair(self, trial, **world):
        """Original bytes, then the hooked site, from the same state."""
        emu, uc, twin = self.emu, self.emu.uc, self.m.twin
        row = self.m.row
        r = twin.rows[row]
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        self.world(uc, **world)
        state = self.state(trial)
        patched = bytes(uc.mem_read(SITE, 5))
        layouts = lambda u: bytes(u.mem_read(BLOCK_E + LAYOUTS_OFF, 0x60))  # noqa: E731
        try:
            emu.write_code(SITE, checked(r)[:5])
            self.binds.clear()
            a = emu.run(BUILDER, self.allowed, state, probe=layouts)
            a['binds'] = list(self.binds)
            emu.write_code(SITE, patched)
            before = emu.stats(row)(uc)
            self.binds.clear()
            b = emu.run(BUILDER, self.allowed, state, probe=lambda u: (layouts(u), emu.stats(row)(u)))
            b['binds'] = list(self.binds)
        finally:
            emu.write_code(SITE, patched)
            emu.write_code(TOWN_ARM, STOCK_ARM)
        b['layouts'], counters = b['probe']
        a['layouts'] = a['probe']
        b['delta'] = tuple(x - y for x, y in zip(counters, before))
        touched = set(a['final']) | set(b['final'])
        baseline = {x: uc.mem_read(x, 1)[0] for x in touched}
        self.assertEqual(emu.unexpected, [], f'unexpected imports: {emu.unexpected}')
        self.assertEqual((a['exit'], b['exit']), (TWIN_BASE + BUILDER_END - 0x120000,) * 2)
        return a, b, baseline, state

    @staticmethod
    def slots(layouts, k):
        return list(struct.unpack_from('<4H', layouts, 0x12 * k))

    def test_town_layouts_and_binds_for_one_two_three_players(self):
        cases = [  # (allowed, expected current, expected binds)
            (bits(0), [0, NONE, NONE, NONE], [(1, NONE), (2, NONE), (3, NONE)]),
            (bits(0, 2), [0, NONE, 2, NONE], [(1, NONE), (3, NONE)]),
            (bits(0, 2, 3), [0, NONE, 2, 3], [(1, NONE)]),
        ]
        for allowed, current, binds in cases:
            self.m.d.ILCT_TownPublish(town_record(allowed))
            for trial in range(self.TRIALS):
                with self.subTest(allowed=hex(allowed), trial=trial):
                    a, b, _, _ = self.run_pair(trial)
                    self.assertEqual(self.slots(a['layouts'], 1), [0, 1, 2, 3], 'Full Party without the filter')
                    self.assertEqual(a['binds'], [])
                    self.assertEqual(self.slots(b['layouts'], 1), current)
                    self.assertEqual(self.slots(b['layouts'], 2), current, 'HUD follows current')
                    for k in (0, 3, 4):
                        self.assertEqual(self.slots(b['layouts'], k), [0, 1, 2, 3], f'layout {k} keeps the party')
                    self.assertEqual(b['binds'], binds, 'the game unbinds exactly the hidden slots')
                    self.assertEqual(b['delta'][:3], (1, 0, 1))
                    self.assertEqual(a['gpr']['RSP'], b['gpr']['RSP'])

    def test_unbound_actors_get_bound_only_to_controlled_members(self):
        self.m.d.ILCT_TownPublish(town_record(bits(0, 2)))
        a, b, _, _ = self.run_pair(0, bound=[NONE] * 4)
        self.assertEqual(a['binds'], [(0, 0), (1, 1), (2, 2), (3, 3)])
        self.assertEqual(b['binds'], [(0, 0), (2, 2)])

    def test_story_allies_controlled_and_uncontrolled(self):
        roster = (0, 1, 2, 8)
        for allowed, current in ((bits(0, 8), [0, NONE, NONE, 8]), (bits(0), [0, NONE, NONE, NONE])):
            self.m.d.ILCT_TownPublish(town_record(allowed))
            with self.subTest(allowed=hex(allowed)):
                _, b, _, _ = self.run_pair(1, roster=roster)
                self.assertEqual(self.slots(b['layouts'], 1), current)

    def test_non_town_layout_is_the_full_party_and_identical_to_stock(self):
        for players_mask in (bits(0), bits(0, 2), bits(0, 2, 3)):
            self.m.d.ILCT_TownPublish(town_record(players_mask))
            for trial in range(self.TRIALS):
                with self.subTest(allowed=hex(players_mask), trial=trial):
                    a, b, baseline, state = self.run_pair(trial, town=False)
                    gt.compare(self, self.emu, 'non-town', a, b, baseline, state['gpr']['RSP'])
                    self.assertEqual(a['binds'], b['binds'])
                    self.assertEqual(self.slots(b['layouts'], 1), [0, 1, 2, 3])

    def test_identical_to_stock_whenever_the_filter_must_not_act(self):
        conditions = {
            'no record': (lambda: self.m.d.ILCT_TownWithdraw(), {}),
            'other epoch': (lambda: self.m.d.ILCT_TownPublish(town_record(bits(0), epoch=EPOCH + 1)), {}),
            'PARTY bit clear': (lambda: self.m.d.ILCT_PatchApplySubsystems(0xFF & ~PARTY), {}),
            'DRY (dry-arm)': (lambda: self.m.d.ILCT_PatchSetMode(gt.DRY), {}),
            'stock town arm, town member = leader': (lambda: None, {'arm': STOCK_ARM}),
            'stock town arm, town member != leader': (lambda: setattr(type(self), 'town_member', 1),
                                                      {'arm': STOCK_ARM}),
            'scripted mode 4 in a town': (lambda: None, {'mode': 4}),
            'everyone seated': (lambda: self.m.d.ILCT_TownPublish(town_record(bits(0, 1, 2, 3))), {}),
        }
        passed = 0
        for label, (prepare, world) in conditions.items():
            self.setUp()
            type(self).town_member = 0
            prepare()
            for trial in range(self.TRIALS):
                with self.subTest(condition=label, trial=trial):
                    a, b, baseline, state = self.run_pair(trial, **world)
                    gt.compare(self, self.emu, label, a, b, baseline, state['gpr']['RSP'])
                    self.assertEqual(a['binds'], b['binds'], label)
                    passed += 1
        type(self).town_member = 0
        print(f'\nTW-01 stock equivalence: {passed} builder runs equal to the original bytes')

    def test_stock_town_collapse_is_left_alone(self):
        type(self).town_member = 1
        try:
            a, b, _, _ = self.run_pair(0, arm=STOCK_ARM)
        finally:
            type(self).town_member = 0
        self.assertEqual(self.slots(a['layouts'], 1), [NONE, 1, NONE, NONE], 'the instrument: stock collapse')
        self.assertEqual(self.slots(b['layouts'], 1), [NONE, 1, NONE, NONE])
        self.assertEqual(b['delta'][:3], (1, 0, 1), 'the handler ran and chose to write nothing')


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class RebuildJobEmulationTest(unittest.TestCase):
    """The UT-01 tick on the hooked twin drains OP_TOWN_REBUILD: it calls the game's 0x37CA80 once (entered with
    RSP = 8 mod 16), then the stock UI tick, and the job completes APPLIED. Refused preconditions call nothing."""

    REMAT, TICK = 0x37CA80, 0x290CF0  # VAs: re-materialize, the UI manager tick (UT-01 callee)

    @classmethod
    def setUpClass(cls):
        cls.m = town_world()
        cls.m.setup()
        cls.emu = MoveEmulator(cls.m.twin)
        cls.seen = []
        X = pe.X

        def remat(uc):
            cls.seen.append(uc.reg_read(X.UC_X86_REG_RSP) & 0xF)
            uc.mem_write(TASK_PTR, struct.pack('<Q', 0x9999))  # the game stored a new task
        cls.emu.answers[cls.REMAT] = remat

    @classmethod
    def tearDownClass(cls):
        cls.emu.answers.pop(cls.REMAT, None)
        cls.m.d.ILCT_TownSessionWithdraw()
        cls.m.twin.reset()

    def world(self, uc, town=True, task=0):
        uc.mem_write(PARTY_PTR, struct.pack('<Q', BLOCK_E))
        uc.mem_write(BLOCK_E + 0x5B04, struct.pack('<I', 0))
        uc.mem_write(LOCATION_ID, struct.pack('<I', AREA))
        uc.mem_write(AREA_TABLE, struct.pack('<Q', TABLE_E))
        uc.mem_write(TABLE_E + 4, struct.pack('<I', 0x100))
        uc.mem_write(TABLE_E + 9 + 3 * AREA, bytes([0x80 if town else 0]))
        uc.mem_write(TASK_PTR, struct.pack('<Q', task))

    def run_tick(self, ticket, **world):
        emu, uc, twin = self.emu, self.emu.uc, self.m.twin
        row = self.m.w.ut01
        site = TWIN_BASE + twin.rows[row]['rva']
        completions = self.m.w.addr.completions + (ticket & (gt.COMPLETIONS - 1)) * 32
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        self.world(uc, **world)
        self.seen.clear()
        state = pe.random_state(random.Random(0x7702))
        b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5, state,
                    probe=lambda u: struct.unpack('<Ii', u.mem_read(completions + 8, 8)))
        self.assertEqual(emu.unexpected, [])
        got_ticket, result = b['probe']
        return b, (result if got_ticket == ticket else gt.PENDING)

    def push(self):
        d = self.m.d
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_TownAttach()
        d.ILCT_GtqSeal()
        d.ILCT_GtqSetGeneration(gt.GEN)
        d.ILCT_TownSessionPublish(players(0, 2))
        ticket = ctypes.c_uint32()
        self.assertEqual(d.ILCT_GtqPush(OP_TOWN_REBUILD, 0xFF, EPOCH, gt.GEN, 200, 0, ticket), gt.PUSH_OK)
        return ticket.value

    def test_job_calls_the_game_function_then_the_stock_tick(self):
        self.m.soft_reset()
        ticket = self.push()
        b, result = self.run_tick(ticket)
        self.assertEqual(b['calls'], [TWIN_BASE + self.REMAT - 0x120000, TWIN_BASE + self.TICK - 0x120000])
        self.assertEqual(self.seen, [8], 'Win64 entry: RSP = 8 mod 16')
        self.assertEqual(result, APPLIED)

    def test_refused_job_calls_only_the_stock_tick(self):
        for label, world in (('not a town', {'town': False}), ('task in flight', {'task': 0x1234})):
            with self.subTest(label):
                self.m.soft_reset()
                ticket = self.push()
                b, result = self.run_tick(ticket, **world)
                self.assertEqual(b['calls'], [TWIN_BASE + self.TICK - 0x120000], label)
                self.assertEqual(result, NOT_TOWN_R if label == 'not a town' else BUSY)


if __name__ == '__main__':
    unittest.main()
