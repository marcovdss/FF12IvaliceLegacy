"""Balance follow-up of 2026-11-17 (Marco: "Go with recomendations"), proposal balance-followup-20261117.

No game is needed for the rule tests: they load prototypes/proficiency.lua and prototypes/hybrid_proficiency.lua under
Lupa. The tip tests read patches/proficiency_tip.json and patches/families_tip.json, and rebuild them from the clean
archive when IVALICE_LEGACY_GAME is set.

Covered:
  1. Hell Knight (Hellfire) stage I also hears a physical hit the character takes; a foe's multi-hit attack is one blow.
  2. Arithmetician (Entropy): stages I and II swapped (Slow at ranks 1-4, the Stop chance from rank 5).
  3. Engineer (Field Kit) stage I: any ailment you land, no Staggered gate, heal 10/10/10/15, push from level 2, cure from 3.
  4. Leech: a drain against a foe known to have 0 MP heals the drainer's HP (weapon hit capped at 2 percent of max HP).
  5. Text: "leech" wording, the counter drain sized by your own max MP, and the Clan Primer paragraph that defines Leech.
"""
import json
import os
from pathlib import Path
import re
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
PROF_TIP = json.loads((ROOT/'patches/proficiency_tip.json').read_text(encoding='utf-8'))
FAM_TIP = json.loads((ROOT/'patches/families_tip.json').read_text(encoding='utf-8'))
RESULT_KEYS = {'list': 'menuhandbook_tutorial.bin', 'checks': 'menuhandbook_tutorial.dat',
               'pages': 'menuhandbook_tutorial000.dat', 'titles': 'penelosdiary.bin'}


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class BalanceFollowupRulesTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.P = self.lua.execute((ROOT/'prototypes/proficiency.lua').read_text(encoding='utf-8'))
        g.H = self.lua.execute((ROOT/'prototypes/hybrid_proficiency.lua').read_text(encoding='utf-8'))
        self.lua.execute(r'''
          H.attach(P)
          P.reset()
          function S(job, t_)
            local s = {job=job, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, damage=100,
                       targetStatus={}, casterStatus={}, targetMp=50, targetMaxMp=100, attackerMaxMp=100, casterHpPct=100, casterMp=50,
                       casterMaxMp=100, selfMaxHp=1000, targetHpPct=80,
                       party={{address=100, job=job, hpPct=100, mp=50, maxMp=100, status={}},
                              {address=200, job=0, hpPct=40, mp=5, maxMp=100, status={poison=true}},
                              {address=300, job=6, hpPct=90, mp=80, maxMp=100, status={}}},
                       foes={{address=901, status={}}, {address=902, status={}}},
                       charge={current=20000, required=30000, last=20000}, roll=function() return true end}
            for k, v in pairs(t_ or {}) do s[k] = v end
            return s
          end
          function idx(name) for i, l in ipairs(H.LINES) do if l.line == name then return i end end end
          function jobOf(name) return H.FIRST_ID + idx(name) - 1 end
          function stageOf(name, k) return H.LINES[idx(name)][k] end
          function others(a, op, unit, id)
            local n = 0
            for _, o in ipairs(a and a.others or {}) do
              if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then n = n + 1 end
            end
            return n
          end
          function other(a, op, unit, id)
            for _, o in ipairs(a and a.others or {}) do
              if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then return o end
            end
          end
          T0 = 9000000000
        ''')

    # ---------- 1. Hellfire stage I ----------

    def test_hellfire_stage_one_hears_a_counter_and_a_physical_hit_taken(self):
        self.lua.execute(r'''
          local st = stageOf('Hellfire', 1)
          assert(type(st[1]) == 'table' and H.hears(st[1], 'counter') and H.hears(st[1], 'struck'), 'counter and struck')
          assert(st[3] == 'A counter or a physical hit you take')
          local hk = jobOf('Hellfire')
          assert(P.rules[hk].struck and P.rules[hk].guard, 'the rule listens to the struck hook and the counter roll')
          local foe = {attacker=901, attackerStatus={protect=true, haste=true}, attackerMp=50}
          -- a physical hit you take: strips the attacker's Protect and Haste and heals 5% of max HP at rank 1
          local a = P.decide('struck', S(hk, foe), 1)
          assert(others(a, 'remove', 901, P.STATUS.protect) == 1 and others(a, 'remove', 901, P.STATUS.haste) == 1)
          assert(other(a, 'hp', 100).amount == 50, 'heals 5% of 1000')
          -- a counter swing pays the same
          P.reset()
          local c = P.decide('guard', S(hk, {outcome='counter', guardOutcome='parry', attacker=901,
                                             attackerStatus={protect=true, haste=true}}), 1)
          assert(others(c, 'remove', 901, P.STATUS.protect) == 1 and other(c, 'hp', 100).amount == 50)
          -- level 4: 8%, Immobilize, Bravery and Faith stripped
          P.reset()
          local d = P.decide('struck', S(hk, {attacker=901, attackerMp=0,
                                              attackerStatus={protect=true, haste=true, bravery=true, faith=true}}), 4)
          assert(other(d, 'hp', 100).amount == 80, '8% of 1000')
          assert(others(d, 'add', 901, P.STATUS.immobilize) == 1)
          assert(others(d, 'remove', 901, P.STATUS.bravery) == 1 and others(d, 'remove', 901, P.STATUS.faith) == 1)
          -- controls: a plain parry, a dodge and a counter roll with no swing are not heard
          P.reset()
          assert(P.decide('guard', S(hk, {outcome='parry', attacker=901, attackerStatus={protect=true}}), 1) == nil)
          assert(P.decide('guard', S(hk, {outcome='dodge', attacker=901, attackerStatus={protect=true}}), 1) == nil)
          -- no Hellfire without the stage unlocked
          assert(P.decide('struck', S(hk, foe), 0) == nil)
        ''')

    def test_a_foes_multi_hit_attack_is_one_blow_for_the_struck_reward(self):
        self.lua.execute(r'''
          local hk = jobOf('Hellfire')
          local function blow(foe, action, now)
            return P.decide('struck', S(hk, {attacker=foe, attackerStatus={protect=true}, attackerMp=50, action=action, now=now}), 1)
          end
          -- a three-hit attack: the hits reach the hook 0.2 s apart; the heal comes once, the strip with every hit
          local pays, strips = 0, 0
          for i = 0, 2 do
            local a = blow(901, 7, T0 + i * 200000)
            pays = pays + others(a, 'hp', 100)
            strips = strips + others(a, 'remove', 901, P.STATUS.protect)
          end
          assert(pays == 1, 'one heal for a three-hit attack, got ' .. pays)
          assert(strips == 3, 'the strip is not a per-action reward')
          -- the same foe\'s next action (another action id) pays again, and so does a later one past the window
          assert(others(blow(901, 8, T0 + 600000), 'hp', 100) == 1, 'another action id')
          assert(others(blow(901, 8, T0 + 600000 + H.ACTION_WINDOW + 1), 'hp', 100) == 1, 'past the action window')
          -- another foe's attack with the same action id is its own blow
          assert(others(blow(902, 8, T0 + 600000 + H.ACTION_WINDOW + 1), 'hp', 100) == 1, 'another foe')
          -- every call within the window of the call before belongs to the action: a long combo stays one blow
          P.reset()
          local total = 0
          for i = 0, 5 do total = total + others(blow(901, 9, T0 + i * 800000), 'hp', 100) end
          assert(total == 1, 'six hits 0.8 s apart are still one action, got ' .. total)
          -- control: without a clock or an action id (older callers) every hit counts, as before
          P.reset()
          local plain = 0
          for i = 1, 3 do
            plain = plain + others(P.decide('struck', S(hk, {attacker=901, attackerStatus={protect=true}, attackerMp=50}), 1), 'hp', 100)
          end
          assert(plain == 3, 'no clock: every hit pays')
        ''')

    def test_the_runtime_passes_the_clock_and_action_to_the_struck_stage(self):
        runtime = (ROOT/'prototypes/proficiency_runtime.lua').read_text(encoding='utf-8')
        self.assertIn('mine.now = clock()', runtime)
        self.assertIn('mine.action = action', runtime)
        self.assertIn('Hybrid.ACTION_WINDOW = R.ACTION_WINDOW', runtime)

    # ---------- 2. Entropy swap ----------

    def test_entropy_stage_one_slows_and_stage_two_may_stop(self):
        self.lua.execute(r'''
          local one, two = stageOf('Entropy', 1), stageOf('Entropy', 2)
          assert(one[3] == 'Repeating an element' and one[5][1][1] == 'foe' and one[5][1][2] == 'slow')
          assert(two[3] == 'The same element twice on a Slowed foe' and two[5][1][1] == 'stopChance')
          assert(#one[2] == 1 and one[2][1] == 'sameElement')
          assert(#two[2] == 2 and two[2][1] == 'slowed' and two[2][2] == 'sameElement')
          local en = jobOf('Entropy')
          local function cast(t_, rank, n)
            return P.decide('magick', S(en, t_), rank)
          end
          -- rank 4, no Slow on the foe: the first spell does nothing, the repeat Slows it at 3/4 and returns 2% MP
          P.reset()
          assert(cast({element=1, now=T0}, 4) == nil, 'the first spell does nothing')
          local a = cast({element=1, now=T0 + 5000000}, 4)
          assert(a.targetAdd[P.STATUS.slow] == 0.75, 'Slow at 3/4 at level 4')
          assert(a.targetAdd[P.STATUS.stop] == nil, 'no Stop below rank 5')
          assert(other(a, 'mp', 100) and other(a, 'mp', 100).amount == 2, '2% of 100 MP')
          -- rank 4 on a Slowed foe: still no Stop (that is stage II, rank 5)
          P.reset()
          cast({element=1, now=T0}, 4)
          local b = cast({element=1, now=T0 + 5000000, targetStatus={slow=true}}, 4)
          assert(b == nil or (b.targetAdd or {})[P.STATUS.stop] == nil, 'a Slowed foe is not Stopped at rank 4')
          -- rank 5: stage II may Stop the Slowed foe; the Slow chain of stage I still fires on an unslowed foe
          P.reset()
          cast({element=1, now=T0}, 5)
          local c = cast({element=1, now=T0 + 5000000, targetStatus={slow=true}}, 5)
          assert(c.targetAdd[P.STATUS.stop] == 0.5, 'Stop on a Slowed foe from rank 5')
          P.reset()
          cast({element=1, now=T0}, 5)
          local d = cast({element=1, now=T0 + 5000000}, 5)
          assert(d.targetAdd[P.STATUS.slow] and d.targetAdd[P.STATUS.stop] == nil, 'Slow first, the Stop needs the Slow')
          -- a different element restarts the chain
          P.reset()
          cast({element=1, now=T0}, 4)
          assert(cast({element=2, now=T0 + 5000000}, 4) == nil)
        ''')

    # ---------- 3. Field Kit stage I ----------

    def test_field_kit_stage_one_pays_on_any_ailment_and_grows_by_level(self):
        self.lua.execute(r'''
          local st = stageOf('Field Kit', 1)
          assert(st[1] == 'status' and st[2] == nil and st[3] == 'An ailment you land', 'no Staggered gate')
          local fk = jobOf('Field Kit')
          local function land(rank, t_)
            P.reset()
            return P.decide('status', S(fk, t_ or {landed={8}}), rank)
          end
          -- level 1: the heal, 10% of the damage to the most hurt ally; nothing else
          local a = land(1)
          assert(other(a, 'hp', 200).amount == 10 and others(a, 'push') == 0 and others(a, 'remove') == 0)
          -- level 2: a 10% push on the ailing foe
          a = land(2)
          assert(other(a, 'hp', 200).amount == 10 and other(a, 'push', 900).fraction == 0.1 and others(a, 'remove') == 0)
          -- level 3: the Chemist's cure of an ally's ailment
          a = land(3)
          assert(other(a, 'hp', 200).amount == 10 and others(a, 'push', 900) == 1 and others(a, 'remove', 200, P.STATUS.poison) == 1)
          -- level 4: the heal rises to 15%
          a = land(4)
          assert(other(a, 'hp', 200).amount == 15 and others(a, 'push', 900) == 1 and others(a, 'remove', 200, P.STATUS.poison) == 1)
          -- the foe need not be Staggered, and a Staggered foe changes nothing
          assert(land(4, {landed={8}, staggered=false}) ~= nil and land(4, {landed={8}, staggered=true}) ~= nil)
          -- no ailment landed: nothing (a hit with no status never reaches a status stage)
          assert(land(4, {landed={}}) == nil)
          -- the stage still hears the spell route: a status spell pays the push and the cure (no damage to share)
          P.reset()
          local ss = S(fk, {allyTarget=false, landed={8}, action=60, now=T0, castId=1}); ss.damage = nil
          local spell = P.decide('cast', ss, 4)
          assert(others(spell, 'push', 900) == 1 and others(spell, 'remove', 200, P.STATUS.poison) == 1 and others(spell, 'hp') == 0)
        ''')

    # ---------- 4. Leech ----------

    def test_a_drain_on_a_foe_with_no_mp_leeches_hp_and_a_hit_is_capped_at_two_percent(self):
        self.lua.execute(r'''
          local fs = jobOf('Foresight')   -- stage I: 'Your hits', drain 5/5/5/8
          local function hit(rank, t_)
            P.reset()
            return P.decide('hit', S(fs, t_), rank)
          end
          -- a foe with MP is drained as before: MP, no HP
          local a = hit(1, {damage=400, targetMp=50, casterMaxMp=1000})
          assert(a.drainMp == 20 and a.healHp == nil, 'MP path unchanged')
          a = hit(1, {damage=400, targetMp=50})
          assert(a.drainMp == 2 and a.healHp == nil, 'the MP cap (2% of max MP) is unchanged')
          -- a foe known to have 0 MP: the same share heals HP (5% of 400 = 20; the cap is 2% of 1000 = 20)
          a = hit(1, {damage=400, targetMp=0})
          assert(a.healHp == 20 and a.drainMp == nil, 'leech 20 HP, got ' .. tostring(a.healHp))
          -- the cap: 5% of 2000 = 100, a weapon hit leeches at most 2% of max HP
          a = hit(1, {damage=2000, targetMp=0})
          assert(a.healHp == 20, 'capped at 2% of 1000, got ' .. tostring(a.healHp))
          a = hit(1, {damage=2000, targetMp=0, selfMaxHp=5000})
          assert(a.healHp == 100, '2% of 5000')
          -- the floor: at least 1
          a = hit(1, {damage=30, targetMp=0, selfMaxHp=10})
          assert(a.healHp == 1)
          -- level 4: 8% leeches, plus the stage's own 5% heal (not capped: it is not a drain)
          a = hit(4, {damage=300, targetMp=0, selfMaxHp=2000})
          assert(a.healHp == 15 + 24, 'own heal 15 plus leech 24, got ' .. tostring(a.healHp))
          a = hit(4, {damage=3000, targetMp=0, selfMaxHp=2000})
          assert(a.healHp == 150 + 40, 'own heal 150 plus the leech capped at 40, got ' .. tostring(a.healHp))
          -- unknown MP pays nothing
          P.reset()
          local s = S(fs, {damage=400}); s.targetMp = nil
          assert(P.decide('hit', s, 1) == nil, 'unknown MP: nothing')
          -- a combo follow-up pays nothing
          assert(hit(1, {damage=400, targetMp=0, comboFollow=true}) == nil, 'a combo follow-up leeches nothing')
          -- no max HP known: a weapon hit pays nothing rather than uncapped
          P.reset()
          s = S(fs, {damage=400, targetMp=0}); s.selfMaxHp = nil
          assert(P.decide('hit', s, 1) == nil)
          -- no damage, nothing to share
          assert(hit(1, {damage=0, targetMp=0}) == nil)
        ''')

    def test_a_spell_leech_has_no_hit_cap(self):
        self.lua.execute(r'''
          local mal = jobOf('Malediction')   -- stage I: spells on an ailing foe, drain 5/5/5/8
          local function spell(rank, t_)
            P.reset()
            return P.decide('magick', S(mal, t_), rank)
          end
          -- the foe has MP: drained as MP
          local a = spell(1, {damage=1500, targetStatus={poison=true}, targetMp=500, element=1, now=T0})
          assert(a.drainMp == 75 and a.healHp == nil)
          -- the foe has none: 5% of 1500 = 75 HP, whatever 2% of max HP would be (20)
          a = spell(1, {damage=1500, targetStatus={poison=true}, targetMp=0, element=1, now=T0})
          assert(a.healHp == 75, 'uncapped on a spell, got ' .. tostring(a.healHp))
          local s = S(mal, {damage=1500, targetStatus={poison=true}, element=1, now=T0}); s.targetMp = nil
          P.reset()
          assert(P.decide('magick', s, 1) == nil, 'unknown MP: nothing')
        ''')

    def test_the_base_green_mage_leeches_a_dry_foe_too(self):
        self.lua.execute(r'''
          local gm = P.JOB.greenMage
          local function hit(rank, t_)
            P.reset()
            return P.decide('hit', S(gm, t_), rank)
          end
          local a = hit(1, {damage=400, targetMp=50, casterMaxMp=1000})
          assert(a.drainMp == 20 and a.healHp == nil, 'MP path unchanged')
          a = hit(1, {damage=400, targetMp=0})
          assert(a.healHp == 20 and a.drainMp == nil and a.leechSilence == nil, 'Mana Leech I leeches HP, got ' .. tostring(a.healHp))
          a = hit(1, {damage=2000, targetMp=0})
          assert(a.healHp == 20, 'capped at 2% of max HP')
          a = hit(4, {damage=400, targetMp=0, selfMaxHp=5000})
          assert(a.healHp == 32, 'level 4 leeches 8%: 32')
          -- a dry foe is not drained down to Silence, and the shared-drain stage II adds nothing
          a = hit(8, {damage=400, targetMp=0, selfMaxHp=5000, casterMp=100})
          assert(a.healHp == 32 and a.targetMpLoss == nil and a.drainMp == nil)
          assert(hit(1, {damage=400, targetMp=0, comboFollow=true}) == nil, 'a combo follow-up leeches nothing')
          P.reset()
          local s = S(gm, {damage=400}); s.targetMp = nil
          assert(P.decide('hit', s, 1) == nil, 'unknown MP: nothing')
        ''')

    def test_a_counter_drain_leeches_hp_from_a_dry_foe_and_keeps_the_mp_path(self):
        self.lua.execute(r'''
          local function counter(name, rank, t_)
            P.reset()
            local s = S(jobOf(name), {outcome='counter', guardOutcome='parry', attacker=901, attackerStatus={}})
            for k, v in pairs(t_ or {}) do s[k] = v end
            return P.decide('guard', s, rank)
          end
          -- Umbra I: 5% of YOUR max MP from a foe that has MP (unchanged) ...
          local a = counter('Umbra', 1, {attackerMp=30})
          assert(other(a, 'mp', 901).amount == -5 and other(a, 'mp', 100).amount == 5, 'MP path: 5% of your 100 MP')
          a = counter('Umbra', 1, {attackerMp=3})
          assert(other(a, 'mp', 100).amount == 3, 'limited by what the foe has')
          -- ... and 5% of YOUR max HP when the foe has none
          a = counter('Umbra', 1, {attackerMp=0})
          assert(other(a, 'hp', 100).amount == 50 and others(a, 'mp') == 0, 'leech 5% of 1000 HP')
          -- rank 4: the leech is 8% (80), beside the stage's own 5% heal (50) that level 2 added
          a = counter('Umbra', 4, {attackerMp=0})
          local amounts = {}
          for _, o in ipairs(a.others) do if o.op == 'hp' and o.unit == 100 then amounts[#amounts + 1] = o.amount end end
          table.sort(amounts)
          assert(#amounts == 2 and amounts[1] == 50 and amounts[2] == 80, 'rank 4: 8% leech and the own 5% heal')
        ''')

    def test_counter_leech_with_an_unknown_pool_pays_nothing(self):
        self.lua.execute(r'''
          P.reset()
          local s = S(jobOf('Umbra'), {outcome='counter', guardOutcome='parry', attacker=901, attackerStatus={}})
          s.attackerMp = nil
          local a = P.decide('guard', s, 1)
          assert(a == nil or (others(a, 'mp') == 0 and others(a, 'hp') == 0), 'unknown pool: no MP, no HP')
        ''')

    def test_snare_abyss_and_last_stand_counter_drains_leech_a_dry_foe(self):
        self.lua.execute(r'''
          -- Snare II level 3 (rank 7): struck while you have Lure
          local s = S(jobOf('Snare'), {attacker=901, attackerStatus={}, casterStatus={lure=true}, attackerMp=0}); P.reset()
          local a = P.decide('struck', s, 7)
          assert(other(a, 'hp', 100) and other(a, 'hp', 100).amount == 50, 'Snare II-3 leeches 5% of max HP')
          s = S(jobOf('Snare'), {attacker=901, attackerStatus={}, casterStatus={lure=true}, attackerMp=40}); P.reset()
          a = P.decide('struck', s, 7)
          assert(other(a, 'mp', 100).amount == 5 and other(a, 'hp', 100) == nil, 'with MP: the MP path')
          -- Abyss I level 3 (rank 3): a counter
          s = S(jobOf('Abyss'), {outcome='counter', guardOutcome='parry', attacker=901, attackerStatus={}, attackerMp=0}); P.reset()
          a = P.decide('guard', s, 3)
          assert(other(a, 'hp', 100) and others(a, 'mp') == 0, 'Abyss I-3')
          -- Last Stand I level 3 (rank 3): struck below half HP
          s = S(jobOf('Last Stand'), {attacker=901, attackerStatus={}, casterHpPct=40, attackerMp=0}); P.reset()
          a = P.decide('struck', s, 3)
          assert(others(a, 'hp', 100) == 1 and others(a, 'mp') == 0, 'Last Stand I-3')
          s = S(jobOf('Last Stand'), {attacker=901, attackerStatus={}, casterHpPct=40, attackerMp=40}); P.reset()
          a = P.decide('struck', s, 3)
          assert(other(a, 'mp', 100).amount == 5 and others(a, 'hp', 100) == 0)
        ''')

    # ---------- review fixes: per-stage paid flags, drainFoe per action, action identity, counters, dry needs a pool ----------

    def test_a_hit_that_does_not_fire_a_stage_holds_nothing_back_for_the_next_hit_of_the_action(self):
        self.lua.execute(r'''
          local ls = jobOf('Last Stand')
          -- Last Stand I level 3 (rank 3): below half HP. Hit 1 at 60 percent does not fire; hit 2 at 45 percent of the same
          -- foe action must still pay Lure and Bravery (they were lost when the first hit set the latch).
          local function hit(hp, now) return P.decide('struck', S(ls, {attacker=901, attackerStatus={}, attackerMp=0,
              casterHpPct=hp, action=7, now=now}), 3) end
          assert(hit(60, T0) == nil, 'hit 1 does not fire')
          local a = hit(45, T0 + 300000)
          assert(others(a, 'add', 100, P.STATUS.lure) == 1 and others(a, 'add', 100, P.STATUS.bravery) == 1,
                 'hit 2 pays Lure and Bravery')
          assert(others(a, 'push', 901) == 1 and others(a, 'hp', 100) == 1, 'and the push and the leech')
          -- hit 3 of the same action: the per-action rewards were paid, the push (a per-hit effect) is not
          local b = hit(40, T0 + 600000)
          assert(others(b, 'add', 100) == 0 and others(b, 'hp', 100) == 0, 'the per-action rewards come once')
          assert(others(b, 'push', 901) == 1)
          -- a critical-drop stage on hit 2: Last Stand III (rank 9) gives the party Bravery when a hit drops you
          P.reset()
          local function drop(dropped, now) return P.decide('struck', S(ls, {attacker=901, attackerStatus={}, attackerMp=0,
              dropCritical=dropped, action=8, now=now}), 9) end
          assert(drop(false, T0) == nil)
          local c = drop(true, T0 + 300000)
          assert(c and others(c, 'add', nil, P.STATUS.bravery) == 3, 'the dropping hit pays although it is hit 2')
          local d = drop(true, T0 + 600000)
          assert(d == nil or others(d, 'add', nil, P.STATUS.bravery) == 0, 'a later dropping hit of the same action pays nothing more')
        ''')

    def test_a_leech_heal_and_mp_theft_on_a_struck_combo_pay_once(self):
        self.lua.execute(r'''
          local ls = jobOf('Last Stand')   -- level 4: drainFoe 8 percent
          local function combo(attackerMp, id)
            P.reset()
            local hp, mp = 0, 0
            for i = 0, 2 do
              local a = P.decide('struck', S(ls, {attacker=901, attackerStatus={}, attackerMp=attackerMp, casterHpPct=40,
                  casterMaxMp=1000, actionId=id, now=T0 + i * 200000}), 4)
              hp = hp + others(a, 'hp', 100); mp = mp + others(a, 'mp', 100)
            end
            return hp, mp
          end
          local hp, mp = combo(0, 1)
          assert(hp == 1 and mp == 0, 'a three-hit combo on a dry foe leeches once, got ' .. hp)
          hp, mp = combo(500, 1)
          assert(hp == 0 and mp == 1, 'and takes the MP once, got ' .. mp)
        ''')

    def test_the_action_id_tells_one_foe_repeating_an_action_apart(self):
        self.lua.execute(r'''
          local hk = jobOf('Hellfire')
          local function blow(id, now)
            return P.decide('struck', S(hk, {attacker=901, attackerStatus={protect=true}, attackerMp=50, action=7, actionId=id,
                now=now}), 1)
          end
          -- the same foe, the same action id, 0.1 s apart, but distinct acting records: three blows, each pays once
          local pays = 0
          pays = pays + others(blow(11, T0), 'hp', 100)
          pays = pays + others(blow(11, T0 + 100000), 'hp', 100)
          pays = pays + others(blow(12, T0 + 300000), 'hp', 100)
          pays = pays + others(blow(12, T0 + 400000), 'hp', 100)
          pays = pays + others(blow(13, T0 + 500000), 'hp', 100)
          assert(pays == 3, 'three actions pay three times, got ' .. pays)
          -- a long action is one blow however long it takes (the record is the identity; the window re-arms with each hit)
          P.reset()
          local total = 0
          for i = 0, 4 do total = total + others(blow(21, T0 + i * 800000), 'hp', 100) end
          assert(total == 1, 'one record, one payout, got ' .. total)
          -- the same id seen again after the action window (from the hit before) starts a new action
          assert(others(blow(21, T0 + 4 * 800000 + H.ACTION_WINDOW + 1), 'hp', 100) == 1, 'a stale id expires')
          assert(others(blow(21, T0 + 4 * 800000 + H.ACTION_WINDOW + 2), 'hp', 100) == 0, 'and holds the new action again')
          -- another foe with the same record id is not the same action
          assert(others(P.decide('struck', S(hk, {attacker=902, attackerStatus={}, attackerMp=50, action=7, actionId=21,
              now=T0 + 20000000}), 1), 'hp', 100) == 1)
        ''')

    def test_a_counter_and_a_hit_of_one_foe_action_pay_hellfire_once(self):
        self.lua.execute(r'''
          local hk = jobOf('Hellfire')
          local function counter(id, now)
            return P.decide('guard', S(hk, {outcome='counter', guardOutcome='parry', attacker=901, attackerStatus={protect=true},
                action=7, actionId=id, now=now or T0}), 1)
          end
          local function struck(id)
            return P.decide('struck', S(hk, {attacker=901, attackerStatus={protect=true}, attackerMp=50, action=7, actionId=id,
                now=T0 + 100000}), 1)
          end
          -- the parried hit is countered and paid; the next hit of the same action lands and pays no second heal
          local c, s = counter(31), struck(31)
          assert(others(c, 'hp', 100) == 1 and others(s, 'hp', 100) == 0, 'counter then struck: one heal')
          assert(others(s, 'remove', 901, P.STATUS.protect) == 1, 'the strip is per hit')
          -- the other way round
          P.reset()
          s, c = struck(32), counter(32, T0 + 200000)
          assert(others(s, 'hp', 100) == 1 and others(c, 'hp', 100) == 0, 'struck then counter: one heal')
          -- the next action pays again
          assert(others(counter(33), 'hp', 100) == 1)
        ''')

    def test_a_unit_with_no_mp_pool_is_never_dry(self):
        self.lua.execute(r'''
          local fs, gm = jobOf('Foresight'), P.JOB.greenMage
          for _, job in ipairs({fs, gm}) do
            P.reset()
            local a = P.decide('hit', S(job, {damage=400, targetMp=0, targetMaxMp=0}), 1)
            assert(a == nil or a.healHp == nil, 'max MP 0: no leech')
            P.reset()
            local s = S(job, {damage=400, targetMp=0}); s.targetMaxMp = nil
            a = P.decide('hit', s, 1)
            assert(a == nil or a.healHp == nil, 'unknown max MP: no leech')
            P.reset()
            a = P.decide('hit', S(job, {damage=400, targetMp=0, targetMaxMp=100}), 1)
            assert(a.healHp == 20, 'a real pool at 0 is dry')
          end
          P.reset()
          local um = P.decide('guard', S(jobOf('Umbra'), {outcome='counter', guardOutcome='parry', attacker=901,
              attackerStatus={}, attackerMp=0, attackerMaxMp=0}), 1)
          assert(um == nil or (others(um, 'hp') == 0 and others(um, 'mp') == 0), 'a counter drain on max MP 0 pays nothing')
        ''')

    # ---------- 5. Text ----------

    def gear(self, name, k, level):
        return self.lua.eval(f'P.GEAR[jobOf("{name}")][{k}][{level}]')

    def test_stage_texts_say_leech(self):
        self.assertEqual(self.gear('Foresight', 1, 1), 'Your hits leech 5% of the damage')
        self.assertEqual(self.gear('Malediction', 1, 4),
                         'Spells on a foe ailing or Broken leech 8% of the damage,\n'
                         'inflict Poison and heal the most hurt ally for 10% of the damage')
        self.assertEqual(self.gear('Umbra', 1, 1), 'Each counter leeches 5% of your max MP from the foe')
        self.assertEqual(self.gear('Snare', 2, 4),
                         'Taking a physical hit while you have Lure inflicts Blind on the\n'
                         'foe at 3/4 duration, strips its Bravery and leeches 5% of your max MP from it')
        self.assertEqual(self.gear('Snare', 2, 3),
                         'Taking a physical hit while you have Lure inflicts Blind on the\n'
                         'foe at half duration, strips its Bravery and leeches 5% of your max MP from it')
        self.assertEqual(self.gear('Abyss', 1, 3),
                         'Each counter gives you Lure at 3/4 duration,\n'
                         'heals you for 5% of max HP and leeches 5% of your max MP from the foe')
        self.assertEqual(self.gear('Abyss', 1, 4),
                         'Each counter gives you Lure at 3/4 duration,\n'
                         'heals you for 8% of max HP and leeches 5% of your max MP from the foe')
        self.assertEqual(self.gear('Last Stand', 1, 3),
                         'Taking a physical hit below half HP gives you Lure\n'
                         'and Bravery, pushes the foe back 10% and leeches 5% of your max MP from it')
        self.assertEqual(self.gear('Last Stand', 1, 4),
                         'Taking a physical hit below half HP gives you Lure\n'
                         'and Bravery, pushes the foe back 10% and leeches 8% of your max MP from it')
        self.assertEqual(self.gear('Frenzy', 1, 3),
                         'While Brave, your hits ignore parry, block and dodge,\n'
                         'leech 5% of the damage and give the most hurt ally Bravery')

    def test_no_text_still_says_drain_of_the_foes_max_mp_or_drain_as_mp(self):
        source = (ROOT/'prototypes/hybrid_proficiency.lua').read_text(encoding='utf-8')
        self.assertNotIn("of its max MP", source)
        self.assertNotIn("of max MP from the foe", source)
        every = self.lua.execute(r'''
          local out = {}
          for i, line in ipairs(H.LINES) do
            for k = 1, 3 do for L = 1, 4 do out[#out + 1] = P.GEAR[H.FIRST_ID + i - 1][k][L] end end
          end
          return table.concat(out, '\n')
        ''')
        self.assertNotIn('max MP from the foe\n', every.replace('your max MP from the foe', ''))
        self.assertNotRegex(every, r'drains? \d+% of')
        base = self.lua.eval('P.GEAR[P.JOB.greenMage][1][1]')
        self.assertEqual(base, 'Hits leech 5% of the damage as MP, up to 2% of your max MP')

    def test_hellfire_texts(self):
        self.assertEqual(self.gear('Hellfire', 1, 1),
                         "A counter or a physical hit you take strips the foe's Protect\n"
                         "and Haste and heals you for 5% of max HP")
        self.assertEqual(self.gear('Hellfire', 1, 2),
                         "A counter or a physical hit you take strips the foe's Protect\n"
                         "and Haste, heals you for 5% of max HP and inflicts Immobilize on it")
        self.assertEqual(self.gear('Hellfire', 1, 3),
                         'A counter or a physical hit you take Immobilizes the foe,\n'
                         'strips its Protect, Haste, Bravery and Faith and heals you for 5% of max HP')
        self.assertEqual(self.gear('Hellfire', 1, 4),
                         'A counter or a physical hit you take Immobilizes the foe,\n'
                         'strips its Protect, Haste, Bravery and Faith and heals you for 8% of max HP')

    def test_entropy_and_field_kit_texts(self):
        self.assertEqual(self.gear('Entropy', 1, 1), 'Repeating an element inflicts Slow at half duration')
        self.assertEqual(self.gear('Entropy', 1, 4),
                         "Repeating an element inflicts Slow at 3/4 duration,\n"
                         "restores 2% of max MP and extends the foe's Slow")
        self.assertEqual(self.gear('Entropy', 2, 1),
                         'The same element twice on a Slowed foe has a 20% chance to Stop\nit')
        self.assertEqual(self.gear('Entropy', 2, 4),
                         'The same element twice on a Slowed foe has a 30% chance to Stop\n'
                         'it, makes your next action 15% faster and restores 3% of max MP')
        self.assertEqual(self.gear('Field Kit', 1, 1),
                         'An ailment you land heals the most hurt ally for 10% of the\ndamage')
        self.assertEqual(self.gear('Field Kit', 1, 2),
                         "An ailment you land heals the most hurt ally for 10% of the\n"
                         "damage and pushes the foe's charge back 10%")
        self.assertEqual(self.gear('Field Kit', 1, 3),
                         "An ailment you land heals the most hurt ally for 10% of the\n"
                         "damage, pushes the foe's charge back 10% and cures an ally's ailment")
        self.assertEqual(self.gear('Field Kit', 1, 4),
                         "An ailment you land heals the most hurt ally for 15% of the\n"
                         "damage, pushes the foe's charge back 10% and cures an ally's ailment")

    def test_every_line_still_produces_text_at_every_level_and_nothing_is_unspoken(self):
        n, empty, unspoken, dupes = self.lua.execute(r'''
          local n, empty, seen, dupes = 0, 0, {}, 0
          for i, line in ipairs(H.LINES) do
            for k = 1, 3 do
              local st = line[k]
              local key = table.concat(type(st[1]) == 'table' and st[1] or {st[1]}, '+') .. '|' .. table.concat(st[2] or {}, '+')
                .. '|' .. tostring(st[5][1][1]) .. '|' .. tostring(st[5][1][2])
              if seen[key] then dupes = dupes + 1 end
              seen[key] = true
              for L = 1, 4 do
                n = n + 1
                local t = H.stageText(st, L, H.firstLimit(line.line))
                if type(t) ~= 'string' or t == '' then empty = empty + 1 end
              end
            end
          end
          return n, empty, #H.unspoken(), dupes
        ''')
        self.assertEqual((n, empty, unspoken), (792, 0, 0))     # 66 lines x 3 stages x 4 levels
        self.assertEqual(dupes, 0, 'two stages share a trigger and a first effect')

    def test_the_proficiency_version_moved(self):
        self.assertEqual(self.lua.eval('P.version'), '0.5.9')


class ClanPrimerLeechTest(unittest.TestCase):
    PARAGRAPH = ('{y}Leech{/} takes MP from a foe you hit, or from the foe\n'
                 'that attacked you. Once that foe has no MP left, the\n'
                 'same leech heals your HP instead. A weapon hit you land\n'
                 'leeches at most 2 percent of your max MP or max HP.\n'
                 'A counter or a hit you take leeches the share that\n'
                 'its stage names, which can be more.')

    def test_the_proficiency_tip_defines_leech_in_the_tip_font(self):
        body = PROF_TIP['tip']['body']
        self.assertTrue(body.endswith(self.PARAGRAPH), 'the Leech paragraph closes the Proficiency tip')
        self.assertEqual(body.count('Leech'), 1)
        for glyph in '%()+/;':
            self.assertNotIn(glyph, self.PARAGRAPH.replace('{/}', ''))
        self.assertIn('2 percent', self.PARAGRAPH)
        from ivalice_legacy import clan_primer as C
        C.check_tip(PROF_TIP['tip'])
        for line in C.visible(PROF_TIP['tip']['body']).split('\n'):
            self.assertLessEqual(len(line), 56, line)
        self.assertEqual(C.decode(C.encode(body, markup=True)), body)
        self.assertEqual(C.visible(body).split('\n')[-6].split(' ')[0], 'Leech')
        self.assertIn('A counter or a hit you take leeches the share that', body)

    def test_the_tip_pins_follow_the_chain(self):
        # families builds on the Proficiency tip's output: its source pins are that tip's result pins
        for key, source in (('list', 'listSha256'), ('checks', 'checksSha256'), ('pages', 'pagesSha256'),
                            ('titles', 'titlesSha256')):
            self.assertEqual(FAM_TIP[source], PROF_TIP['resultSha256'][key], key)
        self.assertEqual(PROF_TIP['resultSha256']['pages'], 'b3a96609d38a24ddea0a0b6721e541021aa40a622018278723fc72faa0291402')
        self.assertEqual(FAM_TIP['resultSha256']['pages'], '8d2581bd9d6bee7f46d4e23304fcb7374f489269326d84eb68625d27edd28055')
        self.assertEqual(FAM_TIP['version'], '0.1.17')

    def test_the_replaced_pages_hashes_stay_accepted(self):
        import test_clan_primer as TC
        for old in ('2533c49f7b497e071d4b97ed521e1f2e92d95760c31cbed55744103b48da1c1b',
                    'f7196152ff5ec94895d02c43bf17bf19bb3fdd9caa9b0a20e7d71ba49b8e4645',
                    '084cfca4644fb2becaa7237c594e9c5ce4f53dc6e597688dad9796a6a4a79212',
                    '00ec8cc4ef1fbbf6b3d7653eefbeacecf9e4ecd44855e73b5446d9beb426817f',
                    '384cd2b295682a4e244ada49696f91dacf8704360a3fcb02c6248bb2ed0d9d8e',
                    'c1a37783887aef16f943de05285805e7d318f874f20e587895430b8a6c1f4a55',
                    'd22caacf96b458f12da6d42554372ddeb1cc25c73c27052c49e45860d7fb5a98',
                    '2432436d0eca5b2f06981a243f07005ec73358a20b675aa6e6876a0f47536af9',
                    '0894f8fa3fbf7ab45fba841bdbbff8d8e60ce1cbc14a3ea6c2727d73a6cb093a'):
            self.assertIn(old, TC.PREVIOUS_PAGES)
        self.assertNotIn(PROF_TIP['resultSha256']['pages'], TC.PREVIOUS_PAGES)
        self.assertNotIn(FAM_TIP['resultSha256']['pages'], TC.PREVIOUS_PAGES)

    @unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME to rebuild from the clean archive')
    def test_both_patches_rebuild_byte_identically_from_the_clean_archive(self):
        from ivalice_legacy import clan_primer as C
        from ivalice_legacy.vbf import VBF
        archive = VBF(Path(os.environ['IVALICE_LEGACY_GAME'])/'FFXII_TZA.vbf')
        first = C.build_files(archive.read, PROF_TIP)
        for key, name in RESULT_KEYS.items():
            data = next(v for rel, v in first.items() if rel.endswith(name))
            self.assertEqual(C.sha(data), PROF_TIP['resultSha256'][key], key)
        second = C.build_files(lambda rel: first[C.DEPLOY + rel], FAM_TIP)
        for key, name in RESULT_KEYS.items():
            data = next(v for rel, v in second.items() if rel.endswith(name))
            self.assertEqual(C.sha(data), FAM_TIP['resultSha256'][key], key)


if __name__ == '__main__':
    unittest.main()
