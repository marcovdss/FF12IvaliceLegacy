"""Host tests for the seat marks in the world and the AoE-only ring rule (native/coop/src/hud/world_marks.*, rings.*).
No game run, nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin.

* the site rows: TG-06 (line CLUT) and TG-05 (arrow CLUT) are the site table's own TARGETS_CARDS detours; WM-01 is an
  OPTIONAL TARGETS_CARDS call row on the overhead pass call, RG-01 one on the stock target list's ring call; their
  signatures measured at one hit; the game bytes WM-01 and the ring operation rely on are pinned at attach;
* the rules: line and arrow CLUTs (a party palette from a seat -> seat palette, gambit ally -> 6, foe, unknown and
  any special palette -> native; the arrow: gold and the party palettes -> the seat's, red always kept), colour ->
  palette and gem cell, the diamond's fade-in / hold / fade-out, the show triggers (fight start after a
  quiet stretch, swap, drop-in, Identify; a new world is a baseline), the ring gate table (magick/technick AoE yes,
  Attack no, the Attack Area Experiment's Attack no, cones and single targets no, Quickenings/items no) and 0x31a990's
  area kinds;
* the handlers on a synthetic ctx: TG-06/TG-05 change only the CLUT register(s) and only for a seat or gambit source;
  WM-01 wraps the pass only while a diamond is visible and appends a gem and a numeral per visible seat, never more
  than the pool holds, nothing without an anchor, a texture or the switch;
* the ring operation on doubles of the game's functions over a synthetic area list: one ring per seat in its colour,
  four live at once, a shared target in the most recent seat's colour, refused rings never applied, rings released when
  the page closes or the target changes, the give-back when not wanted; RG-01 gives Attack the no-ring flag;
* the co-op thread's records from a synthetic session and snapshot;
* unicorn on the hooked twin: the game's own target-line code from the type load through TG-06 to the CLUT bind, and
  the arrow's from its loads through TG-05 to the bind call, with seat, gambit, foe and unknown sources; disarmed is
  the stock CLUT.
"""
from __future__ import annotations

import ctypes
import json
import random
import re
import struct
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_hud as th
import test_coop_hud_driver as hd
import test_coop_patch_engine as pe
from coop_native_support import native_test, quiet_faulthandler
from coop_session_support import GATE, SEATF, Image, Record
from test_coop_patch_engine import GAME, TWIN_BASE

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
f32 = ctypes.c_float
P = ctypes.POINTER
EPOCH, NOW = gt.EPOCH, gt.NOW

# ---- game facts (VAs at the pinned base 0x120000) ---------------------------------------------------------------------
LINE_SITE, ARROW_SITE, OVERHEAD_CALL, RING_CALL = 0x3F54D2, 0x3F719C, 0x28EDAD, 0x2789F0
OVERHEAD_PASS, RING_TRIGGER = 0x2C62B0, 0x27DC10
TYPE_LOAD, LINE_BIND = 0x3F54B9, 0x3F54E0          # movzx ecx,[r15+0x840] ... call 0x2715a0 (r9d = CLUT)
TYPE_TO_CLUT = 0x2713B0
ARROW_LOADS, ARROW_BIND = 0x3F7188, 0x3F71A4       # mov r9d,[rsi+0x32c] ... call 0x181930 (r9d = CLUT)
LINE_SOURCE, LINE_TYPE, ARROW_SOURCE, ARROW_HALF, ARROW_BASE = 0x810, 0x840, 0x2D0, 0x32C, 0x330
UI_ATLASES, ATLAS_PARTYTOP = 0x209AC30, 0x360
GEM_SHEET, DIGIT_SHEET = 0x0105, 0x0002
UI_AREA_LIST, RING_CLUT, RING_FLAGS, RING_KEY, RING_NEXT = 0x10F40, 0x48, 0x50, 0x6C, 0x70
OP_HUD_RINGS = 27
SAPPHIRE, RUBY, PINK, EMERALD, GOLD, AMETHYST, BRONZE = range(7)
MARK_NONE, MARK_SEAT, MARK_GAMBIT = 0, 1, 2
CLUT_GAMBIT = 6
TRIG_FIGHT, TRIG_SWAP, TRIG_DROP_IN, TRIG_IDENTIFY = 1, 2, 4, 8
FADE_IN, HOLD, FADE_OUT, QUIET = 150, 2500, 700, 3000
AREA_NONE, AREA_CIRCLE, AREA_ALT, AREA_CONE, AREA_4, AREA_5 = range(6)
F_OTHER, F_MAGICK, F_TECHNICK, F_ATTACK = range(4)
PG_ROOT, PG_MAGICKS, PG_WHITE, PG_BLACK, PG_TIME, PG_GREEN, PG_ARCANE, PG_TECHNICKS, PG_MIST, PG_QUICKENINGS, \
    PG_SUMMONS, PG_ITEMS = range(12)
MODE_CLOSED, MODE_LIST, MODE_TARGET = 0, 1, 2
EK_LABEL, EK_PAGE, EK_ACTION = 0, 1, 2
ATTACK = 150
ATTACK_FLAGS_EXPERIMENT = 0x0C16408C   # row 150's flags in the pinned table: bits 12/21/24/25 clear
RUN_ORIGINAL, RESUME, CALL_AROUND = 0, 1, 2


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


Head = [('epoch', u32), ('flags', u32), ('generation', u64)]
LineEntry = S('LineEntry', [('handle', u32), ('kind', u8), ('seat', u8), ('clut', u8), ('reserved', u8)])
LinesRecord = S('LinesRecord', Head + [('enabled', u8), ('count', u8), ('reserved', u8 * 6), ('entries', LineEntry * 8)])
SeatInput = S('SeatInput', [('held', u8), ('stance_known', u8), ('stance', u8), ('reserved', u8), ('member', u16),
                            ('reserved2', u16), ('handle', u32)])
SeatTrack = S('SeatTrack', [('member', u16), ('held', u8), ('reserved', u8), ('handle', u32), ('shown_ms', u64)])
TriggerState = S('TriggerState', [('epoch', u32), ('engaged', u8), ('reserved', u8 * 3), ('quiet_since', u64),
                                  ('seats', SeatTrack * 4)])
Diamond = S('Diamond', [('handle', u32), ('seat', u8), ('gem', u8), ('colour', u8), ('reserved', u8), ('shown_ms', u64)])
DiamondsRecord = S('DiamondsRecord', Head + [('sampled_ms', u64), ('enabled', u8), ('reserved', u8 * 7),
                                             ('seats', Diamond * 4)])
WmCounters = S('WmCounters', [(n, u64) for n in ('samples', 'lines_recoloured', 'arrows_recoloured', 'lines_unread',
                                                 'passes_wrapped', 'objects', 'pool_full', 'no_anchor', 'triggers')])
WmStatus = S('WmStatus', [('attached', u8), ('lines', u8), ('diamonds', u8), ('reserved', u8)])
SeatRing = S('SeatRing', [('caster', u32), ('target', u32), ('action', u16), ('clut', u8), ('order', u8)])
RingsRecord = S('RingsRecord', Head + [('enabled', u8), ('reserved', u8 * 7), ('seats', SeatRing * 4)])
SeatAim = S('SeatAim', [('held', u8), ('mode', u8), ('page', u8), ('pending_kind', u8), ('action', u16),
                        ('area_kind', u8), ('on_page', u8), ('caster', u32), ('target', u32)])
RgCounters = S('RgCounters', [(n, u64) for n in ('samples', 'pushed', 'push_refused', 'jobs', 'created', 'refused',
                                                 'released', 'give_backs', 'cleave_gated')])
Applied = S('Applied', [('target', u32), ('clut', u8), ('reserved', u8 * 3)])
RgStatus = S('RgStatus', [('attached', u8), ('enabled', u8), ('applied', u8), ('reserved', u8), ('last_job', i32)])
SIZES = [ctypes.sizeof(t) for t in (LineEntry, LinesRecord, SeatInput, SeatTrack, TriggerState, Diamond, DiamondsRecord,
                                    WmCounters, WmStatus, SeatRing, RingsRecord, SeatAim, RgCounters, Applied, RgStatus)]

SIGNATURES = {
    'ILCT_WmSizes': ([P(u32), u32], u32),
    'ILCT_WmLineClut': ([P(LinesRecord), u32, u32, u32], u32), 'ILCT_WmArrowClut': ([P(LinesRecord), u32, u32], u32),
    'ILCT_WmClutOfColour': ([u32], u32), 'ILCT_WmGemCell': ([u32], u32), 'ILCT_WmAlpha': ([u64, u64], u32),
    'ILCT_WmTriggerStep': ([P(TriggerState), u32, P(SeatInput), u32, u64], u32),
    'ILCT_WmPersistStep': ([P(TriggerState), P(SeatInput), u64], None),
    'ILCT_WmReset': ([], None), 'ILCT_WmPublishLines': ([P(LinesRecord)], None),
    'ILCT_WmPublishDiamonds': ([P(DiamondsRecord)], None), 'ILCT_WmReadLines': ([P(LinesRecord)], i32),
    'ILCT_WmReadDiamonds': ([P(DiamondsRecord)], i32), 'ILCT_WmSetLinesEnabled': ([i32], None),
    'ILCT_WmSetDiamondsEnabled': ([i32], None), 'ILCT_WmIdentify': ([u32], None), 'ILCT_WmSetImageBase': ([u64], None),
    'ILCT_WmSetAttached': ([u32], None), 'ILCT_WmAttach': ([u64], i32), 'ILCT_WmConfigure': ([ctypes.c_void_p], None),
    'ILCT_WmStep': ([u64], None), 'ILCT_WmStop': ([], None), 'ILCT_WmStats': ([P(WmCounters)], None),
    'ILCT_WmStatus': ([P(WmStatus)], None), 'ILCT_WmTriggers': ([P(TriggerState)], None),
    'ILCT_WmLineCall': ([P(th.HookCtx), u32], i32), 'ILCT_WmArrowCall': ([P(th.HookCtx), u32], i32),
    'ILCT_WmOverheadCall': ([P(th.HookCtx), u32], i32), 'ILCT_WmOverheadAfter': ([P(th.HookCtx), u32], None),
    'ILCT_WmRow': ([u32], i32), 'ILCT_WmUseDoubles': ([i32], None), 'ILCT_WmFrameLog': ([P(u64), u32], u32),
    'ILCT_WmAppends': ([], u32),
    'ILCT_RgAreaKind': ([u32, u32], u32), 'ILCT_RgFamily': ([u32], u32), 'ILCT_RgAllowed': ([u32, u32, u32], i32),
    'ILCT_RgStockAllowed': ([u32], i32), 'ILCT_RgWanted': ([P(SeatAim)], u32), 'ILCT_RgReset': ([], None),
    'ILCT_RgPublish': ([P(RingsRecord)], None), 'ILCT_RgRead': ([P(RingsRecord)], i32),
    'ILCT_RgSetEnabled': ([i32], None), 'ILCT_RgSetImageBase': ([u64], None), 'ILCT_RgSetAttached': ([u32], None),
    'ILCT_RgForceLive': ([i32], None), 'ILCT_RgAttach': ([u64], i32), 'ILCT_RgConfigure': ([ctypes.c_void_p], None),
    'ILCT_RgStep': ([u64], None), 'ILCT_RgStop': ([], None), 'ILCT_RgTick': ([i32], None),
    'ILCT_RgRunJob': ([u64, u32], i32), 'ILCT_RgStats': ([P(RgCounters)], None), 'ILCT_RgStatus': ([P(RgStatus)], None),
    'ILCT_RgApplied': ([P(Applied)], None), 'ILCT_RgRelease': ([u64, u32], u32),
    'ILCT_RgSetAims': ([ctypes.c_void_p], None), 'ILCT_RgTriggerCall': ([P(th.HookCtx), u32], i32),
    'ILCT_RgRow': ([], i32), 'ILCT_RgUseDoubles': ([i32], u64), 'ILCT_RgDoubleInfo': ([u32, i32, u32, u32, f32], None),
    'ILCT_RgDoubleLive': ([u32], None), 'ILCT_RgDoubleCreates': ([], u32),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def lines(entries, enabled=1, epoch=EPOCH) -> LinesRecord:
    r = LinesRecord()
    r.epoch, r.enabled, r.count = epoch, enabled, len(entries)
    for k, (handle, kind, seat, clut) in enumerate(entries):
        r.entries[k].handle, r.entries[k].kind, r.entries[k].seat, r.entries[k].clut = handle, kind, seat, clut
    return r


SEAT1, SEAT2, GAMBIT, FOE, STRANGER = 0x10000001, 0x10000002, 0x10000003, 0x20000009, 0x10000007
STANDARD = [(SEAT1, MARK_SEAT, 1, GOLD), (SEAT2, MARK_SEAT, 2, SAPPHIRE), (GAMBIT, MARK_GAMBIT, 0, CLUT_GAMBIT)]


def seat_inputs(*seats) -> ctypes.Array:
    arr = (SeatInput * 4)()
    for k, s in enumerate(seats):
        if s is None:
            continue
        held, member, handle, stance = s
        arr[k].held, arr[k].member, arr[k].handle = held, member, handle
        arr[k].stance_known, arr[k].stance = (1, stance) if stance is not None else (0, 0)
    return arr


def native_world():
    w = th.world()
    declare(hd.declare(w.d))
    return w


# ---- static ---------------------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
class SiteRowsTest(unittest.TestCase):
    def setUp(self):
        self.rows = {r['id']: r for r in pe.site_rows()}

    def test_the_line_rows_are_the_site_tables_own(self):
        for sid, va, hook, expected in (('TG-06', LINE_SITE, 6, '44 8b f0 45 33 c0 44 8b c8'),
                                        ('TG-05', ARROW_SITE, 8, '8b d7 8b cb 46 8d 0c 48')):
            with self.subTest(sid):
                r = self.rows[sid]
                self.assertEqual((r['kind'], r['policy'], r['subsystem']), ('DETOUR', 'PATCH', 'TARGETS_CARDS'))
                self.assertEqual((r['rva'], r['hook_len']), (va - 0x120000, hook))
                self.assertEqual(r['expected'], bytes.fromhex(expected), 'TG-06 pins the `mov r9d, eax` consumer too')
                self.assertEqual(pe.call_offset(r), None, 'a MinHook row (mid-function)')

    def test_the_call_rows(self):
        legacy = {r['id']: r for r in json.loads((COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8'))['sites']}
        for sid, va, callee in (('WM-01', OVERHEAD_CALL, OVERHEAD_PASS), ('RG-01', RING_CALL, RING_TRIGGER)):
            with self.subTest(sid):
                r = self.rows[sid]
                self.assertEqual((r['kind'], r['policy'], r['subsystem'], r['flags']), ('CALL', 'PATCH', 'TARGETS_CARDS',
                                                                                        0x08))
                self.assertEqual(r['rva'], va - 0x120000)
                self.assertEqual(va + 5 + struct.unpack('<i', r['expected'][1:5])[0], callee)
                self.assertEqual(pe.call_offset(r), 0, 'a call row (an island; its after-handler may call the game)')
                self.assertEqual(legacy[sid]['signature']['hits_in_text'], 1)
                self.assertEqual(legacy[sid]['group'], 'targeting')

    def test_the_signatures_hit_once_in_the_exe(self):
        exe = (Path(GAME) / 'x64' / 'FFXII_TZA.exe').read_bytes()
        legacy = {r['id']: r for r in json.loads((COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8'))['sites']}
        for sid in ('WM-01', 'RG-01'):
            sig = legacy[sid]['signature']
            rx = b''.join(b'.' if t == '??' else re.escape(bytes([int(t, 16)])) for t in sig['pattern'].split())
            hits = [m.start() for m in re.finditer(rx, exe, re.S)]
            self.assertEqual(len(hits), 1, sid)


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.wm = (HUD_SRC / 'world_marks.cpp').read_text(encoding='utf-8')
        self.rg = (HUD_SRC / 'rings.cpp').read_text(encoding='utf-8')

    @staticmethod
    def body(text: str, signature: str) -> str:
        start = text.index(signature)
        return text[start:text.index('\n}\n', start)]

    def test_game_thread_code_never_blocks_allocates_or_reads_the_session(self):
        cases = [(self.wm, s) for s in ('int32_t line_body(', 'int32_t arrow_body(', 'int32_t overhead_body(',
                                        'void overhead_after(', 'bool emit(')]
        cases += [(self.rg, s) for s in ('int32_t run_job(', 'int32_t trigger_body(', 'void tick(bool live)',
                                         'uint32_t release_target(')]
        for text, sig in cases:
            body = self.body(text, sig)
            for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                              'memcpy(', 'memset(', 'session::read', 'game_reader', 'snapshot::read', 'frontend::'):
                self.assertNotIn(forbidden, body, f'{sig}: {forbidden}')

    def test_the_mid_function_bodies_call_no_game_function_and_write_no_memory(self):
        for sig in ('int32_t line_body(', 'int32_t arrow_body('):
            body = self.body(self.wm, sig)
            for forbidden in ('live_fns', 'f.alloc', 'f.frame', 'f.append', 'store(', 'copy_to_caller'):
                self.assertNotIn(forbidden, body, f'{sig}: {forbidden}')
        self.assertIn('c->rax = clut;', self.body(self.wm, 'int32_t line_body('))
        arrow = self.body(self.wm, 'int32_t arrow_body(')
        self.assertIn('c->rax = clut;', arrow)
        self.assertIn('c->r9 = 0;', arrow)

    def test_the_ring_writes(self):
        writes = sorted(set(w.strip() for w in re.findall(r'store_u32\(([^,]+),', self.rg)))
        self.assertEqual(writes, ['node + RING_CLUT', 'node + RING_FLAGS', 'node + RING_KEY', 'uint64_t address'])

    def test_the_boot_and_tick_wiring(self):
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        for name in ('world_marks', 'rings'):
            self.assertLess(boot.index(f'hud::{name}::attach('), boot.index('gtq::seal();'), name)
            self.assertEqual(boot.count(f'hud::{name}::configure_from_settings('), 2, name)
            self.assertLess(boot.index('hud::snapshot::step(now);'), boot.index(f'hud::{name}::step(now);'), name)
            self.assertIn(f'hud::{name}::stop();', boot)
        self.assertLess(boot.index('hud::menus_draw::step(now);'), boot.index('hud::rings::step(now);'),
                        'the ring follows the menus of the same beat')
        tick = (COOP / 'src' / 'runtime' / 'tick.cpp').read_text(encoding='utf-8')
        self.assertIn('hud::rings::tick(patch::current_mode() == patch::MODE_LIVE);', tick)

    def test_the_operation_number_is_unique(self):
        numbers = []
        for path in (COOP / 'src').rglob('*.h'):
            numbers += [int(n) for n in re.findall(r'constexpr uint16_t OP_\w+ = (\d+);', path.read_text(encoding='utf-8'))]
        self.assertEqual(numbers.count(OP_HUD_RINGS), 1, numbers)

    @unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the pins are checked against the exe)')
    def test_the_attach_pins_are_the_exes_bytes(self):
        exe = (Path(GAME) / 'x64' / 'FFXII_TZA.exe').read_bytes()
        lfanew = struct.unpack_from('<I', exe, 0x3C)[0]
        nsec = struct.unpack_from('<H', exe, lfanew + 6)[0]
        opt = struct.unpack_from('<H', exe, lfanew + 20)[0]
        sections = [struct.unpack_from('<8sIIII', exe, lfanew + 24 + opt + 40 * k) for k in range(nsec)]

        def at(va, n):
            rva = va - 0x120000
            for _, vsize, vaddr, rsize, raw in sections:
                if vaddr <= rva < vaddr + max(vsize, rsize):
                    return exe[raw + rva - vaddr:raw + rva - vaddr + n]
            raise AssertionError(hex(va))

        def tables(text):
            out = []
            for va, n, body in re.findall(r'\{(0x[0-9A-F]+|FN_\w+(?: \+ \d+)?|SITE_\w+(?: - \d+)?), (\d+), \{([^}]*)\}\}',
                                          text):
                out.append((va, int(n), bytes(int(b, 16) for b in re.findall(r'0x([0-9A-F]{2})', body))))
            return out

        names = {'FN_OVERHEAD_PASS': OVERHEAD_PASS, 'SITE_OVERHEAD_CALL - 22': OVERHEAD_CALL - 22,
                 'FN_OBJECT_ALLOC': 0x3F0040, 'FN_SPRITE_FRAME': 0x3F12E0, 'FN_LIST_APPEND + 9': 0x3F1299,
                 'FN_AREA_INFO': 0x31AB80, 'FN_TARGET_LIVE': 0x273710, 'FN_RING_CREATE': 0x28EA20,
                 'SITE_RING_TRIGGER - 30': RING_CALL - 30, 'FN_RING_TRIGGER': RING_TRIGGER}
        found = tables(self.wm) + tables(self.rg)
        self.assertEqual(len(found), 11)
        for va, n, data in found:
            address = names[va] if va in names else int(va, 16)
            with self.subTest(va):
                self.assertEqual(len(data), n)
                self.assertEqual(at(address, n), data)


# ---- the rules ------------------------------------------------------------------------------------------------------
@native_test
class RuleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = native_world().d

    def test_layouts(self):
        out = (u32 * 16)()
        n = self.d.ILCT_WmSizes(out, 16)
        self.assertEqual(list(out[:n]), SIZES)

    def test_line_clut_table(self):
        r = lines(STANDARD)
        d = self.d
        table = [  # source, type, native -> clut
            (SEAT1, 3, 0, GOLD), (SEAT1, 4, 3, GOLD), (SEAT2, 3, 0, SAPPHIRE), (GAMBIT, 3, 0, CLUT_GAMBIT),
            (GAMBIT, 4, 3, CLUT_GAMBIT), (STRANGER, 3, 0, 0), (FOE, 1, 1, 1), (FOE, 2, 5, 5),
            (SEAT1, 1, 1, 1),   # a seat character's line of a foe type (never made natively): the native colour
            (0, 3, 0, 0),
            # manual ch. 7: only the party palettes (type 3 -> 0, type 4 -> 3) are recoloured; a special value is kept
            (SEAT1, 3, 4, 4), (SEAT1, 4, 4, 4), (GAMBIT, 3, 4, 4), (SEAT1, 3, 3, 3), (SEAT1, 4, 0, 0), (SEAT1, 3, 6, 6),
            (SEAT1, 0, 0, 0), (SEAT1, 5, 0, 0),   # types 0 and 5 also map to 0 natively, but are not party lines
        ]
        for source, typ, native, want in table:
            with self.subTest(source=hex(source), type=typ):
                self.assertEqual(d.ILCT_WmLineClut(ctypes.byref(r), source, typ, native), want)
        empty = lines([])
        self.assertEqual(d.ILCT_WmLineClut(ctypes.byref(empty), SEAT1, 3, 0), 0)
        bad = lines([(SEAT1, MARK_SEAT, 1, 9)])
        self.assertEqual(d.ILCT_WmLineClut(ctypes.byref(bad), SEAT1, 3, 0), 0, 'a palette outside the seven: native')

    def test_arrow_clut_seat_colours_every_state_but_red(self):
        """The arrow's native palettes are 4 gold (in range) and 1 red (out of range). Parity with the co-op fork's
        arrow cave and our earlier rule: gold, and the party palettes 0 and 3 (another hook's), take the source's
        entry palette (the seat's colour: the arrow identifies whoever leads; 6 for a gambit ally); red is kept. A
        source without an entry, and any other palette, keep the native value."""
        r = lines(STANDARD)
        d = self.d
        for source, native, want in ((SEAT1, 4, GOLD), (SEAT2, 4, SAPPHIRE), (GAMBIT, 4, CLUT_GAMBIT), (FOE, 4, 4),
                                     (STRANGER, 4, 4), (SEAT1, 0, GOLD), (SEAT2, 3, SAPPHIRE),
                                     (GAMBIT, 3, CLUT_GAMBIT), (FOE, 0, 0), (SEAT1, 6, 6), (SEAT1, 2, 2),
                                     (SEAT1, 5, 5)):
            with self.subTest(source=hex(source), native=native):
                self.assertEqual(d.ILCT_WmArrowClut(ctypes.byref(r), source, native), want)
        empty = lines([])
        self.assertEqual(d.ILCT_WmArrowClut(ctypes.byref(empty), SEAT1, 4), 4, 'no record entries: native gold')

    def test_arrow_clut_red_stays_red(self):
        """The game's out-of-range red (palette 1) is never recoloured, whoever the source is."""
        r = lines(STANDARD)
        d = self.d
        for source in (SEAT1, SEAT2, GAMBIT, FOE, STRANGER, 0):
            with self.subTest(source=hex(source)):
                self.assertEqual(d.ILCT_WmArrowClut(ctypes.byref(r), source, 1), 1)
        ruby = lines([(SEAT1, MARK_SEAT, 1, 1)])   # even a seat whose own palette is red
        self.assertEqual(d.ILCT_WmArrowClut(ctypes.byref(ruby), SEAT1, 1), 1)
        self.assertEqual(d.ILCT_WmArrowClut(ctypes.byref(ruby), SEAT1, 4), 1, 'its gold arrow takes its red')

    def test_colours(self):
        d = self.d
        # 0.1.60: pink (no palette of its own: 2 is yellow) takes purple, bronze (6 is the gambit white) gold; never 6.
        self.assertEqual([d.ILCT_WmClutOfColour(c) for c in range(8)], [0, 1, 5, 3, 4, 5, 4, 0xFF])
        # pink and bronze: the grey gem, tinted with the seat colour (world object RGB)
        self.assertEqual([d.ILCT_WmGemCell(c) for c in range(8)], [1, 4, 7, 2, 6, 3, 7, 0])

    def test_diamond_fade(self):
        d = self.d
        t0 = NOW
        for dt, want in ((-1, 0), (0, 0), (75, 64), (149, 127), (150, 128), (1000, 128), (FADE_IN + HOLD - 1, 128),
                         (FADE_IN + HOLD, 128), (FADE_IN + HOLD + 350, 64), (FADE_IN + HOLD + FADE_OUT - 1, 0),
                         (FADE_IN + HOLD + FADE_OUT, 0), (10_000, 0)):
            with self.subTest(dt=dt):
                self.assertEqual(d.ILCT_WmAlpha(t0, t0 + dt), want)
        self.assertEqual(d.ILCT_WmAlpha(0, NOW), 0, 'never shown')
        values = [d.ILCT_WmAlpha(t0, t0 + k) for k in range(0, FADE_IN + HOLD + FADE_OUT + 10, 5)]
        peak = values.index(128)
        self.assertEqual(values[:peak], sorted(values[:peak]), 'fade-in rises')
        tail = values[peak:]
        self.assertEqual(tail, sorted(tail, reverse=True), 'then holds and falls, never rises again')

    def step(self, st, epoch, seats, identify=0, now=NOW):
        return self.d.ILCT_WmTriggerStep(ctypes.byref(st), epoch, seat_inputs(*seats), identify, now)

    def test_trigger_table(self):
        st = TriggerState()
        p1, p2 = (1, 0, SEAT1, 0), (1, 2, SEAT2, 0)
        self.assertEqual(self.step(st, EPOCH, [p1, None]), 0, 'the first beat of a world is the baseline')
        self.assertEqual([st.seats[k].shown_ms for k in range(4)], [0] * 4)
        self.assertEqual(self.step(st, EPOCH, [p1, None], now=NOW + 20), 0, 'nothing changed')
        self.assertEqual(self.step(st, EPOCH, [p1, p2], now=NOW + 40), TRIG_DROP_IN << 4, 'seat 2 drops in')
        self.assertEqual(st.seats[1].shown_ms, NOW + 40)
        self.assertEqual(self.step(st, EPOCH, [p1, (1, 4, 0x10000005, 0)], now=NOW + 60), TRIG_SWAP << 4, 'swap')
        self.assertEqual(self.step(st, EPOCH, [p1, (1, 4, 0x10000005, 0)], identify=1, now=NOW + 80), TRIG_IDENTIFY)
        self.assertEqual(self.step(st, EPOCH, [p1, (1, 4, 0x10000005, 0)], identify=0b10, now=NOW + 100),
                         TRIG_IDENTIFY << 4)
        fight = self.step(st, EPOCH, [(1, 0, SEAT1, 1), (1, 4, 0x10000005, None)], now=NOW + 120)
        self.assertEqual(fight, TRIG_FIGHT | (TRIG_FIGHT << 4), 'fight start shows every held seat')
        self.assertEqual(self.step(st, EPOCH, [(1, 0, SEAT1, 1), (1, 4, 0x10000005, 1)], now=NOW + 140), 0,
                         'still in the fight')
        self.assertEqual(self.step(st, EPOCH, [p1, (1, 4, 0x10000005, 0)], now=NOW + 1000), 0, 'out of stance')
        self.assertEqual(self.step(st, EPOCH, [(1, 0, SEAT1, 1), None], now=NOW + 1000 + QUIET - 1), 0,
                         'back in stance before the quiet stretch: the same fight (seat 2 left: no trigger for it)')
        self.assertEqual(st.seats[1].shown_ms, 0, 'a seat that left loses its diamond at once')
        self.assertEqual(self.step(st, EPOCH, [p1, None], now=NOW + 5000), 0)
        self.assertEqual(self.step(st, EPOCH, [(1, 0, SEAT1, 1), None], now=NOW + 5000 + QUIET), TRIG_FIGHT,
                         'after the quiet stretch: a new fight')
        self.assertEqual(self.step(st, EPOCH + 1, [(1, 0, SEAT1, 1), p2], now=NOW + 9000), 0,
                         'a new world: baseline again, nothing shown')
        self.assertEqual([st.seats[k].shown_ms for k in range(4)], [0] * 4)
        self.assertEqual(st.epoch, EPOCH + 1)

    def test_a_held_seat_keeps_its_diamond(self):
        """0.1.57 (Marco): identification stays while a seat plays: after the triggers, persist_step fades a held
        seat's diamond in and then holds it at full; a seat that leaves loses it at once."""
        st = TriggerState()
        p1, p2 = (1, 0, SEAT1, 0), (1, 2, SEAT2, 0)
        for now in (NOW, NOW + 20):
            self.step(st, EPOCH, [p1, p2], now=now)
            self.d.ILCT_WmPersistStep(ctypes.byref(st), seat_inputs(p1, p2), now)
        self.assertEqual(st.seats[0].shown_ms, NOW, 'fading in from the first beat of the world')
        for now in range(NOW + 40, NOW + 60_000, 500):
            self.step(st, EPOCH, [p1, p2], now=now)
            self.d.ILCT_WmPersistStep(ctypes.byref(st), seat_inputs(p1, p2), now)
            if now >= NOW + FADE_IN:
                self.assertEqual(self.d.ILCT_WmAlpha(st.seats[0].shown_ms, now), 128)
        self.assertEqual(self.d.ILCT_WmAlpha(st.seats[1].shown_ms, NOW + 59_540), 128, 'seat 2 too, a minute on')
        self.step(st, EPOCH, [p1, None], now=NOW + 60_000)
        self.d.ILCT_WmPersistStep(ctypes.byref(st), seat_inputs(p1, None), NOW + 60_000)
        self.assertEqual(st.seats[1].shown_ms, 0, 'a seat that left loses its diamond at once')

    def test_a_retrigger_keeps_a_visible_diamond_at_full(self):
        st = TriggerState()
        p1 = (1, 0, SEAT1, 0)
        self.step(st, EPOCH, [p1])
        self.step(st, EPOCH, [p1], identify=1, now=NOW + 10)
        self.assertEqual(st.seats[0].shown_ms, NOW + 10)
        self.step(st, EPOCH, [p1], identify=1, now=NOW + 60)
        self.assertEqual(st.seats[0].shown_ms, NOW + 10, 'still fading in: carries on')
        self.step(st, EPOCH, [p1], identify=1, now=NOW + 2000)
        self.assertEqual(st.seats[0].shown_ms, NOW + 2000 - FADE_IN, 'visible: full at once, hold restarted')
        self.assertEqual(self.d.ILCT_WmAlpha(st.seats[0].shown_ms, NOW + 2000), 128)
        self.step(st, EPOCH, [p1], identify=1, now=NOW + 9000)
        self.assertEqual(st.seats[0].shown_ms, NOW + 9000, 'faded out: a fresh fade-in')

    def test_area_kinds_follow_0x31a990(self):
        d = self.d
        table = [(0, 0, AREA_NONE), (0, 0x2, AREA_5), (4, 1 << 12, AREA_NONE), (4, (1 << 12) | 2, AREA_5),
                 (4, 1 << 24, AREA_CONE), (4, (1 << 24) | (1 << 25), AREA_CONE), (4, 1 << 25, AREA_4),
                 (4, 1 << 21, AREA_ALT), (4, 0, AREA_CIRCLE), (8, 0x0C16408C, AREA_CIRCLE)]
        for radius, flags, want in table:
            with self.subTest(radius=radius, flags=hex(flags)):
                self.assertEqual(d.ILCT_RgAreaKind(radius, flags), want)

    def test_families(self):
        d = self.d
        want = {PG_ROOT: F_ATTACK, PG_MAGICKS: F_OTHER, PG_WHITE: F_MAGICK, PG_BLACK: F_MAGICK, PG_TIME: F_MAGICK,
                PG_GREEN: F_MAGICK, PG_ARCANE: F_MAGICK, PG_TECHNICKS: F_TECHNICK, PG_MIST: F_OTHER,
                PG_QUICKENINGS: F_OTHER, PG_SUMMONS: F_OTHER, PG_ITEMS: F_OTHER, 0xFF: F_OTHER}
        for page, family in want.items():
            self.assertEqual(d.ILCT_RgFamily(page), family, page)

    def test_ring_gate_table(self):
        d = self.d
        attack_kind = d.ILCT_RgAreaKind(4, ATTACK_FLAGS_EXPERIMENT)
        self.assertEqual(attack_kind, AREA_CIRCLE, "the experiment makes Attack a radius-4 circle")
        table = [  # action, family, kind -> ring
            (49, F_MAGICK, AREA_CIRCLE, True),          # an area magick (e.g. a -ra/-ga spell)
            (300, F_TECHNICK, AREA_CIRCLE, True),       # an area technick
            (49, F_MAGICK, AREA_NONE, False),           # a single-target magick
            (49, F_MAGICK, AREA_CONE, False),           # a cone (Spiral Cut style)
            (49, F_MAGICK, AREA_ALT, False), (49, F_MAGICK, AREA_4, False), (49, F_MAGICK, AREA_5, False),
            (ATTACK, F_ATTACK, attack_kind, False),     # the Attack Area Experiment: every weapon, greatsword included
            (ATTACK, F_ATTACK, AREA_NONE, False),       # Attack without the experiment
            (ATTACK, F_MAGICK, AREA_CIRCLE, False),     # Attack never, whatever page it is listed on
            (ATTACK, F_TECHNICK, AREA_CIRCLE, False),
            (60, F_OTHER, AREA_CIRCLE, False),          # an item, a Quickening or a Summon with an area
            (49, F_ATTACK, AREA_CIRCLE, False),
        ]
        for action, family, kind, ring in table:
            with self.subTest(action=action, family=family, kind=kind):
                self.assertEqual(bool(d.ILCT_RgAllowed(action, family, kind)), ring)
        self.assertEqual([d.ILCT_RgStockAllowed(a) for a in (ATTACK, 49, 300, 0)], [0, 1, 1, 1],
                         'the stock target list: only Attack is gated')

    def test_wanted_target(self):
        d = self.d

        def aim(**kw):
            a = SeatAim(held=1, mode=MODE_TARGET, page=PG_BLACK, pending_kind=EK_ACTION, action=49, area_kind=AREA_CIRCLE,
                        on_page=1, caster=SEAT1, target=FOE)
            for k, v in kw.items():
                setattr(a, k, v)
            return d.ILCT_RgWanted(ctypes.byref(a))

        self.assertEqual(aim(), FOE)
        self.assertEqual(aim(page=PG_TECHNICKS), FOE)
        for kw in ({'held': 0}, {'mode': MODE_LIST}, {'mode': MODE_CLOSED}, {'pending_kind': EK_PAGE}, {'on_page': 0},
                   {'target': 0}, {'caster': 0}, {'area_kind': AREA_CONE}, {'page': PG_ROOT, 'action': ATTACK},
                   {'page': PG_ITEMS}, {'page': PG_QUICKENINGS}, {'action': ATTACK}):
            with self.subTest(**kw):
                self.assertEqual(aim(**kw), 0)


# ---- the handlers on a synthetic ctx ----------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class LineHandlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = native_world()
        cls.d = cls.hw.d

    def setUp(self):
        self.hw.setup(attach=False)
        self.d.ILCT_WmReset()
        self.node = (ctypes.c_uint8 * 0x900)()
        self.addr = ctypes.addressof(self.node)
        self.d.ILCT_WmPublishLines(ctypes.byref(lines(STANDARD)))

    def tearDown(self):
        self.d.ILCT_WmReset()
        self.hw.twin.reset()

    def line(self, source, typ, native, epoch=EPOCH):
        struct.pack_into('<I', self.node, LINE_SOURCE, source)
        struct.pack_into('<H', self.node, LINE_TYPE, typ)
        c = th.HookCtx()
        c.index, c.r15, c.rax = self.d.ILCT_WmRow(0), self.addr, native
        self.assertEqual(self.d.ILCT_WmLineCall(ctypes.byref(c), epoch), RUN_ORIGINAL)
        return c

    def arrow(self, source, base, half=0, epoch=EPOCH):
        struct.pack_into('<I', self.node, ARROW_SOURCE, source)
        c = th.HookCtx()
        c.index, c.rsi, c.rax, c.r9 = self.d.ILCT_WmRow(1), self.addr, base, half
        self.assertEqual(self.d.ILCT_WmArrowCall(ctypes.byref(c), epoch), RUN_ORIGINAL)
        return (c.rax + 2 * c.r9) & 0xFFFFFFFF, c

    def test_seat_gambit_foe_and_unknown(self):
        self.assertEqual(self.line(SEAT1, 3, 0).rax, GOLD)
        self.assertEqual(self.line(SEAT2, 4, 3).rax, SAPPHIRE)
        self.assertEqual(self.line(GAMBIT, 3, 0).rax, CLUT_GAMBIT)
        self.assertEqual(self.line(FOE, 1, 1).rax, 1)
        self.assertEqual(self.line(STRANGER, 3, 0).rax, 0)
        c = WmCounters()
        self.d.ILCT_WmStats(ctypes.byref(c))
        self.assertEqual(c.lines_recoloured, 3)

    def test_only_the_clut_register_changes(self):
        before = th.HookCtx()
        before.index, before.r15, before.rax = self.d.ILCT_WmRow(0), self.addr, 0
        for name, value in (('rcx', 0x1111), ('rdx', 0x2222), ('r8', 0x3333), ('r9', 0x4444), ('r14', 0x5555)):
            setattr(before, name, value)
        struct.pack_into('<I', self.node, LINE_SOURCE, SEAT1)
        struct.pack_into('<H', self.node, LINE_TYPE, 3)
        after = th.HookCtx.from_buffer_copy(before)
        self.d.ILCT_WmLineCall(ctypes.byref(after), EPOCH)
        diff = [n for n, _ in th.HookCtx._fields_ if n != 'xmm' and getattr(after, n) != getattr(before, n)]
        self.assertEqual(diff, ['rax'])
        self.assertEqual(bytes(after.xmm), bytes(before.xmm))

    def test_stock_without_record_switch_or_with_another_epoch(self):
        self.d.ILCT_WmSetLinesEnabled(0)
        self.assertEqual(self.line(SEAT1, 3, 0).rax, 0, 'element switch off')
        self.d.ILCT_WmSetLinesEnabled(1)
        self.assertEqual(self.line(SEAT1, 3, 0, epoch=EPOCH + 1).rax, 0, 'record of another epoch')
        self.d.ILCT_WmPublishLines(ctypes.byref(lines(STANDARD, enabled=0)))
        self.assertEqual(self.line(SEAT1, 3, 0).rax, 0, 'record says off')
        self.d.ILCT_WmReset()
        self.assertEqual(self.line(SEAT1, 3, 0).rax, 0, 'no record')
        self.d.ILCT_WmPublishLines(ctypes.byref(lines(STANDARD)))
        self.d.ILCT_PatchApplySubsystems(0xFF & ~0x80)
        self.assertEqual(self.line(SEAT1, 3, 0).rax, 0, 'TARGETS_CARDS off')

    def test_an_unreadable_node_is_stock(self):
        c = th.HookCtx()
        c.index, c.r15, c.rax = self.d.ILCT_WmRow(0), 0x10, 0
        self.assertEqual(self.d.ILCT_WmLineCall(ctypes.byref(c), EPOCH), RUN_ORIGINAL)
        self.assertEqual(c.rax, 0)

    def test_arrow(self):
        clut, c = self.arrow(SEAT2, 4)
        self.assertEqual((clut, c.rax, c.r9), (SAPPHIRE, SAPPHIRE, 0), "a seat's gold arrow takes the seat's palette")
        self.assertEqual(self.arrow(SEAT1, 4)[0], GOLD)
        self.assertEqual(self.arrow(GAMBIT, 4)[0], CLUT_GAMBIT)
        clut, c = self.arrow(SEAT2, 1)
        self.assertEqual((clut, c.rax, c.r9), (1, 1, 0), 'red stays red, registers untouched')
        self.assertEqual(self.arrow(FOE, 4)[0], 4)
        clut, c = self.arrow(STRANGER, 2, 1)
        self.assertEqual((clut, c.rax, c.r9), (4, 2, 1), 'untouched registers when nothing changes')
        clut, c = self.arrow(SEAT1, 0, 0)   # a party palette (another hook's): the seat's, through eax with r9 = 0
        self.assertEqual((clut, c.rax, c.r9), (GOLD, GOLD, 0), 'eax = clut, r9 = 0: the lea gives the clut')
        clut, c = self.arrow(SEAT2, 2, 1)   # gold made as 2 + 2 * 1: the seat's, eax = clut and r9 cleared
        self.assertEqual((clut, c.rax, c.r9), (SAPPHIRE, SAPPHIRE, 0))
        c = WmCounters()
        self.d.ILCT_WmStats(ctypes.byref(c))
        self.assertEqual(c.arrows_recoloured, 4, 'SEAT2 gold, GAMBIT gold, SEAT1 party palette 0, SEAT2 2 + 2 * 1')


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class DiamondTest(unittest.TestCase):
    """WM-01's handler and after-handler on host memory with doubles of the pool, frame and list functions."""

    CAP = 128

    @classmethod
    def setUpClass(cls):
        cls.hw = native_world()
        cls.d = cls.hw.d

    def setUp(self):
        d = self.d
        self.hw.setup(attach=False)
        d.ILCT_WmReset()
        d.ILCT_WmUseDoubles(1)
        self.img = Image()
        self.atlases = self.img.alloc(0x400)
        self.texture = self.img.alloc(0x100)
        self.img.put_global(UI_ATLASES, struct.pack('<Q', self.atlases))
        self.img.p64(self.atlases + ATLAS_PARTYTOP, self.texture)
        d.ILCT_WmSetImageBase(self.img.base)
        self.manager = self.img.alloc(0x2000)
        self.objects = self.img.alloc(self.CAP * 0x100)
        self.pool = self.img.alloc(16)
        self.list_b = self.img.alloc(32)
        self.records = []
        prev = self.manager + 0x1D70 - 0x18   # so that "next" of the head pointer is the manager's own field
        for k, handle in enumerate((FOE, SEAT1, SEAT2, GAMBIT)):
            rec = self.img.alloc(0x40)
            self.img.put(rec, struct.pack('<4f', 1.0 + k, 2.0, 3.0 + k, 1.0))
            self.img.p32(rec + 0x10, handle)
            self.img.p64(prev + 0x18, rec)
            prev = rec
            self.records.append(rec)
        self.reset_pool(0)

    def tearDown(self):
        self.d.ILCT_WmUseDoubles(0)
        self.d.ILCT_WmReset()
        self.hw.twin.reset()
        self.img.close()

    def reset_pool(self, used, cap=None):
        self.img.p64(self.pool, self.objects)
        self.img.put(self.pool + 8, struct.pack('<ii', self.CAP if cap is None else cap, used))
        self.img.put(self.list_b, bytes(32))

    def publish(self, seats, enabled=1, epoch=EPOCH):
        r = DiamondsRecord()
        r.epoch, r.enabled, r.sampled_ms = epoch, enabled, NOW
        for s, (handle, gem, shown) in seats.items():
            r.seats[s].handle, r.seats[s].seat, r.seats[s].gem, r.seats[s].shown_ms = handle, s + 1, gem, shown
        self.d.ILCT_WmPublishDiamonds(ctypes.byref(r))

    def frame(self, now=NOW + 500):
        d = self.d
        d.ILCT_SetFixedClock(now)
        c = th.HookCtx()
        c.index, c.rcx, c.rdx, c.r8, c.r9 = d.ILCT_WmRow(2), self.manager, self.img.alloc(32), self.list_b, self.pool
        res = d.ILCT_WmOverheadCall(ctypes.byref(c), EPOCH)
        if res == CALL_AROUND:
            d.ILCT_WmOverheadAfter(ctypes.byref(c), EPOCH)
        return res

    def used(self):
        return struct.unpack_from('<i', ctypes.string_at(self.pool + 12, 4))[0]

    def chain(self):
        out, obj = [], struct.unpack('<Q', ctypes.string_at(self.list_b, 8))[0]
        while obj:
            out.append(obj)
            obj = struct.unpack('<Q', ctypes.string_at(obj + 0xD0, 8))[0]
        return out

    def log(self):
        buf = (u64 * 256)()
        n = self.d.ILCT_WmFrameLog(buf, 64)
        return [tuple(buf[4 * k:4 * k + 4]) for k in range(n)]

    def test_nothing_visible_runs_the_pass_as_stock(self):
        self.publish({0: (SEAT1, 6, 0), 1: (SEAT2, 1, NOW - 10_000)})
        self.assertEqual(self.frame(), RUN_ORIGINAL)
        self.assertEqual((self.used(), self.chain()), (0, []))

    def test_a_gem_and_its_numeral_per_visible_seat(self):
        self.publish({0: (SEAT1, 6, NOW), 1: (SEAT2, 1, NOW)})
        self.assertEqual(self.frame(), CALL_AROUND)
        objs = self.chain()
        self.assertEqual((self.used(), len(objs)), (4, 4))
        self.assertEqual([(s, c) for _, _, s, c in self.log()],
                         [(GEM_SHEET, 6), (DIGIT_SHEET, 1), (GEM_SHEET, 1), (DIGIT_SHEET, 2)])
        self.assertTrue(all(tex == self.texture for _, tex, _, _ in self.log()), 'partytop_4_c')
        gem = objs[0]
        anchor = struct.unpack('<4f', ctypes.string_at(gem, 16))
        self.assertEqual(anchor, (2.0, 2.0, 4.0, -1.0), "seat 1's record anchor, w = -1 like the leader marker")
        r, g, b, a = struct.unpack('<4I', ctypes.string_at(gem + 0x40, 16))
        self.assertEqual((r, g, b, a), (128, 128, 128, 128))
        quad = struct.unpack('<4f', ctypes.string_at(gem + 0x50, 16))
        self.assertEqual(quad, (-10.0, -17.0, 10.0, 18.0), 'the 40 x 70 gem at 50 %, centred')
        lift = struct.unpack('<2f', ctypes.string_at(gem + 0x70, 8))
        self.assertEqual(lift, (-48.0, 1.0))
        digit_quad = struct.unpack('<4f', ctypes.string_at(objs[1] + 0x50, 16))
        self.assertEqual(digit_quad, (-7.0, -9.0, 7.0, 10.0), 'the 28 x 38 numeral at 50 %, over the gem')
        self.assertEqual(struct.unpack('<4f', ctypes.string_at(objs[2], 16)), (3.0, 2.0, 5.0, -1.0), "seat 2's anchor")

    def test_alpha_follows_the_fade(self):
        self.publish({0: (SEAT1, 6, NOW)})
        self.frame(now=NOW + FADE_IN + HOLD + 350)
        self.assertEqual(struct.unpack('<I', ctypes.string_at(self.chain()[0] + 0x4C, 4))[0], 64)

    def test_the_pool_is_never_overrun_and_never_grows(self):
        self.publish({0: (SEAT1, 6, NOW), 1: (SEAT2, 1, NOW)})
        for used in (self.CAP - 3, self.CAP - 2, self.CAP - 1, self.CAP):
            with self.subTest(used=used):
                self.reset_pool(used)
                self.d.ILCT_WmUseDoubles(1)
                self.frame()
                self.assertLessEqual(self.used(), self.CAP)
                self.assertEqual(len(self.chain()) % 2, 0, 'a gem never without its numeral')
                self.assertEqual(len(self.chain()), {self.CAP - 3: 2, self.CAP - 2: 2}.get(used, 0))
        for _ in range(3):   # the game resets the pool every frame: our share is the same every frame
            self.reset_pool(10)
            self.frame()
            self.assertEqual((self.used(), len(self.chain())), (14, 4))
        c = WmCounters()
        self.d.ILCT_WmStats(ctypes.byref(c))
        self.assertGreater(c.pool_full, 0)

    def test_no_anchor_no_texture_switch_off(self):
        self.publish({0: (0x10000044, 6, NOW)})
        self.assertEqual(self.frame(), CALL_AROUND)
        self.assertEqual(self.chain(), [], 'a character without an overhead record shows nothing')
        c = WmCounters()
        self.d.ILCT_WmStats(ctypes.byref(c))
        self.assertEqual(c.no_anchor, 1)
        self.publish({0: (SEAT1, 6, NOW)})
        self.img.p64(self.atlases + ATLAS_PARTYTOP, 0)
        self.frame()
        self.assertEqual(self.chain(), [], 'partytop_4_c not loaded')
        self.img.p64(self.atlases + ATLAS_PARTYTOP, self.texture)
        self.d.ILCT_WmSetDiamondsEnabled(0)
        self.assertEqual(self.frame(), RUN_ORIGINAL)
        self.d.ILCT_WmSetDiamondsEnabled(1)
        self.publish({0: (SEAT1, 6, NOW)}, enabled=0)
        self.assertEqual(self.frame(), RUN_ORIGINAL)
        self.publish({0: (SEAT1, 0, NOW)})
        self.assertEqual(self.frame(), RUN_ORIGINAL, 'no gem cell: nothing')
        self.publish({0: (SEAT1, 6, NOW)})
        self.d.ILCT_PatchApplySubsystems(0xFF & ~0x80)
        self.assertEqual(self.frame(), RUN_ORIGINAL, 'TARGETS_CARDS off')
        self.assertEqual(self.chain(), [])


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class RingTest(unittest.TestCase):
    """The ring operation over doubles of the game's area info, liveness check and factory (a synthetic area list)."""

    @classmethod
    def setUpClass(cls):
        cls.hw = native_world()
        cls.d = cls.hw.d

    def setUp(self):
        d = self.d
        self.hw.setup(attach=False)
        d.ILCT_RgReset()
        self.ui = d.ILCT_RgUseDoubles(1)
        d.ILCT_RgSetAttached(1)
        d.ILCT_RgDoubleInfo(49, 0, 3, AREA_CIRCLE, 4.0)     # an area magick
        d.ILCT_RgDoubleInfo(300, 0, 3, AREA_CIRCLE, 6.0)    # an area technick
        d.ILCT_RgDoubleInfo(394, 0, 3, AREA_CONE, 6.0)      # a cone
        d.ILCT_RgDoubleInfo(77, -1, 0, 0, 0.0)              # no info
        for h in (FOE, FOE + 1, FOE + 2, FOE + 3):
            d.ILCT_RgDoubleLive(h)

    def tearDown(self):
        self.d.ILCT_RgUseDoubles(0)
        self.d.ILCT_RgReset()
        self.hw.twin.reset()

    def publish(self, seats, enabled=1, epoch=EPOCH):
        r = RingsRecord()
        r.epoch, r.enabled = epoch, enabled
        for s, (target, action, clut, order) in seats.items():
            r.seats[s].caster, r.seats[s].target, r.seats[s].action = SEAT1 + s, target, action
            r.seats[s].clut, r.seats[s].order = clut, order
        self.d.ILCT_RgPublish(ctypes.byref(r))

    def nodes(self):
        out, node = [], struct.unpack('<Q', ctypes.string_at(self.ui + UI_AREA_LIST, 8))[0]
        while node:
            clut, flags = struct.unpack_from('<I4xI', ctypes.string_at(node + RING_CLUT, 12))
            key = struct.unpack('<I', ctypes.string_at(node + RING_KEY, 4))[0]
            out.append((key, clut, flags & 1))
            node = struct.unpack('<Q', ctypes.string_at(node + RING_NEXT, 8))[0]
        return sorted(out)

    def applied(self):
        a = (Applied * 4)()
        self.d.ILCT_RgApplied(a)
        return [(x.target, x.clut) for x in a]

    def test_one_ring_per_seat_four_at_once(self):
        self.publish({0: (FOE, 49, GOLD, 0), 1: (FOE + 1, 300, SAPPHIRE, 1), 2: (FOE + 2, 49, EMERALD, 2),
                      3: (FOE + 3, 300, AMETHYST, 3)})
        self.assertEqual(self.d.ILCT_RgRunJob(self.ui, EPOCH), 1)
        self.assertEqual(self.nodes(), [(FOE, GOLD, 1), (FOE + 1, SAPPHIRE, 1), (FOE + 2, EMERALD, 1),
                                        (FOE + 3, AMETHYST, 1)], 'four live rings, none retiring another')
        self.assertEqual(self.applied(), [(FOE, GOLD), (FOE + 1, SAPPHIRE), (FOE + 2, EMERALD), (FOE + 3, AMETHYST)])
        self.assertEqual(self.d.ILCT_RgRunJob(self.ui, EPOCH), 1)
        self.assertEqual(len(self.nodes()), 4, 'a refresh updates the same nodes')

    def test_a_shared_target_takes_the_most_recent_seats_colour(self):
        self.publish({0: (FOE, 49, GOLD, 1), 1: (FOE, 300, SAPPHIRE, 0)})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.assertEqual(self.nodes(), [(FOE, GOLD, 1)], 'one node per target: seat 1 aimed last')
        self.publish({0: (FOE, 49, GOLD, 0), 1: (FOE, 300, SAPPHIRE, 1)})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.assertEqual(self.nodes(), [(FOE, SAPPHIRE, 1)])
        self.publish({1: (FOE, 300, SAPPHIRE, 1)})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.assertEqual(self.nodes(), [(FOE, SAPPHIRE, 1)], 'seat 1 left: seat 2 still wants it, not released')

    def test_refused_rings_are_never_applied(self):
        self.publish({0: (FOE, 394, GOLD, 0), 1: (FOE + 1, 77, SAPPHIRE, 1), 2: (0x20000077, 49, EMERALD, 2)})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.assertEqual((self.nodes(), self.applied()), ([], [(0, 0)] * 4), 'cone, no info, target not live')
        c = RgCounters()
        self.d.ILCT_RgStats(ctypes.byref(c))
        self.assertEqual((c.refused, c.created), (3, 0))

    def test_released_when_the_page_closes_or_the_target_changes(self):
        self.publish({0: (FOE, 49, GOLD, 0)})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.publish({0: (FOE + 1, 49, GOLD, 0)})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.assertEqual(self.nodes(), [(0, GOLD, 0), (FOE + 1, GOLD, 1)], 'the old ring released (key and bit 0)')
        self.publish({})
        self.assertEqual(self.d.ILCT_RgRunJob(self.ui, EPOCH), 1)
        self.assertEqual(self.nodes(), [(0, GOLD, 0), (0, GOLD, 0)])
        self.assertEqual(self.applied(), [(0, 0)] * 4)

    def test_a_record_of_another_epoch_or_switched_off_releases_everything(self):
        for undo in (lambda: self.publish({0: (FOE, 49, GOLD, 0)}, epoch=EPOCH + 1),
                     lambda: self.publish({0: (FOE, 49, GOLD, 0)}, enabled=0),
                     lambda: self.d.ILCT_RgSetEnabled(0),
                     lambda: self.d.ILCT_RgForceLive(0)):
            with self.subTest():
                self.d.ILCT_RgSetEnabled(1)
                self.d.ILCT_RgForceLive(-1)
                self.publish({0: (FOE, 49, GOLD, 0)})
                self.d.ILCT_RgRunJob(self.ui, EPOCH)
                self.assertEqual(self.nodes()[-1], (FOE, GOLD, 1))
                undo()
                self.assertEqual(self.d.ILCT_RgRunJob(self.ui, EPOCH), 2)
                self.assertNotIn(FOE, [k for k, _, live in self.nodes() if live])
        self.d.ILCT_RgSetEnabled(1)
        self.d.ILCT_RgForceLive(-1)

    def test_others_rings_are_never_released(self):
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        native = self.ui + 0x400   # a native ring on another target, linked first
        self.hw_node = (ctypes.c_uint8 * 0x80).from_address(native)
        struct.pack_into('<I', self.hw_node, RING_KEY, FOE + 3)
        struct.pack_into('<I', self.hw_node, RING_FLAGS, 1)
        ctypes.memmove(self.ui + UI_AREA_LIST, struct.pack('<Q', native), 8)
        self.publish({0: (FOE, 49, GOLD, 0)})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.publish({})
        self.d.ILCT_RgRunJob(self.ui, EPOCH)
        self.assertIn((FOE + 3, 0, 1), self.nodes(), 'only targets we applied are released')

    def test_the_tick_gives_rings_back_when_not_wanted(self):
        img = Image()   # the tick finds the UI manager through its global
        try:
            img.put_global(0x209BE80, struct.pack('<Q', self.ui))
            self.d.ILCT_RgSetImageBase(img.base)
            self.publish({0: (FOE, 49, GOLD, 0)})
            self.d.ILCT_RgRunJob(self.ui, EPOCH)
            self.d.ILCT_RgTick(1)
            self.assertEqual(self.nodes(), [(FOE, GOLD, 1)], 'wanted: kept')
            self.d.ILCT_RgTick(0)
            self.assertEqual((self.nodes(), self.applied()), ([(0, GOLD, 0)], [(0, 0)] * 4), 'DRY: given back')
            for undo, redo in ((lambda: self.d.ILCT_RgSetEnabled(0), lambda: self.d.ILCT_RgSetEnabled(1)),
                               (lambda: self.d.ILCT_RgForceLive(0), lambda: self.d.ILCT_RgForceLive(-1)),
                               (lambda: self.publish({0: (FOE, 49, GOLD, 0)}, enabled=0), lambda: None)):
                with self.subTest():
                    self.publish({0: (FOE, 49, GOLD, 0)})
                    self.d.ILCT_RgRunJob(self.ui, EPOCH)
                    self.assertEqual(self.applied()[0], (FOE, GOLD))
                    undo()
                    self.d.ILCT_RgTick(1)
                    self.assertEqual(self.applied(), [(0, 0)] * 4, 'switch, subsystem or record off: given back')
                    self.assertNotIn(FOE, [k for k, _, live in self.nodes() if live])
                    redo()
            c = RgCounters()
            self.d.ILCT_RgStats(ctypes.byref(c))
            self.assertEqual(c.give_backs, 4)
            self.d.ILCT_RgTick(0)
            self.d.ILCT_RgStats(ctypes.byref(c))
            self.assertEqual(c.give_backs, 4, 'nothing applied: the tick does nothing')
        finally:
            self.d.ILCT_RgSetImageBase(0x120000)
            img.close()

    def test_the_cleave_gate_on_the_stock_list(self):
        self.publish({})
        for action, flag, want in ((ATTACK, 0, 2), (ATTACK, 2, 2), (49, 0, 0), (300, 2, 2), (0x1_0096, 0, 2)):
            with self.subTest(action=action, flag=flag):
                c = th.HookCtx()
                c.index, c.rcx, c.rdx, c.r8, c.r9 = self.d.ILCT_RgRow(), SEAT1, FOE, action, flag
                self.assertEqual(self.d.ILCT_RgTriggerCall(ctypes.byref(c), EPOCH), RUN_ORIGINAL)
                self.assertEqual((c.r9, c.rcx, c.rdx, c.r8), (want, SEAT1, FOE, action))
        c = th.HookCtx()
        c.index, c.r8 = self.d.ILCT_RgRow(), ATTACK
        self.d.ILCT_RgSetEnabled(0)
        self.d.ILCT_RgTriggerCall(ctypes.byref(c), EPOCH)
        self.assertEqual(c.r9, 0, 'the element switch off: stock')
        self.d.ILCT_RgSetEnabled(1)
        self.d.ILCT_RgReset()
        self.d.ILCT_RgTriggerCall(ctypes.byref(c), EPOCH)
        self.assertEqual(c.r9, 0, 'no record (co-op off, no world): stock')


# ---- the co-op thread's records -------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class RecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = native_world()
        cls.d = cls.hw.d

    def setUp(self):
        d = self.d
        self.hw.setup(attach=False)
        d.ILCT_HudDrvReset()
        d.ILCT_CtlReset()
        d.ILCT_WmReset()
        d.ILCT_RgReset()
        d.ILCT_WmSetAttached(7)
        d.ILCT_RgSetAttached(1)
        self.img = Image()
        d.ILCT_WmSetImageBase(self.img.base)
        d.ILCT_RgSetImageBase(self.img.base)
        self.actors = [self.img.alloc(0x100) for _ in range(3)]
        rec = Record()
        rec.head.epoch, rec.head.generation, rec.head.flags = EPOCH, gt.GEN, GATE['WORLD']
        for s in range(4):
            rec.seat[s].member, rec.seat[s].party_slot = 0xFFFF, 0xFF
        for s, member in ((0, 0), (1, 2)):   # P1 Vaan, P2 Fran
            e = rec.seat[s]
            e.flags = SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['ACTOR']
            e.member, e.handle, e.party_slot, e.actor = member, 0x100000 | member, member, self.actors[s]
        self.rec = rec
        d.ILCT_TownSessionPublish(ctypes.byref(rec))
        snap = hd.snapshot(flags=hd.SNAP_WORLD | hd.SNAP_PARTY)
        snap.member_count = 3
        for k, (member, handle, seat) in enumerate(((0, 0x100000, 1), (1, 0x100001, 0), (2, 0x100002, 2))):
            u = snap.members[k]
            u.member, u.handle, u.seat, u.flags = member, handle, seat, hd.UNIT_PRESENT | hd.UNIT_ACTOR | hd.UNIT_PARTY
        self.snap = snap
        d.ILCT_HudSnapPublish(ctypes.byref(snap))

    def tearDown(self):
        d = self.d
        d.ILCT_WmReset()
        d.ILCT_RgReset()
        d.ILCT_HudDrvReset()
        d.ILCT_TownSessionWithdraw()
        d.ILCT_CtlReset()
        self.hw.twin.reset()
        self.img.close()

    def records(self, now=NOW):
        self.d.ILCT_WmStep(now)
        l, m = LinesRecord(), DiamondsRecord()
        ok = (self.d.ILCT_WmReadLines(ctypes.byref(l)), self.d.ILCT_WmReadDiamonds(ctypes.byref(m)))
        return ok, l, m

    def test_lines_by_seat_colour_and_gambit_silver(self):
        ok, l, m = self.records()
        self.assertEqual(ok, (1, 1))
        got = [(l.entries[k].handle, l.entries[k].kind, l.entries[k].seat, l.entries[k].clut) for k in range(l.count)]
        self.assertEqual(got, [(0x100000, MARK_SEAT, 1, SAPPHIRE), (0x100002, MARK_SEAT, 2, AMETHYST),
                               (0x100001, MARK_GAMBIT, 0, CLUT_GAMBIT)],
                         'by character (the default colour mode): Vaan sapphire, Fran amethyst; Ashe unseated: silver')
        self.assertEqual((l.epoch, l.enabled, m.enabled), (EPOCH, 1, 1))

    def test_diamonds_baseline_then_identify_drop_in_and_fight(self):
        d = self.d
        _, _, m = self.records()
        # 0.1.57: a held seat keeps its diamond (persist_step), from the world's first beat on; the triggers still
        # restart a faded one, and a fresh drop-in fades in from its own beat.
        self.assertEqual([m.seats[s].shown_ms for s in range(4)], [NOW, NOW, 0, 0], 'held seats show at once')
        d.ILCT_WmIdentify(1)
        _, _, m = self.records(NOW + 20)
        self.assertEqual([m.seats[s].shown_ms for s in range(4)], [NOW, NOW, 0, 0], 'still fading in: carries on')
        self.assertEqual((m.seats[1].handle, m.seats[1].seat, m.seats[1].gem), (0x100002, 2, 3), 'Fran: amethyst gem')
        self.img.p8(self.actors[0] + 2, 0x80)   # P1 draws the weapon: fight start
        _, _, m = self.records(NOW + 40)
        self.assertEqual(m.seats[0].shown_ms, NOW)
        self.assertEqual(m.seats[0].gem, 1, 'Vaan: sapphire gem')
        e = self.rec.seat[2]
        e.flags = SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['ACTOR']
        e.member, e.handle, e.actor = 1, 0x100001, self.actors[2]
        d.ILCT_TownSessionPublish(ctypes.byref(self.rec))
        ok, l, m = self.records(NOW + 60)
        self.assertEqual(m.seats[2].shown_ms, NOW + 60, 'P3 drops in')
        self.assertNotIn(MARK_GAMBIT, [l.entries[k].kind for k in range(l.count)], 'Ashe is seated now')

    def test_withdrawn_without_a_world_co_op_or_the_subsystem(self):
        self.assertEqual(self.records()[0], (1, 1))
        self.d.ILCT_TownSessionWithdraw()
        self.assertEqual(self.records()[0], (0, 0))
        self.d.ILCT_TownSessionPublish(ctypes.byref(self.rec))
        self.assertEqual(self.records()[0], (1, 1))
        self.d.ILCT_PatchApplySubsystems(0xFF & ~0x80)
        self.assertEqual(self.records()[0], (0, 0), 'TARGETS_CARDS off')
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_CtlKill(1)
        self.d.ILCT_CtlStep(NOW + 40)
        self.assertEqual(self.records(NOW + 40)[0], (0, 0), 'co-op off')

    def test_the_ring_record_from_the_seats_aims(self):
        d = self.d
        aims = (SeatAim * 4)()
        aims[0] = SeatAim(held=1, mode=MODE_TARGET, page=PG_BLACK, pending_kind=EK_ACTION, action=49,
                          area_kind=AREA_CIRCLE, on_page=1, caster=0x100000, target=FOE)
        aims[1] = SeatAim(held=1, mode=MODE_TARGET, page=PG_ROOT, pending_kind=EK_ACTION, action=ATTACK,
                          area_kind=AREA_CIRCLE, on_page=1, caster=0x100002, target=FOE + 1)
        d.ILCT_RgSetAims(ctypes.cast(aims, ctypes.c_void_p))
        d.ILCT_RgStep(NOW)
        r = RingsRecord()
        self.assertEqual(d.ILCT_RgRead(ctypes.byref(r)), 1)
        self.assertEqual([(r.seats[s].caster, r.seats[s].target, r.seats[s].action, r.seats[s].clut) for s in range(2)],
                         [(0x100000, FOE, 49, SAPPHIRE), (0, 0, 0, 0)], "Vaan's area magick; Fran's Attack: none")
        d.ILCT_RgSetAims(None)
        d.ILCT_TownSessionWithdraw()
        d.ILCT_RgStep(NOW + 20)
        self.assertEqual(d.ILCT_RgRead(ctypes.byref(r)), 0, 'no world: withdrawn')


# ---- the patch layer and attach on the twin -------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class TwinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = native_world()
        cls.twin = cls.hw.twin
        cls.d = cls.hw.d
        cls.rows = {r['id']: k for k, r in enumerate(pe.site_rows())}

    def tearDown(self):
        self.d.ILCT_WmReset()
        self.d.ILCT_RgReset()
        self.twin.reset()

    def hooked(self, sid) -> bool:
        return bool(self.twin.info(self.rows[sid]).trampoline)

    def test_all_stock_hooks_every_row(self):
        self.twin.reset()
        self.assertEqual(self.twin.call('ILCT_PatchInstall').result, pe.OK)
        for sid in ('TG-05', 'TG-06', 'WM-01', 'RG-01'):
            self.assertTrue(self.hooked(sid), sid)
        for va in (OVERHEAD_CALL, RING_CALL):
            self.assertEqual(ctypes.string_at(TWIN_BASE + va - 0x120000, 1), b'\xE8', 'the call stays a call')

    def test_a_rewritten_call_is_skipped_not_refused(self):
        for sid, va in (('WM-01', OVERHEAD_CALL), ('RG-01', RING_CALL)):
            with self.subTest(sid):
                self.twin.reset()
                self.twin.poke(va, b'\x90' * 5)
                report = self.twin.call('ILCT_PatchPreflight')
                self.assertEqual((report.result, report.gated_off), (pe.OK, 1))
                self.assertEqual(self.twin.call('ILCT_PatchInstall').result, pe.OK)
                self.assertFalse(self.hooked(sid))
                self.assertTrue(self.hooked('TG-06') and self.hooked('HD-01'), 'the rest installs')
                self.assertEqual(ctypes.string_at(TWIN_BASE + va - 0x120000, 5), b'\x90' * 5, 'their bytes stay')

    def test_attach_sets_every_handler_on_the_stock_twin(self):
        self.hw.setup(attach=False)
        self.assertEqual(self.d.ILCT_WmAttach(TWIN_BASE), 1)
        s = WmStatus()
        self.d.ILCT_WmStatus(ctypes.byref(s))
        self.assertEqual(s.attached, 7)
        # The operation's bit depends on the queue (registered once per process, refused once sealed); RG-01 attaches
        # on its own either way.
        r = RgStatus()
        self.d.ILCT_RgAttach(TWIN_BASE)
        self.d.ILCT_RgStatus(ctypes.byref(r))
        self.assertTrue(r.attached & 2, 'RG-01')

    def test_attach_fails_closed_on_changed_game_bytes(self):
        for va, row_bit, module in ((0x3F0040, 4, 'wm'), (OVERHEAD_PASS + 30, 4, 'wm'), (OVERHEAD_CALL - 3, 4, 'wm'),
                                    (RING_TRIGGER + 1, 2, 'rg'), (RING_CALL - 2, 2, 'rg')):
            with self.subTest(va=hex(va)):
                self.hw.setup(attach=False)
                self.d.ILCT_WmReset()
                self.d.ILCT_RgReset()
                self.d.ILCT_RgSetAttached(0)
                self.twin.poke(va, bytes([ctypes.string_at(TWIN_BASE + va - 0x120000, 1)[0] ^ 0xFF]))
                if module == 'wm':
                    self.assertEqual(self.d.ILCT_WmAttach(TWIN_BASE), 0)
                    s = WmStatus()
                    self.d.ILCT_WmStatus(ctypes.byref(s))
                    self.assertEqual(s.attached, 3, 'the line rows attach; WM-01 stays stock')
                else:
                    self.d.ILCT_RgAttach(TWIN_BASE)
                    r = RgStatus()
                    self.d.ILCT_RgStatus(ctypes.byref(r))
                    self.assertEqual(r.attached & 2, 0, 'RG-01 stays stock')


# ---- unicorn on the hooked twin -------------------------------------------------------------------------------------
NODE = pe.SCRATCH + 0x60000


@native_test
@unittest.skipUnless(GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires IVALICE_LEGACY_GAME, unicorn and capstone')
class NativeLineTest(unittest.TestCase):
    """The game's own target-line and arrow code in unicorn on the hooked twin: from the type load through TG-06 and
    0x2713b0 to the CLUT bind 0x2715a0, and from the arrow's loads through TG-05 to the bind 0x181930."""

    @classmethod
    def setUpClass(cls):
        import test_coop_movement as mv
        cls.hw = native_world()
        cls.d = cls.hw.d
        cls.hw.setup(attach=False)
        cls.d.ILCT_WmReset()
        cls.d.ILCT_WmAttach(TWIN_BASE)
        with quiet_faulthandler():   # unicorn's own first-chance faults while it maps memory
            cls.emu = mv.MoveEmulator(cls.hw.twin)
        cls.rows = {r['id']: k for k, r in enumerate(pe.site_rows())}

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_WmReset()
        cls.d.ILCT_WmSetAttached(0)
        cls.hw.twin.reset()

    def setUp(self):
        self.d.ILCT_WmPublishLines(ctypes.byref(lines(STANDARD)))

    def run_site(self, sid, start, stop, setup, regs, hooked=True):
        emu, uc = self.emu, self.emu.uc
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        setup(uc)
        emu.force_gate(self.rows[sid], hooked)
        seen = []

        def record(u, address):
            X = pe.X
            seen.append((address, u.reg_read(X.UC_X86_REG_R9) & 0xFFFFFFFF, u.reg_read(X.UC_X86_REG_R14) & 0xFFFFFFFF))

        def allowed(x):
            if not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE):
                return True
            return start <= x <= stop or TYPE_TO_CLUT <= x < TYPE_TO_CLUT + 0x29

        state = pe.random_state(random.Random(0x4949))
        state['gpr'].update(regs)
        state['gpr']['RSP'] &= ~0xF
        try:
            with quiet_faulthandler():
                out = emu.run(start, allowed, state, on_callee=record)
        finally:
            emu.force_gate(self.rows[sid], False)
        self.assertEqual(emu.unexpected, [], 'imports the emulator does not answer')
        self.assertEqual(out['exit'], stop + 5, 'back after the bind call')
        return seen

    def line(self, source, typ, hooked=True):
        def setup(uc):
            uc.mem_write(NODE, bytes(0x900))
            uc.mem_write(NODE + LINE_SOURCE, struct.pack('<I', source))
            uc.mem_write(NODE + LINE_TYPE, struct.pack('<H', typ))
        seen = self.run_site('TG-06', TYPE_LOAD, LINE_BIND, setup, {'R15': NODE}, hooked)
        self.assertEqual([a for a, _, _ in seen], [0x2715A0], 'the bind is the only call left to the game')
        _, r9, r14 = seen[0]
        self.assertEqual(r9, r14, 'r9d and r14d (every later use) carry the same CLUT')
        return r9

    def arrow(self, source, base, hooked=True):
        def setup(uc):
            uc.mem_write(NODE, bytes(0x400))
            uc.mem_write(NODE + ARROW_SOURCE, struct.pack('<I', source))
            uc.mem_write(NODE + ARROW_HALF, struct.pack('<I', 0))
            uc.mem_write(NODE + ARROW_BASE, struct.pack('<I', base))
        seen = self.run_site('TG-05', ARROW_LOADS, ARROW_BIND, setup, {'RSI': NODE, 'RDI': 0x40, 'RBX': 0x40}, hooked)
        self.assertEqual([a for a, _, _ in seen], [0x181930])
        return seen[0][1]

    def test_target_lines(self):
        table = [(SEAT1, 3, GOLD), (SEAT2, 3, SAPPHIRE), (SEAT1, 4, GOLD), (GAMBIT, 3, CLUT_GAMBIT),
                 (GAMBIT, 4, CLUT_GAMBIT), (FOE, 1, 1), (FOE, 2, 5), (STRANGER, 3, 0), (STRANGER, 4, 3)]
        for source, typ, want in table:
            with self.subTest(source=hex(source), type=typ):
                self.assertEqual(self.line(source, typ), want)

    def test_lines_disarmed_are_the_stock_cluts(self):
        for source, typ, want in ((SEAT1, 3, 0), (SEAT1, 4, 3), (GAMBIT, 3, 0), (FOE, 1, 1), (FOE, 2, 5)):
            with self.subTest(source=hex(source), type=typ):
                self.assertEqual(self.line(source, typ, hooked=False), want)
        self.d.ILCT_WmSetLinesEnabled(0)
        try:
            self.assertEqual(self.line(SEAT1, 3), 0, 'element switch off: stock through the hook')
        finally:
            self.d.ILCT_WmSetLinesEnabled(1)

    def test_the_arrow(self):
        """Through the game's own arrow code: a seat's gold arrow reaches the bind in the seat's palette (a gambit
        ally's in 6), red reaches it unchanged for every source, a foe's or a stranger's gold stays gold."""
        for source, base, want in ((SEAT1, 4, GOLD), (SEAT2, 4, SAPPHIRE), (GAMBIT, 4, CLUT_GAMBIT), (SEAT1, 1, 1),
                                   (SEAT2, 1, 1), (GAMBIT, 1, 1), (FOE, 4, 4), (STRANGER, 4, 4),
                                   (SEAT1, 0, GOLD), (FOE, 0, 0)):
            with self.subTest(source=hex(source), base=base):
                self.assertEqual(self.arrow(source, base), want)
        self.assertEqual(self.arrow(SEAT1, 4, hooked=False), 4, 'disarmed: the stock gold')


if __name__ == '__main__':
    unittest.main()
