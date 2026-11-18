"""Host tests for the input firewall and the stock-frontend gate (native/coop/src/firewall). No game run, nothing
deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin and the emulator.

* the policy as our own tables: the composition of every co-op state the firewall sees (menus, native screens,
  towns, Quickening, KO recovery, a guest driving a native menu, the desktop owner) into per-device masks and hook
  flags; the player-1 parity oracle (over all 1,024 contracts only P1's LEFT/RIGHT differ from the old leader rule,
  and only while the leader rule holds with no menu open); P1's native pulses (B -> CANCEL, A on a highlighted
  prompt -> CONFIRM, TARGET INFO closed at most every 0.5 s) and the guests' pulse priority;
* one co-op beat on synthetic inputs: which pad object is P1's, the pulse scheduler (65 ms high, 40 ms low, P1
  first, guests round-robin, one queued edge per seat, stale and out-of-context edges dropped), composition with
  input isolation's menu driver (a guest driving a dialog or shop: native policy, no pulses);
* the co-op thread on synthetic game memory: the session and isolation records, the decide setting (and the pad
  A/B swap it hands the input service), pause, TARGET INFO, the stock command tree, P1's prompt with a foe excluded,
  edges through the input seam;
* the handlers natively on saved machine states: fail-open, FW-01 masks and pulses per device, FW-03/04/05 on
  synthetic desktop devices, FG-01/05/06 refusing exactly ids 1, 3 and 4, FG-02/03/04 refusing behind them, FG-07
  counting and never refusing;
* unicorn on the hooked twin: the pad get-button 0x79D670 with every mask bit on P1's device and on another device,
  pulses, the pad get-axis 0x79D730, the desktop button and wheel, the request dispatcher 0x254B50 (and the state
  setter and dispatch) with ids 1/3/4 refused and every other id running the original code; every firewall site
  equals the original bytes when it must not act;
* the site table: FW-01..05 and FG-01..07 are FIREWALL_MENUS port rows, the three Wait call rows are dropped.
"""
from __future__ import annotations

import ctypes
import random
import struct
import unittest

import test_coop_game_thread as gt
import test_coop_input_isolation as iso
import test_coop_patch_engine as pe
from coop_native_support import quiet_faulthandler
from coop_session_support import FACT, GATE, PHASE, Image, Record, RecordHead
from test_coop_patch_engine import CALL_ROWS, GAME, NATIVE, PATCH_ROWS, TWIN_BASE, checked

try:
    import unicorn
    from unicorn import x86_const as X
except ImportError:
    unicorn = None

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


FwRecord = S('FwRecord', [('head', RecordHead), ('p1_mask', u32), ('other_mask', u32), ('refused', u32),
                          ('pulse', u16), ('p1_slot', u8), ('reserved0', u8), ('pulse_until', u64)])
MENU_FIELDS = ('p1_menus', 'p1_open', 'p1_target', 'leader_selector', 'guests_open', 'party_request', 'quickening',
               'suppressed', 'leader_pending', 'party_pulse', 'presenter_live', 'leader_gate',
               'quickening_command_seat', 'quickening_confirm_seat')
MenuReport = S('MenuReport', [(n, u8) for n in MENU_FIELDS] + [('reserved', u8 * 2)])
BeatInputs = S('BeatInputs', [('epoch', u32), ('generation', u64), ('gates', u32), ('facts', u32), ('phase', u8),
                              ('switched_on', u8), ('live', u8), ('paused', u8), ('target_info', u8),
                              ('tree_active', u8), ('prompt_state', u8), ('driver', u8), ('kbm_seat', u8),
                              ('iso_flags', u32), ('p1_slot', u8), ('guest_devices', u8), ('seat_playing', u8),
                              ('act_pressed', u32 * 4), ('act_held', u32 * 4), ('pad_pressed', u32 * 4),
                              ('menus', MenuReport)])
COUNTERS = ['published', 'masked', 'pulsed', 'axes_zeroed', 'wheel_consumed', 'look_cleared'] + \
    [f'refused{k}' for k in range(1, 8)] + ['passed', 'pulses_started', 'pulses_p1', 'pulses_dropped',
                                            'decide_reads', 'probe_failed']
Counters = S('Counters', [(n, u64) for n in COUNTERS])
DebugView = S('DebugView', [('pulse_until', u64), ('next_free', u64), ('target_info_next', u64), ('pulse', u16),
                            ('pending', u16 * 4), ('source', u8), ('rr', u8), ('p1_prompt_ready', u8), ('decide', u8),
                            ('last_p1_kind', u32)])
POLICY_IN = ('active', 'p1_menus', 'control', 'passthrough', 'suppressed', 'p1_open', 'any_open', 'leader_selector',
             'leader_pending', 'party_request', 'quickening', 'desktop_guest', 'presenter', 'leader_gate')
PolicyInputs = S('PolicyInputs', [(n, u8) for n in POLICY_IN])
CONTRACT = ('p1_replacement', 'other_command', 'directional_owner', 'p1_lb', 'party_blocked', 'quickening_command',
            'quickening_confirm', 'leader_replacement', 'mouse_look', 'gate', 'gate_leader', 'p1_stock')
Contract = S('Contract', [(n, u8) for n in CONTRACT])
Policy = S('Policy', [('p1_mask', u32), ('other_mask', u32), ('flags', u32), ('refused', u32)])
P1_IN = ('replacement', 'menu_open', 'target_page', 'leader_selector', 'driver', 'confirm_edge', 'cancel_edge',
         'prompt_state', 'target_info', 'target_info_due', 'confirm_native')
P1Inputs = S('P1Inputs', [(n, u8) for n in P1_IN])
LAYOUTS = [FwRecord, MenuReport, BeatInputs, Counters, DebugView, PolicyInputs, Contract, Policy, P1Inputs]
Ctx = iso.Ctx

# Semantic ids and mask bits (bit = id - 0x0B).
COMMAND, CONFIRM, CANCEL, PARTY, LB, RB, PAUSE, UP, RIGHT, DOWN, LEFT = (0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x14,
                                                                        0x17, 0x18, 0x19, 0x1A)


def M(*ids):
    return sum(1 << (i - 0x0B) for i in ids)


DIRS, VERTICAL, HORIZONTAL = M(UP, RIGHT, DOWN, LEFT), M(UP, DOWN), M(LEFT, RIGHT)
F_ACTIVE, F_GATE, F_ALL_PADS, F_NATIVE, F_HOST, F_ANY = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20
F_AXES, F_WHEEL, F_LOOK, F_DESKTOP_P1 = 0x40, 0x80, 0x100, 0x200
REFUSED = (1 << 1) | (1 << 3) | (1 << 4)
REFUSED_BATTLE = (1 << 1) | (1 << 4)
QB_COMMAND, QB_CONFIRM, QB_CHAIN = 0x01, 0x02, 0x80
LEADER_VERTICAL, LEADER_ALL = 0, 1
P1P_NONE, P1P_CANCEL, P1P_CONFIRM, P1P_TARGET_INFO = range(4)
PULSE_MS, GAP_MS, STALE_MS, TARGET_INFO_MS = 65, 40, 250, 500
# ilc_action bits.
A_UP, A_DOWN, A_LEFT, A_RIGHT, A_PREV, A_NEXT = 8, 9, 10, 11, 12, 13
A_QUICKENING, A_CONFIRM, A_CANCEL, A_PARTY, A_PAUSE = 14, 15, 16, 17, 18
ISO_GUARD, ISO_SUBST, ISO_NATIVE, ISO_DRIVER, ISO_RELEASE = 0x01, 0x02, 0x40, 0x80, 0x20
FIREWALL = 0x10
EPOCH = 7
RUN_ORIGINAL, RESUME = 0, 1
H = {sid: k for k, sid in enumerate(('FW-01', 'FW-02', 'FW-03', 'FW-04', 'FW-05', 'FG-01', 'FG-02', 'FG-03', 'FG-04',
                                     'FG-05', 'FG-06', 'FG-07'))}
ROWS = tuple(H)

# Game facts (VAs, pinned base 0x120000).
GET_BUTTON, GET_AXIS, DESKTOP_BUTTON, DESKTOP_AXIS = 0x79D670, 0x79D730, 0x79D650, 0x79D6A0
GET_BUTTON_RET, GET_AXIS_RET, RETURN_ZERO = 0x79D686, 0x79D765, 0x28FD6C
CB_TREE, CB_LEADER, CB_EMPTY = 0x279E20, 0x2985D0, 0x5C5230
UI_MANAGER, ACTIVE_MENU, PAUSE_A, PAUSE_B, SETTINGS = 0x209BE80, 0x208EE48, 0x209AD28, 0x209AD30, 0x1F82D20
CAN_USE, DESCRIPTOR, OUTPUT, REGISTRY = 0x209A2AA, 0x209A2B0, 0x2B59D00, 0x2098E10

SIGNATURES = {
    'ILCT_FwSizes': ([P(u32), u32], u32),
    'ILCT_FwDerive': ([P(PolicyInputs), P(Contract)], None),
    'ILCT_FwMasks': ([P(Contract), u32, P(Policy)], None),
    'ILCT_FwCompose': ([P(PolicyInputs), P(Policy)], None),
    'ILCT_FwP1Pulse': ([P(P1Inputs), P(u32)], u32),
    'ILCT_FwGuestPulse': ([u32, u32, i32], u32),
    'ILCT_FwReset': ([], None), 'ILCT_FwAttach': ([], i32),
    'ILCT_FwPublish': ([P(FwRecord)], None), 'ILCT_FwWithdraw': ([], None), 'ILCT_FwRecord': ([P(FwRecord)], i32),
    'ILCT_FwBeat': ([P(BeatInputs), u64, P(FwRecord)], None),
    'ILCT_FwStats': ([P(Counters)], None), 'ILCT_FwDebug': ([P(DebugView)], None),
    'ILCT_FwInvoke': ([u32, P(Ctx), u32], i32),
    'ILCT_FwFakeHost': ([u64], None), 'ILCT_FwFakeP1': ([u64], None),
    'ILCT_FwPostMenus': ([P(MenuReport)], None), 'ILCT_FwConfigure': ([ctypes.c_char_p], None),
    'ILCT_FwStart': ([u64, u64], None), 'ILCT_FwStep': ([u64], None), 'ILCT_FwStop': ([], None),
    'ILCT_FwSetImageBase': ([u64], None), 'ILCT_FwDecide': ([], u32), 'ILCT_FwDecideSwapKnob': ([], i32),
    'ILCT_FwPromptReady': ([], i32), 'ILCT_FwPromptWhy': ([], u32), 'ILCT_FwFg07Last': ([], u64), 'ILCT_FwRow': ([ctypes.c_char_p], i32),
    'ILCT_FwSlotAddress': ([], u64),
    'ILCT_IxSessionPublish': ([P(Record)], None), 'ILCT_IxSessionWithdraw': ([], None),
    'ILCT_EdgeReset': ([u32], None), 'ILCT_EdgeRecord': ([u32, P(u32)], None), 'ILCT_EdgeTestReset': ([], None),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def counters(d) -> Counters:
    c = Counters()
    d.ILCT_FwStats(ctypes.byref(c))
    return c


def record(flags=F_ACTIVE, p1_mask=0, other_mask=0, refused=0, pulse=0, until=0, epoch=EPOCH, slot=0xFF):
    r = FwRecord()
    r.head.epoch, r.head.flags, r.head.generation = epoch, flags, 3
    r.p1_mask, r.other_mask, r.refused, r.pulse, r.pulse_until, r.p1_slot = (p1_mask, other_mask, refused, pulse,
                                                                             until, slot)
    return r


class FwWorld:
    """The twin installed, LIVE, armed, every subsystem bit, the firewall attached."""

    def __init__(self):
        self.w = gt.world()
        self.twin = self.w.twin
        self.d = declare(self.w.d)

    def setup(self):
        d = self.d
        self.w.setup()
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_FwReset()
        assert d.ILCT_FwAttach() == 1
        d.ILCT_PatchHeartbeat(EPOCH)


_fw = None


def fw_world() -> FwWorld:
    global _fw
    if _fw is None:
        _fw = FwWorld()
    return _fw


def native_dll():
    return fw_world().d


# ---- layouts -----------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class LayoutTest(unittest.TestCase):
    def test_struct_mirrors(self):
        d = native_dll()
        n = len(LAYOUTS)
        out = (u32 * n)()
        self.assertEqual(d.ILCT_FwSizes(out, n), n)
        for k, cls in enumerate(LAYOUTS):
            self.assertEqual(out[k], ctypes.sizeof(cls), cls.__name__)


# ---- the policy ----------------------------------------------------------------------------------------------------
def pin(**kw) -> PolicyInputs:
    p = PolicyInputs()
    p.active = 1
    p.presenter, p.leader_gate = 1, 1  # the co-op menus are drawn and the leader selector can be shown
    for k, v in kw.items():
        setattr(p, k, v)
    return p


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.d = native_dll()

    def compose(self, **kw) -> Policy:
        out = Policy()
        self.d.ILCT_FwCompose(ctypes.byref(pin(**kw)), ctypes.byref(out))
        return out

    def masks(self, c: Contract, rule=LEADER_VERTICAL) -> Policy:
        out = Policy()
        self.d.ILCT_FwMasks(ctypes.byref(c), rule, ctypes.byref(out))
        return out

    def test_composition_table(self):
        field = dict(p1_menus=1, control=1)
        rows = [
            # label, inputs, P1 mask, other mask, flags beyond ACTIVE
            ('co-op inactive', dict(active=0, **field), 0, 0, None),
            ('no co-op menus yet: only the guest walls', dict(control=1), 0, M(COMMAND), 0),
            ('field, every menu closed', field, M(COMMAND, CONFIRM, CANCEL, UP, DOWN), M(COMMAND) | DIRS,
             F_GATE | F_AXES | F_WHEEL),
            ('field, P1 menu open', dict(p1_open=1, any_open=1, **field),
             M(COMMAND, CONFIRM, CANCEL, LB, RB, PARTY) | DIRS, M(COMMAND, LB, RB, PARTY) | DIRS,
             F_GATE | F_AXES | F_WHEEL),
            ('field, P1 leader selector', dict(p1_open=1, any_open=1, leader_selector=1, **field),
             M(COMMAND, CONFIRM, CANCEL, RB, PARTY) | DIRS, M(COMMAND, LB, RB, PARTY) | DIRS,
             F_GATE | F_AXES | F_WHEEL),
            ('field, a guest menu open', dict(any_open=1, **field), M(COMMAND, CONFIRM, CANCEL, RB, PARTY) | DIRS,
             M(COMMAND, LB, RB, PARTY) | DIRS, F_GATE | F_AXES | F_WHEEL),
            ('cutscene (no control)', dict(p1_menus=1), M(UP, DOWN), M(COMMAND) | DIRS, F_GATE | F_WHEEL),
            ('native menu, modal, pause, town, chocobo, Quickening or a guest driving a menu (passthrough)',
             dict(passthrough=1, p1_open=1, any_open=1, **field), 0, 0, F_GATE),
            ('suppressed for an external frontend', dict(suppressed=1, p1_open=1, any_open=1, **field), M(UP, DOWN),
             M(COMMAND) | DIRS, F_GATE | F_WHEEL),
            ('KO-leader recovery pending, native screen', dict(passthrough=1, leader_pending=1, **field),
             M(COMMAND, CONFIRM, CANCEL, UP, DOWN), M(COMMAND) | DIRS, F_GATE | F_AXES | F_WHEEL),
            ('native Party request in progress', dict(party_request=1, **field),
             M(COMMAND, CONFIRM, CANCEL, UP, DOWN, PARTY), M(COMMAND, PARTY) | DIRS, F_GATE | F_AXES | F_WHEEL),
            ('Quickening with P2 and P3', dict(passthrough=1, quickening=3, **field), M(COMMAND, CONFIRM),
             M(COMMAND, CONFIRM), F_GATE),
            ('drawn menus, the co-op selector cannot be shown', dict(leader_gate=0, **field),
             M(COMMAND, CONFIRM, CANCEL), M(COMMAND), F_GATE | F_AXES),
            ('guest owns keyboard and mouse', dict(desktop_guest=1, **field), M(COMMAND, CONFIRM, CANCEL, UP, DOWN),
             M(COMMAND) | DIRS, F_GATE | F_AXES | F_WHEEL | F_LOOK),
            # P1 on the stock frontends (the co-op menus drawn but not owning P1): a guest's menu keeps only the
            # guests' pads; P1's directions, RB and PARTY reach the stock menus.
            ('P1 on the stock frontends, a guest menu open', dict(any_open=1, control=1), 0,
             M(COMMAND, LB, RB, PARTY) | DIRS, F_WHEEL),
            ('P1 on the stock frontends, a native Party request in progress', dict(any_open=1, control=1,
             party_request=1), M(PARTY) | DIRS | M(RB), M(COMMAND, LB, RB, PARTY) | DIRS, F_WHEEL),
        ]
        for label, kw, p1, other, extra in rows:
            with self.subTest(label):
                p = self.compose(**kw)
                if extra is None:
                    self.assertEqual((p.p1_mask, p.other_mask, p.flags, p.refused), (0, 0, 0, 0))
                    continue
                self.assertEqual(hex(p.p1_mask), hex(p1), 'P1 mask')
                self.assertEqual(hex(p.other_mask), hex(other), 'other mask')
                self.assertEqual(hex(p.flags), hex(F_ACTIVE | extra), 'flags')
                self.assertEqual(p.refused, (REFUSED if kw.get('leader_gate', 1) else REFUSED_BATTLE) if extra & F_GATE
                                 else 0)
        # Invariants over every input combination: P1's START, the guests' START and P1's LEFT/RIGHT with every menu
        # closed are never masked; a guest pad's COMMAND is withheld whenever native input is not authorised.
        for bits in range(1 << 13):
            kw = {n: (bits >> k) & 1 for k, n in enumerate(POLICY_IN[1:])}
            kw['quickening'] = 3 if kw['quickening'] else 0
            p = self.compose(**kw)
            self.assertFalse(p.p1_mask & M(PAUSE) or p.other_mask & M(PAUSE), kw)
            if not kw['any_open'] and not kw['p1_open']:
                self.assertFalse(p.p1_mask & HORIZONTAL, kw)
            if not kw['passthrough']:
                self.assertTrue(p.other_mask & M(COMMAND), kw)
            menus = kw['p1_menus'] and kw['presenter']
            self.assertEqual(bool(p.flags & F_GATE), bool(menus), kw)
            self.assertEqual(p.refused, (REFUSED if kw['leader_gate'] else REFUSED_BATTLE) if menus else 0, kw)
            if not kw['presenter']:
                # Nothing the player cannot see owns P1's input: only a native Quickening's guest links.
                links = M(COMMAND, CONFIRM) if kw['quickening'] else 0
                self.assertEqual(p.p1_mask & ~links, 0, kw)
                self.assertFalse(p.flags & (F_GATE | F_AXES | F_WHEEL), kw)

    def test_without_a_presenter_the_co_op_menus_own_nothing(self):
        for kw in (dict(p1_menus=1, control=1), dict(p1_menus=1, control=1, leader_pending=1),
                   dict(p1_menus=1, control=1, passthrough=1, leader_pending=1)):
            with self.subTest(**kw):
                p = self.compose(presenter=0, **kw)
                self.assertEqual((p.p1_mask, p.flags & F_GATE, p.refused), (0, 0, 0), 'every stock frontend is P1s')
                self.assertEqual(p.other_mask & M(COMMAND), 0 if kw.get('passthrough') else M(COMMAND),
                                 'the guest wall stays')

    def test_p1_on_the_stock_frontends_keeps_its_directions_rb_and_party(self):
        guest = dict(any_open=1, control=1)
        p = self.compose(**guest)
        self.assertEqual(p.p1_mask & (DIRS | M(RB, PARTY)), 0, "P1's pad drives the stock menu")
        self.assertEqual(p.other_mask & (DIRS | M(LB, RB, PARTY)), DIRS | M(LB, RB, PARTY), 'the guests stay walled')
        for label, kw, held in (
                ('control: the co-op menus own P1', dict(p1_menus=1), DIRS | M(RB, PARTY)),
                ("P1's own co-op menu open", dict(p1_open=1), DIRS | M(RB, PARTY)),
                ('a native Party request runs', dict(party_request=1), DIRS | M(RB, PARTY)),
                ('no guest menu open', dict(any_open=0), 0)):
            with self.subTest(label):
                p = self.compose(**dict(guest, **kw))
                self.assertEqual(p.p1_mask & (DIRS | M(RB, PARTY)), held)
        # Over every input: P1's directions are withheld for a menu only while the co-op menus own P1, P1's own menu
        # is open or a Party request runs (never for a guest's menu alone while P1 is on the stock frontends).
        for bits in range(1 << 13):
            kw = {n: (bits >> k) & 1 for k, n in enumerate(POLICY_IN[1:])}
            kw['quickening'] = 3 if kw['quickening'] else 0
            p = self.compose(**kw)
            owned = kw['presenter'] and (kw['p1_menus'] or kw['p1_open'] or kw['party_request'] or kw['leader_pending'])
            if not owned:
                self.assertEqual(p.p1_mask & (DIRS | M(RB, PARTY)), 0, kw)

    def test_the_leader_select_passes_while_the_co_op_selector_cannot_be_shown(self):
        p = self.compose(p1_menus=1, control=1, leader_gate=0)
        self.assertEqual(p.refused, REFUSED_BATTLE, 'id 3 (the KO path too) reaches the stock leader select')
        self.assertFalse(p.p1_mask & M(UP, DOWN), "P1's UP/DOWN drive the stock leader select")
        p = self.compose(p1_menus=1, control=1, leader_gate=1)
        self.assertEqual(p.refused, REFUSED)
        self.assertEqual(p.p1_mask & M(UP, DOWN), M(UP, DOWN), 'they open the co-op selector instead')

    def test_player1_parity_oracle(self):
        """Every one of the 1,024 contracts: only P1's LEFT/RIGHT differ from the old four-direction leader rule,
        exactly when the leader rule holds and no co-op menu owns the directions."""
        differ = 0
        for bits in range(1 << len(CONTRACT)):
            c = Contract(*[(bits >> k) & 1 for k in range(len(CONTRACT))])
            new, old = self.masks(c), self.masks(c, LEADER_ALL)
            self.assertEqual((new.other_mask, new.flags, new.refused), (old.other_mask, old.flags, old.refused))
            diff = new.p1_mask ^ old.p1_mask
            p1_directions = c.directional_owner and not c.p1_stock
            expected = HORIZONTAL if c.leader_replacement and not p1_directions else 0
            self.assertEqual(diff, expected, CONTRACT)
            if diff:
                self.assertEqual(old.p1_mask & diff, diff, 'the old rule withheld them')
                differ += 1
        # Leader on and P1's directions not owned by a menu: directional off (2^10 of the other switches) or on with
        # P1 on the stock frontends (2^9).
        self.assertEqual(differ, 1536)

    def p1(self, **kw) -> tuple[int, int]:
        p = P1Inputs()
        p.replacement = 1
        p.target_info_due = 1
        for k, v in kw.items():
            setattr(p, k, v)
        kind = u32()
        return self.d.ILCT_FwP1Pulse(ctypes.byref(p), ctypes.byref(kind)), kind.value

    def test_player1_pulses(self):
        self.assertEqual(self.p1(cancel_edge=1), (CANCEL, P1P_CANCEL), 'B -> native CANCEL')
        # Prompt readiness: a highlighted prompt (state 1) now interacts, a selected one (2) as before.
        self.assertEqual({s: self.p1(confirm_edge=1, prompt_state=s)[0] for s in (0, 1, 2, 3)},
                         {0: 0, 1: CONFIRM, 2: CONFIRM, 3: CONFIRM})
        self.assertEqual(self.p1(cancel_edge=1, confirm_edge=1, prompt_state=2), (CANCEL, P1P_CANCEL))
        # Bug 3 (0.1.60): the game read P1's A itself (CONFIRM unmasked last beat): never a second, pulsed CONFIRM.
        self.assertEqual({s: self.p1(confirm_edge=1, prompt_state=s, confirm_native=1)[0] for s in (0, 1, 2)},
                         {0: 0, 1: 0, 2: 0})
        self.assertEqual(self.p1(cancel_edge=1, confirm_native=1), (CANCEL, P1P_CANCEL), 'B still pulses')
        for kw in (dict(replacement=0), dict(menu_open=1), dict(driver=1)):
            self.assertEqual(self.p1(cancel_edge=1, confirm_edge=1, prompt_state=2, **kw), (0, P1P_NONE), kw)
        # TARGET INFO: only on P1's target page, outside the leader selector, while the window is up and due.
        page = dict(menu_open=1, target_page=1, target_info=1)
        self.assertEqual(self.p1(**page), (CANCEL, P1P_TARGET_INFO))
        for kw in (dict(target_page=0), dict(leader_selector=1), dict(target_info=0), dict(target_info_due=0),
                   dict(driver=1), dict(replacement=0)):
            self.assertEqual(self.p1(**{**page, **kw}), (0, P1P_NONE), kw)
        self.assertEqual(self.p1(**page, cancel_edge=1, confirm_edge=1, prompt_state=2), (CANCEL, P1P_TARGET_INFO),
                         'an open menu consumes A and B itself')

    def test_guest_pulse_priority(self):
        order = [(A_CANCEL, CANCEL), (A_PARTY, PARTY), (A_CONFIRM, CONFIRM), (A_PREV, LB), (A_NEXT, RB),
                 (A_UP, UP), (A_DOWN, DOWN), (A_LEFT, LEFT), (A_RIGHT, RIGHT)]
        g = lambda pressed, held=0, native=1: self.d.ILCT_FwGuestPulse(pressed, held, native)  # noqa: E731
        for a, sem in order:
            self.assertEqual(g(1 << a), sem)
            self.assertEqual(g(1 << a, native=0), 0, 'nothing reaches the field without a native screen')
        for i, (a, sem) in enumerate(order):
            for b, _ in order[i + 1:]:
                self.assertEqual(g((1 << a) | (1 << b)), sem, 'the higher one wins')
        self.assertEqual(g(1 << A_PAUSE, native=0), PAUSE, 'a guest START pauses through a pulse')
        self.assertEqual(g((1 << A_PAUSE) | (1 << A_CANCEL)), PAUSE)
        for a, opp in ((A_UP, A_DOWN), (A_DOWN, A_UP), (A_LEFT, A_RIGHT), (A_RIGHT, A_LEFT)):
            self.assertEqual(g(1 << a, held=1 << opp), 0, 'an opposite pair held together cancels out')
        self.assertEqual(g(1 << A_UP, held=1 << A_LEFT), UP)


# ---- one beat --------------------------------------------------------------------------------------------------
def beat_in(**kw) -> BeatInputs:
    b = BeatInputs()
    b.epoch, b.generation, b.gates, b.facts, b.phase = EPOCH, 3, GATE['WORLD'], FACT['CONTROL'], PHASE['FIELD']
    b.switched_on, b.live, b.p1_slot, b.seat_playing = 1, 1, 0xFF, 0b111
    menus = kw.pop('menus', {})
    b.menus.presenter_live, b.menus.leader_gate = 1, 1  # a running, drawn menu layer unless a test says otherwise
    for k, v in kw.items():
        if k in ('act_pressed', 'act_held', 'pad_pressed'):
            arr = getattr(b, k)
            for seat, value in v.items():
                arr[seat - 1] = value
        else:
            setattr(b, k, v)
    for k, v in menus.items():
        setattr(b.menus, k, v)
    return b


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class BeatTest(unittest.TestCase):
    def setUp(self):
        self.d = native_dll()
        self.d.ILCT_FwReset()
        self.now = 10_000

    def beat(self, dt=20, **kw) -> FwRecord:
        self.now += dt
        out = FwRecord()
        self.d.ILCT_FwBeat(ctypes.byref(beat_in(**kw)), self.now, ctypes.byref(out))
        return out

    def debug(self) -> DebugView:
        v = DebugView()
        self.d.ILCT_FwDebug(ctypes.byref(v))
        return v

    def test_inactive_states_publish_a_stock_record(self):
        for kw in (dict(live=0), dict(switched_on=0), dict(phase=PHASE['NO_WORLD']), dict(phase=PHASE['STOPPED'])):
            r = self.beat(menus=dict(p1_menus=1), **kw)
            self.assertEqual((r.head.flags, r.p1_mask, r.other_mask, r.refused, r.pulse), (0, 0, 0, 0, 0), kw)
        r = self.beat(menus=dict(p1_menus=1), phase=PHASE['LOADING'])
        self.assertTrue(r.head.flags & F_GATE, 'the gate holds through loads')

    def test_which_pad_object_is_p1s(self):
        cases = [
            (0, 0xFF, F_ALL_PADS),                              # no isolation record or nothing routed
            (ISO_RELEASE, 0xFF, F_ALL_PADS),                    # isolation releasing: stock pads
            (ISO_GUARD | ISO_NATIVE, 1, F_NATIVE),              # P1 on its own XInput pad, guests masked
            (ISO_GUARD | ISO_SUBST, 0xFF, F_HOST),              # P1 on an SDL pad, fed through the host
            (ISO_GUARD | ISO_NATIVE | ISO_DRIVER, 1, F_HOST),   # a guest drives a native menu through the host
            (ISO_GUARD, 0xFF, F_ANY),                           # P1 on keyboard: a pulse reads on every pad
        ]
        for iso_flags, slot, expected in cases:
            r = self.beat(iso_flags=iso_flags, p1_slot=slot, driver=1 if iso_flags & ISO_DRIVER else 0)
            ident = r.head.flags & (F_ALL_PADS | F_NATIVE | F_HOST | F_ANY)
            self.assertEqual(ident, expected, hex(iso_flags))
            self.assertEqual(r.p1_slot, slot if expected == F_NATIVE else 0xFF)
        self.assertTrue(self.beat(kbm_seat=1, iso_flags=ISO_GUARD).head.flags & F_DESKTOP_P1)
        self.assertFalse(self.beat(kbm_seat=2, iso_flags=ISO_GUARD).head.flags & F_DESKTOP_P1)
        self.assertFalse(self.beat(kbm_seat=0, iso_flags=ISO_GUARD).head.flags & F_DESKTOP_P1, 'nobody: other mask')
        self.assertTrue(self.beat(kbm_seat=0, iso_flags=0, guest_devices=0).head.flags & F_DESKTOP_P1)
        self.assertTrue(self.beat(kbm_seat=2).head.flags & F_LOOK, 'a guest on keyboard and mouse')

    def test_p1_axes_only_while_the_stock_tree_is_the_active_menu(self):
        menus = dict(p1_menus=1)
        self.assertFalse(self.beat(menus=menus).head.flags & F_AXES)
        self.assertTrue(self.beat(menus=menus, tree_active=1).head.flags & F_AXES)
        self.assertFalse(self.beat(menus=menus, tree_active=1, paused=1).head.flags & F_AXES)

    def test_a_pulse_is_high_65_ms_then_low_40_ms(self):
        menus = dict(p1_menus=1)
        r = self.beat(menus=menus, act_pressed={1: 1 << A_CANCEL}, pad_pressed={1: 1 << 1})
        start = self.now
        self.assertEqual((r.pulse, r.pulse_until), (CANCEL, start + PULSE_MS))
        self.assertEqual(self.debug().last_p1_kind, P1P_CANCEL)
        # A second B queued during the pulse waits for the low gap.
        times = []
        for _ in range(12):
            r = self.beat(menus=menus, act_pressed={1: 1 << A_CANCEL} if not times else {},
                          pad_pressed={1: 2} if not times else {})
            times.append((self.now - start, r.pulse))
        highs = [t for t, p in times if p]
        self.assertEqual([t for t, p in times if p and t < PULSE_MS], [t for t, _ in times if t < PULSE_MS])
        second = [t for t in highs if t >= PULSE_MS]
        self.assertTrue(second and second[0] >= PULSE_MS + GAP_MS, times)
        self.assertEqual(counters(self.d).pulses_p1, 2)

    def test_player1_keyboard_edges_make_no_pulse(self):
        r = self.beat(menus=dict(p1_menus=1), act_pressed={1: 1 << A_CANCEL})
        self.assertEqual(r.pulse, 0, "the keyboard's own CANCEL reaches the game; a pulse would double it")

    def test_confirm_on_a_highlighted_prompt_and_the_menu_layers_query(self):
        menus = dict(p1_menus=1)
        r = self.beat(menus=menus, prompt_state=0)
        self.assertTrue(r.p1_mask & M(CONFIRM), 'no prompt: P1 A is withheld (the co-op menu takes it)')
        r = self.beat(menus=menus, prompt_state=1)
        self.assertEqual(self.d.ILCT_FwPromptReady(), 1)
        self.assertFalse(r.p1_mask & M(CONFIRM), 'bug 3: a highlighted prompt leaves P1 real CONFIRM to the game')
        self.assertTrue(r.p1_mask & M(COMMAND) and r.p1_mask & M(CANCEL), 'only CONFIRM is given back')
        r = self.beat(menus=menus, prompt_state=1, act_pressed={1: 1 << A_CONFIRM}, pad_pressed={1: 1})
        self.assertEqual(r.pulse, 0, 'the game read A itself: no doubled CONFIRM')
        self.assertEqual(self.d.ILCT_FwPromptWhy(), 0)
        self.beat(dt=300, menus=menus, prompt_state=0)
        self.assertEqual(self.d.ILCT_FwPromptReady(), 0)
        self.assertEqual(self.d.ILCT_FwPromptWhy(), 0x10, 'PB_NO_PROMPT')
        r = self.beat(menus=menus, prompt_state=0, act_pressed={1: 1 << A_CONFIRM}, pad_pressed={1: 1})
        self.assertEqual(r.pulse, 0, 'no prompt: A is the co-op menu')
        self.assertTrue(r.p1_mask & M(CONFIRM))
        self.beat(menus=dict(p1_menus=1, p1_open=1), prompt_state=2)
        self.assertEqual(self.d.ILCT_FwPromptReady(), 0, 'an open menu takes A')
        self.assertEqual(self.d.ILCT_FwPromptWhy(), 0x08, 'PB_MENU_OPEN')

    def test_a_prompt_that_appears_with_the_press_is_pulsed_once(self):
        menus = dict(p1_menus=1)
        self.beat(menus=menus, prompt_state=0)
        # A pressed while the record the game read still withheld CONFIRM: the pulse delivers it, once.
        r = self.beat(menus=menus, prompt_state=2, act_pressed={1: 1 << A_CONFIRM}, pad_pressed={1: 1})
        self.assertEqual((r.pulse, self.debug().last_p1_kind), (CONFIRM, P1P_CONFIRM))
        self.assertFalse(r.p1_mask & M(CONFIRM))

    def test_the_prompt_grace_covers_a_blip(self):
        menus = dict(p1_menus=1)
        self.beat(menus=menus, prompt_state=2)
        r = self.beat(dt=20, menus=menus, prompt_state=0)      # the game's can_use blips to 0 for a frame
        self.assertEqual(self.d.ILCT_FwPromptReady(), 1, 'within PROMPT_GRACE_MS')
        self.assertFalse(r.p1_mask & M(CONFIRM))
        r = self.beat(dt=240, menus=menus, prompt_state=0)     # 260 ms since the last ready beat
        self.assertEqual(self.d.ILCT_FwPromptReady(), 0)
        self.assertTrue(r.p1_mask & M(CONFIRM))
        # The grace never outlives the menu or replacement terms: P1 opens its menu, the prompt is not ready at once.
        self.beat(menus=menus, prompt_state=2)
        self.beat(dt=20, menus=dict(p1_menus=1, p1_open=1), prompt_state=0)
        self.assertEqual(self.d.ILCT_FwPromptReady(), 0)
        self.beat(dt=20, menus=menus, prompt_state=0)
        self.assertEqual(self.d.ILCT_FwPromptReady(), 0, 'the menu reset the grace')

    def test_a_quickening_link_keeps_confirm_withheld(self):
        r = self.beat(menus=dict(p1_menus=1, quickening=QB_CONFIRM | QB_CHAIN), prompt_state=2)
        self.assertTrue(r.p1_mask & M(CONFIRM))

    def test_target_info_close_composes_with_the_target_card(self):
        """P1 on its co-op target page (where co-op draws its own TARGET INFO card): the native window is closed
        with CANCEL at most every 0.5 s while it stays open, never on the leader selector."""
        menus = dict(p1_menus=1, p1_open=1, p1_target=1)
        pulses = []
        for _ in range(60):  # 1.2 s at 20 ms beats
            r = self.beat(menus=menus, target_info=1)
            if r.pulse and (not pulses or pulses[-1][1] != r.pulse_until):
                pulses.append((self.now, r.pulse_until))
        starts = [t for t, _ in pulses]
        self.assertEqual(len(starts), 3, starts)
        self.assertTrue(all(b - a >= TARGET_INFO_MS for a, b in zip(starts, starts[1:])), starts)
        self.d.ILCT_FwReset()
        for _ in range(40):
            r = self.beat(menus=dict(menus, leader_selector=1), target_info=1)
            self.assertEqual(r.pulse, 0)
        for _ in range(40):
            self.assertEqual(self.beat(menus=menus, target_info=0).pulse, 0)

    def test_guests_drive_native_screens_round_robin(self):
        native = FACT['CONTROL'] | FACT['MENU']
        # P2 and P3 press on the same beat, again and again: they alternate, one pulse at a time.
        sources = []
        for k in range(40):
            r = self.beat(facts=native, act_pressed={2: 1 << A_DOWN, 3: 1 << A_CONFIRM} if k % 3 == 0 else {})
            v = self.debug()
            if r.pulse and (not sources or sources[-1][1] != r.pulse_until):
                sources.append((v.source, r.pulse_until, r.pulse))
        seats = [s for s, _, _ in sources]
        self.assertGreaterEqual(len(seats), 6)
        self.assertTrue(all(a != b for a, b in zip(seats, seats[1:])), seats)
        self.assertEqual({p for s, _, p in sources if s == 2}, {DOWN})
        self.assertEqual({p for s, _, p in sources if s == 3}, {CONFIRM})
        self.assertEqual(counters(self.d).pulses_p1, 0)

    def test_queued_edges_are_dropped_not_replayed(self):
        native = FACT['CONTROL'] | FACT['MENU']
        self.beat(facts=native, act_pressed={2: 1 << A_CANCEL, 3: 1 << A_CANCEL})
        self.assertEqual(self.debug().pending[2], CANCEL, "P3 waits behind P2's pulse")
        r = self.beat(facts=FACT['CONTROL'])  # the native screen closes before P3's turn
        self.assertEqual(self.debug().pending[2], 0)
        self.assertEqual(counters(self.d).pulses_dropped, 1)
        # A PAUSE survives a closing screen, but not past 250 ms; and a seat that drops out loses its edge.
        self.d.ILCT_FwReset()
        self.beat(act_pressed={1: 1 << A_CANCEL}, pad_pressed={1: 2}, menus=dict(p1_menus=1))
        self.beat(act_pressed={2: 1 << A_PAUSE})
        self.assertEqual(self.debug().pending[1], PAUSE)
        r = self.beat(dt=STALE_MS + 1)
        self.assertEqual(self.debug().pending[1], 0, 'stale')
        self.d.ILCT_FwReset()
        self.beat(facts=native, act_pressed={2: 1 << A_CANCEL, 3: 1 << A_CONFIRM})
        self.beat(facts=native, seat_playing=0b011)
        self.assertEqual(self.debug().pending[2], 0, 'P3 parked')
        del r

    def test_no_presenter_no_gate(self):
        r = self.beat(menus=dict(p1_menus=1, presenter_live=0))
        self.assertEqual((r.head.flags & F_GATE, r.refused, r.p1_mask), (0, 0, 0), 'every stock frontend is P1s')
        r = self.beat(menus=dict(p1_menus=1, presenter_live=0), act_pressed={1: 1 << A_CANCEL}, pad_pressed={1: 2})
        self.assertEqual(r.pulse, 0, 'P1 needs no parity pulse: its buttons are native')
        r = self.beat(dt=200, menus=dict(p1_menus=1))
        self.assertTrue(r.head.flags & F_GATE, 'instrument: with the presenter the gate arms')

    def test_a_native_quickening_chain_passes_p1_through_and_pulses_each_guests_link(self):
        chain = dict(p1_menus=1, quickening=QB_CHAIN | QB_COMMAND | QB_CONFIRM, quickening_command_seat=2,
                     quickening_confirm_seat=3)
        r = self.beat(menus=chain)
        self.assertEqual(r.p1_mask, M(COMMAND, CONFIRM), "P1's own buttons pass, except the guests' links")
        self.assertEqual(r.other_mask, M(COMMAND, CONFIRM))
        r = self.beat(dt=200, menus=chain, act_pressed={2: 1 << A_QUICKENING})
        self.assertEqual((r.pulse, self.debug().source), (COMMAND, 2), "P2's link: COMMAND on P1's device")
        r = self.beat(dt=200, menus=chain, act_pressed={3: 1 << A_QUICKENING})
        self.assertEqual((r.pulse, self.debug().source), (CONFIRM, 3), "P3's link: CONFIRM")
        r = self.beat(dt=200, menus=chain, act_pressed={3: 1 << A_CONFIRM})
        self.assertEqual(r.pulse, 0, 'only the QUICKENING binding is a link')
        solo = dict(p1_menus=1, quickening=QB_CHAIN)
        r = self.beat(dt=200, menus=solo, act_pressed={2: 1 << A_QUICKENING})
        self.assertEqual((r.p1_mask, r.pulse), (0, 0), 'no guest in a linked slot: P1 presses every link natively')
        r = self.beat(dt=200, menus=dict(p1_menus=1), act_pressed={2: 1 << A_QUICKENING})
        self.assertEqual(r.pulse, 0, 'instrument: no chain, no link')
        self.assertTrue(r.p1_mask & M(COMMAND, CONFIRM), 'instrument: outside the chain P1 is replaced')

    def test_a_guest_driving_a_native_menu_owns_it_alone(self):
        """Composition with input isolation's menu driver: while P2 drives its dialog or shop, the policy is
        native (the host carries P2's pad, P1 waits), no pulse is made from anyone and queued edges are dropped."""
        menus = dict(p1_menus=1)
        native = FACT['CONTROL'] | FACT['MENU'] | FACT['MODAL']
        self.beat(facts=native, act_pressed={3: 1 << A_CONFIRM})
        r = self.beat(facts=native, driver=1, iso_flags=ISO_GUARD | ISO_NATIVE | ISO_DRIVER, p1_slot=0, menus=menus,
                      act_pressed={1: 1 << A_CANCEL, 3: 1 << A_CANCEL}, pad_pressed={1: 2})
        self.assertEqual((r.p1_mask, r.other_mask), (0, 0), 'native: the driver reads its own pad')
        self.assertTrue(r.head.flags & F_HOST and not r.head.flags & F_NATIVE)
        self.assertTrue(r.head.flags & F_GATE, 'the stock battle/leader frontends stay shut')
        self.assertEqual(r.pulse, 0)
        self.assertEqual(list(self.debug().pending), [0, 0, 0, 0])
        for _ in range(10):
            r = self.beat(facts=native, driver=1, iso_flags=ISO_GUARD | ISO_DRIVER, menus=menus,
                          act_pressed={2: 1 << A_PAUSE})
            self.assertEqual(r.pulse, 0)
        # The drive ends: pulses come back (a fresh edge, nothing from during the drive).
        r = self.beat(facts=native, act_pressed={2: 1 << A_CANCEL})
        self.assertEqual(r.pulse, CANCEL)


# ---- the co-op thread on synthetic game memory --------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class GatherTest(unittest.TestCase):
    def setUp(self):
        self.m = fw_world()
        self.m.setup()
        d = self.d = self.m.d
        self.img = Image()
        d.ILCT_FwSetImageBase(self.img.base)
        d.ILCT_FwStart(self.img.base, gt.NOW)
        d.ILCT_EdgeTestReset()
        d.ILCT_EdgeReset(1)
        self.seq = 1
        self.edges()
        self.session()
        self.img.put_global(UI_MANAGER, struct.pack('<Q', 0))
        for va in (PAUSE_A, PAUSE_B, SETTINGS, ACTIVE_MENU):
            self.img.put_global(va, struct.pack('<Q', 0))
        self.img.put_global(CAN_USE, b'\0')
        self.img.put_global(DESCRIPTOR, bytes(24))
        self.img.put_global(OUTPUT, bytes(24))
        self.img.put_global(REGISTRY, bytes(0x30))
        self.now = gt.NOW

    def tearDown(self):
        self.d.ILCT_FwStop()
        self.d.ILCT_IxSessionWithdraw()
        self.d.ILCT_IsoWithdraw()
        self.d.ILCT_FwReset()
        self.img.close()

    def session(self, facts=FACT['CONTROL'], phase=PHASE['FIELD'], playing=(0, 1, 2), epoch=EPOCH):
        r = Record()
        r.head.epoch, r.head.flags, r.head.generation = epoch, GATE['WORLD'], 3
        r.facts, r.phase = facts, phase
        for s in playing:
            r.seat[s].flags = 0x0003
        self.d.ILCT_IxSessionPublish(ctypes.byref(r))

    def edges(self, pad=None, act=None):
        masks = (u32 * 28)()
        for seat, v in (pad or {}).items():
            masks[(seat - 1) * 7 + 0] = v
        for seat, v in (act or {}).items():
            masks[(seat - 1) * 7 + 3] = v
        self.d.ILCT_EdgeRecord(self.seq, masks)
        self.seq += 1

    report = None  # the menu layer's report, posted again before every beat (as a running menu layer does)

    def step(self, post=True) -> FwRecord:
        self.now += 20
        if post and self.report is not None:
            self.d.ILCT_FwPostMenus(ctypes.byref(self.report))
        self.d.ILCT_FwStep(self.now)
        out = FwRecord()
        self.assertEqual(self.d.ILCT_FwRecord(ctypes.byref(out)), 1)
        return out

    def menus(self, **kw):
        m = MenuReport()
        m.presenter_live, m.leader_gate = 1, 1
        for k, v in kw.items():
            setattr(m, k, v)
        self.report = m
        self.d.ILCT_FwPostMenus(ctypes.byref(m))

    def test_a_menu_report_counts_only_while_it_is_posted(self):
        # A menu layer that stops posting must hand P1's stock frontends back: the gate holds for at most
        # MENU_REPORT_BEATS beats after the last post, and a new epoch or a switch edge drops the report at once.
        self.menus(p1_menus=1)
        self.assertTrue(self.step().head.flags & F_GATE)
        gated = [bool(self.step(post=False).head.flags & F_GATE) for _ in range(4)]
        self.assertEqual(gated, [True, False, False, False], 'counted for the two beats after its post, then stale')
        self.assertTrue(self.step().head.flags & F_GATE, 'posting again restores it')
        self.session(epoch=EPOCH + 1)
        self.d.ILCT_FwPostMenus(ctypes.byref(self.report))  # posted in the old epoch, before the boundary
        self.assertFalse(self.step(post=False).head.flags & F_GATE, 'a new epoch drops the report')
        self.assertTrue(self.step().head.flags & F_GATE)
        self.d.ILCT_FwStop()
        self.d.ILCT_FwStart(self.img.base, self.now)
        self.assertFalse(self.step(post=False).head.flags & F_GATE, 'stop/start drops the report')

    def test_a_co_op_switch_edge_drops_the_report(self):
        # A report posted just before co-op is switched off must not survive into the beats after it comes back.
        import test_coop_control as tc
        d = self.d
        for name in ('ILCT_CtlReset', 'ILCT_CtlKill', 'ILCT_CtlStep', 'ILCT_CtlState', 'ILCT_IsoReset',
                     'ILCT_PatchApplySubsystems'):
            if name in tc.SIGNATURES:
                fn = getattr(d, name)
                fn.argtypes, fn.restype = tc.SIGNATURES[name]
        d.ILCT_CtlReset()
        d.ILCT_IsoReset()
        try:
            self.menus(p1_menus=1)
            self.assertTrue(self.step().head.flags & F_GATE)
            self.d.ILCT_FwPostMenus(ctypes.byref(self.report))
            self.assertTrue(self.step(post=False).head.flags & F_GATE, 'instrument: one beat after its post it counts')
            d.ILCT_CtlKill(1)
            d.ILCT_CtlStep(self.now)
            self.assertEqual(d.ILCT_CtlState(), tc.RELEASING)
            d.ILCT_FwPostMenus(ctypes.byref(self.report))   # posted for this beat, then the switch is seen off
            self.assertEqual(self.step(post=False).head.flags, 0, 'switched off: stock')
            d.ILCT_CtlStep(self.now + 260)                   # the release cap: off
            d.ILCT_CtlKill(0)
            d.ILCT_CtlStep(self.now + 280)                   # back on
            self.assertEqual(d.ILCT_CtlState(), tc.ON)
            d.ILCT_PatchApplySubsystems(0xFF)                # what the UI tick's safe point re-applies
            r = self.step(post=False)
            self.assertTrue(r.head.flags & F_ACTIVE)
            self.assertFalse(r.head.flags & F_GATE, 'the switch edge dropped the report posted one beat before')
        finally:
            d.ILCT_CtlReset()
            d.ILCT_IsoReset()
            d.ILCT_PatchApplySubsystems(0xFF)

    def test_a_party_request_pulse_is_queued_once_for_its_seat(self):
        self.menus(p1_menus=1, party_request=1)
        self.step()
        self.report.party_pulse = 2
        r = self.step()
        v = DebugView()
        self.d.ILCT_FwDebug(ctypes.byref(v))
        self.assertEqual((r.pulse, v.source), (PARTY, 2), "outside any native screen, from seat 2, on P1's device")
        self.assertTrue(r.p1_mask & M(PARTY), 'the mask stays: only the pulse reads high')
        started = counters(self.d).pulses_started
        self.report.party_pulse = 0
        for _ in range(3):  # the stale copy of the report still counts for two beats: it must not pulse again
            self.d.ILCT_FwPostMenus(ctypes.byref(self.menus_copy(party_pulse=2)))
            self.d.ILCT_FwPostMenus(ctypes.byref(self.report))
            self.step(post=False)
            self.step(post=False)
        self.assertEqual(counters(self.d).pulses_started, started)
        self.now += 300
        self.report.party_pulse = 1
        r = self.step()
        self.d.ILCT_FwDebug(ctypes.byref(v))
        self.assertEqual((r.pulse, v.source), (PARTY, 1), "P1's own request uses its parity slot")
        self.now += 300
        self.report.party_pulse = 3
        self.session(playing=(0, 1))
        self.assertEqual(self.step().pulse, 0, 'a seat that is not playing asks for nothing')

    def menus_copy(self, **kw):
        m = MenuReport()
        ctypes.memmove(ctypes.byref(m), ctypes.byref(self.report), ctypes.sizeof(m))
        for k, v in kw.items():
            setattr(m, k, v)
        return m

    def test_the_decide_setting_swaps_the_pads_confirm_and_cancel(self):
        settings = self.img.alloc(0x100)
        self.img.put_global(SETTINGS, struct.pack('<Q', settings))
        for value, decide, knob in ((0, 0, 1), (1, 1, 0), (7, 1, 0), (0, 0, 1)):
            self.img.p32(settings + 0x64, value)
            self.now += 300
            self.step()
            self.assertEqual((self.d.ILCT_FwDecide(), self.d.ILCT_FwDecideSwapKnob()), (decide, knob), value)
        self.img.put_global(SETTINGS, struct.pack('<Q', 0))
        self.now += 300
        self.step()
        self.assertEqual((self.d.ILCT_FwDecide(), self.d.ILCT_FwDecideSwapKnob()), (1, 0), 'unreadable: Cross')
        reads = counters(self.d).decide_reads
        self.step()
        self.assertEqual(counters(self.d).decide_reads, reads, 'read every 0.25 s, not every beat')

    def test_native_state_reads(self):
        self.menus(p1_menus=1)
        ui = self.img.alloc(0x12000)
        self.img.put_global(UI_MANAGER, struct.pack('<Q', ui))
        r = self.step()
        self.assertTrue(r.head.flags & F_ACTIVE and r.head.flags & F_GATE)
        self.assertEqual(r.refused, REFUSED)
        # The stock command tree as the active menu -> P1's axes; a pause clears it.
        self.img.p64(ui + 0xAB30, self.img.va(CB_TREE))
        self.img.put_global(ACTIVE_MENU, struct.pack('<Q', ui + 0xAB30))
        self.assertTrue(self.step().head.flags & F_AXES)
        self.img.put_global(PAUSE_B, struct.pack('<Q', 0x1234))
        r = self.step()
        self.assertFalse(r.head.flags & F_AXES)
        self.assertEqual(r.p1_mask, 0, 'the pause screen is native')

    def handle(self, index, gen=5):
        return index | (gen << 20)

    def registry_entry(self, index, unit, gen=5, mimic_asleep=False, actor=True, name_key=0):
        reg = self.img.va(REGISTRY)
        table = self.img.alloc(0x100)
        entry = self.img.alloc(0x200)
        self.img.p64(reg, 1)
        self.img.p64(reg + 8, table)
        self.img.put(reg + 0x10, b'\x01')
        self.img.p32(reg + 0x20, 64)
        self.img.p32(table, 64)
        self.img.p64(table + 8 + 8 * index, entry)
        self.img.put(entry + 0x16, struct.pack('<H', gen))
        self.img.put(entry + 0x0E, bytes([unit]))
        self.img.put(entry + 0x102, struct.pack('<h', name_key))
        if actor and not mimic_asleep:
            a = self.img.alloc(0x800)
            self.img.p64(entry + 0x30, a)
            self.img.p32(a + 0x08, self.handle(index, gen))
        if mimic_asleep:
            actor = self.img.alloc(0x800)
            ext = self.img.alloc(0x10)
            self.img.p64(entry + 0x30, actor)
            self.img.p32(actor + 0x08, self.handle(index, gen))
            self.img.p64(actor + 0x6A0, ext)
            self.img.put(ext, b'\x20')
        return entry

    def test_p1s_prompt_and_a_foe(self):
        self.menus(p1_menus=1)
        self.step()
        # (label, unit type, mimic asleep, state, ready, entry has its own actor, name key)
        cases = [('NPC, highlighted (output)', 3, False, 1, True, True, 0), ('foe', 1, False, 1, False, True, 0),
                 ('foe, selected', 7, False, 2, False, True, 0), ('mimic asleep as a chest', 4, True, 1, True, True, 0),
                 # Bug 3 (0.1.60): a chest prompt in registry 0 never reads as a foe.
                 ('chest named as one (434)', 4, False, 2, True, True, 434),
                 ('chest object without an actor of its own', 4, False, 2, True, False, 0)]
        for k, (label, unit, asleep, state, ready, actor, key) in enumerate(cases):
            with self.subTest(label):
                h = self.handle(10 + k)
                self.registry_entry(10 + k, unit, mimic_asleep=asleep, actor=actor, name_key=key)
                self.img.put_global(OUTPUT, bytes(16) + struct.pack('<II', state, h))
                r = self.step()
                self.assertEqual(self.d.ILCT_FwPromptReady(), 1 if ready else 0)
                self.assertEqual(bool(r.p1_mask & M(CONFIRM)), not ready, 'CONFIRM to the game exactly when ready')
                if not ready:
                    self.assertEqual(self.d.ILCT_FwPromptWhy(), 0x20, 'PB_FOE')
                self.edges()
                self.edges(pad={1: 1}, act={1: 1 << A_CONFIRM})
                r = self.step()
                self.assertEqual(r.pulse, 0, 'ready: the game read A itself; a foe: A is the co-op menu')
                self.img.put_global(OUTPUT, bytes(24))
                self.now += 300
                self.step()
        # The descriptor wins when the scan published a usable target (state >= 2).
        h = self.handle(30)
        self.registry_entry(30, 3)
        self.img.put_global(OUTPUT, bytes(24))
        self.img.put_global(CAN_USE, b'\1')
        self.img.put_global(DESCRIPTOR, bytes(12) + struct.pack('<II', 2, h))
        self.step()
        self.assertEqual(self.d.ILCT_FwPromptReady(), 1)
        # A highlighted descriptor (state 1) counts too: the keyboard's Enter interacts there (bug 3).
        self.now += 300
        self.img.put_global(DESCRIPTOR, bytes(12) + struct.pack('<II', 1, h))
        self.step()
        self.assertEqual(self.d.ILCT_FwPromptReady(), 1)

    def test_records_and_edges_through_the_seams(self):
        # Isolation's record decides P1's device; guests' edges arrive through the input seam (first call seeds).
        rec = iso.IsoRecord()
        rec.epoch, rec.flags, rec.generation, rec.p1_slot = EPOCH, ISO_GUARD | ISO_NATIVE, 3, 2
        self.d.ILCT_IsoPublish(ctypes.byref(rec))
        self.session(facts=FACT['CONTROL'] | FACT['MENU'])
        r = self.step()
        self.assertEqual((r.head.flags & (F_NATIVE | F_ALL_PADS), r.p1_slot), (F_NATIVE, 2))
        self.edges(act={3: 1 << A_CANCEL})
        r = self.step()
        self.assertEqual(r.pulse, CANCEL)
        v = DebugView()
        self.d.ILCT_FwDebug(ctypes.byref(v))
        self.assertEqual(v.source, 3)
        # A new epoch: presses from before it are never replayed.
        self.now += 200
        self.edges(act={2: 1 << A_CANCEL})
        sr = Record()
        sr.head.epoch, sr.head.flags, sr.facts, sr.phase = EPOCH + 1, GATE['WORLD'], FACT['MENU'], PHASE['FIELD']
        sr.seat[1].flags = 3
        self.d.ILCT_IxSessionPublish(ctypes.byref(sr))
        r = self.step()
        self.assertEqual((r.head.epoch, r.pulse), (EPOCH + 1, 0))
        self.d.ILCT_IxSessionWithdraw()
        self.d.ILCT_FwStep(self.now + 20)
        self.assertEqual(self.d.ILCT_FwRecord(ctypes.byref(FwRecord())), 0, 'no session: withdrawn')


# ---- the handlers natively ------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class HandlerNativeTest(unittest.TestCase):
    def setUp(self):
        self.m = fw_world()
        self.m.setup()
        self.d = self.m.d
        self.d.ILCT_SetFixedClock(gt.NOW)
        self.stack = ctypes.create_string_buffer(0x400)
        self.rsp = (ctypes.addressof(self.stack) + 0x200) & ~0xF
        self.dev_a = ctypes.create_string_buffer(0x470)
        self.dev_b = ctypes.create_string_buffer(0x470)
        self.a, self.b = ctypes.addressof(self.dev_a), ctypes.addressof(self.dev_b)

    def tearDown(self):
        self.d.ILCT_FwFakeHost(0)
        self.d.ILCT_FwFakeP1(0)
        self.d.ILCT_FwWithdraw()
        self.m.twin.reset()

    def ctx(self, sid, **regs) -> Ctx:
        c = Ctx()
        rng = random.Random(sum(map(ord, sid)))
        for n in ('rax', 'rcx', 'rdx', 'rbx', 'rbp', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15'):
            setattr(c, n, rng.getrandbits(64))
        c.rsp, c.rflags, c.index = self.rsp, 0x246, self.m.twin.index[sid]
        for n, v in regs.items():
            setattr(c, n, v)
        return c

    def call(self, sid, epoch=EPOCH, **regs):
        c = self.ctx(sid, **regs)
        before = bytes(c)
        result = self.d.ILCT_FwInvoke(H[sid], ctypes.byref(c), epoch)
        return result, c, before

    def button(self, dev, sem, epoch=EPOCH):
        result, c, _ = self.call('FW-01', epoch=epoch, rcx=dev, rdx=0xDEAD00000000 | sem)
        if result == RUN_ORIGINAL:
            return 'stock'
        self.assertEqual(c.resume, GET_BUTTON_RET)
        return c.rax

    def test_fail_open(self):
        d = self.d
        mask = record(p1_mask=0x1FFFF, other_mask=0x1FFFF, flags=F_ACTIVE | F_ALL_PADS | F_GATE, refused=REFUSED)
        d.ILCT_FwPublish(ctypes.byref(mask))
        self.assertEqual(self.button(self.a, COMMAND), 0, 'instrument: the mask acts')
        for epoch in (EPOCH - 1, EPOCH + 1):
            self.assertEqual(self.button(self.a, COMMAND, epoch=epoch), 0, 'one epoch either side still acts')
        for epoch in (EPOCH - 2, EPOCH + 2, 0):
            self.assertEqual(self.button(self.a, COMMAND, epoch=epoch), 'stock', epoch)
        conditions = {
            'withdrawn': lambda: d.ILCT_FwWithdraw(),
            'inactive record': lambda: d.ILCT_FwPublish(ctypes.byref(record(flags=F_ALL_PADS | F_GATE,
                                                                            p1_mask=0x1FFFF, refused=REFUSED))),
            'subsystem off': lambda: d.ILCT_PatchApplySubsystems(0xFF & ~FIREWALL),
            'DRY': lambda: d.ILCT_PatchSetMode(gt.DRY),
        }
        for label, prepare in conditions.items():
            with self.subTest(label):
                d.ILCT_FwPublish(ctypes.byref(mask))
                d.ILCT_PatchApplySubsystems(0xFF)
                d.ILCT_PatchSetMode(gt.LIVE)
                prepare()
                for sid in ROWS:
                    result, c, before = self.call(sid, rcx=1 if sid.startswith('FG') else self.a, rdx=COMMAND,
                                                  r8=CB_TREE)
                    self.assertEqual(result, RUN_ORIGINAL, sid)
                    self.assertEqual(bytes(c), before, f'{sid}: registers untouched')
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)

    def test_fw01_masks_per_device_and_pulses(self):
        d = self.d
        d.ILCT_FwFakeP1(self.a)
        for k in range(17):
            sem = 0x0B + k
            d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_NATIVE, p1_mask=1 << k)))
            self.assertEqual([self.button(self.a, s) for s in range(0x0B, 0x1C)],
                             [0 if s == sem else 'stock' for s in range(0x0B, 0x1C)], f'P1 bit {k}')
            self.assertEqual({self.button(self.b, s) for s in range(0x0B, 0x1C)}, {'stock'})
            d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_NATIVE, other_mask=1 << k)))
            self.assertEqual([self.button(self.b, s) for s in range(0x0B, 0x1C)],
                             [0 if s == sem else 'stock' for s in range(0x0B, 0x1C)], f'other bit {k}')
            self.assertEqual({self.button(self.a, s) for s in range(0x0B, 0x1C)}, {'stock'})
        for s in (0, 0x0A, 0x1C, 0x100, 0xFFFFFFFF):
            d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_ALL_PADS, p1_mask=0x1FFFF, pulse=s & 0xFFFF,
                                                 until=gt.NOW + 50)))
            self.assertEqual(self.button(self.a, s), 'stock', hex(s))
        # Pulses: P1's device reads 1 (over its mask), until the deadline; another device never does.
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_NATIVE, p1_mask=M(CANCEL), other_mask=M(CANCEL),
                                             pulse=CANCEL, until=gt.NOW + 50)))
        self.assertEqual((self.button(self.a, CANCEL), self.button(self.b, CANCEL)), (1, 0))
        self.assertEqual(self.button(self.a, CONFIRM), 'stock')
        d.ILCT_SetFixedClock(gt.NOW + 50)
        self.assertEqual(self.button(self.a, CANCEL), 0, 'the pulse ended: the mask again')
        d.ILCT_SetFixedClock(gt.NOW)
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_ANY, pulse=PAUSE, until=gt.NOW + 50)))
        self.assertEqual((self.button(self.a, PAUSE), self.button(self.b, PAUSE)), (1, 1), 'P1 without a pad')
        # The host object is P1's while substituting or while a guest drives.
        d.ILCT_FwFakeHost(self.b)
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_HOST, p1_mask=M(UP), other_mask=M(DOWN))))
        self.assertEqual([self.button(self.b, UP), self.button(self.b, DOWN)], [0, 'stock'])
        self.assertEqual([self.button(self.a, UP), self.button(self.a, DOWN)], ['stock', 0])
        self.assertEqual(self.button(0, DOWN), 0, 'a null device is never P1')

    def test_fw02_to_fw05(self):
        d = self.d
        d.ILCT_FwFakeP1(self.a)
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_NATIVE | F_AXES)))
        result, c, _ = self.call('FW-02', rcx=self.a, rdx=1)
        self.assertEqual((result, c.resume, c.xmm[0], c.xmm[1]), (RESUME, GET_AXIS_RET, 0, 0))
        self.assertEqual(self.call('FW-02', rcx=self.b, rdx=1)[0], RUN_ORIGINAL)
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_NATIVE)))
        self.assertEqual(self.call('FW-02', rcx=self.a, rdx=1)[0], RUN_ORIGINAL)
        # Desktop buttons: the desktop owner's mask.
        for flags, mask_field in ((F_ACTIVE | F_DESKTOP_P1, 'p1_mask'), (F_ACTIVE, 'other_mask')):
            r = record(flags=flags)
            setattr(r, mask_field, M(COMMAND))
            d.ILCT_FwPublish(ctypes.byref(r))
            self.assertEqual([self.call('FW-03', rcx=self.a, rdx=i)[0] for i in range(3)], [RESUME, 0, 0])
            r = record(flags=flags ^ F_DESKTOP_P1)
            setattr(r, mask_field, M(COMMAND))
            d.ILCT_FwPublish(ctypes.byref(r))
            self.assertEqual(self.call('FW-03', rcx=self.a, rdx=0)[0], RUN_ORIGINAL)
        # Wheel: consumed and 0 while suppressed.
        struct.pack_into('<i', self.dev_a, 0x1F0, -255)
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_WHEEL)))
        result, c, _ = self.call('FW-04', rcx=self.a)
        self.assertEqual((result, c.xmm[0], struct.unpack_from('<i', self.dev_a, 0x1F0)[0]), (RESUME, 0, 0))
        struct.pack_into('<i', self.dev_a, 0x1F0, 255)
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE)))
        self.assertEqual(self.call('FW-04', rcx=self.a)[0], RUN_ORIGINAL)
        self.assertEqual(struct.unpack_from('<i', self.dev_a, 0x1F0)[0], 255)
        # Mouse look: X/Y cleared, the finalizer still runs.
        struct.pack_into('<ii', self.dev_a, 0x1E8, 40, -12)
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE)))
        self.assertEqual(self.call('FW-05', rcx=self.a)[0], RUN_ORIGINAL)
        self.assertEqual(struct.unpack_from('<ii', self.dev_a, 0x1E8), (40, -12))
        d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE | F_LOOK)))
        self.assertEqual(self.call('FW-05', rcx=self.a)[0], RUN_ORIGINAL)
        self.assertEqual(struct.unpack_from('<ii', self.dev_a, 0x1E8), (0, 0))

    def test_frontend_gate_refuses_exactly_1_3_4(self):
        d = self.d
        armed = record(flags=F_ACTIVE | F_GATE, refused=REFUSED)
        for sid in ('FG-01', 'FG-05', 'FG-06'):
            d.ILCT_FwPublish(ctypes.byref(armed))
            for base in (0, 0x8000, 0xFFFF0000):  # bits above the id are ignored as the game ignores them
                refused = []
                for fid in range(0x20):
                    result, c, before = self.call(sid, rcx=base | fid)
                    if result == RESUME:
                        self.assertEqual((c.resume, c.rax), (RETURN_ZERO, 0))
                        refused.append(fid)
                    else:
                        self.assertEqual(bytes(c), before)
                self.assertEqual(refused, [1, 3, 4], f'{sid} base {base:#x}')
            d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE, refused=REFUSED)))
            self.assertEqual({self.call(sid, rcx=fid)[0] for fid in range(0x20)}, {RUN_ORIGINAL}, 'gate disarmed')
        for sid in ('FG-02', 'FG-03', 'FG-04'):
            d.ILCT_FwPublish(ctypes.byref(armed))
            self.assertEqual({self.call(sid, rcx=fid)[0] for fid in (1, 3, 4, 0x81)}, {RESUME}, sid)
            d.ILCT_FwPublish(ctypes.byref(record(flags=F_ACTIVE)))
            self.assertEqual(self.call(sid, rcx=1)[0], RUN_ORIGINAL)
        before = counters(d).refused7
        d.ILCT_FwPublish(ctypes.byref(armed))
        for cb in (CB_TREE, CB_LEADER, CB_EMPTY, 0):
            result, c, regs = self.call('FG-07', rcx=0x5000 + cb, r8=cb)
            self.assertEqual((result, bytes(c)), (RUN_ORIGINAL, regs), 'never refused, never changed')
        self.assertEqual(counters(d).refused7 - before, 2)
        self.assertEqual(d.ILCT_FwFg07Last(), 0x5000 + CB_LEADER)


# ---- unicorn on the hooked twin --------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME and unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class FirewallEmulationTest(unittest.TestCase):
    TRIALS = 4

    @classmethod
    def setUpClass(cls):
        cls.m = fw_world()
        cls.m.setup()
        cls.d = cls.m.d
        cls.twin = cls.m.twin
        with quiet_faulthandler():  # unicorn's own first-chance faults while it maps the image
            cls.emu = iso.IsoEmulator(cls.twin)
        emu = cls.emu
        cls.p1 = emu.alloc(0x470)
        cls.other = emu.alloc(0x470)
        cls.desk = emu.alloc(0x400)
        for dev in (cls.p1, cls.other):
            emu.uc.mem_write(dev + 0x43C, b'\x01' * 17)  # every button held
            for k, off in enumerate((0x414, 0x420, 0x418, 0x424, 0x42C, 0x430)):
                emu.uc.mem_write(dev + off, struct.pack('<i', 64 + k))
        emu.uc.mem_write(cls.desk + 0x1F4, b'\x01\x01\x01')
        emu.uc.mem_write(cls.desk + 0x1F0, struct.pack('<i', 255))

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_FwFakeP1(0)
        cls.d.ILCT_FwWithdraw()
        cls.twin.reset()

    def arm(self, rec: FwRecord | None, clock=gt.NOW + 5):
        d, emu = self.d, self.emu
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_FwFakeP1(self.p1)
        if rec is None:
            d.ILCT_FwWithdraw()
        else:
            d.ILCT_FwPublish(ctypes.byref(rec))
        emu.refresh()
        emu.tick_clock(clock)
        for sid in ROWS:
            emu.force_gate(self.twin.index[sid], True)
        emu.uc.mem_write(self.twin.addresses.liveness, struct.pack('<IIQI', 1, EPOCH, gt.NOW, 0))

    def original(self, sid, on: bool):
        r = self.twin.rows[self.twin.index[sid]]
        site = TWIN_BASE + r['rva']
        data = checked(r)[:r['hook_len']] if on else ctypes.string_at(site, r['hook_len'])
        self.emu.write_code(site, data)

    def buttons(self, dev):
        return [self.emu.call(GET_BUTTON, dev, sem) & 0xFF for sem in range(0x0B, 0x1C)]

    def test_get_button_with_every_mask_bit(self):
        self.arm(None)
        stock = {dev: self.buttons(dev) for dev in (self.p1, self.other)}
        self.assertEqual(stock[self.p1], [1] * 17, 'instrument: every button held on the stock path')
        for k in range(17):
            for field, masked in (('p1_mask', self.p1), ('other_mask', self.other)):
                rec = record(flags=F_ACTIVE | F_NATIVE)
                setattr(rec, field, 1 << k)
                self.arm(rec)
                for dev in (self.p1, self.other):
                    got = self.buttons(dev)
                    want = [0 if (dev == masked and j == k) else 1 for j in range(17)]
                    self.assertEqual(got, want, f'{field} bit {k} on {"P1" if dev == self.p1 else "other"}')
        # Everything masked; ids outside 0x0B..0x1B answer the game's own 0.
        self.arm(record(flags=F_ACTIVE | F_ALL_PADS, p1_mask=0x1FFFF))
        self.assertEqual(self.buttons(self.other), [0] * 17, 'no guest configured: every pad is P1s')
        self.assertEqual([self.emu.call(GET_BUTTON, self.p1, s) & 0xFF for s in (0, 0x0A, 0x1C, 0x40)], [0] * 4)

    def test_pulses_read_high_on_p1s_device_until_the_deadline(self):
        for dev in (self.p1, self.other):
            self.emu.uc.mem_write(dev + 0x43C, b'\x00' * 17)
        try:
            self.arm(record(flags=F_ACTIVE | F_NATIVE, pulse=CONFIRM, until=gt.NOW + 70))
            self.assertEqual(self.buttons(self.p1), [1 if s == CONFIRM else 0 for s in range(0x0B, 0x1C)])
            self.assertEqual(self.buttons(self.other), [0] * 17)
            self.arm(record(flags=F_ACTIVE | F_NATIVE, pulse=CONFIRM, until=gt.NOW + 70), clock=gt.NOW + 70)
            self.assertEqual(self.buttons(self.p1), [0] * 17, 'low once the deadline passed')
        finally:
            for dev in (self.p1, self.other):
                self.emu.uc.mem_write(dev + 0x43C, b'\x01' * 17)

    def axes(self, dev):
        out = []
        for aid in range(0, 0x14):
            self.emu.call(GET_AXIS, dev, aid)
            out.append(self.emu.uc.reg_read(X.UC_X86_REG_XMM0) & 0xFFFFFFFF)
        return out

    def test_get_axis(self):
        self.arm(None)
        stock, stock_other = self.axes(self.p1), self.axes(self.other)
        self.assertTrue(any(stock), 'instrument: the stock axes read the platform fields')
        self.arm(record(flags=F_ACTIVE | F_NATIVE | F_AXES))
        self.assertEqual(self.axes(self.p1), [0] * len(stock), "P1's stick never drives the stock tree")
        self.assertEqual(self.axes(self.other), stock_other)
        self.arm(record(flags=F_ACTIVE | F_NATIVE))
        self.assertEqual(self.axes(self.p1), stock)

    def test_desktop_button_and_wheel(self):
        def desk_buttons():
            return [self.emu.call(DESKTOP_BUTTON, self.desk, s) & 0xFF for s in (0x0B, 0x0C, 0x0D)]

        def wheel(aid):
            self.emu.call(DESKTOP_AXIS, self.desk, aid)
            return self.emu.uc.reg_read(X.UC_X86_REG_XMM0) & 0xFFFFFFFF

        self.arm(None)
        self.assertEqual(desk_buttons(), [1, 1, 1])
        self.assertNotEqual(wheel(3), 0)
        self.arm(record(flags=F_ACTIVE, other_mask=M(COMMAND)))
        self.assertEqual(desk_buttons(), [0, 1, 1], 'M1 is COMMAND')
        self.arm(record(flags=F_ACTIVE | F_DESKTOP_P1, other_mask=M(COMMAND), p1_mask=M(CANCEL)))
        self.assertEqual(desk_buttons(), [1, 1, 0], 'P1 owns the desktop: P1 mask')
        self.arm(record(flags=F_ACTIVE | F_WHEEL))
        for aid in (3, 6, 9):
            self.emu.uc.mem_write(self.desk + 0x1F0, struct.pack('<i', 255))
            self.assertEqual(wheel(aid), 0)
            self.assertEqual(struct.unpack('<i', self.emu.bytes_at(self.desk + 0x1F0, 4))[0], 0, 'consumed')

    def site(self, sid):
        r = self.twin.rows[self.twin.index[sid]]
        return TWIN_BASE + r['rva'], r

    def run_site(self, sid, state, original):
        emu = self.emu
        address, r = self.site(sid)
        out = lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE)  # noqa: E731
        if original:
            patched = bytes(emu.uc.mem_read(address, r['hook_len']))
            emu.write_code(address, checked(r)[:r['hook_len']])
            try:
                return emu.run(address, lambda x: out(x) or address <= x < address + r['hook_len'], state)
            finally:
                emu.write_code(address, patched)
        return emu.run(address, lambda x: out(x) or address <= x < address + 5, state)

    def states(self, salt):
        for trial in range(self.TRIALS):
            state = pe.random_state(random.Random(0xB3300 + salt * 100 + trial))
            state['gpr']['RSP'] &= ~0xF
            state['gpr']['RSP'] -= 8  # a function entry: the return address is on the stack
            yield trial, state

    def test_dispatcher_refuses_1_3_4_and_every_other_id_runs_the_original(self):
        self.arm(record(flags=F_ACTIVE | F_GATE, refused=REFUSED))
        for sid, salt in (('FG-01', 1), ('FG-05', 2), ('FG-06', 3)):
            address, r = self.site(sid)
            refused = []
            for fid in list(range(0x19)) + [0x8001, 0x8003, 0x8004, 0x8002]:
                for trial, state in self.states(salt):
                    state['gpr']['RCX'] = fid
                    with self.subTest(site=sid, id=fid, trial=trial):
                        b = self.run_site(sid, state, original=False)
                        if b['exit'] == TWIN_BASE + RETURN_ZERO - 0x120000:
                            self.assertEqual(b['gpr']['RSP'], state['gpr']['RSP'], 'the caller frame is intact')
                            self.assertEqual(b['gpr']['RAX'] & 0xFFFFFFFF, 0)
                            self.assertEqual(b['calls'], [])
                            if trial == 0:
                                refused.append(fid & 0x7FFF)
                            continue
                        a = self.run_site(sid, state, original=True)
                        baseline = {x: self.emu.uc.mem_read(x, 1)[0] for x in set(a['final']) | set(b['final'])}
                        gt.compare(self, self.emu, f'{sid} id {fid:#x}', a, b, baseline, state['gpr']['RSP'])
            self.assertEqual(sorted(set(refused)), [1, 3, 4], sid)
            self.assertEqual(refused.count(1), 2, '0x8001 too: the game masks the id the same way')

    def test_every_firewall_site_equals_the_original_when_it_must_not_act(self):
        d = self.d
        conditions = {
            'no record': None,
            'inactive record': record(flags=F_ALL_PADS | F_GATE | F_WHEEL | F_LOOK | F_AXES, p1_mask=0x1FFFF,
                                      other_mask=0x1FFFF, refused=REFUSED),
            'gate and masks clear': record(flags=F_ACTIVE | F_ALL_PADS),
        }
        runs = 0
        for label, rec in conditions.items():
            self.arm(rec)
            for k, sid in enumerate(ROWS):
                for trial, state in self.states(10 + k):
                    g = state['gpr']
                    if sid == 'FW-01':
                        g.update(RCX=self.p1, RDX=CONFIRM)
                    elif sid == 'FW-02':
                        g.update(RCX=self.p1, RDX=2)
                    elif sid == 'FW-03':
                        g.update(RCX=self.desk, RDX=1)
                    elif sid == 'FW-04':
                        g.update(RCX=self.desk)
                    elif sid == 'FW-05':
                        g.update(RBX=self.desk)
                    elif sid == 'FG-07':
                        g.update(R8=TWIN_BASE + CB_TREE - 0x120000)
                    else:
                        g.update(RCX=1)
                    with self.subTest(condition=label, site=sid, trial=trial):
                        a = self.run_site(sid, state, original=True)
                        b = self.run_site(sid, state, original=False)
                        baseline = {x: self.emu.uc.mem_read(x, 1)[0] for x in set(a['final']) | set(b['final'])}
                        gt.compare(self, self.emu, f'{sid} {label}', a, b, baseline, g['RSP'])
                        runs += 1
        d.ILCT_PatchSetMode(gt.LIVE)
        print(f'\nfirewall fail-open: {runs} runs equal to the original bytes')


# ---- the site table ------------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (a copy of the exe)')
class SiteTableTest(unittest.TestCase):
    def test_firewall_rows_and_the_dropped_wait_rows(self):
        rows = {r['id']: r for r in pe.site_rows()}
        for sid in ROWS:
            with self.subTest(sid):
                r = rows[sid]
                self.assertEqual((r['policy'], r['subsystem']), ('PATCH', 'FIREWALL_MENUS'))
                self.assertGreaterEqual(r['hook_len'], 5)
                self.assertFalse(r['flags'] & 0x03, 'INTERIOR or DROPPED')
        calls = {r['id'] for r in pe.site_rows() if r['policy'] == 'PATCH' and pe.call_offset(r) is not None}
        self.assertEqual(set(ROWS) & calls, {'FW-05'})
        for sid in ('WT-01', 'WT-02', 'WT-03'):
            self.assertEqual(rows[sid]['policy'], 'INFO', sid)
            self.assertTrue(rows[sid]['flags'] & 0x02, f'{sid} dropped by decision')
        self.assertEqual(rows['MS-01']['policy'], 'INFO')
        self.assertEqual(len(calls), CALL_ROWS)
        self.assertEqual(sum(1 for r in pe.site_rows() if r['policy'] == 'PATCH'), PATCH_ROWS)
        self.assertEqual(checked(rows['FW-01'])[:6], bytes.fromhex('8D 42 F5 83 F8 10'))
        self.assertEqual(checked(rows['FG-01'])[:5], bytes.fromhex('48 89 5C 24 08'))

    @unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (the image twin)')
    def test_the_continuations_hold_the_bytes_the_handlers_resume_at(self):
        image = pe.Twin.get().baseline
        for va, want in ((GET_BUTTON_RET, b'\xC3'), (GET_AXIS_RET, b'\xC3'), (0x79D666, b'\xC3'),
                         (0x79D6F9, b'\xC3'), (RETURN_ZERO, b'\x33\xC0\xC3')):
            self.assertEqual(image[va - 0x120000:va - 0x120000 + len(want)], want, hex(va))


if __name__ == '__main__':
    unittest.main()
