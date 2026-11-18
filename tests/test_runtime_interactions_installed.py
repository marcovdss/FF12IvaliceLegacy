"""Cross-system behaviour of the INSTALLED Proficiency runtime (the composed file), replayed through real
Forge hook calls (R.hook165, 170, 110, 240, 182, 222) against a synthetic battle (runtime_world.py).

Covers what single-line tests rarely do: the pushback budget across several sources, the MP give-back cap across a
cast's targets and items, Haste and Bravery self-grants, immunities and never shortening a grant, once-per-cast rewards,
and a full Cadence streak counting as a counter for every counter stage.

Skips without IVALICE_LEGACY_GAME (see deployed_scripts.py).
"""
import unittest

import deployed_scripts as D
import runtime_world

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

NEEDS = ('proficiency', 'proficiency_runtime', 'hybrid_proficiency', 'mp_economy')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
@unittest.skipUnless(D.have(*NEEDS), D.SKIP_REASON)
class InstalledRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        runtime_world.world(self.lua)

    def run_lua(self, code):
        return self.lua.execute(code)

    # ---------- the pushback budget ----------

    def test_pushback_budget_holds_across_gunner_knight_and_mystic_on_one_foe(self):
        self.run_lua(r'''
          assert(R.PUSH_SHARE == 0.25 and R.PUSH_SHARE_TOUGH == 0.125)
          PARTY_IDS = {1, 2, 3}
          local function scenario(name, share)
            R.resetPushes()
            local foe = unit({type=1, address=900, name=name, charge=24000, required=30000, hp=9000, maxHp=9000})
            local w = works[900]
            local function settle() w.lastChargeTime = w.currentChargeTime end   -- the game's own tick
            local knight = party(1, {job1=P.JOB.knight, status={}})
            local gunner = party(2, {job1=P.JOB.gunner, weapon=0x1000 + 5})
            local mystic = party(3, {job1=P.JOB.mystic, weapon=0x1000 + 6})
            bpack.section13[5] = {category=9, chargeTime=50}
            bpack.section13[6] = {category=6, chargeTime=30}
            local start = w.currentChargeTime
            -- 1. a Knight parry (stage I-4 pushes the attacker back 10%)
            local f = F({outcome=2, target={status={}}})
            R.hook240(f, foe, knight, classes, helpers); settle()
            -- 2. three Gunner shots on the Slowed foe, each asking for far more than the budget left
            foe.temporaryStatusEffects.slow = 1
            for i = 1, 3 do
              local hit = F({initial=900, target={removedHp=200, identifier=9, work=w, status={slow=true}}, caster={status={}}})
              R.hook165(hit, gunner, foe, classes, helpers); settle()
            end
            -- 3. a Mystic counter (stage I-3 pushes the attacker back 10%)
            local c = F({outcome=2, target={status={}}})
            R.hook240(c, foe, mystic, classes, helpers); settle()
            local spent = start - w.currentChargeTime
            assert(spent <= math.floor(30000 * share), name .. ' lost ' .. spent .. ' of ' .. 30000 * share)
            assert(spent >= math.floor(30000 * share) - 1, name .. ' budget not used: ' .. spent)
            -- everything is spent: a later push does nothing
            local before = w.currentChargeTime
            R.hook240(F({outcome=2, target={status={}}}), foe, knight, classes, helpers)
            assert(w.currentChargeTime == before, 'a spent budget pushed again')
            -- a counterattack landing while the charge refills does not hand out a new budget
            R.hook165(F({caster={status={}}, target={removedHp=0, status={}}}), foe, party(1, {job1=P.JOB.knight}), classes, helpers)
            w.currentChargeTime = before + 500; settle()
            R.hook240(F({outcome=2, target={status={}}}), foe, knight, classes, helpers)
            assert(w.currentChargeTime == before + 500, 'a counter reset the budget')
            -- the foe really acts (charge back to 0): a new budget
            w.currentChargeTime, w.lastChargeTime = 0, 0
            R.hook165(F({caster={status={}}, target={removedHp=5, status={}}}), foe, party(1, {job1=P.JOB.knight}), classes, helpers)
            w.currentChargeTime, w.lastChargeTime = 24000, 24000
            R.hook240(F({outcome=2, target={status={}}}), foe, knight, classes, helpers)
            assert(w.currentChargeTime < 24000, 'no new budget after the foe acted')
            return spent
          end
          scenario('imp', 0.25)
          scenario('bossy', 0.125)
          scenario('marky', 0.125)
          scenario('raregame', 0.125)
        ''')


    # ---------- the MP give-back cap ----------

    def test_mp_return_is_half_the_cast_across_every_target_and_unpaid_for_items(self):
        self.run_lua(r"""
          PARTY_IDS = {1, 2, 3, 4}
          bpack.section14[77] = {category=1, mpOrMistCost=40, elements=flags()}      -- a healing spell, 40 MP listed
          local function curaga(caster, hp)
            local allies = {}
            for i = 2, 4 do allies[#allies + 1] = party(i, {hp=1000, maxHp=1000}) end
            allies[#allies + 1] = party(5, {hp=1000, maxHp=1000})
            local first = allies[1]
            for n, ally in ipairs(allies) do
              local f = F({action=77, initial=first.address, target={addedHp=600}})
              R.hook222(f, caster, ally)                                        -- Forge 222 starts the cast on its first target
              R.hook110(f, caster, ally, classes, helpers)
            end
          end
          -- White Mage I-3 returns 5% of each overflow as MP: 30 per target, four targets, against a cap of 20
          local wm = party(1, {job1=P.JOB.whiteMage, magick=30, maxMp=500, mp=100})
          reset(); curaga(wm)
          assert(mpGain() == 20, 'cast cap: got ' .. mpGain())                     -- floor(40 x 1.0 x 1/2)
          -- the cap follows the Magick-scaled price (x2 at Magick 60) ...
          local wm2 = party(1, {job1=P.JOB.whiteMage, magick=60, maxMp=500, mp=100})
          reset(); R.resetBudgets(); curaga(wm2)
          assert(mpGain() == 40, 'scaled cap: got ' .. mpGain())
          -- ... and what the game really charged: Parsimony halves the price, so the cap halves too
          local wm3 = party(1, {job1=P.JOB.whiteMage, magick=30, maxMp=500, mp=100, augments={parsimony=true}})
          reset(); R.resetBudgets(); curaga(wm3)
          assert(mpGain() == 10, 'Parsimony cap: got ' .. mpGain())
          -- a new cast gets a new budget
          reset(); curaga(wm3)
          assert(mpGain() == 10, 'second cast: got ' .. mpGain())
          -- a hybrid line adds its own returns to the same budget: Seer (Green Mage + White Mage) heals for MP
          local seer = party(1, {job1=P.JOB.greenMage, job2=P.JOB.whiteMage, magick=30, maxMp=500, mp=100})
          reset(); R.resetBudgets(); curaga(seer)
          assert(mpGain() <= 20, 'hybrid over the cap: ' .. mpGain())
        """)

    def test_item_heals_are_not_capped_and_only_item_stages_return_mp(self):
        self.run_lua(r"""
          PARTY_IDS = {1, 2}
          -- White Mage: a Potion past full HP gives Regen but never MP (items cost gil, not MP)
          local wm = party(1, {job1=P.JOB.whiteMage, maxMp=500, mp=100})
          local ally = party(2, {hp=1000, maxHp=1000})
          reset()
          for i = 1, 6 do R.hook127(F({action=1, target={addedHp=500}}), wm, ally, classes, helpers) end
          assert(mpGain() == 0, 'a Potion returned MP: ' .. mpGain())
          -- Ronin (Samurai + Chemist) stage I is about items: 3-5% of max MP per item, with no cast budget
          local ronin = party(1, {job1=P.JOB.samurai, job2=P.JOB.chemist, maxMp=500, mp=10})
          reset()
          for i = 1, 6 do R.hook127(F({action=1, target={addedHp=500}}), ronin, ally, classes, helpers) end
          assert(mpGain() == 6 * 25, 'item stage MP: ' .. mpGain())            -- 5% of 500 at level 4, six items
        """)


    # ---------- Haste and Bravery self-grants ----------

    def test_zanshin_and_fresh_hybrid_haste_apply_only_when_not_hasted(self):
        self.run_lua(r"""
          PARTY_IDS = {1}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local function combo(sam, status)
            reset()
            local first = F({initial=900, target={removedHp=50, identifier=9, work=works[900]}, caster={status=status}})
            R.hook165(first, sam, foe, classes, helpers)
            NOW = NOW + 150000                                              -- the follow-up lands 0.15 s later: a combo
            local second = F({initial=900, target={removedHp=50, identifier=9, work=works[900]}, caster={status=status}})
            R.hook165(second, sam, foe, classes, helpers)
            NOW = NOW + 20000000
            return first, second
          end
          local sam = party(1, {job1=P.JOB.samurai})
          local a, b = combo(sam, {})
          assert(b.caster.addedStatusEffects[STATUS.haste] == 1 and b.caster.statusEffectDurations[STATUS.haste] == 75,
                 'Zanshin I-3 grants Haste (3/4)')
          P.reset()
          local c, d = combo(sam, {haste=true})
          assert(d.caster.addedStatusEffects[STATUS.haste] == 0, 'Zanshin I regranted Haste to a Hasted Samurai')
          assert(d.target.removedHp == 50, 'the stage-4 bonus must also wait for the grant')
          -- a hybrid whose frequent self-Haste is fresh-only: Sohei (Samurai + White Mage), healing an ally
          local sohei = party(1, {job1=P.JOB.samurai, job2=P.JOB.whiteMage})
          local ally = party(2, {hp=500, maxHp=1000})
          P.reset()
          local f = F({action=77, caster={status={}}, target={addedHp=100}})
          R.hook110(f, sohei, ally, classes, helpers)
          assert(f.caster.addedStatusEffects[STATUS.haste] == 1, 'Kaji I Haste')
          P.reset()
          f = F({action=77, caster={status={haste=true}}, target={addedHp=100}})
          R.hook110(f, sohei, ally, classes, helpers)
          assert(f.caster.addedStatusEffects[STATUS.haste] == 0, 'Kaji I regranted Haste')
        """)

    def test_a_shorter_grant_never_cuts_a_running_haste_or_bravery(self):
        self.run_lua(r"""
          PARTY_IDS = {1}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          -- Mystic III-3 gives Haste (half) on every counter; the counter-swing outcome is 2 (parry) or 4 (block)
          local function counter(mystic)
            reset()
            R.hook240(F({outcome=2, target={status={}}}), foe, mystic, classes, helpers)
          end
          local mystic = party(1, {job1=P.JOB.mystic, has={[STATUS.haste]=90}})
          counter(mystic)
          for _, a in ipairs(LOG.added) do assert(a.id ~= STATUS.haste, 'a 50 grant replaced a running 90') end
          mystic.statusEffectDurations[STATUS.haste] = 20
          counter(mystic)
          local renewed
          for _, a in ipairs(LOG.added) do if a.id == STATUS.haste then renewed = a.d end end
          assert(renewed == 50, 'a longer grant renews: ' .. tostring(renewed))
          mystic.statusEffectDurations[STATUS.haste] = 20
          -- repeated counters never stack past the grant: ten counters leave Haste at one half-duration
          local lone = party(1, {job1=P.JOB.mystic})
          for i = 1, 10 do counter(lone) end
          assert(lone.statusEffectDurations[STATUS.haste] == 50, lone.statusEffectDurations[STATUS.haste])
          -- Warrior II: a Brave Warrior below half HP is not granted Bravery again
          local warrior = party(1, {job1=P.JOB.warrior, hp=300, maxHp=1000, has={[STATUS.bravery]=80}})
          local f = F({initial=900, target={removedHp=10, identifier=9, work=works[900]}, caster={status={bravery=true}}})
          reset(); R.hook165(f, warrior, foe, classes, helpers)
          assert(f.caster.addedStatusEffects[STATUS.bravery] == 0, 'Cleave II regranted Bravery')
          local g = F({initial=900, target={removedHp=10, identifier=9, work=works[900]}, caster={status={}}})
          local w2 = party(1, {job1=P.JOB.warrior, hp=300, maxHp=1000})
          reset(); R.hook165(g, w2, foe, classes, helpers)
          assert(g.caster.addedStatusEffects[STATUS.bravery] == 1 and g.caster.statusEffectDurations[STATUS.bravery] == 100,
                 'Cleave II grants Bravery below half HP')
        """)

    # ---------- immunities ----------

    def test_granted_statuses_respect_foe_immunities_and_ward_augments(self):
        self.run_lua(r"""
          PARTY_IDS = {1}
          local lancer = party(1, {job1=P.JOB.lancer})
          local function strike(foeSpec)
            local foe = unit(foeSpec)
            local f = F({initial=foe.address, target={removedHp=50, identifier=9, work=works[foe.address]}, caster={status={}}})
            reset(); R.hook165(f, lancer, foe, classes, helpers)
            return f
          end
          -- Lancer III (a buff-less foe is Immobilized): the foe lands it, an immune foe does not
          local f = strike({type=1, address=900, hp=9000, maxHp=9000})
          assert(f.target.addedStatusEffects[STATUS.immobilize] == 1, 'Grounding III-1 Immobilize')
          f = strike({type=1, address=901, hp=9000, maxHp=9000, immune={STATUS.immobilize}})
          assert(f.target.addedStatusEffects[STATUS.immobilize] == 0, 'Immobilize on an immune foe')
          f = strike({type=1, address=902, hp=9000, maxHp=9000, augments={statusImmunity=true}})
          assert(f.target.addedStatusEffects[STATUS.immobilize] == 0, 'Immobilize past a status-immunity ward')
          -- the same checks guard the helper route: Pursuit III-3 slows the most recently engaged other foe on a kill
          local hunter = party(1, {job1=P.JOB.hunter})
          local function kill(otherSpec)
            local other = unit(otherSpec)
            R.seeFoe(901); NOW = NOW + 1000; R.seeFoe(other.address)
            local prey = unit({type=1, address=900, hp=100, maxHp=9000, status={poison=true}})
            local f2 = F({initial=900, target={removedHp=9000, identifier=9, work=works[900], status={poison=true}}, caster={status={}}})
            reset(); R.hook165(f2, hunter, prey, classes, helpers)
            local slowed
            for _, add in ipairs(LOG.added) do if add.unit == other.address and add.id == STATUS.slow then slowed = true end end
            return slowed
          end
          assert(kill({type=1, address=912, hp=9000, maxHp=9000}), 'control: the Slow is given')
          assert(not kill({type=1, address=910, hp=9000, maxHp=9000, immune={STATUS.slow}}), 'Slow reached an immune foe')
        """)


    # ---------- once per cast ----------

    def test_black_mage_rewards_once_per_cast_on_a_three_foe_spell(self):
        self.run_lua(r"""
          PARTY_IDS = {1}
          bpack.section14[40] = {category=1, mpOrMistCost=20, elements=flags({fire=true})}
          local bm = party(1, {job1=P.JOB.blackMage, magick=60, maxMp=200, mp=100})
          local foes = {}
          for i = 0, 2 do foes[#foes + 1] = unit({type=1, address=900 + i, hp=9000, maxHp=9000}) end
          local function cast()
            reset()
            for _, foe in ipairs(foes) do
              local f = F({action=40, initial=900, target={removedHp=500, identifier=9 + foe.address, work=works[foe.address]}})
              R.hook222(f, bm, foe)
              R.hook170(f, bm, foe, classes, helpers)
              NOW = NOW + 1000000                                          -- ~1 s between an area spell's targets
            end
          end
          cast(); cast()                                                   -- two Fire casts build the chain
          cast()                                                           -- the third is a full Resonance cast
          local refunds, splashes, oils = 0, 0, 0
          for _, m in ipairs(LOG.mp) do if m.amount > 0 then refunds = refunds + 1 end end
          for _, h in ipairs(LOG.hp) do if h.amount < 0 then splashes = splashes + 1 end end
          for _, a in ipairs(LOG.added) do if a.id == STATUS.oil then oils = oils + 1 end end
          assert(refunds == 1, 'MP refund came ' .. refunds .. ' times for one cast')
          assert(mpGain() == 12 or mpGain() == 10, 'II-4 refunds 60% (installed) or half (0.6.0) of 20: ' .. mpGain())
          assert(splashes <= 1, 'the other foe was hit ' .. splashes .. ' times for one cast')
          assert(oils <= 1, 'the other foe was Oiled ' .. oils .. ' times for one cast')
        """)

    def test_a_status_spell_on_three_foes_gives_hybrid_party_rewards_once(self):
        # Chaplain-style stages: a cast reaching three foes hands out its self/ally/party rewards once.
        self.run_lua(r"""
          PARTY_IDS = {1}
          bpack.section14[41] = {category=1, mpOrMistCost=20, elements=flags()}
          -- Clockwork stage II (Tinker: Chemist + Time Mage): casting Slow on a foe slows another foe and returns 3% MP
          local tinker = party(1, {job1=P.JOB.chemist, job2=P.JOB.timeMage, magick=30, maxMp=200, mp=100})
          local foes = {}
          for i = 0, 3 do foes[#foes + 1] = unit({type=1, address=900 + i, hp=9000, maxHp=9000}) end
          reset()
          for i = 1, 3 do
            local foe = foes[i]
            local f = F({action=41, initial=900, target={identifier=9 + i, status={slow=true}}})
            R.hook222(f, tinker, foe)
            f.target.addedStatusEffects[STATUS.slow] = 1                  -- the spell landed Slow on this target
            R.hook181(f, tinker, foe, classes, helpers, {})
          end
          assert(#LOG.mp <= 1 and mpGain() <= 6, 'the cast MP reward came more than once: ' .. mpGain())
          local others = 0
          for _, a in ipairs(LOG.added) do if a.id == STATUS.slow then others = others + 1 end end
          assert(others <= 1, 'the other-foe Slow came ' .. others .. ' times for one cast')
        """)

    # ---------- a full Cadence streak is a counter ----------

    def test_a_full_cadence_streak_counts_as_a_counter_for_every_counter_stage(self):
        # Every stage that listens to 'counter' is replayed alone (in the first hybrid line's slot, under that pair's
        # two jobs) with its conditions met, and a full Cadence streak is delivered through the Family hook. Stages that
        # listen to a parry or block only ('guard') must NOT fire: a streak is a counter, not a parry.
        out = self.run_lua(r"""
          PARTY_IDS = {1}
          IvaliceLegacyFamily = {}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000, mp=100, maxMp=100})
          local mystic = party(1, {job1=P.JOB.mystic, hp=500, maxHp=1000, mp=10, maxMp=100})
          R.hook165(F({initial=900, target={removedHp=1, identifier=9, work=works[900]}}), mystic, foe, classes, helpers)
          assert(IvaliceLegacyFamily.onFullCadence, 'Family hook not bound')
          reset(); P.reset()
          IvaliceLegacyFamily.onFullCadence(mystic, foe)
          assert(#LOG.hp >= 1, 'Ki Flow did not hear a full Cadence streak')
          local ORIG = {}
          for i, l in ipairs(H.LINES) do ORIG[i] = l end
          local FILLER = {'aim', nil, 'Never', 's', {{'mp', 1}}}
          local counterStages, counterFired, guardStages, guardFired = 0, 0, 0, 0
          local missed, wrong = {}, {}
          local function replay(stage)
            H.LINES[1] = {line='Test', stage, FILLER, FILLER}
            H.stageText = function() return '' end
            H.attach(P); P.reset(); reset(); printed = {}
            local conds = {}
            for _, c in ipairs(stage[2] or {}) do conds[c] = true end
            local pair = H.PARENTS[H.FIRST_ID]
            local caster = party(1, {job1=pair[1], job2=pair[2], hp=(conds.low50 and 400) or (conds.critical and 200) or 900,
                                     maxHp=1000, mp=10, maxMp=100})
            if conds.hasted then caster.temporaryStatusEffects.haste = 1 end
            if conds.lured then caster.temporaryStatusEffects.lure = 1 end
            foe.temporaryStatusEffects.poison = (conds.ailing or conds.ailing2 or conds.ailing3) and 1 or 0
            foe.temporaryStatusEffects.slow = conds.slowed and 1 or 0
            -- 0.1.44: a stage that reads Staggered needs a foe whose push budget is spent (cleared otherwise)
            R.resetPushes()
            if conds.staggered then R.pushAllowance(900, works[900], 99999, 7) end
            IvaliceLegacyFamily.onFullCadence(caster, foe)
            for _, l in ipairs(printed) do
              if l:find('cadence-counter', 1, true) and l:find('job=' .. H.FIRST_ID, 1, true) then return true end
            end
            return false
          end
          for i, line in ipairs(ORIG) do
            for k = 1, 3 do
              local stage = line[k]
              if H.hears(stage[1], 'counter') then
                counterStages = counterStages + 1
                if replay(stage) then counterFired = counterFired + 1 else missed[#missed + 1] = line.line .. ' ' .. k end
              elseif H.hears(stage[1], 'guard') then
                guardStages = guardStages + 1
                if replay(stage) then wrong[#wrong + 1] = line.line .. ' ' .. k else guardFired = guardFired + 1 end
              end
            end
          end
          return table.concat(missed, ', '), table.concat(wrong, ', '), counterStages, counterFired, guardStages, guardFired
        """)
        missed, wrong, counter_stages, counter_fired, guard_stages, guard_quiet = out
        self.assertEqual(missed, '', 'counter stages a full Cadence streak did not reach')
        self.assertEqual(wrong, '', 'parry/block-only stages fired on a Cadence streak')
        self.assertEqual(counter_stages, counter_fired)
        self.assertEqual(guard_stages, guard_quiet)
        self.assertGreaterEqual(counter_stages, 17)


    # ---------- the Writ gate ----------

    def test_a_missing_writ_switches_its_stages_off(self):
        self.run_lua(r"""
          PARTY_IDS = {1}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local knight = party(1, {job1=P.JOB.knight})
          local function parry()
            reset()
            knight.temporaryStatusEffects[STATUS.lure] = 0
            R.hook240(F({outcome=2, target={status={}}}), foe, knight, classes, helpers)
            return #LOG.added
          end
          assert(R.effective() == 12 and parry() > 0, 'all three Writs: the Knight answers a parry')
          keys[0x81F1] = nil; handlers.onMapJump(100); runTicks()           -- the Journeyman's Writ goes missing
          assert(R.effective() == 4, 'stage II and III are off without the second Writ: ' .. R.effective())
          keys[0x81F0] = nil; handlers.onMapJump(100); runTicks()           -- and the Apprentice's: nothing at all
          assert(R.effective() == 0 and not R.active(), 'no Apprentice Writ, no Proficiency')
          assert(parry() == 0, 'a Knight without the Apprentice Writ gained something')
          keys[0x81F0], keys[0x81F1] = true, true; handlers.onMapJump(100); runTicks()
          assert(R.effective() == 12 and parry() > 0, 'the Writs back: Proficiency back')
        """)


if __name__ == '__main__':
    unittest.main()
