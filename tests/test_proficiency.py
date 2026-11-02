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
          assert(P.decide('hit', S({targetStatus={poison=true}}), 4).damage == 1.25)
          assert(P.decide('hit', S({targetStatus={poison=true}}), 7).targetAdd[30])       -- II-3 Libra
          local a = P.decide('hit', S({targetStatus={poison=true}, kill=true}), 12)
          assert(a.casterAdd[20] == 0.75)                                                -- III Haste, longer at 4
          assert(P.decide('hit', S({targetId=5}), 12).targetAdd[15] == 0.5)               -- III-2 next target Slowed
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
          assert(#a.targetRemove == 2)
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
          assert(a.targetAdd[3] == 0.5 and others(a, 'add') == 2 and others(a, 'remove') == 1)  -- II Slow 2 foes, strip Haste
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
        for n in (165, 240, 110, 170, 182, 22):
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
