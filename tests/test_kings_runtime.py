"""Kings of the zone runtime (package/x64/scripts/IvaliceLegacy/kings_runtime.lua).

- Lua (Lupa): the home table is valid, the data block layout, and install() patches nothing unless the Legacy build
  and both original sites are found (block first, sites last).
- Native (Keystone + Unicorn): both hooks run against stand-ins for the game's tables at their real addresses:
  presence answer 1 only for a living King in a forced home; a dead King's unit row becomes 0xFF only in its home;
  everything else keeps the native value; the displaced instructions still run; callee-saved registers and the
  stack are untouched.
- Game executable (when IVALICE_LEGACY_GAME is set): the sites hold the bytes the hooks displace, and the code
  around them is what the hooks assume.
"""
import os
import re
import struct
import unittest
from pathlib import Path

try:
    import keystone
    import unicorn
    from unicorn import x86_const as X
except ImportError:
    keystone = None
try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'package/x64/scripts/IvaliceLegacy/kings_runtime.lua'
ENTRY = ROOT / 'package/x64/scripts/IvaliceLegacyKings.lua'
SOURCE = RUNTIME.read_text(encoding='utf-8')
CODE_AT, DATA_AT, STACK_AT, TABLES_AT = 0x10000000, 0x20000000, 0x30000000, 0x40000000
KILLS, LOCATION = 0x0216F883, 0x021654C4
UNIT_TABLE, CLASS_TABLE, TABLE_BASE = 0x02EBF120, 0x02EBF150, 0x01F83530


def homes():
    body = re.search(r'K\.HOMES = \{[^\n]*\n(.*?)\n\}', SOURCE, re.S).group(1)
    return [tuple(int(x) for x in m) for m in re.findall(r'\{(\d+), (\d+), ([01])\}', body)]


def code():
    return re.search(r'K\.RARE_CODE = \[\[\n(.*?)\]\]', SOURCE, re.S).group(1).replace('.dq 0x00', '.quad 0')


class HomeTableTest(unittest.TestCase):
    def test_every_rare_game_has_a_home(self):
        h = homes()
        self.assertEqual({e for _, e, _ in h}, set(range(293, 373)))  # all 80 Bestiary rare-game entries
        self.assertEqual(len(h), len(set((l, e) for l, e, _ in h)))

    def test_bountiful_exclusions_keep_natural_rules(self):
        for loc, _, presence in homes():
            if loc in (133, 137, 247, 902, 903):
                self.assertEqual(presence, 0)

    def test_no_absolute_memory_operands(self):
        # "[0x...]" may be assembled rip-relative for registers other than rax (Keystone does): load addresses first.
        self.assertEqual(re.findall(r'\[0x[0-9A-Fa-f]+\]', code()), [])

    def test_known_homes(self):
        h = set(homes())
        for row in ((228, 316, 1),   # Ripe Rampager, Yardang Labyrinth (gated through dst_a02 thread 30's roll)
                    (358, 294, 1),   # Aerieel, Oltam Span
                    (891, 320, 1),   # Minibug, East-West Bypass
                    (891, 336, 1),   # Ishteen, East-West Bypass
                    (892, 336, 1),   # Ishteen, The Zeviah Span
                    (895, 322, 1),   # Ithuno, Special Op Sector 5
                    (374, 353, 1),   # Gemhorn, Site 11
                    (901, 310, 1),   # Bombshell, Lasche Span
                    (137, 329, 0)):  # Grimalkin, Uazcuff Hills (Bountiful-excluded)
            self.assertIn(row, h)


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class LuaTest(unittest.TestCase):
    def runtime(self, mem=None, legacy=True, sites_ok=True, fail_assemble=False):
        lua = LuaRuntime(unpack_returned_tuples=True)
        g = lua.globals()
        mem = {} if mem is None else mem
        calls = []

        def write(at, data):
            for i, b in enumerate(data):
                mem[at + i] = b
        if legacy:
            write(0x120000, b'MZ')
            write(0x120180, b'PE\0\0\x64\x86')
            write(0xD113D4, b'LEGCY_\0')
            write(0xD12200, '%sLEGCY_%03d\0'.encode('utf-16-le'))
        if sites_ok:
            write(0x3504BE, bytes([0x8B, 0xD0, 0x48, 0x8B, 0xCB]))
            write(0x510178, bytes([0x8B, 0xD0, 0x48, 0x8B, 0xCB]))

        def read_array(at, n):
            return lua.table_from([mem.get(at + i, 0) for i in range(n)])

        def write_array(at, t):
            for i, v in enumerate(t.values()):
                mem[at + i] = v

        def assemble(src, where):
            calls.append((src, where if isinstance(where, int) else 'block'))
            if fail_assemble:
                raise RuntimeError('rejected')
            return 0x50000000

        def alloc(n):
            calls.append(('alloc', n))
            return 0x60000000

        class U:
            def __init__(self, size):
                self.size = size

            def __getitem__(self, at):
                return int.from_bytes(bytes(mem.get(at + i, 0) for i in range(self.size)), 'little')

            def __setitem__(self, at, v):
                for i in range(self.size):
                    mem[at + i] = (v >> (8 * i)) & 0xFF
        g.memory = lua.table_from({'readArray': read_array, 'writeArray': write_array, 'assemble': assemble,
                                   'alloc': alloc, 'getSymbol': lambda name: 0x50000100,
                                   'u8': U(1), 'u32': U(4), 'u64': U(8)})
        self.handlers = {}

        def register_async(name, fn):
            self.handlers[name] = fn
            return True
        g.event = lua.table_from({'registerEventAsync': register_async})
        lines = []
        g.print = lambda s: lines.append(s)
        K = lua.execute(SOURCE)
        return lua, K, mem, calls, lines

    def test_validate_and_data_layout(self):
        lua, K, *_ = self.runtime()
        self.assertIs(K.validate(K.HOMES), True)
        data = list(K.dataBytes(K.HOMES, True).values())
        self.assertEqual(len(data), 0x800)
        self.assertEqual(data[0], 1)
        self.assertEqual(struct.unpack_from('<I', bytes(data), 4)[0], len(homes()))
        for i, (loc, entry, presence) in enumerate(homes()):
            self.assertEqual(struct.unpack_from('<IHH', bytes(data), 0x40 + 8 * i), (loc, entry, presence))
        bad = lua.table_from([lua.table_from([228, 292, 1])])
        self.assertEqual(K.validate(bad), (False, 'entry 1'))    # 292 is a boss, not a rare game

    def test_install_patches_block_first_then_sites(self):
        lua, K, mem, calls, lines = self.runtime()
        self.assertTrue(K.register())
        kinds = [c[1] for c in calls if c[0] != 'alloc']
        self.assertEqual(kinds, ['block', 0x3504BE, 0x510178])
        self.assertEqual(int.from_bytes(bytes(mem[0x50000100 + i] for i in range(8)), 'little'), 0x60000000)
        self.assertEqual(mem[0x60000000], 1)                   # switched on once installed
        self.assertTrue(any('installed' in s for s in lines))

    def test_nothing_patched_without_legacy_build_or_original_sites(self):
        for kwargs in ({'legacy': False}, {'sites_ok': False}):
            lua, K, mem, calls, lines = self.runtime(**kwargs)
            self.assertFalse(K.register())
            self.assertEqual([c for c in calls if c[0] != 'alloc'], [])
            self.assertTrue(any('not installed' in s for s in lines))

    def test_install_retried_once_on_the_first_map_change(self):
        lua, K, mem, calls, lines = self.runtime(legacy=False)
        self.assertFalse(K.register())
        for i, b in enumerate(b'MZ'):
            mem[0x120000 + i] = b
        for i, b in enumerate(b'PE\0\0\x64\x86'):
            mem[0x120180 + i] = b
        for i, b in enumerate(b'LEGCY_\0'):
            mem[0xD113D4 + i] = b
        for i, b in enumerate('%sLEGCY_%03d\0'.encode('utf-16-le')):
            mem[0xD12200 + i] = b
        self.handlers['onMapJump'](891)
        self.assertEqual([c[1] for c in calls if c[0] != 'alloc'], ['block', 0x3504BE, 0x510178])
        self.assertEqual(mem[0x60000000], 1)

    def test_install_given_up_after_the_retry(self):
        lua, K, mem, calls, lines = self.runtime(legacy=False)
        K.register()
        self.handlers['onMapJump'](891)
        self.handlers['onMapJump'](892)
        self.assertEqual([c for c in calls if c[0] != 'alloc'], [])
        self.assertEqual(sum('not installed' in s for s in lines), 2)

    def test_rejected_block_patches_no_site(self):
        lua, K, mem, calls, lines = self.runtime(fail_assemble=True)
        self.assertFalse(K.register())
        self.assertEqual([c[1] for c in calls if c[0] != 'alloc'], ['block'])

    def test_report_names_kings_of_the_location(self):
        lua, K, mem, calls, lines = self.runtime()
        K.register()
        mem[KILLS + 336] = 1                                   # Ishteen defeated, Minibug alive
        K.report(891)
        self.assertIn('location 891: 320 alive, 336 defeated', lines[-1])

    def test_entry_script_loads_runtime(self):
        text = ENTRY.read_text(encoding='utf-8')
        self.assertIn("loadfile('scripts/IvaliceLegacy/kings_runtime.lua', 't', _ENV)", text)
        self.assertIn('K.register', text)


@unittest.skipIf(keystone is None, 'requires keystone-engine and unicorn')
class HookTest(unittest.TestCase):
    UNIT_STRIDE, CLASS_STRIDE = 0x58, 0x60

    def machine(self, on=True, location=891, kills=(), unit_class=None, class_entry=None, tables=True):
        ks = keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_64)
        blob, _ = ks.asm(code(), CODE_AT)
        blob = bytes(blob)
        uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
        for base, size in ((CODE_AT, 0x10000), (DATA_AT, 0x10000), (STACK_AT, 0x10000), (TABLES_AT, 0x100000),
                           (0x01F80000, 0x10000), (0x02160000, 0x20000), (0x02EB0000, 0x20000),
                           (0x00350000, 0x10000), (0x00510000, 0x10000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE_AT, blob)
        uc.mem_write(CODE_AT + len(blob) - 8, struct.pack('<Q', DATA_AT))          # legacy_kings_data
        data = bytearray(0x800)
        data[0] = 1 if on else 0
        h = homes()
        struct.pack_into('<I', data, 4, len(h))
        for i, entry in enumerate(h):
            struct.pack_into('<IHH', data, 0x40 + 8 * i, *entry)
        uc.mem_write(DATA_AT, bytes(data))
        uc.mem_write(LOCATION, struct.pack('<I', location))
        for entry in kills:
            uc.mem_write(KILLS + entry, b'\x03')
        # Area tables: descriptor {?, count, stride, offset}; data = offset + [TABLE_BASE].
        uc.mem_write(TABLE_BASE, struct.pack('<Q', TABLES_AT))
        if tables:
            unit_desc, class_desc = TABLES_AT + 0x100, TABLES_AT + 0x200
            uc.mem_write(unit_desc, struct.pack('<IIHHI', 0, 12, self.UNIT_STRIDE, 0, 0x1000))
            uc.mem_write(class_desc, struct.pack('<IIHHI', 0, 20, self.CLASS_STRIDE, 0, 0x8000))
            uc.mem_write(UNIT_TABLE, struct.pack('<Q', unit_desc))
            uc.mem_write(CLASS_TABLE, struct.pack('<Q', class_desc))
            for row in range(12):                                                  # row r -> class r + 3
                uc.mem_write(TABLES_AT + 0x1000 + row * self.UNIT_STRIDE, struct.pack('<H', row + 3))
            for cls in range(20):
                uc.mem_write(TABLES_AT + 0x8000 + cls * self.CLASS_STRIDE + 0x52, struct.pack('<H', 10 + cls))
            if unit_class is not None:
                row, entry = unit_class
                # The class table counts from 0; defeat counts and homes count from 1.
                uc.mem_write(TABLES_AT + 0x8000 + (row + 3) * self.CLASS_STRIDE + 0x52, struct.pack('<H', entry - 1))
        for at in (0x003504C3, 0x0051017D):
            uc.mem_write(at, b'\xf4')
        return uc, blob

    def execute(self, uc, start, eax, stop):
        rsp = STACK_AT + 0x8000
        for reg, value in ((X.UC_X86_REG_RSP, rsp), (X.UC_X86_REG_RAX, eax), (X.UC_X86_REG_RBX, 0x7777),
                           (X.UC_X86_REG_RDI, 0x8888), (X.UC_X86_REG_RSI, 0x9999), (X.UC_X86_REG_RBP, 0xAAAA),
                           (X.UC_X86_REG_R12, 0x1212), (X.UC_X86_REG_R13, 0x1313), (X.UC_X86_REG_R14, 0x1414),
                           (X.UC_X86_REG_R15, 0x1515), (X.UC_X86_REG_RCX, 0x5555), (X.UC_X86_REG_RDX, 0x6666)):
            uc.reg_write(reg, value)
        uc.emu_start(start, stop + 1, count=20000)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RIP), stop + 1)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RSP), rsp)
        for reg, value in ((X.UC_X86_REG_RBX, 0x7777), (X.UC_X86_REG_RDI, 0x8888), (X.UC_X86_REG_RSI, 0x9999),
                           (X.UC_X86_REG_RBP, 0xAAAA), (X.UC_X86_REG_R12, 0x1212), (X.UC_X86_REG_R13, 0x1313),
                           (X.UC_X86_REG_R14, 0x1414), (X.UC_X86_REG_R15, 0x1515)):
            self.assertEqual(uc.reg_read(reg), value)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RCX), 0x7777)                   # mov rcx,rbx still runs
        eax = uc.reg_read(X.UC_X86_REG_RAX) & 0xFFFFFFFF
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RDX) & 0xFFFFFFFF, eax)        # mov edx,eax still runs
        return eax

    def rare(self, native=0, **kw):
        uc, _ = self.machine(**kw)
        return self.execute(uc, CODE_AT, native, 0x003504C3), uc

    def unit(self, row, **kw):
        uc, _ = self.machine(**kw)
        return self.execute(uc, self.unit_start(), row, 0x0051017D), uc

    @staticmethod
    def unit_start():
        # The second hook starts where the first one's source ends.
        ks = keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_64)
        source = code()
        prefix = source[:source.index('legacy_kings_unit:')]
        # A stand-in data label resolves the first hook's reference; its rip-relative encoding has a fixed size.
        return CODE_AT + len(bytes(ks.asm(prefix + '\nlegacy_kings_data:\n', CODE_AT)[0]))

    @staticmethod
    def counters(uc):
        """(presence forced, last vetoed entry, vetoes, last forced location)"""
        return struct.unpack('<IIII', bytes(uc.mem_read(DATA_AT + 0x8, 16)))

    # ---- presence (0x5A9) ----
    def test_living_king_forces_presence(self):
        eax, uc = self.rare(location=891)                    # Minibug and Ishteen live in East-West Bypass
        self.assertEqual(eax, 1)
        forced, _, _, where = self.counters(uc)
        self.assertEqual((forced, where), (1, 891))

    def test_dead_kings_leave_the_native_answer(self):
        for native in (0, 1):
            eax, _ = self.rare(native=native, location=891, kills=(320, 336))
            self.assertEqual(eax, native)

    def test_one_living_king_is_enough(self):
        eax, _ = self.rare(location=891, kills=(336,))
        self.assertEqual(eax, 1)

    def test_other_locations_and_natural_homes_are_untouched(self):
        for loc in (70, 302, 137):                           # Barheim inner map, Clan hall, Grimalkin (presence 0)
            eax, _ = self.rare(native=0, location=loc)
            self.assertEqual(eax, 0)

    def test_switch_off(self):
        eax, _ = self.rare(native=0, location=891, on=False)
        self.assertEqual(eax, 0)

    # ---- veto (btlAtelSetUnit) ----
    def test_dead_king_unit_becomes_no_unit_in_its_home(self):
        eax, uc = self.unit(4, location=891, kills=(320,), unit_class=(4, 320))
        self.assertEqual(eax, 0xFF)
        _, last, vetoes, _ = self.counters(uc)
        self.assertEqual((last, vetoes), (320, 1))

    def test_dead_king_outside_its_home_is_kept(self):        # e.g. Trial Mode or an event using the same class
        eax, _ = self.unit(4, location=302, kills=(320,), unit_class=(4, 320))
        self.assertEqual(eax, 4)

    def test_living_king_and_ordinary_units_are_kept(self):
        self.assertEqual(self.unit(4, location=891, unit_class=(4, 320))[0], 4)
        self.assertEqual(self.unit(5, location=891, kills=(320,), unit_class=(4, 320))[0], 5)  # row 5: entry 19

    def test_the_previous_entrys_death_does_not_count(self):
        # Entry 319 is dead and Minibug (320) lives: reading the defeat count without adding 1 would look at 319.
        self.assertEqual(self.unit(4, location=891, kills=(319,), unit_class=(4, 320))[0], 4)

    def test_no_bestiary_entry_is_kept(self):
        # Adds such as Tiny Battery have 0xFFFF in the class table.
        uc, _ = self.machine(location=891, kills=(320,))
        uc.mem_write(TABLES_AT + 0x8000 + 7 * self.CLASS_STRIDE + 0x52, struct.pack('<H', 0xFFFF))
        self.assertEqual(self.execute(uc, self.unit_start(), 4, 0x0051017D), 4)

    def test_bad_rows_and_missing_tables_are_kept(self):
        self.assertEqual(self.unit(0xFF, location=891, kills=(320,), unit_class=(4, 320))[0], 0xFF)
        self.assertEqual(self.unit(12, location=891, kills=(320,), unit_class=(4, 320))[0], 12)   # row >= count
        self.assertEqual(self.unit(4, location=891, kills=(320,), tables=False)[0], 4)
        self.assertEqual(self.unit(4, location=891, kills=(320,), unit_class=(4, 320), on=False)[0], 4)


GAME = os.environ.get('IVALICE_LEGACY_GAME')


@unittest.skipUnless(GAME and (Path(GAME) / 'x64/FFXII_TZA.exe').is_file(), 'set IVALICE_LEGACY_GAME')
class ExecutableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = (Path(GAME) / 'x64/FFXII_TZA.exe').read_bytes()
        pe = struct.unpack_from('<I', cls.data, 0x3C)[0]
        count, opt = struct.unpack_from('<H', cls.data, pe + 6)[0], struct.unpack_from('<H', cls.data, pe + 20)[0]
        cls.base = struct.unpack_from('<Q', cls.data, pe + 24 + 24)[0]
        cls.sections = []
        for i in range(count):
            s = pe + 24 + opt + 40 * i
            vsize, va, rsize, raw = struct.unpack_from('<IIII', cls.data, s + 8)
            cls.sections.append((cls.base + va, max(vsize, rsize), raw))

    def at(self, va, n):
        for start, size, raw in self.sections:
            if start <= va < start + size:
                return self.data[raw + va - start: raw + va - start + n]
        raise KeyError(hex(va))

    def test_image_base(self):
        self.assertEqual(self.base, 0x120000)

    def test_rare_switch_site(self):
        # push rbx ; sub rsp,0x20 ; mov rbx,rcx ; call 0x17D430 ; [mov edx,eax ; mov rcx,rbx] ; add rsp,0x20 ; pop rbx
        code = self.at(0x3504B0, 0x1D)
        self.assertEqual(code[:9], bytes.fromhex('40534883ec20488bd9'))
        self.assertEqual(code[0x0E:0x13], bytes([0x8B, 0xD0, 0x48, 0x8B, 0xCB]))
        self.assertEqual(code[0x13:0x18], bytes.fromhex('4883c4205b'))
        self.assertEqual(code[0x18], 0xE9)                    # jmp 0x26B4E0 (push the answer)
        self.assertEqual(0x3504C8 + 5 + struct.unpack_from('<i', code, 0x19)[0], 0x26B4E0)

    def test_unit_site(self):
        # mov rcx,r9 ; mov rdi,r8 ; mov rbx,rdx ; call 0x267E10 (pop row) ; [mov edx,eax ; mov rcx,rbx] ; call 0x23A3B0
        code = self.at(0x51016A, 0x18)
        self.assertEqual(code[:9], bytes.fromhex('498bc9498bf8488bda'))
        self.assertEqual(code[0x0E:0x13], bytes([0x8B, 0xD0, 0x48, 0x8B, 0xCB]))
        self.assertEqual(code[0x13], 0xE8)
        self.assertEqual(0x51017D + 5 + struct.unpack_from('<i', code, 0x14)[0], 0x23A3B0)

    def test_no_unit_value(self):
        # 0x23A3B0: cmp ebx,0xFF ; je -> mov ebx,0xFFFF (0xFF means "no unit")
        self.assertEqual(self.at(0x23A3D9, 6), bytes.fromhex('81fbff000000'))
        self.assertEqual(self.at(0x23A3E7, 5), bytes.fromhex('bbffff0000'))

    def test_save_image_and_tables(self):
        # 0x2EF640: lea rax,[rip+...] -> save image 0x2164280 (cleared with size 0x56690 at 0x2EF5F6)
        self.assertEqual(0x2EF647 + struct.unpack_from('<i', self.at(0x2EF643, 4))[0], 0x2164280)
        self.assertEqual(self.at(0x2EF5FD, 6), bytes.fromhex('41b890660500'))
        self.assertEqual(KILLS, 0x2164280 + 0xB603)
        self.assertEqual(LOCATION, 0x2164280 + 0x1244)
        # 0x20E600: mov eax,ecx ; add rax,[rip+...] -> TABLE_BASE
        self.assertEqual(0x20E609 + struct.unpack_from('<i', self.at(0x20E605, 4))[0], TABLE_BASE)

    def test_class_table_counts_from_zero(self):
        # The field the unit hook reads: dst_a's Ripe Rampager, Nekhbet and Greeden, one below their K.HOMES entries.
        from ivalice_legacy.vbf import VBF
        raw = VBF(Path(GAME) / 'FFXII_TZA.vbf').read('ps2data/plan_master/in/plan_map/dst_a/area/dst_a.ard')
        self.assertEqual(raw[:8], b'FF12AR03')
        table = raw[struct.unpack_from('<10I', raw, 8)[2]:]
        self.assertEqual(table[:4], b'st2e')
        count, stride, offset = struct.unpack_from('<III', table, 4)
        stride &= 0xFFFF
        fields = [struct.unpack_from('<H', table, offset + i * stride + 0x52)[0] for i in range(count)]
        h = {(l, e) for l, e, _ in homes()}
        for location, entry in ((228, 316), (229, 331), (230, 297)):
            self.assertIn(entry - 1, fields)
            self.assertNotIn(entry, fields)
            self.assertIn((location, entry), h)


if __name__ == '__main__':
    unittest.main()
