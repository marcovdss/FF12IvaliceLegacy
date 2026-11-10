"""Recovery rules (Marco 2026-11-10, audit points A-E): per-hit MP cap, shared Green Mage drain, no MP from items
outside item stages, no returns on combo follow-ups, and the per-foe charge push-back budget."""
from pathlib import Path
import unittest

try:
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / 'prototypes' / f'{name}.lua').read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class RecoveryDecideTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.P = self.lua.execute(source('proficiency'))
        g.H = self.lua.execute(source('hybrid_proficiency'))
        self.lua.execute(r'''
          H.attach(P)
          function S(job, t)
            local s = {job=job, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, damage=1000,
                       targetStatus={}, casterStatus={}, targetMp=5000, casterHpPct=40, casterMp=50, casterMaxMp=500,
                       selfMaxHp=1000, targetHpPct=40,
                       party={{address=100, job=job, hpPct=40, mp=50, maxMp=500, status={}},
                              {address=200, job=0, hpPct=40, mp=5, maxMp=300, status={}}},
                       foes={{address=901, status={}}}, roll=function() return true end}
            for k, v in pairs(t or {}) do s[k] = v end
            return s
          end
          function line(name) for i, l in ipairs(H.LINES) do if l.line == name then return H.FIRST_ID + i - 1 end end end
          function mp(a, unit)
            local n = 0
            for _, o in ipairs(a and a.others or {}) do if o.op == 'mp' and (unit == nil or o.unit == unit) then n = n + o.amount end end
            return n
          end
        ''')

    def test_a_hit_drain_is_capped_at_2_percent_of_max_mp(self):
        self.lua.execute(r'''
          P.reset()
          -- Kage III: hits while Invisible drain 10% of the damage: 100 MP from 1,000, capped at 2% of 500 = 10
          local a = P.decide('hit', S(line('Kage'), {casterStatus={invisible=true}}), 12)
          assert(a and a.drainMp == 10, tostring(a and a.drainMp))
          -- Green Mage I: 8% of 1,000 = 80, capped at 10; its HP heal keeps the full drain
          P.reset()
          a = P.decide('hit', S(3), 4)
          assert(a.drainMp == 10 and a.healHp == 80)
        ''')

    def test_a_damage_mp_to_an_ally_is_capped_at_2_percent_of_theirs(self):
        self.lua.execute(r'''
          P.reset()
          -- Wanderer II: hits on an ailing foe give the lowest-MP ally 5% of the damage (50), capped at 2% of 300 = 6
          local a = P.decide('hit', S(line('Wanderer'), {targetStatus={poison=true}}), 12)
          assert(mp(a, 200) == 6, mp(a, 200))
        ''')

    def test_b_green_mage_shares_the_drain(self):
        self.lua.execute(r'''
          P.reset()
          local rich = {{address=100, job=3, hpPct=100, mp=500, maxMp=500, status={}},
                        {address=200, job=0, hpPct=40, mp=5, maxMp=9999, status={}},
                        {address=300, job=0, hpPct=40, mp=6, maxMp=9999, status={}}}
          local a = P.decide('hit', S(3, {casterMp=500, damage=500, party=rich}), 8)   -- II 4/4: drain 40
          assert(mp(a) == 40, mp(a))                         -- shared, not 2 x 150%
          assert(a.targetMpLoss == 60)                       -- the foe loses 50% more
        ''')

    def test_c_items_return_mp_only_on_item_stages(self):
        self.lua.execute(r'''
          P.reset()
          -- White Mage III: spells return overflow MP, items do not
          assert(mp(P.decide('heal', S(0, {overflow=400, target=200}), 4)) == 20)
          assert(mp(P.decide('heal', S(0, {overflow=400, target=200, item=true}), 4)) == 0)
          -- Foresight II (overflow MP, Stage II since 0.1.25) is spells only; Provision I (item stage) still gives MP for items
          P.reset()
          local seer = line('Foresight')
          assert(mp(P.decide('heal', S(seer, {overflow=400, target=200, healAmount=900}), 6)) == 20)
          assert(mp(P.decide('heal', S(seer, {overflow=400, target=200, healAmount=900, item=true}), 6)) == 0)
          P.reset()
          assert(mp(P.decide('heal', S(line('Provision'), {target=200, healAmount=300, item=true}), 4)) > 0)
        ''')

    def test_d_combo_follow_ups_give_no_returns(self):
        self.lua.execute(r'''
          P.reset()
          local kage = line('Kage')
          local first = P.decide('hit', S(kage, {casterStatus={invisible=true}}), 12)
          assert(first.drainMp)
          local again = P.decide('hit', S(kage, {casterStatus={invisible=true}, comboFollow=true}), 12)
          assert(again == nil or (again.drainMp == nil and mp(again) == 0 and again.healHp == nil))
          -- a follow-up still deals its damage bonuses
          P.reset()
          local hunter = P.decide('hit', S(8, {targetStatus={poison=true}, comboFollow=true}), 1)
          assert(hunter and hunter.damage > 1)
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class PushBudgetTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {f'scripts/IvaliceLegacy/{n}.lua': source(n)
                   for n in ('proficiency', 'proficiency_runtime', 'clan_rank', 'hybrid_proficiency', 'mp_economy')}
        sources['scripts/DynamicFoes/Foes.lua'] = 'return {[16557] = "rare", [16402] = "boss", [16447] = "common"}'
        self.lua.globals().sources = self.lua.table_from(sources)
        self.lua.execute('''
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          memory = {u8={}, u32={}, u64={}, readArray=function() return {} end, execute=function() return 0 end}
          arg = {s32=0}
          event = {registerEventSync=function() return true end, registerEventAsync=function() return true end,
                   registerLoadHandler=function() return true end, registerSaveHandler=function() return true end,
                   getClock=function() return 1 end, executeAfterMs=function() end}
          R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], 'runtime', 't', _ENV)()
          function foe(name) return {currentChargeTime=20000, requiredChargeTime=30000, ardUnit={name=name}} end
        ''')

    def test_common_foe_budget_is_a_quarter_of_its_charge(self):
        r = self.lua.execute('''
          local w = foe(16447)
          local a = R.pushAllowance(700, w, 6000); w.currentChargeTime = w.currentChargeTime - a
          local b = R.pushAllowance(700, w, 6000); w.currentChargeTime = w.currentChargeTime - b
          local c = R.pushAllowance(700, w, 6000)
          return a, b, c
        ''')
        self.assertEqual(r, (6000, 1500, 0))     # 7,500 = 25% of 30,000 per cycle

    def test_kings_and_bosses_take_half(self):
        r = self.lua.execute('''
          local w = foe(16557)
          local a = R.pushAllowance(701, w, 6000)
          local b = R.pushAllowance(702, foe(16402), 6000)
          return a, b
        ''')
        self.assertEqual(r, (3750, 3750))

    def test_budget_refreshes_when_the_foe_acts(self):
        r = self.lua.execute('''
          local w = foe(16447)
          local a = R.pushAllowance(703, w, 9000); w.currentChargeTime = w.currentChargeTime - a
          assert(R.pushAllowance(703, w, 9000) == 0)
          R.foeActed(703)
          local b = R.pushAllowance(703, w, 9000); w.currentChargeTime = w.currentChargeTime - b
          w.currentChargeTime = 100                     -- the foe acted and its charge restarted
          local c = R.pushAllowance(703, w, 9000)
          return a, b, c
        ''')
        self.assertEqual(r, (7500, 7500, 7500))


if __name__ == '__main__':
    unittest.main()
