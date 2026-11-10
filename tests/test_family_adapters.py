"""Every Forge adapter prototype must load in a sandbox and return a wrapped function.

These are the files that ship as x64/scripts/config/TheInsurgentsForgeConfig/functions/<N>.lua.
The sandbox stubs the game env the way the adapters see it: loadfile maps Forge and IvaliceLegacy
paths to repo prototypes, the Family module registers against a fake event table, and a Legacy
campaign is active. A broken adapter here means a dead identity in game, not a crash: every adapter
falls back to the original Forge function when its module fails, and this test pins that fallback too.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_family_identities import FORGE_FAKES  # noqa: E402

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
FORGE = 'scripts/TheInsurgentsForge/functions'
ADAPTERS = {
    8: 'forge_family_8.lua',
    10: 'forge_family_10.lua',
    22: 'forge_proficiency_22.lua',
    68: 'forge_family_68.lua',
    110: 'forge_proficiency_110.lua',
    113: 'forge_diagnostic_113.lua',
    165: 'forge_proficiency_165.lua',
    41: 'forge_guard_41.lua',
    42: 'forge_guard_42.lua',
    43: 'forge_guard_43.lua',
    171: 'forge_diagnostic_171.lua',
}


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class AdapterLoadTests(unittest.TestCase):
    def build_sandbox(self, family_file='family_identities.lua'):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().ROOT = str(ROOT).replace('\\', '/')
        lua.globals().FAMILY = family_file
        lua.execute('''
          local realLoadfile = loadfile
          loadfile = function(path, mode, env)
            local repo = {
              ['scripts/IvaliceLegacy/family_identities.lua'] = ROOT .. '/prototypes/' .. FAMILY,
              ['scripts/IvaliceLegacy/proficiency_runtime.lua'] = ROOT .. '/prototypes/proficiency_runtime.lua',
              ['scripts/IvaliceLegacy/proficiency.lua'] = ROOT .. '/prototypes/proficiency.lua',
              ['scripts/IvaliceLegacy/clan_rank.lua'] = ROOT .. '/prototypes/clan_rank.lua',
              ['scripts/IvaliceLegacy/hybrid_proficiency.lua'] = ROOT .. '/prototypes/hybrid_proficiency.lua',
              ['scripts/IvaliceLegacy/clan_diagnostic.lua'] = ROOT .. '/prototypes/clan_diagnostic.lua',
              ['scripts/IvaliceLegacy/foe_profile_diagnostic.lua'] = ROOT .. '/prototypes/foe_profile_diagnostic.lua',
              ['scripts/IvaliceLegacy/damage_formula.lua'] = ROOT .. '/prototypes/damage_formula.lua',
              ['scripts/IvaliceLegacy/combat_balance.lua'] = ROOT .. '/package/x64/scripts/IvaliceLegacy/combat_balance.lua',
              ['scripts/IvaliceLegacy/magicks.lua'] = ROOT .. '/package/x64/scripts/IvaliceLegacy/magicks.lua',
              ['scripts/IvaliceLegacy/technicks.lua'] = ROOT .. '/package/x64/scripts/IvaliceLegacy/technicks.lua',
            }
            if repo[path] then return realLoadfile(repo[path], mode, env) end
            if path:sub(1, #FORGE_P) == FORGE_P then
              return function(...) return function() return 'original' end end
            end
            -- the tests themselves load adapters out of prototypes/
            if path:sub(1, #ROOT + 12) == ROOT .. '/prototypes/' then return realLoadfile(path, mode, env) end
            -- other IvaliceLegacy paths act as missing files (the adapters treat them as optional)
            if path:sub(1, #'scripts/IvaliceLegacy/') == 'scripts/IvaliceLegacy/' then return nil end
            error('sandbox: unexpected loadfile path ' .. path)
          end
          prints = {}
          print = function(line) prints[#prints + 1] = tostring(line) end
          event = {registerEventSync = function() return true end,
                   registerEventAsync = function() return true end,
                   registerLoadHandler = function() return true end,
                   registerSaveHandler = function() return true end}
          memory = {readArray = function() return nil end, u64 = setmetatable({}, {__index = function() return 0 end}),
                    u8 = setmetatable({}, {__index = function() return 0 end})}
          bpack = {section13 = setmetatable({}, {__index = function() return {category = 1} end}), count = 600}
        ''')
        return lua

    FORGE_P = FORGE  # exposed to the sandbox chunk

    def test_adapter_loads_and_wraps(self):
        for n, name in sorted(ADAPTERS.items()):
            with self.subTest(adapter=name):
                lua = self.build_sandbox()
                lua.globals().FORGE_P = self.FORGE_P
                lua.globals().ADAPTER = name
                wrapped = lua.eval('''
                  (function()
                    local chunk = assert(loadfile(ROOT .. '/prototypes/' .. ADAPTER, 't', _ENV))
                    local result = assert(chunk())
                    return type(result) == 'function'
                  end)()
                ''')
                self.assertTrue(wrapped)

    def test_family_module_failure_leaves_adapter_falling_back(self):
        # A register-refusing Family module: every adapter must return the original behaviour
        # (a function) and print exactly one 'module not loaded' line, never an error.
        lua = self.build_sandbox('tests/fixtures/family_refuses.lua')
        lua.globals().FORGE_P = self.FORGE_P
        lua.globals().ADAPTER = 'forge_family_8.lua'
        ok = lua.eval('''
          (function()
            local chunk = assert(loadfile(ROOT .. '/prototypes/' .. ADAPTER, 't', _ENV))
            local result = assert(chunk())
            local refused = false
            for _, line in ipairs(prints) do
              if line:find('module not loaded') then refused = true end
            end
            return type(result) == 'function' and refused and #prints == 1
          end)()
        ''')
        self.assertTrue(ok)

    def test_ready_line_prints_once_for_the_family_module(self):
        lua = self.build_sandbox()
        lua.globals().FORGE_P = self.FORGE_P
        lua.globals().ADAPTER = 'forge_family_8.lua'
        lua.execute("assert(loadfile(ROOT .. '/prototypes/' .. ADAPTER, 't', _ENV))()")
        ready = lua.eval('''
          (function()
            local n = 0
            for _, line in ipairs(prints) do
              if line:find('Legacy Family: identities 0.4.2 ready', 1, true) then n = n + 1 end
            end
            return n
          end)()
        ''')
        self.assertEqual(ready, 1)



@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class AdapterFiresTests(AdapterLoadTests):
    """Each identity fires through the adapter file that ships, with the real Legacy gate.

    0.1.x adapters loaded and wrapped (the tests above) yet never passed deps, so no identity ran in
    game. These calls go through the shipped adapter, the registered deps and the memory-signature
    gate, with Forge-shaped strict objects and a stand-in for each wrapped Forge function.
    """

    def fire(self, adapter, forge_n, forge_body, category, script):
        lua = self.build_sandbox()
        lua.globals().FORGE_P = self.FORGE_P
        lua.execute(FORGE_FAKES)
        lua.execute('''
          local LEGACY = {[0x120000]='MZ', [0x120180]='PE\\0\\0\\x64\\x86', [0xD113D4]='LEGCY_\\0'}
          local wide = '%sLEGCY_%03d\\0'
          memory.readArray = function(address, count)
            if address == 0xD12200 then
              local t = {} for i = 1, #wide do t[#t + 1] = wide:byte(i); t[#t + 1] = 0 end return t
            end
            return LEGACY[address]
          end
          bpack.section13 = setmetatable({}, {__index = function() return {category = WEAPON_CATEGORY} end})
          bpack.section13.count = 600
          local inner = loadfile
          loadfile = function(path, mode, env)
            if path == FORGE_P .. '/' .. FORGE_N .. '.lua' then return function() return FORGE_FN end end
            return inner(path, mode, env)
          end
        ''')
        lua.globals().WEAPON_CATEGORY = category
        lua.globals().FORGE_N = forge_n
        lua.execute('FORGE_FN = ' + forge_body)
        lua.globals().ADAPTER = adapter
        lua.execute("WRAPPED = assert(assert(loadfile(ROOT .. '/prototypes/' .. ADAPTER, 't', _ENV))())")
        lua.execute(script)
        return lua

    def test_marksman_through_adapter_22(self):
        self.fire('forge_proficiency_22.lua', 22,
                  'function(f,c,t) f.evadeParry=t.evadeParry f.evadeShield=t.evadeShield or 0 f.evadeWeapon=t.evadeWeapon or 0 end',
                  9, '''
          local c, t = wielder(), foe(); t.evadeParry = 12; t.evadeWeapon = 8; t.evadeShield = 5
          local f = hit(c, t); WRAPPED(f, c, t, {}, {}, {})
          assert(f.evadeParry == 0 and f.evadeWeapon == 8 and f.evadeShield == 5, 'Marksman did not fire')
        ''')

    def test_sunder_through_adapter_68(self):
        self.fire('forge_family_68.lua', 68, 'function(f,c,t) f.modifier=t.defense end', 10, '''
          local c, t = wielder(), foe(555, 40)
          local f = hit(c, t); WRAPPED(f, c, t, {}, {}, {})
          assert(f.modifier == 40 and f.target.defense == 38, 'Sunder did not queue a dent')
        ''')

    def test_chain_through_adapter_10(self):
        self.fire('forge_family_10.lua', 10,
                  'function(f,c,t) f.caster.keep.battleFlags.comboCount=4 f.comboState=1 end', 3, '''
          local c, t = wielder(), foe()
          local f = hit(c, t); WRAPPED(f, c, t, {}, {}, {})
          assert(f.caster.keep.battleFlags.comboCount == 6, 'Chain did not extend the combo')
        ''')

    def test_counterflow_through_adapter_8(self):
        self.fire('forge_family_8.lua', 8, 'function(f,c,t) SEEN=f.counterChance end', 6, '''
          local attacker, defender = foe(), wielder()
          defender.permanentAugments.counter = 1
          local f = hit(attacker, defender, {counterChance = 25}); WRAPPED(f, attacker, defender, {}, {}, {})
          assert(SEEN == 50, 'Counterflow did not double the chance: ' .. tostring(SEEN))
          local fresh = wielder(1, 99)             -- no flow yet (the first defender just built one)
          f = hit(attacker, fresh, {counterChance = 0}); WRAPPED(f, attacker, fresh, {}, {}, {})
          assert(SEEN == 20, 'A pole without Counter must counter at Speed / 2: ' .. tostring(SEEN))
        ''')

    def test_guard_fatigue_through_adapters_41_to_43(self):
        for n in (41, 42, 43):
            with self.subTest(adapter=n):
                self.fire(f'forge_guard_{n}.lua', n,
                          'function(f,c,t) SEEN=f.evadeShield f.outcomeType=3 f.skipState=1 end', 1, """
                  local attacker, tank = foe(), wielder()
                  for i = 1, 2 do
                    local f = hit(attacker, tank); f.evadeShield = 40; WRAPPED(f, attacker, tank, {}, {}, {})
                  end
                  assert(SEEN == 30, 'Guard fatigue did not wear the shield down: ' .. tostring(SEEN))
                """)

    def test_pierce_through_adapter_113(self):
        self.fire('forge_diagnostic_113.lua', 113,
                  'function(f,c,t) SEEN=f.modifier f.target.removedHp=100 end', 5, '''
          local c, t = wielder(), foe(555, 30)
          local f = hit(c, t, {modifier = 30}); WRAPPED(f, c, t, {}, {}, {})
          assert(SEEN ~= nil and SEEN < 30, 'Pierce did not lower the Defence term: ' .. tostring(SEEN))
          assert(f.modifier == 30)
        ''')

    def test_afflict_through_adapter_171(self):
        self.fire('forge_diagnostic_171.lua', 171, 'function(f,c,t) f.onHitRate=20 end', 8, '''
          local c, t = wielder(), foe()
          local f = hit(c, t); WRAPPED(f, c, t, {}, {}, {})
          assert(f.onHitRate == 40, 'Afflict did not double the rate: ' .. tostring(f.onHitRate))
        ''')

    def test_spellbreak_through_adapter_165(self):
        self.fire('forge_proficiency_165.lua', 165, 'function(f,c,t) f.target.removedHp=300 end', 15, '''
          local c, t = wielder(), foe(555, 20, 1000, 10, 500)
          local f = hit(c, t); WRAPPED(f, c, t, {}, {}, {})
          assert(f.target.removedMp == 30, 'Spellbreak did not burn MP: ' .. tostring(f.target.removedMp))
        ''')

    def test_mend_through_adapter_110(self):
        self.fire('forge_proficiency_110.lua', 110, 'function(f,c,t) f.target.addedHp=200 end', 13, '''
          local c, t = wielder(), wielder(1, 99)
          local f = hit(c, t, {action = 5}); WRAPPED(f, c, t, {}, {}, {})
          assert(f.target.addedHp == 240, 'Mend did not boost the heal: ' .. tostring(f.target.addedHp))
        ''')


if __name__ == '__main__':
    unittest.main()
