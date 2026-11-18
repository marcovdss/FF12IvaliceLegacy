"""Accessory effects (prototypes/accessory_effects.lua) through the prototype Proficiency runtime on the synthetic
battle (tests/runtime_world.py): each stage fires from the hook that dispatches it, and its control does not."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_world  # noqa: E402

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def prototype(name):
    return (ROOT / 'prototypes' / f'{name}.lua').read_text(encoding='utf-8').replace(chr(13) + chr(10), chr(10))


EXTRA = r'''
-- second accessory slot (sa_saveMem) and the accessory field the runtime reads
SECOND = {}
memory.getSymbol = function(name) if name == 'sa_saveMem' then return 0x50000 end end
memory.u16 = setmetatable({}, {__index = function(_, a) return SECOND[(a - 0x50000) // 4] or 0 end})
function wear(u, main, second) u.accessory = main; SECOND[u.identifier] = second; return u end
bpack.section14[60] = {category=1, mpOrMistCost=10, elements=flags({fire=true})}    -- a Fire spell
bpack.section14[61] = {category=1, mpOrMistCost=10, elements=flags({ice=true})}     -- an Ice spell
bpack.section13[7] = {category=1, chargeTime=30, offhandCategory=0, elements=flags({fire=true})}  -- a Fire sword
bpack.section13[8] = {category=1, chargeTime=30, offhandCategory=0, elements=flags()}            -- a plain sword
function said(pattern) for _, line in ipairs(printed) do if line:find(pattern, 1, true) then return true end end return false end
function forget() printed = {} end
-- the adapters call the hooks through R.wrap, which runs the accessories after Proficiency
local W = {}
function hook(n, f, caster, target, before) W[n] = W[n] or R.wrap(n, function() end); W[n](f, caster, target, nil, classes, helpers) end
function hit(caster, foe, t)
  t = t or {}
  local f = F({initial=foe.address, critical=t.critical, target={removedHp=t.damage or 100, identifier=9, work=works[foe.address], status=t.status},
               caster={status=t.casterStatus or {}}})
  hook(165, f, caster, foe)
  return f
end
function onHit(caster, foe, ids)   -- Forge 182 after the hit: statuses newly landed
  local f = F({initial=foe.address, target={identifier=9, work=works[foe.address]}})
  local wrapped = R.wrap(182, function(formula) for _, id in ipairs(ids) do formula.target.addedStatusEffects[id] = 1 end end)
  wrapped(f, caster, foe, nil, classes, helpers)
  return f
end
function spell(caster, foe, action, damage)
  local f = F({action=action, initial=foe.address, target={removedHp=damage or 1000, identifier=9, work=works[foe.address]}})
  R.hook222(f, caster, foe)
  R.wrap(170, function() end)(f, caster, foe, nil, classes, helpers)
  return f
end
'''


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class AccessoryEffectsTest(unittest.TestCase):
    def run_lua(self, code):
        lua = LuaRuntime(unpack_returned_tuples=True)
        runtime_world.world(lua, loader=prototype, overrides={'accessory_effects': prototype('accessory_effects')})
        lua.execute(EXTRA)
        return lua.execute(code)

    def test_module_loads_and_copies_no_hybrid_stage(self):
        loaded, copies = self.run_lua('return R.Accessory ~= nil, #R.Accessory.copies(H.LINES)')
        self.assertTrue(loaded)
        self.assertEqual(copies, 0)

    def test_gillie_boots_slow_once_per_oil(self):
        first, again = self.run_lua('''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local gillie = wear(party(1, {weapon=0x1008}), 4474)
          onHit(party(2, {weapon=0x1008}), foe, {STATUS.oil})
          local a = hit(gillie, foe, {status={oil=true}})
          local b = hit(gillie, foe, {status={oil=true}})
          return a.target.addedStatusEffects[STATUS.slow], b.target.addedStatusEffects[STATUS.slow]
        ''')
        self.assertEqual((first, again), (1, 0))

    def test_cameo_mark_is_spent_by_another_members_dodge_roll(self):
        own, gun, other, spent = self.run_lua('''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local cameo = wear(party(1, {weapon=0x1008}), 4468)
          local ally = party(2, {weapon=0x1008})
          local function f(id) local x = F(); x.identifier = id; return x end
          hit(cameo, foe, {critical=true})
          return R.spotter34(f(20), cameo, foe, classes, helpers), R.spotter34(f(27), ally, foe, classes, helpers),
                 R.spotter34(f(20), ally, foe, classes, helpers), R.spotter34(f(20), ally, foe, classes, helpers)
        ''')
        self.assertEqual((own, gun, other, spent), (False, False, True, False))

    def test_cameo_marks_on_a_critical_or_a_combo_follow_up_not_on_a_plain_first_hit(self):
        plain, crit, afterFirst, combo, late = self.run_lua('''
          PARTY_IDS = {1, 2}
          local function foe(a) return unit({type=1, address=a, hp=9000, maxHp=9000}) end
          local function f(id) local x = F(); x.identifier = id; return x end
          local cameo = wear(party(1, {weapon=0x1008}), 4468)
          local ally = party(2, {weapon=0x1008})
          local t1 = foe(900); hit(cameo, t1)                            -- control: a plain first hit
          local plain = R.spotter34(f(20), ally, t1, classes, helpers)
          local t2 = foe(901); hit(cameo, t2, {critical=true})
          local crit = R.spotter34(f(20), ally, t2, classes, helpers)
          local t3 = foe(902); NOW = NOW + 5000000; hit(cameo, t3)
          local afterFirst = R.spotter34(f(20), ally, t3, classes, helpers)
          NOW = NOW + 200000; hit(cameo, t3)                             -- the same member again 0.2 s later
          local combo = R.spotter34(f(20), ally, t3, classes, helpers)
          local t4 = foe(903); NOW = NOW + 5000000; hit(cameo, t4); NOW = NOW + 1000000; hit(cameo, t4)
          local late = R.spotter34(f(20), ally, t4, classes, helpers)    -- a second apart: two swings
          return plain, crit, afterFirst, combo, late
        ''')
        self.assertEqual((plain, crit, afterFirst, combo, late), (False, True, False, True, False))

    def test_a_belt_wearer_is_needed_for_the_mark(self):
        mark = self.run_lua('''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local bare, ally = party(1, {weapon=0x1008}), party(2, {weapon=0x1008})
          hit(bare, foe, {critical=true})
          local x = F(); x.identifier = 20
          return R.spotter34(x, ally, foe, classes, helpers)
        ''')
        self.assertFalse(mark)

    def test_no_stage_grants_bravery_mp_or_pushes_and_haste_only_as_the_hermes_opening(self):
        bad, haste = self.run_lua('''
          local n, haste = 0, {}
          for id, stages in pairs(R.Accessory.STAGES) do
            for _, st in ipairs(stages) do
              for _, fx in ipairs(st[3]) do
                if fx[2] == 'haste' then haste[#haste + 1] = id .. ':' .. st[1] .. ':' .. tostring(fx.fresh) end
                if fx[2] == 'bravery' or fx[1] == 'mp' or fx[1] == 'push' then n = n + 1 end
              end
            end
          end
          return n, table.concat(haste, ',')
        ''')
        self.assertEqual(bad, 0)
        self.assertEqual(haste, '4473:fightStart:true')    # once per fight, never refreshing: fresh on the fight's start only

    # ---------- Hermes Sandals: Opening stride ----------

    HERMES = '''
      PARTY_IDS = {1}
      function hastes(unit) local n = 0
        for _, a in ipairs(LOG.added) do if a.unit == unit.address and a.id == STATUS.haste then n = n + 1 end end
        return n end
    '''

    def test_hermes_hastes_its_wearer_once_at_the_start_of_a_fight_for_a_full_duration(self):
        first, second, duration, only_wearer = self.run_lua(self.HERMES + '''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local hermes = wear(party(1, {weapon=0x1008}), 4473)
          local other = party(2, {weapon=0x1008})
          hit(hermes, foe)
          local first = hastes(hermes)
          hit(hermes, foe); NOW = NOW + 1000000; hit(hermes, foe)
          return first, hastes(hermes), LOG.added[1].d, hastes(other)
        ''')
        self.assertEqual((first, second), (1, 1))      # one Haste, not one per hit
        self.assertEqual(duration, 100)               # scale 1: the status's full duration (the stub's 100), not half
        self.assertEqual(only_wearer, 0)              # a bare party member gets nothing

    def test_a_fight_started_by_someone_else_or_by_a_foe_still_hastes_the_wearer(self):
        by_ally, by_foe = self.run_lua(self.HERMES + '''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local hermes = wear(party(1, {weapon=0x1008}), 4473)
          local ally = party(2, {weapon=0x1008})
          hit(ally, foe)                                    -- the ally opens the fight
          local by_ally = hastes(hermes)
          helpers.removeStatusEffect(hermes.address, STATUS.haste); hermes.temporaryStatusEffects.haste = 0
          reset(); R.Accessory.reset()
          local foe2 = unit({type=1, address=901, hp=9000, maxHp=9000})
          local f = F({initial=hermes.address, target={removedHp=10, identifier=1}})
          R.wrap(165, function() end)(f, foe2, hermes, nil, classes, helpers)   -- a foe's blow opens it
          return by_ally, hastes(hermes)
        ''')
        self.assertEqual((by_ally, by_foe), (1, 1))

    def test_a_fight_opened_by_a_foe_spell_hastes_the_wearer_once_before_any_party_hit(self):
        """Forge 222 sees every foe action on the party, so a spell, a status or a hit that deals nothing opens the fight."""
        opened, again, later, ally_cast, after_hit = self.run_lua(self.HERMES + '''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local hermes = wear(party(1, {weapon=0x1008}), 4473)
          local ally = party(2, {weapon=0x1008})
          local function foeAction(target, removed)
            local f = F({initial=target.address, target={removedHp=removed, identifier=1}})
            R.wrap(222, function() end)(f, foe, target, nil, classes, helpers)
          end
          -- a party caster's action on the party (a Cure) is not the foes' opening
          local cure = F({initial=ally.address, target={removedHp=-1, identifier=2}})
          R.wrap(222, function() end)(cure, hermes, ally, nil, classes, helpers)
          local ally_cast = hastes(hermes)
          foeAction(ally, -1)                       -- a spell that deals no damage, aimed at the ally
          local opened = hastes(hermes)
          foeAction(hermes, -1); foeAction(ally, 40); NOW = NOW + 1000000; foeAction(hermes, 10)
          local again = hastes(hermes)
          helpers.removeStatusEffect(hermes.address, STATUS.haste); hermes.temporaryStatusEffects.haste = 0
          hit(hermes, foe)                          -- a party hit in the same fight does not grant a second one
          local after_hit = hastes(hermes)
          NOW = NOW + 31000000                      -- a quiet gap of more than 30 s: a new fight
          foeAction(hermes, -1)
          return opened, again, hastes(hermes), ally_cast, after_hit
        ''')
        self.assertEqual((ally_cast, opened, again, after_hit, later), (0, 1, 1, 1, 2))

    def test_a_foe_acting_on_another_foe_does_not_open_a_fight(self):
        granted = self.run_lua(self.HERMES + '''
          local foe, foe2 = unit({type=1, address=900, hp=9000, maxHp=9000}), unit({type=1, address=901, hp=9000, maxHp=9000})
          local hermes = wear(party(1, {weapon=0x1008}), 4473)
          local f = F({initial=foe2.address, target={removedHp=5, identifier=1}})
          R.wrap(222, function() end)(f, foe, foe2, nil, classes, helpers)       -- a Confused foe hitting its own side
          return hastes(hermes)
        ''')
        self.assertEqual(granted, 0)

    def test_hermes_follows_the_fight_clock_29_seconds_one_grant_31_seconds_two(self):
        close, apart = self.run_lua(self.HERMES + '''
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local hermes = wear(party(1, {weapon=0x1008}), 4473)
          hit(hermes, foe)
          helpers.removeStatusEffect(hermes.address, STATUS.haste)         -- the Haste ran out
          NOW = NOW + 29000000; hit(hermes, foe)
          local close = hastes(hermes)
          helpers.removeStatusEffect(hermes.address, STATUS.haste)
          NOW = NOW + 31000000; hit(hermes, foe)
          return close, hastes(hermes)
        ''')
        self.assertEqual((close, apart), (1, 2))

    def test_hermes_never_refreshes_a_haste_the_wearer_already_holds(self):
        held, later = self.run_lua(self.HERMES + '''
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local hermes = wear(party(1, {weapon=0x1008, status={haste=true}}), 4473)
          hit(hermes, foe)
          local held = hastes(hermes)
          return held, LOG.added[1] and LOG.added[1].id or 0
        ''')
        self.assertEqual((held, later), (0, 0))

    def test_two_hermes_slots_or_two_wearers_each_grant_once(self):
        a, b = self.run_lua(self.HERMES + '''
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          PARTY_IDS = {1, 2}
          local one = wear(party(1, {weapon=0x1008}), 4473, 4473)       -- the same id in both slots counts once
          local two = wear(party(2, {weapon=0x1008}), 4473)
          hit(one, foe)
          return hastes(one), hastes(two)
        ''')
        self.assertEqual((a, b), (1, 1))

    def test_a_worn_haste_or_float_counts_everywhere_no_guard_remains(self):
        haste, float_, gone = self.run_lua('''
          local u = wear(party(1, {job1=2, weapon=0x1008, status={haste=true, float=true}}), 4473, 4476)
          local s = R.base(u, u.temporaryStatusEffects, classes, helpers)
          local t = R.accessoryBase(u, u.temporaryStatusEffects, classes, helpers)
          return s and s.casterStatus.haste, (s and s.casterStatus.float) and t and t.casterStatus.float,
                 (R.guardTarget == nil and R.wearsHermes == nil and R.HERMES_SANDALS == nil and R.PIXIE_SHOES == nil
                  and s.casterHeldHaste == nil and t.casterHeldHaste == nil)
        ''')
        self.assertTrue(haste)
        self.assertTrue(float_)
        self.assertTrue(gone)

    def test_the_sources_hold_no_hidden_haste_or_float_rule(self):
        for name in ('proficiency_runtime', 'proficiency', 'hybrid_proficiency', 'accessory_effects'):
            text = prototype(name)
            for needle in ('casterHeldHaste', 'wearsHermes', 'guardTarget', 'HERMES_SANDALS', 'PIXIE_SHOES'):
                self.assertNotIn(needle, text, (name, needle))

    # ---------- Elmdore Fang: Drinker ----------

    DRINKER = '''
      PARTY_IDS = {1}
      function healed(unit) local n = 0
        for _, h in ipairs(LOG.hp) do if h.unit == unit.address and h.amount > 0 then n = n + h.amount end end
        return n end
    '''

    def test_drinker_heals_three_percent_of_max_hp_per_attack_on_a_sapped_or_poisoned_foe(self):
        sap, poison, plain, full = self.run_lua(self.DRINKER + '''
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local wearer = wear(party(1, {weapon=0x1008, hp=500, maxHp=1000}), 4464)
          hit(wearer, foe, {status={sap=true}})
          local sap = healed(wearer)
          NOW = NOW + 2000000; reset(); hit(wearer, foe, {status={poison=true}})
          local poison = healed(wearer)
          NOW = NOW + 2000000; reset(); hit(wearer, foe, {status={blind=true}})
          local plain = healed(wearer)
          wearer.currentHp = 1000; NOW = NOW + 2000000; reset(); hit(wearer, foe, {status={sap=true}})
          return sap, poison, plain, healed(wearer)
        ''')
        self.assertEqual((sap, poison, plain, full), (30, 30, 0, 0))   # 3% of 1000; nothing without Sap or Poison, nothing at full HP

    def test_drinker_counts_each_attack_once_never_a_combo_follow_up(self):
        first, follow, next_attack = self.run_lua(self.DRINKER + '''
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local wearer = wear(party(1, {weapon=0x1008, hp=500, maxHp=1000}), 4464)
          hit(wearer, foe, {status={sap=true}})
          local first = healed(wearer)
          NOW = NOW + 150000; hit(wearer, foe, {status={sap=true}})          -- the same Attack's combo follow-up
          local follow = healed(wearer)
          NOW = NOW + 3000000; hit(wearer, foe, {status={sap=true}})         -- a new Attack
          return first, follow, healed(wearer)
        ''')
        self.assertEqual((first, follow, next_attack), (30, 30, 60))

    def test_drinker_heals_once_for_a_sweep_that_reaches_several_sapped_foes(self):
        sweep, next_swing, other_wearer = self.run_lua(self.DRINKER + '''
          local a = unit({type=1, address=900, hp=9000, maxHp=9000})
          local b = unit({type=1, address=901, hp=9000, maxHp=9000})
          local c = unit({type=1, address=902, hp=9000, maxHp=9000})
          local wearer = wear(party(1, {weapon=0x1008, hp=100, maxHp=1000}), 4464)
          hit(wearer, a, {status={sap=true}}); hit(wearer, b, {status={poison=true}}); hit(wearer, c, {status={sap=true}})
          local sweep = healed(wearer)                                  -- three foes, one swing
          NOW = NOW + 3000000; hit(wearer, a, {status={sap=true}})        -- the next swing heals again
          return sweep, healed(wearer), 0
        ''')
        self.assertEqual((sweep, next_swing), (30, 60))

    def test_drinker_heals_only_the_wearer_and_is_not_a_self_harm_item(self):
        other, sap_on_wearer = self.run_lua(self.DRINKER + '''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local wearer = wear(party(1, {weapon=0x1008, hp=500, maxHp=1000}), 4464)
          local ally = party(2, {weapon=0x1008, hp=500, maxHp=1000})
          hit(ally, foe, {status={sap=true}})                        -- an ally's hit does not feed the wearer
          local stages = R.Accessory.STAGES[4464]
          local harms = 0
          for _, st in ipairs(stages) do for _, fx in ipairs(st[3]) do if fx[1] == 'self' then harms = harms + 1 end end end
          return healed(wearer) + healed(ally), harms
        ''')
        self.assertEqual((other, sap_on_wearer), (0, 0))

    def test_the_fight_event_fires_once_per_fight_number(self):
        a, b, c, d = self.run_lua('''
          local A = R.Accessory
          A.reset()
          local a = A.fightStarted()
          local b = A.fightStarted()
          A.reset()                          -- a load, a map change or a 30 s gap makes a new fight
          return a, b, A.fightStarted(), A.fightStarted()
        ''')
        self.assertEqual((a, b, c, d), (True, False, True, False))


if __name__ == '__main__':
    unittest.main()
