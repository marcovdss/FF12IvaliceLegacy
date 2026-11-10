"""Family identity contracts; the Forge adapters around them stay thin."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class FamilyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute(
            (Path(__file__).resolve().parents[1] / 'prototypes/family_identities.lua').read_text())
        self.lua.execute('''
          module.reset()
          reports={}
          active=true; cat=nil
          function wielder(t) return {type=0, identifier=0, weapon=4200, address=11} end
          function foe(address, defense, maxHp, level, mp)
            return {type=1, identifier=100, address=address or 555, defense=defense or 20,
                    maxHp=maxHp or 1000, level=level or 10, currentMp=mp or 50}
          end
          deps={active=function() return active end,
                category=function(unit) return cat end,
                report=function(kind, ...) reports[#reports+1]={kind, ...} end}
          classesFake={getBattleUnitKeep=function(addr) return {address=addr, maxMp=1000} end}
          function run(wrapped, caster, target, extra)
            local f={action=150, caster=caster, target=target}
            for k,v in pairs(extra or {}) do f[k]=v end
            local r=table.pack(wrapped(f, caster, target, {}, classesFake, {}))
            return f, r
          end
        ''')

    # ---------- Sunder ----------

    def test_axe_hit_dents_defence_and_keeps_this_hit_native(self):
        self.lua.execute('''
          cat='axe'
          original=function(f,c,t) f.multiplier=t.defense+50; f.modifier=t.defense; return 'ok' end
          wrapped=module.wrap68(original,deps)
          target=foe(555,20,1000,10)
          f,r=run(wrapped,wielder(),target)
          -- the first hit still met full Defence, and the dent landed on the unit
          assert(f.modifier==20 and f.multiplier==70, 'first hit native')
          assert(target.defense==19, target.defense)
          assert(r.n==1 and r[1]=='ok')
          -- the second hit's original reads the dented unit
          f,_=run(wrapped,wielder(),target)
          assert(f.modifier==19 and f.multiplier==69)
        ''')

    def test_sunder_stacks_to_half_the_fight_start(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,101,1000,10)
          for i=1,40 do run(wrapped,wielder(),target) end
          -- floor: half of the 101 the fight started with (float steps settle on 50.5)
          assert(target.defense>50.49 and target.defense<50.51, target.defense)
        ''')

    def test_sunder_rebaselines_when_an_address_is_recycled(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,100,1000,10)
          run(wrapped,wielder(),target); assert(target.defense==95)
          target.maxHp=9000; target.level=50; target.defense=300
          run(wrapped,wielder(),target); assert(target.defense==285, target.defense)  -- new fight, new baseline
        ''')

    def test_sunder_gates(self):
        self.lua.execute('''
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,20,1000,10)
          cat='hammer'; run(wrapped,wielder(),target); assert(target.defense==20)  -- hammers share function 68
          cat='axe'; f,_=run(wrapped,wielder(),target,{action=114}); assert(target.defense==20)  -- not Attack
          active=false; f,_=run(wrapped,wielder(),target); assert(target.defense==20)  -- gate closed
          active=true; run(wrapped,wielder(),target); assert(target.defense==19)
          foeattacker={type=0, identifier=30, weapon=4200, address=11}  -- an Esper identifier
          run(wrapped,foeattacker,target); assert(target.defense==19)
          ally={type=0, identifier=1, address=99, defense=20, maxHp=100, level=5}
          t2=foe(777,20,1000,10); t2.type=0  -- an attack aimed at a character dents nothing
          run(wrapped,wielder(),t2); assert(t2.defense==20)
        ''')

    # ---------- Pierce ----------

    def test_spear_hit_ignores_part_of_defence_and_restores_the_field(self):
        self.lua.execute('''
          cat='spear'
          seen={}
          inner=function(f,c,t) seen[#seen+1]=f.modifier; f.target.removedHp=7; return 'r', nil, 3 end
          wrapped=module.pierceWrap(inner,deps)
          target=foe(555,30,1000,10)
          f,r=run(wrapped,wielder(),target,{modifier=30})
          assert(seen[1]==21, seen[1])                 -- the inner chain saw 70% of Defence
          assert(f.modifier==30, f.modifier)           -- and the field is native again afterwards
          assert(r.n==3 and r[1]=='r' and r[3]==3)
          cat='sword'; seen={} ; f,_=run(wrapped,wielder(),target,{modifier=30})
          assert(seen[1]==30 and f.modifier==30)       -- other families are untouched
          cat='spear'; seen={}; f,_=run(wrapped,wielder(),target,{modifier=30, outcomeType=10})
          assert(seen[1]==30)                          -- defence-ignoring outcomes skip Pierce
          f,_=run(wrapped,wielder(),target,{modifier=0}); assert(f.modifier==0)
        ''')

    # ---------- Unseen and Marksman ----------

    def test_ninja_and_gun_drop_only_their_own_evasion(self):
        self.lua.execute('''
          original=function(f,c,t) f.evadeWeapon=10; f.evadeShield=10; f.evadeParry=10; return 'ok' end
          wrapped=module.wrap22(original,deps)
          cat='ninjaSword'; f,r=run(wrapped,wielder(),foe())
          assert(f.evadeWeapon==0 and f.evadeShield==0 and f.evadeParry==10 and r[1]=='ok')
          cat='gun'; f,_=run(wrapped,wielder(),foe())
          assert(f.evadeWeapon==10 and f.evadeShield==10 and f.evadeParry==0)
          cat='sword'; f,_=run(wrapped,wielder(),foe())
          assert(f.evadeWeapon==10 and f.evadeParry==10)
        ''')

    # ---------- Afflict ----------

    def test_crossbow_doubles_the_on_hit_rate_to_the_cap(self):
        self.lua.execute('''
          cat='crossbow'
          wrapped=module.wrap171(function(f,c,t) f.onHitRate=30; return 'ok' end,deps)
          f,r=run(wrapped,wielder(),foe(),{action=114})   -- any action: delivery is the family's nature
          assert(f.onHitRate==60 and r[1]=='ok')
          f,_=run(wrapped,wielder(),foe()); assert(f.onHitRate==60)
          wrapped=module.wrap171(function(f,c,t) f.onHitRate=70; end,deps)
          f,_=run(wrapped,wielder(),foe()); assert(f.onHitRate==100)
          cat='bow'; wrapped=module.wrap171(function(f,c,t) f.onHitRate=30; end,deps)
          f,_=run(wrapped,wielder(),foe()); assert(f.onHitRate==30)
        ''')

    # ---------- Spellbreak ----------

    def test_mace_hit_burns_a_share_of_damage_capped_by_the_pool(self):
        self.lua.execute('''
          cat='mace'
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=250; return 'ok' end,deps)
          target=foe(555,20,1000,10,500)
          f,r=run(wrapped,wielder(),target)
          assert(f.target.removedMp==25, f.target.removedMp)   -- 10% of 250
          assert(f.target.addedMp==0 and f.target.mpUpdateState==1)
          target.mpUpdateState=nil; target.removedMp=nil       -- fresh target state for the next hit
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=1000; end,deps)
          f,_=run(wrapped,wielder(),target)                    -- 100 burn asked, pool caps at 10% of 1000
          assert(f.target.removedMp==100, f.target.removedMp)
          target.mpUpdateState=nil; target.removedMp=nil
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=10; end,deps)
          f,_=run(wrapped,wielder(),target)                    -- tiny hit still burns the minimum
          assert(f.target.removedMp==3, f.target.removedMp)
        ''')

    def test_spellbreak_stacks_on_a_queued_leech_never_past_the_pool(self):
        self.lua.execute('''
          cat='mace'
          -- a Green Mage's leech queued 40 MP loss and credited it to the caster first
          local function leeching(f,c,t) f.target.removedHp=400; f.target.mpUpdateState=1
            f.target.addedMp=0; f.target.removedMp=40; return 'ok' end
          wrapped=module.wrap165(leeching,deps)
          target=foe(555,20,1000,10,500)
          f,_=run(wrapped,wielder(),target)
          assert(f.target.removedMp==80, f.target.removedMp)   -- leech 40 kept, burn 40 added on top
          -- a drained foe with little MP left: the burn stops at what remains
          low=foe(777,20,1000,10,45)
          f,_=run(wrapped,wielder(),low)
          assert(f.target.removedMp==45, f.target.removedMp)   -- 40 queued + 5 room, not the full 40 burn
          -- the caster's own gains from the leech are untouched
          castergain=foe(888,20,1000,10,500)
          castergain.mpUpdateState=1; castergain.addedMp=nil; castergain.removedMp=40
          local wielded=wielder(); wielded.mpUpdateState=1; wielded.addedMp=40
          f,_=run(wrapped,wielded,castergain)
          assert(wielded.addedMp==40 and castergain.removedMp==80)
        ''')

    def test_spellbreak_gates(self):
        self.lua.execute('''
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=250; end,deps)
          target=foe(555,20,1000,10,500)
          cat='rod'; run(wrapped,wielder(),target); assert(target.removedMp==nil)
          cat='mace'; run(wrapped,wielder(),target,{action=114}); assert(target.removedMp==nil)
          dry=foe(777,20,1000,10,0)   -- no MP pool, nothing to burn
          run(wrapped,wielder(),dry); assert(dry.removedMp==nil)
          ally=foe(888,20,1000,10,500); ally.type=0
          run(wrapped,wielder(),ally); assert(ally.removedMp==nil)
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=-1; end,deps)  -- immune: nothing landed
          run(wrapped,wielder(),target); assert(target.removedMp==nil)
        ''')

    # ---------- Mend ----------

    def test_rod_boosts_healing_spells_only(self):
        self.lua.execute('''
          cat='rod'
          original=function(f,c,t) f.target.addedHp=100; return 'r', nil, 2 end
          wrapped=module.mendWrap(original,deps)
          ally={type=0, identifier=1, statusEffects={}}
          f,r=run(wrapped,wielder(),ally,{action=5})
          assert(f.target.addedHp==120, f.target.addedHp)
          assert(r.n==3 and r[1]=='r' and r[3]==2)
          cat='staff'; f,_=run(wrapped,wielder(),ally,{action=5}); assert(f.target.addedHp==100)
          cat='rod'; f,_=run(wrapped,wielder(),ally,{action=5})
          ally.statusEffects.reverse=1; f,_=run(wrapped,wielder(),ally,{action=5})
          assert(f.target.addedHp==100)                -- never amplify a reversed heal
          ally.statusEffects.reverse=nil
          f,_=run(wrapped,wielder(),ally,{action=5}); assert(f.target.addedHp==120)
          f,_=run(wrapped,wielder(),foe(),{action=5}); assert(f.target.addedHp==100)  -- a foe heal stays native
          broken=module.mendWrap(function(f,c,t) f.target.addedHp=90; end,deps)
          f,_=run(broken,wielder(),ally,{action=5}); assert(f.target.addedHp==108)
          f,_=run(broken,wielder(),ally,{action=5}); assert(f.target.addedHp==108)  -- re-heals, no compounding
        ''')

    # ---------- Cadence ----------

    def test_fists_ramp_on_consecutive_hits_and_reset_on_a_switch(self):
        self.lua.execute('''
          cat='unarmed'
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=1000; return 'ok' end,deps)
          a=wielder(); b={type=0, identifier=2, weapon=4200, address=12}
          t1=foe(555,20,1000,10); t2=foe(666,20,1000,10); t2.identifier=101   -- a different foe
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)              -- first hit: no bonus yet
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1060)              -- +6%
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1120)              -- +12%
          f,_=run(wrapped,a,t2); assert(f.target.removedHp==1000)              -- switch: restart
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)              -- switching back restarts too
          for i=1,6 do f,_=run(wrapped,a,t1) end
          assert(f.target.removedHp==1300, f.target.removedHp)                 -- six steps: capped at +30%
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1300)              -- the cap holds
          f,_=run(wrapped,b,t1); assert(f.target.removedHp==1000)              -- another fighter has no streak
        ''')

    def test_cadence_gates(self):
        self.lua.execute('''
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=1000; end,deps)
          a=wielder(); t1=foe(555,20,1000,10)
          cat='mace'; run(wrapped,a,t1); run(wrapped,a,t1)
          cat='unarmed'; f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)  -- mace hits never fed the ramp
          f,_=run(wrapped,a,t1,{action=114}); assert(f.target.removedHp==1000)    -- only ordinary Attack counts
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1060)                 -- the streak still counts Attack
          active=false; f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)   -- gate closed: hit not counted
          active=true; f,_=run(wrapped,a,t1); assert(f.target.removedHp==1120)    -- the streak survived the gap
          module.reset(); f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000) -- reset clears the streaks
          foeattacker={type=0, identifier=30, weapon=4200, address=13}            -- an Esper never ramps
          run(wrapped,foeattacker,t1); f,_=run(wrapped,foeattacker,t1); assert(f.target.removedHp==1000)
        ''')

    # ---------- Counterflow ----------

    def test_pole_defender_counters_twice_as_often(self):
        self.lua.execute('''
          seen={}
          original=function(f,c,t) seen[#seen+1]=f.counterChance; f.counterState=1; return 'ok' end
          wrapped=module.wrap8(original,deps)
          defender=wielder()
          cat='pole'
          f,r=run(wrapped,foe(),defender,{counterChance=30})
          assert(seen[1]==60, seen[1])
          assert(f.counterState==1 and r[1]=='ok')
          f,_=run(wrapped,foe(),defender,{counterChance=90}); assert(seen[2]==100)  -- capped
          cat='sword'; f,_=run(wrapped,foe(),defender,{counterChance=30}); assert(seen[3]==30)
          cat='pole'; f,_=run(wrapped,foe(),foe(),{counterChance=30}); assert(seen[4]==30)  -- a foe defender
          f,_=run(wrapped,foe(),defender,{counterChance=0}); assert(seen[5]==0)     -- nothing to double
          f,_=run(wrapped,foe(),defender,{counterChance=30}); assert(seen[6]==60); active=false
          f,_=run(wrapped,foe(),defender,{counterChance=30}); assert(seen[7]==30)   -- gate closed
        ''')

    # ---------- Chain ----------

    def test_katana_combos_run_longer_inside_the_cap(self):
        self.lua.execute('''
          wrapped=module.wrap10(function(f,c,t) f.caster.keep={battleFlags={comboCount=5}}; return 'ok' end,deps)
          caster=wielder()
          cat='katana'
          f,r=run(wrapped,caster,foe())
          assert(f.caster.keep.battleFlags.comboCount==7 and r[1]=='ok')
          wrapped=module.wrap10(function(f,c,t) f.caster.keep={battleFlags={comboCount=10}}; end,deps)
          f,_=run(wrapped,caster,foe()); assert(f.caster.keep.battleFlags.comboCount==11)
          wrapped=module.wrap10(function(f,c,t) f.caster.keep={battleFlags={comboCount=0}}; end,deps)
          f,_=run(wrapped,caster,foe()); assert(f.caster.keep.battleFlags.comboCount==0)  -- no combo, no Chain
          cat='dagger'; wrapped=module.wrap10(function(f,c,t) f.caster.keep={battleFlags={comboCount=5}}; end,deps)
          f,_=run(wrapped,caster,foe()); assert(f.caster.keep.battleFlags.comboCount==5)
          f,_=run(wrapped,caster,foe(),{action=114}); assert(f.caster.keep.battleFlags.comboCount==5)
        ''')

    # ---------- shared ----------

    def test_wielder_never_matches_foes_or_summons(self):
        self.lua.execute('''
          assert(module.isWielder({type=0, identifier=0}))
          assert(module.isWielder({type=0, identifier=5}))
          assert(not module.isWielder({type=1, identifier=0}))
          assert(not module.isWielder({type=0, identifier=27}))
          assert(not module.isWielder({type=0, identifier='x'}))
          assert(not module.isWielder(nil))
        ''')

    def test_original_survives_a_broken_reader(self):
        self.lua.execute('''
          calls=0
          brokenDeps={active=function() error('reader unavailable') end, category=deps.category}
          wrapped=module.wrap68(function(f,c,t) calls=calls+1; f.target.defense=19; return 'x', nil, 3 end, brokenDeps)
          target=foe(555,20,1000,10)
          f,r=run(wrapped,wielder(),target)
          assert(calls==1 and target.defense==19 and r.n==3 and r[1]=='x' and r[3]==3)
          pierce=module.pierceWrap(function(f,c,t) return 'p' end, brokenDeps)
          f,r=run(pierce,wielder(),target,{modifier=30})
          assert(f.modifier==30 and r[1]=='p')
          local r2=table.pack(module.wrap8(function(f,c,t) return 'c' end, brokenDeps)(f,wielder(),foe(),{},{}))
          assert(r2[1]=='c')
        ''')

    def test_reset_clears_the_dent_memory(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,100,1000,10)
          run(wrapped,wielder(),target); assert(target.defense==95)
          module.reset(); target.defense=100
          run(wrapped,wielder(),target); assert(target.defense==95)  -- re-baselined, not stacked from 95
        ''')


if __name__ == '__main__':
    unittest.main()
