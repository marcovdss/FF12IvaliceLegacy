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
        self.assertIn('return observer:wrap(original)', src)
        # Read-only: no assignment to game memory or to Forge's pending results.
        self.assertIsNone(re.search(r'memory\.\w+\[[^\]]+\]\s*=(?!=)', src))
        self.assertNotIn('formula.target.removedHp =', src)


if __name__ == '__main__':
    unittest.main()
