"""Host tests for the shared camera (native/coop/src/camera). No game run, nothing deployed.

* the maths, restated for three players (P1..P3; no fourth seat): centroid with the upper-actor bias, the widest
  pair, a third player between the others does not widen framing and an outlier does, zoom out is faster than zoom
  in, player order never changes framing, velocity history keyed by seat (a member dropping out does not hand its
  history to another), lead from one moving player, vertical focus falls faster than it rises, jitter stays inside
  the deadzone; the collision limiter (a native wall freezes the extra instead of zeroing it, release recovers
  without negative zoom, pressure steps back and recovers slowly, no zoom means no feedback); the pair-wise corridor
  capsules (separation and boom gates with hysteresis, length clamp, radius by boom, camera height) and the
  classification the wrapper uses; the focus escape; the internal latch guard;
* the subsystem on the session core's synthetic memory: two or more roles in a valid world and a camera-allowed
  phase turn it on after three ready samples (no fly-in); P3 keeps the camera when P2 is down; one survivor gives the
  game's camera back; a seat toggle resets it in place (new reset epoch, snapped to the party centre, no re-acquire
  and no beat without records), while off-to-on still waits for three ready samples;
* the hooks: CM-01 writes the predicted focus, CM-06/07 multiply by rig boom + extra only for the primary rig off a
  wall (CM-07 through its continuation, in unicorn); the collision wrapper around the query 0x230C00 runs in unicorn
  through the call island: disarmed and with nothing to classify it is the game's single call; an internal hit is
  asked again from just past it; an external hit stands; a stale pose is stock; every call sees the caller's stack
  arguments 5 and 6; 24 retries at most and never from past 98% of the ray; an unreadable stack is stock. CM-07 end
  to end leaves every other register, the flags and the XMM registers as the game left them.
"""
from __future__ import annotations

import ctypes
import itertools
import math
import random
import struct
import unittest

import test_coop_game_thread as gt
import test_coop_input_isolation as iso
import test_coop_patch_engine as pe
from coop_session_support import GATE, SEATF, ST_KO, native_test
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, call_offset
from test_coop_session import SessionCase

u8, u32, u64, f32, f64 = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_float, ctypes.c_double
RUN_ORIGINAL, RESUME = 0, 1
CAMERA = 0x08
CAMERA_MASTER, PRIMARY_RIG = 0x2AED630, 0x2098060
BACK_ON_WALL = 0x04000000
EPOCH = 7


class Vec3(ctypes.Structure):
    _fields_ = [('x', f64), ('y', f64), ('z', f64)]


class Member(ctypes.Structure):
    _fields_ = [('seat', u32), ('actor_key', u32), ('position', Vec3)]


class Frame(ctypes.Structure):
    _fields_ = [('focus', Vec3), ('extra', f64), ('separation', f64), ('lead', Vec3), ('velocity', Vec3),
                ('reset_epoch', u32), ('pad', u32)]


class Capsule(ctypes.Structure):
    _fields_ = [('a', f32 * 3), ('b', f32 * 3), ('hr2', f32), ('vr2', f32)]


class Latch(ctypes.Structure):
    _fields_ = [('have', u8), ('suppressing', u8), ('have_reference', u8), ('r', u8), ('previous', u32 * 4),
                ('pad', u32), ('recent', f64), ('suppression', f64), ('reference', f64)]


class LatchDecision(ctypes.Structure):
    _fields_ = [('suppress', u8), ('activated', u8), ('released', u8), ('r', u8), ('pad', u32), ('restored', f64)]


class PoseRecord(ctypes.Structure):
    _fields_ = [('epoch', u32), ('flags', u32), ('generation', u64), ('stamp_ms', u64), ('focus', f32 * 3),
                ('velocity', f32 * 3), ('extra', f32), ('reset_epoch', u32), ('reserved', u32 * 2)]


class CollisionRecord(ctypes.Structure):
    _fields_ = [('epoch', u32), ('flags', u32), ('generation', u64), ('escape_max2', f32), ('count', u32),
                ('capsules', Capsule * 3)]


class Ctx(ctypes.Structure):
    _fields_ = [('xmm', u64 * 12), ('resume', u64), ('reserved', u64)] + \
               [(n, u64) for n in ('rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11',
                                   'r12', 'r13', 'r14', 'r15', 'rflags', 'index')]


COUNTERS = ['steps', 'active_steps', 'resets', 'focus_written', 'zoom_applied', 'zoom_yielded', 'queries']


def declare(d):
    P = ctypes.POINTER
    sig = {
        'ILCT_CamSizes': ([P(u32), u32], u32), 'ILCT_CamCtlInit': ([ctypes.c_void_p], None),
        'ILCT_CamCtlReset': ([ctypes.c_void_p], None),
        'ILCT_CamUpdate': ([ctypes.c_void_p, P(Member), u32, f64, P(Frame)], None),
        'ILCT_CamLimited': ([ctypes.c_void_p, f64, f64, f64, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
                             ctypes.c_int32], f64),
        'ILCT_CamFocusTarget': ([P(Member), u32, P(Vec3)], None),
        'ILCT_CamNativeDistance': ([P(Vec3), P(Member), u32], f64),
        'ILCT_CamCorridor': ([P(Member), u32, f64, ctypes.c_int32, P(Capsule)], u32),
        'ILCT_CamFocusEscape': ([P(Member), u32, P(Vec3)], f32),
        'ILCT_CamInside': ([P(Capsule), P(f32)], ctypes.c_int32),
        'ILCT_CamLatchReset': ([P(Latch)], None),
        'ILCT_CamLatchObserve': ([P(Latch), P(u32), f64, ctypes.c_int32, f64, P(LatchDecision)], None),
        'ILCT_CamReset': ([], None), 'ILCT_CamStart': ([u64], None), 'ILCT_CamStep': ([u64], None),
        'ILCT_CamStop': ([], None), 'ILCT_CamRequestReset': ([], None), 'ILCT_CamAttach': ([], ctypes.c_int32),
        'ILCT_CamPose': ([P(PoseRecord)], ctypes.c_int32), 'ILCT_CamCollision': ([P(CollisionRecord)], ctypes.c_int32),
        'ILCT_CamPublishPose': ([P(PoseRecord)], None), 'ILCT_CamPublishCollision': ([P(CollisionRecord)], None),
        'ILCT_CamWithdraw': ([], None), 'ILCT_CamCall': ([u32, P(Ctx), u32], ctypes.c_int32),
        'ILCT_CamThunk': ([], u64), 'ILCT_CamStats': ([ctypes.c_void_p], None),
        'ILCT_CamDebug': ([ctypes.c_void_p], None),
    }
    for name, (args, res) in sig.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def members(*positions, seats=None, keys=None):
    seats = seats if seats is not None else range(len(positions))
    keys = keys if keys is not None else [0x1001 + 2 * s for s in seats]  # one body per seat unless told
    arr = (Member * max(1, len(positions)))()
    for k, (seat, key, p) in enumerate(zip(seats, keys, positions)):
        arr[k].seat = seat
        arr[k].actor_key = key
        arr[k].position = Vec3(*p)
    return arr, len(positions)


class Camera:
    """A controller in this process's memory, driven through the test DLL."""

    def __init__(self, d):
        self.d = d
        sizes = (u32 * 16)()
        d.ILCT_CamSizes(sizes, 16)
        self.buf = ctypes.create_string_buffer(sizes[0] + 16)
        self.ptr = (ctypes.addressof(self.buf) + 15) & ~15
        d.ILCT_CamCtlInit(self.ptr)

    def update(self, positions, dt=1 / 60, seats=None, keys=None) -> Frame:
        arr, n = members(*positions, seats=seats, keys=keys)
        out = Frame()
        self.d.ILCT_CamUpdate(self.ptr, arr, n, dt, ctypes.byref(out))
        return out

    def run(self, positions, steps, dt=1 / 60, seats=None) -> Frame:
        f = None
        for _ in range(steps):
            f = self.update(positions, dt, seats)
        return f

    def limited(self, actual, native, dt=1 / 60, new=True, wall=False, allow=True, release=False) -> float:
        return self.d.ILCT_CamLimited(self.ptr, actual, native, dt, int(new), int(wall), int(allow), int(release))


_dll = None


def dll():
    global _dll
    if _dll is None:
        _dll = declare(pe.Twin.get().dll)
    return _dll


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class CameraMathTest(unittest.TestCase):
    def setUp(self):
        self.d = dll()
        self.c = Camera(self.d)

    def test_focus_is_the_centroid_with_the_upper_actor_bias(self):
        arr, n = members((0, 0, 0), (6, 0, 0), (3, 3, 6))
        out = Vec3()
        self.d.ILCT_CamFocusTarget(arr, n, ctypes.byref(out))
        mean = 1.0
        self.assertAlmostEqual(out.x, 3.0)
        self.assertAlmostEqual(out.z, 2.0)
        self.assertAlmostEqual(out.y, mean + (3 - mean) * 0.85 + 1.30)
        first = self.c.update([(0, 0, 0), (6, 0, 0), (3, 3, 6)])
        for a, b in ((first.focus.x, out.x), (first.focus.y, out.y), (first.focus.z, out.z)):
            self.assertAlmostEqual(a, b, msg='the first frame is the exact target, no fly-in')

    def test_separation_is_the_widest_of_the_three_pairs(self):
        f = self.c.update([(0, 0, 0), (3, 0, 4), (0, 0, 10)])
        self.assertAlmostEqual(f.separation, 10.0)

    def test_a_third_player_between_the_others_does_not_widen_framing(self):
        two = Camera(self.d).run([(0, 0, 0), (12, 0, 0)], 240, seats=[0, 1])
        three = self.c.run([(0, 0, 0), (12, 0, 0), (6, 0, 0)], 240)
        self.assertAlmostEqual(three.extra, two.extra, places=6)
        outlier = Camera(self.d).run([(0, 0, 0), (12, 0, 0), (6, 0, 14)], 240)
        self.assertGreater(outlier.extra, two.extra + 3.0, 'an outlying third player widens framing')

    def test_zoom_out_is_faster_than_zoom_in(self):
        spread, close = [(0, 0, 0), (10, 0, 0), (20, 0, 0)], [(0, 0, 0), (1, 0, 0), (2, 0, 0)]
        self.c.run(close, 60)
        target = 1.05 * (20 - 4.0)
        out_steps = next(k for k in range(1, 600) if self.c.update(spread).extra >= target * 0.5)
        self.c.run(spread, 600)
        in_steps = next(k for k in range(1, 600) if self.c.update(close).extra <= target * 0.5)
        self.assertLess(out_steps * 3, in_steps, (out_steps, in_steps))

    def test_player_order_never_changes_framing(self):
        path = [[(math.sin(t / 9) * 6, 0.1 * t % 1.0, t * 0.05), (4 + t * 0.03, 0.0, -3.0), (-5.0, 1.0, t * 0.02)]
                for t in range(120)]
        reference = None
        for order in itertools.permutations(range(3)):
            cam = Camera(self.d)
            frames = [cam.update([step[k] for k in order], seats=list(order)) for step in path]
            trace = [(f.focus.x, f.focus.y, f.focus.z, f.extra) for f in frames]
            if reference is None:
                reference = trace
            with self.subTest(order=order):
                worst = max(abs(a - b) for x, y in zip(trace, reference) for a, b in zip(x, y))
                self.assertLess(worst, 1e-9)

    def test_velocity_history_is_keyed_by_seat(self):
        """P1, P2 and P3 walk east; P2 drops out (KO). P3 must keep its own history: a position-keyed history would
        pair P3's position with P2's last one and produce a spike in the lead."""
        cam = Camera(self.d)
        leads = []
        for t in range(60):
            x = t * 0.05
            f = cam.update([(x, 0, 0), (x, 0, 5), (x, 0, 12)], seats=[0, 1, 2])
            leads.append(f.lead.x)
        before = leads[-1]
        after = [cam.update([((60 + t) * 0.05, 0, 0), ((60 + t) * 0.05, 0, 12)], seats=[0, 2]).lead for t in range(10)]
        for lead in after:
            self.assertLess(abs(lead.z), 0.05, 'a sideways lead spike: histories were paired by list position')
            self.assertLess(abs(lead.x - before), 0.05)
        # The same seats in another list order are the same members.
        f1 = Camera(self.d)
        f2 = Camera(self.d)
        for t in range(30):
            a = f1.update([(t * 0.05, 0, 0), (0, 0, 9)], seats=[0, 2])
            b = f2.update([(0, 0, 9), (t * 0.05, 0, 0)], seats=[2, 0])
        self.assertAlmostEqual(a.lead.x, b.lead.x, places=9)

    def _standing_then(self, cam, second, keys2, seats2=(0, 1, 2)):
        for _ in range(60):
            cam.update([(0, 0, 0), (2, 0, 0), (0, 0, 4)], keys=[0x11, 0x21, 0x31])
        return [cam.update(second, seats=list(seats2), keys=keys2) for _ in range(10)]

    def test_a_seat_that_changes_body_starts_a_fresh_velocity_history(self):
        """A character swap: P2's seat now drives a body 10 units away. Keyed by seat alone, that is one ~600 u/s
        sample (clamped) and the lead jumps to its cap; keyed by (seat, actor) nothing moved."""
        swapped = self._standing_then(Camera(self.d), [(0, 0, 0), (12, 0, 0), (0, 0, 4)], [0x11, 0x41, 0x31])
        for f in swapped:
            self.assertLess(math.hypot(f.lead.x, f.lead.z), 0.02, 'a swap became a velocity')
        # Instrument: the same jump on the same body is a real (if absurd) motion and does lead.
        jumped = self._standing_then(Camera(self.d), [(0, 0, 0), (12, 0, 0), (0, 0, 4)], [0x11, 0x21, 0x31])
        self.assertGreater(max(math.hypot(f.lead.x, f.lead.z) for f in jumped), 0.1)

    def test_a_two_body_exchange_between_seats_does_not_lead(self):
        """Player 1 and P2 exchange characters: both seats change body in the same step."""
        frames = self._standing_then(Camera(self.d), [(2, 0, 0), (0, 0, 0), (0, 0, 4)], [0x21, 0x11, 0x31])
        for f in frames:
            self.assertLess(math.hypot(f.lead.x, f.lead.z), 0.02)

    def test_park_and_return_elsewhere_starts_afresh(self):
        """P2 parks (leaves the framed set), its body walks off under the AI, P2 comes back: no catch-up velocity."""
        cam = Camera(self.d)
        for _ in range(60):
            cam.update([(0, 0, 0), (2, 0, 0), (0, 0, 4)], keys=[0x11, 0x21, 0x31])
        for _ in range(30):
            cam.update([(0, 0, 0), (0, 0, 4)], seats=[0, 2], keys=[0x11, 0x31])
        back = [cam.update([(0, 0, 0), (14, 0, 0), (0, 0, 4)], keys=[0x11, 0x21, 0x31]) for _ in range(10)]
        for f in back:
            self.assertLess(math.hypot(f.lead.x, f.lead.z), 0.02, 'a returning seat paired with its old position')

    def test_a_pad_disconnect_reconcile_onto_another_body_does_not_lead(self):
        """P3's pad drops; the reconcile keeps P3's seat index but hands it another character in the same step."""
        frames = self._standing_then(Camera(self.d), [(0, 0, 0), (2, 0, 0), (9, 0, -6)], [0x11, 0x21, 0x51])
        for f in frames:
            self.assertLess(math.hypot(f.lead.x, f.lead.z), 0.02)

    def test_a_swap_and_a_toggle_in_the_same_beat_do_not_lead(self):
        """P1 and P2 exchange characters on the very beat P3 toggles out; later P3 toggles back in on the beat P2 is
        handed another body. Every membership change lands in one step each time: none of it is a velocity."""
        cam = Camera(self.d)
        for _ in range(60):
            cam.update([(0, 0, 0), (2, 0, 0), (0, 0, 4)], keys=[0x11, 0x21, 0x31])
        frames = [cam.update([(2, 0, 0), (0, 0, 0)], seats=[0, 1], keys=[0x21, 0x11]) for _ in range(10)]
        frames += [cam.update([(2, 0, 0), (9, 0, -6), (0, 0, 11)], seats=[0, 1, 2], keys=[0x21, 0x51, 0x31])
                   for _ in range(10)]
        for f in frames:
            self.assertLess(math.hypot(f.lead.x, f.lead.z), 0.02, 'a swap or a toggle in the same beat became a lead')
        # Instrument: real motion right after still leads.
        moved = [cam.update([(2 + 0.08 * t, 0, 0), (9, 0, -6), (0, 0, 11)], seats=[0, 1, 2],
                            keys=[0x21, 0x51, 0x31]) for t in range(1, 40)]
        self.assertGreater(max(f.lead.x for f in moved), 0.02)

    def test_one_moving_player_produces_a_lead_toward_its_motion(self):
        f = None
        for t in range(60):
            f = self.c.update([(0, 0, 0), (0, 0, 6), (t * 0.08, 0, 3)])
        self.assertGreater(f.lead.x, 0.05)
        self.assertLessEqual(math.hypot(f.lead.x, f.lead.z), 0.90 + 1e-9)

    def test_vertical_focus_falls_faster_than_it_rises(self):
        low, high = [(0, 0, 0), (2, 0, 0), (4, 0, 0)], [(0, 3, 0), (2, 3, 0), (4, 3, 0)]
        self.c.run(low, 60)
        rise = next(k for k in range(1, 300) if self.c.update(high).focus.y >= 3 + 1.30 - 0.15)
        self.c.run(high, 300)
        fall = next(k for k in range(1, 300) if self.c.update(low).focus.y <= 1.30 + 0.15)
        self.assertLess(fall, rise)

    def test_small_jitter_stays_inside_the_deadzone(self):
        base = [(0, 0, 0), (2, 0, 0), (1, 0, 1)]
        start = self.c.update(base).focus
        rng = random.Random(9)
        for _ in range(120):
            jit = [(x + rng.uniform(-0.004, 0.004), y, z + rng.uniform(-0.004, 0.004)) for x, y, z in base]
            f = self.c.update(jit)
        self.assertLess(math.hypot(f.focus.x - start.x, f.focus.z - start.z), 0.03)

    # ---- the collision limiter ----
    def spread(self):
        return self.c.run([(0, 0, 0), (10, 0, 0), (20, 0, 0)], 240)

    def test_a_native_wall_freezes_the_extension_instead_of_zeroing_it(self):
        self.spread()
        native = 10.0
        free = self.c.limited(native + 16.8, native, allow=True)
        self.assertGreater(free, 10.0)
        frozen = self.c.limited(3.0, native, wall=True)
        self.assertAlmostEqual(frozen, free, places=9)
        self.assertAlmostEqual(self.c.limited(2.0, native, wall=True), free, places=9, msg='stays frozen')
        # Still latched on a clear frame without a release reason.
        self.assertAlmostEqual(self.c.limited(native + free, native, wall=False), free, places=9)

    def test_a_wall_release_recovers_without_negative_zoom(self):
        f = self.spread()
        self.c.limited(10 + f.extra, 10.0)
        self.c.limited(3.0, 10.0, wall=True)
        values = [self.c.limited(10 + f.extra, 10.0, release=True) for _ in range(120)]
        self.assertTrue(all(v >= 0.0 for v in values))
        self.assertAlmostEqual(values[-1], f.extra, delta=0.06)

    def test_collision_pressure_steps_back_then_recovers_slowly(self):
        self.spread()
        native = 10.0
        desired = native + self.c.limited(native + 16.8, native)
        squeezed = self.c.limited(native + 4.0, native)  # the game achieved far less than asked
        self.assertLess(native + squeezed, desired - 0.7)
        held = [self.c.limited(native + squeezed, native, new=False) for _ in range(20)]  # 0.33 s: inside the hold
        self.assertTrue(all(abs(h - held[0]) < 1e-9 for h in held), 'held at the backed-off boom')
        later = [self.c.limited(native + 20.0, native, new=False) for _ in range(120)]
        steps = [b - a for a, b in zip(later, later[1:]) if b > a]
        self.assertTrue(steps and max(steps) <= 6.0 / 60 + 1e-9, 'recovery at most 6 units/s')

    def test_no_zoom_gives_no_feedback(self):
        self.c.run([(0, 0, 0), (1, 0, 0), (2, 0, 0)], 60)
        self.assertEqual(self.c.limited(2.0, 10.0, wall=True), 0.0)
        self.assertEqual(self.c.limited(30.0, 10.0), 0.0)

    # ---- the corridor and the wrapper's classification ----
    def corridor(self, positions, boom=20.0, previously=False):
        arr, n = members(*positions)
        out = (Capsule * 3)()
        count = self.d.ILCT_CamCorridor(arr, n, boom, int(previously), out)
        return [out[k] for k in range(count)]

    def test_corridor_capsules_for_every_pair_far_enough_apart(self):
        caps = self.corridor([(0, 0, 0), (10, 0, 0), (20, 0, 0)])
        self.assertEqual(len(caps), 3, 'all three pairs are >= 6 apart')
        caps = self.corridor([(0, 0, 0), (3, 0, 0), (12, 0, 0)])
        self.assertEqual(len(caps), 2, 'the 3-unit pair is too close')
        self.assertEqual(self.corridor([(0, 0, 0), (2, 0, 0), (4, 0, 0)]), [])
        c = self.corridor([(0, 0, 0), (10, 2, 0)])[0]
        self.assertAlmostEqual(c.a[1], 1.30, places=5)
        self.assertAlmostEqual(c.b[1], 3.30, places=5)
        self.assertAlmostEqual(c.hr2, 25.0, places=4)
        self.assertAlmostEqual(c.vr2, 3.25 ** 2, places=4)

    def test_corridor_boom_gate_hysteresis_length_clamp_and_radius(self):
        positions = [(0, 0, 0), (30, 0, 0)]
        self.assertEqual(self.corridor(positions, boom=7.0), [], 'below 8 it does not turn on')
        self.assertEqual(len(self.corridor(positions, boom=7.0, previously=True)), 1, 'kept down to 6 once on')
        self.assertEqual(self.corridor(positions, boom=5.9, previously=True), [])
        c = self.corridor(positions, boom=9.0)[0]
        self.assertAlmostEqual(c.b[0] - c.a[0], 16.0, places=4, msg='length clamped to 16 around the midpoint')
        self.assertAlmostEqual((c.a[0] + c.b[0]) / 2, 15.0, places=4)
        self.assertAlmostEqual(c.hr2, 25.0, places=4, msg='radius min(5, 0.75 * boom)')
        c = self.corridor([(0, 0, 0), (10, 0, 0)], boom=8.0)[0]
        self.assertAlmostEqual(c.hr2, 25.0, places=4)
        c = self.corridor([(0, 0, 0), (10, 0, 0)], boom=-1.0)[0]
        self.assertAlmostEqual(c.hr2, 25.0, places=4, msg='unknown boom: no boom gate')

    def test_inside_a_capsule(self):
        c = self.corridor([(0, 0, 0), (10, 0, 0)])[0]

        def inside(x, y, z):
            return self.d.ILCT_CamInside(ctypes.byref(c), (f32 * 3)(x, y, z))

        self.assertEqual(inside(5, 1.3, 0), 1, 'a prop at the midpoint')
        self.assertEqual(inside(5, 1.3, 4.9), 1)
        self.assertEqual(inside(5, 1.3, 5.1), 0, 'beside the corridor')
        self.assertEqual(inside(-4.9, 1.3, 0), 1, 'round end')
        self.assertEqual(inside(16, 1.3, 0), 0, 'past the end')
        self.assertEqual(inside(5, 1.3 + 3.3, 0), 0, 'a ceiling stays native')
        self.assertEqual(inside(5, float('nan'), 0), 0)

    def test_focus_escape(self):
        arr, n = members((0, 0, 0), (10, 0, 0), (5, 0, 8))
        near = Vec3(0, 1.3, 0.5)
        far = Vec3(5, 4, 3)
        self.assertEqual(self.d.ILCT_CamFocusEscape(arr, n, ctypes.byref(near)), 0.0)
        self.assertAlmostEqual(self.d.ILCT_CamFocusEscape(arr, n, ctypes.byref(far)), 5.30 ** 2, places=4)

    def test_native_focus_distance(self):
        arr, n = members((0, 0, 0), (4, 0, 0), (2, 0, 4))
        target = Vec3()
        self.d.ILCT_CamFocusTarget(arr, n, ctypes.byref(target))
        self.assertAlmostEqual(self.d.ILCT_CamNativeDistance(ctypes.byref(target), arr, n), 0.0)
        away = Vec3(target.x + 20, target.y, target.z)
        self.assertAlmostEqual(self.d.ILCT_CamNativeDistance(ctypes.byref(away), arr, n), 20.0)

    # ---- the latch guard ----
    def latch(self):
        g = Latch()
        self.d.ILCT_CamLatchReset(ctypes.byref(g))
        return g

    def observe(self, g, counters, boom, wall, dt=1 / 60) -> LatchDecision:
        out = LatchDecision()
        self.d.ILCT_CamLatchObserve(ctypes.byref(g), (u32 * 4)(*counters), boom, int(wall), dt, ctypes.byref(out))
        return out

    def test_latch_guard_suppresses_a_delayed_internal_collapse(self):
        g = self.latch()
        self.observe(g, (0, 0, 0, 0), 12.0, False)
        self.observe(g, (1, 0, 0, 0), 12.0, False)
        d = self.observe(g, (1, 0, 0, 0), 4.0, True)
        self.assertEqual((d.suppress, d.activated), (1, 1))
        self.assertAlmostEqual(d.restored, 12.0)

    def test_latch_guard_never_suppresses_an_external_hit(self):
        g = self.latch()
        self.observe(g, (0, 0, 0, 0), 12.0, False)
        self.observe(g, (1, 0, 0, 0), 12.0, False)
        d = self.observe(g, (2, 0, 0, 1), 4.0, True)
        self.assertEqual(d.suppress, 0)

    def test_latch_guard_expires_stale_internal_evidence(self):
        g = self.latch()
        self.observe(g, (0, 0, 0, 0), 12.0, False)
        self.observe(g, (1, 0, 0, 0), 12.0, False)
        for _ in range(12):  # 0.2 s without an internal hit
            self.observe(g, (1, 0, 0, 0), 12.0, False)
        self.assertEqual(self.observe(g, (1, 0, 0, 0), 4.0, True).suppress, 0)

    def test_latch_guard_releases_after_native_recovery(self):
        g = self.latch()
        self.observe(g, (0, 0, 0, 0), 12.0, False)
        self.observe(g, (1, 0, 0, 0), 12.0, False)
        self.assertEqual(self.observe(g, (1, 0, 0, 0), 4.0, True).suppress, 1)
        d = self.observe(g, (1, 0, 0, 0), 11.5, False)
        self.assertEqual((d.suppress, d.released), (0, 1))


# ---- the subsystem on the session core's synthetic memory ------------------------------------------------------------
CAM_DEBUG = 64 + 8  # active, pending, roles, capsules (4 x u32), extra, native boom, orbit hold, then the controller


@native_test
class CameraSessionTest(SessionCase):
    def setUp(self):
        super().setUp()
        declare(self.d)
        self.d.ILCT_CamReset()

    def tearDown(self):
        self.d.ILCT_CamReset()
        super().tearDown()

    def native_camera(self, focus, eye_offset=(0, 4, -12), boom=12.0, flags=0, yaw=0.0):
        eye = tuple(f + o for f, o in zip(focus, eye_offset))
        self.img.put_global(CAMERA_MASTER + 0x90, struct.pack('<7f', *eye, 0.0, *focus))
        self.img.put_global(PRIMARY_RIG + 0x30, struct.pack('<3f', yaw, 0.3, boom))
        self.img.put_global(PRIMARY_RIG + 0x40, struct.pack('<I', flags))

    def beat(self, n=1):
        for _ in range(n):
            self.now += 20
            self.d.ILCT_SesStep(self.now)
            self.d.ILCT_CamStep(self.now)

    def debug(self):
        raw = ctypes.create_string_buffer(4096)
        self.d.ILCT_CamDebug(raw)
        active, pending, roles, capsules = struct.unpack_from('<4I', raw.raw, 0)
        return {'active': active, 'pending': pending, 'roles': roles, 'capsules': capsules}

    def pose(self):
        p = PoseRecord()
        return p if self.d.ILCT_CamPose(ctypes.byref(p)) else None

    def start_world(self):
        actors = self.field()
        for k, a in enumerate(actors):
            a.set_position((10.0 + 8 * k, 2.0, 30.0))
        self.start()
        self.d.ILCT_CamStart(self.img.base)
        # the game's focus already near the party target: the area has settled
        self.native_camera((18.0, 2.0 + 1.3, 30.0))
        return actors

    def test_on_after_three_ready_samples_and_the_first_frame_snaps(self):
        self.start_world()
        seen = []
        for _ in range(8):
            self.beat()
            seen.append(self.debug()['active'])
        self.assertIn(1, seen)
        first_on = seen.index(1)
        self.assertGreaterEqual(first_on, 2, 'needs three ready samples (no fly-in)')
        p = self.pose()
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p.focus[0], 18.0, places=3, msg='seeded at the group centre')
        self.assertEqual(self.debug()['roles'], 3)

    def test_the_game_focus_far_from_the_party_waits(self):
        self.start_world()
        self.native_camera((200.0, 0.0, 0.0))  # a new area: the game's camera has not arrived yet
        self.beat(10)
        self.assertEqual(self.debug()['active'], 0)
        self.assertIsNone(self.pose())
        self.native_camera((18.0, 3.3, 30.0))
        self.beat(4)
        self.assertEqual(self.debug()['active'], 1)

    def test_p3_keeps_the_camera_when_p2_is_down_and_one_survivor_gives_it_back(self):
        actors = self.start_world()
        self.beat(8)
        self.assertEqual(self.debug()['active'], 1)
        actors[1].set_status(ST_KO)  # P2's character down
        actors[1].set_hp(0)
        self.beat(8)
        d = self.debug()
        self.assertEqual((d['active'], d['roles']), (1, 2), 'P1 + P3 still frame together')
        actors[2].set_status(ST_KO)
        actors[2].set_hp(0)
        self.beat(8)
        self.assertEqual(self.debug()['active'], 0, 'one survivor: the game camera')
        self.assertIsNone(self.pose(), 'records withdrawn: every hook stock')

    def stats(self):
        raw = ctypes.create_string_buffer(256)
        self.d.ILCT_CamStats(raw)
        return dict(zip(COUNTERS, struct.unpack_from('<7Q', raw.raw, 0)))

    def test_a_seat_toggle_resets_in_place_with_a_new_reset_epoch(self):
        """A reset while the camera stays on is in place (the reference resets its controller on a toggle): no beat
        without records, no re-acquire, a new reset epoch and the focus snapped to the party centre on the same
        beat, even with the game's own focus far from the party (a guest more than 16 away would never re-acquire)."""
        actors = self.start_world()
        self.beat(8)
        before = self.pose().reset_epoch
        actors[2].set_position((60.0, 2.0, 30.0))  # P3 runs off: the spring lags behind the new centre
        self.native_camera((200.0, 0.0, 0.0))      # and the game's focus is nowhere near the party
        self.beat(1)
        lagging = self.pose().focus[0]
        self.assertGreater(abs(lagging - (10.0 + 18.0 + 60.0) / 3), 1.0, 'without a reset the focus lags')
        resets = self.stats()['resets']
        self.d.ILCT_CamRequestReset()
        self.beat(1)
        self.assertEqual(self.debug()['active'], 1, 'stays on: no re-acquire')
        p = self.pose()
        self.assertIsNotNone(p, 'records keep publishing')
        self.assertNotEqual(p.reset_epoch, before, 'a new reset epoch: the hooks snap')
        self.assertAlmostEqual(p.focus[0], (10.0 + 18.0 + 60.0) / 3, places=3, msg='seeded at the party centre')
        self.assertEqual(self.stats()['resets'], resets + 1)
        for _ in range(6):
            self.beat(1)
            self.assertEqual(self.debug()['active'], 1)
            self.assertIsNotNone(self.pose())

    def test_off_to_on_still_waits_for_three_ready_samples_after_a_reset(self):
        """A reset while off changes nothing: the next off-to-on change still waits for three ready samples."""
        actors = self.start_world()
        self.beat(8)
        actors[1].set_status(ST_KO)
        actors[1].set_hp(0)
        actors[2].set_status(ST_KO)
        actors[2].set_hp(0)
        self.beat(2)
        self.assertEqual(self.debug()['active'], 0)
        self.d.ILCT_CamRequestReset()
        actors[2].set_status(0)
        actors[2].set_hp(500)
        seen = []
        for _ in range(8):
            self.beat()
            seen.append(self.debug()['active'])
        self.assertIn(1, seen)
        self.assertGreaterEqual(seen.index(1), 2, 'three ready samples before it is back')

    def test_an_f6_drop_out_resets_the_camera(self):
        """The reset the seat toggles deferred to the camera: a seat toggle (here P2 dropping out with F6) resets the
        shared camera. The switches read a drop-in/out key only as that seat's action edge (the action layer reads F6
        for P2), so the press is written into the edge history."""
        from test_coop_control import SIGNATURES, TOGGLE_BIT, settings
        for name, (args, res) in SIGNATURES.items():
            fn = getattr(self.d, name)
            fn.argtypes, fn.restype = args, res
        self.d.ILCT_CtlReset()
        self.d.ILCT_CtlConfigure(settings())
        self.d.ILCT_EdgeTestReset()
        self.d.ILCT_EdgeReset(1)
        u32 = ctypes.c_uint32

        def edge(seq, p2_pressed):
            masks = (u32 * 28)()
            if p2_pressed:
                masks[1 * 7 + 3] = masks[1 * 7 + 6] = TOGGLE_BIT
            self.d.ILCT_EdgeRecord(seq, masks)

        edge(2, False)  # the switches' cursor seeds on this tick at their first beat
        try:
            actors = self.start_world()
            actors[2].set_position((34.0, 2.0, 30.0))  # P1 + P3 centre (22) differs from the three-player centre
            for _ in range(8):
                self.now += 20
                self.d.ILCT_CtlStep(self.now)
                self.d.ILCT_SesStep(self.now)
                self.d.ILCT_CamStep(self.now)
            before = self.pose().reset_epoch
            edge(3, True)  # P2's F6: its TOGGLE_CONTROL press
            self.now += 20
            self.d.ILCT_CtlStep(self.now)
            self.d.ILCT_SesStep(self.now)
            self.d.ILCT_CamStep(self.now)
            edge(4, False)
            d = self.debug()
            self.assertEqual((d['active'], d['roles']), (1, 2), 'reset in place: P1 + P3 frame together on that beat')
            p = self.pose()
            self.assertIsNotNone(p, 'no beat handed back to the game camera')
            self.assertNotEqual(p.reset_epoch, before, 'the toggle reset the camera')
            self.assertAlmostEqual(p.focus[0], (10.0 + 34.0) / 2, places=3, msg='snapped to the P1 + P3 centre')
            for _ in range(5):
                self.now += 20
                self.d.ILCT_CtlStep(self.now)
                self.d.ILCT_SesStep(self.now)
                self.d.ILCT_CamStep(self.now)
                self.assertEqual(self.debug()['active'], 1)
            self.assertEqual(self.debug()['roles'], 2)
        finally:
            self.d.ILCT_CtlReset()

    def test_corridor_and_escape_are_published_for_a_spread_party(self):
        actors = self.start_world()
        for k, a in enumerate(actors):
            a.set_position((10.0 + 12 * k, 2.0, 30.0))
        self.native_camera((22.0, 3.3, 30.0), boom=12.0)
        self.beat(10)
        col = CollisionRecord()
        self.assertEqual(self.d.ILCT_CamCollision(ctypes.byref(col)), 1)
        self.assertEqual(col.count, 3)
        self.assertEqual(col.flags & 1, 1)


# ---- the hooks ---------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class CameraHookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = gt.world()

    def setUp(self):
        self.w.setup()
        self.d = declare(self.w.twin.dll)
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_CamReset()
        self.assertEqual(self.d.ILCT_CamAttach(), 1)
        self.d.ILCT_SetFixedClock(gt.NOW)

    def tearDown(self):
        self.w.twin.reset()

    def publish(self, flags=1, stamp=gt.NOW, focus=(1.0, 2.0, 3.0), velocity=(0.0, 0.0, 0.0), extra=4.0,
                epoch=EPOCH):
        p = PoseRecord(epoch, flags, 1, stamp, (f32 * 3)(*focus), (f32 * 3)(*velocity), extra, 3)
        self.d.ILCT_CamPublishPose(ctypes.byref(p))

    def rig(self, boom=10.0, flags=0):
        self.w.twin.poke(PRIMARY_RIG + 0x38, struct.pack('<f', boom))
        self.w.twin.poke(PRIMARY_RIG + 0x40, struct.pack('<I', flags))

    def test_cm01_writes_the_predicted_focus_and_skips_the_store(self):
        obj = ctypes.create_string_buffer(0x80)
        c = Ctx()
        c.index = self.w.twin.index['CM-01']  # the dispatcher hands the row to the handler
        c.rdi = ctypes.addressof(obj)
        self.publish(stamp=gt.NOW - 20, focus=(4.0, -1.5, 7.25), velocity=(8.0, 0.0, -2.5))
        self.assertEqual(self.d.ILCT_CamCall(1, ctypes.byref(c), EPOCH), RESUME)
        self.assertEqual(c.resume, 0x32B26A)
        x, y, z, w = struct.unpack_from('<4f', obj.raw, 0x60)
        self.assertAlmostEqual(x, 4.0 + 8.0 * 0.020, places=5)
        for got, want in ((y, -1.5), (z, 7.25 - 2.5 * 0.020), (w, 1.0)):
            self.assertAlmostEqual(got, want, places=5)
        self.publish(stamp=gt.NOW - 300)
        self.assertEqual(self.d.ILCT_CamCall(1, ctypes.byref(c), EPOCH), RUN_ORIGINAL, 'a stale pose is stock')
        self.publish(flags=0)
        self.assertEqual(self.d.ILCT_CamCall(1, ctypes.byref(c), EPOCH), RUN_ORIGINAL)
        self.publish()
        self.d.ILCT_PatchApplySubsystems(0xFF & ~CAMERA)
        self.assertEqual(self.d.ILCT_CamCall(1, ctypes.byref(c), EPOCH), RUN_ORIGINAL, 'CAMERA arm bit off: stock')

    def test_cm06_and_cm07_use_rig_boom_plus_extra_for_the_primary_rig_off_a_wall(self):
        self.rig(boom=10.0)
        self.publish(extra=4.0)
        c = Ctx()
        c.index = self.w.twin.index['CM-06']
        c.rsi = PRIMARY_RIG
        c.xmm[6] = struct.unpack('<I', struct.pack('<f', 2.0))[0]  # xmm3 low lane (index 3 * 2)
        self.assertEqual(self.d.ILCT_CamCall(6, ctypes.byref(c), EPOCH), RESUME)
        self.assertEqual(c.resume, 0x32CD6A)
        self.assertAlmostEqual(struct.unpack('<f', struct.pack('<I', c.xmm[6] & 0xFFFFFFFF))[0], 2.0 * 14.0)
        c7 = Ctx()
        c7.index = self.w.twin.index['CM-07']
        c7.rsi = PRIMARY_RIG
        c7.xmm[4] = 0x1234567800000000  # xmm2 low qword: its upper half is kept
        c7.rcx = 0x55
        self.assertEqual(self.d.ILCT_CamCall(7, ctypes.byref(c7), EPOCH), RESUME)
        self.assertEqual(c7.resume, self.d.ILCT_CamThunk())
        self.assertAlmostEqual(struct.unpack('<f', struct.pack('<I', c7.xmm[4] & 0xFFFFFFFF))[0], 14.0)
        self.assertEqual((c7.xmm[4] >> 32, c7.rcx), (0x12345678, 0x55), 'only xmm2 low lane carries the boom')
        self.rig(boom=10.0, flags=BACK_ON_WALL)
        self.assertEqual(self.d.ILCT_CamCall(6, ctypes.byref(c), EPOCH), RUN_ORIGINAL, 'back on a wall: native')
        self.rig(boom=10.0)
        other = Ctx()
        other.index = self.w.twin.index['CM-07']
        other.rsi = PRIMARY_RIG + 0x100
        self.assertEqual(self.d.ILCT_CamCall(7, ctypes.byref(other), EPOCH), RUN_ORIGINAL, 'not the primary rig')

    def test_the_collision_wrapper_fails_open_on_an_unreadable_stack(self):
        """No trap anywhere in the wrapper: an unreadable return address or stack argument is the game's own call,
        decided before any query runs."""
        p = PoseRecord(EPOCH, 1, 1, gt.NOW, (f32 * 3)(0, 0, 0), (f32 * 3)(0, 0, 0), 0.0, 3)
        self.d.ILCT_CamPublishPose(ctypes.byref(p))
        col = CollisionRecord(EPOCH, 1, 1, 5.3 ** 2, 0)
        self.d.ILCT_CamPublishCollision(ctypes.byref(col))
        ray = ctypes.create_string_buffer(64)
        before = (u64 * 16)()
        self.d.ILCT_CamStats(before)
        for rsp in (0x10, 0xFFFF_FFFF_FFFF_FFF0):
            c = Ctx()
            c.index = self.w.twin.index['CM-02']
            c.rsp = rsp
            c.r8 = c.r9 = c.rdx = ctypes.addressof(ray)
            with self.subTest(rsp=hex(rsp)):
                self.assertEqual(self.d.ILCT_CamCall(2, ctypes.byref(c), EPOCH), RUN_ORIGINAL)
        after = (u64 * 16)()
        self.d.ILCT_CamStats(after)
        self.assertEqual(after[COUNTERS.index('queries')], before[COUNTERS.index('queries')], 'no query was made')


# ---- unicorn: the wrapper around the collision query and the CM-07 continuation ----------------------------------------
@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class CameraEmulationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = gt.world()
        cls.w.setup()
        cls.twin = cls.w.twin
        cls.d = declare(cls.twin.dll)
        cls.d.ILCT_PatchApplySubsystems(0xFF)
        cls.d.ILCT_CamReset()
        assert cls.d.ILCT_CamAttach() == 1
        cls.emu = iso.IsoEmulator(cls.twin)
        cls.row = cls.twin.index['CM-02']
        r = cls.twin.rows[cls.row]
        cls.site = TWIN_BASE + r['rva']
        cls.query = cls.site + 5 + struct.unpack_from('<i', r['expected'], 1)[0]
        assert cls.query == 0x230C00

    @classmethod
    def tearDownClass(cls):
        cls.twin.reset()

    def arm(self, gate=True, stamp_age=0, capsules=(), escape=0.0, flags=1):
        d, emu = self.d, self.emu
        p = PoseRecord(EPOCH, flags, 1, gt.NOW - stamp_age, (f32 * 3)(0, 0, 0), (f32 * 3)(0, 0, 0), 0.0, 3)
        d.ILCT_CamPublishPose(ctypes.byref(p))
        col = CollisionRecord(EPOCH, flags, 1, escape, len(capsules))
        for k, c in enumerate(capsules):
            col.capsules[k] = c
        d.ILCT_CamPublishCollision(ctypes.byref(col))
        emu.refresh()
        emu.tick_clock(gt.NOW)
        emu.force_gate(self.row, gate)
        emu.uc.mem_write(self.twin.addresses.liveness, struct.pack('<IIQI', 1, EPOCH, gt.NOW, 0))

    # Stack arguments 5 and 6 of the query as the game's caller leaves them: a word and a float, garbage above.
    ARG5 = 0x5A5A_5A5A_5A5A_0123
    ARG6 = 0xA5A5_A5A5_0000_0000 | struct.unpack('<I', struct.pack('<f', 2.5))[0]

    def run_query(self, hits):
        """Run CM-02's call with r8/r9 the ray (start (0,1.3,0), end (0,1.3,-20)); the emulated query answers
        `hits` in turn ((x, y, z) or None for no hit), or `hits(asked_start)` when it is callable. Every call, the
        game's and each of the wrapper's, must see the caller's stack arguments 5 (word, entry +0x28) and 6 (float,
        entry +0x30). Returns (rax, list of the start points it was asked from, the callees entered)."""
        uc = self.emu.uc
        buf = pe.SCRATCH + 0x1000
        start, end, hit = buf, buf + 0x20, buf + 0x40
        uc.mem_write(start, struct.pack('<4f', 0.0, 1.3, 0.0, 7.0))
        uc.mem_write(end, struct.pack('<4f', 0.0, 1.3, -20.0, 7.0))
        asked = []
        answers = hits if callable(hits) else list(hits)

        def on_callee(uc, address):
            if address != self.query:
                return
            r8 = uc.reg_read(pe.X.UC_X86_REG_R8)
            asked.append(struct.unpack('<4f', bytes(uc.mem_read(r8, 16))))
            rsp = uc.reg_read(pe.X.UC_X86_REG_RSP)  # at the entry: the return address
            arg5, arg6 = struct.unpack('<QQ', bytes(uc.mem_read(rsp + 0x28, 16)))
            self.assertEqual(arg5 & 0xFFFF, self.ARG5 & 0xFFFF, f'stack argument 5 at call {len(asked)}')
            self.assertEqual(struct.unpack('<f', struct.pack('<I', arg6 & 0xFFFFFFFF))[0], 2.5,
                             f'stack argument 6 at call {len(asked)}')
            if callable(answers):
                answer = answers(asked[-1])
            else:
                answer = answers.pop(0) if answers else None
            if answer is None:
                uc.reg_write(pe.X.UC_X86_REG_RAX, 0)
            else:
                uc.mem_write(uc.reg_read(pe.X.UC_X86_REG_RDX), struct.pack('<3f', *answer))
                uc.reg_write(pe.X.UC_X86_REG_RAX, 1)

        state = pe.random_state(random.Random(0xCA3))
        state['rflags'] &= ~0x400
        state['gpr'].update({'RDX': hit, 'R8': start, 'R9': end})
        state['gpr']['RSP'] &= ~0xF
        # before the call: arguments 5 and 6 at [rsp+0x20] and [rsp+0x28] (the call pushes the return address)
        uc.mem_write(state['gpr']['RSP'] + 0x20, struct.pack('<QQ', self.ARG5, self.ARG6))
        out = self.emu.run(self.site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE) or
                           self.site <= x < self.site + 5, state, on_callee=on_callee)
        self.assertEqual(out['exit'], self.site + 5)
        return out['gpr']['RAX'] & 0xFFFFFFFF, asked, out['calls']

    def corridor_capsule(self):
        c = Capsule()
        c.a[:] = (-6.0, 1.3, -5.0)
        c.b[:] = (6.0, 1.3, -5.0)
        c.hr2, c.vr2 = 4.0, 3.25 ** 2
        return c

    def test_disarmed_and_nothing_to_classify_are_the_games_single_call(self):
        self.arm(gate=False, capsules=(self.corridor_capsule(),))
        rax, asked, calls = self.run_query([(0.0, 1.3, -5.0)])
        self.assertEqual((rax, len(asked), calls), (1, 1, [self.query]))
        self.arm(gate=True, capsules=())
        rax, asked, _ = self.run_query([(0.0, 1.3, -5.0)])
        self.assertEqual((rax, len(asked)), (1, 1), 'no capsule, no escape: stock')

    def test_an_internal_hit_is_asked_again_from_just_past_it(self):
        self.arm(capsules=(self.corridor_capsule(),))
        rax, asked, calls = self.run_query([(0.0, 1.3, -5.0), (0.0, 1.3, -15.0)])
        self.assertEqual(rax, 1, 'the second hit (outside the corridor) stands')
        self.assertEqual(len(asked), 2)
        self.assertEqual(calls, [self.query, self.query])
        self.assertEqual(asked[0][:3], (0.0, struct.unpack('<f', struct.pack('<f', 1.3))[0], 0.0))
        retry_z = asked[1][2]
        self.assertAlmostEqual(retry_z, -(5.0 + 0.75), places=4, msg='from 0.75 past the internal hit')
        self.assertAlmostEqual(asked[1][3], 7.0, msg='the w component is kept')

    def test_an_internal_hit_with_nothing_behind_it_is_no_hit(self):
        self.arm(capsules=(self.corridor_capsule(),))
        rax, asked, _ = self.run_query([(0.0, 1.3, -5.0), None])
        self.assertEqual((rax, len(asked)), (0, 2))

    def test_an_external_hit_stands_and_a_stale_or_inactive_pose_is_stock(self):
        self.arm(capsules=(self.corridor_capsule(),))
        rax, asked, _ = self.run_query([(0.0, 1.3, -15.0)])
        self.assertEqual((rax, len(asked)), (1, 1))
        self.arm(capsules=(self.corridor_capsule(),), flags=0)
        rax, asked, _ = self.run_query([(0.0, 1.3, -5.0)])
        self.assertEqual((rax, len(asked)), (1, 1), 'inactive record: stock')

    def ray_capsule(self):
        c = Capsule()  # the whole ray inside the corridor: every hit on it is internal
        c.a[:] = (0.0, 1.3, 0.0)
        c.b[:] = (0.0, 1.3, -30.0)
        c.hr2, c.vr2 = 4.0, 3.25 ** 2
        return c

    def test_retries_are_exhausted_after_24_and_give_no_hit(self):
        """Every answer internal, each just past where it was asked: the first query plus 24 retries, then no hit."""
        self.arm(capsules=(self.ray_capsule(),))
        rax, asked, calls = self.run_query(lambda start: (0.0, 1.3, start[2] - 0.01))
        self.assertEqual((rax, len(asked), len(calls)), (0, 25, 25))
        zs = [a[2] for a in asked]
        self.assertTrue(all(b < a for a, b in zip(zs, zs[1:])), 'monotonic along the ray')
        self.assertTrue(all(a[3] == 7.0 for a in asked), 'w kept')
        self.assertGreater(zs[-1], -20.0 * 0.98, 'the 98% limit was not what stopped it')

    def test_a_retry_never_starts_past_98_percent_of_the_ray(self):
        """Ray length 20: an internal hit at 18.8 retries from 19.55 (97.75%); one at 19.0 would retry from 19.75
        (98.75%), so it is no hit and the query is not asked again."""
        self.arm(capsules=(self.ray_capsule(),))
        rax, asked, _ = self.run_query([(0.0, 1.3, -18.8), None])
        self.assertEqual((rax, len(asked)), (0, 2))
        self.assertAlmostEqual(asked[1][2], -19.55, places=4)
        rax, asked, _ = self.run_query([(0.0, 1.3, -18.8), (0.0, 1.3, -19.7)])
        self.assertEqual((rax, len(asked)), (0, 2), 'the second internal hit would start past 98%: no hit')
        rax, asked, _ = self.run_query([(0.0, 1.3, -19.0)])
        self.assertEqual((rax, len(asked)), (0, 1))

    def test_the_focus_escape_skips_hits_next_to_the_origin(self):
        self.arm(escape=5.30 ** 2)
        rax, asked, _ = self.run_query([(0.0, 1.3, -2.0), (0.0, 1.3, -9.0)])
        self.assertEqual((rax, len(asked)), (1, 2))
        self.assertAlmostEqual(asked[1][2], -2.75, places=4)

    def test_cm07_continuation_scales_xmm6_and_returns_after_the_mulss(self):
        row = self.twin.index['CM-07']
        site = TWIN_BASE + self.twin.rows[row]['rva']
        self.twin.poke(PRIMARY_RIG + 0x38, struct.pack('<f', 10.0))
        self.twin.poke(PRIMARY_RIG + 0x40, struct.pack('<I', 0))
        p = PoseRecord(EPOCH, 1, 1, gt.NOW, (f32 * 3)(0, 0, 0), (f32 * 3)(0, 0, 0), 4.0, 3)
        self.d.ILCT_CamPublishPose(ctypes.byref(p))
        emu = self.emu
        emu.refresh()
        emu.uc.mem_write(PRIMARY_RIG + 0x38, struct.pack('<f', 10.0))
        emu.uc.mem_write(PRIMARY_RIG + 0x40, struct.pack('<I', 0))
        emu.tick_clock(gt.NOW)
        emu.force_gate(row, True)
        emu.uc.mem_write(self.twin.addresses.liveness, struct.pack('<IIQI', 1, EPOCH, gt.NOW, 0))
        state = pe.random_state(random.Random(7))
        state['gpr']['RSI'] = PRIMARY_RIG
        state['gpr']['RSP'] &= ~0xF
        state['xmm'][6] = struct.unpack('<I', struct.pack('<f', 3.0))[0]
        def scaled(by):
            return (state['xmm'][6] & ~0xFFFFFFFF) | struct.unpack('<I', struct.pack('<f', 3.0 * by))[0]

        def check_rest(out, xmm2, label):
            """Every other GPR, the flags and XMM0-15 (except xmm6, and xmm2's low lane on the armed path) as the
            game left them."""
            for name, value in state['gpr'].items():
                self.assertEqual(out['gpr'][name], value, f'{label}: {name}')
            self.assertEqual(out['rflags'], state['rflags'] & pe.FLAG_MASK, f'{label}: flags')
            for k in range(16):
                if k == 6:
                    continue
                want = xmm2 if k == 2 else state['xmm'][k]
                self.assertEqual(out['xmm'][k], want, f'{label}: xmm{k}')

        out = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE) or site <= x < site + 5,
                      state)
        self.assertEqual(out['exit'], site + 5, 'continues right after the replaced mulss')
        self.assertEqual(out['xmm'][6], scaled(14.0), 'xmm6 low lane = 3 x (10 + 4), upper lanes kept')
        # xmm2 is dead here (reloaded at 0x32CD8B): it carries the boom in its low lane, the rest of it is kept
        check_rest(out, (state['xmm'][2] & ~0xFFFFFFFF) | struct.unpack('<I', struct.pack('<f', 14.0))[0], 'armed')
        emu.force_gate(row, False)
        out = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE) or site <= x < site + 5,
                      state)
        self.assertEqual(out['xmm'][6], scaled(10.0), 'disarmed: the game multiplies by its own boom')
        check_rest(out, state['xmm'][2], 'disarmed')


if __name__ == '__main__':
    unittest.main()
