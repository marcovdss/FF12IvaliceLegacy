"""The Lua producers of the HUD states channel (PUSH_HUD_STATES): family_identities.hudStates (a read-only view of the
Family module's own tables, no rule changed) and coop/hud_states.lua (packs it into ilc_hud_states and pushes it from a
timer through the bridge). Run under lupa with the generated schema and the real codec. Nothing touches the game."""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    try:
        from lupa import LuaRuntime
    except ImportError:
        LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'package/x64/scripts/IvaliceLegacy/coop'
FAMILY = ROOT / 'prototypes/family_identities.lua'
S = 1_000_000  # one second of the event clock (microseconds)


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class HudStatesLuaTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.load = self.lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        self.family = self.load(FAMILY.read_text(encoding='utf-8'), 'family_identities')
        schema = self.load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        self.C = self.load((COOP / 'codec.lua').read_text(encoding='utf-8'), 'codec').new(schema)
        self.mod = self.load((COOP / 'hud_states.lua').read_text(encoding='utf-8'), 'hud_states')

    def test_an_empty_family_has_nothing_to_show(self):
        v = self.family.hudStates(10 * S, 0x1000)
        self.assertEqual((len(v.foes), len(v.words), len(v.procs)), (0, 0, 0))
        v = self.family.hudStates(0, 0x1000)
        self.assertEqual(len(v.foes), 0, 'no clock: nothing')

    def test_broken_dent_fatigue_and_words(self):
        root = 0x10000
        L = self.lua
        L.globals().fam = self.family
        L.execute(f'''
          local root = {root}
          local foe = {{type = 1, address = 0x900000, maxHp = 9800, level = 21, defense = 100}}
          fam.noteBroken(foe, {3 * S}, root + 8, false)
          local r = {{defense = -1}}
          fam.dent(foe, r, 1, 3000000)            -- 100 -> 95: 5 %
          fam.noteProc('SUNDER', root + 8, {3 * S})
        ''')
        v = self.family.hudStates(6 * S, root)
        self.assertEqual(len(v.foes), 1)
        f = v.foes[1]
        self.assertEqual((f.record, f.flags & 1, f.broken_s, f.dent_pct), (0x900000, 1, 7, 5),
                         'Broken 7 s left (10 s window), Defense -5%')
        self.assertEqual((len(v.procs), v.procs[1].tag, v.procs[1].record, v.procs[1].age_ms), (1, 'SUNDER', root + 8,
                                                                                               3000))
        v = self.family.hudStates(14 * S, root)
        self.assertEqual(v.foes[1].flags & 1, 0, 'Broken ends after 10 s; the dent stays for the fight')
        self.assertEqual(len(v.procs), 0, 'procs older than 5 s are dropped')

    def test_the_record_packs_into_the_wire_format(self):
        L = self.lua
        P = self.mod.create(L.table_from({'codec': self.C}))
        view = L.eval('''function() return {
            foes = {{record = 0x900000, flags = 1, broken_s = 7, dent_pct = 25, tired = 2}},
            words = {{member = 0, kind = 1, value = 3}, {member = 50, kind = 1, value = 3}},
            procs = {{record = 0x10008, tag = 'SUNDER', age_ms = 40}, {record = 0x10008, tag = 'NOPE', age_ms = 1}}}
            end''')()
        rec = P.record(view)
        self.assertEqual((rec.foe_count, rec.word_count, rec.proc_count), (1, 1, 1), 'a bad member and tag dropped')
        roundtrip = L.eval('''function(C, rec)
            local b = C.encode('ilc_hud_states', rec)
            local back = C.decode('ilc_hud_states', b)
            local errs = C.range_errors('ilc_hud_states', rec, 'rec', {})
            return #b, back.header.record_type, back.foes[1].broken_s, back.foes[1].crown_element, back.procs[1].tag,
                   #errs end''')
        self.assertEqual(tuple(roundtrip(self.C, rec)), (296, 12, 7, 255, 1, 0))

    def test_the_timer_pushes_and_stops_after_three_refusals(self):
        L = self.lua
        env = L.eval('''function(fam)
          local env = {queue = {}, pushed = {}, logs = {}, refuse = false}
          env.bridge = {is_ready = function() return true end,
                        push_hud_states = function(rec) env.pushed[#env.pushed + 1] = rec
                          if env.refuse then return nil, 'BAD_FIELD' end return {} end}
          env.after = function(ms, fn) env.queue[#env.queue + 1] = fn end
          env.opts = {bridge = env.bridge, after = env.after, clock = function() return 5000000 end,
                      view = function() return fam.hudStates(5000000, 0x10000) end,
                      log = function(t) env.logs[#env.logs + 1] = t end}
          return env end''')(self.family)
        env.opts.codec = self.C
        P = self.mod.create(env.opts)
        P.start()
        run = L.eval('function(env) local fn = table.remove(env.queue, 1); if fn then fn() end end')
        run(env)
        self.assertEqual((len(env.pushed), P.pushes), (1, 1))
        env.refuse = True
        for _ in range(3):
            run(env)
        self.assertEqual(P.refused, 3)
        self.assertEqual(len(env.logs), 1)
        self.assertEqual(len(env.queue), 0, 'stopped: no timer re-armed')


    def test_a_dent_ages_out_of_the_hud_but_the_rule_keeps_its_baseline(self):
        L = self.lua
        L.globals().fam = self.family
        L.execute(f"""
          local foe = {{type = 1, address = 0x900000, maxHp = 9800, level = 21, defense = 100}}
          fam.dent(foe, {{defense = -1}}, 1, 3 * {S})
        """)
        self.assertEqual(self.family.hudStates(10 * S, 0x1000).foes[1].dent_pct, 5)
        self.assertEqual(len(self.family.hudStates(3 * S + self.family.DENT_HUD_WINDOW + 1, 0x1000).foes), 0,
                         'a dent nobody has deepened for a minute is not shown (a later foe at the address inherits none)')
        # gameplay unchanged: the next hit deepens from the remembered baseline (100, step 5), not from the dented value
        out = L.eval(f"""(function()
          local foe = {{type = 1, address = 0x900000, maxHp = 9800, level = 21, defense = 95}}
          local r = {{defense = -1}}
          fam.dent(foe, r, 1, 100 * {S})
          return r.defense end)()""")
        self.assertEqual(out, 90)
        self.assertEqual(self.family.hudStates(101 * S, 0x1000).foes[1].dent_pct, 10, 'a fresh hit shows it again')

    def test_the_timer_survives_a_build_error_and_clamps_values(self):
        L = self.lua
        env = L.eval("""function()
          local env = {queue = {}, pushed = {}, logs = {}, view = {}}
          env.bridge = {is_ready = function() return true end,
                        push_hud_states = function(rec) env.pushed[#env.pushed + 1] = rec return {} end}
          env.opts = {bridge = env.bridge, after = function(ms, fn) env.queue[#env.queue + 1] = fn end,
                      view = function() return env.view end, log = function(t) env.logs[#env.logs + 1] = t end}
          return env end""")()
        env.opts.codec = self.C
        P = self.mod.create(env.opts)
        P.start()
        run = L.eval('function(env) local fn = table.remove(env.queue, 1); if fn then fn() end end')
        env.view = L.eval('{foes = 5}')            # ipairs(5) raises inside the record builder
        run(env)
        self.assertEqual((P.errors, len(env.logs), len(env.queue), len(env.pushed)), (1, 1, 1, 0),
                         'the error is logged once and the timer is re-armed')
        env.view = L.eval("""{foes = {{record = 0x900000, flags = 1.9, broken_s = -4, dent_pct = 77.7, tired = 2.5}},
                              words = {{member = 3.0, kind = 1, value = -1}}}""")
        run(env)
        self.assertEqual(len(env.pushed), 1, 'the next tick works')
        rec = env.pushed[1]
        f = rec.foes[1]
        self.assertEqual((f.flags, f.broken_s, f.dent_pct, f.tired, rec.words[1].value), (1, 0, 50, 2, 0))
        self.assertEqual(len(env.queue), 1)

    def test_the_wire_tag_codes_and_block_layout_agree_in_both_files(self):
        codes = {k: v for k, v in self.C.enums.hud_tag.items() if v}
        for name, code in dict(self.family.HUD_TAG_CODES.items()).items():
            self.assertEqual(codes.get(name), code, name)
        a, b = dict(self.family.HUD_SHARE.items()), dict(self.mod.SHARE.items())
        for k, v in a.items():
            self.assertEqual(b[k], v, k)


class SharedMemory:
    """One process address space seen by several loader scripts: blocks, global symbols, a battle-state root."""

    def __init__(self):
        self.blocks, self.symbols, self.next = {}, {}, 0x600000
        self.root = 0x10000
        self.clock = 0

    def _at(self, address, size):
        for base, buf in self.blocks.items():
            if base <= address and address + size <= base + len(buf):
                return buf, address - base
        raise KeyError(hex(address))

    def bind(self, lua):
        """The loader's memory table as one script's Lua state sees it (its own tables, the shared bytes)."""
        t = lua.table_from({})
        fn = lua.eval('function(f) return function(...) return f(...) end end')

        def alloc(size, zero=True):
            base = self.next
            self.next += (size + 15) // 16 * 16 + 16
            self.blocks[base] = bytearray(size)
            return base

        def read_u32(address):
            buf, o = self._at(address, 4)
            return int.from_bytes(buf[o:o + 4], 'little')

        def write_u32(address, value):
            buf, o = self._at(address, 4)
            buf[o:o + 4] = int(value).to_bytes(4, 'little')
            return True

        def read_array(address, size):
            try:
                buf, o = self._at(address, size)
            except KeyError:
                return lua.table_from([])
            return lua.table_from(list(buf[o:o + size]))

        def write_array(address, values):
            data = bytes(int(v) for v in values.values())
            buf, o = self._at(address, len(data))
            buf[o:o + len(data)] = data
            return True

        def register(name, address):
            self.symbols[name] = address
            return True

        t['allocData'], t['alloc'] = fn(alloc), fn(alloc)
        t['readU32'], t['writeU32'] = fn(read_u32), fn(write_u32)
        t['readArray'], t['writeArray'] = fn(read_array), fn(write_array)
        t['registerGlobalSymbol'] = fn(register)
        t['getSymbol'] = fn(lambda name: self.symbols.get(name, 0))
        lua.globals().battle_root = self.root
        t['u64'] = lua.eval('setmetatable({}, {__index = function() return battle_root end})')
        return t


class LoaderScript:
    """One loader script: its own Lua runtime (so its own globals), the shared memory, its own timer queue."""

    def __init__(self, shared):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.shared, self.queue, self.logs = shared, [], []
        lua = self.lua
        self.load = lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        g = lua.globals()
        g.memory = shared.bind(lua)
        fn = lua.eval('function(f) return function(...) return f(...) end end')   # a Lua function, not userdata
        ev = lua.table_from({})
        ev['executeAfterMs'] = fn(lambda ms, f: self.queue.append(f))
        ev['getClock'] = fn(lambda: shared.clock)
        ev['registerEventSync'] = ev['registerEventAsync'] = fn(lambda *a: True)
        g.event = ev
        g.print = lambda *a: self.logs.append(' '.join(str(x) for x in a))

    def run_timer(self):
        fn = self.queue.pop(0)
        fn()


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class SeparateStatesTest(unittest.TestCase):
    """Each loader script has its own Lua state (loader docs README: "Each script has its own Lua state"). The Family
    module lives in the Forge script's state; the co-op script reads it only through the shared block. Control: the
    global the Forge adapters set is invisible from the co-op state, so a design reading it there fails these tests."""

    def setUp(self):
        self.shared = SharedMemory()
        self.shared.clock = 50 * S
        self.forge = LoaderScript(self.shared)
        self.coop = LoaderScript(self.shared)
        # Forge state: an adapter loads the module and keeps it in ITS global, exactly as 138/139 do
        self.fam = self.forge.load(FAMILY.read_text(encoding='utf-8'), 'family_identities')
        self.forge.lua.globals().fam = self.fam
        self.forge.lua.execute("rawset(_G, 'IvaliceLegacyFamily', fam)")
        self.assertTrue(self.fam.register())
        # co-op state: codec, hud_states, the block reader, a recording bridge
        schema = self.coop.load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        self.C = self.coop.load((COOP / 'codec.lua').read_text(encoding='utf-8'), 'codec').new(schema)
        self.mod = self.coop.load((COOP / 'hud_states.lua').read_text(encoding='utf-8'), 'hud_states')
        self.pushed = []
        L = self.coop.lua
        L.globals().hud_pushed_add = lambda rec: self.pushed.append(rec)
        bridge = L.eval("""{is_ready = function() return true end,
                            push_hud_states = function(rec) hud_pushed_add(rec) return {} end}""")
        clock = lambda: self.shared.clock  # noqa: E731
        opts = L.table_from({'codec': self.C, 'bridge': bridge, 'log': lambda t: self.coop.logs.append(t),
                             'after': lambda ms, fn: self.coop.queue.append(fn), 'clock': clock})
        opts['view'] = self.mod.share_reader(L.table_from({'memory': L.globals().memory, 'clock': clock,
                                                           'codec': self.C}))
        self.hud = self.mod.create(opts)

    def feed_forge(self):
        t = self.shared.clock
        self.forge.lua.execute(f"""
          local root = {self.shared.root}
          local foe = {{type = 1, address = 0x900000, maxHp = 9800, level = 21, defense = 100}}
          fam.noteBroken(foe, {t - 3 * S}, root + 8, false)
          fam.dent(foe, {{defense = -1}}, 1, {t - 3 * S})
          fam.noteProc('SUNDER', root + 8, {t - 1 * S})
        """)

    def test_the_family_global_is_invisible_from_the_co_op_state(self):
        self.assertIsNotNone(self.forge.lua.eval("rawget(_G, 'IvaliceLegacyFamily')"))
        self.assertIsNone(self.coop.lua.eval("rawget(_G, 'IvaliceLegacyFamily')"))

    def test_a_broken_foe_reaches_the_co_op_state_through_the_shared_block(self):
        self.feed_forge()
        self.assertEqual(len(self.forge.queue), 1, 'register() armed the publisher timer on the Forge state')
        self.forge.run_timer()
        self.assertEqual(self.shared.symbols.get('ilc_hud_share'), next(iter(self.shared.blocks)))
        self.assertEqual(len(self.forge.queue), 1, 'and it re-armed itself')
        self.hud.start()
        self.coop.run_timer()
        self.assertEqual(len(self.pushed), 1)
        rec = self.pushed[0]
        self.assertEqual((rec.foe_count, rec.proc_count), (1, 1))
        f = rec.foes[1]
        self.assertEqual((f.record, f.flags & 1, f.broken_s, f.dent_pct), (0x900000, 1, 7, 5))
        self.assertEqual((rec.procs[1].tag, rec.procs[1].age_ms), (self.C.enums.hud_tag.SUNDER, 1000))

    def test_nothing_is_pushed_without_a_block_a_fresh_block_or_with_a_damaged_one(self):
        self.hud.start()
        self.coop.run_timer()
        self.assertEqual(len(self.pushed), 0, 'no symbol yet (the Forge side has not published)')
        self.feed_forge()
        self.forge.run_timer()
        block = self.shared.blocks[self.shared.symbols['ilc_hud_share']]
        good = bytes(block)
        block[4:8] = (int.from_bytes(good[4:8], 'little') + 1).to_bytes(4, 'little')
        self.coop.run_timer()
        self.assertEqual(len(self.pushed), 0, 'odd sequence: a write in progress')
        block[:] = good
        block[100] ^= 0xFF
        self.coop.run_timer()
        self.assertEqual(len(self.pushed), 0, 'checksum')
        block[:] = good
        self.shared.clock += 5 * S
        self.coop.run_timer()
        self.assertEqual(len(self.pushed), 0, 'stale: the Forge side stopped publishing')
        self.shared.clock -= 5 * S
        self.coop.run_timer()
        self.assertEqual(len(self.pushed), 1, 'control: the untouched block is read')

    def test_the_co_op_script_does_not_read_the_family_global(self):
        def code(path):
            return '\n'.join(l for l in path.read_text(encoding='utf-8').splitlines() if not l.strip().startswith('--'))
        self.assertNotIn('IvaliceLegacyFamily', code(ROOT / 'package/x64/scripts/IvaliceLegacyCoop.lua'))
        self.assertNotIn('IvaliceLegacyFamily', code(COOP / 'hud_states.lua'))


if __name__ == '__main__':
    unittest.main()
