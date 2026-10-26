"""Pure adapter contracts; shipped Forge execution is a separate offline proof."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5 for the offline Lua prototype')
class ClanStatusTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        source = Path(__file__).resolve().parents[1] / 'prototypes/clan_status.lua'
        self.lua.globals().effect = self.lua.execute(source.read_text())
        self.lua.execute('''
          actor={identifier=0,job1=8,job2=255,weapon=4206,address=123}
          context={actorId=0,kind='main-party'}
          ledger={schema=1,characters={['0']={['8']={'hunter.silence'}},['1']={['11']={'chemist.blind'}}}}
          rules={{id='hunter.silence',classKey='8',status='silence',onHitRate=100},
                 {id='chemist.blind',classKey='11',status='blind',onHitRate=40}}
          deps={ledger=ledger,rules=rules,context=function() return context end,
                nativeRate=function() return 0 end,nativeMask=function() return 0 end}
          function selected(slot) return effect.resolve(actor,context,ledger,rules,slot or 'weapon') end
        ''')

    def check_lua(self, code):
        self.lua.execute(code)

    def test_distinct_actor_and_class_rules(self):
        self.check_lua('''
          assert(selected().statusMask==1<<9)
          actor.identifier=1; actor.job1=11; context.actorId=1
          assert(selected().statusMask==1<<7 and selected().onHitRate==40)
          actor.identifier=0; context.actorId=0
          assert(selected()==nil)
        ''')

    def test_full_class_and_order_are_exact(self):
        self.check_lua('''
          assert(effect.classKey(8,11)=='8:11' and effect.classKey(11,8)=='8:11')
          assert(effect.classKey(8,255)=='8' and effect.classKey(8.0,nil)=='8')
          assert(effect.classKey(8,8)==nil and effect.classKey(8.5,nil)==nil)
          actor.job2=11; assert(selected()==nil)
          ledger.characters['0']['8:11']={'hybrid.slow'}
          rules[#rules+1]={id='hybrid.slow',classKey='8:11',status='slow',onHitRate=5}
          assert(selected().statusMask==1<<15)
          actor.job1=11; actor.job2=8; assert(selected().statusMask==1<<15)
        ''')

    def test_missing_ownership_and_accessories_are_neutral(self):
        self.check_lua('''
          assert(selected('accessory')==nil and selected('secondAccessory')==nil)
          actor.accessory=4206; actor.secondAccessory=4206; actor.weapon=4097
          assert(selected()==nil)
          actor.weapon=4206; ledger.characters={}; assert(selected()==nil)
          ledger=nil; assert(selected()==nil)
        ''')

    def test_ambiguous_or_malformed_rules_fail_neutral(self):
        self.check_lua('''
          ledger.characters['0']['8']={'hunter.silence','hunter.blind'}
          rules[#rules+1]={id='hunter.blind',classKey='8',status='blind',onHitRate=10}
          assert(selected()==nil)
          rules[3]=nil; rules[1].onHitRate=101; assert(selected()==nil)
          rules[1].onHitRate=100; rules[1].classKey='08'; assert(selected()==nil)
          rules[1].classKey='8'; rules[1].status='ko'; assert(selected()==nil)
        ''')

    def test_sparse_or_keyed_arrays_cannot_hide_conflicting_rules(self):
        self.check_lua('''
          ledger.characters['0']['8']={'hunter.silence','hunter.blind'}
          rules[3]={id='hunter.blind',classKey='8',status='blind',onHitRate=10}
          rules[2]=nil;assert(selected()==nil)
          rules[3]=nil;ledger.characters['0']['8']={[1]='hunter.silence',[3]='hunter.blind'}
          assert(selected()==nil)
          ledger.characters['0']['8']={'hunter.silence'};rules.hidden='ignored'
          assert(selected()==nil)
          rules.hidden=nil;setmetatable(rules,{__index=function() return rules[1] end})
          assert(selected()==nil)
        ''')

    def test_enemy_and_nonparty_context_are_neutral(self):
        self.check_lua('''
          assert(effect.resolve(nil,context,ledger,rules,'weapon')==nil)
          assert(effect.resolve(actor,42,ledger,rules,'weapon')==nil)
          context.kind='enemy'; assert(selected()==nil)
          context.kind='main-party'; context.actorId=1; assert(selected()==nil)
          context.actorId=6; actor.identifier=6; assert(selected()==nil)
        ''')

    def test_ephemeral_proxy_and_scratch_rate_leave_shared_helper_unchanged(self):
        self.check_lua('''
          f={action=150,identifier=21}; helper={getEquipmentStatusEffects=function() return 0 end}
          local native171=function(f) f.onHitRate=0 end
          local native182=function(f,c,t,fs,cs,h)
            f.selectedMask=h.getEquipmentStatusEffects(c)
            assert(h.getEquipmentStatusEffects({})==0)
          end
          local a=effect.wrap171(native171,deps); local b=effect.wrap182(native182,deps)
          a(f,actor,{},nil,nil,helper); b(f,actor,{},nil,nil,helper)
          assert(f.onHitRate==100 and f.selectedMask==1<<9)
          assert(helper.getEquipmentStatusEffects(actor)==0)
          deps.ledger=nil; a(f,actor,{},nil,nil,helper); b(f,actor,{},nil,nil,helper)
          assert(f.onHitRate==0 and f.selectedMask==0)
          assert(actor.weapon==4206 and actor.job1==8)
        ''')

    def test_native_payload_and_other_actions_are_preserved(self):
        self.check_lua('''
          local a=effect.wrap171(function(f) f.onHitRate=15 end,deps)
          f={action=150,identifier=21};deps.nativeRate=function() return 15 end
          a(f,actor);assert(f.onHitRate==15)
          deps.nativeRate=function() return 0 end;deps.nativeMask=function() return 1<<9 end
          a(f,actor);assert(f.onHitRate==15)
          deps.nativeMask=function() return 0 end;f.action=114;a(f,actor);assert(f.onHitRate==15)
          f.action=150;f.identifier=20;a(f,actor);assert(f.onHitRate==15)
        ''')


if __name__ == '__main__':
    unittest.main()
