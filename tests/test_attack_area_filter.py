"""No-game contracts for the greatsword-only cleave filter and its Forge 228 adapter."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class AttackAreaFilterTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute((ROOT / 'prototypes/attack_area_filter.lua').read_text())
        self.lua.execute('''
          logs={}
          filter=module.new({emit=function(line) logs[#logs+1]=line end})
          function sample(t)
            local s={action=150, initial=0x5000, target=0x6000, casterType=0, casterId=4, category=2,
                     targetType=1, targetId=8, clock=9}
            for k,v in pairs(t or {}) do s[k]=v end
            return s
          end
        ''')

    def test_party_greatsword_sweeps_everything_else_skips_bystanders(self):
        self.lua.execute('''
          assert(filter:decide(sample())=='sweep')
          assert(logs[1]=='Legacy cleave: clock=9 caster=party:4 category=2 target=foe:8 decision=sweep bonuses=area-test', logs[1])
          assert(filter:decide(sample({category=1}))=='skip')
          assert(filter:decide(sample({category=false}))=='skip')
          assert(filter:decide(sample({casterType=1, targetType=0}))=='skip')
          assert(logs[4]:find('caster=foe:4 category=2 target=party:8 decision=skip '))
        ''')

    def test_aimed_at_unit_other_actions_and_unknown_aim_stay_native(self):
        self.lua.execute('''
          assert(filter:decide(sample({initial=0x6000, category=1}))==nil)
          assert(filter:decide(sample({action=394, category=1}))==nil)
          assert(filter:decide(nil)==nil)
          assert(filter:decide(sample({target=0}))==nil)
          assert(#logs==0)
          assert(filter:decide(sample({initial=0, category=1}))==nil)
          assert(logs[1]:find('aim=unknown decision=native '))
        ''')

    def test_log_is_capped_and_reset_but_decisions_continue(self):
        self.lua.execute('''
          for i=1,150 do filter:decide(sample({category=1})) end
          assert(#logs==100)
          assert(filter:decide(sample({category=1}))=='skip' and #logs==100)
          filter:reset(); filter:decide(sample()); assert(#logs==101)
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class Forge228AdapterTests(unittest.TestCase):
    """Runs the adapter against a stand-in for Forge's 228 (the real one is checked in the lab, not shipped here)."""

    def load(self, with_filter=True):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().filter_source = (ROOT / 'prototypes/attack_area_filter.lua').read_text() if with_filter else None
        lua.globals().adapter_source = (ROOT / 'prototypes/forge_filter_228.lua').read_text()
        lua.execute('''
          printed={}
          print=function(s) printed[#printed+1]=s end
          event={registerEventSync=function() return true end, getClock=function() return 7 end}
          bpack={section13={count=600}}
          setmetatable(bpack.section13, {__index=function(t,i) if i==18 then return {category=2} end return {category=1} end})
          originalCalls=0
          local function original(formula, caster, target)
            originalCalls=originalCalls+1
            if target.xZone then formula.skipState=1; formula.outcomeType=9 end
          end
          loadfile=function(path)
            if path=='scripts/TheInsurgentsForge/functions/228.lua' then return function() return original end end
            if path=='scripts/IvaliceLegacy/attack_area_filter.lua' then
              return filter_source and load(filter_source) or nil
            end
          end
          f228=load(adapter_source)()
          function run(t)
            local caster={type=0, identifier=4, weapon=0x1000+18}
            local target={type=1, address=0x6000, xZone=false}
            local formula={action=150, initialTargetPointer=0x5000, skipState=0, outcomeType=9, target={identifier=8}}
            for k,v in pairs(t or {}) do
              if k=='weapon' then caster.weapon=v elseif k=='casterType' then caster.type=v
              elseif k=='xZone' then target.xZone=v else formula[k]=v end
            end
            f228(formula, caster, target)
            return formula
          end
        ''')
        return lua

    def test_skips_only_non_greatsword_bystanders(self):
        lua = self.load()
        lua.execute('''
          assert(printed[1]:find('^Legacy cleave: filter 0.1.0 ready'))
          assert(run().skipState==0)
          local f=run({weapon=0x1000+5}); assert(f.skipState==1 and f.outcomeType==9)
          assert(run({weapon=0x1000+5, initialTargetPointer=0x6000}).skipState==0)
          assert(run({weapon=0x1000+5, initialTargetPointer=0}).skipState==0)
          assert(run({casterType=1}).skipState==1)
          assert(run({weapon=0x1000+5, action=394}).skipState==0)
          assert(run({weapon=0xFFFF}).skipState==1)
          assert(originalCalls==7)
        ''')

    def test_native_skip_wins_and_is_untouched(self):
        lua = self.load()
        lua.execute('''
          local f=run({xZone=true}); assert(f.skipState==1 and f.outcomeType==9)
          for _,l in ipairs(printed) do assert(not l:find('decision=')) end
        ''')

    def test_missing_filter_falls_back_to_single_target(self):
        lua = self.load(with_filter=False)
        lua.execute('''
          assert(printed[1]:find('filter not loaded; Attack hits only its aimed%-at unit'))
          assert(run().skipState==1)
          assert(run({initialTargetPointer=0x6000}).skipState==0)
          assert(run({initialTargetPointer=0}).skipState==0)
          assert(run({action=394}).skipState==0)
        ''')

    def test_filter_error_leaves_hit_alone_and_logs_once(self):
        lua = self.load()
        lua.execute('''
          bpack.section13=nil
          assert(run({weapon=0x1000+5}).skipState==0)
          assert(run({weapon=0x1000+5}).skipState==0)
          local n=0 for _,l in ipairs(printed) do if l:find('filter error=') then n=n+1 end end
          assert(n==1, n)
        ''')


if __name__ == '__main__':
    unittest.main()
