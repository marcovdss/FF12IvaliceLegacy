"""Family identity contracts, against objects shaped like The Insurgent's Forge 1.0.3 hands them over.

Forge passes three different objects to every function: `caster`/`target` are battle unit keeps
(classes/getBattleUnitKeep.lua), `formula` is the hit's work record (getFormulaProcWork.lua) and
`formula.caster`/`formula.target` are the per-hit results (getFormulaProcWorkPlus.lua). Forge's class
objects raise on any field they do not define. The fakes below do the same, so a decision that reads
the wrong object fails here exactly as it would fail (silently, inside its pcall) in game. 0.1.x
passed its tests with one table standing in for both target objects and never fired in play.
"""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]

# Field names copied from The Insurgent's Forge 1.0.3 classes (only the ones a fake may need; the
# others still raise, which is the point).
FORGE_FAKES = r'''
local function strict(kind, fields, values)
  local store = {}
  for k, v in pairs(values or {}) do
    if not fields[k] then error(kind .. ': fake built with unknown field ' .. k) end
    store[k] = v
  end
  return setmetatable({}, {
    __index = function(_, k)
      if k == 'address' and fields.address then return store.address end
      if not fields[k] then error("Couldn't get unknown property: " .. tostring(k) .. ' (' .. kind .. ')', 2) end
      return store[k]
    end,
    __newindex = function(_, k, v)
      if not fields[k] then error("Couldn't set unknown property: " .. tostring(k) .. ' (' .. kind .. ')', 2) end
      if type(v) ~= 'number' then error("Couldn't set property, value type must be a number: " .. k, 2) end
      store[k] = v
    end,
  })
end
local function set(list) local s = {} for _, k in ipairs(list) do s[k] = true end return s end

KEEP = set{'address', 'identifier', 'type', 'maxHp', 'maxMp', 'strength', 'magickPower', 'vitality', 'speed',
           'attackPower', 'defense', 'magickResist', 'evadeParry', 'evadeWeapon', 'evadeShield', 'currentHp',
           'currentMp', 'weapon', 'offhand', 'level', 'temporaryAugments', 'permanentAugments'}
AUGS = set{'counter', 'counterBoost'}
PLUS = set{'keep', 'battleUnitWork', 'statusEffects', 'augments', 'maxHp', 'maxMp', 'identifier', 'classification',
           'addedHp', 'removedHp', 'matchedMp', 'addedMp', 'removedMp', 'strength', 'magickPower', 'vitality',
           'speed', 'attackPower', 'defense', 'magickResist', 'level', 'hpUpdateState', 'mpUpdateState'}
WORK = set{'caster', 'target', 'initialTarget', 'action', 'identifier', 'causeType', 'outcomeType', 'skipState',
           'absorbState', 'criticalState', 'comboState', 'counterState', 'knockbackState', 'counterChance',
           'knockbackChance', 'comboChance', 'accuracyRate', 'onHitRate', 'evadeParry', 'evadeShield',
           'evadeWeapon', 'power', 'multiplier', 'modifier'}
PROC_KEEP = set{'battleFlags', 'reverseTargetFlags'}
FLAGS = set{'castingState', 'comboState', 'counterState', 'knockbackState', 'comboCount'}
UNIT_WORK = set{'comboCount', 'battleFlags'}
STANCE = set{'isInBattleStance'}
STATUS = set{'reverse', 'blind', 'silence', 'slow', 'stop', 'sleep', 'disable'}

function keep(values)
  local v = values or {}
  v.temporaryAugments = v.temporaryAugments or strict('augments', AUGS, {counter = 0, counterBoost = 0})
  v.permanentAugments = v.permanentAugments or strict('augments', AUGS, {counter = 0, counterBoost = 0})
  return strict('keep', KEEP, v)
end
function plus(values)
  local v = values or {}
  local flags = strict('battleFlags', FLAGS, {comboCount = v.storedCombo or 0})
  local procKeep = strict('procKeep', PROC_KEEP, {battleFlags = flags})
  local unitWork = strict('battleUnitWork', UNIT_WORK,
                          {comboCount = v.combosSoFar or 0, battleFlags = strict('stance', STANCE, {isInBattleStance = 1})})
  local status = strict('statusEffects', STATUS, {reverse = v.reverse or 0, stop = v.stop or 0, sleep = v.sleep or 0,
                                                  disable = v.disable or 0})
  return strict('procWorkPlus', PLUS, {
    augments = strict('augments', AUGS, {counter = v.counterAug or 0, counterBoost = 0}),
    keep = procKeep, battleUnitWork = unitWork, statusEffects = status, identifier = v.identifier or 0,
    classification = v.classification or 0, maxHp = v.maxHp or 1000, maxMp = v.maxMp or 100,
    addedHp = 0, removedHp = 0, addedMp = 0, removedMp = 0, defense = -1, level = v.level or 10,
    hpUpdateState = 0, mpUpdateState = v.mpUpdateState or 0})
end
function work(values) return strict('procWork', WORK, values) end

-- The party member and the foe as Forge sees them: a keep each, plus this hit's result records.
function wielder(id, address) return keep{type = 0, identifier = id or 0, weapon = 4200, address = address or 11,
                                          maxHp = 1000, maxMp = 100, currentHp = 1000, currentMp = 100, level = 20,
                                          speed = 40} end
function foe(address, defense, maxHp, level, mp, identifier)
  return keep{type = 1, identifier = identifier or 100, address = address or 555, defense = defense or 20,
              maxHp = maxHp or 1000, maxMp = 1000, level = level or 10, currentHp = maxHp or 1000,
              currentMp = mp == nil and 50 or mp}
end
-- One hit: a fresh work record whose result records mirror the two keeps.
function hit(caster, target, fields)
  local f = fields or {}
  local w = {action = f.action or 150, outcomeType = f.outcomeType or 0, comboState = 0, counterState = 0,
             counterChance = f.counterChance or 0, comboChance = 0, onHitRate = 0, evadeParry = 0,
             evadeShield = 0, evadeWeapon = 0, power = 0, multiplier = 0, modifier = f.modifier or 0,
             absorbState = 0, skipState = 0}
  w.caster = plus{identifier = caster.identifier, storedCombo = f.storedCombo, combosSoFar = f.combosSoFar}
  w.target = plus{identifier = target.identifier, reverse = f.reverse, classification = f.classification,
                  mpUpdateState = f.mpUpdateState, maxMp = target.maxMp, stop = f.stop, sleep = f.sleep,
                  disable = f.disable, counterAug = f.counterAug}
  return work(w)
end
'''


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class FamilyIdentityTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute((ROOT / 'prototypes/family_identities.lua').read_text())
        self.lua.execute(FORGE_FAKES)
        self.lua.execute('''
          module.reset()
          reports={}
          active=true; cat=nil
          deps={active=function() return active end,
                category=function(unit) return cat end,
                report=function(kind, ...) reports[#reports+1]={kind, ...} end}
          function run(wrapped, caster, target, fields)
            local f = hit(caster, target, fields)
            local r = table.pack(wrapped(f, caster, target, {}, {}, {}))
            return f, r
          end
          -- the engine applying this hit's Defence slot to the keep, as it does for Expose
          function settle(f, target) if f.target.defense >= 0 then target.defense = f.target.defense end end
        ''')

    def test_fakes_raise_on_the_wrong_object_like_forge(self):
        self.lua.execute('''
          local t = foe()
          local f = hit(wielder(), t)
          assert(not pcall(function() return f.target.type end))        -- results have no type
          assert(not pcall(function() return f.target.weapon end))      -- nor a weapon
          assert(not pcall(function() return t.statusEffects end))      -- keeps have no statusEffects
          assert(f.target.defense == -1)                                 -- the Defence slot starts at "no change"
        ''')

    # ---------- Sunder ----------

    def test_axe_hit_queues_a_dent_and_keeps_this_hit_native(self):
        self.lua.execute('''
          cat='axe'
          original=function(f,c,t) f.multiplier=t.defense+50; f.modifier=t.defense; return 'ok' end
          wrapped=module.wrap68(original,deps)
          target=foe(555,20,1000,10)
          f,r=run(wrapped,wielder(),target)
          assert(f.modifier==20 and f.multiplier==70, 'first hit native')
          assert(f.target.defense==19, f.target.defense)   -- queued for the engine
          assert(target.defense==20)                       -- the keep is the engine's to change
          assert(r.n==1 and r[1]=='ok')
          settle(f,target)
          f,_=run(wrapped,wielder(),target)                -- the next hit meets the dented keep
          assert(f.modifier==19 and f.multiplier==69 and f.target.defense==18)
          assert(reports[1][1]=='sunder' and reports[1][4]==20 and reports[1][5]==19)
        ''')

    def test_sunder_stacks_to_half_the_fight_start(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,101,1000,10)
          for i=1,40 do local f=run(wrapped,wielder(),target); settle(f,target) end
          assert(target.defense==51, target.defense)       -- ceil(101 / 2): never below half
          local f=run(wrapped,wielder(),target); assert(f.target.defense==-1)  -- at the floor nothing is queued
        ''')

    def test_sunder_small_defence_still_steps(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,10,1000,10)
          for i=1,10 do local f=run(wrapped,wielder(),target); settle(f,target) end
          assert(target.defense==5, target.defense)
        ''')

    def test_sunder_keeps_a_lower_queued_defence(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) f.target.defense=14 end,deps)  -- an Expose-like rider
          target=foe(555,20,1000,10)
          f,_=run(wrapped,wielder(),target); assert(f.target.defense==14)
        ''')

    def test_sunder_rebaselines_when_an_address_is_recycled(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,100,1000,10)
          f=run(wrapped,wielder(),target); settle(f,target); assert(target.defense==95)
          target.maxHp=9000; target.level=50; target.defense=200
          f=run(wrapped,wielder(),target); assert(f.target.defense==190, f.target.defense)  -- new fight, new baseline
        ''')

    def test_sunder_gates(self):
        self.lua.execute('''
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,20,1000,10)
          cat='hammer'; f=run(wrapped,wielder(),target); assert(f.target.defense==-1)  -- hammers share function 68
          cat='axe'; f=run(wrapped,wielder(),target,{action=114}); assert(f.target.defense==-1)  -- not Attack
          active=false; f=run(wrapped,wielder(),target); assert(f.target.defense==-1)  -- gate closed
          active=true; f=run(wrapped,wielder(),target); assert(f.target.defense==19)
          esper=wielder(30, 13)                                                  -- an Esper identifier
          f=run(wrapped,esper,target); assert(f.target.defense==-1)
          ally=wielder(1, 99)                                                    -- aimed at a character
          f=run(wrapped,wielder(),ally); assert(f.target.defense==-1)
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
          assert(reports[1][1]=='pierce')
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
          assert(reports[1][1]=='unseen' and reports[2][1]=='marksman' and #reports==2)
          assert(reports[1][4]=='evadeWeapon=10 evadeShield=10' and reports[2][4]=='evadeParry=10', reports[2][4])
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
          target=foe(555,20,1000,10,500)
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=250; return 'ok' end,deps)
          f,r=run(wrapped,wielder(),target)
          assert(f.target.removedMp==25, f.target.removedMp)   -- 10% of 250
          assert(f.target.addedMp==0 and f.target.mpUpdateState==1 and r[1]=='ok')
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=5000; end,deps)
          f,_=run(wrapped,wielder(),target)                    -- 500 asked, the pool caps at 10% of 1000
          assert(f.target.removedMp==100, f.target.removedMp)
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=10; end,deps)
          f,_=run(wrapped,wielder(),target)                    -- a tiny hit still burns the minimum
          assert(f.target.removedMp==3, f.target.removedMp)
        ''')

    def test_spellbreak_stacks_on_a_queued_leech_never_past_the_pool(self):
        self.lua.execute('''
          cat='mace'
          -- a Green Mage's leech queued 40 MP loss and credited it to the caster first
          local function leeching(f,c,t) f.target.removedHp=400; f.target.mpUpdateState=1
            f.target.addedMp=0; f.target.removedMp=40; f.caster.mpUpdateState=1; f.caster.addedMp=40; return 'ok' end
          wrapped=module.wrap165(leeching,deps)
          f,_=run(wrapped,wielder(),foe(555,20,1000,10,500))
          assert(f.target.removedMp==80, f.target.removedMp)   -- leech 40 kept, burn 40 added on top
          assert(f.caster.addedMp==40)                         -- the caster's credit is untouched
          f,_=run(wrapped,wielder(),foe(777,20,1000,10,45))
          assert(f.target.removedMp==45, f.target.removedMp)   -- 40 queued + 5 room, not the full 40 burn
        ''')

    def test_spellbreak_gates(self):
        self.lua.execute('''
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=250; end,deps)
          target=foe(555,20,1000,10,500)
          cat='rod'; f=run(wrapped,wielder(),target); assert(f.target.removedMp==0)
          cat='mace'; f=run(wrapped,wielder(),target,{action=114}); assert(f.target.removedMp==0)
          f=run(wrapped,wielder(),foe(777,20,1000,10,0)); assert(f.target.removedMp==0)  -- no MP to burn
          f=run(wrapped,wielder(),wielder(1,88)); assert(f.target.removedMp==0)          -- a character target
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=-1; end,deps)       -- immune: nothing landed
          f=run(wrapped,wielder(),target); assert(f.target.removedMp==0)
        ''')

    # ---------- Mend ----------

    def test_rod_boosts_healing_spells_only(self):
        self.lua.execute('''
          cat='rod'
          original=function(f,c,t) f.target.addedHp=100; return 'r', nil, 2 end
          wrapped=module.mendWrap(original,deps)
          ally=wielder(1, 99)
          f,r=run(wrapped,wielder(),ally,{action=5})
          assert(f.target.addedHp==120, f.target.addedHp)
          assert(r.n==3 and r[1]=='r' and r[3]==2)
          cat='staff'; f,_=run(wrapped,wielder(),ally,{action=5}); assert(f.target.addedHp==100)
          cat='rod'
          f,_=run(wrapped,wielder(),ally,{action=5}); assert(f.target.addedHp==120)
          f,_=run(wrapped,wielder(),foe(),{action=5}); assert(f.target.addedHp==100)  -- a foe heal stays native
        ''')

    def test_mend_never_amplifies_a_backfire(self):
        self.lua.execute('''
          cat='rod'
          -- Forge 110: the heal branch writes addedHp, the reversed branch removedHp
          local function forge110(f,c,t)
            local heal = (f.target.classification == 13 and f.target.statusEffects.reverse == 1) or
                         (f.target.classification ~= 13 and f.target.statusEffects.reverse == 0)
            if heal then f.target.addedHp = 100 else f.target.removedHp = 100 end
          end
          wrapped=module.mendWrap(forge110,deps)
          ally=wielder(1, 99)
          f=run(wrapped,wielder(),ally,{action=5,reverse=1})
          assert(f.target.addedHp==0 and f.target.removedHp==100)          -- reversed: hurts, natively
          f=run(wrapped,wielder(),ally,{action=5,reverse=1,classification=13})
          assert(f.target.addedHp==120)                                    -- reversed undead: a real heal
          f=run(wrapped,wielder(),ally,{action=5,classification=13})
          assert(f.target.removedHp==100 and f.target.addedHp==0)          -- undead: hurts, natively
        ''')

    # ---------- Cadence ----------

    def test_fists_ramp_on_consecutive_hits_and_reset_on_a_switch(self):
        self.lua.execute(r"""
          cat='unarmed'
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=1000; return 'ok' end,deps)
          a=wielder(); b=wielder(2, 12)
          t1=foe(555,20,1000,10); t2=foe(666,20,1000,10,50,101)              -- a different foe
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)              -- first hit: no bonus yet
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1060)              -- +6%
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1120)              -- +12%
          f,_=run(wrapped,a,t2); assert(f.target.removedHp==1000)              -- switch: restart
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)              -- switching back restarts too
          for i=1,6 do f,_=run(wrapped,a,t1) end
          assert(f.target.removedHp==1300, f.target.removedHp)                 -- capped at +30%
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1300)              -- the cap holds
          f,_=run(wrapped,b,t1); assert(f.target.removedHp==1000)              -- another fighter has no streak
          t1.maxHp=5000
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)              -- recycled address: a new foe
        """)

    def test_cadence_hastens_the_next_charge_once_per_attack(self):
        self.lua.execute(r"""
          cat='unarmed'
          NOW=1000000; armed={}
          deps.clock=function() return NOW end
          deps.armCharge=function(address, fraction) armed[#armed+1]={address, fraction} end
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=1000 end,deps)
          a=wielder(); t1=foe(555,20,1000,10)
          f=run(wrapped,a,t1); assert(#armed==0)                               -- first Attack: nothing yet
          NOW=NOW+2000000; f=run(wrapped,a,t1)
          assert(f.target.removedHp==1060 and #armed==1 and armed[1][1]==11)
          assert(math.abs(armed[1][2]-0.05)<1e-9, armed[1][2])                 -- next charge 5% faster
          NOW=NOW+100000; f=run(wrapped,a,t1)                                  -- the same Attack's combo hit
          assert(f.target.removedHp==1060 and #armed==1)                       -- same bonus, no step, no haste
          for i=1,8 do NOW=NOW+2000000; f=run(wrapped,a,t1) end
          assert(f.target.removedHp==1300)
          assert(math.abs(armed[#armed][2]-0.25)<1e-9, armed[#armed][2])       -- capped at 25%
          NOW=NOW+11000000; f=run(wrapped,a,t1)                                -- 11 s away: the streak broke
          assert(f.target.removedHp==1000)
          local last
          for _, r in ipairs(reports) do if r[1]=='cadence' then last=r end end
          assert(last[4]==5 and last[5]=='fresh' and last[6]==1300, tostring(last[5]))
        """)

    def test_cadence_gates(self):
        self.lua.execute(r"""
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=1000; end,deps)
          a=wielder(); t1=foe(555,20,1000,10)
          cat='mace'; run(wrapped,a,t1); run(wrapped,a,t1)
          cat='unarmed'; f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)  -- mace hits never fed the ramp
          f,_=run(wrapped,a,t1,{action=114}); assert(f.target.removedHp==1000)    -- only ordinary Attack counts
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==1060)
          active=false; f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000)   -- gate closed: hit not counted
          active=true; f,_=run(wrapped,a,t1); assert(f.target.removedHp==1120)    -- the streak survived the gap
          module.reset(); f,_=run(wrapped,a,t1); assert(f.target.removedHp==1000) -- reset clears the streaks
          esper=wielder(30, 13)
          run(wrapped,esper,t1); f,_=run(wrapped,esper,t1); assert(f.target.removedHp==1000)
          wrapped=module.wrap165(function(f,c,t) f.target.removedHp=0 end,deps)
          f,_=run(wrapped,a,t1); assert(f.target.removedHp==0)                     -- a miss: nothing to scale
        """)

    def test_arm_charge_moves_the_next_charge_forward(self):
        self.lua.execute(r"""
          NOW=0; queued={}
          event={getClock=function() return NOW end,
                 executeAfterMs=function(ms, fn) queued[#queued+1]=fn end}
          w={address=1, currentChargeTime=1000, requiredChargeTime=1000}        -- the Attack just landed
          classes={getBattleUnitWork=function() return w end}
          helpers={getBattleUnitWork=function(a) return a end}
          assert(module.armCharge(11, 0.25, classes, helpers) == true)
          local fn=table.remove(queued, 1); w.currentChargeTime=0; fn()          -- timer reset, not started
          assert(#queued==1 and w.currentChargeTime==0)
          fn=table.remove(queued, 1); w.currentChargeTime=40; fn()               -- the next charge started
          assert(w.currentChargeTime==40+250 and #queued==0, w.currentChargeTime)
          w.currentChargeTime=1000; module.armCharge(11, 0.25, classes, helpers) -- nearly done: stops short
          w.currentChargeTime=900; fn=table.remove(queued, 1); fn()
          assert(w.currentChargeTime==999, w.currentChargeTime)
          event=nil; assert(module.armCharge(11, 0.25, classes, helpers) == false)  -- no event API: no-op
        """)

    # ---------- Counterflow ----------

    def test_pole_defender_counters_twice_as_often(self):
        self.lua.execute(r"""
          seen={}
          original=function(f,c,t) seen[#seen+1]=f.counterChance; f.counterState=1; return 'ok' end
          wrapped=module.wrap8(original,deps)
          defender=wielder(); defender.permanentAugments.counter=1   -- the Counter licence
          cat='pole'
          f,r=run(wrapped,foe(),defender,{counterChance=30})
          assert(seen[1]==60, seen[1])
          assert(f.counterState==1 and r[1]=='ok')
          assert(reports[1][1]=='counter' and reports[1][3]==30 and reports[1][4]==60 and reports[1][5]==1)
          assert(reports[1][6]=='flow=0' and reports[1][7]=='stance=1', reports[1][7])
          f,_=run(wrapped,foe(),defender,{counterChance=90}); assert(seen[2]==100)  -- capped
          cat='sword'; f,_=run(wrapped,foe(),defender,{counterChance=30}); assert(seen[3]==30)
          cat='pole'; f,_=run(wrapped,wielder(),foe(),{counterChance=30}); assert(seen[4]==30)  -- a foe defender
          active=false
          f,_=run(wrapped,foe(),defender,{counterChance=30}); assert(seen[5]==30)   -- gate closed
        """)

    def test_pole_counters_without_the_counter_augment(self):
        # Forge 7 leaves 0 without Counter: the pole grants Counter's own chance, Speed / 2.
        self.lua.execute(r"""
          seen={}
          wrapped=module.wrap8(function(f,c,t) seen[#seen+1]=f.counterChance; f.counterState=1 end,deps)
          defender=wielder()                       -- speed 40
          cat='pole'
          run(wrapped,foe(),defender,{counterChance=0}); assert(seen[1]==20, seen[1])
          run(wrapped,foe(),defender,{counterChance=0, stop=1}); assert(seen[2]==0)     -- Stop forbids it
          run(wrapped,foe(),defender,{counterChance=0, sleep=1}); assert(seen[3]==0)    -- so does Sleep
          run(wrapped,foe(),defender,{counterChance=0, disable=1}); assert(seen[4]==0)  -- and Disable
          run(wrapped,foe(),defender,{counterChance=40, disable=1}); assert(seen[5]==40) -- even with Counter
          cat='sword'; run(wrapped,foe(),defender,{counterChance=0}); assert(seen[6]==0) -- only poles
          cat='pole'; defender.speed=250; run(wrapped,foe(),defender,{counterChance=0})
          assert(seen[7]==75, seen[7])             -- without Counter the chance stops at 75
        """)

    def test_counterflow_builds_on_blows_taken_and_a_counter_spends_it(self):
        self.lua.execute(r"""
          cat='pole'
          seen={}; COUNTER=0
          wrapped=module.wrap8(function(f,c,t) seen[#seen+1]=f.counterChance; f.counterState=COUNTER end,deps)
          defender=wielder()                       -- speed 40: 20 base
          for i=1,3 do run(wrapped,foe(),defender,{counterChance=0}) end
          assert(seen[1]==20 and seen[2]==30 and seen[3]==40, seen[3])
          COUNTER=1; run(wrapped,foe(),defender,{counterChance=0}); assert(seen[4]==50)
          COUNTER=0; run(wrapped,foe(),defender,{counterChance=0}); assert(seen[5]==20)  -- spent
          run(wrapped,foe(),defender,{counterChance=0, sleep=1})                         -- asleep: no flow
          run(wrapped,foe(),defender,{counterChance=0}); assert(seen[7]==30, seen[7])
          for i=1,20 do run(wrapped,foe(),defender,{counterChance=0}) end
          assert(seen[#seen]==75)                                                        -- the cap holds
          defender.permanentAugments.counter=1
          run(wrapped,foe(),defender,{counterChance=40}); assert(seen[#seen]==100)        -- Counter: up to 100
          other=wielder(1, 99); run(wrapped,foe(),other,{counterChance=0})
          assert(seen[#seen]==20)                                                        -- flow is per wielder
          module.reset(); run(wrapped,foe(),defender,{counterChance=0}); assert(seen[#seen]==40)  -- flow gone; licensed now
          local kinds={}
          for _, r in ipairs(reports) do kinds[r[1]]=(kinds[r[1]] or 0)+1 end
          assert(kinds.counter==1 and kinds.counterflow>1)
        """)

    def test_pole_lends_the_counter_augment_and_takes_it_back(self):
        # The engine swings only for a unit with the Counter augment: a counter roll alone is not a counter.
        self.lua.execute(r"""
          cat='pole'; seen={}
          wrapped=module.wrap8(function(f,c,t) seen[#seen+1]=f.counterChance end,deps)
          defender=wielder()
          run(wrapped,foe(),defender,{counterChance=0})
          assert(defender.temporaryAugments.counter==1 and module.lentCounters()==1)
          assert(reports[#reports][8]=='lent')
          -- next blow: Forge 7 now sees the lent bit and grants Speed / 2; that is not the licence, no doubling
          run(wrapped,foe(),defender,{counterChance=20, counterAug=1}); assert(seen[2]==30, seen[2])
          module.reset()                                   -- a map jump does not forget the loan
          cat='sword'
          run(wrapped,foe(),defender,{counterChance=20, counterAug=1})
          assert(seen[3]==nil or seen[3]==0, tostring(seen[3]))
          assert(defender.temporaryAugments.counter==0 and module.lentCounters()==0)
          -- a licensed wielder is never lent anything and keeps the doubling
          cat='pole'; licensed=wielder(1, 99); licensed.permanentAugments.counter=1
          run(wrapped,foe(),licensed,{counterChance=20, counterAug=1}); assert(seen[#seen]==40)
          assert(licensed.temporaryAugments.counter==0 and module.lentCounters()==0)
          cat='sword'; run(wrapped,foe(),licensed,{counterChance=20, counterAug=1})
          assert(seen[#seen]==20 and licensed.permanentAugments.counter==1)  -- the licence is untouched
        """)

    def test_counterflow_fades_after_a_quiet_spell(self):
        self.lua.execute(r"""
          cat='pole'; seen={}; NOW=5000000
          deps.clock=function() return NOW end
          wrapped=module.wrap8(function(f,c,t) seen[#seen+1]=f.counterChance end,deps)
          defender=wielder()
          run(wrapped,foe(),defender,{counterChance=0}); NOW=NOW+3000000
          run(wrapped,foe(),defender,{counterChance=0}); assert(seen[2]==30)
          NOW=NOW+21000000
          run(wrapped,foe(),defender,{counterChance=0}); assert(seen[3]==20, seen[3])    -- 21 s quiet: faded
        """)

    # ---------- Chain ----------

    def test_katana_combo_extends_once_on_the_rolling_hit(self):
        self.lua.execute('''
          cat='katana'
          -- Forge 10, reduced: a fresh roll stores 5 and opens the combo; a follow-up hit checks the count
          local function forge10(f,c,t)
            f.comboState = 0
            local sofar = f.caster.battleUnitWork.comboCount
            if sofar > 0 then
              if sofar < f.caster.keep.battleFlags.comboCount then f.comboState = 1 end
              return 'follow'
            end
            f.caster.keep.battleFlags.comboCount = ROLL
            if ROLL > 0 then f.comboState = 1 end
            return 'ok'
          end
          wrapped=module.wrap10(forge10,deps)
          caster=wielder()
          ROLL=5; f,r=run(wrapped,caster,foe())
          assert(f.caster.keep.battleFlags.comboCount==7 and r[1]=='ok')
          ROLL=10; f=run(wrapped,caster,foe()); assert(f.caster.keep.battleFlags.comboCount==11)  -- engine cap
          -- follow-up hits of a running 7-hit combo leave the stored length alone
          f=run(wrapped,caster,foe(),{storedCombo=7, combosSoFar=3})
          assert(f.caster.keep.battleFlags.comboCount==7)
          ROLL=0; f=run(wrapped,caster,foe(),{storedCombo=4}); assert(f.caster.keep.battleFlags.comboCount==0)
          ROLL=5; cat='dagger'; f=run(wrapped,caster,foe()); assert(f.caster.keep.battleFlags.comboCount==5)
          cat='katana'; f=run(wrapped,caster,foe(),{action=114}); assert(f.caster.keep.battleFlags.comboCount==5)
        ''')

    # ---------- shared ----------

    def test_wraps_use_the_registered_deps_when_none_are_passed(self):
        # The Forge adapters call Family.wrapNN(original) with no deps: the registered ones must apply.
        self.lua.execute('''
          event={registerEventSync=function() return true end, registerEventAsync=function() return true end}
          local LEGACY = {[0x120000]='MZ', [0x120180]='PE\\0\\0\\x64\\x86', [0xD113D4]='LEGCY_\\0'}
          local wide = '%sLEGCY_%03d\\0'
          memory={readArray=function(address, count)
            if address == 0xD12200 then
              local t = {} for i=1,#wide do t[#t+1]=wide:byte(i); t[#t+1]=0 end return t
            end
            return LEGACY[address]
          end}
          bpack={section13=setmetatable({}, {__index=function(_, i) return {category = WEAPON_CATEGORY} end})}
          prints={}; print=function(line) prints[#prints+1]=line end
          assert(module.register() == true)
          WEAPON_CATEGORY = 9    -- gun
          wrapped=module.wrap22(function(f,c,t) f.evadeParry=10 end)
          f=run(wrapped,wielder(),foe())
          assert(f.evadeParry==0, 'Marksman must fire through the registered deps')
          WEAPON_CATEGORY = 10   -- axe
          target=foe(555,40,1000,10)
          f=run(module.wrap68(function() end),wielder(),target)
          assert(f.target.defense==38)
          local found = false
          for _, line in ipairs(prints) do if line:find('Legacy Family: marksman 0', 1, true) then found = true end end
          assert(found, table.concat(prints, '|'))
        ''')

    def test_family_lines_cover_every_category(self):
        self.lua.execute('''
          for _, name in ipairs(module.CATEGORY_NAMES) do
            local line = module.familyLine(name)
            assert(type(line) == 'table' and type(line[1]) == 'string' and #line[1] >= 3 and
                   type(line[2]) == 'string' and #line[2] >= 10, tostring(name))
            assert(not line[2]:find('\10', 1, true), name)  -- one line only; the help box adds the second
          end
          assert(module.familyLine('notAFamily') == nil)
          assert(module.familyLine(7) == nil and module.familyLine(nil) == nil)
        ''')

    def test_report_limit_is_per_identity(self):
        self.lua.execute('''
          event={registerEventSync=function() return true end, registerEventAsync=function() return true end}
          memory={readArray=function() return nil end}
          bpack={section13=setmetatable({}, {__index=function() return {category=9} end})}
          prints={}; print=function(line) prints[#prints+1]=line end
          module.register()
          local report = module.deps().report
          for i=1,200 do report('marksman', 0) end
          report('sunder', 0, 100, 20, 19)
          local m, s = 0, 0
          for _, line in ipairs(prints) do
            if line:find('marksman') then m = m + 1 end
            if line:find('sunder') then s = s + 1 end
          end
          assert(m == 150 and s == 1, m .. ' ' .. s)
        ''')

    def test_wielder_never_matches_foes_or_summons(self):
        self.lua.execute('''
          assert(module.isWielder(wielder(0)))
          assert(module.isWielder(wielder(5)))
          assert(not module.isWielder(foe()))
          assert(not module.isWielder(wielder(27)))
          assert(not module.isWielder({type=0, identifier='x'}))
          assert(not module.isWielder(nil))
        ''')

    def test_original_survives_a_broken_reader(self):
        self.lua.execute('''
          calls=0
          brokenDeps={active=function() error('reader unavailable') end, category=deps.category}
          wrapped=module.wrap68(function(f,c,t) calls=calls+1; return 'x', nil, 3 end, brokenDeps)
          target=foe(555,20,1000,10)
          f,r=run(wrapped,wielder(),target)
          assert(calls==1 and f.target.defense==-1 and r.n==3 and r[1]=='x' and r[3]==3)
          pierce=module.pierceWrap(function(f,c,t) return 'p' end, brokenDeps)
          f,r=run(pierce,wielder(),target,{modifier=30})
          assert(f.modifier==30 and r[1]=='p')
          f,r=run(module.wrap8(function(f,c,t) return 'c' end, brokenDeps),foe(),wielder())
          assert(r[1]=='c')
        ''')

    def test_unregistered_wraps_fall_back_to_native(self):
        # No deps passed and none registered (the module failed to register): every wrap is native.
        self.lua.execute('''
          cat='axe'
          f,r=run(module.wrap68(function() return 'n' end),wielder(),foe())
          assert(r[1]=='n' and f.target.defense==-1)
          f,r=run(module.wrap10(function() return 'n' end),wielder(),foe()); assert(r[1]=='n')
          f,r=run(module.mendWrap(function() return 'n' end),wielder(),wielder(1,9)); assert(r[1]=='n')
          f,r=run(module.wrap165(function() return 'n' end),wielder(),foe()); assert(r[1]=='n')
        ''')

    # ---------- Guard fatigue ----------

    def test_guard_fatigue_wears_down_a_wall_and_a_landed_hit_resets_it(self):
        self.lua.execute(r"""
          seen={}; OUTCOME=3; SKIP=1                 -- a shield block
          local function roll(f,c,t)
            seen[#seen+1]={f.evadeShield, f.evadeWeapon, f.evadeParry}
            f.outcomeType=OUTCOME; f.skipState=SKIP; return 'ok'
          end
          wrapped=module.guardWrap(roll,deps)
          tank=wielder()
          local function attack()
            local f=hit(foe(),tank); f.evadeShield=60; f.evadeWeapon=40; f.evadeParry=10
            local r=table.pack(wrapped(f,foe(),tank,{},{},{})); return f, r
          end
          local f,r=attack(); assert(r[1]=='ok')
          assert(seen[1][1]==60 and seen[1][2]==40 and seen[1][3]==10)      -- the first roll is untouched
          attack(); assert(seen[2][1]==45 and seen[2][2]==30 and seen[2][3]==7, seen[2][3])  -- one avoid: -25%
          OUTCOME=1; attack(); assert(seen[3][1]==30 and seen[3][2]==20)    -- a parry counts too
          OUTCOME=4; attack(); assert(seen[4][1]==15)                       -- block with a counter too
          attack(); assert(seen[5][1]==0 and seen[5][2]==0 and seen[5][3]==0)  -- four in a row: nothing left
          attack(); assert(seen[6][1]==0)                                    -- and it stays there
          OUTCOME=6; attack(); attack(); assert(seen[8][1]==0)               -- an accuracy miss changes nothing
          OUTCOME=0; SKIP=0; attack()                                        -- the hit lands
          OUTCOME=3; SKIP=1; attack(); assert(seen[10][1]==60, seen[10][1])  -- fresh guard
          local last
          for _, r in ipairs(reports) do if r[1]=='fatigue' then last=r end end
          assert(last[3]==4 and last[4]=='evadeShield=60>0 evadeWeapon=40>0 evadeParry=10>0', last[4])
        """)

    def test_guard_fatigue_gates_and_fades(self):
        self.lua.execute(r"""
          seen={}; NOW=1000000
          deps.clock=function() return NOW end
          wrapped=module.guardWrap(function(f) seen[#seen+1]=f.evadeShield; f.outcomeType=3; f.skipState=1 end,deps)
          local function attack(defender)
            local f=hit(foe(),defender); f.evadeShield=60; wrapped(f,foe(),defender,{},{},{})
          end
          tank=wielder()
          attack(tank); attack(tank); assert(seen[2]==45)
          NOW=NOW+11000000; attack(tank); assert(seen[3]==60, seen[3])      -- 11 s unattacked: fresh
          attack(wielder(1, 99)); assert(seen[4]==60)                        -- per defender
          local guard=foe(); attack(guard); attack(guard); assert(seen[6]==60)  -- foes evade natively
          active=false; attack(tank); assert(seen[7]==60)                    -- gate closed
          active=true; attack(tank); assert(seen[8]==45)
          module.reset(); attack(tank); assert(seen[9]==60)
        """)

    def test_family_costs_on_the_evade_roll(self):
        self.lua.execute(r"""
          OUT=1; SKIP=1; seen={}
          local roll=function(f) seen[#seen+1]={f.evadeShield, f.evadeWeapon, f.evadeParry}; f.outcomeType=OUT; f.skipState=SKIP end
          w={address=1, currentChargeTime=600, requiredChargeTime=1000}
          classes={getBattleUnitWork=function() return w end}; helpers={getBattleUnitWork=function(a) return a end}
          wrapped=module.guardWrap(roll,deps)
          local function attack(defender)
            local f=hit(foe(),defender); f.evadeShield=30; f.evadeWeapon=20; f.evadeParry=40
            wrapped(f,foe(),defender,{},classes,helpers); return f
          end
          -- Braced: a sword wielder's parry costs 10% of its own charge
          cat='sword'; local knight=wielder()
          attack(knight); assert(w.currentChargeTime==500, w.currentChargeTime)
          OUT=3; attack(wielder(1,99)); assert(w.currentChargeTime==400)                -- a block too
          OUT=1; attack(wielder(2,98)); assert(w.currentChargeTime==300)                -- a sword parry (weapon roll)
          OUT=5; attack(wielder(8,92)); assert(w.currentChargeTime==300)                -- a dodge costs nothing
          OUT=0; SKIP=0; attack(wielder(3,97)); assert(w.currentChargeTime==300)        -- nor a hit taken
          w.currentChargeTime=1000; OUT=5; SKIP=1; attack(wielder(4,96)); assert(w.currentChargeTime==1000)  -- full: untouched
          cat='pole'; w.currentChargeTime=600; attack(wielder(5,95)); assert(w.currentChargeTime==600)  -- only swords
          -- Long haft: a spear never parries (weapon roll); its shield and dodge stay
          cat='spear'; seen={}; attack(wielder(6,94))
          assert(seen[1][2]==0 and seen[1][1]==30 and seen[1][3]==40)
          -- Open: after a katana combo the next attack meets no evasion, once
          OUT=0; SKIP=0                                                                  -- hits land: no fatigue
          cat='katana'; local sam=wielder(7,93)
          local function forge10(f) f.comboState=1; f.caster.keep.battleFlags.comboCount=3 end
          local f=hit(sam,foe()); module.wrap10(forge10,deps)(f,sam,foe(),{},{},{})
          seen={}; attack(sam); attack(sam)
          assert(seen[1][1]==0 and seen[1][2]==0 and seen[1][3]==0, 'opened')
          assert(seen[2][3]==40, 'the opening is spent on one attack')
          -- a fresh katana Attack closes an unused opening
          f=hit(sam,foe()); module.wrap10(forge10,deps)(f,sam,foe(),{},{},{})
          f=hit(sam,foe()); module.wrap10(function(f) f.comboState=0 end,deps)(f,sam,foe(),{},{},{})
          seen={}; attack(sam); assert(seen[1][3]==40, 'closed by the next Attack')
          local kinds={}
          for _, r in ipairs(reports) do kinds[r[1]]=true end
          assert(kinds.braced and kinds.fatigue)
        """)

    def test_reset_clears_the_dent_memory(self):
        self.lua.execute('''
          cat='axe'
          wrapped=module.wrap68(function(f,c,t) end,deps)
          target=foe(555,100,1000,10)
          f=run(wrapped,wielder(),target); settle(f,target); assert(target.defense==95)
          module.reset(); target.defense=100
          f=run(wrapped,wielder(),target); assert(f.target.defense==95)  -- re-baselined, not stacked from 95
        ''')


if __name__ == '__main__':
    unittest.main()
