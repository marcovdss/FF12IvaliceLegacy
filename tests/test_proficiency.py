"""No-game contracts for Proficiency 0.2.0 (rules module, runtime and the Forge adapters)."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ProficiencyRulesTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().P = self.lua.execute((ROOT/'prototypes/proficiency.lua').read_text(encoding='utf-8'))
        self.lua.execute('''
          P.reset()
          function S(t)
            local s = {job=8, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, bystander=false,
                       category=1, damage=100, targetStatus={}, casterStatus={}, targetMp=50, casterHpPct=100,
                       casterMp=10, casterMaxMp=100, selfMaxHp=1000,
                       party={{address=100, job=8, hpPct=100, mp=10, maxMp=100, status={}},
                              {address=200, job=0, hpPct=40, mp=5, maxMp=100, status={silence=true}},
                              {address=300, job=6, hpPct=90, mp=80, maxMp=100, status={}}},
                       foes={{address=901, status={}}, {address=902, status={haste=true}}},
                       charge={current=20000, required=30000, last=20000}, roll=function() return true end}
            for k,v in pairs(t or {}) do s[k]=v end
            return s
          end
          function others(a, op) local n = 0 for _, o in ipairs(a and a.others or {}) do if o.op == op then n = n + 1 end end return n end
        ''')

    def run_lua(self, code):
        self.lua.execute(code)

    def test_levels_and_gates(self):
        self.run_lua('''
          local function eq(r, a, b, c) local l = P.levels(r); assert(l[1]==a and l[2]==b and l[3]==c, r) end
          eq(0,0,0,0); eq(1,1,0,0); eq(4,4,0,0); eq(5,4,1,0); eq(9,4,4,1); eq(12,4,4,4); eq(13,0,0,0); eq(2.5,0,0,0)
          assert(P.decide('hit', S({targetStatus={slow=true}}), 0) == nil)
          assert(P.decide('hit', S({job=99}), 12) == nil)
        ''')

    def test_hunter(self):
        self.run_lua('''
          assert(P.decide('hit', S({targetStatus={slow=true}}), 1).damage == 1.15)
          P.reset()
          assert(P.decide('hit', S({targetStatus={poison=true}}), 4).damage == 1.25)
          P.reset()
          assert(P.decide('hit', S({targetStatus={poison=true}}), 7).casterAdd[30])       -- II-3 Libra goes to the Hunter
          assert(P.decide('hit', S({targetStatus={poison=true}, casterStatus={libra=true}}), 7).casterAdd == nil)
          local a = P.decide('hit', S({targetStatus={poison=true}, kill=true}), 12)
          assert(a.casterAdd[20] == 0.75)                                                -- III Haste, longer at 4
          assert(P.decide('hit', S({targetId=5}), 12).targetAdd[15] == 0.5)               -- III-2 next target Slowed
        ''')

    def test_040_levels(self):
        self.run_lua('''
          local function has(list, id) for _, v in ipairs(list or {}) do if v == id then return true end end return false end
          local function op(a, name, id)
            for _, o in ipairs(a and a.others or {}) do if o.op == name and (id == nil or o.id == id) then return o end end
          end
          -- Hunter: in a row, kill heal, renewals, nearest foe Slowed.
          P.reset()
          assert(P.decide('hit', S({targetStatus={slow=true}}), 2).damage == 1.15)
          assert(P.decide('hit', S({targetStatus={slow=true}}), 2).damage == 1.15 + 0.05)
          P.reset()
          assert(P.decide('hit', S({targetStatus={poison=true}, kill=true}), 3).healHp == 10)
          P.reset()
          local h = P.decide('hit', S({targetStatus={poison=true, slow=true, oil=true}}), 5)
          assert(#h.targetRenew == 1 and h.targetRenew[1] == 8)                           -- shortest timer first
          h = P.decide('hit', S({targetStatus={poison=true, slow=true, oil=true, disable=true}}), 8)
          assert(#h.targetRenew == 3)                                                     -- only timed ailments
          P.reset()
          assert(P.decide('hit', S({targetStatus={oil=true}}), 5).targetRenew == nil)     -- Oil has no timer to renew
          P.reset()
          assert(op(P.decide('hit', S({targetStatus={poison=true}, kill=true}), 11), 'add', 15))
          -- Lancer II: only on a foe with an ailment; III-4 Slow.
          local L = S({job=1, targetStatus={protect=true, shell=true}})
          assert(not has((P.decide('hit', L, 5) or {}).targetRemove, 18))
          L = P.decide('hit', S({job=1, targetStatus={poison=true, protect=true, shell=true}}), 5)
          assert(has(L.targetRemove, 18) and not has(L.targetRemove, 19))
          L = P.decide('hit', S({job=1, targetStatus={poison=true, protect=true, shell=true, reflect=true, bubble=true}}), 8)
          assert(has(L.targetRemove, 18) and has(L.targetRemove, 19) and has(L.targetRemove, 23) and has(L.targetRemove, 28))
          assert(P.decide('hit', S({job=1, targetStatus={immobilize=true}}), 12).targetAdd[15] == 0.5)
          -- Warrior II-2.
          assert(P.decide('hit', S({job=7, casterHpPct=40}), 6).casterAdd[18] == 0.5)
          -- Samurai I-3 and III-3.
          P.reset()
          assert(P.decide('hit', S({job=10, targetId=1}), 3) == nil)
          assert(P.decide('hit', S({job=10, targetId=2}), 3).casterAdd[20])               -- survives one switch
          P.reset()
          P.decide('hit', S({job=10, targetId=1}), 2)
          assert(P.decide('hit', S({job=10, targetId=2}), 2) == nil)                      -- not before level 3
          P.reset()
          P.decide('hit', S({job=10, targetId=1}), 3); P.decide('hit', S({job=10, targetId=2}), 3)
          P.decide('hit', S({job=10, targetId=2}), 3)
          assert(P.decide('hit', S({job=10, targetId=3}), 3).casterAdd[20])               -- the grace renews with each chain
          P.reset()
          P.decide('hit', S({job=10, targetId=1}), 1); P.decide('hit', S({job=10, targetId=2}), 1)
          assert(P.decide('hit', S({job=10, targetId=3}), 1) == nil)                      -- level 1: a switch restarts it
          P.reset()
          P.decide('hit', S({job=10, kill=true, casterStatus={haste=true}}), 11)
          assert(P.decide('hit', S({job=10, targetId=11}), 11).damage == 1.20)
          -- Gunner: Slowed push, late foes, Silence, Faith.
          P.reset()
          local g = P.decide('hit', S({job=2, targetStatus={slow=true}}), 2)
          assert(g.chargeTo == 20000 - math.floor(30000 * 0.15))
          P.reset()
          g = P.decide('hit', S({job=2, charge={current=25000, required=30000, last=25000}}), 5)
          assert(g.chargeTo == 25000 - math.floor(30000 * 0.25))
          P.reset()
          g = P.decide('hit', S({job=2, charge={current=25000, required=30000, last=25000}}), 8)
          assert(g.targetAdd[9] == 0.5 and op(g, 'add', 22))
          P.reset()
          g = P.decide('hit', S({job=2, charge={current=2000, required=30000, last=2000}}), 8)
          assert(not g.targetAdd or not g.targetAdd[9])                                   -- not about to act
          -- Knight.
          local k = P.decide('guard', S({job=4, outcome='parry', attacker=777}), 4)
          local o = op(k, 'push')
          assert(o and o.unit == 777 and o.fraction == 0.10)
          assert(not op(P.decide('guard', S({job=4, outcome='dodge', attacker=777}), 4), 'push'))
          assert(op(P.decide('guard', S({job=4, outcome='parry', party={{address=100, job=4, hpPct=100, status={}}, {address=200, job=0, hpPct=40, status={immobilize=true}}}}), 8), 'remove', 14))
          assert(op(P.decide('ally', S({job=4}), 10), 'add', 17).scale == 0.75)
          assert(op(P.decide('ally', S({job=4, ally=300}), 12), 'add', 18).unit == 300)   -- the ally actually struck
          -- Mystic II-3, White Mage I-2.
          assert(op(P.decide('guard', S({job=5, outcome='counter', casterStatus={sap=true}}), 7), 'remove', 10))
          assert(P.decide('heal', S({job=0, overflow=50, item=true}), 1) == nil)          -- potions from level 2
          assert(P.decide('heal', S({job=0, overflow=50, item=true}), 2).targetAdd[25])
          -- Chemist II: heals the most hurt ally.
          local m = P.decide('hit', S({job=11}), 5)
          o = op(m, 'hp')
          assert(o and o.unit == 200 and o.amount == 10)
          assert(op(P.decide('hit', S({job=11}), 7), 'add', 18))
          assert(P.decide('hit', S({job=11, party={{address=100, job=11, hpPct=100, status={}}}}), 5) == nil)
          -- Black Mage III on the third cast only.
          P.reset()
          local function cast(rank) return P.decide('magick', S({job=9, element=1, mpCost=10}), rank) end
          cast(11); cast(11)
          local b = cast(11)
          assert(b.targetAdd[11] == 0.5 and op(b, 'add', 11) and op(b, 'hp').amount == -25)
          -- Legendary: a master of the owning job with the right family; nobody else.
          P.reset()
          local function leg(job, category, rank, weapon)
            return (P.decide('hit', S({job=job, category=category, weapon=weapon or 4130}), rank) or {}).damage or 1
          end
          assert(leg(10, 3, 12) >= 1.25)                                                  -- Samurai with a katana legendary
          assert(leg(10, 3, 11) == 1)                                                     -- not yet a master
          assert(leg(4, 3, 12) == 1)                                                      -- Knight, wrong family
          assert(leg(10, 3, 12, 4097) == 1)                                               -- not a legendary
          for job, want in pairs({[1]=P.JOB.knight, [5]=P.JOB.lancer, [13]=P.JOB.whiteMage, [14]=P.JOB.blackMage}) do
            assert(P.FAMILY_OWNER[job] == want)
          end
        ''')

    def test_warrior(self):
        self.run_lua('''
          assert(P.decide('hit', S({job=7, kill=true}), 1).casterAdd[21] == 1)
          assert(P.decide('hit', S({job=7, bystander=true, category=2}), 2).casterAdd[21] == 1)
          assert(#P.decide('hit', S({job=7, kill=true, casterStatus={bravery=true}}), 4).casterRemove == 3)
          assert(P.decide('hit', S({job=7, casterHpPct=40}), 5).casterAdd[21])            -- II below half
          assert(P.decide('hit', S({job=7, casterHpPct=60}), 5) == nil)
          assert(P.decide('hit', S({job=7, casterHpPct=60}), 7).casterAdd[21])            -- II-3 below 75%
          assert(others(P.decide('hit', S({job=7, casterHpPct=40}), 8), 'add') == 1)     -- II-4 ally Bravery
          assert(P.decide('hit', S({job=7, casterStatus={hpCritical=true}}), 9).targetAdd[10])  -- III Sap
          assert(P.decide('hit', S({job=7, targetStatus={sap=true}}), 10).damage == 1.15)
          assert(others(P.decide('hit', S({job=7, kill=true, targetStatus={sap=true}}), 11), 'add') == 1)
        ''')

    def test_lancer(self):
        self.run_lua('''
          local a = P.decide('hit', S({job=1, targetStatus={float=true, haste=true, faith=true, regen=true}}), 2)
          assert(#a.targetRemove == 3)                                                    -- Float, Haste, Faith
          assert(#P.decide('hit', S({job=1, targetStatus={haste=true}}), 1).targetRemove == 1)  -- rank 1 has a real target
          assert(#P.decide('hit', S({job=1, targetStatus={bravery=true}}), 4).targetRemove == 1)
          assert(P.decide('hit', S({job=1, targetStatus={protect=true}}), 9) == nil)       -- buff left: no pin
          assert(P.decide('hit', S({job=1}), 9).targetAdd[14] == 0.5)                     -- III pin
          assert(P.decide('hit', S({job=1, targetStatus={float=true}}), 9).targetAdd[14]) -- stripped last buff
          assert(P.decide('hit', S({job=1, targetStatus={immobilize=true}}), 11).damage == 1.15)
        ''')

    def test_samurai(self):
        self.run_lua('''
          P.reset()
          assert(P.decide('hit', S({job=10}), 1) == nil); assert(P.decide('hit', S({job=10}), 1) == nil)
          assert(P.decide('hit', S({job=10}), 1).casterAdd[20] == 0.5)
          assert(P.decide('aim', S({job=10, casterStatus={haste=true}}), 5).evade.weapon)
          assert(not P.decide('aim', S({job=10, casterStatus={haste=true}}), 5).evade.parry)
          assert(P.decide('aim', S({job=10, casterStatus={haste=true}}), 6).evade.parry)
          assert(P.decide('aim', S({job=10}), 12) == nil)
          P.reset()
          local a = P.decide('hit', S({job=10, kill=true, casterStatus={haste=true}}), 10)
          assert(a.casterAdd[20] and a.casterAdd[21])                                    -- III refresh + Bravery
          a = P.decide('hit', S({job=10, casterStatus={haste=true}, targetStatus={protect=true}}), 7)
          assert(math.abs(a.damage - 1/0.75) < 1e-9)                                      -- II-3 past Protect
        ''')

    def test_gunner_time_mage_green_mage(self):
        self.run_lua('''
          P.reset()
          assert(P.decide('hit', S({job=2}), 1).chargeTo == 17000)
          assert(others(P.decide('hit', S({job=2}), 3), 'push') == 1)                     -- I-3 second foe
          P.reset(); P.decide('hit', S({job=2}), 10)
          local a = P.decide('hit', S({job=2}), 10)                                       -- III chain from 2 hits
          assert(a.chargeTo == 14000 and a.targetAdd == nil)
          P.reset(); P.decide('hit', S({job=2}), 11)
          assert(P.decide('hit', S({job=2}), 11).targetAdd[15])                           -- III-3 Slow
          a = P.decide('hit', S({job=6, targetStatus={slow=true}}), 7)
          assert(a.targetAdd[3] == 0.5 and others(a, 'add') == 2 and others(a, 'push') == 2)  -- II Slow 2 foes, strip Haste
          assert(P.decide('hit', S({job=6, targetStatus={slow=true}, roll=function() return false end}), 4) == nil)
          a = P.decide('hit', S({job=4, targetStatus={stop=true}, party={{address=100, job=4, hpPct=100, mp=0, maxMp=0, status={}}, {address=300, job=6, hpPct=100, mp=0, maxMp=0, status={}}}}), 10)
          assert(a.casterAdd[20] and a.damage == 1.15)                                    -- Time Mage III aura
          a = P.decide('hit', S({job=3, damage=200}), 4); assert(a.drainMp == 16 and a.healHp == 16)
          a = P.decide('hit', S({job=3, damage=200, casterMp=100}), 8)                    -- II full: gives to allies
          assert(a.drainMp == nil and a.targetMpLoss == 16 and others(a, 'mp') == 2 and others(a, 'remove') == 1)
          assert(P.decide('hit', S({job=3, damage=200}), 9).chargeTo)                     -- III slows charge
        ''')

    def test_knight_mystic_white_black_chemist(self):
        self.run_lua('''
          local a = P.decide('guard', S({job=4, outcome='parry'}), 1); assert(others(a, 'add') == 1)
          assert(P.decide('guard', S({job=4, outcome='dodge'}), 1) == nil)
          assert(others(P.decide('guard', S({job=4, outcome='dodge'}), 2), 'add') == 1)
          assert(others(P.decide('guard', S({job=4, outcome='block'}), 6), 'add') == 3)    -- Lure + Protect + Shell
          assert(others(P.decide('ally', S({job=4}), 11), 'add') == 2)                    -- III Lure + Bravery
          assert(P.decide('guard', S({job=5, outcome='parry'}), 12) == nil)
          a = P.decide('guard', S({job=5, outcome='counter', attacker=777, casterStatus={blind=true, poison=true}}), 12)
          assert(others(a, 'hp') == 2 and others(a, 'push') == 1 and others(a, 'mp') == 1)
          assert(others(a, 'remove') == 3 and others(a, 'add') == 2)                     -- Blind + 2 ailments; Protect, Haste
          a = P.decide('heal', S({job=0, overflow=120}), 12)
          assert(a.targetAdd[25] and a.targetAdd[19] and a.targetAdd[18] and a.targetAdd[22] and others(a, 'add') == 3)
          assert(P.decide('heal', S({job=0, overflow=0}), 12) == nil)
          P.reset()
          assert(P.decide('magick', S({job=9, element=1, mpCost=20}), 12) == nil)
          a = P.decide('magick', S({job=9, element=1, mpCost=20}), 12); assert(a.casterCharge == 0.30)
          a = P.decide('magick', S({job=9, element=1, mpCost=20}), 12); assert(others(a, 'mp') == 1 and a.damage == 1.15)
          assert(P.decide('magick', S({job=9, element=1, mpCost=20}), 12).casterCharge == 0.30)
          P.decide('magick', S({job=9, element=1, mpCost=20}), 12)
          a = P.decide('magick', S({job=9, element=1, mpCost=20}), 12); assert(others(a, 'mp') == 1)  -- every third cast
          P.reset()                                                                       -- one spell, three targets, one cast
          local function aoe(now) return P.decide('magick', S({job=9, element=1, mpCost=20, now=now}), 12) end
          assert(aoe(1000000) == nil and aoe(1005000) == nil and aoe(1010000) == nil)
          assert(aoe(3000000).casterCharge == 0.30 and aoe(3005000) == nil)
          a = aoe(5000000); assert(others(a, 'mp') == 1 and a.damage == 1.15)
          a = aoe(5005000); assert(others(a, 'mp') == 0 and a.damage == 1.15 and a.targetAdd[11])
          P.reset()                                                                       -- I-2 one other spell keeps it
          local function el(e, r) return P.decide('magick', S({job=9, element=e, mpCost=20}), r) end
          el(1, 2); assert(el(2, 2) == nil); assert(el(1, 2).casterCharge == 0.20)
          P.reset(); el(1, 1); el(2, 1); assert(el(1, 1) == nil)                          -- level 1: broken
          P.reset(); el(1, 3); el(1, 3); el(2, 3); assert(el(4, 3).casterCharge == 0.20)  -- I-3 also as the chain ends
          -- Time Mage III-3: a bounded number of extensions per Stop.
          P.reset()
          local function stopped(t) return P.decide('hit', S({job=4, targetId=t or 9, targetStatus={stop=true},
            party={{address=100, job=6, hpPct=100, mp=0, maxMp=0, status={}}}}), 11) end
          for i = 1, 4 do assert(stopped().targetExtend[3] == 0.10) end
          assert(stopped().targetExtend == nil and stopped(8).targetExtend[3] == 0.10)
          P.decide('hit', S({job=4, targetId=9, party={{address=100, job=6, hpPct=100, mp=0, maxMp=0, status={}}}}), 11)
          assert(stopped().targetExtend[3] == 0.10)                                       -- a new Stop counts afresh
          -- A hit only the bonus makes lethal is a kill, and the chain state is not advanced twice.
          P.reset()
          a = P.decide('hit', S({targetStatus={poison=true}, damage=100, targetHp=110}), 3)
          assert(a.healHp == 10 and a.damage == 1.15)
          P.reset()
          assert(P.decide('hit', S({targetStatus={poison=true}, damage=100, targetHp=120}), 3).healHp == nil)
          -- No level is a copy of the one before it.
          for job = 0, 11 do
            for k = 1, 3 do
              for l = 2, 4 do assert(P.GEAR[job][k][l] ~= P.GEAR[job][k][l - 1], P.GEAR[job][k][l]) end
            end
          end
          a = P.decide('status', S({job=11, landed={8}}), 9); assert(others(a, 'add') == 2)  -- III: two foes
          assert(P.decide('status', S({job=11, landed={}}), 12) == nil)
        ''')

    def test_montblanc_speech(self):
        self.run_lua('''
          local b = P.speech(0, 1); assert(#b == 3 and b[1]:find("Apprentice's Writ", 1, true))
          b = P.speech(1, 2); assert(#b == 1 and b[1]:find('Proficiency I 2/4', 1, true))
          b = P.speech(4, 5); assert(b[1]:find("Journeyman's Writ", 1, true))
          b = P.speech(8, 9); assert(b[1]:find("Master's Writ", 1, true))
          b = P.speech(11, 12); assert(b[1]:find('final stamp', 1, true))
          b = P.speech(0, 6); assert(b[1]:find('filing your training papers', 1, true) and b[#b]:find('II 2/4', 1, true))
          assert(#P.speech(3, 3) == 0)
          for _, pair in ipairs({{0,1},{1,2},{4,5},{8,9},{11,12},{0,12}}) do
            for _, box in ipairs(P.speech(pair[1], pair[2])) do
              local n = 0
              for line in (box .. '\\n'):gmatch('(.-)\\n') do
                n = n + 1
                assert(#line:gsub('{color:%a+}', '') <= 48, line)
              end
              assert(n <= 3, box)
            end
          end
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ProficiencyApplyTest(unittest.TestCase):
    """What reaches the game: immunities respected, longer statuses kept, timers renewed, charge bonus deferred."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {f'scripts/IvaliceLegacy/{n}.lua': (ROOT/f'prototypes/{n}.lua').read_text(encoding='utf-8')
                   for n in ('proficiency', 'proficiency_runtime', 'clan_rank')}
        self.lua.globals().sources = self.lua.table_from(sources)
        self.lua.execute('''
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          memory = {u8={}, u32={}, u64={}, readArray=function() return {} end, execute=function() return 0 end}
          arg = {s32=0}
          NOW, ticks = 1000000, {}
          event = {registerEventSync=function() return true end, registerEventAsync=function() return true end,
                   registerLoadHandler=function() return true end, registerSaveHandler=function() return true end,
                   getClock=function() return NOW end, executeAfterMs=function(ms, fn) ticks[#ticks+1] = fn end}
          R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], 'runtime', 't', _ENV)()
          local function flags() return setmetatable({}, {__index=function() return 0 end}) end
          function record(t)
            t = t or {}
            return {flags1={isNegative=t.negative or 0, isNotApplicableToFoes=t.notFoes or 0}, absentStatusEffects=flags()}
          end
          bpack = {section15=setmetatable({[30]=record({notFoes=1}), [3]=record({negative=1}), [15]=record({negative=1}),
                                           [14]=record({negative=1})}, {__index=function() return record() end})}
          added = {}
          helpers = {getStatusEffectDuration=function() return 100 end, getStatusEffectTickDuration=function() return 0 end,
                     getStatusEffectsMatch=function() return false end,
                     addStatusEffect=function(c, u, id, d) added[#added+1] = {u=u, id=id, d=d} end,
                     getBattleUnitWork=function(a) return a end, modifyHp=function() end, modifyMp=function() end,
                     removeStatusEffect=function() end}
          work = {address=500, currentChargeTime=30000, requiredChargeTime=29000, lastChargeTime=0}
          keeps = {}
          function keep(t)
            local k = {type=1, address=t.address or 900, permanentStatusEffectImmunities=flags(), permanentStatusEffects=flags(),
                       temporaryStatusEffects=flags(), statusEffectDurations=flags(), temporaryAugments=flags(), permanentAugments=flags()}
            for _, i in ipairs(t.immune or {}) do k.permanentStatusEffectImmunities[i] = 1 end
            for i, d in pairs(t.has or {}) do k.temporaryStatusEffects[i] = 1; k.statusEffectDurations[i] = d end
            keeps[k.address] = k
            return k
          end
          classes = {getBattleUnitKeep=function(a) return keeps[a] end, getBattleUnitWork=function(a) return work end}
          function plus() return {statusEffects=flags(), addedStatusEffects=flags(), removedStatusEffects=flags(),
                                  statusEffectDurations=setmetatable({}, {__index=function() return -1 end})} end
          function apply(a, target) local f = {caster=plus(), target=plus()}
            R.apply(a, f, keep({address=100, type=0}), classes, helpers, target); return f end
        ''')

    def test_immune_foes_stay_immune(self):
        self.lua.execute('''
          local f = apply({targetAdd={[3]=0.5}}, keep({immune={3}}))
          assert(f.target.addedStatusEffects[3] == 0)
          f = apply({targetAdd={[3]=0.5}}, keep({}))
          assert(f.target.addedStatusEffects[3] == 1 and f.target.statusEffectDurations[3] == 50)
          f = apply({targetAdd={[30]=1}}, keep({}))                 -- Libra cannot sit on a foe
          assert(f.target.addedStatusEffects[30] == 0)
          keep({address=901, immune={15}}); keep({address=902})
          apply({others={{op='add', unit=901, id=15, scale=1}, {op='add', unit=902, id=15, scale=1}}}, keep({}))
          assert(#added == 1 and added[1].u == 902)                 -- the helper is guarded too
        ''')

    def test_a_shorter_proc_never_cuts_a_longer_status(self):
        self.lua.execute('''
          local f = apply({casterAdd={}, targetAdd={[20]=0.5}}, keep({has={[20]=80}}))
          assert(f.target.addedStatusEffects[20] == 0)              -- 80 left beats the 50 on offer
          f = apply({targetAdd={[20]=0.5}}, keep({has={[20]=30}}))
          assert(f.target.addedStatusEffects[20] == 1 and f.target.statusEffectDurations[20] == 50)
          keep({address=901, has={[18]=90}})
          apply({others={{op='add', unit=901, id=18, scale=0.5}}}, keep({}))
          assert(#added == 0)
        ''')

    def test_renew_and_extend(self):
        self.lua.execute('''
          local f = apply({targetRenew={15, 14}}, keep({has={[15]=10}}))
          assert(f.target.addedStatusEffects[15] == 1 and f.target.addedStatusEffects[14] == 0)
          assert(f.target.statusEffectDurations[15] == -1)          -- -1 = the full duration at the end of the hit
          f = apply({targetExtend={[3]=0.10}}, keep({has={[3]=40}}))
          assert(f.target.statusEffectDurations[3] == 50)
          f = apply({targetExtend={[3]=0.10}}, keep({has={[3]=95}}))
          assert(f.target.statusEffectDurations[3] == 100)          -- never past a full Stop
          f = apply({targetExtend={[3]=0.10}}, keep({has={[3]=100}}))
          assert(f.target.addedStatusEffects[3] == 0)
          f = apply({targetExtend={[3]=0.10}}, keep({}))            -- no Stop: nothing to extend
          assert(f.target.addedStatusEffects[3] == 0)
        ''')

    def test_charge_bonus_waits_for_the_next_charge(self):
        self.lua.execute('''
          local function run() local t = ticks; ticks = {}; for _, fn in ipairs(t) do fn() end end
          R.apply({casterCharge=0.20}, nil, keep({address=100, type=0}), classes, helpers)
          assert(work.currentChargeTime == 30000)                   -- the running spell's timer is left alone
          run(); assert(work.currentChargeTime == 30000)            -- still finishing
          work.currentChargeTime, work.requiredChargeTime = 0, 0
          run(); assert(work.currentChargeTime == 0)                -- between actions
          work.currentChargeTime, work.requiredChargeTime = 1000, 20000
          run(); assert(work.currentChargeTime == 5000)             -- 20% of the new charge, once
          assert(#ticks == 0)
          work.currentChargeTime = 30000; work.requiredChargeTime = 29000
          R.apply({casterCharge=0.20}, nil, keep({address=100, type=0}), classes, helpers)
          NOW = NOW + 11000000                                      -- no next action within 10 s: dropped
          run(); assert(#ticks == 0)
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ProficiencyAdapterTest(unittest.TestCase):
    def test_adapters_share_one_runtime_and_always_run_original(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {f'scripts/IvaliceLegacy/{n}.lua': (ROOT/f'prototypes/{n}.lua').read_text(encoding='utf-8')
                   for n in ('proficiency', 'proficiency_runtime', 'clan_rank')}
        lua.globals().sources = lua.table_from(sources)
        lua.execute('''
          printed, registered, calls = {}, 0, {}
          print = function(s) printed[#printed+1] = s end
          local real_load = load
          loadfile = function(p, mode, env)
            local n = p:match('functions/(%d+)%.lua$')
            if n then return function() return function() calls[n] = (calls[n] or 0) + 1 end end end
            return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil
          end
          memory = {u8={}, u32={}, u64={}, readArray=function() return {} end, execute=function() return 0 end}
          arg = {s32=0}
          event = {registerEventSync=function() registered = registered + 1 return true end,
                   registerEventAsync=function() return true end, registerLoadHandler=function() return true end,
                   registerSaveHandler=function() return true end}
        ''')
        for n in (165, 240, 110, 127, 170, 182, 22):
            fn = lua.execute('return load(...)()', (ROOT/f'prototypes/forge_proficiency_{n}.lua').read_text(encoding='utf-8'))
            lua.globals()[f'f{n}'] = fn
        lua.execute('''
          assert(registered == 2, registered)            -- one runtime, registered once
          local n = 0 for _, l in ipairs(printed) do if l:find('ready') then n = n + 1 end end
          assert(n == 1)
          f165({target={removedHp=1}}, {type=0}, {type=1}, {}, {}, {})
          f182({target={addedStatusEffects=setmetatable({}, {__index=function() return 0 end})}}, {type=0}, {type=1}, {}, {}, {})
          assert(calls['165'] == 1 and calls['182'] == 1)
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ProficiencyWritTest(unittest.TestCase):
    """The Writ key item comes first; Proficiency only acts on stages whose Writ is owned."""

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {f'scripts/IvaliceLegacy/{n}.lua': (ROOT/f'prototypes/{n}.lua').read_text(encoding='utf-8')
                   for n in ('proficiency', 'proficiency_runtime')}
        sources['scripts/IvaliceLegacy/clan_rank.lua'] = 'return {MARKS_FN=0, read=function() return {rank=RANK} end}'
        self.lua.globals().sources = self.lua.table_from(sources)
        self.lua.execute(r'''
          printed, boxes, handlers, keys, setFails, RANK = {}, {}, {}, {}, false, 0
          print = function(s) printed[#printed+1] = s end
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          local wide = function(s) return (s:gsub('.', '%0\0')) end
          local mem = {[0x120000]='MZ', [0x120180]='PE\0\0\x64\x86', [0xD113D4]='LEGCY_\0',
                       [0xD12200]=wide('%sLEGCY_%03d\0')}
          memory = {u8={}, u32=setmetatable({}, {__index=function() return 0 end}), u64={},
                    arg={s32='s32', u32='u32'},
                    readArray=function(a, n) return (mem[a] or ''):sub(1, n) end,
                    execute=function(fn, ret, t1, id, t2, v2, t3, v3)
                      if fn == 0x2FD970 then return keys[id] and 1 or 0 end
                      if fn == 0x2FD420 then
                        assert(t3 == 'u32' or t3 == 's32')
                        assert(v3 == 1, 'setter must use mode 1 (absolute)')
                        if setFails then return 0 end
                        keys[id] = v2 == 1; return v2
                      end
                      return 0
                    end}
          dialog = {amese={open=function(t) boxes[#boxes+1] = t.body return 1 end}}
          event = {registerEventSync=function(name, fn) handlers[name] = fn return true end,
                   registerEventAsync=function(name, fn) handlers[name] = fn return true end,
                   registerLoadHandler=function(_, fn) handlers.load = fn return true end,
                   registerSaveHandler=function(_, fn) handlers.save = fn return true end}
          R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime', 't', _ENV)()
          assert(R.register())
          function loadCampaign(rank) handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=rank}) end
          function leaveHall() handlers.onMapJump(302); handlers.onMapJump(100) end
          function said(text) for _, b in ipairs(boxes) do if b:find(text, 1, true) then return true end end end
          function logged(text) for _, l in ipairs(printed) do if l:find(text, 1, true) then return true end end end
        ''')

    def test_rules(self):
        self.lua.execute('''
          local P = R.Proficiency
          assert(P.effective(1, {}) == 0 and P.effective(1, {true}) == 1)
          assert(P.effective(6, {true}) == 4 and P.effective(6, {true, true}) == 6)
          assert(P.effective(12, {true, true}) == 8 and P.effective(12, {true, true, true}) == 12)
          assert(P.effective(12, {false, true, true}) == 0)      -- no Apprentice's Writ: nothing at all
          local n = P.writsNeeded(5); assert(#n == 2 and n[1] == 1 and n[2] == 2)
          assert(#P.writsNeeded(0) == 0)
          assert(P.speechGives(0, 1, 1) and not P.speechGives(1, 2, 1) and P.speechGives(4, 9, 3) and P.speechGives(4, 9, 2))
          for k = 1, 3 do
            for _, box in ipairs(P.writSpeech(k)) do
              local lines = 0
              for line in (box .. '\\n'):gmatch('(.-)\\n') do lines = lines + 1; assert(#line:gsub('{color:%a+}', '') <= 48, line) end
              assert(lines <= 3)
            end
          end
        ''')

    def test_rank_claimed_before_the_writ_existed(self):
        # Marco's campaign: rank 1 claimed by 0.2.0, no Writ in the inventory.
        self.lua.execute('''
          RANK = 1
          loadCampaign(1)
          assert(R.claimed() == 1 and not R.active() and R.effective() == 0)
          assert(logged('acting on rank 0'))
          leaveHall()
          assert(keys[0x81F0] and not keys[0x81F1])
          assert(R.active() and R.effective() == 1 and R.claimed() == 1)
          assert(#boxes == 1 and said("Apprentice's Writ") and said('almost forgot'))
          assert(logged('Writ 1 (0x81F0) granted'))
          boxes = {}; leaveHall(); assert(#boxes == 0)          -- nothing more to give
        ''')

    def test_new_ranks_give_their_writs_first(self):
        self.lua.execute('''
          RANK = 5
          loadCampaign(0)
          assert(not R.active())
          leaveHall()
          assert(keys[0x81F0] and keys[0x81F1] and not keys[0x81F2])
          assert(R.claimed() == 5 and R.effective() == 5)
          assert(said("Apprentice's Writ") and said("Journeyman's Writ") and not said('almost forgot'))
          assert(handlers.save().claimedRank == 5)
        ''')

    def test_refused_grant_claims_nothing(self):
        self.lua.execute('''
          RANK = 1; setFails = true
          loadCampaign(0)
          leaveHall()
          assert(R.claimed() == 0 and not R.active() and #boxes == 0)
          assert(logged('could not be granted; nothing claimed'))
          assert(handlers.save().claimedRank == 0)
        ''')

    def test_tip_flag_follows_the_apprentice_writ(self):
        self.lua.execute('''
          local state, bits, calls = 0x5000, setmetatable({}, {__index=function() return 0 end}), 0
          memory.u64 = {[0x020927C0] = state}
          memory.u8 = bits
          local readArray, execute = memory.readArray, memory.execute
          memory.readArray = function(a, n) if a == state + 0x334C then return string.rep('\\0', n) end return readArray(a, n) end
          memory.execute = function(fn, ret, t1, a1, t2, a2, ...)
            if fn == 0x315A20 then
              calls = calls + 1
              local at = state + 0x334C + (a1 >> 3)
              bits[at] = a2 == 1 and (bits[at] | (1 << (a1 & 7))) or (bits[at] & ~(1 << (a1 & 7)))
              return nil
            end
            return execute(fn, ret, t1, a1, t2, a2, ...)
          end
          local function shown() return (bits[state + 0x334C + 31] >> 6) & 1 == 1 end
          bits[state + 0x334C + 31] = 0x80                -- bit 255, left by 0.3.1
          RANK = 1
          loadCampaign(1)
          handlers.onMapJump(50)
          assert(not shown() and calls == 1)                      -- no Writ yet: only the stray bit is cleared
          assert(bits[state + 0x334C + 31] == 0 and logged('cleared stray flag 255'))
          assert(logged('menu flags before Proficiency: '))
          leaveHall()
          assert(keys[0x81F0] and shown() and calls == 2 and logged('Proficiency tip shown (flag 254)'))
          handlers.onMapJump(60); assert(calls == 2)              -- already set: no second write
          keys[0x81F0] = nil; handlers.onMapJump(70)
          assert(not shown() and calls == 3)                      -- Writ gone: tip hidden again
        ''')

    def test_gear_text(self):
        self.lua.execute('''
          local P = R.Proficiency
          local jobs = 0
          for job = 0, 11 do
            local g = P.GEAR[job]; assert(g and g.line, job); jobs = jobs + 1
            for k = 1, 3 do
              assert(#g[k] == 4, job .. ':' .. k)
              for l = 1, 4 do assert(#g[k][l] <= P.GEAR_LIMIT, g[k][l]); assert(not g[k][l]:find('[;<>{}]'), g[k][l]) end
            end
          end
          assert(jobs == 12)
          local hunter = P.gearLines(P.JOB.hunter, 2)
          assert(hunter[1] == 'Pursuit  I 2/4  II 0/4  III 0/4' and hunter[2]:find('15%%') and hunter[3] == nil and hunter[4] == nil)
          local six = P.gearLines(P.JOB.hunter, 7)                       -- I 4/4, II 3/4
          assert(six[2]:find('25%%') and six[3]:find('Libra') and six[4] == nil)
          assert(P.gearLines(P.JOB.lancer, 6)[3]:find('strip'))            -- every stage now has a line
          for job = 0, 11 do
            for rank = 1, 12 do
              local lines = P.gearLines(job, rank)
              for k = 1, 3 do
                if P.levels(rank)[k] >= 1 then assert(lines[k + 1], job .. ' stage ' .. k) end
              end
            end
          end
          assert(P.gearLines(P.JOB.knight, 0)[1]:find('no Writ yet'))
        ''')

    def test_gear_hook_keeps_one_suffix_per_member(self):
        self.lua.execute(r'''
          local tick
          event.executeAfterMs = function(ms, fn) tick = fn end
          local mem, nextAlloc, assembled, symbols = {}, 0x100000, {}, {}
          local site = {0x33, 0xC9, 0x48, 0x89, 0x5F, 0x18}
          memory.alloc = function(n) local a = nextAlloc; nextAlloc = nextAlloc + n + 0x10; return a end
          memory.writeArray = function(a, arr) for i, b in ipairs(arr) do mem[a + i - 1] = b end end
          memory.u8 = setmetatable({}, {__index = function(_, a) return mem[a] or 0 end,
                                        __newindex = function(_, a, v) mem[a] = v end})
          memory.u64 = setmetatable({}, {__index = function(_, a) return mem['q' .. a] or 0 end,
                                         __newindex = function(_, a, v) mem['q' .. a] = v end})
          local baseRead = memory.readArray
          memory.readArray = function(a, n) if a == 0x29266C then return site end return baseRead(a, n) end
          memory.assemble = function(code, where)
            assembled[#assembled + 1] = where
            if type(where) == 'table' then for i, name in ipairs(where) do symbols[name] = 0x900000 + i * 8 end end
          end
          memory.getSymbol = function(name) return symbols[name] end
          message = {convert = function(s) return 'N:' .. s end}
          local cats = {[0] = 12, 21, 20}
          bpack = {section13 = setmetatable({}, {__index = function(_, i) return cats[i] and {category = cats[i]} or nil end})}
          local jobs = {[0] = 8, [1] = 4}                                    -- member 0 Hunter, member 1 Knight
          local baseExec = memory.execute
          memory.execute = function(fn, ret, t1, a1, ...)
            if fn == 0x320A40 then return jobs[a1] and (0x500000 + a1 * 0x1000) or 0 end
            return baseExec(fn, ret, t1, a1, ...)
          end
          local baseU8 = getmetatable(memory.u8).__index
          getmetatable(memory.u8).__index = function(t, a)
            for m, j in pairs(jobs) do if a == 0x500000 + m * 0x1000 + 0x1C3 then return j end end
            return baseU8(t, a)
          end
          R3 = load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime3', 't', _ENV)()
          assert(R3.register() and tick)
          handlers.load('C:/saves/LEGCY_000', {schema = 1, campaign = 'LEGCY_000', claimedRank = 1})
          keys[0x81F0] = true
          tick()
          assert(#assembled == 2 and type(assembled[1]) == 'table' and assembled[2] == 0x29266C)  -- block, then site
          assert(logged('gear text hook installed (2 items)'))                                   -- dagger, armour
          local bits = memory.u64[symbols.legacy_prof_bits]
          assert(memory.u8[bits] == 0x05)                                                        -- records 0 and 2
          local tbl = memory.u64[symbols.legacy_prof_table]
          local function suffix(m)
            local at, out = memory.u64[tbl + m * 8], {}
            if at == 0 then return nil end
            local i = 0
            while memory.u8[at + i] ~= 0 do out[#out + 1] = string.char(memory.u8[at + i]); i = i + 1 end
            return table.concat(out)
          end
          assert(suffix(0):find('^N:\nProficiency:') == nil and suffix(0):find('Pursuit I 1/4:', 1, true))
          assert(suffix(1):find('Bulwark I 1/4:', 1, true) and suffix(2) == nil)
          local first = memory.u64[tbl]
          tick(); assert(memory.u64[tbl] == first)                            -- nothing changed: same buffer
          jobs[0] = 11; tick()
          assert(memory.u64[tbl] ~= first and suffix(0):find('Contagion', 1, true))   -- other buffer, new text
          -- One added line only: the Equipment help box holds four lines and weapons already use three.
          local one = R3.gearSuffix(8, 1)
          assert(one == '\n{scale:82}{rgb:90,90,90}Pursuit I 1/4:{lowspace}{rgb:255,255,255}+15% damage to foes with an ailment', one)
          assert(R3.gearSuffix(8, 0):find('{rgb:90,90,90}Proficiency:{lowspace}{rgb:255,255,255}Pursuit  (no Writ yet', 1, true))
          assert(R3.gearSuffix(8, 7):find('Pursuit II 3/4:', 1, true))             -- highest stage begun
          for job = 0, 11 do for rank = 0, 12 do
            local s = R3.gearSuffix(job, rank)
            local _, breaks = s:gsub('\n', '')
            local plain = s:gsub('{[^}]*}', '')
            assert(breaks == 1 and #plain <= 96, job .. ':' .. rank .. ' ' .. plain)
          end end
          site = {0xE9, 0, 0, 0, 0, 0x90}                                      -- someone else patched the site
          R4 = load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime4', 't', _ENV)()
          assembled = {}
          assert(R4.register()); handlers.load('C:/saves/LEGCY_000', {schema = 1, campaign = 'LEGCY_000', claimedRank = 1})
          tick(); assert(#assembled == 0 and logged('unexpected code at 0x29266C'))
        ''')

    def test_equipment_box_width_fix(self):
        self.lua.execute('''
          local site, assembled = {0x8B, 0xC8}, {}
          local baseRead = memory.readArray
          memory.readArray = function(a, n) if a == 0x3FF6CD then return site end return baseRead(a, n) end
          memory.assemble = function(code, where) assembled[#assembled + 1] = {code, where} end
          assert(R.installWidthFix() and #assembled == 1)
          assert(assembled[1][1] == 'xor ecx,ecx' and assembled[1][2] == 0x3FF6CD)
          assert(logged('Equipment box width fix applied'))
          site = {0x31, 0xC9}                                   -- already changed (or another mod): leave it
          assert(not R.installWidthFix() and #assembled == 1 and logged('not applied: unexpected code'))
        ''')

    def test_session_logs_keep_five_previous_sessions(self):
        import os, tempfile
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                def put(name, text):
                    with open(name, 'w') as f:
                        f.write(text)
                put('hook.log', 'this session')
                put('hook-session.log', 'session 0')
                for n in range(1, 6):
                    put(f'hook-previous-{n}.log', f'session -{n}')
                self.lua.execute('R.rotateLogs(); R.copyLog()')
                read = lambda name: open(name).read() if os.path.exists(name) else ''
                self.assertEqual([read(f'hook-previous-{n}.log') for n in range(1, 6)],
                                 ['session 0', 'session -1', 'session -2', 'session -3', 'session -4'])
                self.assertEqual(read('hook-session.log'), 'this session')
            finally:
                os.chdir(here)

    def test_side_file_saved_under_another_slot_name_is_used(self):
        # Marco: LEGCY_000 loaded, then saved into slot 3: LEGCY_003.dir carries campaign "LEGCY_000".
        self.lua.execute('''
          RANK = 1; keys[0x81F0] = true
          handlers.load('C:/saves/LEGCY_003', {schema=1, campaign='LEGCY_000', claimedRank=1})
          assert(R.claimed() == 1 and R.effective() == 1)
          assert(logged('side file of LEGCY_003 was written as LEGCY_000; using its rank 1'))
          assert(handlers.save().campaign == 'LEGCY_003' and handlers.save().claimedRank == 1)
          leaveHall(); assert(#boxes == 0)                       -- nothing to hand over again
        ''')

    def test_writs_carried_restore_a_lost_claimed_rank(self):
        # 0.3.10 and earlier: the rejected side file made the next save write rank 0 beside a save holding Writ I.
        self.lua.execute('''
          RANK = 1; keys[0x81F0] = true
          handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_003', claimedRank=0})
          assert(R.effective() == 1 and R.claimed() == 1)
          assert(logged('claimed rank 0 restored to 1 from the Writs carried'))
          assert(handlers.save().claimedRank == 1)
          leaveHall(); assert(#boxes == 0)                       -- no second Writ, no repeated speech
          keys[0x81F1] = true                                     -- a Journeyman's Writ proves rank 5
          handlers.load('C:/saves/LEGCY_001', {schema=1, campaign='LEGCY_001', claimedRank=2})
          assert(R.effective() == 5 and R.claimed() == 5)
        ''')

    def test_palace_guard_holds_companions_at_leader_only(self):
        self.lua.execute('''
          handlers.onMapJump(289)                                   -- no Companions mod: idle, logged once
          handlers.onMapJump(567)
          assert(logged('palace guard idle'))
          local count = 0
          for _, l in ipairs(printed) do if l:find('palace guard idle', 1, true) then count = count + 1 end end
          assert(count == 1)
          local PIT = 0x7000
          memory.getSymbol = function(name) if name == 'pit_ot' then return PIT end end
          memory.u8[PIT] = 1                                        -- Full Party, as configured
          handlers.onMapJump(291)                                   -- East End: untouched
          assert(memory.u8[PIT] == 1)
          handlers.onMapJump(568)                                   -- palace Cellars: held at Leader Only
          assert(memory.u8[PIT] == 0 and logged('held at Leader Only (location 568, was 1)'))
          handlers.onMapJump(569); assert(memory.u8[PIT] == 0)      -- still inside
          handlers.onMapJump(573); assert(memory.u8[PIT] == 1)      -- left: configured value back
          assert(logged('back to 1'))
          memory.u8[PIT] = 0                                        -- Leader Only configured: stays so
          handlers.onMapJump(570); handlers.onMapJump(291)
          assert(memory.u8[PIT] == 0)
        ''')

    def test_palace_guard_finds_the_byte_through_the_patch(self):
        # In game another script's symbols are not visible: the byte is read from "jmp pit3_code" at 0x2838CC and
        # pit3_code's first instruction, cmp byte ptr [rip+rel32],0.
        self.lua.execute('''
          local bytes = {}
          memory.u8 = setmetatable({}, {__index = function(_, a) return bytes[a] or 0 end,
                                        __newindex = function(_, a, v) bytes[a] = v end})
          local function put(a, list) for i, b in ipairs(list) do bytes[a + i - 1] = b end end
          local CODE, PIT = 0x40000000, 0x40000400
          local rel = CODE - (0x2838CC + 5)
          put(0x2838CC, {0xE9, rel & 0xFF, (rel >> 8) & 0xFF, (rel >> 16) & 0xFF, (rel >> 24) & 0xFF})
          local disp = PIT - (CODE + 7)
          put(CODE, {0x80, 0x3D, disp & 0xFF, (disp >> 8) & 0xFF, (disp >> 16) & 0xFF, (disp >> 24) & 0xFF, 0x00})
          bytes[PIT] = 1
          handlers.onMapJump(568)
          assert(bytes[PIT] == 0 and logged('held at Leader Only (location 568, was 1)'))
          handlers.onMapJump(291); assert(bytes[PIT] == 1)
          bytes[CODE] = 0x90                                    -- not the expected patch: idle, nothing written
          bytes[PIT] = 1; handlers.onMapJump(569)
          assert(bytes[PIT] == 1 and logged('palace guard idle'))
        ''')

    def test_lost_writ_caps_the_stage(self):
        self.lua.execute('''
          RANK = 6; keys[0x81F0] = true; keys[0x81F1] = true
          loadCampaign(6)
          assert(R.effective() == 6)
          keys[0x81F1] = nil
          handlers.onMapJump(50)
          assert(R.effective() == 4)                            -- stage II waits for its Writ
          leaveHall()
          assert(keys[0x81F1] and R.effective() == 6 and said("Journeyman's Writ") and said('almost forgot'))
        ''')


if __name__ == '__main__':
    unittest.main()
