"""Host tests for seat assignment, benching, preferences, swaps and leader changes (native/coop/src/party, plus the
session core's posted adoption and preference fill). No game run, nothing deployed; the exe is only read
(IVALICE_LEGACY_GAME, a copy) for the image-twin classes.

* the rules on plain values: the picker, benching, and what a change request turns into (kind, the seats that
  change hands, the new leader) or why it is refused;
* the permutation matrix: every ordering of four of the six regulars in the active party (360), each slot as
  leader, three humans: assignment in seat order with preferences, every seat keeping its character through a slot
  reorder, and every seat asking for every regular (no duplicates, Player 1 never without a character, a leader
  change exactly when Player 1 is involved); with two regulars the third human is benched;
* the session core: a posted change is adopted at the next step without the identity settle, held while the world
  read lags, dropped when it disagrees; a benched seat is flagged and claims its preferred character first;
* the steering remap: applied on the record copy only while pending and of the record's epoch, one member per seat;
* on the image twin with a real session core on synthetic game memory: the change operation (leader setter stub,
  remap and post in the same callback; the guest steers its new character in the very next detour call, before
  any co-op-thread step), every frame refusal (setter never called), stale requests, the leader watcher (Companions'
  cycle onto a guest's character, a KO promotion, a cycle onto an uncontrolled member, a summon, our own write, a
  write inside the stock UI tick seen by the after-handler), the town rebuild for a member without a body, the
  remap retired once the steering record names the new body, and the UI-tick handler asking for the after-handler;
* the Mod Menu's move-seat command reaching the party subsystem through the input service.
"""
from __future__ import annotations

import ctypes
import itertools
import random
import struct
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_movement as mv
import test_coop_patch_engine as pe
import test_coop_towns as tw
from coop_native_support import native_test
from coop_session_support import (_SIGNATURES as SES_SIGNATURES, CHAR_AUTO, CHAR_NONE, GATE, SEATF, GameWorld,
                                  Identity, Image, Record, RecordHead, SeatConfig, identity, layout4)
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, checked, untagged_constants

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER
PARTY_SRC = Path(__file__).resolve().parents[1] / 'native' / 'coop' / 'src' / 'party'
NONE = 0xFFFF


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


SeatsView = S('SeatsView', [('member', u16 * 4), ('flags', u16 * 4)])
PartyView = S('PartyView', [('current', u16 * 4), ('ui', u16 * 4), ('leader', u32), ('able', u8 * 4),
                            ('busy', u8 * 4)])
ChangePlan = S('ChangePlan', [('kind', u8), ('seat', u8), ('other', u8), ('target_slot', u8), ('target', u16),
                              ('released', u16), ('new_leader', u16), ('reserved', u16), ('mask', u32),
                              ('after', u16 * 4)])
RemapRecord = S('RemapRecord', [('head', RecordHead), ('seq', u32), ('mask', u32), ('published_ms', u64),
                                ('slot', mv.SteerEntry * 2)])
COUNTER_NAMES = ('steps', 'requests', 'pushed', 'push_refused', 'applied', 'already', 'refused', 'requests_refused',
                 'requests_timed_out', 'leader_writes', 'remaps', 'remaps_retired', 'remaps_expired',
                 'watcher_changes', 'external_swaps', 'external_left', 'ko_promotions', 'watcher_after',
                 'town_rebuilds', 'town_takes')
Counters = S('Counters', [(n, u64) for n in COUNTER_NAMES])
RequestView = S('RequestView', [('state', u8), ('target', u8), ('attempts', u8), ('town_fails', u8),
                                ('last_result', i32), ('ticket', u32), ('reserved2', u32)])
DebugView = S('DebugView', [('seat', RequestView * 4), ('watcher_leader', u32), ('watcher_epoch', u32),
                            ('remap_seq', u32), ('remap_retired', u32), ('adopted_seq', u32), ('posted_seq', u32)])
Adoption = S('Adoption', [('seq', u32), ('epoch', u32), ('mask', u32), ('member', u16 * 4), ('leader', u16),
                          ('reserved', u16)])
LAYOUTS = [SeatsView, PartyView, ChangePlan, RemapRecord, Counters, DebugView, Adoption]

KIND = {'NONE': 0, 'ALREADY': 1, 'LEADER': 2, 'SWAP_P1': 3, 'SWAP_GUESTS': 4, 'TAKE_FREE': 5}
OKC = 0
BAD_SEAT, NOT_REGULAR, NOT_IN_PARTY, UNAVAILABLE, BUSY, BENCHED, P1_UNSTABLE, DUPLICATE = range(-16, -24, -1)
APPLIED, ALREADY = 1, 2
(NO_WORLD, AREA_PENDING, ROSTER_EDIT, NOT_FIELD, APPLY_PENDING, MODE, LOCATION_MOVED, STALE, UNREADABLE, BAD_ARGS,
 SETTER_FAILED, TOWN_BUSY, TOWN_REBUILD) = range(-32, -45, -1)
REQ = {'IDLE': 0, 'WAITING': 1, 'QUEUED': 2, 'DONE': 3, 'REFUSED': 4, 'TIMED_OUT': 5}
OP_PARTY_CHANGE = 16
GUEST = SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['STEERS']
PARTY_BIT = 0x04
RUN_ORIGINAL, RESUME, CALL_AROUND = 0, 1, 2

# Game facts the tests build (VAs of the pinned build).
PENDING_AREA, LOCK_MASK, PENDING_HANDLE, LOCATION_ID, LOCATION_FLAGS = (0x1EED064, 0x22CB720, 0x22C7FF8, 0x21654C4,
                                                                        0x21654E2)
TASK_PTR, TOWN_ARM, AREA_TABLE_PTR = 0x2EBF400, 0x326768, 0x2ADD100
REQUEST_WAIT_MS, TOWN_RETRY_MS, TOWN_TRIES = 5000, 1000, 3   # party.h

SIGNATURES = {
    'ILCT_PartySizes': ([P(u32), u32], u32),
    'ILCT_PartyPlan': ([P(SeatsView), P(PartyView), u32, u32, P(ChangePlan)], i32),
    'ILCT_PartyPicker': ([u32, u32, u32], i32),
    'ILCT_PartyBenched': ([P(SeatsView), u32], i32),
    'ILCT_PartyAttach': ([], i32),
    'ILCT_PartyStart': ([u64, u64], None),
    'ILCT_PartyStep': ([u64], None),
    'ILCT_PartyStop': ([], None),
    'ILCT_PartyReset': ([], None),
    'ILCT_PartySetImageBase': ([u64], None),
    'ILCT_PartyForceLive': ([i32], None),
    'ILCT_PartySetStubs': ([u32, u64, i32], None),
    'ILCT_PartySetterOverride': ([u32], None),
    'ILCT_PartySetterOverrideFrom': ([u32, u32], None),
    'ILCT_PartyCalls': ([P(u32)], None),
    'ILCT_PartyRequest': ([u32, u32], None),
    'ILCT_PartyOp': ([u32, u64, P(u64)], i32),
    'ILCT_PartyTickBefore': ([], None),
    'ILCT_PartyTickAfter': ([], None),
    'ILCT_PartyAfterWanted': ([], i32),
    'ILCT_PartyRemapPublish': ([u32, u32, u64, u32, P(mv.SteerEntry), u64], None),
    'ILCT_PartyRemapApply': ([P(mv.SteerRecord)], i32),
    'ILCT_PartyRemapPending': ([], i32),
    'ILCT_PartyRemapRead': ([P(RemapRecord)], i32),
    'ILCT_PartyRemapRetire': ([u32], None),
    'ILCT_PartyDebug': ([P(DebugView)], None),
    'ILCT_PartyStats': ([P(Counters)], None),
    'ILCT_SesPostAdoption': ([P(Adoption)], None),
    'ILCT_SesAdoptedSeq': ([], u32),
    'ILCT_SesAdoptionsDropped': ([], u32),
    'ILCT_SesResolvePrefer': ([P(Identity), P(Identity), P(u16), P(u16), u32, P(Identity)], i32),
    'ILCT_TickRun': ([u32], i32),
    'ILCT_PartyAddress': ([u32], u64),
    'ILCT_TownRebuildBinds': ([u64], None),
    'ILCT_TownPendingAllowed': ([u32], u64),
}


def declare(d):
    for table in (SES_SIGNATURES, SIGNATURES):
        for name, (args, res) in table.items():
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


def seats(*members, flags=None) -> SeatsView:
    v = SeatsView()
    for s in range(4):
        v.member[s] = members[s] if s < len(members) else NONE
        if flags is not None:
            v.flags[s] = flags[s] if s < len(flags) else 0
        else:
            v.flags[s] = (SEATF['IN_SESSION'] if s == 0 else GUEST) if s < len(members) and s < 3 else 0
    return v


def party(current, leader, ui=None, able=None, busy=None) -> PartyView:
    p = PartyView()
    cur = list(current) + [NONE] * (4 - len(current))
    uiv = cur if ui is None else list(ui) + [NONE] * (4 - len(ui))
    for k in range(4):
        p.current[k], p.ui[k] = cur[k], uiv[k]
        p.able[k] = 1 if able is None else able[k]
        p.busy[k] = 0 if busy is None else busy[k]
    p.leader = leader
    return p


def expected_change(members, current, seat, target):
    """The rules restated in Python: (refusal or OK, kind, after)."""
    members = list(members)
    if target >= 6:
        return NOT_REGULAR, None, None
    if target not in current:
        return NOT_IN_PARTY, None, None
    mine = members[seat]
    if mine == target:
        return OKC, KIND['ALREADY'], members
    holder = next((s for s, m in enumerate(members) if m == target), None)
    after = list(members)
    after[seat] = target
    if holder is not None:
        if mine == NONE:
            return BENCHED, None, None
        after[holder] = mine
        return OKC, KIND['SWAP_P1'] if 0 in (seat, holder) else KIND['SWAP_GUESTS'], after
    return OKC, KIND['LEADER'] if seat == 0 else KIND['TAKE_FREE'], after


def encode(kind, seat, target, released, slot, location, handle=0, actor=0, phys=0, reload=0):
    a = (u64 * 4)()
    a[0] = kind | (seat << 8) | (target << 16) | (released << 32) | (slot << 48) | (reload << 56)
    a[1] = location | (handle << 32)
    a[2], a[3] = actor, phys
    return a


# ---- layouts and rules ---------------------------------------------------------------------------------------
@native_test
class LayoutTest(unittest.TestCase):
    def test_struct_mirrors_match_the_module(self):
        d = pure_dll()
        sizes = (u32 * 16)()
        n = d.ILCT_PartySizes(sizes, 16)
        self.assertEqual(n, len(LAYOUTS))
        for k, struct_type in enumerate(LAYOUTS):
            self.assertEqual(sizes[k], ctypes.sizeof(struct_type), struct_type.__name__)

    def test_every_address_constant_is_tagged(self):
        problems = []
        for path in sorted(PARTY_SRC.glob('*.[ch]*')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                for lit in untagged_constants(line):
                    problems.append(f'{path.name}:{number}: {lit}')
        self.assertEqual(problems, [])
        self.assertEqual(untagged_constants('x = 0x80000003;'), ['0x80000003'], 'instrument')


@native_test
class RuleTest(unittest.TestCase):
    def setUp(self):
        self.d = pure_dll()

    def plan(self, s, p, seat, target):
        out = ChangePlan()
        return self.d.ILCT_PartyPlan(s, p, seat, target, out), out

    def test_picker_offers_the_regulars_minus_player_ones(self):
        for p1 in range(6):
            for seat in range(4):
                for member in range(16):
                    want = seat < 3 and member < 6 and (seat == 0 or member != p1)
                    with self.subTest(p1=p1, seat=seat, member=member):
                        self.assertEqual(self.d.ILCT_PartyPicker(seat, member, p1), int(want))
        self.assertEqual(self.d.ILCT_PartyPicker(1, 0, 3), 1, 'Vaan is allowed for a guest while P1 is not Vaan')

    def test_benched(self):
        v = seats(0, NONE, 1)
        self.assertEqual([self.d.ILCT_PartyBenched(v, s) for s in range(4)], [0, 1, 0, 0])
        v.flags[1] = 0  # not in the session: not benched, simply absent
        self.assertEqual(self.d.ILCT_PartyBenched(v, 1), 0)

    def test_kinds(self):
        p = party((0, 5, 1, 3), 0)
        s = seats(0, 5, 1)
        cases = [
            (1, 0, KIND['SWAP_P1'], [5, 0, 1, NONE], 5, 0),        # a guest asks for Player 1's character
            (0, 5, KIND['SWAP_P1'], [5, 0, 1, NONE], 5, 1),        # Player 1 asks for a guest's character
            (1, 1, KIND['SWAP_GUESTS'], [0, 1, 5, NONE], NONE, 2),
            (1, 3, KIND['TAKE_FREE'], [0, 3, 1, NONE], NONE, NONE),
            (0, 3, KIND['LEADER'], [3, 5, 1, NONE], 3, NONE),
            (2, 1, KIND['ALREADY'], [0, 5, 1, NONE], NONE, NONE),
        ]
        for seat, target, kind, after, leader, other in cases:
            with self.subTest(seat=seat, target=target):
                r, out = self.plan(s, p, seat, target)
                self.assertEqual(r, OKC)
                self.assertEqual((out.kind, list(out.after), out.new_leader), (kind, after, leader))
                self.assertEqual(out.other, 0xFF if other == NONE else other)

    def test_refusals(self):
        p = party((0, 5, 1, 3), 0)
        s = seats(0, 5, 1)
        self.assertEqual(self.plan(s, p, 3, 1)[0], BAD_SEAT, 'a fourth seat never takes part')
        self.assertEqual(self.plan(s, p, 1, 7)[0], NOT_REGULAR, 'story guests only through recovery')
        self.assertEqual(self.plan(s, p, 1, 4)[0], NOT_IN_PARTY)
        self.assertEqual(self.plan(s, party((0, 5, 1, 3), 0, ui=(0, 5, 1)), 1, 3)[0], NOT_IN_PARTY,
                         'both layouts')
        self.assertEqual(self.plan(s, party((0, 5, 1, 3), 0, able=(1, 1, 1, 0)), 1, 3)[0], UNAVAILABLE)
        self.assertEqual(self.plan(s, party((0, 5, 1, 3), 0, busy=(0, 0, 0, 1)), 1, 3)[0], BUSY)
        self.assertEqual(self.plan(s, party((0, 5, 1, 3), 0, able=(1, 0, 1, 1)), 1, 0)[0], UNAVAILABLE,
                         'Player 1 would receive a character that cannot lead')
        self.assertEqual(self.plan(seats(0, NONE, 1), p, 1, 0)[0], BENCHED)
        self.assertEqual(self.plan(seats(0, NONE, 1), p, 1, 1)[0], BENCHED)
        r, out = self.plan(seats(0, NONE, 1), p, 1, 3)
        self.assertEqual((r, out.kind, list(out.after)), (OKC, KIND['TAKE_FREE'], [0, 3, 1, NONE]),
                         'a benched seat claims a free member')
        self.assertEqual(self.plan(s, party((0, 5, 1, 3), 5), 1, 3)[0], P1_UNSTABLE, 'KO promotion')
        self.assertEqual(self.plan(seats(0, 5, 5), p, 1, 3)[0], DUPLICATE)


@native_test
class PermutationMatrixTest(unittest.TestCase):
    """Three humans and four of the six regulars in every order (6P4 = 360), each slot as leader."""

    def setUp(self):
        self.d = pure_dll()

    def resolve(self, configured, prefer, current, leader):
        out = Identity()
        ok = self.d.ILCT_SesResolvePrefer(configured, prefer, layout4(*current), layout4(*current), leader, out)
        return ok, [out.member[s] for s in range(3)]

    def test_assignment_preferences_reorder_and_every_change(self):
        cases = changes = 0
        for i, perm in enumerate(itertools.permutations(range(6), 4)):
            for leader_slot in range(4):
                leader = perm[leader_slot]
                pref2, pref3 = (i + leader_slot) % 6, (i * 5 + 3 + leader_slot) % 6
                ok, got = self.resolve(identity(0, NONE, NONE, in_session=0b111),
                                       identity(0, pref2, pref3, in_session=0b111), perm, leader)
                taken = [leader]
                for pref in (pref2, pref3):
                    pick = pref if pref in perm and pref not in taken else next(m for m in perm if m not in taken)
                    taken.append(pick)
                self.assertEqual((ok, got), (1, taken), (perm, leader, pref2, pref3))
                # Every seat keeps its character through a slot reorder.
                for order in ((1, 2, 3, 0), (3, 2, 1, 0), (2, 0, 3, 1)):
                    shuffled = [perm[k] for k in order]
                    ok, again = self.resolve(identity(*got), None, shuffled, leader)
                    self.assertEqual(again, got, (perm, shuffled))
                # Every seat asks for every regular.
                s = seats(*got)
                p = party(perm, leader)
                for seat in range(3):
                    for target in range(6):
                        out = ChangePlan()
                        r = self.d.ILCT_PartyPlan(s, p, seat, target, out)
                        want_r, kind, after = expected_change(got, perm, seat, target)
                        self.assertEqual(r, want_r, (perm, got, seat, target))
                        if r == OKC:
                            self.assertEqual((out.kind, list(out.after)[:3]), (kind, after), (perm, seat, target))
                            members = [m for m in out.after[:3] if m != NONE]
                            self.assertEqual(len(members), len(set(members)), 'two seats on one character')
                            self.assertNotEqual(out.after[0], NONE, 'Player 1 without a character')
                            leader_change = out.new_leader != NONE
                            self.assertEqual(leader_change, kind in (KIND['LEADER'], KIND['SWAP_P1']))
                            if leader_change:
                                self.assertEqual(out.new_leader, out.after[0])
                        changes += 1
                cases += 1
        self.assertEqual((cases, changes), (1440, 1440 * 18))

    def test_two_regulars_bench_the_third_human(self):
        for pair in itertools.permutations(range(6), 2):
            ok, got = self.resolve(identity(0, NONE, NONE, in_session=0b111), None, pair, pair[0])
            self.assertEqual((ok, got), (1, [pair[0], pair[1], NONE]))
            v = seats(*got)
            self.assertEqual(self.d.ILCT_PartyBenched(v, 2), 1)


# ---- the session core: posted adoption, benching, preference fill ------------------------------------------------
class SessionCase(unittest.TestCase):
    NOW0 = 10_000

    def setUp(self):
        self.d = pure_dll()
        self.d.ILCT_SesReset()
        self.img = Image()
        self.world = GameWorld(self.img)
        self.now = self.NOW0

    def tearDown(self):
        self.d.ILCT_SesReset()
        self.img.close()

    def start(self, characters=(0, 5, 1, CHAR_NONE), has_input=0b0111, parked=0):
        cfg = SeatConfig()
        for k in range(4):
            cfg.character[k] = characters[k]
        cfg.has_input, cfg.parked, cfg.enabled = has_input, parked, 1
        self.d.ILCT_SesConfigure(ctypes.byref(cfg))
        self.d.ILCT_SesStart(self.img.base, self.now)

    def step(self, n=1):
        for _ in range(n):
            self.now += 20
            self.d.ILCT_SesStep(self.now)
        r = Record()
        self.assertEqual(self.d.ILCT_SesRecord(r), 1)
        return r

    def post(self, seq, mask, members, leader, epoch=None):
        a = Adoption()
        a.seq, a.mask, a.leader = seq, mask, leader
        a.epoch = self.d.ILCT_SesEpoch() if epoch is None else epoch
        for s in range(4):
            a.member[s] = members[s] if s < len(members) else NONE
        self.d.ILCT_SesPostAdoption(a)

    @staticmethod
    def members(r):
        return [r.seat[s].member for s in range(3)]


@native_test
class AdoptionTest(SessionCase):
    def setUp(self):
        super().setUp()
        self.world.party([0, 5, 1, 3])
        self.start()
        self.step(3)

    def test_a_posted_swap_is_adopted_at_the_next_step(self):
        self.world.leader(5)
        self.post(1, 0b011, [5, 0, 1], 5)
        r = self.step()
        self.assertEqual(self.members(r), [5, 0, 1], 'no identity settle for our own change')
        self.assertEqual(self.d.ILCT_SesAdoptedSeq(), 1)
        self.assertTrue(r.seat[1].flags & SEATF['STEERS'])
        self.assertEqual(r.seat[1].actor, self.world.actors[0].actor, 'P2 steers Player 1\'s former body')

    def test_a_lagging_world_read_holds_the_post_then_drops_it(self):
        self.post(2, 0b011, [5, 0, 1], 5)   # the leader word still says 0
        for _ in range(4):
            self.assertEqual(self.members(self.step()), [0, 5, 1])
        self.world.leader(5)                # the game catches up within the window
        self.assertEqual(self.members(self.step()), [5, 0, 1])
        self.post(3, 0b011, [0, 5, 1], 0)   # a post the world never agrees with
        before = self.d.ILCT_SesAdoptionsDropped()
        self.step(6)
        self.assertEqual(self.d.ILCT_SesAdoptionsDropped(), before + 1)
        self.assertEqual(self.d.ILCT_SesAdoptedSeq(), 2)

    def test_disagreeing_posts_are_dropped(self):
        epoch = self.d.ILCT_SesEpoch()
        for label, post in (
                ('another epoch', dict(seq=4, mask=0b011, members=[5, 0, 1], leader=5, epoch=epoch + 1)),
                ('two seats on one member', dict(seq=5, mask=0b010, members=[0, 1, 1], leader=0)),
                ('not in the party', dict(seq=6, mask=0b010, members=[0, 4, 1], leader=0)),
                ('Player 1 not the leader', dict(seq=7, mask=0b011, members=[3, 0, 1], leader=0)),
                ('a story guest', dict(seq=8, mask=0b010, members=[0, 7, 1], leader=0))):
            with self.subTest(label):
                before = self.d.ILCT_SesAdoptionsDropped()
                self.post(**post)
                self.assertEqual(self.members(self.step()), [0, 5, 1])
                self.assertEqual(self.d.ILCT_SesAdoptionsDropped(), before + 1)

    def test_a_newer_post_replaces_an_unapplied_one(self):
        self.post(9, 0b011, [5, 0, 1], 5)
        self.post(10, 0b010, [0, 3, 1], 0)
        r = self.step()
        self.assertEqual((self.members(r), self.d.ILCT_SesAdoptedSeq()), ([0, 3, 1], 10))


@native_test
class BenchTest(SessionCase):
    def test_a_benched_seat_is_flagged_and_claims_its_preference_first(self):
        self.world.party([0, 5])
        self.start(characters=(0, CHAR_AUTO, 3, CHAR_NONE))
        r = self.step(14)
        self.assertEqual(self.members(r), [0, 5, NONE])
        self.assertTrue(r.seat[2].flags & SEATF['BENCHED'])
        self.assertFalse(r.seat[1].flags & SEATF['BENCHED'])
        # Two members join; P3 prefers Balthier (3), who is second in party order.
        self.world.layout([0, 5, 2, 3])
        self.world.add_actor(2, 2)
        self.world.add_actor(3, 3)
        r = self.step(14)
        self.assertEqual(self.members(r), [0, 5, 3])
        self.assertFalse(r.seat[2].flags & SEATF['BENCHED'])

    def test_a_parked_seat_without_a_character_is_not_benched(self):
        self.world.party([0, 5])
        self.start(characters=(0, CHAR_AUTO, CHAR_AUTO, CHAR_NONE), parked=0b100)
        r = self.step(14)
        self.assertFalse(r.seat[2].flags & SEATF['BENCHED'])


@native_test
class RemapTest(unittest.TestCase):
    def setUp(self):
        self.d = pure_dll()
        self.d.ILCT_PartyReset()

    def tearDown(self):
        self.d.ILCT_PartyReset()

    def entries(self, actor):
        e = (mv.SteerEntry * 2)()
        e[0].actor, e[0].phys, e[0].root, e[0].flags, e[0].heading = actor, actor + 0x100, actor + 0x200, 1, 0.5
        return e

    def test_applies_while_pending_and_of_the_same_epoch(self):
        rec = mv.record_for(dict(actor=0xB000, phys=0xB100, root=0xB200), dict(actor=0xA000, phys=1, root=2), epoch=4)
        rec.active = 0b11
        rec.slot[0].flags = rec.slot[1].flags = 1
        self.assertEqual(self.d.ILCT_PartyRemapPending(), 0)
        self.assertEqual(self.d.ILCT_PartyRemapApply(rec), 0)
        self.d.ILCT_PartyRemapPublish(1, 4, 9, 0b01, self.entries(0xA000), 0)
        self.assertEqual(self.d.ILCT_PartyRemapPending(), 1)
        other = mv.SteerRecord.from_buffer_copy(rec)
        other.head.epoch = 5
        self.assertEqual(self.d.ILCT_PartyRemapApply(other), 0, 'another epoch')
        self.assertEqual(self.d.ILCT_PartyRemapApply(rec), 1)
        self.assertEqual((rec.slot[0].actor, rec.slot[0].heading, rec.slot[0].magnitude), (0xA000, 0.5, 0.0))
        self.assertEqual(rec.active, 0b01, 'the slot that still named the moved body stops steering')
        self.d.ILCT_PartyRemapRetire(1)
        self.assertEqual(self.d.ILCT_PartyRemapPending(), 0)

    def test_an_inactive_entry_steers_nothing(self):
        rec = mv.record_for(dict(actor=0xB000, phys=0xB100, root=0xB200), epoch=4)
        rec.active = 0b01
        e = (mv.SteerEntry * 2)()
        self.d.ILCT_PartyRemapPublish(2, 4, 9, 0b01, e, 0)
        self.assertEqual(self.d.ILCT_PartyRemapApply(rec), 1)
        self.assertEqual(rec.active, 0)


# ---- on the image twin: the operation, the watcher, the step -----------------------------------------------------
class PartyWorld:
    def __init__(self):
        self.w = gt.world()
        self.d = declare(tw.declare(mv.declare(self.w.d)))
        self.twin = self.w.twin

    def setup(self):
        d = self.d
        self.twin.reset()
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_MoveReset()
        d.ILCT_TownReset()
        d.ILCT_PartyReset()
        d.ILCT_SesReset()
        report = self.twin.call('ILCT_PatchInstall')
        assert report.result == OK, report.result
        d.ILCT_SetFixedClock(gt.NOW)
        assert d.ILCT_TickAttach() == 1
        assert d.ILCT_MoveAttach() == 1
        assert d.ILCT_TownAttach() == 1
        assert d.ILCT_PartyAttach() == 1
        d.ILCT_GtqSeal()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_PatchHeartbeat(gt.EPOCH)


_pw = None


def party_world() -> PartyWorld:
    global _pw
    if _pw is None:
        _pw = PartyWorld()
    return _pw


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class TwinCase(unittest.TestCase):
    MEMBERS = (0, 5, 1, 3)   # Vaan leads (P1), Penelo is P2's, Ashe P3's, Balthier nobody's

    def setUp(self):
        self.m = party_world()
        self.m.setup()
        self.d = self.m.d
        self.img = Image()
        self.world = GameWorld(self.img)
        self.actors = self.world.party(list(self.MEMBERS))
        for a in self.actors:
            self.img.p64(a.root + 0x30, a.actor)   # the scene root leads back to its actor
            self.img.put(a.phys + 0xA4, struct.pack('<2f', 0.25 * a.index, -0.5))  # heading, facing
        for va, data in ((PENDING_AREA, struct.pack('<i', -1)), (LOCK_MASK, bytes(4)), (PENDING_HANDLE, bytes(1)),
                         (LOCATION_ID, struct.pack('<I', 7)), (LOCATION_FLAGS, bytes(1)),
                         (TASK_PTR, bytes(8))):
            self.img.put_global(va, data)
        self.leader_word = self.world.battle + 0x5AA4
        self.now = gt.NOW
        cfg = SeatConfig()
        for k, ch in enumerate((0, 5, 1, CHAR_NONE)):
            cfg.character[k] = ch
        cfg.has_input, cfg.enabled = 0b0111, 1
        self.d.ILCT_SesConfigure(ctypes.byref(cfg))
        self.d.ILCT_SesStart(self.img.base, self.now)
        self.d.ILCT_PartySetImageBase(self.img.base)
        self.d.ILCT_TownSetImageBase(self.img.base)
        self.d.ILCT_PartySetStubs(1, self.leader_word, 1)
        self.d.ILCT_PartyStart(self.img.base, self.now)
        self.beat(14, drain=False)
        self.assertEqual(self.members(), [0, 5, 1])
        self.assertEqual(self.steering(), [1, 2])

    def tearDown(self):
        self.d.ILCT_PartyStop()
        self.d.ILCT_PartyReset()
        self.d.ILCT_SesReset()
        self.d.ILCT_MoveWithdraw()
        self.d.ILCT_TownSetRematerialize(0, 0, 0)
        self.d.ILCT_TownSetImageBase(TWIN_BASE)
        self.d.ILCT_PartySetImageBase(0x120000)
        self.img.close()

    # ---- driving ----
    def beat(self, n=1, drain=True, party_step=True):
        for _ in range(n):
            self.now += 20
            self.d.ILCT_SetFixedClock(self.now)
            self.d.ILCT_SesStep(self.now)
            self.d.ILCT_PatchHeartbeat(self.d.ILCT_SesEpoch())
            if party_step:
                self.d.ILCT_PartyStep(self.now)
            if drain:
                self.d.ILCT_GtqDrain()

    def record(self) -> Record:
        r = Record()
        self.assertEqual(self.d.ILCT_SesRecord(r), 1)
        return r

    def members(self):
        r = self.record()
        return [r.seat[s].member for s in range(3)]

    def steering(self):
        r = self.record()
        return [s for s in range(4) if r.seat[s].flags & SEATF['STEERS']]

    def calls(self):
        out = (u32 * 4)()
        self.d.ILCT_PartyCalls(out)
        return list(out)

    def stats(self) -> Counters:
        c = Counters()
        self.d.ILCT_PartyStats(c)
        return c

    def debug(self) -> DebugView:
        v = DebugView()
        self.d.ILCT_PartyDebug(v)
        return v

    def leader(self):
        return struct.unpack('<I', ctypes.string_at(self.leader_word, 4))[0]

    def remap(self):
        r = RemapRecord()
        return r if self.d.ILCT_PartyRemapRead(r) == 1 else None

    def steer_from_session(self):
        """The steering record the co-op thread would have published before the change (guest seats' bodies)."""
        r = self.record()
        rec = mv.SteerRecord()
        rec.head.epoch, rec.head.flags, rec.head.generation = r.head.epoch, mv.STEERF_MOVE | mv.STEERF_LOGICAL, \
            r.head.generation
        for k in range(2):
            e = r.seat[k + 1]
            if e.flags & SEATF['STEERS']:
                x = rec.slot[k]
                x.actor, x.phys, x.root = e.actor, e.phys, self.actors[[a.actor for a in self.actors].index(e.actor)].root
                x.member, x.seat, x.flags, x.handle = e.member, k + 1, 1, e.handle
                rec.active |= 1 << k
        self.d.ILCT_MovePublish(ctypes.byref(rec))
        return rec

    def mv05(self, actor):
        c = mv.Ctx()
        c.r13, c.r10 = actor, TWIN_BASE + mv.FOLLOW_LIST_PRIMARY - 0x120000
        return self.d.ILCT_MoveInvoke(5, ctypes.byref(c), self.d.ILCT_SesEpoch())


class OperationTest(TwinCase):
    def test_a_guest_takes_player_ones_character_in_one_callback(self):
        steer = self.steer_from_session()
        vaan, penelo = self.actors[0], self.actors[1]
        self.assertEqual((self.mv05(penelo.actor), self.mv05(vaan.actor)), (RESUME, RUN_ORIGINAL), 'instrument')
        self.d.ILCT_PartyRequest(1, 0)
        self.beat(drain=False)
        self.assertEqual(self.debug().seat[1].state, REQ['QUEUED'])
        self.d.ILCT_GtqDrain()   # the UI tick: setter, remap and post in one callback
        self.assertEqual(self.calls()[:3], [1, 5, 1], 'leader setter once with Penelo; field ready checked')
        self.assertEqual(self.leader(), 5)
        m = self.remap()
        self.assertIsNotNone(m)
        self.assertEqual((m.mask, m.slot[0].actor, m.slot[0].member, m.slot[0].magnitude), (0b01, vaan.actor, 0, 0.0))
        # The next frame's movement detours, before any co-op-thread step: P2's pad drives Vaan, never Penelo.
        self.assertEqual(self.mv05(vaan.actor), RESUME, 'Vaan is kept from the follower AI: P2 steers him')
        self.assertEqual(self.mv05(penelo.actor), RUN_ORIGINAL, 'Penelo is Player 1\'s now')
        del steer
        self.assertEqual(self.members(), [0, 5, 1], 'the session has not stepped yet')
        self.beat(drain=False)
        self.assertEqual(self.members(), [5, 0, 1], 'adopted at the next step, no 200 ms settle')
        self.assertEqual(self.steering(), [1, 2])
        self.assertEqual(self.record().seat[1].actor, vaan.actor)
        self.assertEqual(self.debug().seat[1].state, REQ['DONE'])
        c = self.stats()
        self.assertEqual((c.applied, c.leader_writes, c.remaps, c.watcher_changes), (1, 1, 1, 0))

    def test_player_one_takes_an_uncontrolled_member(self):
        self.d.ILCT_PartyRequest(0, 3)
        self.beat()
        self.assertEqual(self.calls()[:2], [1, 3])
        self.assertIsNone(self.remap(), 'no guest changed hands: nothing to remap')
        self.beat()
        self.assertEqual(self.members(), [3, 5, 1])

    def test_two_guests_swap_without_a_leader_change(self):
        self.steer_from_session()
        self.d.ILCT_PartyRequest(2, 5)   # P3 asks for P2's Penelo
        self.beat()
        self.assertEqual(self.calls()[0], 0, 'no leader write')
        m = self.remap()
        self.assertEqual((m.mask, m.slot[0].actor, m.slot[1].actor), (0b11, self.actors[2].actor,
                                                                     self.actors[1].actor))
        self.beat()
        self.assertEqual(self.members(), [0, 1, 5])

    def test_a_guest_takes_an_uncontrolled_member_and_releases_its_own(self):
        self.d.ILCT_PartyRequest(1, 3)
        self.beat()
        m = self.remap()
        self.assertEqual((m.mask, m.slot[0].actor), (0b01, self.actors[3].actor))
        self.beat()
        self.assertEqual(self.members(), [0, 3, 1])

    def test_the_already_held_character_is_done_without_a_job(self):
        self.d.ILCT_PartyRequest(1, 5)
        self.beat()
        self.assertEqual((self.debug().seat[1].state, self.stats().pushed), (REQ['DONE'], 0))

    def queued(self, seat, target):
        self.d.ILCT_PartyRequest(seat, target)
        self.beat(drain=False)
        v = self.debug().seat[seat]
        self.assertEqual(v.state, REQ['QUEUED'])
        return v.ticket

    def test_every_frame_refusal_runs_nothing(self):
        conditions = [
            ('area requested', PENDING_AREA, struct.pack('<i', 12), AREA_PENDING),
            ('transition lock', LOCK_MASK, struct.pack('<I', 1 << 12), AREA_PENDING),
            ('script roster edit', LOCK_MASK, struct.pack('<I', 1 << 19), ROSTER_EDIT),
            ('controlled-actor change pending', PENDING_HANDLE, b'\x01', APPLY_PENDING),
            ('location moved', LOCATION_ID, struct.pack('<I', 8), LOCATION_MOVED),
            ('area reloading', LOCATION_FLAGS, b'\x20', LOCATION_MOVED),
        ]
        for label, va, data, code in conditions:
            with self.subTest(label):
                ticket = self.queued(1, 0)
                saved = ctypes.string_at(self.img.va(va), len(data))
                self.img.put_global(va, data)
                self.d.ILCT_GtqDrain()
                self.img.put_global(va, saved)
                self.assertEqual(self.d.ILCT_GtqResult(ticket, None), code)
                self.assertEqual(self.calls()[0], 0, label)
                self.assertIsNone(self.remap())
                self.d.ILCT_PartyReset()
                self.d.ILCT_PartySetImageBase(self.img.base)
                self.d.ILCT_PartySetStubs(1, self.leader_word, 1)
                self.d.ILCT_PartyStart(self.img.base, self.now)
        for label, setup, code in (
                ('summon', lambda: self.img.p32(self.world.battle + 0x5B04, 1), MODE),
                ('frontend menu', lambda: self.world.menu(True), NOT_FIELD),
                ('field not ready', lambda: self.d.ILCT_PartySetStubs(1, self.leader_word, 0), NOT_FIELD)):
            with self.subTest(label):
                ticket = self.queued(1, 0)
                setup()
                self.d.ILCT_GtqDrain()
                self.assertEqual(self.d.ILCT_GtqResult(ticket, None), code)
                self.assertEqual(self.calls()[0], 0)
                self.img.p32(self.world.battle + 0x5B04, 0)
                self.world.menu(False)
                self.d.ILCT_PartySetStubs(1, self.leader_word, 1)
        self.assertEqual(self.members(), [0, 5, 1])

    def test_a_stale_target_is_refused_before_the_setter(self):
        ticket = self.queued(1, 0)
        self.actors[0].set_hp(0)          # Player 1's character falls before the frame runs
        self.d.ILCT_GtqDrain()
        self.assertIn(self.d.ILCT_GtqResult(ticket, None), (UNAVAILABLE, P1_UNSTABLE))
        self.assertEqual(self.calls()[0], 0)

    def test_a_setter_that_does_not_stick_is_reported(self):
        self.d.ILCT_PartySetterOverride(0)
        ticket = self.queued(1, 0)
        self.d.ILCT_GtqDrain()
        self.assertEqual(self.d.ILCT_GtqResult(ticket, None), SETTER_FAILED)
        self.assertIsNone(self.remap())

    def test_a_layout_change_between_plan_and_frame_rejects_then_retries(self):
        self.queued(1, 3)
        self.world.layout([0, 5, 1, 2])       # Balthier left; Fran in slot 3
        self.d.ILCT_GtqDrain()
        self.beat(3)
        self.assertEqual(self.debug().seat[1].state, REQ['REFUSED'])
        self.assertEqual(self.debug().seat[1].last_result, NOT_IN_PARTY)
        self.assertEqual(self.calls()[0], 0)

    def test_a_request_waits_out_a_cutscene_then_runs(self):
        self.world.control_enabled(False)
        self.d.ILCT_PartyRequest(1, 3)
        self.beat(5)
        self.assertEqual(self.debug().seat[1].state, REQ['WAITING'])
        self.world.control_enabled(True)
        self.beat(3)
        self.assertEqual(self.members(), [0, 3, 1])

    def test_a_request_that_never_can_run_times_out(self):
        self.world.control_enabled(False)
        self.d.ILCT_PartyRequest(1, 3)
        self.beat(5000 // 20 + 2)
        self.assertEqual(self.debug().seat[1].state, REQ['TIMED_OUT'])

    def test_the_remap_is_retired_once_steering_names_the_new_body(self):
        self.d.ILCT_PartyRequest(1, 0)
        self.beat()                      # job applied
        seq = self.remap().seq
        self.beat(party_step=False, drain=False)  # the session adopts
        self.steer_from_session()        # movement publishes from the adopted record
        self.d.ILCT_PartyStep(self.now)
        self.assertEqual(self.debug().remap_retired, seq)
        self.assertEqual(self.d.ILCT_PartyRemapPending(), 0)
        self.assertEqual(self.stats().remaps_retired, 1)

    def test_an_unconfirmed_remap_expires(self):
        self.d.ILCT_PartyRequest(1, 0)
        self.beat()
        self.beat(15)                    # nothing publishes a steering record
        self.assertEqual(self.d.ILCT_PartyRemapPending(), 0)
        self.assertEqual(self.stats().remaps_expired, 1)


class WatcherTest(TwinCase):
    def test_a_cycle_onto_a_guests_character_swaps_player_one_and_that_guest(self):
        self.d.ILCT_PartyTickBefore()       # follows the leader
        self.world.leader(5)                # Companions' cycle in the field tick
        self.steer_from_session()
        self.d.ILCT_PartyTickBefore()
        m = self.remap()
        self.assertEqual((m.mask, m.slot[0].actor), (0b01, self.actors[0].actor))
        self.assertEqual(self.mv05(self.actors[0].actor), RESUME)
        self.assertEqual(self.mv05(self.actors[1].actor), RUN_ORIGINAL)
        self.assertEqual(self.calls()[0], 0, 'the watcher never writes the leader')
        self.beat(drain=False)
        self.assertEqual(self.members(), [5, 0, 1], 'adopted at the next step')
        c = self.stats()
        self.assertEqual((c.watcher_changes, c.external_swaps), (1, 1))

    def test_a_ko_promotion_is_not_a_swap(self):
        self.d.ILCT_PartyTickBefore()
        self.actors[0].set_hp(0)
        self.world.leader(5)
        self.d.ILCT_PartyTickBefore()
        self.assertIsNone(self.remap())
        self.assertEqual(self.stats().ko_promotions, 1)

    def test_a_cycle_onto_an_uncontrolled_member_is_left_to_the_settled_adoption(self):
        self.d.ILCT_PartyTickBefore()
        self.world.leader(3)
        self.d.ILCT_PartyTickBefore()
        self.assertIsNone(self.remap())
        self.assertEqual(self.debug().posted_seq, 0)
        self.assertEqual(self.stats().external_left, 1)
        self.beat(2, drain=False)
        self.assertEqual(self.members(), [0, 5, 1], 'not before the settle')
        self.beat(12, drain=False)
        self.assertEqual(self.members(), [3, 5, 1])

    def test_a_summon_moves_the_leader_without_a_swap(self):
        self.d.ILCT_PartyTickBefore()
        self.img.p32(self.world.battle + 0x5B04, 1)
        self.world.leader(5)
        self.d.ILCT_PartyTickBefore()
        self.assertIsNone(self.remap())
        self.assertEqual(self.stats().external_swaps, 0)

    def test_our_own_leader_write_is_not_an_external_change(self):
        self.d.ILCT_PartyTickBefore()
        self.d.ILCT_PartyRequest(1, 0)
        self.beat()
        self.d.ILCT_PartyTickAfter()
        self.d.ILCT_PartyTickBefore()
        self.assertEqual((self.stats().watcher_changes, self.stats().applied), (0, 1))

    def test_the_after_handler_sees_a_write_inside_the_stock_tick(self):
        self.d.ILCT_PartyTickBefore()
        self.world.leader(1)                # a menu commit inside the stock UI tick, onto P3's Ashe
        self.d.ILCT_PartyTickAfter()
        self.assertEqual(self.stats().external_swaps, 1)
        self.assertEqual(self.remap().slot[1].actor, self.actors[0].actor)

    def test_the_ui_tick_asks_for_the_after_handler_only_while_party_may_act(self):
        epoch = self.d.ILCT_SesEpoch()
        self.assertEqual(self.d.ILCT_PartyAfterWanted(), 1)
        self.assertEqual(self.d.ILCT_TickRun(epoch), CALL_AROUND)
        self.d.ILCT_PatchApplySubsystems(0xFF & ~PARTY_BIT)
        self.assertEqual(self.d.ILCT_TickRun(epoch), RUN_ORIGINAL)
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_PatchSetMode(gt.DRY)
        self.assertEqual(self.d.ILCT_TickRun(epoch), RUN_ORIGINAL)
        self.d.ILCT_PatchSetMode(gt.LIVE)

    def test_the_ui_tick_runs_the_watcher_before_the_drain(self):
        epoch = self.d.ILCT_SesEpoch()
        self.d.ILCT_TickRun(epoch)
        self.world.leader(5)
        self.steer_from_session()
        self.d.ILCT_TickRun(epoch)
        self.assertEqual(self.stats().external_swaps, 1)

    def test_a_town_cycle_onto_a_member_without_a_body_rebuilds_in_the_same_callback(self):
        # A Full Party town filtered to the controlled members: Balthier (3) has no body.
        self.img.put_global(TOWN_ARM, tw.FULL_PARTY_ARM)
        areas = self.world.areas
        self.img.p8(areas + 9 + 3 * 7, 0x80)
        self.d.ILCT_TownSetRematerialize(1, self.img.va(TASK_PTR), 0x4242)
        self.d.ILCT_PartyTickBefore()
        self.world.layout([0, 5, 1, NONE], [0, 5, 1, NONE])
        self.world.leader(3)
        self.d.ILCT_PartyTickBefore()
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 1)
        self.assertEqual(self.stats().town_rebuilds, 1)
        # Not in a town: nothing is called.
        self.img.put_global(TASK_PTR, bytes(8))
        self.img.p8(areas + 9 + 3 * 7, 0)
        self.world.leader(0)
        self.d.ILCT_PartyTickBefore()
        self.world.leader(2)
        self.d.ILCT_PartyTickBefore()
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 1)


class TownTakeTest(TwinCase):
    """A Full Party town filtered to the controlled members: Balthier (3) is in the roster (default layout) but has no
    body (empty current and HUD slot). Player 1's leader change and a guest's take-free onto him are planned against
    the roster; the operation writes the leader (P1) or lets TW-01 keep him (a guest), runs the town rebuild and
    steers the body it bound, in one callback. The rebuild stub here runs the builder's layout step and the real
    TW-01 handler."""

    STOCK_ARM = bytes([0x83, 0xE8, 0x01, 0x0F, 0x84, 0x82, 0x01, 0x00, 0x00])
    RECORD = 0x1C8   # member record stride (block + 8 + id * 0x1C8)

    def setUp(self):
        super().setUp()
        d, img, w = self.d, self.img, self.world
        img.put_global(TOWN_ARM, tw.FULL_PARTY_ARM)
        img.p8(w.areas + 9 + 3 * 7, 0x80)                     # location 7 is a town
        img.put(w.battle + 0x5A48, struct.pack('<9H', 0, 5, 1, 3, *([NONE] * 5)))  # the roster
        w.layout([0, 5, 1, NONE], [0, 5, 1, NONE])           # TW-01 hid Balthier
        self.member_record(3, hp=812)
        d.ILCT_TownSetRematerialize(1, img.va(TASK_PTR), 0x4242)
        d.ILCT_TownRebuildBinds(w.battle + 0x5A48)
        d.ILCT_TownStart(img.base, self.now)
        self.beat(2, drain=False)
        self.assertEqual(self.members(), [0, 5, 1], 'instrument: the seats as before')

    def tearDown(self):
        self.d.ILCT_TownRebuildBinds(0)
        self.d.ILCT_TownStop()
        super().tearDown()

    def member_record(self, member, hp, status=0):
        record = self.world.battle + 8 + member * self.RECORD
        self.img.p32(record + 0x48, hp)
        self.img.p32(record + 0x64, status)
        self.img.p32(record + 0x3C, 0)

    def beat(self, n=1, drain=True, party_step=True):
        for _ in range(n):
            self.now += 20
            self.d.ILCT_SetFixedClock(self.now)
            self.d.ILCT_SesStep(self.now)
            self.d.ILCT_PatchHeartbeat(self.d.ILCT_SesEpoch())
            self.d.ILCT_TownStep(self.now)
            if party_step:
                self.d.ILCT_PartyStep(self.now)
            if drain:
                self.d.ILCT_GtqDrain()

    def current(self):
        return list(struct.unpack('<4H', ctypes.string_at(self.world.battle + 0x5A5A, 8)))

    def test_a_guest_takes_an_unseated_town_member_in_one_callback(self):
        self.d.ILCT_PartyRequest(1, 3)       # P2 asks for Balthier, who has no body in this town
        self.beat(drain=False)
        self.assertEqual(self.debug().seat[1].state, REQ['QUEUED'], 'planned against the roster, not refused')
        self.d.ILCT_GtqDrain()
        epoch = self.d.ILCT_SesEpoch()
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 1, 'the rebuild ran in the same callback')
        self.assertEqual(self.current(), [0, 5, 1, 3], 'TW-01 kept Balthier: the body is bound')
        self.assertEqual(self.d.ILCT_TownPendingAllowed(epoch), 1 << 3, 'kept through the pending word')
        self.assertEqual(self.calls()[0], 0, 'no leader write')
        m = self.remap()
        self.assertEqual((m.mask, m.slot[0].actor, m.slot[0].member), (0b01, self.actors[3].actor, 3),
                         "P2 steers the body the rebuild bound")
        self.assertEqual(self.stats().town_takes, 1)
        self.img.put_global(TASK_PTR, bytes(8))   # the streaming task finished
        self.beat(drain=False)
        self.assertEqual(self.members(), [0, 3, 1], 'adopted at the next step')
        self.assertEqual(self.d.ILCT_TownPendingAllowed(epoch), 0, 'the record names him now: the word is gone')
        self.assertEqual(self.debug().seat[1].state, REQ['DONE'])

    def test_player_one_leads_an_unseated_town_member_in_one_callback(self):
        self.d.ILCT_PartyRequest(0, 3)
        self.beat()
        self.assertEqual(self.calls()[:2], [1, 3], 'the leader setter once, with Balthier')
        self.assertEqual(self.leader(), 3)
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 1)
        self.assertEqual(self.current(), [0, 5, 1, 3], 'TW-01 keeps the new leader')
        self.assertIsNone(self.remap(), 'no guest changed hands')
        self.img.put_global(TASK_PTR, bytes(8))
        self.beat()
        self.assertEqual(self.members(), [3, 5, 1])
        self.assertEqual(self.stats().watcher_changes, 0, 'our own write is not an external change')

    def ui_beat(self, n=1):
        """A co-op beat, then the game's UI tick as it runs: the watcher, the drain, the watcher's second look."""
        for _ in range(n):
            self.beat(drain=False)
            self.d.ILCT_PartyTickBefore()
            self.d.ILCT_GtqDrain()
            self.d.ILCT_PartyTickAfter()

    def test_a_rebuild_that_does_not_run_undoes_the_leader_write_and_retries_after_a_wait(self):
        self.d.ILCT_TownSetRematerialize(2, 0, 0)  # the game's task pool is full: nothing is rebuilt
        self.d.ILCT_PartyRequest(0, 3)
        self.ui_beat()
        ticket = self.debug().seat[0].ticket
        self.assertEqual(self.d.ILCT_GtqResult(ticket, None), TOWN_REBUILD)
        self.assertEqual(self.calls()[:2], [2, 0], 'written, then put back to Vaan in the same callback')
        self.assertEqual(self.leader(), 0)
        self.assertIsNone(self.remap())
        self.assertEqual((self.stats().refused, self.stats().town_takes), (1, 0))
        self.assertEqual(self.debug().watcher_leader, 0, 'the undo read back: the watcher follows Vaan')
        self.ui_beat()
        self.assertEqual(self.stats().watcher_changes, 0, 'the beat after the undo sees no external change')
        self.assertEqual(self.debug().seat[0].town_fails, 1)
        # No retry on every beat: the request waits TOWN_RETRY_MS before it is planned again.
        self.ui_beat(TOWN_RETRY_MS // 20 - 2)
        self.assertEqual(self.calls()[0], 2, 'no setter call while it backs off')
        self.assertEqual(self.debug().seat[0].state, REQ['WAITING'])
        self.d.ILCT_TownSetRematerialize(1, self.img.va(TASK_PTR), 0x4242)
        self.ui_beat(3)
        self.assertEqual(self.calls()[0], 3, 'tried again once the wait ended, and it ran')
        self.assertEqual(self.leader(), 3, 'the retry ran once the rebuild could')
        self.assertEqual(self.stats().town_takes, 1)
        self.assertEqual(self.stats().watcher_changes, 0)

    def test_a_rebuild_that_never_runs_ends_the_request_after_its_tries(self):
        self.d.ILCT_TownSetRematerialize(2, 0, 0)
        self.d.ILCT_PartyRequest(0, 3)
        self.ui_beat(REQUEST_WAIT_MS // 20 + 10)   # past the whole request window
        v = self.debug().seat[0]
        self.assertEqual((v.state, v.last_result, v.town_fails), (REQ['REFUSED'], TOWN_REBUILD, TOWN_TRIES))
        self.assertEqual(self.calls()[0], 2 * TOWN_TRIES, 'each try is one write and its undo, nothing more')
        self.assertEqual(self.d.ILCT_TownRematerializeCalls(), TOWN_TRIES)
        self.assertEqual(self.leader(), 0)
        self.assertEqual((self.stats().requests_refused, self.stats().watcher_changes), (1, 0))
        # Instrument: a new request is planned at once (the count belongs to the request).
        self.d.ILCT_TownSetRematerialize(1, self.img.va(TASK_PTR), 0x4242)
        self.d.ILCT_PartyRequest(0, 3)
        self.ui_beat()
        self.assertEqual(self.leader(), 3)

    def test_an_undo_that_does_not_read_back_makes_the_watcher_start_over(self):
        self.d.ILCT_TownSetRematerialize(2, 0, 0)
        self.d.ILCT_PartySetterOverrideFrom(7, 2)   # the second setter call (the undo) leaves 7, not Vaan
        self.d.ILCT_PartyRequest(0, 3)
        self.beat(drain=False)
        self.d.ILCT_PartyTickBefore()
        self.assertEqual(self.debug().watcher_leader, 0, 'instrument: the watcher knows Vaan before the job')
        self.d.ILCT_GtqDrain()
        self.assertEqual(self.calls()[:2], [2, 0])
        self.assertEqual(self.leader(), 7, 'instrument: the undo did not take')
        self.assertEqual(self.debug().watcher_leader, 0xFFFF, 'the watcher forgot what it knew')
        changes = self.stats().watcher_changes
        self.d.ILCT_PartyTickAfter()
        self.assertEqual(self.debug().watcher_leader, 7, 'it follows what it reads next')
        self.ui_beat()
        self.assertEqual(self.stats().watcher_changes, changes, 'learned again, not taken as an external change')

    def test_a_task_in_flight_waits_before_anything_is_written(self):
        self.img.put_global(TASK_PTR, struct.pack('<Q', 0x77))
        self.d.ILCT_PartyRequest(1, 3)
        self.beat()
        ticket = self.debug().seat[1].ticket
        self.assertEqual(self.d.ILCT_GtqResult(ticket, None), TOWN_BUSY)
        self.beat(2)
        self.assertEqual((self.d.ILCT_TownRematerializeCalls(), self.calls()[0]), (0, 0))
        self.assertEqual(self.d.ILCT_TownPendingAllowed(self.d.ILCT_SesEpoch()), 0, 'nothing kept for him either')
        self.assertIsNone(self.remap())
        self.assertNotEqual(self.debug().seat[1].state, REQ['REFUSED'], 'it waits')
        self.img.put_global(TASK_PTR, bytes(8))
        self.beat(2)
        self.assertEqual(self.remap().slot[0].actor, self.actors[3].actor)

    def test_refusals_that_stay(self):
        cases = (
            ('not in the roster at all', lambda: None, 2, NOT_IN_PARTY),
            ('cannot lead (KO in his record)', lambda: self.member_record(3, hp=0), 3, UNAVAILABLE),
            ('stock town arm (no Party In Towns)', lambda: self.img.put_global(TOWN_ARM, self.STOCK_ARM), 3,
             NOT_IN_PARTY),
        )
        for label, prepare, target, code in cases:
            with self.subTest(label):
                prepare()
                self.d.ILCT_PartyRequest(1, target)
                self.beat(3)
                v = self.debug().seat[1]
                self.assertEqual((v.state, v.last_result), (REQ['REFUSED'], code), label)
                self.assertEqual(self.d.ILCT_TownRematerializeCalls(), 0)
                self.member_record(3, hp=812)
                self.img.put_global(TOWN_ARM, tw.FULL_PARTY_ARM)


# ---- unicorn: the UT-01 after-handler on the image twin ----------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class Ut01AfterHandlerEmulationTest(TwinCase):
    """The game's own caller 0x28E46A..0x28E485 (load the UI manager, test its tick bit, `mov rcx,rbx`, the UT-01
    call to 0x290CF0) on the hooked twin, with the session and party world of TwinCase mapped into the emulator.
    With PARTY live and LIVE the UT-01 handler returns CALL_AROUND: the callee is entered once, from inside our stub,
    with rcx = rbx; the after-handler runs once after it; the machine state at 0x28E485 is the stock run's. With PARTY
    clear or in DRY it is the old tail jump (the callee returns straight to 0x28E485) and no after-handler."""

    START, END = 0x28E46A, 0x28E485
    CALLEE, EARLY_RET = 0x290CF0, 0x290DE3      # the UI manager tick, up to its early-return `ret` (inclusive)
    UI_PTR, TICK_FLAGS = 0x209BE80, 0x1E0C464    # globals the caller reads (RIP-relative)
    UI_OBJECT = pe.SCRATCH + 0x300000
    NONVOLATILE = ('RBX', 'RBP', 'RSI', 'RDI', 'R12', 'R13', 'R14', 'R15', 'RSP')

    def tw(self, va):
        return TWIN_BASE + va - 0x120000

    def map_world(self, uc):
        """The synthetic image's committed globals and heap, at the same addresses as in this process."""
        pages = set(self.img.committed)
        top = (self.img.top + pe.PAGE - 1) & ~(pe.PAGE - 1)
        pages.update(range(self.img.heap, top, pe.PAGE))
        for page in sorted(pages):
            if page not in self.emu.lazy and page not in self.mapped:
                uc.mem_map(page, pe.PAGE)
                self.mapped.add(page)
            uc.mem_write(page, ctypes.string_at(page, pe.PAGE))

    def run_case(self, early_return, original):
        emu, uc, X = self.emu, self.emu.uc, pe.X
        row = self.m.w.ut01
        r = self.m.twin.rows[row]
        site = TWIN_BASE + r['rva']
        emu.refresh()
        emu.tick_clock(self.now + 5)
        self.map_world(uc)
        uc.mem_write(self.tw(self.UI_PTR), struct.pack('<Q', self.UI_OBJECT))
        uc.mem_write(self.tw(self.TICK_FLAGS), struct.pack('<I', 0x2000))
        uc.mem_write(self.UI_OBJECT + 0x10F68, struct.pack('<I', 0x2000 if early_return else 0))
        entries = []
        ret_flags = []
        callee = self.tw(self.CALLEE)
        ret_at = self.tw(self.EARLY_RET - 1)   # the early return's `ret`

        def at_ret(u):
            ret_flags.append(u.reg_read(X.UC_X86_REG_RFLAGS) & pe.FLAG_MASK)

        def at_callee(u):
            entries.append((u.reg_read(X.UC_X86_REG_RCX), u.reg_read(X.UC_X86_REG_RBX),
                            struct.unpack('<Q', bytes(u.mem_read(u.reg_read(X.UC_X86_REG_RSP), 8)))[0]))
            if not early_return:   # the stub callee: a menu commit inside the stock tick, onto P2's Penelo
                u.mem_write(self.leader_word, struct.pack('<I', 5))
        emu.answers[callee] = at_callee
        emu.answers[ret_at] = at_ret
        patched = bytes(uc.mem_read(site, 5))
        if original:
            emu.write_code(site, checked(r)[:5])
        lo, hi = self.tw(self.START), self.tw(self.END)
        body = (callee, self.tw(self.EARLY_RET) + 1) if early_return else (0, 0)

        def allowed(x):
            return not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or lo <= x < hi or body[0] <= x < body[1]
        state = pe.random_state(random.Random(0x7701 + early_return))
        state['gpr']['RSP'] &= ~0xF
        counters, posted = self.d.ILCT_PartyAddress(0), self.d.ILCT_PartyAddress(1)
        try:
            out = emu.run(lo, allowed, state, probe=lambda u: (
                struct.unpack(f'<{len(COUNTER_NAMES)}Q', bytes(u.mem_read(counters, 8 * len(COUNTER_NAMES)))),
                struct.unpack('<I', bytes(u.mem_read(posted, 4)))[0]))
        finally:
            emu.write_code(site, patched)
            emu.answers.pop(callee, None)
            emu.answers.pop(ret_at, None)
        self.assertEqual(emu.unexpected, [], f'unexpected imports: {emu.unexpected}')
        out['entries'] = entries
        out['ret_flags'] = ret_flags
        out['counters'] = dict(zip(COUNTER_NAMES, out['probe'][0]))
        out['posted'] = out['probe'][1]
        return out

    STACK_FREE_FLAGS = 0x1 | 0x40 | 0x80 | 0x400 | 0x800  # CF ZF SF DF OF

    def assert_same_machine(self, a, b, label):
        """rsp, the non-volatile GPRs and XMM6-15 equal the stock run's at 0x28E485. Flags: a stub callee returns at
        once, so they must be equal bit for bit. The real 0x290CF0 ends with `add rsp,0x30`; under CALL_AROUND it runs
        deeper on the stack, so PF/AF of that add differ by construction (status flags are volatile across a call
        and 0x28E485 reads none). There the flags at 0x28E485 must be exactly what the callee returned, and every
        stack-independent bit equal to the stock run's."""
        self.assertEqual(b['exit'], self.tw(self.END), f'{label}: continues at 0x28E485')
        self.assertEqual(a['exit'], b['exit'])
        for name in self.NONVOLATILE:
            self.assertEqual(a['gpr'][name], b['gpr'][name], f'{label}: {name}')
        self.assertEqual(a['xmm'][6:], b['xmm'][6:], f'{label}: XMM6-15')
        if not b['ret_flags']:
            self.assertEqual(a['rflags'], b['rflags'], f'{label}: flags')
        else:
            self.assertEqual((a['ret_flags'], b['ret_flags']), ([a['rflags']], [b['rflags']]),
                             f"{label}: the callee's own flags reach 0x28E485 unchanged")
            self.assertEqual(a['rflags'] & self.STACK_FREE_FLAGS, b['rflags'] & self.STACK_FREE_FLAGS,
                             f'{label}: stack-independent flags')

    def test_the_ui_tick_call_runs_the_after_handler_once_and_keeps_the_machine_state(self):
        self.emu = mv.MoveEmulator(self.m.twin)
        self.mapped = set()
        self.d.ILCT_PartyTickBefore()   # the watcher follows Vaan
        before = self.stats()
        posted0 = self.debug().posted_seq
        cont = self.tw(self.END)
        for early_return in (True, False):
            label = 'real 0x290CF0, early return' if early_return else 'stub callee writes the leader'
            with self.subTest(label, path='CALL_AROUND'):
                a = self.run_case(early_return, original=True)
                b = self.run_case(early_return, original=False)
                self.assertEqual(len(a['entries']), 1, 'instrument: the stock call enters the callee once')
                self.assertEqual(len(b['entries']), 1, 'the callee is entered exactly once')
                rcx, rbx, back = b['entries'][0]
                self.assertEqual((rcx, rbx), (self.UI_OBJECT, self.UI_OBJECT), 'entered with rcx = rbx')
                self.assertNotEqual(back, cont, 'called from inside our stub (CALL_AROUND), not tail-jumped')
                self.assertEqual(a['entries'][0][2], cont)
                self.assert_same_machine(a, b, label)
                delta = {k: b['counters'][k] - getattr(before, k) for k in COUNTER_NAMES}
                self.assertEqual(delta['watcher_after'], 1, 'the after-handler ran once')
                if early_return:
                    self.assertEqual((delta['external_swaps'], delta['remaps'], b['posted']), (0, 0, posted0))
                else:
                    self.assertEqual((delta['watcher_changes'], delta['external_swaps'], delta['remaps']), (1, 1, 1),
                                     'the after-handler committed the swap: remap')
                    self.assertNotEqual(b['posted'], posted0, '... and the session post')
        for label, prepare, undo in (
                ('PARTY clear', lambda: self.d.ILCT_PatchApplySubsystems(0xFF & ~PARTY_BIT),
                 lambda: self.d.ILCT_PatchApplySubsystems(0xFF)),
                ('DRY', lambda: self.d.ILCT_PatchSetMode(gt.DRY), lambda: self.d.ILCT_PatchSetMode(gt.LIVE))):
            prepare()
            try:
                for early_return in (True, False):
                    with self.subTest(label, early_return=early_return, path='TAIL'):
                        a = self.run_case(early_return, original=True)
                        b = self.run_case(early_return, original=False)
                        self.assertEqual(len(b['entries']), 1)
                        self.assertEqual(b['entries'][0][2], cont, 'the old tail jump: the callee returns to 0x28E485')
                        self.assert_same_machine(a, b, label)
                        self.assertEqual(b['counters']['watcher_after'], before.watcher_after, 'no after-handler')
                        self.assertEqual(b['counters']['external_swaps'], before.external_swaps)
            finally:
                undo()


# ---- the Mod Menu's move-seat command ----------------------------------------------------------------------------
@native_test
class MoveSeatCommandTest(unittest.TestCase):
    def test_the_command_reaches_the_party_subsystem(self):
        from test_coop_input_actions import Rig, S as SCHEMA
        d = declare(fresh_test_dll())
        rig = Rig(d)
        try:
            d.ILCT_PartyReset()
            d.ILCT_SesReset()
            rig.begin()
            rig.step(0)
            d.ILCT_PartyStart(0x120000, 0)
            d.ILCT_PartyStep(0)                     # seeds the commands seen so far: nothing replayed
            status, _request = rig.command('MOVE_SEAT_NOW', 2, SCHEMA.e('character', 'BALTHIER'))
            self.assertEqual(status, 0)
            rig.step(8)
            d.ILCT_PartyStep(20)
            v = DebugView()
            d.ILCT_PartyDebug(v)
            self.assertEqual((v.seat[1].state, v.seat[1].target), (REQ['WAITING'], 3),
                             'queued for the session: waits for a world, then plans')
            c = Counters()
            d.ILCT_PartyStats(c)
            self.assertEqual(c.requests, 1)
        finally:
            d.ILCT_PartyReset()
            rig.end()


if __name__ == '__main__':
    unittest.main()
