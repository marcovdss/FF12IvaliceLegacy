"""Host tests for the HUD frame driver, the presenter stamp and the HudSnapshot (native/coop/src/hud/driver.*,
snapshot.*) and for the co-op menus' presenter policy while they are suspended (src/frontend). No game run, nothing
deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin the patch layer installs on.

* static: the passes run in the fixed order party extras, Target Info, log, menus last; only the driver writes the
  presenter stamp; the game-thread code never blocks, allocates or calls the C runtime; the boot samples the snapshot
  after the menus each beat and withdraws it at exit;
* the presenter policy (pure): live = run; dead = hold the menus while they are suspended for a native screen (or one
  is up and may still be adopted), idle otherwise and always in a load, cutscene, party-down recovery or a Party
  request in flight;
* the frame driver on the frame hook (host memory, synthetic draw context): it stamps on every frame where the menu
  layer was presented, an empty one included, and never when the snapshot is missing, torn, of another epoch, stale or
  in an unknown layout, when co-op is off, the UI is hidden, RENDER or FIREWALL_MENUS is not live, the hook is not
  attached, the context is not the window's or faded out, the UI is not ready, or the frame is over budget; a fault in
  a pass or in the replay latches RENDER and withdraws the stamp at once;
* compose: the passes' order in the frame (menus last, drawn over the rest), the menu pass skipped without
  FIREWALL_MENUS, the Diagnostics test frame appended underneath with its strings remapped;
* the snapshot sampler on a synthetic game: party rows in HUD order with seat, leader, HP, statuses and only the timed
  statuses' durations, level, weakness, handle and target; targets (seat targets first, then the party's, deduplicated
  and capped) with the raw Anti-Libra bit; seat targets with since_ms; Libra; coherence (a party header or a record
  that moves under the read is read again, and left out, never mixed, if it keeps moving); the seqlock never hands
  out a mix of two snapshots (a writer thread against a reader); publication follows the world, co-op and RENDER;
* the layer end to end (the co-op menus, the firewall and the frame hook in one process): the hook drawing every frame
  makes the presenter live with no menu open, so P1's A opens its menu; without the hook nothing opens; a stall of
  250 ms, or 300 ms of hidden UI, gives P1 the stock menus back; a fault gives them back on the next beat; two seconds
  in the main menu hold the suspended menus and they resume where they were; presenter death outside a suspension, a
  load during the hold, or a resume with nothing drawn close them;
* unicorn on the hooked twin: the passes reach the native text renderer in order with the menus last, an empty layer
  stamps without drawing, and the detour disarmed, the gate closed or the UI hidden is the stock log draw with no
  stamp.
"""
from __future__ import annotations

import ctypes
import re
import struct
import unittest
from pathlib import Path

import test_coop_firewall as tf
import test_coop_frontend as tfe
import test_coop_game_thread as gt
import test_coop_hud as th
import test_coop_patch_engine as pe
from coop_native_support import native_test, quiet_faulthandler
from coop_session_support import FACT, GATE, PHASE, SEATF, GameWorld, Image, Record
from test_coop_patch_engine import GAME, TWIN_BASE

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER

# ---- facts and constants ------------------------------------------------------------------------------------------------
RENDER_FLAGS, RENDER_UI = 0x22C7F90, 0x08
TICK_SCALE = 0x1DFE0B4
LAYOUT_TABLE, LAYOUT_STRIDE, LEADER = 0x5A48, 0x12, 0x5AA4
RECORDS, RECORD_SIZE = 0x08, 0x1C8
REC_HP_MAX, REC_LASTING, REC_WEAKNESS, REC_HP, REC_TIMED = 0x24, 0x3C, 0x40, 0x48, 0x64
REC_AUG_TIMED, REC_AUG_LASTING, REC_DURATIONS, REC_LEVEL = 0x68, 0x78, 0xBC, 0x1C2
LIBRA = 1 << 30
PASS_PARTY, PASS_TI, PASS_LOG, PASS_MENUS, PASS_COUNT = 0, 1, 2, 3, 4
PASS_NAMES = ['party', 'target info', 'log', 'menus']
GATE_OK, GATE_NO_SNAPSHOT, GATE_COOP_OFF, GATE_UI_HIDDEN = 0, 1, 2, 3
COMPOSE_OK, COMPOSE_NO_SNAPSHOT, COMPOSE_FAULTED, COMPOSE_BUSY = 0, 1, 2, 3
SNAP_WORLD, SNAP_PARTY, SNAP_LIBRA_KNOWN, SNAP_LIBRA, SNAP_TICKS = 0x1, 0x2, 0x4, 0x8, 0x10
UNIT_PRESENT, UNIT_ACTOR, UNIT_LEADER, UNIT_ANTI_LIBRA, UNIT_PARTY = 0x1, 0x2, 0x4, 0x8, 0x10
LAYOUT_COUCH, LAYOUT_ONLINE = 0, 1
PM_RUN, PM_HOLD, PM_IDLE = 0, 1, 2
RENDER = 0x40
LIVE_MS, STALE_MS = 250, 250
EPOCH, NOW = gt.EPOCH, gt.NOW


def subsystem_bit(name: str) -> int:
    text = (COOP / 'include' / 'ilc' / 'bridge_abi.h').read_text(encoding='utf-8')
    m = re.search(rf'X\(ILC_SUBSYS_, {name}, (0x[0-9A-Fa-f]+|\d+),', text)
    assert m, name
    return int(m.group(1), 0)


FIREWALL_MENUS = subsystem_bit('FIREWALL_MENUS')
assert subsystem_bit('RENDER') == RENDER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


Unit = S('Unit', [('handle', u32), ('member', u16), ('flags', u8), ('seat', u8), ('hp', u32), ('hp_max', u32),
                  ('status_timed', u32), ('status_lasting', u32), ('target', u32), ('level', u8), ('weakness', u8),
                  ('unit_type', u8), ('reserved', u8), ('duration', i32 * 32)])
SeatTarget = S('SeatTarget', [('handle', u32), ('group', u8), ('on_page', u8), ('reserved', u16), ('since_ms', u64)])
Snapshot = S('Snapshot', [('epoch', u32), ('flags', u32), ('generation', u64), ('sampled_ms', u64), ('layout', u8),
                          ('member_count', u8), ('leader', u16), ('tick_scale', u32), ('members', Unit * 4),
                          ('target_count', u8), ('reserved', u8 * 7), ('targets', Unit * 8),
                          ('seats', SeatTarget * 4)])
DrvCounters = S('DrvCounters', [('frames', u64), ('declined', u64 * 4), ('composed', u64), ('torn', u64),
                                ('pass_faults', u64), ('stamped', u64), ('not_stamped', u64), ('appended', u64),
                                ('last_stamp_ms', u64), ('last_passes', u64), ('trace', u8 * 4), ('traced', u8),
                                ('layout', u8), ('reserved', u8 * 2), ('kept_commands', u64), ('dropped_frames', u64),
                                ('pass_commands', u16 * 4), ('kept_log', u64), ('menus_no_view', u64)])
SnapCounters = S('SnapCounters', [(n, u64) for n in ('samples', 'withdrawn', 'party_torn', 'record_retries',
                                                     'records_torn', 'targets_dropped')])
PresenterIn = S('PresenterIn', [(n, u8) for n in ('live', 'suspended', 'source', 'observed_open', 'native_open',
                                                  'settling', 'down', 'party_busy')])
LAYOUTS = [ctypes.sizeof(Snapshot), ctypes.sizeof(Unit), ctypes.sizeof(SeatTarget), ctypes.sizeof(DrvCounters),
           ctypes.sizeof(SnapCounters), PASS_COUNT, 8, ctypes.sizeof(PresenterIn)]

BETWEEN = ctypes.CFUNCTYPE(None, u32, u64)

# The target mode window and registry 0 (the aim the snapshot feeds as P1's seat target).
TMODE_PTR, REGISTRY = 0x2B47730, 0x2098E10

SIGNATURES = {
    'ILCT_HudDrvSizes': ([P(u32), u32], u32), 'ILCT_HudDrvReset': ([], None),
    'ILCT_HudDrvStats': ([P(DrvCounters)], None), 'ILCT_HudDrvCountersAddress': ([], u64),
    'ILCT_HudDrvSetProbe': ([u32], None), 'ILCT_HudDrvInjectFault': ([u32], None),
    'ILCT_HudDrvGate': ([u32, u64], i32), 'ILCT_HudDrvCompose': ([u32, u64, P(th.Frame), P(th.Frame)], i32),
    'ILCT_HudDrvPresented': ([u64], None), 'ILCT_HudDrvFaulted': ([], None),
    'ILCT_HudDrvPassName': ([u32], ctypes.c_char_p), 'ILCT_HudDrvStampAddress': ([], u64),
    'ILCT_HudSnapPublish': ([P(Snapshot)], None), 'ILCT_HudSnapWithdraw': ([], None),
    'ILCT_HudSnapRead': ([P(Snapshot)], i32), 'ILCT_HudSnapMeta': ([P(u32), P(u64)], i32),
    'ILCT_HudSnapStart': ([u64, u64], None), 'ILCT_HudSnapStep': ([u64], None), 'ILCT_HudSnapStop': ([], None),
    'ILCT_HudSnapSample': ([u64, P(Snapshot)], i32), 'ILCT_HudSnapSetImageBase': ([u64], None),
    'ILCT_HudSnapSeatTarget': ([u32, u32, u32, u32], None), 'ILCT_HudSnapUseSeatTargets': ([i32], None),
    'ILCT_HudSnapSetBetween': ([BETWEEN], None), 'ILCT_HudSnapStats': ([P(SnapCounters)], None),
    'ILCT_HudSnapLayout': ([], u32), 'ILCT_HudSnapStress': ([u32, P(u64)], i32),
    'ILCT_FePresenterMode': ([P(PresenterIn)], u32), 'ILCT_FePresenterDrawn': ([], u64),
    'ILCT_CtlReset': ([], None), 'ILCT_CtlKill': ([i32], None), 'ILCT_CtlStep': ([u64], None),
    'ILCT_CtlState': ([], u32), 'ILCT_TownSessionPublish': ([ctypes.c_void_p], None),
    'ILCT_TownSessionWithdraw': ([], None), 'ILCT_PatchSubsystems': ([], u32),
    'ILCT_TiGbReset': ([], None), 'ILCT_TiGbForcePins': ([i32], None),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def snapshot(epoch=EPOCH, sampled=NOW, flags=SNAP_WORLD, layout=LAYOUT_COUCH) -> Snapshot:
    s = Snapshot()
    s.epoch, s.flags, s.generation, s.sampled_ms, s.layout, s.leader = epoch, flags, 1, sampled, layout, 0xFFFF
    return s


def texts(frame) -> list[bytes]:
    pool = ctypes.string_at(ctypes.addressof(frame) + th.Frame.text.offset, th.TEXT_POOL)  # the field stops at a NUL
    out = []
    for k in range(frame.count):
        c = frame.cmds[k]
        if c.kind == th.CMD_TEXT:
            out.append(pool[c.text:].split(b'\0', 1)[0])
    return out


def publish_menu_view(d, epoch=EPOCH, built=NOW):
    """An empty co-op menu view (no window open), as the co-op thread publishes it on every beat while the world is up:
    the menu pass draws from it, so a presented frame stamps the presenter (no usable view: no stamp)."""
    import test_coop_hud_menus as thm   # imported here: that module imports this one
    d.ILCT_HmPublish.argtypes, d.ILCT_HmPublish.restype = [P(thm.MenuView)], None
    d.ILCT_HmPublish(ctypes.byref(thm.menu_view({}, epoch=epoch, built=built)))


def withdraw_menu_view(d):
    d.ILCT_HmWithdraw.argtypes, d.ILCT_HmWithdraw.restype = [], None
    d.ILCT_HmWithdraw()


def stats(d) -> DrvCounters:
    c = DrvCounters()
    d.ILCT_HudDrvStats(ctypes.byref(c))
    return c


def snap_stats(d) -> SnapCounters:
    c = SnapCounters()
    d.ILCT_HudSnapStats(ctypes.byref(c))
    return c


# ---- static -------------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_the_passes_run_in_the_fixed_order_menus_last(self):
        text = (HUD_SRC / 'driver.cpp').read_text(encoding='utf-8')
        table = re.search(r'PASSES\[PASS_COUNT\] = \{(.*?)\};', text, re.S).group(1)
        self.assertEqual(re.findall(r'\{(PASS_\w+), (\w+)\}', table),
                         [('PASS_PARTY', 'pass_party_extras'), ('PASS_TARGET_INFO', 'pass_target_info'),
                          ('PASS_LOG', 'pass_battle_log'), ('PASS_MENUS', 'pass_menus')])
        self.assertIn('static_assert(passes_in_order()', text)
        header = (HUD_SRC / 'driver.h').read_text(encoding='utf-8')
        self.assertRegex(header, r'PASS_PARTY = 0,.*\n.*PASS_TARGET_INFO = 1,.*\n.*PASS_LOG = 2,.*\n.*PASS_MENUS = 3')

    def test_only_the_driver_stamps_the_presenter(self):
        def stamps(path: Path) -> int:
            code = [ln for ln in path.read_text(encoding='utf-8').splitlines() if not ln.lstrip().startswith('//')]
            return sum(1 for ln in code if re.search(r'\bnote_drawn\(', ln) and 'void note_drawn' not in ln)
        writers = {p.relative_to(COOP).as_posix(): stamps(p) for p in (COOP / 'src').rglob('*.[ch]*')}
        writers = {name: n for name, n in writers.items() if n}
        self.assertEqual(writers, {'src/hud/driver.cpp': 2}, 'the stamp, and its withdrawal on a fault')

    def test_only_the_driver_reports_seats_left_out(self):
        def calls(path: Path) -> int:
            code = [ln for ln in path.read_text(encoding='utf-8').splitlines() if not ln.lstrip().startswith('//')]
            return sum(1 for ln in code if re.search(r'\bnote_dropped\(', ln) and 'void note_dropped' not in ln)
        writers = {p.relative_to(COOP).as_posix(): calls(p) for p in (COOP / 'src').rglob('*.[ch]*')}
        self.assertEqual({n: k for n, k in writers.items() if k}, {'src/hud/driver.cpp': 1})
        text = (HUD_SRC / 'driver.cpp').read_text(encoding='utf-8')
        body = text[text.index('void presented(uint64_t now_ms)'):text.index('void faulted()')]
        self.assertLess(body.index('note_dropped('), body.index('note_drawn('), 'per seat first, then the stamp')
        passes = text[text.index('void run_passes(void* arg)'):text.index('// Appends `src`')]
        self.assertLess(passes.index('menus_draw::prepare('), passes.index('p.fn(g_cv'), 'the floor is kept first')
        self.assertIn('g_cv.reserve(0, 0)', passes)

    def test_the_game_thread_code_never_blocks_allocates_or_calls_the_c_runtime(self):
        for name in ('driver.cpp', 'replay.cpp'):
            text = (HUD_SRC / name).read_text(encoding='utf-8')
            for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                              'copy_to_caller', 'memcpy(', 'memset(', 'session::read', 'game_reader'):
                self.assertNotIn(forbidden, text, f'{name}: {forbidden}')
        snap = (HUD_SRC / 'snapshot.cpp').read_text(encoding='utf-8')
        for forbidden in ('copy_to_caller', 'VirtualProtect', 'memcpy(', 'Sleep', 'WaitFor'):
            self.assertNotIn(forbidden, snap, forbidden)

    def test_the_boot_samples_after_the_menus_and_withdraws_at_exit(self):
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        order = [boot.index(s) for s in ('frontend::step(now);', 'hud::step(now);', 'hud::snapshot::step(now);',
                                         'interaction::step(now);')]
        self.assertEqual(order, sorted(order))
        self.assertLess(boot.index('hud::stop();'), boot.index('hud::snapshot::stop();'))
        self.assertIn('hud::snapshot::start(g_host.image_base', boot)
        build = (COOP / 'build.py').read_text(encoding='utf-8')
        self.assertIn("'src/hud/driver.cpp', 'src/hud/snapshot.cpp'", build)

    def test_the_layout_mode_is_couch_with_online_as_a_seam(self):
        header = (HUD_SRC / 'snapshot.h').read_text(encoding='utf-8')
        self.assertIn('LAYOUT_COUCH = 0,', header)
        self.assertIn('LAYOUT_ONLINE = 1,', header)
        body = (HUD_SRC / 'snapshot.cpp').read_text(encoding='utf-8')
        fn = re.search(r'uint8_t layout_for_session\(\)\n\{(.*?)\n\}', body, re.S).group(1)
        self.assertIn('return LAYOUT_COUCH;', fn)
        self.assertNotIn('LAYOUT_ONLINE', body, 'nothing selects the online layout in this build')

    def test_snapshot_facts_are_the_known_offsets(self):
        facts = (HUD_SRC / 'snapshot_facts.h').read_text(encoding='utf-8')
        for name, value in (('RENDER_FLAGS', RENDER_FLAGS), ('BATTLE_STATE_PTR', 0x2EBF190), ('TICK_SCALE', TICK_SCALE)):
            self.assertRegex(facts, rf'constexpr uint64_t {name} = 0x{value:X};\s+// VA', name)
        for name, value in (('LAYOUT_TABLE', LAYOUT_TABLE), ('LEADER_MEMBER', LEADER), ('REC_DURATIONS', REC_DURATIONS),
                            ('REC_HP', REC_HP), ('REC_HP_MAX', REC_HP_MAX), ('REC_LEVEL', REC_LEVEL),
                            ('REC_WEAKNESS', REC_WEAKNESS), ('MEMBER_RECORD_SIZE', RECORD_SIZE)):
            self.assertRegex(facts, rf'constexpr uint32_t {name} = 0x{value:X};', name)


# ---- the presenter policy (pure) ------------------------------------------------------------------------------------------
@native_test
class PresenterPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(gt.world().d)

    def mode(self, **kw) -> int:
        v = PresenterIn()
        for k, value in kw.items():
            setattr(v, k, value)
        return self.d.ILCT_FePresenterMode(ctypes.byref(v))

    def test_layouts(self):
        out = (u32 * 16)()
        n = self.d.ILCT_HudDrvSizes(out, 16)
        self.assertEqual(list(out[:n]), LAYOUTS)

    def test_the_rule_over_every_input(self):
        import itertools
        for bits in itertools.product((0, 1), repeat=7):
            live, suspended, party_source, observed, native, settling, down = bits
            for busy in (0, 1):
                kw = dict(live=live, suspended=suspended, source=tfe.SRC_PARTY if party_source else tfe.SRC_EXTERNAL,
                          observed_open=observed, native_open=native, settling=settling, down=down, party_busy=busy)
                if live:
                    want = PM_RUN
                elif settling or down or busy:
                    want = PM_IDLE
                elif suspended:
                    want = PM_HOLD if (not party_source or observed) else PM_IDLE
                else:
                    want = PM_HOLD if native else PM_IDLE
                with self.subTest(**kw):
                    self.assertEqual(self.mode(**kw), want)

    def test_named_cases(self):
        self.assertEqual(self.mode(live=1, settling=1, down=1), PM_RUN, 'a live presenter changes nothing')
        self.assertEqual(self.mode(suspended=1, source=tfe.SRC_EXTERNAL, observed_open=1), PM_HOLD, 'main menu')
        self.assertEqual(self.mode(suspended=1, source=tfe.SRC_EXTERNAL, observed_open=1, settling=1), PM_IDLE,
                         'a load during the main menu')
        self.assertEqual(self.mode(native_open=1), PM_HOLD, 'a screen up, not adopted yet')
        self.assertEqual(self.mode(), PM_IDLE, 'presenter death outside a suspension')
        self.assertEqual(self.mode(suspended=1, source=tfe.SRC_PARTY, observed_open=0), PM_IDLE,
                         'a Party request whose screen never showed')
        self.assertEqual(self.mode(suspended=1, source=tfe.SRC_PARTY, observed_open=1), PM_HOLD, 'the Party screen')
        self.assertEqual(self.mode(suspended=1, source=tfe.SRC_EXTERNAL, observed_open=1, party_busy=1), PM_IDLE)


# ---- the frame driver on the frame hook --------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class DriverHookTest(unittest.TestCase):
    """h0 and its after-handler on host memory with a published snapshot. Every case either composes an empty frame
    (no game call) or stops before the first game call (the fault drills), so nothing of the game's runs here."""

    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)
        tfe.declare(cls.d)
        cls.mgr = ctypes.create_string_buffer(0x4100)
        cls.ctx = (u8 * 0xA0)()
        cls.arena = (u64 * 3)()

    def setUp(self):
        self.world_up()

    def world_up(self, reset=True):
        d = self.d
        self.hw.setup(reset_hud=reset)
        d.ILCT_HudSetTestDraw(0)
        d.ILCT_CtlReset()
        d.ILCT_FePresenterReset()
        a = ctypes.addressof
        struct.pack_into('<Q', self.mgr, 0x38, a(self.ctx))
        struct.pack_into('<h', self.ctx, 0x58, 0x80)
        struct.pack_into('<h', self.ctx, 0x5A, 0x80)
        self.arena[0], self.arena[1], self.arena[2] = 0x10_0000, 0x10_0000, 0x10_0000 + th.RING
        self.hw.twin.poke(th.RENDER_ARENA, struct.pack('<Q', a(self.arena)))
        self.hw.twin.poke(th.UI_READY, struct.pack('<I', 1))
        self.ui(True)
        self.publish()

    def tearDown(self):
        d = self.d
        d.ILCT_HudInjectFault(0)
        d.ILCT_HudDrvReset()
        d.ILCT_CtlReset()
        d.ILCT_FePresenterReset()
        withdraw_menu_view(d)
        self.hw.twin.reset()

    def ui(self, shown: bool):
        self.hw.twin.poke(RENDER_FLAGS, struct.pack('<I', RENDER_UI if shown else 0))

    def publish(self, **kw):
        """The snapshot, and the co-op thread's (empty) menu view of the same beat."""
        self.d.ILCT_HudSnapPublish(ctypes.byref(snapshot(**kw)))
        publish_menu_view(self.d, epoch=kw.get('epoch', EPOCH), built=kw.get('sampled', NOW))

    def call(self, epoch=EPOCH) -> int:
        c = th.HookCtx()
        c.rcx, c.rdx = ctypes.addressof(self.mgr), ctypes.addressof(self.ctx)
        c.index = self.hw.row
        with quiet_faulthandler():
            r = self.d.ILCT_HudCall(ctypes.byref(c), epoch)
            if r == 2:
                self.d.ILCT_HudAfter(ctypes.byref(c), epoch)
        return r

    def drawn(self) -> int:
        return self.d.ILCT_FePresenterDrawn()

    def test_no_snapshot_never_stamps_and_the_hook_stays_stock(self):
        self.d.ILCT_HudSnapWithdraw()
        before = stats(self.d)
        self.assertEqual(self.call(), 0, 'nothing to draw: RUN_ORIGINAL')
        after = stats(self.d)
        self.assertEqual(after.declined[GATE_NO_SNAPSHOT] - before.declined[GATE_NO_SNAPSHOT], 1)
        self.assertEqual((after.stamped, self.drawn()), (0, 0))

    def test_an_empty_layer_is_presented_and_stamped(self):
        before = self.hw.counters()
        self.assertEqual(self.call(), 2, 'called around: the layer is drawn this frame')
        c = stats(self.d)
        self.assertEqual((c.frames, c.composed, c.stamped, c.last_stamp_ms), (1, 1, 1, NOW))
        self.assertEqual(list(c.trace[:c.traced]), [PASS_PARTY, PASS_TI, PASS_LOG, PASS_MENUS])
        self.assertEqual(self.drawn(), NOW, 'presenter::note_drawn(now)')
        after = self.hw.counters()
        self.assertEqual(after.drawn - before.drawn, 0, 'nothing replayed: no menu open, nothing to show')
        self.assertEqual(self.d.ILCT_FePresenterLive(NOW + LIVE_MS), 1)
        self.assertEqual(self.d.ILCT_FePresenterLive(NOW + LIVE_MS + 1), 0)

    def test_every_reason_not_to_stamp(self):
        d = self.d
        a = ctypes.addressof
        cases = {
            'snapshot of another epoch': lambda: self.publish(epoch=EPOCH + 1),
            'stale snapshot': lambda: self.publish(sampled=NOW - STALE_MS - 1),
            'snapshot from the future': lambda: self.publish(sampled=NOW + STALE_MS + 1),
            'snapshot withdrawn': lambda: d.ILCT_HudSnapWithdraw(),
            'snapshot without a world': lambda: self.publish(flags=0),
            'unknown layout mode': lambda: self.publish(layout=2),
            'co-op off (kill switch)': lambda: (d.ILCT_CtlKill(1), d.ILCT_CtlStep(NOW)),
            'UI hidden': lambda: self.ui(False),
            'RENDER disarmed': lambda: d.ILCT_PatchApplySubsystems(0xFF & ~RENDER),
            'RENDER faulted': lambda: d.ILCT_PatchFault(RENDER),
            'FIREWALL_MENUS disarmed': lambda: d.ILCT_PatchApplySubsystems(0xFF & ~FIREWALL_MENUS),
            'not attached': lambda: d.ILCT_HudSetPinsOk(0),
            'not the window context': lambda: struct.pack_into('<Q', self.mgr, 0x38, a(self.ctx) + 0x10),
            'faded out': lambda: struct.pack_into('<h', self.ctx, 0x58, 0),
            'UI not ready': lambda: self.hw.twin.poke(th.UI_READY, struct.pack('<I', 0)),
            'no menu view (withdrawn)': lambda: withdraw_menu_view(d),
            'stale menu view (co-op thread stalled)': lambda: publish_menu_view(d, built=NOW - STALE_MS - 1),
            'menu view of another epoch': lambda: publish_menu_view(d, epoch=EPOCH + 1),
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.world_up()
                prepare()
                self.call()
                self.assertEqual((stats(d).stamped, self.drawn()), (0, 0), name)
        self.world_up()
        self.publish(sampled=NOW - STALE_MS)
        self.call()
        self.assertEqual(self.drawn(), NOW, 'exactly SNAPSHOT_STALE_MS old still presents (instrument)')

    def test_a_menu_pass_without_a_usable_view_presents_nothing_of_the_menus(self):
        # The co-op thread stalled past VIEW_STALE_MS (snapshot still fresh): the menu pass runs on no view, its
        # bit stays out of the completed passes and the frame stamps nothing; a fresh view stamps again.
        d = self.d
        publish_menu_view(d, built=NOW - STALE_MS - 1)
        self.assertEqual(self.call(), 2, 'the layer is still composed and drawn')
        c = stats(d)
        self.assertEqual(list(c.trace[:c.traced]), [PASS_PARTY, PASS_TI, PASS_LOG, PASS_MENUS], 'the menu pass ran')
        self.assertEqual(c.last_passes & (1 << PASS_MENUS), 0, 'but did not complete with a view')
        self.assertEqual((c.composed, c.stamped, c.not_stamped, c.menus_no_view), (1, 0, 1, 1))
        self.assertEqual(self.drawn(), 0)
        publish_menu_view(d, built=NOW - STALE_MS)
        self.call()
        c = stats(d)
        self.assertEqual((c.stamped, c.menus_no_view, self.drawn()), (1, 1, NOW), 'exactly VIEW_STALE_MS old presents')
        self.assertEqual(c.last_passes, 0b1111)

    def test_without_firewall_menus_the_menu_pass_is_skipped_and_nothing_is_stamped(self):
        self.d.ILCT_PatchApplySubsystems(0xFF & ~FIREWALL_MENUS)
        self.call()
        c = stats(self.d)
        self.assertEqual(list(c.trace[:c.traced]), [PASS_PARTY, PASS_TI, PASS_LOG], 'party, TI and log still run')
        self.assertEqual((c.composed, c.stamped, c.not_stamped), (1, 0, 1))

    def test_a_fault_in_any_pass_latches_render_and_withdraws_the_stamp(self):
        d = self.d
        for p in range(PASS_COUNT):
            with self.subTest(pass_=PASS_NAMES[p]):
                self.world_up()
                self.call()
                self.assertEqual(self.drawn(), NOW, 'stamped by a good frame first')
                d.ILCT_HudDrvInjectFault(p)
                before = self.hw.counters()
                self.call()
                self.assertEqual(self.hw.counters().faults - before.faults, 1)
                self.assertEqual(stats(d).pass_faults, 1)
                self.assertEqual(self.drawn(), 0, 'withdrawn at once: the menus idle on the next beat')
                self.assertTrue(d.ILCT_PatchSubsystems() & RENDER, 'still armed')
                d.ILCT_HudDrvInjectFault(PASS_COUNT)
                self.assertEqual(self.call(), 0, 'RENDER faulted: stock from the next frame')
                self.assertEqual(self.drawn(), 0)

    def test_a_fault_in_the_replay_withdraws_the_stamp(self):
        d = self.d
        self.call()
        self.assertEqual(self.drawn(), NOW)
        d.ILCT_HudDrvSetProbe(1 << PASS_MENUS)  # the frame has a command: the replay runs (and faults at once)
        d.ILCT_HudInjectFault(1)
        before = self.hw.counters()
        self.call()
        self.assertEqual(self.hw.counters().faults - before.faults, 1)
        self.assertEqual((self.drawn(), stats(d).stamped), (0, 1), 'the faulted frame did not stamp, and withdrew')

    def test_a_frame_over_budget_is_not_presented(self):
        d = self.d
        d.ILCT_HudDrvSetProbe(1 << PASS_MENUS)
        d.ILCT_HudInjectFault(1)  # an allowed frame faults before any game call; a refused one is not replayed
        self.call()               # the ring baseline (it faults: RENDER latched)
        d.ILCT_HudInjectFault(0)
        self.world_up(reset=False)
        d.ILCT_HudDrvSetProbe(1 << PASS_MENUS)
        self.arena[1] = 0x10_0000 + th.RING_SAFE + 0x10000
        before = self.hw.counters()
        self.call()
        self.assertEqual(self.hw.counters().declined_budget - before.declined_budget, 1)
        self.assertEqual(self.drawn(), 0, 'nothing drawn, so the menu layer was not presented')

    # The ring baseline across empty frames: the first menu frame after a run of empty, stamped frames is charged
    # one frame's traffic, never the whole run's. allowed = the fault drill fires; refused = declined_budget.
    def ring_baseline(self):
        d = self.d
        d.ILCT_HudDrvSetProbe(1 << PASS_MENUS)
        d.ILCT_HudInjectFault(1)
        self.call()                  # a frame with a command: the ring baseline at the ring's start (RENDER latched)
        d.ILCT_HudInjectFault(0)
        self.world_up(reset=False)   # a fresh install; the cursor back at the baseline

    def empty_frames(self, frames: int, step: int) -> int:
        self.d.ILCT_HudDrvSetProbe(0)  # no menu open, nothing to show: an empty layer, presented and stamped
        stamped = 0
        for k in range(frames):
            self.arena[1] = 0x10_0000 + (k + 1) * step
            stamped += self.call() == 2 and self.drawn() == NOW
        return stamped

    def menu_frame(self) -> tuple[int, int]:
        d = self.d
        d.ILCT_HudDrvSetProbe(1 << PASS_MENUS)  # a menu opens: the layer has a command
        d.ILCT_HudInjectFault(1)
        before = self.hw.counters()
        self.call()
        after = self.hw.counters()
        d.ILCT_HudInjectFault(0)
        return after.declined_budget - before.declined_budget, after.faults - before.faults

    def test_empty_frames_keep_the_ring_baseline_fresh(self):
        self.ring_baseline()
        self.assertEqual(self.empty_frames(40, 0x40000), 40, '40 empty frames, each stamped')  # 10 MiB in all
        b = th.Budget()
        self.d.ILCT_HudBudget(ctypes.byref(b))
        self.assertEqual(b.frame_use, 0x40000, 'each empty frame measured its own 256 KiB')
        self.assertEqual(self.menu_frame(), (0, 1), 'the first menu frame is allowed (one frame of traffic)')

    def test_empty_frames_without_traffic_allow_the_menu_frame(self):
        self.ring_baseline()
        self.assertEqual(self.empty_frames(40, 0), 40)
        self.assertEqual(self.menu_frame(), (0, 1), 'control: no traffic at all')

    def test_one_frame_over_budget_is_still_refused_after_empty_frames(self):
        self.ring_baseline()
        self.empty_frames(3, 0x40000)
        self.arena[1] += th.RING_SAFE + 0x10000   # the menu frame's own frame is over the safe quarter
        self.assertEqual(self.menu_frame(), (1, 0), 'instrument: real traffic in one frame is still refused')

    def test_a_hook_call_that_draws_nothing_still_samples_the_ring(self):
        d = self.d
        self.ring_baseline()
        d.ILCT_HudSnapWithdraw()           # no snapshot: the hook runs the stock log draw, no layer
        for k in range(40):
            self.arena[1] = 0x10_0000 + (k + 1) * 0x40000
            self.assertEqual(self.call(), 0)
        self.publish()
        self.assertEqual(self.menu_frame(), (0, 1), 'the baseline moved with every call, drawn or not')

    def test_a_seat_left_out_of_a_presented_frame_is_not_shown(self):
        d = self.d
        d.ILCT_HmForceDropped.argtypes, d.ILCT_HmForceDropped.restype = [u32], None
        try:
            d.ILCT_HmForceDropped(0b100)   # the menu pass reports seat 3's window left out
            self.assertEqual(self.call(), 2)
            self.assertEqual(self.drawn(), NOW, 'the layer is still presented')
            self.assertEqual([d.ILCT_FeSeatShown(s) for s in range(4)], [1, 1, 0, 1])
            self.assertEqual(stats(d).dropped_frames, 1)
            self.assertEqual(d.ILCT_FeSeatLost(2, NOW + LIVE_MS), 0)
            self.assertEqual(d.ILCT_FeSeatLost(2, NOW + LIVE_MS + 1), 1, 'left out for longer than LIVE_MS')
            d.ILCT_HmForceDropped(0)
            self.call()
            self.assertEqual([d.ILCT_FeSeatShown(s) for s in range(4)], [1, 1, 1, 1], 'drawn again: shown')
            d.ILCT_HmForceDropped(0b100)
            d.ILCT_PatchApplySubsystems(0xFF & ~FIREWALL_MENUS)
            self.call()
            self.assertEqual(d.ILCT_FeSeatShown(2), 1, 'no menu pass, no report')
        finally:
            d.ILCT_HmForceDropped(0)

    def test_the_test_draw_alone_never_stamps(self):
        d = self.d
        d.ILCT_HudSnapWithdraw()
        d.ILCT_HudSetTestDraw(1)
        self.hw.publish()
        d.ILCT_HudInjectFault(1)  # the test frame alone is replayed (and faults before any game call)
        self.call()
        self.assertEqual((stats(d).stamped, self.drawn()), (0, 0))


# ---- compose: order, menus last, the test frame underneath ---------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class ComposeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)
        tfe.declare(cls.d)

    def setUp(self):
        self.hw.setup()
        self.d.ILCT_HudSetTestDraw(0)
        self.d.ILCT_HudSnapPublish(ctypes.byref(snapshot()))
        publish_menu_view(self.d)

    def tearDown(self):
        self.d.ILCT_HudDrvReset()
        withdraw_menu_view(self.d)
        self.hw.twin.reset()

    def compose(self, test_frame=None):
        out = th.Frame()
        r = self.d.ILCT_HudDrvCompose(EPOCH, NOW, ctypes.byref(test_frame) if test_frame else None, ctypes.byref(out))
        return r, out

    def test_pass_names(self):
        self.assertEqual([self.d.ILCT_HudDrvPassName(p).decode() for p in range(PASS_COUNT)], PASS_NAMES)
        self.assertIsNone(self.d.ILCT_HudDrvPassName(PASS_COUNT))

    def test_the_passes_draw_in_order_with_the_menus_last(self):
        self.d.ILCT_HudDrvSetProbe(0xF)
        r, f = self.compose()
        self.assertEqual(r, COMPOSE_OK)
        self.assertEqual(texts(f), [th.encoded(n) for n in PASS_NAMES], 'menus are the last commands: drawn on top')
        c = stats(self.d)
        self.assertEqual((list(c.trace[:c.traced]), c.last_passes, c.layout), ([0, 1, 2, 3], 0xF, LAYOUT_COUCH))

    def test_stub_passes_draw_nothing(self):
        r, f = self.compose()
        self.assertEqual((r, f.count, f.text_used), (COMPOSE_OK, 0, 0))

    def test_the_test_frame_goes_underneath_with_its_strings_remapped(self):
        d = self.d
        d.ILCT_HudBegin(EPOCH, NOW)
        d.ILCT_HudText(760, 150, b'P1 test line', th.rgba(0x78, 0x5F, 0x19), 100, 0)
        d.ILCT_HudSprite(th.ATLAS_PARTYTOP_4_C, 3, 0, 18, 700, 140, th.WHITE, 0, 0)
        d.ILCT_HudText(760, 190, b'P2 test line', th.rgba(0x23, 0x41, 0x78), 100, 0)
        test = self.hw.frame()
        d.ILCT_HudDrvSetProbe(0xF)
        r, f = self.compose(test)
        self.assertEqual(r, COMPOSE_OK)
        self.assertEqual(texts(f), [th.encoded('P1 test line'), th.encoded('P2 test line')] +
                         [th.encoded(n) for n in PASS_NAMES])
        self.assertEqual([f.cmds[k].kind for k in range(f.count)],
                         [th.CMD_TEXT, th.CMD_SPRITE, th.CMD_TEXT] + [th.CMD_TEXT] * 4)
        self.assertEqual((f.cmds[1].x, f.cmds[1].cell), (700, 18))
        self.assertEqual(stats(d).appended, 3)

    def test_without_firewall_menus_no_menu_commands(self):
        self.d.ILCT_PatchApplySubsystems(0xFF & ~FIREWALL_MENUS)
        self.d.ILCT_HudDrvSetProbe(0xF)
        r, f = self.compose()
        self.assertEqual(texts(f), [th.encoded(n) for n in PASS_NAMES[:3]])

    def test_the_online_seam_is_accepted_and_an_unknown_mode_is_not(self):
        self.d.ILCT_HudSnapPublish(ctypes.byref(snapshot(layout=LAYOUT_ONLINE)))
        r, _ = self.compose()
        self.assertEqual((r, stats(self.d).layout), (COMPOSE_OK, LAYOUT_ONLINE), 'the passes get the mode')
        self.d.ILCT_HudSnapPublish(ctypes.byref(snapshot(layout=2)))
        r, _ = self.compose()
        self.assertEqual(r, COMPOSE_NO_SNAPSHOT)

    def test_no_coherent_snapshot_composes_nothing(self):
        self.d.ILCT_HudSnapWithdraw()
        r, _ = self.compose()
        self.assertEqual((r, stats(self.d).torn), (COMPOSE_NO_SNAPSHOT, 1))

    def test_presented_stamps_only_after_a_completed_menu_pass(self):
        d = self.d
        d.ILCT_FePresenterReset()
        d.ILCT_HudDrvPresented(NOW)
        self.assertEqual(d.ILCT_FePresenterDrawn(), 0, 'no compose this frame: nothing to stamp')
        self.compose()
        d.ILCT_HudDrvPresented(NOW)
        self.assertEqual(d.ILCT_FePresenterDrawn(), NOW)
        d.ILCT_HudDrvPresented(NOW + 5)
        self.assertEqual(d.ILCT_FePresenterDrawn(), NOW, 'one stamp per composed frame')
        d.ILCT_HudDrvFaulted()
        self.assertEqual(d.ILCT_FePresenterDrawn(), 0)
        d.ILCT_FePresenterReset()


# ---- the snapshot sampler on a synthetic game -------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class SnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)

    def setUp(self):
        d = self.d
        self.hw.setup(attach=False)
        d.ILCT_HudDrvReset()
        d.ILCT_CtlReset()
        self.img = Image()
        self.gw = GameWorld(self.img)
        self.img.put_global(TICK_SCALE, struct.pack('<I', 2))
        self.gw.layout([0, 1, 2], [2, 0, 1])  # the world's order and the HUD's row order differ
        self.gw.leader(0)
        self.actors = []
        for slot, member in enumerate([0, 1, 2]):
            a = self.gw.add_actor(slot, slot, position=(10.0 + slot, 2.0, 30.0))
            self.img.p64(a.actor + 0x698, self.record(member))
            self.actors.append(a)
        for member, hp in ((0, 900), (1, 450), (2, 1200)):
            self.set_record(member, hp=hp, hp_max=1500, level=30 + member, weakness=1 << member)
        self.foe = self.gw.add_actor(10, 9, handle_class=0x200000, position=(20.0, 2.0, 30.0))
        self.img.p8(self.foe.root + 0x0E, 0x11)   # unit type 1 (a foe) in the low nibble
        d.ILCT_HudSnapSetImageBase(self.img.base)
        d.ILCT_HudSnapUseSeatTargets(1)
        self.publish_session()

    def tearDown(self):
        d = self.d
        d.ILCT_TiGbReset()
        d.ILCT_HudSnapSetBetween(BETWEEN(0))
        d.ILCT_HudDrvReset()
        d.ILCT_TownSessionWithdraw()
        d.ILCT_CtlReset()
        self.hw.twin.reset()
        self.img.close()

    def record(self, member) -> int:
        return self.gw.battle + RECORDS + member * RECORD_SIZE

    def set_record(self, member=None, address=None, hp=None, hp_max=None, timed=None, lasting=None, level=None,
                   weakness=None, durations=None, anti_libra=None):
        rec = self.record(member) if address is None else address
        img = self.img
        if hp is not None:
            img.p32(rec + REC_HP, hp)
        if hp_max is not None:
            img.p32(rec + REC_HP_MAX, hp_max)
        if timed is not None:
            img.p32(rec + REC_TIMED, timed)
        if lasting is not None:
            img.p32(rec + REC_LASTING, lasting)
        if level is not None:
            img.p8(rec + REC_LEVEL, level)
        if weakness is not None:
            img.p8(rec + REC_WEAKNESS, weakness)
        for sid, ticks in (durations or {}).items():
            img.put(rec + REC_DURATIONS + 4 * sid, struct.pack('<i', ticks))
        if anti_libra is not None:
            img.p8(rec + anti_libra + 5, 0x02)

    def publish_session(self, epoch=EPOCH, gates=GATE['WORLD']):
        rec = Record()
        rec.head.epoch, rec.head.generation, rec.head.flags = epoch, gt.GEN, gates
        rec.facts, rec.phase, rec.leader = FACT['CONTROL'], PHASE['FIELD'], 0
        for s in range(4):
            e = rec.seat[s]
            e.member, e.party_slot = 0xFFFF, 0xFF
        for s, member in ((0, 0), (1, 2)):  # P1 holds member 0, P2 member 2; member 1 is AI
            e = rec.seat[s]
            e.flags = SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['ACTOR']
            e.member, e.handle, e.party_slot = member, 0x100000 | member, member
        self.rec = rec
        self.d.ILCT_TownSessionPublish(ctypes.byref(rec))

    def sample(self, now=NOW) -> Snapshot:
        s = Snapshot()
        self.assertEqual(self.d.ILCT_HudSnapSample(now, ctypes.byref(s)), 1)
        return s

    def test_party_rows_in_hud_order(self):
        self.set_record(2, timed=(1 << 18) | (1 << 25) | (1 << 7), lasting=1 << 25,
                        durations={18: 9000, 25: 1234, 7: 600, 5: 777})
        self.actors[0].set_action(target=0x20000A)
        s = self.sample()
        self.assertEqual(s.flags & (SNAP_WORLD | SNAP_PARTY | SNAP_TICKS), SNAP_WORLD | SNAP_PARTY | SNAP_TICKS)
        self.assertEqual((s.epoch, s.sampled_ms, s.layout, s.member_count, s.leader, s.tick_scale),
                         (EPOCH, NOW, LAYOUT_COUCH, 3, 0, 2))
        rows = [s.members[k] for k in range(3)]
        self.assertEqual([r.member for r in rows], [2, 0, 1], 'the HUD layout decides the order')
        self.assertEqual([r.seat for r in rows], [2, 1, 0], 'P2 holds member 2, P1 member 0, member 1 is AI')
        self.assertEqual([r.flags for r in rows],
                         [UNIT_PARTY | UNIT_PRESENT | UNIT_ACTOR, UNIT_PARTY | UNIT_PRESENT | UNIT_ACTOR | UNIT_LEADER,
                          UNIT_PARTY | UNIT_PRESENT | UNIT_ACTOR])
        self.assertEqual([(r.hp, r.hp_max, r.level, r.weakness) for r in rows],
                         [(1200, 1500, 32, 4), (900, 1500, 30, 1), (450, 1500, 31, 2)])
        self.assertEqual([r.handle for r in rows], [0x100002, 0x100000, 0x100001])
        self.assertEqual(rows[1].target, 0x20000A)
        r = rows[0]
        self.assertEqual((r.status_timed, r.status_lasting), ((1 << 18) | (1 << 25) | (1 << 7), 1 << 25))
        want = [0] * 32
        want[18], want[7] = 9000, 600
        self.assertEqual(list(r.duration), want, 'only timed, not lasting, statuses count down; other slots read 0')
        self.assertEqual(s.members[3].member, 0xFFFF)
        self.assertEqual(s.flags & (SNAP_LIBRA_KNOWN | SNAP_LIBRA), SNAP_LIBRA_KNOWN, 'nobody has Libra')

    def test_libra(self):
        self.set_record(1, lasting=LIBRA)
        self.assertEqual(self.sample().flags & (SNAP_LIBRA_KNOWN | SNAP_LIBRA), SNAP_LIBRA_KNOWN | SNAP_LIBRA)
        self.set_record(1, lasting=0, timed=LIBRA)
        self.assertTrue(self.sample().flags & SNAP_LIBRA, 'timed Libra counts too')

    def test_targets_seat_targets_first_then_the_partys_deduplicated(self):
        foe_rec = self.foe.battle
        self.set_record(address=foe_rec, hp=5000, hp_max=8000, timed=1 << 15, durations={15: 300}, level=40,
                        weakness=0x20, anti_libra=REC_AUG_LASTING)
        foe2 = self.gw.add_actor(11, 9, handle_class=0x200000)
        self.set_record(address=foe2.battle, hp=10, hp_max=10)
        self.actors[0].set_action(target=0x20000A)   # member 0 targets the foe
        self.actors[1].set_action(target=0x20000B)   # member 1 targets foe 2
        self.actors[2].set_action(target=0x20000A)   # member 2 the foe too (deduplicated)
        self.d.ILCT_HudSnapSeatTarget(1, 0x20000B, tfe.TG_FOES, 1)   # P2's menu points at foe 2
        s = self.sample()
        self.assertEqual(s.target_count, 2)
        t0, t1 = s.targets[0], s.targets[1]
        self.assertEqual(t0.handle, 0x20000B, 'seat targets come first')
        self.assertEqual(t1.handle, 0x20000A)
        self.assertEqual((t1.hp, t1.hp_max, t1.level, t1.weakness, t1.unit_type, t1.member),
                         (5000, 8000, 40, 0x20, 1, 0xFFFF))
        self.assertEqual(t1.flags, UNIT_ACTOR | UNIT_PRESENT | UNIT_ANTI_LIBRA)
        self.assertEqual(t1.duration[15], 300)
        self.assertEqual(t0.flags, UNIT_ACTOR | UNIT_PRESENT)
        st = s.seats[1]
        self.assertEqual((st.handle, st.group, st.on_page, st.since_ms), (0x20000B, tfe.TG_FOES, 1, NOW))
        self.assertEqual(s.seats[0].handle, 0)

    def test_an_ally_target_carries_its_party_identity(self):
        self.actors[0].set_action(target=0x100002)   # member 0 targets member 2 (a heal)
        s = self.sample()
        t = s.targets[0]
        self.assertEqual((s.target_count, t.handle, t.member, t.seat), (1, 0x100002, 2, 2))
        self.assertEqual(t.flags & (UNIT_PARTY | UNIT_ACTOR | UNIT_PRESENT), UNIT_PARTY | UNIT_ACTOR | UNIT_PRESENT)

    def test_eight_sources_eight_targets_seats_first(self):
        a3 = self.gw.add_actor(3, 3, position=(13.0, 2.0, 30.0))   # a fourth party member
        self.img.p64(a3.actor + 0x698, self.record(3))
        self.gw.layout([0, 1, 2, 3], [2, 0, 1, 3])
        self.actors.append(a3)
        handles = []
        for k in range(8):
            a = self.gw.add_actor(20 + k, 9, handle_class=0x200000)
            self.set_record(address=a.battle, hp=1, hp_max=1)
            handles.append(0x200000 | (20 + k))
        for seat in range(4):
            self.d.ILCT_HudSnapSeatTarget(seat, handles[seat], 0, 1)
        for k, a in enumerate(self.actors):   # members 0..3 target handles 4..7
            a.set_action(target=handles[4 + k])
        s = self.sample()
        self.assertEqual(s.member_count, 4)
        rows_order = [s.members[k].member for k in range(4)]
        self.assertEqual(rows_order, [2, 0, 1, 3])
        want = handles[:4] + [handles[4 + m] for m in rows_order]
        self.assertEqual([s.targets[k].handle for k in range(s.target_count)], want, 'seats, then rows in HUD order')
        self.assertEqual(snap_stats(self.d).targets_dropped, 0, 'four seats and four rows always fit')

    def test_an_unknown_handle_stays_without_data(self):
        self.actors[0].set_action(target=0x20007F)
        s = self.sample()
        self.assertEqual((s.target_count, s.targets[0].handle, s.targets[0].flags), (1, 0x20007F, 0))

    def test_seat_targets_remember_since_when(self):
        d = self.d
        d.ILCT_HudSnapSeatTarget(0, 0x20000A, tfe.TG_FOES, 1)
        self.assertEqual(self.sample(NOW).seats[0].since_ms, NOW)
        self.assertEqual(self.sample(NOW + 100).seats[0].since_ms, NOW, 'still on it: unchanged')
        d.ILCT_HudSnapSeatTarget(0, 0, 0, 0)
        s = self.sample(NOW + 200).seats[0]
        self.assertEqual((s.handle, s.on_page, s.since_ms), (0x20000A, 0, NOW), 'off the page: the last target stays')
        d.ILCT_HudSnapSeatTarget(0, 0x20000A, tfe.TG_FOES, 1)
        self.assertEqual(self.sample(NOW + 300).seats[0].since_ms, NOW + 300, 'back on the page: targeted again')
        d.ILCT_HudSnapSeatTarget(0, 0x20000B, tfe.TG_FOES, 1)
        self.assertEqual(self.sample(NOW + 400).seats[0].since_ms, NOW + 400, 'another target')
        self.publish_session(epoch=EPOCH + 1)
        d.ILCT_HudSnapSeatTarget(0, 0, 0, 0)
        self.assertEqual(self.sample(NOW + 500).seats[0].handle, 0, 'a new epoch forgets every seat target')

    # ---- P1's aim in the field target mode ----
    def target_mode(self, target, focus=True, pins=1):
        """The target mode window (the native Target button) holding focus on `target`, and registry 0 enabled with
        each actor's generation, so the aim resolves; the give-back's pins forced."""
        img = self.img
        if not hasattr(self, 'tmode'):
            self.tmode = img.alloc(0x100)
            img.put_global(TMODE_PTR, struct.pack('<Q', self.tmode))
            img.put_global(REGISTRY, struct.pack('<Q', 1))           # head
            img.put_global(REGISTRY + 0x10, b'')                 # enabled
            img.put_global(REGISTRY + 0x20, struct.pack('<i', 64))   # index limit
            for a in [self.foe] + self.actors:
                handle = struct.unpack('<I', ctypes.string_at(a.actor + 0x08, 4))[0]
                img.p16(a.entry + 0x16, handle >> 20)
        img.p32(self.tmode + 0x40, 0x10 if focus else 0)
        img.p32(self.tmode + 0xC4, target)
        self.d.ILCT_TiGbForcePins(pins)

    def test_the_aimed_foe_is_p1s_seat_target_while_the_target_mode_holds_focus(self):
        d = self.d
        d.ILCT_HudSnapSeatTarget(0, 0x100001, tfe.TG_PARTY, 1)
        self.sample(NOW)
        d.ILCT_HudSnapSeatTarget(0, 0, 0, 0)
        normal = self.sample(NOW + 100).seats[0]
        self.assertEqual((normal.handle, normal.on_page, normal.since_ms), (0x100001, 0, NOW), 'the normal track')
        self.target_mode(0x20000A)
        s = self.sample(NOW + 200)
        seat = s.seats[0]
        self.assertEqual((seat.handle, seat.on_page, seat.since_ms), (0x20000A, 1, NOW + 200),
                         'P1 aims at the foe: P1 targeted just now')
        foe = [s.targets[k] for k in range(s.target_count) if s.targets[k].handle == 0x20000A]
        self.assertEqual(len(foe), 1)
        self.assertTrue(foe[0].flags & UNIT_PRESENT, 'its record is read for the Target Info')
        self.assertEqual(s.seats[1].handle, 0, "P2's seat is untouched")
        self.assertEqual(self.sample(NOW + 300).seats[0].since_ms, NOW + 200, 'still aiming: unchanged')
        self.target_mode(0x100002)   # cycles onto a party member
        seat = self.sample(NOW + 400).seats[0]
        self.assertEqual((seat.handle, seat.since_ms), (0x100002, NOW + 400))
        self.target_mode(0x100002, focus=False)   # the target mode ends
        seat = self.sample(NOW + 500).seats[0]
        self.assertEqual((seat.handle, seat.on_page, seat.since_ms), (0x100001, 0, NOW),
                         'back to the normal target, exactly as before')

    def test_an_invalid_or_stale_aim_is_ignored(self):
        cases = {
            'no target': lambda: self.target_mode(0),
            'stale generation': lambda: (self.target_mode(0x20000A), self.img.p16(self.foe.entry + 0x16, 9)),
            'not registry 0': lambda: self.target_mode(0x0021000A),
            'unknown index': lambda: self.target_mode(0x20003F),
            'no battle record': lambda: (self.target_mode(0x20000A), self.img.p64(self.foe.actor + 0x698, 0)),
            'no focus': lambda: self.target_mode(0x20000A, focus=False),
            'a TI-V row off': lambda: self.target_mode(0x20000A, pins=2),
            'pins not compared yet': lambda: self.target_mode(0x20000A, pins=0),
        }
        for name, make in cases.items():
            with self.subTest(name):
                make()
                seat = self.sample(NOW).seats[0]
                self.assertEqual((seat.handle, seat.on_page), (0, 0))
                self.img.p16(self.foe.entry + 0x16, 2)
                self.img.p64(self.foe.actor + 0x698, self.foe.battle)
        self.target_mode(0x20000A)
        self.assertEqual(self.sample(NOW).seats[0].handle, 0x20000A, 'control: a valid aim is fed')

    def test_p1s_menu_target_page_wins_over_the_aim(self):
        self.target_mode(0x20000A)
        self.d.ILCT_HudSnapSeatTarget(0, 0x100002, 0, 1)
        seat = self.sample(NOW).seats[0]
        self.assertEqual((seat.handle, seat.on_page), (0x100002, 1))
        self.d.ILCT_HudSnapSeatTarget(0, 0, 0, 0)
        self.assertEqual(self.sample(NOW + 100).seats[0].handle, 0x20000A, 'the page closed: the aim again')

    def test_an_unreadable_battle_state_or_record(self):
        self.img.put_global(0x2EBF190, struct.pack('<Q', 0))  # VA: the battle state pointer
        s = self.sample()
        self.assertEqual((s.flags & SNAP_PARTY, s.member_count, s.flags & SNAP_WORLD), (0, 0, SNAP_WORLD),
                         'published without rows: the world is still there')
        self.gw.roots(True)
        self.img.protect(self.record(0), 0x1000, 0x01)   # PAGE_NOACCESS: the first records
        try:
            s = self.sample()
        finally:
            self.img.protect(self.record(0), 0x1000, 0x04)
        self.assertTrue(s.flags & SNAP_PARTY, 'the header was read')
        self.assertEqual([s.members[k].flags & UNIT_PRESENT for k in range(3)], [0, 0, 0])
        self.assertEqual([s.members[k].member for k in range(3)], [2, 0, 1])
        self.assertFalse(s.flags & SNAP_LIBRA_KNOWN, 'no row read: Libra unknown')

    # ---- coherence ----
    def run_with(self, fn, now=NOW) -> Snapshot:
        cb = BETWEEN(fn)
        self.d.ILCT_HudSnapSetBetween(cb)
        try:
            return self.sample(now)
        finally:
            self.d.ILCT_HudSnapSetBetween(BETWEEN(0))

    def test_a_party_change_under_the_read_is_read_again(self):
        calls = []

        def between(stage, address):
            if stage == 1:
                calls.append(stage)
                if len(calls) == 1:
                    self.gw.layout([0, 1, 2], [1, 2, 0])   # the HUD order changes during the first read
        s = self.run_with(between)
        self.assertEqual([s.members[k].member for k in range(3)], [1, 2, 0], 'the second, stable read')
        self.assertEqual(len(calls), 2, 'the first try saw the change, the second was stable')
        self.assertTrue(s.flags & SNAP_PARTY)
        self.assertEqual(snap_stats(self.d).party_torn, 0)

    def test_a_party_that_keeps_moving_is_left_out_never_mixed(self):
        flip = [0]

        def between(stage, address):
            if stage == 1:
                flip[0] ^= 1
                self.gw.layout([0, 1, 2], [1, 2, 0] if flip[0] else [2, 0, 1])
        s = self.run_with(between)
        self.assertEqual((s.flags & SNAP_PARTY, s.member_count, s.leader), (0, 0, 0xFFFF))
        self.assertEqual([s.members[k].member for k in range(4)], [0xFFFF] * 4)
        self.assertEqual(snap_stats(self.d).party_torn, 1)

    def test_a_record_change_under_the_read_is_read_again(self):
        target = self.record(1)
        seen = []

        def between(stage, address):
            if stage == 2 and address == target:
                seen.append(address)
                if len(seen) == 1:
                    self.set_record(1, hp=10, durations={18: 50}, timed=1 << 18)
        s = self.run_with(between)
        row = s.members[2]
        self.assertEqual((row.member, row.hp, row.status_timed, row.duration[18]), (1, 10, 1 << 18, 50))
        self.assertTrue(row.flags & UNIT_PRESENT)
        self.assertEqual(snap_stats(self.d).record_retries, 1)

    def test_a_record_that_keeps_moving_is_left_out(self):
        target = self.record(1)
        hp = [100]

        def between(stage, address):
            if stage == 2 and address == target:
                hp[0] += 1
                self.set_record(1, hp=hp[0])
        s = self.run_with(between)
        self.assertEqual(s.members[2].flags & UNIT_PRESENT, 0)
        self.assertEqual((s.members[2].hp, s.members[2].member), (0, 1), 'no value from a moving record')
        self.assertEqual([s.members[k].flags & UNIT_PRESENT for k in (0, 1)], [UNIT_PRESENT] * 2)
        self.assertEqual(snap_stats(self.d).records_torn, 1)

    def test_the_seqlock_never_hands_out_a_mix(self):
        out = (u64 * 3)()
        self.assertEqual(self.d.ILCT_HudSnapStress(200_000, out), 1)
        coherent, torn, mixed = out
        self.assertEqual(mixed, 0, 'a reader never sees two snapshots at once')
        self.assertGreater(coherent, 0)

    # ---- publication ----
    def test_publication_follows_the_world_co_op_and_render(self):
        d = self.d
        d.ILCT_HudSnapStep(NOW)
        s, epoch, ms = Snapshot(), u32(), u64()
        self.assertEqual(d.ILCT_HudSnapRead(ctypes.byref(s)), 1)
        self.assertEqual(d.ILCT_HudSnapMeta(ctypes.byref(epoch), ctypes.byref(ms)), 1)
        self.assertEqual((epoch.value, ms.value, s.member_count), (EPOCH, NOW, 3))
        cases = {
            'no world': lambda: self.publish_session(gates=0),
            'co-op off': lambda: (d.ILCT_CtlKill(1), d.ILCT_CtlStep(NOW)),
            'RENDER disarmed': lambda: d.ILCT_PatchApplySubsystems(0xFF & ~RENDER),
            'session withdrawn': lambda: d.ILCT_TownSessionWithdraw(),
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.hw.setup(attach=False, reset_hud=False)
                d.ILCT_CtlReset()
                self.publish_session()
                d.ILCT_HudSnapStep(NOW)
                self.assertEqual(d.ILCT_HudSnapRead(ctypes.byref(s)), 1, 'instrument: published')
                prepare()
                d.ILCT_HudSnapStep(NOW + 20)
                self.assertEqual(d.ILCT_HudSnapRead(ctypes.byref(s)), 0, name)
                self.assertEqual(d.ILCT_HudSnapMeta(ctypes.byref(epoch), ctypes.byref(ms)), 0, name)
        d.ILCT_CtlReset()
        self.hw.setup(attach=False, reset_hud=False)
        self.publish_session()
        d.ILCT_HudSnapStep(NOW)
        d.ILCT_HudSnapStop()
        self.assertEqual(d.ILCT_HudSnapRead(ctypes.byref(s)), 0, 'stop withdraws')
        self.assertEqual(d.ILCT_HudSnapLayout(), LAYOUT_COUCH)


# ---- the layer end to end: co-op menus + firewall + frame hook ---------------------------------------------------------------
class HookedLayerCase(tfe.LayerCase):
    """The co-op menus with the real presenter (no simulation): the HUD frame hook runs once per beat while the HUD
    draws (`self.drawing`), on an empty layer (no game call), and the snapshot is sampled every beat as at run time."""
    PRESENTER = -1
    hooked = False

    def setUp(self):
        self.hooked = False
        super().setUp()
        d = self.d
        declare(d)
        th.declare(d)
        d.ILCT_HudReset()
        assert d.ILCT_HudAttach(TWIN_BASE) == 1
        d.ILCT_HudSetTestDraw(0)
        d.ILCT_CtlReset()
        self.mgr = ctypes.create_string_buffer(0x4100)
        self.ctx = (u8 * 0xA0)()
        self.arena = (u64 * 3)(0x10_0000, 0x10_0000, 0x10_0000 + th.RING)
        a = ctypes.addressof
        struct.pack_into('<Q', self.mgr, 0x38, a(self.ctx))
        struct.pack_into('<h', self.ctx, 0x58, 0x80)
        self.sw.twin.poke(th.RENDER_ARENA, struct.pack('<Q', a(self.arena)))
        self.sw.twin.poke(th.UI_READY, struct.pack('<I', 1))
        self.ui(True)
        d.ILCT_HudSnapStart(tfe.TWIN_IMAGE, self.now)
        self.drawing = True
        self.stamps = []
        self.hooked = True
        self.beat()

    def tearDown(self):
        d = self.d
        d.ILCT_HudDrvReset()
        d.ILCT_HudReset()
        d.ILCT_CtlReset()
        withdraw_menu_view(d)
        super().tearDown()

    def ui(self, shown):
        self.sw.twin.poke(RENDER_FLAGS, struct.pack('<I', RENDER_UI if shown else 0))

    def beat(self, seats=None, firewall=True, dt=20):
        super().beat(seats, firewall, dt)
        if not self.hooked:
            return
        d = self.d
        d.ILCT_HudSnapStep(self.now)
        # The co-op thread's menu view of this beat, empty (no window drawn: no game call in the replay).
        publish_menu_view(d, epoch=self.rec.head.epoch, built=self.now)
        if self.drawing:
            c = th.HookCtx()
            c.rcx, c.rdx, c.index = ctypes.addressof(self.mgr), ctypes.addressof(self.ctx), d.ILCT_HudRow()
            with quiet_faulthandler():
                if d.ILCT_HudCall(ctypes.byref(c), self.rec.head.epoch) == 2:
                    d.ILCT_HudAfter(ctypes.byref(c), self.rec.head.epoch)
            if d.ILCT_FePresenterDrawn() == self.now:
                self.stamps.append(self.now)

    def owns(self) -> bool:
        st = self.state()
        return bool(st.report.p1_menus and st.report.presenter_live and self.fw().head.flags & tf.F_GATE)


class PresenterLayerTest(HookedLayerCase):
    def test_the_hook_drawing_every_frame_makes_the_presenter_live_with_no_menu_open(self):
        self.beat()
        st = self.state()
        self.assertEqual((st.presenter_live, st.report.presenter_live, st.report.p1_menus), (1, 1, 1))
        self.assertTrue(self.fw().head.flags & tf.F_GATE, 'the co-op menus own P1 battle UI (presented, none open)')
        self.assertEqual([self.seat(s).mode for s in range(3)], [tfe.CLOSED] * 3, 'no menu was open')
        self.assertGreaterEqual(stats(self.d).stamped, 2)
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.LIST, "P1's A opens its co-op menu")

    def test_without_the_hook_nothing_opens(self):
        self.drawing = False
        for _ in range(int(LIVE_MS / 20) + 2):
            self.beat()
        self.assertFalse(self.owns())
        self.press(1, tfe.A_CONFIRM)
        self.press(2, tfe.A_BATTLE)
        self.assertEqual((self.seat(0).mode, self.seat(1).mode), (tfe.CLOSED, tfe.CLOSED))

    def test_a_stall_of_250_ms_gives_p1_the_stock_menus_back(self):
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.LIST)
        last = self.stamps[-1]
        self.drawing = False
        while self.now + 20 - last <= LIVE_MS:
            self.beat()
            self.assertTrue(self.owns(), f'{self.now - last} ms after the last presented frame: still live')
            self.assertEqual(self.seat(0).mode, tfe.LIST)
        self.beat()
        self.assertFalse(self.owns())
        self.assertLessEqual(self.now - last, LIVE_MS + 20, 'the first beat past 250 ms')
        st, fw = self.state(), self.fw()
        self.assertEqual((st.report.p1_menus, fw.head.flags & tf.F_GATE, fw.p1_mask, self.seat(0).mode),
                         (0, 0, 0, tfe.CLOSED), 'every stock frontend and button is P1s again')
        self.drawing = True
        self.beat()
        self.beat()
        self.assertTrue(self.owns(), 'instrument: the hook drawing again makes it live again')

    def test_ui_hidden_for_300_ms_idles(self):
        self.press(1, tfe.A_CONFIRM)
        self.ui(False)
        start = self.now
        while self.now - start < 300:
            self.beat()
        self.assertFalse(self.owns())
        self.assertEqual(self.seat(0).mode, tfe.CLOSED)
        self.assertGreater(stats(self.d).declined[GATE_UI_HIDDEN], 0)
        self.ui(True)
        self.beat()
        self.beat()
        self.assertTrue(self.owns(), 'instrument: UI shown again')

    def test_a_fault_gives_p1_the_stock_menus_back_on_the_next_beat(self):
        self.press(1, tfe.A_CONFIRM)
        self.assertTrue(self.owns())
        self.d.ILCT_HudDrvInjectFault(PASS_MENUS)
        self.beat()   # this beat's frame faults (after the layer stepped)
        faulted_at = self.now
        self.d.ILCT_HudDrvInjectFault(PASS_COUNT)
        self.beat()
        self.assertFalse(self.owns(), 'the stamp was withdrawn: idle on the very next beat')
        self.assertLessEqual(self.now - faulted_at, 20)
        self.assertEqual(self.seat(0).mode, tfe.CLOSED)
        for _ in range(5):
            self.beat()
        self.assertFalse(self.owns(), 'RENDER latched: never presented again')

    def test_the_kill_switch_stops_the_stamp(self):
        self.beat()
        n = stats(self.d).stamped
        self.d.ILCT_CtlKill(1)
        self.d.ILCT_CtlStep(self.now)
        for _ in range(3):
            self.beat()
        self.assertEqual(stats(self.d).stamped, n, 'co-op off: nothing stamped')
        self.assertGreater(stats(self.d).declined[GATE_NO_SNAPSHOT], 0, 'the sampler withdrew the snapshot')
        self.assertFalse(self.owns())


class SuspendedPresenterTest(HookedLayerCase):
    """The main menu (and the map, the pause screen) stop the field HUD, so the presenter dies 250 ms into them. The
    suspended menus are held, not closed, and resume where they were once the screen is gone and the layer draws."""

    def open_guest(self):
        self.press(2, tfe.A_BATTLE)
        self.press(2, tfe.A_DOWN)
        self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (tfe.LIST, 1))

    def main_menu(self, open_):
        if open_:
            self.publish(facts=FACT['CONTROL'] | FACT['MENU'], phase=PHASE['MENU'])
        else:
            self.publish()
        self.drawing = not open_

    def test_two_seconds_in_the_main_menu_then_the_menus_resume_where_they_were(self):
        self.open_guest()
        self.main_menu(True)
        start = self.now
        while self.now - start < 2000:
            self.beat()
            if self.now - start >= 300:
                st = self.state()
                self.assertEqual((st.presenter_live, st.susp.suspended), (0, 1), 'held')
                self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (tfe.LIST, 1), 'kept as it was')
                self.assertEqual((st.report.p1_menus, st.report.presenter_live, st.report.guests_open,
                                  self.fw().head.flags & tf.F_GATE, st.wait_requested), (0, 0, 0, 0, 0),
                                 'owning nothing')
        self.press(2, tfe.A_DOWN)
        self.assertEqual(self.seat(1).cursor, 1, 'no input while held')
        self.main_menu(False)
        self.beat()
        self.assertEqual(self.state().susp.suspended, 1, 'the screen closed: the resume wait starts, still held')
        self.beat()
        self.assertEqual(self.state().presenter_live, 1, 'the HUD drew again: live from its first frame')
        for _ in range(tfe.RESUME_MS // 20):
            self.beat()
        st = self.state()
        self.assertEqual((st.susp.suspended, st.report.guests_open), (0, 0b10), 'resumed')
        self.assertEqual((self.seat(1).mode, self.seat(1).cursor), (tfe.LIST, 1), 'back exactly where it was')
        self.press(2, tfe.A_DOWN)
        self.assertEqual(self.seat(1).cursor, 2, 'and takes input again')

    def test_presenter_death_outside_a_suspension_still_idles(self):
        self.open_guest()
        self.drawing = False
        for _ in range(int(LIVE_MS / 20) + 2):
            self.beat()
        self.assertEqual((self.seat(1).mode, self.state().susp.suspended), (tfe.CLOSED, 0))

    def test_a_load_during_the_hold_closes_every_menu(self):
        self.open_guest()
        self.main_menu(True)
        for _ in range(20):
            self.beat()
        self.assertEqual((self.state().susp.suspended, self.seat(1).mode), (1, tfe.LIST))
        self.publish(phase=PHASE['LOADING'])
        self.beat()
        self.assertEqual((self.state().susp.suspended, self.seat(1).mode), (0, tfe.CLOSED))

    def test_a_resume_with_nothing_drawn_closes_the_menus(self):
        self.open_guest()
        self.main_menu(True)
        for _ in range(20):
            self.beat()
        self.publish()          # the screen closed, but the HUD still does not draw
        for _ in range(tfe.RESUME_MS // 20 + 2):
            self.beat()
        self.assertEqual((self.state().susp.suspended, self.seat(1).mode), (0, tfe.CLOSED),
                         'menus come back only when they are drawn')

    def test_a_screen_up_when_the_presenter_dies_is_held_until_adopted(self):
        self.open_guest()
        self.drawing = False
        last = self.stamps[-1]
        while self.now - last < LIVE_MS - 10:
            self.beat()
        self.publish(facts=FACT['CONTROL'] | FACT['MENU'], phase=PHASE['MENU'])  # the screen shows late
        self.beat()
        self.beat()
        st = self.state()
        self.assertEqual((st.presenter_live, st.susp.suspended, self.seat(1).mode), (0, 0, tfe.LIST),
                         'presenter dead, screen not adopted yet: held, not closed')
        for _ in range(3):
            self.beat()
        self.assertEqual((self.state().susp.suspended, self.seat(1).mode), (1, tfe.LIST), 'adopted, still held')


# ---- unicorn on the hooked twin ------------------------------------------------------------------------------------------
class NativeDriverTest(th.NativeEquivalenceTest):
    """The frame hook on the hooked twin in unicorn with the co-op layer on (a snapshot published, the test draw off):
    the passes' commands reach the native text renderer through the game's own text draw, menus last."""

    def setUp(self):
        d = declare(self.hw.d)
        d.ILCT_HudReset()
        d.ILCT_HudSetTestDraw(0)
        d.ILCT_HudSetPinsOk(1)
        d.ILCT_CtlReset()
        d.ILCT_HudSnapPublish(ctypes.byref(snapshot(sampled=gt.NOW + 5)))
        publish_menu_view(d, built=gt.NOW + 5)
        self.ui_flags = RENDER_UI
        self.counters_drv = d.ILCT_HudDrvCountersAddress()

    def tearDown(self):
        self.hw.d.ILCT_HudDrvReset()
        withdraw_menu_view(self.hw.d)

    def lay_out(self, uc, *args, **kw):
        super().lay_out(uc, *args, **kw)
        uc.mem_write(RENDER_FLAGS, struct.pack('<I', self.ui_flags))

    def run_hook(self, *args, **kw):
        # The emulator puts memory back after a run: read the driver's counters in the run's own probe.
        emu = self.emu
        original = emu.run
        self.drv_seen = None

        def run(start, allowed, state, probe=None, on_callee=None):
            def both(u):
                self.drv_seen = DrvCounters.from_buffer_copy(bytes(u.mem_read(self.counters_drv,
                                                                              ctypes.sizeof(DrvCounters))))
                return probe(u) if probe else None
            return original(start, allowed, state, probe=both, on_callee=on_callee)

        emu.run = run
        try:
            return super().run_hook(*args, **kw)
        finally:
            del emu.run

    def drv(self) -> DrvCounters:
        self.assertIsNotNone(self.drv_seen)
        return self.drv_seen

    def test_the_passes_reach_the_native_text_renderer_in_order_menus_last(self):
        self.hw.d.ILCT_HudDrvSetProbe(0xF)
        self.run_hook()
        self.assertEqual([s[2] for s in self.seen if s[0] == 'text'], [th.encoded(n) for n in PASS_NAMES])
        c = self.drv()
        self.assertEqual((c.composed, c.stamped, c.last_stamp_ms), (1, 1, gt.NOW + 5))

    def test_an_empty_layer_stamps_without_drawing(self):
        self.run_hook()
        self.assertEqual(self.seen, [])
        self.assertEqual(self.drv().stamped, 1)

    def test_the_native_log_line_stays_under_the_layer(self):
        self.hw.d.ILCT_HudDrvSetProbe(1 << PASS_MENUS)
        self.run_hook(text='Vaan attacks.')
        self.assertEqual([s[2] for s in self.seen if s[0] == 'text'], [th.encoded('Vaan attacks.'),
                                                                        th.encoded('menus')])

    def test_detour_disarmed_is_stock_and_never_stamps(self):
        self.hw.d.ILCT_HudDrvSetProbe(0xF)
        self.run_hook(hooked=False, text='Vaan attacks.')
        self.assertEqual([s[2] for s in self.seen if s[0] == 'text'], [th.encoded('Vaan attacks.')])
        self.assertEqual(self.drv().stamped, 0)

    def test_gate_closed_is_stock_and_never_stamps(self):
        self.hw.d.ILCT_HudDrvSetProbe(0xF)
        self.run_hook(gate=False)
        self.assertEqual((self.seen, self.drv().stamped), ([], 0))

    def test_ui_hidden_is_stock_and_never_stamps(self):
        self.hw.d.ILCT_HudDrvSetProbe(0xF)
        self.ui_flags = 0
        self.run_hook()
        self.assertEqual((self.seen, self.drv().stamped), ([], 0))
        self.assertEqual(self.drv().declined[GATE_UI_HIDDEN], 1)


# The base class's own cases run in test_coop_hud.py; here only the driver cases above.
for _name in [n for n in vars(th.NativeEquivalenceTest) if n.startswith('test_')]:
    if _name not in vars(NativeDriverTest):
        setattr(NativeDriverTest, _name, None)


if __name__ == '__main__':
    unittest.main()
