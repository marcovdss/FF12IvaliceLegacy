"""0.1.35 Proficiency side of the base-jobs release: Zanshin I grants Haste only when the Samurai is not Hasted (S-1),
Zanshin II-1 is the dodge and II-2 adds parry and block (S-2), Detonate I-3 counts Confused foes (T-1 A), and Suppression
can never take more than half of a foe's charge between two of its actions (G-1, held by the per-foe push budget).

The budget checks run against the repo runtime and, when IVALICE_LEGACY_COMPOSED names it, the deployable runtime."""
from pathlib import Path
import os
import unittest

try:
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
COMPOSED = Path(os.environ['IVALICE_LEGACY_COMPOSED']) if os.environ.get('IVALICE_LEGACY_COMPOSED') else None


def source(name):
    return (ROOT / 'prototypes' / f'{name}.lua').read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class RulesTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().P = self.lua.execute(source('proficiency'))
        self.lua.execute('''
          P.reset()
          function S(t)
            local s = {job=10, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, category=1, damage=100,
                       targetStatus={}, casterStatus={}, targetMp=50, casterHpPct=100, casterMp=10, casterMaxMp=100,
                       selfMaxHp=1000, party={{address=100, job=10, hpPct=100, mp=10, maxMp=100, status={}}},
                       foes={{address=901, status={}}}, charge={current=20000, required=30000, last=20000},
                       roll=function() return true end, canLand=function() return true end}
            for k, v in pairs(t or {}) do s[k] = v end
            return s
          end
        ''')

    def test_zanshin_i_does_not_refresh_haste(self):
        self.lua.execute('''
          local H = {haste=true}
          for rank = 1, 4 do
            P.reset()
            assert(P.decide('hit', S({comboFollow=true, casterStatus=H}), rank) == nil, 'combo while Hasted, rank ' .. rank)
            P.reset()
            assert(P.decide('hit', S({critical=true, casterStatus=H}), math.max(rank, 2)) == nil, 'crit while Hasted')
            P.reset()
            assert(P.decide('hit', S({comboFollow=true}), rank).casterAdd[20], 'combo not Hasted, rank ' .. rank)
          end
          -- the I-4 +15% belongs to the hit that grants Haste
          P.reset(); assert(P.decide('hit', S({comboFollow=true, casterStatus=H}), 4) == nil)
          P.reset(); assert(P.decide('hit', S({comboFollow=true}), 4).damage == 1.15)
          -- Haste ran out: the next Attack's combo grants it again
          P.reset(); assert(P.decide('hit', S({comboFollow=true, casterStatus=H}), 1) == nil)
          P.decide('hit', S({}), 1)
          assert(P.decide('hit', S({comboFollow=true}), 1).casterAdd[20] == 0.5)
          -- Zanshin III still renews on a kill while Hasted
          P.reset(); assert(P.decide('hit', S({kill=true, casterStatus=H}), 9).casterAdd[20])
        ''')

    def test_zanshin_ii_dodge_first_then_parry_and_block(self):
        self.lua.execute('''
          local H = {haste=true}
          local function evade(rank) return P.decide('aim', S({casterStatus=H}), rank).evade end
          local e = evade(5); assert(e.parry and not e.weapon and not e.shield)    -- II-1: dodge only
          e = evade(6); assert(e.parry and e.weapon and e.shield)                  -- II-2: + parry and block
          assert(evade(7).parry and evade(7).weapon and evade(8).shield)
          assert(P.decide('aim', S({}), 6) == nil)                                 -- not Hasted: nothing
          local g = P.GEAR[P.JOB.samurai][2]
          assert(g[1]:find('dodged') and not g[1]:find('parried'), g[1])
          assert(g[2]:find('dodged') and g[2]:find('parried') and g[2]:find('blocked'), g[2])
        ''')

    def test_detonate_counts_confused_foes_from_the_third_level(self):
        self.lua.execute('''
          local function stops(rank, status) P.reset(); local a = P.decide('hit', S({job=6, targetStatus=status}), rank)
            return a ~= nil and a.targetAdd ~= nil and a.targetAdd[3] ~= nil end
          assert(not stops(1, {confuse=true}) and not stops(2, {confuse=true}))
          assert(stops(3, {confuse=true}) and stops(4, {confuse=true}))
          assert(stops(3, {slow=true}) and stops(3, {immobilize=true}) and not stops(3, {}))
          local g = P.GEAR[P.JOB.timeMage][1]
          assert(not g[2]:find('Confused') and g[3]:find('Confused') and g[4]:find('Confused'))
          assert(g[3] ~= g[2], 'I-3 is its own step')
          assert(P.STATUS.confuse == 5)
        ''')

    def test_max_suppression_stack_wants_more_than_half_the_charge(self):
        # The study's lock: the pure rule asks for 70% of the charge per shot. The runtime budget is what bounds it.
        self.lua.execute('''
          local s = S({job=2, weaponCt=50, targetStatus={slow=true},
                       charge={current=29000, required=30000, last=29000}})
          P.decide('hit', s, 12)                                              -- III needs two hits in a row
          local wanted = 29000 - P.decide('hit', s, 12).chargeTo
          assert(wanted >= 0.5 * 30000, wanted)
        ''')


def runtimes():
    yield 'repo', source('proficiency_runtime')
    if COMPOSED and COMPOSED.exists():
        yield 'composed', COMPOSED.read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class SuppressionCapTest(unittest.TestCase):
    def build(self, runtime_source):
        lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {f'scripts/IvaliceLegacy/{n}.lua': source(n) for n in ('proficiency', 'clan_rank', 'hybrid_proficiency', 'mp_economy')}
        sources['scripts/IvaliceLegacy/proficiency_runtime.lua'] = runtime_source
        sources['scripts/DynamicFoes/Foes.lua'] = 'return {[16557] = "rare", [16402] = "boss", [16447] = "common"}'
        lua.globals().sources = lua.table_from(sources)
        lua.execute('''
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          memory = {u8={}, u32={}, u64={}, readArray=function() return {} end, execute=function() return 0 end}
          arg = {s32=0}
          event = {registerEventSync=function() return true end, registerEventAsync=function() return true end,
                   registerLoadHandler=function() return true end, registerSaveHandler=function() return true end,
                   getClock=function() return 1 end, executeAfterMs=function() end}
          R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], 'runtime', 't', _ENV)()
          function foe(name) return {currentChargeTime=29000, requiredChargeTime=30000, ardUnit={name=name}} end
        ''')
        return lua

    def test_budget_stays_at_or_under_half_per_foe_action(self):
        for name, text in runtimes():
            with self.subTest(runtime=name):
                lua = self.build(text)
                r = lua.execute('''
                  assert(R.PUSH_SHARE <= 0.5 and R.PUSH_SHARE_TOUGH <= R.PUSH_SHARE)
                  local out = {}
                  for i, kind in ipairs({16447, 16557, 16402}) do
                    local w = foe(kind)
                    local start = w.currentChargeTime
                    for shot = 1, 6 do                                   -- six max-stack shots, 70% each
                      w.currentChargeTime = w.currentChargeTime - R.pushAllowance(800 + i, w, 21000)
                    end
                    out[i] = start - w.currentChargeTime
                    R.foeActed(800 + i)
                    out[i + 3] = R.pushAllowance(800 + i, w, 21000)     -- a new foe action: a fresh budget
                  end
                  return out[1], out[2], out[3], out[4], out[5], out[6]
                ''')
                self.assertEqual(tuple(r[:3]), (7500, 3750, 3750))
                for pushed in r:
                    self.assertLessEqual(pushed, 15000)          # never more than 50% of 30000
                self.assertGreater(r[3], 0)


if __name__ == '__main__':
    unittest.main()
