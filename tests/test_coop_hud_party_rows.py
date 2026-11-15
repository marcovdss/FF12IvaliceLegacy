"""Host tests for the party list row additions (native/coop/src/hud/party_rows.*). No game run, nothing deployed; the
exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin the patch layer installs on.

* the site rows: HD-02 is an OPTIONAL RENDER call row on the party row update's call of the HP digits function,
  HD-03 one on that function's call of the digit setter; HD-V9/HD-V11 (the HP digits function, the call's arguments)
  gate HD-02 and HD-V10 (the digit setter) gates HD-03; signatures measured at one hit; on the twin all stock hooks
  both, a gate or a rewritten call skips only its row, never the install;
* the anchor table: row k of n at y = G40.y - gap * (n - 1 - k) under the stock and the reduced layout, mapped into
  the canvas with one scale (15/13) from the stock bottom row at 970; the stock rows land exactly on the measured
  party-target geometry (790 + 60 * visual row), the reduced bottom row on its reduced form; relayout's state picks
  the geometry, any other state draws nothing;
* the rules: time-left formatting (seconds, minutes, Doom/Petrify counters), seat numerals and colours (by seat or by
  character), the low-HP threshold (the game's HP Critical bit or below a fifth, doubled under Bubble, never at 0 or
  KO), the glow's pulse, the damage ghost's hold and drain, the status icons' order and cap;
* the pass on a snapshot and the co-op thread's record: edge and numeral per row (G for gambit allies, struck through
  with gambits off), the ghost on the HP gauge, statuses with time left, "Choosing..." and the target suffix,
  party-target hands per seat, nothing for an Esper row, nothing without the record or a known geometry or with the
  element off, the pass's own command cap; through the frame driver it is the first pass;
* the co-op thread's record from a synthetic game: rows matched by member, gambits bit, colours by colour mode, the
  geometry from relayout's state; withdrawn without a world;
* the HP colour handlers called directly: a low-HP row forces the digits (the cache set to -1 around the call and put
  back after it) and arms the glow for the digit setter call of that row only; a coloured row that recovers is forced
  once more with the native colour; the element off or RENDER off is stock;
* the NumberColors interplay: Legacy's number-colour script (battle pop-ups: the type and style tables, the palette
  read-only) and this element (the party row's own digit colour argument) share no address;
* unicorn on the hooked twin: the game's own party row code from the HP digits call: a low-HP row reaches the digit
  setter with the glow colour and the cache comes back; a row that is not low early-exits as stock; both rows
  disarmed is the stock path bit for bit.
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
from coop_session_support import FACT, GATE, PHASE, SEATF, GameWorld, Image, Record
from test_coop_patch_engine import GAME, OK, TWIN_BASE, checked

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'

u8, u16, u32, u64, i16, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int16, \
    ctypes.c_int32
P = ctypes.POINTER
EPOCH, NOW = gt.EPOCH, gt.NOW

# ---- game facts (VAs at the pinned base 0x120000) ---------------------------------------------------------------------
ROW_UPDATE = 0x29A0A0          # the party row window's update
HP_ARGS = 0x29A544             # mov r8d,[rbx+0x44]; mov edx,r15d; mov rcx,rbp; mov edi,eax
HP_CALL = 0x29A550             # call 0x297d10 (HD-02)
AFTER_HP_CALL = 0x29A555
HP_DIGITS = 0x297D10           # the HP digits function
DIGITS_CALL = 0x297DF1         # call 0x29f6e0 after mov rcx,rsi (HD-03)
DIGIT_SETTER = 0x29F6E0
ROW_CACHE, ROW_STATUS_COLOUR, ROW_NODES, ROW_DIGITS = 0xE0, 0x108, 0xC0, 0xDC
RECORD_MAX, RECORD_STATUS = 0x24, 0x44
NUMBER_COLORS_ADDRESSES = (0x120000 + 0x1DCD300, 0x120000 + 0xC027A8, 0x120000 + 0x203E6B8, 0x120000 + 0x203E6C8)
HP_ROWS = {'HD-02': (HP_CALL, 'e8 bb d7 ff ff', HP_DIGITS), 'HD-03': (DIGITS_CALL, 'e8 ea 78 00 00', DIGIT_SETTER)}
HP_GATES = {'HD-V9': ('HD-02', HP_DIGITS, '48 89 6c 24 18 48 89 74 24 20 57 48 83 ec 30 33 f6 8b ea 48 8b f9 3b 91 e0 '
                                          '00 00 00 75 1b 48 39 b1 08 01 00 00 75 12'),
            'HD-V11': ('HD-02', HP_ARGS, '44 8b 43 44 41 8b d7 48 8b cd 8b f8'),
            'HD-V10': ('HD-03', DIGIT_SETTER, '48 89 5c 24 10 48 89 6c 24 18 48 89 74 24 20 57')}
WHITE = 0x80808080
HP_CRITICAL, BUBBLE, KO = 1 << 29, 1 << 28, 1
PROTECT, SHELL, HASTE, POISON, SLOW, DOOM, PETRIFY, REGEN = 18, 19, 20, 8, 15, 6, 2, 25
# relayout layout_state
L_UNKNOWN, L_UNLOADED, L_INVALID, L_SETTLING, L_REDUCED, L_REDUCED_OTHER, L_FOREIGN, L_FAILED, L_BUSY, L_STOCK = range(10)
STOCK, REDUCED, NONE = 0, 1, 0xFF
COLOUR_NONE = 0xFF
SAPPHIRE, RUBY, PINK, EMERALD, GOLD, AMETHYST, BRONZE = range(7)
MODE_CHARACTER, MODE_PLAYER = 0, 1
EXTRA_GAMBITS_ON, EXTRA_ACTING, EXTRA_CHOOSING, EXTRA_CHOOSING_TARGET, EXTRA_READ = 0x1, 0x2, 0x4, 0x8, 0x10
RUN_ORIGINAL, RESUME, CALL_AROUND = 0, 1, 2
ATLAS_PARTYTOP_4_C, ATLAS_CURSOL_4_P = 1, 5
PASS_MAX = 40


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


Anchor = S('Anchor', [('top', i16), ('pitch', i16), ('name_x', i16), ('name_y', i16), ('edge_x', i16),
                      ('edge_y', i16), ('edge_w', i16), ('edge_h', i16), ('numeral_x', i16), ('numeral_y', i16),
                      ('numeral_scale', u16), ('gauge_x', i16), ('gauge_y', i16), ('gauge_w', i16),
                      ('gauge_h', i16), ('status_x', i16), ('status_y', i16), ('status_w', i16), ('status_h', i16),
                      ('status_stride', i16), ('timer_dx', i16), ('timer_dy', i16), ('timer_scale', u16),
                      ('label_x', i16), ('label_y', i16), ('label_scale', u16), ('suffix_x', i16), ('suffix_y', i16),
                      ('suffix_scale', u16), ('cursor_x', i16), ('cursor_y', i16), ('cursor_w', i16),
                      ('cursor_h', i16)])
RowExtra = S('RowExtra', [('member', u16), ('flags', u8), ('colour', u8), ('action', u16), ('reserved', u16)])
RowsRecord = S('RowsRecord', [('epoch', u32), ('flags', u32), ('generation', u64), ('sampled_ms', u64),
                              ('geometry', u8), ('enabled', u8), ('reserved', u8 * 2), ('seat_colour', u8 * 4),
                              ('rows', RowExtra * 4)])
Ghost = S('Ghost', [('member', u16), ('reserved', u16), ('hp_max', u32), ('hp', u32), ('from_', u32), ('hit_ms', u64)])
StatusPick = S('StatusPick', [('id', u8), ('reserved', u8 * 3), ('seconds', i32)])
RowsCounters = S('RowsCounters', [(n, u64) for n in ('samples', 'drawn_frames', 'no_geometry', 'no_record', 'capped',
                                                     'forced', 'glowed', 'hp_declined')])
RowsStatus = S('RowsStatus', [('attached', u8), ('enabled', u8), ('hp_rows_hooked', u8), ('geometry', u8)])
SIZES = [ctypes.sizeof(Anchor), ctypes.sizeof(RowsRecord), ctypes.sizeof(RowExtra), ctypes.sizeof(Ghost),
         ctypes.sizeof(StatusPick), ctypes.sizeof(RowsCounters), ctypes.sizeof(RowsStatus), PASS_MAX, 2]

SIGNATURES = {
    'ILCT_RowsSizes': ([P(u32), u32], u32), 'ILCT_RowsAnchor': ([u32, u32, u32, P(Anchor)], i32),
    'ILCT_RowsLayoutY': ([u32, u32, u32], i32), 'ILCT_RowsCanvasY': ([u32, i32], i32),
    'ILCT_RowsGeometryOf': ([u32], u32), 'ILCT_RowsLowHp': ([u32, u32, u32], i32), 'ILCT_RowsGlow': ([u64], u32),
    'ILCT_RowsTimer': ([i32, u32, u32, ctypes.c_char_p, u32], u32), 'ILCT_RowsSeconds': ([i32, u32], i32),
    'ILCT_RowsBeneficial': ([u32], i32), 'ILCT_RowsGlyph': ([u32, i32], i32),
    'ILCT_RowsRowColour': ([u32, P(u8), P(u8), u32, u32], u32), 'ILCT_RowsColourRgba': ([u32], u32),
    'ILCT_RowsGhostStep': ([P(Ghost), u32, u32, u32, u64], u32),
    'ILCT_RowsPick': ([P(hd.Unit), u32, P(StatusPick), u32], u32),
    'ILCT_RowsReset': ([], None), 'ILCT_RowsPublish': ([P(RowsRecord)], None), 'ILCT_RowsRead': ([P(RowsRecord)], i32),
    'ILCT_RowsSetEnabled': ([i32], None), 'ILCT_RowsSetAttached': ([i32], None), 'ILCT_RowsSetImageBase': ([u64], None),
    'ILCT_RowsForceLayoutState': ([i32], None), 'ILCT_RowsConfigure': ([ctypes.c_void_p], None),
    'ILCT_RowsStep': ([u64], None), 'ILCT_RowsStop': ([], None), 'ILCT_RowsStats': ([P(RowsCounters)], None),
    'ILCT_RowsStatus': ([P(RowsStatus)], None), 'ILCT_RowsAttach': ([u64], i32),
    'ILCT_RowsDraw': ([P(hd.Snapshot), u64, P(th.Frame)], u32),
    'ILCT_RowsHpCall': ([P(th.HookCtx), u32], i32), 'ILCT_RowsHpAfter': ([P(th.HookCtx), u32], None),
    'ILCT_RowsDigitsCall': ([P(th.HookCtx), u32], i32), 'ILCT_RowsRow': ([u32], i32),
    'ILCT_RowsGlowPending': ([P(u64), P(u32)], i32), 'ILCT_RowsColoured': ([], u32),
    'ILCT_RowsCountersAddress': ([], u64),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def scale(v: int) -> int:
    """Layout units -> canvas units: 15/13, rounded half away from zero (the module's rule)."""
    p = v * 15
    return (p + 6) // 13 if p >= 0 else -((-p + 6) // 13)


def unpack_rgba(c: int) -> tuple:
    return c & 0xFF, c >> 8 & 0xFF, c >> 16 & 0xFF, c >> 24


def cmds(frame) -> list:
    pool = ctypes.string_at(ctypes.addressof(frame) + th.Frame.text.offset, th.TEXT_POOL)
    out = []
    for k in range(frame.count):
        c = frame.cmds[k]
        if c.kind == th.CMD_TEXT:
            out.append(('text', pool[c.text:].split(b'\0', 1)[0], c.x, c.y, c.colour, c.scale))
        elif c.kind == th.CMD_SPRITE:
            out.append(('sprite', c.atlas, c.page, c.sheet, c.cell, c.x, c.y, c.w, c.h, c.colour))
    return out


def texts(frame) -> list:
    return [c[1] for c in cmds(frame) if c[0] == 'text']


def sprites(frame, atlas=None, page=None, sheet=None) -> list:
    return [c for c in cmds(frame) if c[0] == 'sprite' and (atlas is None or c[1] == atlas) and
            (page is None or c[2] == page) and (sheet is None or c[3] == sheet)]


# ---- static -------------------------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
class SiteRowsTest(unittest.TestCase):
    def test_the_hp_colour_rows(self):
        rows = pe.site_rows()
        index = {r['id']: k for k, r in enumerate(rows)}
        for sid, (va, expected, callee) in HP_ROWS.items():
            with self.subTest(sid):
                r = rows[index[sid]]
                self.assertEqual((r['kind'], r['policy'], r['subsystem']), ('CALL', 'PATCH', 'RENDER'))
                self.assertEqual(r['rva'], va - 0x120000)
                self.assertEqual(checked(r), bytes.fromhex(expected))
                self.assertEqual(r['flags'], 0x08, 'OPTIONAL: a rewrite by another mod skips it, nothing else')
                self.assertEqual(pe.call_offset(r), 0, 'a call row (an island)')
                self.assertEqual(va + 5 + struct.unpack('<i', r['expected'][1:5])[0], callee)

    def test_the_gates(self):
        rows = pe.site_rows()
        index = {r['id']: k for k, r in enumerate(rows)}
        for sid, (gated, va, expected) in HP_GATES.items():
            with self.subTest(sid):
                r = rows[index[sid]]
                self.assertEqual((r['kind'], r['policy'], r['subsystem']), ('VERIFY', 'GATE', 'RENDER'))
                self.assertEqual(r['gate'], str(index[gated]))
                self.assertEqual(r['rva'], va - 0x120000)
                self.assertEqual(r['expected'], bytes.fromhex(expected))
        legacy = {r['id']: r for r in json.loads((COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8'))['sites']}
        for sid in (*HP_ROWS, *HP_GATES):
            self.assertEqual(legacy[sid]['signature']['hits_in_text'], 1, sid)
            self.assertEqual(legacy[sid]['group'], 'hud', sid)


class SourceTest(unittest.TestCase):
    def setUp(self):
        self.cpp = (HUD_SRC / 'party_rows.cpp').read_text(encoding='utf-8')
        self.h = (HUD_SRC / 'party_rows.h').read_text(encoding='utf-8')

    def body(self, signature: str) -> str:
        start = self.cpp.index(signature)
        end = self.cpp.index('\n}\n', start)
        return self.cpp[start:end]

    def test_the_pass_and_the_handlers_never_block_allocate_or_read_the_session(self):
        for sig in ('void draw(canvas& cv, const driver::pass_input& in)', 'void draw_row(',
                    'int32_t hp_call(patch::ctx* c', 'void hp_after(patch::ctx* c', 'int32_t digits_call(patch::ctx* c',
                    'uint32_t pick_statuses('):
            text = self.body(sig)
            for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                              'memcpy(', 'memset(', 'session::read', 'game_reader', 'snapshot::read', 'relayout::'):
                self.assertNotIn(forbidden, text, f'{sig}: {forbidden}')
        self.assertNotIn('copy_to_caller', self.body('void draw(canvas& cv'), 'the pass writes no game memory')

    def test_the_handlers_write_only_the_row_cache(self):
        writes = re.findall(r'store_u32\(([^,]+),', self.cpp)
        self.assertEqual(sorted(set(w.strip() for w in writes)), ['g_force_row + ROW_HP_CACHE', 'row + ROW_HP_CACHE',
                                                                  'uint64_t address'])
        self.assertEqual(self.cpp.count('copy_to_caller('), 1, 'one writer: store_u32')

    def test_the_driver_runs_it_as_the_party_pass(self):
        driver = (HUD_SRC / 'driver.cpp').read_text(encoding='utf-8')
        m = re.search(r'void pass_party_extras\(canvas& cv, const pass_input& in\)\n\{(.*?)\n\}', driver, re.S)
        self.assertIsNotNone(m)
        self.assertIn('party_rows::draw(cv, in);', m.group(1))

    def test_the_boot_wiring(self):
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        attach = boot.index('hud::party_rows::attach(')
        self.assertLess(boot.index('hud::native::attach();'), attach)
        self.assertLess(attach, boot.index('gtq::seal();'), 'handlers set before the install is sealed')
        self.assertLess(boot.index('hud::snapshot::step(now);'), boot.index('hud::party_rows::step(now);'),
                        'the record follows the snapshot of the same beat')
        self.assertEqual(boot.count('hud::party_rows::configure_from_settings('), 2)
        self.assertIn('hud::party_rows::stop();', boot)

    def test_number_colors_shares_no_address(self):
        ours = self.cpp + self.h
        for va in NUMBER_COLORS_ADDRESSES:
            for text in (f'0x{va:X}', f'0x{va:x}', f'0x{va - 0x120000:X}', f'0x{va - 0x120000:x}'):
                self.assertNotIn(text, ours, 'the pop-up number tables and the palette are not ours')
        for name in ('prototypes/number_colors.lua', 'prototypes/IvaliceLegacyNumberColors.lua'):
            lua = (ROOT / name).read_text(encoding='utf-8').lower()
            for va in (HP_CALL, DIGITS_CALL, HP_DIGITS, DIGIT_SETTER, ROW_UPDATE):
                self.assertNotIn(f'{va:x}', lua, f'{name}: the party row digits are not the script\'s')
                self.assertNotIn(f'{va - 0x120000:x}', lua, name)
            self.assertNotIn('0x297', lua, name)
        script = (ROOT / 'prototypes' / 'number_colors.lua').read_text(encoding='utf-8')
        # The script writes only the pop-up type table; the palette is read (dump) and compared, never written.
        writes = re.findall(r'mem\.u32\[([^\]]+)\]\s*=(?!=)', script)
        self.assertEqual(writes, ['M.TYPE_TABLE + 4 * i'], 'its one write target is the pop-up type table')


# ---- the rules ----------------------------------------------------------------------------------------------------------
@native_test
class RulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(gt.world().d)

    def test_layouts(self):
        out = (u32 * 16)()
        n = self.d.ILCT_RowsSizes(out, 16)
        self.assertEqual(list(out[:n]), SIZES)

    def test_geometry_follows_relayout(self):
        expect = {L_REDUCED: REDUCED, L_REDUCED_OTHER: REDUCED, L_SETTLING: STOCK, L_STOCK: STOCK, L_FAILED: STOCK,
                  L_UNKNOWN: NONE, L_UNLOADED: NONE, L_INVALID: NONE, L_FOREIGN: NONE, L_BUSY: NONE, 10: NONE,
                  0xFFFFFFFF: NONE}
        for state, geometry in expect.items():
            with self.subTest(state=state):
                self.assertEqual(self.d.ILCT_RowsGeometryOf(state), geometry)

    def anchor(self, geometry, index, rows) -> Anchor:
        a = Anchor()
        self.assertEqual(self.d.ILCT_RowsAnchor(geometry, index, rows, ctypes.byref(a)), 1)
        return a

    def test_rows_follow_the_native_formula_after_the_shrink(self):
        for geometry, (g40_y, gap) in ((STOCK, (228, 52)), (REDUCED, (242, 42))):
            for rows in range(1, 5):
                for index in range(rows):
                    with self.subTest(geometry=geometry, rows=rows, index=index):
                        layout_y = g40_y - gap * (rows - 1 - index)
                        self.assertEqual(self.d.ILCT_RowsLayoutY(geometry, index, rows), layout_y)
                        a = self.anchor(geometry, index, rows)
                        self.assertEqual(a.top, 970 + scale(layout_y - 228))
                        self.assertEqual(a.pitch, scale(gap))
                        if index:
                            prev = self.anchor(geometry, index - 1, rows)
                            self.assertIn(a.top - prev.top, (scale(gap) - 1, scale(gap), scale(gap) + 1))
        # bottom-anchored: the last row is at the same place whatever the count
        for geometry in (STOCK, REDUCED):
            self.assertEqual(len({self.anchor(geometry, n - 1, n).top for n in range(1, 5)}), 1)

    def test_stock_rows_match_the_measured_party_target_geometry(self):
        # The measured party-target geometry (game facts): rows at 790 + 60 * visual row, the hand's sprite at x 1184 and
        # 8 below the row top; reduced: 3/4 about (1972, 1027), 3 lower, 48x40, which the reduced bottom row matches.
        for rows in range(1, 5):
            for index in range(rows):
                visual = 4 - rows + index
                a = self.anchor(STOCK, index, rows)
                self.assertEqual((a.top, a.cursor_x, a.cursor_y, a.cursor_w, a.cursor_h),
                                 (790 + 60 * visual, 1184, 790 + 60 * visual + 8, 64, 54))
        a = self.anchor(REDUCED, 3, 4)
        ref_x = 1972 + (1184 - 1972) * 0.75
        ref_y = 1027 + (790 + 60 * 3 + 5 - 1027) * 0.75 + 3
        self.assertEqual((a.cursor_x, a.cursor_y, a.cursor_w, a.cursor_h), (round(ref_x), round(ref_y), 48, 40))
        for geometry in (STOCK, REDUCED):
            a = self.anchor(geometry, 0, 4)
            self.assertEqual(a.name_x, a.cursor_x + a.cursor_w - 2, 'the hand points at the name')

    def test_elements_are_placed_by_their_layout_entries(self):
        # stock: E19 name (540, 8), E6 HP gauge (680, 36, 200, 22), MP number ends at 1040, G50 at (-32, +2) from G40
        # with E1 (236, 0) and E0 (292, 29, 200); reduced: E19 (405, 3), E6 (510, 27, 150, 17), MP ends at 778, G50 at
        # (-44, +2), E1 (165, 0), E0 (183, 20, 140).
        facts = {STOCK: dict(name=(540, 8), gauge=(680, 36, 200, 22), mp=1040, g50=(-32, 2), e1=(236, 0),
                             e0=(292, 29, 200), name_x=1246, label=80, numeral=80),
                 REDUCED: dict(name=(405, 3), gauge=(510, 27, 150, 17), mp=778, g50=(-44, 2), e1=(165, 0),
                               e0=(183, 20, 140), name_x=1427, label=56, numeral=64)}
        for geometry, f in facts.items():
            with self.subTest(geometry=geometry):
                a = self.anchor(geometry, 1, 3)
                x = lambda lx: f['name_x'] + scale(lx - f['name'][0])
                self.assertEqual((a.name_x, a.name_y), (f['name_x'], a.top + scale(f['name'][1])))
                self.assertEqual((a.gauge_x, a.gauge_y, a.gauge_w, a.gauge_h),
                                 (x(f['gauge'][0]), a.top + scale(f['gauge'][1]), scale(f['gauge'][2]),
                                  scale(f['gauge'][3])))
                self.assertGreater(a.status_x, x(f['mp']), 'statuses start right of the MP number')
                self.assertEqual((a.label_x, a.label_y, a.label_scale),
                                 (x(f['g50'][0] + f['e1'][0]), a.top + scale(f['g50'][1] + f['e1'][1]), f['label']))
                self.assertGreater(a.suffix_x, x(f['g50'][0] + f['e0'][0] + f['e0'][2]), 'after the action gauge')
                self.assertEqual(a.suffix_y, a.top + scale(f['g50'][1] + f['e0'][1]))
                self.assertLess(a.edge_x + a.edge_w, a.name_x, 'the edge sits left of the name')
                self.assertLess(a.numeral_x, a.edge_x, 'the numeral sits left of the edge')
                self.assertEqual(a.numeral_scale, f['numeral'])
                self.assertTrue(0 < a.edge_h < a.pitch)
                last_text = a.status_x + a.status_stride * 1 + a.status_w
                self.assertLessEqual(last_text, 1920, 'two icons fit before the screen edge')

    def test_bad_arguments(self):
        a = Anchor()
        for args in ((NONE, 0, 1), (2, 0, 1), (STOCK, 0, 0), (STOCK, 4, 4), (STOCK, 0, 5)):
            self.assertEqual(self.d.ILCT_RowsAnchor(*args, ctypes.byref(a)), 0, args)

    def timer(self, ticks, scale_=1, sid=PROTECT) -> str:
        buf = ctypes.create_string_buffer(8)
        n = self.d.ILCT_RowsTimer(ticks, scale_, sid, buf, 8)
        self.assertEqual(n, len(buf.value))
        return buf.value.decode()

    def test_timer_formatting(self):
        cases = [(1, 1, '1'), (300, 1, '1'), (301, 1, '2'), (3000, 1, '10'), (6000, 2, '10'), (29700, 1, '99'),
                 (29701, 1, '2m'), (30000, 1, '2m'), (36000, 1, '2m'), (36001, 1, '3m'), (72000, 2, '2m'),
                 (300 * 60 * 99, 1, '99m'), (300 * 60 * 200, 1, '99m'), (0, 1, ''), (-5, 1, ''), (3000, 0, ''),
                 (3000, 9, '')]
        for ticks, scale_, text in cases:
            with self.subTest(ticks=ticks, scale=scale_):
                self.assertEqual(self.timer(ticks, scale_), text)
        # Doom and Petrify hold a step counter: shown as the overhead count (value - 1), tick scale irrelevant
        for sid in (DOOM, PETRIFY):
            self.assertEqual([self.timer(v, 0, sid) for v in (11, 10, 2, 1, 0)], ['10', '9', '1', '0', ''])
        self.assertEqual(self.d.ILCT_RowsTimer(3000, 1, 32, ctypes.create_string_buffer(8), 8), 0)
        self.assertEqual(self.d.ILCT_RowsTimer(3000, 1, PROTECT, ctypes.create_string_buffer(8), 3), 0)
        self.assertEqual([self.d.ILCT_RowsSeconds(t, s) for t, s in ((600, 1), (601, 1), (600, 2), (0, 1), (600, 0))],
                         [2, 3, 1, -1, -1])

    def test_seat_numerals(self):
        self.assertEqual([chr(self.d.ILCT_RowsGlyph(s, 1)) for s in (1, 2, 3, 4)], ['1', '2', '3', '4'])
        self.assertEqual(chr(self.d.ILCT_RowsGlyph(0, 1)), 'G', 'an unseated party member is a gambit ally')
        self.assertEqual(self.d.ILCT_RowsGlyph(0, 0), 0)
        self.assertEqual(self.d.ILCT_RowsGlyph(5, 1), ord('G'))

    def test_seat_colours(self):
        seats = (u8 * 4)(GOLD, SAPPHIRE, EMERALD, AMETHYST)
        chars = (u8 * 6)(SAPPHIRE, PINK, AMETHYST, BRONZE, EMERALD, GOLD)
        c = lambda mode, seat, member: self.d.ILCT_RowsRowColour(mode, seats, chars, seat, member)
        self.assertEqual([c(MODE_PLAYER, s, 3) for s in (1, 2, 3, 4)], [GOLD, SAPPHIRE, EMERALD, AMETHYST])
        self.assertEqual([c(MODE_CHARACTER, 1, m) for m in range(6)], [SAPPHIRE, PINK, AMETHYST, BRONZE, EMERALD, GOLD])
        self.assertEqual(c(MODE_CHARACTER, 2, 9), SAPPHIRE, 'a guest takes its seat colour')
        self.assertEqual(c(MODE_CHARACTER, 0, 1), COLOUR_NONE, 'unseated: no seat colour (gambit silver)')
        self.assertEqual(c(MODE_PLAYER, 5, 1), COLOUR_NONE)
        bad = (u8 * 4)(9, 9, 9, 9)
        self.assertEqual(self.d.ILCT_RowsRowColour(MODE_PLAYER, bad, chars, 1, 0), COLOUR_NONE)
        palette = [unpack_rgba(self.d.ILCT_RowsColourRgba(k)) for k in range(7)]
        self.assertEqual(len(set(palette)), 7, 'seven distinct seat colours')
        self.assertTrue(all(p[3] == 0x80 and max(p[:3]) <= 0x80 for p in palette), 'PS2 scale, opaque')
        self.assertEqual(unpack_rgba(self.d.ILCT_RowsColourRgba(COLOUR_NONE)), (0x60, 0x64, 0x6C, 0x80), 'silver')

    def test_low_hp_threshold(self):
        f = self.d.ILCT_RowsLowHp
        self.assertEqual([f(hp, 1000, 0) for hp in (1, 199, 200, 201, 1000)], [1, 1, 0, 0, 0], 'below a fifth')
        self.assertEqual([f(hp, 1004, 0) for hp in (199, 200, 201)], [1, 0, 0], 'integer fifth (200)')
        self.assertEqual([f(hp, 1000, BUBBLE) for hp in (399, 400)], [1, 0], 'Bubble doubles the maximum')
        self.assertEqual(f(900, 1000, HP_CRITICAL), 1, 'the game\'s own HP Critical status wins')
        self.assertEqual(f(0, 1000, 0), 0, 'never at 0 HP (the row shows KO)')
        self.assertEqual(f(50, 1000, KO), 0, 'never KO')
        self.assertEqual(f(0, 1000, HP_CRITICAL), 0)
        self.assertEqual(f(50, 0, 0), 0, 'no maximum known: only the status decides')

    def test_glow_pulses_red(self):
        values = [unpack_rgba(self.d.ILCT_RowsGlow(t)) for t in range(0, 1400, 50)]
        self.assertTrue(all(v[:3] == (0x9C, 0x2C, 0x2C) for v in values), 'red, a little over 1.0')
        alphas = [v[3] for v in values]
        self.assertEqual((alphas[0], min(alphas), alphas[14]), (0x80, 0x4C, 0x4C), 'full, dipping to 0.6 at the middle')
        self.assertEqual(self.d.ILCT_RowsGlow(1400), self.d.ILCT_RowsGlow(0), 'period 1.4 s')
        self.assertEqual(alphas[:15], sorted(alphas[:15], reverse=True), 'smooth: no jumps back')

    def test_the_damage_ghost(self):
        g = Ghost()
        step = lambda hp, t, member=0, mx=1000: self.d.ILCT_RowsGhostStep(ctypes.byref(g), member, hp, mx, t)
        self.assertEqual(step(1000, NOW), 1000, 'first sight: no ghost')
        self.assertEqual(step(600, NOW + 10), 1000, 'a hit: the lost HP shows')
        self.assertEqual(step(600, NOW + 10 + 1200), 1000, 'held 1.2 s')
        mid = step(600, NOW + 10 + 1400)
        self.assertTrue(600 < mid < 1000, mid)
        self.assertEqual(step(600, NOW + 10 + 1600), 600, 'drained in 0.4 s')
        self.assertEqual(step(600, NOW + 3000), 600)
        # a second hit while held extends from what shows
        step(1000, NOW + 4000, member=1)
        step(700, NOW + 4010, member=1)
        self.assertEqual(step(400, NOW + 4500, member=1), 1000, 'the ghost keeps the first HP')
        self.assertEqual(step(400, NOW + 4500 + 1199, member=1), 1000)
        # a heal clears it; another member or a new maximum starts over
        self.assertEqual(step(1000, NOW + 4600, member=1), 1000)
        self.assertEqual(step(300, NOW + 4700, member=1), 1000)
        self.assertEqual(step(300, NOW + 4701, member=2), 300, 'another member in this row')
        self.assertEqual(step(100, NOW + 4702, member=2, mx=900), 100, 'the maximum changed')
        drain = [step(100 if k == 0 else 0, NOW + 9000 + k, member=3) for k in range(2)]
        self.assertEqual(drain, [100, 100])

    def test_status_icons_order_and_cap(self):
        u = hd.Unit()
        for sid, ticks in ((POISON, 900), (PROTECT, 3000), (HASTE, 600), (SLOW, 6000), (DOOM, 5)):
            u.duration[sid] = ticks
        out = (StatusPick * 8)()
        n = self.d.ILCT_RowsPick(ctypes.byref(u), 1, out, 8)
        self.assertEqual([(out[k].id, out[k].seconds) for k in range(n)],
                         [(HASTE, 2), (PROTECT, 10), (POISON, 3), (DOOM, 4), (SLOW, 20)],
                         'beneficial first, then the soonest to run out')
        n = self.d.ILCT_RowsPick(ctypes.byref(u), 1, out, 2)
        self.assertEqual([out[k].id for k in range(n)], [HASTE, PROTECT])
        n = self.d.ILCT_RowsPick(ctypes.byref(u), 0, out, 8)
        self.assertEqual([out[k].id for k in range(n)], [PROTECT, HASTE, DOOM, POISON, SLOW],
                         'unknown tick scale: times unknown (then by bit), Doom keeps its counter')
        self.assertEqual(self.d.ILCT_RowsPick(ctypes.byref(hd.Unit()), 1, out, 8), 0)
        self.assertEqual([self.d.ILCT_RowsBeneficial(k) for k in (PROTECT, SHELL, HASTE, REGEN, 30, POISON, SLOW, DOOM,
                                                                  17, 27)],
                         [1, 1, 1, 1, 1, 0, 0, 0, 0, 0])


# ---- the pass -------------------------------------------------------------------------------------------------------------
def unit(member, seat=0, hp=1000, hp_max=1000, handle=None, flags=None, target=0):
    u = hd.Unit()
    u.member, u.seat, u.hp, u.hp_max = member, seat, hp, hp_max
    u.handle = handle if handle is not None else 0x100000 | member
    u.flags = flags if flags is not None else (hd.UNIT_PRESENT | hd.UNIT_ACTOR | hd.UNIT_PARTY)
    u.target = target
    return u


def party_snapshot(units, now=NOW, tick_scale=1) -> hd.Snapshot:
    s = hd.snapshot(sampled=now, flags=hd.SNAP_WORLD | hd.SNAP_PARTY | hd.SNAP_TICKS)
    s.member_count = len(units)
    s.tick_scale = tick_scale
    for k, u in enumerate(units):
        s.members[k] = u
    for k in range(len(units), 4):
        s.members[k].member = 0xFFFF
    return s


def record(rows, geometry=REDUCED, now=NOW, seat_colours=(GOLD, SAPPHIRE, EMERALD, AMETHYST), enabled=1):
    r = RowsRecord()
    r.epoch, r.generation, r.sampled_ms, r.geometry, r.enabled = EPOCH, 1, now, geometry, enabled
    for k, c in enumerate(seat_colours):
        r.seat_colour[k] = c
    for k in range(4):
        r.rows[k].member, r.rows[k].colour, r.rows[k].action = 0xFFFF, COLOUR_NONE, 0xFFFF
    for k, (member, flags, colour, action) in enumerate(rows):
        r.rows[k].member, r.rows[k].flags, r.rows[k].colour, r.rows[k].action = member, flags, colour, action
    return r


@native_test
class PassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(hd.declare(gt.world().d))

    def setUp(self):
        self.d.ILCT_RowsReset()

    def tearDown(self):
        self.d.ILCT_RowsReset()

    def draw(self, snap, now=NOW) -> th.Frame:
        f = th.Frame()
        self.d.ILCT_RowsDraw(ctypes.byref(snap), now, ctypes.byref(f))
        return f

    def anchor(self, geometry, index, rows) -> Anchor:
        a = Anchor()
        self.d.ILCT_RowsAnchor(geometry, index, rows, ctypes.byref(a))
        return a

    def stats(self) -> RowsCounters:
        c = RowsCounters()
        self.d.ILCT_RowsStats(ctypes.byref(c))
        return c

    def four(self):
        units = [unit(0, seat=1), unit(2, seat=2), unit(1), unit(5)]
        rows = [(0, EXTRA_READ | EXTRA_GAMBITS_ON, SAPPHIRE, 0xFFFF), (2, EXTRA_READ, AMETHYST, 0xFFFF),
                (1, EXTRA_READ | EXTRA_GAMBITS_ON, COLOUR_NONE, 0xFFFF), (5, EXTRA_READ, COLOUR_NONE, 0xFFFF)]
        return units, rows

    def test_edges_and_numerals_on_every_row(self):
        units, rows = self.four()
        self.d.ILCT_RowsPublish(ctypes.byref(record(rows)))
        f = self.draw(party_snapshot(units))
        self.assertEqual(texts(f), [b'\x86', b'\x87', th.encoded('G'), th.encoded('G'), th.encoded('/')],
                         '1, 2 (st2e digits), G, and G struck through: gambits off')
        edges = sprites(f, ATLAS_PARTYTOP_4_C, 0, 6)
        self.assertEqual(len(edges), 4)
        for k, e in enumerate(edges):
            a = self.anchor(REDUCED, k, 4)
            self.assertEqual((e[4], e[5], e[6], e[7], e[8]), (1, a.edge_x, a.edge_y, a.edge_w, a.edge_h))
        colours = [unpack_rgba(e[9]) for e in edges]
        self.assertEqual(colours[0], unpack_rgba(self.d.ILCT_RowsColourRgba(SAPPHIRE)), 'the row colour')
        self.assertEqual(colours[1], unpack_rgba(self.d.ILCT_RowsColourRgba(AMETHYST)))
        self.assertEqual(colours[2], unpack_rgba(self.d.ILCT_RowsColourRgba(COLOUR_NONE)), 'gambit silver')
        self.assertEqual(colours[3][3], 0x48, 'gambits off: dimmed')
        numerals = [c for c in cmds(f) if c[0] == 'text']
        self.assertEqual([(c[2], c[3], c[5]) for c in numerals[:4]],
                         [(self.anchor(REDUCED, k, 4).numeral_x, self.anchor(REDUCED, k, 4).numeral_y, 64)
                          for k in range(4)])
        self.assertEqual(self.stats().drawn_frames, 1)

    def test_stock_geometry_and_fewer_rows(self):
        self.d.ILCT_RowsPublish(ctypes.byref(record([(0, 0, GOLD, 0xFFFF), (2, 0, SAPPHIRE, 0xFFFF)], geometry=STOCK)))
        f = self.draw(party_snapshot([unit(0, seat=1), unit(2, seat=2)]))
        edges = sprites(f, ATLAS_PARTYTOP_4_C, 0, 6)
        self.assertEqual([(e[5], e[6]) for e in edges],
                         [(self.anchor(STOCK, k, 2).edge_x, self.anchor(STOCK, k, 2).edge_y) for k in range(2)])
        self.assertEqual(self.anchor(STOCK, 1, 2).top, 970, 'bottom-anchored')

    def test_nothing_without_the_record_a_known_geometry_or_the_switch(self):
        units, rows = self.four()
        snap = party_snapshot(units)
        self.assertEqual(self.draw(snap).count, 0)
        self.assertEqual(self.stats().no_record, 1)
        self.d.ILCT_RowsPublish(ctypes.byref(record(rows, geometry=NONE)))
        self.assertEqual(self.draw(snap).count, 0)
        self.assertEqual(self.stats().no_geometry, 1)
        for rec in (record(rows, now=NOW - 251), record(rows, now=NOW + 251)):
            self.d.ILCT_RowsPublish(ctypes.byref(rec))
            self.assertEqual(self.draw(snap).count, 0, 'stale record')
        other = record(rows)
        other.epoch = EPOCH + 1
        self.d.ILCT_RowsPublish(ctypes.byref(other))
        self.assertEqual(self.draw(snap).count, 0, 'another epoch')
        self.d.ILCT_RowsPublish(ctypes.byref(record(rows, enabled=0)))
        self.assertEqual(self.draw(snap).count, 0, 'the element switch as sampled')
        self.d.ILCT_RowsPublish(ctypes.byref(record(rows)))
        self.d.ILCT_RowsSetEnabled(0)
        self.assertEqual(self.draw(snap).count, 0, 'the element switch now')
        self.d.ILCT_RowsSetEnabled(1)
        self.assertGreater(self.draw(snap).count, 0, 'instrument')
        no_party = party_snapshot(units)
        no_party.flags = hd.SNAP_WORLD
        self.assertEqual(self.draw(no_party).count, 0, 'no coherent party')

    def test_the_damage_ghost_on_the_hp_gauge(self):
        rows = [(0, 0, SAPPHIRE, 0xFFFF)]
        a = self.anchor(REDUCED, 0, 1)
        for t in (NOW, NOW + 100, NOW + 1400, NOW + 1800):
            self.d.ILCT_RowsPublish(ctypes.byref(record(rows, now=t)))
            f = self.draw(party_snapshot([unit(0, seat=1, hp=1000 if t == NOW else 600)], now=t), now=t)
            ghosts = sprites(f, ATLAS_PARTYTOP_4_C, 0, 5)
            if t == NOW or t == NOW + 1800:
                self.assertEqual(ghosts, [], t)
            else:
                g = ghosts[0]
                x0 = a.gauge_x + a.gauge_w * 600 // 1000
                self.assertEqual((g[4], g[5], g[6], g[8]), (1, x0, a.gauge_y, a.gauge_h))
                if t == NOW + 100:
                    self.assertEqual(g[7], a.gauge_x + a.gauge_w - x0, 'held: up to the HP before the hit')
                self.assertEqual(unpack_rgba(g[9]), (0x7A, 0x6A, 0x46, 0x80), 'pale gold')

    def test_statuses_with_time_left(self):
        u = unit(0, seat=1)
        for sid, ticks in ((POISON, 900), (PROTECT, 3000), (HASTE, 1200)):
            u.duration[sid] = ticks
        self.d.ILCT_RowsPublish(ctypes.byref(record([(0, 0, SAPPHIRE, 0xFFFF)])))
        f = self.draw(party_snapshot([u], tick_scale=1))
        a = self.anchor(REDUCED, 0, 1)
        icons = sprites(f, ATLAS_PARTYTOP_4_C, 3, 0)
        self.assertEqual([(i[4], i[5], i[6], i[7], i[8]) for i in icons],
                         [(HASTE, a.status_x, a.status_y, a.status_w, a.status_h),
                          (PROTECT, a.status_x + a.status_stride, a.status_y, a.status_w, a.status_h)],
                         'the native status cells (cell = status bit), two at most, beneficial first')
        self.assertEqual(unpack_rgba(icons[0][9])[3], 0x48, 'Haste (4 s): dimmed in its last 5 s')
        self.assertEqual(unpack_rgba(icons[1][9])[3], 0x80)
        timers = [c for c in cmds(f) if c[0] == 'text'][1:]
        self.assertEqual([(c[1], c[2], c[3], c[5]) for c in timers],
                         [(th.encoded('4'), a.status_x, a.status_y + a.timer_dy, a.timer_scale),
                          (th.encoded('10'), a.status_x + a.status_stride, a.status_y + a.timer_dy, a.timer_scale)])
        # unknown tick scale: icons without times
        f = self.draw(party_snapshot([u], tick_scale=0))
        self.assertEqual((len(sprites(f, ATLAS_PARTYTOP_4_C, 3, 0)), texts(f)), (2, [b'\x86']))

    def test_the_action_line(self):
        a = self.anchor(REDUCED, 0, 2)
        units = [unit(0, seat=1), unit(2, seat=2)]
        self.d.ILCT_RowsPublish(ctypes.byref(record([(0, EXTRA_CHOOSING, GOLD, 0xFFFF),
                                                     (2, EXTRA_CHOOSING | EXTRA_CHOOSING_TARGET, SAPPHIRE, 0xFFFF)])))
        f = self.draw(party_snapshot(units))
        lines = [c for c in cmds(f) if c[0] == 'text' and len(c[1]) > 2]
        self.assertEqual([(c[1], c[2], c[3], c[5]) for c in lines],
                         [(th.encoded('Choosing...'), a.label_x, a.label_y, a.label_scale),
                          (th.encoded('Choosing target...'), a.label_x, self.anchor(REDUCED, 1, 2).label_y,
                           a.label_scale)])
        # performing an action: no "Choosing", the target's name after the native gauge (a party target only)
        units = [unit(0, seat=1, target=0x100002), unit(2, seat=2, target=0x200010), unit(1, target=0x100001)]
        self.d.ILCT_RowsPublish(ctypes.byref(record([(0, EXTRA_CHOOSING | EXTRA_ACTING, GOLD, 0x10),
                                                     (2, EXTRA_ACTING, SAPPHIRE, 0x20),
                                                     (1, EXTRA_ACTING | EXTRA_GAMBITS_ON | EXTRA_READ, COLOUR_NONE,
                                                      0x30)])))
        f = self.draw(party_snapshot(units))
        a = self.anchor(REDUCED, 0, 3)
        lines = [c for c in cmds(f) if c[0] == 'text' and len(c[1]) > 2]
        self.assertEqual([(c[1], c[2], c[3], c[5]) for c in lines],
                         [(th.encoded('- Fran'), a.suffix_x, a.suffix_y, a.suffix_scale)],
                         'a foe target or a self target adds nothing')
        # not performing (idle actor, stale target): nothing
        self.d.ILCT_RowsPublish(ctypes.byref(record([(0, 0, GOLD, 0xFFFF)])))
        f = self.draw(party_snapshot([unit(0, seat=1, target=0x100002), unit(2, seat=2)]))
        self.assertEqual([t for t in texts(f) if len(t) > 2], [])

    def test_party_target_cursors_per_seat(self):
        units = [unit(0, seat=1), unit(2, seat=2), unit(1)]
        snap = party_snapshot(units)
        snap.seats[1].handle, snap.seats[1].on_page = 0x100001, 1    # P2 targets Ashe (row 2)
        snap.seats[2].handle, snap.seats[2].on_page = 0x100001, 1    # P3 too: a second hand on that row
        snap.seats[3].handle, snap.seats[3].on_page = 0x100000, 0    # P4's last target, page closed: no hand
        snap.seats[0].handle, snap.seats[0].on_page = 0x200099, 1    # P1 on a foe: not a party row
        self.d.ILCT_RowsPublish(ctypes.byref(record([(0, 0, GOLD, 0xFFFF), (2, 0, SAPPHIRE, 0xFFFF)],
                                                    seat_colours=(GOLD, SAPPHIRE, EMERALD, RUBY), now=NOW + 500)))
        self.assertEqual((NOW + 500) % 600, 300)
        f = self.draw(snap, now=NOW + 500)   # the bob's top (+4)
        hands = sprites(f, ATLAS_CURSOL_4_P, 0, 0)
        a = self.anchor(REDUCED, 2, 3)
        self.assertEqual([(h[4], h[5], h[6], h[7], h[8], unpack_rgba(h[9])) for h in hands],
                         [(0, a.cursor_x + 4, a.cursor_y, a.cursor_w, a.cursor_h,
                           unpack_rgba(self.d.ILCT_RowsColourRgba(SAPPHIRE))),
                          (0, a.cursor_x + 4 - a.cursor_w // 3, a.cursor_y, a.cursor_w, a.cursor_h,
                           unpack_rgba(self.d.ILCT_RowsColourRgba(EMERALD)))])

    def test_an_esper_row_gets_nothing_and_the_cap_holds(self):
        self.d.ILCT_RowsPublish(ctypes.byref(record([(30, 0, COLOUR_NONE, 0xFFFF)])))
        self.assertEqual(self.draw(party_snapshot([unit(30)])).count, 0)
        units = []
        for k, member in enumerate((0, 1, 2, 3)):
            u = unit(member, seat=k + 1, target=0x100000 | ((member + 1) % 4))
            for sid in (PROTECT, SHELL, HASTE, POISON):
                u.duration[sid] = 3000
            units.append(u)
        snap = party_snapshot(units)
        for s in range(4):
            snap.seats[s].handle, snap.seats[s].on_page = 0x100000, 1
        self.d.ILCT_RowsPublish(ctypes.byref(record([(m, EXTRA_ACTING, k, 0x10) for k, m in enumerate((0, 1, 2, 3))])))
        f = self.draw(snap)
        self.assertLessEqual(f.count, PASS_MAX)
        self.assertEqual(self.stats().capped, 0, 'a full four-seat frame fits the pass cap')


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class ComposeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(hd.declare(cls.hw.d))

    def setUp(self):
        self.hw.setup()
        self.d.ILCT_HudSetTestDraw(0)
        self.d.ILCT_RowsReset()

    def tearDown(self):
        self.d.ILCT_RowsReset()
        self.d.ILCT_HudDrvReset()
        self.hw.twin.reset()

    def test_the_party_pass_draws_first_in_the_composed_frame(self):
        snap = party_snapshot([unit(0, seat=1), unit(2, seat=2)])
        self.d.ILCT_HudSnapPublish(ctypes.byref(snap))
        self.d.ILCT_RowsPublish(ctypes.byref(record([(0, 0, GOLD, 0xFFFF), (2, 0, SAPPHIRE, 0xFFFF)])))
        out = th.Frame()
        self.assertEqual(self.d.ILCT_HudDrvCompose(EPOCH, NOW, None, ctypes.byref(out)), hd.COMPOSE_OK)
        alone = th.Frame()
        n = self.d.ILCT_RowsDraw(ctypes.byref(snap), NOW, ctypes.byref(alone))
        self.assertGreater(n, 0)
        self.assertEqual(cmds(out)[:n], cmds(alone), 'the party extras are the first commands of the frame')


# ---- the co-op thread's record ------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class RecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(hd.declare(cls.hw.d))

    def setUp(self):
        d = self.d
        self.hw.setup(attach=False)
        d.ILCT_HudDrvReset()
        d.ILCT_CtlReset()
        d.ILCT_RowsReset()
        self.img = Image()
        self.gw = GameWorld(self.img)
        self.gw.layout([0, 1, 2], [2, 0, 1])
        self.gw.leader(0)
        for slot, member in enumerate([0, 1, 2]):
            a = self.gw.add_actor(slot, slot, position=(10.0 + slot, 2.0, 30.0))
            self.img.p64(a.actor + 0x698, self.gw.battle + hd.RECORDS + member * hd.RECORD_SIZE)
            self.img.p32(self.gw.battle + hd.RECORDS + member * hd.RECORD_SIZE + hd.REC_HP_MAX, 1000)
            self.img.put(a.actor + 0x714, struct.pack('<H', 0x96 if member == 1 else 0xFFFF))  # member 1 attacks
        self.img.p8(self.gw.battle + hd.RECORDS + 1 * hd.RECORD_SIZE, 0x04)   # member 1: gambits on
        d.ILCT_HudSnapSetImageBase(self.img.base)
        d.ILCT_RowsSetImageBase(self.img.base)
        d.ILCT_HudSnapUseSeatTargets(1)
        rec = Record()
        rec.head.epoch, rec.head.generation, rec.head.flags = EPOCH, gt.GEN, GATE['WORLD']
        rec.facts, rec.phase, rec.leader = FACT['CONTROL'], PHASE['FIELD'], 0
        for s in range(4):
            rec.seat[s].member, rec.seat[s].party_slot = 0xFFFF, 0xFF
        for s, member in ((0, 0), (1, 2)):
            e = rec.seat[s]
            e.flags = SEATF['IN_SESSION'] | SEATF['MANUAL'] | SEATF['ACTOR']
            e.member, e.handle, e.party_slot = member, 0x100000 | member, member
        self.rec = rec
        d.ILCT_TownSessionPublish(ctypes.byref(rec))

    def tearDown(self):
        d = self.d
        d.ILCT_RowsReset()
        d.ILCT_HudDrvReset()
        d.ILCT_TownSessionWithdraw()
        d.ILCT_CtlReset()
        self.hw.twin.reset()
        self.img.close()

    def step(self, state=L_REDUCED) -> RowsRecord:
        self.d.ILCT_HudSnapStep(NOW)
        self.d.ILCT_RowsForceLayoutState(state)
        self.d.ILCT_RowsStep(NOW)
        r = RowsRecord()
        self.assertEqual(self.d.ILCT_RowsRead(ctypes.byref(r)), 1)
        return r

    def test_rows_by_member_with_gambits_and_colours(self):
        r = self.step()
        self.assertEqual((r.epoch, r.sampled_ms, r.geometry, r.enabled), (EPOCH, NOW, REDUCED, 1))
        members = [r.rows[k].member for k in range(4)]
        self.assertEqual(members, [2, 0, 1, 0xFFFF], 'the HUD order of the snapshot')
        by = {r.rows[k].member: r.rows[k] for k in range(3)}
        self.assertEqual(by[1].flags & (EXTRA_READ | EXTRA_GAMBITS_ON), EXTRA_READ | EXTRA_GAMBITS_ON)
        self.assertEqual(by[0].flags & EXTRA_GAMBITS_ON, 0)
        self.assertEqual((by[0].colour, by[2].colour, by[1].colour), (SAPPHIRE, AMETHYST, COLOUR_NONE),
                         'default colour mode: by character (Vaan sapphire, Fran amethyst); unseated: none')
        self.assertEqual(list(r.seat_colour), [SAPPHIRE, AMETHYST, EMERALD, AMETHYST],
                         "by character for a seated regular, else the seat's own colour")
        self.assertEqual([by[m].action for m in (0, 1, 2)], [0xFFFF, 0x96, 0xFFFF])
        self.assertEqual([by[m].flags & EXTRA_ACTING for m in (0, 1, 2)], [0, EXTRA_ACTING, 0])

    def test_geometry_from_relayout(self):
        self.assertEqual(self.step(L_STOCK).geometry, STOCK)
        self.assertEqual(self.step(L_FOREIGN).geometry, NONE)
        s = RowsStatus()
        self.d.ILCT_RowsStatus(ctypes.byref(s))
        self.assertEqual(s.geometry, NONE)

    def test_withdrawn_without_a_world_or_with_co_op_off(self):
        self.step()
        self.d.ILCT_TownSessionWithdraw()
        self.d.ILCT_RowsStep(NOW + 20)
        self.assertEqual(self.d.ILCT_RowsRead(ctypes.byref(RowsRecord())), 0)
        self.d.ILCT_TownSessionPublish(ctypes.byref(self.rec))
        self.step()
        self.d.ILCT_CtlKill(1)
        self.d.ILCT_CtlStep(NOW + 40)
        self.d.ILCT_RowsStep(NOW + 40)
        self.assertEqual(self.d.ILCT_RowsRead(ctypes.byref(RowsRecord())), 0)


# ---- the HP colour handlers --------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HpColourTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)
        cls.row = ctypes.create_string_buffer(0x200)
        cls.row2 = ctypes.create_string_buffer(0x200)
        cls.rec = ctypes.create_string_buffer(0x100)

    def setUp(self):
        self.hw.setup()
        self.d.ILCT_RowsReset()
        self.d.ILCT_RowsSetAttached(1)
        struct.pack_into('<I', self.rec, RECORD_MAX, 1000)

    def tearDown(self):
        self.d.ILCT_RowsReset()
        self.d.ILCT_RowsSetAttached(0)
        self.hw.twin.reset()

    def cache(self, row=None) -> int:
        return struct.unpack_from('<I', row or self.row, ROW_CACHE)[0]

    def hp(self, hp, status=0, cache=None, row=None, rbx=None) -> int:
        row = row or self.row
        if cache is not None:
            struct.pack_into('<I', row, ROW_CACHE, cache)
        c = th.HookCtx()
        c.rcx, c.rdx, c.r8 = ctypes.addressof(row), hp, status
        c.rbx = ctypes.addressof(self.rec) if rbx is None else rbx
        c.index = self.d.ILCT_RowsRow(0)
        return self.d.ILCT_RowsHpCall(ctypes.byref(c), EPOCH)

    def digits(self, row=None, colour=WHITE) -> int:
        c = th.HookCtx()
        c.rcx, c.rdx, c.r9 = 0x1234, 3, 0xDEAD00000000 | colour
        c.rdi = ctypes.addressof(row or self.row)
        c.index = self.d.ILCT_RowsRow(1)
        self.assertEqual(self.d.ILCT_RowsDigitsCall(ctypes.byref(c), EPOCH), RUN_ORIGINAL)
        self.assertEqual(c.r9 >> 32, 0xDEAD, 'only r9d changes')
        return c.r9 & 0xFFFFFFFF

    def after(self):
        self.d.ILCT_RowsHpAfter(ctypes.byref(th.HookCtx()), EPOCH)

    def stats(self) -> RowsCounters:
        c = RowsCounters()
        self.d.ILCT_RowsStats(ctypes.byref(c))
        return c

    def test_rows_and_attach(self):
        self.assertEqual([self.d.ILCT_RowsRow(k) for k in (0, 1)],
                         [k for k, r in enumerate(pe.site_rows()) if r['id'] in ('HD-02', 'HD-03')])
        self.assertEqual(self.d.ILCT_RowsAttach(TWIN_BASE), 1)
        s = RowsStatus()
        self.d.ILCT_RowsStatus(ctypes.byref(s))
        self.assertEqual((s.attached, s.enabled, s.hp_rows_hooked), (1, 1, 1))

    def test_a_low_hp_row_glows_through_the_colour_argument(self):
        self.assertEqual(self.hp(150, cache=150), CALL_AROUND)
        self.assertEqual(self.cache(), 0xFFFFFFFF, 'the digits are set again this frame')
        row, colour = u64(), u32()
        self.assertEqual(self.d.ILCT_RowsGlowPending(ctypes.byref(row), ctypes.byref(colour)), 1)
        self.assertEqual((row.value, colour.value), (ctypes.addressof(self.row), self.d.ILCT_RowsGlow(gt.NOW)))
        self.assertEqual(self.digits(), self.d.ILCT_RowsGlow(gt.NOW), 'the glow replaces the colour argument')
        self.after()
        self.assertEqual(self.cache(), 150, 'the cache is back: the HP gauge update sees it unchanged')
        self.assertEqual(self.d.ILCT_RowsGlowPending(None, None), 0)
        self.assertEqual((self.stats().forced, self.stats().glowed, self.d.ILCT_RowsColoured()), (1, 1, 1))

    def test_the_glow_is_for_that_row_only(self):
        self.assertEqual(self.hp(150, cache=150), CALL_AROUND)
        self.assertEqual(self.digits(row=self.row2), WHITE, 'another row keeps its colour')
        self.assertEqual(self.digits(), WHITE, 'consumed: one digit call per arming')
        self.after()

    def test_hp_critical_and_bubble_follow_the_rule(self):
        self.assertEqual(self.hp(900, status=HP_CRITICAL, cache=900), CALL_AROUND)
        self.after()
        self.assertEqual(self.hp(300, status=BUBBLE, cache=300), CALL_AROUND, 'below 2000 / 5 under Bubble')
        self.after()

    def test_a_row_that_is_not_low_is_stock(self):
        self.assertEqual(self.hp(800, cache=800), RUN_ORIGINAL)
        self.assertEqual(self.cache(), 800)
        self.assertEqual(self.digits(), WHITE)
        self.assertEqual(self.stats().forced, 0)

    def test_recovery_restores_the_native_colour_once(self):
        self.hp(150, cache=150)
        self.digits()
        self.after()
        self.assertEqual(self.hp(800, cache=800), CALL_AROUND, 'forced once more with the native colour')
        self.assertEqual(self.d.ILCT_RowsGlowPending(None, None), 0)
        self.assertEqual(self.digits(), WHITE)
        self.after()
        self.assertEqual((self.cache(), self.d.ILCT_RowsColoured()), (800, 0))
        self.assertEqual(self.hp(800, cache=800), RUN_ORIGINAL, 'then left alone')
        self.assertEqual(self.hp(0, cache=0), RUN_ORIGINAL, 'KO: not ours')

    def test_the_switch_off_restores_then_stays_stock(self):
        self.hp(150, cache=150)
        self.digits()
        self.after()
        self.d.ILCT_RowsSetEnabled(0)
        self.assertEqual(self.hp(150, cache=150), CALL_AROUND, 'the coloured row gets its native colour back')
        self.assertEqual(self.digits(), WHITE)
        self.after()
        self.assertEqual(self.hp(150, cache=150), RUN_ORIGINAL)

    def test_render_off_unattached_or_unreadable_is_stock(self):
        cases = {'render': lambda: self.d.ILCT_PatchApplySubsystems(0xFF & ~th.RENDER),
                 'attach': lambda: self.d.ILCT_RowsSetAttached(0),
                 'record': None}
        for name, prepare in cases.items():
            with self.subTest(name):
                self.setUp()
                if prepare:
                    prepare()
                with quiet_faulthandler():
                    r = self.hp(150, cache=150, rbx=8 if name == 'record' else None)
                self.assertEqual((r, self.cache()), (RUN_ORIGINAL, 150))
                self.assertEqual(self.digits(), WHITE)
                self.assertGreaterEqual(self.stats().hp_declined, 1 if name != 'record' else 0)

    def test_a_cache_changed_during_the_call_is_left_alone(self):
        self.hp(150, cache=150)
        struct.pack_into('<I', self.row, ROW_CACHE, 140)   # the game stored something else meanwhile
        self.after()
        self.assertEqual(self.cache(), 140)


# ---- the patch layer on the twin ---------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class GateTwinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.twin = cls.hw.twin
        cls.d = declare(cls.hw.d)
        cls.rows = {r['id']: k for k, r in enumerate(pe.site_rows())}

    def tearDown(self):
        self.twin.reset()

    def hooked(self, sid) -> bool:
        return bool(self.twin.info(self.rows[sid]).trampoline)

    def test_all_stock_hooks_both(self):
        self.twin.reset()
        report = self.twin.call('ILCT_PatchPreflight')
        self.assertEqual((report.result, report.gated_off), (OK, 0))
        self.assertEqual(self.twin.call('ILCT_PatchInstall').result, OK)
        self.assertTrue(self.hooked('HD-02') and self.hooked('HD-03'))
        for va in (HP_CALL, DIGITS_CALL):
            self.assertEqual(ctypes.string_at(TWIN_BASE + va - 0x120000, 1), b'\xE8', 'the call stays a call')

    def test_a_gate_not_stock_skips_only_its_row(self):
        for sid, (gated, va, expected) in HP_GATES.items():
            with self.subTest(sid):
                self.twin.reset()
                at = va + len(bytes.fromhex(expected)) - 1
                self.twin.poke(at, bytes([ctypes.string_at(TWIN_BASE + at - 0x120000, 1)[0] ^ 0xFF]))
                report = self.twin.call('ILCT_PatchPreflight')
                self.assertEqual((report.result, report.gated_off), (OK, 1))
                self.assertEqual(self.twin.call('ILCT_PatchInstall').result, OK)
                other = 'HD-03' if gated == 'HD-02' else 'HD-02'
                self.assertFalse(self.hooked(gated))
                self.assertTrue(self.hooked(other) and self.hooked('HD-01'), 'the rest installs')

    def test_a_rewritten_call_is_skipped_not_refused(self):
        for sid, (va, expected, _) in HP_ROWS.items():
            with self.subTest(sid):
                self.twin.reset()
                self.twin.poke(va, b'\x90' * 5)
                report = self.twin.call('ILCT_PatchPreflight')
                self.assertEqual((report.result, report.gated_off), (OK, 1))
                self.assertEqual(self.twin.call('ILCT_PatchInstall').result, OK)
                self.assertFalse(self.hooked(sid))
                self.assertEqual(ctypes.string_at(TWIN_BASE + va - 0x120000, 5), b'\x90' * 5, 'their bytes stay')


# ---- unicorn on the hooked twin -----------------------------------------------------------------------------------------
SCRATCH = pe.SCRATCH
ROW, REC = SCRATCH + 0x70000, SCRATCH + 0x71000


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class NativeHpColourTest(unittest.TestCase):
    """The party row update's own code from its HP digits call (0x29a544) in unicorn on the hooked twin: through
    HD-02's island and handler into the HP digits function, through HD-03's island to the digit setter (where the run
    records the colour argument and returns), back to the row update after the call."""

    @classmethod
    def setUpClass(cls):
        import test_coop_movement as mv
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)
        cls.hw.setup()
        cls.d.ILCT_RowsReset()
        assert cls.d.ILCT_RowsAttach(TWIN_BASE) == 1   # the handlers on HD-02 and HD-03
        cls.emu = mv.MoveEmulator(cls.hw.twin)
        cls.rows = {r['id']: k for k, r in enumerate(pe.site_rows())}
        cls.update_end = 0x29A8E0   # the next function (.pdata)

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_RowsReset()
        cls.d.ILCT_RowsSetAttached(0)
        cls.hw.twin.reset()

    def allowed(self, x: int) -> bool:
        if not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE):
            return True  # our module, the islands
        if x == DIGIT_SETTER:
            return False
        if ROW_UPDATE <= x < self.update_end:
            return HP_ARGS <= x < AFTER_HP_CALL
        return HP_DIGITS <= x < HP_DIGITS + 0xFB   # the HP digits function only

    def record(self, uc, address):
        X = pe.X
        if address == DIGIT_SETTER:
            self.seen.append((uc.reg_read(X.UC_X86_REG_R9) & 0xFFFFFFFF, uc.reg_read(X.UC_X86_REG_RDX) & 0xFFFFFFFF))

    def run_row(self, hp, hp_max=1000, status=0, cache=None, hooked=True) -> int:
        emu, uc = self.emu, self.emu.uc
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        uc.mem_write(ROW, bytes(0x200))
        uc.mem_write(ROW + ROW_CACHE, struct.pack('<I', hp if cache is None else cache))
        uc.mem_write(ROW + ROW_DIGITS, b'\x02')
        uc.mem_write(REC, bytes(0x100))
        uc.mem_write(REC + RECORD_MAX, struct.pack('<I', hp_max))
        uc.mem_write(REC + RECORD_STATUS, struct.pack('<I', status))
        for sid in ('HD-02', 'HD-03'):
            emu.force_gate(self.rows[sid], hooked)
        self.seen = []
        state = pe.random_state(random.Random(0x4646))
        state['gpr'].update(RBX=REC, RBP=ROW, R15=hp)
        state['gpr']['RSP'] &= ~0xF
        try:
            out = emu.run(HP_ARGS, self.allowed, state, on_callee=self.record,
                          probe=lambda u: struct.unpack('<I', bytes(u.mem_read(ROW + ROW_CACHE, 4)))[0])
        finally:
            for sid in ('HD-02', 'HD-03'):
                emu.force_gate(self.rows[sid], False)
        self.assertEqual(emu.unexpected, [], 'imports the emulator does not answer')
        self.assertEqual(out['exit'], AFTER_HP_CALL, 'back in the row update after the call')
        self.out = out
        return out['probe']

    def test_a_low_hp_row_reaches_the_digit_setter_with_the_glow(self):
        cache = self.run_row(150)
        self.assertEqual(self.seen, [(self.d.ILCT_RowsGlow(gt.NOW + 5), 3)], 'the game sets 3 digits in our colour')
        self.assertEqual(cache, 150, 'the cache came back')
        self.assertEqual(self.out['gpr']['RAX'] & 0xFFFFFFFF, 1, 'the HP digits function reports it drew')

    def test_a_row_that_is_not_low_early_exits_as_stock(self):
        cache = self.run_row(800)
        self.assertEqual((self.seen, cache), ([], 800))
        self.assertEqual(self.out['gpr']['RAX'] & 0xFFFFFFFF, 0)

    def test_disarmed_is_the_stock_path(self):
        self.assertEqual((self.run_row(150, hooked=False), self.seen), (150, []), 'stock: the cache matches, no draw')
        self.run_row(150, cache=999, hooked=False)
        self.assertEqual(self.seen, [(WHITE, 3)], 'stock colour when the HP changed')
        hooked = self.run_row(800, cache=999)
        self.assertEqual((self.seen, hooked), ([(WHITE, 3)], 999), 'not low: the same as stock')


if __name__ == '__main__':
    unittest.main()
