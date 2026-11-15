"""Review fixes and Montblanc dialogue of Ivalice Legacy 0.1.47: an axe renewal of Broken (at the Sunder floor or on a fresh
dent) keeps a live breaker, so an ally's Break still counts for Hyper Wrist; hook 240 reuses an action's Staggered reading only
while the foe's charge is unchanged (a quick counter is read afresh), and a push record made mid-action keeps the action's
reading; Montblanc's topic: the greeting by state, where the party stands and what the next rank needs (the game's rank
table), the Writs and their stamps, six topic pages, the polished rank-up lines, five choices with the claim first. Every
change carries a control. Static: the shipped prototypes in a synthetic battle and in mocked dialogue windows; nothing here
touches a game or a save."""
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
class Review0147Tests(unittest.TestCase):
    def test_every_change_and_its_control(self):
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        FAM = rd('prototypes/family_identities.lua')
        PROF = rd('prototypes/proficiency.lua')
        CLAN = rd('prototypes/clan_rank.lua')
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



        # ---- A1 (sandbox): a renewal keeps a live breaker; a fresh dent on an ally's Broken keeps it too; controls
        got = sandbox().execute(STUB + r"""
          local M = newFamily()
          CAT = 'axe'
          stubRuntime()
          local wrapped = M.wrap68(function() end, deps)
          local axe = wielder(1)
          local ally = wielder(2)
          local function swing(foe)
            local g = {action = M.ATTACK, caster = {}, target = {defense = -1}}
            wrapped(g, axe, foe, {}, {}, {})
            if g.target.defense >= 0 then foe.defense = g.target.defense end
            return g.target.defense
          end
          local function floorFoe(addr) local x = foeUnit(addr); for i = 1, 10 do swing(x) end; NOW = NOW + 11000000; return x end
          local out = {}
          -- an ally Exposes a foe at the floor, then the axe hits it 6 s later: renewed (Broken 9 s after the ally's 10 s ran out)
          local a = floorFoe(700)
          M.noteBroken(a, NOW, ally.address)
          NOW = NOW + 6000000
          out.floorWrite = swing(a)
          out.floorBy = M.brokenBy(a, NOW) == ally.address
          NOW = NOW + 9000000
          out.floorRenewed = M.isBroken(a, NOW) and M.brokenBy(a, NOW) == axe.address   -- 0.1.48: past the ally's 10 s, the axe user
          -- a fresh dent on a foe the ally Broke: dented, still the ally's Break, timer restarted
          local b = foeUnit(701)
          M.noteBroken(b, NOW, ally.address)
          NOW = NOW + 6000000
          out.freshWrite = swing(b)
          out.freshBy = M.brokenBy(b, NOW) == ally.address
          NOW = NOW + 9000000
          out.freshRenewed = M.isBroken(b, NOW) and M.brokenBy(b, NOW) == axe.address   -- 0.1.48: past the ally's 10 s, the axe user
          -- controls: no live entry (expired), a live entry naming nobody, another foe at the same address: the wielder is recorded
          local c = floorFoe(702); M.noteBroken(c, NOW, ally.address); NOW = NOW + 11000000; swing(c)
          out.expired = M.brokenBy(c, NOW) == axe.address
          local d = floorFoe(703); M.noteBroken(d, NOW); swing(d)
          out.nobody = M.brokenBy(d, NOW) == axe.address
          local e = foeUnit(704); M.noteBroken(foeUnit(704, {maxHp = 4000, level = 12}), NOW, ally.address); swing(e)
          out.recycled = M.brokenBy(e, NOW) == axe.address
          local f = foeUnit(705); swing(f)
          out.plain = M.brokenBy(f, NOW) == axe.address                  -- a fresh dent with no entry: the wielder (as 0.1.46)
          -- the other writers keep their rule: the last cut records its own caster
          local g = foeUnit(706); M.noteBroken(g, NOW, ally.address); M.noteBroken(g, NOW + 1, axe.address)
          out.overwrite = M.brokenBy(g, NOW + 1) == axe.address
          -- keepLive itself: a clock earlier than the entry is not live (no breaker kept)
          local h = foeUnit(707); M.noteBroken(h, NOW, ally.address); M.noteBroken(h, NOW - 5, axe.address, true)
          out.backwards = M.brokenBy(h, NOW - 5) == axe.address
          return out.floorWrite, out.floorBy, out.floorRenewed, out.freshWrite, out.freshBy, out.freshRenewed, out.expired,
                 out.nobody, out.recycled, out.plain, out.overwrite, out.backwards
        """)
        check('A1 fix: an ally Broke the foe, the axe hits it at the floor: no Defence write, Broken still names the ally',
              got[0] == -1 and got[1] is True)
        check('A1 fix: that floor hit restarted the 10 s (Broken 15 s after the ally\'s cut); 0.1.48: credited to the axe user, '
              'since the ally\'s own Break is over 10 s old', got[2] is True)
        check('A1 fix: a fresh dent on a foe the ally Broke dents (95) and keeps the ally as breaker',
              got[3] == 95 and got[4] is True)
        check('A1 fix: the fresh dent restarted the 10 s too; 0.1.48: 15 s after the ally\'s cut it is the axe user\'s', got[5] is True)
        check('A1 controls: an expired entry, an entry naming nobody, another foe at the address, no entry: the wielder is recorded',
              got[6] is True and got[7] is True and got[8] is True and got[9] is True)
        check('A1 control: the other writers (no keepLive) still record their own caster over a live entry', got[10] is True)
        check('A1 control: a clock earlier than the entry is not a live entry (the new breaker is recorded)', got[11] is True)

        # ---- A1 (synthetic battle): R.alliedHold is true for the axe user after an ally's Expose, at the floor and on a fresh dent
        got = world().execute(r"""
          PARTY_IDS = {1, 2}
          installFamily()
          local axeIndex
          for i, n in ipairs(Family.CATEGORY_NAMES) do if n == 'axe' then axeIndex = i - 1 end end
          for i = 0, 63 do bpack.section13[i] = {category = axeIndex, chargeTime = 50} end
          local axeman = party(1, {weapon = 0x1001})
          local ally = party(2, {weapon = 0x1001})
          local w68 = Family.wrap68(function() end)
          local function swing(foe)
            local f = F({initial = foe.address, target = {removedHp = 50, identifier = 9, work = works[foe.address]}, caster = {status = {}}})
            f.action = Family.ATTACK
            f.target.defense = -1
            w68(f, axeman, foe, {}, classes, helpers)
            if f.target.defense >= 0 then foe.defense = f.target.defense end
            return f.target.defense
          end
          local function held(foe) local broken = R.alliedHold(axeman, foe, NOW, works[foe.address]) return broken end
          local out = {}
          local a = foeAt(800, {charge = 20000, required = 30000}); a.defense = 100
          for i = 1, 10 do swing(a) end
          out.floorDef = a.defense
          NOW = NOW + 11000000
          Family.noteBroken(a, NOW, ally.address)                         -- the ally's Expose (Forge 138 records the caster)
          out.before = held(a)
          NOW = NOW + 2000000
          out.floorWrite = swing(a)
          out.floorHeld = held(a)                                          -- 0.1.46: false (the axe user became the breaker)
          local b = foeAt(801, {charge = 20000, required = 30000}); b.defense = 100
          Family.noteBroken(b, NOW, ally.address)
          out.freshWrite = swing(b)
          out.freshHeld = held(b)
          local c = foeAt(802, {charge = 20000, required = 30000}); c.defense = 100
          out.ownWrite = swing(c)
          out.ownHeld = held(c)                                            -- control: the axe user's own Break is not an ally's
          return out.floorDef, out.before, out.floorWrite, out.floorHeld, out.freshWrite, out.freshHeld, out.ownWrite, out.ownHeld
        """)
        check('A1 world: ten axe hits reach the floor (50); the ally\'s Expose is heard by alliedHold (the instrument)',
              got[0] == 50 and got[1] is True)
        check('A1 world: after an axe hit at the floor, R.alliedHold is still true for the axe user (Hyper Wrist\'s ally-Broke branch)',
              got[2] == -1 and got[3] is True)
        check('A1 world: after a fresh dent on a foe the ally Broke, R.alliedHold is true for the axe user', got[4] == 95 and got[5] is True)
        check('A1 world control: the axe user\'s own fresh dent is not an ally\'s Break', got[6] == 95 and got[7] is False)

        # ---- A2/A3 (synthetic battle): the charge snapshot gate and the record-change rule
        got = world().execute(r"""
          installFamily()
          PARTY_IDS = {1, 2, 3}
          local heroes = {}
          for i = 1, 3 do heroes[i] = party(i, {job1 = P.JOB.knight, weapon = 0x1001}) end
          local seen = {}
          local decide = P.decide
          P.decide = function(ev, s, rank) if ev == 'guard' then seen[#seen + 1] = s.attackerStaggered end return decide(ev, s, rank) end
          local n = 0
          local function spend(opts)
            opts = opts or {}
            n = n + 1
            local addr = 1950 + n
            local foe = foeAt(addr, {charge = 20000, required = 30000, maxHp = 9000, level = 30})
            local w = works[addr]
            R.pushAllowance(addr, w, 9999, 1111, foe)
            assert(R.isStaggered(addr, w, foe) == true, 'setup: not Staggered')
            w.currentChargeTime = 30000
            return foe, w
          end
          local function att(foe, tgt, action, initial)
            hook(240, F({outcome = 5, action = action or 150, initial = initial or heroes[1].address, target = {status = {}}}), foe, tgt)
          end
          local function list() local o = {} for i, v in ipairs(seen) do o[i] = tostring(v) end return table.concat(o, ',') end
          local out = {}
          -- A2 controls kept: area and multi-hit (the charge does not move inside the action)
          do local foe, w = spend(); seen = {}; for i = 1, 3 do att(foe, heroes[i]) end; out.area = list() end
          do local foe, w = spend(); seen = {}; for i = 1, 3 do att(foe, heroes[1]); NOW = NOW + 200000 end; out.multi = list() end
          -- A2: a counter 0.5 s later (same action id and first target, no push), the foe charging again: read afresh
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1]); NOW = NOW + 500000; w.currentChargeTime = 4000; w.lastChargeTime = 4000; att(foe, heroes[1])
            out.counter = list()
          end
          -- A2: an unreadable charge (no battle work) is never a reuse: per call
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1]); local saved = works[foe.address]; works[foe.address] = nil
            att(foe, heroes[2]); works[foe.address] = saved
            out.noWork = list()
          end
          -- A3: a guard push on target 1 makes a new record (budget not spent) mid-action: targets 2 and 3 keep Staggered
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1])
            w.currentChargeTime = 20000
            R.pushAllowance(foe.address, w, 3000, heroes[1].address, foe)   -- 3000 of 7500: not Staggered on its own
            w.currentChargeTime = 30000
            att(foe, heroes[2]); att(foe, heroes[3])
            out.guardPush = list()
          end
          -- A3: the same with the push written to the charge (R.pushFoe): the charge sits where the push left it
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1])
            w.currentChargeTime, w.lastChargeTime = 20000, 20000
            local from, to = R.pushFoe(w, foe.address, 0.1, heroes[1].address, foe)   -- 20000 -> 17000
            out.pushed = tostring(from) .. '->' .. tostring(to)
            att(foe, heroes[2]); att(foe, heroes[3])
            out.pushFoe = list()
          end
          -- A3 control: a new record and a charge that moved on past where the push left it is a new action: read afresh
          do
            local foe, w = spend(); seen = {}
            att(foe, heroes[1])
            w.currentChargeTime = 20000
            R.pushAllowance(foe.address, w, 3000, heroes[1].address, foe)   -- the record says 17000
            w.currentChargeTime = 18000                                     -- charging again: a counter
            att(foe, heroes[1])
            out.pushThenCounter = list()
          end
          -- A3 control kept (0.1.46): not Staggered at the start, a push spends the budget mid-action: the next hit hears it
          do
            local foe = foeAt(1985, {charge = 20000, required = 30000, maxHp = 9000, level = 30}); local w = works[1985]
            w.currentChargeTime = 30000; seen = {}
            att(foe, heroes[1])
            w.currentChargeTime = 20000
            R.pushAllowance(1985, w, 9999, 1111, foe)
            w.currentChargeTime = 30000
            att(foe, heroes[2])
            out.midPush = list()
          end
          return out.area, out.multi, out.counter, out.noWork, out.guardPush, out.pushed, out.pushFoe, out.pushThenCounter, out.midPush
        """)
        check('A2: an area attack and a multi-hit still give true,true,true (' + got[0] + ' / ' + got[1] + ')',
              got[0] == 'true,true,true' and got[1] == 'true,true,true')
        check('A2: a counter within 1 s (same action and first target, the charge moved: 30000 -> 4000) is a fresh reading ' + got[2],
              got[2] == 'true,false')
        check('A2 control: a charge that cannot be read is never reused (per call) ' + got[3], got[3] == 'true,false')
        check('A3: a guard push on target 1 (a new record, budget not spent) does not drop Staggered for targets 2-3 ' + got[4]
              + ' (0.1.46: true,false,false)', got[4] == 'true,true,true')
        check('A3: the same with the push written to the charge (' + got[5] + '): targets 2-3 keep Staggered ' + got[6],
              got[5] == '20000->17000' and got[6] == 'true,true,true')
        check('A3 control: a new record and a charge past where its push left it (a counter) is read afresh ' + got[7], got[7] == 'true,false')
        check('A3 control (0.1.46 kept): a push that spends the budget mid-action is heard by the next hit ' + got[8], got[8] == 'false,true')

        # ---- A4: documented, per swing
        check('A4 (0.1.49): wrap68 cites Forge 280 (no evaded, missed or Safety-stopped swing reaches 68) and calls the evade '
              'gate a defensive guard', 'functions/280.lua' in FAM and 'M.evaded test below is a defensive guard' in FAM
              and '0.1.48: an evaded swing neither dents nor Breaks' not in FAM)

        # ---- Part B: the Montblanc helpers (pure)
        ROWS = [(0, 0, 0), (700, 2, 0), (8000, 4, 0), (20000, 8, 0), (30000, 10, 0), (40000, 12, 0), (100000, 14, 0),
                (200000, 16, 0), (250000, 24, 0), (300000, 28, 0), (500000, 32, 0), (1000000, 44, 30)]   # the game's table

        pl = LuaRuntime(unpack_returned_tuples=True)
        pl.globals().PROF, pl.globals().CLAN = PROF, CLAN
        pl.globals().ROWS = pl.table_from([pl.table_from(list(r)) for r in ROWS])
        pl.execute(r'''
          P = load(PROF, '@proficiency')()
          C = load(CLAN, '@clan_rank')()
          function memWith(points, marks, denBits)
            local m = {}
            for r = 1, 12 do
              local a = C.RANK_TABLE + 8 * (r - 1)
              local row = ROWS[r]
              m[a] = {u32 = row[1]}; m[a + 4] = {u8 = row[2]}; m[a + 5] = {u8 = row[3]}
            end
            local den = {}
            for i = 0, 29 do if i < denBits then local b = C.DEN_BITS + i // 8; den[b] = (den[b] or 0) | (1 << (i % 8)) end end
            local writes = 0
            return {u32 = function(a) if a == C.POINTS then return points end return m[a] and m[a].u32 end,
                    u8 = function(a) if m[a] then return m[a].u8 end return den[a] or 0 end,
                    marks = function() return marks end}
          end
          function state(rank, claimed, have, writs)
            local clan = have and C.read(memWith(have[1], have[2], have[3])) or nil
            local s = {rank = rank, claimed = claimed, writs = writs, have = clan}
            if clan then s.need = C.nextNeeds(clan, math.max(rank or 0, claimed or 0)) end
            return s
          end
          function joined(boxes) return table.concat(boxes, '\n|\n') end
          -- every box: at most 3 lines of 48 visible characters (markup does not count)
          function fits(boxes)
            for _, box in ipairs(boxes) do
              local n = 0
              for line in (box .. '\n'):gmatch('(.-)\n') do
                n = n + 1
                if #line:gsub('{color:%a+}', '') > 48 then return false, line end
              end
              if n > 3 then return false, box end
            end
            return true
          end
        ''')
        L = pl.eval

        # clan_rank: rows and nextNeeds (pure)
        got = pl.execute(r'''
          local c = C.read(memWith(8000, 4, 0))
          local n2 = C.nextNeeds(C.read(memWith(700, 2, 0)))
          local n7 = C.nextNeeds(c, 6)
          local n12 = C.nextNeeds(c, 11)
          return c.rank, #c.rows, n2.rank, n2.points, n2.marks, n2.den, n7.rank, n7.points, n7.marks, n12.rank, n12.points, n12.marks,
                 n12.den, C.nextNeeds(c, 12) == nil, C.nextNeeds({rank = 3}) == nil, C.nextNeeds(nil) == nil
        ''')
        check('B clan_rank: read() returns the rank and the 12 rows; nextNeeds gives rank 3 after rank 2 (8000 points, 4 marks, no den)',
              got[0] == 3 and got[1] == 12 and got[2:6] == (3, 8000, 4, 0))
        check('B clan_rank: the next-rank numbers match the game\'s rows for ranks 7 (100000, 14) and 12 (1000000, 44, 30 den)',
              got[6:9] == (7, 100000, 14) and got[9:13] == (12, 1000000, 44, 30))
        check('B clan_rank: nothing after rank 12, and nothing without rows or without a clan table', got[13] is True and got[14] is True and got[15] is True)
        check('B clan_rank stays free of side effects: no call to 0x315390 or 0x316670 and no memory write',
              '0x315390' not in CLAN.split('So 0x315390/0x316670 are never called here.')[1] and 'memory.' not in CLAN and 'execute' not in CLAN
              and not re.search(r'mem\.\w+\([^)]*\)\s*=', CLAN))

        # D2 greeting
        got = pl.execute(r'''
          return P.greeting({rank = 5, claimed = 4}), P.greeting({rank = 4, claimed = 4}), P.greeting({rank = 12, claimed = 12}),
                 P.greeting({claimed = 2}), P.greeting(nil), P.greeting({rank = 12, claimed = 11})
        ''')
        check('D2: a rank waiting -> "Kupo! Your rank has gone up since we last / spoke. Shall we see what you\'ve earned?"',
              got[0] == "Kupo! Your rank has gone up since we last\nspoke. Shall we see what you've earned?" and got[5] == got[0])
        check('D2: nothing waiting (or the rank unreadable) -> "Proficiency, kupo? What would you like / to know?"',
              got[1] == 'Proficiency, kupo? What would you like\nto know?' and got[3] == got[1] and got[4] == got[1])
        check('D2: rank 12 claimed -> "My finest hunter! What can Clan Centurio / do for you today?"',
              got[2] == 'My finest hunter! What can Clan Centurio\ndo for you today?')

        # D3 standing and the "nothing new" answer
        got = pl.execute(r'''
          local s0 = P.standing(state(1, 0, {0, 0, 0}))
          local s2 = P.standing(state(2, 2, {1500, 3, 0}))
          local s6 = P.standing(state(6, 6, {45000, 13, 0}))
          local s11 = P.standing(state(11, 11, {600000, 40, 12}))
          local s12 = P.standing(state(12, 12, {1000000, 44, 30}))
          local ahead = P.standing(state(7, 5, {100000, 14, 0}))
          local blind = P.standing({claimed = 3})
          local n2 = P.nothingNew(state(2, 2, {1500, 3, 0}))
          local n11 = P.nothingNew(state(11, 11, {600000, 40, 12}))
          local n12 = P.nothingNew(state(12, 12, {1000000, 44, 30}))
          local nNone = P.nothingNew({claimed = 3})
          return joined(s0), joined(s2), joined(s6), joined(s11), joined(s12), joined(ahead), joined(blind), joined(n2), joined(n11),
                 joined(n12), joined(nNone)
        ''')
        check('D3: rank 0 rewarded has its own wording ("I haven\'t rewarded / any rank yet. Ask what you\'ve earned!") and no second "ask"',
              got[0] == "Clan rank 1. I haven't rewarded\nany rank yet. Ask what you've earned!\n|\nStage I is at 0/4, stage II at 0/4\nand stage III at 0/4.")
        check('D3: rank 2 -> rank, stages, and rank 3\'s needs from the table with the save\'s counts (plain digits)',
              got[1] == "Clan rank 2, and I've rewarded you up to\nrank 2.\n|\nStage I is at 2/4, stage II at 0/4\nand stage III at 0/4.\n|\n"
                        "Rank 3 needs 8000 clan points and\n4 marks hunted. You have 1500 and 3.")
        check('D3: rank 6 -> rank 7 needs 100000 clan points and 14 marks; no den line',
              got[2].endswith("Rank 7 needs 100000 clan points and\n14 marks hunted. You have 45000 and 13.") and 'Den' not in got[2])
        check('D3: rank 11 -> rank 12 needs 1000000 and 44, then the den line "...and a full Sky Pirate\'s Den. You have 12 / of 30 entries."',
              got[3].endswith("Rank 12 needs 1000000 clan points and\n44 marks hunted. You have 600000 and 40.\n|\n"
                              "...and a full Sky Pirate's Den. You have 12\nof 30 entries."))
        check('D3: rank 12 -> the master line "You hold everything the clan teaches. A / master hunter, kupo!"',
              got[4].endswith('You hold everything the clan teaches. A\nmaster hunter, kupo!') and 'needs' not in got[4])
        check('D3: a rank ahead of the rewarded one -> "Your rank is ahead of my papers! Ask me / what you\'ve earned, kupo."',
              got[5].endswith("Your rank is ahead of my papers! Ask me\nwhat you've earned, kupo.") and 'needs' not in got[5])
        check('D3 fallback: the rank unreadable -> the old "Reach Clan rank N" line', got[6].endswith('Reach Clan rank 4 and ask me again,\nkupo! There is more to learn.'))
        check('D3: "nothing new" -> "Nothing new yet. Rank 3 needs 8000 clan / points and 4 marks hunted, kupo!"',
              got[7] == 'Nothing new yet. Rank 3 needs 8000 clan\npoints and 4 marks hunted, kupo!')
        check('D3: "nothing new" before rank 12 adds the den line; at rank 12 it is the master line; unreadable keeps the old line',
              got[8].endswith("...and a full Sky Pirate's Den. You have 12\nof 30 entries.") and got[9] == 'You hold everything the clan teaches. A\nmaster hunter, kupo!'
              and got[10] == 'Nothing new yet, kupo! Raise your Clan\nrank and come see me again.')

        # D4 the Writs
        got = pl.execute(r'''
          return joined(P.writStatus({claimed = 0, writs = {}})), joined(P.writStatus({claimed = 3, writs = {true}})),
                 joined(P.writStatus({claimed = 6, writs = {true, true}})), joined(P.writStatus({claimed = 12, writs = {true, true, true}})),
                 joined(P.writStatus({claimed = 6, writs = {true}})), joined(P.writStatus({claimed = 2, writs = {}})),
                 joined(P.writStatus({claimed = 9, writs = {true, true, true}}))
        ''')
        HEAD = ("{color:gold}Apprentice's Writ{color:none}: stage I, ranks 1-4.\n{color:gold}Journeyman's Writ{color:none}: stage II, ranks 5-8.\n"
                "{color:gold}Master's Writ{color:none}: stage III, ranks 9-12.\n|\nKeep them in your Key Items. A stage only\nanswers to a hunter who carries its Writ.")
        LOST = "Lost a Writ? Ask what you've earned and\nI'll write you a new one, kupo."
        check('D4: no Writ at rank 0 -> the table, the keeping line, "You carry no Writ yet. Ask what you\'ve / earned and I\'ll start your papers!"',
              got[0] == HEAD + "\n|\nYou carry no Writ yet. Ask what you've\nearned and I'll start your papers!")
        check('D4: one Writ at rank 3 -> "You carry the Apprentice\'s Writ, with / 3 of 4 stamps."',
              got[1] == HEAD + "\n|\nYou carry the {color:gold}Apprentice's Writ{color:none}, with\n3 of 4 stamps.")
        check('D4: two Writs at rank 6 -> the Journeyman\'s with 2 of 4; three at 12 -> the Master\'s with 4 of 4; three at 9 -> 1 of 4',
              got[2].endswith("Journeyman's Writ{color:none}, with\n2 of 4 stamps.") and got[3].endswith("Master's Writ{color:none}, with\n4 of 4 stamps.")
              and got[6].endswith("Master's Writ{color:none}, with\n1 of 4 stamps.") and LOST not in got[2] + got[3] + got[6])
        check('D4: a missing Writ (rank 6 with only the first) -> the carried one, then "Lost a Writ? ..."; no Writ at rank 2 -> only the lost line',
              got[4].endswith("Apprentice's Writ{color:none}, with\n4 of 4 stamps.\n|\n" + LOST) and got[5] == HEAD + '\n|\n' + LOST)

        # D5 pages and D6 speech
        got = pl.execute(r'''
          return #P.TOPIC_PAGES, joined(P.TOPIC_PAGES), joined(P.speech(11, 12)), joined(P.speech(0, 1)), joined(P.speech(1, 2)),
                 joined(P.speech(4, 5)), joined(P.speech(8, 9)), joined(P.speech(0, 6)), joined(P.writSpeech(2)), #P.speech(3, 3),
                 #P.TOPIC_CHOICES, P.TOPIC_CHOICES[1], P.TOPIC_CHOICES[5], P.TOPIC.bye
        ''')
        D5 = ['Clan Centurio trains every member in their\njob, kupo. That training is {color:gold}Proficiency{color:none}.',
              'Each job has its own line of three stages,\nand each stage grows through four levels.\nEvery new Clan rank brings a new level.',
              "Learned two jobs? Then you fight with that\npair's own line, not either job's alone.\nEvery pairing has one, kupo!",
              'The line follows your job, not your gear.\nChange jobs freely: what the clan taught\nyou stays taught, whatever you wield.',
              'To see a stage at work, open Equipment.\nWeapons show stage I, body armour stage II\nand helmets stage III for that character.',
              "Master all three stages at rank 12, and a\nlegendary weapon of your job's own kind\nstrikes a quarter harder, kupo!"]
        check('D5: six topic pages, exactly as drafted (hybrids on page 3, Equipment on page 5, legendary mastery on page 6)',
              got[0] == 6 and got[1] == '\n|\n'.join(D5))
        check('D6: the final stamp says "And the last stamp on your Master\'s Writ!" and the legendary line follows it',
              got[2] == "And the last stamp on your {color:gold}Master's Writ{color:none}!\nEvery {color:gold}Proficiency{color:none} at its peak. Even I'm a\n"
                        "little jealous, kupo!\n|\nTake up a legendary weapon of your job's\nown kind. Now you'll feel what mastery is!")
        check('D6: one "kupo" per Writ handed over (Apprentice 1, Journeyman 1 "Kupo-po", Master 1) and none on a plain stamp',
              got[3].lower().count('kupo') == 1 and got[5].lower().count('kupo') == 1 and got[6].lower().count('kupo') == 1
              and 'kupo' not in got[4].lower() and got[8].lower().count('kupo') == 1)
        check('D6: the box order and content the repo tests check are kept (Apprentice 3 boxes, I 2/4, Journeyman, Master, catch-up, II 2/4)',
              got[3].count('\n|\n') == 2 and "Apprentice's Writ" in got[3].split('\n|\n')[0] and 'Proficiency I 2/4' in got[4]
              and "Journeyman's Writ" in got[5].split('\n|\n')[0] and "Master's Writ" in got[6].split('\n|\n')[0]
              and 'filing your training papers' in got[7].split('\n|\n')[0] and 'II 2/4' in got[7].split('\n|\n')[-1] and got[9] == 0)
        check('D6: the Apprentice\'s box 2 is unchanged', 'Whatever job you take up, the Writ unlocks its\n{color:gold}Proficiency{color:none}: your weapons and armour fight\nthe way that job does, whatever you wield!' in got[3])
        check('D1: five choices, claim first, "That\'s all." last (also the cancel index 4)',
              got[10] == 5 and got[11] == 'Have I earned anything new?' and got[12] == "That's all." and got[13] == 4)

        # every box of every helper in every state fits the window
        got = pl.execute(r'''
          local bad = {}
          local function test(name, boxes) local ok, what = fits(boxes) if not ok then bad[#bad + 1] = name .. ': ' .. tostring(what) end end
          for from = 0, 12 do for to = from + 1, 12 do test('speech ' .. from .. '-' .. to, P.speech(from, to)) end end
          for k = 1, 3 do test('writSpeech', P.writSpeech(k)) end
          test('pages', P.TOPIC_PAGES); test('reminder', {P.REMINDER})
          local big = {0xFFFFFFFF, 255, 30}
          for claimed = 0, 12 do
            for rank = 1, 12 do
              for _, have in ipairs({{0, 0, 0}, big, {999999, 43, 29}}) do
                local s = state(rank, claimed, have, {true, true, true})
                test('greeting', {P.greeting(s)}); test('standing', P.standing(s)); test('nothingNew', P.nothingNew(s))
                test('writs', P.writStatus(s)); test('writs0', P.writStatus({claimed = claimed, writs = {}}))
              end
            end
            test('blind standing', P.standing({claimed = claimed}))
          end
          return table.concat(bad, '; ')
        ''')
        check('D1-D6 text budget: every box (speech for every rank pair, the pages, the greetings, the standing, "nothing new" and the Writ '
              'status for every rank, rewarded rank and the largest counts) fits 3 lines of 48 characters ' + got, got == '')

        # ---- D1-D4 in the repo's topic runtime: the menu order, the greeting by state, and the answers, read through the real
        # clan_rank.lua (the rank table rows, points, marks and den bits are memory the mock serves; nothing is written)
        tl = LuaRuntime(unpack_returned_tuples=True)
        srcs = {f'scripts/IvaliceLegacy/{n}.lua': rd(f'prototypes/{n}.lua')
                for n in ('proficiency', 'proficiency_runtime', 'hybrid_proficiency', 'clan_rank')}
        tl.globals().sources = tl.table_from(srcs)
        tl.globals().ROWS = tl.table_from([tl.table_from(list(r)) for r in ROWS])
        tl.execute(r'''
          printed, boxes, notes, asked, ASK, ticks, handlers, keys = {}, {}, {}, {}, {}, {}, {}, {}
          print = function(s) printed[#printed+1] = s end
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          local wide = function(s) return (s:gsub('.', '%0\0')) end
          local mem = {[0x120000]='MZ', [0x120180]='PE\0\0\x64\x86', [0xD113D4]='LEGCY_\0', [0xD12200]=wide('%sLEGCY_%03d\0')}
          local C = real_load(sources['scripts/IvaliceLegacy/clan_rank.lua'])()
          MARKS = 0
          arg = {s32='s32', u32='u32'}   -- the loader's global argument types (the runtime passes arg.s32)
          memory = {u8=setmetatable({}, {__index=function() return 0 end}), u32=setmetatable({}, {__index=function() return 0 end}),
                    u64={}, arg={s32='s32', u32='u32'},
                    readArray=function(a, n) return (mem[a] or ''):sub(1, n) end,
                    execute=function(fn, ret, t1, id, t2, v2, t3, v3)
                      if fn == C.MARKS_FN then return MARKS end
                      if fn == 0x2FD970 then return keys[id] and 1 or 0 end
                      if fn == 0x2FD420 then keys[id] = v2 == 1; return v2 end
                      return 0
                    end}
          for r = 1, 12 do
            local a = C.RANK_TABLE + 8 * (r - 1)
            memory.u32[a] = ROWS[r][1]; memory.u8[a + 4] = ROWS[r][2]; memory.u8[a + 5] = ROWS[r][3]
          end
          function save(points, marks, den)
            memory.u32[C.POINTS] = points; MARKS = marks
            for i = 0, 3 do memory.u8[C.DEN_BITS + i] = 0 end
            for i = 0, den - 1 do local b = C.DEN_BITS + i // 8; memory.u8[b] = memory.u8[b] | (1 << (i % 8)) end
          end
          dialog = {amese={open=function(t)
                      if t.body:find('Nothing new yet', 1, true) or t.body:find('Before you go', 1, true) then notes[#notes+1] = t.body
                      else boxes[#boxes+1] = t.body end
                      if t.onClose then t.onClose() end
                      return 1
                    end},
                    aaske={open=function(t)
                      asked[#asked+1] = t
                      local i = table.remove(ASK, 1)
                      if i == nil then i = #t.choice - 1 end
                      t.onClose(i)
                      return 1
                    end}}
          event = {registerEventSync=function(name, fn) handlers[name] = fn return true end,
                   registerEventAsync=function(name, fn) handlers[name] = fn return true end,
                   registerLoadHandler=function(_, fn) handlers.load = fn return true end,
                   registerSaveHandler=function(_, fn) handlers.save = fn return true end,
                   executeAfterMs=function(ms, fn) ticks[#ticks+1] = fn end}
          function run(rounds) for _ = 1, rounds or 40 do local t = ticks; ticks = {}; for _, fn in ipairs(t) do pcall(fn) end end end
          R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime', 't', _ENV)()
          assert(R.register())
          P = R.Proficiency
          function ask(answers)
            handlers.onMapJump(302); run(2)
            asked, ASK = {}, answers
            memory.u8[0x02099DF0 + 0x99] = 1
            run()
          end
          function said(text) for _, b in ipairs(boxes) do if b:find(text, 1, true) then return true end end return false end
        ''')
        got = tl.execute(r'''
          local out = {}
          save(1500, 3, 0)                                                 -- rank 2 (700 / 2), not rank 3 (8000 / 4)
          handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          ask({1, 2, 4})                                                   -- where do I stand, the Writs, that's all
          local m = asked[1]
          out.choices = table.concat(m.choice, '|'); out.cancel = m.cancel; out.greetNone = m.body
          out.stand = said('Rank 3 needs 8000 clan points and\n4 marks hunted. You have 1500 and 3.')
          out.lost = said('Lost a Writ?')                                  -- rank 2 rewarded, no Writ in the Key Items
          out.asks = #asked
          boxes = {}
          save(8000, 4, 0)                                                 -- rank 3 is waiting
          ask({0})
          out.greetWaiting = asked[1].body
          out.claimed3 = R.claimed() == 3 and keys[0x81F0] == true and said('Proficiency I 3/4')
          boxes, notes = {}, {}
          ask({0})                                                         -- nothing new: the next rank's needs
          out.nothing = notes[1]
          boxes, notes = {}, {}
          ask({2})
          out.carry = said("You carry the {color:gold}Apprentice's Writ{color:none}, with\n3 of 4 stamps.") and not said('Lost a Writ?')
          save(1000000, 44, 30)
          ask({0})
          boxes = {}
          ask({1})
          out.greetMaster = asked[1].body
          out.master = R.claimed() == 12 and said('You hold everything the clan teaches. A\nmaster hunter, kupo!')
          return out.choices, out.cancel, out.greetNone, out.stand, out.lost, out.asks, out.greetWaiting, out.claimed3, out.nothing,
                 out.carry, out.greetMaster, out.master
        ''')
        check('D1 repo topic: five choices in order (claim, stand, Writs, what is, that\'s all) and Cancel is "That\'s all." (index 4)',
              got[0] == "Have I earned anything new?|Where do I stand?|Tell me about the Writs.|What is Proficiency?|That's all." and got[1] == 4)
        check('D2 repo topic: the greeting follows the state (nothing waiting / a rank waiting / rank 12)',
              got[2] == 'Proficiency, kupo? What would you like\nto know?'
              and got[6] == "Kupo! Your rank has gone up since we last\nspoke. Shall we see what you've earned?"
              and got[10] == 'My finest hunter! What can Clan Centurio\ndo for you today?')
        check('D3 repo topic: "Where do I stand?" names rank 3\'s needs from the game\'s table and the save\'s counts', got[3] is True)
        check('D4 repo topic: with no Writ carried at rank 2 he says a lost Writ is handed back; after the claim he names the Writ and its stamps',
              got[4] is True and got[9] is True)
        check('D1 repo topic: the menu comes back after each answer (three menus for stand, Writs, that\'s all)', got[5] == 3)
        check('repo topic control: the claim is unchanged (rank 3 claimed, the Writ granted, the stamp line spoken)', got[7] is True)
        check('D3 repo topic: "nothing new" gives "Nothing new yet. Rank 4 needs 20000 clan / points and 8 marks hunted, kupo!"',
              got[8] == 'Nothing new yet. Rank 4 needs 20000 clan\npoints and 8 marks hunted, kupo!')
        check('D3 repo topic: at rank 12 the standing ends with the master line', got[11] is True)

        self.assertGreaterEqual(len(results), 50)
        self.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
