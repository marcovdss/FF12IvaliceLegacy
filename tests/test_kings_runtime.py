"""Kings of the zone runtime (package/x64/scripts/IvaliceLegacy/kings_runtime.lua).

- Lua (Lupa): the home table is valid, the data block layout, and install() patches nothing unless the Legacy build,
  the original site and the original command table are found (block first, then site, then table).
- Native (Keystone + Unicorn): both pieces run against stand-ins for the game's data at their real addresses: every
  recognised spawner (a King's own, or a writer thread of an indirect King) is forced for its own King only, and only
  until that King is defeated; the spawn cancel is skipped only for the presence-3 pairs, keyed on (location, thread
  index); everything else keeps native behaviour; callee-saved registers and the stack are untouched.
- Game executable (when IVALICE_LEGACY_GAME is set): the site holds the bytes the hook displaces, and the code around
  it is what the pieces assume.
"""
import json
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
TABLE_BASE = 0x01F83530
DATA_SLOT, KEEP_ADDRESS, CANCEL_SLOT, CANCEL_HANDLER = 0x50000100, 0x50000200, 0x01F28B70, 0x005102D0
CALLS = {0x58, 0x59, 0x5D}                              # the VM's command-call opcodes


def homes():
    body = re.search(r'K\.HOMES = \{[^\n]*\n(.*?)\n\}', SOURCE, re.S).group(1)
    return [tuple(int(x) for x in m) for m in re.findall(r'\{(\d+), (\d+), ([0123])\}', body)]


def spawners():
    body = re.search(r'K\.SPAWNERS = \{[^\n]*\n(.*?)\n\}', SOURCE, re.S).group(1)
    return [tuple(int(x) for x in m) for m in re.findall(r'\{(\d+), (\d+), (\d+)\}', body)]


def cancel_skips():
    body = re.search(r'K\.CANCEL_SKIPS = \{[^\n]*\n(.*?)\n\}', SOURCE, re.S).group(1)
    return [tuple(int(x) for x in m) for m in re.findall(r'\{(\d+), (\d+), (\d+)\}', body)]


def code():
    return re.search(r'K\.RARE_CODE = \[\[\n(.*?)\]\]', SOURCE, re.S).group(1).replace('.dq 0x00', '.quad 0')


def variables_in(code):
    """The variables a selection reads, in order."""
    out, pc = [], 0
    while pc < len(code):
        if code[pc] == 0x48:
            out.append(struct.unpack_from('<H', code, pc + 1)[0])
        pc += 3 if code[pc] >= 0x48 else 1
    return out


def run_vm(code, rand, variables, switch):
    """The few opcodes of a selection, from the interpreter 0x25E550: 0x4F push, 0x48 read a variable, 0x58 a command
    (0x29 rand, 0x5A9 switch), 0x0D <, 0x0A ==, 0x05 OR, 0x06 AND. Comparisons and logic leave 0 or 1."""
    stack, pc = [], 0
    while pc < len(code):
        op = code[pc]
        if op in (0x4F, 0x48, 0x58):
            arg = struct.unpack_from('<H', code, pc + 1)[0]
            pc += 3
            if op == 0x4F:
                stack.append(arg)
            elif op == 0x48:
                stack.append(variables[arg])
            elif arg == 0x29:
                stack.pop()
                stack.append(rand)
            else:
                assert arg == 0x5A9
                stack.append(switch)
            continue
        pc += 1
        b, a = stack.pop(), stack.pop()
        stack.append(int({0x0D: a < b, 0x0A: a == b, 0x05: bool(a or b), 0x06: bool(a and b)}[op]))
    assert len(stack) == 1
    return stack[0]


class HomeTableTest(unittest.TestCase):
    def test_every_rare_game_has_a_home(self):
        h = homes()
        self.assertEqual({e for _, e, _ in h}, set(range(293, 373)))  # all 80 Bestiary rare-game entries
        self.assertEqual(len(h), len(set((l, e) for l, e, _ in h)))

    def test_only_spawners_without_a_way_in_stay_natural(self):
        h = homes()
        # Spawners that never ask the switch: Larva Eater's gil_e/f/g, Aeros, Ishteen in mic_e01.
        self.assertEqual({(l, e) for l, e, p in h if p == 0 and e != 342}, {(536, 328), (891, 336)})
        self.assertEqual({e for _, e, p in h if p == 0}, {342, 328, 336})
        self.assertEqual([x for x in h if x[2] == 1], [])                # no home may depend on recognising the spawner
        for location, entry in ((133, 330), (137, 329), (247, 349), (902, 358), (903, 358), (411, 335), (947, 337)):
            self.assertIn((location, entry, 2), h)
        self.assertEqual([x for x in h if x[2] == 3], [(216, 345, 3)])  # Grey Molter alone relies on skipping its cancel

    def test_no_absolute_memory_operands(self):
        # "[0x...]" may be assembled rip-relative for registers other than rax (Keystone does): load addresses first.
        self.assertEqual(re.findall(r'\[0x[0-9A-Fa-f]+\]', code()), [])

    def test_units_are_never_replaced(self):
        # Every spawner keeps writing through the unit's work pointer after btlAtelSetUnit: "no unit" crashes the game
        # (2026-11-04, Sand-swept Naze: SetDirection at 0x239210 on a null [actor+0x6A0]).
        self.assertNotIn('0x00510178', SOURCE)
        self.assertNotIn('mov eax,0xFF', code())

    def test_no_king_is_ever_blocked(self):
        # After a defeat a King follows the game's own conditions: nothing may veto its spawner, and a data edit may
        # only add the switch to a natural condition (natural OR switch), never force the spawn.
        self.assertNotIn('legacy_kings_hold', SOURCE)
        patch = json.loads((ROOT / 'patches/kings.json').read_text(encoding='utf-8'))
        self.assertEqual({k['king'] for k in patch['kings']},
                         {'Cultsworn Lich', 'Wary Wolf', 'Matriarch Bomb', 'Grimalkin', 'Disma', 'Evil Spirit',
                          'Terror Tyrant', 'Negalmuur', 'Crystal Knight', 'Aspidochelon'})
        for king in patch['kings']:
            self.assertNotIn('immediates', king)
            for edit in king['replacements']:
                before, after = edit['before'], edit['after']
                if king['king'] == 'Cultsworn Lich':
                    self.assertEqual((before, after), ('4f01000a', '58a90505'))   # == 1 becomes OR switch
                elif king['king'] == 'Aspidochelon':
                    self.aspidochelon_table(before, after)
                elif len(before) == 6:
                    # Matriarch Bomb's later "if switch" is now in the roll; the others are switch-only spawners.
                    self.assertIn(king['king'], ('Matriarch Bomb', 'Terror Tyrant', 'Negalmuur', 'Crystal Knight'))
                    self.assertEqual((before, after), ('58a905', '4f0000'))
                else:
                    self.roll_table(before, after)

    def aspidochelon_table(self, before, after):
        """Its Hunt Club gate and its three checks. Both edits keep the length."""
        old, new = bytes.fromhex(before), bytes.fromhex(after)
        self.assertEqual(len(old), len(new))
        if len(old) == 4:
            # gate == 1 becomes gate <= switch (the variable is read just before): free while it lives, whatever the quest
            # says; with the trophy earned the gate is 2 and it stays gone; no switch and no quest, no entry.
            self.assertEqual((old, new), (bytes.fromhex('4f01000a'), bytes.fromhex('58a9050f')))
            for gate, switch, want in ((0, 1, 1), (1, 1, 1), (2, 1, 0), (2, 0, 0), (1, 0, 0)):
                self.assertEqual(int(gate <= switch), want, (gate, switch))
            return
        # Three checks jump to one wait, so they become one expression: B AND (roll OR switch) after the untouched A.
        self.assertEqual(new[-3:], old[-3:])
        b, roll = old[3:27], old[30:40]
        self.assertEqual(new[:-3], b + b'\x06' + roll + bytes.fromhex('58a905') + b'\x05\x06')

    def roll_table(self, before, after):
        """A rewritten selection equals the original when the switch says no, and when it says yes passes exactly when
        no other spawner holds the King yet (flag 0). The original's variables, in order: chance, flag, optional gate."""
        old, new = bytes.fromhex(before), bytes.fromhex(after)
        self.assertEqual(len(old), len(new))
        names = variables_in(old)
        for rand in range(0, 100, 3):
            for chance in (0, 5, 40, 100):
                for flag in (0, 1):
                    for gate in (0, 1, 2):
                        values = dict(zip(names, (chance, flag, gate)))
                        self.assertEqual(run_vm(new, rand, values, 0), run_vm(old, rand, values, 0))
                        self.assertEqual(run_vm(new, rand, values, 1), int(flag == 0))

    def test_known_homes(self):
        h = set(homes())
        for row in ((228, 316, 2),   # Ripe Rampager, Yardang Labyrinth (gated through dst_a02 thread 30's roll)
                    (230, 297, 2),   # Greeden, Banks of the Nebra: missing in 0.0.17 (2026-11-04)
                    (358, 294, 2),   # Aerieel, Oltam Span
                    (891, 320, 2),   # Minibug, East-West Bypass
                    (891, 336, 0),   # Ishteen, East-West Bypass: its spawner there never asks
                    (892, 336, 2),   # Ishteen, The Zeviah Span
                    (895, 322, 2),   # Ithuno, Special Op Sector 5
                    (374, 353, 2),   # Gemhorn, Site 11
                    (901, 310, 2),   # Bombshell, Lasche Span
                    (137, 329, 2)):  # Grimalkin, Uazcuff Hills
            self.assertIn(row, h)

    def test_spawner_table_covers_every_king_that_asks(self):
        two = spawners()
        self.assertEqual(len(two), len({(l, t) for l, t, _ in two}))       # one answer per (location, thread)
        h = {(l, e) for l, e, _ in homes()}
        for l, t, e in two:
            self.assertIn((l, e), h)                                       # every pair sits in a declared home
        covered = {e for _, _, e in two}
        # Aeros (328) and Grey Molter (345) never ask the switch: Aeros follows Bull Croc's v22 chain, and Grey
        # Molter's cancel is skipped instead (the 2026-11-07/08 RE studies).
        self.assertEqual(covered | {328, 345}, set(range(293, 373)))
        indirect = {316: 'Ripe Rampager', 317: 'Razorfin', 309: 'Dreadguard', 311: 'Crypt Bunny', 333: 'Glaring Eye'}
        for entry, name in indirect.items():
            self.assertIn(entry, covered, name)                            # the writer pairs of all five indirect Kings
        self.assertEqual(cancel_skips(), [(216, 20, 345)])                 # Grey Molter alone skips its cancel


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class LuaTest(unittest.TestCase):
    def runtime(self, mem=None, legacy=True, sites_ok=True, fail_assemble=False, table=0x5102D0, sticky=False,
                site_returns_zero=False, site_unchanged=False):
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
        write(CANCEL_SLOT, struct.pack('<Q', table))

        def read_array(at, n):
            return lua.table_from([mem.get(at + i, 0) for i in range(n)])

        def write_array(at, t):
            for i, v in enumerate(t.values()):
                mem[at + i] = v

        def assemble(src, where):
            calls.append((src, where if isinstance(where, int) else 'block'))
            if fail_assemble:
                raise RuntimeError('rejected')
            if isinstance(where, int):
                if site_returns_zero:                  # the loader's documented failure: (0, 0), nothing written
                    return 0
                if not site_unchanged:
                    mem[where] = 0xE9
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
                if sticky and at == CANCEL_SLOT:
                    return
                calls.append(('write', at))
                for i in range(self.size):
                    mem[at + i] = (v >> (8 * i)) & 0xFF
        symbols = {'legacy_kings_data': DATA_SLOT, 'legacy_kings_cancel': KEEP_ADDRESS}
        g.memory = lua.table_from({'readArray': read_array, 'writeArray': write_array, 'assemble': assemble,
                                   'alloc': alloc, 'getSymbol': lambda name: symbols.get(name, 0),
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
        self.assertEqual(len(data), 0x1000)
        self.assertEqual(data[0], 1)
        self.assertEqual(struct.unpack_from('<I', bytes(data), 4)[0], len(homes()))
        for i, (loc, entry, presence) in enumerate(homes()):
            self.assertEqual(struct.unpack_from('<IHH', bytes(data), 0x40 + 8 * i), (loc, entry, presence))
        two, skips = spawners(), cancel_skips()
        self.assertEqual(struct.unpack_from('<I', bytes(data), 0x24)[0], len(two))
        self.assertEqual(struct.unpack_from('<I', bytes(data), 0x2C)[0], len(skips))
        for i, entry in enumerate(two):
            self.assertEqual(struct.unpack_from('<IHH', bytes(data), 0x600 + 8 * i), entry)
        for i, entry in enumerate(skips):
            self.assertEqual(struct.unpack_from('<IHH', bytes(data), 0x600 + 8 * len(two) + 8 * i), entry)
        bad = lua.table_from([lua.table_from([228, 292, 1])])
        self.assertEqual(K.validate(bad), (False, 'entry 1'))    # 292 is a boss, not a rare game

    def test_install_order_block_then_site_then_table_then_on(self):
        lua, K, mem, calls, lines = self.runtime()
        self.assertTrue(K.register())
        kinds = [c[1] for c in calls if c[0] != 'alloc']
        self.assertEqual(kinds, ['block', DATA_SLOT, 0x3504BE, CANCEL_SLOT, 0x60000000])
        self.assertEqual(int.from_bytes(bytes(mem[DATA_SLOT + i] for i in range(8)), 'little'), 0x60000000)
        self.assertEqual(int.from_bytes(bytes(mem[CANCEL_SLOT + i] for i in range(8)), 'little'), KEEP_ADDRESS)
        self.assertEqual(mem[0x60000000], 1)                   # switched on once installed
        self.assertTrue(any('installed' in s for s in lines))

    def test_nothing_patched_without_legacy_build_or_original_game(self):
        for kwargs in ({'legacy': False}, {'sites_ok': False}, {'table': 0x12345}):
            lua, K, mem, calls, lines = self.runtime(**kwargs)
            self.assertFalse(K.register())
            self.assertEqual([c for c in calls if c[0] != 'alloc'], [])
            self.assertTrue(any('not installed' in s for s in lines))

    def test_table_write_that_does_not_stick_stays_off(self):
        lua, K, mem, calls, lines = self.runtime(sticky=True)
        self.assertFalse(K.register())
        self.assertEqual(mem[0x60000000], 0)                   # the block stays inert
        self.assertTrue(any('kept its old value' in s for s in lines))

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
        self.assertEqual([c[1] for c in calls if c[0] != 'alloc'],
                         ['block', DATA_SLOT, 0x3504BE, CANCEL_SLOT, 0x60000000])
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

    def test_site_that_was_not_patched_stays_off(self):
        # memory.assemble's documented failure is a 0 return, not an error; a site still holding its bytes is no better.
        for kwargs in ({'site_returns_zero': True}, {'site_unchanged': True}):
            lua, K, mem, calls, lines = self.runtime(**kwargs)
            self.assertFalse(K.register())
            self.assertEqual(mem[0x60000000], 0)               # the block stays inert
            self.assertNotIn(CANCEL_SLOT, [c[1] for c in calls if c[0] == 'write'])
            self.assertTrue(any('rejected' in s for s in lines))
            self.assertFalse(any('installed:' in s for s in lines))

    def test_report_names_kings_of_the_location(self):
        lua, K, mem, calls, lines = self.runtime()
        K.register()
        mem[KILLS + 336] = 2                                   # Ishteen: one kill, the byte's lowest "defeated" value
        mem[KILLS + 320] = 1                                   # Minibug: recorded, never killed
        K.report(891)
        self.assertIn('location 891: 320 alive, 336 defeated', lines[-1])

    def test_entry_script_loads_runtime(self):
        text = ENTRY.read_text(encoding='utf-8')
        self.assertIn("loadfile('scripts/IvaliceLegacy/kings_runtime.lua', 't', _ENV)", text)
        self.assertIn('K.register', text)


@unittest.skipIf(keystone is None, 'requires keystone-engine and unicorn')
class HookTest(unittest.TestCase):
    THREAD, ACTOR, CLASS = TABLES_AT + 0x1000, TABLES_AT + 0x2000, TABLES_AT + 0x8000
    RETURN = 0x00511000

    def machine(self, on=True, location=891, kills=(), own=None, flags=0x1D, cls=True, actor=True, seen=(), thread=0):
        ks = keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_64)
        blob, _ = ks.asm(code(), CODE_AT)
        blob = bytes(blob)
        uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
        for base, size in ((CODE_AT, 0x10000), (DATA_AT, 0x10000), (STACK_AT, 0x10000), (TABLES_AT, 0x100000),
                           (0x02160000, 0x20000), (0x00350000, 0x10000), (0x00510000, 0x10000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE_AT, blob)
        uc.mem_write(CODE_AT + len(blob) - 8, struct.pack('<Q', DATA_AT))          # legacy_kings_data
        data = bytearray(0x1000)
        data[0] = 1 if on else 0
        h = homes()
        struct.pack_into('<I', data, 4, len(h))
        for i, entry in enumerate(h):
            struct.pack_into('<IHH', data, 0x40 + 8 * i, *entry)
        two = spawners()
        struct.pack_into('<I', data, 0x24, len(two))
        for i, entry in enumerate(two):
            struct.pack_into('<IHH', data, 0x600 + 8 * i, *entry)
        skips = cancel_skips()
        struct.pack_into('<I', data, 0x2C, len(skips))
        for i, entry in enumerate(skips):
            struct.pack_into('<IHH', data, 0x600 + 8 * len(two) + 8 * i, *entry)
        uc.mem_write(DATA_AT, bytes(data))
        uc.mem_write(LOCATION, struct.pack('<I', location))
        for entry in kills:
            uc.mem_write(KILLS + entry, b'\x02')                                   # exactly one kill
        for entry in seen:
            uc.mem_write(KILLS + entry, b'\x01')                                   # recorded, never killed
        uc.mem_write(0x003504C3, b'\xf4')
        uc.mem_write(CANCEL_HANDLER, b'\xf4')
        uc.mem_write(self.RETURN, b'\xf4')
        # The calling thread: +0 index; +0x30 actor; actor flags, +0xE68 class; class +0x52 = Bestiary entry - 1 (own=None: 0).
        uc.mem_write(self.THREAD, struct.pack('<H', thread))
        uc.mem_write(self.THREAD + 0x30, struct.pack('<Q', self.ACTOR if actor else 0))
        uc.mem_write(self.ACTOR, struct.pack('<Q', flags))
        uc.mem_write(self.ACTOR + 0xE68, struct.pack('<Q', self.CLASS if cls else 0))
        if own is not None:
            uc.mem_write(self.CLASS + 0x52, struct.pack('<H', (own - 1) & 0xFFFF))
        return uc

    def rare(self, native=0, **kw):
        uc = self.machine(**kw)
        rsp, stop = STACK_AT + 0x8000, 0x003504C3
        saved = dict(RBX=self.THREAD, RDI=0x8888, RSI=0x9999, RBP=0xAAAA, R12=0x1212, R13=0x1313, R14=0x1414, R15=0x1515)
        uc.reg_write(X.UC_X86_REG_RSP, rsp)
        for name, value in dict(saved, RAX=native, RCX=0x5555, RDX=0x6666).items():
            uc.reg_write(getattr(X, 'UC_X86_REG_' + name), value)
        uc.emu_start(CODE_AT, stop + 1, count=20000)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RIP), stop + 1)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RSP), rsp)
        for name, value in saved.items():
            self.assertEqual(uc.reg_read(getattr(X, 'UC_X86_REG_' + name)), value)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RCX), self.THREAD)              # mov rcx,rbx still runs
        eax = uc.reg_read(X.UC_X86_REG_RAX) & 0xFFFFFFFF
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RDX) & 0xFFFFFFFF, eax)        # mov edx,eax still runs
        return eax, uc

    @staticmethod
    def counters(uc):
        """(presence forced, last forced location)"""
        return struct.unpack('<I', bytes(uc.mem_read(DATA_AT + 0x8, 4)))[0], struct.unpack('<I', bytes(uc.mem_read(DATA_AT + 0x14, 4)))[0]

    # ---- a King's own spawner ----
    def test_undefeated_king_forces_its_own_spawner(self):
        eax, uc = self.rare(location=891, own=320)
        self.assertEqual(eax, 1)
        self.assertEqual(self.counters(uc), (1, 891))

    def test_defeated_king_gets_the_native_answer(self):
        for native in (0, 1):                                # 1: the game's own answer, or Bountiful's option if left on
            eax, uc = self.rare(native=native, location=891, kills=(320,), own=320)
            self.assertEqual(eax, native)
            self.assertEqual(self.counters(uc)[0], 0)

    def test_a_king_recorded_without_a_kill_is_still_undefeated(self):
        # The byte is 1 once the game has recorded the King, with no kill: every native reader must still force it.
        eax, uc = self.rare(location=891, seen=(320,), own=320)
        self.assertEqual(eax, 1)
        self.assertEqual(self.rare(location=228, seen=(316,), own=2)[0], 1)                # another spawner, presence 2
        self.assertFalse(self.cancel(thread=20, location=216, seen=(345,))[0])             # the cancel is still skipped

    def test_a_high_kill_count_is_defeated(self):
        for byte in (2, 3, 0x7F, 0x80, 0xFF):
            # An unsigned comparison: a count past 127 must not read as "never killed".
            uc = self.machine(location=891, own=320)
            uc.mem_write(KILLS + 320, bytes([byte]))
            uc.reg_write(X.UC_X86_REG_RSP, STACK_AT + 0x8000)
            uc.reg_write(X.UC_X86_REG_RBX, self.THREAD)
            uc.reg_write(X.UC_X86_REG_RAX, 0)
            uc.emu_start(CODE_AT, 0x003504C4, count=20000)
            self.assertEqual(uc.reg_read(X.UC_X86_REG_RAX) & 0xFFFFFFFF, 0, byte)

    def test_the_living_neighbour_does_not_bring_a_defeated_king_back(self):
        # Wildwood Deep's two Kings: Kaiser Wolf defeated, Dustia alive. A recognised spawner answers for its own King.
        self.assertEqual(self.rare(location=346, kills=(305,), own=305)[0], 0)
        self.assertEqual(self.rare(location=346, kills=(305,), own=334)[0], 1)
        # Aeros's spawner never asks, so she cannot bring a defeated Bull Croc back.
        self.assertEqual(self.rare(location=536, kills=(299,), own=2)[0], 0)

    def test_dustia_stays_out_beside_a_living_kaiser_wolf(self):
        # Marco's session: Dustia (334) defeated, Kaiser Wolf (305) alive, and no asker recognised by its class.
        kills = (334,)
        eax, uc = self.rare(location=346, kills=kills, thread=21, actor=False)                  # Dustia's spawner
        self.assertEqual(eax, 0)
        self.assertEqual(self.counters(uc), (0, 0))
        self.assertEqual(self.rare(location=346, kills=kills, thread=21, actor=False, native=1)[0], 1)  # natural roll stands
        self.assertEqual(self.rare(location=346, kills=kills, thread=22, actor=False)[0], 1)    # Kaiser Wolf's spawner
        self.assertEqual(self.rare(location=346, kills=(305,), thread=22, actor=False)[0], 0)   # and the other way round
        self.assertEqual(self.rare(location=346, kills=(305,), thread=21, actor=False)[0], 1)

    def test_every_two_king_spawner_answers_for_its_own_king(self):
        for location, thread, entry in spawners():
            self.assertEqual(self.rare(location=location, thread=thread, actor=False)[0], 1, (location, thread))
            self.assertEqual(self.rare(location=location, thread=thread, actor=False, kills=(entry,))[0], 0, (location, thread))

    def test_a_king_only_recorded_counts_as_undefeated(self):
        # Byte 1 = recorded, no kill.
        self.assertEqual(self.rare(location=346, thread=21, actor=False, seen=(334,))[0], 1)

    def test_other_threads_and_maps_keep_the_location_answer(self):
        # The trophy thread on the same map, and a matching thread number on another map.
        self.assertEqual(self.rare(location=346, kills=(334,), thread=5, actor=False)[0], 1)
        self.assertEqual(self.rare(location=229, thread=21, actor=False)[0], 1)
        self.assertEqual(self.rare(location=229, kills=(331,), thread=21, actor=False)[0], 0)

    def test_two_king_spawners_are_counted(self):
        _, uc = self.rare(location=346, thread=21, actor=False)
        self.assertEqual(struct.unpack('<I', bytes(uc.mem_read(DATA_AT + 0x28, 4)))[0], 1)
        _, uc = self.rare(location=346, thread=5, actor=False)
        self.assertEqual(struct.unpack('<I', bytes(uc.mem_read(DATA_AT + 0x28, 4)))[0], 0)

    def test_nekhbet_in_the_map_that_crashed(self):
        # The game's own class field holds 330 for Nekhbet, entry 331, home 229.
        self.assertEqual(self.rare(location=229, kills=(331,), own=331)[0], 0)
        self.assertEqual(self.rare(location=229, own=331)[0], 1)

    def test_the_previous_entrys_defeat_does_not_count(self):
        # Entry 319 is defeated and Minibug (320) lives: reading the count without adding 1 would look at 319.
        self.assertEqual(self.rare(location=891, kills=(319,), own=320)[0], 1)

    def test_natural_homes_keep_the_native_answer(self):
        self.assertEqual(self.rare(native=0, location=621, own=342)[0], 0)   # Larva Eater, gil_e01
        self.assertEqual(self.rare(native=1, location=621, own=342)[0], 1)

    def test_presence_does_not_depend_on_recognising_the_spawner(self):
        # 2026-11-04, MAIN 0.0.17: a whole session with no yes and Greeden missing at Banks of the Nebra. Whatever the hook
        # reads from the asker, her map answers yes until she is defeated.
        for kw in (dict(own=297), dict(own=2), dict(cls=False), dict(actor=False), dict(flags=0x1)):
            self.assertEqual(self.rare(location=230, **kw)[0], 1, kw)
            self.assertEqual(self.rare(location=230, kills=(297,), **kw)[0], 0, kw)

    def test_the_hook_records_what_it_sees(self):
        uc = self.rare(location=230, own=297)[1]
        self.assertEqual(struct.unpack('<III', bytes(uc.mem_read(DATA_AT + 0x18, 12))), (1, 297, 0))
        uc = self.rare(location=230, cls=False)[1]
        self.assertEqual(struct.unpack('<III', bytes(uc.mem_read(DATA_AT + 0x18, 12))), (1, 0, 1))

    # ---- any other spawner ----
    def test_a_defeated_king_s_map_keeps_the_native_answer(self):
        for native in (0, 1):
            self.assertEqual(self.rare(native=native, location=229, kills=(331,), own=2)[0], native)

    def test_indirect_gates_follow_the_location_s_king(self):
        # Ripe Rampager waits for a roll in dst_a02 thread 30, whose own unit is a Cactite.
        self.assertEqual(self.rare(location=228, own=2)[0], 1)
        self.assertEqual(self.rare(location=228, own=2, kills=(316,))[0], 0)
        self.assertEqual(self.rare(location=228, own=316)[0], 1)
        self.assertEqual(self.rare(location=228, own=316, kills=(316,))[0], 0)

    def test_indirect_writer_threads_answer_for_their_own_king(self):
        # The five indirect Kings (2026-11-08 spawn map): the asking writer latches the variable the King's
        # non-asking reader consumes, so it must answer for that King alone.
        self.assertEqual(self.rare(location=228, thread=30, actor=False)[0], 1)                        # Rampager
        self.assertEqual(self.rare(location=228, thread=30, actor=False, kills=(316,))[0], 0)
        self.assertEqual(self.rare(location=313, thread=0, actor=False)[0], 1)                         # Razorfin
        self.assertEqual(self.rare(location=313, thread=0, actor=False, kills=(317,))[0], 0)
        self.assertEqual(self.rare(location=541, thread=0, actor=False)[0], 1)                         # Dreadguard
        self.assertEqual(self.rare(location=541, thread=0, actor=False, kills=(309,))[0], 0)
        self.assertEqual(self.rare(location=540, thread=32, actor=False)[0], 1)                        # Crypt Bunny
        self.assertEqual(self.rare(location=540, thread=32, actor=False, kills=(311,))[0], 0)
        self.assertEqual(self.rare(location=726, thread=23, actor=False)[0], 1)                        # Glaring Eye,
        self.assertEqual(self.rare(location=728, thread=24, actor=False)[0], 1)                        # both home maps
        self.assertEqual(self.rare(location=726, thread=23, actor=False, kills=(333,))[0], 0)

    def test_actors_without_a_battle_unit_count_as_other_spawners(self):
        # Party-style actors have no class, others no unit at all: +0xE68 must not be read without flag 4 (the
        # unmapped pointer would stop the emulator if it were), and a thread may have no actor.
        self.assertEqual(self.rare(location=228, cls=False)[0], 1)
        self.assertEqual(self.rare(location=228, actor=False)[0], 1)
        self.assertEqual(self.rare(location=70, cls=False)[0], 0)              # no King lives here
        uc = self.machine(location=228, flags=0x1)
        uc.mem_write(self.ACTOR + 0xE68, struct.pack('<Q', 0x7000000000))
        uc.reg_write(X.UC_X86_REG_RSP, STACK_AT + 0x8000)
        uc.reg_write(X.UC_X86_REG_RBX, self.THREAD)
        uc.reg_write(X.UC_X86_REG_RAX, 0)
        uc.emu_start(CODE_AT, 0x003504C4, count=20000)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RAX) & 0xFFFFFFFF, 1)

    def test_no_bestiary_entry_counts_as_another_spawner(self):
        # Adds such as Tiny Battery have 0xFFFF in the class table.
        self.assertEqual(self.rare(location=228, own=0x10000)[0], 1)
        self.assertEqual(self.rare(location=70, own=0x10000)[0], 0)

    def test_other_locations_are_untouched(self):
        for loc in (70, 302):                                # Barheim inner map, Clan hall
            for native in (0, 1):
                self.assertEqual(self.rare(native=native, location=loc, own=320)[0], native)

    def test_switch_off(self):
        self.assertEqual(self.rare(native=0, location=891, own=320, on=False)[0], 0)

    # ---- the spawn cancel: True = the game's handler runs, False = skipped ----
    @staticmethod
    def cancel_start():
        ks = keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_64)
        source = code()
        prefix = source[:source.index('legacy_kings_cancel:')]
        return CODE_AT + len(bytes(ks.asm(prefix + '\nlegacy_kings_data:\n', CODE_AT)[0]))

    def cancel(self, rdx=None, thread=0, **kw):
        """rdx: the handler's thread argument. None = a stand-in thread whose u16 [0] is `thread`;
        0 = the null pointer (the hook must run the game's handler)."""
        uc = self.machine(thread=thread, **kw)
        rsp = STACK_AT + 0x8000
        uc.mem_write(rsp - 8, struct.pack('<Q', self.RETURN))
        saved = dict(RBX=0x7777, RDI=0x8888, RSI=0x9999, RBP=0xAAAA, R12=0x1212, R13=0x1313, R14=0x1414, R15=0x1515)
        uc.reg_write(X.UC_X86_REG_RSP, rsp - 8)
        rdx_value = self.THREAD if rdx is None else rdx
        for name, value in dict(saved, RDX=rdx_value, RCX=self.THREAD).items():
            uc.reg_write(getattr(X, 'UC_X86_REG_' + name), value)
        uc.hook_add(unicorn.UC_HOOK_CODE, lambda u, a, s, d: u.emu_stop(), begin=CANCEL_HANDLER, end=CANCEL_HANDLER)
        uc.hook_add(unicorn.UC_HOOK_CODE, lambda u, a, s, d: u.emu_stop(), begin=self.RETURN, end=self.RETURN)
        uc.emu_start(self.cancel_start(), 0, count=20000)
        for name, value in saved.items():
            self.assertEqual(uc.reg_read(getattr(X, 'UC_X86_REG_' + name)), value)
        rip = uc.reg_read(X.UC_X86_REG_RIP)
        self.assertIn(rip, (CANCEL_HANDLER, self.RETURN))
        if rip == CANCEL_HANDLER:
            self.assertEqual(uc.reg_read(X.UC_X86_REG_RSP), rsp - 8)            # a clean tail jump, rdx intact
            self.assertEqual(uc.reg_read(X.UC_X86_REG_RDX), rdx_value)
        else:
            self.assertEqual(uc.reg_read(X.UC_X86_REG_RSP), rsp)                # ret popped the return address
        return rip == CANCEL_HANDLER, uc

    def test_undefeated_king_skips_its_cancel(self):
        ran, uc = self.cancel(thread=20, location=216)       # Grey Molter's spawner: mnt_b02 thread 20
        self.assertFalse(ran)
        self.assertEqual(struct.unpack('<I', bytes(uc.mem_read(DATA_AT + 0xC, 4)))[0], 1)

    def test_cancel_skips_without_reading_the_actor(self):
        # 0.7.0 keyed on the actor's class, but a never-entered spawner has no unit flag and no class row yet
        # (2026-11-07 RE study, Q3), so that key could never fire. The thread key must not dereference the actor.
        ran, uc = self.cancel(thread=20, location=216, actor=False, flags=0)   # no actor at all
        self.assertFalse(ran)
        uc = self.machine(thread=20, location=216, flags=0x1)                  # unit flag clear, wild class pointer
        uc.mem_write(self.ACTOR + 0xE68, struct.pack('<Q', 0x7000000000))
        rsp, saved = STACK_AT + 0x8000, dict(RBX=0x7777, RDX=self.THREAD, RCX=self.THREAD)
        uc.reg_write(X.UC_X86_REG_RSP, rsp - 8)
        uc.mem_write(rsp - 8, struct.pack('<Q', self.RETURN))
        for name, value in saved.items():
            uc.reg_write(getattr(X, 'UC_X86_REG_' + name), value)
        uc.hook_add(unicorn.UC_HOOK_CODE, lambda u, a, s, d: u.emu_stop(), begin=CANCEL_HANDLER, end=CANCEL_HANDLER)
        uc.hook_add(unicorn.UC_HOOK_CODE, lambda u, a, s, d: u.emu_stop(), begin=self.RETURN, end=self.RETURN)
        uc.emu_start(self.cancel_start(), 0, count=20000)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RIP), self.RETURN)           # skipped, no actor read
        self.assertEqual(struct.unpack('<I', bytes(uc.mem_read(DATA_AT + 0xC, 4)))[0], 1)

    def test_cancel_runs_for_everything_else(self):
        evil = next((l, t) for l, t, e in spawners() if e == 337)              # an Evil Spirit spawner thread
        cases = (dict(thread=20, location=216, kills=(345,)),  # defeated: the game's rules again
                 dict(thread=20, location=302),                # outside its home
                 dict(thread=20, location=621),                # a natural home
                 dict(location=evil[0], thread=evil[1]),       # Evil Spirit: skipping it would let all spawners enter
                 dict(thread=23, location=210),                # Wary Wolf's spare spawners keep their cancel
                 dict(thread=21, location=229),                # a forced King enters through its own branch
                 dict(thread=21, location=216),                # Grey Molter's map, another thread
                 dict(thread=26, location=216),                # an ordinary mnt_b02 spawner (the RE census: 23-26)
                 dict(thread=20, location=216, on=False),
                 dict(rdx=0, thread=20, location=216))         # null thread
        for case in cases:
            self.assertTrue(self.cancel(**case)[0], case)


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

    def test_handler_keeps_the_thread_in_rbx(self):
        # The executor calls a handler with rcx = the calling thread (0x2622F5 mov rcx,r14 before the call).
        self.assertEqual(self.at(0x262275, 3), bytes.fromhex('498bce'))
        self.assertEqual(self.at(0x3504B6, 3), bytes.fromhex('488bd9'))

    def test_cancel_handler_and_its_slot(self):
        # Set 3 id 0x4D: the handler at (id + 1) * 32; it reads only rdx: sub rsp,0x28 ; mov rcx,rdx ; call 0x31B760
        self.assertEqual(0x1F281B0 + 0x4E * 32, CANCEL_SLOT)
        self.assertEqual(struct.unpack('<Q', self.at(CANCEL_SLOT, 8))[0], CANCEL_HANDLER)
        self.assertEqual(self.at(CANCEL_HANDLER, 7), bytes.fromhex('4883ec28488bca'))

    def test_actor_fields(self):
        # 0x263E30: mov rax,[rcx+0x30] (thread -> actor). 0x23A91C: mov [rdi+0xE68],r15 (the class), flag 4 set later.
        self.assertEqual(self.at(0x263E30, 5), bytes.fromhex('488b4130c3'))
        self.assertEqual(self.at(0x23A91C, 7), bytes.fromhex('4c89bf680e0000'))
        # 0x23A59C: test byte ptr [rcx],4 is how the game itself asks "does this actor have a unit".
        self.assertEqual(self.at(0x23A59C, 3), bytes.fromhex('f60104'))

    def test_save_image_and_tables(self):
        # 0x2EF640: lea rax,[rip+...] -> save image 0x2164280 (cleared with size 0x56690 at 0x2EF5F6)
        self.assertEqual(0x2EF647 + struct.unpack_from('<i', self.at(0x2EF643, 4))[0], 0x2164280)
        self.assertEqual(self.at(0x2EF5FD, 6), bytes.fromhex('41b890660500'))
        self.assertEqual(KILLS, 0x2164280 + 0xB603)
        self.assertEqual(LOCATION, 0x2164280 + 0x1244)
        # 0x20E600: mov eax,ecx ; add rax,[rip+...] -> TABLE_BASE
        self.assertEqual(0x20E609 + struct.unpack_from('<i', self.at(0x20E605, 4))[0], TABLE_BASE)

    def test_kill_byte_encoding(self):
        # Getter: 0 -> -1, otherwise byte - 1 (cmp byte [rax+rbx+0x3404],0 ; ... ; movzx eax,byte ; dec eax).
        self.assertEqual(self.at(0x315440, 8), bytes.fromhex('80bc180434000000'))
        self.assertEqual(self.at(0x31545C, 8), bytes.fromhex('0fb6841804340000'))
        self.assertEqual(self.at(0x315469, 2), bytes.fromhex('ffc8'))
        # The routine that records an entry writes 1 and counts no kill (mov byte [rax+rbx+0x3404],1).
        self.assertEqual(self.at(0x315AB7, 8), bytes.fromhex('c684180434000001'))
        # A kill stores (byte - 1, or 0 from unknown) + 2, so a first kill stores 2.
        self.assertEqual(self.at(0x31586D, 2), bytes.fromhex('33db'))          # xor ebx,ebx from unknown
        self.assertEqual(self.at(0x31587B, 2), bytes.fromhex('ffc3'))          # inc ebx
        self.assertEqual(self.at(0x315887, 2), bytes.fromhex('fec3'))          # inc bl

    def decode(self, section, start, end):
        """One thread's instructions; the VM's instruction sizes come from the executable's table."""
        pc, out = start, []
        while pc < end and section[pc] < 0x64:
            op = section[pc]
            size = max(struct.unpack('<h', self.at(0x1EFEA60 + 24 * op, 2))[0], 1)
            out.append((pc, op, struct.unpack_from('<H', section, pc + 1)[0] if size == 3 else None))
            pc += size
        return out

    def threads(self, section):
        table = struct.unpack_from('<I', section, 0x18)[0]
        count = struct.unpack_from('<I', section, table)[0]
        stride = 48 if struct.unpack_from('<H', section, 0)[0] >= 8 else 32
        codes = [struct.unpack_from('<I', section, table + 4 + stride * i + 8)[0] for i in range(count)]
        ends = sorted(set(codes)) + [len(section)]
        return [(c, ends[ends.index(c) + 1]) for c in codes]

    def test_every_selection_of_a_king_tests_the_flag_first(self):
        # On the real scripts, after the edits: only the listed bytes changed, and every selection of every thread holding
        # the King, run as written, passes on a forced switch only while the shared flag is 0. A selection that skipped
        # the flag would let several spawners pick the King (2026-11-04 audit).
        from ivalice_legacy import patches
        from ivalice_legacy.vbf import VBF
        vbf = VBF(Path(GAME) / 'FFXII_TZA.vbf')
        patch = json.loads((ROOT / 'patches/kings.json').read_text(encoding='utf-8'))
        for king in patch['kings']:
            raw = vbf.read(king['file'])
            old = patches.ebp_section(raw, king['section'])
            edited = patches.kings(lambda name: raw, {'kings': [king]})[f"{king['file']}.dir/section_000.bin"]
            self.assertEqual(len(old), len(edited))
            changed = {i for i in range(len(old)) if old[i] != edited[i]}
            spans = set()
            for edit in king['replacements']:
                spans |= set(range(edit['sectionByteOffset'], edit['sectionByteOffset'] + len(edit['before']) // 2))
            self.assertLessEqual(changed, spans, king['area'])
            if 'kingRow' not in king:
                if king['king'] == 'Aspidochelon':
                    # Both its checks now ask the switch, in the one thread that holds it.
                    self.assertEqual(sum(bytes.fromhex('58a905') in bytes.fromhex(e['after']) for e in king['replacements']), 2)
                continue
            row = king['kingRow']
            holders, selections, switches = 0, 0, 0
            for start, end in self.threads(edited):
                new = self.decode(edited, start, end)
                first = next((new[i - 1][2] for i, x in enumerate(new) if x[1] in CALLS and x[2] == 0x3001 and i
                              and new[i - 1][1] == 0x4F), None)        # 0x3001 btlAtelSetUnit, its row pushed before it
                if first != row:
                    continue
                holders += 1
                switches += sum(x[1] in CALLS and x[2] == 0x5A9 for x in new)
                for i, x in enumerate(new):
                    if x[1] in CALLS and x[2] == 0x29:                 # rand: push 100 before it, the flag before that
                        selections += 1
                        self.assertEqual((new[i - 1][1], new[i - 1][2], new[i - 2][1]), (0x4F, 100, 0x48), king['area'])
                        jump = next(y for y in new[i:] if y[1] == 0x5C)
                        code = edited[new[i - 2][0]:jump[0]]
                        names = variables_in(code)
                        self.assertEqual(names[0], new[i - 2][2])
                        for flag, want in ((0, 1), (1, 0)):
                            # A roll that fails and a gate that is shut: only the forced switch can pass it.
                            values = dict(zip(names, (flag, 0, 0)))
                            self.assertEqual(run_vm(code, 99, values, 1), want, (king['area'], hex(x[0])))
                            self.assertEqual(run_vm(code, 99, values, 0), 0, (king['area'], hex(x[0])))
            self.assertGreater(holders, 1, king['area'])
            self.assertGreater(selections, 0, king['area'])
            # No other route may ask the switch: a separate "if switch: choose the King" would skip the flag. Grimalkin and
            # Disma also ask it in two checks that only keep a remembered group and set the flag; they choose no new King.
            per_selection = 3 if king['king'] in ('Grimalkin', 'Disma') else 1
            self.assertEqual(switches, selections * per_selection, king['area'])

    def test_class_table_counts_from_zero(self):
        # The field the hook reads: dst_a's Ripe Rampager, Nekhbet and Greeden, one below their K.HOMES entries.
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
