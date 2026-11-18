"""Review fixes and the "ask Montblanc" topic of Ivalice Legacy 0.1.48: an axe swing that was parried, blocked or dodged, or
that Safety stopped (the Forge outcome 1-5 or 10), neither dents Defence nor renews Broken; an axe renewal keeps an ally as
breaker only within 10 s of the ally's own Break, then credits the axe user (R.alliedHold follows); Montblanc's topic: the hall
byte is polled only in the Clan hall, the five-choice menu opens on it, "Have I earned anything new?" claims the waiting rank,
leaving the hall hands nothing out and reminds once per visit, and a claim that cannot be completed (a Writ not granted, the
rank unreadable, an error) answers with a neutral line instead of "Nothing new yet". Every change carries a control. Static:
the prototypes in a sandbox, a synthetic battle and mocked dialogue windows; nothing here touches a game or a save."""
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
class Review0148Tests(unittest.TestCase):
    def test_every_change_and_its_control(self):
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        FAM = rd('prototypes/family_identities.lua')
        PROF = rd('prototypes/proficiency.lua')
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



        ROWS = [(0, 0, 0), (700, 2, 0), (8000, 4, 0), (20000, 8, 0), (30000, 10, 0), (40000, 12, 0), (100000, 14, 0),
                (200000, 16, 0), (250000, 24, 0), (300000, 28, 0), (500000, 32, 0), (1000000, 44, 30)]   # the game's table

        evaded_keys = sandbox().execute(r'''
          local M = newFamily(); local k = {}
          for x, on in pairs(M.EVADED) do if on then k[#k + 1] = x end end
          table.sort(k); return table.concat(k, ',')
        ''')
        check('A4: M.EVADED holds 1-5 (parry, block, body evade) and 10 (Safety) ' + evaded_keys, evaded_keys == '1,2,3,4,5,10')

        # ---- A4 (sandbox): an evaded or Safety-stopped swing neither dents nor renews Broken; controls
        got = sandbox().execute(STUB + r'''
          local M = newFamily()
          CAT = 'axe'
          stubRuntime()
          local wrapped = M.wrap68(function() end, deps)
          local axe = wielder(1)
          local function swing(foe, outcome, broken)
            local g = {action = M.ATTACK, caster = {}, target = {defense = -1}, outcomeType = outcome}
            if broken then setmetatable(g, {__index = function(_, k) if k == 'outcomeType' then error('unreadable') end end}); g.outcomeType = nil end
            wrapped(g, axe, foe, {}, {}, {})
            if g.target.defense >= 0 then foe.defense = g.target.defense end
            return g.target.defense
          end
          local ev = {}
          for _, o in ipairs({1, 2, 3, 4, 5, 10}) do
            reports = {}
            local f = foeUnit(600 + o)
            local w = swing(f, o)
            ev[#ev + 1] = string.format('%d:%d:%s:%d', o, w, tostring(M.isBroken(f, NOW)), count('sunder') + count('sunder-floor'))
          end
          -- a floored foe whose Broken is 9 s old: an evaded swing does not renew it, so it ends on time; a landed one renews it
          local fl = foeUnit(650); for i = 1, 10 do swing(fl, 0) end
          local T = NOW
          NOW = T + 9000000; local flEv = swing(fl, 5)
          NOW = T + 10000001; local flEnded = not M.isBroken(fl, NOW)
          local fl2 = foeUnit(651); for i = 1, 10 do swing(fl2, 0) end
          T = NOW
          NOW = T + 9000000; swing(fl2, 0)
          NOW = T + 10000001; local fl2Live = M.isBroken(fl2, NOW)
          -- controls: landed (0), the plain miss (6, not an evade outcome here), other outcomes, no field, an unreadable field
          local ctl = {}
          for _, o in ipairs({0, 6, 7, 8, 9, 11}) do local f = foeUnit(660 + o); ctl[#ctl + 1] = o .. ':' .. swing(f, o) .. ':' .. tostring(M.isBroken(f, NOW)) end
          local fn = foeUnit(680); local none = swing(fn, nil)
          local fu = foeUnit(681); local unread = swing(fu, nil, true)
          -- 0.6.0: Reckless is gone: an evaded axe Attack takes no step and logs nothing
          reports = {}
          local fresh = wielder(5)
          local fr = foeUnit(690)
          wrapped({action = M.ATTACK, caster = {}, target = {defense = -1}, outcomeType = 3}, fresh, fr, {}, {}, {})
          return table.concat(ev, ' '), flEv, flEnded, fl2Live, table.concat(ctl, ' '), none, M.isBroken(fn, NOW), unread,
                 M.isBroken(fu, NOW), count('reckless')
        ''')
        check('A4 fix: outcomes 1-5 and 10 (parry, block, body evade, Safety) on a fresh foe: no Defence write, not Broken, no sunder report: '
              + got[0], got[0] == '1:-1:false:0 2:-1:false:0 3:-1:false:0 4:-1:false:0 5:-1:false:0 10:-1:false:0')
        check('A4 fix: at the floor an evaded swing does not renew Broken (it ends 10 s after the last landed hit); control: a landed '
              'floor hit renews it', got[1] == -1 and got[2] is True and got[3] is True)
        check('A4 controls: landed (0), plain miss (6), and outcomes 7, 8, 9, 11 dent (95) and Break as before: ' + got[4],
              got[4] == '0:95:true 6:95:true 7:95:true 8:95:true 9:95:true 11:95:true')
        check('A4 controls: no outcome field, and an unreadable one, count as a landed swing (95, Broken), as before 0.1.48',
              got[5] == 95 and got[6] is True and got[7] == 95 and got[8] is True)
        check('A4 control: Reckless is gone (an evaded axe Attack logs no Reckless step)', got[9] == 0)

        # ---- A1 (sandbox): an ally's credit lasts at most 10 s from the ally's own Break; renewals past that credit the axe user
        got = sandbox().execute(STUB + r'''
          local M = newFamily()
          CAT = 'axe'
          stubRuntime()
          local wrapped = M.wrap68(function() end, deps)
          local axe, ally, other = wielder(1), wielder(2), wielder(3)
          local S = 1000000
          local function swing(foe, who)
            local g = {action = M.ATTACK, caster = {}, target = {defense = -1}}
            wrapped(g, who or axe, foe, {}, {}, {})
            if g.target.defense >= 0 then foe.defense = g.target.defense end
            return g.target.defense
          end
          local function floorFoe(addr) local x = foeUnit(addr); for i = 1, 10 do swing(x) end; NOW = NOW + 11 * S; return x end
          local r = {}
          -- the floor: the ally Breaks at T, the axe renews at 3 s and 9 s
          local a = floorFoe(710); local T = NOW
          M.noteBroken(a, T, ally.address)
          NOW = T + 3 * S; swing(a); r.a3 = M.brokenBy(a, NOW) == ally.address
          NOW = T + 9 * S; swing(a); r.a9 = M.brokenBy(a, NOW) == ally.address
          NOW = T + 10 * S; r.a10 = M.brokenBy(a, NOW) == ally.address
          NOW = T + 10 * S + 1; r.a10p = M.isBroken(a, NOW) and M.brokenBy(a, NOW) == axe.address
          NOW = T + 11 * S; swing(a); r.a11 = M.brokenBy(a, NOW) == axe.address
          NOW = T + 21 * S; r.a21 = M.isBroken(a, NOW) and M.brokenBy(a, NOW) == axe.address
          NOW = T + 21 * S + 1; r.a21p = not M.isBroken(a, NOW)
          -- a renewal exactly at 10 s still keeps the ally; the credit then ends without another hit, Broken lives on
          local b = floorFoe(711); T = NOW; M.noteBroken(b, T, ally.address)
          NOW = T + 10 * S; swing(b); r.b10 = M.brokenBy(b, NOW) == ally.address
          NOW = T + 10 * S + 1; r.b10p = M.brokenBy(b, NOW) == axe.address
          NOW = T + 19 * S; r.b19 = M.isBroken(b, NOW) and M.brokenBy(b, NOW) == axe.address
          -- a fresh dent (not at the floor): the ally within 10 s, the axe user after
          local c = foeUnit(712); T = NOW; M.noteBroken(c, T, ally.address)
          NOW = T + 6 * S; r.c6d = swing(c); r.c6 = M.brokenBy(c, NOW) == ally.address
          NOW = T + 10 * S + 500000; r.c10d = swing(c); r.c10 = M.brokenBy(c, NOW) == axe.address
          M.noteBroken(c, NOW + 1, ally.address); r.cAgain = M.brokenBy(c, NOW + 1) == ally.address
          -- the wielder's own Break, renewed: always the wielder
          local d = foeUnit(713); T = NOW; swing(d); NOW = T + 9 * S; swing(d); NOW = T + 15 * S
          r.dOwn = M.isBroken(d, NOW) and M.brokenBy(d, NOW) == axe.address
          -- a second axe user renewing the first one's Break: the first within 10 s of the first one's own Break, then the second
          local e = foeUnit(714); T = NOW; swing(e, axe); NOW = T + 5 * S; swing(e, other); r.e5 = M.brokenBy(e, NOW) == axe.address
          NOW = T + 10 * S + 1; r.e10 = M.brokenBy(e, NOW) == other.address
          -- the breaker's own renewal restarts its own credit (byAt = now): the first axe user renews at 9 s, a second axe user
          -- renewing at 12 s keeps the first (3 s into its renewed credit); past 19 s the second
          local h = foeUnit(716); T = NOW; swing(h, axe); NOW = T + 9 * S; swing(h, axe); NOW = T + 12 * S; swing(h, other)
          r.h12 = M.brokenBy(h, NOW) == axe.address
          NOW = T + 19 * S + 1; r.h19 = M.brokenBy(h, NOW) == other.address
          -- past the ally's 10 s the axe user's renewal is recorded as the axe user's own Break (by = wielder, byAt = now): a second
          -- axe user renewing 2 s later keeps the first axe user, not the ally
          local k = floorFoe(717); T = NOW; M.noteBroken(k, T, ally.address)
          NOW = T + 9 * S; swing(k, axe); NOW = T + 11 * S; swing(k, axe); NOW = T + 13 * S; swing(k, other)
          r.k13 = M.brokenBy(k, NOW) == axe.address
          -- a renewal by an unknown address keeps the ally within 10 s, and names nobody after
          local f = foeUnit(715); T = NOW; M.noteBroken(f, T, ally.address); NOW = T + 4 * S; M.noteBroken(f, NOW, nil, true)
          r.f4 = M.brokenBy(f, NOW) == ally.address
          NOW = T + 10 * S + 1; r.fNone = M.isBroken(f, NOW) and M.brokenBy(f, NOW) == nil
          return r.a3, r.a9, r.a10, r.a10p, r.a11, r.a21, r.a21p, r.b10, r.b10p, r.b19, r.c6d, r.c6, r.c10d, r.c10, r.cAgain, r.dOwn,
                 r.e5, r.e10, r.f4, r.fNone, r.h12, r.h19, r.k13
        ''')
        check('A1 credit window: the ally Breaks at T, the axe renews at 3 s and 9 s: the ally stays the breaker, also at exactly 10 s',
              got[0:3] == (True, True, True))
        check('A1 credit window: 1 microsecond past 10 s from the ally\'s Break the foe is still Broken (renewed at 9 s) but credited to '
              'the axe user; an axe hit at 11 s records the axe user', got[3] is True and got[4] is True)
        check('A1 credit window: the axe user\'s own renewal at 11 s keeps Broken until 21 s, then it ends', got[5] is True and got[6] is True)
        check('A1 boundary: a renewal at exactly 10 s keeps the ally; the credit ends right after without another hit, Broken lives on to 19 s',
              got[7:10] == (True, True, True))
        check('A1 fresh dent: dents (95, 90); the ally within 10 s, the axe user after; a later Expose by the ally credits the ally again',
              got[10] == 95 and got[11] is True and got[12] == 90 and got[13] is True and got[14] is True)
        check('A1 controls: the wielder\'s own Break renewed stays the wielder\'s; a second axe user renewing the first one\'s Break keeps '
              'the first within 10 s of the first one\'s own Break, then the second', got[15] is True and got[16] is True and got[17] is True)
        check('A1 control: a renewal by an unknown address keeps the ally within 10 s and names nobody after (Broken still live)',
              got[18] is True and got[19] is True)
        check('A1: the breaker\'s own renewal records it again with a fresh time (by = wielder, byAt = now): a second axe user 3 s later '
              'keeps the first as breaker; 10 s after that renewal the credit passes to the second', got[20] is True and got[21] is True)
        check("A1: past the ally's 10 s the axe user's renewal is the axe user's own Break (by = wielder, byAt = now): a second axe "
              'user renewing 2 s later keeps the first axe user as breaker, not the ally', got[22] is True)

        # ---- A1 (synthetic battle): R.alliedHold (Hyper Wrist's ally-Broke branch) for the axe user lasts at most 10 s from the ally's Break
        got = world().execute(r"""
          PARTY_IDS = {1, 2, 3}
          installFamily()
          local axeIndex
          for i, n in ipairs(Family.CATEGORY_NAMES) do if n == 'axe' then axeIndex = i - 1 end end
          for i = 0, 63 do bpack.section13[i] = {category = axeIndex, chargeTime = 50} end
          local axeman = party(1, {weapon = 0x1001})
          local ally = party(2, {weapon = 0x1001})
          local third = party(3, {weapon = 0x1001})
          local w68 = Family.wrap68(function() end)
          local function swing(foe)
            local f = F({initial = foe.address, target = {removedHp = 50, identifier = 9, work = works[foe.address]}, caster = {status = {}}})
            f.action = Family.ATTACK
            f.target.defense = -1
            w68(f, axeman, foe, {}, classes, helpers)
            if f.target.defense >= 0 then foe.defense = f.target.defense end
            return f.target.defense
          end
          local function held(who, foe) local broken = R.alliedHold(who, foe, NOW, works[foe.address]) return broken end
          local S = 1000000
          local a = foeAt(820, {charge = 20000, required = 30000}); a.defense = 100
          for i = 1, 10 do swing(a) end
          NOW = NOW + 11 * S
          local T = NOW
          Family.noteBroken(a, T, ally.address)
          NOW = T + 9 * S; local w9 = swing(a); local h9 = held(axeman, a)
          NOW = T + 10 * S + 1; local h10 = held(axeman, a)
          NOW = T + 11 * S; swing(a); local h11 = held(axeman, a); local third11 = held(third, a)
          return w9, h9, h10, h11, third11
        """)
        check('A1 world: 9 s after the ally\'s Expose an axe floor hit (no Defence write) keeps R.alliedHold true for the axe user',
              got[0] == -1 and got[1] is True)
        check('A1 world: past 10 s from the ally\'s Break R.alliedHold is false for the axe user, also after the axe user\'s own next hit '
              '(the wearer\'s own cuts do not count)', got[2] is False and got[3] is False)
        check('A1 world: a third member hitting that foe hears an ally\'s Break (the axe user\'s own renewal)', got[4] is True)

        # ---- B: the topic runtime driven end to end (the candidate tree's runtime, proficiency, hybrid lines and the real clan_rank)
        tl = LuaRuntime(unpack_returned_tuples=True)
        tl.globals().sources = tl.table_from({f'scripts/IvaliceLegacy/{n}.lua': (ROOT / f'prototypes/{n}.lua').read_text(encoding='utf-8')
                                              for n in ('proficiency', 'proficiency_runtime', 'hybrid_proficiency', 'clan_rank')})
        tl.globals().ROWS = tl.table_from([tl.table_from(list(r)) for r in ROWS])
        tl.execute(r'''
          printed, spoken, asked, ASK, ticks, handlers, keys = {}, {}, {}, {}, {}, {}, {}
          GRANT_FAILS = false
          print = function(s) printed[#printed+1] = s end
          local real_load = load
          loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
          local wide = function(s) return (s:gsub('.', '%0\0')) end
          local mem = {[0x120000]='MZ', [0x120180]='PE\0\0\x64\x86', [0xD113D4]='LEGCY_\0', [0xD12200]=wide('%sLEGCY_%03d\0')}
          C = real_load(sources['scripts/IvaliceLegacy/clan_rank.lua'])()
          MARKS = 0
          arg = {s32='s32', u32='u32'}
          memory = {u8=setmetatable({}, {__index=function() return 0 end}), u32=setmetatable({}, {__index=function() return 0 end}),
                    u64={}, arg={s32='s32', u32='u32'},
                    readArray=function(a, n) return (mem[a] or ''):sub(1, n) end,
                    execute=function(fn, ret, t1, id, t2, v2, t3, v3)
                      if fn == C.MARKS_FN then return MARKS end
                      if fn == 0x2FD970 then return keys[id] and 1 or 0 end
                      if fn == 0x2FD420 then if not GRANT_FAILS then keys[id] = v2 == 1 end; return v2 end
                      return 0
                    end}
          for r = 1, 12 do
            local a = C.RANK_TABLE + 8 * (r - 1)
            memory.u32[a] = ROWS[r][1]; memory.u8[a + 4] = ROWS[r][2]; memory.u8[a + 5] = ROWS[r][3]
          end
          function save(points, marks)
            memory.u32[C.POINTS] = points; MARKS = marks
            for i = 0, 3 do memory.u8[C.DEN_BITS + i] = 0 end
          end
          dialog = {amese={open=function(t)
                      spoken[#spoken+1] = t.body
                      if t.onClose then t.onClose() end
                      return 1
                    end},
                    aaske={open=function(t)
                      asked[#asked+1] = t
                      local i = table.remove(ASK, 1)
                      if i == nil then i = t.cancel end
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
          FLAG = 0x02099DF0 + 0x99
          function fresh() spoken, asked = {}, {} end
          function said(text) for _, b in ipairs(spoken) do if b:find(text, 1, true) then return true end end return false end
          function times(text) local n = 0 for _, b in ipairs(spoken) do if b:find(text, 1, true) then n = n + 1 end end return n end
          function enter() handlers.onMapJump(302); run(2) end
          function leave(to) handlers.onMapJump(to or 100); run(2) end
          function pick(answers) fresh(); ASK = answers; memory.u8[FLAG] = 1; run() end
        ''')
        got = tl.execute(r'''
          local o = {}
          save(8000, 4)                                                    -- rank 3 waiting; rank 2 rewarded; no Writ carried
          handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          -- not in the hall: the byte is not read (the poll runs only in the Clan hall)
          leave(100); fresh(); memory.u8[FLAG] = 1; run()
          o.outside = #asked == 0 and memory.u8[FLAG] == 1 and R.claimed() == 2
          -- entering the hall drops a stale request: no menu
          fresh(); enter(); run()
          o.stale = #asked == 0 and memory.u8[FLAG] == 0
          -- the native choice sets the byte; the poll opens the five-choice topic; choice 0 claims rank 3 end to end
          pick({0})
          local m = asked[1]
          o.menu = m and m.speaker == 'Montblanc' and table.concat(m.choice, '|') or ''
          o.cancel, o.closeOnCancel, o.lock = m and m.cancel, m and m.closeOnCancel, m and m.lockPlayer
          o.byteCleared = memory.u8[FLAG] == 0
          o.claimed = R.claimed() == 3 and keys[0x81F0] == true and said('Proficiency I 3/4') and said('Wait, I almost forgot your')
          o.oneMenu = #asked == 1
          -- leaving the hall hands nothing out: rank 4 waiting, rank 3 rewarded; one reminder per visit
          save(20000, 8)
          fresh(); leave(100)
          o.leaveNoClaim = R.claimed() == 3 and times('Before you go') == 1
          leave(101); o.noSecondReminder = times('Before you go') == 1
          enter(); leave(100); o.nextVisit = times('Before you go') == 2 and R.claimed() == 3
          -- nothing waiting: no reminder
          save(8000, 4); fresh(); enter(); leave(100); o.noneWaiting = times('Before you go') == 0
          -- "nothing new" (reason 'none'): the next rank's needs, never the neutral line
          enter(); pick({0, 4})
          o.none = said('Nothing new yet. Rank 4 needs 20000 clan') and not said(P.CLAIM_WAIT) and #asked == 2
          -- a Writ that cannot be granted (reason 'writ'): the neutral line, the menu comes back, nothing claimed
          save(30000, 10)                                                  -- rank 5 waiting: needs the Journeyman's Writ
          GRANT_FAILS = true
          pick({0, 4})
          o.writ = said(P.CLAIM_WAIT) and not said('Nothing new yet') and R.claimed() == 3 and keys[0x81F1] ~= true and #asked == 2
          GRANT_FAILS = false
          -- the rank cannot be read (reason 'rank'): the rank table does not match the pinned rows
          local a2 = C.RANK_TABLE + 8
          memory.u32[a2] = 701
          pick({0, 4})
          o.rank = said(P.CLAIM_WAIT) and not said('Nothing new yet') and R.claimed() == 3 and #asked == 2
          memory.u32[a2] = 700
          -- the claim raises (before it changes anything): the neutral line too
          local wn = P.writsNeeded
          P.writsNeeded = function() error('boom') end
          pick({0, 4})
          o.raised = said(P.CLAIM_WAIT) and not said('Nothing new yet') and R.claimed() == 3
          P.writsNeeded = wn
          -- and once the papers are found, the same question claims rank 5
          pick({0})
          o.retry = R.claimed() == 5 and keys[0x81F1] == true
          return o.outside, o.stale, o.menu, o.cancel, o.closeOnCancel, o.lock, o.byteCleared, o.claimed, o.oneMenu, o.leaveNoClaim,
                 o.noSecondReminder, o.nextVisit, o.noneWaiting, o.none, o.writ, o.rank, o.raised, o.retry
        ''')
        check('B poll: outside the Clan hall the byte is not read; entering the hall drops a stale request (no menu)',
              got[0] is True and got[1] is True)
        check('B end to end: the byte the native choice sets opens the topic menu: five choices in order, Cancel = "That\'s all." (4), '
              'closes on Cancel, locks the player; the poll clears the byte',
              got[2] == "Have I earned anything new?|Where do I stand?|Tell me about the Writs.|What is Proficiency?|That's all."
              and got[3] == 4 and got[4] is True and got[5] is True and got[6] is True)
        check('B end to end: choice 0 claims the waiting rank (2 -> 3), grants the Apprentice\'s Writ and speaks the late-Writ and stamp lines; '
              'the talk ends there (one menu)', got[7] is True and got[8] is True)
        check('B leave-hall: leaving with a rank waiting hands out nothing and reminds once; another map change in the same visit does '
              'not remind again; the next visit reminds again', got[9] is True and got[10] is True and got[11] is True)
        check('B leave-hall control: nothing waiting, no reminder', got[12] is True)
        check('B claim reason: "nothing new" (none) answers with the next rank\'s needs, not the neutral line; the menu comes back', got[13] is True)
        check('B claim reason: a Writ that cannot be granted answers with the neutral line (not "Nothing new yet"); nothing is claimed; '
              'the menu comes back', got[14] is True)
        check('B claim reason: an unreadable Clan rank answers with the neutral line', got[15] is True)
        check('B claim reason: a claim that raises answers with the neutral line', got[16] is True)
        check('B claim reason control: once the Writ can be granted, the same question claims rank 5', got[17] is True)

        # ---- B: the neutral line fits the window and is not the "nothing new" line
        lines = PROF.split("M.CLAIM_WAIT = '")[1].split("'\n")[0].split('\\n')
        check('B text: M.CLAIM_WAIT is "Give me a moment to find your papers, kupo. / Ask me again shortly." (2 lines, at most 48 '
              'characters each)', lines == ['Give me a moment to find your papers, kupo.', 'Ask me again shortly.'] and max(map(len, lines)) <= 48)


        self.assertGreaterEqual(len(results), 27)
        self.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
