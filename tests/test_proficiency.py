"""No-game contracts for Proficiency 0.1.0 (rules module and the Forge 165 adapter)."""
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
          function hit(t)
            local s = {job=8, actorId=1, targetId=9, foe=true, kill=false, bystander=false, category=1, damage=100,
                       targetStatus={}, casterStatus={}, targetMp=50,
                       charge={current=20000, required=30000, last=20000}, roll=function() return true end}
            for k,v in pairs(t or {}) do s[k]=v end
            return s
          end
        ''')

    def test_levels_follow_ranks(self):
        self.lua.execute('''
          local function eq(r, a, b, c) local l = P.levels(r); assert(l[1]==a and l[2]==b and l[3]==c, r) end
          eq(0,0,0,0); eq(1,1,0,0); eq(4,4,0,0); eq(5,4,1,0); eq(8,4,4,0); eq(9,4,4,1); eq(12,4,4,4)
          eq(13,0,0,0); eq(-1,0,0,0); eq(2.5,0,0,0)
        ''')

    def test_no_rank_no_effect_and_unknown_jobs_ignored(self):
        self.lua.execute('''
          assert(P.decide(hit({targetStatus={slow=true}}), 0) == nil)
          assert(P.decide(hit({job=4}), 12) == nil)      -- Knight: not built in 0.1.0
          assert(P.decide(hit({job=99}), 12) == nil)
        ''')

    def test_pursuit_any_weapon(self):
        self.lua.execute('''
          assert(P.decide(hit(), 4) == nil)
          assert(P.decide(hit({targetStatus={slow=true}, category=9}), 1).damage == 1.15)
          assert(P.decide(hit({targetStatus={poison=true}}), 4).damage == 1.25)
          assert(P.decide(hit({targetStatus={slow=true}, foe=false}), 4) == nil)
        ''')

    def test_cleave_grounding_detonate(self):
        self.lua.execute('''
          local a = P.decide(hit({job=7, kill=true}), 1); assert(a.casterAdd[21] == 1 and not a.casterRemove)
          assert(P.decide(hit({job=7, bystander=true, category=2}), 1) == nil)
          assert(P.decide(hit({job=7, bystander=true, category=2}), 2).casterAdd[21] == 1)
          assert(P.decide(hit({job=7, bystander=true, category=1}), 4) == nil)
          assert(P.decide(hit({job=7, kill=true}), 3).casterAdd[21] == 1.5)
          a = P.decide(hit({job=7, kill=true, casterStatus={bravery=true}}), 4)
          assert(#a.casterRemove == 3)
          a = P.decide(hit({job=1, targetStatus={float=true, haste=true, faith=true, regen=true}}), 2)
          assert(#a.targetRemove == 2 and a.targetRemove[1] == 26 and a.targetRemove[2] == 20)
          assert(#P.decide(hit({job=1, targetStatus={float=true, haste=true, faith=true, regen=true}}), 4).targetRemove == 4)
          assert(P.decide(hit({job=1}), 4) == nil)
          assert(P.decide(hit({job=6, targetStatus={slow=true}}), 1).targetAdd[3] == 0.5)
          assert(P.decide(hit({job=6, targetStatus={immobilize=true}}), 1) == nil)
          assert(P.decide(hit({job=6, targetStatus={immobilize=true}}), 2))
          assert(P.decide(hit({job=6, targetStatus={slow=true, stop=true}}), 4) == nil)
          assert(P.decide(hit({job=6, targetStatus={slow=true}, roll=function() return false end}), 4) == nil)
        ''')

    def test_zanshin_chain(self):
        self.lua.execute('''
          P.reset()
          assert(P.decide(hit({job=10}), 1) == nil); assert(P.decide(hit({job=10}), 1) == nil)
          assert(P.decide(hit({job=10}), 1).casterAdd[20] == 1)
          assert(P.decide(hit({job=10}), 2) == nil); assert(P.decide(hit({job=10}), 2).casterAdd[20] == 1)
          assert(P.decide(hit({job=10, targetId=3}), 4) == nil); assert(P.decide(hit({job=10, targetId=3}), 4).casterAdd[20] == 1.5)
          assert(P.decide(hit({job=10, actorId=2, targetId=3}), 4) == nil)  -- chains are per character
        ''')

    def test_suppression_and_mana_leech(self):
        self.lua.execute('''
          assert(P.decide(hit({job=2}), 1).chargeTo == 17000)
          assert(P.decide(hit({job=2}), 4).chargeTo == 15500)
          assert(P.decide(hit({job=2, charge={current=30000, required=30000, last=30000}}), 4) == nil)
          assert(P.decide(hit({job=2, charge={current=20000, required=30000, last=0}}), 4) == nil)
          assert(P.decide(hit({job=2, charge=false}), 4) == nil)
          local a = P.decide(hit({job=3, damage=200}), 1); assert(a.drainMp == 10 and not a.targetAdd and not a.healHp)
          a = P.decide(hit({job=3, damage=200, targetMp=4}), 2); assert(a.drainMp == 4 and a.targetAdd[9] == 1)
          a = P.decide(hit({job=3, damage=200}), 4); assert(a.drainMp == 16 and a.healHp == 16)
          assert(P.decide(hit({job=3, targetMp=0}), 4) == nil)
        ''')

    def test_montblanc_speech(self):
        self.lua.execute('''
          local b = P.speech(0, 1); assert(#b == 3 and b[1]:find("Apprentice's Writ", 1, true))
          b = P.speech(1, 2); assert(#b == 1 and b[1]:find('Proficiency I 2/4', 1, true))
          b = P.speech(4, 5); assert(#b == 2 and b[1]:find("Journeyman's Writ", 1, true))
          b = P.speech(8, 9); assert(#b == 2 and b[1]:find("Master's Writ", 1, true))
          b = P.speech(11, 12); assert(#b == 1 and b[1]:find('final stamp', 1, true))
          b = P.speech(0, 6); assert(b[1]:find('filing your training papers', 1, true) and b[#b]:find('II 2/4', 1, true))
          assert(#P.speech(3, 3) == 0 and #P.speech(5, 2) == 0)
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
    def load(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {'scripts/IvaliceLegacy/proficiency.lua': (ROOT/'prototypes/proficiency.lua').read_text(encoding='utf-8'),
                   'scripts/IvaliceLegacy/clan_rank.lua': (ROOT/'prototypes/clan_rank.lua').read_text(encoding='utf-8')}
        lua.globals().sources = lua.table_from(sources)
        lua.execute('''
          printed, handlers, events, opened = {}, {}, {}, {}
          print = function(s) printed[#printed+1] = s end
          originalCalls = 0
          local real_load = load
          loadfile = function(p)
            if p == 'scripts/TheInsurgentsForge/functions/165.lua' then
              return function() return function() originalCalls = originalCalls + 1 end end
            end
            return sources[p] and real_load(sources[p], '@'..p) or nil
          end
          local ram = {}
          local function view() return setmetatable({}, {__index=function(_,a) return ram[a] or 0 end,
            __newindex=function(_,a,v) ram[a]=v end}) end
          memory = {u8=view(), u32=view(), u64=view()}
          memory.readArray = function() return {} end
          memory.execute = function() return 0 end
          arg = {s32 = 0}
          event = {registerEventSync=function(n, f) events[n]=f return true end,
                   registerEventAsync=function(n, f) events[n]=f return true end,
                   registerLoadHandler=function(n, f) handlers.load=f return true end,
                   registerSaveHandler=function(n, f) handlers.save=f return true end}
          dialog = {amese={open=function(t) opened[#opened+1]=t return {} end}}
          bpack = {section13=setmetatable({count=600}, {__index=function() return {category=1} end})}
        ''')
        f165 = lua.execute('return load(...)()', (ROOT/'prototypes/forge_proficiency_165.lua').read_text(encoding='utf-8'))
        lua.globals().f165 = f165
        return lua

    def test_inactive_without_campaign_and_original_always_runs(self):
        lua = self.load()
        lua.execute('''
          assert(printed[1]:find('^Legacy Proficiency: 0.1.0 ready'))
          handlers.load('C:/saves/FFXII_001', nil)
          assert(handlers.save('x') == nil)
          f165({target={removedHp=10}}, {type=0}, {type=1}, {}, {}, {})
          assert(originalCalls == 1)
        ''')

    def test_legacy_gate_refuses_without_executable_pins(self):
        lua = self.load()
        lua.execute('''
          handlers.load('C:/saves/LEGCY_001', {schema=1, campaign='LEGCY_001', claimedRank=5})
          assert(handlers.save('x') == nil)  -- legacyMode() pins fail on fake memory
        ''')


if __name__ == '__main__':
    unittest.main()
