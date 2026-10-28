"""No-game contracts for the read-only Attack area probe."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class AttackAreaProbeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute((ROOT / 'prototypes/attack_area_probe.lua').read_text())
        self.lua.execute('''
          logs={}
          probe=module.new({emit=function(line) logs[#logs+1]=line end})
          function sample(t)
            local s={action=150, clock=9, casterType=0, casterId=4, weapon=4114, category=2, focus=7,
                     targetType=1, targetId=7, removed=120}
            for k,v in pairs(t or {}) do s[k]=v end
            return s
          end
        ''')

    def test_marks_targeted_unit_and_bystanders(self):
        self.lua.execute('''
          assert(probe:hit(sample()))
          assert(logs[1]=='Legacy attack area: clock=9 caster=party:4 weapon=4114 category=2 focus=7 target=foe:7 primary=yes removed=120 bonuses=area-test', logs[1])
          assert(probe:hit(sample({targetId=8, removed=60})))
          assert(logs[2]:find('target=foe:8 primary=no removed=60 '))
          assert(probe:hit(sample({focus=false})))
          assert(logs[3]:find('focus=false target=foe:7 primary=%? '))
        ''')

    def test_ignores_other_actions_caps_and_resets(self):
        self.lua.execute('''
          assert(not probe:hit(sample({action=429}))); assert(not probe:hit(nil))
          for i=1,250 do probe:hit(sample()) end
          assert(#logs==200)
          probe:reset(); assert(probe:hit(sample({casterType=1}))); assert(logs[201]:find('caster=foe:4 '))
        ''')


if __name__ == '__main__':
    unittest.main()
