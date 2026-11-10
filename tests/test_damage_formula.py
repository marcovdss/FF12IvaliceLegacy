"""Damage formula (damage_formula.lua 0.3.0): Forge's attack - defense with a floor of attack/4, party and foe hits."""
import os
import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
GAME = os.environ.get('IVALICE_LEGACY_GAME')
FORGE_113 = Path(GAME) / 'x64/scripts/TheInsurgentsForge/functions/113.lua' if GAME else None

# Stand-in for Forge 113 with its exact arithmetic (the real file is used below when the game path is set).
FAKE_113 = '''
calls = 0
return function(formula, caster, target)
  calls = calls + 1
  if formula.outcomeType == 10 then return end
  local powerMul = formula.multiplier - formula.modifier
  if powerMul < 0 then formula.target.removedHp = 0 return end
  formula.target.removedHp = formula.power * powerMul
end
'''


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class DamageFormulaTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().M = self.lua.execute((ROOT / 'prototypes/damage_formula.lua').read_text())
        self.lua.globals().native = self.lua.execute(FAKE_113)
        self.lua.execute('''
          function hit(casterType, attack, defense, outcome)
            local f = {power = 10, multiplier = attack, modifier = defense, outcomeType = outcome or 0,
                       target = {removedHp = -1}}
            M.wrap(native)(f, {type = casterType}, {})
            return f.target.removedHp, f.multiplier, f.modifier
          end
        ''')

    def test_ordinary_hits_are_forge_own(self):
        self.lua.execute('''
          assert(M.version == '0.3.0' and M.FLOOR_DIVISOR == 4)
          assert(hit(0, 60, 30) == 300, 'attack - defense above the floor: unchanged')
          assert(hit(0, 40, 30) == 100, 'exactly at the floor (40-30 = 40/4): unchanged')
          assert(hit(0, 60, 0) == 600, 'no defense term: unchanged')
        ''')

    def test_zeros_become_a_quarter_of_attack(self):
        self.lua.execute('''
          local d, m, mod = hit(0, 30, 35)
          assert(d == 10 * 30 / 4, d)
          assert(m == 30 and mod == 35, 'fields restored for later functions')
          assert(hit(0, 32, 30) == 10 * 8, 'attack - defense 2 is below the floor 8')
          assert(hit(0, 5, 200) > 0)
        ''')

    def test_foe_hits_get_the_same_floor_and_immune_outcomes_stay_native(self):
        self.lua.execute('''
          assert(hit(1, 30, 35) == 10 * 30 / 4, 'a foe against matching armour deals a quarter of its attack')
          assert(hit(1, 60, 30) == 300, 'foe hits above the floor are Forge own')
          assert(hit(0, 30, 35, 10) == -1, 'immune outcome untouched')
        ''')

    def test_errors_restore_fields_and_propagate(self):
        self.lua.execute('''
          local f = {power = 1, multiplier = 20, modifier = 40, outcomeType = 0, target = {}}
          local ok, err = pcall(M.wrap(function() error('forge failed', 0) end), f, {type = 0}, {})
          assert(not ok and err == 'forge failed' and f.multiplier == 20 and f.modifier == 40)
        ''')

    def test_floor_edges(self):
        self.lua.execute('''
          assert(M.floor(30, 0) == nil and M.floor(0, 30) == nil and M.floor(nil, 3) == nil)
          assert(M.floor(60, 30) == nil and M.floor(40, 30) == nil and M.floor(36, 30) == 9)
        ''')

    def test_adapter_loads_it_optionally(self):
        src = (ROOT / 'prototypes/forge_diagnostic_113.lua').read_text()
        self.assertIn("local Damage = optional('scripts/IvaliceLegacy/damage_formula.lua')", src)
        self.assertIn('original = Damage.wrap(original)', src)
        # The observer wraps the formula, so logged damage is the damage dealt.
        self.assertLess(src.index('original = Damage.wrap(original)'), src.index('local observed = observer:wrap(original)'))

    @unittest.skipIf(FORGE_113 is None or not FORGE_113.is_file(), 'set IVALICE_LEGACY_GAME for the real Forge 113')
    def test_with_real_forge_113(self):
        self.lua.globals().native = self.lua.execute(FORGE_113.read_text())
        self.lua.execute('''
          local function real(casterType, attack, defense)
            local f = {power = 14, multiplier = attack, modifier = defense, outcomeType = 0, absorbState = 0,
                       target = {removedHp = -1, statusEffects = {reverse = 0}}, caster = {keep = {reverseTargetFlags = {}}}}
            M.wrap(native)(f, {type = casterType}, {})
            return f.target.removedHp
          end
          assert(math.abs(real(1, 31.9, 35) - 14 * 31.9 / 4) < 1e-9)
          assert(math.abs(real(0, 31.9, 35) - 14 * 31.9 / 4) < 1e-9)
          assert(math.abs(real(0, 42.5, 24) - 14 * (42.5 - 24)) < 1e-9)
        ''')


if __name__ == '__main__':
    unittest.main()
