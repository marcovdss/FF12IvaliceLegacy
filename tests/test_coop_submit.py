"""Host tests for battle-menu requests on the game thread (native/coop/src/submit). No game run, nothing deployed; the
exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin and the unicorn class.

* attach: the game functions' first bytes are checked before any operation is registered (fail closed);
* the request slot: request -> queued -> one terminal result -> idle; busy while queued; quarantine when no result
  arrives by the deadline, refusal while quarantined, release only by the same ticket's late result (restated from
  the UI dispatcher quarantine tests); no busy-wait anywhere (every call returns at once);
* stale rejection right before the call: the queue's epoch and generation, then the issuer seat's character, handle
  and actor, its record, its status, the target (or the reserve member joining the formation), the game's
  availability check (magicks and technicks) and admission (MP, charges, gil); none of them calls the game;
* the game calls: the manual and reserve submit routes with their exact arguments, Dismiss only for the summon owner;
* Gambits: the identity proof table on the game thread (restated from the native Gambits toggle tests), the query,
  the proof repeated before the setter, verification of the setter's effect, result codes and sounds 0x12/0x13;
* the summon owner: a summon request remembered for 20 s, the fallback owner, the owner's Dismiss;
* the combat indicator: first try after 75 ms (at once on the issuer), 350 ms retries, a starting node's 400 ms
  grace, shown on state 2, given up after 30 s or when the issuer or the target is gone;
* menu sounds per seat, at most one pending, only allowed ids;
* a model event (submit through the candidates the seat was shown, the Gambits toggle);
* unicorn on the hooked twin: the UT-01 tick drains a stale submit (another generation, or the issuer's handle
  changed) and calls nothing but the stock tick; a valid one calls admission, then 0x311CD0 with the issuer, target
  and action in ECX/EDX/R8W on a Win64-aligned stack, then the stock tick.
"""
from __future__ import annotations

import ctypes
import random
import struct
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_menu as tm
import test_coop_patch_engine as pe
import test_coop_targets as tg
import test_coop_towns as tw
from coop_native_support import native_test
from coop_session_support import GATE, GameWorld, Image, Record
from test_coop_movement import MoveEmulator
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, untagged_constants

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER
SUBMIT_SRC = Path(__file__).resolve().parents[1] / 'native' / 'coop' / 'src' / 'submit'


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


ActionRequest = S('ActionRequest', [('action', u16), ('kind', u8), ('flags', u8), ('target', u32),
                                    ('target_actor', u64), ('reserve_member', u16), ('reserved', u16 * 3)])
SlotView = S('SlotView', [('state', u8), ('kind', u8), ('action', u16), ('ticket', u32), ('epoch', u32),
                          ('last_result', i32), ('generation', u64), ('enqueued_ms', u64), ('deadline_ms', u64),
                          ('completed', u32), ('quarantined', u32), ('released', u32), ('late_result', u32)])
IndicatorView = S('IndicatorView', [('active', u8), ('in_flight', u8), ('attempts', u16), ('source', u32),
                                    ('target', u32), ('ticket', u32), ('node', u64), ('next_try_ms', u64),
                                    ('attempt_ms', u64), ('deadline_ms', u64), ('last', i32), ('reserved', u32)])
SummonView = S('SummonView', [('active', u8), ('owner', u8), ('pending', u8), ('source', u8), ('reserved', u32),
                              ('pending_until_ms', u64)])
COUNTER_NAMES = ('requests', 'queued', 'refused', 'completed', 'quarantined', 'released', 'lost', 'op_ran',
                 'op_rejected', 'indicators_shown', 'indicators_expired', 'indicators_cancelled', 'sounds',
                 'sounds_dropped', 'prologue_mismatch')
Counters = S('Counters', [(n, u64) for n in COUNTER_NAMES])

F_SUBMIT = ctypes.CFUNCTYPE(i32, u32, u32, u16, u32)
F_RESERVE = ctypes.CFUNCTYPE(i32, u32, u32, u8, u16, u32)
F_DISMISS = ctypes.CFUNCTYPE(None, u32)
F_ADMISSION = ctypes.CFUNCTYPE(i32, u64, u16)
F_AVAILABLE = ctypes.CFUNCTYPE(i32, u8, u16, i32)
F_QUERY = ctypes.CFUNCTYPE(i32, u8)
F_SET = ctypes.CFUNCTYPE(None, u8, i32)
F_RECORD = ctypes.CFUNCTYPE(u64, u8)
F_INDICATOR = ctypes.CFUNCTYPE(u64, u64, u32, u32)
F_SOUND = ctypes.CFUNCTYPE(i32, u32)
GameFns = S('GameFns', [('submit', F_SUBMIT), ('reserve', F_RESERVE), ('dismiss', F_DISMISS),
                        ('admission', F_ADMISSION), ('available', F_AVAILABLE), ('query', F_QUERY), ('set', F_SET),
                        ('record', F_RECORD), ('indicator', F_INDICATOR), ('sound', F_SOUND)])
Job = S('Job', [('ticket', u32), ('op', u16), ('issuer', u8), ('reserved0', u8), ('epoch', u32), ('ttl_ms', u32),
                ('generation', u64), ('enqueued_ms', u64), ('arg', u64 * 4)])
LAYOUTS = [ActionRequest, SlotView, IndicatorView, SummonView, Counters, GameFns, Job]

OP_ACTION, OP_GAMBITS, OP_DISMISS, OP_INDICATOR, OP_SOUND = 17, 18, 19, 20, 21
RK_ACTION, RK_RESERVE, RK_DISMISS, RK_GAMBITS = 1, 2, 3, 4
RF_LICENSED, RF_INDICATOR, RF_WORLD, RF_REVIVE = 0x01, 0x02, 0x04, 0x08
SUB_QUEUED, SUB_BUSY, SUB_QUARANTINED, SUB_NOT_SEAT, SUB_NO_ISSUER, SUB_NOT_LIVE = 1, -1, -2, -3, -4, -5
SUB_FULL, SUB_NO_WORLD, SUB_BAD_REQUEST, SUB_NOT_OWNER, SUB_REFUSED, SUB_GAMBITS_REFUSED = -6, -7, -8, -9, -10, -11
SLOT_IDLE, SLOT_QUEUED, SLOT_QUARANTINED = 0, 1, 2
DONE, NO_NODE = 1, 2
GAMBITS_OFF, GAMBITS_ON = 1, 2
(REJ_BAD_ARGS, REJ_NO_WORLD, REJ_STALE_ISSUER, REJ_STALE_TARGET, REJ_STATUS, REJ_UNAVAILABLE, REJ_GAME_REFUSED,
 REJ_NOT_OWNER, REJ_NO_SUMMON, REJ_DISABLED) = range(-16, -26, -1)
REJ_ADMISSION, REJ_GAMBIT = -0x100, -0x200
GB_APPLIED, GB_STALE_SEQUENCE, GB_STALE_IDENTITY, GB_STALE_PHYSICS, GB_INVALID_MEMBER, GB_STALE_RECORD = (
    1, 0x10, 0x11, 0x12, 0x13, 0x14)
GB_VERIFY_FAILED, GB_BLOCKED = 0x15, 0x16
ACTION_TTL_MS, WAIT_MS = 400, 650
FACT_SUMMON = 0x20
SEATF_IN, SEATF_MANUAL, SEATF_ACTOR = 0x0001, 0x0002, 0x0008
GUEST = SEATF_IN | SEATF_MANUAL | SEATF_ACTOR
EPOCH, GEN, NOW = gt.EPOCH, gt.GEN, gt.NOW
ST_KO, ST_STONE, ST_SLEEP, ST_BERSERK = 1 << 0, 1 << 1, 1 << 4, 1 << 27
ATTACK, CURE, POTION, ESPER, DISMISS_ACTION = 0x96, 0x00, 0x52, 0x106, 0x113
FN_ADMISSION, FN_SUBMIT, UI_TICK = 0x303BD0, 0x311CD0, 0x290CF0   # VAs
BATTLE_STATE_PTR, ACTOR_MANAGER_PTR = 0x2EBF190, 0x2098E18          # VAs

SIGNATURES = {
    'ILCT_SubAttach': ([u64], i32), 'ILCT_SubEnabled': ([], i32), 'ILCT_SubReset': ([], None),
    'ILCT_SubForceEnabled': ([i32], None), 'ILCT_SubSetImageBase': ([u64], None),
    'ILCT_SubSetFns': ([P(GameFns)], None), 'ILCT_SubStart': ([u64, u64], None), 'ILCT_SubStep': ([u64], None),
    'ILCT_SubStop': ([], None), 'ILCT_SubRequest': ([u32, P(ActionRequest), u64], i32),
    'ILCT_SubDismiss': ([u32, u64], i32), 'ILCT_SubGambits': ([u32, u64], i32),
    'ILCT_SubEvent': ([u32, P(tm.Event), P(tg.CandidateList), u64], i32),
    'ILCT_SubSlot': ([u32, P(SlotView)], None), 'ILCT_SubIndicator': ([u32, P(IndicatorView)], None),
    'ILCT_SubSummon': ([P(SummonView)], None), 'ILCT_SubOwner': ([], u32),
    'ILCT_SubSummonUpdate': ([P(SummonView), u32, P(u16), u32, u64], None),
    'ILCT_SubGambitOutcome': ([i32, P(i32), P(u32)], None), 'ILCT_SubSoundFor': ([u32], u32),
    'ILCT_SubPlay': ([u32, u32], None), 'ILCT_SubSetSounds': ([u32], None), 'ILCT_SubStats': ([P(Counters)], None),
    'ILCT_SubOp': ([u32, P(Job)], i32), 'ILCT_SubEncode': ([u32, P(ActionRequest), u32, u32, u64, P(u64)], None),
}


def declare(d):
    tw.declare(d)
    tg.declare(d)
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def request(action=ATTACK, target=0, target_actor=0, kind=RK_ACTION, flags=RF_INDICATOR, reserve=0):
    r = ActionRequest()
    r.action, r.kind, r.flags, r.target, r.target_actor, r.reserve_member = (action, kind, flags, target,
                                                                              target_actor, reserve)
    return r


class Doubles:
    """The game functions, recorded; the Gambits setter writes the record bit like the game does."""

    def __init__(self, img=None):
        self.img = img
        self.calls = []
        self.admission_code = 0
        self.available_value = 1
        self.submit_value = 0
        self.gambits = {}            # member -> record address (query/set act on it)
        self.record_answer = {}      # member -> what the getter returns (default: the record)
        self.set_effect = None       # None: the game's behaviour; else a function(member, on)
        self.on_query = None
        self.node = 0
        fns = GameFns()
        fns.submit = F_SUBMIT(self._submit)
        fns.reserve = F_RESERVE(self._reserve)
        fns.dismiss = F_DISMISS(lambda flag: self.calls.append(('dismiss', flag)))
        fns.admission = F_ADMISSION(self._admission)
        fns.available = F_AVAILABLE(self._available)
        fns.query = F_QUERY(self._query)
        fns.set = F_SET(self._set)
        fns.record = F_RECORD(self._record)
        fns.indicator = F_INDICATOR(self._indicator)
        fns.sound = F_SOUND(lambda sid: self.calls.append(('sound', sid)) or 0)
        self.fns = fns

    def _submit(self, issuer, target, action, flag):
        self.calls.append(('submit', issuer, target, action, flag))
        return self.submit_value

    def _reserve(self, issuer, target, member, action, flag):
        self.calls.append(('reserve', issuer, target, member, action, flag))
        return self.submit_value

    def _admission(self, actor, action):
        self.calls.append(('admission', actor, action))
        return self.admission_code

    def _available(self, member, action, unused):
        self.calls.append(('available', member, action))
        return self.available_value

    def _query(self, member):
        self.calls.append(('query', member))
        if self.on_query:
            self.on_query()
        record = self.gambits[member]
        return (struct.unpack('<I', ctypes.string_at(record, 4))[0] >> 2) & 1

    def _set(self, member, on):
        self.calls.append(('set', member, on))
        if self.set_effect is not None:
            self.set_effect(member, on)
            return
        record = self.gambits[member]
        value = struct.unpack('<I', ctypes.string_at(record, 4))[0]
        value = (value | 4) if on else (value & ~4)
        ctypes.memmove(record, struct.pack('<I', value & 0xFFFFFFFF), 4)

    def _record(self, member):
        return self.record_answer.get(member, self.gambits.get(member, 0))

    def _indicator(self, ui, source, target):
        self.calls.append(('indicator', ui, source, target))
        return self.node

    def names(self):
        return [c[0] for c in self.calls]


class SubmitWorld:
    """The patch layer installed on the twin (the queue needs it), submit attached, a synthetic game image."""

    def __init__(self):
        self.w = gt.world()
        self.d = declare(self.w.d)
        self.twin = self.w.twin

    def setup(self):
        d = self.d
        self.twin.reset()
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_SubReset()
        d.ILCT_MenuSeatReset(1)
        d.ILCT_MenuSeatReset(2)
        report = self.twin.call('ILCT_PatchInstall')
        assert report.result == OK, report.result
        d.ILCT_SetFixedClock(NOW)
        assert d.ILCT_TickAttach() == 1
        assert d.ILCT_SubAttach(TWIN_BASE) == 1, 'the pinned prologues'
        d.ILCT_GtqSeal()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_GtqSetGeneration(GEN)


_sw = None


def submit_world() -> SubmitWorld:
    global _sw
    if _sw is None:
        _sw = SubmitWorld()
    return _sw


def session_record(seats, facts=0x10, epoch=EPOCH, generation=GEN, leader=0) -> Record:
    rec = Record()
    rec.head.epoch, rec.head.generation, rec.head.flags = epoch, generation, GATE['WORLD']
    rec.facts, rec.phase, rec.leader = facts, 5, leader
    for s in range(4):
        rec.seat[s].member, rec.seat[s].party_slot = 0xFFFF, 0xFF
    for s, (member, a, flags) in seats.items():
        e = rec.seat[s]
        e.flags, e.member, e.handle, e.actor, e.phys = flags, member, 0x100000 | a.index, a.actor, a.phys
    return rec


# ---- static -----------------------------------------------------------------------------------------------------------
@native_test
class StaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(tw.pure_dll())

    def test_struct_mirrors_match_the_module(self):
        sizes = (u32 * 32)()
        n = self.d.ILCT_TgtSizes(sizes, 32)
        self.assertEqual(n, 14)
        for k, layout in enumerate(LAYOUTS):
            self.assertEqual(sizes[7 + k], ctypes.sizeof(layout), layout.__name__)

    def test_every_address_constant_is_tagged(self):
        problems = []
        for path in sorted(SUBMIT_SRC.glob('*.[ch]*')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                problems += [f'{path.name}:{number}: {lit}' for lit in untagged_constants(line)]
        self.assertEqual(problems, [])

    def test_no_wait_anywhere(self):
        # The requester never waits for the game thread: no sleep, no wait, no spin on a result.
        for path in sorted(SUBMIT_SRC.glob('*.cpp')):
            text = path.read_text(encoding='utf-8')
            for forbidden in ('Sleep(', 'WaitFor', 'while (gtq::result', 'SwitchToThread', 'YieldProcessor'):
                self.assertNotIn(forbidden, text, path.name)

    def test_gambit_outcome_and_sounds(self):
        code, final = i32(), u32()
        table = [(GAMBITS_ON, GB_APPLIED, 1), (GAMBITS_OFF, GB_APPLIED, 0)]
        table += [(REJ_GAMBIT - c, c, 0xFF) for c in range(0x10, 0x17)]
        table += [(gt.STALE_EPOCH, GB_STALE_SEQUENCE, 0xFF), (gt.EXPIRED, GB_STALE_SEQUENCE, 0xFF),
                  (REJ_DISABLED, GB_STALE_SEQUENCE, 0xFF)]
        for result, want_code, want_final in table:
            self.d.ILCT_SubGambitOutcome(result, ctypes.byref(code), ctypes.byref(final))
            self.assertEqual((code.value, final.value), (want_code, want_final), result)
        sounds = {tm.EV_CURSOR: 1, tm.EV_TARGET_MOVED: 1, tm.EV_TARGET_GROUP: 0x0A, tm.EV_PAGE_OPENED: 2,
                  tm.EV_TARGET_OPENED: 2, tm.EV_SUBMIT: 2, tm.EV_PAGE_CLOSED: 3, tm.EV_TARGET_CLOSED: 3,
                  tm.EV_CLOSED: 3, tm.EV_OPENED: 4, tm.EV_IGNORED: 0, tm.EV_TOGGLE_GAMBITS: 0}
        for kind, sid in sounds.items():
            self.assertEqual(self.d.ILCT_SubSoundFor(kind), sid, kind)

    def test_the_summon_owner_rule(self):
        s = SummonView()
        s.owner = s.pending = 0xFF
        party = (u16 * 4)(1, 2, 3, 33)
        upd = lambda active, leader, now: self.d.ILCT_SubSummonUpdate(  # noqa: E731
            ctypes.byref(s), active, party, leader, now)
        s.pending, s.pending_until_ms = 2, NOW + 20000
        upd(1, 1, NOW + 20000)
        self.assertEqual((s.active, s.owner, s.source, s.pending), (1, 2, 1, 0xFF), 'the summoner, within 20 s')
        upd(1, 1, NOW + 30000)
        self.assertEqual(s.owner, 2, 'kept while the Esper stays out')
        upd(0, 1, NOW + 31000)
        self.assertEqual((s.active, s.owner), (0, 0xFF))
        s.pending, s.pending_until_ms = 2, NOW
        upd(1, 1, NOW + 1)
        self.assertEqual((s.owner, s.source), (1, 2), 'the window passed: the leader')
        upd(0, 1, NOW + 2)
        upd(1, 33, NOW + 3)
        self.assertEqual(s.owner, 1, 'the leader is the Esper: the first regular in the party')


# ---- the slot, the operations and the stale checks (host, doubles for the game) ------------------------------------
class HostCase(unittest.TestCase):
    def setUp(self):
        self.sw = submit_world()
        self.sw.setup()
        self.d = self.sw.d
        self.img = Image()
        self.gw = GameWorld(self.img)
        self.party = self.gw.party([0, 1, 2])
        for k, a in enumerate(self.party):
            self.bind_record(a, k)
        self.foe = self.gw.add_actor(10, 9, handle_class=0x200000, position=(20.0, 2.0, 30.0))
        self.g = Doubles(self.img)
        self.d.ILCT_SubSetImageBase(self.img.base)
        self.d.ILCT_SubSetFns(ctypes.byref(self.g.fns))
        self.d.ILCT_SubStart(self.img.base, NOW)
        self.now = NOW
        self.publish()

    def tearDown(self):
        self.d.ILCT_SubStop()
        self.d.ILCT_SubSetFns(None)
        self.d.ILCT_TownSessionWithdraw()
        self.d.ILCT_SubReset()
        self.sw.twin.reset()
        self.img.close()

    def bind_record(self, a, member):
        record = self.gw.battle + 8 + member * 0x1C8
        self.img.p64(a.actor + 0x698, record)
        a.battle = record
        a.set_hp(100)
        a.set_status(0)
        return record

    def publish(self, seats=None, **kw):
        seats = seats if seats is not None else {0: (0, self.party[0], SEATF_IN | SEATF_ACTOR),
                                                 1: (1, self.party[1], GUEST)}
        self.rec = session_record(seats, **kw)
        self.d.ILCT_TownSessionPublish(ctypes.byref(self.rec))

    def handle(self, a):
        return 0x100000 | a.index if a in self.party else 0x200000 | a.index

    def submit(self, seat=1, **kw) -> int:
        return self.d.ILCT_SubRequest(seat, ctypes.byref(request(**kw)), self.now)

    def foe_request(self, **kw):
        kw.setdefault('target', self.handle(self.foe))
        kw.setdefault('target_actor', self.foe.actor)
        return self.submit(**kw)

    def slot(self, seat=1) -> SlotView:
        v = SlotView()
        self.d.ILCT_SubSlot(seat, ctypes.byref(v))
        return v

    def stats(self) -> Counters:
        c = Counters()
        self.d.ILCT_SubStats(ctypes.byref(c))
        return c

    def step(self, dt=20):
        self.now += dt
        self.d.ILCT_SetFixedClock(self.now)
        self.d.ILCT_PatchHeartbeat(self.rec.head.epoch)
        self.d.ILCT_SubStep(self.now)

    def drain(self):
        return self.d.ILCT_GtqDrain()


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class SlotStateMachineTest(HostCase):
    def test_request_complete_and_back_to_idle(self):
        self.assertEqual(self.foe_request(), SUB_QUEUED)
        v = self.slot()
        self.assertEqual((v.state, v.kind, v.action, v.epoch, v.generation), (SLOT_QUEUED, RK_ACTION, ATTACK, EPOCH,
                                                                             GEN))
        self.assertEqual(self.foe_request(), SUB_BUSY, 'one request in flight per seat')
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.g.calls, [('admission', self.party[1].actor, ATTACK),
                                        ('submit', 0x100001, self.handle(self.foe), ATTACK, 0)])
        self.step()
        v = self.slot()
        self.assertEqual((v.state, v.last_result, v.completed), (SLOT_IDLE, DONE, 1))
        self.assertEqual(self.foe_request(), SUB_QUEUED, 'idle again')

    def test_no_result_by_the_deadline_quarantines_until_the_same_ticket_returns(self):
        self.assertEqual(self.foe_request(), SUB_QUEUED)
        ticket = self.slot().ticket
        self.step(WAIT_MS - 30)
        self.assertEqual(self.slot().state, SLOT_QUEUED)
        self.step(40)
        v = self.slot()
        self.assertEqual((v.state, v.quarantined), (SLOT_QUARANTINED, 1))
        self.assertEqual(self.foe_request(), SUB_QUARANTINED)
        self.assertEqual(self.d.ILCT_SubDismiss(1, self.now), SUB_QUARANTINED)
        # The late drain finds the job past its ttl: rejected, never run late; the slot is released by that result.
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.g.calls, [], 'never executed late')
        self.step()
        v = self.slot()
        self.assertEqual((v.state, v.released, v.late_result, v.ticket), (SLOT_IDLE, 1, gt.EXPIRED & 0xFFFFFFFF,
                                                                         ticket))
        self.assertEqual(self.foe_request(), SUB_QUEUED)

    def test_a_stalled_game_thread_is_reaped_and_the_quarantine_released(self):
        self.foe_request()
        self.step(WAIT_MS + 10)
        self.assertEqual(self.slot().state, SLOT_QUARANTINED)
        self.step(ACTION_TTL_MS)  # past ttl + margin: the requester's poll reaps the job itself
        v = self.slot()
        self.assertEqual((v.state, v.late_result), (SLOT_IDLE, gt.EXPIRED & 0xFFFFFFFF))
        self.assertEqual(self.g.calls, [])

    def overwrite_result(self):
        """Push and drain 130 newer jobs: the completion table keeps 128 results, so the request's own is gone."""
        ticket = self.slot().ticket
        pushed = 0
        while pushed < 130:
            for _ in range(32):
                t = ctypes.c_uint32()
                self.d.ILCT_GtqPushRaw(0, EPOCH, GEN, 400, self.now, 0, ctypes.byref(t))  # op 0: rejected
                pushed += 1
            while self.d.ILCT_GtqDrain():
                pass
        self.assertEqual(self.d.ILCT_GtqResult(ticket, None), gt.LOST, 'instrument: the result was overwritten')

    def test_a_lost_result_is_released_once_the_requests_deadline_passed(self):
        # A lost result: a newer completion took its slot. The queue is FIFO, so the job had already finished and
        # nothing can still act on it; the seat waits only for its own deadline (ttl + margin), not for a new epoch.
        self.assertEqual(self.foe_request(), SUB_QUEUED)
        self.overwrite_result()
        self.step()
        v = self.slot()
        self.assertEqual((v.state, v.quarantined, self.stats().lost), (SLOT_QUARANTINED, 1, 1))
        self.assertEqual(self.foe_request(), SUB_QUARANTINED, 'refused before the deadline')
        self.step(WAIT_MS)
        v = self.slot()
        self.assertEqual((v.state, v.released, v.late_result), (SLOT_IDLE, 1, gt.LOST & 0xFFFFFFFF))
        self.assertEqual(self.foe_request(), SUB_QUEUED, 'the seat may ask again')

    def test_a_lost_result_is_released_at_once_by_a_new_epoch(self):
        self.assertEqual(self.foe_request(), SUB_QUEUED)
        self.overwrite_result()
        self.step()
        self.assertEqual(self.slot().state, SLOT_QUARANTINED)
        self.publish(epoch=EPOCH + 1)
        self.step()
        v = self.slot()
        self.assertEqual((v.state, v.released, v.late_result), (SLOT_IDLE, 1, gt.LOST & 0xFFFFFFFF))

    def test_refusals_before_the_queue(self):
        self.assertEqual(self.foe_request(seat=4), SUB_NOT_SEAT, 'no fifth seat')
        self.assertEqual(self.foe_request(seat=3), SUB_NO_ISSUER, 'P4 is a seat (0.1.53), here not in session')
        self.assertEqual(self.foe_request(seat=2), SUB_NO_ISSUER, 'seat 3 is not in session')
        self.assertEqual(self.submit(target=0), SUB_BAD_REQUEST)
        self.assertEqual(self.submit(kind=RK_RESERVE, reserve=0x28), SUB_BAD_REQUEST)
        self.assertEqual(self.submit(action=DISMISS_ACTION, target=1), SUB_BAD_REQUEST, 'Dismiss has its own route')
        self.publish({0: (0, self.party[0], SEATF_IN | SEATF_ACTOR), 1: (1, self.party[1], SEATF_IN | SEATF_ACTOR)})
        self.assertEqual(self.foe_request(), SUB_NO_ISSUER, 'a parked guest')
        self.publish()
        self.d.ILCT_PatchApplySubsystems(0xFF & ~0x10)
        self.assertEqual(self.foe_request(), SUB_NOT_LIVE, 'menus subsystem off')
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_SubForceEnabled(0)
        self.assertEqual(self.foe_request(), SUB_NOT_LIVE, 'the functions failed their check')
        self.d.ILCT_SubForceEnabled(-1)
        self.assertEqual(self.foe_request(), SUB_QUEUED)


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class StaleRejectionTest(HostCase):
    def run_one(self, change=None, **kw):
        self.assertEqual(self.foe_request(**kw) if 'target' not in kw else self.submit(**kw), SUB_QUEUED)
        if change:
            change()
        self.assertEqual(self.drain(), 1)
        self.step()
        return self.slot().last_result

    def test_queue_rejections(self):
        cases = [('another generation', lambda: self.d.ILCT_GtqSetGeneration(GEN + 1), gt.STALE_GENERATION),
                 ('another epoch', lambda: self.d.ILCT_PatchHeartbeat(EPOCH + 1), gt.STALE_EPOCH)]
        for label, change, want in cases:
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.assertEqual(self.foe_request(), SUB_QUEUED)
                change()
                self.drain()
                self.d.ILCT_SubStep(self.now + 1)
                self.assertEqual((self.slot().last_result, self.g.calls), (want, []), label)

    def test_operation_rejections_call_nothing(self):
        cases = [
            ('the seat now names another character',
             lambda: self.publish({0: (0, self.party[0], SEATF_IN), 1: (2, self.party[2], GUEST)}), REJ_STALE_ISSUER),
            ('the actor no longer carries the handle', lambda: self.party[1].set_handle(0x300001), REJ_STALE_ISSUER),
            ("the actor's record is not the party block's", lambda: self.img.p64(self.party[1].actor + 0x698, self.img.alloc(64)),
             REJ_STALE_ISSUER),
            ('the record moved under the publisher (another epoch in the record)',
             lambda: self.publish(epoch=EPOCH + 1), REJ_NO_WORLD),
            ('KO', lambda: self.party[1].set_status(ST_KO), REJ_STATUS),
            ('Sleep', lambda: self.party[1].set_status(0, ST_SLEEP), REJ_STATUS),
            ('Berserk and not Attack', None, None),
            ('the target died and its slot was reused', lambda: self.foe.set_handle(0x300000 | self.foe.index),
             REJ_STALE_TARGET),
            ('the target is gone from the manager', lambda: self.gw.remove_actor(self.foe.index), REJ_STALE_TARGET),
            ('admission: MP short', lambda: setattr(self.g, 'admission_code', 0x13), REJ_ADMISSION - 0x13),
            ('admission: gil short', lambda: setattr(self.g, 'admission_code', 0x15), REJ_ADMISSION - 0x15),
        ]
        for label, change, want in cases:
            if change is None:
                continue
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.assertEqual(self.foe_request(), SUB_QUEUED)
                change()
                self.drain()
                self.step()
                self.assertEqual(self.slot().last_result, want, label)
                self.assertNotIn('submit', self.g.names(), label)
        self.tearDown()
        self.setUp()
        self.party[1].set_status(ST_BERSERK)
        self.assertEqual(self.foe_request(action=CURE), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual(self.slot().last_result, REJ_STATUS, 'Berserk: Attack only')
        self.assertEqual(self.foe_request(), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual(self.slot().last_result, DONE, 'Berserk: Attack goes through')

    def test_a_world_target_must_still_be_alive_unless_the_action_revives(self):
        """A foe chosen while alive that dies before the game thread runs the job is refused (nothing is called);
        a revival may target it, and a party target keeps the picker's own rule."""
        cases = [('a foe that died', RF_INDICATOR | RF_WORLD, lambda: self.foe.set_hp(0), REJ_STALE_TARGET),
                 ('a foe with the KO bit', RF_INDICATOR | RF_WORLD, lambda: self.foe.set_status(ST_KO),
                  REJ_STALE_TARGET),
                 ('a foe alive (instrument)', RF_INDICATOR | RF_WORLD, None, DONE),
                 ('a revival on a KO foe', RF_INDICATOR | RF_WORLD | RF_REVIVE, lambda: self.foe.set_hp(0), DONE),
                 ('not a world target (party rule)', RF_INDICATOR, lambda: self.foe.set_hp(0), DONE)]
        for label, flags, change, want in cases:
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.assertEqual(self.foe_request(flags=flags), SUB_QUEUED)
                if change:
                    change()
                self.drain()
                self.step()
                self.assertEqual(self.slot().last_result, want, label)
                self.assertEqual('submit' in self.g.names(), want == DONE, label)

    def test_magicks_and_technicks_pass_the_games_availability_check(self):
        self.g.available_value = 0
        self.assertEqual(self.foe_request(action=CURE, flags=RF_LICENSED), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual(self.slot().last_result, REJ_UNAVAILABLE)
        self.assertEqual(self.g.calls, [('available', 1, CURE)])
        self.g.calls.clear()
        self.assertEqual(self.foe_request(action=POTION, flags=0), SUB_QUEUED)
        self.drain()
        self.assertNotIn('available', self.g.names(), 'items are not checked against licences')

    def test_the_game_refusing_the_submit(self):
        self.g.submit_value = -1
        self.foe_request()
        self.drain()
        self.step()
        self.assertEqual(self.slot().last_result, REJ_GAME_REFUSED)

    def test_self_and_reserve_routes(self):
        self.assertEqual(self.submit(action=POTION, target=0x100001, flags=0), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual(self.g.calls[-1], ('submit', 0x100001, 0x100001, POTION, 0), 'self: target = issuer')
        self.assertEqual(self.submit(action=POTION, kind=RK_RESERVE, reserve=5, flags=0), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual(self.g.calls[-1], ('reserve', 0x100001, 0, 5, POTION, 0))
        self.assertEqual(self.slot().last_result, DONE)
        self.assertEqual(self.submit(action=POTION, kind=RK_RESERVE, reserve=2, flags=0), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual(self.slot().last_result, REJ_STALE_TARGET, 'member 2 is in the formation')

    def test_dry_mode_runs_nothing(self):
        self.foe_request()
        self.d.ILCT_PatchSetMode(gt.DRY)
        self.d.ILCT_GtqFlush(gt.DISARMED)
        self.step()
        self.assertEqual((self.slot().last_result, self.g.calls), (gt.DISARMED, []))


# ---- Gambits ---------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class GambitTest(HostCase):
    def setUp(self):
        super().setUp()
        self.member = 1
        self.actor = self.party[1]
        self.record = self.actor.battle
        self.g.gambits[self.member] = self.record
        self.img.p32(self.record, 0x00F00000)  # gambits off, unrelated bits set

    def job(self, member=1, handle=None, actor=None, physics=None, record=None, epoch=EPOCH, generation=GEN) -> Job:
        j = Job()
        j.op, j.issuer, j.epoch, j.generation, j.ttl_ms = OP_GAMBITS, 1, epoch, generation, 400
        handle = 0x100001 if handle is None else handle
        j.arg[0] = handle | (member << 32)
        j.arg[1] = self.actor.actor if actor is None else actor
        j.arg[2] = self.actor.phys if physics is None else physics
        j.arg[3] = self.record if record is None else record
        return j

    def op(self, j) -> int:
        return self.d.ILCT_SubOp(OP_GAMBITS, ctypes.byref(j))

    def word(self):
        return struct.unpack('<I', ctypes.string_at(self.record, 4))[0]

    def test_the_identity_proof_table(self):
        # (label, prepare(), job arguments, expected result); every case starts from a fresh world.
        cases = [
            ('applied: off -> on', None, {}, GAMBITS_ON),
            ('a story guest or Esper', None, {'member': 6}, REJ_GAMBIT - GB_INVALID_MEMBER),
            ('another epoch', None, {'epoch': EPOCH + 1}, REJ_GAMBIT - GB_STALE_SEQUENCE),
            ('the handle names another actor now', lambda: self.actor.set_handle(0x300001), {},
             REJ_GAMBIT - GB_STALE_IDENTITY),
            ('the manager entry points elsewhere',
             lambda: self.img.p64(self.actor.entry + 0x30, self.img.alloc(0x1C8)), {},
             REJ_GAMBIT - GB_STALE_IDENTITY),
            ('another physics object', None, {'physics': 'other'}, REJ_GAMBIT - GB_STALE_PHYSICS),
            ("the actor's record changed", lambda: self.img.p64(self.actor.actor + 0x698, self.img.alloc(0x1C8)), {},
             REJ_GAMBIT - GB_STALE_RECORD),
            ('the getter disagrees', lambda: self.g.record_answer.__setitem__(1, self.img.alloc(0x1C8)), {},
             REJ_GAMBIT - GB_STALE_RECORD),
            ('the job names another record', None, {'record': 'other'}, REJ_GAMBIT - GB_STALE_RECORD),
            ('KO', lambda: self.actor.set_status(ST_KO), {}, REJ_GAMBIT - GB_BLOCKED),
            ('Stone', lambda: self.actor.set_status(0, ST_STONE), {}, REJ_GAMBIT - GB_BLOCKED),
            ('the setter did nothing', lambda: setattr(self.g, 'set_effect', lambda m, on: None), {},
             REJ_GAMBIT - GB_VERIFY_FAILED),
            ('the setter touched another bit',
             lambda: setattr(self.g, 'set_effect', lambda m, on: self.img.p32(self.record, self.word() | 4 | 1)), {},
             REJ_GAMBIT - GB_VERIFY_FAILED),
        ]
        for label, prepare, kw, want in cases:
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                if kw.get('physics') == 'other':
                    kw = {'physics': self.actor.phys + 0x10}
                elif kw.get('record') == 'other':
                    kw = {'record': self.img.alloc(0x1C8)}
                if prepare:
                    prepare()
                self.assertEqual(self.op(self.job(**kw)), want, label)
                setter_ran = 'set' in self.g.names()
                self.assertEqual(setter_ran, want in (GAMBITS_ON, REJ_GAMBIT - GB_VERIFY_FAILED), label)
                if want == GAMBITS_ON:
                    self.assertEqual(self.word(), 0x00F00004, 'bit 2 set, every other bit kept')

    def test_the_proof_is_repeated_before_the_setter(self):
        self.g.on_query = lambda: self.actor.set_handle(0x300001)  # the identity changes during the query
        self.assertEqual(self.op(self.job()), REJ_GAMBIT - GB_STALE_IDENTITY)
        self.assertEqual(self.g.names(), ['query'], 'queried, then refused before the setter')

    def test_toggle_through_the_slot_updates_the_model_and_plays_its_sound(self):
        self.d.ILCT_MenuGambitObserve(1, 0)
        self.assertEqual(self.d.ILCT_SubGambits(1, self.now), SUB_QUEUED)
        self.assertEqual(self.d.ILCT_SubGambits(1, self.now), SUB_BUSY)
        self.drain()
        self.step()
        state = tm.Gambits()
        self.d.ILCT_MenuGambitState(1, ctypes.byref(state))
        self.assertEqual((state.enabled, state.last_result, state.sound, state.in_flight), (1, GB_APPLIED, 0x12, 0))
        self.assertEqual(self.word(), 0x00F00004)
        self.step()                # the sound job
        self.drain()
        self.assertEqual(self.g.calls[-1], ('sound', 0x12))
        self.d.ILCT_SubGambits(1, self.now)
        self.drain()
        self.step()
        self.d.ILCT_MenuGambitState(1, ctypes.byref(state))
        self.assertEqual((state.enabled, state.sound), (0, 0x13))

    def test_model_refusals_never_reach_the_queue(self):
        self.assertEqual(self.d.ILCT_SubGambits(1, self.now), SUB_GAMBITS_REFUSED, 'state not read yet')
        self.d.ILCT_MenuGambitObserve(1, 0)
        self.actor.set_status(ST_KO)
        self.assertEqual(self.d.ILCT_SubGambits(1, self.now), SUB_GAMBITS_REFUSED)
        state = tm.Gambits()
        self.d.ILCT_MenuGambitState(1, ctypes.byref(state))
        self.assertEqual(state.last_result, GB_BLOCKED)
        self.assertEqual(self.drain(), 0)

    def test_a_late_result_is_not_applied(self):
        self.d.ILCT_MenuGambitObserve(1, 0)
        self.d.ILCT_SubGambits(1, self.now)
        self.step(WAIT_MS + 10)
        self.assertEqual(self.slot().state, SLOT_QUARANTINED)
        self.step(ACTION_TTL_MS)
        state = tm.Gambits()
        self.d.ILCT_MenuGambitState(1, ctypes.byref(state))
        self.assertEqual((state.in_flight, state.last_result, state.sound), (0, GB_STALE_SEQUENCE, 0))
        self.assertEqual(self.g.calls, [])


# ---- summon owner and Dismiss ----------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class DismissTest(HostCase):
    def summon_out(self):
        self.gw.layout([0, 1, 2, 27])
        self.publish(facts=0x10 | FACT_SUMMON)

    def test_the_summoner_owns_the_esper_and_only_it_dismisses(self):
        self.publish({0: (0, self.party[0], SEATF_IN | SEATF_ACTOR), 1: (1, self.party[1], GUEST),
                      2: (2, self.party[2], GUEST)})
        self.assertEqual(self.foe_request(action=ESPER, flags=0), SUB_QUEUED)
        self.drain()
        self.step()
        s = SummonView()
        self.d.ILCT_SubSummon(ctypes.byref(s))
        self.assertEqual((s.pending, s.pending_until_ms), (1, self.now + 20000))
        self.gw.layout([0, 1, 2, 27])
        self.rec.facts |= FACT_SUMMON
        self.d.ILCT_TownSessionPublish(ctypes.byref(self.rec))
        self.step()
        self.assertEqual(self.d.ILCT_SubOwner(), 1)
        self.assertEqual(self.d.ILCT_SubDismiss(2, self.now), SUB_NOT_OWNER)
        self.assertEqual(self.d.ILCT_SubDismiss(1, self.now), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual((self.slot().last_result, self.g.calls[-1]), (DONE, ('dismiss', 0)))

    def test_the_operation_rechecks_owner_and_summon(self):
        self.summon_out()
        self.step()
        self.assertEqual(self.d.ILCT_SubOwner(), 0, 'no request: the leader owns it')
        self.publish({0: (0, self.party[0], SEATF_IN | SEATF_ACTOR), 1: (0, self.party[0], GUEST)},
                     facts=0x10 | FACT_SUMMON)
        self.assertEqual(self.d.ILCT_SubDismiss(1, self.now), SUB_QUEUED)
        self.publish(facts=0x10)  # the summon ended (and seat 2 names member 1 again) before the frame
        self.drain()
        self.step()
        self.assertIn(self.slot().last_result, (REJ_STALE_ISSUER, REJ_NO_SUMMON, REJ_NOT_OWNER))
        self.assertNotIn('dismiss', self.g.names())


# ---- the combat indicator --------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class IndicatorTest(HostCase):
    def setUp(self):
        super().setUp()
        self.node = self.img.alloc(0x400)
        self.g.node = self.node

    def node_state(self, source, target, state, flags):
        self.img.p32(self.node + 0x2D0, source)
        self.img.p32(self.node + 0x2D4, target)
        self.img.p32(self.node + 0x2EC, flags)
        self.img.p16(self.node + 0x2F0, state)

    def view(self) -> IndicatorView:
        v = IndicatorView()
        self.d.ILCT_SubIndicator(1, ctypes.byref(v))
        return v

    def submitted(self):
        self.foe_request()
        self.drain()
        self.step(1)  # delivered at self.now
        return self.now

    def test_first_try_after_75_ms_then_shown(self):
        t0 = self.submitted()
        v = self.view()
        self.assertEqual((v.active, v.next_try_ms, v.deadline_ms), (1, t0 + 75, t0 + 30000))
        self.step(74)
        self.assertEqual(self.drain(), 0, 'not before 75 ms')
        self.step(1)
        self.assertEqual(self.view().in_flight, 1)
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.g.calls[-1][2:], (0x100001, self.handle(self.foe)))
        self.node_state(0x100001, self.handle(self.foe), 1, 0)
        self.step(5)
        self.assertEqual((self.view().node, self.view().attempts), (self.node, 1))
        self.step(100)
        self.assertEqual(self.view().active, 1, 'a starting node gets 400 ms')
        self.node_state(0x100001, self.handle(self.foe), 2, 1)
        self.step(20)
        v = self.view()
        self.assertEqual((v.active, v.last), (0, 1))
        self.assertEqual(self.stats().indicators_shown, 1)

    def test_a_node_that_does_not_take_is_retried_after_350_ms(self):
        self.submitted()
        self.step(75)
        self.drain()
        self.node_state(0x100001, self.handle(self.foe), 1, 0)
        self.step(5)
        attempt = self.view().attempt_ms
        self.step(401)
        v = self.view()
        self.assertEqual((v.node, v.next_try_ms), (0, self.now + 350), 'grace over: retry in 350 ms')
        self.node_state(0, 0, 0, 0)
        self.step(349)
        self.assertEqual(self.drain(), 0)
        self.step(1)
        self.assertEqual(self.drain(), 1)
        self.step(1)
        self.assertEqual(self.view().attempts, 2)
        self.assertGreater(self.view().attempt_ms, attempt)

    def test_gives_up_after_30_s_and_when_the_target_dies(self):
        self.g.node = 0
        t0 = self.submitted()
        self.now = t0 + 29999
        self.step(0)
        self.assertEqual(self.view().active, 1)
        while self.view().in_flight:
            self.drain()
            self.step(0)
        self.step(1)
        self.assertEqual((self.view().active, self.view().last), (0, 2))
        self.submitted()
        self.foe.set_hp(0)
        self.step(75)
        self.assertEqual((self.view().active, self.view().last), (0, 3), 'the target died')

    def test_on_the_issuer_it_is_tried_once_at_once(self):
        self.g.node = 0
        self.submit(target=0x100001)
        self.drain()
        self.step(1)
        v = self.view()
        self.assertEqual((v.active, v.next_try_ms), (1, self.now))
        self.step(0)
        self.drain()
        self.step(1)
        self.assertEqual((self.view().active, self.view().last, self.view().attempts), (0, 1, 1))

    def test_reserve_targets_show_no_indicator(self):
        self.submit(action=POTION, kind=RK_RESERVE, reserve=5, flags=RF_INDICATOR)
        self.drain()
        self.step(1)
        self.assertEqual(self.view().active, 0)


# ---- sounds and model events -----------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class SoundAndEventTest(HostCase):
    def test_sounds_per_seat_one_pending_allowed_ids(self):
        self.d.ILCT_SubSetSounds(0b0001)
        self.d.ILCT_SubPlay(1, 2)
        self.step()
        self.assertEqual(self.drain(), 0, "seat 2's sounds are off")
        self.d.ILCT_SubSetSounds(0b0011)
        self.d.ILCT_SubPlay(1, 2)
        self.d.ILCT_SubPlay(0, 4)      # dropped: one pending at a time (the first wins)
        self.d.ILCT_SubPlay(0, 0x99)   # dropped: not a menu sound
        self.step()
        self.assertEqual(self.drain(), 1)
        self.assertEqual(self.g.calls, [('sound', 2)])
        self.assertEqual(self.stats().sounds_dropped, 3)

    def menu_with(self, entries):
        c = tm.hand_catalog(entries)
        r = tm.Commit()
        self.d.ILCT_MenuSeatCommit(1, ctypes.byref(c), ctypes.byref(r))

    def test_a_submit_event_on_a_foe_requires_it_alive_unless_the_entry_revives(self):
        """handle_event marks a Foes/Neutrals target as a world target and a revival entry as a revival: a foe that
        dies before the game thread runs the job refuses Attack, and lets the revival through."""
        phoenix = 0x5A
        for action, flags, want in ((ATTACK, tm.EF_ENABLED, REJ_STALE_TARGET),
                                    (phoenix, tm.EF_ENABLED | tm.EF_REVIVE, DONE)):
            with self.subTest(action=action):
                self.foe.set_hp(100)
                self.menu_with({tm.PG_ROOT: [tm.hand_entry(action, flags=flags, groups=1 << tg.TG_FOES,
                                                           default_group=tg.TG_FOES)]})
                shown = tg.CandidateList()
                inp = tg.picker([tg.foe(self.handle(self.foe), 1.0, actor=self.foe.actor)], issuer_handle=0x100001)
                self.d.ILCT_TgtBuild(ctypes.byref(inp), ctypes.byref(tg.rules()), ctypes.byref(shown))
                e = tm.Event()
                e.kind, e.action, e.group, e.index = tm.EV_SUBMIT, action, tg.TG_FOES, 0
                self.assertEqual(self.d.ILCT_SubEvent(1, ctypes.byref(e), ctypes.byref(shown), self.now), SUB_QUEUED)
                self.foe.set_hp(0)
                self.drain()
                self.step()
                self.assertEqual(self.slot().last_result, want)
                self.step(WAIT_MS)

    def test_a_submit_event_uses_the_candidates_the_seat_was_shown(self):
        self.menu_with({tm.PG_ROOT: [tm.hand_entry(ATTACK, groups=1 << tg.TG_FOES, default_group=tg.TG_FOES),
                                     tm.hand_entry(kind=tm.EK_PAGE, page=tm.PG_WHITE, role=tm.ROLE_SCHOOL)],
                        tm.PG_WHITE: [tm.hand_entry(CURE, groups=1 << tg.TG_PARTY)]})
        shown = tg.CandidateList()
        inp = tg.picker([tg.foe(self.handle(self.foe), 1.0, actor=self.foe.actor)], issuer_handle=0x100001)
        self.d.ILCT_TgtBuild(ctypes.byref(inp), ctypes.byref(tg.rules()), ctypes.byref(shown))
        e = tm.Event()
        e.kind, e.action, e.group, e.index = tm.EV_SUBMIT, ATTACK, tg.TG_FOES, 0
        self.assertEqual(self.d.ILCT_SubEvent(1, ctypes.byref(e), ctypes.byref(shown), self.now), SUB_QUEUED)
        self.drain()
        self.step()
        self.assertEqual(self.g.calls[-1], ('submit', 0x100001, self.handle(self.foe), ATTACK, 0))
        e.action, e.group = CURE, 4  # self
        self.assertEqual(self.d.ILCT_SubEvent(1, ctypes.byref(e), ctypes.byref(shown), self.now), SUB_QUEUED)
        self.drain()
        self.assertIn(('available', 1, CURE), self.g.calls, 'a White Magick: the availability check')
        self.assertEqual(self.g.calls[-1], ('submit', 0x100001, 0x100001, CURE, 0))
        self.step()
        e.action = 0x77  # not in the catalog
        self.assertEqual(self.d.ILCT_SubEvent(1, ctypes.byref(e), ctypes.byref(shown), self.now), SUB_BAD_REQUEST)
        e.action, e.group, e.index = ATTACK, tg.TG_FOES, 5
        self.assertEqual(self.d.ILCT_SubEvent(1, ctypes.byref(e), ctypes.byref(shown), self.now), SUB_BAD_REQUEST)


# ---- unicorn: the UT-01 drain on the hooked twin ---------------------------------------------------------------------
SCR = pe.SCRATCH
BLOCK_U, MANAGER_U, ENTRY_U, ACTOR_U, ROOT_U, FOE_ENTRY_U, FOE_U, FOE_BATTLE_U = (
    SCR + 0x600000, SCR + 0x610000, SCR + 0x620000, SCR + 0x630000, SCR + 0x640000, SCR + 0x650000, SCR + 0x660000,
    SCR + 0x670000)
ISSUER_INDEX, FOE_INDEX = 1, 10
ISSUER_H, FOE_H = 0x100000 | ISSUER_INDEX, 0x200000 | FOE_INDEX


@native_test
@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class Ut01DrainEmulationTest(unittest.TestCase):
    """The UT-01 tick on the hooked twin drains OP_MENU_ACTION. A stale job reaches no game function; a valid one
    calls admission, then the manual submit once (entered with RSP = 8 mod 16), then the stock UI tick."""

    @classmethod
    def setUpClass(cls):
        cls.sw = submit_world()
        cls.sw.setup()
        cls.emu = MoveEmulator(cls.sw.twin)
        cls.seen = []
        X = pe.X

        def admission(uc):
            uc.reg_write(X.UC_X86_REG_RAX, 0)
            cls.seen.append(('admission', uc.reg_read(X.UC_X86_REG_RCX), uc.reg_read(X.UC_X86_REG_RDX) & 0xFFFF))

        def submit(uc):
            cls.seen.append(('submit', uc.reg_read(X.UC_X86_REG_RCX) & 0xFFFFFFFF,
                             uc.reg_read(X.UC_X86_REG_RDX) & 0xFFFFFFFF, uc.reg_read(X.UC_X86_REG_R8) & 0xFFFF,
                             uc.reg_read(X.UC_X86_REG_R9) & 0xFFFFFFFF, uc.reg_read(X.UC_X86_REG_RSP) & 0xF))
            uc.reg_write(X.UC_X86_REG_RAX, 0)
        cls.emu.answers[FN_ADMISSION] = admission
        cls.emu.answers[FN_SUBMIT] = submit

    @classmethod
    def tearDownClass(cls):
        cls.emu.answers.pop(FN_ADMISSION, None)
        cls.emu.answers.pop(FN_SUBMIT, None)
        cls.sw.d.ILCT_TownSessionWithdraw()
        cls.sw.twin.reset()

    def world(self, uc, issuer_handle=ISSUER_H):
        record = BLOCK_U + 8 + 1 * 0x1C8
        rows = [(TWIN_BASE + BATTLE_STATE_PTR - 0x120000, 0, 'Q', BLOCK_U),
                (TWIN_BASE + ACTOR_MANAGER_PTR - 0x120000, 0, 'Q', MANAGER_U),
                (MANAGER_U, 0, 'I', 16), (MANAGER_U, 8 + 8 * FOE_INDEX, 'Q', FOE_ENTRY_U),
                (MANAGER_U, 8 + 8 * ISSUER_INDEX, 'Q', ENTRY_U),
                (ENTRY_U, 0x30, 'Q', ACTOR_U), (FOE_ENTRY_U, 0x30, 'Q', FOE_U),
                (ACTOR_U, 0x08, 'I', issuer_handle), (ACTOR_U, 0x698, 'Q', record), (ACTOR_U, 0x10, 'Q', ROOT_U),
                (record, 0x3C, 'I', 0), (record, 0x64, 'I', 0), (record, 0x48, 'I', 100),
                (FOE_U, 0x08, 'I', FOE_H), (FOE_U, 0x698, 'Q', FOE_BATTLE_U), (FOE_BATTLE_U, 0x48, 'I', 50)]
        for obj, offset, fmt, value in rows:
            uc.mem_write(obj + offset, struct.pack('<' + fmt, value))

    def push(self, generation=GEN):
        d = self.sw.d
        d.ILCT_GtqReset()
        d.ILCT_TickReset()
        d.ILCT_SubReset()
        assert d.ILCT_TickAttach() == 1
        assert d.ILCT_SubAttach(TWIN_BASE) == 1
        d.ILCT_GtqSeal()
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_SetFixedClock(NOW)
        d.ILCT_PatchHeartbeat(EPOCH)
        d.ILCT_GtqSetGeneration(GEN)
        d.ILCT_SubSetImageBase(TWIN_BASE)
        d.ILCT_SubSetFns(None)
        rec = Record()
        rec.head.epoch, rec.head.generation, rec.head.flags = EPOCH, GEN, GATE['WORLD']
        for s in range(4):
            rec.seat[s].member, rec.seat[s].party_slot = 0xFFFF, 0xFF
        rec.seat[1].flags, rec.seat[1].member, rec.seat[1].handle, rec.seat[1].actor = GUEST, 1, ISSUER_H, ACTOR_U
        d.ILCT_TownSessionPublish(ctypes.byref(rec))
        d.ILCT_SubStart(TWIN_BASE, NOW)
        r = request(target=FOE_H, target_actor=FOE_U)
        self.assertEqual(d.ILCT_SubRequest(1, ctypes.byref(r), NOW), SUB_QUEUED)
        v = SlotView()
        d.ILCT_SubSlot(1, ctypes.byref(v))
        d.ILCT_GtqSetGeneration(generation)
        return v.ticket

    def run_tick(self, ticket, **world):
        emu, uc, twin = self.emu, self.emu.uc, self.sw.twin
        row = self.sw.w.ut01
        site = TWIN_BASE + twin.rows[row]['rva']
        completions = self.sw.w.addr.completions + (ticket & (gt.COMPLETIONS - 1)) * 32
        emu.refresh()
        emu.tick_clock(NOW + 5)
        self.world(uc, **world)
        self.seen.clear()
        state = pe.random_state(random.Random(0x3535))
        b = emu.run(site, lambda x: not (TWIN_BASE <= x < TWIN_BASE + TWIN_SIZE) or site <= x < site + 5, state,
                    probe=lambda u: struct.unpack('<Ii', u.mem_read(completions + 8, 8)))
        self.assertEqual(emu.unexpected, [])
        got_ticket, result = b['probe']
        return b, (result if got_ticket == ticket else gt.PENDING)

    def test_a_stale_generation_calls_only_the_stock_tick(self):
        ticket = self.push(generation=GEN + 1)
        b, result = self.run_tick(ticket)
        self.assertEqual(b['calls'], [UI_TICK])
        self.assertEqual((result, self.seen), (gt.STALE_GENERATION, []))

    def test_a_stale_issuer_calls_only_the_stock_tick(self):
        ticket = self.push()
        b, result = self.run_tick(ticket, issuer_handle=0x300001)   # the actor was rebuilt under another handle
        self.assertEqual(b['calls'], [UI_TICK])
        self.assertEqual((result, self.seen), (REJ_STALE_ISSUER, []))

    def test_a_valid_submit_calls_admission_then_the_submit_then_the_stock_tick(self):
        ticket = self.push()
        b, result = self.run_tick(ticket)
        self.assertEqual(b['calls'], [FN_ADMISSION, FN_SUBMIT, UI_TICK])
        self.assertEqual(self.seen, [('admission', ACTOR_U, ATTACK), ('submit', ISSUER_H, FOE_H, ATTACK, 0, 8)])
        self.assertEqual(result, DONE)


if __name__ == '__main__':
    unittest.main()
