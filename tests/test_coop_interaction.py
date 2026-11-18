"""Host tests for guest interactions (native/coop/src/interaction). No game run, nothing deployed; the exe is only
read (IVALICE_LEGACY_GAME, a copy) for the image twin and the emulator.

* the rules as our own tables: a guest's press (weapon handling, execution motion, busy states and actions refuse),
  P1's leader for an object interaction, shop-open detection, the forced and reset descriptors, mimic state, the
  slot that names the current object, the stale/torn target check, and the hold (dialog -> shop -> dialog stays
  with the guest, an object without a dialog ends without ever giving the guest the menus, owner and world loss);
* the site table: the NPC guards, the consumer call and the loot call are port rows now, without interior flags;
* the handlers natively on saved machine states with synthetic actor sets (the image twin installed, LIVE, the
  window's game calls replaced by recorded fakes): fail-open, the gateway collision class, the private-pass gates
  and sources, the publisher, the NPC guards, loot ownership, the gateway passes (the follower-gateway rules
  restated: stock pass first, once per guest, never twice for one character, the controlled handle restored, no
  pass while an area change is pending), and the window: object and NPC requests, the stale-target guard
  immediately before the consumer, torn targets, P1 busy, owner/leader busy, blocked states, P1's own trigger first;
* the co-op thread on synthetic game memory: the record (entries and skeletons, generation-checked), presses turned
  into requests (object from the guest's own scan, chest, sleeping mimic, NPC otherwise), the hold after a talk
  and shop with its menu driver, expiry, owner loss;
* pad routing to the guest who started it (input isolation's menu driver): record composition and the device
  bodies (the host carries the guest's pad, P1's own device is masked, the keyboard follows its owner);
* unicorn on the hooked twin: every interaction site equals the original bytes when it must not act; the gateway
  call runs the native pass for P1 then once per guest with the controlled handle swapped and restored; the
  window in the emulator runs the game's consumer with the guest's source and descriptor and restores both, and a
  stale target never reaches the consumer; the NPC accept guard leaves the NPC's state alone and keeps the game's
  own out-of-range path (distance bias included).
"""
from __future__ import annotations

import ctypes
import random
import struct
import unittest

import test_coop_game_thread as gt
import test_coop_input_isolation as iso
import test_coop_patch_engine as pe
from coop_session_support import GATE, PHASE, SEATF, FACT, Image, Record, RecordHead
from test_coop_patch_engine import CALL_ROWS, GAME, NATIVE, PATCH_ROWS, TWIN_BASE, TWIN_SIZE, checked

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


IxEntry = S('IxEntry', [('entry', u64), ('skeleton', u64), ('handle', u32), ('member', u16), ('seat', u8),
                        ('active', u8)])
IxRecord = S('IxRecord', [('head', RecordHead), ('active', u32), ('reserved', u32), ('slot', IxEntry * 3)])  # guest seats P2..P4
IxPrivate = S('IxPrivate', [('resource', u64 * 8), ('text', u64 * 8), ('state', u32 * 8), ('handle', u32 * 8),
                            ('tick', u32), ('mark', u32), ('source_handle', u32), ('epoch', u32)])
Request = S('Request', [('state', u32), ('ticket', u32), ('epoch', u32), ('policy', u32), ('seat', u32),
                        ('source_handle', u32), ('object_handle', u32), ('object_state', u32), ('deadline_ms', u64),
                        ('result', i32), ('target_handle', u32), ('touched', u32), ('generation', u32)])
COUNTER_NAMES = ['steps', 'published', 'presses', 'requests', 'requests_expired'] + [f'r{k}' for k in range(12)] + \
    ['holds'] + [f'end{k}' for k in range(5)] + ['gateway_passes', 'gateway_skipped', 'class_rewritten',
                                                 'private_passes', 'private_finds', 'windows', 'guard_hits',
                                                 'loot_owned', 'menu_opens']
Counters = S('Counters', [(n, u64) for n in COUNTER_NAMES])
DebugView = S('DebugView', [('pass_active', u32), ('pass_slot', u32), ('pass_entry', u64), ('guard_active', u32),
                            ('driver', u32), ('hold_active', u32), ('hold_seat', u32), ('hold_opened', u32),
                            ('hold_target', u32), ('last_end', u32), ('last_result', i32), ('last_policy', u32)])
TargetView = S('TargetView', [('present', u8), ('type', u8), ('script_context', u8), ('reserved', u8), ('flags', u32),
                              ('script', u64), ('script_table', u64)])
HoldState = S('HoldState', [('active', u8), ('seat', u8), ('opened', u8), ('reserved', u8), ('location', u32),
                            ('target_handle', u32), ('reserved2', u32), ('start_ms', u64), ('last_signal_ms', u64)])
HoldSignals = S('HoldSignals', [('talking', u8), ('menu_queued', u8), ('frontend', u8), ('modal', u8),
                                ('owner_ok', u8), ('world_ok', u8), ('reserved', u8 * 2), ('location', u32)])
FakeEvent = S('FakeEvent', [('kind', u32), ('arg', i32), ('controlled', u32), ('guard', u32), ('source', u64),
                            ('descriptor', u32 * 6), ('can_use', u8), ('target_range', u8), ('reserved', u8 * 6),
                            ('target', u64)])
Ctx = iso.Ctx
LAYOUTS = [IxEntry, IxRecord, IxPrivate, Request, Counters, DebugView, TargetView, HoldState, HoldSignals, FakeEvent]

RUN_ORIGINAL, RESUME, CALL_AROUND = 0, 1, 2
IXF_GATEWAYS, IXF_ROUTING, IXF_LOOT = 1, 2, 4
POLICY_LEADER, POLICY_TREASURE, POLICY_NPC = 1, 2, 3
REQ_IDLE, REQ_ARMED, REQ_CLAIMED, REQ_DONE = range(4)
WR = {'NONE': 0, 'CONSUMED': 1, 'TALK': 2, 'STALE': -16, 'BLOCKED': -17, 'P1_BUSY': -18, 'LEADER_BUSY': -19,
      'OWNER_BUSY': -20, 'SOURCE_GONE': -21, 'NO_TARGET': -22, 'TARGET_STALE': -23, 'GUARDS_OFF': -24}
TARGET_OK, TARGET_GONE, TARGET_TORN, TARGET_NO_CONTEXT = range(4)
HOLD_KEEP, HOLD_DONE, HOLD_NEVER_OPENED, HOLD_OWNER_GONE, HOLD_WORLD_CHANGED = range(5)
EV_RESOLVE, EV_GENERIC, EV_EXACT, EV_BUILD, EV_CONSUMER, EV_GATE, EV_AUX, EV_GATEWAY = range(1, 9)
MOVEMENT, PARTY = 0x02, 0x04
EPOCH = 7
NONE32 = 0xFFFFFFFF

# Handler numbers of ILCT_IxInvoke.
H = {sid: k for k, sid in enumerate(('MV-07', 'MV-08', 'IX-01', 'IX-02', 'IX-03', 'IX-04', 'IX-05', 'IX-06', 'IX-07',
                                     'IX-08', 'IX-09', 'IX-10', 'IX-17', 'IX-18', 'IX-19', 'IX-20', 'LT-01'))}
ROWS = tuple(H)

# Game facts (VAs, pinned base 0x120000).
SOURCE, CAN_USE, DESCRIPTOR, TRIGGER = 0x2099D78, 0x209A2AA, 0x209A2B0, 0x209A2C4
CONTROLLED, PENDING_AREA, LOCK_MASK = 0x22C7FE0, 0x1EED064, 0x22CB720
RENDER, SUPPRESSION, EVENTS = 0x22C7F90, 0x2089340, 0x1FD46D4
FRONTEND_ID, FRONTEND_ACTIVE, MENU_QUEUED = 0x2092730, 0x2092734, 0x215F1A0
REGISTRY, STRIDE = 0x2098E10, 0x288
FORCED_DISTANCE, RESET_DISTANCE, READY = 0x3F1999AC, 0x501502F9, 0x01010101
GATEWAY, CONSUMER, RESOLVE, GATE_FN = 0x25B600, 0x268D00, 0x3588B0, 0x26DFB0
EXACT_SCAN, BUILD_HANDLE, GENERIC_SCAN, AUX = 0x25B820, 0x268650, 0x25C170, 0x263990
WEAPON_HANDLING, EXECUTION, WEAPON_DRAWN = 1 << 24, 1 << 46, 1 << 23

SIGNATURES = {
    'ILCT_IxSizes': ([P(u32), u32], u32),
    'ILCT_IxPressAllowed': ([u64, u32, u32], i32), 'ILCT_IxLeaderAllows': ([u64], i32),
    'ILCT_IxShopOpen': ([u32, u32], i32), 'ILCT_IxForced': ([u32, u32, u32, P(u32)], None),
    'ILCT_IxResetDescriptor': ([P(u32)], None), 'ILCT_IxObjectState': ([u32, i32], u32),
    'ILCT_IxCurrentSlot': ([P(u32)], i32), 'ILCT_IxTargetReady': ([P(TargetView), u32], i32),
    'ILCT_IxHoldBegin': ([P(HoldState), u32, u32, u32, u64], None),
    'ILCT_IxHoldStep': ([P(HoldState), P(HoldSignals), u64], u32), 'ILCT_IxHoldDriver': ([P(HoldState)], u32),
    'ILCT_IxReset': ([], None), 'ILCT_IxAttach': ([], i32), 'ILCT_IxStart': ([u64, u64], None),
    'ILCT_IxStep': ([u64], None), 'ILCT_IxStop': ([], None), 'ILCT_IxSetImageBase': ([u64], None),
    'ILCT_IxPress': ([u32], None), 'ILCT_IxPublish': ([P(IxRecord)], None), 'ILCT_IxWithdraw': ([], None),
    'ILCT_IxRecord': ([P(IxRecord)], i32), 'ILCT_IxPrivate': ([u32, P(IxPrivate)], i32),
    'ILCT_IxPublishPrivate': ([u32, P(IxPrivate)], None), 'ILCT_IxRequest': ([], u64),
    'ILCT_IxDebug': ([P(DebugView)], None), 'ILCT_IxStats': ([P(Counters)], None), 'ILCT_IxDriver': ([], u32),
    'ILCT_IxHoldSeats': ([u32], u32), 'ILCT_IxShopOpenNow': ([], i32), 'ILCT_IxRow': ([ctypes.c_char_p], i32),
    'ILCT_IxInvoke': ([u32, P(Ctx), u32], i32), 'ILCT_IxAfter': ([P(Ctx), u32], None),
    'ILCT_IxSetPass': ([u32, u32, u64], None), 'ILCT_IxSetGuard': ([u32], None),
    'ILCT_IxUseFakes': ([i32], None), 'ILCT_IxFakeResolve': ([u32, u64], None), 'ILCT_IxFakeGate': ([i32], None),
    'ILCT_IxFakeAuxiliary': ([u64], None), 'ILCT_IxFakeScan': ([i32, u32, u32, u32, u32], None),
    'ILCT_IxFakeConsumer': ([u32], None), 'ILCT_IxFakeLog': ([P(FakeEvent), u32], u32),
    'ILCT_IxFakeLogClear': ([], None), 'ILCT_IxFakePublish': ([u32, u32, u32, u32, u64, u64], None),
    'ILCT_IxFakePublishResult': ([P(u64)], i32), 'ILCT_IxSessionPublish': ([P(Record)], None),
    'ILCT_IxSessionWithdraw': ([], None), 'ILCT_IsoFakeDriver': ([i32], None),
    'ILCT_IxFakeAuxiliarySwap': ([u64, u64], None),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def counters(d) -> Counters:
    c = Counters()
    d.ILCT_IxStats(ctypes.byref(c))
    return c


def result_index(result: int) -> str:
    return f'r{result if result >= 0 else -result - 13}'


def session_record(seats=None, gates=None, epoch=EPOCH, phase=PHASE['FIELD'], location=0x123, facts=0) -> Record:
    """seats = {s: dict(actor=, handle=, flags=)}; P1 is always in session."""
    r = Record()
    r.head.epoch, r.head.generation = epoch, gt.GEN
    r.head.flags = (GATE['WORLD'] | GATE['LOGICAL'] | GATE['MOVEMENT'] | GATE['INTERACTION'] | GATE['GUESTS_MOVE']) \
        if gates is None else gates
    r.phase, r.location, r.facts = phase, location, facts
    for s in range(4):
        r.seat[s].member, r.seat[s].party_slot = 0xFFFF, 0xFF
    r.seat[0].flags = SEATF['IN_SESSION'] | SEATF['MANUAL']
    for s, v in (seats or {}).items():
        e = r.seat[s]
        e.flags = v.get('flags', SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['ACTOR'] | SEATF['STEERS'] |
                        SEATF['ALIVE'] | SEATF['ALIVE_KNOWN'] | SEATF['COHERENT'])
        e.actor, e.handle, e.member = v['actor'], v['handle'], v.get('member', s)
        e.phys, e.actor_generation = v.get('phys', v['actor'] + 0x800), 1
    return r


def ix_record(slots=(), flags=IXF_GATEWAYS | IXF_ROUTING | IXF_LOOT, epoch=EPOCH) -> IxRecord:
    """slots = [(k, entry, skeleton, handle)]."""
    r = IxRecord()
    r.head.epoch, r.head.flags, r.head.generation = epoch, flags, gt.GEN
    for k, entry, skeleton, handle in slots:
        x = r.slot[k]
        x.entry, x.skeleton, x.handle, x.seat, x.member, x.active = entry, skeleton, handle, k + 1, k + 1, 1
        r.active |= 1 << k
    return r


def handle_of(registry: int, index: int, generation: int) -> int:
    return (generation << 20) | (registry << 16) | index


class LayoutTest(unittest.TestCase):
    @unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++')
    def test_struct_mirrors_match(self):
        d = declare(pe.Twin.get().dll) if GAME else None
        if d is None:
            self.skipTest('requires IVALICE_LEGACY_GAME')
        out = (u32 * 16)()
        n = d.ILCT_IxSizes(out, 16)
        self.assertEqual([out[k] for k in range(n)], [ctypes.sizeof(t) for t in LAYOUTS])


# ---- rules ----------------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME')
class RulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(pe.Twin.get().dll)

    def test_a_guest_press_needs_its_character_free(self):
        d = self.d
        self.assertTrue(d.ILCT_IxPressAllowed(0, 1, 0x4021), 'idle and unarmed')
        self.assertTrue(d.ILCT_IxPressAllowed(0, 0, 0xFFFF), 'standing, no action')
        self.assertTrue(d.ILCT_IxPressAllowed(WEAPON_DRAWN, 1, 0x4021), 'a drawn weapon: the game holsters it')
        self.assertTrue(d.ILCT_IxPressAllowed(0, 1, 0x4124), 'following the leader: walking with the party')
        for flags, state, action, why in ((WEAPON_HANDLING, 1, 0x4021, 'drawing or sheathing'),
                                          (EXECUTION, 1, 0x4021, 'an execution motion'),
                                          (WEAPON_HANDLING, 1, 0x4124, 'following, but drawing or sheathing'),
                                          (0, 2, 0x4021, 'busy state'), (0, 2, 0x4124, 'following, busy state'),
                                          (0, 1, 0x0096, 'an action queued'),
                                          (0, 0x101, 0x4021, 'state read as a whole word'),
                                          (0, 1, 0x14021, 'not a u16 action value')):
            self.assertFalse(d.ILCT_IxPressAllowed(flags, state, action), why)

    def test_the_action_is_the_u16_at_0x714(self):
        """The readers take two bytes: whatever follows the action (+0x716) never makes a free character busy."""
        src = (pe.COOP / 'src' / 'interaction')
        facts = (src / 'interaction_facts.h').read_text(encoding='utf-8')
        self.assertIn('ACTOR_ACTION = 0x714;               // u16', facts)
        self.assertIn('r.u16(actor + facts::ACTOR_ACTION, &action)', (src / 'interaction.cpp').read_text(encoding='utf-8'))
        self.assertIn('load(actor + facts::ACTOR_ACTION, &word)', (src / 'hooks.cpp').read_text(encoding='utf-8'))

    def test_p1s_leader_gate_for_object_interactions(self):
        d = self.d
        for flags in (WEAPON_HANDLING, EXECUTION, WEAPON_HANDLING | EXECUTION):
            self.assertFalse(d.ILCT_IxLeaderAllows(flags))
        for flags in (0, WEAPON_DRAWN, 1 << 37):
            self.assertTrue(d.ILCT_IxLeaderAllows(flags))
        samples = (0, WEAPON_DRAWN, WEAPON_HANDLING, 0, EXECUTION, 0)
        self.assertEqual([bool(d.ILCT_IxLeaderAllows(f)) for f in samples], [True, True, False, True, False, True],
                         'the gate reopens right after each animation')
        self.assertTrue(all(d.ILCT_IxLeaderAllows(0) for _ in range(240)), 'an idle leader never closes it')

    def test_shop_open_detection(self):
        d = self.d
        self.assertEqual([d.ILCT_IxShopOpen(i, a) for i, a in ((10, 1), (10, 0), (3, 1), (0, 1), (10, 2), (11, 1))],
                         [1, 0, 0, 0, 0, 0])

    def test_descriptors(self):
        out = (u32 * 6)()
        h = handle_of(2, 0x41, 3)
        self.d.ILCT_IxForced(h, 2, 0x00000100, out)
        self.assertEqual(list(out), [FORCED_DISTANCE, 2, 0x41, 2, h, 0x00000100 | READY])
        self.d.ILCT_IxResetDescriptor(out)
        self.assertEqual(list(out), [RESET_DISTANCE, NONE32, NONE32, 2, 0, READY])
        self.assertEqual([self.d.ILCT_IxObjectState(s, m) for s, m in ((0, 0), (1, 0), (2, 0), (5, 0), (1, 1), (2, 1))],
                         [0, 0, 2, 5, 10, 10], 'usable from state 2; a sleeping mimic wakes with 10')
        for states, want in (([0] * 8, -1), ([1, 0, 0, 0, 0, 0, 0, 0], 0), ([1, 0, 3, 0, 0, 0, 0, 2], 7),
                             ([0, 0, 3, 0, 0, 0, 0, 0], 2)):
            self.assertEqual(self.d.ILCT_IxCurrentSlot((u32 * 8)(*states)), want)

    def test_the_target_check_mirrors_what_the_consumer_dereferences(self):
        def view(present=1, type_=0, flags=0, script=0x1000, table=0x2000, context=0):
            return TargetView(present, type_, context, 0, flags, script, table)
        cases = [
            (view(present=0), 2, TARGET_GONE, 'no entry at the index: the consumer would read a null target'),
            (view(type_=1, script=0, table=0), 2, TARGET_OK, 'type 1 has no script path'),
            (view(script=0), 2, TARGET_TORN, 'a non-type-1 target without +0x40'),
            (view(flags=0x4, table=0), 2, TARGET_TORN, 'talk state + talk flag: +0x48 is read'),
            (view(flags=0x4, table=0), 3, TARGET_OK, 'another state reads +0x48 only with flag 0x400'),
            (view(flags=0x400, table=0), 3, TARGET_TORN, ''),
            (view(flags=0x400, table=0), 2, TARGET_OK, 'the talk state looks at the talk flag only'),
            (view(flags=0x4, context=0xFF), 2, TARGET_NO_CONTEXT, 'no script context: the request is refused'),
            (view(flags=0x4), 2, TARGET_OK, ''),
        ]
        for v, state, want, why in cases:
            self.assertEqual(self.d.ILCT_IxTargetReady(ctypes.byref(v), state), want, why)

    # The hold, restated: a dialog holds the owner until it closes; a dialog -> shop -> dialog sequence never hands
    # the menus back to P1 in its gaps; an object without a dialog ends after a second without ever driving.
    def hold(self, seat=1, now=1000, location=0x55):
        h = HoldState()
        self.d.ILCT_IxHoldBegin(ctypes.byref(h), seat, 0x123, location, now)
        return h

    def step(self, h, now, talking=0, queued=0, frontend=0, modal=0, owner=1, world=1, location=0x55):
        s = HoldSignals(talking, queued, frontend, modal, owner, world, (u8 * 2)(), location)
        return self.d.ILCT_IxHoldStep(ctypes.byref(h), ctypes.byref(s), now)

    def test_a_dialog_holds_the_owner_until_it_closes(self):
        h = self.hold()
        self.assertEqual(self.d.ILCT_IxHoldDriver(ctypes.byref(h)), 0, 'nothing open yet: P1 keeps the menus')
        self.assertEqual(self.step(h, 1050, talking=1, modal=1), HOLD_KEEP)
        self.assertEqual(self.d.ILCT_IxHoldDriver(ctypes.byref(h)), 1, 'the dialog is up: the guest drives')
        self.assertEqual(self.step(h, 1100), HOLD_KEEP)
        self.assertEqual(self.step(h, 1129), HOLD_KEEP, '79 ms after the last signal')
        self.assertEqual(self.d.ILCT_IxHoldDriver(ctypes.byref(h)), 1)
        self.assertEqual(self.step(h, 1130), HOLD_DONE, '80 ms after the dialog closed')
        self.assertEqual((h.active, self.d.ILCT_IxHoldDriver(ctypes.byref(h))), (0, 0))

    def test_dialog_then_shop_then_dialog_stays_with_the_guest(self):
        h = self.hold(seat=2)
        sequence = [(1020, dict(talking=1, modal=1)), (1060, dict(talking=1)), (1100, dict(queued=1)),
                    (1140, dict()), (1180, dict(frontend=1)), (5000, dict(frontend=1)), (5040, dict()),
                    (5070, dict(talking=1, modal=1)), (5300, dict(modal=1))]
        for now, sig in sequence:
            self.assertEqual(self.step(h, now, **sig), HOLD_KEEP, (now, sig))
            self.assertEqual(self.d.ILCT_IxHoldDriver(ctypes.byref(h)), 2, (now, sig))
        self.assertEqual(self.step(h, 5379), HOLD_KEEP)
        self.assertEqual(self.step(h, 5380), HOLD_DONE)

    def test_an_object_without_a_dialog_never_drives(self):
        h = self.hold()
        for now in range(1000, 2000, 20):
            self.assertEqual(self.step(h, now), HOLD_KEEP)
            self.assertEqual(self.d.ILCT_IxHoldDriver(ctypes.byref(h)), 0)
        self.assertEqual(self.step(h, 2000), HOLD_NEVER_OPENED)

    def test_owner_and_world_loss_end_the_hold_at_once(self):
        for kw, end in ((dict(owner=0), HOLD_OWNER_GONE), (dict(world=0), HOLD_WORLD_CHANGED),
                        (dict(location=0x56), HOLD_WORLD_CHANGED)):
            h = self.hold()
            self.step(h, 1010, talking=1)
            self.assertEqual(self.step(h, 1020, talking=1, **kw), end, kw)
            self.assertEqual(self.d.ILCT_IxHoldDriver(ctypes.byref(h)), 0)
        h = self.hold(location=NONE32)
        self.assertEqual(self.step(h, 1010, talking=1, location=0x77), HOLD_KEEP, 'an unknown area adopts the first')
        self.assertEqual(self.step(h, 1020, talking=1, location=0x78), HOLD_WORLD_CHANGED)


# ---- the site table ------------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (a copy of the exe)')
class SiteTableTest(unittest.TestCase):
    def test_interaction_rows_are_port_rows_without_interior_references(self):
        rows = {r['id']: r for r in pe.site_rows()}
        for sid in ROWS:
            with self.subTest(sid):
                r = rows[sid]
                self.assertEqual(r['policy'], 'PATCH')
                self.assertGreaterEqual(r['hook_len'], 5)
                self.assertFalse(r['flags'] & 0x03, 'INTERIOR or DROPPED')
                self.assertEqual(r['subsystem'], 'PARTY' if sid == 'LT-01' else 'MOVEMENT')
        calls = {r['id'] for r in pe.site_rows() if r['policy'] == 'PATCH' and pe.call_offset(r) is not None}
        self.assertTrue({'MV-07', 'IX-01', 'IX-20', 'LT-01'} <= calls)
        self.assertFalse({'MV-08', 'IX-02', 'IX-17', 'IX-18', 'IX-19'} & calls, 'mid-function rows never call')
        self.assertEqual(len(calls), CALL_ROWS)
        self.assertEqual(sum(1 for r in pe.site_rows() if r['policy'] == 'PATCH'), PATCH_ROWS)
        self.assertEqual(checked(rows['IX-18'])[:8], bytes.fromhex('80 4F 0C 02 0F B6 47 0B'))
        self.assertEqual(checked(rows['IX-20'])[:5], bytes.fromhex('E8 04 4B F5 FF'))


# ---- the handlers natively ------------------------------------------------------------------------------------------
class IxWorld:
    """The twin installed, LIVE, armed, every subsystem bit, interaction attached; the window's game calls faked."""

    def __init__(self):
        self.w = gt.world()
        self.twin = self.w.twin
        self.d = declare(self.w.d)

    def setup(self, fakes=True):
        d = self.d
        self.w.setup()
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_IxReset()
        assert d.ILCT_IxAttach() == 1
        d.ILCT_IxUseFakes(1 if fakes else 0)
        d.ILCT_PatchHeartbeat(EPOCH)
        # A field frame: no area change pending, UI on, nothing suppressed, P1's trigger clear.
        self.put32(PENDING_AREA, 0xFFFFFFFF)
        self.put32(LOCK_MASK, 0)
        self.put32(RENDER, 0x8)
        self.put32(SUPPRESSION, 0)
        self.put32(EVENTS, 0)
        self.twin.poke(TRIGGER, b'\0\0\0\0')

    def put32(self, va, value):
        self.twin.poke(va, struct.pack('<I', value & 0xFFFFFFFF))

    def put64(self, va, value):
        self.twin.poke(va, struct.pack('<Q', value))

    def get(self, va, n):
        return ctypes.string_at(va, n)

    def u32(self, va):
        return struct.unpack('<I', self.get(va, 4))[0]

    def u64(self, va):
        return struct.unpack('<Q', self.get(va, 8))[0]

    def gates(self):
        return {sid for sid in ROWS if self.twin.info(self.twin.index[sid]).gate_open}


_ixw = None


def ix_world() -> IxWorld:
    global _ixw
    if _ixw is None:
        _ixw = IxWorld()
    return _ixw


class Heap:
    """Zeroed, aligned objects in this process that stay alive for the test."""

    def __init__(self):
        self.bufs = []

    def obj(self, size=0x1000) -> int:
        b = ctypes.create_string_buffer(size + 16)
        self.bufs.append(b)
        return (ctypes.addressof(b) + 15) & ~15

    @staticmethod
    def put(address, fmt, *values):
        ctypes.memmove(address, struct.pack('<' + fmt, *values), struct.calcsize('<' + fmt))

    @staticmethod
    def get(address, fmt):
        return struct.unpack('<' + fmt, ctypes.string_at(address, struct.calcsize('<' + fmt)))


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class HandlerNativeTest(unittest.TestCase):
    def setUp(self):
        self.m = ix_world()
        self.m.setup()
        self.d = self.m.d
        self.h = Heap()
        self.g2, self.g3 = self.h.obj(), self.h.obj()       # guests' actor manager entries
        self.k2, self.k3 = self.h.obj(), self.h.obj()       # their skeleton keeps
        self.p1h, self.g2h, self.g3h = 0x00500000, 0x00100001, 0x00100002
        self.d.ILCT_IxPublish(ctypes.byref(ix_record([(0, self.g2, self.k2, self.g2h), (1, self.g3, self.k3,
                                                                                         self.g3h)])))
        self.stack = ctypes.create_string_buffer(0x400)
        self.rsp = (ctypes.addressof(self.stack) + 0x200) & ~0xF
        self.d.ILCT_IxSessionPublish(ctypes.byref(session_record({1: dict(actor=self.h.obj(), handle=self.g2h),
                                                                  2: dict(actor=self.h.obj(), handle=self.g3h)})))

    def tearDown(self):
        self.d.ILCT_IxUseFakes(0)
        self.d.ILCT_IxSessionWithdraw()
        self.m.twin.reset()

    def ctx(self, sid, **regs) -> Ctx:
        c = Ctx()
        rng = random.Random(sum(map(ord, sid)))
        for n in ('rax', 'rcx', 'rdx', 'rbx', 'rbp', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15'):
            setattr(c, n, rng.getrandbits(64))
        c.rsp = self.rsp
        c.rflags = 0x246
        c.index = self.m.twin.index[sid]
        for n, v in regs.items():
            setattr(c, n, v)
        return c

    def invoke(self, sid, c, epoch=EPOCH):
        return self.d.ILCT_IxInvoke(H[sid], ctypes.byref(c), epoch)

    def assert_stock(self, sid, label, epoch=EPOCH, **regs):
        c = self.ctx(sid, **regs)
        before = bytes(c)
        self.assertEqual(self.invoke(sid, c, epoch), RUN_ORIGINAL, f'{sid} {label}')
        self.assertEqual(bytes(c), before, f'{sid} {label}: registers changed')

    def log(self):
        out = (FakeEvent * 64)()
        n = self.d.ILCT_IxFakeLog(out, 64)
        return list(out[:n])

    def test_attach_arms_every_interaction_row(self):
        self.assertEqual(self.m.gates(), set(ROWS))
        self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        self.assertEqual(self.m.gates(), {'LT-01'}, 'loot belongs to the party subsystem')
        self.d.ILCT_PatchApplySubsystems(0xFF & ~PARTY)
        self.assertEqual(self.m.gates(), set(ROWS) - {'LT-01'})
        self.d.ILCT_PatchSetMode(gt.DRY)
        self.assertEqual(self.m.gates(), set())

    def test_record_rows_fail_open(self):
        regs = {'MV-07': {}, 'MV-08': dict(rbx=1, rcx=self.k2), 'IX-01': dict(rcx=0), 'LT-01': dict(rcx=self.g2h)}
        for label, prepare, epoch in (
                ('no record', lambda: self.d.ILCT_IxWithdraw(), EPOCH),
                ('another epoch', lambda: None, EPOCH + 1),
                ('flags clear', lambda: self.d.ILCT_IxPublish(ctypes.byref(ix_record(
                    [(0, self.g2, self.k2, self.g2h)], flags=0))), EPOCH),
                ('no guest', lambda: self.d.ILCT_IxPublish(ctypes.byref(ix_record([]))), EPOCH),
                ('MOVEMENT and PARTY clear', lambda: self.d.ILCT_PatchApplySubsystems(0xFF & ~(MOVEMENT | PARTY)), EPOCH),
                ('DRY', lambda: self.d.ILCT_PatchSetMode(gt.DRY), EPOCH)):
            self.setUp()
            prepare()
            for sid, r in regs.items():
                self.assert_stock(sid, label, epoch=epoch, **r)
            self.assertEqual(self.log(), [], f'{label}: the game was called')

    def test_pass_and_guard_rows_are_stock_outside_our_own_calls(self):
        regs = {'IX-02': dict(rdx=1), 'IX-03': {}, 'IX-04': {}, 'IX-05': dict(rbx=0), 'IX-06': dict(rsi=0),
                'IX-07': dict(rsi=0), 'IX-08': dict(rax=0, r15=0, rbx=self.g2), 'IX-09': dict(rbx=self.g2),
                'IX-10': dict(rbx=self.g2), 'IX-17': {}, 'IX-18': {}, 'IX-19': {}}
        obj = self.h.obj()
        regs['IX-06']['rsi'] = regs['IX-07']['rsi'] = obj
        for sid, r in regs.items():
            self.assert_stock(sid, 'no pass, no guard', **r)
        self.d.ILCT_IxSetPass(1, 0, self.g2)
        self.d.ILCT_IxSetGuard(1)
        self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        for sid, r in regs.items():
            self.assert_stock(sid, 'MOVEMENT clear', **r)

    def test_gateway_collision_class_for_guest_skeletons(self):
        cases = [(1, self.k2, 0), (1, self.k3, 0), (1, self.h.obj(), 1), (0, self.k2, 0), (2, self.k2, 2),
                 (0x10001, self.k2, 0)]
        for bx, rcx, want_bx in cases:
            c = self.ctx('MV-08', rbx=0xDEAD0000 | bx, rcx=rcx)
            self.assertEqual(self.invoke('MV-08', c), RUN_ORIGINAL)
            self.assertEqual(c.rbx & 0xFFFF, want_bx, (bx, hex(rcx)))
            self.assertEqual(c.rbx == 0, (bx & 0xFFFF) == 1 and rcx in (self.k2, self.k3), 'as xor ebx,ebx')
        self.d.ILCT_IxPublish(ctypes.byref(ix_record([(0, self.g2, self.k2, self.g2h)], flags=IXF_ROUTING)))
        self.assert_stock('MV-08', 'gateways off', rbx=1, rcx=self.k2)

    def test_loot_is_owned_for_seat_controlled_handles_only(self):
        back = 0x3110E4
        Heap.put(self.rsp, 'Q', back)
        for handle in (self.g2h, self.g3h):
            c = self.ctx('LT-01', rcx=0xFFFFFFFF00000000 | handle)
            self.assertEqual(self.invoke('LT-01', c), RESUME)
            self.assertEqual((c.rax, c.resume), (1, back))
        self.assert_stock('LT-01', "P1's handle asks the game", rcx=self.p1h)
        self.assert_stock('LT-01', 'unknown handle', rcx=0x00100009)
        self.assert_stock('LT-01', 'no handle', rcx=0)
        self.d.ILCT_IxPublish(ctypes.byref(ix_record([(0, self.g2, self.k2, self.g2h)], flags=IXF_GATEWAYS)))
        self.assert_stock('LT-01', 'loot off', rcx=self.g2h)

    def test_private_pass_gates_and_sources(self):
        d, g2 = self.d, self.g2
        d.ILCT_IxSetPass(1, 0, g2)
        frame = self.h.obj()
        obj = self.h.obj()
        cases = [  # (site, regs, expected result, resume VA or None, register checks)
            ('IX-03', {}, RESUME, 0x25C89F, dict(rax=g2)),
            ('IX-04', {}, RESUME, 0x25C283, dict(rsi=g2)),
            ('IX-05', dict(rbx=0x100000000), RESUME, 0x25C930, {}),
            ('IX-05', dict(rbx=5), RUN_ORIGINAL, None, {}),
            ('IX-08', dict(rax=0x10, r15=0x01, rbx=g2), RESUME, 0x25CC7D, {}),
            ('IX-08', dict(rax=0x10, r15=0x10, rbx=g2), RUN_ORIGINAL, None, {}),
            ('IX-08', dict(rax=0x10, r15=0x01, rbx=self.g3), RUN_ORIGINAL, None, {}),
            ('IX-09', dict(rbx=g2), RESUME, 0x25C3F0, {}),
            ('IX-09', dict(rbx=self.g3), RESUME, 0x25C6B9, {}),
            ('IX-10', dict(rbx=g2), RESUME, 0x25C509, {}),
            ('IX-10', dict(rbx=self.g3), RESUME, 0x25C6A1, {}),
        ]
        for sid, regs, want, resume, checks in cases:
            c = self.ctx(sid, **regs)
            self.assertEqual(self.invoke(sid, c), want, (sid, regs))
            if resume is not None:
                self.assertEqual(c.resume, resume, sid)
            for n, v in checks.items():
                self.assertEqual(getattr(c, n), v, (sid, n))
        # Range gates: the game's own range bit accepts; otherwise the guest as the scan source does.
        for sid, mask, accept in (('IX-06', 4, 0x25D04A), ('IX-07', 2, 0x25D117)):
            for bits, source, want in ((mask, self.g3, RUN_ORIGINAL), (0, g2, RESUME), (0, self.g3, RUN_ORIGINAL),
                                       (mask ^ 6, g2, RESUME)):
                Heap.put(obj + 0x5D, 'B', bits)
                Heap.put(frame + 0x100 - 0x71, 'Q', source)
                c = self.ctx(sid, rsi=obj, rbp=frame + 0x100)
                self.assertEqual(self.invoke(sid, c), want, (sid, bits, hex(source)))
                if want == RESUME:
                    self.assertEqual(c.resume, accept)

    def test_the_publisher_writes_the_guests_slots_and_returns_like_the_game(self):
        self.d.ILCT_IxSetPass(1, 0, self.g2)
        args = self.rsp
        Heap.put(args + 0x28, 'Q', 0x7777)
        c = self.ctx('IX-02', rcx=0xAAAA0000BBBB, rdx=0xFFFFFFFF00000005, r8=0x2, r9=0x9999, rsp=args)
        self.assertEqual(self.invoke('IX-02', c), RESUME)
        self.assertEqual((c.resume, c.rax, c.r10, c.rdx), (0x3DF78F, 0x7777, 5, 0x120000), 'the ret, as stock leaves')
        for slot in (8, -1):
            self.assert_stock('IX-02', f'slot {slot}', rdx=slot & 0xFFFFFFFF, rsp=args)

    def test_npc_guards(self):
        self.d.ILCT_IxSetGuard(1)
        c = self.ctx('IX-17', rdx=0x1234)
        self.assertEqual(self.invoke('IX-17', c), RESUME)
        self.assertEqual((c.resume, c.rdi), (0x25BAF9, 0x1234), 'only `mov rdi,rdx` of the two instructions')
        c = self.ctx('IX-18')
        self.assertEqual(self.invoke('IX-18', c), RESUME)
        self.assertEqual((c.resume, c.rax), (0x25BC8A, 0), 'the distance bias, then the out-of-range branch')
        c = self.ctx('IX-19')
        self.assertEqual(self.invoke('IX-19', c), RESUME)
        self.assertEqual(c.resume, 0x25BE0B)

    def test_dual_scan_runs_one_private_pass_per_guest_before_the_stock_scan(self):
        d = self.d
        d.ILCT_IxFakeResolve(self.g2h, self.g2)
        d.ILCT_IxFakeResolve(self.g3h, self.g3)
        d.ILCT_IxFakePublish(1, 6, handle_of(1, 0x22, 4), 2, 0x5151, 0x7171)
        c = self.ctx('IX-01', rcx=3)
        before = bytes(c)
        self.assertEqual(self.invoke('IX-01', c), RUN_ORIGINAL, 'the stock scan follows for P1')
        self.assertEqual(bytes(c), before, 'with every register as it was')
        log = self.log()
        self.assertEqual([(e.kind, e.arg) for e in log],
                         [(EV_RESOLVE, self.g2h), (EV_GENERIC, 3), (EV_RESOLVE, self.g3h), (EV_GENERIC, 3)])
        self.assertEqual([(e.guard, e.target) for e in log if e.kind == EV_GENERIC], [(1, self.g2), (1, self.g3)],
                         'each pass names its guest')
        v = DebugView()
        d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((v.pass_active, v.pass_entry), (0, 0), 'the pass flag drops after the call')
        for k, handle in ((0, self.g2h), (1, self.g3h)):
            p = IxPrivate()
            self.assertEqual(d.ILCT_IxPrivate(k, ctypes.byref(p)), 1)
            self.assertEqual((p.tick, p.mark, p.source_handle, p.state[6], p.handle[6], p.resource[6], p.text[6]),
                             (1, 1, handle, 2, handle_of(1, 0x22, 4), 0x5151, 0x7171))
        regs = (u64 * 4)()
        self.assertEqual(d.ILCT_IxFakePublishResult(regs), RESUME)
        # An empty pass keeps the last find but ages it; a guest that does not resolve still counts a pass.
        d.ILCT_IxFakePublish(0, 0, 0, 0, 0, 0)
        d.ILCT_IxFakeResolve(self.g3h, 0)
        d.ILCT_IxFakeLogClear()
        self.invoke('IX-01', self.ctx('IX-01', rcx=4))
        self.assertEqual([e.kind for e in self.log()], [EV_RESOLVE, EV_GENERIC, EV_RESOLVE])
        p = IxPrivate()
        d.ILCT_IxPrivate(0, ctypes.byref(p))
        self.assertEqual((p.tick, p.mark, p.state[6]), (2, 1, 2))
        d.ILCT_IxPrivate(1, ctypes.byref(p))
        self.assertEqual((p.tick, p.mark), (2, 1))

    # ---- the gateway passes (restated: the native pass first, once per guest, the handle restored) ----
    def run_gateways(self):
        c = self.ctx('MV-07')
        self.assertEqual(self.invoke('MV-07', c), CALL_AROUND, 'the stock pass runs as the game call')
        d = self.d
        d.ILCT_IxFakeLogClear()
        d.ILCT_IxAfter(ctypes.byref(c), EPOCH)
        return [e.controlled for e in self.log() if e.kind == EV_GATEWAY]

    def test_gateway_passes_name_each_guest_and_restore_the_controlled_handle(self):
        m = self.m
        m.put32(CONTROLLED, self.p1h)
        self.assertEqual(self.run_gateways(), [self.g2h, self.g3h], 'one pass per guest, in seat order')
        self.assertEqual(m.u32(CONTROLLED), self.p1h, 'restored after the passes')
        c = counters(self.d)
        self.assertEqual((c.gateway_passes, c.gateway_skipped), (2, 0))

    def test_gateway_never_runs_twice_for_one_character(self):
        m = self.m
        m.put32(CONTROLLED, self.g2h)  # P2's character leads this frame: the stock pass already was its pass
        self.assertEqual(self.run_gateways(), [self.g3h])
        self.assertEqual(m.u32(CONTROLLED), self.g2h)

    def test_gateway_stops_once_an_area_change_is_pending(self):
        m = self.m
        m.put32(CONTROLLED, self.p1h)
        m.put32(PENDING_AREA, 0x81)
        self.assertEqual(self.run_gateways(), [])
        self.assertEqual(m.u32(CONTROLLED), self.p1h)
        self.assertEqual(counters(self.d).gateway_skipped, 2)

    def test_gateway_without_guests_is_one_stock_call(self):
        self.d.ILCT_IxPublish(ctypes.byref(ix_record([])))
        self.assert_stock('MV-07', 'no guest')
        c = self.ctx('MV-07')
        self.d.ILCT_IxWithdraw()
        self.d.ILCT_IxAfter(ctypes.byref(c), EPOCH)
        self.assertEqual(self.log(), [])

    # ---- the window ----
    def registry(self, reg, entries, flags=1):
        """Registry `reg` in the twin with a table of `entries` (pointers, 0 for a hole)."""
        table = self.h.obj(16 + 8 * len(entries))
        Heap.put(table, 'i', len(entries))
        for k, e in enumerate(entries):
            Heap.put(table + 8 + 8 * k, 'Q', e)
        base = REGISTRY + reg * STRIDE
        self.m.put64(base, 1)
        self.m.put64(base + 8, table)
        self.m.twin.poke(base + 0x10, bytes([flags]))
        self.m.put32(base + 0x20, len(entries))
        return table

    def target(self, type_=0, flags=0x4, script=True, table=True, context=0, range_=0x10):
        t = self.h.obj()
        Heap.put(t + 0x18, 'B', type_)
        Heap.put(t + 0x1C, 'I', flags)
        Heap.put(t + 0x40, 'Q', self.h.obj() if script else 0)
        Heap.put(t + 0x48, 'Q', self.h.obj() if table else 0)
        Heap.put(t + 0x60, 'B', context)
        Heap.put(t + 0x0C, 'B', range_)
        return t

    def actor_entry(self, flags=0, state=1, action=0x4021, physics=True):
        entry, actor = self.h.obj(), self.h.obj()
        Heap.put(entry + 0x30, 'Q', actor)
        Heap.put(entry + 0xB8, 'Q', self.h.obj() if physics else 0)
        Heap.put(actor, 'Q', flags)
        Heap.put(actor + 0x6B4, 'I', state)
        Heap.put(actor + 0x714, 'I', action)
        return entry, actor

    def window_world(self, owner_flags=0, leader_flags=0):
        d, m = self.d, self.m
        self.owner, self.owner_actor = self.actor_entry(flags=owner_flags)
        self.leader, self.leader_actor = self.actor_entry(flags=leader_flags)
        d.ILCT_IxFakeResolve(self.g2h, self.owner)
        d.ILCT_IxFakeResolve(self.p1h, self.leader)
        m.put32(CONTROLLED, self.p1h)
        self.p1_source = 0x5A5A5A5A5A5A
        m.put64(SOURCE, self.p1_source)
        m.twin.poke(CAN_USE, b'\x01')
        self.p1_descriptor = struct.pack('<6I', 0x40000000, 0, 3, 2, 0x00100003, 0x00000100)
        m.twin.poke(DESCRIPTOR, self.p1_descriptor)
        self.back = 0x3141FC
        Heap.put(self.rsp, 'Q', self.back)

    def arm(self, policy, object_handle=0, state=0, epoch=EPOCH, deadline=gt.NOW + 200, seat=1,
            generation=gt.GEN & 0xFFFFFFFF):
        q = Request.from_address(self.d.ILCT_IxRequest())
        q.ticket, q.epoch, q.policy, q.seat, q.source_handle = 99, epoch, policy, seat, self.g2h
        q.object_handle, q.object_state, q.deadline_ms, q.result, q.target_handle, q.touched = \
            object_handle, state, deadline, 0, 0, 0
        q.generation = generation
        q.state = REQ_ARMED
        return q

    def window(self):
        c = self.ctx('IX-20')
        return self.invoke('IX-20', c), c

    def assert_restored(self):
        m = self.m
        self.assertEqual(m.u64(SOURCE), self.p1_source, 'source restored')
        self.assertEqual(m.get(CAN_USE, 1), b'\x01', 'can-use restored')
        self.assertEqual(m.get(DESCRIPTOR, 24), self.p1_descriptor, 'descriptor restored')

    def test_no_request_is_the_games_own_consumer_call(self):
        self.window_world()
        res, c = self.window()
        self.assertEqual(res, RUN_ORIGINAL)
        self.assertEqual(self.log(), [])

    def test_object_request_feeds_the_guests_target_and_restores_everything(self):
        self.window_world()
        t = self.target(type_=0, flags=0x400)
        self.registry(2, [0, 0, t])
        h = handle_of(2, 2, 5)
        self.d.ILCT_IxFakeResolve(h, t)
        q = self.arm(POLICY_LEADER, h, 3)
        res, c = self.window()
        self.assertEqual((res, c.resume, c.rax), (RESUME, self.back, 0), "the window took this frame's consumer call")
        self.assertEqual((q.state, q.result, q.target_handle, q.touched), (REQ_DONE, WR['CONSUMED'], h, 1))
        log = self.log()
        self.assertEqual([e.kind for e in log], [EV_GATE, EV_RESOLVE, EV_RESOLVE, EV_RESOLVE, EV_CONSUMER,
                                                 ], [(e.kind, hex(e.arg)) for e in log])
        self.assertEqual(log[0].arg, 0, 'the gate without its descriptor-byte terms')
        call = log[-1]
        self.assertEqual(call.source, self.owner, "the guest's entry is the source for the call")
        self.assertEqual(list(call.descriptor), [FORCED_DISTANCE, 2, 2, 3, h, 0x00000100 | READY])
        self.assertEqual((call.can_use, call.target, call.target_range), (1, t, 0x12), 'in range for this call only')
        self.assert_restored()
        self.assertEqual(Heap.get(t + 0x0C, 'B')[0], 0x10, "the target's range byte restored")

    def test_the_stale_target_guard_runs_immediately_before_the_consumer(self):
        h = handle_of(2, 2, 5)
        for label, entries, resolved, registry_flags in (
                ('index past the table', [0, 0], None, 1),
                ('hole in the table', [0, 0, 0], None, 1),
                ('registry disabled', None, None, 0),
                ('handle generation names another entry', None, 'other', 1)):
            with self.subTest(label):
                self.setUp()
                self.window_world()
                t = self.target()
                self.registry(2, entries if entries is not None else [0, 0, t], flags=registry_flags)
                self.d.ILCT_IxFakeResolve(h, self.h.obj() if resolved == 'other' else t)
                q = self.arm(POLICY_LEADER, h, 2)
                res, c = self.window()
                self.assertEqual((res, q.result, q.touched), (RESUME, WR['TARGET_STALE'], 1))
                self.assertNotIn(EV_CONSUMER, [e.kind for e in self.log()], 'the consumer was called')
                self.assert_restored()

    def test_torn_targets_never_reach_the_consumer(self):
        h = handle_of(1, 0, 2)
        for label, kw, state in (('no +0x40', dict(script=False), 2), ('talk without +0x48', dict(table=False), 2),
                                 ('no script context', dict(context=0xFF), 2)):
            with self.subTest(label):
                self.setUp()
                self.window_world()
                t = self.target(**kw)
                self.registry(1, [t])
                self.d.ILCT_IxFakeResolve(h, t)
                q = self.arm(POLICY_LEADER, h, state)
                self.window()
                self.assertEqual(q.result, WR['TARGET_STALE'])
                self.assertNotIn(EV_CONSUMER, [e.kind for e in self.log()])
                self.assert_restored()

    def test_a_target_the_consumer_removes_is_not_touched_afterwards(self):
        self.window_world()
        t = self.target()
        self.registry(1, [t])
        h = handle_of(1, 0, 2)
        self.d.ILCT_IxFakeResolve(h, t)
        self.d.ILCT_IxFakeConsumer(2 | 4)  # removes the target and clears its range byte
        q = self.arm(POLICY_TREASURE, h, 2)
        self.window()
        self.assertEqual(q.result, WR['CONSUMED'])
        self.assertEqual(Heap.get(t + 0x0C, 'B')[0], 0, 'nothing written to a removed target')
        self.assert_restored()

    def test_npc_request_runs_the_exact_scan_from_the_guest_with_the_guards_armed(self):
        self.window_world()
        npc = self.target(flags=0x4)
        self.registry(3, [0, npc])
        h = handle_of(3, 1, 9)
        self.d.ILCT_IxFakeResolve(h, npc)
        self.d.ILCT_IxFakeScan(3, 1, h, 2, 1)
        self.d.ILCT_IxFakeConsumer(1)  # the NPC enters the conversation
        q = self.arm(POLICY_NPC)
        res, c = self.window()
        self.assertEqual((res, q.result, q.target_handle), (RESUME, WR['TALK'], h))
        log = self.log()
        scans = [e for e in log if e.kind == EV_EXACT]
        self.assertEqual([e.arg for e in scans], [0, 1, 2, 3, 4], 'all five registries')
        self.assertTrue(all(e.guard == 1 for e in scans), 'the guards are armed during the scan')
        self.assertTrue(all(e.source == self.owner for e in scans), 'measured from the guest')
        first = scans[0]
        self.assertEqual((list(first.descriptor), first.can_use), ([RESET_DISTANCE, NONE32, NONE32, 2, 0, READY], 0))
        call = [e for e in log if e.kind == EV_CONSUMER][0]
        self.assertEqual((call.guard, call.target, call.source), (0, npc, self.owner))
        v = DebugView()
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual(v.guard_active, 0)
        self.assert_restored()

    def test_npc_request_with_nobody_near_restores_and_calls_no_consumer(self):
        self.window_world()
        self.d.ILCT_IxFakeScan(-1, 0, 0, 2, 0)
        q = self.arm(POLICY_NPC)
        res, _ = self.window()
        self.assertEqual((res, q.result), (RESUME, WR['NO_TARGET']))
        self.assertNotIn(EV_CONSUMER, [e.kind for e in self.log()])
        self.assert_restored()

    def test_refusals_before_any_write(self):
        h = handle_of(1, 0, 2)
        cases = [
            ('P1 busy', lambda: self.d.ILCT_IxFakeGate(0), POLICY_LEADER, WR['P1_BUSY']),
            ('guest gone', lambda: self.d.ILCT_IxFakeResolve(self.g2h, 0), POLICY_LEADER, WR['SOURCE_GONE']),
            ('area change pending', lambda: self.m.put32(PENDING_AREA, 3), POLICY_TREASURE, WR['BLOCKED']),
            ('transition in progress', lambda: self.m.put32(LOCK_MASK, 1 << 12), POLICY_TREASURE, WR['BLOCKED']),
            ('UI off', lambda: self.m.put32(RENDER, 0), POLICY_LEADER, WR['BLOCKED']),
            ('interactions suppressed', lambda: self.m.put32(SUPPRESSION, 0x100), POLICY_NPC, WR['BLOCKED']),
            ('event busy', lambda: self.m.put32(EVENTS, 1 << 10), POLICY_LEADER, WR['BLOCKED']),
            ('guards off', lambda: self.d.ILCT_PatchSetArmed(1) or self.m.twin.dll.ILCT_PatchSetSiteArmed(
                self.m.twin.index['IX-18'], 0), POLICY_NPC, WR['GUARDS_OFF']),
        ]
        for label, prepare, policy, want in cases:
            with self.subTest(label):
                self.setUp()
                self.window_world()
                prepare()
                q = self.arm(policy, h if policy != POLICY_NPC else 0, 2)
                res, _ = self.window()
                self.assertEqual((res, q.result, q.touched), (RUN_ORIGINAL, want, 0), label)
                self.assertNotIn(EV_CONSUMER, [e.kind for e in self.log()])
                self.assert_restored()

    def test_owner_and_leader_busy(self):
        h = handle_of(1, 0, 2)
        for owner, leader, policy, want in ((EXECUTION, 0, POLICY_LEADER, WR['OWNER_BUSY']),
                                            (0, WEAPON_HANDLING, POLICY_LEADER, WR['LEADER_BUSY']),
                                            (0, EXECUTION, POLICY_NPC, WR['LEADER_BUSY']),
                                            (EXECUTION, WEAPON_HANDLING, POLICY_TREASURE, WR['CONSUMED'])):
            with self.subTest(owner=owner, leader=leader, policy=policy):
                self.setUp()
                self.window_world(owner_flags=owner, leader_flags=leader)
                t = self.target()
                self.registry(1, [t])
                self.d.ILCT_IxFakeResolve(h, t)
                q = self.arm(policy, h if policy != POLICY_NPC else 0, 2)
                self.window()
                self.assertEqual(q.result, want)

    def test_p1s_own_trigger_goes_first_and_stale_requests_are_dropped(self):
        self.window_world()
        self.m.twin.poke(TRIGGER, b'\x01')
        q = self.arm(POLICY_LEADER, handle_of(1, 0, 2), 2)
        res, _ = self.window()
        self.assertEqual((res, q.state), (RUN_ORIGINAL, REQ_ARMED), 'waits for a frame without P1 interacting')
        self.m.twin.poke(TRIGGER, b'\x00')
        for label, kw in (('other epoch', dict(epoch=EPOCH + 1)), ('past its deadline', dict(deadline=gt.NOW - 1))):
            q = self.arm(POLICY_LEADER, handle_of(1, 0, 2), 2, **kw)
            res, _ = self.window()
            self.assertEqual((res, q.state, q.result), (RUN_ORIGINAL, REQ_DONE, WR['STALE']), label)
        self.d.ILCT_IxSessionPublish(ctypes.byref(session_record(gates=GATE['LOGICAL'])))
        q = self.arm(POLICY_LEADER, handle_of(1, 0, 2), 2)
        self.assertEqual((self.window()[0], q.result), (RUN_ORIGINAL, WR['STALE']), 'no valid world')
        self.assertNotIn(EV_CONSUMER, [e.kind for e in self.log()])

    def test_a_request_armed_before_a_swap_does_not_survive_it(self):
        """The request names the seat layout it was armed under: a swap (a new session generation) or the seat now
        steering another character makes it stale before anything is written."""
        h = handle_of(1, 0, 2)
        for label, prepare, kw in (
                ('seat layout changed (generation)', None, dict(generation=(gt.GEN + 1) & 0xFFFFFFFF)),
                ('the seat steers another character', lambda: self.d.ILCT_IxSessionPublish(ctypes.byref(
                    session_record({1: dict(actor=self.h.obj(), handle=0x00100009)}))), {})):
            with self.subTest(label):
                self.setUp()
                self.window_world()
                t = self.target()
                self.registry(1, [t])
                self.d.ILCT_IxFakeResolve(h, t)
                if prepare:
                    prepare()
                q = self.arm(POLICY_LEADER, h, 2, **kw)
                res, _ = self.window()
                self.assertEqual((res, q.state, q.result, q.touched), (RUN_ORIGINAL, REQ_DONE, WR['STALE'], 0))
                self.assertEqual(self.log(), [], 'nothing of the game was called')
                self.assert_restored()
        # Instrument: the same request under the layout it was armed for runs.
        self.setUp()
        self.window_world()
        t = self.target()
        self.registry(1, [t])
        self.d.ILCT_IxFakeResolve(h, t)
        q = self.arm(POLICY_LEADER, h, 2)
        self.window()
        self.assertEqual(q.result, WR['CONSUMED'])

    def test_an_unreadable_return_address_claims_nothing(self):
        """The return address is read before the request is claimed: without it the window could not take this
        frame's consumer call, so the request waits and nothing is written or called."""
        self.window_world()
        t = self.target()
        self.registry(1, [t])
        h = handle_of(1, 0, 2)
        self.d.ILCT_IxFakeResolve(h, t)
        q = self.arm(POLICY_LEADER, h, 2)
        c = self.ctx('IX-20', rsp=0x10)  # nothing mapped there: the probe refuses the read
        self.assertEqual(self.invoke('IX-20', c), RUN_ORIGINAL)
        self.assertEqual((q.state, q.touched), (REQ_ARMED, 0), 'still waiting for a frame it can take')
        self.assertEqual(self.log(), [])
        self.assert_restored()
        res, c = self.window()  # the next frame, readable: it runs
        self.assertEqual((res, c.resume, q.result), (RESUME, self.back, WR['CONSUMED']))

    def test_an_auxiliary_resolver_that_changes_the_entry_is_refused(self):
        """A target with the auxiliary flag: the resolver runs, then the entry is read again. If it is no longer the
        entry validated before the resolver (or the re-read one is torn), the consumer is never called."""
        h = handle_of(1, 0, 2)
        for label, swap in (('the table names another entry', 'other'), ('the entry is torn afterwards', 'torn'),
                            ('the table slot is emptied', 'hole'), ('unchanged (instrument)', None)):
            with self.subTest(label):
                self.setUp()
                self.window_world()
                t = self.target(flags=0x4 | 0x2000)
                table = self.registry(1, [t])
                self.d.ILCT_IxFakeResolve(h, t)
                if swap == 'other':
                    other = self.target(flags=0x4)
                    self.d.ILCT_IxFakeAuxiliarySwap(table + 8, other)
                elif swap == 'torn':
                    self.d.ILCT_IxFakeAuxiliarySwap(t + 0x40, 0)  # +0x40 cleared by the resolver's side effect
                elif swap == 'hole':
                    self.d.ILCT_IxFakeAuxiliarySwap(table + 8, 0)
                q = self.arm(POLICY_LEADER, h, 2)
                res, _ = self.window()
                kinds = [e.kind for e in self.log()]
                self.assertIn(EV_AUX, kinds, 'the resolver ran')
                if swap is None:
                    self.assertEqual((res, q.result), (RESUME, WR['CONSUMED']))
                    self.assertIn(EV_CONSUMER, kinds)
                else:
                    self.assertEqual((res, q.result, q.touched), (RESUME, WR['TARGET_STALE'], 1), label)
                    self.assertNotIn(EV_CONSUMER, kinds, 'the consumer was called')
                self.assert_restored()


# ---- the co-op thread --------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (a copy of the exe)')
class CoopThreadTest(unittest.TestCase):
    """step() on a synthetic image: registries, actors and the frontend globals; the session record published."""

    def setUp(self):
        self.m = ix_world()
        self.m.setup()
        self.d = self.m.d
        self.img = Image()
        self.now = 50_000
        self.d.ILCT_SetFixedClock(gt.NOW)
        for va in (MENU_QUEUED, FRONTEND_ID, FRONTEND_ACTIVE):
            self.img.put_global(va, bytes(8))
        # Registry 0 (actors), 1 (objects), 3 (NPCs) of the synthetic image.
        self.tables = {}
        for reg in (0, 1, 3):
            table = self.img.alloc(8 + 8 * 64)
            self.img.p32(table, 64)
            self.tables[reg] = table
            base = REGISTRY + reg * STRIDE
            self.img.put_global(base, struct.pack('<QQ', 1, table) + bytes([1]) + bytes(15) + struct.pack('<I', 64))
        self.p2 = self.actor(0, 1, gen=3)
        self.p3 = self.actor(0, 2, gen=4)
        self.d.ILCT_IxStart(self.img.base, self.now)
        self.d.ILCT_IxSetImageBase(self.img.base)

    def tearDown(self):
        self.d.ILCT_IxStop()
        self.d.ILCT_IxSessionWithdraw()
        self.img.close()
        self.m.twin.reset()

    def entry(self, reg, index, gen):
        e = self.img.alloc(0x200, page=True)
        self.img.p16(e + 0x16, gen)
        self.img.p64(self.tables[reg] + 8 + 8 * index, e)
        return e

    def actor(self, reg, index, gen):
        e = self.entry(reg, index, gen)
        a = self.img.alloc(0x800, page=True)
        self.img.p64(e + 0x30, a)
        host = self.img.alloc(0x200)
        skeleton = self.img.alloc(0x100)
        self.img.p64(e + 0xC0, host)
        self.img.p64(host + 0x138, skeleton)
        h = handle_of(reg, index, gen)
        self.img.p32(a + 0x08, h)
        self.img.p32(a + 0x6B4, 1)
        self.img.p32(a + 0x714, 0x4021)
        return dict(entry=e, actor=a, skeleton=skeleton, handle=h)

    def publish(self, seats=None, **kw):
        seats = {1: self.p2, 2: self.p3} if seats is None else seats
        self.d.ILCT_IxSessionPublish(ctypes.byref(session_record(
            {s: dict(actor=v['actor'], handle=v['handle']) for s, v in seats.items()}, **kw)))

    def step(self, n=1, dt=20):
        for _ in range(n):
            self.now += dt
            self.d.ILCT_IxStep(self.now)

    def record(self) -> IxRecord:
        r = IxRecord()
        self.assertEqual(self.d.ILCT_IxRecord(ctypes.byref(r)), 1)
        return r

    def request(self) -> Request:
        return Request.from_address(self.d.ILCT_IxRequest())

    def finish(self, result, target=0):
        q = self.request()
        q.result, q.target_handle = result, target
        q.state = REQ_DONE

    def find(self, slot, handle, state, tick=10, mark=10, source=None):
        p = IxPrivate()
        p.state[slot], p.handle[slot], p.tick, p.mark = state, handle, tick, mark
        p.source_handle = self.p2['handle'] if source is None else source
        self.d.ILCT_IxPublishPrivate(0, ctypes.byref(p))

    def test_record_names_each_steering_guest_by_entry_skeleton_and_handle(self):
        self.publish()
        self.step()
        r = self.record()
        self.assertEqual((r.head.epoch, r.head.flags, r.active), (EPOCH, IXF_GATEWAYS | IXF_ROUTING | IXF_LOOT, 0b11))
        for k, g in ((0, self.p2), (1, self.p3)):
            self.assertEqual((r.slot[k].entry, r.slot[k].skeleton, r.slot[k].handle, r.slot[k].seat),
                             (g['entry'], g['skeleton'], g['handle'], k + 1))
        # A handle whose generation no longer names the actor drops that guest for the beat.
        self.img.p16(self.p3['entry'] + 0x16, 5)
        self.step()
        self.assertEqual(self.record().active, 0b01)
        # Gates: no interaction gate -> no private passes; no logical co-op -> no gateways and no loot.
        self.publish(gates=GATE['WORLD'] | GATE['LOGICAL'])
        self.step()
        self.assertEqual(self.record().head.flags, IXF_GATEWAYS | IXF_LOOT)
        self.publish(gates=GATE['WORLD'] | GATE['INTERACTION'])
        self.step()
        self.assertEqual(self.record().head.flags, IXF_ROUTING)
        self.publish(seats={1: dict(self.p2)}, gates=GATE['WORLD'] | GATE['LOGICAL'] | GATE['PARTY_DOWN'])
        self.step()
        self.assertEqual(self.record().head.flags, 0, 'party down: hands off')

    def test_a_fourth_seat_gets_its_own_slot(self):
        p4 = self.actor(0, 3, gen=5)
        self.publish(seats={1: self.p2, 2: self.p3, 3: p4})
        self.step()
        r = self.record()
        self.assertEqual(r.active, 0b111, 'three guest slots with four seats')
        self.assertEqual((r.slot[2].entry, r.slot[2].handle, r.slot[2].seat), (p4['entry'], p4['handle'], 3))
        self.d.ILCT_IxPress(1 << 3)
        self.step()
        self.assertEqual((self.request().state, self.request().seat), (REQ_ARMED, 3), "P4's press arms its request")

    def menu_opens(self) -> int:
        fn = self.d.ILCT_IxTakeMenuOpens
        fn.argtypes, fn.restype = [ctypes.c_uint32], ctypes.c_uint32
        return fn(EPOCH)

    def test_a_confirm_that_finds_nothing_opens_the_guests_menu(self):
        """Together's order (0.1.58, Marco's playtest): an interaction first, then NPC talk, then the menu. A guest's
        CONFIRM whose request finds nothing (no object, no NPC, or no NPC scan possible) asks for that guest's menu;
        anything the guest could use, or a busy character, never does."""
        self.publish()
        self.step()
        self.assertEqual(self.menu_opens(), 0)
        cases = [(WR['NO_TARGET'], 1 << 1), (WR['TALK'], 0), (WR['CONSUMED'], 0), (WR['P1_BUSY'], 1 << 1),
                 (WR['GUARDS_OFF'], 1 << 1), (WR['OWNER_BUSY'], 0), (WR['BLOCKED'], 0), (WR['STALE'], 0)]
        for result, want in cases:
            with self.subTest(result=result):
                self.d.ILCT_IxStop()
                self.d.ILCT_IxStart(self.img.base, self.now)
                self.publish()
                self.step()
                self.menu_opens()
                self.d.ILCT_IxPress(1 << 1)
                self.step()
                q = self.request()
                self.assertEqual((q.state, q.seat, q.policy), (REQ_ARMED, 1, 3), 'no object found: the NPC request')
                self.finish(result)
                self.step()
                self.assertEqual(self.menu_opens(), want)
                self.assertEqual(self.menu_opens(), 0, 'taken once')

    def test_guest_interactions_off_a_confirm_opens_the_menu(self):
        opt = self.d.ILCT_OptSet
        opt.argtypes = [ctypes.c_char_p]
        try:
            opt(bytes([1, 0, 0, 0, 1, 5, 20, 6]))
            self.publish()
            self.step()
            self.menu_opens()
            self.d.ILCT_IxPress(1 << 2)
            self.step()
            self.assertEqual(self.menu_opens(), 1 << 2, 'guests stay out of the world: CONFIRM is the menu button')
        finally:
            self.d.ILCT_OptReset()

    def test_guest_interactions_off_keeps_doors_chests_npcs_and_shops_p1s(self):
        """Settings guest_interactions 0 (0.1.53): no private passes (no ROUTING) and a guest's CONFIRM arms nothing;
        gateways and walk-over loot keep working. Back on, the same press arms a request."""
        opt = self.d.ILCT_OptSet
        opt.argtypes = [ctypes.c_char_p]
        try:
            opt(bytes([1, 0, 0, 0, 1, 5, 20, 6]))
            self.publish()
            self.step()
            self.assertEqual(self.record().head.flags, IXF_GATEWAYS | IXF_LOOT)
            self.d.ILCT_IxPress(1 << 1)
            self.step()
            self.assertNotEqual(self.request().state, REQ_ARMED, 'no request while guests stay out of the world')
            self.assertEqual(self.d.ILCT_IxHoldSeats(EPOCH), 0)
        finally:
            self.d.ILCT_OptReset()
        self.step()
        self.assertEqual(self.record().head.flags, IXF_GATEWAYS | IXF_ROUTING | IXF_LOOT)
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.assertEqual(self.request().state, REQ_ARMED)

    def test_a_press_without_a_find_asks_for_npc_talk(self):
        self.publish()
        self.step()
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        q = self.request()
        self.assertEqual((q.state, q.policy, q.seat, q.source_handle, q.epoch), (REQ_ARMED, POLICY_NPC, 1,
                                                                                 self.p2['handle'], EPOCH))
        self.assertEqual(q.generation, gt.GEN & 0xFFFFFFFF, 'armed under this seat layout')
        self.assertEqual(self.d.ILCT_IxHoldSeats(EPOCH), 0b10, "the guest's character stands still meanwhile")
        self.assertEqual(self.d.ILCT_IxHoldSeats(EPOCH + 1), 0, 'tagged with its epoch')

    def test_a_press_near_an_object_uses_the_guests_own_scan(self):
        door = handle_of(1, 4, 2)
        self.entry(1, 4, 2)
        chest_e = self.entry(1, 5, 2)
        self.img.p16(chest_e + 0x102, 434)
        chest = handle_of(1, 5, 2)
        mimic = self.actor(0, 9, gen=6)
        self.img.p8(mimic['entry'] + 0x0E, 0x34)
        ext = self.img.alloc(16)
        self.img.p8(ext, 0x20)
        self.img.p64(mimic['actor'] + 0x6A0, ext)
        for handle, state, policy, want_state in ((door, 3, POLICY_LEADER, 3), (chest, 2, POLICY_TREASURE, 2),
                                                  (mimic['handle'], 1, POLICY_LEADER, 10)):
            with self.subTest(handle=hex(handle)):
                self.request().state = REQ_IDLE
                self.publish()
                self.find(5, handle, state, tick=10 + state, mark=10 + state)
                self.step()
                self.d.ILCT_IxPress(1 << 1)
                self.step()
                q = self.request()
                self.assertEqual((q.state, q.policy, q.object_handle, q.object_state),
                                 (REQ_ARMED, policy, handle, want_state))

    def test_stale_or_unusable_finds(self):
        door = handle_of(1, 4, 2)
        self.entry(1, 4, 2)
        for label, kw, want in (('state 1 is not usable', dict(state=1), None),
                                ('find older than four passes', dict(state=3, tick=20, mark=15), POLICY_NPC),
                                ('a find for another character', dict(state=3, source=0x00100007), POLICY_NPC)):
            with self.subTest(label):
                self.request().state = REQ_IDLE
                self.publish()
                self.find(2, door, kw.pop('state'), **kw)
                self.step()
                self.d.ILCT_IxPress(1 << 1)
                self.step()
                q = self.request()
                if want is None:
                    self.assertEqual(q.state, REQ_IDLE)
                else:
                    self.assertEqual((q.state, q.policy), (REQ_ARMED, want))
        # Passes that stopped running (the tick last seen 20, unchanged for more than 100 ms) make a find stale too.
        self.request().state = REQ_IDLE
        self.find(2, door, 3, tick=20, mark=20)
        self.step()
        self.step(n=6)
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.assertEqual(self.request().policy, POLICY_NPC)

    def test_a_guest_following_the_leader_presses_like_an_idle_one(self):
        """The party walks: the guest's action word is 0x4124 and the bytes after it hold something else."""
        door = handle_of(1, 4, 2)
        self.entry(1, 4, 2)
        for label, word in (('follow the leader', 0x4124), ('idle, other bytes after the action', 0x00074021),
                            ('follow, other bytes after the action', 0xBEEF4124)):
            with self.subTest(label):
                self.request().state = REQ_IDLE
                self.img.p32(self.p2['actor'] + 0x714, word)
                self.publish()
                self.find(5, door, 3, tick=20 + word % 7, mark=20 + word % 7)
                self.step()
                self.d.ILCT_IxPress(1 << 1)
                self.step()
                q = self.request()
                self.assertEqual((q.state, q.policy, q.object_handle), (REQ_ARMED, POLICY_LEADER, door))
        self.request().state = REQ_IDLE
        self.img.p32(self.p2['actor'] + 0x714, 0x0096)
        self.step()
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.assertEqual(self.request().state, REQ_IDLE, 'an action queued: no request')

    def test_a_busy_guest_or_a_parked_one_asks_nothing(self):
        self.publish()
        self.img.p64(self.p2['actor'], EXECUTION)
        self.step()
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.assertEqual(self.request().state, REQ_IDLE)
        self.img.p64(self.p2['actor'], 0)
        self.publish(gates=GATE['WORLD'] | GATE['LOGICAL'])  # no interaction gate (a menu, a cutscene)
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.assertEqual(self.request().state, REQ_IDLE)
        self.assertEqual(counters(self.d).presses, 2)

    def test_talk_then_shop_hold_and_its_menu_driver(self):
        npc_e = self.entry(3, 1, 9)
        npc = handle_of(3, 1, 9)
        self.publish()
        self.step()
        self.d.ILCT_IxPress(1 << 2)  # P3 starts the conversation
        self.step()
        self.assertEqual((self.request().state, self.request().seat), (REQ_ARMED, 2))
        self.finish(WR['TALK'], npc)
        self.img.p8(npc_e + 0x0B, 0x08)
        self.publish(facts=FACT['MODAL'], phase=PHASE['MENU'], gates=GATE['WORLD'] | GATE['LOGICAL'])
        self.step()
        v = DebugView()
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((v.hold_active, v.hold_seat, v.hold_opened, v.driver), (1, 2, 1, 2))
        self.assertEqual(self.d.ILCT_IxDriver(), 2, "P3's pad drives the dialog")
        self.assertEqual(self.d.ILCT_IxHoldSeats(EPOCH), 0b100)
        # The talk ends, the shop is queued, then opens (frontend 10 active): still P3's.
        self.img.p8(npc_e + 0x0B, 0)
        self.img.put_global(MENU_QUEUED, b'\x01')
        self.publish(phase=PHASE['CUTSCENE'], gates=GATE['LOGICAL'])
        self.step()
        self.assertEqual(self.d.ILCT_IxDriver(), 2)
        self.img.put_global(MENU_QUEUED, b'\x00')
        self.img.put_global(FRONTEND_ID, struct.pack('<II', 10, 1))
        self.publish(phase=PHASE['MENU'])
        self.step(n=50)
        self.assertEqual((self.d.ILCT_IxShopOpenNow(), self.d.ILCT_IxDriver()), (1, 2), 'the shop is P3s')
        self.d.ILCT_IxPress(1 << 1)  # P2 presses meanwhile: one interaction at a time
        self.step()
        self.assertEqual(self.request().state, REQ_IDLE)
        # The shop closes: 80 ms later the menus are P1's again.
        self.img.put_global(FRONTEND_ID, struct.pack('<II', 0, 0))
        self.publish()
        self.step(n=3)
        self.assertEqual(self.d.ILCT_IxDriver(), 2)
        self.step(n=2)
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((v.hold_active, v.driver, v.last_end), (0, 0, HOLD_DONE))
        self.assertEqual(self.d.ILCT_IxShopOpenNow(), 0)

    def test_owner_leaving_mid_dialog_gives_the_menus_back(self):
        npc_e = self.entry(3, 1, 9)
        self.publish()
        self.step()
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.finish(WR['TALK'], handle_of(3, 1, 9))
        self.img.p8(npc_e + 0x0B, 0x08)
        self.step()
        self.assertEqual(self.d.ILCT_IxDriver(), 1)
        parked = SEATF['IN_SESSION'] | SEATF['ACTOR']  # not manual: toggled off or parked
        self.d.ILCT_IxSessionPublish(ctypes.byref(session_record(
            {1: dict(actor=self.p2['actor'], handle=self.p2['handle'], flags=parked)})))
        self.step()
        v = DebugView()
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((v.hold_active, v.driver, v.last_end), (0, 0, HOLD_OWNER_GONE))

    def test_unclaimed_requests_expire_and_failures_start_no_hold(self):
        self.publish()
        self.step()
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.d.ILCT_SetFixedClock(gt.NOW + 1000)
        self.step()
        self.assertEqual((self.request().state, counters(self.d).requests_expired), (REQ_IDLE, 1))
        self.d.ILCT_SetFixedClock(gt.NOW)
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.finish(WR['P1_BUSY'])
        self.step()
        v = DebugView()
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((self.request().state, v.hold_active, self.d.ILCT_IxHoldSeats(EPOCH)), (REQ_IDLE, 0, 0))

    def test_the_kill_switch_ends_the_hold_and_co_op_back_on_leaves_the_menus_with_p1(self):
        """F8 mid-shop: MOVEMENT may no longer act. The hold ends for good and a waiting request is withdrawn, so when
        co-op is switched back on while the shop is still open, P1 keeps the menus."""
        npc_e = self.entry(3, 1, 9)
        self.publish()
        self.step()
        self.d.ILCT_IxPress(1 << 2)
        self.step()
        self.finish(WR['TALK'], handle_of(3, 1, 9))
        self.img.p8(npc_e + 0x0B, 0x08)
        self.img.put_global(FRONTEND_ID, struct.pack('<II', 10, 1))  # the shop is open
        self.step(n=3)
        self.assertEqual(self.d.ILCT_IxDriver(), 2, 'instrument: P3 drives the shop')
        self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)  # the kill switch's ordered disarm clears the bit
        self.step()
        v = DebugView()
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((v.hold_active, v.driver, v.last_end), (0, 0, HOLD_OWNER_GONE))
        self.assertEqual((self.d.ILCT_IxDriver(), self.d.ILCT_IxHoldSeats(EPOCH)), (0, 0))
        self.d.ILCT_PatchApplySubsystems(0xFF)  # co-op back on; the shop and the talk flag are still up
        self.step(n=10)
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((v.hold_active, v.driver), (0, 0), 'the menus stay with P1')
        self.assertEqual(self.d.ILCT_IxDriver(), 0)

    def test_the_kill_switch_withdraws_a_waiting_request_and_a_late_result_starts_no_hold(self):
        self.publish()
        self.step()
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.assertEqual(self.request().state, REQ_ARMED)
        self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        self.step()
        self.assertEqual((self.request().state, counters(self.d).requests_expired), (REQ_IDLE, 1))
        self.assertEqual(self.d.ILCT_IxHoldSeats(EPOCH), 0, "the guest's character is free again")
        # A request the window had already claimed finishes while the bit is clear: no hold from it.
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.d.ILCT_PatchApplySubsystems(0xFF & ~MOVEMENT)
        self.finish(WR['CONSUMED'])
        self.step()
        v = DebugView()
        self.d.ILCT_IxDebug(ctypes.byref(v))
        self.assertEqual((self.request().state, v.hold_active, self.d.ILCT_IxDriver()), (REQ_IDLE, 0, 0))
        self.d.ILCT_PatchApplySubsystems(0xFF)

    def test_stop_withdraws_and_ends_the_hold(self):
        self.publish()
        self.step()
        self.d.ILCT_IxPress(1 << 1)
        self.step()
        self.finish(WR['CONSUMED'])
        self.step()
        self.d.ILCT_IxStop()
        r = IxRecord()
        self.assertEqual(self.d.ILCT_IxRecord(ctypes.byref(r)), 0)
        self.assertEqual((self.d.ILCT_IxDriver(), self.d.ILCT_IxHoldSeats(EPOCH)), (0, 0))


# ---- pad routing to the guest who started it ----------------------------------------------------------------------
@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class MenuDriverRoutingTest(unittest.TestCase):
    ISO_DRIVER = 0x80

    @classmethod
    def setUpClass(cls):
        cls.iso = iso.Iso.get()

    def setUp(self):
        self.iso.setup()
        self.d = declare(self.iso.d)
        self.d.ILCT_IxReset()
        self.d.ILCT_IsoFakeFlee(EPOCH, 0)  # no guest asks for flee: the blocks below carry the pads alone
        self.xi0, self.xi1 = iso.Device(iso.VT_XI, 0), iso.Device(iso.VT_XI, 1)
        self.di = iso.Device(iso.VT_DI, product=0x0CE6054C)
        self.pads = [(1, self.xi0), (1, self.xi1), (2, self.di)]

    def tearDown(self):
        self.d.ILCT_IsoFakeDriver(-1)
        self.d.ILCT_IsoFakeFlee(0, 0)
        self.iso.twin.reset()

    def compose(self, settings) -> iso.IsoRecord:
        self.d.ILCT_IsoConfigure(settings)
        out = iso.IsoRecord()
        self.d.ILCT_IsoCompose(EPOCH, 3, None, ctypes.byref(out))
        return out

    def settings(self, **kw):
        return iso.IsolationBodyTest.settings(self, **kw)

    def test_record_names_the_driver_and_the_keyboard_follows_its_owner(self):
        self.iso.mirror(1, iso.SRC_XINPUT, slot=0)
        self.d.ILCT_IsoFakeDriver(0)
        r = self.compose(self.settings(guests=(1,), kbm=1))
        self.assertEqual((r.flags & self.ISO_DRIVER, r.reserved0), (0, 0), 'no driver: P1 as always')
        self.d.ILCT_IsoFakeDriver(1)
        r = self.compose(self.settings(guests=(1,), kbm=1))
        self.assertEqual((r.flags & (self.ISO_DRIVER | iso.ISO_GUARD | iso.ISO_KB), r.reserved0),
                         (self.ISO_DRIVER | iso.ISO_GUARD | iso.ISO_KB, 1), "P1's keyboard waits too")
        r = self.compose(self.settings(guests=(1,), kbm=2))
        self.assertEqual(r.flags & iso.ISO_KB, 0, 'the driving guest owns the keyboard: it passes')
        self.d.ILCT_IsoFakeDriver(2)
        self.assertEqual(self.compose(self.settings(guests=(1, 2))).reserved0, 2)
        self.d.ILCT_IsoFakeDriver(3)
        r = self.compose(self.settings(guests=(1, 2)))
        self.assertEqual((r.flags & self.ISO_DRIVER, r.reserved0), (self.ISO_DRIVER, 3), 'P4 may drive (0.1.53)')

    def visit(self):
        return [self.iso.call(which, dev.ctx()) for which, dev in self.pads]

    def test_the_host_carries_the_guests_pad_and_p1_waits(self):
        guest_pad = (0x1000 | 0x0001, 0, 0, 0, 20000, 0, 0)  # A + d-pad up: confirm, move the shop cursor
        p1_pad = (0x2000, 0, 0, 0, 0, 0, 0)
        self.iso.mirror(1, iso.SRC_XINPUT, slot=0, pad=p1_pad)
        self.iso.mirror(2, iso.SRC_SDL, pad=guest_pad)
        record = iso.IsoRecord(EPOCH, self.ISO_DRIVER | iso.ISO_GUARD | iso.ISO_NATIVE | iso.ISO_BG, 1, 0, 0, 0b10, 1,
                               0, 0, 0)
        self.d.ILCT_IsoPublish(ctypes.byref(record))
        for _ in range(3):
            for _, dev in self.pads:
                dev.reset()
            self.assertEqual(self.visit(), [RUN_ORIGINAL] * 3, 'the finalizer always runs')
        self.assertEqual(self.xi0.peek(iso.BLOCK, iso.BLOCK_SIZE), iso.reference_block(guest_pad),
                         "P1's own object carries the guest's pad: P1's buttons are gone")
        self.assertEqual(self.xi0.peek(0x45C, 4), bytes(4), "the next real packet of P1's pad is read again")
        for dev in (self.xi1, self.di):
            self.assertEqual(dev.peek(iso.BLOCK, iso.BLOCK_SIZE), bytes(iso.BLOCK_SIZE), 'every other pad masked')
        # The dialog closes: P1's own device passes again.
        record.flags, record.reserved0 = iso.ISO_GUARD | iso.ISO_NATIVE | iso.ISO_BG, 0
        self.d.ILCT_IsoPublish(ctypes.byref(record))
        for _, dev in self.pads:
            dev.reset()
        self.visit()
        self.assertTrue(self.xi0.peek(iso.BLOCK, iso.BLOCK_SIZE) == self.xi0.junk, "P1's pad is P1's again")

    def test_a_new_drive_never_replays_presses_made_before_it(self):
        """Two drives within 2 s in the same epoch. A guest's tap between the drives and P1's taps during a drive are
        consumed when the feeding seat changes, never injected: the second drive starts without a CONFIRM, and P1's
        own pad (on SDL here) gets none of the presses it made while the guest drove."""
        d = self.iso.d
        for name, args, res in (('ILCT_EdgeReset', [u32], None), ('ILCT_EdgeRecord', [u32, P(u32)], None)):
            fn = getattr(d, name)
            fn.argtypes, fn.restype = args, res
        seq = [0]

        def tick(seat=None, pressed=0):
            masks = (u32 * 28)()
            if seat is not None:
                masks[(seat - 1) * 7] = pressed      # pad pressed
                masks[(seat - 1) * 7 + 1] = pressed  # ... and released within the same tick: a tap
            seq[0] += 1
            d.ILCT_EdgeRecord(seq[0], masks)

        A = 1  # ilc_pad_bit(ILC_PAD_A): code 1 is bit 0
        idle = (0, 0, 0, 0, 0, 0, 0)
        d.ILCT_EdgeReset(0x51)
        tick()
        self.iso.mirror(1, iso.SRC_SDL, pad=idle)  # P1 on SDL: the host is fed from seat 1 outside a drive
        self.iso.mirror(2, iso.SRC_SDL, pad=idle)
        sub = iso.ISO_GUARD | iso.ISO_SUBST | iso.ISO_BG
        record = iso.IsoRecord(EPOCH, sub, 1, 0xFF, 0, 0b10, 0, 0, 0, 0)

        def visit(flags, driver=0):
            record.flags, record.reserved0 = flags, driver
            self.d.ILCT_IsoPublish(ctypes.byref(record))
            for _, dev in self.pads:
                dev.reset()
            self.visit()
            return self.xi0.peek(iso.BLOCK, iso.BLOCK_SIZE)

        neutral = iso.reference_block(idle)
        self.assertEqual(visit(sub), neutral)
        # Drive 1: the guest's tap during the drive reaches the game (instrument).
        self.assertEqual(visit(sub | self.ISO_DRIVER, 1), neutral)
        tick(2, A)
        self.assertNotEqual(visit(sub | self.ISO_DRIVER, 1), neutral, 'instrument: a tap during a drive is injected')
        tick(1, A)  # P1 presses A while the guest drives: P1 waits
        tick()
        self.assertEqual(visit(sub | self.ISO_DRIVER, 1), neutral)
        # The drive ends: P1's own taps from the drive are not replayed onto P1's pad.
        self.assertEqual(visit(sub), neutral, "P1's tap made during the drive was replayed")
        tick(2, A)  # the guest taps CONFIRM between the drives (P1 has the menus)
        self.assertEqual(visit(sub), neutral)
        # Drive 2, well inside the 2 s history: no replayed CONFIRM.
        self.assertEqual(visit(sub | self.ISO_DRIVER, 1), neutral, 'the tap from before the drive was replayed')
        tick()
        self.assertEqual(visit(sub | self.ISO_DRIVER, 1), neutral)
        self.assertGreaterEqual(self.iso.stats()['feed_drains'], 4)

    def test_a_feeding_seat_counts_as_drained_only_when_the_history_answered(self):
        """The edge history cannot answer the moment a drive starts (no tick recorded yet). The new feeding seat is
        not taken as drained: the next visit drains it once the history answers, so the guest's tap from before the
        drive is never injected."""
        d = self.iso.d
        for name, args, res in (('ILCT_EdgeReset', [u32], None), ('ILCT_EdgeRecord', [u32, P(u32)], None)):
            fn = getattr(d, name)
            fn.argtypes, fn.restype = args, res
        seq = [0]

        def tick(seat=None, pressed=0):
            masks = (u32 * 28)()
            if seat is not None:
                masks[(seat - 1) * 7] = pressed
                masks[(seat - 1) * 7 + 1] = pressed
            seq[0] += 1
            d.ILCT_EdgeRecord(seq[0], masks)

        A = 1
        idle = (0, 0, 0, 0, 0, 0, 0)
        run = 0x53
        d.ILCT_EdgeReset(run)
        tick()
        self.iso.mirror(1, iso.SRC_SDL, pad=idle)
        self.iso.mirror(2, iso.SRC_SDL, pad=idle)
        sub = iso.ISO_GUARD | iso.ISO_SUBST | iso.ISO_BG
        record = iso.IsoRecord(EPOCH, sub, 1, 0xFF, 0, 0b10, 0, 0, 0, 0)

        def visit(flags, driver=0):
            record.flags, record.reserved0 = flags, driver
            self.d.ILCT_IsoPublish(ctypes.byref(record))
            for _, dev in self.pads:
                dev.reset()
            self.visit()
            return self.xi0.peek(iso.BLOCK, iso.BLOCK_SIZE)

        neutral = iso.reference_block(idle)
        visit(sub)
        visit(sub | self.ISO_DRIVER, 1)   # the guest's seat has a cursor now
        tick(2, A)
        self.assertNotEqual(visit(sub | self.ISO_DRIVER, 1), neutral, 'instrument: a tap during a drive is injected')
        visit(sub)                        # the drive ends
        tick(2, A)                        # the guest taps between the drives
        drains = self.iso.stats()['feed_drains']
        d.ILCT_EdgeReset(run)             # the history has no tick to answer with right now
        self.assertEqual(visit(sub | self.ISO_DRIVER, 1), neutral)
        self.assertEqual(self.iso.stats()['feed_drains'], drains, 'nothing drained: the seat is not taken yet')
        tick()                            # the history answers again, the guest's old tap still held in it
        self.assertEqual(visit(sub | self.ISO_DRIVER, 1), neutral, 'the tap from before the drive was replayed')
        self.assertEqual(self.iso.stats()['feed_drains'], drains + 1, 'drained on the visit the history answered')
        tick(2, A)
        self.assertNotEqual(visit(sub | self.ISO_DRIVER, 1), neutral, 'a tap made during the drive still reaches it')

    def test_a_guest_on_xinput_drives_through_the_host_too(self):
        guest_pad = (0x0002, 0, 0, 0, 0, 0, 0)
        self.iso.mirror(1, iso.SRC_XINPUT, slot=0)
        self.iso.mirror(2, iso.SRC_XINPUT, slot=1, pad=guest_pad)
        record = iso.IsoRecord(EPOCH, self.ISO_DRIVER | iso.ISO_GUARD | iso.ISO_NATIVE, 1, 0, 0, 0b10, 1, 0, 0, 0)
        self.d.ILCT_IsoPublish(ctypes.byref(record))
        self.visit()
        self.assertEqual(self.xi0.peek(iso.BLOCK, iso.BLOCK_SIZE), iso.reference_block(guest_pad))
        self.assertEqual(self.xi1.peek(iso.BLOCK, iso.BLOCK_SIZE), bytes(iso.BLOCK_SIZE))
        # The guest's pad disconnects: the host goes neutral.
        self.iso.mirror(2, iso.SRC_XINPUT, slot=1, pad=guest_pad, connected=False)
        self.visit()
        self.assertEqual(self.xi0.peek(iso.BLOCK, iso.BLOCK_SIZE), bytes(iso.BLOCK_SIZE))


# ---- unicorn: the hooked sites on the twin -------------------------------------------------------------------------
from test_coop_movement import MoveEmulator  # noqa: E402 (the emulator with the probe's imports answered)

SCR = pe.SCRATCH
E_G2, E_G3, E_LEADER, A_G2, A_LEADER, T_OBJ, K_G2, PHYS_G2 = (SCR + 0x10000 * k for k in range(1, 9))
TABLE = SCR + 0x90000


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class InteractionEmulationTest(unittest.TestCase):
    TRIALS = 4
    P1H, G2H, G3H = 0x00500000, 0x00100001, 0x00100002

    @classmethod
    def setUpClass(cls):
        cls.m = ix_world()
        cls.m.setup(fakes=False)
        cls.twin = cls.m.twin
        cls.d = cls.m.d
        cls.emu = MoveEmulator(cls.twin)

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_IxSessionWithdraw()
        cls.twin.reset()

    def setUp(self):
        d = self.d
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_SetFixedClock(gt.NOW)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_IxSetPass(0, 0, 0)
        d.ILCT_IxSetGuard(0)
        Request.from_address(d.ILCT_IxRequest()).state = REQ_IDLE
        d.ILCT_IxPublish(ctypes.byref(ix_record([(0, E_G2, K_G2, self.G2H), (1, E_G3, 0, self.G3H)])))
        d.ILCT_IxSessionPublish(ctypes.byref(session_record({1: dict(actor=A_G2, handle=self.G2H)})))

    def site(self, sid):
        r = self.twin.rows[self.twin.index[sid]]
        return TWIN_BASE + r['rva'], r

    def world_bytes(self, uc):
        uc.mem_write(PENDING_AREA, struct.pack('<i', -1))
        uc.mem_write(LOCK_MASK, bytes(4))
        uc.mem_write(RENDER, struct.pack('<I', 8))
        uc.mem_write(SUPPRESSION, bytes(4))
        uc.mem_write(EVENTS, bytes(4))
        uc.mem_write(TRIGGER, bytes(4))
        uc.mem_write(CONTROLLED, struct.pack('<I', self.P1H))

    def pair(self, sid, state, setup=None, on_callee=None):
        """Original bytes, then the hooked site, from the same state."""
        emu, uc = self.emu, self.emu.uc
        address, r = self.site(sid)
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        self.world_bytes(uc)
        if setup:
            setup(uc)
        out = lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE)  # noqa: E731
        patched = bytes(uc.mem_read(address, r['hook_len']))
        emu.write_code(address, checked(r)[:r['hook_len']])
        a = emu.run(address, lambda x: out(x) or address <= x < address + r['hook_len'], state, on_callee=on_callee)
        emu.write_code(address, patched)
        b = emu.run(address, lambda x: out(x) or address <= x < address + 5, state, on_callee=on_callee)
        touched = set(a['final']) | set(b['final'])
        baseline = {x: uc.mem_read(x, 1)[0] for x in touched}
        self.assertEqual(emu.unexpected, [], f'unexpected imports: {emu.unexpected}')
        return a, b, baseline

    def states(self, sid, salt):
        for trial in range(self.TRIALS):
            state = pe.random_state(random.Random(0xB2800 + salt * 100 + trial))
            g = state['gpr']
            g['RSP'] &= ~0xF
            if sid == 'MV-08':
                g.update(RBX=1, RCX=K_G2)
            elif sid in ('IX-06', 'IX-07'):
                g.update(RSI=T_OBJ, RBP=SCR + 0xA0100)
            elif sid in ('IX-08', 'IX-09', 'IX-10'):
                g.update(RBX=E_G2, RAX=0, R15=0)
            elif sid == 'IX-02':
                g.update(RDX=3)
            elif sid == 'LT-01':
                g.update(RCX=self.G2H)
            yield trial, state

    MID = ('MV-08', 'IX-02', 'IX-03', 'IX-04', 'IX-05', 'IX-06', 'IX-07', 'IX-08', 'IX-09', 'IX-10', 'IX-17', 'IX-18',
           'IX-19')

    def test_every_site_equals_the_original_bytes_when_it_must_not_act(self):
        d = self.d
        conditions = {
            'no record, no pass, no guard': lambda: d.ILCT_IxWithdraw(),
            'gates off (flags)': lambda: d.ILCT_IxPublish(ctypes.byref(ix_record([(0, E_G2, K_G2, self.G2H)],
                                                                                    flags=0))),
            'MOVEMENT and PARTY clear': lambda: d.ILCT_PatchApplySubsystems(0xFF & ~(MOVEMENT | PARTY)),
            'DRY': lambda: d.ILCT_PatchSetMode(gt.DRY),
        }
        runs = 0
        for label, prepare in conditions.items():
            self.setUp()
            prepare()
            for k, sid in enumerate(self.MID + ('MV-07', 'IX-01', 'IX-20', 'LT-01')):
                for trial, state in self.states(sid, k):
                    with self.subTest(condition=label, site=sid, trial=trial):
                        a, b, baseline = self.pair(sid, state)
                        gt.compare(self, self.emu, f'{sid} {label}', a, b, baseline, state['gpr']['RSP'])
                        runs += 1
        print(f'\ninteraction fail-open: {runs} runs equal to the original bytes')

    def test_armed_gateway_class_store_writes_the_leaders_class(self):
        for trial, state in self.states('MV-08', 1):
            a, b, _ = self.pair('MV-08', state)
            self.assertEqual(a['exit'], b['exit'])
            self.assertEqual(b['gpr']['RBX'], 0)
            stored = bytes(b['final'].get(K_G2 + 0x80 + i, 0xEE) for i in range(2))
            self.assertEqual(stored, b'\0\0', 'class 0 stored for the guest skeleton')
            self.assertEqual(bytes(a['final'][K_G2 + 0x80 + i] for i in range(2)), b'\x01\x00')

    def test_gateway_call_runs_the_native_pass_for_p1_then_each_guest(self):
        address, _ = self.site('MV-07')
        seen = []
        uc = self.emu.uc

        def on_callee(u, at):
            if at == GATEWAY:
                seen.append(struct.unpack('<I', bytes(u.mem_read(CONTROLLED, 4)))[0])

        for label, prepare, want in (
                ('two guests', None, [self.P1H, self.G2H, self.G3H]),
                ('a guest leads', lambda u: u.mem_write(CONTROLLED, struct.pack('<I', self.G2H)),
                 [self.G2H, self.G3H]),
                ('area change pending', lambda u: u.mem_write(PENDING_AREA, struct.pack('<i', 12)), [self.P1H]),
                ('no guest', 'withdraw', [self.P1H])):
            with self.subTest(label):
                self.setUp()
                if prepare == 'withdraw':
                    self.d.ILCT_IxWithdraw()
                    prepare = None
                seen.clear()
                state = pe.random_state(random.Random(0xB2870))
                state['gpr']['RSP'] &= ~0xF
                _, b, _ = self.pair('MV-07', state, setup=prepare, on_callee=on_callee)
                self.assertEqual(b['exit'], address + 5)
                self.assertEqual(b['calls'], [GATEWAY] * len(want), 'the native pass, once per character')
                self.assertEqual(seen[-len(want):], want, 'the controlled handle at each pass')
                final = bytes(b['final'].get(CONTROLLED + i, uc.mem_read(CONTROLLED + i, 1)[0]) for i in range(4))
                self.assertEqual(struct.unpack('<I', final)[0], want[0], 'restored after the passes')

    def window_scene(self, uc, entries):
        uc.mem_write(TABLE, struct.pack('<i', len(entries)) + bytes(4) + b''.join(struct.pack('<Q', e) for e in entries))
        base = REGISTRY + 1 * STRIDE
        uc.mem_write(base, struct.pack('<QQ', 1, TABLE) + bytes([1]) + bytes(15) + struct.pack('<I', len(entries)))
        uc.mem_write(E_G2 + 0x30, struct.pack('<Q', A_G2))
        uc.mem_write(E_G2 + 0xB8, struct.pack('<Q', PHYS_G2))
        uc.mem_write(A_G2, struct.pack('<Q', 0))
        uc.mem_write(A_G2 + 0x6B4, struct.pack('<II', 1, 0))
        uc.mem_write(A_G2 + 0x714, struct.pack('<I', 0x4021))
        uc.mem_write(E_LEADER + 0x30, struct.pack('<Q', A_LEADER))
        uc.mem_write(A_LEADER, struct.pack('<Q', 0))
        uc.mem_write(T_OBJ + 0x0C, bytes([0x10]))
        uc.mem_write(T_OBJ + 0x18, bytes([1]))  # type 1: no script path
        uc.mem_write(T_OBJ + 0x1C, struct.pack('<I', 0))
        uc.mem_write(SOURCE, struct.pack('<Q', 0xABCDEF))
        uc.mem_write(CAN_USE, b'\x01')
        uc.mem_write(DESCRIPTOR, struct.pack('<6I', 0x40000000, 0, 3, 2, 0x00100003, 0))

    def run_window(self, entries, target_handle):
        X = pe.X
        address, _ = self.site('IX-20')
        q = Request.from_address(self.d.ILCT_IxRequest())
        q.ticket, q.epoch, q.policy, q.seat, q.source_handle = 5, EPOCH, POLICY_LEADER, 1, self.G2H
        q.object_handle, q.object_state, q.deadline_ms = target_handle, 2, gt.NOW + 200
        q.generation = gt.GEN & 0xFFFFFFFF
        q.state = REQ_ARMED
        resolved = {self.G2H: E_G2, self.P1H: E_LEADER, target_handle: T_OBJ}
        at_consumer = []

        def on_callee(u, at):
            rcx = u.reg_read(X.UC_X86_REG_RCX) & 0xFFFFFFFF
            if at == RESOLVE:
                u.reg_write(X.UC_X86_REG_RAX, resolved.get(rcx, 0))
            elif at == GATE_FN:
                u.reg_write(X.UC_X86_REG_RAX, 1)
            elif at == CONSUMER:
                at_consumer.append((struct.unpack('<Q', bytes(u.mem_read(SOURCE, 8)))[0],
                                    struct.unpack('<6I', bytes(u.mem_read(DESCRIPTOR, 24))),
                                    u.mem_read(CAN_USE, 1)[0], u.mem_read(T_OBJ + 0x0C, 1)[0]))
        state = pe.random_state(random.Random(0xB2820))
        state['gpr']['RSP'] &= ~0xF
        emu, uc = self.emu, self.emu.uc
        emu.refresh()  # the armed request is copied in from this process
        emu.tick_clock(gt.NOW + 5)
        self.world_bytes(uc)
        self.window_scene(uc, entries)
        b = emu.run(address, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or address <= x < address + 5,
                    state, on_callee=on_callee, probe=lambda u: (bytes(u.mem_read(SOURCE, 8)),
                                                                bytes(u.mem_read(DESCRIPTOR, 24)),
                                                                bytes(u.mem_read(CAN_USE, 1))))
        q.state = REQ_IDLE
        self.assertEqual(emu.unexpected, [])
        return b, at_consumer

    def test_the_window_runs_the_games_consumer_for_the_guest_and_restores(self):
        h = handle_of(1, 0, 2)
        b, at = self.run_window([T_OBJ], h)
        self.assertEqual(b['exit'], self.site('IX-20')[0] + 5, 'continues after the call')
        self.assertEqual(b['calls'].count(CONSUMER), 1)
        self.assertEqual(at[0][0], E_G2, "the guest's entry is the source")
        self.assertEqual(at[0][1], (FORCED_DISTANCE, 1, 0, 2, h, READY))
        self.assertEqual((at[0][2], at[0][3]), (1, 0x12))
        self.assertEqual(b['probe'], (struct.pack('<Q', 0xABCDEF), struct.pack('<6I', 0x40000000, 0, 3, 2,
                                                                                0x00100003, 0), b'\x01'),
                         "P1's source, descriptor and can-use byte are back before the frame goes on")

    def test_a_stale_target_never_reaches_the_games_consumer(self):
        h = handle_of(1, 0, 2)
        for entries in ([], [0]):
            b, at = self.run_window(entries, h)
            self.assertNotIn(CONSUMER, b['calls'])
            self.assertEqual(at, [])
            self.assertEqual(b['probe'][0], struct.pack('<Q', 0xABCDEF))
            self.assertEqual(b['exit'], self.site('IX-20')[0] + 5)

    def test_the_npc_accept_guard_keeps_the_games_out_of_range_path(self):
        """From 0x25BC82 to 0x25BD74: guarded vs. the original bytes taking their own out-of-range branch (the
        NPC's +0xB & bpl == 0). The same registers (eax aside: the guard leaves al = 0, which nothing reads before
        the next call) and xmm6 (the distance bias applied), and the NPC's range bit not set."""
        address, r = self.site('IX-18')
        stop = TWIN_BASE + 0x25BD74 - 0x120000
        npc = T_OBJ
        for trial in range(self.TRIALS):
            state = pe.random_state(random.Random(0xB2818 + trial))
            state['gpr'].update(RDI=npc, RBP=0)  # bpl = 0: the original takes its je whatever +0xB holds
            state['gpr']['RSP'] &= ~0xF
            emu, uc = self.emu, self.emu.uc
            emu.refresh()
            emu.tick_clock(gt.NOW + 5)
            uc.mem_write(npc + 0x0B, bytes([0x04, 0x00]))
            uc.mem_write(state['gpr']['RSP'] + 0x6C, struct.pack('<f', 0.25))
            # Allowed: our module and MinHook's buffers (outside the image), and the game's code up to 0x25BD74.
            allowed = lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or address <= x < stop  # noqa: E731
            patched = bytes(uc.mem_read(address, r['hook_len']))
            emu.write_code(address, checked(r)[:r['hook_len']])
            a = emu.run(address, allowed, state)
            emu.write_code(address, patched)
            self.d.ILCT_IxSetGuard(1)
            emu.refresh()
            uc.mem_write(npc + 0x0B, bytes([0x04, 0x00]))
            uc.mem_write(state['gpr']['RSP'] + 0x6C, struct.pack('<f', 0.25))
            b = emu.run(address, allowed, state)
            self.d.ILCT_IxSetGuard(0)
            self.assertEqual((a['exit'], b['exit']), (stop, stop))
            ga, gb = dict(a['gpr']), dict(b['gpr'])
            ga.pop('RAX'), gb.pop('RAX')
            self.assertEqual(ga, gb)
            self.assertEqual(a['xmm'][6], b['xmm'][6], 'the distance bias is applied on both paths')
            self.assertEqual(a['final'].get(npc + 0x0C), 0x02, 'the original sets the range bit')
            self.assertNotIn(npc + 0x0C, b['final'], 'the guard leaves it as it was')


if __name__ == '__main__':
    unittest.main()
