"""No-game contracts for the charge pushback mechanism probe; native persistence is what the probe measures."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ChargePushbackProbeTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute((ROOT / 'prototypes/charge_pushback_probe.lua').read_text())
        self.lua.execute('''
          logs={}
          probe=module.new({emit=function(line) logs[#logs+1]=line end})
          memory=20000
          function sample(t)
            local s={action=150, casterType=0, targetType=1, clock=5, unit=0x1A2B0, current=memory, required=36000,
                     last=memory, set=function(v) memory=v end, get=function() return memory end}
            for k,v in pairs(t or {}) do s[k]=v end
            return s
          end
        ''')

    def test_alternates_control_then_write_and_logs_readback(self):
        self.lua.execute('''
          assert(probe:hit(sample()))
          assert(memory==20000 and logs[1]:find('n=1 arm=control ') and logs[1]:find('written=20000 readback=20000 writes=0'))
          assert(probe:hit(sample()))
          assert(memory==11000, memory)
          assert(logs[2]:find('n=2 arm=write clock=5 unit=0x1A2B0 before=20000/36000 written=11000 readback=11000 writes=1 bonuses=charge%-test$'))
        ''')

    def test_only_party_attack_on_mid_charge_foe_qualifies(self):
        self.lua.execute('''
          assert(not probe:hit(sample({action=429})))
          assert(not probe:hit(sample({casterType=1})))
          assert(not probe:hit(sample({targetType=0})))
          assert(not probe:hit(sample({current=36000, last=36000})))
          assert(not probe:hit(sample({current=0, last=0})))
          assert(not probe:hit(sample({last=0})))
          assert(not probe:hit(sample({unit=0})))
          assert(not probe:hit(sample({current=-1})))
          assert(#logs==0 and memory==20000)
        ''')

    def test_never_writes_to_zero_and_caps_writes(self):
        self.lua.execute('''
          assert(probe:hit(sample())); memory=5000
          assert(probe:hit(sample()))
          assert(memory==5000 and logs[2]:find('arm=control '))
          for i=1,40 do memory=20000; probe:hit(sample()) end
          local w=0 for _,l in ipairs(logs) do if l:find('arm=write') then w=w+1 end end
          assert(w==12, w)
        ''')

    def test_line_cap_and_reset(self):
        self.lua.execute('''
          for i=1,100 do memory=20000; probe:hit(sample()) end
          assert(#logs==60)
          probe:reset(); memory=20000; assert(probe:hit(sample()))
          assert(logs[61]:find('n=1 arm=control '))
        ''')


if __name__ == '__main__':
    unittest.main()
