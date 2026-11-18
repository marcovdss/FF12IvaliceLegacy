"""The smaller player systems against the INSTALLED files: the damage floor (party quarter, foe-on-party
half, Glancing eighth), legendary mastery per family owner, the MP economy's numbers and the family identity constants
the docs quote.

Skips without IVALICE_LEGACY_GAME (see deployed_scripts.py).
"""
import unittest

import deployed_scripts as D

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

NEEDS = ('proficiency', 'hybrid_proficiency', 'damage_formula', 'mp_economy', 'family_identities')

# docs/FEATURES.md, "Jobs": which legendary families a job's mastery covers.
MASTERY = {
    'knight': ['sword'], 'lancer': ['spear'], 'warrior': ['greatsword', 'axe'], 'samurai': ['katana', 'ninjaSword'],
    'hunter': ['bow', 'dagger'], 'gunner': ['gun'], 'chemist': ['crossbow', 'measure'], 'greenMage': ['mace'],
    'timeMage': ['handBomb', 'hammer'], 'mystic': ['pole'], 'whiteMage': ['rod'], 'blackMage': ['staff'],
}


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
@unittest.skipUnless(D.have(*NEEDS), D.SKIP_REASON)
class InstalledSystemsTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.P = self.lua.execute(D.module('proficiency'))
        g.H = self.lua.execute(D.module('hybrid_proficiency'))
        g.DMG = self.lua.execute(D.module('damage_formula'))
        g.ECON = self.lua.execute(D.module('mp_economy'))
        g.FAM = self.lua.execute(D.module('family_identities'))
        g.MASTERY = self.lua.table_from({k: self.lua.table_from(v) for k, v in MASTERY.items()})
        self.lua.execute('H.attach(P)')

    def test_damage_floor_numbers(self):
        self.lua.execute(r'''
          local function native(f) f.target.removedHp = f.multiplier > f.modifier and f.power * (f.multiplier - f.modifier) or 0 end
          local function hit(casterType, targetType, attack, defense, deps, action)
            local f = {power=10, multiplier=attack, modifier=defense, outcomeType=0, action=action or 150, target={removedHp=-1}}
            DMG.wrap(function(formula) native(formula) end, deps)(f, {type=casterType}, {type=targetType}, {}, {}, {})
            return f.target.removedHp
          end
          -- party hit on a foe: the game's own above a quarter of the attack, a quarter below
          assert(hit(0, 1, 60, 30) == 300 and hit(0, 1, 30, 35) == 10 * 30 / 4 and hit(0, 1, 40, 30) == 100)
          -- foe on a party member: at least HALF of the attack
          assert(hit(1, 0, 30, 35) == 10 * 30 / 2, 'foe against matching armour keeps half its attack')
          assert(hit(1, 0, 30, 20) == 10 * 30 / 2, 'attack - defense 10 is under the half floor 15')
          assert(hit(1, 0, 60, 20) == 400, 'above the floor: the game own')
          -- a foe on a foe is not a party member: the common quarter
          assert(hit(1, 1, 30, 35) == 10 * 30 / 4)
          -- Glancing: party dagger or ninja sword Attack keeps an eighth; other weapons a quarter
          local function deps(name) return {active=function() return true end, category=function() return name end} end
          assert(hit(0, 1, 40, 38, deps('dagger')) == 10 * 40 / 8, 'dagger')
          assert(hit(0, 1, 40, 38, deps('ninjaSword')) == 10 * 40 / 8, 'ninja sword')
          assert(hit(0, 1, 40, 38, deps('sword')) == 10 * 40 / 4, 'sword keeps a quarter')
          assert(hit(0, 1, 40, 38, deps('dagger'), 77) == 10 * 40 / 4, 'only the Attack command glances')
          assert(DMG.FLOOR_DIVISOR == 4 and DMG.FOE_FLOOR_DIVISOR == 2 and DMG.GLANCING_DIVISOR == 8)
        ''')

    def test_legendary_mastery_follows_the_family_owner_table(self):
        self.lua.execute(r'''
          local names = FAM.CATEGORY_NAMES
          local index = {}
          for i, n in ipairs(names) do index[n] = i - 1 end
          local function boost(job, family, rank, weapon)
            P.reset()
            local s = {job=job, actorId=1, self=100, targetId=9, target=900, foe=true, damage=100, targetStatus={}, casterStatus={},
                       party={}, foes={}, weapon=weapon or 4130, category=index[family], roll=function() return false end}
            local a = P.decide('hit', s, rank)
            return (a and a.damage) or 1
          end
          local function base(job, family, rank)           -- the same hit with a weapon that is not legendary
            return boost(job, family, rank, 0x1001)
          end
          local function ratio(job, family, rank)
            return boost(job, family, rank) / base(job, family, rank)
          end
          local checked = 0
          for jobName, families in pairs(MASTERY) do
            local job = P.JOB[jobName]
            for _, family in ipairs(families) do
              assert(math.abs(ratio(job, family, 12) - 1.25) < 1e-9, jobName .. ' masters ' .. family)
              assert(ratio(job, family, 11) == 1, jobName .. ' is not a master before rank 12')
              checked = checked + 1
            end
            -- every family it does not own is wielded at base power
            for _, family in ipairs({'sword', 'spear', 'bow', 'gun', 'rod', 'staff', 'katana'}) do
              local owns = false
              for _, f in ipairs(families) do if f == family then owns = true end end
              if not owns then assert(ratio(job, family, 12) == 1, jobName .. ' must not master ' .. family) end
            end
          end
          assert(checked == 17, checked)
          -- a hybrid masters what either parent owns (Paladin: Knight + White Mage)
          local paladin = H.id(P.JOB.knight, P.JOB.whiteMage)
          assert(math.abs(ratio(paladin, 'sword', 12) - 1.25) < 1e-9 and math.abs(ratio(paladin, 'rod', 12) - 1.25) < 1e-9)
          assert(ratio(paladin, 'spear', 12) == 1)
          -- the thirteen legendary weapons, and nothing else, take the mastery
          local n = 0 for _ in pairs(P.LEGENDARY) do n = n + 1 end
          assert(n == 13, n)
          assert(ratio(P.JOB.knight, 'sword', 12) == 1.25 and boost(P.JOB.knight, 'sword', 12, 4131) == base(P.JOB.knight, 'sword', 12))
        ''')

    def test_mp_economy_numbers(self):
        self.lua.execute(r'''
          assert(ECON.DIVISOR == 30 and ECON.CAP == 3 and ECON.RETURN_SHARE == 0.5)
          assert(ECON.total(26, 20) == 26 and ECON.total(26, 57) == 49 and ECON.total(46, 99) == 138 and ECON.total(46, 200) == 138)
          assert(ECON.returnCap(26, 57) == 24 and ECON.returnCap(18, 20) == 9)
          local function aug(list, params)
            local set = {}
            for _, n in ipairs(list) do set[n] = true end
            return function(n) return set[n] == true end, function(bit) return (params or {})[bit] or 10 end
          end
          assert(ECON.paid(26, aug({})) == 26 and ECON.paid(26, aug({'channeling1', 'channeling2', 'channeling3'})) == 18)
          assert(ECON.paid(40, aug({'parsimony'})) == 20 and ECON.paid(40, aug({'spellspring'})) == 0 and ECON.paid(40, aug({'offering'})) == 0)
          -- the cap follows what the game really charged
          assert(ECON.returnCap(40, 30, ECON.paid(40, aug({'parsimony'}))) == 10)
        ''')

    def test_family_constants_match_what_the_docs_promise(self):
        self.lua.execute(r'''
          assert(FAM.PIERCE_FACTOR == 0.7, 'Pierce: 30% of Defense ignored')
          assert(FAM.MEND_FACTOR == 1.2, 'Mend: +20%')
          assert(FAM.COUNTERFLOW_FACTOR == 2 and FAM.COUNTERFLOW_STEP == 10 and FAM.COUNTERFLOW_CAP == 75, 'Counterflow')
          assert(FAM.CHAIN_EXTRA == 1, 'Chain: one hit longer')
          assert(FAM.AFFLICT_FACTOR == 2 and FAM.AFFLICT_CAP == 100, 'Afflict: doubled, up to 100%')
          assert(FAM.SPELLBREAK_SHARE == 0.1 and FAM.SPELLBREAK_CAP == 0.1, 'Spellbreak: 10% of damage, at most 10% of the pool')
          assert(FAM.SUNDER_STEP == 0.05 and FAM.SUNDER_FLOOR == 0.5, 'Sunder: 5%, down to half')
          -- Installed 0.5.x carries Reckless, Braced, Open and the rod's Lure; 0.6.0 removed all four. Either state is read
          -- here for what it is; a half-removed module (some constants, not others) fails.
          local oldCosts = FAM.RECKLESS_STEP ~= nil
          if oldCosts then
            assert(FAM.RECKLESS_STEP == 0.05 and FAM.RECKLESS_CAP == 0.20 and FAM.RECKLESS_WINDOW == 10000000, 'Reckless: foes hit 5% harder per axe Attack, up to +20%, gone after ten seconds')
          else
            assert(FAM.RECKLESS_CAP == nil and FAM.RECKLESS_WINDOW == nil and FAM.recklessTaken == nil, 'Reckless is gone entirely')
          end
          assert(FAM.CADENCE_STEP == 0.06 and FAM.CADENCE_HASTE == 0.05 and FAM.CADENCE_CAP == 5, 'Cadence: +6%, 5% faster, five steps')
          assert(FAM.FATIGUE_CUT == 0.25 and FAM.FATIGUE_MAX == 4, 'guard fatigue: 25% less evasion per avoid in a row')
          if oldCosts then
            assert(FAM.BRACED_PUSH == 0.10, 'Braced: 10%')
            assert(FAM.OPEN_WINDOW == 10000000 and FAM.RECKLESS_WINDOW == 10000000, 'ten seconds')
            assert(FAM.LURE_SCALE == 0.25, 'Mend Lure: a quarter of the full duration')
          else
            assert(FAM.BRACED_PUSH == nil and FAM.OPEN_WINDOW == nil and FAM.LURE_SCALE == nil and FAM.LURE == nil, 'Braced, Open and Lure are gone')
          end
          assert(FAM.FATIGUE_WINDOW == 10000000 and FAM.CADENCE_WINDOW == 10000000, 'ten seconds')
          assert(FAM.COUNTER_PARRY_EVADE == 30, 'a rolled counter parries at 30')
          local lines = 0 for _ in pairs(FAM.FAMILY_LINES) do lines = lines + 1 end
          assert(lines == 18, 'one tip line per family: ' .. lines)
          -- the tip lines quote the same numbers
          if oldCosts then assert(FAM.FAMILY_LINES.sword[2]:find('10%%'))
          else assert(not FAM.FAMILY_LINES.sword[2]:find('charge back'), 'the sword line no longer states a cost') end
          assert(FAM.FAMILY_LINES.spear[2]:find('30%% of Defense'))
          assert((FAM.FAMILY_LINES.rod[2]:find('+20%%') or FAM.FAMILY_LINES.rod[2]:find('20%% more')))
          assert(FAM.FAMILY_LINES.mace[2]:find('10%% of the damage') and FAM.FAMILY_LINES.mace[2]:find('10%% of its pool'))
          if oldCosts then
            assert(FAM.FAMILY_LINES.axe[2]:find('5%% of its start') and FAM.FAMILY_LINES.axe[2]:find('5%% stronger, up to 20%%'))
          else
            assert(FAM.FAMILY_LINES.axe[2]:find('5%% of its starting value') and not FAM.FAMILY_LINES.axe[2]:find('stronger'))
          end
          assert(((FAM.FAMILY_LINES.unarmed[2]:find('+6%%') and FAM.FAMILY_LINES.unarmed[2]:find('+30%%')) or FAM.FAMILY_LINES.unarmed[2]:find('6%% harder, up to 30%%')))
        ''')


if __name__ == '__main__':
    unittest.main()
