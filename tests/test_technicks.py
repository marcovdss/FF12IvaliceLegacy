"""Technick pass 0.1.0: every Technick accounted for, and the three party-only improvements behave as stated."""
import json
from pathlib import Path
import unittest

from ivalice_legacy.technicks import validate_registry

try:
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


class TechnickRegistryTest(unittest.TestCase):
    def test_all_24_accounted_for(self):
        rows = validate_registry(json.loads((ROOT / 'patches/technicks.json').read_text(encoding='utf-8')))
        improved = sorted(r['name'] for r in rows if r['decision'] == 'improve')
        self.assertEqual(improved, ['Achilles', 'First Aid', 'Horology'])


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class TechnickRulesTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().src = (ROOT / 'package/x64/scripts/IvaliceLegacy/technicks.lua').read_text(encoding='utf-8')
        self.lua.execute(r'''
          T = load(src)()
          function flags(on) return setmetatable(on or {}, {__index=function() return 0 end}) end
          function party(t) return {type=0, level=t and t.level or 30} end
        ''')

    def test_first_aid_guarantees_ten_percent_on_critical_allies(self):
        self.lua.execute(r'''
          local fn = T.wrap(132, function(f) f.target.addedHp = 40 end)
          local f = {action=158, skipState=0, target={maxHp=2000, statusEffects=flags({hpCritical=1}), addedHp=0}}
          fn(f, party(), {type=0, currentHp=100}, {}, {}, {})
          assert(f.target.addedHp == 200, f.target.addedHp)
          f.target.statusEffects = flags({hpCritical=1}); fn(f, party(), {type=0, currentHp=100}, {}, {}, {})
          local g = {action=158, skipState=0, target={maxHp=2000, statusEffects=flags({hpCritical=1}), addedHp=0}}
          T.wrap(132, function(x) x.target.addedHp = 380 end)(g, party(), {type=0, currentHp=100}, {}, {}, {})
          assert(g.target.addedHp == 380)                       -- a better roll is kept
          local h = {action=158, skipState=0, target={maxHp=2000, statusEffects=flags(), addedHp=0}}
          fn(h, party(), {type=0, currentHp=100}, {}, {}, {})
          assert(h.target.addedHp == 40)                        -- not critical: native
          local k = {action=158, skipState=1, target={maxHp=2000, statusEffects=flags({hpCritical=1}), addedHp=0}}
          fn(k, party(), {type=0, currentHp=100}, {}, {}, {})
          assert(k.target.addedHp == 40)                        -- the technick failed: native
        ''')

    def test_horology_floor_is_ten_times_level_for_the_party_only(self):
        self.lua.execute(r'''
          local fn = T.wrap(133, function(f) f.target.removedHp = 0 end)
          local f = {action=160, skipState=0, target={removedHp=0}}
          fn(f, party({level=30}), {type=1}, {}, {}, {})
          assert(f.target.removedHp == 300, f.target.removedHp)
          local g = {action=160, skipState=0, target={removedHp=0}}
          T.wrap(133, function(x) x.target.removedHp = 2430 end)(g, party({level=30}), {type=1}, {}, {}, {})
          assert(g.target.removedHp == 2430)                    -- clock peaks remain
          local h = {action=160, skipState=0, target={removedHp=0}}
          fn(h, {type=1, level=30}, {type=1}, {}, {}, {})
          assert(h.target.removedHp == 0)                       -- foes keep native Horology
        ''')

    def test_achilles_never_rolls_an_existing_weakness(self):
        self.lua.execute(r'''
          local seen = {}
          local function native(f, c, t, fs, cl, helpers)
            local e = helpers.getRandomNumber(8)
            seen[#seen + 1] = e
          end
          local fn = T.wrap(194, native)
          local weak = flags({[0]=1, [1]=1, [2]=1, [3]=1, [4]=1, [5]=1, [7]=1})  -- only element 6 is new
          local target = {type=1, permanentElementalAffinities={weak=weak}}
          local helpers = {getRandomNumber=function(n) return 0 end}
          for _ = 1, 5 do fn({action=162, skipState=0}, party(), target, {}, {}, helpers) end
          for _, e in ipairs(seen) do assert(e == 6, e) end
          seen = {}
          fn({action=162, skipState=0}, {type=1}, target, {}, {}, helpers)
          assert(seen[1] == 0)                                  -- a foe's Achilles stays native
        ''')


if __name__ == '__main__':
    unittest.main()
