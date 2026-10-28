"""No-game contracts for the foe profile diagnostic; native integration is verified separately."""
from pathlib import Path
import re
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class FoeProfileDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute((ROOT / 'prototypes/foe_profile_diagnostic.lua').read_text())
        self.lua.execute('''
          logs={}
          foe={foe=true,nameId=88,bestiary=12,classification=3,genus=7,level=14,maxHp=2200,
               str=15,mag=7,vit=42,spd=15,atk=16,def=8,mres=14,immune=0x208,innate=0x40000,story=1450}
          current=foe
          deps={profile=function() return current end, emit=function(line) logs[#logs+1]=line end}
          observer=module.new(deps)
        ''')

    def test_logs_each_foe_once_with_decoded_masks(self):
        self.lua.execute('''
          assert(observer:observe()); assert(not observer:observe())
          assert(#logs==1)
          assert(logs[1]:find('story=1450 name=88 ') and logs[1]:find('level=14 ') and logs[1]:find('vit=42 '))
          assert(logs[1]:find('immune=0x00000208%[stop,silence%]'))
          assert(logs[1]:find('innate=0x00040000%[protect%]') and logs[1]:find('bonuses=off$'))
          foe.level=15; assert(observer:observe()); assert(#logs==2)
        ''')

    def test_unknown_story_is_marked_not_guessed(self):
        self.lua.execute('''
          foe.story=nil; assert(observer:observe()); assert(logs[1]:find('story=%? name=88 '))
          foe.level=20; foe.story=70000; assert(observer:observe()); assert(logs[2]:find('story=%? '))
        ''')

    def test_charge_probe_logs_both_sides_and_is_capped(self):
        self.lua.execute('''
          sample={clock=12, action=150, casterType=0, targetType=1,
                  caster={current=40,required=100,last=90,idle=0}, target={current=7,required=60,last=55,idle=3}}
          assert(observer:charge(sample))
          assert(logs[1]:find('action=150 casterType=0 caster=40/100/90/0 targetType=1 target=7/60/55/3 bonuses=off$'))
          sample.target.current=-1; assert(not observer:charge(sample)); sample.target.current=7
          assert(not observer:charge({caster=sample.caster}))
          for i=1,200 do observer:charge(sample) end
          assert(#logs==120)
          observer:reset(); assert(observer:charge(sample))
        ''')

    def test_hit_line_logs_both_directions_and_is_capped(self):
        self.lua.execute('''
          s={story=68, action=150, casterType=0, targetType=1, targetName=16542, targetLevel=2, targetMaxHp=170, removed=46}
          assert(observer:hit(s))
          assert(logs[1]=='Legacy hit: story=68 action=150 caster=party target=foe:16542 lv=2 maxHp=170 removed=46 bonuses=off')
          s2={story=68, action=429, casterType=1, casterName=16542, targetType=0, targetLevel=4, targetMaxHp=95, removed=9}
          assert(observer:hit(s2)); assert(logs[2]:find('caster=foe:16542 target=party lv=4 maxHp=95 removed=9 '))
          s.removed=-1; assert(not observer:hit(s)); s.removed=0/0; assert(not observer:hit(s))
          s.removed=46.875; assert(observer:hit(s)); assert(logs[3]:find(' removed=46 bonuses=off$')); s.removed=46
          for i=1,400 do observer:hit(s) end
          assert(#logs==301 and logs[301]:find('limit reached'))  -- 300 hits including the fractional one
          observer:reset(); assert(observer:hit(s))
        ''')

    def test_wrap_runs_charge_probe_after_profile(self):
        self.lua.execute('''
          deps.charge=function() return {clock=1, action=2, casterType=0, targetType=1,
            caster={current=1,required=2,last=3,idle=4}, target={current=5,required=6,last=7,idle=8}} end
          local wrapped=observer:wrap(function() return 'r' end)
          assert(wrapped()=='r')
          assert(#logs==2 and logs[1]:find('foe profile') and logs[2]:find('charge probe'))
        ''')

    def test_rejects_party_and_malformed_profiles(self):
        self.lua.execute('''
          current=nil; assert(not observer:observe())
          current={foe=false,nameId=1,level=1,maxHp=1,immune=0,innate=0}; assert(not observer:observe())
          current={foe=true,nameId=1,level=1.5,maxHp=1,immune=0,innate=0}; assert(not observer:observe())
          current={foe=true,nameId=1,level=1,maxHp=1,immune=-1,innate=0}; assert(not observer:observe())
          assert(#logs==0)
        ''')

    def test_reset_and_limit(self):
        self.lua.execute('''
          for i=1,405 do current={foe=true,nameId=i,level=1,maxHp=1,immune=0,innate=0}; observer:observe() end
          assert(#logs==401 and logs[401]:find('limit reached'))
          observer:reset(); current={foe=true,nameId=1,level=1,maxHp=1,immune=0,innate=0}
          assert(observer:observe())
        ''')

    def test_wrap_preserves_results_and_survives_errors(self):
        self.lua.execute('''
          deps.profile=function() error('boom') end
          local wrapped=observer:wrap(function(a,b) return a,b,nil end)
          local x,y,z=wrapped(1,2)
          assert(x==1 and y==2 and z==nil)
          wrapped(1,2)
          assert(#logs==1 and logs[1]:find('observer error='))
        ''')

    def test_adapter_wraps_unchanged_forge_function(self):
        src = (ROOT / 'prototypes/forge_diagnostic_113.lua').read_text()
        self.assertIn("loadfile('scripts/TheInsurgentsForge/functions/113.lua'", src)
        self.assertIn('local observed = observer:wrap(original)', src)
        self.assertIn('table.pack(observed(...))', src)
        # The observer is read-only: no assignment to game memory or to Forge's pending results. The one write in
        # the adapter is the charge pushback probe's setter for the target's current charge time.
        self.assertIsNone(re.search(r'memory\.\w+\[[^\]]+\]\s*=(?!=)', src))
        self.assertNotIn('formula.target.removedHp =', src)
        writes = re.findall(r'\b\w+\.\w+\s*=(?!=)\s*\S+', src)
        self.assertEqual(writes, ['work.currentChargeTime = value'])

    def test_action_line_logs_foe_casts_and_is_capped(self):
        self.lua.execute('''
          a={foe=true, nameId=16542, unitId=2, location=1450, action=82, targetType=1, targetName=16542,
             currentHp=85, maxHp=170, clock=10}
          assert(observer:action(a))
          assert(logs[1]:find('^Legacy action: clock=10 callback=onCastEnd foe=16542 unit=2 '))
          assert(logs[1]:find('action=82 target=foe:16542 hpPct=50.00 maxHp=170 bonuses=off$'))
          a.targetType=0; a.targetId=3; a.clock=11; assert(observer:action(a))
          assert(logs[2]:find('target=party:3 '))
          assert(not observer:action({foe=false, nameId=1, action=1, currentHp=1, maxHp=1, clock=12}))
          a.clock=5; assert(not observer:action(a))
          a.currentHp=171; a.clock=12; assert(not observer:action(a)); a.currentHp=85
          for i=1,400 do a.clock=12+i; observer:action(a) end
          assert(#logs==301 and logs[301]=='Legacy action: limit reached for this load')
          observer:reset(); a.clock=1; assert(observer:action(a))
        ''')

    def test_wrap_action_preserves_results_and_survives_errors(self):
        self.lua.execute('''
          deps.action=function() error('boom') end
          local wrapped=observer:wrap_action(function(a,b) return a,b,nil end)
          local x,y,z=wrapped(1,2)
          assert(x==1 and y==2 and z==nil)
          wrapped(1,2)
          assert(#logs==1 and logs[1]:find('Legacy action: observer error='))
        ''')

    def test_action_adapter_wraps_unchanged_forge_261(self):
        src = (ROOT / 'prototypes/forge_diagnostic_261.lua').read_text()
        self.assertIn("loadfile('scripts/TheInsurgentsForge/functions/261.lua'", src)
        self.assertIn('return observer:wrap_action(original)', src)
        self.assertIsNone(re.search(r'memory\.\w+\[[^\]]+\]\s*=(?!=)', src))


if __name__ == '__main__':
    unittest.main()
