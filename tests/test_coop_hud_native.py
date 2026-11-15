"""Host tests for the native HUD relayout and toggles (native/coop/src/hud/relayout, circles, native). No game run,
nothing deployed.

* the reduced party list: the stock and reduced fingerprints and the reviewed values of the reduced layout, restated
  as facts and checked on the stock field layout from the game's own pack (ILC_MRP_PACK names an extracted, unmodified
  `mrppack_ys.bin`; without it those classes are skipped), live-relocated into low memory as the game holds it;
* the settle window (no write before the layout has been unchanged for 1.25 s), the reduced layout's guard, apply ->
  restore byte-identical, restore refused (no write) when the layout was reloaded, replaced or changed by someone else,
  foreign layouts (a changed record or panel, the reduced layout we did not write) skipped with zero writes, the
  structure checks (unloaded, invalid records), the busy flag;
* target circles off: the site classes (stock, ours, Vision's forms, foreign), apply and restore by one byte on a
  private code page and on the image twin (byte-identical restore, protections back as they were), Vision's patch
  adopted with zero writes, ownership dropped when someone writes over ours, a failed exchange changes nothing;
* the owner: the job applies both, the UI tick gives both back when not wanted (kill switch / RENDER off / element
  off / DRY), the exit path restores, nothing is pushed while not wanted; the wiring in boot and the UI tick; the
  Target Info window and its Lua scaler's groups are never touched.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import struct
import unittest
from pathlib import Path

import test_coop_patch_engine as pe
from coop_native_support import CAN_BUILD, REQUIRE_NATIVE
from test_coop_patch_engine import GAME, TWIN_BASE, untagged_constants

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
HUD_SRC = COOP / 'src' / 'hud'
PACK = Path(os.environ.get('ILC_MRP_PACK', '__no_mrp_pack__'))
NATIVE = CAN_BUILD or REQUIRE_NATIVE

u8, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
k32 = ctypes.WinDLL('kernel32', use_last_error=True) if os.name == 'nt' else None
if k32 is not None:
    k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, u32, u32]
    k32.VirtualAlloc.restype = ctypes.c_void_p
    k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, u32]
    k32.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, u32, ctypes.POINTER(u32)]
MEM_COMMIT_RESERVE, MEM_RELEASE = 0x3000, 0x8000
PAGE_READWRITE, PAGE_EXECUTE_READ, PAGE_EXECUTE_READWRITE = 0x04, 0x20, 0x40

# ---- game facts (restated) -------------------------------------------------------------------------------------------
MRP_SLOT = 0x209AC68           # MRP section table 0x209ac60, slot 1 (field HUD)
LIVE_MAGIC = 0x0150524D
FIELD_SECTION = 1
STOCK_FP = '24e93218b194875235248869b51c8ca710d297df3f0cf32a8cd204cc2b3dc71c'
REDUCED_FP = '3c2d721e5fc305d28272ce5b61848d79eadd1a5983c56e68c67812d736641a81'
PANEL_STOCK, PANEL_REDUCED = (570, 741, 1800, 304), (950, 741, 1800, 304)
GROUPS = range(40, 52)
FIELDS_STOCK = 110
# The reduced layout's reviewed values (the transform restated as its results).
REDUCED_ROOTS = {40: (122, 242, 900, 44), 49: (317, 245, 900, 60), 50: (78, 244, 408, 48), 51: (470, 236, 1350, 66)}
REDUCED_GAPS = {40: 42, 49: 42, 50: 39}
REDUCED_ACTION_ENTRIES = {0: (183, 20, 140, 17), 3: (119, 0, 94, 34), 4: (220, 17, 113, 25)}
REDUCED_DIGIT_G44_E0 = (61, 4, 23, 32)    # 75 %, then 85 % size and 4 lower
REDUCED_TIME_G48_E0 = (75, 7, 18, 20)
REDUCED_ESPER_NAME = ((384, 11, 150, 24), 61)
REDUCED_ROW_NAME_Y = 3
STOCK_ROOTS = {40: (202, 228, 1200, 58), 49: (462, 232, 1200, 80), 50: (170, 230, 544, 64), 51: (0, 220, 1800, 88)}

CIRCLE_SITE = 0x28C5A0
CIRCLE_STOCK = bytes.fromhex('f6834c0d00000174')
CIRCLE_OURS = bytes.fromhex('f6834c0d00000074')
VISION_FORMS = [bytes.fromhex(h) for h in ('39c0909090909074', '3bc0909090909074', '39c00f1f44000074',
                                           '39c066900f1f0074', '3bc00f1f40009074')]
FOREIGN_FORMS = [bytes.fromhex(h) for h in ('e900000000909074', '9090909090909074', '39c0909090909090',
                                            'f6834c0d00000274', '39c0cccccccccc74', '31c0909090909074')]

# layout_state / restore_result / circles enums
L_UNLOADED, L_INVALID, L_SETTLING, L_REDUCED, L_OTHER, L_FOREIGN, L_FAILED, L_BUSY, L_STOCK = range(1, 10)
R_NOTHING, R_DONE, R_GONE, R_CHANGED, R_FAILED, R_BUSY = range(6)
S_UNREADABLE, S_STOCK, S_OURS, S_VISION, S_FOREIGN = range(5)
C_OFF_OURS, C_ADOPTED, C_ON, C_FOREIGN, C_UNREADABLE, C_WRITE_FAILED = range(1, 7)
CR_NOTHING, CR_DONE, CR_GONE, CR_FAILED = range(4)


class LayoutCounters(ctypes.Structure):
    _fields_ = [(n, u64) for n in ('looks', 'quick', 'applied', 'restored', 'dropped', 'foreign', 'invalid',
                                   'settling', 'raced', 'failed', 'busy')]


class LayoutStatus(ctypes.Structure):
    _fields_ = [('state', u32), ('fields', u32), ('base', u64), ('owned_base', u64), ('fingerprint', u8 * 32)]


class CircleCounters(ctypes.Structure):
    _fields_ = [(n, u64) for n in ('looks', 'writes', 'applied', 'restored', 'adopted', 'foreign', 'write_failed',
                                   'dropped')]


class CircleStatus(ctypes.Structure):
    _fields_ = [('state', u32), ('site', u32), ('owned', u8), ('reserved', u8 * 7), ('bytes', u8 * 8)]


class NativeCounters(ctypes.Structure):
    _fields_ = [(n, u64) for n in ('pushed', 'push_refused', 'jobs_ran', 'give_backs', 'exit_restores')]


class NativeStatus(ctypes.Structure):
    _fields_ = [('attached', u8), ('enabled', u8), ('wanted', u8), ('reserved', u8 * 5), ('layout_state', u32),
                ('circles_state', u32), ('circles_site', u32), ('last_job', i32)]


SIGNATURES = {
    'ILCT_RelayoutSizes': ([ctypes.POINTER(u32), u32], u32), 'ILCT_RelayoutReset': ([], None),
    'ILCT_RelayoutSetSlot': ([u64], None), 'ILCT_RelayoutEnsure': ([u64], u32), 'ILCT_RelayoutRestore': ([], i32),
    'ILCT_RelayoutRestoreWaiting': ([u32], i32), 'ILCT_RelayoutOwned': ([], i32), 'ILCT_RelayoutHold': ([i32], i32),
    'ILCT_RelayoutStatus': ([ctypes.POINTER(LayoutStatus)], None),
    'ILCT_RelayoutStats': ([ctypes.POINTER(LayoutCounters)], None),
    'ILCT_CirclesReset': ([], None), 'ILCT_CirclesSetSite': ([u64], None), 'ILCT_CirclesFailCas': ([i32], None),
    'ILCT_CirclesClassify': ([ctypes.c_char_p], u32), 'ILCT_CirclesEnsure': ([], u32),
    'ILCT_CirclesRestore': ([], i32), 'ILCT_CirclesOwned': ([], i32),
    'ILCT_CirclesStatus': ([ctypes.POINTER(CircleStatus)], None),
    'ILCT_CirclesStats': ([ctypes.POINTER(CircleCounters)], None),
    'ILCT_HudNativeReset': ([], None), 'ILCT_HudNativeSetAttached': ([i32], None),
    'ILCT_HudNativeForceLive': ([i32], None), 'ILCT_HudNativeSetEnabled': ([i32], None),
    'ILCT_HudNativeWanted': ([], i32), 'ILCT_HudNativeRunJob': ([u64], i32), 'ILCT_HudNativeTick': ([i32], None),
    'ILCT_TickRunStale': ([u32], i32),
    'ILCT_HudNativeStep': ([u64], None), 'ILCT_HudNativeRestoreAtExit': ([], None),
    'ILCT_HudNativeStatus': ([ctypes.POINTER(NativeStatus)], None),
    'ILCT_HudNativeStats': ([ctypes.POINTER(NativeCounters)], None),
}

_dll = {}


def shared_dll():
    if 'd' not in _dll:
        d = ctypes.WinDLL(str(pe.built()['test']))
        for name, (args, res) in SIGNATURES.items():
            fn = getattr(d, name)
            fn.argtypes, fn.restype = args, res
        _dll['d'] = d
    return _dll['d']


def alloc_low(size: int, protect: int = PAGE_READWRITE) -> int:
    """Committed memory below 4 GiB (the live layout holds 32-bit absolute pointers)."""
    for hint in range(0x40000000, 0xF0000000, 0x01000000):
        at = k32.VirtualAlloc(hint, size, MEM_COMMIT_RESERVE, protect)
        if at:
            return at
    raise AssertionError('no free address range below 4 GiB')


def free(at: int):
    k32.VirtualFree(at, 0, MEM_RELEASE)


def protect(at: int, size: int, prot: int) -> int:
    old = u32()
    if not k32.VirtualProtect(at, size, prot, ctypes.byref(old)):
        raise AssertionError('VirtualProtect failed')
    return old.value


def query_protect(at: int) -> int:
    class MBI(ctypes.Structure):
        _fields_ = [('base', u64), ('alloc_base', u64), ('alloc_protect', u32), ('pad0', u32), ('size', u64),
                    ('state', u32), ('protect', u32), ('type', u32), ('pad1', u32)]
    mbi = MBI()
    k32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
    k32.VirtualQuery(at, ctypes.byref(mbi), ctypes.sizeof(mbi))
    return mbi.protect


# ---- the field layout as the game holds it ------------------------------------------------------------------------------

def live_section(pack: bytes, base: int) -> bytes:
    """Pack section 1 as it is once loaded at `base`: byte 3 of the magic set, offsets turned into 32-bit absolute
    pointers (group list from the section start, entries from each group record)."""
    start, end = struct.unpack_from('<II', pack, FIELD_SECTION * 4)
    s = bytearray(pack[start:end])
    s[3] = 1
    count, glist = struct.unpack_from('<II', s, 0x1C)
    struct.pack_into('<I', s, 0x20, base + glist)
    for g in range(count):
        rec = glist + g * 0x14
        struct.pack_into('<I', s, rec + 0x10, base + rec + struct.unpack_from('<I', s, rec + 0x10)[0])
    return bytes(s)


def fields_of(mem: bytes, base: int) -> list[tuple]:
    """(group, entry, kind, offset, value) in fingerprint order, read from a live section image."""
    glist = struct.unpack_from('<I', mem, 0x20)[0] - base
    out = []
    for g in GROUPS:
        rec = glist + g * 0x14
        count = mem[rec]
        out.append((g, -1, 'rect', rec + 4, mem[rec + 4:rec + 12]))
        out.append((g, -1, 'gap', rec + 12, mem[rec + 12:rec + 16]))
        e = struct.unpack_from('<I', mem, rec + 0x10)[0] - base
        for k in range(count):
            size, kind = mem[e], mem[e + 1]
            out.append((g, k, 'rect', e + 4, mem[e + 4:e + 12]))
            if kind == 5 and size >= 0x18:
                out.append((g, k, 'scale', e + 0x10, mem[e + 0x10:e + 0x11]))
            e += size
    return out


def fingerprint(mem: bytes, base: int) -> str:
    return hashlib.sha256(b''.join(f[4] for f in fields_of(mem, base))).hexdigest()


def panel_of(mem: bytes, base: int) -> tuple:
    glist = struct.unpack_from('<I', mem, 0x20)[0] - base
    return struct.unpack_from('<hhhh', mem, glist + 21 * 0x14 + 4)


class LiveLayout:
    """A live field layout in low memory and a slot pointing at it."""

    def __init__(self, section: bytes | None = None, pack: bytes | None = None, span: int = 0x10000):
        self.span = span
        self.base = alloc_low(span)
        self.section = live_section(pack, self.base) if pack is not None else section(self.base)
        ctypes.memmove(self.base, self.section, len(self.section))
        self.slot = ctypes.c_uint64(self.base)

    @property
    def slot_address(self) -> int:
        return ctypes.addressof(self.slot)

    def image(self) -> bytes:
        return ctypes.string_at(self.base, len(self.section))

    def poke(self, offset: int, data: bytes):
        ctypes.memmove(self.base + offset, data, len(data))

    def group(self, g: int) -> int:
        return struct.unpack_from('<I', self.image(), 0x20)[0] - self.base + g * 0x14

    def rect(self, offset: int) -> tuple:
        return struct.unpack_from('<hhhh', self.image(), offset)

    def entry(self, g: int, k: int) -> int:
        mem = self.image()
        e = struct.unpack_from('<I', mem, self.group(g) + 0x10)[0] - self.base
        for _ in range(k):
            e += mem[e]
        return e

    def close(self):
        free(self.base)


def synthetic_section(base: int) -> bytes:
    """52 groups, one 0x18-byte text entry each (not the stock layout: a fingerprint nobody reviewed)."""
    glist = 0x30
    entries = glist + 52 * 0x14
    s = bytearray(entries + 52 * 0x18)
    struct.pack_into('<IIII', s, 0, LIVE_MAGIC, 0, 0, 0)
    struct.pack_into('<II', s, 0x1C, 52, base + glist)
    for g in range(52):
        rec = glist + g * 0x14
        e = entries + g * 0x18
        s[rec] = 1
        s[rec + 1] = (g << 2) & 0xFF
        struct.pack_into('<hhhhiI', s, rec + 4, 10 * g, 20, 300, 40, 0, base + e)
        s[e], s[e + 1] = 0x18, 5
        struct.pack_into('<hhhh', s, e + 4, 1, 2, 100, 20)
        s[e + 0x10] = 100
    struct.pack_into('<hhhh', s, glist + 21 * 0x14 + 4, *PANEL_STOCK)
    return bytes(s)


# ---- static -----------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_facts_restated(self):
        facts = (HUD_SRC / 'native_facts.h').read_text(encoding='utf-8')
        self.assertIn(f'FINGERPRINT_STOCK[] = "{STOCK_FP}"', facts)
        self.assertIn(f'FINGERPRINT_REDUCED[] = "{REDUCED_FP}"', facts)
        self.assertIn('MRP_FIELD_SLOT = 0x209AC68;', facts)
        self.assertIn('PANEL_STOCK[4] = {570, 741, 1800, 304}', facts)
        self.assertIn('PANEL_REDUCED[4] = {950, 741, 1800, 304}', facts)
        self.assertIn('CIRCLE_SITE = 0x28C5A0;', facts)
        self.assertIn('CIRCLE_STOCK[8] = {0xF6, 0x83, 0x4C, 0x0D, 0x00, 0x00, 0x01, 0x74}', facts)

    def test_address_constants_tagged(self):
        bad = []
        for path in sorted(HUD_SRC.glob('*')):
            if path.name not in ('native_facts.h', 'relayout.h', 'relayout.cpp', 'circles.h', 'circles.cpp',
                                 'native.h', 'native.cpp'):
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                bad += [f'{path.name}:{number}: {c}' for c in untagged_constants(line)]
        self.assertEqual(bad, [])

    def test_wiring(self):
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        tick = (COOP / 'src' / 'runtime' / 'tick.cpp').read_text(encoding='utf-8')
        for call in ('hud::native::attach();', 'hud::native::step(now);', 'hud::native::stop();',
                     'hud::native::restore_at_exit();'):
            self.assertEqual(boot.count(call), 1, call)
        # attach before the operation table is sealed; the exit restore after the co-op thread was joined
        self.assertLess(boot.index('hud::native::attach();'), boot.index('gtq::seal();'))
        self.assertLess(boot.index('EV_THREAD_JOINED'), boot.index('hud::native::restore_at_exit();'))
        # the UI tick gives back in every mode: after the LIVE/DRY branch, inside the re-entry flag
        give = tick.index('hud::native::tick(patch::current_mode() == patch::MODE_LIVE);')
        self.assertLess(tick.index('gtq::flush(gtq::REJECT_DISARMED)'), give)
        self.assertLess(give, tick.index('__atomic_store_n(&g_in_tick, 0u'))
        build = (COOP / 'build.py').read_text(encoding='utf-8')
        self.assertIn("'src/hud/relayout.cpp', 'src/hud/circles.cpp', 'src/hud/native.cpp'", build)

    def test_operation_number_unique_and_render(self):
        numbers = {}
        for path in (COOP / 'src').rglob('*.h'):
            for m in re.finditer(r'constexpr uint16_t (OP_[A-Z_]+) = (\d+);', path.read_text(encoding='utf-8')):
                numbers.setdefault(int(m.group(2)), []).append(m.group(1))
        self.assertEqual(numbers.get(24), ['OP_HUD_NATIVE'])
        self.assertEqual({n: v for n, v in numbers.items() if len(v) > 1}, {})
        native = (HUD_SRC / 'native.cpp').read_text(encoding='utf-8')
        self.assertIn('gtq::register_op(OP_HUD_NATIVE, ILC_SITE_SUBSYS_RENDER, op_native)', native)

    def test_target_info_and_its_lua_scaler_untouched(self):
        # The relayout owns groups 21 and 40..51; the Lua TI scaler owns 22..29. Nothing here reads the TI window.
        facts = (HUD_SRC / 'native_facts.h').read_text(encoding='utf-8')
        self.assertIn('GROUP_FIRST = 40;', facts)
        self.assertIn('GROUP_LAST = 51;', facts)
        self.assertIn('GROUP_PANEL = 21;', facts)
        for name in ('relayout.cpp', 'circles.cpp', 'native.cpp'):
            code = (HUD_SRC / name).read_text(encoding='utf-8')
            for fact in ('UI_MANAGER', 'UI_TARGET_INFO', 'WINDOW_FLAGS', 'WINDOW_GROUP'):
                self.assertNotIn(fact, code, f'{name} uses {fact}')
        lua = ROOT / 'prototypes' / 'target_info_scale.lua'
        if lua.is_file():
            text = lua.read_text(encoding='utf-8')
            self.assertIn('M.ROOT_SIGNATURE = {count = 19, index = 22, w = 1400, h = 320}', text)


# ---- the reduced party list on the stock layout ----------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
@unittest.skipUnless(PACK.is_file(), 'set ILC_MRP_PACK to an extracted, unmodified mrppack_ys.bin')
class StockLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = shared_dll()
        cls.pack = PACK.read_bytes()

    def setUp(self):
        self.d.ILCT_RelayoutReset()
        self.live = LiveLayout(pack=self.pack)
        self.d.ILCT_RelayoutSetSlot(self.live.slot_address)
        self.stock = self.live.image()

    def tearDown(self):
        self.d.ILCT_RelayoutReset()
        self.d.ILCT_RelayoutSetSlot(0)
        self.live.close()

    def status(self) -> LayoutStatus:
        s = LayoutStatus()
        self.d.ILCT_RelayoutStatus(ctypes.byref(s))
        return s

    def stats(self) -> LayoutCounters:
        c = LayoutCounters()
        self.d.ILCT_RelayoutStats(ctypes.byref(c))
        return c

    def reduce(self):
        self.assertEqual(self.d.ILCT_RelayoutEnsure(10_000), L_SETTLING)
        self.assertEqual(self.d.ILCT_RelayoutEnsure(11_250), L_REDUCED)

    def test_stock_fingerprint_and_fields(self):
        self.assertEqual(hashlib.sha256(self.pack).hexdigest()[:8], 'ceec522d')
        self.assertEqual(len(fields_of(self.stock, self.live.base)), FIELDS_STOCK)
        self.assertEqual(fingerprint(self.stock, self.live.base), STOCK_FP)
        self.assertEqual(panel_of(self.stock, self.live.base), PANEL_STOCK)
        for g, rect in STOCK_ROOTS.items():
            self.assertEqual(self.live.rect(self.live.group(g) + 4), rect)
        self.assertEqual(self.d.ILCT_RelayoutEnsure(1), L_SETTLING)
        s = self.status()
        self.assertEqual((s.fields, s.base, bytes(s.fingerprint).hex()), (FIELDS_STOCK, self.live.base, STOCK_FP))

    def test_settle_window_then_reviewed_reduced_layout(self):
        for now in (10_000, 10_500, 11_000, 11_249):
            self.assertEqual(self.d.ILCT_RelayoutEnsure(now), L_SETTLING)
            self.assertEqual(self.live.image(), self.stock, f'written before the settle window ({now})')
        self.assertEqual(self.d.ILCT_RelayoutEnsure(11_250), L_REDUCED)
        mem = self.live.image()
        self.assertEqual(fingerprint(mem, self.live.base), REDUCED_FP)
        self.assertEqual(panel_of(mem, self.live.base), PANEL_REDUCED)
        for g, rect in REDUCED_ROOTS.items():
            self.assertEqual(self.live.rect(self.live.group(g) + 4), rect, f'G{g}')
        for g, gap in REDUCED_GAPS.items():
            self.assertEqual(struct.unpack_from('<i', mem, self.live.group(g) + 12)[0], gap, f'G{g} gap')
        for k, rect in REDUCED_ACTION_ENTRIES.items():
            self.assertEqual(self.live.rect(self.live.entry(50, k) + 4), rect, f'G50 E{k}')
        self.assertEqual(self.live.rect(self.live.entry(44, 0) + 4), REDUCED_DIGIT_G44_E0)
        self.assertEqual(self.live.rect(self.live.entry(48, 0) + 4), REDUCED_TIME_G48_E0)
        esper = self.live.entry(47, 0)
        self.assertEqual((self.live.rect(esper + 4), mem[esper + 0x10]), REDUCED_ESPER_NAME)
        self.assertEqual(self.live.rect(self.live.entry(40, 19) + 4)[1], REDUCED_ROW_NAME_Y)
        # only the owned fields and the panel changed
        allowed = {f[3] + k for f in fields_of(self.stock, self.live.base) for k in range(len(f[4]))}
        glist = struct.unpack_from('<I', self.stock, 0x20)[0] - self.live.base
        allowed |= {glist + 21 * 0x14 + 4 + k for k in range(8)}
        changed = {i for i, (a, b) in enumerate(zip(self.stock, mem)) if a != b}
        self.assertTrue(changed and changed <= allowed, sorted(changed - allowed)[:8])
        s = self.status()
        self.assertEqual((s.state, s.owned_base, bytes(s.fingerprint).hex()), (L_REDUCED, self.live.base, REDUCED_FP))
        self.assertEqual(self.stats().applied, 1)

    def test_reduced_layout_is_idempotent_through_the_guard(self):
        self.reduce()
        image = self.live.image()
        looks = self.stats().looks
        for now in (12_000, 12_500, 13_000):
            self.assertEqual(self.d.ILCT_RelayoutEnsure(now), L_REDUCED)
        self.assertEqual(self.live.image(), image)
        c = self.stats()
        self.assertEqual((c.quick, c.looks, c.applied), (3, looks, 1))

    def test_restore_is_byte_identical(self):
        self.reduce()
        self.assertEqual(self.d.ILCT_RelayoutOwned(), 1)
        self.assertEqual(self.d.ILCT_RelayoutRestore(), R_DONE)
        self.assertEqual(self.live.image(), self.stock)
        self.assertEqual(self.d.ILCT_RelayoutOwned(), 0)
        self.assertEqual(self.status().state, L_STOCK)
        self.assertEqual(self.d.ILCT_RelayoutRestore(), R_NOTHING)
        # wanted again: a fresh settle, then the same reduced bytes
        self.assertEqual(self.d.ILCT_RelayoutEnsure(20_000), L_SETTLING)
        self.assertEqual(self.d.ILCT_RelayoutEnsure(21_250), L_REDUCED)
        self.assertEqual(fingerprint(self.live.image(), self.live.base), REDUCED_FP)
        self.assertEqual(self.d.ILCT_RelayoutRestoreWaiting(5), R_DONE)
        self.assertEqual(self.live.image(), self.stock)

    def test_reloaded_in_place_is_not_restored_and_is_reduced_again(self):
        self.reduce()
        self.live.poke(0, self.stock)  # the game reloads the layout at the same address
        self.assertEqual(self.d.ILCT_RelayoutRestore(), R_GONE)
        self.assertEqual(self.live.image(), self.stock)
        self.reduce()
        self.live.poke(0, self.stock)
        self.assertEqual(self.d.ILCT_RelayoutEnsure(30_000), L_SETTLING)  # guard broken: a full look, settle again
        self.assertEqual(self.d.ILCT_RelayoutEnsure(31_250), L_REDUCED)
        self.assertEqual(self.d.ILCT_RelayoutRestore(), R_DONE)
        self.assertEqual(self.live.image(), self.stock)

    def test_replaced_allocation_is_not_written(self):
        self.reduce()
        reduced = self.live.image()
        other = LiveLayout(pack=self.pack)
        try:
            self.live.slot.value = other.base
            self.assertEqual(self.d.ILCT_RelayoutRestore(), R_GONE)
            self.assertEqual(self.live.image(), reduced, 'the old allocation was written')
            self.assertEqual(other.image(), live_section(self.pack, other.base))
            self.assertEqual(self.d.ILCT_RelayoutOwned(), 0)
        finally:
            self.live.slot.value = self.live.base
            other.close()

    def test_changed_by_someone_else_after_apply_is_left_alone(self):
        self.reduce()
        row = self.live.group(40) + 4
        self.live.poke(row, struct.pack('<h', 123))
        changed = self.live.image()
        self.assertEqual(self.d.ILCT_RelayoutRestore(), R_CHANGED)
        self.assertEqual(self.live.image(), changed)
        self.assertEqual(self.d.ILCT_RelayoutEnsure(40_000), L_FOREIGN)
        self.assertEqual(self.live.image(), changed)

    def test_foreign_layout_skipped_with_zero_writes(self):
        cases = {
            'G40 x': (self.live.group(40) + 4, struct.pack('<h', 203)),
            'G50 text scale': (self.live.entry(50, 1) + 0x10, b'\x51'),  # stock 80
            'G21 panel x': (self.live.group(21) + 4, struct.pack('<h', 571)),
        }
        for name, (offset, data) in cases.items():
            with self.subTest(name):
                self.live.poke(0, self.stock)
                self.d.ILCT_RelayoutReset()
                self.live.poke(offset, data)
                before = self.live.image()
                for now in (1, 5_000, 10_000):
                    self.assertEqual(self.d.ILCT_RelayoutEnsure(now), L_FOREIGN)
                self.assertEqual(self.live.image(), before)
                self.assertGreaterEqual(self.stats().foreign, 3)
                self.assertEqual(self.d.ILCT_RelayoutRestore(), R_NOTHING)

    def test_reduced_layout_we_did_not_write_is_left_alone(self):
        self.reduce()
        reduced = self.live.image()
        self.d.ILCT_RelayoutReset()  # a module instance that never wrote it
        self.assertEqual(self.d.ILCT_RelayoutEnsure(50_000), L_OTHER)
        self.assertEqual(self.d.ILCT_RelayoutRestore(), R_NOTHING)
        self.assertEqual(self.live.image(), reduced)

    def test_unloaded_and_invalid_records(self):
        self.live.slot.value = 0
        self.assertEqual(self.d.ILCT_RelayoutEnsure(1), L_UNLOADED)
        self.live.slot.value = self.live.base
        self.live.poke(3, b'\x00')  # the on-disk magic: not live yet
        self.assertEqual(self.d.ILCT_RelayoutEnsure(2), L_UNLOADED)
        self.live.poke(0, self.stock)
        self.live.poke(self.live.entry(40, 0), b'\x08')  # entry shorter than its header
        self.assertEqual(self.d.ILCT_RelayoutEnsure(3), L_INVALID)
        self.live.poke(0, self.stock)
        self.live.poke(self.live.group(45) + 0x10, struct.pack('<I', self.live.base + 0x20000))  # outside the section
        self.assertEqual(self.d.ILCT_RelayoutEnsure(4), L_INVALID)
        self.live.poke(0, self.stock)
        self.live.poke(0x1C, struct.pack('<I', 40))  # too few groups
        self.assertEqual(self.d.ILCT_RelayoutEnsure(5), L_INVALID)
        self.assertEqual(self.live.image()[0x20:], self.stock[0x20:])

    def test_busy_state(self):
        self.assertEqual(self.d.ILCT_RelayoutHold(1), 1)
        try:
            self.assertEqual(self.d.ILCT_RelayoutEnsure(1), L_BUSY)
            self.assertEqual(self.d.ILCT_RelayoutRestore(), R_BUSY)
            self.assertEqual(self.d.ILCT_RelayoutRestoreWaiting(3), R_BUSY)
        finally:
            self.d.ILCT_RelayoutHold(0)
        self.assertEqual(self.live.image(), self.stock)


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class SyntheticLayoutTest(unittest.TestCase):
    """Without the pack: a layout that is not the stock one is never written."""

    def setUp(self):
        self.d = shared_dll()
        self.d.ILCT_RelayoutReset()
        self.live = LiveLayout(section=synthetic_section)
        self.d.ILCT_RelayoutSetSlot(self.live.slot_address)

    def tearDown(self):
        self.d.ILCT_RelayoutReset()
        self.d.ILCT_RelayoutSetSlot(0)
        self.live.close()

    def test_unknown_layout_is_foreign_and_never_written(self):
        before = self.live.image()
        for now in (1, 2_000, 4_000):
            self.assertEqual(self.d.ILCT_RelayoutEnsure(now), L_FOREIGN)
        self.assertEqual(self.live.image(), before)
        s = LayoutStatus()
        self.d.ILCT_RelayoutStatus(ctypes.byref(s))
        self.assertEqual((s.fields, bytes(s.fingerprint).hex()), (12 * 4, fingerprint(before, self.live.base)))

    def test_struct_mirrors(self):
        out = (u32 * 6)()
        self.assertEqual(self.d.ILCT_RelayoutSizes(out, 6), 6)
        self.assertEqual(list(out), [ctypes.sizeof(t) for t in (LayoutCounters, LayoutStatus, CircleCounters,
                                                               CircleStatus, NativeCounters, NativeStatus)])


# ---- target circles ---------------------------------------------------------------------------------------------------
class CodePage:
    """An 8-byte window at a 16-aligned address of a private PAGE_EXECUTE_READ page."""

    def __init__(self, window: bytes):
        self.page = alloc_low(0x1000, PAGE_READWRITE)
        self.site = self.page + 0x5A0
        ctypes.memmove(self.site - 0x10, b'\xCC' * 0x30, 0x30)
        self.write(window)

    def write(self, window: bytes):
        protect(self.page, 0x1000, PAGE_READWRITE)
        ctypes.memmove(self.site, window, 8)
        protect(self.page, 0x1000, PAGE_EXECUTE_READ)

    def window(self) -> bytes:
        return ctypes.string_at(self.site, 8)

    def surroundings(self) -> bytes:
        return ctypes.string_at(self.site - 0x10, 0x10) + ctypes.string_at(self.site + 8, 0x18)

    def close(self):
        free(self.page)


@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class CircleTest(unittest.TestCase):
    def setUp(self):
        self.d = shared_dll()
        self.d.ILCT_CirclesReset()
        self.code = CodePage(CIRCLE_STOCK)
        self.d.ILCT_CirclesSetSite(self.code.site)

    def tearDown(self):
        self.d.ILCT_CirclesReset()
        self.d.ILCT_CirclesSetSite(0)
        self.code.close()

    def stats(self) -> CircleCounters:
        c = CircleCounters()
        self.d.ILCT_CirclesStats(ctypes.byref(c))
        return c

    def test_classes(self):
        self.assertEqual(self.d.ILCT_CirclesClassify(CIRCLE_STOCK), S_STOCK)
        self.assertEqual(self.d.ILCT_CirclesClassify(CIRCLE_OURS), S_OURS)
        for form in VISION_FORMS:
            self.assertEqual(self.d.ILCT_CirclesClassify(form), S_VISION, form.hex())
        for form in FOREIGN_FORMS:
            self.assertEqual(self.d.ILCT_CirclesClassify(form), S_FOREIGN, form.hex())

    def test_ours_differs_from_stock_in_the_immediate_only(self):
        self.assertEqual([i for i in range(8) if CIRCLE_STOCK[i] != CIRCLE_OURS[i]], [6])

    def test_apply_and_restore_one_byte(self):
        around = self.code.surroundings()
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_OFF_OURS)
        self.assertEqual(self.code.window(), CIRCLE_OURS)
        self.assertEqual(query_protect(self.code.site), PAGE_EXECUTE_READ)
        self.assertEqual(self.d.ILCT_CirclesOwned(), 1)
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_OFF_OURS)  # idempotent, no second write
        self.assertEqual(self.stats().writes, 1)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_DONE)
        self.assertEqual(self.code.window(), CIRCLE_STOCK)
        self.assertEqual(self.code.surroundings(), around)
        self.assertEqual(query_protect(self.code.site), PAGE_EXECUTE_READ)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_NOTHING)

    def test_vision_patch_adopted_with_zero_writes(self):
        for form in VISION_FORMS:
            with self.subTest(form.hex()):
                self.d.ILCT_CirclesReset()
                self.code.write(form)
                self.assertEqual(self.d.ILCT_CirclesEnsure(), C_ADOPTED)
                self.assertEqual(self.d.ILCT_CirclesRestore(), CR_NOTHING)
                self.assertEqual(self.code.window(), form)
                self.assertEqual(self.stats().writes, 0)
                s = CircleStatus()
                self.d.ILCT_CirclesStatus(ctypes.byref(s))
                self.assertEqual((s.site, s.owned, bytes(s.bytes)), (S_VISION, 0, form))

    def test_vision_over_ours_then_vision_off(self):
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_OFF_OURS)
        self.code.write(VISION_FORMS[0])   # Vision turns its option on over ours
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_ADOPTED)
        self.assertEqual(self.d.ILCT_CirclesOwned(), 0)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_NOTHING)
        self.assertEqual(self.code.window(), VISION_FORMS[0])
        self.code.write(CIRCLE_STOCK)      # Vision turns it off: its original bytes
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_OFF_OURS)
        self.assertEqual(self.code.window(), CIRCLE_OURS)

    def test_vision_over_ours_before_a_restore(self):
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_OFF_OURS)
        self.code.write(VISION_FORMS[2])
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_GONE)
        self.assertEqual(self.code.window(), VISION_FORMS[2])

    def test_our_form_not_written_by_us_is_adopted(self):
        self.code.write(CIRCLE_OURS)
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_ADOPTED)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_NOTHING)
        self.assertEqual(self.code.window(), CIRCLE_OURS)

    def test_foreign_bytes_skipped_with_zero_writes(self):
        for form in FOREIGN_FORMS:
            with self.subTest(form.hex()):
                self.d.ILCT_CirclesReset()
                self.code.write(form)
                self.assertEqual(self.d.ILCT_CirclesEnsure(), C_FOREIGN)
                self.assertEqual(self.code.window(), form)
                self.assertEqual(self.stats().writes, 0)

    def test_failed_exchange_changes_nothing(self):
        self.d.ILCT_CirclesFailCas(1)
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_WRITE_FAILED)
        self.assertEqual(self.code.window(), CIRCLE_STOCK)
        self.assertEqual(self.d.ILCT_CirclesOwned(), 0)
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_OFF_OURS)
        self.d.ILCT_CirclesFailCas(1)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_FAILED)
        self.assertEqual(self.code.window(), CIRCLE_OURS)
        self.assertEqual(self.d.ILCT_CirclesOwned(), 1)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_DONE)
        self.assertEqual(self.code.window(), CIRCLE_STOCK)


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (the image twin)')
class CircleTwinTest(unittest.TestCase):
    """The game's own site on the image twin (the exe mapped at 0x120000)."""

    @classmethod
    def setUpClass(cls):
        cls.twin = pe.Twin.get()
        cls.d = shared_dll()

    def setUp(self):
        self.twin.reset()
        self.d.ILCT_CirclesReset()
        self.d.ILCT_CirclesSetSite(0)
        self.image = self.twin.image()
        self.protections = pe.regions(TWIN_BASE, TWIN_BASE + pe.TWIN_SIZE)

    def tearDown(self):
        self.d.ILCT_CirclesReset()
        self.twin.reset()

    def test_stock_site_and_the_area_ring_path_untouched(self):
        self.assertEqual(ctypes.string_at(CIRCLE_SITE, 8), CIRCLE_STOCK)
        # the area list draw (0x3f3260, called at 0x28ed34) and the area node factory (0x2911d0) are elsewhere
        self.assertFalse(CIRCLE_SITE <= 0x28ED34 < CIRCLE_SITE + 8)
        self.assertNotIn(CIRCLE_SITE, range(0x3F3260, 0x3F3268))

    def test_apply_restore_byte_identical(self):
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_OFF_OURS)
        after = self.twin.image()
        diff = [TWIN_BASE + i for i, (a, b) in enumerate(zip(self.image, after)) if a != b]
        self.assertEqual(diff, [CIRCLE_SITE + 6])
        self.assertEqual(pe.regions(TWIN_BASE, TWIN_BASE + pe.TWIN_SIZE), self.protections)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_DONE)
        self.assertEqual(self.twin.image(), self.image)
        self.assertEqual(pe.regions(TWIN_BASE, TWIN_BASE + pe.TWIN_SIZE), self.protections)

    def test_vision_patched_site_adopted_zero_writes(self):
        self.twin.poke(CIRCLE_SITE, VISION_FORMS[0][:7])
        patched = self.twin.image()
        self.assertEqual(self.d.ILCT_CirclesEnsure(), C_ADOPTED)
        self.assertEqual(self.d.ILCT_CirclesRestore(), CR_NOTHING)
        self.assertEqual(self.twin.image(), patched)
        c = CircleCounters()
        self.d.ILCT_CirclesStats(ctypes.byref(c))
        self.assertEqual(c.writes, 0)


# ---- the owner ----------------------------------------------------------------------------------------------------------
@unittest.skipUnless(NATIVE, 'requires the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1 makes this a failure)')
class OwnerTest(unittest.TestCase):
    def setUp(self):
        self.d = shared_dll()
        for reset in ('ILCT_RelayoutReset', 'ILCT_CirclesReset', 'ILCT_HudNativeReset'):
            getattr(self.d, reset)()
        self.d.ILCT_HudNativeSetAttached(1)
        self.code = CodePage(CIRCLE_STOCK)
        self.d.ILCT_CirclesSetSite(self.code.site)
        self.live = LiveLayout(pack=PACK.read_bytes()) if PACK.is_file() else None
        if self.live is not None:
            self.d.ILCT_RelayoutSetSlot(self.live.slot_address)
            self.stock = self.live.image()
        else:
            self.d.ILCT_RelayoutSetSlot(self.code.page + 0x800)  # reads zero: unloaded

    def tearDown(self):
        for reset in ('ILCT_RelayoutReset', 'ILCT_CirclesReset', 'ILCT_HudNativeReset'):
            getattr(self.d, reset)()
        self.d.ILCT_HudNativeSetAttached(0)
        self.d.ILCT_RelayoutSetSlot(0)
        self.d.ILCT_CirclesSetSite(0)
        self.code.close()
        if self.live is not None:
            self.live.close()

    def stats(self) -> NativeCounters:
        c = NativeCounters()
        self.d.ILCT_HudNativeStats(ctypes.byref(c))
        return c

    def apply(self):
        self.d.ILCT_HudNativeForceLive(1)
        self.assertEqual(self.d.ILCT_HudNativeRunJob(10_000), 1)
        self.assertEqual(self.d.ILCT_HudNativeRunJob(11_250), 1)
        self.assertEqual(self.code.window(), CIRCLE_OURS)
        if self.live is not None:
            self.assertEqual(fingerprint(self.live.image(), self.live.base), REDUCED_FP)

    def assert_stock(self):
        self.assertEqual(self.code.window(), CIRCLE_STOCK)
        if self.live is not None:
            self.assertEqual(self.live.image(), self.stock)

    def test_wanted_tick_keeps_both(self):
        self.apply()
        for _ in range(3):
            self.d.ILCT_HudNativeTick(1)
        self.assertEqual(self.code.window(), CIRCLE_OURS)
        self.assertEqual(self.stats().give_backs, 0)
        s = NativeStatus()
        self.d.ILCT_HudNativeStatus(ctypes.byref(s))
        self.assertEqual((s.attached, s.enabled, s.wanted, s.circles_state, s.circles_site),
                         (1, 1, 1, C_OFF_OURS, S_OURS))
        if self.live is not None:
            self.assertEqual(s.layout_state, L_REDUCED)

    def test_kill_switch_or_render_off_gives_both_back_on_the_next_tick(self):
        self.apply()
        self.d.ILCT_HudNativeForceLive(0)   # F8 clears every subsystem bit; the RENDER switch clears this one
        self.assertEqual(self.d.ILCT_HudNativeWanted(), 0)
        self.d.ILCT_HudNativeTick(1)
        self.assert_stock()
        self.d.ILCT_HudNativeTick(1)        # nothing left: no second give-back
        self.assertEqual(self.stats().give_backs, 1)

    def test_a_stalled_co_op_thread_gives_both_back(self):
        # UT-01's stale-liveness handler (the co-op thread stopped beating for over 2 s) gives the stock party list
        # and circles back, as it gives back a Wait pause; the co-op thread applies them again once it beats.
        self.apply()
        self.assertEqual(self.d.ILCT_TickRunStale(1), 0, 'RUN_ORIGINAL: the stock tick runs')
        self.assert_stock()
        self.assertEqual(self.stats().give_backs, 1)

    def test_element_switch_off_gives_both_back(self):
        self.apply()
        self.d.ILCT_HudNativeSetEnabled(0)
        self.d.ILCT_HudNativeTick(1)
        self.assert_stock()

    def test_dry_mode_gives_both_back(self):
        self.apply()
        self.d.ILCT_HudNativeTick(0)
        self.assert_stock()

    def test_exit_restores_both(self):
        self.apply()
        self.d.ILCT_HudNativeRestoreAtExit()
        self.assert_stock()
        self.assertEqual(self.stats().exit_restores, 1)
        self.d.ILCT_HudNativeRestoreAtExit()
        self.assertEqual(self.stats().exit_restores, 1)

    def test_nothing_pushed_while_not_wanted(self):
        self.d.ILCT_HudNativeForceLive(0)
        for now in (1_000, 2_000, 3_000):
            self.d.ILCT_HudNativeStep(now)
        c = self.stats()
        self.assertEqual((c.pushed, c.push_refused), (0, 0))
        self.assert_stock()

    def test_not_attached_does_nothing(self):
        self.apply()
        self.d.ILCT_HudNativeSetAttached(0)
        self.d.ILCT_HudNativeForceLive(0)
        self.d.ILCT_HudNativeTick(1)
        self.assertEqual(self.code.window(), CIRCLE_OURS)
        self.d.ILCT_HudNativeSetAttached(1)
        self.d.ILCT_HudNativeTick(1)
        self.assert_stock()


if __name__ == '__main__':
    unittest.main()
