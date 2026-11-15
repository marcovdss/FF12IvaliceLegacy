"""Host tests for a guest's target candidates and cursor (native/coop/src/targets). No game run, nothing deployed.

* the candidate filter table: Foes are unit types 1, 4 and 7 (the unit type decides, not the aggression flag or the
  handle's generation), Neutrals type 3; inactive, hidden, untargetable (extended block), dead or handle-less actors
  never; 24 units on the ground (X/Z), boundary included; nearest first; the game's own copy letters, else A, B, ...
  among actors sharing a name; the party always listed as its own group;
* the party rules: KO and Stone against the action's rule bits, revivals only on KO members;
* the reserve: only party members outside the formation, from the native default and menu layouts when they agree
  (an Esper in the party ignored), none while they disagree; a reserve target submits a party id and no handle;
* the cursor: a remedy opens on the first member carrying what it cures, anything else on the issuer; the cursor
  follows the issuer through a reorder and keeps any other candidate by identity; LB/RB cycle the groups that have
  selectable candidates (one press reaches the reserve without foes, two with them);
* the live picker on synthetic game memory: the issuer's position, the party in HUD order, the reserve, the world
  actors from the actor manager, and the live-actor check the indicator uses.
"""
from __future__ import annotations

import ctypes
import struct
import unittest
from pathlib import Path

import test_coop_menu as tm
from coop_native_support import native_test
from coop_session_support import GameWorld, Image, Record
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import untagged_constants

u8, u16, u32, u64, i32, f32 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32,
                               ctypes.c_float)
P = ctypes.POINTER
TARGETS_SRC = Path(__file__).resolve().parents[1] / 'native' / 'coop' / 'src' / 'targets'


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


Snapshot = S('Snapshot', [('handle', u32), ('unit_type', u8), ('flags', u8), ('instance', u16), ('hp', u32),
                          ('status', u32), ('x', f32), ('y', f32), ('z', f32), ('reserved', u32), ('actor', u64),
                          ('name', u64)])
PartyMember = S('PartyMember', [('handle', u32), ('member', u16), ('present', u8), ('reserved', u8), ('hp', u32),
                                ('status', u32), ('actor', u64)])
PickerInput = S('PickerInput', [('issuer_x', f32), ('issuer_y', f32), ('issuer_z', f32), ('issuer_handle', u32),
                                ('actor_count', u32), ('party_count', u32), ('reserve_count', u32),
                                ('reserved', u32), ('party', PartyMember * 4), ('reserve', u16 * 5),
                                ('reserved2', u16 * 3), ('actors', Snapshot * 256)])
ActionRules = S('ActionRules', [('action', u16), ('rules', u16), ('groups', u8), ('revive', u8),
                                ('reserved', u8 * 2)])
Candidate = S('Candidate', [('handle', u32), ('member', u16), ('group', u8), ('flags', u8), ('letter', u8),
                            ('reserved', u8), ('number', u16), ('distance', f32), ('status', u32), ('actor', u64),
                            ('name', u64)])
CandidateList = S('CandidateList', [('first', u16 * 4), ('count', u16 * 4), ('selectable', u16 * 4), ('used', u16),
                                    ('truncated', u8), ('reserved', u8), ('reserved2', u32),
                                    ('c', Candidate * 96)])
Cursor = S('Cursor', [('action', u16), ('group', u8), ('fresh', u8), ('issuer_locked', u8), ('reserved', u8),
                      ('member', u16), ('handle', u32)])
LAYOUTS = [Snapshot, PartyMember, PickerInput, ActionRules, Candidate, CandidateList, Cursor]

SNAP_ACTIVE, SNAP_HIDDEN, SNAP_BLOCKED, SNAP_RECORDS = 0x01, 0x02, 0x04, 0x08
LIVE = SNAP_ACTIVE | SNAP_RECORDS
CF_SELECTABLE, CF_ISSUER = 0x01, 0x02
TG_FOES, TG_NEUTRALS, TG_PARTY, TG_RESERVE = range(4)
ALL_GROUPS = 0x0F
SYNC_IDLE, SYNC_OK, SYNC_EMPTY = 0, 1, 2
NONE = 0xFFFF
POISON, BLIND, SILENCE, SLEEP, CONFUSE, STOP, SLOW, DISEASE = (1 << 8, 1 << 7, 1 << 9, 1 << 4, 1 << 5, 1 << 3,
                                                                1 << 15, 1 << 16)
KO, STONE = 1 << 0, 1 << 1
ISSUER = 0x100001

SIGNATURES = {
    'ILCT_TgtSizes': ([P(u32), u32], u32),
    'ILCT_TgtBuild': ([P(PickerInput), P(ActionRules), P(CandidateList)], None),
    'ILCT_TgtRemedy': ([u32], u32),
    'ILCT_TgtAvailable': ([P(CandidateList)], u32),
    'ILCT_TgtReserve': ([P(u16), P(u16), P(u16), P(u16), P(u32)], i32),
    'ILCT_TgtReadPicker': ([u64, P(Record), u32, P(PickerInput)], i32),
    'ILCT_TgtLiveActor': ([u64, u32, u32], u64),
    'ILCT_TgtCursorReset': ([u32], None),
    'ILCT_TgtSync': ([u32, P(CandidateList)], i32),
    'ILCT_TgtNote': ([u32, P(CandidateList)], None),
    'ILCT_TgtCursor': ([u32, P(Cursor)], None),
}


def declare(d):
    tm.declare(d)
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def foe(handle, x, z=0.0, y=0.0, unit=1, flags=LIVE, hp=100, status=0, name=0, instance=0, actor=None):
    a = Snapshot()
    a.handle, a.unit_type, a.flags, a.hp, a.status = handle, unit, flags, hp, status
    a.x, a.y, a.z, a.name, a.instance = x, y, z, name, instance
    a.actor = actor if actor is not None else 0x7000000 + (handle & 0xFFFF) * 0x1000
    return a


def picker(actors=(), party=(), reserve=(), issuer=(0.0, 50.0, 0.0), issuer_handle=ISSUER) -> PickerInput:
    p = PickerInput()
    p.issuer_x, p.issuer_y, p.issuer_z = issuer
    p.issuer_handle = issuer_handle
    for k, a in enumerate(actors):
        p.actors[k] = a
    p.actor_count = len(actors)
    for k, (handle, member, hp, status) in enumerate(party):
        m = p.party[k]
        m.handle, m.member, m.present, m.hp, m.status = handle, member, 1, hp, status
        m.actor = 0x6000000 + k * 0x1000
    p.party_count = len(party)
    for k, member in enumerate(reserve):
        p.reserve[k] = member
    p.reserve_count = len(reserve)
    return p


def rules(action=0x96, rule_bits=0, groups=ALL_GROUPS, revive=False) -> ActionRules:
    r = ActionRules()
    r.action, r.rules, r.groups, r.revive = action, rule_bits, groups, 1 if revive else 0
    return r


class TargetCase(unittest.TestCase):
    d = None

    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())

    def build(self, inp: PickerInput, act: ActionRules | None = None) -> CandidateList:
        out = CandidateList()
        self.d.ILCT_TgtBuild(ctypes.byref(inp), ctypes.byref(act or rules()), ctypes.byref(out))
        return out

    @staticmethod
    def group(l: CandidateList, g: int, selectable_only=True) -> list[Candidate]:
        rows = [l.c[l.first[g] + k] for k in range(l.count[g])]
        return [c for c in rows if c.flags & CF_SELECTABLE] if selectable_only else rows

    def handles(self, l, g, selectable_only=True):
        return [c.handle for c in self.group(l, g, selectable_only)]


# ---- layouts --------------------------------------------------------------------------------------------------------
@native_test
class LayoutTest(TargetCase):
    def test_struct_mirrors_match_the_module(self):
        sizes = (u32 * 32)()
        n = self.d.ILCT_TgtSizes(sizes, 32)
        self.assertGreaterEqual(n, len(LAYOUTS))
        for k, layout in enumerate(LAYOUTS):
            self.assertEqual(sizes[k], ctypes.sizeof(layout), layout.__name__)

    def test_every_address_constant_is_tagged(self):
        problems = []
        for path in sorted(TARGETS_SRC.glob('*.[ch]*')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                problems += [f'{path.name}:{number}: {lit}' for lit in untagged_constants(line)]
        self.assertEqual(problems, [])

    def test_the_picker_never_writes_or_calls_game_memory(self):
        for path in sorted(TARGETS_SRC.glob('*.cpp')):
            text = path.read_text(encoding='utf-8')
            for forbidden in ('reinterpret_cast<', 'VirtualProtect', 'WriteProcess', 'memcpy(reinterpret'):
                self.assertNotIn(forbidden, text, path.name)


# ---- the candidate filter table (restated from the guest targeting tests) -------------------------------------------
@native_test
class CandidateFilterTest(TargetCase):
    def test_the_range_is_horizontal_and_its_boundary_included(self):
        inside = self.build(picker([foe(0x200027, 24.0, y=-900.0, unit=4)]))
        self.assertEqual(self.handles(inside, TG_FOES), [0x200027])
        self.assertAlmostEqual(self.group(inside, TG_FOES)[0].distance, 24.0, places=5)
        diagonal = self.build(picker([foe(0x200028, 16.97, 16.97)]))
        self.assertEqual(self.handles(diagonal, TG_FOES), [0x200028], 'X and Z together: 23.999')
        outside = self.build(picker([foe(0x200027, 24.001, unit=4)]))
        self.assertEqual(self.handles(outside, TG_FOES), [])

    def test_the_unit_type_decides_not_the_handle_generation(self):
        l = self.build(picker([foe(0x400027, 1.0), foe(0x200028, 2.0)]))
        self.assertEqual(self.handles(l, TG_FOES), [0x400027, 0x200028])

    def test_rejected_actors(self):
        cases = [('hidden', foe(0x31, 1.0, flags=LIVE | SNAP_HIDDEN)),
                 ('inactive', foe(0x32, 1.0, flags=SNAP_RECORDS)),
                 ('untargetable (extended block)', foe(0x33, 1.0, flags=LIVE | SNAP_BLOCKED)),
                 ('no battle records', foe(0x34, 1.0, flags=SNAP_ACTIVE)),
                 ('dead (HP 0)', foe(0x35, 1.0, hp=0)),
                 ('KO bit', foe(0x36, 1.0, status=KO)),
                 ('no handle', foe(0, 1.0)),
                 ('another unit type', foe(0x37, 1.0, unit=2)),
                 ('not a number', foe(0x38, float('nan')))]
        for label, a in cases:
            with self.subTest(label):
                l = self.build(picker([a]))
                self.assertEqual((l.count[TG_FOES], l.count[TG_NEUTRALS]), (0, 0), label)

    def test_mimics_and_hunts_are_foes_and_type_3_is_a_neutral(self):
        l = self.build(picker([foe(0x34, 2.0, unit=7), foe(0x33, 1.0, unit=4), foe(0x30, 1.5, unit=3)]))
        self.assertEqual(self.handles(l, TG_FOES), [0x33, 0x34], 'nearest first')
        self.assertEqual(self.handles(l, TG_NEUTRALS), [0x30])

    def test_nearest_first_and_ties_keep_scan_order(self):
        l = self.build(picker([foe(0x41, 10.0), foe(0x42, 3.0), foe(0x43, 0.0, 3.0), foe(0x44, 7.0)]))
        self.assertEqual(self.handles(l, TG_FOES), [0x42, 0x43, 0x44, 0x41])

    def test_copy_letters(self):
        name = 0x9000
        actors = [foe(0x51, 1.0, name=name, instance=1), foe(0x52, 2.0, name=name, instance=26),
                  foe(0x53, 3.0, name=name, instance=27), foe(0x54, 4.0, name=0x9100, instance=0),
                  foe(0x55, 5.0, name=0x9200), foe(0x56, 6.0, name=0x9200, instance=0xFFFF),
                  foe(0x57, 99.0, name=0x9200)]  # out of range, still a sibling
        l = self.build(picker(actors))
        got = {c.handle: (chr(c.letter) if c.letter else '', c.number) for c in self.group(l, TG_FOES)}
        self.assertEqual(got, {0x51: ('A', 0), 0x52: ('Z', 0), 0x53: ('', 27), 0x54: ('', 0),
                               0x55: ('A', 0), 0x56: ('B', 0)})

    def test_more_than_26_unnamed_copies_are_numbered(self):
        actors = [foe(0x100 + k, 0.1 * k, name=0x9300) for k in range(30)]
        l = self.build(picker(actors))
        tail = self.group(l, TG_FOES)[26:]
        self.assertEqual([(c.letter, c.number) for c in tail], [(0, 27), (0, 28), (0, 29), (0, 30)])

    def test_the_party_is_its_own_group_whatever_the_distance(self):
        l = self.build(picker([foe(0x61, 1.0)], party=[(0x100007, 0, 100, 0), (ISSUER, 1, 100, 0)]))
        self.assertEqual(self.handles(l, TG_PARTY), [0x100007, ISSUER])
        self.assertEqual([bool(c.flags & CF_ISSUER) for c in self.group(l, TG_PARTY)], [False, True])
        self.assertEqual([c.member for c in self.group(l, TG_PARTY)], [0, 1])

    def test_the_list_is_bounded(self):
        actors = [foe(0x1000 + k, 0.05 * k, unit=1 if k % 2 else 3) for k in range(200)]
        l = self.build(picker(actors, party=[(ISSUER, 0, 100, 0)]))
        self.assertEqual(l.used, 96)
        self.assertEqual(l.truncated, 1)


# ---- party rules ---------------------------------------------------------------------------------------------------
@native_test
class PartyRulesTest(TargetCase):
    PARTY = [(0x100001, 0, 100, 0), (0x100002, 1, 0, 0), (0x100003, 2, 100, KO), (0x100004, 3, 100, STONE)]

    def selectable(self, act):
        l = self.build(picker(party=self.PARTY), act)
        return [bool(c.flags & CF_SELECTABLE) for c in self.group(l, TG_PARTY, selectable_only=False)]

    def test_ko_and_stone_against_the_action_rules(self):
        table = [('plain', rules(rule_bits=0), [True, False, False, False]),
                 ('KO allies allowed', rules(rule_bits=0x40), [True, True, True, False]),
                 ('stoned allies allowed', rules(rule_bits=0x80), [True, False, False, True]),
                 ('both', rules(rule_bits=0xC0), [True, True, True, True]),
                 ('a revival: KO members only', rules(0x58, 0x40, revive=True), [False, True, True, False])]
        for label, act, want in table:
            with self.subTest(label):
                self.assertEqual(self.selectable(act), want)

    def test_every_member_is_listed_and_unread_members_stay_selectable(self):
        p = picker(party=self.PARTY)
        p.party[2].present = 0
        l = self.build(p, rules())
        self.assertEqual(l.count[TG_PARTY], 4)
        self.assertEqual(l.selectable[TG_PARTY], 2)


# ---- remedies ----------------------------------------------------------------------------------------------------
@native_test
class RemedyTableTest(TargetCase):
    def test_each_remedy_names_its_own_ailment(self):
        table = {0x5B: POISON, 0x5C: BLIND, 0x5A: SILENCE, 0x5D: SLEEP, 0x5F: STOP | SLOW, 0x60: CONFUSE,
                 0x61: DISEASE, 0x5E: 1 << 11, 0x59: (1 << 1) | (1 << 2), 0x03: POISON, 0x01: BLIND, 0x02: SILENCE,
                 0x07: (1 << 1) | (1 << 2), 0x09: DISEASE, 0x0C: SLOW}
        for action, mask in table.items():
            self.assertEqual(self.d.ILCT_TgtRemedy(action), mask, hex(action))
        self.assertTrue(self.d.ILCT_TgtRemedy(0x62) & POISON and self.d.ILCT_TgtRemedy(0x62) & BLIND, 'Remedy')
        self.assertTrue(self.d.ILCT_TgtRemedy(0x0A) & SLOW, 'Esuna')

    def test_a_plain_restorative_is_not_a_remedy(self):
        for action in (0x52, 0x53, 0x96, 0x58):
            self.assertEqual(self.d.ILCT_TgtRemedy(action), 0, hex(action))


# ---- the cursor on a real model --------------------------------------------------------------------------------------
class ModelCase(TargetCase):
    SEAT = 2

    def setUp(self):
        self.d.ILCT_MenuSeatReset(self.SEAT)
        self.d.ILCT_TgtCursorReset(self.SEAT)

    def open_target(self, action, groups, party_first=True, default=TG_PARTY):
        flags = tm.EF_ENABLED | (tm.EF_PARTY_FIRST if party_first else 0)
        c = tm.hand_catalog({tm.PG_ROOT: [tm.hand_entry(action, flags=flags, groups=groups, default_group=default)]})
        r = tm.Commit()
        self.assertEqual(self.d.ILCT_MenuSeatCommit(self.SEAT, ctypes.byref(c), ctypes.byref(r)), 0)
        self.op(tm.OP_OPEN)
        e = self.op(tm.OP_CONFIRM)
        self.assertEqual(e.kind, tm.EV_TARGET_OPENED)

    def op(self, op, a=0, b=0) -> tm.Event:
        e = tm.Event()
        self.assertEqual(self.d.ILCT_MenuSeatOp(self.SEAT, op, a, b, ctypes.byref(e)), 0)
        return e

    def model(self) -> tm.Model:
        m = tm.Model()
        self.d.ILCT_MenuSeatModel(self.SEAT, ctypes.byref(m))
        return m

    def sync(self, l) -> int:
        return self.d.ILCT_TgtSync(self.SEAT, ctypes.byref(l))

    def selected(self, l) -> Candidate:
        m = self.model()
        return self.group(l, m.target_group)[m.target_index]


@native_test
class RemedyPriorityTest(ModelCase):
    """Items and magicks that cure a status start the party page on a member who has it (restated cases)."""

    def party_page(self, action, party):
        self.open_target(action, 1 << TG_PARTY)
        l = self.build(picker(party=party), rules(action, groups=1 << TG_PARTY))
        self.assertEqual(self.sync(l), SYNC_OK)
        return self.selected(l).handle

    def test_cases(self):
        poisoned, blinded, sleeping = 0x100002, 0x100003, 0x100004
        cases = [('an Antidote opens on the poisoned ally', 0x5B,
                  [(ISSUER, 0, 100, 0), (poisoned, 1, 100, POISON)], poisoned),
                 ('a Potion still opens on its issuer', 0x52, [(poisoned, 1, 100, POISON), (ISSUER, 0, 100, 0)],
                  ISSUER),
                 ("Prince's Kiss opens on the sleeping ally", 0x5D, [(ISSUER, 0, 100, 0), (sleeping, 2, 100, SLEEP)],
                  sleeping),
                 ('nobody afflicted: the issuer', 0x5B, [(blinded, 3, 100, BLIND), (ISSUER, 0, 100, 0)], ISSUER),
                 ('a Remedy matches any ailment it treats', 0x62, [(ISSUER, 0, 100, 0), (blinded, 3, 100, BLIND)],
                  blinded),
                 ('the first afflicted ally wins', 0x5B,
                  [(ISSUER, 0, 100, 0), (poisoned, 1, 100, POISON), (blinded, 3, 100, POISON | BLIND)], poisoned)]
        for label, action, party, want in cases:
            with self.subTest(label):
                self.setUp()
                self.assertEqual(self.party_page(action, party), want)


@native_test
class CursorTest(ModelCase):
    def test_the_issuer_lock_follows_a_reorder_and_other_candidates_are_kept_by_identity(self):
        self.open_target(0x52, (1 << TG_PARTY))
        party = [(0x100005, 5, 100, 0), (ISSUER, 0, 100, 0), (0x100003, 3, 100, 0)]
        l = self.build(picker(party=party), rules(0x52, groups=1 << TG_PARTY))
        self.sync(l)
        self.assertEqual(self.selected(l).handle, ISSUER)
        reordered = self.build(picker(party=[party[1], party[2], party[0]]), rules(0x52, groups=1 << TG_PARTY))
        self.sync(reordered)
        self.assertEqual(self.selected(reordered).handle, ISSUER, 'locked to the issuer')
        self.op(tm.OP_NAV, 1, 1)
        self.d.ILCT_TgtNote(self.SEAT, ctypes.byref(reordered))
        self.assertEqual(self.selected(reordered).handle, 0x100003)
        again = self.build(picker(party=[party[2], party[0], party[1]]), rules(0x52, groups=1 << TG_PARTY))
        self.sync(again)
        self.assertEqual(self.selected(again).handle, 0x100003, 'kept by handle, not by position')
        cur = Cursor()
        self.d.ILCT_TgtCursor(self.SEAT, ctypes.byref(cur))
        self.assertEqual((cur.issuer_locked, cur.handle), (0, 0x100003))

    def test_a_vanished_candidate_falls_back_to_a_clamped_index(self):
        self.open_target(0x96, 1 << TG_FOES, party_first=False, default=TG_FOES)
        l = self.build(picker([foe(0x71, 1.0), foe(0x72, 2.0), foe(0x73, 3.0)]), rules())
        self.sync(l)
        self.assertEqual(self.selected(l).handle, 0x71, 'foes: the nearest')
        self.op(tm.OP_NAV, 2, 1)
        self.d.ILCT_TgtNote(self.SEAT, ctypes.byref(l))
        fewer = self.build(picker([foe(0x71, 1.0), foe(0x72, 2.0)]), rules())
        self.sync(fewer)
        self.assertEqual((self.model().target_count, self.selected(fewer).handle), (2, 0x72))

    def test_an_empty_group_moves_on_and_nothing_at_all_is_empty(self):
        self.open_target(0x96, (1 << TG_FOES) | (1 << TG_PARTY), party_first=False, default=TG_FOES)
        l = self.build(picker(party=[(ISSUER, 0, 100, 0)]), rules())
        self.assertEqual(self.sync(l), SYNC_OK)
        self.assertEqual(self.model().target_group, TG_PARTY, 'no foe in range: the party')
        nothing = self.build(picker(party=[(ISSUER, 0, 0, KO)]), rules())
        self.assertEqual(self.sync(nothing), SYNC_EMPTY)
        self.assertEqual(self.model().target_count, 0)
        self.assertEqual(self.op(tm.OP_CONFIRM).kind, tm.EV_INVALID_TARGET, 'nothing to submit')

    def test_outside_target_mode_the_cursor_resets(self):
        l = self.build(picker(party=[(ISSUER, 0, 100, 0)]))
        self.assertEqual(self.sync(l), SYNC_IDLE)
        cur = Cursor()
        self.d.ILCT_TgtCursor(self.SEAT, ctypes.byref(cur))
        self.assertEqual((cur.action, cur.group), (NONE, 4))


@native_test
class ReserveTest(ModelCase):
    ACTIVE, FOE, RESERVE_MEMBER = 0x100008, 0x200010, 5
    POTION_GROUPS = (1 << TG_PARTY) | (1 << TG_NEUTRALS) | (1 << TG_FOES) | (1 << TG_RESERVE)

    def layouts(self, current, default, menu):
        out, count = (u16 * 5)(), u32()
        arrays = [(u16 * 9)(*(list(x) + [NONE] * (9 - len(x)))) for x in (current, default, menu)]
        ok = self.d.ILCT_TgtReserve(arrays[0], arrays[1], arrays[2], out, ctypes.byref(count))
        return ok, [out[k] for k in range(count.value)]

    def test_only_non_materialized_slots_come_from_agreeing_layouts(self):
        ok, reserve = self.layouts([4, 5], [4, 5, NONE, NONE, 0, 3, 2, 1], [4, 5, NONE, NONE, 0, 3, 2, 1])
        self.assertEqual((ok, reserve[:4]), (1, [0, 3, 2, 1]))
        ok, reserve = self.layouts([3, 1, 5, 33], [3, 1, 5, NONE, 0, 2, 4], [3, 1, 5, NONE, 0, 2, 4])
        self.assertEqual((ok, reserve[:3]), (1, [0, 2, 4]), 'a temporary Esper does not erase the reserve')
        ok, _ = self.layouts([4, 5], [4, 5, NONE, NONE, 0, 3, 2, 1], [4, 5, NONE, NONE, 0, 3, 2])
        self.assertEqual(ok, 0, 'disagreeing layouts: a change is in flight')
        ok, _ = self.layouts([4, 2], [4, 5, NONE, NONE, 0, 3], [4, 5, NONE, NONE, 0, 3])
        self.assertEqual(ok, 0, 'an active member missing from the roster')
        ok, _ = self.layouts([4, 5], [4, 4, NONE, NONE, 0], [4, 4, NONE, NONE, 0])
        self.assertEqual(ok, 0, 'a repeated member')

    def test_reserve_candidates_skip_active_members_and_repeats(self):
        l = self.build(picker(party=[(self.ACTIVE, 0, 100, 0)], reserve=[0, NONE, 5, 5, 2]), rules())
        rows = self.group(l, TG_RESERVE)
        self.assertEqual([(c.handle, c.member) for c in rows], [(0, 5), (0, 2)], 'member 0 is active')

    def potion_model(self, with_foe):
        self.open_target(0x52, self.POTION_GROUPS)
        actors = [foe(self.FOE, 3.0)] if with_foe else []
        l = self.build(picker(actors, party=[(self.ACTIVE, 0, 100, 0)], reserve=[self.RESERVE_MEMBER],
                              issuer_handle=self.ACTIVE), rules(0x52, groups=self.POTION_GROUPS))
        self.sync(l)
        self.assertEqual(self.model().target_group, TG_PARTY)
        return l

    def cycle(self, l):
        e = self.op(tm.OP_GROUP, 1, self.d.ILCT_TgtAvailable(ctypes.byref(l)))
        self.sync(l)
        return e

    def test_without_foes_one_press_reaches_the_reserve(self):
        l = self.potion_model(with_foe=False)
        self.assertEqual(self.cycle(l).group, TG_RESERVE)

    def test_with_foes_two_presses_reach_the_reserve(self):
        l = self.potion_model(with_foe=True)
        self.assertEqual(self.cycle(l).group, TG_FOES)
        self.assertEqual(self.cycle(l).group, TG_RESERVE)

    def test_submit_carries_the_party_id_and_no_world_handle(self):
        l = self.potion_model(with_foe=False)
        self.cycle(l)
        e = self.op(tm.OP_CONFIRM)
        self.assertEqual((e.kind, e.group), (tm.EV_SUBMIT, TG_RESERVE))
        target = self.group(l, e.group)[e.index]
        self.assertEqual((target.handle, target.member), (0, self.RESERVE_MEMBER))


# ---- the live picker -----------------------------------------------------------------------------------------------
@native_test
class LivePickerTest(TargetCase):
    def setUp(self):
        self.img = Image()
        self.gw = GameWorld(self.img)
        self.party = self.gw.party([0, 1, 2])
        self.gw.layout([0, 1, 2], [2, 0, 1])       # the HUD lists 2 first
        for which, members in ((0, [0, 1, 2, NONE, 4, 5]), (3, [0, 1, 2, NONE, 4, 5])):
            self.img.put(self.gw.battle + 0x5A48 + which * 0x12, struct.pack('<9H', *(members + [NONE] * 3)))

    def tearDown(self):
        self.img.close()

    def world_actor(self, index, unit, position, name=0, instance=0, ext_flags=0, hp=100, flags=1):
        a = self.gw.add_actor(index, 9, hp=hp, handle_class=0x200000, position=position)
        ext = self.img.alloc(16)
        self.img.p64(a.actor + 0x6A0, ext)
        self.img.p8(ext, ext_flags)
        self.img.p64(a.actor, flags)
        self.img.p8(a.root + 0x0E, unit)
        self.img.p64(a.root + 0xF8, name)
        self.img.p32(a.root + 0x100, instance)
        return a

    def record(self, seat_actor) -> Record:
        r = Record()
        for s in range(4):
            r.seat[s].member, r.seat[s].party_slot = NONE, 0xFF
        r.seat[1].flags = 0x0001 | 0x0002 | 0x0008
        r.seat[1].member, r.seat[1].handle, r.seat[1].actor = 1, 0x100000 | seat_actor.index, seat_actor.actor
        return r

    def read(self, rec) -> tuple[int, PickerInput]:
        out = PickerInput()
        return self.d.ILCT_TgtReadPicker(self.img.base, ctypes.byref(rec), 1, ctypes.byref(out)), out

    def test_the_picker_reads_issuer_party_reserve_and_world(self):
        for a in self.party:
            self.img.p64(a.actor, 1)
        near = self.world_actor(10, 1, (20.0, 2.0, 30.0), name=0x5000, instance=2)
        self.world_actor(11, 3, (15.0, 2.0, 30.0))
        self.world_actor(12, 1, (12.0, 2.0, 30.0), ext_flags=0x10)   # dead in the extended block
        self.world_actor(13, 5, (12.0, 2.0, 30.0))                   # not a combat unit: not read at all
        self.world_actor(14, 1, (12.0, 2.0, 30.0), flags=3)          # hidden: not read at all
        status, p = self.read(self.record(self.party[1]))
        self.assertEqual(status, 0)
        self.assertEqual((p.issuer_x, p.issuer_handle), (11.0, 0x100001))
        self.assertEqual([p.party[k].member for k in range(p.party_count)], [2, 0, 1], 'HUD order')
        self.assertEqual([p.reserve[k] for k in range(p.reserve_count)][:2], [4, 5])
        handles = {p.actors[k].handle: p.actors[k] for k in range(p.actor_count)}
        self.assertEqual(sorted(h & 0xFFFF for h in handles), [10, 11, 12])
        self.assertEqual((handles[0x20000A].unit_type, handles[0x20000A].instance, handles[0x20000A].name),
                         (1, 2, 0x5000))
        self.assertTrue(handles[0x20000C].flags & SNAP_BLOCKED)
        l = self.build(p)
        self.assertEqual(self.handles(l, TG_FOES), [near.index | 0x200000])
        self.assertEqual(self.group(l, TG_FOES)[0].letter, ord('B'))
        self.assertEqual(self.handles(l, TG_NEUTRALS), [0x20000B])

    def test_no_issuer_or_a_stale_handle_is_refused(self):
        rec = self.record(self.party[1])
        rec.seat[1].handle = 0x100099
        self.assertEqual(self.read(rec)[0], -1)
        rec.seat[1].flags = 0x0003
        self.assertEqual(self.read(rec)[0], -1)

    def test_live_actor(self):
        a = self.party[0]
        handle = 0x100000 | a.index
        self.assertEqual(self.d.ILCT_TgtLiveActor(self.img.base, handle, 1), a.actor)
        self.assertEqual(self.d.ILCT_TgtLiveActor(self.img.base, handle + 0x100000, 0), 0, 'another generation')
        a.set_hp(0)
        self.assertEqual(self.d.ILCT_TgtLiveActor(self.img.base, handle, 1), 0, 'dead')
        self.assertEqual(self.d.ILCT_TgtLiveActor(self.img.base, handle, 0), a.actor, 'present')
        self.assertEqual(self.d.ILCT_TgtLiveActor(self.img.base, 0, 0), 0)


if __name__ == '__main__':
    unittest.main()
