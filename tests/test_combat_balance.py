"""Greatsword cleave reduction before native recoil, Proficiency and leech."""
import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class CombatBalanceTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().balance = self.lua.execute((ROOT / 'package/x64/scripts/IvaliceLegacy/combat_balance.lua').read_text())
        self.lua.execute('''
          bpack = {section13 = {count = 4, [0] = {category=2}, [1] = {category=1}, [2] = {category=7}, [3] = {category=3}}}
          native = function(f)
            f.seenNative = f.target.removedHp
            if f.doubleEdged then
              f.target.removedHp = f.target.removedHp * 2
              f.caster.removedHp = f.target.removedHp
            end
          end
          apply = balance.wrap165(native)
          function run(action, casterType, targetType, weapon, amount, double, skip)
            local f = {action=action, target={removedHp=amount}, caster={}, doubleEdged=double, skipState=skip}
            apply(f, {type=casterType, weapon=weapon}, {type=targetType})
            return f
          end
        ''')

    def test_greatsword_basic_hit_and_bystanders_receive_half(self):
        f = self.lua.globals().run(150, 0, 1, 0x1000, 101, False, 0)
        self.assertEqual(f.target.removedHp, 50)
        self.assertEqual(f.seenNative, 50)

    def test_native_double_edged_recoil_sees_reduced_damage(self):
        f = self.lua.globals().run(150, 0, 1, 0x1000, 200, True, 0)
        self.assertEqual((f.target.removedHp, f.caster.removedHp), (200, 200))

    def test_other_weapons_skills_foes_and_misses_are_unchanged(self):
        cases = [(150,0,1,0x1001,101,0), (165,0,1,0x1000,101,0), (150,1,0,0x1000,101,0),
                 (150,0,0,0x1000,101,0), (150,0,1,0x1000,-1,0), (150,0,1,0x1000,0,0),
                 (150,0,1,0x1000,101,1), (150,0,1,0xFFFF,101,0)]
        for action, caster, target, weapon, amount, skip in cases:
            with self.subTest(case=(action,caster,target,weapon,amount,skip)):
                f = self.lua.globals().run(action,caster,target,weapon,amount,False,skip)
                self.assertEqual(f.target.removedHp, amount)
