"""Host tests for the co-op session core: world epochs and lifecycle gates, fault-safe actor resolution, the seat
table (capped at the active-seat limit), ownership hand-off and the published session record.

No game run, nothing deployed, no game file read. Every world here is a synthetic memory image built in this process
(coop_session_support.Image): the module's real readers walk it through the fault-safe probe, including pages made
unreadable on purpose. The pure rules are driven as tables through the test DLL's ILCT_* exports.
"""
from __future__ import annotations

import ctypes
import unittest
from pathlib import Path

from coop_session_support import (ACTION_FOLLOW, ACTION_IDLE, CHAR_AUTO, CHAR_NONE, FACT, FX, GATE, KO_CLASS,
                                  LAYOUTS, LEADER_CLASS, NO, NONE, PAGE, PAGE_GUARD, PAGE_NOACCESS, PAGE_READWRITE,
                                  PARTY_CLASS, PHASE, SEATF, ST_BERSERK, ST_CONFUSE, ST_DISABLE, ST_IMMOBILIZE, ST_KO,
                                  ST_SLEEP, ST_STONE, ST_STOP, ST_XZONE, UNKNOWN, WHY, YES, Counters, DebugView,
                                  EffectList, GameWorld, Generations, Identity, Image, Ownership, PartyActor,
                                  PartyActors, PendingEffect, Reacquire, Record, Sample, SeatConfig, SteerFacts, Token,
                                  WorldEpochs, WorldSample, WorldView, i8, identity, layout4, native_test,
                                  session_dll, u8, u16, u32)
from test_coop_patch_engine import untagged_constants

ROOT = Path(__file__).resolve().parents[1]
SESSION_SRC = ROOT / 'native' / 'coop' / 'src' / 'session'


def tri(v):
    return {None: UNKNOWN, False: NO, True: YES}.get(v, v)


class DllCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = session_dll()


# =====================================================================================================
# Layouts and source rules
# =====================================================================================================
@native_test
class LayoutTest(DllCase):
    def test_struct_mirrors_match_the_module(self):
        sizes = (u32 * 64)()
        n = self.d.ILCT_SesSizes(sizes, 64)
        self.assertEqual(n, len(LAYOUTS))
        for k, cls in enumerate(LAYOUTS):
            self.assertEqual(sizes[k], ctypes.sizeof(cls), cls.__name__)

    def test_every_global_in_the_session_sources_is_tagged_as_a_va(self):
        # The patch layer's rule, applied to the session sources: an address constant at or above the image size
        # must say it is a VA (or that it is not an address).
        found = [f'{path.name}:{number}: {literal}'
                 for path in sorted(SESSION_SRC.glob('*.[hc]*'))
                 for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
                 for literal in untagged_constants(line)]
        self.assertEqual(found, [])

    def test_seat_arrays_never_exceed_the_active_seat_cap(self):
        text = (SESSION_SRC / 'policy.h').read_text(encoding='utf-8')
        self.assertIn('ACTIVE_SEATS = ILC_ACTIVE_SEATS_MAX', text)
        abi = (ROOT / 'native' / 'coop' / 'include' / 'ilc' / 'bridge_abi.h').read_text(encoding='utf-8')
        self.assertRegex(abi, r'X\(ACTIVE_SEATS_MAX, 3u,')


# =====================================================================================================
# Lifecycle gates (restated tables: KO keeps the session; per-guest availability; shared camera needs two roles)
# =====================================================================================================
@native_test
class LifecycleGateTest(DllCase):
    def flags(self, enabled, p1, p2, p3=None, present2=True, present3=None):
        if present3 is None:
            present3 = p3 is not None
        alive = (i8 * 3)(tri(p2), tri(p3), UNKNOWN)
        present = (u8 * 3)(int(present2), int(present3), 0)
        bits = self.d.ILCT_SesLifecycle(int(enabled), tri(p1), alive, present, 2)
        return bool(bits & 1), bool(bits & 2), bool(bits & 4)

    def test_table(self):
        rows = [
            # enabled, P1, P2, P3, P2 present, P3 present -> logical, movement, shared camera
            ((True, True, True), (True, True, True)),
            ((True, False, True), (True, True, False)),     # P1 down: P2 still moves, one role cannot be framed
            ((True, True, False), (True, False, False)),    # the only guest down: no guest movement
            ((True, None, True), (True, True, False)),      # P1 unknown: never counted as a camera role
            ((False, True, True), (False, False, False)),   # co-op off wins
            ((True, True, False, True), (True, True, True)),    # P3 keeps movement and camera with P2 down
            ((True, True, False, False), (True, False, False)),
            ((True, False, True, True), (True, True, True)),    # two live guests frame the camera with P1 down
            ((True, False, True, False), (True, True, False)),
        ]
        for args, want in rows:
            with self.subTest(args=args):
                self.assertEqual(self.flags(*args), want)

    def test_unknown_guest_status_is_not_a_departure(self):
        self.assertEqual(self.flags(True, True, False, None, present2=True, present3=True), (True, True, True))


@native_test
class SteerRuleTest(DllCase):
    def steers(self, enabled=True, alive=True, leader=0, member=5, actor_present=True, identity_current=True,
               layout_coherent=True, allow_leader=False):
        f = SteerFacts(int(enabled), tri(alive), int(actor_present), int(identity_current), int(layout_coherent),
                       int(allow_leader), member, leader)
        return bool(self.d.ILCT_SesSteers(ctypes.byref(f)))

    def test_table(self):
        rows = [
            ({}, True),                                    # a live guest that is not the leader
            ({'leader': 5}, False),                        # the guest's character leads: the game drives it
            ({'alive': False}, False),
            ({'identity_current': False}, False),
            ({'leader': 5, 'allow_leader': True}, True),   # a KO promotion keeps the guest steering on purpose
            ({'alive': None, 'actor_present': False}, False),
            ({'alive': None}, True),                       # one torn status sample keeps the owner
            ({'layout_coherent': False}, False),
            ({'member': NONE}, False),
            ({'enabled': False}, False),
            ({'member': 1}, True),                         # P3's character is independent of P2's state
        ]
        for kw, want in rows:
            with self.subTest(kw=kw):
                self.assertEqual(self.steers(**kw), want)

    def test_master_stays_armed_while_any_guest_steers(self):
        p2 = self.steers(alive=None, actor_present=False)
        p3 = self.steers(member=1)
        self.assertEqual((p2, p3), (False, True))
        self.assertTrue(self.d.ILCT_SesGuestsMaster(1, 0b100))
        self.assertFalse(self.d.ILCT_SesGuestsMaster(1, 0b001))  # P1 is never a follower
        self.assertFalse(self.d.ILCT_SesGuestsMaster(0, 0b110))


@native_test
class GapRetentionTest(DllCase):
    def test_mapping_gap_keeps_only_the_same_manual_owner(self):
        base = dict(mapping_unknown=1, coop=1, manual=1, owner_member=5, owner_epoch=7, seat_member=5, leader=0,
                    world_epoch=7, allow_leader=0)
        rows = [
            ({}, True),
            ({'manual': 0}, False),        # switching a guest off wins over an unknown mapping
            ({'coop': 0}, False),          # co-op off wins
            ({'mapping_unknown': 0}, False),
            ({'owner_member': NONE}, False),
            ({'owner_epoch': 6}, False),   # an owner from another world
            ({'seat_member': 1}, False),   # the seat now names another character
            ({'leader': 5}, False),
            ({'leader': 5, 'allow_leader': 1}, True),
        ]
        for change, want in rows:
            with self.subTest(change=change):
                a = base | change
                self.assertEqual(bool(self.d.ILCT_SesKeepMapGap(a['mapping_unknown'], a['coop'], a['manual'],
                                                                a['owner_member'], a['owner_epoch'],
                                                                a['seat_member'], a['leader'], a['world_epoch'],
                                                                a['allow_leader'])), want)

    def test_status_gap_keeps_only_the_exact_current_owner(self):
        base = dict(alive=UNKNOWN, coop=1, manual=1, owner_member=5, owner_epoch=7, same=1, seat_member=5, leader=0,
                    world_epoch=7, allow_leader=0)
        rows = [
            ({}, True),
            ({'alive': NO}, False),       # a coherent sample decides by itself
            ({'alive': YES}, False),
            ({'same': 0}, False),         # another candidate incarnation
            ({'manual': 0}, False),       # switching off wins over an unknown status
            ({'coop': 0}, False),
        ]
        for change, want in rows:
            with self.subTest(change=change):
                a = base | change
                self.assertEqual(bool(self.d.ILCT_SesKeepStatusGap(a['alive'], a['coop'], a['manual'],
                                                                   a['owner_member'], a['owner_epoch'], a['same'],
                                                                   a['seat_member'], a['leader'], a['world_epoch'],
                                                                   a['allow_leader'])), want)


@native_test
class LayoutCacheTest(DllCase):
    def both(self, member, current, ui):
        return bool(self.d.ILCT_SesMemberInBoth(member, (u16 * 4)(*current), (u16 * 4)(*ui)))

    def survives(self, changed, member, current, ui, valid):
        return bool(self.d.ILCT_SesCacheSurvives(int(changed), member, (u16 * 4)(*current), (u16 * 4)(*ui),
                                                 tri(valid)))

    def test_per_guest_coherence(self):
        current = (0, 5, 1, NONE)
        for ui, p2, p3 in (((0, NONE, 1, NONE), False, True), ((0, 5, NONE, NONE), True, False)):
            with self.subTest(ui=ui):
                self.assertEqual((self.both(5, current, ui), self.both(1, current, ui)), (p2, p3))

    def test_cache_survival(self):
        layout = (5, 0, 1, NONE)
        self.assertTrue(self.survives(False, 1, layout, layout, None))     # unchanged seat across a P1/P2 swap
        for changed, cur, valid in ((True, layout, None), (False, (5, 0, NONE, NONE), None), (False, layout, False)):
            with self.subTest(changed=changed, cur=cur, valid=valid):
                self.assertFalse(self.survives(changed, 1, cur, cur, valid))
        for member, ui in ((5, (0, NONE, 1, NONE)), (1, (0, 5, NONE, NONE))):
            with self.subTest(member=member):
                self.assertFalse(self.survives(False, member, (0, 5, 1, NONE), ui, None))


@native_test
class MovementCameraRuleTest(DllCase):
    def test_custom_wait_menu_ownership_vs_input(self):
        rows = [((1, 0, 1, 1), (1, 1)), ((1, 0, 1, 0), (1, 0)), ((1, 0, 0, 1), (0, 0)), ((1, 1, 0, 0), (1, 1)),
                ((0, 1, 1, 1), (0, 0))]
        for args, (own, inp) in rows:
            with self.subTest(args=args):
                bits = self.d.ILCT_SesMovementControl(*args)
                self.assertEqual((bits & 1, (bits >> 1) & 1), (own, inp))

    def test_camera_hold_during_a_guest_interaction(self):
        self.assertTrue(self.d.ILCT_SesCameraGate(1, 0, 1, 0, 1))
        self.assertFalse(self.d.ILCT_SesCameraGate(1, 0, 1, 1, 1))
        self.assertFalse(self.d.ILCT_SesCameraGate(0, 1, 1, 0, 1))

    def test_camera_reacquire_needs_three_ready_samples(self):
        st = Reacquire()
        self.d.ILCT_SesCameraReacquire(1, 0, ctypes.byref(st), 0, 3)
        self.assertEqual((st.active, st.pending, st.ready_samples), (0, 1, 0))
        for want in (1, 2):
            self.d.ILCT_SesCameraReacquire(1, 1, ctypes.byref(st), 1, 3)
            self.assertEqual((st.active, st.pending, st.ready_samples), (0, 1, want))
        self.d.ILCT_SesCameraReacquire(1, 1, ctypes.byref(st), 1, 3)
        self.assertEqual((st.active, st.pending, st.ready_samples), (1, 0, 3))
        # Once shared, a not-ready sample does not reopen the wait; a release resets everything.
        self.d.ILCT_SesCameraReacquire(1, 1, ctypes.byref(st), 0, 3)
        self.assertEqual((st.active, st.pending, st.ready_samples), (1, 0, 3))
        self.d.ILCT_SesCameraReacquire(0, 1, ctypes.byref(st), 0, 3)
        self.assertEqual((st.active, st.pending, st.ready_samples), (0, 0, 0))

    def test_camera_positions(self):
        def ready(*points):
            flat = [v for p in points for v in p]
            return bool(self.d.ILCT_SesPositionsReady((ctypes.c_float * len(flat))(*flat), len(points)))
        self.assertFalse(ready((0, 0, 0), (0, 0, 0)))
        self.assertFalse(ready((210, 42, 239), (0, 0, 0)))
        self.assertTrue(ready((209.2, 42.3, 239.7), (211.4, 42.9, 239.1)))
        self.assertFalse(ready((float('nan'), 1, 2), (3, 4, 5)))
        self.assertFalse(ready((1, 2, 3)))

    def test_party_down_needs_every_actor_coherently_dead(self):
        def down(*states):
            return bool(self.d.ILCT_SesPartyDown((i8 * len(states))(*states), len(states)))
        self.assertTrue(down(NO, NO, NO))
        self.assertFalse(down(NO, UNKNOWN, NO))   # an actor being rebuilt keeps hands off
        self.assertFalse(down(NO, YES))
        self.assertFalse(down())


# =====================================================================================================
# World evaluation (the session snapshot) on plain samples
# =====================================================================================================
def sample(current=(0, 5, NONE, NONE), ui=None, leader=0, location=(100, 100), control=True, menu=False,
           modal=False, town=False, chocobo=False, roots=True, battle=0x5000):
    w = WorldSample()
    w.roots = int(roots)
    w.control_enabled = int(control)
    w.menu_open = int(menu)
    w.modal_active = int(modal)
    w.town = int(town)
    w.chocobo = int(chocobo)
    w.location_read = int(roots)
    w.leader = leader
    w.battle_state = battle if roots else 0
    ui = current if ui is None else ui
    for k in range(9):
        w.current[k] = current[k] if k < len(current) else NONE
        w.ui[k] = ui[k] if k < len(ui) else NONE
    w.location_current, w.location_ui = location if roots else (0xFFFFFFFF, 0xFFFFFFFF)
    return w


@native_test
class WorldEvaluateTest(DllCase):
    def view(self, id_, w, materialized=-1):
        v = WorldView()
        self.d.ILCT_SesEvaluate(ctypes.byref(id_), ctypes.byref(w), materialized, ctypes.byref(v))
        return v

    def test_field_and_each_boundary(self):
        id_ = identity(0, 5)
        rows = [
            ({}, dict(loading=0, cutscene=0, gameplay=1, movement=1, camera=1, p1_menu=1, valid=1)),
            ({'roots': False}, dict(loading=1, gameplay=0, movement=0, camera=0, p1_menu=0, valid=0)),
            ({'location': (100, 101)}, dict(loading=1, gameplay=0, valid=0)),    # a load finishing
            ({'control': False}, dict(cutscene=1, movement=0, camera=0, p1_menu=0, valid=0)),
            ({'menu': True}, dict(cutscene=0, movement=0, camera=1, p1_menu=0, valid=1)),
            ({'modal': True}, dict(cutscene=0, movement=0, p1_menu=0, valid=1)),
            ({'town': True}, dict(movement=1, p1_menu=0, valid=1)),
            ({'chocobo': True}, dict(movement=1, p1_menu=0, valid=1)),
            ({'current': (5, NONE, NONE, NONE)}, dict(loading=1, valid=0)),       # P1's character left the party
            ({'current': (0, NONE, NONE, NONE)}, dict(loading=0, gameplay=0, movement=0, valid=1)),  # guest gone
        ]
        for change, want in rows:
            with self.subTest(change=change):
                v = self.view(id_, sample(**change))
                got = {k: getattr(v, k) for k in want if k != 'valid'}
                if 'valid' in want:
                    got['valid'] = self.d.ILCT_SesWorldValid(ctypes.byref(v))
                self.assertEqual(got, want)

    def test_materialized_actor_count(self):
        id_ = identity(0, 5)
        self.assertEqual(self.view(id_, sample(), 0).loading, 1)
        self.assertEqual(self.view(id_, sample(), 1).gameplay, 0)
        self.assertEqual(self.view(id_, sample(), 2).gameplay, 1)

    def test_guest_menu_follows_its_own_seat(self):
        v = self.view(identity(0, 5, 1), sample(current=(0, 5, 1, NONE)))
        self.assertEqual(list(v.guest_menu)[:3], [0, 1, 1])
        v = self.view(identity(0, 5, 1), sample(current=(0, 5, 1, NONE), town=True))
        self.assertEqual(list(v.guest_menu)[:3], [0, 0, 0])

    def test_summon_owner(self):
        # One Esper materialized in both layouts; its owner is the leader if a seat controls it.
        id_ = identity(0, 5)
        cur = (0, 30, NONE, NONE)
        self.assertEqual(self.d.ILCT_SesSummonedEsper((u16 * 4)(*cur), (u16 * 4)(*cur)), 30)
        self.assertEqual(self.d.ILCT_SesSummonOwner(ctypes.byref(id_), (u16 * 4)(*cur), (u16 * 4)(*cur), 0), 0)
        cur2 = (5, 30, NONE, NONE)
        self.assertEqual(self.d.ILCT_SesSummonOwner(ctypes.byref(id_), (u16 * 4)(*cur2), (u16 * 4)(*cur2), 30), 1)
        two = (30, 31, NONE, NONE)
        self.assertEqual(self.d.ILCT_SesSummonedEsper((u16 * 4)(*two), (u16 * 4)(*two)), NONE)
        v = self.view(id_, sample(current=(5, 30, NONE, NONE), leader=5))
        self.assertEqual((v.summon_seat, v.loading), (1, 0))

    def test_guest_session_gate_needs_no_other_guest(self):
        id_ = identity(0, 5, 1)
        w = sample(current=(0, 1, NONE, NONE))   # P2's character is not in the party, P3's is
        v = self.view(id_, w)
        self.assertEqual(v.movement, 0)
        self.assertTrue(self.d.ILCT_SesGuestMayMove(ctypes.byref(v), ctypes.byref(id_), ctypes.byref(w), 0b100))
        for change in ({'menu': True}, {'modal': True}, {'control': False}, {'roots': False}):
            with self.subTest(change=change):
                w2 = sample(current=(0, 1, NONE, NONE), **change)
                v2 = self.view(id_, w2)
                self.assertFalse(self.d.ILCT_SesGuestMayMove(ctypes.byref(v2), ctypes.byref(id_), ctypes.byref(w2),
                                                             0b100))
        self.assertTrue(self.d.ILCT_SesP1MayMove(ctypes.byref(v), ctypes.byref(w)))


@native_test
class IdentityTest(DllCase):
    def resolve(self, configured, current, ui=None, leader=NONE):
        out = Identity()
        ui = current if ui is None else ui
        ok = self.d.ILCT_SesResolveIdentity(ctypes.byref(configured), (u16 * 4)(*current), (u16 * 4)(*ui), leader,
                                            ctypes.byref(out))
        return list(out.member)[:3] if ok else None

    def test_leader_change_onto_a_guest_swaps_two_seats_only(self):
        self.assertEqual(self.resolve(identity(0, 5, 1), (0, 5, 1, 3), leader=5), [5, 0, 1])
        self.assertEqual(self.resolve(identity(0, 5, 1), (0, 5, 1, 3), leader=1), [1, 5, 0])

    def test_auto_and_departed_seats_fill_in_party_order(self):
        self.assertEqual(self.resolve(identity(0, NONE, NONE, in_session=0b111), (0, 3, 2, NONE), leader=0),
                         [0, 3, 2])
        self.assertEqual(self.resolve(identity(0, 4, 1), (0, 3, 1, NONE), leader=0), [0, 3, 1])
        self.assertIsNone(self.resolve(identity(0, 5), (30, NONE, NONE, NONE)))

    def test_seats_out_of_session_get_no_character(self):
        id_ = identity(0, 5, NONE, in_session=0b011)
        self.assertEqual(self.resolve(id_, (0, 5, 1, 3), leader=0), [0, 5, NONE])

    def test_identity_is_adopted_only_from_a_settled_party(self):
        id_ = identity(0, 5)
        out = Identity()

        def adopt(**change):
            w = sample(**({'current': (0, 5, 3, NONE), 'leader': 5} | change))
            return bool(self.d.ILCT_SesIdentityFromParty(ctypes.byref(id_), ctypes.byref(w), ctypes.byref(out)))
        self.assertTrue(adopt())
        self.assertEqual(list(out.member)[:2], [5, 0])
        for change in ({'control': False}, {'menu': True}, {'modal': True}, {'location': (1, 2)}, {'roots': False},
                       {'ui': (0, 5, NONE, NONE)}, {'current': (0, 0, 5, NONE)}, {'leader': 30},
                       {'current': (0, 30, 5, NONE)}):
            with self.subTest(change=change):
                self.assertFalse(adopt(**change))
        w = sample(current=(0, 5, NONE, NONE), leader=0)
        self.assertFalse(self.d.ILCT_SesIdentityFromParty(ctypes.byref(id_), ctypes.byref(w), ctypes.byref(out)))

    def test_p1_character_and_ko_promotion(self):
        id_ = identity(0, 5)
        cur = (u16 * 4)(0, 5, 3, NONE)
        self.assertEqual(self.d.ILCT_SesP1Member(ctypes.byref(id_), cur, cur, 3), 3)   # an unowned leader is P1's
        self.assertEqual(self.d.ILCT_SesP1Member(ctypes.byref(id_), cur, cur, 5), 0)   # a guest's leader is not
        self.assertEqual(self.d.ILCT_SesP1Member(ctypes.byref(id_), cur, cur, 30), 0)  # an Esper is not
        self.assertEqual(self.d.ILCT_SesKoPromoted(ctypes.byref(id_), 5, NO, 1), 1)
        self.assertEqual(self.d.ILCT_SesKoPromoted(ctypes.byref(id_), 5, YES, 1), -1)
        self.assertEqual(self.d.ILCT_SesKoPromoted(ctypes.byref(id_), 5, NO, 0), -1)
        self.assertEqual(self.d.ILCT_SesKoPromoted(ctypes.byref(id_), 5, UNKNOWN, 1), 1)


# =====================================================================================================
# Ownership hand-off (restated tables): release before acquire, bounded follow requests, passive waits, cancels
# =====================================================================================================
def tok(seat, member, actor, generation=1, epoch=1, handle=None, phys=None):
    return Token(seat, 0, member, generation, epoch,
                 ((generation & 0xFFFF) << 16) | (member & 0xFFFF) if handle is None else handle,
                 actor, actor + 0x1000 if phys is None else phys)


def obs(t, n=1, of=None, **change):
    s = Sample()
    s.of = t if of is None else of
    s.token = t
    s.sample = n
    values = dict(available=1, alive=YES, materialized=1, layout_coherent=1, cutscene=0, command_blocked=NO,
                  follows_list=1, leader=0, action_state=1, target=0, action=ACTION_IDLE,
                  descriptor_action=ACTION_FOLLOW)
    values.update(change)
    for k, v in values.items():
        setattr(s, k, tri(v) if k in ('alive', 'command_blocked') else v)
    return s


def same(a: Token, b: Token):
    return bytes(a) == bytes(b)


class Planner:
    def __init__(self, d):
        self.d = d
        self.state = Ownership()

    def own(self, seat, t):
        self.state.owner[seat] = t
        self.state.has_owner[seat] = 1

    def intent(self, t, created=0, deadline=1000, attempts=0, next_ms=0, requested=None, waiting=0):
        i = self.state.intent[self.state.intent_count]
        i.token = t
        i.created_ms, i.deadline_ms, i.next_attempt_ms, i.attempts = created, deadline, next_ms, attempts
        i.has_request, i.requested_sample = (0, 0) if requested is None else (1, requested)
        i.waiting = waiting
        self.state.intent_count += 1

    def plan(self, desired: dict, eligible=None, samples=(), now=0, epoch=1):
        want = (Token * 4)()
        has = (u8 * 4)()
        elig = (u8 * 4)()
        for seat, t in desired.items():
            if t is not None:
                want[seat] = t
                has[seat] = 1
                elig[seat] = 1 if eligible is None else int(eligible.get(seat, False))
        arr = (Sample * max(1, len(samples)))(*samples)
        out = EffectList()
        self.d.ILCT_HoPlan(ctypes.byref(self.state), want, has, elig, arr, len(samples), now, epoch, ctypes.byref(out))
        return [(out.item[k].kind, out.item[k].reason, out.item[k].token) for k in range(out.count)]

    def advance(self, samples=(), now=0, epoch=1):
        arr = (Sample * max(1, len(samples)))(*samples)
        out = EffectList()
        self.d.ILCT_HoAdvance(ctypes.byref(self.state), arr, len(samples), now, epoch, ctypes.byref(out))
        return [(out.item[k].kind, out.item[k].reason, out.item[k].token) for k in range(out.count)]

    def owners(self):
        return {s: self.state.owner[s] for s in range(4) if self.state.has_owner[s]}

    def intents(self):
        return [self.state.intent[k] for k in range(self.state.intent_count)]


def kinds(effects):
    return [k for k, _, _ in effects]


@native_test
class HandoffPlanTest(DllCase):
    def setUp(self):
        self.p = Planner(self.d)

    def test_old_owner_released_and_cleared_before_the_new_one(self):
        old, new = tok(1, 5, 0x10000), tok(1, 1, 0x20000)
        self.p.own(1, old)
        fx = self.p.plan({1: new}, samples=[obs(old)], now=10_000)
        self.assertEqual(kinds(fx), [FX['RELEASE'], FX['CLEAR'], FX['ASK_FOLLOW'], FX['ACQUIRE']])
        self.assertTrue(same(self.p.owners()[1], new))
        self.assertEqual(self.p.intents()[0].attempts, 1)

    def swap(self):
        p2, p3 = tok(1, 5, 0x10000), tok(2, 1, 0x20000)
        np2 = Token(1, 0, p3.member, p3.actor_generation, p3.world_epoch, p3.handle, p3.actor, p3.phys)
        np3 = Token(2, 0, p2.member, p2.actor_generation, p2.world_epoch, p2.handle, p2.actor, p2.phys)
        self.p.own(1, p2)
        self.p.own(2, p3)
        return p2, p3, np2, np3

    def test_two_seat_swap_releases_everything_before_any_acquire(self):
        _p2, _p3, np2, np3 = self.swap()
        fx = self.p.plan({1: np2, 2: np3})
        k = kinds(fx)
        first = k.index(FX['ACQUIRE'])
        self.assertTrue(all(x in (FX['RELEASE'], FX['CLEAR']) for x in k[:first]))
        self.assertNotIn(FX['ASK_FOLLOW'], k)
        self.assertEqual(self.p.intents(), [])
        self.assertEqual([r for kk, r, _ in fx if kk == FX['ACQUIRE']], [WHY['TRANSFER']] * 2)

    def test_refused_transfer_gets_its_release_intent_back_across_a_mapping_gap(self):
        _p2, _p3, np2, np3 = self.swap()
        self.p.plan({1: np2, 2: np3})
        self.d.ILCT_HoRefuse(ctypes.byref(self.p.state), ctypes.byref(np2), 1, 10)
        self.assertEqual(self.p.plan({1: None, 2: np3}, now=20), [])
        self.assertEqual(self.p.advance(now=20), [])
        self.assertNotIn(1, self.p.owners())
        self.assertTrue(same(self.p.owners()[2], np3))
        self.assertTrue(same(self.p.intents()[0].token, np2))

    def test_refusal_removes_only_the_matching_owner(self):
        a, b = tok(1, 5, 0x10000), tok(2, 1, 0x20000)
        self.p.own(1, a)
        self.p.own(2, b)
        self.d.ILCT_HoRefuse(ctypes.byref(self.p.state), ctypes.byref(a), 0, 0)
        self.assertEqual(list(self.p.owners()), [2])

    def test_reacquire_cancels_a_pending_intent(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t)
        self.assertEqual(kinds(self.p.plan({1: t}, samples=[obs(t)], now=100)), [FX['ACQUIRE']])
        self.assertEqual(self.p.intents(), [])

    def test_current_owner_cancels_a_stale_intent_for_its_incarnation(self):
        t = tok(1, 5, 0x10000)
        self.p.own(1, t)
        self.p.intent(t)
        self.assertEqual(self.p.plan({1: t}, samples=[obs(t)], now=100), [])
        self.assertEqual(self.p.intents(), [])

    def test_confirmation_must_come_from_a_later_sample(self):
        t = tok(1, 5, 0x10000)
        self.p.own(1, t)
        self.p.plan({1: None}, samples=[obs(t, 4)])
        self.assertEqual(self.p.advance([obs(t, 4, action=ACTION_FOLLOW)], now=10), [])
        self.assertEqual(len(self.p.intents()), 1)
        fx = self.p.advance([obs(t, 5, action=ACTION_FOLLOW)], now=20)
        self.assertEqual(kinds(fx), [FX['CONFIRMED']])
        self.assertEqual(self.p.intents(), [])

    def test_already_following_confirms_without_a_request(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t)
        fx = self.p.advance([obs(t, 7, action=ACTION_FOLLOW)], now=200)
        self.assertEqual([(k, r) for k, r, _ in fx], [(FX['CONFIRMED'], WHY['ALREADY_FOLLOWING'])])

    def test_four_attempts_inside_one_bounded_window(self):
        t = tok(1, 5, 0x10000)
        self.p.own(1, t)
        requests = kinds(self.p.plan({1: None}, samples=[obs(t, 1)])).count(FX['ASK_FOLLOW'])
        for n, now in enumerate((100, 200, 300), start=2):
            requests += kinds(self.p.advance([obs(t, n)], now=now)).count(FX['ASK_FOLLOW'])
        self.assertEqual(requests, 4)
        # The fourth request keeps its answer window (until 400), then the cap ends the intent.
        self.assertEqual(self.p.advance([obs(t, 5)], now=399), [])
        self.assertEqual(len(self.p.intents()), 1)
        fx = self.p.advance([obs(t, 6)], now=400)
        self.assertEqual(self.p.intents(), [])
        self.assertEqual([(k, r) for k, r, _ in fx], [(FX['ABANDONED'], WHY['BOUNDED'])])

    def test_the_last_request_keeps_its_full_answer_window(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t, attempts=4, next_ms=400, requested=4)
        self.assertEqual(self.p.advance([obs(t, 5)], now=310), [])
        self.assertEqual(len(self.p.intents()), 1)
        self.assertEqual(kinds(self.p.advance([obs(t, 5, action=ACTION_FOLLOW)], now=310)), [FX['CONFIRMED']])

    def test_cancels(self):
        for change in ({'materialized': 0}, {'layout_coherent': 0}, {'cutscene': 1}):
            with self.subTest(change=change):
                p = Planner(self.d)
                t = tok(1, 5, 0x10000)
                p.intent(t)
                fx = p.advance([obs(t, **change)], now=100)
                self.assertEqual((p.intents(), fx[0][0], fx[0][1]), ([], FX['ABANDONED'], WHY['INELIGIBLE']))
        p = Planner(self.d)
        t = tok(1, 5, 0x10000)
        p.intent(t)
        self.assertEqual([(k, r) for k, r, _ in p.advance(now=100, epoch=2)], [(FX['ABANDONED'], WHY['WORLD'])])
        p = Planner(self.d)
        p.intent(t)
        fx = p.advance([obs(tok(1, 5, 0x10000, generation=2), of=t)], now=100)
        self.assertEqual([(k, r) for k, r, _ in fx], [(FX['ABANDONED'], WHY['GENERATION'])])

    def test_ko_waits_without_spending_attempts(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t)
        self.assertEqual(self.p.advance([obs(t, alive=False, command_blocked=True)], now=1_000_000), [])
        i = self.p.intents()[0]
        self.assertEqual((i.attempts, i.waiting), (0, 1))
        self.assertEqual(self.p.advance([obs(t, 2, alive=False, command_blocked=True, action_state=4, action=0x4999,
                                             follows_list=0)], now=2_000_000_000), [])
        self.assertEqual(self.p.advance([obs(t, 3, action_state=4, action=0x4999, follows_list=0)],
                                        now=2_000_000_100), [])
        fx = self.p.advance([obs(t, 4)], now=2_000_000_200)
        self.assertEqual(kinds(fx), [FX['ASK_FOLLOW']])
        self.assertEqual(self.p.intents()[0].attempts, 1)

    def test_current_leader_waits_then_follows(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t)
        self.assertEqual(self.p.advance([obs(t, leader=5, action=0x4999, follows_list=0)], now=1_000_000), [])
        self.assertEqual(self.p.intents()[0].attempts, 0)
        self.assertEqual(kinds(self.p.advance([obs(t, 2)], now=1_000_100)), [FX['ASK_FOLLOW']])

    def test_protected_action_waits_then_requests_then_confirms(self):
        t = tok(1, 5, 0x10000)
        busy = obs(t, 1, action_state=2, target=0x1234, action=0x4999, descriptor_action=0x4888, follows_list=0)
        self.p.own(1, t)
        self.assertEqual(kinds(self.p.plan({1: None}, samples=[busy])), [FX['RELEASE'], FX['CLEAR']])
        self.assertEqual(self.p.intents()[0].attempts, 0)
        self.assertEqual(self.p.advance([obs(t, 2, target=0x1234)], now=100), [])
        self.assertEqual(kinds(self.p.advance([obs(t, 3)], now=200)), [FX['ASK_FOLLOW']])
        self.assertEqual(kinds(self.p.advance([obs(t, 4, action=ACTION_FOLLOW)], now=210)), [FX['CONFIRMED']])

    def test_each_action_divergence_waits_without_an_attempt(self):
        for change in ({'available': 0}, {'action_state': 2}, {'target': 0x1234}, {'action': 0x4999},
                       {'descriptor_action': 0x4888}, {'follows_list': 0}):
            with self.subTest(change=change):
                p = Planner(self.d)
                t = tok(1, 5, 0x10000)
                p.intent(t)
                self.assertEqual(p.advance([obs(t, **change)], now=100), [])
                self.assertEqual(p.intents()[0].attempts, 0)

    def test_a_busy_character_keeps_a_write_free_intent_past_the_window(self):
        t = tok(1, 5, 0x10000)
        busy = obs(t, action=0x4999, target=0x1234)
        self.p.intent(t)
        self.p.advance([busy], now=999)
        i = self.p.intents()[0]
        self.assertEqual((i.attempts, i.deadline_ms, i.waiting), (0, 1999, 1))
        self.p.advance([busy], now=1000)
        self.assertEqual(self.p.intents()[0].deadline_ms, 2000)

    def test_rapid_off_on_off_converges_once_the_action_settles(self):
        for seat, member, actor in ((1, 5, 0x10000), (2, 1, 0x20000)):
            with self.subTest(seat=seat):
                p = Planner(self.d)
                t = tok(seat, member, actor)
                busy = obs(t, 1, action_state=2, target=0x1234, action=0x4999, descriptor_action=0x4888,
                           follows_list=0)
                p.own(seat, t)
                self.assertEqual(kinds(p.plan({seat: None}, samples=[busy])), [FX['RELEASE'], FX['CLEAR']])
                self.assertEqual(kinds(p.plan({seat: t}, samples=[busy], now=10)), [FX['ACQUIRE']])
                self.assertEqual(p.intents(), [])
                p.plan({seat: None}, samples=[busy], now=20)
                self.assertEqual(p.advance([busy], now=5000), [])
                self.assertEqual(p.intents()[0].attempts, 0)
                self.assertEqual(kinds(p.advance([obs(t, 2)], now=5100)), [FX['ASK_FOLLOW']])
                self.assertEqual(kinds(p.advance([obs(t, 3, action=ACTION_FOLLOW)], now=5110)), [FX['CONFIRMED']])

    def test_a_requested_release_survives_a_later_incompatible_action(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t, attempts=1, next_ms=100, requested=1)
        busy = obs(t, 2, action_state=2, target=0x1234, action=0x4999, descriptor_action=0x4888, follows_list=0)
        self.assertEqual(self.p.advance([busy], now=5000), [])
        i = self.p.intents()[0]
        self.assertEqual((i.attempts, i.deadline_ms, i.waiting), (1, 6000, 1))
        self.assertEqual(kinds(self.p.advance([obs(t, 3)], now=5100)), [FX['ASK_FOLLOW']])
        self.assertEqual((self.p.intents()[0].attempts, self.p.intents()[0].requested_sample), (1, 3))

    def test_unknown_state_and_read_gaps_never_request(self):
        for change in ({'alive': None}, {'leader': NONE}):
            with self.subTest(change=change):
                p = Planner(self.d)
                t = tok(1, 5, 0x10000)
                p.intent(t)
                self.assertEqual(p.advance([obs(t, **change)], now=100), [])
                self.assertEqual(len(p.intents()), 1)
        p = Planner(self.d)
        t = tok(1, 5, 0x10000)
        p.intent(t)
        self.assertEqual(p.advance([obs(t, available=0, alive=None)], now=5000), [])
        self.assertEqual(p.intents()[0].deadline_ms, 6000)
        self.assertEqual(kinds(p.advance([obs(t, 2)], now=5100)), [FX['ASK_FOLLOW']])

    def test_command_blocking_status_is_passive_and_never_a_confirmation(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t)
        self.assertEqual(self.p.advance([obs(t, command_blocked=True)], now=60_000), [])
        self.assertEqual((self.p.intents()[0].attempts, self.p.intents()[0].deadline_ms), (0, 61_000))
        blocked_follow = obs(t, 7, action=ACTION_FOLLOW, command_blocked=True)
        self.assertEqual(self.d.ILCT_HoSampleFlags(ctypes.byref(blocked_follow)) & 1, 0)
        self.assertEqual(self.p.advance([blocked_follow], now=100_000), [])
        self.assertEqual(kinds(self.p.advance([obs(t, 8)], now=100_100)), [FX['ASK_FOLLOW']])
        fx = self.p.advance([obs(tok(1, 5, 0x10000, generation=2), of=t)], now=100_200)
        self.assertEqual([(k, r) for k, r, _ in fx], [(FX['ABANDONED'], WHY['GENERATION'])])

    def test_a_status_interruption_opens_a_new_attempt_window(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t, attempts=4, next_ms=400, requested=4)
        self.assertEqual(self.p.advance([obs(t, 5, command_blocked=True)], now=100_000), [])
        self.assertEqual(self.p.intents()[0].attempts, 4)
        self.assertEqual(kinds(self.p.advance([obs(t, 6)], now=100_100)), [FX['ASK_FOLLOW']])
        self.assertEqual(self.p.intents()[0].attempts, 1)

    def test_no_native_handle_never_spends_an_attempt(self):
        t = tok(1, 5, 0x10000, handle=0)
        self.p.intent(t)
        self.assertEqual(self.p.advance([obs(t)], now=500), [])
        self.assertEqual(self.p.intents()[0].attempts, 0)
        self.assertEqual(self.d.ILCT_HoSampleFlags(ctypes.byref(obs(t))) & 2, 0)

    def test_a_cleared_status_gets_a_full_settle_window_at_the_old_deadline(self):
        t = tok(1, 5, 0x10000)
        self.p.intent(t)
        self.p.advance([obs(t, action=0x4999, target=0x1234, command_blocked=True)], now=990)
        clearing = obs(t, action=0x4999, target=0x1234, command_blocked=False)
        self.assertEqual(self.p.advance([clearing], now=1000), [])
        self.assertEqual((self.p.intents()[0].deadline_ms, self.p.intents()[0].waiting), (2000, 1))
        self.assertEqual(self.p.advance([clearing], now=100_000), [])
        self.assertEqual(kinds(self.p.advance([obs(t, 2)], now=100_100)), [FX['ASK_FOLLOW']])

    def test_an_ineligible_transfer_keeps_the_old_incarnation_releasable(self):
        old = tok(1, 5, 0x10000)
        moved = Token(2, 0, old.member, old.actor_generation, old.world_epoch, old.handle, old.actor, old.phys)
        self.p.own(1, old)
        fx = self.p.plan({1: None, 2: moved}, eligible={2: False}, samples=[obs(old, available=0, alive=None)])
        self.assertEqual(kinds(fx), [FX['RELEASE'], FX['CLEAR']])
        self.assertEqual(self.p.owners(), {})
        self.assertTrue(same(self.p.intents()[0].token, old))
        self.assertEqual(kinds(self.p.advance([obs(old, 2)], now=100)), [FX['ASK_FOLLOW']])

    def test_a_boundary_ends_every_intent(self):
        self.p.intent(tok(1, 5, 0x10000))
        self.p.intent(tok(2, 1, 0x20000))
        out = EffectList()
        self.d.ILCT_HoEnd(ctypes.byref(self.p.state), WHY['SHUTDOWN'], ctypes.byref(out))
        self.assertEqual([(out.item[k].kind, out.item[k].reason) for k in range(out.count)],
                         [(FX['ABANDONED'], WHY['SHUTDOWN'])] * 2)
        self.assertEqual(self.p.intents(), [])


@native_test
class EpochAndGenerationTest(DllCase):
    def test_world_epoch_moves_after_every_invalid_stretch_and_every_new_world(self):
        st = WorldEpochs()
        steps = [(1, 0x50, 10, 1), (0, 0, 0, 1), (1, 0x50, 10, 2), (1, 0x50, 10, 2), (1, 0x50, 11, 3),
                 (1, 0x60, 11, 4), (0, 0, 0, 4), (0, 0, 0, 4), (1, 0x60, 11, 5)]
        for valid, a, b, want in steps:
            self.assertEqual(self.d.ILCT_WorldEpochs(ctypes.byref(st), valid, a, b), want)
        self.assertEqual(self.d.ILCT_WorldEpochs(ctypes.byref(WorldEpochs()), 0, 0, 0), 0)

    def observe(self, st, actor, phys, handle=0, epoch=1, alive=True, present=True, member=5):
        t = Token()
        ok = self.d.ILCT_Generations(ctypes.byref(st), 1, member, actor, phys, handle, epoch, tri(alive), int(present),
                                     ctypes.byref(t))
        return t if ok else None

    def test_generations(self):
        st = Generations()
        first = self.observe(st, 0x1000, 0x2000)
        self.assertIsNone(self.observe(st, 0, 0, alive=None))                 # a scan gap is unknown
        self.assertEqual(self.observe(st, 0x1000, 0x2000).actor_generation, first.actor_generation)
        self.assertIsNone(self.observe(st, 0, 0, alive=None, present=False))  # off to the reserve
        back = self.observe(st, 0x1000, 0x2000)
        self.assertGreater(back.actor_generation, first.actor_generation)
        dead = self.observe(st, 0x1000, 0x2000, alive=False)
        revived = self.observe(st, 0x1000, 0x2000, alive=True)
        self.assertEqual((dead.actor_generation, revived.actor_generation), (back.actor_generation,) * 2)
        moved = self.observe(st, 0x3000, 0x4000)
        self.assertGreater(moved.actor_generation, revived.actor_generation)
        new_world = self.observe(st, 0x3000, 0x4000, epoch=2)
        self.assertGreater(new_world.actor_generation, moved.actor_generation)

    def test_handle_reads(self):
        st = Generations()
        first = self.observe(st, 0x1000, 0x2000, handle=0x00100019)
        unknown = self.observe(st, 0x1000, 0x2000, handle=0, alive=None)
        self.assertEqual((unknown.actor_generation, unknown.handle), (first.actor_generation, 0x00100019))
        changed = self.observe(st, 0x1000, 0x2000, handle=0x00200019)
        self.assertGreater(changed.actor_generation, first.actor_generation)
        self.assertEqual(changed.handle, 0x00200019)
        self.assertIsNone(self.observe(st, 0x1000, 0x2000, member=40))       # no slot for that party id


# =====================================================================================================
# Fault-safe world and actor reads on synthetic memory
# =====================================================================================================
@native_test
class ProbeLoadTest(DllCase):
    def scalar(self, address, width):
        out, single = ctypes.c_uint64(), u32()
        ok = self.d.ILCT_SesReadScalar(address, width, ctypes.byref(out), ctypes.byref(single))
        return (out.value if ok else None), single.value

    def test_aligned_scalars_are_one_load_and_misaligned_ones_still_read(self):
        img = Image()
        try:
            base = img.alloc(64)
            img.put(base, bytes(range(1, 17)))
            for width, want in ((1, 0x01), (2, 0x0201), (4, 0x04030201), (8, 0x0807060504030201)):
                with self.subTest(width=width):
                    self.assertEqual(self.scalar(base, width), (want, 1))
            self.assertEqual(self.scalar(base + 1, 4), (0x05040302, 0))  # byte path, same value
            page = img.alloc(PAGE, page=True)
            img.protect(page, PAGE, PAGE_NOACCESS)
            self.assertEqual(self.scalar(page, 4), (None, 0))
            self.assertEqual(self.scalar(page + 2, 2), (None, 0))
            self.assertEqual(self.scalar(0, 8), (None, 0))
        finally:
            img.close()

    def test_a_value_written_concurrently_never_reads_torn(self):
        rows = ((4, 0x100, 0xFF), (8, 0x00000000FFFFFFFF, 0x0000000100000000))
        for width, a, b in rows:
            with self.subTest(width=width):
                self.assertEqual(self.d.ILCT_ProbeTearDrill(width, a, b, 2_000_000, 0), 0)
        # Instrument: the byte copy can tear the same values (HP 0x100 -> 0xFF read as 0 or 0x1FF).
        torn = sum(self.d.ILCT_ProbeTearDrill(width, a, b, 2_000_000, 1) for width, a, b in rows)
        if torn == 0:
            self.skipTest('instrument: no tear provoked on this machine (needs two free cores)')
        self.assertGreater(torn, 0)


class ImageCase(DllCase):
    def setUp(self):
        self.img = Image()
        self.world = GameWorld(self.img)

    def tearDown(self):
        self.img.close()

    def scan(self) -> PartyActors:
        out = PartyActors()
        self.d.ILCT_SesScanParty(self.img.base, ctypes.byref(out), None)
        return out

    def slots(self) -> dict:
        p = self.scan()
        return {k: p.slot[k] for k in range(4) if p.slot[k].actor}

    def validity(self, actor):
        return self.d.ILCT_SesActorInParty(self.img.base, actor)

    def fact(self, actor, which):
        return self.d.ILCT_SesActorFacts(self.img.base, actor, which)


@native_test
class ActorReadTest(ImageCase):
    def test_alive_uses_hp_and_the_ko_bit(self):
        a = self.world.add_actor(0, 0)
        self.assertEqual(self.fact(a.actor, 0), YES)
        a.set_status(ST_KO)
        self.assertEqual(self.fact(a.actor, 0), NO)
        a.set_status(0, ST_KO)
        self.assertEqual(self.fact(a.actor, 0), NO)
        a.set_status(0)
        a.set_hp(0)
        self.assertEqual(self.fact(a.actor, 0), NO)

    def test_walking_statuses(self):
        a = self.world.add_actor(0, 0)
        for bit in (ST_KO, ST_STONE, ST_STOP, ST_SLEEP, ST_IMMOBILIZE, ST_XZONE):
            a.set_status(bit)
            self.assertEqual(self.fact(a.actor, 1), NO, hex(bit))
        for bit in (ST_DISABLE, ST_CONFUSE, ST_BERSERK, 0):
            a.set_status(bit)
            self.assertEqual(self.fact(a.actor, 1), YES, hex(bit))
        a.set_status(ST_STONE)
        self.assertEqual((self.fact(a.actor, 0), self.fact(a.actor, 1)), (YES, NO))  # petrified is not dead
        a.set_status(ST_BERSERK)
        self.assertEqual((self.fact(a.actor, 2), self.fact(a.actor, 3)), (YES, YES))
        a.set_status(ST_DISABLE)
        self.assertEqual((self.fact(a.actor, 2), self.fact(a.actor, 3)), (YES, NO))

    def test_leader_availability(self):
        a = self.world.add_actor(0, 0)
        self.assertEqual((self.fact(a.actor, 4), self.fact(a.actor, 5)), (YES, YES))
        for timed, lasting in ((ST_KO, 0), (ST_STONE, 0), (0, ST_STOP), (ST_SLEEP, 0), (ST_CONFUSE, 0),
                               (ST_DISABLE, 0), (ST_XZONE, 0)):
            a.set_status(timed, lasting)
            self.assertEqual((self.fact(a.actor, 4), self.fact(a.actor, 5)), (NO, NO), hex(timed | lasting))
        a.set_status(ST_BERSERK)   # blocks unless the movement subsystem's command gate patch is live
        self.assertEqual((self.fact(a.actor, 4), self.fact(a.actor, 5)), (NO, YES))
        a.set_status(0)
        a.set_hp(0)
        self.assertEqual(self.fact(a.actor, 4), NO)
        a.set_hp(100)
        self.img.p64(a.actor + 0x698, 0)
        self.assertEqual(self.fact(a.actor, 4), UNKNOWN)
        self.img.p64(a.actor + 0x698, a.battle)
        self.img.protect(a.battle, PAGE, PAGE_NOACCESS)
        self.assertEqual(self.fact(a.actor, 4), UNKNOWN)
        self.img.protect(a.battle, PAGE, PAGE_READWRITE)

    def test_unreadable_records_are_unknown_never_a_fault(self):
        a = self.world.add_actor(0, 0)
        self.img.protect(a.battle, PAGE, PAGE_NOACCESS)
        self.assertEqual([self.fact(a.actor, k) for k in range(4)], [UNKNOWN] * 4)
        self.img.protect(a.battle, PAGE, PAGE_READWRITE)
        self.img.protect(a.actor, PAGE, PAGE_GUARD | PAGE_READWRITE)   # a guard page is never touched
        self.assertEqual(self.fact(a.actor, 0), UNKNOWN)
        self.img.protect(a.actor, PAGE, PAGE_READWRITE)
        self.assertEqual(self.fact(a.actor, 0), YES)
        self.assertEqual(self.fact(0, 0), UNKNOWN)
        self.assertEqual(self.fact(0x10, 0), UNKNOWN)


@native_test
class PartyResolutionTest(ImageCase):
    def test_missing_manager_resolves_nothing(self):
        self.world.manager_pointer(False)
        self.assertEqual(self.slots(), {})
        self.assertEqual(self.scan().manager_ok, 0)

    def test_party_slot_decides_not_the_actor_index(self):
        a5 = self.world.add_actor(5, 1)
        a2 = self.world.add_actor(2, 0)
        got = self.slots()
        self.assertEqual({k: v.index for k, v in got.items()}, {0: 2, 1: 5})
        self.assertEqual(self.validity(a2.actor), YES)
        self.assertEqual(self.validity(a5.actor), YES)

    def test_ko_and_leader_handle_classes_and_rebuilt_generations(self):
        for classes in ((PARTY_CLASS, KO_CLASS), (KO_CLASS, LEADER_CLASS), (0x00400000, 0x00700000)):
            with self.subTest(classes=[hex(c) for c in classes]):
                img = Image()
                try:
                    w = GameWorld(img)
                    a0 = w.add_actor(0, 2, handle_class=classes[0])
                    a1 = w.add_actor(1, 0, handle_class=classes[1])
                    p = PartyActors()
                    self.d.ILCT_SesScanParty(img.base, ctypes.byref(p), None)
                    self.assertEqual((p.slot[2].actor, p.slot[0].actor), (a0.actor, a1.actor))
                    self.assertEqual(self.d.ILCT_SesActorInParty(img.base, a1.actor), YES)
                finally:
                    img.close()

    def test_a_stale_manager_index_alias_is_rejected(self):
        a = self.world.add_actor(0, 1)
        a.set_handle(0x00100001)   # the entry is index 0 but the actor names index 1: a rebuild alias
        self.assertEqual(self.slots(), {})
        self.assertEqual(self.validity(a.actor), NO)

    def test_two_actors_claiming_one_slot_are_both_dropped(self):
        self.world.add_actor(0, 1)
        self.world.add_actor(1, 1)
        keeper = self.world.add_actor(2, 0)
        self.assertEqual({k: v.actor for k, v in self.slots().items()}, {0: keeper.actor})

    def test_body_and_physics_must_agree(self):
        a = self.world.add_actor(0, 0)
        a.set_position((10.0, 2.0, 30.0), (12.0, 2.0, 30.0))
        self.assertEqual(self.slots(), {})
        a.set_position((10.0, 2.0, 30.0), (10.5, 2.5, 30.5))
        self.assertIn(0, self.slots())
        a.set_position((float('nan'), 2.0, 30.0))
        self.assertEqual(self.slots(), {})
        a.set_position((1.0e9, 2.0, 30.0))
        self.assertEqual(self.slots(), {})

    def test_validity_is_unknown_through_gaps_and_no_once_proven(self):
        a = self.world.add_actor(0, 0)
        self.world.manager_pointer(False)
        self.assertEqual(self.validity(a.actor), UNKNOWN)            # no manager: unknown, not a departure
        self.world.manager_pointer(True)
        self.world.remove_actor(0)
        self.world.count(1)
        self.assertEqual(self.validity(a.actor), NO)                 # a stable manager that does not list it
        other = self.world.add_actor(1, 1)
        self.img.protect(other.entry, PAGE, PAGE_NOACCESS)           # an unreadable entry could have listed it
        self.assertEqual(self.validity(a.actor), UNKNOWN)
        self.img.protect(other.entry, PAGE, PAGE_READWRITE)
        a.set_slot(7)
        self.world.img.p64(self.world.manager + 8, a.entry)
        self.assertEqual(self.validity(a.actor), NO)                 # its slot left the party

    def test_unreadable_pages_anywhere_in_the_scan_never_fault(self):
        actors = [self.world.add_actor(k, k % 4) for k in range(4)]
        for target in ('entry', 'actor', 'root', 'phys'):
            with self.subTest(target=target):
                victim = actors[1]
                self.img.protect(getattr(victim, target), PAGE, PAGE_NOACCESS)
                got = self.slots()
                self.assertNotIn(1, got)
                self.assertEqual({0, 2, 3} - set(got), set())
                self.img.protect(getattr(victim, target), PAGE, PAGE_READWRITE)
        # An unreadable battle record does not hide the actor (only its pointer is checked); its facts are unknown.
        self.img.protect(actors[1].battle, PAGE, PAGE_NOACCESS)
        self.assertEqual(set(self.slots()), {0, 1, 2, 3})
        self.assertEqual(self.fact(actors[1].actor, 0), UNKNOWN)
        self.img.protect(actors[1].battle, PAGE, PAGE_READWRITE)
        self.assertEqual(set(self.slots()), {0, 1, 2, 3})

    def test_per_slot_recheck(self):
        a = self.world.add_actor(3, 1)
        cached = self.scan().slot[1]
        self.assertEqual(self.d.ILCT_SesSlotHolds(self.img.base, 1, ctypes.byref(cached)), 1)
        self.assertEqual(self.d.ILCT_SesSlotHolds(self.img.base, 2, ctypes.byref(cached)), 0)
        a.set_handle(PARTY_CLASS | 3 | 0x00200000)
        self.assertEqual(self.d.ILCT_SesSlotHolds(self.img.base, 1, ctypes.byref(cached)), 0)

    def test_the_scan_reuses_checked_regions(self):
        for k in range(16):
            self.world.add_actor(k, 0 if k == 0 else 9)
        queries = u32()
        reads = self.d.ILCT_SesScanParty(self.img.base, ctypes.byref(PartyActors()), ctypes.byref(queries))
        self.assertLess(queries.value, reads, 'every read asked the kernel again')


@native_test
class WorldSampleTest(ImageCase):
    def sample(self) -> WorldSample:
        w = WorldSample()
        self.d.ILCT_SesSampleWorld(self.img.base, ctypes.byref(w), None)
        return w

    def test_field_sample(self):
        self.world.layout([0, 5, 1], [0, 5, 1])
        self.world.leader(0)
        w = self.sample()
        self.assertEqual((w.roots, w.control_enabled, w.menu_open, w.modal_active, w.town, w.chocobo),
                         (1, 1, 0, 0, 0, 0))
        self.assertEqual((list(w.current)[:4], w.leader, w.location_current), ([0, 5, 1, NONE], 0, 100))

    def test_each_flag(self):
        self.world.menu(True)
        self.assertEqual(self.sample().menu_open, 1)
        self.world.menu(False)
        self.world.modal(True)
        self.assertEqual(self.sample().modal_active, 1)
        self.world.modal(False)
        self.world.town_flag(True)
        self.assertEqual(self.sample().town, 1)
        self.world.town_flag(False)
        self.world.area_town(100, True)
        self.assertEqual(self.sample().town, 1)        # town from the area metadata
        self.world.area_town(100, False)
        self.world.chocobo(True)
        self.assertEqual(self.sample().chocobo, 1)
        self.world.control_enabled(False)
        self.assertEqual(self.sample().control_enabled, 0)

    def test_missing_or_unreadable_roots(self):
        self.world.roots(False)
        w = self.sample()
        self.assertEqual((w.roots, w.leader, list(w.current)[:4]), (0, NONE, [NONE] * 4))
        self.world.roots(True)
        self.img.protect(self.world.battle, 0x6000, PAGE_NOACCESS)
        self.assertEqual(self.sample().roots, 0)
        self.img.protect(self.world.battle, 0x6000, PAGE_READWRITE)
        self.img.protect(self.world.ui, PAGE, PAGE_NOACCESS)   # an unreadable UI manager is just "no modal"
        self.world.modal(False)
        self.assertEqual(self.sample().roots, 1)


# =====================================================================================================
# The session core: scripted world sequences on synthetic memory
# =====================================================================================================
class SessionCase(ImageCase):
    NOW0 = 10_000

    def setUp(self):
        super().setUp()
        self.d.ILCT_SesReset()
        self.now = self.NOW0

    def tearDown(self):
        self.d.ILCT_SesReset()
        super().tearDown()

    def configure(self, characters=(0, 5, 1, CHAR_NONE), has_input=0b0111, parked=0, enabled=1):
        cfg = SeatConfig()
        for k in range(4):
            cfg.character[k] = characters[k]
        cfg.has_input, cfg.parked, cfg.enabled = has_input, parked, enabled
        self.d.ILCT_SesConfigure(ctypes.byref(cfg))

    def start(self, **kw):
        self.configure(**kw)
        self.d.ILCT_SesStart(self.img.base, self.now)

    def step(self, n=1, dt=20):
        for _ in range(n):
            self.now += dt
            self.d.ILCT_SesStep(self.now)
        return self.record()

    def record(self) -> Record:
        r = Record()
        self.assertEqual(self.d.ILCT_SesRecord(ctypes.byref(r)), 1, 'no session record published')
        return r

    def debug(self) -> DebugView:
        v = DebugView()
        self.d.ILCT_SesDebug(ctypes.byref(v))
        return v

    def effects(self):
        buf = (PendingEffect * 32)()
        n = self.d.ILCT_SesTakeEffects(buf, 32)
        return [(buf[k].epoch, buf[k].fx.kind, buf[k].fx.token.member) for k in range(n)]

    def field(self, members=(0, 5, 1)):
        self.world.roots(True)
        self.world.control_enabled(True)
        return self.world.party(list(members))

    def assert_gates(self, rec, on=(), off=()):
        for name in on:
            self.assertTrue(rec.head.flags & GATE[name], f'gate {name} closed ({rec.head.flags:#x})')
        for name in off:
            self.assertFalse(rec.head.flags & GATE[name], f'gate {name} open ({rec.head.flags:#x})')

    def steering(self, rec):
        return [s for s in range(4) if rec.seat[s].flags & SEATF['STEERS']]


@native_test
class SessionLifecycleTest(SessionCase):
    def test_title_screen_then_field_then_map_jump_then_load_then_title(self):
        self.world.roots(False)
        self.start()
        rec = self.record()
        self.assertEqual((rec.head.epoch, rec.phase), (1, PHASE['NO_WORLD']))
        rec = self.step(3)
        # Co-op is logically on (guests under manual control) but nothing else opens without a world.
        self.assertEqual((rec.head.epoch, rec.phase, rec.head.flags), (1, PHASE['NO_WORLD'], GATE['LOGICAL']))
        self.assertEqual(self.d.ILCT_SesEpoch(), 1)
        # The world appears: a new epoch, play, both guests steer their own character.
        actors = self.field()
        rec = self.step(2)
        self.assertEqual((rec.head.epoch, rec.world_epoch, rec.phase), (2, 1, PHASE['FIELD']))
        self.assert_gates(rec, on=('WORLD', 'GAMEPLAY', 'MOVEMENT', 'LOGICAL', 'GUESTS_MOVE', 'SHARED_CAMERA',
                                   'INTERACTION'), off=('PARTY_DOWN',))
        self.assertEqual(self.steering(rec), [1, 2])
        self.assertEqual([rec.seat[s].member for s in range(4)], [0, 5, 1, NONE])
        self.assertEqual([rec.seat[s].actor for s in range(3)], [a.actor for a in actors])
        self.assertEqual([rec.seat[s].party_slot for s in range(4)], [0, 1, 2, 0xFF])
        self.assertTrue(rec.seat[0].flags & SEATF['LEADER'])
        self.assertEqual(rec.head.generation, self.d.ILCT_GtqGeneration(), 'the queue knows the seat layout')
        gen_field = rec.head.generation
        # Map jump: the area changes (the game rebuilds the actors at new addresses).
        self.world.place(101)
        for k in list(self.world.actors):
            self.world.remove_actor(k)
        actors = self.world.party([0, 5, 1], base_index=8)
        rec = self.step(2)
        self.assertEqual((rec.head.epoch, rec.world_epoch, rec.phase), (3, 2, PHASE['FIELD']))
        self.assertEqual(self.steering(rec), [1, 2])
        self.assertEqual(rec.seat[1].actor, actors[1].actor)
        self.assertGreater(rec.head.generation, gen_field)
        # A load: the HUD area lags behind the current one.
        self.world.place(102, 101)
        rec = self.step()
        self.assertEqual((rec.head.epoch, rec.phase), (4, PHASE['LOADING']))
        self.assert_gates(rec, off=('WORLD', 'MOVEMENT', 'GUESTS_MOVE', 'INTERACTION'))
        self.assertEqual(self.steering(rec), [])
        self.world.place(102)
        rec = self.step()
        self.assertEqual((rec.head.epoch, rec.world_epoch, rec.phase), (5, 3, PHASE['FIELD']))
        # Back to the title screen.
        self.world.roots(False)
        rec = self.step()
        self.assertEqual((rec.head.epoch, rec.phase), (6, PHASE['NO_WORLD']))
        self.assertEqual(rec.head.flags & ~GATE['LOGICAL'], 0)
        self.assertEqual([rec.seat[s].flags & SEATF['ACTOR'] for s in range(4)], [0] * 4)

    def test_same_area_after_an_invalid_stretch_is_a_new_world(self):
        self.field()
        self.start()
        rec = self.step(2)
        self.assertEqual((rec.head.epoch, rec.world_epoch), (2, 1))
        self.world.control_enabled(False)
        rec = self.step()
        self.assertEqual((rec.head.epoch, rec.phase), (3, PHASE['CUTSCENE']))
        self.world.control_enabled(True)
        rec = self.step()
        self.assertEqual((rec.head.epoch, rec.world_epoch), (4, 2))

    def test_menus_and_modals_close_movement_without_a_new_epoch(self):
        self.field()
        self.start()
        rec = self.step(2)
        epoch = rec.head.epoch
        for setter in (self.world.menu, self.world.modal):
            with self.subTest(setter=setter.__name__):
                setter(True)
                rec = self.step()
                self.assertEqual((rec.head.epoch, rec.phase), (epoch, PHASE['MENU']))
                self.assert_gates(rec, on=('WORLD',), off=('MOVEMENT', 'P1_MENU'))
                setter(False)
                rec = self.step()
                self.assertEqual((rec.head.epoch, rec.phase), (epoch, PHASE['FIELD']))

    def test_game_over_hands_input_to_the_game(self):
        actors = self.field()
        self.start()
        rec = self.step(2)
        epoch = rec.head.epoch
        for a in actors:
            a.set_hp(0)
            a.set_status(ST_KO)
        rec = self.step()
        self.assertEqual((rec.phase, rec.head.epoch), (PHASE['PARTY_DOWN'], epoch))
        self.assert_gates(rec, on=('PARTY_DOWN',), off=('MOVEMENT', 'GUESTS_MOVE', 'INTERACTION', 'P1_MENU',
                                                       'GUEST_MENU'))
        self.assertEqual(self.steering(rec), [])
        # The game over screen tears the world down: title.
        self.world.roots(False)
        rec = self.step()
        self.assertEqual((rec.phase, rec.head.epoch), (PHASE['NO_WORLD'], epoch + 1))

    def test_a_ko_guest_stops_steering_but_the_session_continues(self):
        actors = self.field()
        self.start()
        self.step(2)
        actors[1].set_hp(0)
        rec = self.step()
        self.assertEqual(self.steering(rec), [2])           # P3 keeps steering with P2 down
        self.assert_gates(rec, on=('LOGICAL', 'GUESTS_MOVE'))
        self.assertTrue(rec.seat[1].flags & SEATF['ALIVE_KNOWN'])
        self.assertFalse(rec.seat[1].flags & SEATF['ALIVE'])
        actors[1].set_hp(100)
        rec = self.step()
        self.assertEqual(self.steering(rec), [1, 2])

    def test_ko_promotion_keeps_the_promoted_guest_steering(self):
        actors = self.field()
        self.start()
        self.step(2)
        actors[0].set_hp(0)
        self.world.leader(5)   # the game promotes P2's character while P1 is down
        rec = self.step(15)
        self.assertTrue(rec.seat[1].flags & SEATF['KO_PROMOTED'])
        self.assertIn(1, self.steering(rec))
        self.assertEqual([rec.seat[s].member for s in range(3)], [0, 5, 1], 'a KO promotion is not a seat swap')


@native_test
class SessionSeatTableTest(SessionCase):
    def test_the_fourth_seat_never_takes_part(self):
        self.field(members=(0, 5, 1, 3))
        self.start(characters=(0, 5, 1, 3), has_input=0b1111)
        rec = self.step(2)
        self.assertEqual(rec.seat[3].flags, 0)
        self.assertEqual((rec.seat[3].member, rec.seat[3].actor), (NONE, 0))
        self.assertEqual(self.steering(rec), [1, 2])
        self.assertEqual(self.debug().configured.in_session & 0b1000, 0)

    def test_parked_and_characterless_guests(self):
        self.field()
        self.start(parked=0b100)
        rec = self.step(2)
        self.assertEqual(self.steering(rec), [1])
        self.assertTrue(rec.seat[2].flags & SEATF['IN_SESSION'])
        self.assertFalse(rec.seat[2].flags & SEATF['MANUAL'])
        self.d.ILCT_SesSetParked(0b010)   # live park (the input service's) on top
        rec = self.step()
        self.assertEqual(self.steering(rec), [])
        self.configure(characters=(0, 5, CHAR_NONE, CHAR_NONE))
        self.d.ILCT_SesSetParked(0)
        rec = self.step()
        self.assertEqual(self.steering(rec), [1])
        self.assertEqual((rec.seat[2].flags, rec.seat[2].member), (0, NONE))

    def test_auto_characters_fill_from_the_party(self):
        self.field(members=(3, 2, 4))
        self.start(characters=(CHAR_AUTO, CHAR_AUTO, CHAR_AUTO, CHAR_NONE))
        rec = self.step(2)
        self.assertEqual([rec.seat[s].member for s in range(3)], [0, NONE, NONE], 'adopted before it settled')
        rec = self.step(12)
        self.assertEqual([rec.seat[s].member for s in range(3)], [3, 2, 4])
        self.assertEqual(self.steering(rec), [1, 2])

    def test_an_external_leader_change_is_adopted_as_a_two_seat_swap(self):
        self.field(members=(0, 5, 1, 3))
        self.start()
        self.step(2)
        self.world.leader(5)   # another mod (or the game) makes P2's character the leader
        rec = self.step()
        self.assertEqual([rec.seat[s].member for s in range(3)], [0, 5, 1], 'adopted before it settled')
        rec = self.step(12)
        self.assertEqual([rec.seat[s].member for s in range(3)], [5, 0, 1])
        self.assertEqual(self.steering(rec), [1, 2])
        self.assertEqual(rec.seat[1].actor, self.world.actors[0].actor)
        self.assertGreater(self.debug().owners.has_owner[1], 0)

    def assert_quiet_reconfigure(self, rec_before, configure, members):
        effects_before = self.effects()
        del effects_before
        configure()
        rec = self.step(3)
        self.assertEqual([rec.seat[s].member for s in range(3)], members)
        self.assertEqual((rec.head.epoch, rec.head.generation), (rec_before.head.epoch, rec_before.head.generation),
                         'a settings change that does not touch seats moved the epoch or the seat layout')
        self.assertEqual(self.effects(), [], 'a settings change released a guest')
        return rec

    def test_settings_that_do_not_touch_seats_keep_auto_characters(self):
        self.field(members=(3, 2, 4))
        self.start(characters=(CHAR_AUTO, CHAR_AUTO, CHAR_AUTO, CHAR_NONE))
        rec = self.step(14)
        self.assertEqual([rec.seat[s].member for s in range(3)], [3, 2, 4])
        self.effects()
        # Same seat fields (what a colour or sound change produces).
        rec = self.assert_quiet_reconfigure(rec, lambda: self.configure(characters=(CHAR_AUTO, CHAR_AUTO, CHAR_AUTO,
                                                                                    CHAR_NONE)), [3, 2, 4])
        self.assertEqual(self.steering(rec), [1, 2])
        # Parking P3 changes who steers, never who controls whom.
        self.configure(characters=(CHAR_AUTO, CHAR_AUTO, CHAR_AUTO, CHAR_NONE), parked=0b100)
        rec = self.step(3)
        self.assertEqual([rec.seat[s].member for s in range(3)], [3, 2, 4])
        self.assertEqual(self.steering(rec), [1])

    def test_settings_that_do_not_touch_seats_keep_an_adopted_swap(self):
        self.field(members=(0, 5, 1, 3))
        self.start()
        self.step(2)
        self.world.leader(5)
        rec = self.step(14)
        self.assertEqual([rec.seat[s].member for s in range(3)], [5, 0, 1])
        self.effects()
        rec = self.assert_quiet_reconfigure(rec, lambda: self.configure(), [5, 0, 1])
        # Another seat's character changes: the swap holds, only that seat moves.
        self.configure(characters=(0, 5, 3, CHAR_NONE))
        rec = self.step(3)
        self.assertEqual([rec.seat[s].member for s in range(3)], [5, 0, 3])

    def test_a_colour_change_through_the_settings_record(self):
        from coop_native_support import SCHEMA as SC
        record = bytearray(SC.defaults())
        kind_at = SC.locate('ilc_settings', ['seats', 1, 'device', 'kind'])[0]
        record[kind_at] = SC.e('device_kind', 'XINPUT')
        record[SC.locate('ilc_settings', ['seats', 1, 'character'])[0]] = 5
        record[SC.locate('ilc_settings', ['seats', 0, 'character'])[0]] = 0
        self.field(members=(0, 5, 1))
        buf = ctypes.create_string_buffer(bytes(record), len(record))
        self.d.ILCT_SesConfigureFromSettings(buf)
        self.d.ILCT_SesStart(self.img.base, self.now)
        rec = self.step(3)
        self.assertEqual(self.steering(rec), [1])
        self.effects()
        colour = SC.locate('ilc_settings', ['seats', 1, 'colour'])[0]
        record[colour] = (record[colour] + 1) % 4
        buf = ctypes.create_string_buffer(bytes(record), len(record))
        self.assert_quiet_reconfigure(rec, lambda: self.d.ILCT_SesConfigureFromSettings(buf), [0, 5, NONE])

    def test_the_queue_follows_the_world_of_each_epoch(self):
        self.world.roots(False)
        self.start()
        self.step()
        self.assertEqual(self.d.ILCT_GtqWorld(), (1 << 32) | 0x2, 'title screen: epoch 1, no valid world')
        self.field()
        self.step(2)
        self.assertEqual(self.d.ILCT_GtqWorld(), (2 << 32) | 0x3)
        self.world.control_enabled(False)
        self.step()
        self.assertEqual(self.d.ILCT_GtqWorld(), (3 << 32) | 0x2, 'a cutscene epoch is fresh but not a valid world')
        self.d.ILCT_SesStop()
        self.assertEqual(self.d.ILCT_GtqWorld() & 0x3, 0x2)

    def test_berserk_p1_is_not_a_leader_unless_movement_is_live(self):
        actors = self.field()
        self.start()
        self.step(2)
        actors[0].set_status(ST_BERSERK)
        self.world.leader(5)
        # Without the override the session asks the Berserk gate itself (MV-10's gate open and MOVEMENT live), not
        # merely whether MOVEMENT is armed: here nothing is installed, so a Berserk P1 cannot lead.
        self.d.ILCT_SesForceMovementLive(-1)
        rec = self.step(15)
        self.assertTrue(rec.seat[1].flags & SEATF['KO_PROMOTED'], 'no Berserk gate: KO promotion')
        self.d.ILCT_SesForceMovementLive(0)
        rec = self.step(15)
        self.assertTrue(rec.seat[1].flags & SEATF['KO_PROMOTED'], 'a Berserk P1 cannot lead without the gate patch')
        self.d.ILCT_SesForceMovementLive(1)
        rec = self.step(15)
        self.assertFalse(rec.seat[1].flags & SEATF['KO_PROMOTED'])
        self.assertEqual([rec.seat[s].member for s in range(3)], [5, 0, 1], 'an able P1: the leader change is a swap')
        source = (ROOT / 'native' / 'coop' / 'src' / 'session' / 'session.cpp').read_text(encoding='utf-8')
        self.assertIn('movement::berserk_gate_live()', source)
        self.assertNotIn('patch::subsystem_live(ILC_SITE_SUBSYS_MOVEMENT)', source)

    def test_an_unreadable_actor_keeps_its_owner_through_the_gap(self):
        actors = self.field()
        self.start()
        rec = self.step(2)
        self.assertEqual(self.steering(rec), [1, 2])
        self.effects()
        self.img.protect(actors[2].actor, 2 * PAGE, PAGE_NOACCESS)
        rec = self.step()
        self.assertFalse(rec.seat[2].flags & SEATF['ACTOR'])
        self.assertEqual(self.debug().owners.has_owner[2], 1, 'a read gap released the owner')
        self.assertEqual(self.effects(), [])
        self.img.protect(actors[2].actor, 2 * PAGE, PAGE_READWRITE)
        rec = self.step(4)   # the missing actor is looked for again within a few beats
        self.assertEqual(self.steering(rec), [1, 2])
        self.assertEqual(self.effects(), [], 'the owner came back without a release and re-acquire')

    def test_switching_a_guest_off_releases_and_asks_the_game_to_take_it_back(self):
        self.field()
        self.start()
        rec = self.step(2)
        epoch = rec.head.epoch
        self.effects()
        self.d.ILCT_SesSetParked(0b010)
        rec = self.step()
        self.assertEqual(self.steering(rec), [2])
        self.assertEqual(self.effects(), [(epoch, FX['ASK_FOLLOW'], 5)])
        self.assertFalse(self.debug().owners.has_owner[1])

    def test_generation_follows_the_seat_layout(self):
        self.field()
        self.start()
        rec = self.step(2)
        g = rec.head.generation
        rec = self.step(3)
        self.assertEqual(rec.head.generation, g, 'nothing changed, the generation moved')
        self.d.ILCT_SesSetParked(0b100)
        rec = self.step()
        self.assertGreater(rec.head.generation, g)
        self.assertEqual(self.d.ILCT_GtqGeneration(), rec.head.generation)
        self.assertEqual(self.d.ILCT_SesGeneration(), rec.head.generation)

    def test_town_keeps_guest_interaction_open(self):
        self.field()
        self.world.town_flag(True)
        self.start()
        rec = self.step(2)
        self.assertTrue(rec.facts & FACT['TOWN'])
        self.assert_gates(rec, on=('INTERACTION', 'MOVEMENT'), off=('P1_MENU', 'GUEST_MENU'))

    def test_stop_withdraws_the_record(self):
        self.field()
        self.start()
        self.step(2)
        self.d.ILCT_SesStop()
        self.assertEqual(self.d.ILCT_SesRecord(ctypes.byref(Record())), 0)

    def test_the_actor_scan_is_rate_limited(self):
        self.field()
        self.start()
        self.step(2)
        before = Counters()
        self.d.ILCT_SesStats(ctypes.byref(before))
        self.step(5, dt=20)   # 100 ms, nothing changes: cached slots are re-checked, no full scan
        after = Counters()
        self.d.ILCT_SesStats(ctypes.byref(after))
        self.assertEqual(after.scans, before.scans)
        self.step(1, dt=300)
        self.d.ILCT_SesStats(ctypes.byref(after))
        self.assertEqual(after.scans, before.scans + 1)


if __name__ == '__main__':
    unittest.main()
