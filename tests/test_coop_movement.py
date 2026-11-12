"""Host tests for guest movement (native/coop/src/movement). No game run, nothing deployed; the exe is only read
(IVALICE_LEGACY_GAME, a copy) for the image-twin and emulator classes.

* steering maths, restated as our own tables: the stick response plateaus (nothing up to 0.40, a short ramp, a flat
  walk at one third of the step from 0.55, the full step from 0.92), camera-relative headings, walk and armed steps,
  turn rate and its clamps, the camera basis from the view matrix and its jump filter;
* the planner on plain structs: who gets a steering entry, headings seeded from the actor when control starts,
  walk/run toggling on presses only, execution motions, menus (entry kept, command zero), flee, seat cap;
* the co-op-thread step on synthetic game memory (session record published directly, inputs injected): facts read
  through the fault-safe reader, the record and flee request published, follow effects turned into jobs;
* the follow operation on synthetic actors: applied, already following, and every refusal leaves memory unchanged;
* the handlers natively on saved machine states (the image twin installed, LIVE): fail-open in every refused
  condition, and the armed behaviour per site;
* unicorn on the hooked twin: per site, the hooked path equals the original bytes whenever movement must not act
  (record missing, other epoch, other actor, MOVEMENT bit clear, DRY) and does exactly the armed change otherwise;
  MV-04 and MV-07 stay stock with movement armed; and the game's own locomotion function 0x2E4380, run whole from
  the hooked entry, turns the guest's heading and command into its output vector (approach gate run to its return).
"""
from __future__ import annotations

import ctypes
import math
import random
import struct
import unittest

import test_coop_game_thread as gt
import test_coop_patch_engine as pe
from coop_native_support import native_test
from coop_session_support import (FOLLOW_LIST_PRIMARY, FOLLOW_LIST_SECONDARY, GATE, SEATF, GameWorld, Image,
                                  Record, RecordHead)
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, Twin, checked

u8, u16, u32, u64, f32, f64 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_float,
                               ctypes.c_double)
P = ctypes.POINTER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


SteerEntry = S('SteerEntry', [('actor', u64), ('phys', u64), ('root', u64), ('heading', f32), ('visual', f32),
                              ('magnitude', f32), ('member', u16), ('seat', u8), ('flags', u8), ('handle', u32),
                              ('actor_generation', u32)])
SteerRecord = S('SteerRecord', [('head', RecordHead), ('active', u32), ('reserved', u32), ('slot', SteerEntry * 2)])
SeatInput = S('SeatInput', [('present', u8), ('walk_toggle', u8), ('flee', u8), ('hold', u8), ('up', u16),
                            ('down', u16), ('left', u16), ('right', u16)])
ActorFacts = S('ActorFacts', [('ok', u8), ('armed', u8), ('execution', u8), ('has_target', u8), ('headings_ok', u8),
                              ('reserved', u8 * 3), ('root', u64), ('target_heading', f32), ('phys_heading', f32),
                              ('phys_visual', f32), ('reserved2', u32)])
PlanInputs = S('PlanInputs', [('session', Record), ('input', SeatInput * 4), ('actor', ActorFacts * 4),
                              ('camera_ok', u8), ('reserved', u8 * 3), ('view', f32 * 16), ('now_ms', u64)])
Basis = S('Basis', [('fx', f64), ('fz', f64), ('rx', f64), ('rz', f64)])
CameraFilter = S('CameraFilter', [('pending_heading', f64), ('pending_count', u32), ('reserved', u32)])
SlotState = S('SlotState', [('actor', u64), ('phys', u64), ('actor_generation', u32), ('world_epoch', u32),
                            ('last_heading', f64), ('visual', f64), ('walk', u8), ('reserved', u8 * 7)])
PlannerState = S('PlannerState', [('last_ms', u64), ('camera', Basis), ('filter', CameraFilter),
                                  ('slot', SlotState * 2)])
PlanOutputs = S('PlanOutputs', [('record', SteerRecord), ('flee_seats', u32), ('started', u32), ('waiting', u32),
                                ('camera_accepted', u32)])
SteerResult = S('SteerResult', [('moving', u8), ('reserved', u8 * 7), ('heading', f64), ('magnitude', f64)])
COUNTER_NAMES = ('steps', 'published', 'withdrawn', 'camera_reads', 'facts_failed', 'follow_pushed',
                 'follow_refused', 'follow_stale', 'follow_applied', 'follow_rejected', 'list_rejected',
                 'approach_blocked', 'vectors_written', 'root_motion_overridden', 'facings_set')
Counters = S('Counters', [(n, u64) for n in COUNTER_NAMES])
GPR = ('rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15')
Ctx = S('Ctx', [('xmm', u64 * 12), ('resume', u64), ('reserved', u64)] + [(n, u64) for n in GPR] +
        [('rflags', u64), ('index', u64)])
LAYOUTS = [SteerEntry, SteerRecord, SeatInput, ActorFacts, PlanInputs, PlannerState, PlanOutputs, Counters, Basis,
           SteerResult, CameraFilter, SlotState]

ENTRY_ACTIVE, ENTRY_MOVING = 1, 2
STEERF_MOVE, STEERF_LOGICAL = 1, 2
RUN_ORIGINAL, RESUME = 0, 1
OP_FOLLOW = 8
APPLIED, ALREADY = 1, 2
STALE_IDENTITY, STALE_PHYSICS, NOT_READY, ACTION_CHANGED, UNREADABLE, BAD_ARGS, NO_WORLD = range(-16, -23, -1)
ACTION_FOLLOW, ACTION_IDLE = 0x4124, 0x4021
MOVEMENT = 0x02
FULL = 65535
ACT_WALK_RUN = 5

SIGNATURES = {
    'ILCT_MoveSizes': ([P(u32), u32], u32),
    'ILCT_MoveConstants': ([P(f64)], None),
    'ILCT_MoveWrap': ([f64], f64),
    'ILCT_MoveShare': ([f64], f64),
    'ILCT_MoveSteer': ([f64, f64, P(Basis), ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, P(SteerResult)], None),
    'ILCT_MoveTurn': ([f64, f64, f64, f64], f64),
    'ILCT_MoveBasis': ([P(f32), P(Basis), P(CameraFilter), P(Basis)], ctypes.c_int32),
    'ILCT_MoveDefaultBasis': ([P(Basis)], None),
    'ILCT_MovePlanReset': ([P(PlannerState)], None),
    'ILCT_MovePlan': ([P(PlannerState), P(PlanInputs), P(PlanOutputs)], None),
    'ILCT_MovePublish': ([P(SteerRecord)], None),
    'ILCT_MoveWithdraw': ([], None),
    'ILCT_MoveRecord': ([P(SteerRecord)], ctypes.c_int32),
    'ILCT_MoveAttach': ([], ctypes.c_int32),
    'ILCT_MoveRow': ([ctypes.c_char_p], ctypes.c_int32),
    'ILCT_MoveInvoke': ([u32, P(Ctx), u32], ctypes.c_int32),
    'ILCT_MoveFollow': ([u64, u64, u32, u32], ctypes.c_int32),
    'ILCT_MoveSetImageBase': ([u64], None),
    'ILCT_MoveStats': ([P(Counters)], None),
    'ILCT_MoveReset': ([], None),
    'ILCT_MoveStart': ([u64, u64], None),
    'ILCT_MoveStep': ([u64], None),
    'ILCT_MoveStop': ([], None),
    'ILCT_MoveSetInputs': ([P(SeatInput), u32], None),
    'ILCT_MoveLast': ([P(PlanOutputs)], None),
    'ILCT_MoveFlee': ([u32], u32),
    'ILCT_MoveForceFleeLive': ([ctypes.c_int32], None),
    'ILCT_MoveBerserkGate': ([], ctypes.c_int32),
    'ILCT_MoveSessionPublish': ([P(Record)], None),
    'ILCT_MoveSessionWithdraw': ([], None),
    'ILCT_GtqPushRaw': ([u32, u32, u64, u32, u64, u64, P(u32)], ctypes.c_int32),
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


def constants(d) -> dict:
    out = (f64 * 13)()
    d.ILCT_MoveConstants(out)
    names = ('RUN', 'ARMED', 'WALK', 'WALK_START', 'WALK_FULL', 'RUN_START', 'SHARE', 'TURN', 'ACTION_TURN',
             'DT_MAX', 'CAM_STEP', 'CAM_CONFIRM', 'TURN_DEG')
    return dict(zip(names, out))


NORTH = Basis(0.0, 1.0, 1.0, 0.0)  # forward +Z, right +X


def steering_record(epoch=5, gates=None, seats=None, world_epoch=3, generation=9) -> Record:
    """A session record: seats = {s: dict(flags=, actor=, phys=, member=, handle=, gen=)}."""
    rec = Record()
    rec.head.epoch = epoch
    rec.head.flags = (GATE['WORLD'] | GATE['LOGICAL'] | GATE['MOVEMENT'] | GATE['GUESTS_MOVE']) if gates is None \
        else gates
    rec.head.generation = generation
    rec.world_epoch = world_epoch
    for s in range(4):
        rec.seat[s].member = 0xFFFF
        rec.seat[s].party_slot = 0xFF
    for s, v in (seats or {}).items():
        e = rec.seat[s]
        e.flags = v.get('flags', SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['ACTOR'] | SEATF['STEERS'] |
                        SEATF['ALIVE'] | SEATF['ALIVE_KNOWN'] | SEATF['COHERENT'])
        e.actor, e.phys = v['actor'], v['phys']
        e.member, e.handle, e.actor_generation = v.get('member', s), v.get('handle', 0x100000 | s), v.get('gen', 1)
    return rec


def steering_seat(actor, phys, **kw):
    return dict(actor=actor, phys=phys, **kw)


def facts(root, heading=0.0, visual=0.0, armed=0, execution=0, target=None, ok=1, headings_ok=1) -> ActorFacts:
    f = ActorFacts()
    f.ok, f.root, f.armed, f.execution = ok, root, armed, execution
    f.headings_ok, f.phys_heading, f.phys_visual = headings_ok, heading, visual
    if target is not None:
        f.has_target, f.target_heading = 1, target
    return f


def stick(forward=0.0, right=0.0, present=1, walk_toggle=0, flee=0, hold=0) -> SeatInput:
    si = SeatInput()
    si.present, si.walk_toggle, si.flee, si.hold = present, walk_toggle, flee, hold
    si.up, si.down = (round(forward * FULL), 0) if forward >= 0 else (0, round(-forward * FULL))
    si.right, si.left = (round(right * FULL), 0) if right >= 0 else (0, round(-right * FULL))
    return si


def view_for_heading(heading: float) -> list[float]:
    """A view matrix whose camera-forward world heading is `heading` (rotation about Y, translation arbitrary)."""
    yaw = math.pi - heading  # the module reads heading = PI - atan2(m[8], m[10])
    s, c = math.sin(yaw), math.cos(yaw)
    return [c, 0.0, -s, 0.0,
            0.0, 1.0, 0.0, 0.0,
            s, 0.0, c, 0.0,
            12.0, -3.0, 40.0, 1.0]


# ---- layouts and steering maths --------------------------------------------------------------------------------
@native_test
class LayoutTest(unittest.TestCase):
    def test_struct_mirrors_match(self):
        d = pure_dll()
        out = (u32 * 32)()
        n = d.ILCT_MoveSizes(out, 32)
        self.assertEqual(n, len(LAYOUTS))
        for k, cls in enumerate(LAYOUTS):
            self.assertEqual(out[k], ctypes.sizeof(cls), cls.__name__)
        self.assertEqual(ctypes.sizeof(Ctx), 0x100)
        self.assertLessEqual(ctypes.sizeof(SteerRecord), 128, 'record copied at mid-function sites stays small')


@native_test
class StickResponseTest(unittest.TestCase):
    """The leader's measured response curve (restated as our own rows)."""

    @classmethod
    def setUpClass(cls):
        cls.d = pure_dll()
        cls.c = constants(cls.d)

    def share(self, x):
        return self.d.ILCT_MoveShare(x)

    def test_constants(self):
        c = self.c
        self.assertEqual((c['RUN'], c['WALK'], c['WALK_START'], c['WALK_FULL'], c['RUN_START']),
                         (0.10, 0.04, 0.40, 0.55, 0.92))
        self.assertAlmostEqual(c['ARMED'], 1 / 12)
        self.assertAlmostEqual(c['SHARE'], 1 / 3)
        self.assertEqual((c['ACTION_TURN'], c['DT_MAX'], c['CAM_STEP'], c['CAM_CONFIRM']), (25.0, 0.05, 0.35, 3))

    def test_dead_zone_up_to_four_tenths(self):
        for x in (0.0, 0.05, 0.2, 0.39, 0.40, -0.3, float('nan')):
            self.assertEqual(self.share(x), 0.0, x)

    def test_flat_walk_is_a_third_of_the_run_step(self):
        for x in (0.55, 0.6, 0.7, 0.8, 0.91):
            self.assertAlmostEqual(self.share(x) * self.c['RUN'], 0.1 / 3, places=12, msg=x)

    def test_run_plateau_is_the_whole_step(self):
        for x in (0.92, 0.95, 0.999, 1.0, 1.5):
            self.assertEqual(self.share(x), 1.0, x)

    def test_only_two_values_from_the_walk_plateau_up(self):
        values = {round(self.share(k / 1000.0), 9) for k in range(550, 1001)}
        self.assertEqual(values, {round(1 / 3, 9), 1.0})

    def test_ramp_rises_strictly_and_stays_below_the_walk(self):
        previous = 0.0
        for k in range(401, 550, 7):
            v = self.share(k / 1000.0)
            self.assertGreater(v, previous)
            self.assertLess(v, 1 / 3)
            previous = v
        self.assertAlmostEqual(self.share(0.475), (1 / 3) * 0.5)

    def test_turn_rate(self):
        c = self.c
        self.assertAlmostEqual(math.degrees(c['TURN']) / 60.0, 13.63, places=9)
        self.assertGreater(c['TURN'], 11.25)
        self.assertGreater(c['ACTION_TURN'], c['TURN'])


@native_test
class SteerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pure_dll()
        cls.c = constants(cls.d)

    def steer(self, forward=0.0, right=0.0, walk=False, armed=False, allowed=True, basis=NORTH):
        out = SteerResult()
        self.d.ILCT_MoveSteer(forward, right, ctypes.byref(basis), int(walk), int(armed), int(allowed),
                              ctypes.byref(out))
        return out

    def test_no_input_requests_nothing(self):
        r = self.steer()
        self.assertEqual((r.moving, r.magnitude), (0, 0.0))

    def test_directions_in_the_camera_basis(self):
        for forward, right, heading in ((1, 0, 0.0), (0, 1, math.pi / 2), (0, -1, -math.pi / 2), (1, 1, math.pi / 4)):
            r = self.steer(forward, right)
            self.assertEqual(r.moving, 1)
            self.assertAlmostEqual(r.heading, heading)
        self.assertAlmostEqual(abs(self.steer(-1, 0).heading), math.pi)

    def test_full_stick_runs_walk_mode_walks_armed_runs_slower(self):
        self.assertAlmostEqual(self.steer(1).magnitude, 0.10)
        self.assertAlmostEqual(self.steer(1, walk=True).magnitude, 0.04)
        self.assertAlmostEqual(self.steer(1, armed=True).magnitude, 1 / 12)
        self.assertAlmostEqual(self.steer(1, walk=True, armed=True).magnitude, 0.04, msg='walk wins over armed')

    def test_diagonal_is_not_faster_than_straight(self):
        self.assertLessEqual(self.steer(1, 1).magnitude, self.steer(1).magnitude + 1e-12)

    def test_partial_stick_follows_the_curve(self):
        r = self.steer(0.5)
        self.assertAlmostEqual(r.magnitude, 0.10 * self.d.ILCT_MoveShare(0.5))
        self.assertLess(r.magnitude, 0.10 / 3)
        self.assertAlmostEqual(self.steer(0.95).magnitude, 0.10)
        self.assertEqual(self.steer(0.3).magnitude, 0.0)
        self.assertEqual(self.steer(0.3).moving, 1, 'a light touch still turns toward the stick')

    def test_disallowed_requests_nothing(self):
        r = self.steer(1, allowed=False)
        self.assertEqual((r.moving, r.magnitude), (0, 0.0))

    def test_matches_the_basis_formula(self):
        rng = random.Random(21)
        for _ in range(200):
            h = rng.uniform(-math.pi, math.pi)
            b = Basis(math.sin(h), math.cos(h), -math.cos(h), math.sin(h))
            f, r_ = rng.uniform(-1, 1), rng.uniform(-1, 1)
            got = self.steer(f, r_, basis=b)
            x, z = f * b.fx + r_ * b.rx, f * b.fz + r_ * b.rz
            self.assertAlmostEqual(got.heading, math.atan2(x, z))
            self.assertAlmostEqual(got.magnitude, 0.10 * self.d.ILCT_MoveShare(min(1.0, math.hypot(f, r_))))

    def test_default_basis(self):
        b = Basis()
        self.d.ILCT_MoveDefaultBasis(ctypes.byref(b))
        self.assertEqual((b.fx, b.fz, b.rx, b.rz), (0.0, 1.0, -1.0, 0.0))


@native_test
class TurnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pure_dll()
        cls.c = constants(cls.d)

    def turn(self, v, t, rate=None, dt=1 / 60):
        return self.d.ILCT_MoveTurn(v, t, self.c['TURN'] if rate is None else rate, dt)

    def test_one_frame_is_capped_at_the_native_rate(self):
        self.assertAlmostEqual(math.degrees(self.turn(0.0, 2.0)), 13.63, places=6)
        self.assertAlmostEqual(self.turn(0.0, 0.1), 0.1, msg='a small turn completes')

    def test_takes_the_shorter_way_round_and_wraps(self):
        got = self.turn(3.0, -3.0)  # 0.283 rad the short way, across PI: one frame's cap, then wrapped
        self.assertAlmostEqual(got, self.d.ILCT_MoveWrap(3.0 + math.radians(13.63)), places=9)
        self.assertGreater(abs(got), 3.0)
        for v in (-math.pi, 3.1, -3.1):
            self.assertLessEqual(abs(self.turn(v, v + 3.0)), math.pi + 1e-12)

    def test_dt_is_clamped(self):
        self.assertAlmostEqual(self.turn(0.0, 3.0, dt=1.0), self.c['TURN'] * 0.05)
        self.assertEqual(self.turn(0.5, 3.0, dt=-1.0), self.d.ILCT_MoveWrap(0.5))

    def test_action_rate_is_faster(self):
        self.assertGreater(self.turn(0.0, 3.0, rate=self.c['ACTION_TURN']), self.turn(0.0, 3.0))


@native_test
class CameraBasisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = pure_dll()

    def basis(self, view, previous, flt):
        out = Basis()
        accepted = self.d.ILCT_MoveBasis((f32 * 16)(*view), ctypes.byref(previous), ctypes.byref(flt),
                                         ctypes.byref(out))
        return accepted, out

    @staticmethod
    def heading(b):
        return math.atan2(b.fx, b.fz)

    def test_reads_the_rendered_yaw(self):
        for h in (0.0, 0.3, -2.0, 2.7669):
            prev = Basis(math.sin(h + 0.1), math.cos(h + 0.1), 0, 0)
            ok, b = self.basis(view_for_heading(h), prev, CameraFilter())
            self.assertEqual(ok, 1)
            self.assertAlmostEqual(self.heading(b), h, places=5)
            self.assertAlmostEqual(math.hypot(b.fx, b.fz), 1.0)
            self.assertAlmostEqual((b.rx, b.rz), (-b.fz, b.fx))

    def test_refuses_a_matrix_that_is_not_a_rotation(self):
        prev = Basis(0.0, 1.0, -1.0, 0.0)
        bad = [view_for_heading(0.1), view_for_heading(0.1), view_for_heading(0.1)]
        bad[0][0] = 1.5                     # row not unit length
        bad[1][4] = 0.3                     # rows not orthogonal
        bad[2][10] = float('nan')
        for view in bad:
            ok, b = self.basis(view, prev, CameraFilter())
            self.assertEqual((ok, b.fx, b.fz), (0, 0.0, 1.0))

    def test_a_jump_needs_three_samples(self):
        prev, flt = Basis(0.0, 1.0, -1.0, 0.0), CameraFilter()
        jump = view_for_heading(1.5)
        results = [self.basis(jump, prev, flt) for _ in range(3)]
        self.assertEqual([r[0] for r in results], [0, 0, 1])
        self.assertAlmostEqual(self.heading(results[2][1]), 1.5, places=5)
        self.assertEqual(flt.pending_count, 0)

    def test_an_interrupted_jump_starts_counting_again(self):
        prev, flt = Basis(0.0, 1.0, -1.0, 0.0), CameraFilter()
        self.assertEqual(self.basis(view_for_heading(1.5), prev, flt)[0], 0)
        self.assertEqual(self.basis(view_for_heading(-1.5), prev, flt)[0], 0)  # another jump: new pending
        self.assertEqual(self.basis(view_for_heading(-1.5), prev, flt)[0], 0)
        self.assertEqual(self.basis(view_for_heading(-1.4), prev, flt)[0], 1)  # within the step of the pending one

    def test_a_small_step_is_taken_at_once_and_clears_pending(self):
        prev, flt = Basis(0.0, 1.0, -1.0, 0.0), CameraFilter()
        self.basis(view_for_heading(2.0), prev, flt)
        ok, _ = self.basis(view_for_heading(0.3), prev, flt)
        self.assertEqual((ok, flt.pending_count), (1, 0))


# ---- the planner ------------------------------------------------------------------------------------------------
ACTOR2, PHYS2, ROOT2 = 0x7000_0000, 0x7100_0000, 0x7200_0000
ACTOR3, PHYS3, ROOT3 = 0x7300_0000, 0x7400_0000, 0x7500_0000


@native_test
class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.d = pure_dll()
        self.st = PlannerState()
        self.d.ILCT_MovePlanReset(ctypes.byref(self.st))
        self.now = 10_000

    def plan(self, rec, inputs=None, actor=None, view=None, dt_ms=16) -> PlanOutputs:
        self.now += dt_ms
        pin = PlanInputs()
        pin.session = rec
        for s, si in (inputs or {}).items():
            pin.input[s] = si
        for s, f in (actor or {}).items():
            pin.actor[s] = f
        if view is not None:
            pin.camera_ok = 1
            for k, v in enumerate(view):
                pin.view[k] = v
        pin.now_ms = self.now
        out = PlanOutputs()
        self.d.ILCT_MovePlan(ctypes.byref(self.st), ctypes.byref(pin), ctypes.byref(out))
        return out

    def two_guests(self, **kw):
        return steering_record(seats={1: steering_seat(ACTOR2, PHYS2), 2: steering_seat(ACTOR3, PHYS3)}, **kw)

    def test_each_steering_guest_gets_an_entry(self):
        out = self.plan(self.two_guests(), actor={1: facts(ROOT2, 0.5, 0.4), 2: facts(ROOT3, -1.0, -1.0)})
        r = out.record
        self.assertEqual((r.head.epoch, r.head.generation, r.head.flags, r.active), (5, 9, STEERF_MOVE |
                                                                                     STEERF_LOGICAL, 0b11))
        e = r.slot[0]
        self.assertEqual((e.actor, e.phys, e.root, e.seat, e.member, e.flags), (ACTOR2, PHYS2, ROOT2, 1, 1,
                                                                                ENTRY_ACTIVE))
        self.assertAlmostEqual(e.heading, 0.5, places=6)
        self.assertAlmostEqual(e.visual, 0.4, places=6, msg='taking control never snaps the body round')
        self.assertEqual(out.started, 0b11)
        self.assertEqual(r.slot[1].seat, 2)

    def test_no_entry_without_steering(self):
        rec = self.two_guests()
        rec.seat[2].flags &= ~SEATF['STEERS']
        out = self.plan(rec, actor={1: facts(ROOT2), 2: facts(ROOT3)})
        self.assertEqual(out.record.active, 0b01)
        self.assertEqual(out.record.slot[1].flags, 0)

    def test_fourth_seat_never_steers(self):
        rec = steering_record(seats={3: steering_seat(ACTOR3, PHYS3)})
        out = self.plan(rec, actor={3: facts(ROOT3)}, inputs={3: stick(1.0, flee=1)})
        self.assertEqual((out.record.active, out.flee_seats), (0, 0))

    def test_missing_facts_or_headings_wait(self):
        out = self.plan(self.two_guests(), actor={1: facts(ROOT2, ok=0), 2: facts(ROOT3, headings_ok=0)})
        self.assertEqual((out.record.active, out.waiting, out.started), (0, 0b11, 0))
        out = self.plan(self.two_guests(), actor={1: facts(ROOT2), 2: facts(ROOT3, float('nan'), 0.0)})
        self.assertEqual((out.record.active, out.waiting), (0b01, 0b10))

    def test_stick_drives_heading_and_command(self):
        rec = self.two_guests()
        self.plan(rec, actor={1: facts(ROOT2), 2: facts(ROOT3)})  # first tick: control starts, no time has passed
        out = self.plan(rec, inputs={1: stick(0.0, 1.0)}, actor={1: facts(ROOT2), 2: facts(ROOT3)})
        e = out.record.slot[0]
        # default basis: forward +Z, right -X, so "right" faces -PI/2
        self.assertAlmostEqual(e.heading, -math.pi / 2, places=6)
        self.assertAlmostEqual(e.magnitude, 0.10, places=6)
        self.assertEqual(e.flags, ENTRY_ACTIVE | ENTRY_MOVING)
        self.assertAlmostEqual(e.visual, -math.radians(13.63) * 0.96, places=5, msg='16 ms at the native rate')
        out = self.plan(rec, inputs={1: stick()}, actor={1: facts(ROOT2), 2: facts(ROOT3)})
        e = out.record.slot[0]
        self.assertAlmostEqual(e.heading, -math.pi / 2, places=6, msg='no input keeps the last heading')
        self.assertEqual((e.magnitude, e.flags), (0.0, ENTRY_ACTIVE))

    def test_camera_basis_is_used_while_moving_only(self):
        rec = self.two_guests()
        a = {1: facts(ROOT2), 2: facts(ROOT3)}
        out = self.plan(rec, inputs={1: stick(1.0)}, actor=a, view=view_for_heading(math.pi - 0.2))
        self.assertEqual(out.camera_accepted, 0, 'a jump from the default basis waits for confirmation')
        for _ in range(2):
            out = self.plan(rec, inputs={1: stick(1.0)}, actor=a, view=view_for_heading(math.pi - 0.2))
        self.assertEqual(out.camera_accepted, 1)
        self.assertAlmostEqual(out.record.slot[0].heading, math.pi - 0.2, places=5)
        menu = steering_record(gates=GATE['WORLD'] | GATE['LOGICAL'] | GATE['GUESTS_MOVE'],
                               seats={1: steering_seat(ACTOR2, PHYS2)})
        out = self.plan(menu, inputs={1: stick(1.0)}, actor=a, view=view_for_heading(0.0))
        self.assertEqual(out.camera_accepted, 0, 'no camera refresh without the movement gate')

    def test_without_the_movement_gate_the_entry_stays_with_a_zero_command(self):
        rec = steering_record(gates=GATE['WORLD'] | GATE['LOGICAL'] | GATE['GUESTS_MOVE'],
                              seats={1: steering_seat(ACTOR2, PHYS2)})
        out = self.plan(rec, inputs={1: stick(1.0)}, actor={1: facts(ROOT2, 0.7, 0.7)})
        e = out.record.slot[0]
        self.assertEqual((out.record.active, e.flags, e.magnitude), (1, ENTRY_ACTIVE, 0.0))
        self.assertAlmostEqual(e.heading, 0.7, places=6)

    def test_head_flags_follow_the_session_gates(self):
        for gates, flags in ((GATE['LOGICAL'], STEERF_LOGICAL), (GATE['GUESTS_MOVE'], STEERF_MOVE), (0, 0)):
            out = self.plan(steering_record(gates=gates))
            self.assertEqual(out.record.head.flags, flags)

    def test_held_input_and_interaction_hold_give_no_command(self):
        a = {1: facts(ROOT2), 2: facts(ROOT3)}
        out = self.plan(self.two_guests(), inputs={1: stick(1.0, hold=1), 2: stick(1.0, present=0)}, actor=a)
        self.assertEqual((out.record.slot[0].magnitude, out.record.slot[1].magnitude), (0.0, 0.0))

    def test_walk_toggle_flips_on_each_press_per_seat(self):
        rec = self.two_guests()
        a = {1: facts(ROOT2), 2: facts(ROOT3)}
        mags = []
        for toggle in (0, 1, 0, 0, 1):
            out = self.plan(rec, inputs={1: stick(1.0, walk_toggle=toggle), 2: stick(1.0)}, actor=a)
            mags.append((round(out.record.slot[0].magnitude, 6), round(out.record.slot[1].magnitude, 6)))
        self.assertEqual(mags, [(0.1, 0.1), (0.04, 0.1), (0.04, 0.1), (0.04, 0.1), (0.1, 0.1)])

    def test_walk_mode_survives_losing_the_actor(self):
        rec = self.two_guests()
        a = {1: facts(ROOT2), 2: facts(ROOT3)}
        self.plan(rec, inputs={1: stick(1.0, walk_toggle=1)}, actor=a)
        lost = self.two_guests()
        lost.seat[1].flags &= ~SEATF['STEERS']
        self.plan(lost, actor=a)
        out = self.plan(rec, inputs={1: stick(1.0)}, actor=a)
        self.assertAlmostEqual(out.record.slot[0].magnitude, 0.04, places=6)
        self.assertEqual(out.started & 1, 1, 'regaining the actor seeds its headings again')

    def test_armed_actor_runs_slower(self):
        out = self.plan(self.two_guests(), inputs={1: stick(1.0)}, actor={1: facts(ROOT2, armed=1),
                                                                          2: facts(ROOT3)})
        self.assertAlmostEqual(out.record.slot[0].magnitude, 1 / 12, places=6)

    def test_execution_motion_stops_and_faces_its_target(self):
        rec = self.two_guests()
        self.plan(rec, actor={1: facts(ROOT2), 2: facts(ROOT3)})
        out = self.plan(rec, inputs={1: stick(1.0)}, actor={1: facts(ROOT2, 0.0, 0.0, execution=1, target=2.0),
                                                           2: facts(ROOT3)})
        e = out.record.slot[0]
        self.assertEqual(e.magnitude, 0.0)
        self.assertAlmostEqual(e.heading, 2.0, places=6)
        self.assertAlmostEqual(e.visual, 25.0 * 0.016, places=5, msg='execution turns at the action rate')
        out = self.plan(rec, inputs={1: stick()}, actor={1: facts(ROOT2, execution=1), 2: facts(ROOT3)})
        self.assertAlmostEqual(out.record.slot[0].heading, 2.0, places=6, msg='target heading becomes the last')

    def test_new_incarnation_or_world_reseeds(self):
        a = {1: facts(ROOT2, 1.0, 1.0)}
        rec = steering_record(seats={1: steering_seat(ACTOR2, PHYS2)})
        self.assertEqual(self.plan(rec, actor=a).started, 1)
        self.assertEqual(self.plan(rec, actor={1: facts(ROOT2, -2.0, -2.0)}).started, 0)
        for change in ('gen', 'world', 'phys'):
            rec2 = steering_record(seats={1: steering_seat(ACTOR2, PHYS2 + (0x100 if change == 'phys' else 0),
                                                           gen=2 if change == 'gen' else 1)},
                                   world_epoch=4 if change == 'world' else 3)
            out = self.plan(rec2, actor={1: facts(ROOT2, -2.0, -2.0)})
            self.assertEqual(out.started, 1, change)
            self.assertAlmostEqual(out.record.slot[0].visual, -2.0, places=6)
            self.plan(rec, actor=a)

    def test_flee_needs_co_op_and_a_manual_guest(self):
        rec = self.two_guests()
        a = {1: facts(ROOT2), 2: facts(ROOT3)}
        self.assertEqual(self.plan(rec, inputs={1: stick(flee=1)}, actor=a).flee_seats, 0b010)
        self.assertEqual(self.plan(rec, inputs={1: stick(flee=1), 2: stick(flee=1)}, actor=a).flee_seats, 0b110)
        parked = self.two_guests()
        parked.seat[1].flags &= ~SEATF['MANUAL']
        self.assertEqual(self.plan(parked, inputs={1: stick(flee=1)}, actor=a).flee_seats, 0)
        off = steering_record(gates=GATE['WORLD'] | GATE['MOVEMENT'], seats={1: steering_seat(ACTOR2, PHYS2)})
        self.assertEqual(self.plan(off, inputs={1: stick(flee=1)}, actor=a).flee_seats, 0)
        menu = steering_record(gates=GATE['WORLD'] | GATE['LOGICAL'], seats={1: steering_seat(ACTOR2, PHYS2)})
        self.assertEqual(self.plan(menu, inputs={1: stick(flee=1)}, actor=a).flee_seats, 0,
                         'movement gate closed (a menu): flee must not reach P1 RT')
        no_world = steering_record(gates=GATE['MOVEMENT'] | GATE['LOGICAL'], seats={1: steering_seat(ACTOR2, PHYS2)})
        self.assertEqual(self.plan(no_world, inputs={1: stick(flee=1)}, actor=a).flee_seats, 0,
                         'no valid world (load, cutscene): no flee')
        both = steering_record(gates=GATE['WORLD'] | GATE['MOVEMENT'] | GATE['LOGICAL'],
                               seats={1: steering_seat(ACTOR2, PHYS2)})
        self.assertEqual(self.plan(both, inputs={1: stick(flee=1)}, actor=a).flee_seats, 0b010)

    def test_no_epoch_no_entry(self):
        out = self.plan(steering_record(epoch=0, seats={1: steering_seat(ACTOR2, PHYS2)}), actor={1: facts(ROOT2)})
        self.assertEqual(out.record.active, 0)


# ---- the co-op-thread step and the follow operation on synthetic memory -----------------------------------------
@native_test
class LiveStepTest(unittest.TestCase):
    def setUp(self):
        self.d = declare(fresh_test_dll())
        self.img = Image()
        self.w = GameWorld(self.img)
        self.actors = self.w.party([0, 1, 2])
        self.d.ILCT_MoveReset()
        self.d.ILCT_MoveForceFleeLive(1)  # no patch layer installed here: MOVEMENT counts as live for the flee word
        self.d.ILCT_MoveStart(self.img.base, 1000)

    def tearDown(self):
        self.d.ILCT_MoveStop()
        self.d.ILCT_MoveForceFleeLive(-1)
        self.img.close()

    def guest(self, k):
        a = self.actors[k]
        return steering_seat(a.actor, a.phys, member=k, handle=0x100000 | a.index)

    def test_step_publishes_the_record_from_live_reads(self):
        a = self.actors[1]
        self.img.put(a.phys + 0xA4, struct.pack('<2f', 0.25, 0.5))
        self.img.p8(a.actor + 0x02, 0x80)
        self.img.put_global(0x1F7E528, struct.pack('<16f', *view_for_heading(0.1)))
        rec = steering_record(seats={1: self.guest(1)})
        self.d.ILCT_MoveSessionPublish(ctypes.byref(rec))
        inputs = (SeatInput * 4)()
        inputs[1] = stick(1.0, flee=1)
        self.d.ILCT_MoveSetInputs(inputs, 0b10)
        self.d.ILCT_MoveStep(1016)
        out = SteerRecord()
        self.assertEqual(self.d.ILCT_MoveRecord(ctypes.byref(out)), 1)
        e = out.slot[0]
        self.assertEqual((out.head.epoch, out.active, e.actor, e.phys, e.root), (5, 1, a.actor, a.phys, a.root))
        self.assertAlmostEqual(e.magnitude, 1 / 12, places=6, msg='fighting stance read from the actor')
        self.assertAlmostEqual(e.heading, 0.1, places=5, msg='stick forward = the camera forward read from the game')
        self.assertEqual(self.d.ILCT_MoveFlee(5), 0b10)
        self.assertEqual(self.d.ILCT_MoveFlee(6), 0, 'a flee request answers only for its own epoch')
        c = Counters()
        self.d.ILCT_MoveStats(ctypes.byref(c))
        self.assertEqual((c.published, c.camera_reads, c.facts_failed), (1, 1, 0))
        self.d.ILCT_MoveForceFleeLive(0)  # MOVEMENT disarmed (bit off, faulted or DRY): no flee word
        self.d.ILCT_MoveStep(1032)
        self.assertEqual(self.d.ILCT_MoveFlee(5), 0, 'the flee word must not be published while MOVEMENT is disarmed')
        self.d.ILCT_MoveForceFleeLive(1)
        self.d.ILCT_MoveStep(1048)
        self.assertEqual(self.d.ILCT_MoveFlee(5), 0b10, 'armed again: the word is back')

    def test_execution_target_heading_is_read_through_the_actor_manager(self):
        a, target = self.actors[1], self.actors[2]
        flags = struct.unpack('<Q', ctypes.string_at(a.actor, 8))[0] | (1 << 46)
        self.img.p64(a.actor, flags)
        self.img.p32(a.actor + 0x710, 0x100000 | target.index)
        target.set_position((11.0 + 3.0, 2.0, 30.0 + 3.0))  # actor 1 stands at (11, 2, 30)
        self.d.ILCT_MoveSessionPublish(ctypes.byref(steering_record(seats={1: self.guest(1)})))
        inputs = (SeatInput * 4)()
        inputs[1] = stick(1.0)
        self.d.ILCT_MoveSetInputs(inputs, 0b10)
        self.d.ILCT_MoveStep(1016)
        out = SteerRecord()
        self.d.ILCT_MoveRecord(ctypes.byref(out))
        self.assertEqual(out.slot[0].magnitude, 0.0)
        self.assertAlmostEqual(out.slot[0].heading, math.pi / 4, places=5)

    def test_unreadable_actor_withholds_the_entry(self):
        a = self.actors[1]
        self.d.ILCT_MoveSessionPublish(ctypes.byref(steering_record(seats={1: self.guest(1)})))
        self.img.protect(a.root, 4096, 0x01)
        try:
            self.d.ILCT_MoveStep(1016)
        finally:
            self.img.protect(a.root, 4096, 0x04)
        out = SteerRecord()
        self.d.ILCT_MoveRecord(ctypes.byref(out))
        last = PlanOutputs()
        self.d.ILCT_MoveLast(ctypes.byref(last))
        self.assertEqual((out.active, last.waiting), (0, 1))
        c = Counters()
        self.d.ILCT_MoveStats(ctypes.byref(c))
        self.assertEqual(c.facts_failed, 1)

    def test_no_session_record_withdraws(self):
        self.d.ILCT_MoveSessionPublish(ctypes.byref(steering_record(seats={1: self.guest(1)})))
        self.d.ILCT_MoveStep(1016)
        self.d.ILCT_MoveSessionWithdraw()
        self.d.ILCT_MoveStep(1032)
        self.assertEqual(self.d.ILCT_MoveRecord(ctypes.byref(SteerRecord())), 0)
        self.assertEqual(self.d.ILCT_MoveFlee(5), 0)
        self.d.ILCT_MoveSessionPublish(ctypes.byref(steering_record(epoch=0, seats={1: self.guest(1)})))
        self.d.ILCT_MoveStep(1048)
        self.assertEqual(self.d.ILCT_MoveRecord(ctypes.byref(SteerRecord())), 0)

    def test_stop_withdraws(self):
        self.d.ILCT_MoveSessionPublish(ctypes.byref(steering_record(seats={1: self.guest(1)})))
        self.d.ILCT_MoveStep(1016)
        self.d.ILCT_MoveStop()
        self.assertEqual(self.d.ILCT_MoveRecord(ctypes.byref(SteerRecord())), 0)


@native_test
class FollowOperationTest(unittest.TestCase):
    EPOCH = 5

    def setUp(self):
        self.d = declare(fresh_test_dll())
        self.img = Image()
        self.w = GameWorld(self.img)
        self.actors = self.w.party([0, 1, 2])
        self.a = self.actors[1]
        self.handle = 0x100000 | self.a.index
        self.d.ILCT_MoveSetImageBase(self.img.base)
        self.d.ILCT_MoveSessionPublish(ctypes.byref(steering_record(epoch=self.EPOCH)))
        self.img.p16(self.a.actor + 0x72C, ACTION_IDLE)

    def tearDown(self):
        self.img.close()

    def follow(self, actor=None, phys=None, handle=None, epoch=None):
        return self.d.ILCT_MoveFollow(self.a.actor if actor is None else actor, self.a.phys if phys is None else phys,
                                      self.handle if handle is None else handle,
                                      self.EPOCH if epoch is None else epoch)

    def actions(self):
        return struct.unpack('<H', ctypes.string_at(self.a.actor + 0x714, 2))[0], \
            struct.unpack('<H', ctypes.string_at(self.a.actor + 0x72C, 2))[0]

    def test_idle_follower_is_asked_to_follow_then_already_follows(self):
        self.assertEqual(self.follow(), APPLIED)
        self.assertEqual(self.actions(), (ACTION_FOLLOW, ACTION_FOLLOW))
        self.assertEqual(self.follow(), ALREADY)

    def test_secondary_list_and_list_b_count(self):
        self.img.p64(self.a.actor + 0x6E8, 0)
        self.img.p64(self.a.actor + 0x6F0, self.img.va(FOLLOW_LIST_SECONDARY))
        self.assertEqual(self.follow(), APPLIED)

    def test_every_refusal_changes_nothing(self):
        cases = {
            'stale handle': (lambda: None, dict(handle=self.handle + 1), STALE_IDENTITY),
            'other actor': (lambda: None, dict(actor=self.actors[2].actor), STALE_IDENTITY),
            'other physics': (lambda: None, dict(phys=self.actors[2].phys), STALE_PHYSICS),
            'busy': (lambda: self.a.set_action(state=4), {}, NOT_READY),
            'targeted': (lambda: self.a.set_action(target=0x200013), {}, NOT_READY),
            'shadow targeted': (lambda: self.img.p32(self.a.actor + 0x728, 7), {}, NOT_READY),
            'not listed': (lambda: self.a.set_action(listed=False), {}, NOT_READY),
            'no follow descriptor': (lambda: self.a.set_action(descriptor_action=0x4000), {}, NOT_READY),
            'shadow differs': (lambda: self.img.p16(self.a.actor + 0x72C, 0x4050), {}, ACTION_CHANGED),
            'action differs': (lambda: self.a.set_action(action=0x4050), {}, ACTION_CHANGED),
            'no args': (lambda: None, dict(actor=0), BAD_ARGS),
            'wrong epoch': (lambda: None, dict(epoch=self.EPOCH + 1), NO_WORLD),
        }
        for label, (prepare, kw, expected) in cases.items():
            with self.subTest(label):
                self.a.set_action()
                self.img.p16(self.a.actor + 0x72C, ACTION_IDLE)
                self.img.p32(self.a.actor + 0x728, 0)
                prepare()
                before = ctypes.string_at(self.a.actor, 0x800)
                self.assertEqual(self.follow(**kw), expected)
                self.assertEqual(ctypes.string_at(self.a.actor, 0x800), before)

    def test_world_gate_and_missing_record(self):
        self.d.ILCT_MoveSessionPublish(ctypes.byref(steering_record(epoch=self.EPOCH, gates=GATE['LOGICAL'])))
        self.assertEqual(self.follow(), NO_WORLD)
        self.d.ILCT_MoveSessionWithdraw()
        self.assertEqual(self.follow(), NO_WORLD)
        self.assertEqual(self.actions(), (ACTION_IDLE, ACTION_IDLE))

    def test_unreadable_memory_is_refused_without_a_fault(self):
        self.img.protect(self.a.root, 4096, 0x01)
        try:
            self.assertEqual(self.follow(), UNREADABLE)
        finally:
            self.img.protect(self.a.root, 4096, 0x04)
        self.w.manager_pointer(False)
        self.assertEqual(self.follow(), UNREADABLE)
        self.assertEqual(self.actions(), (ACTION_IDLE, ACTION_IDLE))


# ---- handlers on the image twin ---------------------------------------------------------------------------------
EPOCH = 7


class MoveWorld:
    """The twin installed, LIVE, armed, MOVEMENT applied, movement attached (handlers + OP_FOLLOW), queue sealed."""

    def __init__(self):
        self.w = gt.world()
        self.d = declare(self.w.d)
        self.twin = self.w.twin
        self.rows = {sid: self.twin.index[sid] for sid in ('MV-01', 'MV-02', 'MV-03', 'MV-04', 'MV-05', 'MV-06',
                                                           'MV-07')}

    def setup(self, mode=gt.LIVE, subsystems=0xFF):
        d = self.d
        self.twin.reset()
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_MoveReset()
        report = self.twin.call('ILCT_PatchInstall')
        assert report.result == OK, report.result
        d.ILCT_SetFixedClock(gt.NOW)
        assert d.ILCT_TickAttach() == 1
        assert d.ILCT_MoveAttach() == 1
        d.ILCT_GtqSeal()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(subsystems)
        d.ILCT_PatchSetMode(mode)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_GtqSetGeneration(gt.GEN)

    def soft_reset(self):
        """Back to LIVE, armed, every bit applied, a fresh heartbeat; no reinstall (MinHook's buffers stay where an
        emulator copied them)."""
        d = self.d
        d.ILCT_MoveReset()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_SetFixedClock(gt.NOW)
        d.ILCT_PatchHeartbeat(EPOCH)

    def gates(self):
        return {sid for sid, row in self.rows.items() if self.twin.info(row).gate_open}


_mw = None


def move_world() -> MoveWorld:
    global _mw
    if _mw is None:
        _mw = MoveWorld()
    return _mw


def record_for(slot0=None, slot1=None, epoch=EPOCH, flags=STEERF_MOVE | STEERF_LOGICAL) -> SteerRecord:
    r = SteerRecord()
    r.head.epoch, r.head.flags, r.head.generation = epoch, flags, gt.GEN
    for k, e in enumerate((slot0, slot1)):
        if e is None:
            continue
        x = r.slot[k]
        x.actor, x.phys, x.root = e['actor'], e['phys'], e['root']
        x.heading, x.visual, x.magnitude = e.get('heading', 1.25), e.get('visual', -0.5), e.get('magnitude', 0.1)
        x.seat, x.member, x.flags = k + 1, k + 1, ENTRY_ACTIVE | (ENTRY_MOVING if e.get('magnitude', 0.1) else 0)
        r.active |= 1 << k
    return r


def f32_bits(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def bits_f32(b):
    return struct.unpack('<f', struct.pack('<I', b & 0xFFFFFFFF))[0]


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class HandlerNativeTest(unittest.TestCase):
    """Each handler on a saved machine state, with synthetic objects in this process."""

    def setUp(self):
        self.m = move_world()
        self.m.setup()
        self.d = self.m.d
        self.img = Image()
        h = self.img
        self.root, self.actor, self.phys, self.vec = (h.alloc(4096, page=True) for _ in range(4))
        h.p64(self.root + 0x30, self.actor)
        h.p64(self.root + 0xB8, self.phys)
        h.p64(self.actor + 0x10, self.root)
        h.put(self.phys + 0xA0, struct.pack('<3f', 0.3, 2.0, 2.5))
        h.put(self.phys + 0xD0, struct.pack('<f', 0.07))
        self.entry = dict(actor=self.actor, phys=self.phys, root=self.root)
        self.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry)))

    def tearDown(self):
        self.img.close()
        self.d.ILCT_MoveWithdraw()

    def ctx(self, **regs) -> Ctx:
        c = Ctx()
        rng = random.Random(77)
        for n in GPR:
            setattr(c, n, rng.getrandbits(64))
        for k in range(12):
            c.xmm[k] = rng.getrandbits(64)
        c.rflags = 0x246
        stack = ctypes.create_string_buffer(0x200)
        self._stack = stack
        c.rsp = ctypes.addressof(stack) + 0x80
        for n, v in regs.items():
            setattr(c, n, v)
        return c

    def invoke(self, which, c, epoch=EPOCH):
        return self.d.ILCT_MoveInvoke(which, ctypes.byref(c), epoch)

    def phys_fields(self):
        return struct.unpack('<3f', ctypes.string_at(self.phys + 0xA0, 12)) + \
            struct.unpack('<f', ctypes.string_at(self.phys + 0xD0, 4))

    def sites(self):
        return {1: dict(rcx=self.root, r8=0, r9=0), 2: dict(rdi=self.phys), 3: dict(rbx=self.actor),
                5: dict(r13=self.actor, r10=TWIN_BASE + FOLLOW_LIST_PRIMARY - 0x120000),
                6: dict(rdi=self.actor)}

    def assert_untouched(self, which, regs, epoch=EPOCH, label=''):
        c = self.ctx(**regs)
        before, mem = bytes(c), self.phys_fields()
        self.assertEqual(self.invoke(which, c, epoch), RUN_ORIGINAL, label)
        after = Ctx.from_buffer_copy(before)
        after.index = c.index
        self.assertEqual(bytes(c), bytes(after), f'MV-0{which} {label}: registers changed')
        self.assertEqual(self.phys_fields(), mem, f'MV-0{which} {label}: memory changed')

    def test_fail_open_in_every_refused_condition(self):
        for which, regs in self.sites().items():
            self.assert_untouched(which, regs, epoch=EPOCH + 1, label='record of another epoch')
        self.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry, epoch=0)))
        for which, regs in self.sites().items():
            self.assert_untouched(which, regs, epoch=EPOCH, label='record epoch 0')
        self.d.ILCT_MoveWithdraw()
        for which, regs in self.sites().items():
            self.assert_untouched(which, regs, label='no record')
        self.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry, flags=0)))
        for which, regs in self.sites().items():
            self.assert_untouched(which, regs, label='session gates closed')
        other = dict(actor=self.actor + 8, phys=self.phys + 8, root=self.root + 8)
        self.d.ILCT_MovePublish(ctypes.byref(record_for(other)))
        for which, regs in self.sites().items():
            self.assert_untouched(which, regs, label='another actor')
        self.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry)))
        for label, action in (('MOVEMENT bit clear', lambda: self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)),
                              ('DRY', lambda: self.d.ILCT_PatchSetMode(gt.DRY)),
                              ('MOVEMENT faulted', lambda: self.d.ILCT_PatchFault(MOVEMENT))):
            action()
            for which, regs in self.sites().items():
                self.assert_untouched(which, regs, label=label)
            self.m.setup()
            self.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry)))

    def test_mv01_requires_the_root_to_lead_back(self):
        self.img.p64(self.root + 0x30, self.actor + 0x40)
        self.assert_untouched(1, dict(rcx=self.root, r8=0, r9=0), label='root names another actor')
        self.img.p64(self.root + 0x30, self.actor)
        self.img.p64(self.root + 0xB8, self.phys + 0x40)
        self.assert_untouched(1, dict(rcx=self.root, r8=0, r9=0), label='root names another physics object')
        self.img.p64(self.root + 0xB8, self.phys)
        self.img.protect(self.root, 4096, 0x01)
        try:
            self.assert_untouched(1, dict(rcx=self.root, r8=0, r9=0), label='root unreadable')
        finally:
            self.img.protect(self.root, 4096, 0x04)

    def test_mv01_writes_the_command(self):
        c = self.ctx(rcx=self.root, r8=0, r9=0)
        self.assertEqual(self.invoke(1, c), RUN_ORIGINAL)
        pitch, heading, visual, mag = self.phys_fields()
        self.assertEqual((pitch, round(heading, 6), round(mag, 6)), (0.0, 1.25, 0.1))
        self.assertAlmostEqual(visual, 2.5, places=6, msg='the body facing is not MV-01s to write')

    def test_mv01_root_motion_override_rules(self):
        h = self.img
        cases = [  # (r8, vector, magnitude, execution, expected r8 after)
            (1, (0.0, 0.0, 0.0), 0.1, False, 0),
            (1, (-0.0, 0.0, -0.0), 0.1, False, 0),
            (1, (0.0, 0.01, 0.0), 0.1, False, 1),
            (1, (0.0, 0.0, 0.0), 0.0, False, 1),
            (1, (0.0, 0.0, 0.0), 0.1, True, 1),
            (0x500000000, (0.0, 0.0, 0.0), 0.1, False, 0x500000000),  # r8d is 0: nothing selected
        ]
        for r8, vec, mag, execution, expected in cases:
            with self.subTest(r8=r8, vec=vec, mag=mag, execution=execution):
                self.d.ILCT_MovePublish(ctypes.byref(record_for(dict(self.entry, magnitude=mag))))
                h.put(self.vec, struct.pack('<3f', *vec))
                h.p64(self.actor, (1 << 46) if execution else 0)
                c = self.ctx(rcx=self.root, r8=r8, r9=self.vec)
                self.assertEqual(self.invoke(1, c), RUN_ORIGINAL)
                self.assertEqual(c.r8, expected)
        c = self.ctx(rcx=self.root, r8=1, r9=0)
        self.invoke(1, c)
        self.assertEqual(c.r8, 1, 'no vector: nothing selected')

    def test_mv02_and_mv03_replace_the_stored_value_only(self):
        for which, regs, value in ((2, dict(rdi=self.phys), 1.25), (3, dict(rbx=self.actor), -0.5)):
            c = self.ctx(**regs)
            before = Ctx.from_buffer_copy(bytes(c))
            self.assertEqual(self.invoke(which, c), RUN_ORIGINAL)
            self.assertEqual(c.xmm[0] >> 32, before.xmm[0] >> 32, 'xmm0 lanes above s0 kept')
            self.assertAlmostEqual(bits_f32(c.xmm[0]), value, places=6)
            for k in range(1, 12):
                self.assertEqual(c.xmm[k], before.xmm[k])
            for n in GPR:
                self.assertEqual(getattr(c, n), getattr(before, n), n)

    def test_mv03_checks_the_actor_physics_chain(self):
        self.img.p64(self.actor + 0x10, self.root + 0x100)
        self.assert_untouched(3, dict(rbx=self.actor), label='actor root changed')

    def test_mv05_rejects_only_the_native_follower_lists(self):
        primary = TWIN_BASE + FOLLOW_LIST_PRIMARY - 0x120000
        secondary = TWIN_BASE + FOLLOW_LIST_SECONDARY - 0x120000
        for lst in (primary, secondary):
            c = self.ctx(r13=self.actor, r10=lst)
            self.assertEqual(self.invoke(5, c), RESUME)
            self.assertEqual(c.resume, TWIN_BASE + 0x305CF7 - 0x120000)
        self.assert_untouched(5, dict(r13=self.actor, r10=primary + 0x10), label='another list')
        self.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry, flags=STEERF_LOGICAL)))
        self.assertEqual(self.invoke(5, self.ctx(r13=self.actor, r10=primary)), RESUME,
                         'logical co-op alone keeps the AI away (no movement gate needed)')
        c = Counters()
        self.d.ILCT_MoveStats(ctypes.byref(c))
        self.assertEqual(c.list_rejected, 3)

    def test_mv06_takes_the_no_approach_exit_with_rbx_restored(self):
        c = self.ctx(rdi=self.actor)
        saved = 0x1122334455667788
        ctypes.memmove(c.rsp + 0x30, struct.pack('<Q', saved), 8)
        before = Ctx.from_buffer_copy(bytes(c))
        self.assertEqual(self.invoke(6, c), RESUME)
        self.assertEqual((c.resume, c.rbx), (TWIN_BASE + 0x325DA5 - 0x120000, saved))
        for n in GPR:
            if n != 'rbx':
                self.assertEqual(getattr(c, n), getattr(before, n), n)
        self.d.ILCT_MovePublish(ctypes.byref(record_for(dict(self.entry), flags=STEERF_LOGICAL)))
        self.assertEqual(self.invoke(6, self.ctx(rdi=self.actor)), RESUME)

    def test_the_second_slot_is_matched_too(self):
        other = dict(actor=self.actor + 0x100, phys=self.phys + 0x100, root=self.root + 0x100)
        self.img.p64(self.root + 0x130, self.actor + 0x100)
        self.img.p64(self.root + 0x1B8, self.phys + 0x100)
        self.d.ILCT_MovePublish(ctypes.byref(record_for(None, dict(other, heading=-2.0))))
        c = self.ctx(rcx=self.root + 0x100, r8=0, r9=0)
        self.invoke(1, c)
        self.assertAlmostEqual(struct.unpack('<f', ctypes.string_at(self.phys + 0x1A4, 4))[0], -2.0)
        self.assertEqual(self.invoke(6, self.ctx(rdi=self.actor + 0x100)), RESUME)


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class AttachTest(unittest.TestCase):
    def test_attach_arms_the_non_call_sites_only(self):
        m = move_world()
        m.setup()
        self.assertEqual(m.gates(), {'MV-01', 'MV-02', 'MV-03', 'MV-04', 'MV-05', 'MV-06'})
        m.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        self.assertEqual(m.gates(), set())
        m.setup(mode=gt.DRY)
        self.assertEqual(m.gates(), set())
        self.assertEqual(m.d.ILCT_MoveBerserkGate(), 0, 'MV-10 has no handler: P1 Berserk rule stays off')

    def test_follow_job_runs_through_the_queue(self):
        m = move_world()
        m.setup()
        ticket = ctypes.c_uint32()
        self.assertEqual(m.d.ILCT_GtqPush(OP_FOLLOW, 1, EPOCH, gt.GEN, 100, 0, ctypes.byref(ticket)), gt.PUSH_OK)
        self.assertEqual(m.d.ILCT_GtqDrain(), 1)
        self.assertEqual(m.d.ILCT_GtqResult(ticket.value, None), NO_WORLD,
                         'the operation ran on the drain and refused without a session world')


# ---- unicorn: the hooked sites on the twin ---------------------------------------------------------------------
SCR = pe.SCRATCH
ROOT_E, ACTOR_E, PHYS_E, VEC_E, OUT_E = SCR + 0x10000, SCR + 0x20000, SCR + 0x30000, SCR + 0x40000, SCR + 0x50000


# The synthetic scene the emulator tests run on, as (object, offset, struct format, value) rows.
SCENE = [
    (ROOT_E, 0x03, 'B', 0x60),          # scene-root type tag the locomotion function requires
    (ROOT_E, 0x30, 'Q', ACTOR_E),
    (ROOT_E, 0xB8, 'Q', PHYS_E),
    (ROOT_E, 0xC0, 'Q', 0),             # no animation data
    (ACTOR_E, 0x00, 'Q', 0),            # no execution motion
    (ACTOR_E, 0x10, 'Q', ROOT_E),
    (PHYS_E, 0xA0, 'f', 0.3),           # pitch, heading, facing, facing pitch as the game left them
    (PHYS_E, 0xA4, 'f', 2.0),
    (PHYS_E, 0xA8, 'f', 2.5),
    (PHYS_E, 0xAC, 'f', 0.75),
    (PHYS_E, 0xD0, 'f', 0.07),
    (PHYS_E, 0xF8, 'f', 0.0),
    (VEC_E, 0x00, '3f', (0.0, 0.0, 0.0)),  # an all-zero root-motion vector
]
BATTLE_E = PHYS_E + 0x400
# Everything 0x325D90 checks before its leader comparison, set so that the comparison is reached.
APPROACH_ACTOR = [(ACTOR_E, 0x1F0, 'I', 0), (ACTOR_E, 0x698, 'Q', BATTLE_E), (BATTLE_E, 0x3C, 'I', 0),
                  (BATTLE_E, 0x64, 'I', 0), (ACTOR_E, 0x60, 'I', 0), (ACTOR_E, 0x6B7, 'B', 0),
                  (ACTOR_E, 0x708, 'Q', 0), (ACTOR_E, 0x714, 'H', 0x414E)]


def lay_out(uc, rows):
    for obj, offset, fmt, value in rows:
        values = value if isinstance(value, tuple) else (value,)
        uc.mem_write(obj + offset, struct.pack('<' + fmt, *values))


class MoveEmulator(gt.TickEmulator):
    """The UT-01 emulator plus doubles for the fault-safe probe's two imports, and `answers`: address -> function
    of uc, run before the base hook stops at a callee (unicorn runs no later hook once emu_stop was called)."""

    def __init__(self, twin):
        self.answers = {}
        super().__init__(twin)

    def _code(self, uc, address, size, user):
        answer = self.answers.get(address)
        if answer is not None:
            answer(uc)
        super()._code(uc, address, size, user)

    def _trap(self, uc, address, size, user):
        name = self.trap.get(address)
        X = pe.X
        if name == 'VirtualQuery':
            rcx, rdx = uc.reg_read(X.UC_X86_REG_RCX), uc.reg_read(X.UC_X86_REG_RDX)
            page = rcx & ~0xFFF
            uc.mem_write(rdx, struct.pack('<QQIHHQIII', page, page, 4, 0, 0, 0x1000, 0x1000, 4, 0x20000) + bytes(4))
            uc.reg_write(X.UC_X86_REG_RAX, 48)
            self.imports.append(name)
            return
        if name == 'AddVectoredExceptionHandler':
            uc.reg_write(X.UC_X86_REG_RAX, 1)
            self.imports.append(name)
            return
        super()._trap(uc, address, size, user)


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class MovementEmulationTest(unittest.TestCase):
    TRIALS = 6

    @classmethod
    def setUpClass(cls):
        cls.m = move_world()
        cls.m.setup()
        cls.emu = MoveEmulator(cls.m.twin)

    @classmethod
    def tearDownClass(cls):
        cls.m.d.ILCT_MoveWithdraw()
        cls.m.twin.reset()

    def setUp(self):
        self.m.soft_reset()
        self.entry = dict(actor=ACTOR_E, phys=PHYS_E, root=ROOT_E)
        self.m.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry)))

    def objects(self, uc):
        lay_out(uc, SCENE)

    def site_regs(self, sid, state):
        g = state['gpr']
        if sid == 'MV-01':
            g.update(RCX=ROOT_E, RDX=OUT_E, R8=1, R9=VEC_E)
        elif sid == 'MV-02':
            g['RDI'] = PHYS_E
        elif sid in ('MV-03', 'MV-04'):
            g['RBX'] = ACTOR_E
            g['RCX'] = ROOT_E
        if sid == 'MV-04':
            g['RSP'] &= ~0xF  # a call site inside a function body: RSP is 16-byte aligned before the call
        elif sid == 'MV-05':
            g.update(R13=ACTOR_E, R10=TWIN_BASE + FOLLOW_LIST_PRIMARY - 0x120000)
        elif sid == 'MV-06':
            g['RDI'] = ACTOR_E

    def pair(self, sid, state, setup=None):
        """Original bytes, then the hooked site, from the same state. Returns (a, b, counters delta, baseline)."""
        emu, uc, twin = self.emu, self.emu.uc, self.m.twin
        row = twin.index[sid]
        r = twin.rows[row]
        site = TWIN_BASE + r['rva']
        window = (site, site + r['hook_len'])
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        self.objects(uc)
        if setup:
            setup(uc)
        patched = bytes(uc.mem_read(site, r['hook_len']))
        emu.write_code(site, checked(r)[:r['hook_len']])
        a = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or window[0] <= x < window[1],
                    state)
        emu.write_code(site, patched)
        before = emu.stats(row)(uc)
        b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5, state,
                    probe=emu.stats(row))
        touched = set(a['final']) | set(b['final'])
        baseline = {x: uc.mem_read(x, 1)[0] for x in touched}
        delta = tuple(x - y for x, y in zip(b['probe'], before))
        self.assertEqual(emu.unexpected, [], f'unexpected imports: {emu.unexpected}')
        return a, b, delta, baseline

    def states(self, sid, salt):
        for trial in range(self.TRIALS):
            state = pe.random_state(random.Random(0xB2100 + salt * 100 + trial))
            self.site_regs(sid, state)
            if sid == 'MV-06':
                state['saved_rbx'] = 0xABCDEF0123456789 ^ trial
            yield trial, state

    def with_saved_rbx(self, state):
        if 'saved_rbx' not in state:
            return None
        return lambda uc: uc.mem_write(state['gpr']['RSP'] + 0x30, struct.pack('<Q', state['saved_rbx']))

    SITES = ('MV-01', 'MV-02', 'MV-03', 'MV-04', 'MV-05', 'MV-06')

    def test_hooked_sites_equal_stock_whenever_movement_must_not_act(self):
        conditions = {
            'no record': lambda: self.m.d.ILCT_MoveWithdraw(),
            'other epoch': lambda: self.m.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry, epoch=EPOCH + 1))),
            'gates closed': lambda: self.m.d.ILCT_MovePublish(ctypes.byref(record_for(self.entry, flags=0))),
            'other actor': lambda: self.m.d.ILCT_MovePublish(ctypes.byref(record_for(
                dict(actor=ACTOR_E + 8, phys=PHYS_E + 8, root=ROOT_E + 8)))),
            'MOVEMENT bit clear': lambda: self.m.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT),
            'DRY': lambda: self.m.d.ILCT_PatchSetMode(gt.DRY),
        }
        expected_delta = {'MOVEMENT bit clear': (0, 0, 0), 'DRY': (0, 0, 0)}
        passed = 0
        for label, prepare in conditions.items():
            self.setUp()
            prepare()
            for k, sid in enumerate(self.SITES):
                for trial, state in self.states(sid, k):
                    with self.subTest(condition=label, site=sid, trial=trial):
                        a, b, delta, baseline = self.pair(sid, state, self.with_saved_rbx(state))
                        gt.compare(self, self.emu, f'{sid} {label}', a, b, baseline, state['gpr']['RSP'])
                        self.assertEqual(delta[:3], expected_delta.get(label, (1, 0, 1)), f'{sid} {label}')
                        passed += 1
        print(f'\nmovement fail-open: {passed} runs equal to the original bytes '
              f'({len(conditions)} conditions x {len(self.SITES)} sites x {self.TRIALS} states)')

    def test_armed_mv01_writes_the_command_and_selects_steering(self):
        for trial, state in self.states('MV-01', 1):
            a, b, delta, _ = self.pair('MV-01', state)
            self.assertEqual((a['exit'], b['exit']), (a['exit'], TWIN_BASE + 0x2E4385 - 0x120000))
            self.assertEqual(delta[:3], (1, 0, 1))
            ga, gb = dict(a['gpr']), dict(b['gpr'])
            self.assertEqual(gb.pop('R8'), 0, 'zero root-motion vector + moving -> steering path')
            ga.pop('R8')
            self.assertEqual(ga, gb)
            self.assertEqual(a['xmm'], b['xmm'])
            mem = {addr: v for addr, v in b['final'].items() if PHYS_E <= addr < PHYS_E + 0x100}
            got = bytes(mem.get(PHYS_E + 0xA0 + i, 0) for i in range(8)) + \
                bytes(mem.get(PHYS_E + 0xD0 + i, 0) for i in range(4))
            self.assertEqual(struct.unpack('<3f', got), (0.0, 1.25, struct.unpack('<f', struct.pack('<f', 0.1))[0]))
            other = {addr for addr in set(a['final']) ^ set(b['final'])
                     if not (PHYS_E + 0xA0 <= addr < PHYS_E + 0xA8 or PHYS_E + 0xD0 <= addr < PHYS_E + 0xD4)
                     and not (self.emu.dll[0] <= addr < self.emu.dll[1]) and not pe.STACK <= addr < pe.STACK +
                     pe.STACK_SIZE}
            self.assertEqual(other, set(), f'trial {trial}: writes outside the physics command')

    def test_armed_store_sites_store_the_guest_value(self):
        for sid, reg, offset, value in (('MV-02', 'RDI', 0xA4, 1.25), ('MV-03', 'RBX', 0x15C, -0.5)):
            for trial, state in self.states(sid, 2):
                a, b, delta, _ = self.pair(sid, state)
                self.assertEqual(delta[:3], (1, 0, 1))
                self.assertEqual(a['exit'], b['exit'])
                self.assertEqual(a['gpr'], b['gpr'])
                self.assertEqual(a['xmm'][1:], b['xmm'][1:])
                self.assertEqual(b['xmm'][0] >> 32, a['xmm'][0] >> 32)
                self.assertAlmostEqual(bits_f32(b['xmm'][0]), value, places=6)
                base = state['gpr'][reg] + offset
                stored = bytes(b['final'][base + i] for i in range(4))
                self.assertAlmostEqual(struct.unpack('<f', stored)[0], value, places=6, msg=f'{sid} {trial}')

    def test_armed_list_and_approach_exits(self):
        for trial, state in self.states('MV-05', 3):
            a, b, delta, _ = self.pair('MV-05', state)
            self.assertEqual(a['exit'], TWIN_BASE + 0x305D0A - 0x120000)
            self.assertEqual(b['exit'], TWIN_BASE + 0x305CF7 - 0x120000)
            self.assertEqual(delta[:3], (1, 0, 1))
            gb = dict(b['gpr'])
            self.assertEqual(gb, {**state['gpr'], 'RSP': gb['RSP']})
            self.assertEqual(gb['RSP'], state['gpr']['RSP'])
        for trial, state in self.states('MV-06', 4):
            a, b, delta, _ = self.pair('MV-06', state, self.with_saved_rbx(state))
            self.assertEqual(a['exit'], TWIN_BASE + 0x325E0E - 0x120000)
            self.assertEqual(b['exit'], TWIN_BASE + 0x325DA5 - 0x120000)
            self.assertEqual(b['gpr']['RBX'], state['saved_rbx'], 'rbx restored before the no-approach exit')
            self.assertEqual(a['gpr']['RBX'], state['saved_rbx'])
            ga, gb = dict(a['gpr']), dict(b['gpr'])
            self.assertEqual(ga, gb)

    def test_armed_mv04_calls_the_facing_setter_then_the_original(self):
        X = pe.X
        setter = TWIN_BASE + 0x2E5E10 - 0x120000
        callee = TWIN_BASE + 0x263CC0 - 0x120000
        seen = []
        self.emu.answers[setter] = lambda uc: seen.append(
            (uc.reg_read(X.UC_X86_REG_RCX), bits_f32(uc.reg_read(X.UC_X86_REG_XMM1)),
             bits_f32(uc.reg_read(X.UC_X86_REG_XMM2)), uc.reg_read(X.UC_X86_REG_R9) & 0xFFFFFFFF,
             uc.reg_read(X.UC_X86_REG_RSP) & 0xF))
        try:
            for trial, state in self.states('MV-04', 5):
                seen.clear()
                a, b, delta, _ = self.pair('MV-04', state)
                self.assertEqual(delta[:3], (1, 0, 1))
                self.assertEqual(a['calls'], [callee])
                self.assertEqual(b['calls'], [setter, callee], 'the setter, then the original read')
                self.assertEqual(a['exit'], b['exit'])
                self.assertEqual(len(seen), 1)
                root, facing, pitch, flags, align = seen[0]
                self.assertEqual((root, flags, align), (ROOT_E, 0, 8), 'Win64 entry: RSP = 8 mod 16')
                self.assertAlmostEqual(facing, -0.5, places=6)
                self.assertAlmostEqual(pitch, 0.75, places=6)
            # already facing the guest's way: no setter, the same as stock
            for trial, state in self.states('MV-04', 6):
                a, b, delta, baseline = self.pair('MV-04', state, lambda uc: uc.mem_write(
                    PHYS_E + 0xA8, struct.pack('<f', -0.5)))
                gt.compare(self, self.emu, 'MV-04 same facing', a, b, baseline, state['gpr']['RSP'])
        finally:
            self.emu.answers.pop(setter, None)

    def test_gateway_call_stays_stock_with_movement_armed(self):
        emu, uc, twin = self.emu, self.emu.uc, self.m.twin
        for sid in ('MV-07',):
            row = twin.index[sid]
            self.assertEqual(twin.info(row).gate_open, 0)
            r = twin.rows[row]
            site = TWIN_BASE + r['rva']
            for trial in range(self.TRIALS):
                state = pe.random_state(random.Random(0xB2140 + trial))
                emu.refresh()
                emu.tick_clock(gt.NOW + 5)
                patched = bytes(uc.mem_read(site, 5))
                emu.write_code(site, checked(r)[:5])
                a = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5,
                            state)
                emu.write_code(site, patched)
                before = emu.stats(row)(uc)
                b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5,
                            state, probe=emu.stats(row))
                touched = set(a['final']) | set(b['final'])
                baseline = {x: uc.mem_read(x, 1)[0] for x in touched}
                gt.compare(self, emu, sid, a, b, baseline, state['gpr']['RSP'])
                self.assertEqual(tuple(x - y for x, y in zip(b['probe'], before))[:3], (0, 0, 0))


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class GameFunctionOracleTest(unittest.TestCase):
    """The game's own code, run whole from the hooked site with synthetic objects."""

    SINF, COSF, SQRT, PITCH_GATE, PITCH_GATE2, ANIM = 0x861F0E, 0x861F08, 0x3A1D30, 0x267260, 0x264460, 0x424080

    @classmethod
    def setUpClass(cls):
        cls.m = move_world()
        cls.m.setup()
        cls.emu = MoveEmulator(cls.m.twin)
        uc = cls.emu.uc
        X = pe.X
        rebase = lambda va: TWIN_BASE + va - 0x120000  # noqa: E731

        def answer(name):
            def hook(uc_):
                x0 = struct.unpack('<f', struct.pack('<I', uc_.reg_read(X.UC_X86_REG_XMM0) & 0xFFFFFFFF))[0]
                if name == 'sinf':
                    v = math.sin(x0)
                elif name == 'cosf':
                    v = math.cos(x0)
                elif name == 'sqrt':
                    v = math.sqrt(max(x0, 0.0))
                else:
                    uc_.reg_write(X.UC_X86_REG_RAX, 0)
                    return
                uc_.reg_write(X.UC_X86_REG_XMM0, f32_bits(v))
            return hook

        for va, name in ((cls.SINF, 'sinf'), (cls.COSF, 'cosf'), (cls.SQRT, 'sqrt'), (cls.PITCH_GATE, 'zero'),
                         (cls.PITCH_GATE2, 'zero'), (cls.ANIM, 'zero')):
            cls.emu.answers[rebase(va)] = answer(name)
        cls.rebase = staticmethod(rebase)

    @classmethod
    def tearDownClass(cls):
        cls.m.d.ILCT_MoveWithdraw()
        cls.m.twin.reset()

    RETURN_MARK = TWIN_BASE + 0x1000  # in the image, outside both functions: the run ends at the function's ret

    def run_function(self, start, end, state, setup):
        """Runs the hooked game function from its entry to its own ret (callees answered by the hooks above)."""
        emu, uc = self.emu, self.emu.uc
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        setup(uc)
        uc.mem_write(state['gpr']['RSP'], struct.pack('<Q', self.RETURN_MARK))
        out = emu.run(start, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or start <= x < end, state,
                      probe=lambda u: bytes(u.mem_read(OUT_E, 16)))
        self.assertEqual(out['exit'], self.RETURN_MARK, 'the function returned')
        self.assertEqual(emu.unexpected, [])
        return out

    def objects(self, uc, heading=2.0, magnitude=0.07):
        lay_out(uc, SCENE + [(PHYS_E, 0xA0, 'f', 0.0), (PHYS_E, 0xA4, 'f', heading), (PHYS_E, 0xD0, 'f', magnitude),
                             (OUT_E, 0x00, '4f', (0.0, 0.0, 0.0, 0.0))])

    def entry_state(self, r8):
        state = pe.random_state(random.Random(0xB21))
        state['gpr'].update(RCX=ROOT_E, RDX=OUT_E, R8=r8, R9=VEC_E)
        sp = state['gpr']['RSP'] & ~0xF
        state['gpr']['RSP'] = sp - 8  # a call just pushed its return address
        return state

    def test_locomotion_vector_follows_the_guest_command(self):
        start, end = self.rebase(0x2E4380), self.rebase(0x2E45D9)
        cases = [(0.0, 0.1), (1.25, 0.1), (-2.5, 0.04), (3.1, 1 / 12)]
        for heading, magnitude in cases:
            self.m.soft_reset()
            self.m.d.ILCT_MovePublish(ctypes.byref(record_for(dict(actor=ACTOR_E, phys=PHYS_E, root=ROOT_E,
                                                                   heading=heading, magnitude=magnitude))))
            for r8 in (0, 1):  # steering path selected by the game, or a zero root-motion vector overridden
                with self.subTest(heading=heading, magnitude=magnitude, r8=r8):
                    res = self.run_function(start, end, self.entry_state(r8), self.objects)
                    x, y, z, w = struct.unpack('<4f', res['probe'])
                    self.assertAlmostEqual(x, math.sin(heading) * magnitude, places=5)
                    self.assertAlmostEqual(y, 0.0, places=6)
                    self.assertAlmostEqual(z, math.cos(heading) * magnitude, places=5)
                    self.assertEqual(w, 1.0)

    def test_control_stock_function_uses_its_own_fields(self):
        """Control: with the record withdrawn the same function reads the physics object as the game left it,
        and with a zero root-motion vector selected it moves nothing; the instrument sees the difference."""
        start, end = self.rebase(0x2E4380), self.rebase(0x2E45D9)
        self.m.soft_reset()
        self.m.d.ILCT_MoveWithdraw()
        res = self.run_function(start, end, self.entry_state(0), lambda uc: self.objects(uc, 2.0, 0.07))
        x, _, z, _ = struct.unpack('<4f', res['probe'])
        self.assertAlmostEqual(x, math.sin(2.0) * 0.07, places=5)
        self.assertAlmostEqual(z, math.cos(2.0) * 0.07, places=5)
        res = self.run_function(start, end, self.entry_state(1), self.objects)
        self.assertEqual(struct.unpack('<4f', res['probe']), (0.0, 0.0, 0.0, 1.0))

    def approach_objects(self, uc, actor_handle, leader_handle):
        lay_out(uc, APPROACH_ACTOR + [(ACTOR_E, 0x08, 'I', actor_handle)])
        self.leader_handle = leader_handle

    def test_approach_gate_returns_no_approach_for_a_controlled_actor(self):
        """0x325D90 run to its return: a controlled follower answers 0 with the caller's rbx intact; the same
        follower uncontrolled answers 1 (it is not the leader); the leader is unaffected either way."""
        X = pe.X
        start, end = self.rebase(0x325D90), self.rebase(0x325E5B)
        leader_call = self.rebase(0x3590D0)
        self.emu.answers[leader_call] = lambda uc: uc.reg_write(X.UC_X86_REG_RAX, self.leader_handle)
        try:
            for controlled, actor_handle, expected in ((True, 0x100001, 0), (False, 0x100001, 1),
                                                       (False, 0x100000, 1)):
                with self.subTest(controlled=controlled, actor_handle=actor_handle):
                    self.m.soft_reset()
                    if controlled:
                        self.m.d.ILCT_MovePublish(ctypes.byref(record_for(
                            dict(actor=ACTOR_E, phys=PHYS_E, root=ROOT_E), flags=STEERF_LOGICAL)))
                    else:
                        self.m.d.ILCT_MoveWithdraw()
                    state = self.entry_state(0)
                    state['gpr']['RCX'] = ACTOR_E
                    caller_rbx = state['gpr']['RBX']
                    res = self.run_function(start, end, state, lambda uc: (
                        lay_out(uc, SCENE), self.approach_objects(uc, actor_handle, 0x100000)))
                    self.assertEqual(res['gpr']['RAX'] & 0xFFFFFFFF, expected)
                    self.assertEqual(res['gpr']['RBX'], caller_rbx, 'the caller rbx survives the gate')
                    self.assertEqual(res['gpr']['RSP'], state['gpr']['RSP'] + 8, 'returned through ret')
        finally:
            self.emu.answers.pop(leader_call, None)


if __name__ == '__main__':
    unittest.main()
