"""Synergy pass 2 and the 0.1.43 review fixes (Ivalice Legacy 0.1.44): Broken (fingerprint, ailment tests, Brave spear, Faithful
staff), Staggered (the push budget and its readers), foe guard fatigue, Regen's five-second guard, Unseen on a held or tired foe,
the True aim interrupt, party element rhythm, Magebreaker's low-MP test, and the Forge 138/139 loader failure flag. Every change
carries a control that must not change. Static: the shipped prototypes in a synthetic battle (tests/runtime_world.py); nothing
here touches a game or a save."""
import sys
import unittest
from pathlib import Path

import runtime_world

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def rd(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class Pass2SynergyTests(unittest.TestCase):
    def test_every_pass2_change_and_its_control(self):
        self_ = self
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        # ---------------- compile ----------------
        lua0 = LuaRuntime()
        for p in [ROOT / 'prototypes' / n for n in ('family_identities.lua', 'proficiency.lua', 'proficiency_runtime.lua', 'accessory_effects.lua',
                                                            'hybrid_proficiency.lua')] + [ROOT / 'package/x64/scripts/config/TheInsurgentsForgeConfig/functions' / n for n in ('138.lua', '139.lua')]:
            err = lua0.eval('function(s, n) local f, e = load(s, n) return e end')(p.read_text(encoding='utf-8'), p.name)
            check(f'compiles {p.name}', err is None)

        FAM = rd('prototypes/family_identities.lua')
        F138 = rd('package/x64/scripts/config/TheInsurgentsForgeConfig/functions/138.lua')
        F139 = rd('package/x64/scripts/config/TheInsurgentsForgeConfig/functions/139.lua')
        ACC = rd('prototypes/accessory_effects.lua')

        # ---------------- Part A ----------------
        PRE = r'''
        function newFamily() local f = load(FAM, '@family')(); f.reset(); return f end
        reports = {}
        NOW = 1000000
        deps = {active = function() return ACTIVE end, clock = function() return NOW end,
                category = function(u) return u.cat or CAT end, report = function(kind, ...) reports[#reports + 1] = {kind, ...} end}
        ACTIVE, CAT = true, nil
        local n = 0
        function wielder(id, extra)
          n = n + 1
          local u = {type = 0, identifier = id or 1, address = 1000 + n, weapon = 0x1001, temporaryStatusEffects = {}, permanentStatusEffects = {}}
          for k, v in pairs(extra or {}) do u[k] = v end
          return u
        end
        function foeUnit(addr, extra)
          local u = {type = 1, identifier = 9, address = addr or 900, defense = 100, maxHp = 1000, level = 10, temporaryStatusEffects = {}}
          for k, v in pairs(extra or {}) do u[k] = v end
          return u
        end
        function count(kind) local c = 0 for _, r in ipairs(reports) do if r[1] == kind then c = c + 1 end end return c end
        '''


        def sandbox():
            lua = LuaRuntime(unpack_returned_tuples=True)
            lua.globals().FAM = FAM
            lua.execute(PRE)
            return lua


        # ---- review fix: the Broken table keeps {at, hp, level, by} and isBroken compares the fingerprint
        got = sandbox().execute(r'''
          local M = newFamily()
          local foe = foeUnit(900)
          local a = M.isBroken(foe, NOW)
          local noted = M.noteBroken(foe, NOW, 1234)
          local b = M.isBroken(foe, NOW + 10000000)                     -- exactly 10 s
          local c = M.isBroken(foe, NOW + 10000001)                     -- just past
          local d = M.isBroken(foe, NOW + 5)                            -- dropped on expiry
          M.noteBroken(foe, NOW, 1234)
          local sameFoe = M.isBroken(foeUnit(900), NOW + 1)             -- control: the same fingerprint at the same address
          local byWho = M.brokenBy(foe, NOW + 1)
          local recycledHp = M.isBroken(foeUnit(900, {maxHp = 2500}), NOW + 1)   -- a different foe at a recycled address
          M.noteBroken(foe, NOW, 1234)
          local recycledLevel = M.isBroken(foeUnit(900, {level = 40}), NOW + 1)
          M.noteBroken(foe, NOW, 1234)
          local other = M.isBroken(foeUnit(902), NOW)                   -- another foe
          M.noteBroken(foe, NOW)
          local noBy = M.brokenBy(foe, NOW + 1)
          M.reset()
          local afterReset = M.isBroken(foe, NOW)
          local g, h = M.noteBroken(foe, 0), M.noteBroken(foe, nil)
          local i = M.isBroken(foe, 0)
          local j, k = M.noteBroken(nil, NOW), M.noteBroken(900, NOW)  -- a bare address is no longer a foe: nothing is kept
          return a, noted, b, c, d, sameFoe, byWho, recycledHp, recycledLevel, other, noBy, afterReset, g, h, i, j, k
        ''')
        check('Broken: not Broken before a cut, Broken after it', (got[0], got[1]) == (False, True))
        check('Broken: lasts 10 s and no longer; an old entry is dropped', got[2] is True and got[3] is False and got[4] is False)
        check('Broken control: the same foe (same maxHp and level) at its address stays Broken; brokenBy names who',
              got[5] is True and got[6] == 1234)
        check('Broken fix: a different maxHp or level at the same address is another foe (not Broken)',
              got[7] is False and got[8] is False)
        check('Broken: per foe; a cut with no actor is Broken with nobody recorded; a map change or load forgets it',
              got[9] is False and got[10] is None and got[11] is False)
        check('Broken: without a clock, or without a foe table, nothing is kept and nothing reads Broken',
              not got[12] and not got[13] and not got[14] and not got[15] and not got[16])

        # ---- review fix: Forge 138 / 139 remember a failed lazy load
        ADAPT = r'''
        LOADS = 0
        function adapter(src, mode)
          -- the override stays installed: the lazy loader inside the adapter calls loadfile at cast time
          local f = function(path, m, env)
            if path == 'scripts/IvaliceLegacy/family_identities.lua' then
              LOADS = LOADS + 1
              if mode == 'missing' then return nil end
              if mode == 'throws' then return function() error('boom') end end
              return load(FAM, '@family', 't', env)
            end
            return function() return function(formula, caster, target) end end
          end
          loadfile = f
          _ENV.event = {registerEventSync = function() return true end, registerEventAsync = function() return true end}
          local fn = load(src, '@adapter', 't', _ENV)()
          return fn
        end
        function stub(M) M.deps = function() return {active = function() return ACTIVE end, clock = function() return NOW end} end end
        function cast(fn, address, casterAddress)
          local target = {address = address, type = 1, defense = 100, magickResist = 100, maxHp = 500, level = 5}
          local formula = {skipState = 0, target = {defense = -1, magickResist = -1}}
          fn(formula, {type = 0, address = casterAddress or 77}, target, {}, {}, {})
          return formula, target
        end
        '''
        for name, src, field in (('Expose 138', F138, 'defense'), ('Shear 139', F139, 'magickResist')):
            for mode in ('missing', 'throws'):
                lua = LuaRuntime(unpack_returned_tuples=True)
                lua.globals().FAM, lua.globals().SRC, lua.globals().MODE = FAM, src, mode
                lua.execute('ACTIVE, NOW = true, 1000000')
                lua.execute(ADAPT)
                got = lua.execute(r'''
                  rawset(_G, 'IvaliceLegacyFamily', nil)
                  local fn = adapter(SRC, MODE)
                  local f1 = cast(fn, 700); local f2 = cast(fn, 701); local f3 = cast(fn, 702)
                  return LOADS, f1.target[FIELD], f3.target[FIELD]
                '''.replace('FIELD', repr(field)))
                check(f'{name}: a failed load ({mode}) is tried once, not on every cast', got[0] == 1)
                check(f'{name}: the effect itself still takes 30% when the load failed ({mode})', got[1] == 70 and got[2] == 70)
            lua = LuaRuntime(unpack_returned_tuples=True)
            lua.globals().FAM, lua.globals().SRC, lua.globals().MODE = FAM, src, 'ok'
            lua.execute('ACTIVE, NOW = true, 1000000')
            lua.execute(ADAPT)
            got = lua.execute(r'''
              rawset(_G, 'IvaliceLegacyFamily', nil)
              local fn = adapter(SRC, 'ok')
              cast(fn, 699)                                  -- the first cast loads the module (the campaign check cannot run here)
              local M = rawget(_G, 'IvaliceLegacyFamily')
              stub(M)                                        -- from now on a clock we control and the campaign on
              local _, target = cast(fn, 700, 77)
              cast(fn, 701, 77)
              local _, foeCast = pcall(function() local f = {skipState = 0, target = {defense = -1}}
                fn(f, {type = 1, address = 5}, {address = 702, type = 1, defense = 100, maxHp = 500, level = 5}, {}, {}, {})
                return M.isBroken({address = 702, maxHp = 500, level = 5}, NOW) end)
              return LOADS, M ~= nil, M.isBroken(target, NOW), M.brokenBy(target, NOW), foeCast
            ''')
            check(f'{name} control: a working load happens once, marks the foe Broken and records the caster',
                  got[0] == 1 and got[1] and got[2] is True and got[3] == 77 and got[4] is False)

        # ---- the carried-over 0.1.43 floor control, now asserting (was computed and thrown away in test_0143.py:168-172)
        got = sandbox().execute(r'''
          local M = newFamily()
          CAT = 'axe'
          local wrapped = M.wrap68(function() end, deps)
          local caster = wielder()
          local floorFoe = foeUnit(557)
          local sawBroken = false
          local function swing()
            local g = {action = M.ATTACK, caster = {}, target = {defense = -1}}
            wrapped(g, caster, floorFoe, {}, {}, {})
            if g.target.defense >= 0 then floorFoe.defense = g.target.defense end
            return g.target.defense
          end
          for i = 1, 12 do swing() end                                -- down to the floor: half of 100
          local atFloor = floorFoe.defense
          local brokenAtFloor = M.isBroken(floorFoe, NOW)             -- the last real dent marked it just now
          NOW = NOW + 11000000                                        -- 11 s on: the mark has expired
          local expired = M.isBroken(floorFoe, NOW)
          local after = swing()                                       -- a swing at the floor makes no dent ...
          return atFloor, brokenAtFloor, expired, after, M.isBroken(floorFoe, NOW), floorFoe.defense   -- ... but renews Broken (0.1.46)
        ''')
        check('floor control: Sunder runs down to half and stops there', got[0] == 50)
        check('floor control: the instrument works (the last real dent marked the foe Broken just before)', got[1] is True and got[2] is False)
        check('floor (0.1.46): a swing on a foe at the Sunder floor dents nothing more and renews Broken', got[3] == -1 and got[5] == 50 and got[4] is True)

        # ---- wording fixes (text only): Grounding II level 1 defines Broken where it uses the word
        PRO = rd('prototypes/proficiency.lua')
        gr = PRO[PRO.index("[M.JOB.lancer] = {line = 'Grounding'"):PRO.index("[M.JOB.gunner] = {line = 'Suppression'")]
        stage2 = gr.split("    {'")[2]       # the Grounding II block
        lvl1 = stage2[:stage2.index("',\n")]
        check('Grounding II level 1 defines Broken where it uses the word', 'Broken' in lvl1 and 'cut' in lvl1 and lvl1.index('Resist') < lvl1.index('Broken'))
        check('Grounding II level 1 keeps its two lines within 60 characters each',
              all(len(x) <= 60 for x in lvl1.replace("\\n", '\n').splitlines()))

        # ---- D6: foes tire too (and the party's own guard rule is unchanged: the 0.1.43 S1 control)
        got = sandbox().execute(r'''
          local M = newFamily()
          local seen = {}
          local OUTCOME, SKIP = 3, 1
          local wrapped = M.guardWrap(function(f) seen[#seen + 1] = f.evadeShield; f.outcomeType = OUTCOME; f.skipState = SKIP end, deps)
          local function hitFoe(foe, attacker, blind, extra)
            local f = {evadeShield = 60, evadeWeapon = 40, evadeParry = 10, outcomeType = 0, skipState = 0, counterState = 0,
                       caster = {statusEffects = {blind = blind and 1 or 0}}, target = {}}
            for k, v in pairs(extra or {}) do f[k] = v end
            wrapped(f, attacker, foe, {}, {}, {})
            return f
          end
          local party = wielder(1)
          local foe = foeUnit(900)
          for i = 1, 6 do hitFoe(foe, party) end
          local foeSeen = {seen[1], seen[2], seen[3], seen[4], seen[5], seen[6]}
          -- a landed hit resets it
          OUTCOME, SKIP = 0, 0; hitFoe(foe, party)
          OUTCOME, SKIP = 3, 1; seen = {}; hitFoe(foe, party)
          local afterHit = seen[1]
          -- 10 s without being attacked: fresh again
          seen = {}; hitFoe(foe, party); hitFoe(foe, party)
          NOW = NOW + 10000001; hitFoe(foe, party)
          local afterWindow = seen[3]
          -- a different foe at a recycled address (other maxHp) starts fresh
          seen = {}; hitFoe(foe, party); hitFoe(foe, party)
          local recycled = foeUnit(900, {maxHp = 4000}); hitFoe(recycled, party)
          local afterRecycle = seen[3]
          -- a Blinded attacker's avoid adds no step on a foe either
          seen = {}
          local f2 = foeUnit(910)
          hitFoe(f2, party, true); hitFoe(f2, party, true); hitFoe(f2, party, true)
          local blindSeen = {seen[1], seen[2], seen[3]}
          -- controls: a foe attacking a foe, or an Esper summon (identifier 30), leaves the foe's evasion alone
          seen = {}
          local f3 = foeUnit(920)
          for i = 1, 3 do hitFoe(f3, foeUnit(5)) end
          for i = 1, 3 do hitFoe(f3, wielder(30)) end
          local ignored = {seen[1], seen[2], seen[3], seen[4], seen[5], seen[6]}
          -- no counter-parry for a foe (a party defender's rolled counter swings against 30; a foe's weapon evade is left at 40)
          local weapon
          local w2 = M.guardWrap(function(f) weapon = f.evadeWeapon end, deps)
          local function counter(def, attacker)
            local f = {evadeShield = 0, evadeWeapon = 10, evadeParry = 0, outcomeType = 0, skipState = 0, counterState = 1, caster = {}, target = {}}
            CAT = 'sword'
            w2(f, attacker, def, {}, {}, {})
            return weapon
          end
          local foeWeapon = counter(foeUnit(930), party)
          local partyWeapon = counter(wielder(2), foeUnit(5))
          CAT = nil
          local kinds = {foe = count('foe-fatigue'), party = count('fatigue'), blind = count('foe-fatigue-blind')}
          return foeSeen[1], foeSeen[2], foeSeen[3], foeSeen[4], foeSeen[5], afterHit, afterWindow, afterRecycle,
                 blindSeen[1], blindSeen[2], blindSeen[3], ignored[1], ignored[3], ignored[4], ignored[6], foeWeapon, partyWeapon,
                 kinds.foe > 0, kinds.blind
        ''')
        check('D6: an attack a foe avoids leaves its next evasion 25% lower (60, 45, 30, 15, then none)', tuple(got[0:5]) == (60, 45, 30, 15, 0))
        check('D6: a hit that lands resets a foe, and 10 s without being attacked does too', got[5] == 60 and got[6] == 60)
        check('D6: another foe at a recycled address starts fresh (maxHp differs)', got[7] == 60)
        check('D6: a Blinded attacker\'s avoids add no step on a foe (stays at 60)', tuple(got[8:11]) == (60, 60, 60))
        check('D6 control: a foe or an Esper summon attacking a foe leaves its evasion alone', tuple(got[11:15]) == (60, 60, 60, 60))
        check('D6 control: no counter-parry for a foe (40 stays 10 -> 10), a party defender still gets 30', got[15] == 10 and got[16] == 30)
        check('D6: foe fatigue logs under its own name, not the party\'s', got[17] is True and got[18] > 0)

        # ---- D9: Regen steadies the guard
        got = sandbox().execute(r'''
          local M = newFamily()
          local seen = {}
          local wrapped = M.guardWrap(function(f) seen[#seen + 1] = f.evadeShield; f.outcomeType = 3; f.skipState = 1 end, deps)
          local function attackAfter(def, secs)
            NOW = NOW + secs * 1000000
            wrapped({evadeShield = 60, evadeWeapon = 0, evadeParry = 0, outcomeType = 0, skipState = 0, counterState = 0, caster = {statusEffects = {blind = 0}}, target = {}},
                    foeUnit(5), def, {}, {}, {})
          end
          local function run(def, gap)
            seen = {}
            attackAfter(def, 20)                       -- starts from a quiet guard
            for i = 1, 3 do attackAfter(def, gap) end
            return seen[2], seen[3], seen[4]
          end
          local plain, plain2 = run(wielder(1), 6)                               -- control: no Regen, 6 s gaps: the guard keeps wearing down
          local regenA, regenB, regenC = run(wielder(3, {temporaryStatusEffects = {regen = 1}}), 6)   -- Regen: fresh after 5 s
          local auto = {run(wielder(4, {permanentStatusEffects = {regen = 1}}), 6)}                 -- Auto-Regen counts too
          local fastRegen = {run(wielder(5, {temporaryStatusEffects = {regen = 1}}), 4)}            -- control: under 5 s the guard still tires
          local fastPlain = {run(wielder(6), 4)}
          return plain, plain2, regenA, regenB, regenC, auto[1], auto[2], fastRegen[1], fastRegen[2], fastPlain[1]
        ''')
        check('D9 control: without Regen a guard attacked every 6 s still wears down (45, 30)', (got[0], got[1]) == (45, 30))
        check('D9: a Regen holder\'s guard is fresh after 5 s (60 every time)', (got[2], got[3], got[4]) == (60, 60, 60))
        check('D9: Auto Regen (permanent) counts too', (got[5], got[6]) == (60, 60))
        check('D9 control: attacked every 4 s, even a Regen holder\'s guard wears down (45, 30), as does a plain one', (got[7], got[8], got[9]) == (45, 30, 45))

        # ---- D7: Unseen on a held or tired foe
        got = sandbox().execute(r'''
          local M = newFamily()
          CAT = 'ninjaSword'
          local orig = function(f) end
          local wrapped = M.wrap22(orig, deps)
          local function swing(foe, extra)
            local f = {action = M.ATTACK, evadeWeapon = 30, evadeShield = 20, evadeParry = 10, caster = {}, target = {}}
            wrapped(f, wielder(1, extra), foe, {}, {}, {})
            return f.evadeWeapon, f.evadeShield, f.evadeParry
          end
          local fresh = {swing(foeUnit(900))}                                   -- control: parry and block go, the dodge stays
          local slow = {swing(foeUnit(901, {temporaryStatusEffects = {slow = 1}}))}
          local imm = {swing(foeUnit(902, {temporaryStatusEffects = {immobilize = 1}}))}
          local stop = {swing(foeUnit(903, {temporaryStatusEffects = {stop = 1}}))}
          local ailed = {swing(foeUnit(904, {temporaryStatusEffects = {poison = 1, blind = 1}}))}   -- other ailments do not hold a foe
          -- two fatigue steps (two avoids in a row against a party attacker)
          local tired = foeUnit(905)
          local g = M.guardWrap(function(f) f.outcomeType = 3; f.skipState = 1 end, deps)
          local function avoid() g({evadeShield = 60, evadeWeapon = 0, evadeParry = 0, outcomeType = 0, skipState = 0, counterState = 0,
                                    caster = {statusEffects = {blind = 0}}, target = {}}, wielder(9), tired, {}, {}, {}) end
          avoid()
          local oneStep = {swing(tired)}                                        -- control: one step is not tired yet
          avoid()
          local twoSteps = {swing(tired)}
          local steps = M.fatigueSteps(tired, NOW)
          NOW = NOW + 10000001
          local expired = {swing(tired)}                                        -- fatigue ends after 10 s
          CAT = 'sword'
          local notNinja = {swing(foeUnit(906, {temporaryStatusEffects = {slow = 1}}))}   -- control: another family is untouched
          CAT = 'ninjaSword'
          local logged = count('unseen')
          return fresh[1], fresh[2], fresh[3], slow[3], imm[3], stop[3], ailed[3], oneStep[3], twoSteps[1], twoSteps[3], steps, expired[3],
                 notNinja[1], notNinja[3], logged
        ''')
        check('D7 control: Unseen still removes parry and block and leaves the dodge on a fresh foe (0, 0, 10)', tuple(got[0:3]) == (0, 0, 10))
        check('D7: a Slowed, Immobilized or Stopped foe loses its dodge too', got[3] == 0 and got[4] == 0 and got[5] == 0)
        check('D7 control: other ailments (Poison, Blind) do not hold the dodge down', got[6] == 10)
        check('D7 control: one fatigue step is not tired (dodge stays 10)', got[7] == 10)
        check('D7: two fatigue steps take the dodge (parry too)', got[8] == 0 and got[9] == 0 and got[10] == 2)
        check('D7: the fatigue is forgotten after 10 s (dodge back)', got[11] == 10)
        check('D7 control: a non-ninja weapon keeps all its evasion on a Slowed foe', got[12] == 30 and got[13] == 10)
        check('D7: the Unseen log line names why', got[14] >= 4)

        # ---- D2: Sunder on a Staggered foe (the runtime is a stub here; Part B uses the real one)
        STUB = r'''
        STAGGER = {}
        rawset(_G, 'IvaliceLegacyProficiency', nil)
        function stubRuntime()
          rawset(_G, 'IvaliceLegacyProficiency', {foeStaggered = function(address) return STAGGER[address] == true end})
        end
        '''
        got = sandbox().execute(STUB + r'''
          local M = newFamily()
          CAT = 'axe'
          local wrapped = M.wrap68(function() end, deps)
          local caster = wielder()
          local function run(address, staggered, swings)
            STAGGER[address] = staggered
            local foe = foeUnit(address)
            local first
            for i = 1, swings do
              local g = {action = M.ATTACK, caster = {}, target = {defense = -1}}
              wrapped(g, caster, foe, {}, {}, {})
              if g.target.defense >= 0 then foe.defense = g.target.defense end
              first = first or g.target.defense
            end
            return first, foe.defense
          end
          stubRuntime()
          local c1, c2 = run(800, false, 3)               -- control: 5% per hit (95, 90, 85)
          local s1, s2 = run(801, true, 3)                -- Staggered: 10% per hit (90, 80, 70)
          local f1, f2 = run(802, true, 30)               -- the floor stays at half
          local logs = count('sunder-staggered')
          rawset(_G, 'IvaliceLegacyProficiency', nil)     -- no runtime: the single step
          local n1 = run(803, true, 1)
          return c1, c2, s1, s2, f1, f2, logs, n1
        ''')
        check('D2 control: Sunder dents 5% a hit on a foe that is not Staggered (95; 85 after three)', (got[0], got[1]) == (95, 85))
        check('D2: on a Staggered foe each hit dents twice as deep (90; 70 after three)', (got[2], got[3]) == (90, 70))
        check('D2: the floor stays at half of the start however fast the dent comes', got[5] == 50)
        check('D2: the doubled dent is logged; without the runtime the dent is the single step', got[6] >= 1 and got[7] == 95)

        # ---- D4: Reckless and Pierce under Bravery
        got = sandbox().execute(r'''
          local M = newFamily()
          CAT = 'axe'
          local wrapped = M.wrap68(function() end, deps)
          local caster = wielder()
          local function swing(brave, foe)
            local g = {action = M.ATTACK, caster = {statusEffects = {bravery = brave and 1 or 0}}, target = {defense = -1}}
            wrapped(g, caster, foe or foeUnit(900), {}, {}, {})
          end
          swing(false); NOW = NOW + 1000000; swing(false)
          local twoSteps = M.recklessDent(caster, deps)                       -- control: two plain swings, two steps
          NOW = NOW + 1000000; swing(true)                                    -- Brave: no new step
          local brave = M.recklessDent(caster, deps)
          local fades = (function() NOW = NOW + 9500000; return M.recklessDent(caster, deps) end)()  -- 10.5 s after the last unbraced swing
          M.reset()
          CAT = 'axe'
          local other = wielder(2)
          local function openBrave()
            local g = {action = M.ATTACK, caster = {statusEffects = {bravery = 1}}, target = {defense = -1}}
            wrapped(g, other, foeUnit(901), {}, {}, {})
          end
          openBrave(); openBrave()
          local fromBrave = M.recklessDent(other, deps)                       -- a Brave axe fighter never builds a step
          local unreadable = (function()
            local g = {action = M.ATTACK, caster = {}, target = {defense = -1}}
            wrapped(g, wielder(3), foeUnit(902), {}, {}, {})
            return M.recklessDent(wielder(3), deps)
          end)()
          return twoSteps, brave, fades, fromBrave, unreadable
        ''')
        check('D4 control: two plain axe swings are two Reckless steps (+10%)', abs(got[0] - 0.10) < 1e-9)
        check('D4: a swing while Brave adds no step (still +10%), and the steps fade after 10 s as before', abs(got[1] - 0.10) < 1e-9 and got[2] == 0)
        check('D4: an axe fighter who is Brave from the start never builds a step', got[3] == 0)

        got = sandbox().execute(r'''
          local M = newFamily()
          CAT = 'spear'
          local function pierce(opts)
            local f = {action = M.ATTACK, outcomeType = 0, modifier = 100, caster = {statusEffects = {bravery = opts.brave and 1 or 0}},
                       target = {removedHp = opts.damage == nil and 50 or opts.damage}}
            local wrapped = M.pierceWrap(function(fm) end, deps)
            wrapped(f, wielder(1), opts.foe or foeUnit(900), {}, {}, {})
            return f
          end
          local foe = foeUnit(900)
          local ctl = pierce({foe = foe})                                               -- control: not Brave
          local plain = M.isBroken(foe, NOW)
          local modifierKept = ctl.modifier
          local braveFoe = foeUnit(901)
          pierce({brave = true, foe = braveFoe})
          local brave = M.isBroken(braveFoe, NOW)
          local by = M.brokenBy(braveFoe, NOW)
          local missFoe = foeUnit(902)
          pierce({brave = true, foe = missFoe, damage = 0})                              -- a hit that dealt nothing breaks nothing
          local miss = M.isBroken(missFoe, NOW)
          CAT = 'sword'
          local swordFoe = foeUnit(903)
          pierce({brave = true, foe = swordFoe})
          local notSpear = M.isBroken(swordFoe, NOW)
          CAT = 'spear'
          local partyTarget = wielder(5)
          pierce({brave = true, foe = partyTarget})
          local ally = M.isBroken(partyTarget, NOW)
          local logs = count('pierce-broken')
          return plain, modifierKept, brave, by ~= nil, miss, notSpear, ally, logs
        ''')
        check('D4 control: a spear hit by a wielder who is not Brave Breaks nothing; the Defence term is restored (100)', got[0] is False and got[1] == 100)
        check('D4: a spear hit while Brave Breaks the foe and records the wielder', got[2] is True and got[3] is True)
        check('D4 control: a hit that dealt no damage, a non-spear and a party target Break nothing', got[4] is False and got[5] is False and got[6] is False)
        check('D4: the Brave break is logged once per hit', got[7] == 1)

        # ---- D5: Channel (staff) needs Faith
        got = sandbox().execute(r'''
          local M = newFamily()
          local function chan(opts)
            CAT = opts.cat or 'staff'
            local foe = opts.foe or foeUnit(900 + (opts.n or 0))
            local f = {action = 60, caster = {statusEffects = {faith = opts.faith and 1 or 0}}, target = {removedHp = opts.damage or 80}}
            if opts.noStatus then f.caster = {} end
            local caster = wielder(1, opts.casterExtra)
            local noted = M.channel(f, caster, foe, deps)
            return noted, M.isBroken(foe, NOW), M.brokenBy(foe, NOW) == caster.address
          end
          local ctl = {chan({n = 1})}                                              -- control: no Faith
          local ok = {chan({n = 2, faith = true})}
          local rod = {chan({n = 3, faith = true, cat = 'rod'})}
          local zero = {chan({n = 4, faith = true, damage = 0})}
          local heal = {chan({n = 5, faith = true, damage = -1})}
          local viaUnit = {chan({n = 6, noStatus = true, casterExtra = {temporaryStatusEffects = {faith = 1}}})}
          local allyTarget = {chan({n = 7, faith = true, foe = wielder(7)})}
          ACTIVE = false
          local inactive = {chan({n = 8, faith = true})}
          ACTIVE = true
          return ctl[1], ctl[2], ok[1], ok[2], ok[3], rod[2], zero[2], heal[2], viaUnit[2], allyTarget[2], inactive[2]
        ''')
        check('D5 control: a staff damage spell without Faith Breaks nothing', got[0] is False and got[1] is False)
        check('D5: a staff damage spell cast with Faith Breaks the foe and records the caster', got[2] is True and got[3] is True and got[4] is True)
        check('D5 control: a rod, a spell that dealt nothing, a heal, an ally target and an inactive campaign Break nothing',
              got[5] is False and got[6] is False and got[7] is False and got[9] is False and got[10] is False)
        check('D5: Faith read from the caster unit counts when the spell record has no status table', got[8] is True)

        # ---- D8: True aim interrupt, with a stub push helper
        got = sandbox().execute(r'''
          local M = newFamily()
          PUSHED = {}
          local function setup(runtime)
            rawset(_G, 'IvaliceLegacyProficiency', runtime and {pushFoe = function(w, address, fraction, by)
              PUSHED[#PUSHED + 1] = {w = w, address = address, fraction = fraction, by = by}
              return w.currentChargeTime, w.currentChargeTime - 100
            end} or nil)
          end
          local function shot(opts)
            CAT = opts.cat or 'gun'
            local work = {address = 5, currentChargeTime = opts.cur or 25000, requiredChargeTime = 30000, lastChargeTime = opts.cur or 25000}
            local f = {action = opts.action or M.ATTACK, caster = {}, target = {removedHp = opts.damage or 40, battleUnitWork = work}}
            M.wrap165(function() end, deps)(f, wielder(1), foeUnit(900), {}, {}, {})
          end
          setup(true)
          shot({})
          local pushed = #PUSHED
          local call = PUSHED[1]
          shot({cur = 20000}); local early = #PUSHED            -- control: charge at 2/3 is not about to act
          shot({cur = 22500}); local edge = #PUSHED             -- exactly 3/4 is
          shot({cat = 'bow'}); local bow = #PUSHED
          shot({damage = 0}); local nothing = #PUSHED
          shot({action = 60}); local spell = #PUSHED
          shot({cur = 30000}); local full = #PUSHED             -- a full charge is acting, not about to act
          setup(false)
          shot({})
          local noRuntime = #PUSHED
          return pushed, call.fraction, call.by ~= nil, early, edge, bow, nothing, spell, full, noRuntime, count('true-aim-push')
        ''')
        check('D8: a gun hit on a foe about to act asks for a 10% push, naming the shooter', got[0] == 1 and abs(got[1] - 0.10) < 1e-9 and got[2] is True)
        check('D8 control: a foe at 2/3 charge is not pushed; at exactly 3/4 it is', got[3] == 1 and got[4] == 2)
        check('D8 control: a bow, a hit that dealt nothing, a spell and a full charge push nothing', got[5] == 2 and got[6] == 2 and got[7] == 2 and got[8] == 2)
        check('D8: without the Proficiency runtime nothing is pushed and nothing fails', got[9] == 2)
        check('D8: each interrupt is logged', got[10] == 2)

        # ---------------- Part B ----------------
        EXTRA = r'''
        SECOND = {}
        memory.getSymbol = function(name) if name == 'sa_saveMem' then return 0x50000 end end
        memory.u16 = setmetatable({}, {__index = function(_, a) return SECOND[(a - 0x50000) // 4] or 0 end})
        function wear(u, main, second) u.accessory = main; SECOND[u.identifier] = second; return u end
        local W = {}
        function hook(n, f, caster, target) W[n] = W[n] or R.wrap(n, function() end); W[n](f, caster, target, nil, classes, helpers) end
        Family = load(FAMSRC, '@family')()
        Family.reset()
        assert(Family.register(), 'family register failed in the synthetic world')
        function installFamily() rawset(_G, 'IvaliceLegacyFamily', Family); rawset(_G, 'IvaliceLegacyProficiency', R) end
        function foeAt(addr, extra)
          extra = extra or {}
          extra.type, extra.address, extra.hp, extra.maxHp = 1, addr, extra.hp or 9000, extra.maxHp or 9000
          return unit(extra)
        end
        function strike(attacker, foe, status, extra)
          local f = F({initial=foe.address, target={removedHp=50, identifier=9, work=works[foe.address], status=status or {}},
                       caster={status={}}, critical=extra and extra.critical})
          reset(); R.hook165(f, attacker, foe, classes, helpers)
          return f
        end
        function stripped(f, name)
          if f.target.removedStatusEffects[STATUS[name]] == 1 then return true end
          for _, r in ipairs(LOG.removed) do if r.id == STATUS[name] then return true end end
          return false
        end
        function lineId(name)
          for i, l in ipairs(H.LINES) do if l.line == name then return H.FIRST_ID + i - 1 end end
        end
        function stageOf(name, k) for _, l in ipairs(H.LINES) do if l.line == name then return l[k] end end end
        local function listsOp(stage, ...)
          local want = {...}
          for _, fx in ipairs(stage[5]) do for _, op in ipairs(want) do if fx[1] == op then return true end end end
          return false
        end
        function hasOp(name, k, ...) return listsOp(stageOf(name, k), ...) end
        function hasCond(name, k, cond) for _, c in ipairs(stageOf(name, k)[2] or {}) do if c == cond then return true end end return false end
        '''


        def world(rank=12):
            lua = LuaRuntime(unpack_returned_tuples=True)
            over = {'accessory_effects': (ROOT / 'prototypes/accessory_effects.lua').read_text(encoding='utf-8')}
            runtime_world.world(lua, rank=rank, overrides=over, loader=lambda n: (ROOT / 'prototypes' / f'{n}.lua').read_text(encoding='utf-8'))
            lua.globals().FAMSRC = (ROOT / 'prototypes/family_identities.lua').read_text(encoding='utf-8')
            lua.execute(EXTRA)
            return lua


        # ---- D2: the push budget as Staggered
        got = world().execute(r'''
          installFamily()
          local function foe(addr, opts) return foeAt(addr, opts) end
          local a = foe(900, {charge = 20000, required = 30000})
          local w = works[a.address]
          local before = R.isStaggered(a.address, w)                         -- control: nothing pushed yet
          local allowed1 = R.pushAllowance(a.address, w, 5000, 1111)         -- cap = 25% of 30000 = 7500
          local mid = R.isStaggered(a.address, w)                            -- control: budget not spent
          local allowed2 = R.pushAllowance(a.address, w, 5000, 2222)         -- only 2500 left: this push completes the budget
          local on, by = R.isStaggered(a.address, w)
          local third = R.pushAllowance(a.address, w, 100, 3333)             -- nothing left
          local stillBy = select(2, R.isStaggered(a.address, w))
          R.foeActed(a.address)                                              -- the foe acted
          local afterAct = R.isStaggered(a.address, w)
          -- a charge found below where the last push left it is a new cycle
          local b = foe(901, {charge = 20000, required = 30000})
          local wb = works[b.address]
          R.pushAllowance(b.address, wb, 9999, 1)
          local spent = R.isStaggered(b.address, wb)
          wb.currentChargeTime = 12000                                       -- below where the push left it (12500)
          local newCycle = R.isStaggered(b.address, wb)
          -- bosses and marks: 12.5%
          local boss = foe(902, {charge = 20000, required = 30000, name = 'bossy'})
          local wbo = works[boss.address]
          R.pushAllowance(boss.address, wbo, 2000, 1)
          local bossHalf = R.isStaggered(boss.address, wbo)
          R.pushAllowance(boss.address, wbo, 2000, 1)
          local bossFull = R.isStaggered(boss.address, wbo)
          local viaTables = select(1, R.foeStaggered(boss.address, classes, helpers))
          local unreadable = select(1, R.foeStaggered(99999, classes, helpers))
          return before, allowed1, mid, allowed2, on, by, third, stillBy, afterAct, spent, newCycle, bossHalf, bossFull, viaTables, unreadable
        ''')
        check('D2 control: a foe nothing has pushed, or one with budget left, is not Staggered', got[0] is False and got[2] is False)
        check('D2: the push that uses up the budget Staggers the foe and is remembered (the budget itself is unchanged: 7500 of 30000)',
              got[1] == 5000 and got[3] == 2500 and got[4] is True and got[5] == 2222 and got[6] == 0 and got[7] == 2222)
        check('D2: a foe that acts is no longer Staggered', got[8] is False)
        check('D2: a charge found below where the last push left it starts a new cycle (not Staggered)', got[9] is True and got[10] is False)
        check('D2: a boss\'s budget is 12.5% (not Staggered at 2000 of 3750, Staggered at 3750), also read through the Forge tables',
              got[11] is False and got[12] is True and got[13] is True and got[14] is False)

        # ---- D2/D3 hybrid stages and conditions (data + conditions)
        got = world().execute(r'''
          local function cond(name, s, ev) return H.cond(name, s, {}, ev) end
          local staggered = {cond('staggered', {staggered = true}, 'hit'), cond('staggered', {}, 'hit'),
                             cond('staggered', {attackerStaggered = true}, 'counter'), cond('staggered', {staggered = true}, 'counter')}
          local stages = {}
          for _, name in ipairs({'Last Rites', 'Barrage', 'Field Kit', 'Hex Round'}) do
            stages[#stages + 1] = hasCond(name, 1, 'staggered') and not hasOp(name, 1, 'push', 'pushOther')
          end
          local ev = {stageOf('Last Rites', 1)[1], stageOf('Barrage', 1)[1], stageOf('Field Kit', 1)[1], stageOf('Hex Round', 1)[1]}
          -- the payoffs
          local S = function(job, t)
            local s = {job = job, actorId = 1, self = 100, targetId = 9, target = 900, foe = true, kill = false, damage = 100,
                       targetStatus = {}, casterStatus = {}, targetMp = 500, targetMaxMp = 1000, casterHpPct = 100, casterHp = 1000,
                       casterMp = 500, casterMaxMp = 1000, selfMaxHp = 1000, targetHpPct = 80,
                       party = {{address = 100, job = job, hpPct = 100, hp = 1000, maxHp = 1000, mp = 500, maxMp = 1000, status = {}},
                                {address = 200, job = 0, hpPct = 40, hp = 400, maxHp = 1000, mp = 5, maxMp = 100, status = {}}},
                       foes = {{address = 901, status = {}}}, charge = {current = 25000, required = 30000, last = 25000},
                       roll = function() return true end, now = 9000000000, element = 1, castId = 1, action = 60, landed = {8}}
            for k, v in pairs(t or {}) do s[k] = v end
            return s
          end
          local function fires(job, ev, t) return P.decide(ev, S(job, t), 4) ~= nil end
          local barrage, fieldKit = lineId('Barrage'), lineId('Field Kit')
          local bOn = P.decide('magick', S(barrage, {staggered = true}), 4)
          local bOff = P.decide('magick', S(barrage, {staggered = false}), 4)
          local fOn = P.decide('status', S(fieldKit, {staggered = true}), 4)
          local mpBack = bOn and bOn.others and #bOn.others > 0
          local noPush = true
          for _, a in ipairs({bOn, fOn}) do for _, o in ipairs(a and a.others or {}) do if o.op == 'push' then noPush = false end end end
          return staggered[1], staggered[2], staggered[3], staggered[4], stages[1], stages[2], stages[3], stages[4],
                 ev[1], ev[2], ev[3], ev[4], bOn ~= nil, mpBack, bOff == nil, fOn ~= nil, noPush
        ''')
        check('D2: the staggered condition reads the foe (a hit) or the attacker foe (a counter), and is false when not set',
              got[0] is True and got[1] is False and got[2] is True and got[3] is False)
        check('D2: Last Rites I, Barrage I, Field Kit I and Hex Round I read Staggered and no longer push', all(got[4:8]))
        check('D2: the stages keep their events (hit, magick, status) and Hex Round I now listens to the counter',
              (got[8], got[9], got[10], got[11]) == ('hit', 'magick', 'status', 'counter'))
        check('D2: Barrage I pays MP on a Staggered foe and not otherwise; Field Kit I heals on one; neither pushes',
              got[12] is True and got[13] is True and got[14] is True and got[15] is True and got[16] is True)

        # ---- D3: Broken counts as an ailment (Pursuit I, Hunter III, the hybrid tests, dmgAiling)
        got = world().execute(r'''
          PARTY_IDS = {1}
          installFamily()
          local hunter = party(1, {job1 = P.JOB.hunter, weapon = 0x1008})
          local function f(addr, opts) return foeAt(addr, opts) end
          local function dmg(foe, status) local fm = strike(hunter, foe, status); return fm.target.removedHp end
          -- Pursuit I: +15% on an ailing foe, nothing on a foe with neither an ailment nor Broken (control)
          local plain = f(900)
          local a = P.decide('hit', {job = P.JOB.hunter, actorId = 1, L = {4, 0, 0}, foe = true, targetStatus = {}, targetId = 1, party = {}, foes = {},
                                     casterStatus = {}, self = 1, damage = 50}, 12)
          local function pursuit(status, L)
            local s = {job = P.JOB.hunter, actorId = 2, L = L or {1, 0, 0}, foe = true, targetStatus = status, targetId = 7, party = {}, foes = {},
                       casterStatus = {}, self = 1, damage = 50, kill = false}
            local out = P.rules[P.JOB.hunter].hit
            local act = {}
            out(s, act)
            return act.damage
          end
          local ctl = pursuit({})
          local ail = pursuit({poison = true})
          local brk = pursuit({broken = true})
          local brkKill = (function()
            local s = {job = P.JOB.hunter, actorId = 3, L = {0, 0, 1}, foe = true, targetStatus = {broken = true}, targetId = 7, party = {}, foes = {}, casterStatus = {}, self = 1, kill = true, damage = 50}
            local act = {}
            P.rules[P.JOB.hunter].hit(s, act)
            return act.casterAdd ~= nil and act.casterAdd[P.STATUS.haste] ~= nil
          end)()
          -- the runtime snapshot carries `broken` only while the family clock says so, and only for foes
          local foe = f(901)
          local before = R.foeStatusSet(flags({}), foe, NOW).broken
          Family.noteBroken(foe, NOW, 1)
          local afterNote = R.foeStatusSet(flags({}), foe, NOW).broken
          local poisonAlso = R.foeStatusSet(flags({poison = true}), foe, NOW)
          local expired = R.foeStatusSet(flags({}), foe, NOW + 10000001).broken
          -- the hybrid 'ailing' tests: Broken is one ailment; ailing2 still needs two real ones
          local c = function(name, ts) return H.cond(name, {targetStatus = ts}, {}, 'hit') end
          local ailingBroken = c('ailing', {broken = true})
          local ailing2Broken = c('ailing2', {broken = true})
          local ailing2One = c('ailing2', {broken = true, poison = true})
          local ailing2Two = c('ailing2', {poison = true, blind = true})
          local ailingNone = c('ailing', {})
          local reactionBroken = H.cond('ailing', {attackerStatus = {broken = true}, targetStatus = {}}, {}, 'counter')
          return ctl, ail, brk, brkKill, before, afterNote, expired, poisonAlso.poison, poisonAlso.broken, ailingBroken, ailing2Broken,
                 ailing2One, ailing2Two, ailingNone, reactionBroken
        ''')
        check('D3 control: Pursuit I does nothing to a foe with no ailment and not Broken', got[0] is None)
        check('D3: Pursuit I adds +15% on an ailing foe, and the same on a Broken foe', abs(got[1] - 1.15) < 1e-9 and abs(got[2] - 1.15) < 1e-9)
        check('D3: the Hunter\'s Haste on a kill reads Broken too', got[3] is True)
        check('D3: the foe snapshot carries `broken` only while the clock says so (not before a cut, not after 10 s)',
              got[4] is None and got[5] is True and got[6] is None)
        check('D3: the snapshot keeps real statuses next to the pseudo-flag', got[7] is True and got[8] is True)
        check('D3: the hybrid ailing test reads Broken (hits and reactions); two ailments still need two real ones',
              got[9] is True and got[10] is False and got[11] is False and got[12] is True and got[13] is False and got[14] is True)

        # ---- D3/D2: Hyper Wrist reads what an ally did
        got = world().execute(r'''
          PARTY_IDS = {1, 2}
          installFamily()
          local wrist = wear(party(1, {weapon = 0x1008}), 4444)
          local ally = party(2, {weapon = 0x1008})
          local function hitWith(foe)
            local x = F({initial = foe.address, target = {removedHp = 100, identifier = 9, work = works[foe.address]}, caster = {status = {}}})
            reset()
            hook(165, x, wrist, foe)
            return R.alliedHold(wrist, foe, NOW, works[foe.address])
          end
          local function quick(foe)
            local x = F({initial = foe.address, target = {removedHp = 100, identifier = 9, work = works[foe.address]}, caster = {status = {}}})
            local a
            local spy = R.Accessory.decide
            R.Accessory.decide = function(ev, s) a = spy(ev, s); return a end
            reset()
            R.accessory165(x, wrist, foe, classes, helpers)
            R.Accessory.decide = spy
            return a ~= nil and a.casterCharge or nil
          end
          local plain = foeAt(900, {charge = 20000, required = 30000})
          local ctl = quick(plain)                                                  -- control: nothing an ally did
          local broken = foeAt(901, {charge = 20000, required = 30000})
          Family.noteBroken(broken, NOW, ally.address)
          local allyBroken = quick(broken)
          local own = foeAt(902, {charge = 20000, required = 30000})
          Family.noteBroken(own, NOW, wrist.address)
          local ownBroken = quick(own)                                              -- the wearer's own cut does not count
          local nobody = foeAt(903, {charge = 20000, required = 30000})
          Family.noteBroken(nobody, NOW)
          local noBy = quick(nobody)                                                -- nobody recorded: not an ally's
          local st = foeAt(904, {charge = 20000, required = 30000})
          local w = works[st.address]
          R.pushAllowance(st.address, w, 9999, ally.address)
          local allyStag = quick(st)
          local own2 = foeAt(905, {charge = 20000, required = 30000})
          R.pushAllowance(own2.address, works[own2.address], 9999, wrist.address)
          local ownStag = quick(own2)
          local half = foeAt(906, {charge = 20000, required = 30000})
          R.pushAllowance(half.address, works[half.address], 3000, ally.address)
          local notYet = quick(half)                                                -- budget not spent: not Staggered
          local ail = foeAt(907, {charge = 20000, required = 30000, status = {poison = true}})
          return ctl, allyBroken, ownBroken, noBy, allyStag, ownStag, notYet
        ''')
        check('D3 control: Hyper Wrist does nothing on a foe nobody weakened', got[0] is None)
        check('D3: Hyper Wrist fires (10% faster) on a foe an ally Broke', got[1] is not None and abs(got[1] - 0.10) < 1e-9)
        check('D3 control: the wearer\'s own Broken, and a Broken with nobody recorded, do not count', got[2] is None and got[3] is None)
        check('D2: Hyper Wrist fires on a foe an ally Staggered, not on one the wearer Staggered, not on one with budget left',
              got[4] is not None and abs(got[4] - 0.10) < 1e-9 and got[5] is None and got[6] is None)

        # ---- D4/D5: the hybrid conditions and stages
        got = world().execute(r'''
          local c = function(name, cs) return H.cond(name, {casterStatus = cs}, {}, 'hit') end
          local frenzy, havoc = stageOf('Frenzy', 1), stageOf('Havoc', 1)
          local imbue, rune = stageOf('Imbue', 2), stageOf('Runeguard', 2)
          local function selfGrant(stage, id) for _, fx in ipairs(stage[5]) do if fx[1] == 'self' and fx[2] == id then return true end end return false end
          local function allyGrant(stage, id) for _, fx in ipairs(stage[5]) do if fx[1] == 'ally' and fx[2] == id then return true end end return false end
          local frenzyAlly = false
          for _, fx in ipairs(frenzy[5]) do if fx[1] == 'ally' and fx[2] == 'bravery' and fx.fresh then frenzyAlly = true end end
          return c('brave', {bravery = true}), c('brave', {}), c('faithful', {faith = true}), c('faithful', {}),
                 hasCond('Frenzy', 1, 'brave'), hasCond('Havoc', 1, 'brave'), not hasCond('Frenzy', 1, 'low50'), not hasCond('Havoc', 1, 'low50'),
                 frenzyAlly, not selfGrant(frenzy, 'bravery'),
                 hasCond('Imbue', 2, 'imbuedOrFaith'), not hasCond('Imbue', 2, 'imbued') and not hasCond('Imbue', 2, 'faithful'),
                 allyGrant(imbue, 'faith'), not selfGrant(imbue, 'faith'),
                 hasCond('Runeguard', 2, 'lureOrFaith'), not hasCond('Runeguard', 2, 'lured') and not hasCond('Runeguard', 2, 'faithful'),
                 allyGrant(rune, 'faith'), not selfGrant(rune, 'faith')
        ''')
        check('D4/D5: the brave and faithful conditions read the caster\'s Bravery and Faith', got[0] is True and got[1] is False and got[2] is True and got[3] is False)
        check('D4: Frenzy I and Havoc I read Brave instead of "below half HP"; Frenzy I-3 gives an ally Bravery, not the wielder',
              all(got[4:10]))
        check('0.1.45 R7: Imbue II and Runeguard II read Faith OR their old trigger (one condition each) and pass their Faith grant to an ally',
              all(got[10:18]))

        # ---- D12: Magebreaker reads a quarter of the foe's MP
        got = world().execute(r'''
          local c = function(name, t) return H.cond(name, t, {}, 'hit') end
          local quarter = c('lowMp', {targetMp = 25, targetMaxMp = 100})
          local over = c('lowMp', {targetMp = 26, targetMaxMp = 100})
          local zero = c('lowMp', {targetMp = 0, targetMaxMp = 100})
          local nopool = c('lowMp', {targetMp = 0, targetMaxMp = 0})
          local unknown = c('lowMp', {targetMp = 0})
          local react = H.cond('lowMp', {attackerMp = 10, attackerMaxMp = 40}, {}, 'guard')
          local emptyStill = c('emptyMp', {targetMp = 5, targetMaxMp = 100})        -- control: emptyMp still means an empty pool
          local u1, u3 = stageOf('Unmake', 1), stageOf('Unmake', 3)
          local function has(stage, name) for _, x in ipairs(stage[2]) do if x == name then return true end end return false end
          local phrase1, phrase3 = u1[3], u3[3]
          return quarter, over, zero, nopool, unknown, react, emptyStill, has(u1, 'lowMp'), has(u3, 'lowMp'), has(u3, 'buffless'),
                 not has(u1, 'emptyMp'), not has(u3, 'emptyMp'), phrase1, phrase3
        ''')
        check('D12: lowMp is true at a quarter of the pool or less (25 of 100, 0 of 100), false above (26), never with no or unknown pool',
              got[0] is True and got[1] is False and got[2] is True and got[3] is False and got[4] is False)
        check('D12: lowMp reads the attacker on reactions; control: emptyMp still needs an empty pool', got[5] is True and got[6] is False)
        check('D12: Unmake I and III read lowMp (III keeps "bare") and no longer emptyMp', all(got[7:12]))
        check('D12: the stage wording says a quarter of its MP or less', 'quarter of its MP' in got[12] and 'quarter of its MP' in got[13])
        check('D12: Mana Leech I-2 ("drained to 0 MP: Silence") is unchanged', 'A foe drained down to 0 MP is Silenced' in rd('prototypes/proficiency.lua'))

        # ---- D10: Resonance I counts an ally's same-element hit
        got = world().execute(r'''
          PARTY_IDS = {1, 2}
          installFamily()
          bpack.section14[40] = {category = 1, mpOrMistCost = 20, elements = flags({fire = true})}
          local bm = party(1, {job1 = P.JOB.blackMage, weapon = 0x1008})
          local ally = party(2, {weapon = 0x1008})
          local A = R.Accessory
          local function rule(partyElement, element, chain)
            local s = {job = P.JOB.blackMage, actorId = 40 + (chain or 0) + (partyElement and 100 or 0), L = {1, 0, 0}, element = element or 1, castId = 1,
                       now = NOW, mpCost = 10, foe = true, targetStatus = {}, party = {}, foes = {}, casterStatus = {}, self = 1,
                       partyElement = partyElement}
            local a = {}
            P.rules[P.JOB.blackMage].magick(s, a)
            return a.casterCharge
          end
          local ctl = rule(nil)                       -- control: the first cast of an element charges nothing
          local withAlly = rule(true)                 -- an ally's hit of this element on the foe
          -- the getter
          local foe = foeAt(900)
          A.noteElement(foe.address, ally.address, 1, NOW)
          local getter = A.partyElement(foe.address, bm.address, 1, NOW + 1000000)
          local otherElement = A.partyElement(foe.address, bm.address, 2, NOW + 1000000)
          local stale = A.partyElement(foe.address, bm.address, 1, NOW + 5000001)
          local edge = A.partyElement(foe.address, bm.address, 1, NOW + 5000000)
          local ownHit = A.partyElement(foe.address, ally.address, 1, NOW + 1000000)      -- the caster's own hit is not an ally's
          -- through hook 170: the runtime hands the flag to the rule
          local seen
          local realDecide = P.decide
          P.decide = function(ev, s, rank) if ev == 'magick' then seen = s.partyElement end return realDecide(ev, s, rank) end
          local function cast(foeUnit)
            local f = F({initial = foeUnit.address, target = {removedHp = 100, identifier = 9, work = works[foeUnit.address]}, caster = {status = {}}})
            f.action = 40
            seen = nil
            R.hook170(f, bm, foeUnit, classes, helpers)
            return seen
          end
          local hooked = cast(foe)
          local quiet = foeAt(901)
          local hookedQuiet = cast(quiet)
          P.decide = realDecide
          return ctl, withAlly, getter, otherElement, stale, edge, ownHit, hooked, hookedQuiet
        ''')
        check('D10 control: Resonance I charges nothing on a first cast with no party element', got[0] is None)
        check('D10: an ally\'s same-element hit on the foe makes Resonance I charge the next action (20%)', got[1] is not None and abs(got[1] - 0.20) < 1e-9)
        check('D10: the party-element getter: another member, same element, within 5 s (the edge counts)',
              got[2] is True and got[5] is True)
        check('D10 control: a different element, a hit older than 5 s and the caster\'s own hit do not count',
              got[3] is False and got[4] is False and got[6] is False)
        check('D10: hook 170 passes the flag (true next to an ally\'s hit, false on a foe nobody hit)', got[7] is True and got[8] is False)

        # ---- D8 real: the gun interrupt inside the shared push budget
        got = world().execute(r'''
          PARTY_IDS = {1}
          installFamily()
          for i = 0, 63 do bpack.section13[i] = {category = 9, chargeTime = 50} end       -- every weapon is a gun
          local gunner = party(1, {weapon = 0x1001})
          local function shoot(foe)
            local w = works[foe.address]
            local f = F({initial = foe.address, target = {removedHp = 60, identifier = 9, work = w}, caster = {status = {}}})
            f.target.battleUnitWork = w
            reset()
            Family.wrap165(function() end)(f, gunner, foe, {}, classes, helpers)
            return w.currentChargeTime
          end
          local foe = foeAt(900, {charge = 29000, required = 30000})                     -- 97%: about to act
          local first = shoot(foe)                                                      -- 10% of 30000 = 3000 -> 26000
          local w = works[foe.address]
          w.lastChargeTime = w.currentChargeTime                                          -- the game's tick settles last = current
          local second = shoot(foe)                                                     -- 23000 (still about to act: 3/4 is 22500)
          w.lastChargeTime = w.currentChargeTime
          local third = shoot(foe)                                                      -- 3000 wanted, 1500 of the 7500 budget left: 21500
          local staggered, by = R.isStaggered(foe.address, w)
          local boss = foeAt(901, {charge = 29000, required = 30000, name = 'bossy'})   -- 12.5% = 3750: one push of 3000, then 750 left
          local b1 = shoot(boss)
          works[boss.address].lastChargeTime = works[boss.address].currentChargeTime
          local b2 = shoot(boss)
          local bStag = R.isStaggered(boss.address, works[boss.address])
          local calm = foeAt(902, {charge = 12000, required = 30000})                    -- control: 40% is not about to act
          local c1 = shoot(calm)
          return first, second, third, staggered, by ~= nil, b1, b2, bStag, c1
        ''')
        check('D8: a gun hit on a foe about to act pushes it 10% (29000 -> 26000), then again (23000)', got[0] == 26000 and got[1] == 23000)
        check('D8: the interrupt stops at the shared budget (25%: 7500 of 30000, so 29000 -> 21500 at the most) and then the foe is Staggered, by the shooter',
              got[2] == 21500 and got[3] is True and got[4] is True)
        check('D8: a boss\'s budget is 12.5% (3750): 29000 -> 26000, then only 750 more', got[5] == 26000 and got[6] == 25250 and got[7] is True)
        check('D8 control: a foe at 40% charge is not pushed', got[8] == 12000)

        # ---- D5 real: Channel through hook 170 (a staff Black Mage with Faith Breaks the foe; the Lancer then strips)
        got = world().execute(r'''
          PARTY_IDS = {1}
          installFamily()
          bpack.section14[40] = {category = 1, mpOrMistCost = 20, elements = flags({fire = true})}
          bpack.section13[5] = {category = 14, chargeTime = 30}                          -- 0x1005: a staff
          local mage = party(1, {job1 = P.JOB.blackMage, weapon = 0x1005})
          local function cast(foe, faith)
            local f = F({initial = foe.address, target = {removedHp = 100, identifier = 9, work = works[foe.address]},
                          caster = {status = faith and {faith = true} or {}}})
            f.action = 40
            reset()
            R.hook170(f, mage, foe, classes, helpers)
          end
          local a = foeAt(900)
          cast(a, false)
          local ctl = R.isBroken(a, NOW)                                                  -- control: no Faith
          local b = foeAt(901)
          cast(b, true)
          local broke = R.isBroken(b, NOW)
          local by = R.brokenBy(b, NOW) == mage.address
          NOW = NOW + 10000001
          local expired = R.isBroken(b, NOW)
          return ctl, broke, by, expired
        ''')
        check('D5 control: a staff spell without Faith leaves the foe unbroken (through hook 170)', got[0] is False)
        check('D5: a staff damage spell cast with Faith Breaks the foe, the caster is recorded, and it ends after 10 s', got[1] is True and got[2] is True and got[3] is False)

        # ---- carried 0.1.43 checks: Grounding II on Broken, Cameo Spotter, budgets
        got = world().execute(r'''
          PARTY_IDS = {1}
          installFamily()
          local lancer = party(1, {job1 = P.JOB.lancer})
          local buffs = {protect = true, shell = true}
          local a = strike(lancer, foeAt(900), buffs)
          local control = stripped(a, 'protect')
          local b0 = foeAt(901)
          Family.noteBroken(b0, NOW, 5)
          local b = strike(lancer, b0, buffs)
          local broken = stripped(b, 'protect')
          local c0 = foeAt(902)
          Family.noteBroken(c0, NOW, 5)
          NOW = NOW + 10000001
          local expired = stripped(strike(lancer, c0, buffs), 'protect')
          local d = stripped(strike(lancer, foeAt(903), {protect = true, shell = true, poison = true}), 'protect')
          -- a recycled address: a Broken foe's entry does not make the next foe at that address Broken
          local old = foeAt(904, {maxHp = 9000})
          Family.noteBroken(old, NOW, 5)
          local newFoe = foeAt(904, {maxHp = 5000})
          local recycled = stripped(strike(lancer, newFoe, buffs), 'protect')
          return control, broken, expired, d, recycled
        ''')
        check('Grounding II control: a foe with no ailment and not Broken keeps its Protect', got[0] is False)
        check('Grounding II: a Broken foe loses its Protect; Broken ends after 10 s; an ailment still opens it', got[1] is True and got[2] is False and got[3] is True)
        check('Broken fix through the runtime: another foe at a recycled address is not Broken', got[4] is False)

        got = world().execute(r'''
          PARTY_IDS = {1, 2}
          local function foe(addr) return foeAt(addr) end
          local function f(id) local x = F(); x.identifier = id; return x end
          local function hit(caster, target, crit)
            local x = F({initial=target.address, critical=crit, target={removedHp=100, identifier=9, work=works[target.address]}, caster={status={}}})
            hook(165, x, caster, target)
            return x
          end
          local cameo = wear(party(1, {weapon=0x1008}), 4468)
          local ally = party(2, {weapon=0x1008})
          local t1 = foe(900); hit(cameo, t1)
          local plain = R.spotter34(f(20), ally, t1, classes, helpers)
          local t2 = foe(901); hit(cameo, t2, true)
          local crit = R.spotter34(f(20), ally, t2, classes, helpers)
          local copies = #R.Accessory.copies(H.LINES)
          local bad = 0
          for id, stages in pairs(R.Accessory.STAGES) do
            for _, st in ipairs(stages) do
              for _, fx in ipairs(st[3]) do
                if fx[2] == 'haste' or fx[2] == 'bravery' or fx[1] == 'mp' or fx[1] == 'push' or fx[1] == 'pushOther' then bad = bad + 1 end
              end
            end
          end
          local dropped = 0
          for _, stages in pairs(R.Accessory.STAGES) do for _, st in ipairs(stages) do if st.dropped then dropped = dropped + 1 end end end
          return plain, crit, copies, bad, dropped
        ''')
        check('Cameo control: a plain first hit does not mark; a critical hit does', got[0] is False and got[1] is True)
        check('accessory rules hold: no stage copies a hybrid stage (after the four hybrid changes), none is dropped', got[2] == 0 and got[4] == 0)
        check('accessory rules hold: no accessory Haste, Bravery, MP or push', got[3] == 0)

        self_.assertGreaterEqual(len(results), 100)
        self_.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
