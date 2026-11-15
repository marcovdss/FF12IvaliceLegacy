"""Host tests for the co-op battle menus at run time (native/coop/src/frontend). No game run, nothing deployed; the
exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin the patch layer installs on.

* the input bindings (pure): guests open with BATTLE_MENU and keep CONFIRM for the world, P1 opens with CONFIRM
  unless A goes to a highlighted prompt; cursor and page movement with repeat and opposite pairs; LB/RB (and
  left/right) cycle target groups; CONFIRM/CANCEL; BATTLE_MENU closes; PARTY with the menu closed;
* external frontends (pure): adopted after 0.04 s, resumed 0.20 s after they close (a modal or a loss of control
  restarts that wait), aborted by a load, a cutscene or a role change; a Party request defers adoption to itself;
* the native Party request (pure): Wait's release first, then one pulse, acknowledged only by a native screen after
  the pulse; cancelled by the pause screen, another screen opening first, a modal, a session gate and the 1.25 s
  timeout; a stock command tree drains first;
* the party-down recovery latch (pure): all native while down, until the recovery's screen has closed for 0.20 s
  (2 s when no screen ever showed);
* the layer with doubles of its game readers: P1's battle menu is a co-op menu (A opens it, the report carries
  p1_menus and the firewall's gate arms; it expires two beats after the layer stops posting and drops at once when
  the menus' subsystem may not act); a guest opens, navigates, targets with LB/RB and submits through the request
  slot; a guest's CONFIRM inside its open menu is not an interaction; external frontends suspend and resume the
  menus where they were; a load aborts; the Party request pulses PARTY from the requesting seat through the
  firewall's scheduler (after Wait gives its pause back) and times out cleanly; all regulars down gives the stock
  recovery the battle UI; the Wait request follows every participant's menu (benched and dead seats excluded);
  a character change and a new epoch close the menus.
"""
from __future__ import annotations

import ctypes
import struct
import unittest

import test_coop_firewall as tf
import test_coop_game_thread as gt
import test_coop_interaction as ti
import test_coop_menu as tm
import test_coop_submit as ts
import test_coop_targets as tg
import test_coop_wait as tw8
from coop_native_support import native_test
from coop_session_support import FACT, GATE, PHASE, SEATF, Image, Record
from test_coop_patch_engine import GAME

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


SeatEdges = S('SeatEdges', [('pressed', u32), ('repeated', u32), ('held', u32)])
Suspension = S('Suspension', [('suspended', u8), ('source', u8), ('observed_open', u8), ('reserved', u8),
                              ('roles', u32), ('since', u64), ('open_since', u64), ('close_since', u64)])
ScreenIn = S('ScreenIn', [('native_open', u8), ('modal', u8), ('control', u8), ('settling', u8),
                          ('party_transfer', u8), ('reserved', u8 * 3), ('roles', u32), ('reserved2', u32)])
PartyRequest = S('PartyRequest', [('state', u8), ('seat', u8), ('drain', u8), ('last', u8), ('reserved', u32),
                                  ('started', u64), ('deadline', u64)])
PartyIn = S('PartyIn', [('native_open', u8), ('paused', u8), ('modal', u8), ('gate_ok', u8), ('tree_active', u8),
                        ('wait_owned', u8), ('reserved', u8 * 2)])
PartyOut = S('PartyOut', [('result', u8), ('pulse', u8), ('reserved', u8 * 2)])
Recovery = S('Recovery', [('latched', u8), ('observed', u8), ('reserved', u8 * 6), ('up_since', u64),
                          ('close_since', u64)])
FeSeat = S('FeSeat', [('mode', u8), ('eligible', u8), ('member_known', u8), ('group', u8), ('member', u16),
                      ('target_index', u16), ('target_count', u16), ('page', u16), ('cursor', u16),
                      ('selector_kind', u8), ('selector_index', u8), ('last_commands', u32), ('last_submit', i32),
                      ('opens', u32), ('submits', u32), ('reopen_at', u64), ('guard_until', u64)])
FeState = S('FeState', [('owns_p1', u8), ('recovery', u8), ('tables_ready', u8), ('wait_requested', u8),
                        ('party_state', u8), ('party_seat', u8), ('party_last', u8), ('interaction_mask', u8),
                        ('susp', Suspension), ('epoch', u32), ('roles', u32), ('tables_status', i32),
                        ('presenter_live', u8), ('p1_ko', u8), ('ko_live', u8), ('quickening', u8),
                        ('report', tf.MenuReport)])
FE_COUNTERS = ('steps', 'opens', 'closes', 'hard_closes', 'submits', 'suspended', 'resumed', 'aborted',
               'party_requests', 'party_opened', 'party_cancelled', 'party_pulses', 'recoveries', 'table_reads',
               'table_failures', 'seat_refreshes', 'picks', 'selector_opens', 'selector_choices', 'selector_requests',
               'ko_opens', 'p1_givebacks')
FeCounters = S('FeCounters', [(n, u64) for n in FE_COUNTERS])
SelMember = S('SelMember', [('member', u16), ('able', u8), ('holder', u8)])
SelIn = S('SelIn', [('slot', SelMember * 4), ('own', u16), ('own_able', u8), ('seat', u8)])
Selector = S('Selector', [('kind', u8), ('count', u8), ('index', u8), ('reserved', u8), ('member', u16 * 4)])
LAYOUTS = [SeatEdges, Suspension, ScreenIn, PartyRequest, PartyIn, PartyOut, Recovery, FeSeat, FeState, FeCounters,
           SelMember, SelIn, Selector]

# ilc_action bits
A_BATTLE, A_UP, A_DOWN, A_LEFT, A_RIGHT, A_PREV, A_NEXT = 7, 8, 9, 10, 11, 12, 13
A_CONFIRM, A_CANCEL, A_PARTY, A_PAUSE = 15, 16, 17, 18
CMD = {'OPEN': 0x1, 'CLOSE': 0x2, 'UP': 0x4, 'DOWN': 0x8, 'PAGE_UP': 0x10, 'PAGE_DOWN': 0x20, 'GROUP_PREV': 0x40,
       'GROUP_NEXT': 0x80, 'CONFIRM': 0x100, 'BACK': 0x200, 'PARTY': 0x400, 'SELECT': 0x800}
SEL_NONE, SEL_LEADER, SEL_CHARACTER, SEL_KO = 0, 1, 2, 3
SELE_NONE, SELE_MOVED, SELE_CHOSEN, SELE_CLOSED = 0, 1, 2, 3
NO_MEMBER, NO_SEAT = 0xFFFF, 0xFF
ST_KO, ST_STONE = 0x1, 0x2
LEADER_ID = 3
CLOSED, LIST, TARGET = 0, 1, 2
SE_NONE, SE_SUSPENDED, SE_RESUMED, SE_ABORTED = 0, 1, 2, 3
SRC_NONE, SRC_EXTERNAL, SRC_PARTY = 0, 1, 2
PR_IDLE, PR_PENDING, PR_PULSED = 0, 1, 2
PRR_NONE, PRR_OPENED, PRR_PAUSE, PRR_UNEXPECTED, PRR_MODAL, PRR_GATE, PRR_TIMEOUT = range(7)
ADOPT_MS, RESUME_MS, PARTY_TIMEOUT_MS, RECOVERY_QUIET_MS = 40, 200, 1250, 2000
REOPEN_QUIET_MS = 80
LIVE_MS = 250   # presenter.h
P1_GIVEBACK_MS = 4 * LIVE_MS   # frontend.h: P1's stock frontends after its own window was left out
SCREEN_PAUSED, SCREEN_TREE, SCREEN_QUICKENING = 0x1, 0x2, 0x4
SEM_PARTY = 0x0E
ATTACK = 0x96
TG_FOES, TG_NEUTRALS, TG_PARTY = 0, 1, 2
EPOCH, GEN, NOW = gt.EPOCH, gt.GEN, gt.NOW
SEAT_FULL = (SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['ACTOR'] | SEATF['ALIVE'] | SEATF['ALIVE_KNOWN'] |
             SEATF['MENU'])

SIGNATURES = {
    'ILCT_FeSizes': ([P(u32), u32], u32), 'ILCT_FeCommands': ([u32, u32, u32, P(SeatEdges)], u32),
    'ILCT_FeSuspBegin': ([P(Suspension), u32, u32, u64], None),
    'ILCT_FeSuspStep': ([P(Suspension), P(ScreenIn), u64], i32),
    'ILCT_FePartyBegin': ([P(PartyRequest), u32, u64], i32),
    'ILCT_FePartyStep': ([P(PartyRequest), P(PartyIn), u64, P(PartyOut)], None),
    'ILCT_FeRecoveryStep': ([P(Recovery), u32, u32, u64], i32), 'ILCT_FeRoles': ([P(Record)], u32),
    'ILCT_FeReset': ([], None), 'ILCT_FeUseDoubles': ([u32], None), 'ILCT_FeSetImageBase': ([u64], None),
    'ILCT_FeTablesFromMenu': ([i32], None), 'ILCT_FeSetShared': ([P(tm.Shared), i32], None),
    'ILCT_FeSetSeatState': ([u32, P(tm.Seat), i32], None),
    'ILCT_FeSetPicker': ([u32, P(tg.PickerInput), i32], None), 'ILCT_FeSetScreen': ([u32], None),
    'ILCT_FeCalls': ([P(u32)], None), 'ILCT_FeStart': ([u64, u64], None), 'ILCT_FeStep': ([u64], None),
    'ILCT_FeStop': ([], None), 'ILCT_FeSeat': ([u32, P(FeSeat)], None), 'ILCT_FeState': ([P(FeState)], None),
    'ILCT_FeCandidates': ([u32, P(tg.CandidateList)], None), 'ILCT_FeStats': ([P(FeCounters)], None),
    'ILCT_IxSetMenuSeats': ([u32, u32], None), 'ILCT_IxMenuSeats': ([u32], u32),
    'ILCT_FeSelectorBuild': ([P(Selector), u32, P(SelIn)], u32),
    'ILCT_FeSelectorCommand': ([P(Selector), u32, P(u32)], i32),
    'ILCT_FePresenter': ([i32], None), 'ILCT_FeNoteDrawn': ([u64], None), 'ILCT_FePresenterLive': ([u64], i32),
    'ILCT_FePresenterReset': ([], None), 'ILCT_FeSelector': ([u32, P(Selector)], None),
    'ILCT_FeNoteDropped': ([u32, u64], None), 'ILCT_FeSeatShown': ([u32], i32), 'ILCT_FeSeatLost': ([u32, u64], i32),
    'ILCT_PartyPeekRequest': ([u32, P(u32), P(u32)], None),
}


def declare(d):
    ts.declare(d)
    ti.declare(d)
    tw8.declare(d)
    tf.declare(d)
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def edges(pressed=(), repeated=(), held=()) -> SeatEdges:
    e = SeatEdges()
    e.pressed = sum(1 << a for a in pressed)
    e.repeated = sum(1 << a for a in repeated)
    e.held = sum(1 << a for a in held) | e.pressed
    return e


# ---- pure rules -----------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class PureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(ts.submit_world().d)


class LayoutTest(PureCase):
    def test_struct_mirrors(self):
        out = (u32 * 16)()
        self.assertEqual(self.d.ILCT_FeSizes(out, 16), len(LAYOUTS))
        for k, cls in enumerate(LAYOUTS):
            self.assertEqual(out[k], ctypes.sizeof(cls), cls.__name__)


class BindingTest(PureCase):
    def cmds(self, player1, mode, prompt=False, **kw):
        return self.d.ILCT_FeCommands(int(player1), mode, int(prompt), ctypes.byref(edges(**kw)))

    def test_opening(self):
        self.assertEqual(self.cmds(False, CLOSED, pressed=[A_BATTLE]), CMD['OPEN'], 'a guest opens with X')
        self.assertEqual(self.cmds(False, CLOSED, pressed=[A_CONFIRM]), 0, 'a guest\'s A stays a world interaction')
        self.assertEqual(self.cmds(True, CLOSED, pressed=[A_CONFIRM]), CMD['OPEN'], 'P1 opens with A')
        self.assertEqual(self.cmds(True, CLOSED, True, pressed=[A_CONFIRM]), 0, 'A on a highlighted prompt interacts')
        self.assertEqual(self.cmds(True, CLOSED, True, pressed=[A_BATTLE]), CMD['OPEN'], 'a bound X still opens')
        self.assertEqual(self.cmds(False, CLOSED, pressed=[A_PARTY]), CMD['PARTY'])
        self.assertEqual(self.cmds(False, CLOSED, pressed=[A_CANCEL, A_PREV]), 0, 'nothing else while closed')

    def test_up_or_down_alone_opens_the_character_selector(self):
        for p1 in (False, True):
            self.assertEqual(self.cmds(p1, CLOSED, pressed=[A_UP]), CMD['SELECT'])
            self.assertEqual(self.cmds(p1, CLOSED, pressed=[A_DOWN]), CMD['SELECT'])
            self.assertEqual(self.cmds(p1, CLOSED, pressed=[A_UP, A_DOWN]), 0, 'both: neither')
            self.assertEqual(self.cmds(p1, CLOSED, pressed=[A_UP], held=[A_DOWN]), 0, 'the other one held')
            self.assertEqual(self.cmds(p1, CLOSED, repeated=[A_UP], held=[A_UP]), 0, 'a repeat never opens')
            self.assertEqual(self.cmds(p1, LIST, pressed=[A_UP]), CMD['UP'], 'open: a step as before')

    def test_list_movement(self):
        g = False
        self.assertEqual(self.cmds(g, LIST, pressed=[A_DOWN]), CMD['DOWN'])
        self.assertEqual(self.cmds(g, LIST, repeated=[A_UP], held=[A_UP]), CMD['UP'], 'a held direction repeats')
        self.assertEqual(self.cmds(g, LIST, pressed=[A_UP], held=[A_DOWN]), 0, 'an opposite pair cancels')
        self.assertEqual(self.cmds(g, LIST, pressed=[A_PREV]), CMD['PAGE_UP'], 'a guest pages with LB')
        self.assertEqual(self.cmds(g, LIST, pressed=[A_RIGHT]), CMD['PAGE_DOWN'])
        self.assertEqual(self.cmds(g, LIST, pressed=[A_PREV, A_NEXT, A_DOWN]), CMD['DOWN'], 'both pages: none')
        self.assertEqual(self.cmds(g, LIST, pressed=[A_NEXT, A_DOWN]), CMD['PAGE_DOWN'], 'a page jump wins')
        self.assertEqual(self.cmds(True, LIST, pressed=[A_LEFT]), CMD['PAGE_UP'], 'P1 pages with left (and LB)')
        self.assertEqual(self.cmds(True, LIST, pressed=[A_PREV]), 0, 'P1 has no TARGET_PREV page binding')
        self.assertEqual(self.cmds(g, LIST, pressed=[A_CONFIRM, A_CANCEL]), CMD['CONFIRM'] | CMD['BACK'])
        self.assertEqual(self.cmds(g, LIST, pressed=[A_BATTLE, A_CONFIRM, A_DOWN]), CMD['CLOSE'], 'X closes, alone')

    def test_target_groups(self):
        for p1 in (False, True):
            self.assertEqual(self.cmds(p1, TARGET, pressed=[A_PREV]), CMD['GROUP_PREV'], 'LB')
            self.assertEqual(self.cmds(p1, TARGET, pressed=[A_NEXT]), CMD['GROUP_NEXT'], 'RB')
            self.assertEqual(self.cmds(p1, TARGET, pressed=[A_LEFT]), CMD['GROUP_PREV'])
            self.assertEqual(self.cmds(p1, TARGET, pressed=[A_RIGHT]), CMD['GROUP_NEXT'])
            self.assertEqual(self.cmds(p1, TARGET, pressed=[A_PREV, A_NEXT]), 0)
            self.assertEqual(self.cmds(p1, TARGET, pressed=[A_PREV, A_RIGHT]), CMD['GROUP_PREV'], 'LB/RB first')
            self.assertEqual(self.cmds(p1, TARGET, pressed=[A_DOWN, A_CONFIRM]), CMD['DOWN'] | CMD['CONFIRM'])


class SuspensionTest(PureCase):
    def step(self, s, now, **kw):
        si = ScreenIn()
        si.control, si.roles = 1, 7
        for k, v in kw.items():
            setattr(si, k, v)
        return self.d.ILCT_FeSuspStep(ctypes.byref(s), ctypes.byref(si), now)

    def test_adopt_after_40_ms_and_resume_200_ms_after_close(self):
        s, t = Suspension(), NOW
        self.assertEqual(self.step(s, t, native_open=1), SE_NONE)
        self.assertEqual(self.step(s, t + ADOPT_MS - 1, native_open=1), SE_NONE)
        self.assertEqual(self.step(s, t + ADOPT_MS, native_open=1), SE_SUSPENDED)
        self.assertEqual((s.suspended, s.source, s.observed_open), (1, SRC_EXTERNAL, 1))
        t += 500
        self.assertEqual(self.step(s, t), SE_NONE, 'closed: the wait starts')
        self.assertEqual(self.step(s, t + RESUME_MS - 1), SE_NONE)
        self.assertEqual(self.step(s, t + RESUME_MS), SE_RESUMED)
        self.assertEqual(s.suspended, 0)

    def test_a_flash_is_never_adopted(self):
        s = Suspension()
        self.step(s, NOW, native_open=1)
        self.assertEqual(self.step(s, NOW + 20), SE_NONE, 'closed before 40 ms')
        self.assertEqual(self.step(s, NOW + 30, native_open=1), SE_NONE, 'a new open starts its own 40 ms')
        self.assertEqual(self.step(s, NOW + 60, native_open=1), SE_NONE)
        self.assertEqual(self.step(s, NOW + 70, native_open=1), SE_SUSPENDED)

    def test_a_modal_or_no_control_restarts_the_resume_wait(self):
        s = Suspension()
        self.d.ILCT_FeSuspBegin(ctypes.byref(s), SRC_EXTERNAL, 7, NOW)
        s.observed_open = 1
        t = NOW + 100
        self.step(s, t)
        self.assertEqual(self.step(s, t + 150, modal=1), SE_NONE)
        self.assertEqual(self.step(s, t + 300, control=0), SE_NONE)
        self.assertEqual(self.step(s, t + 310), SE_NONE, 'the wait starts again')
        self.assertEqual(self.step(s, t + 310 + RESUME_MS - 1), SE_NONE)
        self.assertEqual(self.step(s, t + 310 + RESUME_MS), SE_RESUMED)

    def test_aborts(self):
        for label, kw in (('load or cutscene', dict(settling=1)), ('role change', dict(roles=8))):
            s = Suspension()
            self.d.ILCT_FeSuspBegin(ctypes.byref(s), SRC_EXTERNAL, 7, NOW)
            with self.subTest(label):
                self.assertEqual(self.step(s, NOW + 10, native_open=1, **kw), SE_ABORTED)
                self.assertEqual(s.suspended, 0)

    def test_a_party_request_owns_its_screen(self):
        s = Suspension()
        self.d.ILCT_FeSuspBegin(ctypes.byref(s), SRC_PARTY, 7, NOW)
        self.assertEqual(self.step(s, NOW + 100, native_open=1, party_transfer=1), SE_NONE)
        self.assertEqual(s.observed_open, 0, 'only the request\'s acknowledgement marks it seen')
        self.assertEqual(self.step(s, NOW + 500, party_transfer=1), SE_NONE, 'never resumed while in flight')
        self.assertEqual(self.step(s, NOW + 900), SE_NONE, 'never seen open: nothing to resume from')


class PartyRequestTest(PureCase):
    def step(self, p, now, **kw) -> PartyOut:
        pi = PartyIn()
        pi.gate_ok = 1
        for k, v in kw.items():
            setattr(pi, k, v)
        out = PartyOut()
        self.d.ILCT_FePartyStep(ctypes.byref(p), ctypes.byref(pi), now, ctypes.byref(out))
        return out

    def test_wait_releases_first_then_one_pulse_then_the_screen(self):
        p = PartyRequest()
        self.assertEqual(self.d.ILCT_FePartyBegin(ctypes.byref(p), 2, NOW), 1)
        self.assertEqual(self.d.ILCT_FePartyBegin(ctypes.byref(p), 3, NOW), 0, 'one request at a time')
        self.assertEqual(self.step(p, NOW + 20, wait_owned=1).pulse, 0, 'Wait still holds its pause')
        self.assertEqual(self.step(p, NOW + 40).pulse, 1)
        self.assertEqual(p.state, PR_PULSED)
        self.assertEqual(self.step(p, NOW + 60).pulse, 0, 'one pulse per request')
        out = self.step(p, NOW + 80, native_open=1)
        self.assertEqual((out.result, p.state, p.last), (PRR_OPENED, PR_IDLE, PRR_OPENED))

    def test_cancellations(self):
        rows = [('pause screen', dict(paused=1), PRR_PAUSE),
                ('another screen before the pulse', dict(native_open=1, wait_owned=1), PRR_UNEXPECTED),
                ('a modal', dict(modal=1), PRR_MODAL),
                ('a load, cutscene or loss of control', dict(gate_ok=0), PRR_GATE)]
        for label, kw, result in rows:
            p = PartyRequest()
            self.d.ILCT_FePartyBegin(ctypes.byref(p), 2, NOW)
            with self.subTest(label):
                self.assertEqual(self.step(p, NOW + 20, **kw).result, result)
                self.assertEqual(p.state, PR_IDLE)

    def test_the_timeout(self):
        p = PartyRequest()
        self.d.ILCT_FePartyBegin(ctypes.byref(p), 2, NOW)
        self.step(p, NOW + 20)
        self.assertEqual(self.step(p, NOW + PARTY_TIMEOUT_MS - 1).result, PRR_NONE)
        self.assertEqual(self.step(p, NOW + PARTY_TIMEOUT_MS).result, PRR_TIMEOUT)
        self.assertEqual(p.state, PR_IDLE)

    def test_a_stock_command_tree_drains_first(self):
        p = PartyRequest()
        self.d.ILCT_FePartyBegin(ctypes.byref(p), 2, NOW)
        out = self.step(p, NOW + 20, native_open=1, tree_active=1)
        self.assertEqual((out.result, out.pulse, p.drain), (PRR_NONE, 0, 1), 'the tree is not "another screen"')
        self.assertEqual(self.step(p, NOW + 40, native_open=1).result, PRR_NONE, 'still draining')
        self.assertEqual(self.step(p, NOW + 60).pulse, 1, 'closed: now the pulse')


class RecoveryTest(PureCase):
    def step(self, r, down, native, now):
        return bool(self.d.ILCT_FeRecoveryStep(ctypes.byref(r), int(down), int(native), now))

    def test_holds_until_the_recovery_screen_has_closed(self):
        r = Recovery()
        self.assertFalse(self.step(r, False, False, NOW))
        self.assertTrue(self.step(r, True, False, NOW + 20))
        self.assertTrue(self.step(r, True, True, NOW + 40))
        self.assertTrue(self.step(r, False, True, NOW + 60), 'a reserve came in, the selection screen is still up')
        self.assertTrue(self.step(r, False, False, NOW + 80))
        self.assertTrue(self.step(r, False, False, NOW + 80 + RESUME_MS - 1))
        self.assertFalse(self.step(r, False, False, NOW + 80 + RESUME_MS))

    def test_no_screen_ever_releases_after_the_quiet_time(self):
        r = Recovery()
        self.step(r, True, False, NOW)
        self.assertTrue(self.step(r, False, False, NOW + 20))
        self.assertTrue(self.step(r, False, False, NOW + 20 + RECOVERY_QUIET_MS - 1))
        self.assertFalse(self.step(r, False, False, NOW + 20 + RECOVERY_QUIET_MS))


# ---- the layer with doubles ---------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class LayerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sw = ts.submit_world()
        cls.d = declare(cls.sw.d)
        cls.fixture = tm.Fixture()

    def setUp(self):
        self.sw.setup()
        d = self.d
        assert tm.load_sections(d, self.fixture.sections()) == tm.TABLES_OK
        d.ILCT_FeReset()
        d.ILCT_FePresenter(self.PRESENTER)
        d.ILCT_FeUseDoubles(1)
        d.ILCT_FeTablesFromMenu(tm.TABLES_OK)
        d.ILCT_FeSetShared(ctypes.byref(tm.shared()), 0)
        for s in range(3):
            d.ILCT_FeSetSeatState(s, ctypes.byref(tm.seat(member=s)), 0)
        d.ILCT_FeSetScreen(0)
        d.ILCT_WaitReset()
        d.ILCT_IxReset()
        d.ILCT_EdgeTestReset()
        d.ILCT_EdgeReset(1)
        self.img = Image()
        for va in (tf.UI_MANAGER, tf.PAUSE_A, tf.PAUSE_B, tf.SETTINGS, tf.ACTIVE_MENU):
            self.img.put_global(va, struct.pack('<Q', 0))
        self.img.put_global(tf.CAN_USE, b'\0')
        self.img.put_global(tf.DESCRIPTOR, bytes(24))
        self.img.put_global(tf.OUTPUT, bytes(24))
        self.img.put_global(tf.REGISTRY, bytes(0x30))
        d.ILCT_FwReset()
        d.ILCT_FwSetImageBase(self.img.base)
        d.ILCT_FwStart(self.img.base, NOW)
        self.seq = 1
        self.now = NOW
        self.flags = {0: SEAT_FULL, 1: SEAT_FULL, 2: SEAT_FULL}
        self.members = {0: 0, 1: 1, 2: 2}
        self.publish()
        d.ILCT_FeStart(TWIN_IMAGE, self.now)
        self.beat()  # seeds the edge cursors

    PRESENTER = 1  # the overlay drew the co-op menus (simulated); -1 = only a real stamp counts

    def tearDown(self):
        d = self.d
        d.ILCT_FeStop()
        d.ILCT_FeUseDoubles(0)
        d.ILCT_FeReset()
        d.ILCT_FwStop()
        d.ILCT_FwReset()
        d.ILCT_WaitReset()
        d.ILCT_IxReset()
        d.ILCT_TownSessionWithdraw()
        d.ILCT_SubReset()
        self.sw.twin.reset()
        self.img.close()

    def publish(self, epoch=EPOCH, facts=FACT['CONTROL'], phase=PHASE['FIELD'], gates=GATE['WORLD']):
        rec = Record()
        rec.head.epoch, rec.head.generation, rec.head.flags = epoch, GEN, gates
        rec.facts, rec.phase, rec.leader = facts, phase, 0
        for s in range(4):
            e = rec.seat[s]
            e.member, e.party_slot = 0xFFFF, 0xFF
            if s in self.flags:
                e.flags, e.member = self.flags[s], self.members[s]
                e.handle, e.actor, e.phys = 0x100001 + s, 0x5000000 + s * 0x1000, 0x5800000 + s * 0x1000
                e.party_slot = s
        self.rec = rec
        self.d.ILCT_TownSessionPublish(ctypes.byref(rec))

    def beat(self, seats=None, firewall=True, dt=20):
        """One co-op beat: an input tick with these edges, then the layer, then the firewall."""
        self.now += dt
        d = self.d
        d.ILCT_SetFixedClock(self.now)
        d.ILCT_PatchHeartbeat(self.rec.head.epoch)
        masks = (u32 * 28)()
        for seat, e in (seats or {}).items():
            masks[(seat - 1) * 7 + 3] = e.pressed
            masks[(seat - 1) * 7 + 5] = e.repeated
            masks[(seat - 1) * 7 + 6] = e.held
        d.ILCT_EdgeRecord(self.seq, masks)
        self.seq += 1
        d.ILCT_FeStep(self.now)
        if firewall:
            d.ILCT_FwStep(self.now)

    def press(self, seat, *actions, **kw):
        self.beat({seat: edges(pressed=actions)}, **kw)

    def seat(self, s) -> FeSeat:
        v = FeSeat()
        self.d.ILCT_FeSeat(s, ctypes.byref(v))
        return v

    def state(self) -> FeState:
        v = FeState()
        self.d.ILCT_FeState(ctypes.byref(v))
        return v

    def stats(self) -> FeCounters:
        c = FeCounters()
        self.d.ILCT_FeStats(ctypes.byref(c))
        return c

    def fw(self) -> tf.FwRecord:
        out = tf.FwRecord()
        self.assertEqual(self.d.ILCT_FwRecord(ctypes.byref(out)), 1)
        return out

    def wait_requested(self) -> bool:
        r = tw8.RequestRecord()
        self.assertEqual(self.d.ILCT_WaitRecord(ctypes.byref(r)), 1)
        return bool(r.head.flags & 1)

    def picker(self, seat, foes=1, party=None):
        actors = [tg.foe(0x200010 + k, x=3.0 + k, z=0.0) for k in range(foes)]
        party = [(0x100001 + s, s, 100, 0) for s in range(3)] if party is None else party
        self.d.ILCT_FeSetPicker(seat, ctypes.byref(tg.picker(actors=actors, party=party,
                                                              issuer_handle=0x100001 + seat)), 0)

    def party_of(self, members):
        """[(member, hp, status)] -> the picker's party rows (HUD order)."""
        return [(0x100001 + k, m, hp, st) for k, (m, hp, st) in enumerate(members)]

    def selector(self, seat) -> Selector:
        v = Selector()
        self.d.ILCT_FeSelector(seat, ctypes.byref(v))
        return v

    def requested(self, seat):
        seq, member = u32(), u32()
        self.d.ILCT_PartyPeekRequest(seat, ctypes.byref(seq), ctypes.byref(member))
        return seq.value, member.value


TWIN_IMAGE = 0x120000


class P1MenuAndGateTest(LayerCase):
    def test_p1s_a_opens_its_co_op_menu_and_the_gate_arms(self):
        self.assertTrue(self.state().report.p1_menus, 'the co-op menus own P1\'s battle UI')
        self.assertTrue(self.fw().head.flags & tf.F_GATE, 'the frontend gate is armed')
        self.press(1, A_CONFIRM)
        v = self.seat(0)
        self.assertEqual((v.mode, v.page, v.opens), (LIST, 0, 1))
        self.assertEqual((self.state().report.p1_open, self.state().report.guests_open), (1, 0))

    def test_the_gate_expires_when_the_layer_stops_posting(self):
        self.assertTrue(self.fw().head.flags & tf.F_GATE)
        self.d.ILCT_FeStop()   # posts an empty report and stops
        self.d.ILCT_FwStep(self.now + 20)
        self.assertFalse(self.fw().head.flags & tf.F_GATE, 'an empty report releases the gate at once')
        self.d.ILCT_FeStart(TWIN_IMAGE, self.now)
        self.beat()
        self.assertTrue(self.fw().head.flags & tf.F_GATE)
        gates = []
        for _ in range(4):
            self.now += 20
            self.d.ILCT_FwStep(self.now)  # the layer stalls: nothing posted
            gates.append(bool(self.fw().head.flags & tf.F_GATE))
        self.assertEqual(gates, [True, False, False, False], 'two beats after the last post, then stock')

    def test_the_layer_off_releases_p1s_stock_menus(self):
        self.d.ILCT_PatchApplySubsystems(0xFF & ~0x10)
        self.beat()
        self.assertEqual(self.state().report.p1_menus, 0)
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_FeTablesFromMenu(tm.TABLES_MISSING)
        self.d.ILCT_FeReset()
        self.d.ILCT_FeUseDoubles(1)
        self.d.ILCT_FeStart(TWIN_IMAGE, self.now)
        self.beat()
        self.assertEqual((self.state().report.p1_menus, self.state().tables_ready), (0, 0),
                         'no tables, no catalog: the stock menus stay P1\'s')
        self.assertFalse(self.fw().head.flags & tf.F_GATE)

    def test_a_highlighted_prompt_keeps_p1s_a_for_the_world(self):
        self.d.ILCT_FeStop()
        self.d.ILCT_FeStart(TWIN_IMAGE, self.now)
        # The firewall's view (last beat): P1's A goes to a highlighted prompt. Synthesised through its record.
        self.img.put_global(tf.CAN_USE, b'\0')
        out = bytearray(24)
        struct.pack_into('<II', out, 0x10, 1, 0x30001)  # state 1, a non-actor handle (registry 3)
        self.img.put_global(tf.OUTPUT, bytes(out))
        self.beat()
        self.beat()
        self.assertEqual(self.d.ILCT_FwPromptReady(), 1)
        self.press(1, A_CONFIRM)
        self.assertEqual(self.seat(0).mode, CLOSED)


class GuestMenuTest(LayerCase):
    def open_guest(self, seat=1):
        self.press(seat + 1, A_BATTLE)
        self.assertEqual(self.seat(seat).mode, LIST)

    def test_a_guest_opens_navigates_targets_and_submits(self):
        self.open_guest()
        self.assertEqual(self.state().report.guests_open, 0b10)
        self.press(2, A_DOWN)
        self.assertEqual(self.seat(1).cursor, 1)
        self.press(2, A_UP)
        self.assertEqual(self.seat(1).cursor, 0, 'back on Attack')
        self.picker(1, foes=2)
        self.press(2, A_CONFIRM)
        v = self.seat(1)
        self.assertEqual((v.mode, v.group, v.target_count), (TARGET, TG_FOES, 2))
        self.press(2, A_DOWN)
        self.assertEqual(self.seat(1).target_index, 1)
        shown = tg.CandidateList()
        self.d.ILCT_FeCandidates(1, ctypes.byref(shown))
        self.assertEqual(shown.selectable[TG_FOES], 2)
        self.assertEqual(shown.selectable[TG_PARTY], 3, 'the party is offered to Attack too')
        self.press(2, A_NEXT)
        self.assertEqual(self.seat(1).group, TG_PARTY, 'RB: the next group with candidates (Neutrals has none)')
        self.press(2, A_PREV)
        self.assertEqual((self.seat(1).group, self.seat(1).target_index), (TG_FOES, 0), 'LB: back, from the top')
        self.press(2, A_DOWN)
        self.press(2, A_CONFIRM)
        v = self.seat(1)
        self.assertEqual((v.mode, v.submits, v.last_submit), (CLOSED, 1, ts.SUB_QUEUED))
        slot = ts.SlotView()
        self.d.ILCT_SubSlot(1, ctypes.byref(slot))
        self.assertEqual((slot.state, slot.action), (ts.SLOT_QUEUED, ATTACK))
        self.press(2, A_BATTLE)
        self.assertEqual(self.seat(1).mode, CLOSED, f'cannot reopen within {REOPEN_QUIET_MS} ms')
        self.beat(dt=REOPEN_QUIET_MS)
        self.press(2, A_BATTLE)
        self.assertEqual(self.seat(1).mode, LIST)

    def test_cancel_backs_out_and_closes_at_the_root(self):
        self.open_guest()
        self.picker(1)
        self.press(2, A_CONFIRM)
        self.assertEqual(self.seat(1).mode, TARGET)
        self.press(2, A_CANCEL)
        self.assertEqual(self.seat(1).mode, LIST)
        self.press(2, A_CANCEL)
        self.assertEqual(self.seat(1).mode, CLOSED)
        self.assertTrue(self.state().report.guests_open & 0b10, 'the release guard still owns its directions')

    def test_a_guests_confirm_in_its_open_menu_is_not_an_interaction(self):
        self.assertEqual(self.d.ILCT_IxMenuSeats(EPOCH), 0)
        self.open_guest()
        self.assertEqual(self.d.ILCT_IxMenuSeats(EPOCH), 0b10)
        self.assertEqual(self.d.ILCT_IxMenuSeats(EPOCH + 1), 0, 'only for its epoch')
        # Guest interactions drop that seat's CONFIRM press.
        d = self.d
        d.ILCT_IxSetImageBase(self.img.base)
        d.ILCT_IxStart(self.img.base, self.now)
        d.ILCT_IxPress(0b10)
        d.ILCT_IxStep(self.now)
        c = tf_ix_counters(d)
        self.assertEqual(c.presses, 0)
        d.ILCT_IxSetMenuSeats(EPOCH, 0)
        d.ILCT_IxPress(0b10)
        d.ILCT_IxStep(self.now)
        self.assertEqual(tf_ix_counters(d).presses, 1, 'instrument: without the menu it is a press')
        d.ILCT_IxStop()

    def test_a_character_change_closes_the_menu_and_a_new_epoch_too(self):
        self.open_guest()
        self.members[1] = 4
        self.publish()
        self.beat()
        self.assertEqual(self.seat(1).mode, CLOSED)
        self.members[1] = 1
        self.publish()
        self.beat(dt=REOPEN_QUIET_MS)
        self.open_guest()
        self.publish(epoch=EPOCH + 1)
        self.beat()
        self.assertEqual(self.seat(1).mode, CLOSED)
        self.press(2, A_BATTLE)  # the first beat of a new epoch replays nothing; this press is new
        self.assertEqual(self.seat(1).mode, LIST)

    def test_parked_and_benched_seats_have_no_menu(self):
        self.flags[1] = SEATF['IN_SESSION'] | SEATF['ACTOR'] | SEATF['MENU']   # parked: not manual
        self.flags[2] = SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['BENCHED']
        self.publish()
        self.press(2, A_BATTLE)
        self.press(3, A_BATTLE)
        self.assertEqual((self.seat(1).mode, self.seat(2).mode), (CLOSED, CLOSED))


def tf_ix_counters(d):
    c = ti.Counters()
    d.ILCT_IxStats(ctypes.byref(c))
    return c


class ExternalFrontendTest(LayerCase):
    def test_suspend_and_resume_where_the_menu_was(self):
        self.press(2, A_BATTLE)
        self.press(2, A_DOWN)
        self.publish(facts=FACT['CONTROL'] | FACT['MENU'], phase=PHASE['MENU'])  # the map opens
        self.beat()
        self.beat()
        self.assertEqual(self.state().susp.suspended, 0, '40 ms not reached yet')
        self.beat()
        st = self.state()
        self.assertEqual((st.susp.suspended, st.susp.source, st.report.suppressed, st.report.guests_open),
                         (1, SRC_EXTERNAL, 1, 0))
        self.press(2, A_DOWN)
        self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (LIST, 1), 'kept as it was, no input taken')
        self.publish()  # closed
        for _ in range(RESUME_MS // 20):
            self.beat()
        self.assertEqual(self.state().susp.suspended, 1, '0.20 s not reached yet')
        self.beat()
        st = self.state()
        self.assertEqual((st.susp.suspended, st.report.suppressed, st.report.guests_open), (0, 0, 0b10))
        self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (LIST, 1), 'back exactly where it was')

    def test_the_pause_screen_suspends_and_a_load_aborts(self):
        self.press(1, A_CONFIRM)
        self.d.ILCT_FeSetScreen(SCREEN_PAUSED)
        for _ in range(3):
            self.beat()
        self.assertEqual(self.state().susp.suspended, 1)
        self.publish(phase=PHASE['LOADING'])
        self.beat()
        st = self.state()
        self.assertEqual((st.susp.suspended, self.seat(0).mode), (0, CLOSED), 'a load closes every menu')
        self.assertGreaterEqual(self.stats().aborted, 1)


class NativeScreenPulseTest(LayerCase):
    """E11 composed: while a native screen is up the co-op menus are suspended and take nothing; the guests' edges
    reach the screen as the firewall's pulses on P1's device, one edge per beat in priority order."""

    def test_guest_edges_drive_the_native_screen_not_the_suspended_menu(self):
        self.press(2, A_BATTLE)
        self.publish(facts=FACT['CONTROL'] | FACT['MENU'], phase=PHASE['MENU'])
        for _ in range(3):
            self.beat()
        self.assertEqual(self.state().susp.suspended, 1)
        self.press(2, A_CONFIRM, A_CANCEL, A_DOWN)
        fw = self.fw()
        dv = tf.DebugView()
        self.d.ILCT_FwDebug(ctypes.byref(dv))
        self.assertEqual((fw.pulse, dv.source), (tf.CANCEL, 2), 'cancel > confirm > directions, one edge')
        self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (LIST, 0), 'the suspended menu took nothing')
        self.beat(dt=120)
        self.press(3, A_PARTY, A_CONFIRM)
        self.d.ILCT_FwDebug(ctypes.byref(dv))
        self.assertEqual((self.fw().pulse, dv.source), (SEM_PARTY, 3), 'party > confirm on a native screen')
        self.assertEqual(self.state().party_state, PR_IDLE, 'on a native screen PARTY is a pulse, not a request')

    def test_no_co_op_menu_opens_over_a_native_screen(self):
        # A shop or dialog (a guest may drive it; the firewall then pulses nothing, see its own tests).
        self.publish(facts=FACT['CONTROL'] | FACT['MENU'] | FACT['MODAL'], phase=PHASE['MENU'])
        for _ in range(3):
            self.beat()
        self.press(2, A_BATTLE)
        self.assertEqual(self.seat(1).mode, CLOSED, 'no co-op menu opens over a native screen')


class PartyRequestLayerTest(LayerCase):
    def test_a_guests_party_pulses_party_from_that_seat(self):
        self.press(2, A_PARTY)
        st = self.state()
        self.assertEqual((st.party_state, st.party_seat, st.susp.source), (PR_PENDING, 2, SRC_PARTY))
        self.assertEqual((st.report.party_request, st.report.suppressed), (1, 0),
                         'P1\'s battle buttons stay withheld until the screen is seen')
        self.assertFalse(self.wait_requested())
        self.beat()
        st = self.state()
        self.assertEqual((st.party_state, st.report.party_pulse), (PR_PULSED, 2))
        fw = self.fw()
        self.assertEqual(fw.pulse, SEM_PARTY, 'the firewall pulses PARTY on P1\'s device for seat 2')
        dv = tf.DebugView()
        self.d.ILCT_FwDebug(ctypes.byref(dv))
        self.assertEqual(dv.source, 2)
        self.beat()
        self.assertEqual(self.state().report.party_pulse, 0, 'once')
        self.publish(facts=FACT['CONTROL'] | FACT['MENU'], phase=PHASE['MENU'])  # the Party screen
        self.beat()
        st = self.state()
        self.assertEqual((st.party_state, st.party_last, st.susp.observed_open, st.report.suppressed),
                         (PR_IDLE, PRR_OPENED, 1, 1))
        self.publish()
        for _ in range(RESUME_MS // 20 + 1):
            self.beat()
        self.assertEqual(self.state().susp.suspended, 0)

    def test_the_request_waits_for_wait_and_times_out_cleanly(self):
        self.press(2, A_BATTLE)
        self.press(1, A_CONFIRM)
        self.d.ILCT_WaitSetOwned(1)
        self.beat(dt=REOPEN_QUIET_MS)
        self.press(3, A_PARTY)
        self.beat()
        self.assertEqual(self.state().party_state, PR_PENDING, 'no pulse while Wait holds its pause')
        self.d.ILCT_WaitSetOwned(0)
        self.beat()
        self.assertEqual(self.state().party_state, PR_PULSED)
        for _ in range(PARTY_TIMEOUT_MS // 20):
            self.beat()
            self.assertFalse(self.wait_requested(), 'never requested during the transfer')
        st = self.state()
        self.assertEqual((st.party_state, st.party_last, st.susp.suspended), (PR_IDLE, PRR_TIMEOUT, 0))
        self.assertEqual((self.seat(0).mode, self.seat(1).mode), (LIST, LIST), 'the menus come back as they were')

    def test_p1s_party_is_native_unless_a_co_op_menu_is_open(self):
        self.press(1, A_PARTY)
        self.assertEqual(self.state().party_state, PR_IDLE, 'no menu open: P1\'s Y reaches the game itself')
        self.press(2, A_BATTLE)
        self.press(1, A_PARTY)
        self.assertEqual((self.state().party_state, self.state().party_seat), (PR_PENDING, 1))

    def test_a_seat_with_its_menu_open_cannot_ask(self):
        self.press(2, A_BATTLE)
        self.press(2, A_PARTY)
        self.assertEqual(self.state().party_state, PR_IDLE)


class RecoveryLayerTest(LayerCase):
    def test_all_regulars_down_gives_the_stock_recovery_the_battle_ui(self):
        self.press(1, A_CONFIRM)
        self.press(2, A_BATTLE)
        self.publish(gates=GATE['WORLD'] | GATE['PARTY_DOWN'], phase=PHASE['PARTY_DOWN'])
        self.beat()
        st = self.state()
        self.assertEqual((st.recovery, st.report.p1_menus, st.report.suppressed), (1, 0, 1))
        self.assertEqual((self.seat(0).mode, self.seat(1).mode), (CLOSED, CLOSED))
        self.assertFalse(self.fw().head.flags & tf.F_GATE, 'the stock leader select (the KO path) may open')
        self.assertFalse(self.wait_requested())
        self.press(1, A_CONFIRM)
        self.assertEqual(self.seat(0).mode, CLOSED, 'no co-op menu during the recovery')
        self.publish(facts=FACT['CONTROL'] | FACT['MENU'], phase=PHASE['MENU'])  # a reserve chosen, screen still up
        self.beat()
        self.assertEqual(self.state().recovery, 1)
        self.publish()
        for _ in range(RESUME_MS // 20 + 1):
            self.beat()
        self.assertEqual((self.state().recovery, self.state().report.p1_menus), (0, 1))
        self.assertTrue(self.fw().head.flags & tf.F_GATE)


class WaitRequestTest(LayerCase):
    def test_every_participants_menu_open(self):
        self.flags.pop(2)
        self.publish()
        self.press(1, A_CONFIRM)
        self.assertFalse(self.wait_requested(), 'P2 has not opened its menu')
        self.press(2, A_BATTLE)
        self.assertTrue(self.wait_requested())
        self.assertTrue(self.state().wait_requested)
        self.press(2, A_BATTLE)
        self.assertFalse(self.wait_requested(), 'P2 closed: time runs again')

    def test_benched_and_dead_seats_are_excluded(self):
        self.flags[2] = SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['BENCHED']
        self.publish()
        self.press(1, A_CONFIRM)
        self.press(2, A_BATTLE)
        self.assertTrue(self.wait_requested(), 'the benched P3 never holds it back')
        self.flags[2] = SEAT_FULL & ~SEATF['ALIVE']
        self.publish()
        self.beat()
        self.assertTrue(self.wait_requested(), 'P3\'s character is down: not a participant')
        self.flags[2] = SEAT_FULL
        self.publish()
        self.beat()
        self.assertFalse(self.wait_requested(), 'P3 back up with its menu closed')

    def test_not_requested_while_suspended_or_outside_field_play(self):
        self.flags.pop(2)
        self.flags.pop(1)
        self.publish()
        self.press(1, A_CONFIRM)
        self.assertTrue(self.wait_requested(), 'P1 alone: stock Wait behaviour')
        self.publish(facts=0)  # a scripted moment: no control
        self.beat()
        self.assertFalse(self.wait_requested())
        self.publish()
        self.d.ILCT_FeSetScreen(SCREEN_PAUSED)
        for _ in range(3):
            self.beat()
        self.assertFalse(self.wait_requested(), 'the pause screen suspended the menus')



# ---- the presenter guard: nothing undrawn owns P1's battle UI ---------------------------------------------------------
class PresenterRuleTest(PureCase):
    def test_the_live_window(self):
        d = self.d
        d.ILCT_FePresenterReset()
        try:
            self.assertEqual(d.ILCT_FePresenterLive(NOW), 0, 'never drawn: not live')
            d.ILCT_FeNoteDrawn(NOW)
            self.assertEqual([d.ILCT_FePresenterLive(NOW + dt) for dt in (0, 249, 250, 251, 5000)], [1, 1, 1, 0, 0])
            self.assertEqual(d.ILCT_FePresenterLive(NOW - 250), 1, 'a stamp just ahead of another clock read')
            self.assertEqual(d.ILCT_FePresenterLive(NOW - 251), 0)
            d.ILCT_FePresenter(0)
            self.assertEqual(d.ILCT_FePresenterLive(NOW), 0, 'the switch simulates no overlay')
            d.ILCT_FePresenter(1)
            self.assertEqual(d.ILCT_FePresenterLive(NOW + 9999), 1, 'the switch simulates an overlay')
        finally:
            d.ILCT_FePresenterReset()


class SeatPresenterRuleTest(PureCase):
    def test_shown_and_lost(self):
        d = self.d
        d.ILCT_FePresenterReset()
        try:
            self.assertEqual([d.ILCT_FeSeatShown(s) for s in range(4)], [1, 1, 1, 1], 'nothing reported: shown')
            d.ILCT_FeNoteDropped(0b0110, NOW)
            self.assertEqual([d.ILCT_FeSeatShown(s) for s in range(4)], [1, 0, 0, 1])
            d.ILCT_FeNoteDropped(0b0010, NOW + 100)   # seat 2 drawn again; seat 1 still out, since NOW
            self.assertEqual([d.ILCT_FeSeatShown(s) for s in range(4)], [1, 0, 1, 1])
            self.assertEqual([d.ILCT_FeSeatLost(1, NOW + dt) for dt in (0, 250, 251)], [0, 0, 1])
            self.assertEqual(d.ILCT_FeSeatLost(2, NOW + 999), 0)
            self.assertEqual(d.ILCT_FeSeatLost(1, NOW - 5), 0, 'another clock a little behind: not lost')
            d.ILCT_FeNoteDropped(0, NOW + 300)
            self.assertEqual((d.ILCT_FeSeatShown(1), d.ILCT_FeSeatLost(1, NOW + 999)), (1, 0))
            self.assertEqual((d.ILCT_FeSeatShown(7), d.ILCT_FeSeatLost(7, NOW)), (1, 0), 'out of range')
        finally:
            d.ILCT_FePresenterReset()


class SeatPresenterLayerTest(LayerCase):
    """A seat whose window the overlay left out of its frame: its menu takes no input and holds no Wait; left out for
    longer than LIVE_MS it closes. The other seats go on."""

    def setUp(self):
        super().setUp()
        self.flags.pop(2)
        self.publish()
        self.press(1, A_CONFIRM)
        self.press(2, A_BATTLE)
        self.assertEqual((self.seat(0).mode, self.seat(1).mode), (LIST, LIST))
        self.assertTrue(self.wait_requested(), 'instrument: both menus open and shown hold Wait')

    def test_a_seat_left_out_takes_no_input_and_holds_no_wait(self):
        self.d.ILCT_FeNoteDropped(0b10, self.now)
        self.press(2, A_DOWN)
        self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (LIST, 0), 'kept in place, no input')
        self.assertFalse(self.wait_requested(), 'a menu nobody sees holds no Wait')
        self.press(1, A_DOWN)
        self.assertEqual(self.seat(0).cursor, 1, 'Player 1 goes on')
        self.d.ILCT_FeNoteDropped(0, self.now)
        self.press(2, A_DOWN)
        self.assertEqual(self.seat(1).cursor, 1, 'drawn again: input again')
        self.assertTrue(self.wait_requested())

    def test_a_seat_left_out_longer_than_live_ms_closes(self):
        since = self.now
        self.d.ILCT_FeNoteDropped(0b10, since)
        while self.now + 20 <= since + LIVE_MS:
            self.beat()
        self.assertEqual(self.seat(1).mode, LIST, f'{self.now - since} ms out: still open')
        closes = self.stats().hard_closes
        self.beat(dt=LIVE_MS)
        self.assertEqual(self.seat(1).mode, CLOSED, 'lost: closed')
        self.assertEqual(self.stats().hard_closes - closes, 1)
        self.assertEqual(self.seat(0).mode, LIST, "Player 1's menu stays")

    def test_a_guest_left_out_is_not_reported_open(self):
        self.assertEqual(self.state().report.guests_open & 0b10, 0b10, 'instrument: shown, reported open')
        self.d.ILCT_FeNoteDropped(0b10, self.now)
        self.beat()
        self.assertEqual(self.state().report.guests_open & 0b10, 0, "a menu nobody sees blocks nothing of P1's")
        self.assertEqual(self.seat(1).mode, LIST, 'it keeps its place')
        self.d.ILCT_FeNoteDropped(0, self.now)
        self.beat()
        self.assertEqual(self.state().report.guests_open & 0b10, 0b10, 'drawn again: open again')


class P1WindowLeftOutTest(LayerCase):
    """Player 1's own window left out of a presented frame: P1 gets the stock battle menu and leader select back on
    the next beat (well inside the 250 ms contract), the invisible menu closes and takes no input; the co-op menus take
    P1's buttons back only once seat 0 is shown again and P1_GIVEBACK_MS has passed. Guests go on."""

    def setUp(self):
        super().setUp()
        self.flags.pop(2)
        self.publish()
        self.picker(0, party=self.party_of([(0, 100, 0), (1, 100, 0), (2, 100, 0), (3, 100, 0)]))
        self.press(1, A_CONFIRM)
        self.press(2, A_BATTLE)
        st = self.state()
        self.assertEqual((self.seat(0).mode, self.seat(1).mode), (LIST, LIST))
        self.assertEqual((st.owns_p1, st.report.p1_menus, st.report.p1_open, st.report.leader_gate), (1, 1, 1, 1))
        self.assertTrue(self.fw().head.flags & tf.F_GATE, "instrument: the co-op menus own P1's frontends")

    def assert_stock_for_p1(self, why: str):
        st = self.state()
        self.assertEqual((st.owns_p1, st.report.p1_menus, st.report.p1_open, st.report.leader_gate,
                          st.report.leader_selector), (0, 0, 0, 0, 0), why)
        fw = self.fw()
        self.assertFalse(fw.head.flags & tf.F_GATE, f'{why}: the stock battle menu is P1s')
        self.assertFalse(fw.refused & (1 << LEADER_ID), f'{why}: the stock leader select passes')
        # Every P1 button reaches the stock menus, a guest's open menu or not: that menu keeps only the guests' pads.
        self.assertEqual(fw.p1_mask, 0, f"{why}: every P1 button reaches the game")

    def test_p1_gets_the_stock_menu_and_leader_select_within_the_contract(self):
        t0 = self.now
        givebacks = self.stats().p1_givebacks
        self.d.ILCT_FeNoteDropped(0b1, t0)   # the overlay left P1's window out of the frame it presented
        self.beat()
        self.assertLess(self.now - t0, LIVE_MS, 'one beat, inside the contract')
        self.assertEqual(self.seat(0).mode, CLOSED, 'the invisible menu closed')
        self.assert_stock_for_p1('next beat')
        self.assertEqual(self.stats().p1_givebacks - givebacks, 1)
        self.assertFalse(self.wait_requested(), 'no Wait for a menu nobody sees')
        self.press(1, A_CONFIRM)
        self.press(1, A_DOWN)
        self.assertEqual((self.seat(0).mode, self.seat(0).selector_kind), (CLOSED, SEL_NONE),
                         "P1's presses are the stock frontends', the co-op menu and selector stay shut")
        self.assert_stock_for_p1('after presses')
        self.press(2, A_DOWN)
        self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (LIST, 1), 'the guest goes on')

    def test_the_co_op_menus_take_p1_back_only_after_the_cooldown(self):
        self.d.ILCT_FeNoteDropped(0b1, self.now)
        self.beat()
        out_at = self.now
        self.d.ILCT_FeNoteDropped(0, self.now)   # the next presented frame: nothing of P1's open, seat 0 shown
        while self.now + 20 - out_at < P1_GIVEBACK_MS:
            self.beat()
            self.assert_stock_for_p1(f'{self.now - out_at} ms after the give-back')
        self.beat()
        st = self.state()
        self.assertEqual((st.owns_p1, st.report.p1_menus, st.report.leader_gate), (1, 1, 1), 'the cooldown is over')
        self.assertTrue(self.fw().head.flags & tf.F_GATE)
        self.press(1, A_CONFIRM)
        self.assertEqual(self.seat(0).mode, LIST, 'P1 has its co-op menu again')

    def test_seat_0_still_left_out_keeps_the_stock_frontends(self):
        self.d.ILCT_FeNoteDropped(0b1, self.now)
        for _ in range(P1_GIVEBACK_MS // 20 + 5):
            self.beat()
        self.assert_stock_for_p1('never shown again')

    def test_the_leader_selector_left_out_gives_the_stock_leader_select_back(self):
        self.press(1, A_CANCEL)
        self.beat(dt=REOPEN_QUIET_MS)
        self.press(1, A_DOWN)
        self.assertEqual(self.seat(0).selector_kind, SEL_LEADER, 'instrument: the co-op Change Leader is up')
        self.assertTrue(self.fw().refused & (1 << LEADER_ID))
        self.d.ILCT_FeNoteDropped(0b1, self.now)
        self.beat()
        self.assertEqual(self.seat(0).selector_kind, SEL_NONE)
        self.assert_stock_for_p1('selector left out')

    def test_alone_every_p1_button_reaches_the_game(self):
        self.press(2, A_CANCEL)    # the guest closes its menu
        self.beat(dt=200)          # past its release guard
        self.assertEqual(self.state().report.guests_open, 0, 'instrument: no guest menu open')
        self.d.ILCT_FeNoteDropped(0b1, self.now)
        self.beat()
        self.assert_stock_for_p1('P1 alone')
        self.assertEqual(self.fw().p1_mask, 0)

    def test_p1s_d_pad_reaches_the_stock_menu_while_a_guest_menu_is_open(self):
        self.d.ILCT_FeNoteDropped(0b1, self.now)
        self.beat()
        self.assertEqual((self.seat(1).mode, self.state().report.guests_open & 0b10), (LIST, 0b10),
                         "instrument: the guest's menu is open and shown")
        fw = self.fw()
        self.assertEqual(fw.p1_mask & (tf.DIRS | tf.M(tf.RB, tf.PARTY)), 0, "P1's d-pad, RB and Y reach the stock menu")
        self.assertEqual(fw.other_mask & (tf.DIRS | tf.M(tf.LB, tf.RB, tf.PARTY)), tf.DIRS | tf.M(tf.LB, tf.RB, tf.PARTY),
                         "the guests' pads stay walled off from the game")
        cursor = self.seat(1).cursor
        self.press(1, A_DOWN)
        self.assertEqual(self.seat(1).cursor, cursor, "P1's d-pad never moves the guest's menu")
        self.press(2, A_DOWN)
        self.assertEqual(self.seat(1).cursor, cursor + 1, "the guest's menu still gets the guest's directions")
        requests = self.stats().party_requests
        self.press(1, A_PARTY)
        self.assertEqual((self.state().party_state, self.stats().party_requests), (PR_IDLE, requests),
                         "P1's Y went to the game natively: it is never also a Party request")

    def test_control_p1_owned_by_the_co_op_menus_is_walled_as_before(self):
        self.press(1, A_CANCEL)    # P1 closes its own menu; the guest's stays open
        self.beat(dt=200)          # past its release guard
        st = self.state()
        self.assertEqual((st.owns_p1, st.report.p1_open, st.report.guests_open & 0b10), (1, 0, 0b10), 'instrument')
        fw = self.fw()
        self.assertEqual(fw.p1_mask & (tf.DIRS | tf.M(tf.RB, tf.PARTY)), tf.DIRS | tf.M(tf.RB, tf.PARTY),
                         "a guest's menu owns P1's directions, RB and Y while the co-op menus own P1")
        self.press(1, A_PARTY)
        self.assertEqual((self.state().party_state, self.state().party_seat), (PR_PENDING, 1),
                         "P1's Y is withheld and becomes a Party request instead")

    def test_a_guest_left_out_gives_p1_nothing_back(self):
        self.d.ILCT_FeNoteDropped(0b10, self.now)
        self.beat()
        st = self.state()
        self.assertEqual((st.owns_p1, st.report.p1_menus, self.seat(0).mode), (1, 1, LIST), 'control: P1 keeps it')
        self.assertEqual(self.stats().p1_givebacks, 0)


class NoPresenterLayerTest(LayerCase):
    """No overlay: nothing writes the drawn stamp (this build), so the layer idles. P1 keeps every stock frontend, no
    menu opens for anyone, and Wait is never requested."""
    PRESENTER = -1

    def test_the_gate_never_arms(self):
        for _ in range(5):
            self.beat()
            st = self.state()
            self.assertEqual((st.report.p1_menus, st.report.presenter_live, st.owns_p1, st.presenter_live),
                             (0, 0, 0, 0))
            fw = self.fw()
            self.assertEqual((fw.head.flags & tf.F_GATE, fw.refused), (0, 0), 'every stock frontend is P1s')
            self.assertEqual(fw.p1_mask, 0, "P1's buttons all reach the game")

    def test_p1s_a_and_a_guests_x_open_nothing_and_wait_is_never_requested(self):
        self.d.ILCT_WaitSetOwned(0)
        for seat, action in ((1, A_CONFIRM), (1, A_BATTLE), (2, A_BATTLE), (3, A_BATTLE), (1, A_DOWN), (2, A_UP)):
            self.press(seat, action)
            self.assertEqual((self.seat(seat - 1).mode, self.seat(seat - 1).selector_kind), (CLOSED, SEL_NONE),
                             f'seat {seat}: no menu, no selector')
            self.assertFalse(self.wait_requested(), 'never requested')
        self.assertEqual((self.stats().opens, self.stats().selector_opens), (0, 0))
        self.press(2, A_PARTY)
        self.assertEqual(self.state().party_state, PR_IDLE, 'no Party request either')

    def test_a_stale_stamp_counts_for_nothing_and_a_fresh_one_arms(self):
        self.d.ILCT_FeNoteDrawn(self.now - 300)
        self.beat()
        self.assertEqual(self.state().report.p1_menus, 0, 'drawn 300 ms ago: not live')
        self.d.ILCT_FeNoteDrawn(self.now + 20)
        self.beat()
        self.assertEqual((self.state().report.p1_menus, self.state().report.presenter_live), (1, 1))
        self.assertTrue(self.fw().head.flags & tf.F_GATE, 'instrument: a drawn layer arms the gate')
        self.press(1, A_CONFIRM)
        self.assertEqual(self.seat(0).mode, LIST, 'a stamp 20 ms old is live: P1 has its co-op menu')

    def test_the_ko_path_passes_without_a_presenter(self):
        self.flags[0] = SEAT_FULL & ~SEATF['ALIVE']
        self.publish()
        self.picker(0, party=self.party_of([(0, 0, ST_KO), (1, 100, 0), (2, 100, 0), (3, 100, 0)]))
        for _ in range(3):
            self.beat()
        st = self.state()
        self.assertEqual((st.p1_ko, st.ko_live, st.report.leader_pending), (0, 0, 0))
        fw = self.fw()
        self.assertFalse(fw.refused & (1 << LEADER_ID), 'stock id 3 (the KO leader select) passes')
        self.assertFalse(fw.head.flags & tf.F_GATE)


# ---- the character selectors ------------------------------------------------------------------------------------------
def sel_in(slots, own, own_able=1, seat=0) -> SelIn:
    v = SelIn()
    for k in range(4):
        m, able, holder = slots[k] if k < len(slots) else (NO_MEMBER, 0, NO_SEAT)
        v.slot[k].member, v.slot[k].able, v.slot[k].holder = m, able, holder
    v.own, v.own_able, v.seat = own, own_able, seat
    return v


class SelectorRuleTest(PureCase):
    def build(self, kind, slots, own, **kw):
        sel = Selector()
        n = self.d.ILCT_FeSelectorBuild(ctypes.byref(sel), kind, ctypes.byref(sel_in(slots, own, **kw)))
        return sel, n

    def members(self, sel):
        return list(sel.member[:sel.count])

    def test_the_choices_per_kind(self):
        # Vaan (P1), Fran (P2's), Ashe (free), Penelo (free, KO); a story guest (id 8) in the last slot is never offered.
        party = [(0, 1, 0), (2, 1, 1), (1, 1, NO_SEAT), (5, 0, NO_SEAT)]
        sel, n = self.build(SEL_LEADER, party, 0)
        self.assertEqual((self.members(sel), n, sel.index), ([0, 2, 1], 3, 0), 'starts on P1s own character')
        sel, _ = self.build(SEL_LEADER, party, 0, own_able=0)
        self.assertEqual(self.members(sel), [0, 1], "an exchange hands P1's character over: it must be able to lead")
        sel, _ = self.build(SEL_CHARACTER, party, 2, seat=1)
        self.assertEqual((self.members(sel), sel.index), ([0, 2, 1], 1), "a guest starts on its own")
        sel, _ = self.build(SEL_KO, party, 0, own_able=0)
        self.assertEqual(self.members(sel), [1], 'the KO recovery: living characters nobody holds')
        sel, n = self.build(SEL_KO, [(0, 0, 0), (2, 1, 1)], 0, own_able=0)
        self.assertEqual(n, 0, 'nobody free: nothing to offer (the stock leader select runs)')
        sel, _ = self.build(SEL_LEADER, [(0, 1, 0), (8, 1, NO_SEAT), (4, 1, NO_SEAT)], 0)
        self.assertEqual(self.members(sel), [0, 4], 'story guests are never offered')

    def test_commands(self):
        party = [(0, 1, 0), (1, 1, NO_SEAT), (3, 1, NO_SEAT)]
        sel, _ = self.build(SEL_LEADER, party, 0)
        chosen = u32()
        cmd = lambda c: self.d.ILCT_FeSelectorCommand(ctypes.byref(sel), c, ctypes.byref(chosen))  # noqa: E731
        self.assertEqual((cmd(CMD['UP']), sel.index), (SELE_MOVED, 2), 'wraps')
        self.assertEqual((cmd(CMD['DOWN']), sel.index), (SELE_MOVED, 0))
        self.assertEqual((cmd(CMD['DOWN']), sel.index), (SELE_MOVED, 1))
        self.assertEqual((cmd(CMD['CONFIRM']), chosen.value, sel.kind), (SELE_CHOSEN, 1, SEL_NONE), 'chosen, closed')
        sel, _ = self.build(SEL_LEADER, party, 0)
        self.assertEqual((cmd(CMD['BACK']), sel.kind), (SELE_CLOSED, SEL_NONE))
        sel, _ = self.build(SEL_CHARACTER, party, 1, seat=1)
        self.assertEqual((cmd(CMD['CLOSE']), sel.kind), (SELE_CLOSED, SEL_NONE), 'the menu button closes it')
        sel, _ = self.build(SEL_KO, party, 0, own_able=0)
        self.assertEqual((cmd(CMD['BACK'] | CMD['CLOSE']), sel.kind), (SELE_NONE, SEL_KO), 'the KO recovery stays')
        self.assertEqual((cmd(CMD['CONFIRM']), chosen.value, sel.kind), (SELE_CHOSEN, 1, SEL_KO),
                         'it stays up until a living character leads')

    def test_a_refresh_keeps_the_highlighted_member(self):
        sel, _ = self.build(SEL_LEADER, [(0, 1, 0), (1, 1, NO_SEAT), (3, 1, NO_SEAT)], 0)
        chosen = u32()
        self.d.ILCT_FeSelectorCommand(ctypes.byref(sel), CMD['UP'], ctypes.byref(chosen))
        self.assertEqual(sel.member[sel.index], 3)
        self.d.ILCT_FeSelectorBuild(ctypes.byref(sel), SEL_LEADER,
                                    ctypes.byref(sel_in([(3, 1, NO_SEAT), (0, 1, 0), (1, 1, NO_SEAT)], 0)))
        self.assertEqual(sel.member[sel.index], 3, 'the party reordered: still on Basch')


class LeaderSelectorLayerTest(LayerCase):
    """D-pad UP/DOWN with P1's battle menu closed: the co-op Change Leader; a choice is the seat-change transaction."""

    def setUp(self):
        super().setUp()
        self.picker(0, party=self.party_of([(0, 100, 0), (1, 100, 0), (2, 100, 0), (3, 100, 0)]))

    def test_p1_opens_moves_and_chooses_a_new_leader(self):
        before = self.requested(0)[0]
        self.press(1, A_DOWN)
        v = self.seat(0)
        self.assertEqual((v.mode, v.selector_kind, v.selector_index), (CLOSED, SEL_LEADER, 0))
        st = self.state()
        self.assertEqual((st.report.leader_selector, st.report.p1_open, st.report.leader_gate), (1, 1, 1))
        fw = self.fw()
        self.assertTrue(fw.refused & (1 << LEADER_ID), 'the stock leader select stays shut')
        self.assertTrue(fw.p1_mask & tf.M(tf.UP, tf.DOWN), "P1's UP/DOWN belong to the co-op selector")
        self.assertEqual(list(self.selector(0).member[:4]), [0, 1, 2, 3])
        self.press(1, A_DOWN)
        self.press(1, A_DOWN)
        self.press(1, A_DOWN)
        self.assertEqual(self.selector(0).index, 3)
        self.press(1, A_CONFIRM)
        self.assertEqual(self.seat(0).selector_kind, SEL_NONE, 'closed after the choice')
        seq, member = self.requested(0)
        self.assertEqual((seq - before, member), (1, 3), "the leader setter's transaction asks for Basch")
        self.assertEqual(self.stats().selector_requests, 1)

    def test_cancel_and_choosing_ones_own_character_change_nothing(self):
        before = self.requested(0)[0]
        self.press(1, A_UP)
        self.press(1, A_CANCEL)
        self.assertEqual(self.seat(0).selector_kind, SEL_NONE)
        self.beat(dt=REOPEN_QUIET_MS)
        self.press(1, A_UP)
        self.press(1, A_CONFIRM)  # still on Vaan
        self.assertEqual((self.seat(0).selector_kind, self.requested(0)[0] - before), (SEL_NONE, 0))
        self.assertEqual(self.stats().selector_choices, 1)

    def test_down_members_are_not_offered_and_guests_characters_are_exchanges(self):
        self.picker(0, party=self.party_of([(0, 100, 0), (1, 100, 0), (2, 0, ST_KO), (4, 100, ST_STONE)]))
        self.press(1, A_DOWN)
        self.assertEqual(list(self.selector(0).member[:self.selector(0).count]), [0, 1],
                         "P2's Ashe (an exchange) yes, KO Fran and stoned Basch no")

    def test_a_guest_opens_change_character_and_its_choice_is_its_own_seat_change(self):
        self.picker(1, party=self.party_of([(0, 100, 0), (1, 100, 0), (2, 100, 0), (3, 100, 0)]))
        before = self.requested(1)[0]
        self.press(2, A_UP)
        v = self.seat(1)
        self.assertEqual((v.selector_kind, self.selector(1).member[v.selector_index]), (SEL_CHARACTER, 1),
                         'starts on its own character')
        self.assertEqual(self.state().report.guests_open & 0b10, 0b10, "the selector owns the guest's directions")
        self.assertEqual(self.d.ILCT_IxMenuSeats(EPOCH), 0b10, "its CONFIRM is not an interaction")
        self.press(2, A_DOWN)
        self.press(2, A_DOWN)
        self.press(2, A_CONFIRM)
        seq, member = self.requested(1)
        self.assertEqual((seq - before, member), (1, 3), "P2 takes Basch (nobody's): a take, its old one to the AI")

    def test_the_selector_counts_for_wait_like_a_menu(self):
        self.flags.pop(2)
        self.flags.pop(1)
        self.publish()
        self.press(1, A_DOWN)
        self.assertTrue(self.wait_requested(), 'P1 alone choosing a leader: Wait pauses as for its menu')


class KoRecoveryLayerTest(LayerCase):
    """P1's character down (the party not): the co-op KO selector, or the stock leader select when it cannot help."""

    def ko(self, party):
        self.flags[0] = SEAT_FULL & ~SEATF['ALIVE']
        self.publish()
        self.picker(0, party=self.party_of(party))
        self.beat()

    def test_the_ko_selector_shuts_the_stock_one_and_takes_a_living_free_character(self):
        self.press(1, A_CONFIRM)  # P1's menu open when the character falls
        before = self.requested(0)[0]
        self.ko([(0, 0, ST_KO), (1, 100, 0), (2, 100, 0), (3, 100, 0)])
        st = self.state()
        self.assertEqual((st.p1_ko, st.ko_live, self.seat(0).mode, self.seat(0).selector_kind), (1, 1, CLOSED, SEL_KO))
        self.assertEqual((st.report.leader_pending, st.report.leader_selector, st.report.p1_menus), (1, 1, 1))
        fw = self.fw()
        self.assertTrue(fw.refused & (1 << LEADER_ID), 'the KO path (id 3, every frame) is refused')
        self.assertEqual(list(self.selector(0).member[:self.selector(0).count]), [3],
                         "Ashe and Fran are P2's and P3's; Basch is free")
        self.press(1, A_CANCEL)
        self.assertEqual(self.seat(0).selector_kind, SEL_KO, 'it cannot be dismissed')
        self.press(1, A_BATTLE)
        self.assertEqual((self.seat(0).mode, self.seat(0).selector_kind), (CLOSED, SEL_KO), 'no battle menu meanwhile')
        self.press(1, A_CONFIRM)
        self.assertEqual(self.requested(0), (before + 1, 3), 'Basch leads')
        self.assertEqual(self.seat(0).selector_kind, SEL_KO, 'up until a living character leads')
        self.members[0] = 3
        self.flags[0] = SEAT_FULL
        self.publish()
        self.beat()
        st = self.state()
        self.assertEqual((st.p1_ko, st.ko_live, self.seat(0).selector_kind), (0, 0, SEL_NONE))

    def test_with_nobody_to_offer_the_stock_leader_select_runs(self):
        self.ko([(0, 0, ST_KO), (1, 100, 0), (2, 100, 0)])
        st = self.state()
        self.assertEqual((st.p1_ko, st.ko_live, st.owns_p1, st.report.p1_menus, st.report.leader_pending),
                         (1, 0, 0, 0, 0))
        fw = self.fw()
        self.assertFalse(fw.refused & (1 << LEADER_ID), 'stock id 3 passes: P1 is never stuck')
        self.assertFalse(fw.head.flags & tf.F_GATE)
        self.assertEqual(fw.p1_mask, 0, "P1's buttons drive the stock leader select")

    def test_a_ko_promotion_leaves_it_to_the_game(self):
        """The game already made someone else leader while P1's seat keeps its fallen character: no co-op selector, an
        empty P1 part of the report (no p1_menus, no leader_gate), so stock id 3, P1's buttons and the stock leader
        select are the game's. P1's open battle menu closes. A living P1 character arms the gate again."""
        self.press(1, A_CONFIRM)
        self.assertEqual(self.seat(0).mode, LIST, "instrument: P1's co-op menu is open when the character falls")
        self.assertTrue(self.fw().head.flags & tf.F_GATE, 'instrument: the gate is armed (presenter simulated on)')
        self.flags[0] = SEAT_FULL & ~SEATF['ALIVE']
        self.publish()
        self.rec.leader = 1  # the game already made someone else leader
        self.d.ILCT_TownSessionPublish(ctypes.byref(self.rec))
        self.picker(0, party=self.party_of([(0, 0, ST_KO), (1, 100, 0), (2, 100, 0), (3, 100, 0)]))
        self.beat()
        self.beat(dt=200)  # past the closed menu's release guard
        st = self.state()
        self.assertEqual((st.p1_ko, st.ko_live, st.presenter_live), (0, 0, 1))
        self.assertEqual((st.owns_p1, st.report.p1_menus, st.report.leader_gate, st.report.leader_pending), (0, 0, 0, 0))
        self.assertEqual((self.seat(0).mode, self.seat(0).selector_kind), (CLOSED, SEL_NONE), 'the menu closed')
        fw = self.fw()
        self.assertFalse(fw.refused & (1 << LEADER_ID), 'stock id 3 (the leader select) passes')
        self.assertFalse(fw.head.flags & tf.F_GATE, 'the gate is off')
        self.assertEqual(fw.p1_mask, 0, "P1's buttons all reach the game")
        for action in (A_DOWN, A_UP, A_CONFIRM, A_BATTLE):
            self.press(1, action)
            self.assertEqual((self.seat(0).mode, self.seat(0).selector_kind), (CLOSED, SEL_NONE),
                             f'{action}: nothing of ours opens on a fallen character')
            fw = self.fw()
            self.assertEqual((fw.p1_mask, fw.refused & (1 << LEADER_ID)), (0, 0),
                             f"{action}: P1's press reaches the stock leader select")
        # A living character of P1's leads again: the co-op layer owns P1's frontends again.
        self.flags[0], self.members[0] = SEAT_FULL, 1
        self.members[1] = 0
        self.publish()
        self.rec.leader = 1
        self.d.ILCT_TownSessionPublish(ctypes.byref(self.rec))
        self.beat()
        self.assertEqual((self.state().owns_p1, self.state().report.leader_gate), (1, 1))
        self.assertTrue(self.fw().head.flags & tf.F_GATE, 'instrument: the gate arms again')


# ---- a native Quickening chain ------------------------------------------------------------------------------------------
class QuickeningLayerTest(LayerCase):
    def test_the_chain_is_reported_with_each_guests_link(self):
        self.d.ILCT_FeSetScreen(SCREEN_QUICKENING)
        self.beat()
        r = self.state().report
        self.assertEqual((r.quickening, r.quickening_command_seat, r.quickening_confirm_seat),
                         (tf.QB_CHAIN | tf.QB_COMMAND | tf.QB_CONFIRM, 2, 3), 'slot 1 = P2 (COMMAND), slot 2 = P3')
        fw = self.fw()
        self.assertEqual(fw.p1_mask, tf.M(tf.COMMAND, tf.CONFIRM), 'P1 passes through but for the guests links')
        self.press(2, 14)  # QUICKENING
        self.assertEqual(self.fw().pulse, tf.COMMAND, "P2's link reaches the chain")
        self.d.ILCT_FeSetScreen(0)
        self.beat(dt=200)
        self.assertEqual(self.state().report.quickening, 0)

    def test_the_chain_passes_through_without_a_presenter_too(self):
        self.d.ILCT_FePresenter(0)
        self.d.ILCT_FeSetScreen(SCREEN_QUICKENING)
        self.beat()
        r = self.state().report
        self.assertEqual((r.p1_menus, r.quickening & tf.QB_CHAIN), (0, tf.QB_CHAIN))
        self.press(3, 14)
        self.assertEqual(self.fw().pulse, tf.CONFIRM, "P3's link")

    def test_a_parked_guest_has_no_link(self):
        self.flags[1] = SEATF['IN_SESSION'] | SEATF['ACTOR'] | SEATF['MENU']
        self.publish()
        self.d.ILCT_FeSetScreen(SCREEN_QUICKENING)
        self.beat()
        r = self.state().report
        self.assertEqual((r.quickening, r.quickening_command_seat), (tf.QB_CHAIN | tf.QB_CONFIRM, 0))


if __name__ == '__main__':
    unittest.main()
