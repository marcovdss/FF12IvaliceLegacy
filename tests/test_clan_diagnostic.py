"""No-game diagnostic contracts; native integration is verified separately."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ClanDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute(
            (Path(__file__).resolve().parents[1] / 'prototypes/clan_diagnostic.lua').read_text())
        self.lua.execute('''
          logs={}; mode=true
          actor={identifier=0,job1=8,job2=255,weapon=4206,address=123}
          formula={action=150,identifier=21}
          deps={legacyMode=function() return mode end,
            identity=function(c) if c.address==123 then return {actorId=c.identifier,kind='main-party'} end end,
            emit=function(line) logs[#logs+1]=line end}
          observer=module.new(deps)
        ''')

    def test_campaign_and_mode_gates(self):
        self.lua.execute('''
          assert(not observer:observe(formula,actor))
          for _,path in ipairs({'FFXII_003','LEGCY_003.json','LEGCY_03','LEGCY_003x','LEGCY_003/','LEGCY_003\\n'}) do
            assert(not observer:load(path)); assert(not observer:observe(formula,actor))
          end
          assert(observer:load('C:/saves/LEGCY_003'))
          assert(observer:observe(formula,actor))
          mode=false; assert(not observer:observe(formula,actor))
          mode=true; assert(not observer:observe(formula,actor))
          assert(not observer:load(nil))
        ''')

    def test_exact_full_class_and_controls(self):
        self.lua.execute('''
          observer:load('LEGCY_003'); assert(observer:observe(formula,actor))
          assert(logs[2]:find('class=8 ') and logs[2]:find('pilot=base'))
          actor.job2=11; assert(observer:observe(formula,actor))
          assert(logs[3]:find('class=8:11 ') and logs[3]:find('pilot=control'))
          actor.job1=11; actor.job2=8; assert(not observer:observe(formula,actor))
          actor.job2=255; assert(observer:observe(formula,actor))
          actor.job2=11; assert(not observer:observe(formula,actor))
          actor.job2=12; assert(not observer:observe(formula,actor))
        ''')

    def test_identity_action_and_weapon_filters(self):
        self.lua.execute('''
          observer:load('LEGCY_003')
          actor.address=999; assert(not observer:observe(formula,actor))
          actor.address=123; actor.identifier=6; assert(not observer:observe(formula,actor))
          actor.identifier=0; formula.action=114; assert(not observer:observe(formula,actor))
          formula.action=150; actor.weapon=65535; assert(not observer:observe(formula,actor))
          actor.weapon=4206; assert(observer:observe(formula,actor))
        ''')

    def test_original_once_arguments_returns_and_failure(self):
        self.lua.execute('''
          local calls=0; local tail={}
          local wrapped=observer:wrap(function(f,c,t,...)
            calls=calls+1; assert(f==formula and c==actor and t==tail)
            assert(select('#',...)==2); return 7,nil,9
          end)
          observer:load('LEGCY_003')
          deps.identity=function() error('reader unavailable') end
          local result=table.pack(wrapped(formula,actor,tail,'x',nil))
          assert(calls==1 and result.n==3 and result[1]==7 and result[2]==nil and result[3]==9)
          local broken=observer:wrap(function() error('original failed') end)
          local ok,err=pcall(broken); assert(not ok and err:find('original failed'))
        ''')

    def test_read_only_dedup_reset_and_cap(self):
        self.lua.execute('''
          observer:load('LEGCY_003'); assert(observer:observe(formula,actor))
          assert(not observer:observe(formula,actor))
          local fields=0; for _ in pairs(formula) do fields=fields+1 end
          assert(fields==2 and formula.action==150 and formula.identifier==21)
          assert(actor.weapon==4206 and actor.job1==8 and actor.job2==255)
          observer:reset(); assert(not observer:observe(formula,actor))
          observer:load('LEGCY_004')
          for i=1,256 do actor.weapon=4096+i; assert(observer:observe(formula,actor)) end
          actor.weapon=5000; assert(not observer:observe(formula,actor))
          observer:load('LEGCY_005'); assert(observer:observe(formula,actor))
        ''')

    def test_bad_load_and_log_failure_cannot_reuse_campaign(self):
        self.lua.execute('''
          observer:load('LEGCY_003'); assert(observer:observe(formula,actor))
          assert(not observer:load('FFXII_003'))
          actor.weapon=4097; assert(not observer:observe(formula,actor))
          mode=1; assert(not observer:load('LEGCY_003'))
          mode=true; observer:load('LEGCY_003')
          deps.emit=function() error('logger failed') end
          local wrapped=observer:wrap(function() return 'unchanged' end)
          assert(wrapped(formula,actor)=='unchanged')
        ''')
