"""Host tests for the co-op HUD canvas and its frame hook (native/coop/src/hud, src/runtime/guard). No game run,
nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the image twin the patch layer installs on.

* the site rows: HD-01 is an OPTIONAL RENDER call row at the combat-log manager's draw call (its callee is the log
  draw 0x2bf230), HD-V1..HD-V8 are GATE rows on it pinning that callee and every native draw function the replay
  calls; the replay's own pins are those rows; every address constant in the sources is tagged;
* the patch layer: several GATE rows may gate one row (any mismatch skips it, the last look compares what they
  decide together), and an OPTIONAL row that another mod rewrote (Vision's No Combat Log NOPs this call) is skipped
  instead of refusing the install;
* the canvas: encoding for the game's font, the builder's limits, the seqlock publication, the ring estimate, and the
  worst-case four-seat frame against the budget;
* the hook, called directly: every reason it stays stock (switch off, RENDER off, not attached, no/torn/stale/other
  epoch frame, context not the window's or faded out, UI not ready, ring unreadable or over a limit), and the fault
  path (a fault in the replay is caught, RENDER latched, stock after);
* the guard: faults in the called code resume at the guard with every non-volatile register restored; one call at a
  time;
* unicorn on the hooked twin: (1) the canvas's text gives the native text renderer exactly the record the stock log
  draw gives it for the same string, position and colour, while the stock line itself is unchanged; (2) a canvas
  sprite gives the batched sprite draw the texture, UV, size, tint and CLUT the native MRP node draw 0x2435c0 gives
  for the same node; (3) the bar reaches the selection bar with the seat RGBA; the gate closed, the switch off or the
  frame over budget is the stock log draw alone;
* the Diagnostics switch: off by default and until the player's settings say otherwise, in the schema, the Lua store
  and the Mod Menu.
"""
from __future__ import annotations

import ctypes
import json
import random
import struct
import unittest
from pathlib import Path

import test_coop_game_thread as gt
import test_coop_patch_engine as pe
from coop_native_support import native_test, quiet_faulthandler
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, checked, untagged_constants

try:
    from lupa import LuaRuntime
except ImportError:                                  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'
LUA = ROOT / 'package' / 'x64' / 'scripts' / 'IvaliceLegacy' / 'coop'

u8, u16, u32, u64, i32, f32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32, ctypes.c_float
P = ctypes.POINTER

# ---- game facts (VAs at the pinned base 0x120000) ---------------------------------------------------------------------
LOG_HANDLER = 0x2BE4F0          # the combat-log manager's window handler
LOG_DRAW_MSG = 0x2BE7D3         # its message 4: mov rdx,[rbx+0x38]; mov rcx,rbx; call 0x2bf230
H0_SITE = 0x2BE7DA
AFTER_CALL = 0x2BE7DF
LOG_DRAW = 0x2BF230
TEXT_DRAW, TEXT_PARAMS, TEXT_RENDER, TEXT_RENDER_EX = 0x2D8650, 0x2B3400, 0x2B0280, 0x3649A0
BATCH_BEGIN, BATCH_SPRITE, BATCH_END = 0x2D9C40, 0x2D9380, 0x2D9B00
SELECTION_BAR, TEXTURE_BY_NAME, NODE_DRAW = 0x242C90, 0x24A5A0, 0x2435C0
SCISSOR = 0x2D8530              # the scissor setter (HD-V12): the replay lifts the log window's clip and sets it back
REGISTRY, UI_READY, RENDER_ARENA = 0x208F580, 0x215E6D0, 0x1F7F4C0
IMPORT_THUNKS = range(0x861E90, 0x862088, 6)  # jmp [iat] thunks: the twin's IAT is not bound, never run them
MEMSET_THUNK = 0x861EC0
HD_ROWS = {'HD-V1': (LOG_DRAW, '40 55 41 54 41 56 41 57 48 81 ec 28 01 00 00 48'),
           'HD-V2': (TEXT_DRAW, '66 41 0f 6e d0 66 0f 6e ca 0f 5b d2 0f 5b c9 e9 2c 00 00 00'),
           'HD-V3': (TEXT_PARAMS, '48 89 5c 24 08 48 89 74 24 10 57 48 83 ec 20 49'),
           'HD-V4': (BATCH_BEGIN, '40 53 48 83 ec 20 83 3d 83 4a e8 01 00'),
           'HD-V5': (BATCH_SPRITE, '48 8b c4 48 89 58 20 55 56 57 41 54 41 55 41 56'),
           'HD-V6': (BATCH_END, '48 89 5c 24 10 57 48 83 ec 20 4c 8b 49 08 48 8b'),
           'HD-V7': (SELECTION_BAR, '40 55 53 56 41 56 41 57 48 8d 6c 24 d9 48 81 ec'),
           'HD-V8': (TEXTURE_BY_NAME, '4c 8b d9 4c 8d 0d d6 4f e4 01 45 33 d2 0f 1f 00'),
           'HD-V12': (SCISSOR, 'f7 41 5c 00 00 02 00 48 8d 49 48 b8 00 00 00 00')}
RENDER = 0x40
SITE_MISMATCH = 7
SITE_CHANGED = 28
CMD_TEXT, CMD_SPRITE, CMD_BAR = 1, 2, 3
ATLAS_PARTYTOP_4_C = 1
MAX_COMMANDS, TEXT_POOL = 192, 3072
RING_OURS_MAX, RING_SAFE, RING, RING_GAP_MS = 0x80000, 0x800000, 0x2000000, 100
RING_SLOW_MS = 500   # canvas.h: a steady cadence up to this long is still one frame per call
WHITE = 0x80808080
FAULT_RETURNED, FAULT_FAULTED, FAULT_BUSY = 0, 1, 2

# The st2e character table (game fact; tools/ff12_hdat.py CHARS).
CHARS = {' ': 4, '!': 171, '"': 173, '%': 158, '&': 160, "'": 172, '(': 174, ')': 175, '+': 166, ',': 167, '-': 165,
         '.': 168, '/': 162, ':': 169, ';': 170, '?': 154,
         **{chr(ord('A') + k): 32 + k for k in range(26)}, **{chr(ord('a') + k): 58 + k for k in range(26)},
         **{str(k): 0x85 + k for k in range(10)}}


def encoded(text: str) -> bytes:
    return bytes(CHARS.get(c, 154) for c in text)


class Cmd(ctypes.Structure):
    _fields_ = [('kind', u8), ('atlas', u8), ('font', u8), ('page', u8), ('sheet', u8), ('reserved', u8),
                ('cell', u16), ('x', ctypes.c_int16), ('y', ctypes.c_int16), ('w', u16), ('h', u16), ('scale', u16),
                ('text', u16), ('colour', u32)]


class Frame(ctypes.Structure):
    _fields_ = [('epoch', u32), ('flags', u32), ('generation', u64), ('built_ms', u64), ('count', u32),
                ('text_used', u32), ('cmds', Cmd * MAX_COMMANDS), ('text', ctypes.c_char * TEXT_POOL)]


COUNTERS = ('reached', 'drawn', 'commands', 'texts', 'sprites', 'bars', 'declined_off', 'declined_frame',
            'declined_context', 'declined_budget', 'skipped_commands', 'faults', 'published', 'ring_gaps',
            'hook_ticks_last', 'hook_ticks_max', 'hook_ticks_total', 'hook_calls', 'atlas_lookups', 'text_last',
            'chars_last', 'ring_ours_last', 'zero_output', 'child_space', 'declined_matrix', 'clips')


HOOK_TIMING = {'hook_ticks_last', 'hook_ticks_max', 'hook_ticks_total', 'hook_calls'}   # move on every call


class Counters(ctypes.Structure):
    _fields_ = [(n, u64) for n in COUNTERS]


class Budget(ctypes.Structure):
    _fields_ = [('capacity', u64), ('frame_use', u64), ('peak', u64), ('ours', u64), ('cursor', u64),
                ('matrix', f32 * 16), ('depth', u32), ('source', u32)]


class Status(ctypes.Structure):
    _fields_ = [('attached', u8), ('pins_ok', u8), ('guard_ready', u8), ('test_draw', u8), ('row_hooked', u8),
                ('reserved', u8 * 3)]


class FaultView(ctypes.Structure):
    _fields_ = [('faults', u64), ('code', u32), ('reserved', u32), ('rip', u64)]


LAYOUTS = [ctypes.sizeof(Frame), ctypes.sizeof(Cmd), ctypes.sizeof(Counters), ctypes.sizeof(Budget),
           ctypes.sizeof(Status), MAX_COMMANDS, TEXT_POOL, ctypes.sizeof(FaultView)]

SIGNATURES = {
    'ILCT_HudSizes': ([P(u32), u32], u32), 'ILCT_HudReset': ([], None), 'ILCT_HudSetTestDraw': ([i32], None),
    'ILCT_HudTestDraw': ([], i32), 'ILCT_HudConfigure': ([ctypes.c_char_p], None), 'ILCT_HudAttach': ([u64], i32),
    'ILCT_HudSetImageBase': ([u64], None), 'ILCT_HudSetPinsOk': ([i32], None), 'ILCT_HudInjectFault': ([i32], None), 'ILCT_HudSetRing': ([u64, u64, u64], None),
    'ILCT_HudEncode': ([ctypes.c_char_p, ctypes.c_char_p, u32, P(u32)], i32), 'ILCT_HudBegin': ([u32, u64], None),
    'ILCT_HudText': ([i32, i32, ctypes.c_char_p, u32, u32, u32], i32),
    'ILCT_HudSprite': ([u32, u32, u32, u32, i32, i32, u32, u32, u32], i32),
    'ILCT_HudBar': ([i32, i32, u32, u32, u32], i32), 'ILCT_HudRefused': ([], u32), 'ILCT_HudFrame': ([P(Frame)], None),
    'ILCT_HudPublish': ([], None), 'ILCT_HudPublishFrame': ([P(Frame)], None), 'ILCT_HudWithdraw': ([], None),
    'ILCT_HudLatest': ([P(Frame)], i32), 'ILCT_HudRingEstimate': ([P(Frame)], u64),
    'ILCT_HudProduce': ([u32, u64], None), 'ILCT_HudStep': ([u64], None), 'ILCT_HudStop': ([], None),
    'ILCT_HudCall': ([ctypes.c_void_p, u32], i32), 'ILCT_HudAfter': ([ctypes.c_void_p, u32], None),
    'ILCT_HudStats': ([P(Counters)], None), 'ILCT_HudBudget': ([P(Budget)], None),
    'ILCT_HudStatus': ([P(Status)], None), 'ILCT_HudReplay': ([P(Frame), u64], i32), 'ILCT_HudRow': ([], i32),
    'ILCT_HudCountersAddress': ([], u64), 'ILCT_HudGuardAttach': ([], i32), 'ILCT_HudGuardReset': ([], None),
    'ILCT_HudGuardFault': ([P(FaultView)], None), 'ILCT_HudGuardTrace': ([], u64),
    'ILCT_HudGuardCall': ([u32, u64, P(i32)], i32), 'ILCT_HudGuardPreserves': ([P(i32)], u32),
}


def declare(d):
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def rgba(r, g, b, a=0x80) -> int:
    return r | g << 8 | b << 16 | a << 24


class HudWorld:
    """The patch layer installed on the twin (live, every subsystem armed), the HUD attached to it."""

    def __init__(self):
        self.w = gt.world()
        self.twin = self.w.twin
        self.d = declare(self.w.d)
        self.row = self.d.ILCT_HudRow()

    def setup(self, attach=True, reset_hud=True):
        d = self.d
        self.twin.reset()
        if reset_hud:
            d.ILCT_HudReset()
        report = self.twin.call('ILCT_PatchInstall')
        assert report.result == OK, report.result
        d.ILCT_SetFixedClock(gt.NOW)
        d.ILCT_PatchSetArmed(1)
        d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_PatchSetMode(gt.LIVE)
        d.ILCT_PatchHeartbeat(gt.EPOCH)
        if attach:
            assert d.ILCT_HudAttach(TWIN_BASE) == 1
            d.ILCT_HudSetTestDraw(1)

    def counters(self) -> Counters:
        c = Counters()
        self.d.ILCT_HudStats(ctypes.byref(c))
        return c

    def status(self) -> Status:
        s = Status()
        self.d.ILCT_HudStatus(ctypes.byref(s))
        return s

    def frame(self) -> Frame:
        f = Frame()
        self.d.ILCT_HudFrame(ctypes.byref(f))
        return f

    def publish(self, epoch=gt.EPOCH, built=gt.NOW, build=None):
        d = self.d
        d.ILCT_HudBegin(epoch, built)
        if build:
            build(d)
        else:
            d.ILCT_HudText(760, 150, b'P1 canvas', rgba(0x78, 0x5F, 0x19), 100, 0)
        d.ILCT_HudPublish()


_world = None


def world() -> HudWorld:
    global _world
    if _world is None:
        _world = HudWorld()
    return _world


# ---- static ---------------------------------------------------------------------------------------------------------
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the generated table is checked against the exe)')
class SiteRowsTest(unittest.TestCase):
    def test_the_frame_hook_row(self):
        rows = {r['id']: r for r in pe.site_rows()}
        r = rows['HD-01']
        self.assertEqual((r['kind'], r['policy'], r['subsystem']), ('CALL', 'PATCH', 'RENDER'))
        self.assertEqual(r['rva'], H0_SITE - 0x120000)
        self.assertEqual(checked(r), bytes.fromhex('e8 51 0a 00 00'))
        self.assertEqual(H0_SITE + 5 + struct.unpack('<i', r['expected'][1:5])[0], LOG_DRAW, 'the callee is the log draw')
        self.assertEqual(r['flags'], 0x08, 'OPTIONAL: a rewrite by another mod skips it, nothing else')
        self.assertEqual(pe.call_offset(r), 0, 'a call row (an island; may call game functions)')

    def test_the_gates_pin_every_native_function(self):
        rows = pe.site_rows()
        index = {r['id']: k for k, r in enumerate(rows)}
        for sid, (va, expected) in HD_ROWS.items():
            with self.subTest(sid):
                r = rows[index[sid]]
                self.assertEqual((r['kind'], r['policy'], r['subsystem']), ('VERIFY', 'GATE', 'RENDER'))
                self.assertEqual(r['gate'], str(index['HD-01']))
                self.assertEqual(r['rva'], va - 0x120000)
                self.assertEqual(r['expected'], bytes.fromhex(expected))
        legacy = {r['id']: r for r in json.loads((COOP / 'tools' / 'sites_legacy.json').read_text(encoding='utf-8'))['sites']}
        for sid in ('HD-01', *HD_ROWS):
            self.assertEqual(legacy[sid]['signature']['hits_in_text'], 1, sid)
            self.assertEqual(legacy[sid]['group'], 'hud', sid)

    def test_the_replay_pins_are_the_rows(self):
        text = (HUD_SRC / 'replay.cpp').read_text(encoding='utf-8')
        for sid in HD_ROWS:
            self.assertIn(f'"{sid}"', text)
        self.assertIn('static_assert(pins_match_rows()', text)
        facts = (HUD_SRC / 'hud_facts.h').read_text(encoding='utf-8')
        for name, va in (('FN_LOG_DRAW', LOG_DRAW), ('FN_TEXT_DRAW', TEXT_DRAW), ('FN_TEXT_PARAMS', TEXT_PARAMS),
                         ('FN_BATCH_BEGIN', BATCH_BEGIN), ('FN_BATCH_SPRITE', BATCH_SPRITE),
                         ('FN_BATCH_END', BATCH_END), ('FN_SELECTION_BAR', SELECTION_BAR),
                         ('FN_TEXTURE_BY_NAME', TEXTURE_BY_NAME), ('UI_READY', UI_READY),
                         ('RENDER_ARENA', RENDER_ARENA)):
            self.assertRegex(facts, rf'constexpr uint64_t {name} = 0x{va:X};', name)

    def test_no_untagged_address_constant(self):
        bad = []
        for path in [*sorted(HUD_SRC.glob('*.[ch]*')), COOP / 'src' / 'runtime' / 'guard.cpp',
                     COOP / 'src' / 'runtime' / 'guard.h']:
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                bad += [f'{path.name}:{number}: {c}' for c in untagged_constants(line)]
        self.assertEqual(bad, [])

    def test_the_replay_never_blocks_or_writes_game_memory(self):
        text = (HUD_SRC / 'replay.cpp').read_text(encoding='utf-8') + (HUD_SRC / 'canvas.cpp').read_text(encoding='utf-8')
        for forbidden in ('Sleep', 'WaitFor', 'AcquireSRW', 'EnterCritical', 'VirtualProtect', 'malloc', 'new ',
                          'copy_to_caller', 'memcpy(', 'memset('):
            self.assertNotIn(forbidden, text, forbidden)

    def test_the_canvas_and_the_replay_never_stamp_the_presenter(self):
        # The stamp belongs to the frame driver (driver.cpp, test_coop_hud_driver.py): it means the menu layer was
        # presented, which only the driver knows.
        for path in HUD_SRC.glob('*.[ch]*'):
            if path.stem != 'driver':
                self.assertNotIn('note_drawn', path.read_text(encoding='utf-8'), path.name)


# ---- the patch layer: several gates on one row, an optional row ---------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class GateTwinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = world()
        cls.twin = cls.hw.twin

    def tearDown(self):
        self.twin.reset()

    def test_all_stock_hooks_the_frame_hook(self):
        self.twin.reset()
        report = self.twin.call('ILCT_PatchPreflight')
        self.assertEqual((report.result, report.gated_off), (OK, 0))
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual(report.result, OK)
        self.assertIsNotNone(self.twin.info(self.hw.row).trampoline or None)
        self.assertEqual(ctypes.string_at(H0_SITE, 1), b'\xE8', 'the call stays a call (island)')

    def test_any_gate_not_stock_skips_only_the_frame_hook(self):
        for sid, (va, expected) in HD_ROWS.items():
            with self.subTest(sid):
                self.twin.reset()
                at = va + len(bytes.fromhex(expected)) - 1
                self.twin.poke(at, bytes([ctypes.string_at(at, 1)[0] ^ 0xFF]))
                report = self.twin.call('ILCT_PatchPreflight')
                self.assertEqual((report.result, report.gated_off), (OK, 1))
                report = self.twin.call('ILCT_PatchInstall')
                self.assertEqual(report.result, OK)
                self.assertFalse(self.twin.info(self.hw.row).trampoline, 'HD-01 not hooked')
                self.assertEqual(ctypes.string_at(H0_SITE, 5), bytes.fromhex('e8 51 0a 00 00'), 'site untouched')
                others = [r for r in self.twin.patch if r != self.hw.row]
                self.assertTrue(all(self.twin.info(r).trampoline for r in others[:5]), 'the rest installs')

    def test_two_gates_not_stock_still_skip_once(self):
        self.twin.reset()
        for va, expected in (HD_ROWS['HD-V2'], HD_ROWS['HD-V7']):
            at = va + 1
            self.twin.poke(at, bytes([ctypes.string_at(at, 1)[0] ^ 0xFF]))
        report = self.twin.call('ILCT_PatchPreflight')
        self.assertEqual((report.result, report.gated_off), (OK, 1))
        self.assertEqual(self.twin.call('ILCT_PatchInstall').result, OK)
        self.assertFalse(self.twin.info(self.hw.row).trampoline)

    def test_vision_no_combat_log_skips_the_hook_instead_of_refusing(self):
        self.twin.reset()
        self.twin.poke(H0_SITE, b'\x90' * 5)  # Vision's patch: nop 0x05
        report = self.twin.call('ILCT_PatchPreflight')
        self.assertEqual((report.result, report.gated_off), (OK, 1))
        report = self.twin.call('ILCT_PatchInstall')
        self.assertEqual(report.result, OK)
        self.assertFalse(self.twin.info(self.hw.row).trampoline)
        self.assertEqual(ctypes.string_at(H0_SITE, 5), b'\x90' * 5, 'their bytes stay theirs')

    def test_instrument_a_non_optional_row_still_refuses(self):
        self.twin.reset()
        rows = {r['id']: r for r in pe.site_rows()}
        site = TWIN_BASE + rows['IN-01']['rva']
        self.twin.poke(site, bytes([ctypes.string_at(site, 1)[0] ^ 0xFF]))
        self.assertEqual(self.twin.call('ILCT_PatchPreflight').result, SITE_MISMATCH)


# ---- the canvas ------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class CanvasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = world()
        cls.d = cls.hw.d

    def setUp(self):
        self.d.ILCT_HudReset()

    def test_layouts(self):
        out = (u32 * 16)()
        self.assertEqual(self.d.ILCT_HudSizes(out, 16), len(LAYOUTS))
        self.assertEqual(list(out[:len(LAYOUTS)]), LAYOUTS)

    def test_encoding_is_the_game_table(self):
        out = ctypes.create_string_buffer(256)
        subs = u32()
        text = 'P1 Ready: Fire, 99% (x2) - ok?'
        n = self.d.ILCT_HudEncode(text.encode(), out, 256, ctypes.byref(subs))
        self.assertEqual(out.raw[:n + 1], encoded(text) + b'\0')
        self.assertEqual(subs.value, 0)
        n = self.d.ILCT_HudEncode('a<b*c'.encode(), out, 256, ctypes.byref(subs))
        self.assertEqual((out.raw[:n], subs.value), (bytes([58, 154, 59, 154, 60]), 2), "missing glyphs become '?'")
        self.assertEqual(self.d.ILCT_HudEncode(b'x' * 121, out, 256, ctypes.byref(subs)), -1, 'longer than TEXT_MAX')
        self.assertEqual(self.d.ILCT_HudEncode(b'abcd', out, 4, ctypes.byref(subs)), -1, 'no room for the NUL')

    def test_builder_and_limits(self):
        d = self.d
        d.ILCT_HudBegin(3, 1000)
        self.assertEqual(d.ILCT_HudText(10, 20, b'Hello', rgba(1, 2, 3, 4), 150, 2), 1)
        self.assertEqual(d.ILCT_HudSprite(ATLAS_PARTYTOP_4_C, 3, 0, 18, 5, 6, WHITE, 0, 0), 1)
        self.assertEqual(d.ILCT_HudBar(7, 8, 300, 12, rgba(9, 9, 9)), 1)
        self.assertEqual(d.ILCT_HudSprite(99, 0, 0, 0, 0, 0, WHITE, 0, 0), 0, 'unknown atlas')
        self.assertEqual(d.ILCT_HudBar(0, 0, 0, 5, WHITE), 0, 'empty bar')
        self.assertEqual(d.ILCT_HudText(0, 0, b'', WHITE, 100, 0), 0, 'empty text')
        self.assertEqual(d.ILCT_HudText(0, 0, b'x', WHITE, 0, 0), 0, 'zero size')
        f = self.hw.frame()
        self.assertEqual((f.epoch, f.built_ms, f.count, d.ILCT_HudRefused()), (3, 1000, 3, 4))
        t, s, b = f.cmds[0], f.cmds[1], f.cmds[2]
        self.assertEqual((t.kind, t.x, t.y, t.scale, t.font, t.colour), (CMD_TEXT, 10, 20, 150, 2, rgba(1, 2, 3, 4)))
        self.assertEqual(ctypes.string_at(ctypes.addressof(f) + Frame.text.offset + t.text, 6),
                         encoded('Hello') + b'\0')
        self.assertEqual((s.kind, s.atlas, s.page, s.sheet, s.cell, s.x, s.y), (CMD_SPRITE, 1, 3, 0, 18, 5, 6))
        self.assertEqual((b.kind, b.x, b.y, b.w, b.h, b.colour), (CMD_BAR, 7, 8, 300, 12, rgba(9, 9, 9)))
        d.ILCT_HudBegin(3, 1000)
        for k in range(MAX_COMMANDS):
            self.assertEqual(d.ILCT_HudBar(k, 0, 10, 2, WHITE), 1)
        self.assertEqual(d.ILCT_HudBar(0, 0, 10, 2, WHITE), 0, 'full')
        d.ILCT_HudBegin(3, 1000)
        n = 0
        while d.ILCT_HudText(0, 0, b'x' * 100, WHITE, 100, 0):
            n += 1
        self.assertEqual(n, TEXT_POOL // 101, 'the text pool bounds the strings')

    def test_publication(self):
        d = self.d
        f = Frame()
        self.assertEqual(d.ILCT_HudLatest(ctypes.byref(f)), 0, 'nothing published')
        self.hw.publish(epoch=9, built=1234)
        self.assertEqual(d.ILCT_HudLatest(ctypes.byref(f)), 1)
        self.assertEqual((f.epoch, f.built_ms, f.count), (9, 1234, 1))
        d.ILCT_HudWithdraw()
        self.assertEqual(d.ILCT_HudLatest(ctypes.byref(f)), 0, 'withdrawn')
        bad = self.hw.frame()
        bad.count = MAX_COMMANDS + 1
        d.ILCT_HudPublishFrame(ctypes.byref(bad))
        self.assertEqual(d.ILCT_HudLatest(ctypes.byref(f)), 0, 'an impossible count is refused')

    def test_ring_estimate(self):
        d = self.d
        d.ILCT_HudBegin(1, 1)
        for k in range(13):
            d.ILCT_HudSprite(0, 0, 0, k, 0, 0, WHITE, 0, 0)
        d.ILCT_HudText(0, 0, b'x', WHITE, 100, 0)
        d.ILCT_HudSprite(0, 0, 0, 0, 0, 0, WHITE, 0, 0)
        d.ILCT_HudBar(0, 0, 10, 2, WHITE)
        f = self.hw.frame()
        batch, vertex4 = 0x250, 4 * 0x30
        expected = 2 * batch + 13 * vertex4 + batch + vertex4 + 3 * (batch + vertex4)
        self.assertEqual(d.ILCT_HudRingEstimate(ctypes.byref(f)), expected,
                         '13 sprites = two batches of at most 12; text breaks a batch; a bar is three')

    def test_worst_case_four_seat_frame_fits_with_headroom(self):
        d = self.d
        d.ILCT_HudBegin(1, 1)
        made = 0
        # four command menus (window frame 9 pieces, 10 rows of text, a cursor, a selection bar, 2 arrows, a header)
        for seat in range(4):
            for piece in range(9):
                made += d.ILCT_HudSprite(6, 0, 0, piece, 0, 0, WHITE, 0, 0)
            for row in range(10):
                made += d.ILCT_HudText(0, row * 30, b'Cura           MP 12', WHITE, 100, 0)
            made += d.ILCT_HudSprite(5, 0, 0, 0, 0, 0, WHITE, 0, 0)
            made += d.ILCT_HudBar(0, 0, 360, 30, WHITE)
            made += d.ILCT_HudSprite(5, 0, 6, 0, 0, 0, WHITE, 0, 0)
            made += d.ILCT_HudSprite(5, 0, 6, 1, 0, 0, WHITE, 0, 0)
            made += d.ILCT_HudText(0, 0, b'Balthier', WHITE, 100, 0)
        # four party rows: edge, numeral, low-HP number, ghost, two status icons
        for seat in range(4):
            made += d.ILCT_HudBar(0, 0, 4, 40, WHITE)
            made += d.ILCT_HudText(0, 0, b'P1', WHITE, 85, 0)
            made += d.ILCT_HudText(0, 0, b'9999', WHITE, 100, 0)
            made += d.ILCT_HudBar(0, 0, 200, 6, WHITE)
            made += d.ILCT_HudSprite(1, 3, 0, 18, 0, 0, WHITE, 0, 0)
            made += d.ILCT_HudSprite(1, 3, 0, 19, 0, 0, WHITE, 0, 0)
        # Target Info: name, level, HP numbers and bar, 8 element icons, 6 statuses, drops
        for line in (b'Wild Saurian  Lv 23', b'12345 / 12345   100%', b'Drops: Hi-Potion  Steal: Ether'):
            made += d.ILCT_HudText(0, 0, line, WHITE, 100, 0)
        made += d.ILCT_HudBar(0, 0, 234, 12, WHITE)
        for k in range(8):
            made += d.ILCT_HudSprite(0, 10, 0, k, 0, 0, WHITE, 0, 0)
        for k in range(6):
            made += d.ILCT_HudSprite(1, 3, 0, k, 0, 0, WHITE, 0, 0)
        # the battle log expanded to 20 lines, each with a seat dot
        for k in range(20):
            made += d.ILCT_HudSprite(2, 0, 0, 0, 0, 0, WHITE, 0, 0)
            made += d.ILCT_HudText(0, 0, b'Vaan > Cura > Basch  +1234', WHITE, 100, 0)
        f = self.hw.frame()
        self.assertEqual(d.ILCT_HudRefused(), 0, f'the worst case fits the frame ({made} commands)')
        self.assertLessEqual(f.count, MAX_COMMANDS)
        ours = d.ILCT_HudRingEstimate(ctypes.byref(f))
        print(f'\nworst-case 4-seat frame: {f.count}/{MAX_COMMANDS} commands, {f.text_used}/{TEXT_POOL} text bytes, '
              f'ring estimate {ours} bytes = {100 * ours / RING_OURS_MAX:.1f}% of the per-frame share, '
              f'{100 * ours / RING:.2f}% of the 32 MiB ring')
        self.assertLess(ours, RING_OURS_MAX // 2, 'under half of the canvas share of the ring')
        self.assertLess(ours, RING // 100, 'under 1% of the ring')

    def test_the_test_draw_frame(self):
        d = self.d
        d.ILCT_HudProduce(4, 77)
        f = Frame()
        self.assertEqual(d.ILCT_HudLatest(ctypes.byref(f)), 1)
        kinds = [f.cmds[k].kind for k in range(f.count)]
        self.assertEqual(kinds, [CMD_TEXT] * 4 + [CMD_SPRITE, CMD_BAR], 'four seat strings, a status icon, a bar')
        colours = {f.cmds[k].colour for k in range(4)}
        self.assertEqual(len(colours), 4, 'four distinct seat colours by default')
        s = f.cmds[4]
        self.assertEqual((s.atlas, s.page, s.sheet, s.cell), (ATLAS_PARTYTOP_4_C, 3, 0, 18), 'Protect')
        for k in range(4):
            self.assertTrue(all(0 <= c <= 0x80 for c in f.cmds[k].colour.to_bytes(4, 'little')), 'PS2 scale')


# ---- the guard -------------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class GuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = world().d
        assert cls.d.ILCT_HudGuardAttach() == 1

    def test_returns(self):
        nested = i32(-1)
        self.assertEqual(self.d.ILCT_HudGuardCall(0, 0x1234, ctypes.byref(nested)), FAULT_RETURNED)
        self.assertEqual(self.d.ILCT_HudGuardTrace(), 0x1234)

    def test_faults_are_caught_and_counted(self):
        self.d.ILCT_HudGuardReset()
        nested = i32(-1)
        with quiet_faulthandler():
            for mode in (1, 2, 3):
                self.assertEqual(self.d.ILCT_HudGuardCall(mode, 0x77, ctypes.byref(nested)), FAULT_FAULTED, mode)
        v = FaultView()
        self.d.ILCT_HudGuardFault(ctypes.byref(v))
        self.assertEqual((v.faults, v.code), (3, 0xC0000005))
        self.assertEqual(self.d.ILCT_HudGuardCall(0, 5, ctypes.byref(nested)), FAULT_RETURNED, 'usable again')

    def test_non_volatile_registers_come_back(self):
        result = i32(-1)
        with quiet_faulthandler():
            changed = self.d.ILCT_HudGuardPreserves(ctypes.byref(result))
        self.assertEqual(result.value, FAULT_FAULTED)
        self.assertEqual(changed, 0, f'registers not restored: {changed:#x}')

    def test_one_call_at_a_time(self):
        nested = i32(-1)
        self.assertEqual(self.d.ILCT_HudGuardCall(4, 0, ctypes.byref(nested)), FAULT_RETURNED)
        self.assertEqual(nested.value, FAULT_BUSY)


# ---- the hook, called directly ---------------------------------------------------------------------------------------
class HookCtx(ctypes.Structure):
    _fields_ = [('xmm', u64 * 12), ('resume', u64), ('reserved', u64), ('rax', u64), ('rcx', u64), ('rdx', u64),
                ('rbx', u64), ('rsp', u64), ('rbp', u64), ('rsi', u64), ('rdi', u64), ('r8', u64), ('r9', u64),
                ('r10', u64), ('r11', u64), ('r12', u64), ('r13', u64), ('r14', u64), ('r15', u64), ('rflags', u64),
                ('index', u64)]


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HookDecisionTest(unittest.TestCase):
    """h0 and its after-handler on host memory: a synthetic log manager and draw context, the twin's globals poked.
    Every case below stops before the first game call (the fault drill faults before it), so nothing of the game's
    runs in this process."""

    @classmethod
    def setUpClass(cls):
        cls.hw = world()
        cls.d = cls.hw.d
        cls.mgr = ctypes.create_string_buffer(0x4100)
        cls.ctx = (u8 * 0xA0)()
        cls.arena = (u64 * 3)()
        cls.ring = ctypes.create_string_buffer(0x100)

    def setUp(self):
        self.world_up()

    def world_up(self, reset_hud=True):
        self.hw.setup(reset_hud=reset_hud)
        a = ctypes.addressof
        struct.pack_into('<Q', self.mgr, 0x38, a(self.ctx))
        ctypes.memmove(self.ctx, IDENTITY, len(IDENTITY))   # a usable draw matrix (an all-zero one is refused)
        struct.pack_into('<h', self.ctx, 0x58, 0x80)
        struct.pack_into('<h', self.ctx, 0x5A, 0x80)
        if reset_hud:
            self.set_ring(0)
        self.hw.twin.poke(RENDER_ARENA, struct.pack('<Q', a(self.arena)))
        self.hw.twin.poke(UI_READY, struct.pack('<I', 1))
        self.hw.publish()

    def tearDown(self):
        self.d.ILCT_HudInjectFault(0)
        self.hw.twin.reset()

    def set_ring(self, cursor_offset):
        base = 0x10_0000
        self.arena[0], self.arena[1], self.arena[2] = base, base + cursor_offset, base + RING

    def call(self, epoch=gt.EPOCH) -> int:
        c = HookCtx()
        c.rcx, c.rdx = ctypes.addressof(self.mgr), ctypes.addressof(self.ctx)
        c.index = self.hw.row
        r = self.d.ILCT_HudCall(ctypes.byref(c), epoch)
        if r == 2:
            self.d.ILCT_HudAfter(ctypes.byref(c), epoch)
        return r

    def delta(self, before: Counters, after: Counters) -> dict:
        return {n: getattr(after, n) - getattr(before, n) for n in COUNTERS
                if n not in HOOK_TIMING and getattr(after, n) != getattr(before, n)}

    def run_case(self, prepare=None, epoch=gt.EPOCH) -> tuple[int, dict]:
        if prepare:
            prepare()
        before = self.hw.counters()
        with quiet_faulthandler():
            r = self.call(epoch)
        return r, self.delta(before, self.hw.counters())

    def test_switch_off_is_stock(self):
        r, d = self.run_case(lambda: self.d.ILCT_HudSetTestDraw(0))
        self.assertEqual((r, d), (0, {'reached': 1, 'declined_off': 1}))

    def test_render_disarmed_or_faulted_is_stock(self):
        r, d = self.run_case(lambda: self.d.ILCT_PatchApplySubsystems(0xFF & ~RENDER))
        self.assertEqual((r, d), (0, {'reached': 1, 'declined_off': 1}))
        self.hw.setup()
        r, d = self.run_case(lambda: self.d.ILCT_PatchFault(RENDER))
        self.assertEqual((r, d), (0, {'reached': 1, 'declined_off': 1}))

    def test_not_attached_is_stock(self):
        r, d = self.run_case(lambda: self.d.ILCT_HudSetPinsOk(0))
        self.assertEqual((r, d), (0, {'reached': 1, 'declined_off': 1}))

    def test_frame_reasons(self):
        cases = {
            'none': lambda: self.d.ILCT_HudWithdraw(),
            'other epoch': lambda: self.hw.publish(epoch=gt.EPOCH + 1),
            'stale': lambda: self.hw.publish(built=gt.NOW - 251),
            'from the future': lambda: self.hw.publish(built=gt.NOW + 251),
            'empty': lambda: self.hw.publish(build=lambda d: None),
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.hw.publish()
                r, d = self.run_case(prepare)
                self.assertEqual((r, d), (2, {'reached': 1, 'declined_frame': 1}), name)
        r, d = self.run_case(lambda: self.hw.publish(built=gt.NOW - 250))
        self.assertNotIn('declined_frame', d, 'exactly STALE_MS old still counts (instrument)')

    def test_context_reasons(self):
        a = ctypes.addressof
        cases = {
            'not the window context': lambda: struct.pack_into('<Q', self.mgr, 0x38, a(self.ctx) + 0x10),
            'faded out': lambda: struct.pack_into('<h', self.ctx, 0x58, 0),
            'UI not ready': lambda: self.hw.twin.poke(UI_READY, struct.pack('<I', 0)),
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.setUp()
                r, d = self.run_case(prepare)
                self.assertEqual((r, d), (2, {'reached': 1, 'declined_context': 1}), name)

    def test_budget_reasons(self):
        cases = {
            'ring unreadable': lambda: self.hw.twin.poke(RENDER_ARENA, struct.pack('<Q', 0)),
            'cursor outside the ring': lambda: self.set_ring(RING + 1),
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.setUp()
                r, d = self.run_case(prepare)
                self.assertEqual((r, d), (2, {'reached': 1, 'declined_budget': 1}), name)
        # traffic: the first call only sets the baseline (fault drill = "allowed": it faults before any game call)
        self.setUp()
        self.d.ILCT_HudInjectFault(1)
        self.set_ring(0)
        self.run_case()
        self.d.ILCT_HudInjectFault(0)
        self.world_up(reset_hud=False)  # the fault latched RENDER: a fresh install, the ring baseline kept
        b = Budget()
        self.set_ring(RING_SAFE + 0x10000)  # over 8 MiB of traffic in one frame: over the safe quarter
        r, d = self.run_case()
        self.d.ILCT_HudBudget(ctypes.byref(b))
        self.assertEqual(d, {'reached': 1, 'declined_budget': 1})
        self.assertEqual((b.capacity, b.frame_use), (RING, RING_SAFE + 0x10000))

    def test_a_ring_delta_longer_than_a_frame_is_not_charged(self):
        # The baseline from a call RING_GAP_MS or more ago (the HUD tree did not draw in between) is replaced, not
        # charged to this frame. Control: the same traffic within one frame's time is refused.
        header = (ROOT / 'native' / 'coop' / 'src' / 'hud' / 'canvas.h').read_text(encoding='utf-8')
        self.assertIn(f'constexpr uint64_t RING_GAP_MS = {RING_GAP_MS};', header)
        for gap, refused in ((RING_GAP_MS + 1, False), (RING_GAP_MS, True), (20, True)):
            with self.subTest(gap=gap):
                self.world_up()
                self.d.ILCT_HudInjectFault(1)
                self.set_ring(0)
                self.run_case()                    # the baseline at NOW (the drill faults: RENDER latched)
                self.world_up(reset_hud=False)     # a fresh install (clock back at NOW), the baseline kept
                self.d.ILCT_SetFixedClock(gt.NOW + gap)
                self.d.ILCT_PatchHeartbeat(gt.EPOCH)
                self.hw.publish(built=gt.NOW + gap)
                self.set_ring(RING_SAFE + 0x10000)
                before = self.hw.counters().ring_gaps
                r, d = self.run_case()
                self.assertEqual('declined_budget' in d, refused, d)
                self.assertEqual('faults' in d, not refused, 'allowed: the drill fires before any game call')
                self.assertEqual(self.hw.counters().ring_gaps - before, 0 if refused else 1)
                b = Budget()
                self.d.ILCT_HudBudget(ctypes.byref(b))
                self.assertEqual(b.frame_use, RING_SAFE + 0x10000 if refused else 0)
        self.d.ILCT_HudInjectFault(0)

    def sample_at(self, ms: int, cursor: int) -> dict:
        """One hook call at NOW + ms with the ring cursor at `cursor` (a fresh install each time: an allowed frame's
        fault drill latched RENDER; the ring baseline is kept)."""
        self.world_up(reset_hud=False)
        self.d.ILCT_SetFixedClock(gt.NOW + ms)
        self.d.ILCT_PatchHeartbeat(gt.EPOCH)
        self.hw.publish(built=gt.NOW + ms)
        self.set_ring(cursor)
        return self.run_case()[1]

    def test_a_slow_steady_frame_rate_is_still_charged(self):
        # Under 10 fps every call is more than RING_GAP_MS after the last. Calls at a steady cadence up to
        # RING_SLOW_MS apart (and at most twice the spacing before) are consecutive frames, so their traffic is
        # charged and the budget still refuses an over-budget frame. Over RING_SLOW_MS it is a gap (not charged).
        header = (ROOT / 'native' / 'coop' / 'src' / 'hud' / 'canvas.h').read_text(encoding='utf-8')
        self.assertIn(f'constexpr uint64_t RING_SLOW_MS = {RING_SLOW_MS};', header)
        for spacing, refused in ((120, True), (250, True), (RING_SLOW_MS, True), (RING_SLOW_MS + 1, False)):
            with self.subTest(spacing=spacing):
                self.world_up()
                self.d.ILCT_HudInjectFault(1)
                self.set_ring(0)
                self.run_case()                                   # the first baseline
                gaps = self.hw.counters().ring_gaps
                self.sample_at(spacing, 0x1000)                   # the first slow call: a gap, it sets the cadence
                self.assertEqual(self.hw.counters().ring_gaps - gaps, 1)
                d = self.sample_at(2 * spacing, 0x1000 + RING_SAFE + 0x10000)
                self.assertEqual('declined_budget' in d, refused, d)
                self.assertEqual(self.hw.counters().ring_gaps - gaps, 1 if refused else 2)
                b = Budget()
                self.d.ILCT_HudBudget(ctypes.byref(b))
                self.assertEqual(b.frame_use, RING_SAFE + 0x10000 if refused else 0)
        with self.subTest('a long gap after quick frames'):
            self.world_up()
            self.d.ILCT_HudInjectFault(1)
            self.set_ring(0)
            self.run_case()
            self.sample_at(16, 0x1000)                            # a quick frame: charged (16 ms spacing)
            d = self.sample_at(16 + 300, 0x1000 + RING_SAFE + 0x10000)   # 300 ms > 2 x 16: a load, not a frame
            self.assertNotIn('declined_budget', d)
        self.d.ILCT_HudInjectFault(0)

    def test_a_gap_neither_charges_nor_decays_the_peak(self):
        self.d.ILCT_HudInjectFault(1)
        self.set_ring(0)
        self.run_case()
        self.d.ILCT_HudInjectFault(0)
        self.world_up(reset_hud=False)
        self.set_ring(RING_SAFE + 0x10000)
        self.assertIn('declined_budget', self.run_case()[1])
        b = Budget()
        self.d.ILCT_HudBudget(ctypes.byref(b))
        peak = b.peak
        self.assertEqual(peak, RING_SAFE + 0x10000)
        gaps = self.hw.counters().ring_gaps
        self.sample_at(1000, RING_SAFE + 0x10000)                 # a second later (a load): a gap
        self.d.ILCT_HudBudget(ctypes.byref(b))
        self.assertEqual((self.hw.counters().ring_gaps - gaps, b.peak, b.frame_use), (1, peak, 0), 'kept as it was')
        self.run_case()                                           # the next call, same time: charged, no traffic
        self.d.ILCT_HudBudget(ctypes.byref(b))
        self.assertEqual(b.peak, peak - peak // 32, 'a charged sample decays it')

    def test_budget_decays_and_allows_again(self):
        self.d.ILCT_HudInjectFault(1)  # an allowed frame faults at once (no game call); a refused one does not
        self.set_ring(0)
        self.run_case()
        self.world_up(reset_hud=False)
        self.set_ring(RING_SAFE + 0x10000)
        r, d = self.run_case()
        self.assertIn('declined_budget', d)
        peak = []
        for _ in range(200):
            b = Budget()
            r, d = self.run_case()
            self.d.ILCT_HudBudget(ctypes.byref(b))
            peak.append(b.peak)
            if 'declined_budget' not in d:
                self.assertEqual(d.get('faults'), 1, 'allowed again (the drill faults before any game call)')
                break
        self.assertLess(peak[-1], RING_SAFE, 'the peak decays when the traffic stops')
        self.assertLess(len(peak), 200)

    def test_a_fault_in_the_replay_latches_render_and_is_stock_after(self):
        self.d.ILCT_HudInjectFault(1)
        r, d = self.run_case()
        self.assertEqual((r, d), (2, {'reached': 1, 'faults': 1}))
        self.d.ILCT_PatchSubsystems.restype = u32
        self.assertEqual(self.d.ILCT_PatchSubsystems() & RENDER, RENDER, 'still armed')
        r, d = self.run_case()
        self.assertEqual((r, d), (0, {'reached': 1, 'declined_off': 1}), 'RENDER faulted: stock')

    def test_the_test_draw_producer_follows_the_switch_and_the_world(self):
        d = self.d
        d.ILCT_HudWithdraw()
        d.ILCT_HudSetTestDraw(0)
        before = self.hw.counters().published
        d.ILCT_HudStep(gt.NOW)
        self.assertEqual(self.hw.counters().published, before, 'off: nothing')
        d.ILCT_HudSetTestDraw(1)
        d.ILCT_HudStep(gt.NOW)
        f = Frame()
        # the session has no world in this harness: the producer withdraws instead of publishing
        self.assertEqual(d.ILCT_HudLatest(ctypes.byref(f)), 0, 'no valid world: nothing to draw')


# ---- unicorn on the hooked twin ----------------------------------------------------------------------------------------
SCRATCH = pe.SCRATCH
MGR, NODE, CTX, ARENA_OBJ = SCRATCH + 0x10000, SCRATCH + 0x20000, SCRATCH + 0x30000, SCRATCH + 0x38000
PICTURE, MRP_GROUP, MRP_NODE, MRP_NODES = SCRATCH + 0x40000, SCRATCH + 0x50000, SCRATCH + 0x51000, SCRATCH + 0x52000
BATCH = SCRATCH + 0x53000
BG_WINDOW, ROOT_WINDOW, CTX_BG, CTX_ROOT = SCRATCH + 0x60000, SCRATCH + 0x61000, SCRATCH + 0x62000, SCRATCH + 0x63000
IDENTITY = struct.pack('<16f', 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)


def context_block(dx=0.0, dy=0.0, alpha=0x80) -> bytes:
    """A draw context as the window draw leaves it (0x2d8c00 defaults, a translation, full alpha)."""
    b = bytearray(0xA0)
    b[0:64] = IDENTITY
    struct.pack_into('<ff', b, 48, dx, dy)
    struct.pack_into('<hh', b, 0x58, alpha, 0x80)
    struct.pack_into('<I', b, 0x5C, 0x20000)
    struct.pack_into('<f', b, 0x60, 1.0)
    struct.pack_into('<ff', b, 0x68, 1.0, 1.0)
    struct.pack_into('<QQQ', b, 0x78, 0x44, 0x1001, 0x60)
    struct.pack_into('<II', b, 0x90, WHITE, WHITE)
    return bytes(b)


# A synthetic TIM2 picture header with an eXt block in the game format (values ours): five pages,
# page 3 holds one sheet; frame k of that sheet at u = 4k, v = 7, 52x64, palette 0x21.
EXT_PAGES = [0, 0, 0, 5, 6]          # A[0..4]: index of each page's sheet list inside A
SHEET_FIRST = {5: 0, 6: 0}           # A[5] = first frame of page 3 sheet 0; A[6] spare


def picture_header() -> bytes:
    a = EXT_PAGES + [SHEET_FIRST[5], SHEET_FIRST[6]]
    frames_off = 0x14 + 2 * len(a)
    frames_off += (-frames_off) % 8
    frames = b''.join(struct.pack('<HHI', 4 * k, 7, 52 | 64 << 12 | 0x21 << 24) for k in range(32))
    clut_off = frames_off + len(frames)
    clut = bytes([0x20 + 8 * g for g in range(16)])
    body = struct.pack('<HH', frames_off, clut_off) + struct.pack(f'<{len(a)}H', *a)
    body = body.ljust(frames_off - 0x10, b'\0') + frames + clut
    size = 0x10 + len(body)
    ext = b'eXt\0' + struct.pack('<III', size, size, 0) + body
    return bytes(0x30) + ext


def frame_table_address(page=3, sheet=0) -> int:
    ext = PICTURE + 0x30
    frames_off = struct.unpack_from('<H', picture_header(), 0x40)[0]
    a = EXT_PAGES + [SHEET_FIRST[5], SHEET_FIRST[6]]
    return ext + frames_off + 8 * a[a[page] + sheet]


@unittest.skipUnless(NATIVE and GAME and pe.unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class NativeEquivalenceTest(unittest.TestCase):
    """The frame hook on the hooked twin in unicorn: the stock log draw, then our replay, through the island and the
    after-handler. The native text renderer (0x2b0280), the batched sprite draw, the batch begin/end and the selection
    bar are where the run stops and records what reached them; everything before them is the game's own code."""

    STOPS = {TEXT_RENDER, TEXT_RENDER_EX, BATCH_BEGIN, BATCH_SPRITE, BATCH_END, SELECTION_BAR, SCISSOR}

    @classmethod
    def setUpClass(cls):
        import test_coop_movement as mv
        cls.hw = world()
        cls.hw.setup()
        cls.emu = mv.MoveEmulator(cls.hw.twin)
        cls.counters_at = cls.hw.d.ILCT_HudCountersAddress()

    @classmethod
    def tearDownClass(cls):
        cls.hw.d.ILCT_HudReset()
        cls.hw.twin.reset()

    def setUp(self):
        self.hw.d.ILCT_HudReset()
        self.hw.d.ILCT_HudSetTestDraw(1)
        self.hw.d.ILCT_HudSetPinsOk(1)

    # ---- the world in the emulator ----
    def lay_out(self, uc, text=None, colour=WHITE, fade=0x100, ancestors=False):
        uc.mem_write(MGR, bytes(0x40C8))
        uc.mem_write(MGR + 0x38, struct.pack('<Q', CTX))
        uc.mem_write(CTX, context_block())
        if ancestors:  # log manager -> log background window (sliding, half faded) -> the root
            for window, ctx, parent, block in ((BG_WINDOW, CTX_BG, ROOT_WINDOW, context_block(-20.0, 92.0, 0x40)),
                                               (ROOT_WINDOW, CTX_ROOT, 0, context_block(100.0, 50.0))):
                uc.mem_write(window, bytes(0x100))
                uc.mem_write(window + 0x10, struct.pack('<Q', parent))
                uc.mem_write(window + 0x38, struct.pack('<Q', ctx))
                uc.mem_write(ctx, block)
            uc.mem_write(MGR + 0x10, struct.pack('<Q', BG_WINDOW))
            uc.mem_write(CTX, context_block(-20.0, 92.0, 0x40))
        if text is not None:
            uc.mem_write(NODE, bytes(0x198))
            uc.mem_write(NODE, encoded(text) + b'\0')
            uc.mem_write(NODE + 0x180, struct.pack('<I', colour))
            uc.mem_write(MGR + 0x40A0, struct.pack('<Q', NODE))
        uc.mem_write(MGR + 0x40B4, struct.pack('<h', 0x80))
        uc.mem_write(MGR + 0x40B8, struct.pack('<h', fade))
        uc.mem_write(ARENA_OBJ, struct.pack('<QQQ', 0x10_0000, 0x10_0000, 0x10_0000 + RING))
        uc.mem_write(RENDER_ARENA, struct.pack('<Q', ARENA_OBJ))
        uc.mem_write(UI_READY, struct.pack('<I', 1))
        # the texture registry: entry 0 = partytop_4_c (loaded), its picture header
        uc.mem_write(REGISTRY, bytes(0x48))
        uc.mem_write(REGISTRY, b'partytop_4_c\0')
        uc.mem_write(REGISTRY + 0x38, b'\x20')
        uc.mem_write(REGISTRY + 0x20, struct.pack('<Q', PICTURE))
        uc.mem_write(PICTURE, picture_header())

    def allowed(self, x: int) -> bool:
        if not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE):
            return True  # our module, the island
        if x in self.STOPS or x in IMPORT_THUNKS:
            return False
        if LOG_HANDLER <= x < 0x2BE8D0:
            return LOG_DRAW_MSG <= x < AFTER_CALL
        return True

    def record(self, uc, address):
        X = pe.X
        if address in IMPORT_THUNKS:
            if address == MEMSET_THUNK:
                rcx, rdx, r8 = (uc.reg_read(r) for r in (X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8))
                uc.mem_write(rcx, bytes([rdx & 0xFF]) * r8)
                uc.reg_write(X.UC_X86_REG_RAX, rcx)
            return
        rcx, rdx, r8, r9 = (uc.reg_read(r) for r in (X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8,
                                                      X.UC_X86_REG_R9))
        rsp = uc.reg_read(X.UC_X86_REG_RSP)
        stack = struct.unpack('<6q', bytes(uc.mem_read(rsp + 0x28, 48)))
        if address in (TEXT_RENDER, TEXT_RENDER_EX):
            params = bytes(uc.mem_read(rcx, 0xD0))
            matrix = struct.unpack('<16f', bytes(uc.mem_read(struct.unpack_from('<Q', params, 0x20)[0], 64)))
            text = bytes(uc.mem_read(struct.unpack_from('<Q', params, 0x28)[0], 128)).split(b'\0', 1)[0]
            rest = params[:0x20] + params[0x30:]
            self.seen.append(('text', address, text, matrix, rest))
        elif address == BATCH_SPRITE:
            uvwh = struct.unpack('<4h', bytes(uc.mem_read(stack[0], 8)))
            colours = struct.unpack('<4I', bytes(uc.mem_read(r9, 16)))
            ctx = bytes(uc.mem_read(rdx, 0xA0))
            self.seen.append(('sprite', r8, colours, uvwh, tuple(s & 0xFFFFFFFF for s in stack[1:5]), ctx[0x5C],
                              ctx[0x90:0x94]))
        elif address == BATCH_BEGIN:
            self.seen.append(('begin', r8 & 0xFFFFFFFF))
        elif address == BATCH_END:
            self.seen.append(('end',))
        elif address == SELECTION_BAR:
            colour = struct.unpack('<I', bytes(uc.mem_read(stack[1], 4)))[0]
            self.seen.append(('bar', rdx & 0xFFFFFFFF, r8 & 0xFFFFFFFF, r9 & 0xFFFFFFFF, stack[0] & 0xFFFFFFFF,
                              colour))

    def run_hook(self, hooked=True, text=None, colour=WHITE, fade=0x100, gate=True, entry_args=None,
                 ancestors=False):
        emu, uc, twin = self.emu, self.emu.uc, self.hw.twin
        row = self.hw.row
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        self.lay_out(uc, text, colour, fade, ancestors)
        self.seen = []
        stock = bytes.fromhex('e8 51 0a 00 00')
        patched = bytes(uc.mem_read(H0_SITE, 5))
        if not hooked:
            emu.write_code(H0_SITE, stock)
        else:
            emu.force_gate(row, gate)
        if entry_args is not None:
            def at_text_draw(u):
                X = pe.X
                ctx = u.reg_read(X.UC_X86_REG_RCX)
                entry_args.append((u.reg_read(X.UC_X86_REG_RDX) & 0xFFFFFFFF,
                                   u.reg_read(X.UC_X86_REG_R8) & 0xFFFFFFFF,
                                   bytes(u.mem_read(ctx + 0x58, 4)), bytes(u.mem_read(ctx + 0x90, 4))))
            emu.answers[TEXT_DRAW] = at_text_draw
        state = pe.random_state(random.Random(0x4040))
        state['gpr']['RBX'] = MGR
        state['gpr']['RSP'] &= ~0xF
        try:
            out = emu.run(LOG_DRAW_MSG, self.allowed, state, on_callee=self.record,
                          probe=lambda u: Counters.from_buffer_copy(bytes(u.mem_read(self.counters_at,
                                                                                     ctypes.sizeof(Counters)))))
        finally:
            emu.answers.pop(TEXT_DRAW, None)
            if not hooked:
                emu.write_code(H0_SITE, patched)
            else:
                emu.force_gate(row, False)
        self.assertEqual(emu.unexpected, [], 'imports the emulator does not answer')
        self.assertEqual(out['exit'], AFTER_CALL, 'returned to the log handler after the call')
        return out

    # ---- (1) text ----
    def test_canvas_text_equals_the_native_log_line(self):
        text, colour = 'Balthier casts Cura.', rgba(0x80, 0x70, 0x40)
        args = []
        native = self.run_hook(hooked=False, text=text, colour=colour, entry_args=args)
        self.assertEqual(len(self.seen), 1, self.seen)
        reference = self.seen[0]
        self.assertEqual(reference[:3], ('text', TEXT_RENDER, encoded(text)))
        (x, y, alpha_bright, ctx_colour), = args
        self.assertEqual(alpha_bright, struct.pack('<hh', 0x80, 0x80), 'a fully faded-in line (fade 0x100)')
        self.assertEqual(ctx_colour, struct.pack('<I', colour))
        self.hw.publish(build=lambda d: d.ILCT_HudText(x, y, text.encode(), colour, 100, 0))
        hooked = self.run_hook(hooked=True, text=text, colour=colour)
        print(f'\nnative log line at ({x}, {y}); the canvas text gives 0x2b0280 the same record')
        self.assertEqual(len(self.seen), 2, self.seen)
        self.assertEqual(self.seen[0], reference, 'the stock line is unchanged when hooked')
        self.assertEqual(self.seen[1], reference, 'the canvas text gives the renderer exactly the native record')
        c = hooked['probe']
        self.assertEqual((c.drawn, c.texts, c.faults), (1, 1, 0))
        self.assertEqual(native['probe'].reached, 0, 'stock: the hook never ran')

    def test_size_and_colour_are_ours(self):
        self.hw.publish(build=lambda d: (d.ILCT_HudText(100, 200, b'Size', rgba(0x10, 0x20, 0x30), 150, 0),
                                         d.ILCT_HudText(100, 260, b'Size', rgba(0x10, 0x20, 0x30), 100, 0)))
        self.run_hook(hooked=True)
        big, normal = self.seen
        self.assertEqual(struct.unpack_from('<f', big[4], 0x34 - 0x10)[0], 1.5, 'params+0x34 = size')
        self.assertEqual(struct.unpack_from('<f', normal[4], 0x34 - 0x10)[0], 1.0)
        self.assertEqual(struct.unpack_from('<I', normal[4], 0x30 - 0x10)[0], rgba(0x10, 0x20, 0x30),
                         'params+0x30 = the colour scaled by the window alpha (0x80 = unchanged)')
        self.assertEqual((normal[3][12], normal[3][13]), (100.0, 260.0), 'translation = (x, y) in the window space')

    def test_canvas_draws_in_the_root_space(self):
        self.hw.publish(build=lambda d: d.ILCT_HudText(10, 20, b'Root', WHITE, 100, 0))
        out = self.run_hook(hooked=True, text='Log line.', ancestors=True)
        native, ours = self.seen
        self.assertEqual((native[3][12], native[3][13]), (-20.0 + 26, 92.0 + 2), 'the log line: its window space')
        self.assertEqual((ours[3][12], ours[3][13]), (110.0, 70.0), 'the canvas: the root window space')
        self.assertEqual(struct.unpack_from('<I', ours[4], 0x30 - 0x10)[0], WHITE,
                         'not faded with the log window (its alpha is half)')
        self.assertEqual(out['probe'].texts, 1)

    # ---- (2) sprite ----
    def native_node_draw(self, cell, tint):
        """The native MRP node draw 0x2435c0 on one sprite node built as 0x29f4c0 builds it (frame table of the page and sheet, cell in the flags)."""
        emu, uc = self.emu, self.emu.uc
        emu.refresh()
        self.lay_out(uc)
        node = bytearray(0x40)
        struct.pack_into('<I', node, 0x08, 1 | 2 << 2 | cell << 16)        # visible, type 2, cell
        struct.pack_into('<hhHH', node, 0x0C, 640, 360, 52, 64)             # x, y, w, h
        struct.pack_into('<Q', node, 0x18, REGISTRY)                         # texture entry
        struct.pack_into('<Q', node, 0x20, frame_table_address())            # frame table of (page 3, sheet 0)
        struct.pack_into('<4I', node, 0x28, *[tint] * 4)
        node[0x38], node[0x39] = 0, 0x21                                     # no crop; palette byte (frame's own)
        uc.mem_write(MRP_NODE, bytes(node))
        uc.mem_write(MRP_NODES, struct.pack('<Q', MRP_NODE))
        uc.mem_write(MRP_GROUP, struct.pack('<IIQ', 1 << 2 | 1, 0, 0) + struct.pack('<Q', MRP_NODES))
        self.seen = []
        state = pe.random_state(random.Random(0x2435))
        state['gpr'].update(RCX=MRP_GROUP, RDX=CTX)
        state['gpr']['RSP'] = (state['gpr']['RSP'] & ~0xF) - 8
        top = state['gpr']['RSP']
        uc.mem_write(top, struct.pack('<Q', 0x2462E5))  # returns into the window draw (outside the allowed range)
        out = emu.run(NODE_DRAW, lambda x: not (TWIN_BASE <= x < TWIN_BASE + pe.TWIN_SIZE) or
                      (x not in self.STOPS and x not in IMPORT_THUNKS and NODE_DRAW <= x < 0x243A00) or
                      x == 0x808C40, state,
                      on_callee=self.record)
        self.assertEqual(out['exit'], 0x2462E5)
        return list(self.seen)

    def test_canvas_sprite_equals_the_native_node_draw(self):
        tint = rgba(0x60, 0x70, 0x80, 0x7F)
        native = self.native_node_draw(18, tint)
        self.assertEqual([s[0] for s in native], ['begin', 'sprite', 'end'])
        self.hw.publish(build=lambda d: d.ILCT_HudSprite(ATLAS_PARTYTOP_4_C, 3, 0, 18, 640, 360, tint, 0, 0))
        out = self.run_hook(hooked=True)
        ours = list(self.seen)
        self.assertEqual([s[0] for s in ours], ['begin', 'sprite', 'end'])
        n, o = native[1], ours[1]
        self.assertEqual(o[1], REGISTRY, 'the registry entry 0x24a5a0 returns for partytop_4_c')
        self.assertEqual(o[1:6], n[1:6], 'texture, colours, UV, x/y/w/h and CLUT as the native node draw')
        self.assertEqual(o[3], (4 * 18, 7, 52, 64), 'cell 18 of page 3 sheet 0')
        self.assertEqual(o[5], 0x20 + 8 * 2 + 1, 'CLUT = group[pal >> 4] + (pal & 0xF)')
        self.assertEqual(ours[0], native[0], 'batch begin(…, 1)')
        self.assertEqual((out['probe'].sprites, out['probe'].skipped_commands), (1, 0))

    def test_thirteen_sprites_are_two_batches_as_native(self):
        self.hw.publish(build=lambda d: [d.ILCT_HudSprite(ATLAS_PARTYTOP_4_C, 3, 0, k, k, 0, WHITE, 0, 0)
                                         for k in range(13)])
        self.run_hook(hooked=True)
        self.assertEqual([s[0] for s in self.seen],
                         ['begin'] + ['sprite'] * 12 + ['end', 'begin', 'sprite', 'end'])

    def test_unknown_atlas_or_cell_is_skipped_alone(self):
        self.hw.publish(build=lambda d: (d.ILCT_HudSprite(0, 0, 0, 0, 0, 0, WHITE, 0, 0),         # battle_4_c: not loaded
                                         d.ILCT_HudSprite(ATLAS_PARTYTOP_4_C, 3, 0, 5000, 0, 0, WHITE, 0, 0),
                                         d.ILCT_HudText(5, 5, b'still drawn', WHITE, 100, 0)))
        out = self.run_hook(hooked=True)
        self.assertEqual([s[0] for s in self.seen], ['text'])
        self.assertEqual((out['probe'].skipped_commands, out['probe'].texts, out['probe'].drawn), (2, 1, 1))

    # ---- (3) bar ----
    def test_bar_reaches_the_selection_bar_with_the_seat_colour(self):
        seat = rgba(0x23, 0x41, 0x78)
        self.hw.publish(build=lambda d: d.ILCT_HudBar(760, 320, 400, 12, seat))
        out = self.run_hook(hooked=True)
        self.assertEqual(self.seen, [('bar', 760, 320, 400, 12, seat)])
        self.assertEqual(out['probe'].bars, 1)

    # ---- stock cases ----
    def test_gate_closed_is_the_stock_log_draw(self):
        self.hw.publish()
        out = self.run_hook(hooked=True, text='Stock.', gate=False)
        self.assertEqual([s[:3] for s in self.seen], [('text', TEXT_RENDER, encoded('Stock.'))])
        self.assertEqual(out['probe'].reached, 0)

    def test_switch_off_is_the_stock_log_draw(self):
        self.hw.publish()
        self.hw.d.ILCT_HudSetTestDraw(0)
        out = self.run_hook(hooked=True, text='Stock.')
        self.assertEqual([s[:3] for s in self.seen], [('text', TEXT_RENDER, encoded('Stock.'))])
        self.assertEqual((out['probe'].reached, out['probe'].declined_off), (1, 1))

    def test_over_budget_is_the_stock_log_draw(self):
        self.hw.publish(build=lambda d: [d.ILCT_HudSprite(ATLAS_PARTYTOP_4_C, 3, 0, 0, 0, 0, WHITE, 0, 0)
                                         for _ in range(MAX_COMMANDS)])
        out = self.run_hook(hooked=True)
        self.assertEqual((out['probe'].drawn, out['probe'].sprites), (1, MAX_COMMANDS), 'instrument: it fits')

        def heavy(u):  # the ring cursor moved 8 MiB + 64 KiB since the last frame
            u.mem_write(ARENA_OBJ + 8, struct.pack('<Q', 0x10_0000 + RING_SAFE + 0x10000))
        self.hw.d.ILCT_HudSetRing(0x10_0000, 0x10_0000 + RING, 0x10_0000)
        self.emu.answers[LOG_DRAW] = heavy
        try:
            out = self.run_hook(hooked=True, text='Stock.')
        finally:
            self.emu.answers.pop(LOG_DRAW, None)
        self.assertEqual([s[0] for s in self.seen], ['text'], 'the stock line only')
        self.assertEqual((out['probe'].declined_budget, out['probe'].drawn), (1, 0))


# ---- the Diagnostics switch --------------------------------------------------------------------------------------------
def settings_field(name: str) -> dict:
    schema = json.loads((COOP / 'schema' / 'bridge-abi-v1.json').read_text(encoding='utf-8'))
    for f in schema['structs']['ilc_settings']['fields']:
        if f['name'] == name:
            return f
    raise AssertionError(f'{name} is not in the settings record')


class SwitchTest(unittest.TestCase):
    def test_schema_field_and_default_off(self):
        f = settings_field('hud_test_draw')
        self.assertEqual((f['kind'], f['min'], f['max']), ('VAL', 0, 1))
        schema = json.loads((COOP / 'schema' / 'bridge-abi-v1.json').read_text(encoding='utf-8'))
        defaults = bytes.fromhex(schema['defaults']['hex'])
        self.assertEqual(defaults[f['offset']], 0, 'off by default')

    def test_native_reads_it_only_from_the_players_settings(self):
        src = (HUD_SRC / 'canvas.cpp').read_text(encoding='utf-8')
        self.assertIn('const bool on = false;', src)   # the old test draw stays off: the switch is the overlay's card
        self.assertIn('volatile uint32_t g_test_draw = 0;', src)

    def test_mod_menu_row(self):
        page = (LUA / 'page.lua').read_text(encoding='utf-8')
        self.assertIn("toggle(dev, 'hudTestDraw', 'Overlay test card', {'Off', 'On'},", page)  # Advanced > Developer

    @unittest.skipIf(LuaRuntime is None, 'requires lupa')
    def test_store_round_trip(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        load = lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        store = load((LUA / 'store.lua').read_text(encoding='utf-8'), 'store')
        schema = load((LUA / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        fields = schema.structs['ilc_settings'].fields
        self.assertEqual(fields[store.POSITIONS['settings']['hudTestDraw'] + 1].name, 'hud_test_draw')


@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class SwitchNativeTest(unittest.TestCase):
    def test_generation_zero_never_turns_it_on(self):
        d = world().d
        f = settings_field('hud_test_draw')
        schema = json.loads((COOP / 'schema' / 'bridge-abi-v1.json').read_text(encoding='utf-8'))
        rec = bytearray.fromhex(schema['defaults']['hex'])
        rec[f['offset']] = 1
        struct.pack_into('<I', rec, 12, 0)  # header.generation = 0: the native defaults record
        d.ILCT_HudConfigure(bytes(rec))
        self.assertEqual(d.ILCT_HudTestDraw(), 0)
        struct.pack_into('<I', rec, 12, 3)
        d.ILCT_HudConfigure(bytes(rec))
        self.assertEqual(d.ILCT_HudTestDraw(), 0, 'the old canvas test draw stays off (the switch is the overlay card)')
        rec[f['offset']] = 0
        d.ILCT_HudConfigure(bytes(rec))
        self.assertEqual(d.ILCT_HudTestDraw(), 0)


if __name__ == '__main__':
    unittest.main()
