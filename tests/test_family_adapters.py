"""Every Forge adapter prototype must load in a sandbox and return a wrapped function.

These are the files that ship as x64/scripts/config/TheInsurgentsForgeConfig/functions/<N>.lua.
The sandbox stubs the game env the way the adapters see it: loadfile maps Forge and IvaliceLegacy
paths to repo prototypes, the Family module registers against a fake event table, and a Legacy
campaign is active. A broken adapter here means a dead identity in game, not a crash: every adapter
falls back to the original Forge function when its module fails, and this test pins that fallback too.
"""
from pathlib import Path
import unittest

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
              if line:find('Legacy Family: identities 0.1.1 ready', 1, true) then n = n + 1 end
            end
            return n
          end)()
        ''')
        self.assertEqual(ready, 1)


if __name__ == '__main__':
    unittest.main()
