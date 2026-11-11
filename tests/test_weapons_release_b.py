"""0.1.34 Proficiency side of the weapons release: counters pay only on a real swing (outcome 2/4), a completed fists Cadence
streak is one counter, Detonate counts Disabled from I-1, Suppression scales with the weapon's charge time, and a measure
buff spreads to a second ally.

The runtime hunks are tested twice through the same harness: against the repo runtime, and against the deployable one that
the compose script builds (deployed 0.1.31 bytes + these hunks). The second class runs only when IVALICE_LEGACY_COMPOSED names that file."""
from pathlib import Path
import os
import unittest

try:
    from lupa import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
# The deployable runtime is built outside the repo (the release workspace); point this at it to run the composed checks.
COMPOSED = Path(os.environ['IVALICE_LEGACY_COMPOSED']) if os.environ.get('IVALICE_LEGACY_COMPOSED') else None


def source(name):
    return (ROOT / 'prototypes' / f'{name}.lua').read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class RulesTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.P = self.lua.execute(source('proficiency'))
        self.lua.execute('''
          P.reset()
          function S(t)
            local s = {job=11, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, category=1, damage=100,
                       targetStatus={}, casterStatus={}, targetMp=50, casterHpPct=100, casterMp=10, casterMaxMp=100,
                       selfMaxHp=1000,
                       party={{address=100, job=11, hpPct=100, mp=10, maxMp=100, status={}},
                              {address=200, job=0, hpPct=40, mp=5, maxMp=100, status={}},
                              {address=300, job=6, hpPct=90, mp=80, maxMp=100, status={}}},
                       foes={{address=901, status={}}}, charge={current=20000, required=30000, last=20000},
                       roll=function() return true end}
            for k, v in pairs(t or {}) do s[k] = v end
            return s
          end
        ''')

    def test_detonate_counts_disabled_from_the_first_level(self):
        self.lua.execute('''
          local function stops(rank, status) P.reset(); local a = P.decide('hit', S({job=6, targetStatus=status}), rank)
            return a ~= nil and a.targetAdd ~= nil and a.targetAdd[3] ~= nil end
          assert(stops(1, {disable=true}))
          assert(stops(1, {slow=true}))
          assert(not stops(1, {immobilize=true}))          -- Immobilized still from I-2
          assert(stops(2, {immobilize=true}))
          assert(not stops(1, {}))
          assert(P.GEAR[P.JOB.timeMage][1][1]:find('Disabled'))
          assert(P.GEAR[P.JOB.timeMage][1][2]:find('Disabled'))
        ''')

    def test_suppression_scales_with_weapon_charge_time(self):
        self.lua.execute('''
          local function push(ct, rank, extra)
            P.reset()
            local s = S({job=2, weaponCt=ct}); for k, v in pairs(extra or {}) do s[k] = v end
            local a = P.decide('hit', s, rank)
            return 20000 - a.chargeTo
          end
          assert(push(nil, 1) == 3000)                      -- no weapon data: 10% of 30000, as before
          assert(push(24, 1) == 3000)                       -- CT 24 = x1
          assert(push(50, 1) == 6000)                       -- the gun (CT 50): capped at x2
          assert(push(48, 1) == 6000)
          assert(push(12, 1) == 1500)                       -- a quick weapon pushes half
          assert(push(36, 1) == 4500)
          assert(push(50, 2, {targetStatus={slow=true}}) == 9000)   -- Slowed 15% x2
          assert(P.SUPPRESSION_CT_BASE == 24 and P.SUPPRESSION_CT_CAP == 2)
          assert(P.GEAR[P.JOB.gunner][1][1]:find('CT/24'))
        ''')

    def test_measure_buff_spreads_to_a_second_ally(self):
        self.lua.execute('''
          local function others(a) local n = {} for _, o in ipairs(a and a.others or {}) do n[#n + 1] = o end return n end
          local function buff(t, rank) P.reset(); return P.decide('buff', S(t), rank) end
          assert(buff({landed={18}, target=200}, 0) == nil)
          local o = others(buff({landed={18}, target=200}, 1))
          assert(#o == 1 and o[1].op == 'add' and o[1].id == 18 and o[1].unit == 300 and o[1].scale == 0.75)
          o = others(buff({landed={18}, target=200}, 2))
          assert(o[1].scale == 1)
          -- not the struck ally, and an ally who has the buff is skipped
          o = others(buff({landed={18}, target=300}, 1)); assert(o[1].unit == 200)
          o = others(buff({landed={18}, target=300, party={{address=100, job=11, hpPct=100, status={}},
                  {address=200, job=0, hpPct=40, status={protect=true}}, {address=300, job=6, hpPct=90, status={}}}}, 1))
          assert(o[1].unit == 100)                          -- the Chemist last
          assert(buff({landed={18}, target=200, roll=function() return false end}, 1) == nil)
          assert(buff({landed={8}, target=200}, 1) == nil)  -- Poison is not a buff
          local chances = {}
          buff({landed={18}, target=200, roll=function(c) chances[#chances + 1] = c return true end}, 1)
          buff({landed={18}, target=200, roll=function(c) chances[#chances + 1] = c return true end}, 4)
          assert(chances[1] == 35 and chances[2] == 50)
        ''')


HARNESS = r'''
  printed, handlers, keys, RANK = {}, {}, {}, 12
  print = function(s) printed[#printed + 1] = s end
  real_load = load
  loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@' .. p, mode, env) or nil end
  local wide = function(s) return (s:gsub('.', '%0\0')) end
  local mem = {[0x120000]='MZ', [0x120180]='PE\0\0\x64\x86', [0xD113D4]='LEGCY_\0', [0xD12200]=wide('%sLEGCY_%03d\0')}
  memory = {u8={}, u32=setmetatable({}, {__index=function() return 0 end}), u64={}, arg={s32='s32', u32='u32'},
            readArray=function(a, n) return (mem[a] or ''):sub(1, n) end,
            execute=function(fn, ret, t1, id) if fn == 0x2FD970 then return keys[id] and 1 or 0 end return 0 end}
  event = {registerEventSync=function(name, fn) handlers[name] = fn return true end,
           registerEventAsync=function(name, fn) handlers[name] = fn return true end,
           registerLoadHandler=function(_, fn) handlers.load = fn return true end,
           registerSaveHandler=function(_, fn) handlers.save = fn return true end,
           executeAfterMs=function() end}
  for _, k in ipairs({0x81F0, 0x81F1, 0x81F2}) do keys[k] = true end
  R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime', 't', _ENV)()
  assert(R.register())
  handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=12})
  assert(R.effective() == 12)
  local IDS = {lure=17, slow=15, blind=7, haste=20, protect=18}
  function flags(t)
    local f = setmetatable({}, {__index=function() return 0 end})
    for name, on in pairs(t or {}) do f[name] = on; f[IDS[name]] = on end
    return f
  end
  local function record() return {flags1={isNegative=0, isNotApplicableToFoes=0, isRefreshable=1}, absentStatusEffects=flags()} end
  -- section 13: row 0 is a measure (category 16), row 1 a gun (category 9, CT 50), row 2 a pole
  bpack = {section15=setmetatable({}, {__index=function() return record() end}),
           section13={count=5, [0]={category=16, chargeTime=24}, [1]={category=9, chargeTime=50}, [2]={category=6, chargeTime=40},
                      [3]={category=0, chargeTime=0}, [4]={category=9, chargeTime=24}}}
  keeps, added, hp = {}, {}, {}
  function unit(t)
    local u = {type=t.type, identifier=t.id or 0, address=t.address, job1=t.job1 or 0, job2=t.job2 or 255,
               maxHp=100, currentHp=t.hp or 100, maxMp=50, currentMp=10, weapon=t.weapon,
               temporaryStatusEffects=flags(t.status), permanentStatusEffectImmunities=flags(),
               permanentStatusEffects=flags(), statusEffectDurations=flags(), temporaryAugments=flags(),
               permanentAugments=flags()}
    keeps[u.address] = u
    return u
  end
  function party(id) return 1000 + 8 + id * 0x1C8 end
  memory.u64[0x02EBF190] = 1000
  roster = {}
  helpers = {getActivePartyMember=function(i) return roster[i + 1] or -1 end,
    getBattleUnitKeep=function(id) return party(id) end, getRandomNumber=function() return 0 end,
    getStatusEffectDuration=function() return 100 end, getStatusEffectTickDuration=function() return 0 end,
    getStatusEffectsMatch=function() return false end, removeStatusEffect=function() end,
    addStatusEffect=function(c, u, id, d) added[#added + 1] = {u=u, id=id} end,
    modifyHp=function(_, u, amount) hp[#hp + 1] = {u=u, amount=amount} end, modifyMp=function() end,
    getBattleUnitWork=function() return 0 end}
  classes = {getBattleUnitKeep=function(a) return keeps[a] end, getBattleUnitWork=function() return {address=0} end}
  function plus(t)
    local p = {statusEffects=flags(t and t.status), addedStatusEffects=flags(), removedStatusEffects=flags(),
               statusEffectDurations=setmetatable({}, {__index=function() return -1 end})}
    for k, v in pairs(t or {}) do if k ~= 'status' then p[k] = v end end
    return p
  end
  function reset() added, hp = {}, {} end
  function healed(addr) for _, h in ipairs(hp) do if h.u == addr and h.amount > 0 then return true end end return false end
  function addedTo(addr, id) for _, a in ipairs(added) do if a.u == addr and a.id == id then return true end end return false end
'''


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class RuntimeTest(unittest.TestCase):
    RUNTIME = ROOT / 'prototypes' / 'proficiency_runtime.lua'

    def setUp(self):
        if self.RUNTIME is None or not self.RUNTIME.exists():
            self.skipTest('composed runtime: IVALICE_LEGACY_COMPOSED is unset or missing')
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        sources = {f'scripts/IvaliceLegacy/{n}.lua': source(n) for n in ('proficiency', 'hybrid_proficiency', 'family_identities')}
        sources['scripts/IvaliceLegacy/proficiency_runtime.lua'] = self.RUNTIME.read_text(encoding='utf-8')
        sources['scripts/IvaliceLegacy/clan_rank.lua'] = 'return {MARKS_FN=0, read=function() return {rank=12} end}'
        self.lua.globals().sources = self.lua.table_from(sources)
        self.lua.execute(HARNESS)

    def hit_by_foe(self, job1, job2, outcome, counter):
        """A foe's attack on a party member whose blow was answered (or not): returns whether the member was healed."""
        self.lua.globals().case = self.lua.table_from(
            {'job1': job1, 'job2': job2, 'outcome': outcome, 'counter': counter})
        return self.lua.execute('''
          reset()
          local me = unit({type=0, id=2, address=party(2), job1=case.job1, job2=case.job2, hp=50})
          local foe = unit({type=1, address=7000})
          R.hook240({caster=plus(), target=plus(), outcomeType=case.outcome, counterState=case.counter},
                    foe, me, classes, helpers)
          return healed(me.address)
        ''')

    def test_counters_pay_only_on_a_real_swing(self):
        for job1, job2, name in ((5, 255, 'Mystic'), (5, 0, 'White Monk hybrid')):
            with self.subTest(name):
                self.assertTrue(self.hit_by_foe(job1, job2, 2, 1), 'parry + counter swing pays')
                self.assertTrue(self.hit_by_foe(job1, job2, 4, 1), 'block + counter swing pays')
                self.assertFalse(self.hit_by_foe(job1, job2, 1, 1), 'a roll with a plain parry: no swing, no pay')
                self.assertFalse(self.hit_by_foe(job1, job2, 3, 1), 'a roll with a plain block: no swing, no pay')
                self.assertFalse(self.hit_by_foe(job1, job2, 5, 1), 'a roll with a dodge: no swing, no pay')
                self.assertFalse(self.hit_by_foe(job1, job2, 0, 1), 'a roll on a landed blow: no swing, no pay')

    def test_a_roll_without_a_swing_keeps_the_hybrids_guard_stages(self):
        # Sentinel (Knight + Hunter) has a stage on any parry, block or dodge: outcome 1 with a counter roll is a parry,
        # so that stage hears it (it never heard a swing-less roll as a parry before).
        self.lua.execute('''
          reset()
          local me = unit({type=0, id=2, address=party(2), job1=4, job2=8})
          local foe = unit({type=1, address=7000})
          R.hook240({caster=plus(), target=plus(), outcomeType=1, counterState=1}, foe, me, classes, helpers)
          assert(addedTo(me.address, 30), 'Sentinel Libra on the parry')
          reset()
          R.hook240({caster=plus(), target=plus(), outcomeType=2, counterState=1}, foe, me, classes, helpers)
          assert(not addedTo(me.address, 30), 'a counter swing is a counter, not a guard')
        ''')

    def test_full_cadence_is_one_counter(self):
        self.lua.execute('''
          Family = {}
          rawset(_G, 'IvaliceLegacyFamily', Family)
          local mystic = unit({type=0, id=2, address=party(2), job1=5, hp=50, weapon=0x1000})
          local monk = unit({type=0, id=3, address=party(3), job1=5, job2=0, hp=50})
          local blackMage = unit({type=0, id=4, address=party(4), job1=9, hp=50})
          local foe = unit({type=1, address=7000})
          -- before any 165 call there is nothing to bind to or run with
          assert(Family.onFullCadence == nil)
          R.hook165({caster=plus(), target=plus({removedHp=-1})}, foe, mystic, classes, helpers)   -- binds, stashes classes
          assert(type(Family.onFullCadence) == 'function', 'the runtime sets the Family callback')
          reset(); Family.onFullCadence(mystic, foe)
          assert(healed(mystic.address), 'Ki Flow pays on a completed streak')
          reset(); Family.onFullCadence(monk, foe)
          assert(healed(monk.address), 'a hybrid counter stage pays too')
          reset(); Family.onFullCadence(blackMage, foe)
          assert(#hp == 0 and #added == 0, 'a job with no counter line is untouched')
          reset(); Family.onFullCadence(mystic, mystic)
          assert(#hp == 0, 'a party member as the target: nothing')
          -- an existing callback is never replaced
          local mine = function() end
          Family.onFullCadence = mine
          R.hook165({caster=plus(), target=plus({removedHp=-1})}, foe, mystic, classes, helpers)
          assert(Family.onFullCadence == mine)
        ''')

    def test_measure_buff_spreads_through_forge_182(self):
        self.lua.execute('''
          roster = {1, 2, 3}
          reset()
          local chemist = unit({type=0, id=1, address=party(1), job1=11, weapon=0x1000})
          local struck = unit({type=0, id=2, address=party(2)})
          local third = unit({type=0, id=3, address=party(3)})
          local f = {caster=plus(), target=plus(), action=150}
          f.target.addedStatusEffects[18] = 1                       -- the measure landed Protect on ally 2
          R.hook182(f, chemist, struck, classes, helpers, {})
          assert(addedTo(third.address, 18), 'Protect spread to the third ally')
          assert(not addedTo(struck.address, 18))
          -- not a measure: nothing
          reset(); chemist.weapon = 0x1001
          R.hook182(f, chemist, struck, classes, helpers, {})
          assert(#added == 0, 'a gun does not spread buffs')
          -- not the Chemist
          reset(); chemist.weapon = 0x1000; chemist.job1 = 0
          R.hook182(f, chemist, struck, classes, helpers, {})
          assert(#added == 0, 'only the Chemist line spreads')
          -- a measure's item or spell is not its Attack
          reset(); f.action = 77
          R.hook182(f, chemist, struck, classes, helpers, {})
          assert(#added == 0, 'only the Attack spreads')
          f.action = 150
          -- the buff was already there before the hit: nothing landed
          reset(); chemist.job1 = 11
          R.hook182(f, chemist, struck, classes, helpers, {[18]=true})
          assert(#added == 0)
        ''')

    def test_suppression_reads_the_weapon_charge_time_through_hook165(self):
        self.lua.execute('''
          keys[0x81F1], keys[0x81F2] = false, false                -- Writ I only: rank 1 stays rank 1
          handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=1})
          assert(R.effective() == 1)
          roster = {1}
          local function pushed(weapon)
            reset(); R.Proficiency.reset()
            local gunner = unit({type=0, id=1, address=party(1), job1=2, weapon=weapon})
            local foe = unit({type=1, address=7000 + weapon})
            local work = {address=1, currentChargeTime=20000, requiredChargeTime=30000, lastChargeTime=20000}
            local f = {caster=plus(), target=plus({removedHp=10, identifier=9, battleUnitWork=work}), action=150,
                       initialTargetPointer=0, criticalState=0}
            R.hook165(f, gunner, foe, classes, helpers)
            return 20000 - work.currentChargeTime
          end
          assert(pushed(0x1004) == 3000, 'CT 24 pushes 10% of 30000')
          assert(pushed(0x1001) == 6000, 'the gun, CT 50: capped at double')
          assert(pushed(0x1003) == 3000, 'a weapon with no charge time (0) keeps the base push')
        ''')

    def test_a_five_step_cadence_streak_is_one_ki_flow_counter(self):
        self.lua.execute('''
          roster = {2}
          NOW = 1000000
          event.getClock = function() return NOW end
          local Family = assert(real_load(sources['scripts/IvaliceLegacy/family_identities.lua'], '@family', 't', _ENV))()
          assert(Family.register() == true)
          rawset(_G, 'IvaliceLegacyFamily', Family)
          local wrapped = Family.wrap165(R.wrap(165, function() end))
          local mystic = unit({type=0, id=2, address=party(2), job1=5, hp=50, weapon=0x1003})   -- fists
          local foe = unit({type=1, address=7000})
          foe.maxHp, foe.currentHp = 100000, 100000
          for i = 1, 9 do
            NOW = NOW + 1000000                                  -- separate Attacks, 1 s apart, inside the 10 s window
            local f = {caster=plus(), target=plus({removedHp=50, identifier=9, battleUnitWork={address=0}}), action=150,
                       initialTargetPointer=0, criticalState=0}
            wrapped(f, mystic, foe, {}, classes, helpers)
            local heals = 0
            for _, h in ipairs(hp) do if h.u == mystic.address and h.amount > 0 then heals = heals + 1 end end
            if i < 6 then assert(heals == 0, 'no counter before the streak completes (attack ' .. i .. ')') end
            assert(heals == (i >= 6 and 1 or 0), 'exactly one counter (attack ' .. i .. ', heals ' .. heals .. ')')
          end
        ''')

    def test_the_foe_side_of_182_is_unchanged(self):
        self.lua.execute('''
          roster = {1}
          reset()
          local chemist = unit({type=0, id=1, address=party(1), job1=11, weapon=0x1000})
          local foe = unit({type=1, address=7000})
          local other = unit({type=1, address=7001})
          local f = {caster=plus(), target=plus({removedHp=10}), action=150}
          f.target.addedStatusEffects[8] = 1                        -- Poison landed on the foe
          R.seeFoe(7001)
          R.hook182(f, chemist, foe, classes, helpers, {})
          assert(addedTo(other.address, 8), 'Contagion still spreads a foe status to another foe')
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class ComposedRuntimeTest(RuntimeTest):
    """The same checks against the deployable runtime: the deployed 0.1.31 bytes plus this release's hunks."""
    RUNTIME = COMPOSED

    def test_composition_keeps_the_claim_and_drops_the_topic(self):
        text = self.RUNTIME.read_bytes()
        self.assertIn(b'then claim() end', text)
        self.assertNotIn(b'TOPIC_FLAG', text)
        self.assertNotIn(b'watchTopic', text)
        self.assertNotIn(b'\r\n', text)


if __name__ == '__main__':
    unittest.main()
