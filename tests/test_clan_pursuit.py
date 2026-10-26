"""Pursuit pilot contracts; the full Forge hit chain is verified separately."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class PursuitTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute(
            (Path(__file__).resolve().parents[1] / 'prototypes/clan_pursuit.lua').read_text())
        self.lua.execute('''
          reports={}; active=true; category=12
          hunter={identifier=0,job1=8,job2=255,weapon=4206,address=123}
          function formula(statuses) return {action=150,power=10,target={statusEffects=statuses or {}}} end
          deps={active=function() return active end,
            context=function(c) if c.address==123 then return {actorId=c.identifier,kind='main-party'} end end,
            weaponCategory=function() return category end,
            report=function(c,a,b,n) reports[#reports+1]={a,b,n} end}
          original=function(f) f.power=f.power*2; return 'kept', nil, 3 end
          wrapped=module.wrap66(original,deps)
          function run(c,f) local r=table.pack(wrapped(f,c,{})); return f.power, r end
        ''')

    def test_each_hindering_ailment_adds_a_quarter(self):
        self.lua.execute('''
          for _,name in ipairs(module.AILMENTS) do
            local power=run(hunter,formula({[name]=1}))
            assert(power==25, name)
          end
          assert(#reports==#module.AILMENTS and reports[1][2]==20 and reports[1][3]==25)
        ''')

    def test_untouched_without_a_counted_ailment(self):
        self.lua.execute('''
          for _,statuses in ipairs({{},{sleep=1},{confuse=1},{doom=1},{berserk=1},{stop=1},{poison=0}}) do
            assert(run(hunter,formula(statuses))==20)
          end
          assert(#reports==0)
        ''')

    def test_only_a_base_hunter_with_a_dagger_on_an_ordinary_attack(self):
        self.lua.execute('''
          local f=formula({poison=1}); f.action=114; assert(run(hunter,f)==20)
          hunter.job1=11; assert(run(hunter,formula({poison=1}))==20)
          hunter.job1=8; hunter.job2=4; assert(run(hunter,formula({poison=1}))==20)
          hunter.job2=255; category=13; assert(run(hunter,formula({poison=1}))==20)
          category=12; hunter.address=999; assert(run(hunter,formula({poison=1}))==20)
          hunter.address=123; active=false; assert(run(hunter,formula({poison=1}))==20)
          active=true; assert(run(hunter,formula({poison=1}))==25)
        ''')

    def test_original_runs_once_and_its_results_survive_reader_failure(self):
        self.lua.execute('''
          local calls=0
          local w=module.wrap66(function(f) calls=calls+1; f.power=7; return 'x', nil, 9 end,
            {active=function() error('reader unavailable') end, context=deps.context, weaponCategory=deps.weaponCategory})
          local f=formula({poison=1})
          local r=table.pack(w(f,hunter,{}))
          assert(calls==1 and f.power==7 and r.n==3 and r[1]=='x' and r[2]==nil and r[3]==9)
          local _, kept=run(hunter,formula({poison=1}))
          assert(kept.n==3 and kept[1]=='kept' and kept[3]==3)
          deps.report=function() error('log failed') end
          assert(run(hunter,formula({poison=1}))==25)
        ''')

    def test_actor_and_formula_fields_other_than_power_are_untouched(self):
        self.lua.execute('''
          local f=formula({poison=1}); f.multiplier=5; f.modifier=2
          run(hunter,f)
          assert(f.multiplier==5 and f.modifier==2 and f.action==150 and f.target.statusEffects.poison==1)
          assert(hunter.job1==8 and hunter.job2==255 and hunter.weapon==4206)
        ''')


if __name__ == '__main__':
    unittest.main()
