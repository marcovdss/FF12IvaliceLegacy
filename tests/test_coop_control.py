"""Host tests for the co-op switches (native/coop/src/control): kill switch, seat drop-in/out, P1 lock, ordered
disarm. No game run, nothing deployed.

The module runs on the co-op thread before the session step; these tests drive it the same way (one control step,
then one session step per beat) against the session core on synthetic game memory, with a scripted keyboard:

* the kill switch needs a press edge whose previous sample was also in the foreground (no edge from a key held at
  start or across an alt-tab); the bridge's KILL command does the same;
* off is an ordered disarm: the same beat the session goes off (no guest steers, both characters are asked to follow
  the leader) and isolation releases; the mask drops INPUT_ISOLATION first, then everything, only once isolation's
  release has settled or 250 ms have passed; on again restores the session and hands the settings' mask to the next
  safe point;
* a settings record (a Mod Menu subsystem switch) that turns INPUT_ISOLATION off keeps it until the same release has
  run;
* F6/F7 (and a seat's TOGGLE_CONTROL press edge) drop a guest out on the same beat (its character is asked to follow,
  and the movement subsystem turns that into an OP_FOLLOW job request) and back in, where it claims its character
  by the session's rules (its preference; AUTO = the first free regular; never P1's);
* P1 is never a toggle target and never loses its seat or character through a toggle, the kill switch or a command.
"""
from __future__ import annotations

import ctypes
import unittest

from coop_native_support import SCHEMA as SC
from coop_session_support import CHAR_AUTO, CHAR_NONE, FX, GATE, SEATF, native_test
from test_coop_session import SessionCase

u32, u64 = ctypes.c_uint32, ctypes.c_uint64
ON, RELEASING, OFF = 0, 1, 2
VK_F6, VK_F7, VK_F8, VK_F9 = 0x75, 0x76, 0x77, 0x78
ISOLATION, ALL = 0x01, 0xFF
CTL_STATS = ['kill_presses', 'kills', 'revives', 'released_by_settle', 'released_by_cap', 'toggles',
             'toggles_ignored', 'isolation_drops', 'mask_busy', 'last_release_ms']
MOVE_STATS = ['steps', 'published', 'withdrawn', 'camera_reads', 'facts_failed', 'follow_pushed', 'follow_refused',
              'follow_stale', 'follow_applied', 'follow_rejected', 'list_rejected', 'approach_blocked',
              'vectors_written', 'root_motion_overridden', 'facings_set']


class IsoRecord(ctypes.Structure):
    _fields_ = [('epoch', u32), ('flags', u32), ('generation', u64), ('p1_slot', ctypes.c_uint8),
                ('kbm_seat', ctypes.c_uint8), ('guest_seats', ctypes.c_uint8), ('reserved0', ctypes.c_uint8),
                ('p1_vid', ctypes.c_uint16), ('p1_pid', ctypes.c_uint16), ('reserved1', u32)]


SIGNATURES = {
    'ILCT_CtlReset': ([], None), 'ILCT_CtlKey': ([u32, ctypes.c_int32], None),
    'ILCT_CtlFocus': ([ctypes.c_int32], None), 'ILCT_CtlKeyReads': ([], u32),
    'ILCT_CtlConfigure': ([ctypes.c_char_p], None), 'ILCT_CtlStep': ([u64], None),
    'ILCT_CtlKill': ([ctypes.c_int32], None), 'ILCT_CtlState': ([], u32), 'ILCT_CtlParked': ([], u32),
    'ILCT_CtlMaskForSafePoint': ([u32], u32), 'ILCT_CtlTakeReapply': ([ctypes.POINTER(u32)], ctypes.c_int32),
    'ILCT_CtlStats': ([ctypes.POINTER(u64), u32], None), 'ILCT_CtlMaskLog': ([ctypes.POINTER(u32), u32], u32),
    'ILCT_PatchApplySubsystems': ([u32], None), 'ILCT_PatchSubsystems': ([], u32),
    'ILCT_IsoReset': ([], None), 'ILCT_IsoReleaseSettled': ([], ctypes.c_int32),
    'ILCT_IsoCompose': ([u32, u64, ctypes.c_char_p, ctypes.POINTER(IsoRecord)], None),
    'ILCT_MoveReset': ([], None), 'ILCT_MoveStart': ([u64, u64], None), 'ILCT_MoveStep': ([u64], None),
    'ILCT_MoveStats': ([ctypes.POINTER(u64 * len(MOVE_STATS))], None), 'ILCT_MoveStop': ([], None),
}


def settings(kbm: int = 1, p1_toggle: int = 0, p2_toggle: int = VK_F6) -> bytes:
    record = bytearray(SC.defaults())
    record[SC.locate('ilc_settings', ['kbm_seat'])[0]] = kbm
    toggle = SC.e('action', 'TOGGLE_CONTROL')
    if p1_toggle:  # whatever a record says about P1, the module never reads a P1 toggle
        at = SC.locate('ilc_settings', ['seats', 0, 'bindings', toggle * 4 + 3])[0]
        record[at] = SC.e('binding_kind', 'KEY')
        record[at + 1] = p1_toggle
    at = SC.locate('ilc_settings', ['seats', 1, 'bindings', toggle * 4])[0]
    record[at + 1] = p2_toggle
    return bytes(record)


@native_test
class ControlCase(SessionCase):
    def setUp(self):
        super().setUp()
        for name, (args, res) in SIGNATURES.items():
            fn = getattr(self.d, name)
            fn.argtypes = args
            fn.restype = res
        self.d.ILCT_CtlReset()
        self.d.ILCT_IsoReset()
        self.d.ILCT_PatchApplySubsystems(ALL)
        self.d.ILCT_CtlConfigure(settings())

    def tearDown(self):
        self.d.ILCT_CtlReset()
        self.d.ILCT_IsoReset()
        super().tearDown()

    # one beat: control first, then the session (the boot loop's order)
    def beat(self, n=1, dt=20):
        for _ in range(n):
            self.now += dt
            self.d.ILCT_CtlStep(self.now)
            self.d.ILCT_SesStep(self.now)
        return self.record()

    def ctl_only(self, dt=20):
        self.now += dt
        self.d.ILCT_CtlStep(self.now)

    def press(self, vk):
        self.d.ILCT_CtlKey(vk, 1)
        rec = self.beat()
        self.d.ILCT_CtlKey(vk, 0)
        return rec

    def stats(self) -> dict:
        out = (u64 * len(CTL_STATS))()
        self.d.ILCT_CtlStats(out, len(CTL_STATS))
        return dict(zip(CTL_STATS, out))

    def masks(self) -> list:
        out = (u32 * 16)()
        n = self.d.ILCT_CtlMaskLog(out, 16)
        return list(out[:n])

    def compose_release(self):
        """What isolation's publish does each beat (the session epoch exists): builds the release record."""
        r = IsoRecord()
        self.d.ILCT_IsoCompose(1, 1, None, ctypes.byref(r))
        return r


class KillSwitchTest(ControlCase):
    def test_the_kill_key_needs_a_trusted_foreground_edge(self):
        self.d.ILCT_CtlKey(VK_F8, 1)  # held when the module starts: never a toggle
        self.ctl_only()
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlState(), ON)
        self.d.ILCT_CtlKey(VK_F8, 0)
        self.d.ILCT_CtlFocus(0)       # pressed in another window
        self.ctl_only()
        self.d.ILCT_CtlKey(VK_F8, 1)
        self.ctl_only()
        self.d.ILCT_CtlFocus(1)       # focus comes back with the key still held: no edge either
        self.ctl_only()
        self.ctl_only()
        self.assertEqual((self.d.ILCT_CtlState(), self.stats()['kill_presses']), (ON, 0))
        self.d.ILCT_CtlKey(VK_F8, 0)
        self.ctl_only()
        self.d.ILCT_CtlKey(VK_F8, 1)
        self.ctl_only()
        self.assertEqual((self.d.ILCT_CtlState(), self.stats()['kill_presses']), (RELEASING, 1))

    def test_off_is_an_ordered_disarm_once_isolation_has_released(self):
        self.ctl_only()
        self.d.ILCT_CtlKill(1)  # the Mod Menu action (bridge KILL command)
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlState(), RELEASING)
        self.assertEqual((self.masks(), self.d.ILCT_PatchSubsystems()), ([], ALL), 'nothing disarmed before release')
        self.assertEqual(self.compose_release().flags, 0x20, 'isolation publishes the release record (RELEASE only)')
        self.assertEqual(self.d.ILCT_IsoReleaseSettled(), 1, 'no host: settled once the record is out')
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlState(), OFF)
        self.assertEqual(self.masks(), [ALL & ~ISOLATION, 0], 'INPUT_ISOLATION first, then everything')
        self.assertEqual(self.d.ILCT_PatchSubsystems(), 0)
        s = self.stats()
        self.assertEqual((s['kills'], s['released_by_settle'], s['released_by_cap']), (1, 1, 0))
        self.assertEqual(self.d.ILCT_IsoReleaseSettled(), 0, 'release ended with the disarm')

    def test_the_bridge_kill_command_runs_the_same_sequence(self):
        from coop_native_support import Bridge
        bridge = Bridge(self.d)
        record = bytes(SC.encode('ilc_command', {'header': SC.header('COMMAND', SC.size('ilc_command'), 9),
                                                 'command': SC.e('command', 'KILL_SWITCH'), 'seat': 0, 'arg0': 1,
                                                 'arg1': 0}))
        self.assertEqual(bridge.call(SC.enums['op']['COMMAND'], record)[0], 0)
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlState(), RELEASING)
        self.now += 300
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlState(), OFF)
        back = bytearray(record)
        back[SC.locate('ilc_command', ['arg0'])[0]] = 0
        self.assertEqual(bridge.call(SC.enums['op']['COMMAND'], bytes(back))[0], 0)
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlState(), ON)

    def test_the_release_wait_is_capped_at_250_ms(self):
        self.ctl_only()
        self.d.ILCT_CtlKill(1)
        self.ctl_only(dt=1)
        t0 = self.now
        for t in (100, 200, 249):
            self.now = t0 + t - 1
            self.ctl_only(dt=1)
            self.assertEqual(self.d.ILCT_CtlState(), RELEASING, f'+{t} ms')
            self.assertEqual(self.masks(), [])
        self.now = t0 + 249
        self.ctl_only(dt=1)
        self.assertEqual(self.d.ILCT_CtlState(), OFF, 'the cap is 250 ms')
        s = self.stats()
        self.assertEqual((s['released_by_cap'], s['released_by_settle'], s['last_release_ms']), (1, 0, 250))
        self.assertEqual(self.masks(), [ALL & ~ISOLATION, 0])

    def test_safe_points_while_off_and_the_revive(self):
        self.assertEqual(self.d.ILCT_CtlMaskForSafePoint(0xF7), 0xF7)
        self.d.ILCT_CtlKill(1)
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlMaskForSafePoint(0x7F), ALL, 'releasing: the sequence owns the mask')
        self.now += 300
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlState(), OFF)
        self.assertEqual(self.d.ILCT_CtlMaskForSafePoint(0x3F), 0, 'off: nothing armed; the record is remembered')
        mask = u32()
        self.assertEqual(self.d.ILCT_CtlTakeReapply(ctypes.byref(mask)), 0)
        self.d.ILCT_CtlKey(VK_F8, 1)
        self.ctl_only()
        self.d.ILCT_CtlKey(VK_F8, 0)
        self.assertEqual(self.d.ILCT_CtlState(), ON)
        self.assertEqual((self.d.ILCT_CtlTakeReapply(ctypes.byref(mask)), mask.value), (1, 0x3F),
                         'on: the latest settings mask goes to the next safe point')
        self.assertEqual(self.d.ILCT_CtlTakeReapply(ctypes.byref(mask)), 0, 'once')
        self.assertEqual(self.d.ILCT_CtlMaskForSafePoint(0x3F), 0x3F)
        self.assertEqual(self.stats()['revives'], 1)

    def test_a_settings_record_that_drops_isolation_releases_first(self):
        self.assertEqual(self.d.ILCT_CtlMaskForSafePoint(ALL & ~ISOLATION), ALL, 'the bit stays for now')
        self.ctl_only()
        self.assertEqual(self.masks(), [])
        self.compose_release()
        self.ctl_only()
        self.assertEqual(self.masks(), [ALL & ~ISOLATION])
        self.assertEqual(self.d.ILCT_PatchSubsystems(), ALL & ~ISOLATION)
        self.assertEqual(self.stats()['isolation_drops'], 1)
        self.assertEqual(self.d.ILCT_CtlState(), ON, 'a subsystem switch is not the kill switch')
        # Turning it back on needs no release.
        self.assertEqual(self.d.ILCT_CtlMaskForSafePoint(ALL), ALL)
        # A record that drops it and a later one that wants it back: nothing is dropped.
        self.d.ILCT_PatchApplySubsystems(ALL)
        self.d.ILCT_CtlMaskForSafePoint(ALL & ~ISOLATION)
        self.d.ILCT_CtlMaskForSafePoint(ALL)
        self.now += 300
        self.ctl_only()
        self.ctl_only()
        self.assertEqual(self.masks(), [ALL & ~ISOLATION], 'no second drop')

    def test_off_makes_every_guest_stock_on_the_same_beat_and_on_brings_them_back(self):
        self.field()
        self.start()
        rec = self.beat(2)
        self.assertEqual(self.steering(rec), [1, 2])
        epoch = rec.head.epoch
        self.effects()
        rec = self.press(VK_F8)
        self.assertEqual(self.steering(rec), [], 'off: no guest steers in the very beat of the press')
        self.assert_gates(rec, off=('LOGICAL', 'GUESTS_MOVE'))
        self.assertEqual(sorted(m for _, k, m in self.effects() if k == FX['ASK_FOLLOW']), [1, 5],
                         'both characters are asked to follow the leader')
        self.assertTrue(rec.seat[0].flags & SEATF['IN_SESSION'])
        self.assertEqual((rec.seat[0].member, rec.head.epoch), (0, epoch), 'P1 keeps its seat; not a world change')
        self.now += 300
        self.beat()
        self.assertEqual(self.d.ILCT_CtlState(), OFF)
        self.press(VK_F8)
        rec = self.beat(2)
        self.assertEqual(self.steering(rec), [1, 2], 'on again: both guests steer their characters')
        self.assertEqual([rec.seat[s].member for s in range(3)], [0, 5, 1])


class SeatToggleTest(ControlCase):
    def test_f6_drops_p2_out_on_the_same_beat_and_its_character_is_asked_to_follow(self):
        self.field()
        self.start()
        self.d.ILCT_MoveReset()
        self.d.ILCT_MoveStart(self.img.base, self.now)
        rec = self.beat(2)
        epoch = rec.head.epoch
        self.effects()
        before = (u64 * len(MOVE_STATS))()
        self.d.ILCT_MoveStats(ctypes.byref(before))
        rec = self.press(VK_F6)
        self.assertEqual(self.steering(rec), [2], 'P2 is parked in the very beat of the press')
        self.assertFalse(rec.seat[1].flags & SEATF['MANUAL'])
        self.assertTrue(rec.seat[1].flags & SEATF['IN_SESSION'], 'dropping out keeps the seat, only the driver goes')
        self.assertEqual(self.d.ILCT_CtlParked(), 0b010)
        # The movement subsystem drains the session's follow request into an OP_FOLLOW job for P2's character.
        self.now += 1
        self.d.ILCT_MoveStep(self.now)
        after = (u64 * len(MOVE_STATS))()
        self.d.ILCT_MoveStats(ctypes.byref(after))
        tried = lambda c: c[MOVE_STATS.index('follow_pushed')] + c[MOVE_STATS.index('follow_refused')]  # noqa: E731
        self.assertEqual(tried(after) - tried(before), 1, 'one OP_FOLLOW request for the released character')
        self.assertEqual(after[MOVE_STATS.index('follow_stale')], before[MOVE_STATS.index('follow_stale')])
        self.d.ILCT_MoveStop()
        # F7 for P3, and F6 again brings P2 back to its own character.
        rec = self.press(VK_F7)
        self.assertEqual((self.steering(rec), self.d.ILCT_CtlParked()), ([], 0b110))
        rec = self.press(VK_F6)
        rec = self.beat(2)
        self.assertEqual(self.steering(rec), [1])
        self.assertEqual(rec.seat[1].member, 5, 'P2 claims its preferred character again')
        self.assertEqual(self.stats()['toggles'], 3)
        self.assertEqual(epoch, rec.head.epoch)

    def test_drop_in_claims_by_the_picker_rules(self):
        self.field(members=(3, 2, 4))
        self.start(characters=(CHAR_AUTO, CHAR_AUTO, CHAR_AUTO, CHAR_NONE))
        rec = self.beat(14)
        self.assertEqual([rec.seat[s].member for s in range(3)], [3, 2, 4])
        self.press(VK_F6)
        rec = self.beat(2)
        self.assertEqual(self.steering(rec), [2])
        rec = self.press(VK_F6)
        rec = self.beat(14)
        self.assertEqual(self.steering(rec), [1, 2])
        members = [rec.seat[s].member for s in range(3)]
        self.assertEqual(members[1], 2, 'AUTO: the first free regular of the party, never P1s or P3s')
        self.assertNotIn(members[1], (members[0], members[2]))

    def test_a_pad_toggle_edge_is_ignored_while_co_op_is_off(self):
        self.field()
        self.start()
        self.beat(2)
        self.d.ILCT_CtlKill(1)
        self.beat()
        self.now += 300
        self.beat()
        self.assertEqual(self.d.ILCT_CtlState(), OFF)
        self.press(VK_F6)
        self.assertEqual((self.d.ILCT_CtlParked(), self.stats()['toggles_ignored']), (0, 1))

    def test_a_guest_that_owns_the_keyboard_toggles_through_its_own_bindings(self):
        self.d.ILCT_CtlConfigure(settings(kbm=2))
        reads = self.d.ILCT_CtlKeyReads()
        self.ctl_only()
        self.d.ILCT_CtlKey(VK_F6, 1)
        self.ctl_only()
        self.assertEqual(self.d.ILCT_CtlParked(), 0, 'P2 owns the keyboard: F6 arrives as its action edge instead')
        self.assertGreater(self.d.ILCT_CtlKeyReads(), reads, 'the kill switch and P3 toggle keys are still read')


class P1LockTest(ControlCase):
    def test_p1_is_never_reassigned(self):
        self.d.ILCT_CtlConfigure(settings(p1_toggle=VK_F9))
        self.field()
        self.start()
        self.beat(2)
        for vk in (VK_F9, VK_F6, VK_F7, VK_F6, VK_F7, VK_F8):
            rec = self.press(vk)
            self.assertEqual(self.d.ILCT_CtlParked() & 1, 0, 'P1 park bit')
            self.assertTrue(rec.seat[0].flags & SEATF['IN_SESSION'])
            self.assertEqual(rec.seat[0].member, 0, 'P1 keeps its character')
        self.assertEqual(self.stats()['toggles'], 4, "a P1 toggle binding is never read (F9 did nothing)")
        self.now += 300
        rec = self.beat()
        self.assertEqual(self.d.ILCT_CtlState(), OFF)
        self.assertEqual((rec.seat[0].member, bool(rec.seat[0].flags & SEATF['IN_SESSION'])), (0, True))
        rec = self.press(VK_F8)
        rec = self.beat(2)
        self.assertEqual((rec.seat[0].member, self.d.ILCT_CtlParked() & 1), (0, 0))


if __name__ == '__main__':
    unittest.main()
