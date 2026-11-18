"""Review fixes of Ivalice Legacy 0.1.46: an axe hit on a foe already at the Sunder floor renews Broken without a deeper
dent; hook 240 reads a foe's Staggered state once per foe action, so every target and every hit of an area or multi-hit
action hears it, and the record still ends after the action; R.alliedHold and the True aim push name the foe for its
fingerprint; the family version; the Weapon Families tip text and the gambit row 226 reason. Every change carries a control
that must not change. Static: the shipped prototypes in a synthetic battle (tests/runtime_world.py); nothing here touches
a game or a save."""
import re
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
class Review0146Tests(unittest.TestCase):
    def test_every_review_fix_and_its_control(self):
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        FAM = rd('prototypes/family_identities.lua')
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



        STUB = r'''
        STAGGER = {}
        rawset(_G, 'IvaliceLegacyProficiency', nil)
        function stubRuntime()
          rawset(_G, 'IvaliceLegacyProficiency', {foeStaggered = function(address) return STAGGER[address] == true end})
        end
        '''
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
            runtime_world.world(lua, rank=rank, overrides=over,
                                loader=lambda n: (ROOT / 'prototypes' / f'{n}.lua').read_text(encoding='utf-8'))
            lua.globals().FAMSRC = (ROOT / 'prototypes/family_identities.lua').read_text(encoding='utf-8')
            lua.execute(EXTRA)
            return lua



        # ---- A1: an axe hit at the Defence floor renews Broken and dents nothing more; a fresh dent is unchanged
        got = sandbox().execute(STUB + r"""
          local M = newFamily()
          CAT = 'axe'
          stubRuntime()
          local wrapped = M.wrap68(function() end, deps)
          local caster = wielder()
          local function swing(foe, action)
            local g = {action = action or M.ATTACK, caster = {}, target = {defense = -1}}
            wrapped(g, caster, foe, {}, {}, {})
            if g.target.defense >= 0 then foe.defense = g.target.defense end
            return g.target.defense
          end
          local out = {}
          -- a fresh dent, as before: 95, Broken by the wielder, one 'sunder' line and no floor line
          local f = foeUnit(600)
          out.first = swing(f)
          out.freshBroken, out.freshBy = M.isBroken(f, NOW), M.brokenBy(f, NOW)
          out.freshSunder, out.freshFloor = count('sunder'), count('sunder-floor')
          for i = 1, 9 do swing(f) end                                 -- ten dents: 100 -> 50, the floor
          out.floorDef, out.sunderAtFloor, out.floorLinesAtFloor = f.defense, count('sunder'), count('sunder-floor')
          NOW = NOW + 11000000                                         -- 11 s on: the last real dent's Broken has expired
          out.expired = M.isBroken(f, NOW)
          out.write = swing(f)                                         -- a hit at the floor: no Defence write ...
          out.defAfter, out.renewed, out.renewedBy = f.defense, M.isBroken(f, NOW), M.brokenBy(f, NOW)   -- ... Broken renewed
          out.sunderAfter, out.floorLines = count('sunder'), count('sunder-floor')
          -- the renewal restarts the 10 s: hit again 9 s later, still Broken 9 s after that (18 s after the first renewal)
          NOW = NOW + 9000000; swing(f); NOW = NOW + 9000000
          out.extended = M.isBroken(f, NOW)
          local g2 = foeUnit(601); for i = 1, 10 do swing(g2) end      -- control: the same history without the second floor hit
          NOW = NOW + 11000000; swing(g2); NOW = NOW + 18000000
          out.notExtended = M.isBroken(g2, NOW)
          -- a Staggered foe at the floor: no dent (the double depth needs room), Broken renewed, no 'sunder-staggered' line
          local s = foeUnit(602); for i = 1, 10 do swing(s) end
          NOW = NOW + 11000000
          STAGGER[602] = true
          local stagLines = count('sunder-staggered')
          out.stagWrite = swing(s)
          out.stagDef, out.stagRenewed = s.defense, M.isBroken(s, NOW)
          out.stagLines = count('sunder-staggered') - stagLines
          STAGGER[603] = true
          local s2 = foeUnit(603); out.stagFresh = swing(s2)           -- control: the Staggered double depth on a fresh foe (90)
          -- controls at the floor: no other family, no other action, no campaign, no Defence, no party target: nothing renews
          local function floorFoe(addr, extra) local x = foeUnit(addr, extra); for i = 1, 10 do swing(x) end; NOW = NOW + 11000000; return x end
          local c1 = floorFoe(610); CAT = 'sword'; swing(c1); CAT = 'axe'; out.otherFamily = M.isBroken(c1, NOW)
          local c2 = floorFoe(611); swing(c2, 60); out.otherAction = M.isBroken(c2, NOW)
          local c3 = floorFoe(612); ACTIVE = false; swing(c3); ACTIVE = true; out.inactive = M.isBroken(c3, NOW)
          local c4 = foeUnit(613, {defense = 0}); swing(c4); out.noDefense = M.isBroken(c4, NOW)
          local ally = wielder(2, {defense = 40, maxHp = 900, level = 9}); swing(ally); out.allyTarget = M.isBroken(ally, NOW)
          -- an Expose rider already queued lower in the same hit (not at the floor): unchanged, Sunder adds nothing
          local q = foeUnit(614)
          local g = {action = M.ATTACK, caster = {}, target = {defense = 40}}
          wrapped(g, caster, q, {}, {}, {})
          out.queuedDef, out.queuedBroken = g.target.defense, M.isBroken(q, NOW)
          -- M.dent itself: two values for a dent, nil, nil, true at the floor
          local b1, a1, fl1 = M.dent(foeUnit(615), {defense = -1}, 1)
          local e = foeUnit(617); for i = 1, 10 do swing(e) end
          local b3, a3, fl3 = M.dent(e, {defense = -1}, 1)
          return out.first, out.freshBroken, out.freshBy == caster.address, out.freshSunder, out.freshFloor,
                 out.floorDef, out.expired, out.write, out.defAfter, out.renewed, out.renewedBy == caster.address,
                 out.sunderAfter - out.sunderAtFloor, out.floorLines - out.floorLinesAtFloor,
                 out.extended, out.notExtended, out.stagWrite, out.stagDef, out.stagRenewed, out.stagLines, out.stagFresh,
                 out.otherFamily, out.otherAction, out.inactive, out.noDefense, out.allyTarget, out.queuedDef, out.queuedBroken,
                 b1, a1, fl1, b3, a3, fl3
        """)
        check('A1 control: a fresh dent is unchanged (100 -> 95, Broken by the wielder, a "sunder" line, no floor line)',
              got[0] == 95 and got[1] is True and got[2] is True and got[3] == 1 and got[4] == 0)
        check('A1: ten dents reach the floor (50); 11 s later the old Broken has expired (the instrument)', got[5] == 50 and got[6] is False)
        check('A1: an axe hit at the floor writes no Defence (the slot stays -1, the foe stays at 50) and renews Broken, the wielder recorded',
              got[7] == -1 and got[8] == 50 and got[9] is True and got[10] is True)
        check('A1: the renewal logs one "sunder-floor" line and no "sunder" (dent) line', got[11] == 0 and got[12] == 1)
        check('A1: each floor hit restarts the 10 s (Broken 18 s after the first renewal when hit again at 9 s); control without it: expired',
              got[13] is True and got[14] is False)
        check('A1: a Staggered foe at the floor is not dented deeper, is Broken again, and logs no "sunder-staggered" line',
              got[15] == -1 and got[16] == 50 and got[17] is True and got[18] == 0)
        check('A1 control: the Staggered double depth on a fresh foe is unchanged (100 -> 90)', got[19] == 90)
        check('A1 controls: at the floor, a sword, a non-Attack action, no campaign, a foe with no Defence or a party target renews nothing',
              got[20] is False and got[21] is False and got[22] is False and got[23] is False and got[24] is False)
        check('A1 control: a lower Defence an Expose rider already queued in the same hit is kept and Sunder adds no Broken (as 0.1.45)',
              got[25] == 40 and got[26] is False)
        check('A1: M.dent returns before, after on a dent and nil, nil, true at the floor',
              (got[27], got[28], got[29]) == (100, 95, None) and (got[30], got[31], got[32]) == (None, None, True))

        # ---- A3: the Staggered reading is taken once per foe action in hook 240; the record still ends after the action
        got = world().execute(r"""
          installFamily()
          PARTY_IDS = {1, 2, 3}
          local heroes = {}
          for i = 1, 3 do heroes[i] = party(i, {job1 = P.JOB.knight, weapon = 0x1001}) end
          local seen = {}
          local decide = P.decide
          P.decide = function(ev, s, rank) if ev == 'guard' then seen[#seen + 1] = s.attackerStaggered end return decide(ev, s, rank) end
          local n = 0
          local function spend(opts)                   -- a foe whose push budget is spent (cap 7500 of 30000; the push left it at 12500)
            opts = opts or {}
            n = n + 1
            local addr = opts.addr or (950 + n)
            local foe = foeAt(addr, {charge = 20000, required = 30000, maxHp = opts.maxHp or 9000, level = opts.level or 30})
            local w = works[addr]
            R.pushAllowance(addr, w, 9999, 1111, foe)
            assert(R.isStaggered(addr, w, foe) == true, 'setup: not Staggered')
            w.currentChargeTime = 30000                -- the foe is acting: its charge is full
            return foe, w
          end
          local function att(foe, tgt, action, initial)  -- one 240 call: a dodge (outcome 5), so Hex Round's guard event hears it
            hook(240, F({outcome = 5, action = action or 150, initial = initial or heroes[1].address, target = {status = {}}}), foe, tgt)
          end
          local function refilled(foe, w) w.currentChargeTime = 20000; w.lastChargeTime = 20000; return R.isStaggered(foe.address, w, foe) end
          local function list() local o = {} for i, v in ipairs(seen) do o[i] = tostring(v) end return table.concat(o, ',') end
          local out = {}
          -- an area attack: three targets in one action, one clock
          do local foe, w = spend(); seen = {}; for i = 1, 3 do att(foe, heroes[i]) end; out.area = list(); out.areaAfter = refilled(foe, w) end
          -- a multi-hit: three hits on one target, 0.2 s apart
          do
            local foe, w = spend(); seen = {}
            for i = 1, 3 do att(foe, heroes[1]); NOW = NOW + 200000 end
            out.multi = list(); out.multiAfter = refilled(foe, w)
          end
          -- control: the foe's next action (same action and first target) more than ACTION_WINDOW later reads afresh: not Staggered
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1]); NOW = NOW + 1500000; w.currentChargeTime = 30000; att(foe, heroes[1])
            out.nextAction = list()
          end
          -- controls inside the window: another action id, another first target read afresh
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1]); att(foe, heroes[2], 151); att(foe, heroes[3], 150, heroes[3].address)
            out.otherAction = list()
          end
          -- control: no clock (0): the 0.1.45 per-call rule (the first target hears it, the record ends, the next does not)
          do
            local saved = NOW; NOW = 0
            local foe, w = spend(); seen = {}
            for i = 1, 3 do att(foe, heroes[i]) end
            out.noClock = list(); NOW = saved
          end
          -- control: another foe at the same address (other maxHp and level) inside the window reads afresh
          do
            local a, wa = spend({addr = 980}); seen = {}
            att(a, heroes[1])
            local b = foeAt(980, {charge = 30000, required = 30000, maxHp = 4000, level = 12})
            att(b, heroes[2])
            out.recycled = list()
          end
          -- early exit: the first call of the action has a foe target (no party event); the party targets after it still hear it
          do
            local foe, w = spend(); seen = {}
            att(foe, foeAt(990, {})); att(foe, heroes[1]); att(foe, heroes[2])
            out.earlyExit = list(); out.earlyExitAfter = refilled(foe, w)
          end
          -- a push that makes a new record mid-action is read afresh (and ended), so a hit after it hears it
          do
            local foe = foeAt(985, {charge = 20000, required = 30000, maxHp = 9000, level = 30}); local w = works[985]
            w.currentChargeTime = 30000; seen = {}
            att(foe, heroes[1])                                       -- not Staggered when the action starts
            w.currentChargeTime = 20000
            R.pushAllowance(985, w, 9999, 1111, foe)                  -- the budget is spent mid-action: a new record
            w.currentChargeTime = 30000                               -- still acting
            att(foe, heroes[2])
            out.midPush = list(); out.midPushAfter = refilled(foe, w)
          end
          -- R.resetPushes (a load, a map jump) forgets the latch too
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1]); R.resetPushes(); att(foe, heroes[2])
            out.reset = list()
          end
          -- not in a campaign: nothing is decided, but the record still ends once per action (as 0.1.45)
          do
            local foe, w = spend(); local saved = R.active; R.active = function() return false end
            seen = {}; att(foe, heroes[1]); att(foe, heroes[2]); R.active = saved
            out.inactive = list(); out.inactiveAfter = refilled(foe, w)
          end
          return out.area, out.areaAfter, out.multi, out.multiAfter, out.nextAction, out.otherAction, out.noClock, out.recycled,
                 out.earlyExit, out.earlyExitAfter, out.midPush, out.midPushAfter, out.reset, out.inactive, out.inactiveAfter
        """)
        check('A3: an area attack: all three targets hear Staggered (0.1.45: only the first) ' + got[0], got[0] == 'true,true,true')
        check('A3: after the area attack the record has ended (a refill past it is not Staggered)', got[1] is False)
        check('A3: a multi-hit: all three hits hear Staggered ' + got[2] + '; the record has ended afterwards', got[2] == 'true,true,true' and got[3] is False)
        check('A3 control: the foe\'s next action, past ACTION_WINDOW, reads afresh (not Staggered) ' + got[4], got[4] == 'true,false')
        check('A3 control: another action id or another first target inside the window reads afresh ' + got[5], got[5] == 'true,false,false')
        check('A3 control: with no clock the 0.1.45 per-call rule applies (fail closed) ' + got[6], got[6] == 'true,false,false')
        check('A3 control: another foe at the same address inside the window reads afresh (fingerprint) ' + got[7], got[7] == 'true,false')
        check('A3: an early exit on the first call (a foe target) still reads and ends it; the party targets after it hear it ' + got[8],
              got[8] == 'true,true' and got[9] is False)
        check('A3: a push that spends a new budget mid-action is read afresh by the next hit and then ends ' + got[10],
              got[10] == 'false,true' and got[11] is False)
        check('A3: R.resetPushes forgets the action latch ' + got[12], got[12] == 'true,false')
        check('A3 control: outside a campaign nothing is decided and the record still ends', got[13] == '' and got[14] is False)

        # ---- A4: the two fingerprint callers that had no test (each mutation survived 286/286 in 0.1.45)
        got = world().execute(r"""
          PARTY_IDS = {1, 2}
          installFamily()
          local wrist = party(1, {weapon = 0x1008})
          local ally = party(2, {weapon = 0x1008})
          local a = foeAt(960, {charge = 20000, required = 30000, maxHp = 9000, level = 30})
          R.pushAllowance(960, works[960], 9999, ally.address, a)                  -- the ally spends the budget: Staggered by the ally
          local _, same = R.alliedHold(wrist, a, NOW, works[960])                  -- control: the same foe
          local b = foeAt(960, {charge = 20000, required = 30000, maxHp = 4000, level = 12})   -- another foe at the recycled address
          local _, recycled = R.alliedHold(wrist, b, NOW, works[960])
          local c = foeAt(961, {charge = 20000, required = 30000, maxHp = 9000, level = 30})
          R.pushAllowance(961, works[961], 9999, ally.address, c)
          local _, noKeep = pcall(R.isStaggered, 961, works[961])                  -- control: the address-only rule without a keep
          return same, recycled, noKeep
        """)
        check('A4 control: R.alliedHold hears an ally\'s Stagger on the same foe', got[0] is True)
        check('A4: R.alliedHold passes the foe keep to isStaggered: another foe at a recycled address is not held by the ally', got[1] is False)
        check('A4 control: without a keep, isStaggered keeps the address-only rule (what a dropped keep would read)', got[2] is True)
        got = world().execute(r"""
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
            w.lastChargeTime = w.currentChargeTime
            return w.currentChargeTime
          end
          local a = foeAt(970, {charge = 29000, required = 30000, maxHp = 9000, level = 30})
          shoot(a); shoot(a); local spentAt = shoot(a)                                   -- 29000 -> 26000 -> 23000 -> 21500: spent
          local spent = R.isStaggered(970, works[970], a)
          works[970].currentChargeTime = 29000; works[970].lastChargeTime = 29000
          local sameFoe = shoot(a)                                                      -- control: the same foe has no budget left
          local b = foeAt(970, {charge = 29000, required = 30000, maxHp = 4000, level = 12})   -- a new foe at the recycled address
          local recycled = shoot(b)
          return spentAt, spent, sameFoe, recycled, R.isStaggered(970, works[970], b)
        """)
        check('A4 control: True aim spends the budget (29000 -> 21500, Staggered) and then cannot push the same foe again',
              got[0] == 21500 and got[1] is True and got[2] == 29000)
        check('A4: the True aim push names the foe (its keep): a new foe at the recycled address gets a fresh budget (29000 -> 26000)',
              got[3] == 26000 and got[4] is False)

        # ---- the version, FAMILY_LINES and the repository's patches (the shipped text sources)
        import json
        from ivalice_legacy import clan_primer as CP
        check('A6: family_identities is 0.5.4 or later (0.1.47: 0.5.5) and its ready line prints the version',
              (lambda v: (int(v.group(1)), int(v.group(2))) >= (5, 4))(re.search(r"M\.version = '0\.(\d+)\.(\d+)'", FAM))
              and "'Legacy Family: identities ' .. M.version .. ' ready" in FAM)
        ax = re.search(r"axe = \{'Sunder', (['\"])(.*?)\1\},", FAM, re.S).group(2)
        check('A1: FAMILY_LINES.axe says every hit Breaks, twice as deep on a Staggered foe, and the dents stop at half',
              'Every hit Breaks the foe' in ax and 'stops at half, but the Breaks go on' in ax and 'twice as deep on a Staggered foe' in ax)
        tp = json.loads((ROOT / 'patches/families_tip.json').read_text(encoding='utf-8'))
        bodies = [t['body'] for t in CP.tips(tp)]
        check('tips: Sunder says every hit Breaks and the dents stop at half; Broken is an ailment for job and hybrid triggers, not gambits',
              'Every hit {y}Breaks{/} the foe' in bodies[0] and 'down to half. The Breaks go on after that.' in bodies[0].replace(chr(10) + '  ', ' ')
              and 'gambits do not.' in bodies[0].replace(chr(10) + '  ', ' ')
              and 'but gambits do not.' in bodies[3].replace(chr(10) + '  ', ' ') and 'It counts as an ailment.' not in bodies[0])
        check('tips: the 5 s Regen guard is an ally\'s and the smaller push limit names rare game; every tip fits the window',
              '5 for an ally with Regen' in bodies[2].replace('\n  ', ' ') and 'bosses, marks and rare game' in bodies[2].replace('\n  ', ' ')
              and all(len(CP.visible(b).split('\n')) <= CP.MAX_BODY_LINES and max(map(len, CP.visible(b).split('\n'))) <= 56 for b in bodies))
        gp = json.loads((ROOT / 'patches/gambit_text.json').read_text(encoding='utf-8'))
        fix = [c for c in gp['fieldCorrections'] if c['row'] == 226 and c['offset'] == 8]
        check('gambits: row 226\'s reason calls it a native gambit (0.1.48: in Vaan\'s starting inventory, not sold in shops), not an '
              'unused row; the correction itself is unchanged',
              len(fix) == 1 and 'unused' not in fix[0]['reason'] and "Vaan's starting inventory" in fix[0]['reason']
              and (fix[0]['offset'], fix[0]['before'], fix[0]['after']) == (8, 0x221B, 0x221E))

        self.assertGreaterEqual(len(results), 30)
        self.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
