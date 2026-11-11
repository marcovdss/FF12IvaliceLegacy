"""Weapons release 0.1.34, family and damage side: the Forge-chain membership check (B6) and the behaviours of
B1, B3, B5, B7, D1a, D2, D4, D6, D8 and D11, against Forge-shaped strict fakes (see test_family_identities)."""
import json
import os
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_family_identities import FORGE_FAKES  # noqa: E402

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
CHAINS = json.loads((ROOT / 'tests/fixtures/forge_formula_chains.json').read_text(encoding='utf-8'))
# Optional cross-check against an installed Forge: set IVALICE_LEGACY_FORGE_FORMULAS to its formulas.lua.
_forge = os.environ.get('IVALICE_LEGACY_FORGE_FORMULAS')
FORGE_LIVE = Path(_forge) if _forge else None

MELEE = [20, 21, 22, 23, 24, 28, 29, 64, 103, 104]

# identity -> (adapter file that wraps the Forge function, phase, the weapon formulas that must run that function)
# Formulas are the deployed section_013 ids per family (RUNTIME-AUDIT 0 fact 2); Mend wraps the heal of formula 4.
IDENTITIES = {
    'sunder / reckless': ('forge_family_68.lua', 'onHit', [23]),
    'pierce': ('forge_diagnostic_113.lua', 'onHit', [20]),
    'glancing (dagger, ninja sword)': ('forge_diagnostic_113.lua', 'onHit', [21]),
    'damage floor, spear/katana/pole/bow/crossbow/gun': ('forge_diagnostic_113.lua', 'onHit', [20, 22, 24, 25, 26, 27]),
    'damage floor, axe/hammer/hand-bomb': ('forge_damage_126.lua', 'onHit', [23]),
    'unseen': ('forge_proficiency_22.lua', 'onCast', [21]),
    'true aim': ('forge_family_44.lua', 'onCast', [27]),
    'afflict': ('forge_diagnostic_171.lua', 'onHit', [25, 26]),
    'spellbreak': ('forge_proficiency_165.lua', 'onHit', [104, 27]),
    'cadence': ('forge_proficiency_165.lua', 'onHit', [29]),
    'mend / lure': ('forge_proficiency_110.lua', 'onHit', [4]),
    'counterflow': ('forge_family_8.lua', 'onCast', [24, 20]),
    'chain / open': ('forge_family_10.lua', 'onCast', [22]),
    'fatigue 41': ('forge_guard_41.lua', 'onCast', MELEE),
    'fatigue 42': ('forge_guard_42.lua', 'onCast', [25]),
    'fatigue 43': ('forge_guard_43.lua', 'onCast', [26]),
}


def adapter_function(path):
    """The Forge function number an adapter file wraps (its loadfile of scripts/TheInsurgentsForge/functions/N.lua)."""
    text = (ROOT / 'prototypes' / path).read_text(encoding='utf-8')
    found = re.search(r"functions/(\d+)\.lua'", text)
    if found:
        return int(found.group(1))
    return int(re.search(r'local N = (\d+)', text).group(1))


class ChainMembershipTests(unittest.TestCase):
    """A wrap on a function the weapon's formula never runs is a dead identity (Marksman was one for ten releases)."""

    def test_every_identitys_function_is_in_its_weapon_formulas_chain(self):
        for name, (adapter, phase, formulas) in sorted(IDENTITIES.items()):
            n = adapter_function(adapter)
            for formula in formulas:
                with self.subTest(identity=name, function=n, formula=formula):
                    self.assertIn(n, CHAINS[phase][str(formula)],
                                  f'{name}: Forge {n} is not in formula {formula} {phase} {CHAINS[phase][str(formula)]}')

    def test_the_known_gaps_are_the_documented_ones(self):
        # formula 23 damages through 126, so the 113 floor never reaches it; a formula-64 gun is melee-like and has no 44
        self.assertNotIn(113, CHAINS['onHit']['23'])
        self.assertIn(126, CHAINS['onHit']['23'])
        self.assertNotIn(44, CHAINS['onCast']['64'])
        # guns (27) have no evade roll: nothing of the fatigue chain runs for them
        for n in (41, 42, 43):
            self.assertNotIn(n, CHAINS['onCast']['27'])
        # 42 is the bow roll (formula 25), 43 the crossbow roll (formula 26)
        self.assertIn(42, CHAINS['onCast']['25'])
        self.assertIn(43, CHAINS['onCast']['26'])

    @unittest.skipUnless(FORGE_LIVE is not None and FORGE_LIVE.exists(),
                         'set IVALICE_LEGACY_FORGE_FORMULAS to an installed formulas.lua to cross-check')
    def test_fixture_matches_the_installed_forge(self):
        text = FORGE_LIVE.read_text(encoding='utf-8')
        for phase, name in (('onCast', 'onCastFormulas'), ('onHit', 'onHitFormulas')):
            body = re.search(r'local ' + name + r' = \{(.*?)\n\}', text, re.S).group(1)
            live = {k: [int(x) for x in v.split(',') if x.strip()] for k, v in re.findall(r'\[(\d+)\] = \{([^}]*)\}', body)}
            self.assertEqual(live, CHAINS[phase])


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ReleaseBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().module = self.lua.execute((ROOT / 'prototypes/family_identities.lua').read_text())
        self.lua.globals().damage = self.lua.execute((ROOT / 'prototypes/damage_formula.lua').read_text())
        self.lua.globals().filterModule = self.lua.execute((ROOT / 'prototypes/attack_area_filter.lua').read_text())
        self.lua.execute(FORGE_FAKES)
        self.lua.execute('''
          module.reset()
          reports = {}
          active = true; cat = nil; now = 1000000000
          deps = {active = function() return active end,
                  category = function(unit) return cat end,
                  clock = function() return now end,
                  report = function(kind, ...) reports[#reports + 1] = {kind, ...} end}
          function kinds()
            local k = {}
            for _, r in ipairs(reports) do k[#k + 1] = r[1] end
            return table.concat(k, ',')
          end
          function run(wrapped, caster, target, fields, helpers)
            local f = hit(caster, target, fields)
            local r = table.pack(wrapped(f, caster, target, {}, {}, helpers or {}))
            return f, r
          end
        ''')

    # ---------- B1 ----------

    def test_greatsword_sweep_reaches_foes_only(self):
        self.lua.execute('''
          local logs = {}
          local filter = filterModule.new({emit = function(l) logs[#logs + 1] = l end})
          local function sample(t)
            local s = {action = 150, initial = 0x5000, target = 0x6000, casterType = 0, casterId = 1, category = 2,
                       targetType = 1, targetId = 8, clock = 1}
            for k, v in pairs(t or {}) do s[k] = v end
            return s
          end
          assert(filter:decide(sample()) == 'sweep', 'a foe beside the target is cleaved')
          assert(filter:decide(sample({targetType = 0})) == 'skip', 'an ally beside the target is spared')
          assert(filter:decide(sample({targetType = 0, category = 1})) == 'skip')
          assert(filter:decide(sample({casterType = 1, targetType = 0})) == 'skip')
          assert(filter:decide(sample({targetType = 'x'})) == 'skip', 'an unreadable side is not a foe')
          assert(logs[2]:find('target=party:8 decision=skip', 1, true), logs[2])
        ''')

    # ---------- B3 ----------

    def test_open_is_spent_only_by_an_in_stance_evade_roll(self):
        self.lua.execute(r"""
          cat = 'katana'
          local sam = wielder(7, 93)
          local seen = {}
          local wrapped = module.guardWrap(function(f) seen[#seen + 1] = {f.evadeShield, f.evadeWeapon, f.evadeParry}
                                            f.outcomeType = 0 f.skipState = 0 end, deps)
          local function attack(stance)
            local f = hit(foe(), sam); f.evadeShield = 30; f.evadeWeapon = 20; f.evadeParry = 40
            f.target.battleUnitWork.battleFlags.isInBattleStance = stance
            wrapped(f, foe(), sam, {}, {}, {})
          end
          local function forge10(f) f.comboState = 1; f.caster.keep.battleFlags.comboCount = 3 end
          local f = hit(sam, foe()); module.wrap10(forge10, deps)(f, sam, foe(), {}, {}, {})
          attack(0)                                                   -- mid-action: no roll runs, the opening is kept
          assert(seen[1][1] == 30 and seen[1][3] == 40, 'out of stance nothing is zeroed')
          attack(1)                                                   -- the next in-stance roll meets no evasion
          assert(seen[2][1] == 0 and seen[2][2] == 0 and seen[2][3] == 0, 'opened')
          attack(1); assert(seen[3][3] == 40, 'and it is spent once')
          -- a stale opening is dropped by an out-of-stance attack too
          f = hit(sam, foe()); module.wrap10(forge10, deps)(f, sam, foe(), {}, {}, {})
          now = now + 11000000; attack(0); attack(1)
          assert(seen[5][3] == 40, 'the window passed')
        """)

    # ---------- B5 ----------

    def test_a_failing_reader_logs_one_error_per_identity(self):
        self.lua.execute('''
          local broken = {active = function() error('reader unavailable') end, category = deps.category, report = deps.report}
          cat = 'axe'
          local axe = module.wrap68(function() end, broken)
          for i = 1, 4 do run(axe, wielder(), foe()) end
          local pierce = module.pierceWrap(function() end, broken)
          for i = 1, 4 do run(pierce, wielder(), foe(), {modifier = 5}) end
          local seen = {}
          for _, r in ipairs(reports) do
            assert(r[1] == 'error', r[1])
            seen[r[2]] = (seen[r[2]] or 0) + 1
          end
          assert(seen.sunder == 1 and seen.reckless == 1 and seen.pierce == 1, kinds())
          assert(reports[1][3]:find('reader unavailable', 1, true) and not reports[1][3]:find('\\n'))
        ''')

    def test_error_throttle_is_per_load(self):
        self.lua.execute('''
          event = {registerEventSync = function() return true end, registerEventAsync = function() return true end}
          memory = {readArray = function() return nil end}
          bpack = {section13 = setmetatable({}, {__index = function() return {category = 9} end})}
          local lines = {}
          print = function(l) lines[#lines + 1] = l end
          module.register()
          local d = module.deps()
          module.fail(d, 'x', 'boom'); module.fail(d, 'x', 'boom'); module.fail(d, 'y', 'boom')
          local n = 0 for _, l in ipairs(lines) do if l:find('Legacy Family: error', 1, true) then n = n + 1 end end
          assert(n == 2, n)
        ''')

    def test_counter_lines_carry_the_outcome_and_the_weapons(self):
        self.lua.execute('''
          cat = 'pole'
          local defender = wielder(1, 21)
          defender.permanentAugments.counter = 1
          local wrapped = module.wrap8(function(f) f.counterState = 1 f.outcomeType = 2 end, deps)
          run(wrapped, foe(), defender, {counterChance = 25})
          local r = reports[1]
          assert(r[1] == 'counter' and r[9] == 'outcome=2' and r[10] == 'weapon=4200 offhand=4300', table.concat(r, ' '))
        ''')

    def test_family_lines_carry_weapon_ids_and_damage_after_the_effect(self):
        self.lua.execute('''
          cat = 'spear'
          local pierce = module.pierceWrap(function(f) f.target.removedHp = 77 end, deps)
          run(pierce, wielder(), foe(), {modifier = 30})
          assert(reports[1][1] == 'pierce' and reports[1][5] == 'removed=77' and reports[1][6] == 'weapon=4200 offhand=4300')
          reports = {}
          cat = 'mace'
          local spell = module.wrap165(function(f) f.target.removedHp = 300 end, deps)
          run(spell, wielder(), foe(555, 20, 1000, 10, 500))
          assert(reports[1][1] == 'spellbreak' and reports[1][5] == 'damage=300', table.concat(reports[1], ' '))
        ''')

    def test_guard_line_carries_the_stance_and_a_counter_swing_gets_its_own_line(self):
        self.lua.execute('''
          cat = 'spear'
          local tank = wielder(1, 31)
          local wrapped = module.guardWrap(function(f) f.outcomeType = 2 f.skipState = 1 end, deps)
          local f = hit(foe(), tank); f.evadeShield = 40
          wrapped(f, foe(), tank, {}, {}, {})
          local found = false
          for _, r in ipairs(reports) do
            if r[1] == 'counter-swing' and r[3] == 'outcome=2' then found = true end
          end
          assert(found, kinds())
        ''')

    # ---------- B7 ----------

    def test_spellbreak_keeps_a_queued_mp_gain_on_the_target(self):
        self.lua.execute('''
          cat = 'mace'
          local wrapped = module.wrap165(function(f)
            f.target.removedHp = 300
            f.target.mpUpdateState = 1; f.target.addedMp = 7
          end, deps)
          local f = run(wrapped, wielder(), foe(555, 20, 1000, 10, 500))
          assert(f.target.addedMp == 7, 'an MP gain queued by another stage is not erased')
          assert(f.target.removedMp == 30 and f.target.mpUpdateState == 1)
        ''')

    def test_counterflow_treats_a_temporary_counter_from_elsewhere_as_a_licence(self):
        self.lua.execute('''
          cat = 'pole'
          local wrapped = module.wrap8(function(f) SEEN = f.counterChance end, deps)
          local defender = wielder(1, 41)
          defender.temporaryAugments.counter = 1        -- a hybrid reaction, not our loan
          run(wrapped, foe(), defender, {counterChance = 25})
          assert(SEEN == 50, 'doubled like a permanent licence: ' .. tostring(SEEN))
          assert(module.lentCounters() == 0, 'nothing was lent')
          -- our own loan is still not a licence
          local bare = wielder(2, 42)
          run(wrapped, foe(), bare, {counterChance = 0})
          assert(SEEN == 20 and module.lentCounters() == 1)
          run(wrapped, foe(), bare, {counterChance = 12})
          assert(SEEN == 30, 'the loan keeps the Speed / 2 rule plus flow: ' .. tostring(SEEN))
        ''')

    # ---------- D1a: counter-parry ----------

    PARRY = """
          local seen = {}
          wrapped = module.guardWrap(function(f) seen[#seen + 1] = f.evadeWeapon end, deps)
          -- one blow: the defender's weapon evade is `evade`, Forge 8 rolled `counterState`
          function blow(defender, evade, counterState)
            local f = hit(foe(), defender, {}); f.evadeWeapon = evade; f.counterState = counterState
            wrapped(f, foe(), defender, {}, {}, {})
            return f
          end
    """

    def test_a_defender_with_no_weapon_evade_parries_a_counter_it_rolled(self):
        self.lua.execute(self.PARRY + """
          cat = 'spear'
          blow(wielder(1, 51), 0, 1); assert(seen[1] == 30, 'a spear that rolled a counter parries and counters')
          blow(wielder(2, 52), 0, 0); assert(seen[2] == 0, 'a plain blow meets no spear parry')
          blow(wielder(3, 53), 30, 0); assert(seen[3] == 0, 'Long haft still zeroes a spear evade')
          blow(wielder(4, 54), 40, 1); assert(seen[4] == 40, 'the legendary spear keeps its own higher evade, never lowered')
          cat = 'bow'; blow(wielder(5, 55), 0, 1); assert(seen[5] == 30, 'a bow')
          cat = 'bow'; blow(wielder(6, 56), 0, 0); assert(seen[6] == 0)
          cat = 'handBomb'; blow(wielder(7, 57), 0, 1); assert(seen[7] == 30, 'a hand-bomb')
          cat = 'staff'; blow(wielder(8, 58), 0, 1); assert(seen[8] == 30, 'any weapon row with no weapon evade')
          cat = 'staff'; blow(wielder(9, 59), 15, 1); assert(seen[9] == 15, 'a weapon with its own evade is native')
          cat = 'bow'; blow(wielder(10, 60), 10, 1); assert(seen[10] == 30, 'a bow with a little evade is raised to the pole band')
          local kinds_ = {}
          for _, r in ipairs(reports) do kinds_[r[1]] = (kinds_[r[1]] or 0) + 1 end
          assert(kinds_['counter-parry'] == 5, kinds())     -- spear, bow, hand-bomb, no-evade staff, 10-evade bow
          assert(module.COUNTER_PARRY_EVADE == 30)
          assert(reports[1][1] == 'counter-parry' or reports[2][1] == 'counter-parry')
        """)

    def test_counter_parry_gives_the_counter_swing_outcome_two_through_forge_41(self):
        self.lua.execute(self.PARRY + """
          -- Forge 41, the weapon roll only: a counter swing is outcome 2 when random(accuracy) < evadeWeapon
          wrapped = module.guardWrap(function(f)
            local roll = ROLL
            if roll < f.evadeWeapon then f.outcomeType = f.counterState == 1 and 2 or 1; f.skipState = 1 end
          end, deps)
          cat = 'spear'
          ROLL = 29
          local f = hit(foe(), wielder(1, 71), {}); f.evadeWeapon = 0; f.counterState = 1
          wrapped(f, foe(), wielder(1, 71), {}, {}, {})
          assert(f.outcomeType == 2 and f.skipState == 1, 'a roll under 30: parry and counter')
          ROLL = 30
          f = hit(foe(), wielder(3, 73), {}); f.evadeWeapon = 0; f.counterState = 1
          wrapped(f, foe(), wielder(3, 73), {}, {}, {})
          assert(f.outcomeType == 0 and f.skipState == 0, 'a roll of 30 or more is a hit: the counter is not guaranteed')
          ROLL = 0
          f = hit(foe(), wielder(2, 72), {}); f.evadeWeapon = 0; f.counterState = 0
          wrapped(f, foe(), wielder(2, 72), {}, {}, {})
          assert(f.outcomeType == 0 and f.skipState == 0, 'a spear that did not roll a counter never parries')
        """)

    def test_counter_parry_leaves_fists_foes_and_opened_samurai_alone(self):
        self.lua.execute(self.PARRY + """
          cat = 'unarmed'; blow(wielder(1, 81), 0, 1); assert(seen[1] == 0, 'fists count through the Cadence streak')
          cat = 'spear'; blow(foe(), 0, 1)
          assert(seen[2] == nil or seen[2] == 0, 'a foe defender is native')
          active = false; blow(wielder(2, 82), 0, 1); assert(seen[#seen] == 0, 'outside Legacy')
          active = true
          -- an opened Samurai meets no evasion at all, counter or not
          cat = 'katana'
          local sam = wielder(3, 83)
          local f = hit(sam, foe()); module.wrap10(function(g) g.comboState = 1; g.caster.keep.battleFlags.comboCount = 3 end,
                                                  deps)(f, sam, foe(), {}, {}, {})
          blow(sam, 20, 1); assert(seen[#seen] == 0, 'Open wins: ' .. tostring(seen[#seen]))
        """)

    # ---------- D2 ----------

    CADENCE = '''
          cat = 'unarmed'
          calls = {}
          order = {}
          wrapped = module.wrap165(function(f) order[#order + 1] = 'original'; f.target.removedHp = 100 end, deps)
          target = foe(555, 20, 1000, 10)
          striker = wielder()
          local function swing(gap)
            now = now + gap
            return run(wrapped, striker, target)
          end
          SWING = swing
    '''

    def test_full_cadence_calls_back_once_per_streak_after_the_original(self):
        self.lua.execute(self.CADENCE + '''
          assert(module.onFullCadence == nil, 'nil by default')
          module.onFullCadence = function(c, t) calls[#calls + 1] = {c, t}; order[#order + 1] = 'callback' end
          for i = 1, 5 do SWING(1000000) end                 -- steps 0..4
          assert(#calls == 0, 'the streak is not complete yet')
          SWING(1000000)                                     -- step 5
          assert(#calls == 1 and calls[1][1] == striker and calls[1][2] == target)
          assert(order[#order] == 'callback' and order[#order - 1] == 'original', 'after the inner original')
          SWING(100000)                                      -- a combo follow-up within 350 ms
          SWING(1000000); SWING(1000000)                     -- the streak stays full
          assert(#calls == 1, 'once per streak, not per hit: ' .. #calls)
          target = foe(556, 20, 1000, 10)                    -- a new foe restarts the streak
          for i = 1, 6 do SWING(1000000) end
          assert(#calls == 2)
        ''')

    def test_a_failing_callback_never_touches_the_hit(self):
        self.lua.execute(self.CADENCE + '''
          module.onFullCadence = function() error('boom') end
          local f
          for i = 1, 6 do f = SWING(1000000) end
          assert(f.target.removedHp == 130, 'damage still scaled: ' .. f.target.removedHp)
          local errors = 0
          for _, r in ipairs(reports) do if r[1] == 'error' and r[2] == 'cadence-callback' then errors = errors + 1 end end
          assert(errors == 1)
        ''')

    # ---------- D6: Reckless (Lua state, applied by the damage formula) ----------

    RECKLESS = """
          cats = {}
          deps.category = function(unit) return cats[unit.address] end
          axeman = wielder(1, 61, {defense = 40})
          cats[61] = 'axe'
          dummy = foe(555, 20, 1000, 10)
          wrapped = module.wrap68(function(f) end, deps)
          function swing(gap, target)
            now = now + gap
            return run(wrapped, axeman, target or dummy, {}, {})
          end
          -- a foe's hit on `unit` through the damage formula: the Defense the native subtraction saw
          seenDefense = nil
          function foeHit(unit, defense, attack, action)
            now = now + 1
            local f = {power = 10, multiplier = attack or 100, modifier = defense, outcomeType = 0, action = action or 150,
                       target = {removedHp = -1}}
            local native = function(g) seenDefense = g.modifier; g.target.removedHp = g.power * (g.multiplier - g.modifier) end
            _G.IvaliceLegacyFamily = module
            damage.wrap(native, deps)(f, {type = 1, identifier = 100, address = 555}, unit)
            _G.IvaliceLegacyFamily = nil
            return f.target.removedHp, f.modifier
          end
    """

    def test_reckless_grows_per_attack_and_stops_at_a_quarter(self):
        self.lua.execute(self.RECKLESS + """
          assert(module.recklessDent(axeman, deps) == 0, 'no swing yet')
          swing(1000000); assert(math.abs(module.recklessDent(axeman, deps) - 0.05) < 1e-9)
          swing(100000); assert(math.abs(module.recklessDent(axeman, deps) - 0.05) < 1e-9, 'a combo follow-up shares its Attack')
          for i = 1, 12 do swing(1000000) end
          assert(math.abs(module.recklessDent(axeman, deps) - 0.25) < 1e-9, 'floor: -25%')
          local lines = {}
          for _, r in ipairs(reports) do if r[1] == 'reckless' then lines[#lines + 1] = r[3] end end
          assert(lines[1] == 'dent=5%' and lines[2] == 'dent=10%' and lines[5] == 'dent=25%' and #lines == 5, table.concat(lines, ','))
          assert(axeman.additiveDefense == 0 and axeman.defense == 40, 'no keep is ever written')
        """)

    def test_reckless_lowers_the_defense_a_foes_hit_meets_and_logs_it(self):
        self.lua.execute(self.RECKLESS + """
          local damage0 = foeHit(axeman, 40, 100); assert(seenDefense == 40 and damage0 == 600, 'no dent yet')
          for i = 1, 3 do swing(1000000) end                          -- 15%
          local hp, modifier = foeHit(axeman, 40, 100)
          assert(math.abs(seenDefense - 34) < 1e-9, 'defense x 0.85: ' .. tostring(seenDefense))
          assert(math.abs(hp - 10 * 66) < 1e-6)
          assert(modifier == 40, 'the field is restored for later observers')
          local hit_ = nil
          for _, r in ipairs(reports) do if r[1] == 'reckless-hit' then hit_ = r end end
          local shown = hit_ and hit_[3]:match('^def 40%->(.+)$')
          assert(hit_[2] == 1 and shown and math.abs(tonumber(shown) - 34) < 1e-6, hit_ and hit_[3])
          -- the floor rule judges the dented Defense: attack 40, defense 40 -> 34, still above 40/2 nothing; attack 20 -> floor
          local lifted = foeHit(axeman, 40, 20)
          assert(math.abs(lifted - 10 * 10) < 1e-6, 'foe on party floor attack/2: ' .. tostring(lifted))
        """)

    def test_reckless_applies_only_to_foe_hits_on_the_axe_wielder(self):
        self.lua.execute(self.RECKLESS + """
          for i = 1, 5 do swing(1000000) end
          local other = wielder(2, 62, {defense = 40}); cats[62] = 'axe'
          foeHit(other, 40); assert(seenDefense == 40, 'another axe wielder has no dent')
          cats[61] = 'sword'; foeHit(axeman, 40); assert(seenDefense == 40, 'no axe in hand, no dent')
          cats[61] = 'axe'; active = false; foeHit(axeman, 40); assert(seenDefense == 40, 'outside Legacy'); active = true
          foeHit(axeman, 40); assert(math.abs(seenDefense - 30) < 1e-9, 'the wielder itself: -25%')
          -- a party hit on a foe, or on an ally, never reads it
          now = now + 1
          local f = {power = 10, multiplier = 100, modifier = 40, outcomeType = 0, action = 150, target = {removedHp = -1}}
          _G.IvaliceLegacyFamily = module
          damage.wrap(function(g) seenDefense = g.modifier end, deps)(f, axeman, {type = 1, identifier = 100, address = 555})
          assert(seenDefense == 40, 'the wielder own hits on foes meet the foe Defense')
          damage.wrap(function(g) seenDefense = g.modifier end, deps)(f, other, axeman)
          _G.IvaliceLegacyFamily = nil
          assert(seenDefense == 40, 'a party member hitting the wielder is not a foe')
        """)

    def test_swings_at_allies_or_without_an_axe_build_no_dent(self):
        self.lua.execute(self.RECKLESS + """
          local ally = wielder(3, 63)
          swing(1000000, ally); assert(module.recklessDent(axeman, deps) == 0, 'only foes are swung at')
          cats[61] = 'sword'; swing(1000000); cats[61] = 'axe'; assert(module.recklessDent(axeman, deps) == 0)
          active = false; swing(1000000); active = true; assert(module.recklessDent(axeman, deps) == 0)
          wrapped(hit(axeman, dummy, {action = 394}), axeman, dummy, {}, {}, {})
          assert(module.recklessDent(axeman, deps) == 0, 'only the Attack')
        """)

    def test_reckless_is_forgotten_after_ten_quiet_seconds_and_on_reset(self):
        self.lua.execute(self.RECKLESS + """
          swing(1000000); swing(1000000); swing(1000000)
          assert(math.abs(module.recklessDent(axeman, deps) - 0.15) < 1e-9)
          now = now + 9000000; assert(math.abs(module.recklessDent(axeman, deps) - 0.15) < 1e-9, 'still within 10 s')
          now = now + 2000000; assert(module.recklessDent(axeman, deps) == 0, 'forgotten 10 s after the last swing')
          swing(1000000); assert(math.abs(module.recklessDent(axeman, deps) - 0.05) < 1e-9, 'a new streak restarts at 5%')
          module.reset(); assert(module.recklessDent(axeman, deps) == 0, 'map jump, load and new game clear it')
          -- the timer and the reset are Lua state only: no native handle exists to call
          assert(axeman.additiveDefense == 0)
        """)

    def test_reckless_never_raises_out_of_the_wrap_or_the_formula(self):
        self.lua.execute(self.RECKLESS + """
          local bad = {active = function() error('reader unavailable') end, category = deps.category, report = deps.report}
          local w = module.wrap68(function() return 'n' end, bad)
          local f, r = run(w, axeman, dummy, {}, {})
          assert(r[1] == 'n' and kinds():find('error', 1, true))
          -- the formula falls back to the native Defense when the Family deps are unreadable
          _G.IvaliceLegacyFamily = module
          local g = {power = 10, multiplier = 100, modifier = 40, outcomeType = 0, action = 150, target = {removedHp = -1}}
          damage.wrap(function(h) seenDefense = h.modifier end, bad)(g, {type = 1}, axeman)
          _G.IvaliceLegacyFamily = nil
          assert(seenDefense == 40)
        """)

    # ---------- D8 / D4 ----------

    GLANCING = '''
          local function native(f) f.target.removedHp = f.power * (f.multiplier - f.modifier) end
          function strike(casterType, category, attack, defense, action)
            local f = {power = 10, multiplier = attack, modifier = defense, outcomeType = 0, action = action or 150,
                       target = {removedHp = -1}}
            local d = {active = function() return active end, category = function() return category end}
            damage.wrap(native, d)(f, {type = casterType, identifier = 0}, {type = 1})
            return f.target.removedHp, f.multiplier, f.modifier
          end
    '''

    def test_glancing_floor_is_an_eighth_for_light_blades(self):
        self.lua.execute(self.GLANCING + '''
          assert(damage.GLANCING_DIVISOR == 8)
          assert(strike(0, 'dagger', 40, 100) == 10 * 5, 'dagger: attack/8')
          assert(strike(0, 'ninjaSword', 40, 100) == 10 * 5)
          assert(strike(0, 'sword', 40, 100) == 10 * 10, 'every other weapon keeps attack/4')
          assert(strike(0, 'spear', 40, 100) == 10 * 10)
          assert(strike(0, 'dagger', 60, 30) == 10 * 30, 'above the floor: Forge own')
          assert(strike(0, 'dagger', 40, 33) == 10 * 7, 'attack - defense 7 is above attack/8 = 5')
          assert(strike(0, 'sword', 40, 33) == 10 * 10, 'a sword is lifted to 10 there')
          local _, m, mod = strike(0, 'dagger', 40, 100); assert(m == 40 and mod == 100, 'fields restored')
        ''')

    def test_glancing_gates(self):
        self.lua.execute(self.GLANCING + '''
          assert(strike(0, 'dagger', 40, 100, 394) == 10 * 10, 'only the Attack')
          active = false; assert(strike(0, 'dagger', 40, 100) == 10 * 10, 'outside Legacy'); active = true
          -- a foe's dagger is native: the foe floor (half) applies against the party, the common one otherwise
          local f = {power = 10, multiplier = 40, modifier = 100, outcomeType = 0, action = 150, target = {removedHp = -1}}
          damage.wrap(function(g) g.target.removedHp = g.power * (g.multiplier - g.modifier) end,
                      {active = function() return true end, category = function() return 'dagger' end})(
                        f, {type = 1, identifier = 100}, {type = 0})
          assert(f.target.removedHp == 10 * 20, 'foe on party keeps attack/2')
          local g = {power = 10, multiplier = 40, modifier = 100, outcomeType = 0, action = 150, target = {removedHp = -1}}
          damage.wrap(function(h) h.target.removedHp = h.power * (h.multiplier - h.modifier) end)(g, {type = 0}, {type = 1})
          assert(g.target.removedHp == 10 * 10, 'no deps and no Family: the common floor')
        ''')

    def test_glancing_reads_the_registered_family_deps_without_being_handed_any(self):
        self.lua.execute('''
          event = {registerEventSync = function() return true end, registerEventAsync = function() return true end}
          local LEGACY = {[0x120000] = 'MZ', [0x120180] = 'PE\\0\\0\\x64\\x86', [0xD113D4] = 'LEGCY_\\0'}
          local wide = '%sLEGCY_%03d\\0'
          memory = {readArray = function(address)
            if address == 0xD12200 then
              local t = {} for i = 1, #wide do t[#t + 1] = wide:byte(i); t[#t + 1] = 0 end return t
            end
            return LEGACY[address]
          end}
          WEAPON_CATEGORY = 12
          bpack = {section13 = setmetatable({}, {__index = function() return {category = WEAPON_CATEGORY} end})}
          print = function() end
          assert(module.register() == true)
          _G.IvaliceLegacyFamily = module
          local f = {power = 10, multiplier = 40, modifier = 100, outcomeType = 0, action = 150, target = {removedHp = -1}}
          damage.wrap(function(g) g.target.removedHp = g.power * (g.multiplier - g.modifier) end)(
            f, {type = 0, identifier = 0, weapon = 4200}, {type = 1})
          assert(f.target.removedHp == 10 * 5, 'dagger took the Glancing floor: ' .. f.target.removedHp)
          _G.IvaliceLegacyFamily = nil
        ''')

    # ---------- D11 ----------

    LURE = '''
          cat = 'rod'
          helpers = {getStatusEffectDuration = function(a, id) return 600 end}
          local function heal(raw)
            return module.mendWrap(function(f) f.target.addedHp = raw end, deps)
          end
          HEAL = heal
          function cast(raw, missing, casterExtra)
            local caster = wielder(0, 11, casterExtra)
            local ally = wielder(1, 99, {currentHp = 1000 - missing})
            local f = run(heal(raw), caster, ally, {action = 5}, helpers)
            return f, caster
          end
    '''

    def test_an_overflowing_rod_heal_draws_lure_for_a_quarter_duration(self):
        self.lua.execute(self.LURE + '''
          local f = cast(200, 100)                     -- 240 after Mend, 100 missing: overflow
          assert(f.target.addedHp == 240)
          assert(f.caster.addedStatusEffects[17] == 1 and f.caster.statusEffectDurations[17] == 150)
          assert(kinds():find('lure', 1, true))
          f = cast(200, 300)                           -- the heal fits: no Lure
          assert(f.caster.addedStatusEffects[17] == 0 and f.caster.statusEffectDurations[17] == -1)
          f = cast(200, 240)                           -- exactly full: not an overflow
          assert(f.caster.addedStatusEffects[17] == 0)
        ''')

    def test_lure_gates(self):
        self.lua.execute(self.LURE + '''
          local immune = slots(0); immune[17] = 1
          local f = cast(200, 100, {permanentStatusEffectImmunities = immune})
          assert(f.caster.addedStatusEffects[17] == 0, 'a caster immune to Lure is left alone')
          local running = slots(0); running[17] = 1
          local dur = slots(0); dur[17] = 400
          f = cast(200, 100, {temporaryStatusEffects = running, statusEffectDurations = dur})
          assert(f.caster.addedStatusEffects[17] == 0, 'a longer Lure already running is kept')
          dur = slots(0); dur[17] = 20
          f = cast(200, 100, {temporaryStatusEffects = running, statusEffectDurations = dur})
          assert(f.caster.addedStatusEffects[17] == 1 and f.caster.statusEffectDurations[17] == 150, 'a shorter one is refreshed')
          cat = 'staff'; f = cast(200, 100); assert(f.caster.addedStatusEffects[17] == 0, 'a rod only')
          cat = 'rod'; active = false; f = cast(200, 100); assert(f.caster.addedStatusEffects[17] == 0, 'outside Legacy')
          active = true
          -- a backfire (the undead branch) is no heal
          local caster, ally = wielder(0, 11), wielder(1, 99, {currentHp = 900})
          local g = hit(caster, ally, {action = 5, reverse = 1})
          module.mendWrap(function(h) h.target.removedHp = 200 end, deps)(g, caster, ally, {}, {}, helpers)
          assert(g.caster.addedStatusEffects[17] == 0)
        ''')

    def test_a_queued_longer_lure_is_kept_and_a_broken_helper_never_stops_the_heal(self):
        self.lua.execute(self.LURE + '''
          local caster, ally = wielder(0, 11), wielder(1, 99, {currentHp = 900})
          local g = hit(caster, ally, {action = 5})
          g.caster.addedStatusEffects[17] = 1; g.caster.statusEffectDurations[17] = 400
          module.mendWrap(function(h) h.target.addedHp = 200 end, deps)(g, caster, ally, {}, {}, helpers)
          assert(g.caster.statusEffectDurations[17] == 400)
          local bad = {getStatusEffectDuration = function() error('native call failed') end}
          local f, r = run(module.mendWrap(function(h) h.target.addedHp = 200 end, deps), wielder(0, 12),
                           wielder(1, 98, {currentHp = 900}), {action = 5}, bad)
          assert(f.target.addedHp == 240, 'the heal itself is untouched')
          assert(kinds():find('error', 1, true))
        ''')


if __name__ == '__main__':
    unittest.main()
