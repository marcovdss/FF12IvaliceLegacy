"""Host tests for what a co-op battle-menu entry costs and says (native/coop/src/menu/cost.*, help.*, the menu view's
help line, package/.../coop/economy.lua). No game run, nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a
copy whose SHA-256 is the sites.inc pin).

* the game's own cost code run in unicorn on the exe: 0x309110 (MP), 0x2F95D0 (augment-adjusted cost), 0x309060 (gil)
  and 0x308FF0 (Mist charges), called by a small driver loop for every base cost 0..255, over every set of augments
  {32 Offering, 36 Parsimony, 51 Spellspring, 91..93 Channeling} x menu kinds x Quickening/Esper/plain ids x record
  sides x where the augments sit (timed, lasting, both) x Channeling values; the module's restatement equals it row for
  row;
* the admission check 0x303BD0 in unicorn (its first check, about held content, answered 0) against the module's
  verdict on cost over MP, Mist charges, the party's gil and the gil put aside; the catalog greys exactly what that
  verdict refuses, with the reason;
* Legacy's MP economy against prototypes/mp_economy.lua run through lupa (surcharge, total, paid; versions 0.1 and
  0.2; several DIVISOR/CAP pairs), and how a catalog entry shows it;
* help lines: the formatting tokens, fitting into the help strip, the game's help walk on synthetic memory (bank,
  loaded flag, bounds), the view's choice between the game's help and its own lines; with ILC_HELP_ACTION (Legacy's
  help_action.bin) and the battle pack, every Legacy item and menu action resolves to its help;
* coop/economy.lua: the parameters Legacy's file gives, and nothing for a missing or unusable file.
"""
from __future__ import annotations

import ctypes
import hashlib
import itertools
import os
import struct
import unittest
from pathlib import Path

import test_coop_menu as tm
from coop_native_support import native_test, quiet_faulthandler
from coop_session_support import Image
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import GAME

try:
    import unicorn as U
    from unicorn import x86_const as X
except ImportError:  # pragma: no cover
    U = None
try:
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs
except ImportError:  # pragma: no cover
    Cs = None
try:
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
ECONOMY_LUA = ROOT / 'prototypes' / 'mp_economy.lua'
COOP_LUA = ROOT / 'package' / 'x64' / 'scripts' / 'IvaliceLegacy' / 'coop'
ENTRY_LUA = ROOT / 'package' / 'x64' / 'scripts' / 'IvaliceLegacyCoop.lua'
BATTLE_PACK = os.environ.get('ILC_BATTLE_PACK')
BATTLE_PACK_DIR = os.environ.get('ILC_BATTLE_PACK_DIR')
HELP_ACTION = os.environ.get('ILC_HELP_ACTION')
EXE_SHA256 = '73461d0896204b1c259c52f61aef4efbc95a2ffc388cadb36493d5d1d89721f8'

u8, u16, u32, u64, i32 = ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int32
P = ctypes.POINTER
Economy = tm.S('Economy', [('present', u8), ('major', u8), ('minor', u8), ('patch', u8), ('divisor_x100', u16),
                           ('cap_x100', u16)])
I256 = i32 * 256

SW_MP, SW_HELPER, SW_GIL, SW_CHARGES, SW_SHOWN, SW_SURCHARGE, SW_PAID = range(7)
COST_OK, COST_MP, COST_CHARGES, COST_GIL, COST_UNKNOWN = 0, 0x13, 0x14, 0x15, -1
CF_SURCHARGE, CF_GIL, CF_CHARGES, CF_GIL_SHORT, CF_CHARGES_SHORT = 0x01, 0x02, 0x04, 0x08, 0x10
OFFERING, PARSIMONY, SPELLSPRING = 32, 36, 51
AUGMENTS = (32, 36, 51, 91, 92, 93)
ALL_SETS = [tuple(a for k, a in enumerate(AUGMENTS) if mask >> k & 1) for mask in range(64)]

# The game's functions (VAs of the pinned build).
FN_MP, FN_HELPER, FN_GIL, FN_CHARGES, FN_ADMISSION, FN_HELD_CONTENT = (0x309110, 0x2F95D0, 0x309060, 0x308FF0,
                                                                      0x303BD0, 0x3037A0)
ACTION_TABLE_PTR, AUGMENT_TABLE_PTR, LOCATION_PTR = 0x2EBF138, 0x2EBF040, 0x2092758   # VA
HELP_BANKS, HELP_LOADED = 0x2F973C0, 0x2EC3D80   # VA
ACTOR_RECORD, ACTOR_GIL_RESERVED = 0x698, 0xE58
REC_SIDE, REC_MP, REC_CHARGES, REC_AUG_TIMED, REC_AUG_LASTING = 0x05, 0x4C, 0x4E, 0x68, 0x78
PLAIN, QUICKENING, ESPER = 0x00, 0xD3, 0x107

SIGNATURES = {
    'ILCT_CostSweep': ([u32, u32, u32, P(tm.Seat), P(I256)], None),
    'ILCT_CostAdmissionSweep': ([u32, u32, P(tm.Seat), i32, P(I256)], None),
    'ILCT_CostOne': ([u32, u32, P(tm.Seat)], i32),
    'ILCT_CostAdmission': ([u32, P(tm.Seat), i32], i32),
    'ILCT_CostSetEconomy': ([P(Economy)], None),
    'ILCT_CostEconomy': ([P(Economy)], None),
    'ILCT_CostEconomyTotal': ([i32, u32, P(Economy)], i32),
    'ILCT_CostSurchargeSweep': ([u32, P(tm.Seat), P(Economy), P(I256)], None),
    'ILCT_CostPriceEntry': ([P(tm.Shared), P(tm.Seat), P(tm.Entry)], None),
    'ILCT_HelpStrip': ([ctypes.c_char_p, u32, ctypes.c_char_p, u32], u32),
    'ILCT_HelpFit': ([ctypes.c_char_p, ctypes.c_char_p, u32], u32),
    'ILCT_HelpLive': ([u64, u32, ctypes.c_char_p, u32, P(u64)], u32),
    'ILCT_HelpId': ([u64, u32, P(u32)], i32),
    'ILCT_HelpAt': ([u64, u32], u64),
    'ILCT_HelpSeatLine': ([u32, u32, ctypes.c_char_p, u32], None),
    'ILCT_HelpViewLine': ([u64, u32, ctypes.c_char_p, u32], i32),
}


def declare(d):
    tm.declare(d)
    for name, (args, res) in SIGNATURES.items():
        fn = getattr(d, name)
        fn.argtypes, fn.restype = args, res
    return d


def economy(present=1, version=(0, 2, 0), divisor=3000, cap=300) -> Economy:
    e = Economy()
    if present:
        e.present, (e.major, e.minor, e.patch), e.divisor_x100, e.cap_x100 = 1, version, divisor, cap
    return e


def seat(augments=(), side=0, mp=999, charges=3, magick=0, gil_reserved=0) -> tm.Seat:
    return tm.seat(augments=augments, side=side, mp=mp, charges=charges, magick=magick, gil_reserved=gil_reserved)


class CostCase(unittest.TestCase):
    d = None

    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())

    def setUp(self):
        # The fault-safe reader meets unmapped pages on purpose (banks not loaded, tables past their end).
        quiet = quiet_faulthandler()
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def load(self, channeling=(5, 10, 15)):
        fx = tm.Fixture()
        sections = fx.sections()
        if channeling is not None:
            sections[tm.T_AUGMENTS] = tm.augment_section(channeling)
        self.assertEqual(tm.load_sections(self.d, sections), tm.TABLES_OK)
        return fx

    def sweep(self, fn, action, kind, st) -> list[int]:
        out = I256()
        self.d.ILCT_CostSweep(fn, action, kind, ctypes.byref(st), ctypes.byref(out))
        return list(out)

    def admission_sweep(self, action, kind, st, gil) -> list[int]:
        out = I256()
        self.d.ILCT_CostAdmissionSweep(action, kind, ctypes.byref(st), gil, ctypes.byref(out))
        return list(out)

    def set_economy(self, e: Economy):
        self.d.ILCT_CostSetEconomy(ctypes.byref(e))

    def build(self, sh, st, expect=tm.BUILD_OK) -> tm.Catalog:
        c = tm.Catalog()
        self.assertEqual(self.d.ILCT_MenuBuild(ctypes.byref(sh), ctypes.byref(st), ctypes.byref(c)), expect)
        return c


# ---- the game's code in unicorn -------------------------------------------------------------------------------------
DRIVER, DATA, STACK = 0x10000, 0x40000000, 0x50000000
ACTIONS_AT, AUGMENTS_AT, ACTOR_AT, RECORD_AT, GIL_AT, OUT_AT = (DATA + 0x10000, DATA + 0x30000, DATA + 0x40000,
                                                              DATA + 0x42000, DATA + 0x43000, DATA + 0x44000)
ACTION_ROWS, ACTION_STRIDE = 0x21F, 60
# The driver: for edi = 0..255 { byte [rbx] = dil (the base cost); eax = fn(rcx = r12 actor, edx = r13d action);
# [rsi + rdi * 4] = eax }. Win64: rbx, rsi, rdi, r12..r14 survive the call; 32 bytes of shadow space, RSP 16-aligned at
# each call.
DRIVER_CODE = bytes.fromhex('4883ec28' '40883b' '4c89e1' '4489ea' '41ffd6' '8904be' 'ffc7' '81ff00010000' '75e7')
DRIVER_TEXT = ['sub rsp, 0x28', 'mov byte ptr [rbx], dil', 'mov rcx, r12', 'mov edx, r13d', 'call r14',
               'mov dword ptr [rsi + rdi*4], eax', 'inc edi', 'cmp edi, 0x100', 'jne 0x10004']


class GameCode:
    """FFXII_TZA.exe's sections mapped at its fixed base in unicorn, a synthetic action table, augment table, actor,
    member record and gil block (all below 4 GiB, the resource base 0), and the driver loop."""

    def __init__(self, exe: Path):
        raw = exe.read_bytes()
        if hashlib.sha256(raw).hexdigest() != EXE_SHA256:
            raise unittest.SkipTest('IVALICE_LEGACY_GAME is not the pinned build')
        pe = struct.unpack_from('<I', raw, 0x3C)[0]
        sections, optional = struct.unpack_from('<H', raw, pe + 6)[0], struct.unpack_from('<H', raw, pe + 20)[0]
        image_base = struct.unpack_from('<Q', raw, pe + 24 + 24)[0]
        size = struct.unpack_from('<I', raw, pe + 24 + 56)[0]
        assert image_base == 0x120000
        uc = U.Uc(U.UC_ARCH_X86, U.UC_MODE_64)
        uc.mem_map(image_base, (size + 0xFFF) & ~0xFFF)
        for k in range(sections):
            _name, vsize, va, rsize, rptr = struct.unpack_from('<8sIIII', raw, pe + 24 + optional + 40 * k)
            uc.mem_write(image_base + va, raw[rptr:rptr + min(rsize, max(vsize, rsize))])
        uc.mem_map(DRIVER, 0x1000)
        uc.mem_write(DRIVER, DRIVER_CODE)
        uc.mem_map(DATA, 0x100000)
        uc.mem_map(STACK, 0x10000)
        # The admission's first check (an action whose content the party no longer holds) is not about cost.
        uc.mem_write(FN_HELD_CONTENT, bytes.fromhex('31c0c3'))   # xor eax, eax; ret
        # Tables as the pack loader leaves them: a global holding a qword pointer to the header, whose +0x0C is the
        # 32-bit address of the rows (+4 count, +8 row size).
        self.uc = uc
        self.put_table(ACTION_TABLE_PTR, DATA + 0x100, ACTIONS_AT, ACTION_ROWS, ACTION_STRIDE)
        self.put_table(AUGMENT_TABLE_PTR, DATA + 0x200, AUGMENTS_AT, 129, 8)
        uc.mem_write(LOCATION_PTR, struct.pack('<Q', GIL_AT))
        uc.mem_write(ACTOR_AT + ACTOR_RECORD, struct.pack('<Q', RECORD_AT))

    def put_table(self, glob, header, rows, count, stride):
        self.uc.mem_write(header, b'st2e' + struct.pack('<IHHI', count, stride, 0, rows))
        self.uc.mem_write(glob, struct.pack('<Q', header))

    def driver_text(self) -> list[str]:
        md = Cs(CS_ARCH_X86, CS_MODE_64)
        return [f'{i.mnemonic} {i.op_str}' for i in md.disasm(DRIVER_CODE, DRIVER)]

    def setup(self, action, kind=1, augments=(), side=0, where='timed', channeling=(5, 10, 15), mp=0, charges=0,
              gil=0, gil_reserved=0):
        uc = self.uc
        row = ACTIONS_AT + action * ACTION_STRIDE if action < ACTION_ROWS else DATA + 0x8000
        uc.mem_write(row, bytes(ACTION_STRIDE))
        uc.mem_write(row + 0x1E, bytes([kind]))
        for k, value in enumerate(channeling):
            uc.mem_write(AUGMENTS_AT + (91 + k) * 8 + 4, struct.pack('<H', value))
        timed, lasting = bytearray(12), bytearray(12)
        for n, a in enumerate(augments):
            target = timed if where == 'timed' or (where == 'both' and n % 2 == 0) else lasting
            target[a // 8] |= 1 << (a % 8)
        rec = bytearray(0x1C8)
        rec[REC_SIDE] = side
        struct.pack_into('<h', rec, REC_MP, mp)
        rec[REC_CHARGES] = charges
        rec[REC_AUG_TIMED:REC_AUG_TIMED + 12] = timed
        rec[REC_AUG_LASTING:REC_AUG_LASTING + 12] = lasting
        uc.mem_write(RECORD_AT, bytes(rec))
        uc.mem_write(ACTOR_AT + ACTOR_GIL_RESERVED, struct.pack('<i', gil_reserved))
        uc.mem_write(GIL_AT + 8, struct.pack('<I', gil & 0xFFFFFFFF))
        return row

    def run(self, fn, action, **kw) -> list[int]:
        """fn(actor, action) for every base cost 0..255 (written at action row +0x0A by the driver)."""
        uc = self.uc
        row = self.setup(action, **kw)
        uc.reg_write(X.UC_X86_REG_RBX, row + 0x0A)
        uc.reg_write(X.UC_X86_REG_RSI, OUT_AT)
        uc.reg_write(X.UC_X86_REG_RDI, 0)
        uc.reg_write(X.UC_X86_REG_R12, ACTOR_AT)
        uc.reg_write(X.UC_X86_REG_R13, action)
        uc.reg_write(X.UC_X86_REG_R14, fn)
        uc.reg_write(X.UC_X86_REG_RSP, STACK + 0x8000 - 8)
        uc.emu_start(DRIVER, DRIVER + len(DRIVER_CODE), count=256 * 2000)
        return list(struct.unpack('<256i', bytes(uc.mem_read(OUT_AT, 1024))))


_GAME_CODE: dict = {}


def game_code() -> GameCode:
    if 'g' not in _GAME_CODE:
        _GAME_CODE['g'] = GameCode(Path(GAME) / 'x64' / 'FFXII_TZA.exe')
    return _GAME_CODE['g']


def native_seat(augments, side, mp=0, charges=0, gil_reserved=0) -> tm.Seat:
    return seat(augments=augments, side=side, mp=mp, charges=charges, gil_reserved=gil_reserved)


@native_test
@unittest.skipUnless(GAME and U and Cs, 'requires IVALICE_LEGACY_GAME (a copy of the exe), unicorn and capstone')
class GameCostOracleTest(CostCase):
    """The module's restatement against the game's own code, row by row (256 base costs per comparison)."""

    def compare(self, fns, action, kind, augments, side, where='timed', channeling=(5, 10, 15)):
        g = game_code()
        st = native_seat(augments, side)
        for native_fn, game_fn in fns:
            want = g.run(game_fn, action, kind=kind, augments=augments, side=side, where=where, channeling=channeling)
            got = self.sweep(native_fn, action, kind, st)
            if got != want:
                k = next(i for i in range(256) if got[i] != want[i])
                self.fail(f'fn {game_fn:#x} action {action:#x} kind {kind} augments {augments} side {side} {where} '
                          f'channeling {channeling} base {k}: module {got[k]} != game {want[k]}')
        return True

    def test_the_driver_is_what_it_says(self):
        self.assertEqual(game_code().driver_text(), DRIVER_TEXT)

    def test_instrument_the_game_code_sees_the_inputs(self):
        g = game_code()
        self.assertEqual(g.run(FN_MP, PLAIN, kind=1)[:4], [0, 1, 2, 3], 'MP cost = base without augments')
        self.assertEqual(g.run(FN_MP, PLAIN, kind=5)[30], 0, 'kind 5 costs no MP')
        self.assertEqual(g.run(FN_CHARGES, PLAIN, kind=5)[3], 3, 'kind 5 costs its base in charges')
        self.assertEqual(g.run(FN_MP, PLAIN, kind=1, augments=(36,))[30], 15, 'Parsimony halves')
        self.assertEqual(g.run(FN_GIL, PLAIN, kind=1, augments=(32,))[26], 330, 'Offering: trunc(26*64/50)*10')
        self.assertEqual(g.run(FN_MP, PLAIN, kind=1, augments=(93,), channeling=(0, 0, 50))[30], 14,
                         'single precision: 30 MP with 50% Channeling is 14')

    def test_instrument_a_difference_is_caught(self):
        self.load((5, 10, 15))
        with self.assertRaises(AssertionError):
            self.compare(((SW_MP, FN_MP),), PLAIN, 1, (91,), 0, channeling=(6, 10, 15))
        with self.assertRaises(AssertionError):
            self.compare(((SW_GIL, FN_MP),), PLAIN, 1, (), 0)

    def test_every_augment_set_base_kind_side_and_id(self):
        self.load((5, 10, 15))
        mp_gil = ((SW_MP, FN_MP), (SW_GIL, FN_GIL))
        for augments, side in itertools.product(ALL_SETS, (0, 1, 2)):
            with self.subTest(augments=augments, side=side):
                for action, kind in itertools.product((PLAIN, QUICKENING, ESPER), (1, 5, 6)):
                    self.compare(mp_gil, action, kind, augments, side)
                for action in (PLAIN, QUICKENING, ESPER):
                    self.compare(((SW_HELPER, FN_HELPER),), action, 1, augments, side)

    def test_where_the_augments_sit(self):
        self.load((5, 10, 15))
        fns = ((SW_MP, FN_MP), (SW_GIL, FN_GIL), (SW_HELPER, FN_HELPER))
        for augments, where in itertools.product(ALL_SETS, ('lasting', 'both')):
            with self.subTest(augments=augments, where=where):
                self.compare(fns, PLAIN, 1, augments, 0, where=where)

    def test_channeling_values(self):
        sets = [a for a in ALL_SETS if set(a) & {91, 92, 93}]
        for channeling in ((0, 0, 0), (25, 50, 100), (33, 1, 7), (255, 1000, 65535), (65535, 65535, 65535)):
            self.load(channeling)
            for augments, side in itertools.product(sets, (0, 1)):
                with self.subTest(channeling=channeling, augments=augments, side=side):
                    self.compare(((SW_MP, FN_MP), (SW_HELPER, FN_HELPER)), PLAIN, 1, augments, side,
                                 channeling=channeling)

    def test_other_kinds_and_mist_charges(self):
        self.load((5, 10, 15))
        fns = ((SW_MP, FN_MP), (SW_GIL, FN_GIL), (SW_CHARGES, FN_CHARGES))
        for kind, augments in itertools.product((0, 1, 2, 3, 4, 5, 6, 7, 255), ((), (32,), (36, 91, 92, 93))):
            for action in (PLAIN, QUICKENING, ESPER, 0x96, 0x21E):
                with self.subTest(kind=kind, augments=augments, action=action):
                    self.compare(fns, action, kind, augments, 0)

    def test_ids_past_the_cost_helpers_bound_cost_nothing(self):
        self.load((5, 10, 15))
        g = game_code()
        for action in (0x21F, 0x220, 0x3FF):
            for fn, game_fn in ((SW_MP, FN_MP), (SW_GIL, FN_GIL), (SW_CHARGES, FN_CHARGES)):
                want = g.run(game_fn, action, kind=1, augments=(32,))
                self.assertEqual(want, [0] * 256)
                self.assertEqual(self.d.ILCT_CostOne(fn, action, ctypes.byref(native_seat((32,), 0))), 0)


@native_test
@unittest.skipUnless(GAME and U and Cs, 'requires IVALICE_LEGACY_GAME (a copy of the exe), unicorn and capstone')
class AdmissionOracleTest(CostCase):
    """The admission check (0x303BD0) against the module's verdict on cost, row by row."""

    def compare(self, action, kind, augments, side, mp, charges, gil, reserved):
        g = game_code()
        want = g.run(FN_ADMISSION, action, kind=kind, augments=augments, side=side, mp=mp, charges=charges, gil=gil,
                     gil_reserved=reserved)
        st = native_seat(augments, side, mp=mp, charges=charges, gil_reserved=reserved)
        got = self.admission_sweep(action, kind, st, gil)
        if got != want:
            k = next(i for i in range(256) if got[i] != want[i])
            self.fail(f'action {action:#x} kind {kind} augments {augments} side {side} mp {mp} charges {charges} '
                      f'gil {gil} reserved {reserved} base {k}: module {got[k]} != game {want[k]}')
        return want

    def test_mp_and_gil_over_every_augment_set(self):
        self.load((5, 10, 15))
        seen = set()
        for augments, side in itertools.product(ALL_SETS, (0, 1)):
            with self.subTest(augments=augments, side=side):
                for mp, (gil, reserved) in itertools.product((-1, 0, 13, 300),
                                                             ((0, 0), (1000, 0), (0, 3260), (99999, 0))):
                    seen.update(self.compare(PLAIN, 1, augments, side, mp, 0, gil, reserved))
        self.assertEqual(seen, {COST_OK, COST_MP, COST_GIL}, 'instrument: every verdict occurs')

    def test_a_record_on_another_side_never_pays_gil(self):
        self.load((5, 10, 15))
        for augments in ((32,), (32, 36), (32, 91)):
            self.compare(PLAIN, 1, augments, 2, 0, 0, 0, 0)

    def test_mist_charges(self):
        self.load((5, 10, 15))
        seen = set()
        for kind, action, charges, augments in itertools.product((5, 6), (PLAIN, QUICKENING, ESPER),
                                                                 (0, 1, 2, 3, 255), ((), (32,), (36, 51))):
            seen.update(self.compare(action, kind, augments, 0, 0, charges, 0, 0))
        self.assertEqual(seen, {COST_OK, COST_CHARGES})


# ---- the catalog greys what the admission refuses -------------------------------------------------------------------
@native_test
class GreyingTest(CostCase):
    def entries(self, c):
        for page, entries in tm.pages_of(c).items():
            for e in entries:
                if e.kind == tm.EK_ACTION and e.role != tm.ROLE_DISMISS:
                    yield page, e

    def test_greys_follow_the_admission_verdict(self):
        fx = self.load((5, 10, 15))
        for k in range(0x106, 0x113):                      # Espers of several prices
            fx.actions[k] = tm.action_row(cost=1 + k % 3, targeting=0x41, kind=5, rules=0x100)
        raw = tm.st2e(fx.actions, 60)
        self.d.ILCT_MenuTablesSection(tm.T_ACTIONS, raw, len(raw))
        self.assertEqual(self.d.ILCT_MenuTablesFinish(), tm.TABLES_OK)
        sh_args = dict(magicks=range(8), technicks=(0, 1), mist=(6, 7, 8, 18, 19, 20, 21), items={0: 2, 3: 1})
        checked = set()
        for augments, mp, charges, gil, reserved in itertools.product(
                ((), (32,), (36,), (51,), (91, 92, 93), (32, 36), (36, 93)), (-1, 0, 6, 10, 24, 999), (0, 1, 2, 3),
                (0, 100, 5000), (0, 400)):
            sh = tm.shared(gil=gil, **sh_args)
            st = tm.seat(member=2, licences=tuple(range(0, 10)) + tuple(range(18, 31)) + (280,),
                         summary=(6, 7, 8, 9), mp=mp, augments=augments, charges=charges, gil_reserved=reserved)
            c = self.build(sh, st)
            for page, e in self.entries(c):
                verdict = self.d.ILCT_CostAdmission(e.action, ctypes.byref(st), gil)
                checked.add(verdict)
                reason = {COST_MP: bool(e.flags & tm.EF_MP_SHORT), COST_CHARGES: bool(e.cost_flags & CF_CHARGES_SHORT),
                          COST_GIL: bool(e.cost_flags & CF_GIL_SHORT)}
                if verdict in reason:
                    self.assertFalse(tm.enabled(e), (page, hex(e.action), verdict))
                    self.assertTrue(reason[verdict], (page, hex(e.action), verdict))
                    self.assertEqual(sum(reason.values()), 1, 'one reason')
                else:
                    self.assertFalse(any(reason.values()), (page, hex(e.action)))
                    self.assertTrue(tm.enabled(e), (page, hex(e.action), augments, mp))
        self.assertTrue({COST_OK, COST_MP, COST_CHARGES, COST_GIL} <= checked, checked)

    def test_entry_prices(self):
        self.load((5, 10, 15))
        self.set_economy(economy(present=0))
        sh = tm.shared(gil=0)
        e = tm.hand_entry(action=0x00)                     # Cure: 8 MP
        self.d.ILCT_CostPriceEntry(ctypes.byref(sh), ctypes.byref(seat(mp=20)), ctypes.byref(e))
        self.assertEqual((e.cost, e.cost_flags, tm.enabled(e)), (8, 0, True))
        e = tm.hand_entry(action=0x00)
        self.d.ILCT_CostPriceEntry(ctypes.byref(sh), ctypes.byref(seat(augments=(32,))), ctypes.byref(e))
        self.assertEqual((e.cost, e.cost_flags, tm.enabled(e)), (0, CF_GIL | CF_GIL_SHORT, False),
                         'Offering: no MP, 100 gil the party does not have')
        e = tm.hand_entry(action=0xD3)                     # a Quickening: one Mist charge
        self.d.ILCT_CostPriceEntry(ctypes.byref(sh), ctypes.byref(seat(charges=0)), ctypes.byref(e))
        self.assertEqual((e.cost, e.cost_flags, tm.enabled(e)), (1, CF_CHARGES | CF_CHARGES_SHORT, False))
        e = tm.hand_entry(action=0x00)
        self.d.ILCT_CostPriceEntry(ctypes.byref(sh), ctypes.byref(seat(augments=(91,), mp=0)), ctypes.byref(e))
        self.assertEqual(e.cost, 7)
        self.load(None)
        e = tm.hand_entry(action=0x00)
        self.d.ILCT_CostPriceEntry(ctypes.byref(sh), ctypes.byref(seat(augments=(91,), mp=0)), ctypes.byref(e))
        self.assertEqual((e.cost, bool(e.flags & tm.EF_COST_UNKNOWN), tm.enabled(e)), (-1, True, True),
                         'an unknown cost is shown as unknown, not greyed for MP')

    def test_gil_put_aside_lets_the_queued_action_through(self):
        self.load((5, 10, 15))
        st = seat(augments=(32,), gil_reserved=100)
        self.assertEqual(self.d.ILCT_CostAdmission(0x00, ctypes.byref(st), 0), COST_OK)
        st = seat(augments=(32,), gil_reserved=99)
        self.assertEqual(self.d.ILCT_CostAdmission(0x00, ctypes.byref(st), 99), COST_GIL)
        self.assertEqual(self.d.ILCT_CostAdmission(0x00, ctypes.byref(st), 100), COST_OK)


# ---- Legacy's MP economy ---------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(LuaRuntime, 'requires lupa')
class EconomyTest(CostCase):
    MAGICK = (0, 1, 29, 30, 31, 45, 57, 60, 89, 90, 91, 99, 150, 255)
    PARAMS = (economy(), economy(version=(0, 1, 0)), economy(divisor=2500, cap=200), economy(version=(1, 0, 0),
              divisor=100, cap=1600), economy(divisor=4550, cap=125))

    def setUp(self):
        super().setUp()
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().SRC = ECONOMY_LUA.read_text(encoding='utf-8')
        self.lua.execute(r"""
          M = load(SRC)()
          function sweep(divisor, cap, legacy_paid, mask, channeling, magick)
            M.DIVISOR, M.CAP = divisor, cap
            local names = {'offering', 'parsimony', 'spellspring', 'channeling3', 'channeling2', 'channeling1'}
            local set = {}
            for k, n in ipairs(names) do if (mask >> (k - 1)) & 1 == 1 then set[n] = true end end
            local function has(n) return set[n] == true end
            local function param(bit) return channeling[bit - 90] or 0 end
            local out = {}
            for cost = 0, 255 do
              if legacy_paid then
                out[#out + 1] = M.surcharge(cost, magick, M.paid(cost, has, param))
              else
                out[#out + 1] = M.surcharge(cost, magick)
              end
            end
            return table.concat(out, ',')
          end
        """)

    def tearDown(self):
        self.set_economy(economy(present=0))

    def test_the_file_is_the_one_the_menus_follow(self):
        M = self.lua.eval('M')
        self.assertEqual((M.version, M.DIVISOR, M.CAP, M.MAGICK_CATEGORY), ('0.2.0', 30, 3, 1))
        self.assertEqual(dict(M.AUGMENTS), {'offering': 32, 'parsimony': 36, 'spellspring': 51, 'channeling3': 91,
                                            'channeling2': 92, 'channeling1': 93})

    def test_surcharge_matches_the_lua_rule(self):
        channeling = (5, 10, 15)
        self.load(channeling)
        lua_channeling = self.lua.table_from({1: channeling[0], 2: channeling[1], 3: channeling[2]})
        sweep = self.lua.eval('sweep')
        compared = 0
        for p in self.PARAMS:
            legacy_paid = (p.major, p.minor) >= (0, 2)
            for mask, magick in itertools.product(range(64), self.MAGICK):
                augments = ALL_SETS[mask]
                want = [int(x) for x in sweep(p.divisor_x100 / 100, p.cap_x100 / 100, legacy_paid, mask,
                                              lua_channeling, magick).split(',')]
                out = I256()
                self.d.ILCT_CostSurchargeSweep(0x00, ctypes.byref(seat(augments=augments, magick=magick)),
                                               ctypes.byref(p), ctypes.byref(out))
                if list(out) != want:
                    k = next(i for i in range(256) if out[i] != want[i])
                    self.fail(f'{p.major}.{p.minor} {p.divisor_x100}/{p.cap_x100} augments {augments} magick {magick} '
                              f'cost {k}: module {out[k]} != Lua {want[k]}')
                compared += 256
        self.assertEqual(compared, len(self.PARAMS) * 64 * len(self.MAGICK) * 256)

    def test_total_matches_the_lua_rule(self):
        total = self.lua.eval('function(c, m) M.DIVISOR, M.CAP = 30, 3 return M.total(c, m) end')
        p = economy()
        for cost, magick in itertools.product(range(0, 256, 3), range(256)):
            self.assertEqual(self.d.ILCT_CostEconomyTotal(cost, magick, ctypes.byref(p)), total(cost, magick))

    def test_the_game_price_is_what_legacy_scales(self):
        self.load((5, 10, 15))
        for augments in ALL_SETS:
            st = seat(augments=augments)
            paid = self.sweep(SW_PAID, 0x00, 1, st)
            game = self.sweep(SW_MP, 0x00, 1, st)
            self.assertEqual(paid, [max(0, v) for v in game], augments)

    def test_shown_cost_and_who_pays_it(self):
        self.load((5, 10, 15))
        self.set_economy(economy())
        pen = seat(magick=57)
        self.assertEqual(self.d.ILCT_CostOne(SW_SHOWN, 0x00, ctypes.byref(pen)), 15, "Cure 8 at Magick 57: 8 + 7")
        self.assertEqual(self.d.ILCT_CostOne(SW_SHOWN, 0x00, ctypes.byref(seat(magick=57, augments=(91, 92, 93)))),
                         5 + 5, 'the surcharge scales what the game charges (5)')
        self.assertEqual(self.d.ILCT_CostOne(SW_SHOWN, 0x00, ctypes.byref(seat(magick=20))), 8, 'below DIVISOR')
        self.assertEqual(self.d.ILCT_CostOne(SW_SHOWN, 0x00, ctypes.byref(seat(magick=255))), 24, 'at most CAP x')
        for who, st in (('a record on side 1', seat(magick=99, side=1)), ('Offering', seat(magick=99, augments=(32,))),
                        ('Spellspring', seat(magick=99, augments=(51,)))):
            with self.subTest(who):
                self.assertEqual(self.d.ILCT_CostOne(SW_SURCHARGE, 0x00, ctypes.byref(st)), 0)
        tech = tm.Fixture().technick_actions[0]
        self.assertEqual(self.d.ILCT_CostOne(SW_SURCHARGE, tech, ctypes.byref(pen)), 0, 'technicks are not priced')
        self.assertEqual(self.d.ILCT_CostOne(SW_SURCHARGE, 0xD3, ctypes.byref(pen)), 0, 'nor Quickenings')
        self.set_economy(economy(present=0))
        self.assertEqual(self.d.ILCT_CostOne(SW_SHOWN, 0x00, ctypes.byref(pen)), 8, 'economy off: the game price')

    def test_the_catalog_shows_the_surcharge_and_greys_on_the_game_price(self):
        self.load((5, 10, 15))
        self.set_economy(economy())
        st = tm.seat(summary=(6, 7), mp=8, magick=57)
        c = self.build(tm.shared(magicks=(0, 1, 2)), st)
        white = {e.action: e for e in tm.pages_of(c)[tm.PG_WHITE]}
        self.assertEqual((white[0x00].cost, white[0x00].cost_flags & CF_SURCHARGE), (15, CF_SURCHARGE))
        self.assertTrue(tm.enabled(white[0x00]), 'the game admits Cure on its own price (8), MP 8 is enough')
        self.assertFalse(tm.enabled(white[0x05]), 'Raise: 20 > 8')
        self.assertEqual(white[0x01].cost, 6 + 5, 'Blindna 6 at Magick 57: 6 x 1.9 = 11.4 -> 11')

    def test_new_parameters_rebuild_the_catalog(self):
        self.load((5, 10, 15))
        sh, st = tm.shared(magicks=(0,)), tm.seat(summary=(6,), magick=57)
        self.set_economy(economy(present=0))
        before = self.d.ILCT_MenuSignature(ctypes.byref(sh), ctypes.byref(st))
        self.set_economy(economy())
        self.assertNotEqual(self.d.ILCT_MenuSignature(ctypes.byref(sh), ctypes.byref(st)), before)

    def test_the_parameters_in_force(self):
        self.set_economy(economy(present=0))
        e = Economy()
        self.d.ILCT_CostEconomy(ctypes.byref(e))
        self.assertEqual(bytes(e), bytes(8))
        self.set_economy(economy(divisor=2500, cap=200))
        self.d.ILCT_CostEconomy(ctypes.byref(e))
        self.assertEqual((e.present, e.minor, e.divisor_x100, e.cap_x100), (1, 2, 2500, 200))


# ---- help lines ----------------------------------------------------------------------------------------------------------
def encoded(text: str) -> bytes:
    out = bytearray()
    for ch in text:
        if 'A' <= ch <= 'Z':
            out.append(32 + ord(ch) - 65)
        elif 'a' <= ch <= 'z':
            out.append(58 + ord(ch) - 97)
        elif '0' <= ch <= '9':
            out.append(0x85 + ord(ch) - 48)
        else:
            out.append({' ': 4, '.': 0xA8, ',': 0xA7}[ch])
    return bytes(out)


def place_help_bank(img: Image, heap: tm.LowHeap, raw: bytes, bank=4, loaded=True) -> int:
    """A help bank as the game holds it: header pointer per bank, its +0x14 the 32-bit address of the directory,
    the directory's +4 the address of the text list, the list's entries text addresses (all below 4 GiB)."""
    raw = bytearray(raw)
    base = heap.alloc(len(raw), page=True)
    directory = struct.unpack_from('<I', raw, 0x14)[0]
    lst = directory + struct.unpack_from('<I', raw, directory + 4)[0]
    count = struct.unpack_from('<I', raw, lst)[0]
    for i in range(count):
        rel = struct.unpack_from('<I', raw, lst + 4 + 4 * i)[0]
        struct.pack_into('<I', raw, lst + 4 + 4 * i, base + lst + rel)
    struct.pack_into('<I', raw, directory + 4, base + lst)
    struct.pack_into('<I', raw, 0x14, base + directory)
    Image.put(base, bytes(raw))
    img.put_global(HELP_BANKS + bank * 8, struct.pack('<Q', base))
    img.put_global(HELP_LOADED + bank * 8, struct.pack('<Q', 1 if loaded else 0))
    return base


def help_bank(texts: list[bytes]) -> bytes:
    """A bank file: st2e header (+4 count, +0x14 directory), directory {1, +0xC}, list {count, offsets...}."""
    head = bytearray(32)
    head[:4] = b'st2e'
    struct.pack_into('<I', head, 4, len(texts))
    directory = 32
    body = bytearray(struct.pack('<III', 1, 12, 0))
    offsets, blob = [], bytearray()
    start = 4 + 4 * len(texts)
    for t in texts:
        offsets.append(start + len(blob))
        blob += t + b'\0'
    body += struct.pack(f'<I{len(texts)}I', len(texts), *offsets) + blob
    struct.pack_into('<I', head, 0x14, directory)
    return bytes(head + body)


@native_test
class HelpTest(CostCase):
    def strip(self, raw: bytes) -> bytes:
        out = ctypes.create_string_buffer(256)
        n = self.d.ILCT_HelpStrip(raw, len(raw), out, 256)
        return out.raw[:n]

    def fit(self, text: str, cap=64) -> str:
        out = ctypes.create_string_buffer(cap)
        self.d.ILCT_HelpFit(text.encode(), out, cap)
        return out.value.decode()

    def test_tokens_are_removed(self):
        holy = encoded('Deal heavy ') + bytes.fromhex('0f3cc1fe0f3f81900f3c8180') + encoded(' holy damage.')
        self.assertEqual(self.strip(holy), encoded('Deal heavy  holy damage.'))
        run = encoded('A') + bytes.fromhex('0f29838080') + bytes([0x80]) + encoded('B')
        self.assertEqual(self.strip(run), encoded('AB'), 'a colour run: header + its values')
        self.assertEqual(self.strip(encoded('A') + bytes.fromhex('0f2980ff') + encoded('B')), encoded('AB'),
                         'a colour run of no values carries one byte')
        self.assertEqual(self.strip(encoded('A') + bytes.fromhex('0f50aabb') + encoded('B')), b'',
                         'an unknown token: the text is not used')
        self.assertEqual(self.strip(encoded('Abc') + b'\0' + encoded('Def')), encoded('Abc'))
        self.assertEqual(self.strip(encoded('Abc') + b'\x0f'), encoded('Abc'), 'a token cut off at the end')

    def test_fitting_into_the_help_strip(self):
        self.assertEqual(self.fit('Restore HP to one ally.'), 'Restore HP to one ally.')
        long = 'Inflict one foe with any harmful status effects on the user and some more words here.'
        got = self.fit(long)
        self.assertLessEqual(len(got), 63)
        self.assertTrue(got.endswith('...') and long.startswith(got[:-3]), got)
        self.assertNotEqual(got[-4], ' ')
        self.assertEqual(self.fit('x' * 80), 'x' * 60 + '...', 'no space: cut hard')
        self.assertEqual(self.fit('x' * 63), 'x' * 63)

    def live_world(self, texts, actions_help: dict[int, int], loaded=True, bank=4):
        img, heap = Image(), tm.LowHeap(1 << 20)
        actions = [tm.action_row() for _ in range(0x21F)]
        for action, hid in actions_help.items():
            row = bytearray(actions[action])
            struct.pack_into('<H', row, 0, hid)
            actions[action] = bytes(row)
        tm.place_tables(img, heap, {tm.T_ACTIONS: tm.st2e(actions, 60)})
        place_help_bank(img, heap, help_bank(texts), bank=bank, loaded=loaded)
        return img, heap

    def help(self, img, action) -> tuple[bytes, int]:
        out = ctypes.create_string_buffer(256)
        where = u64()
        n = self.d.ILCT_HelpLive(img.base, action, out, 256, ctypes.byref(where))
        return out.raw[:n], where.value

    def view_line(self, img, action):
        out = ctypes.create_string_buffer(64)
        ok = self.d.ILCT_HelpViewLine(img.base, action, out, 64)
        return out.value.decode() if ok else None

    def test_the_games_help_walk(self):
        texts = [b'', encoded('Restore HP to one ally.'),
                 encoded('Deal ') + bytes.fromhex('0f3f8190') + encoded('holy damage.')]
        img, heap = self.live_world(texts, {0x00: 4001, 0x01: 4002, 0x96: 4000, 0x10: 4003, 0x11: 5001, 0x12: 28001})
        try:
            self.assertEqual(self.help(img, 0x00)[0], texts[1])
            self.assertNotEqual(self.help(img, 0x00)[1], 0, 'the text address (the cache key)')
            self.assertEqual(self.view_line(img, 0x00), 'Restore HP to one ally.')
            self.assertEqual(self.view_line(img, 0x01), 'Deal holy damage.', 'icon removed, spaces folded')
            self.assertEqual(self.help(img, 0x96), (b'', self.help(img, 0x96)[1]), "Attack's help is empty")
            self.assertIsNone(self.view_line(img, 0x96), 'empty: the view keeps its own line')
            self.assertEqual(self.help(img, 0x10), (b'', 0), 'index past the bank')
            self.assertEqual(self.help(img, 0x11), (b'', 0), 'bank 5 is not loaded')
            self.assertEqual(self.help(img, 0x12), (b'', 0), 'bank 28 does not exist')
            self.assertEqual(self.help(img, 0x300), (b'', 0), 'past the action table')
            hid = u32()
            self.assertEqual(self.d.ILCT_HelpId(img.base, 0x01, ctypes.byref(hid)), 1)
            self.assertEqual(hid.value, 4002)
        finally:
            img.close()
            heap.close()

    def test_an_unloaded_bank_gives_nothing(self):
        img, heap = self.live_world([b'', encoded('Text.')], {0x00: 4001}, loaded=False)
        try:
            self.assertEqual(self.help(img, 0x00), (b'', 0))
        finally:
            img.close()
            heap.close()

    def test_the_view_prefers_the_games_help_and_keeps_its_reasons(self):
        self.load((5, 10, 15))
        SEAT = 3
        self.d.ILCT_MenuSeatReset(SEAT)
        sh, st = tm.shared(magicks=(0, 1, 2)), tm.seat(summary=(6, 7), mp=10)
        self.assertEqual(self.d.ILCT_MenuSeatRefresh(SEAT, ctypes.byref(sh), ctypes.byref(st), None), 1)
        e = tm.Event()
        for op, a, b in ((tm.OP_OPEN, 0, 0), (tm.OP_NAV, 1, 1), (tm.OP_CONFIRM, 0, 0), (tm.OP_CONFIRM, 0, 0)):
            self.d.ILCT_MenuSeatOp(SEAT, op, a, b, ctypes.byref(e))
        out = ctypes.create_string_buffer(64)

        def line(on):
            self.d.ILCT_HelpSeatLine(SEAT, on, out, 64)
            return out.value.decode()

        self.assertEqual(line(1), 'help 1', 'White Magicks, first row (action 0x01): the game help')
        self.assertTrue(line(0).startswith('Target'), 'without the game help: our own line')
        for _ in range(2):                                                    # Raise: 20 MP, MP 10
            self.d.ILCT_MenuSeatOp(SEAT, tm.OP_NAV, 1, 1, ctypes.byref(e))
        self.assertTrue(line(1).startswith('Not enough MP.'), 'a grey says why, whatever the help says')


@native_test
@unittest.skipUnless(HELP_ACTION and BATTLE_PACK, "set ILC_HELP_ACTION (Legacy's help_action.bin) and ILC_BATTLE_PACK")
class LegacyHelpTest(CostCase):
    """Legacy's own help text: every item and every menu action resolves to its help line."""

    def test_every_legacy_item_and_menu_action_has_its_help(self):
        sections = tm.pack_sections(BATTLE_PACK, BATTLE_PACK_DIR)
        actions = tm.section_rows(sections[tm.T_ACTIONS])
        items = tm.section_rows(sections[tm.T_ITEMS])
        img, heap = Image(), tm.LowHeap(4 << 20)
        try:
            tm.place_tables(img, heap, {tm.T_ACTIONS: sections[tm.T_ACTIONS]})
            place_help_bank(img, heap, Path(HELP_ACTION).read_bytes())
            out = ctypes.create_string_buffer(64)

            def line(action):
                return out.value.decode() if self.d.ILCT_HelpViewLine(img.base, action, out, 64) else None

            usable, unused = [], []
            for k, row in enumerate(items):
                action = row[7]
                kind = actions[action][0x1E]
                (usable if kind == 3 else unused).append((k, action))
                if kind == 3:
                    text = line(action)
                    self.assertTrue(text, f'item {k} (action {action:#x}) has no help line')
                else:
                    # Unused rows (menu kind 255): the game's own help id is 4000, the empty text, as in Steam's data.
                    self.assertEqual(struct.unpack_from('<H', actions[action], 0)[0], 4000)
                    self.assertIsNone(line(action))
            self.assertGreaterEqual(len(usable), 50, 'instrument: the item table was read')
            menu = [a for a, row in enumerate(actions) if row[0x1E] in (1, 2, 3, 5, 6)]
            missing = [hex(a) for a in menu if not line(a)]
            self.assertEqual(missing, [])
            self.assertGreater(len(menu), 150)
            self.assertEqual(line(0x00), 'Restore a small amount of HP to one ally.')
        finally:
            img.close()
            heap.close()


# ---- coop/economy.lua -------------------------------------------------------------------------------------------------------
@unittest.skipUnless(LuaRuntime, 'requires lupa')
class EconomyLuaTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.E = self.lua.execute((COOP_LUA / 'economy.lua').read_text(encoding='utf-8'))

    def provider(self, files: dict):
        g = self.lua.globals()
        g.FILES = self.lua.table_from(files)
        loader = self.lua.eval("function(path, mode) local s = FILES[path]; if not s then return nil end "
                               "return load(s, path, mode) end")
        return self.E.provider(loader, None)

    def test_legacys_file_gives_the_record_values(self):
        got = self.provider({self.E.PATH: ECONOMY_LUA.read_text(encoding='utf-8')})()
        self.assertEqual(dict(got), {'major': 0, 'minor': 2, 'patch': 0, 'divisor': 3000, 'cap': 300})

    def test_nothing_for_a_missing_or_unusable_file(self):
        self.assertIsNone(self.provider({})())
        for src in ("return {version = '0.2', DIVISOR = 30, CAP = 3}", "return {version = '0.2.0', DIVISOR = 0, CAP = 3}",
                    "return {version = '0.2.0', DIVISOR = 30, CAP = 17}", "error('broken')", "return 7",
                    "return {version = '0.300.0', DIVISOR = 30, CAP = 3}"):
            with self.subTest(src):
                self.assertIsNone(self.provider({self.E.PATH: src})())

    def test_read_once(self):
        calls = self.lua.eval('function() CALLS = (CALLS or 0) + 1 return load("return {version=\'0.2.0\', '
                              'DIVISOR=27.5, CAP=2.25}") end')
        p = self.E.provider(calls, None)
        self.assertEqual(dict(p()), {'major': 0, 'minor': 2, 'patch': 0, 'divisor': 2750, 'cap': 225})
        p()
        self.assertEqual(self.lua.globals().CALLS, 1)

    def test_the_entry_script_hands_it_to_the_store(self):
        text = ENTRY_LUA.read_text(encoding='utf-8')
        self.assertIn("load('economy').provider(loadfile, _ENV)", text)
        self.assertIn('economy = economy', text)


if __name__ == '__main__':
    unittest.main()
