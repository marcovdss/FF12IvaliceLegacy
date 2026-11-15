"""Review fixes of Ivalice Legacy 0.1.49: Montblanc's topic claims the rank even without the Lua loader's dialog plugin
(the hall byte claims directly and his lines go to the log); every topic window wires onError to its failure step behind one
settled flag shared with onClose, so a window that is accepted and then fails can never leave the talk stuck, and the next
step never runs twice; "About Proficiency." with no Legacy campaign loaded answers with one short line and hands nothing out;
the axe's evade gate is documented as a defensive guard (Forge's onHitPre middleware 280 already keeps evaded swings out of
the hit stage). Every change carries a control. Static: the prototypes with mocked dialogue windows whose callbacks arrive
later; nothing here touches a game or a save."""
import re
import unittest
from pathlib import Path

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def rd(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class Review0149Tests(unittest.TestCase):
    def test_every_change_and_its_control(self):
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        FAM = rd('prototypes/family_identities.lua')
        PROF = rd('prototypes/proficiency.lua')
        ROWS = [(0, 0, 0), (700, 2, 0), (8000, 4, 0), (20000, 8, 0), (30000, 10, 0), (40000, 12, 0), (100000, 14, 0),
                (200000, 16, 0), (250000, 24, 0), (300000, 28, 0), (500000, 32, 0), (1000000, 44, 30)]   # the game's table

        check('F3: the evade gate is kept (code unchanged); the comments cite functions/280.lua and call it a defensive guard',
              'if M.evaded(formula) then return nil end' in FAM and FAM.count('defensive guard') >= 2 and 'functions/280.lua' in FAM)

        TOPIC_WORLD = r'''
          printed, spoken, asked, pagesOpened, ASK, ticks, handlers, keys = {}, {}, {}, {}, {}, {}, {}, {}
          PAGEMODE, ASKMODE = {}, {}
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
          local function later(fn) ticks[#ticks+1] = fn end
          local function deliver(t, mode, opened, closeArg)
            if mode == 'error' then later(function() if t.onError then t.onError('late failure') end end)
            elseif mode == 'close-error' then
              opened(); later(function() if t.onClose then t.onClose(closeArg) end; later(function() if t.onError then t.onError('late failure') end end) end)
            elseif mode == 'error-close' then
              later(function() if t.onError then t.onError('late failure') end; later(function() if t.onClose then t.onClose(closeArg) end end) end)
            else opened(); later(function() if t.onClose then t.onClose(closeArg) end end) end
          end
          REAL_DIALOG = {amese={open=function(t)
                           local mode = table.remove(PAGEMODE, 1) or 'ok'
                           if mode == 'refuse' then return nil, 'no free window slot' end
                           deliver(t, mode, function() spoken[#spoken+1] = t.body; pagesOpened[#pagesOpened+1] = t end)
                           return {}
                         end},
                         aaske={open=function(t)
                           local mode = table.remove(ASKMODE, 1) or 'ok'
                           if mode == 'refuse' then return nil, 'no free window slot' end
                           asked[#asked+1] = t
                           local i = table.remove(ASK, 1)
                           if i == nil then i = t.cancel end
                           deliver(t, mode, function() end, i)
                           return {}
                         end}}
          dialog = REAL_DIALOG
          event = {registerEventSync=function(name, fn) handlers[name] = fn return true end,
                   registerEventAsync=function(name, fn) handlers[name] = fn return true end,
                   registerLoadHandler=function(_, fn) handlers.load = fn return true end,
                   registerSaveHandler=function(_, fn) handlers.save = fn return true end,
                   executeAfterMs=function(ms, fn) ticks[#ticks+1] = fn end}
          function run(rounds) for _ = 1, rounds or 60 do local t = ticks; ticks = {}; for _, fn in ipairs(t) do pcall(fn) end end end
          R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime', 't', _ENV)()
          assert(R.register())
          P = R.Proficiency
          FLAG = 0x02099DF0 + 0x99
          function fresh() spoken, asked, pagesOpened, printed = {}, {}, {}, {} end
          function said(text) for _, b in ipairs(spoken) do if b:find(text, 1, true) then return true end end return false end
          function logged(text) for _, l in ipairs(printed) do if l:find(text, 1, true) then return true end end return false end
          function enter() handlers.onMapJump(302); run(2) end
          function pick(answers) fresh(); ASK = answers or {}; memory.u8[FLAG] = 1; run() end
          function campaign(rank) save(({[2]={700,2},[3]={8000,4},[4]={20000,8},[5]={30000,10}})[rank][1], ({[2]={700,2},[3]={8000,4},[4]={20000,8},[5]={30000,10}})[rank][2]) end
        '''


        def topic_world():
            w = LuaRuntime(unpack_returned_tuples=True)
            w.globals().sources = w.table_from({f'scripts/IvaliceLegacy/{n}.lua': (ROOT / f'prototypes/{n}.lua').read_text(encoding='utf-8')
                                                for n in ('proficiency', 'proficiency_runtime', 'hybrid_proficiency', 'clan_rank')})
            w.globals().ROWS = w.table_from([w.table_from(list(r)) for r in ROWS])
            w.execute(TOPIC_WORLD)
            return w


        # ---- F1: no dialog plugin: the topic byte still claims the rank, and speak() logs Montblanc's lines as in 0.1.47
        got = topic_world().execute(r'''
          local o = {}
          campaign(3); handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          enter()
          dialog = nil
          pick()
          o.none = R.claimed() == 3 and keys[0x81F0] == true and #asked == 0 and logged('claiming directly')
                   and logged('speech (no dialog service)') and logged('direct claim spoke') and memory.u8[FLAG] == 0
          -- the plugin without the choice window (aaske): still direct, and speak() uses the dialogue window it has
          dialog = {amese = REAL_DIALOG.amese}
          campaign(4)
          pick(); run()
          o.noAaske = R.claimed() == 4 and #asked == 0 and #spoken > 0 and logged('claiming directly')
                      and not logged('speech (no dialog service)')
          -- the plugin without the dialogue window (amese): direct, speak() logs
          dialog = {aaske = REAL_DIALOG.aaske}
          campaign(5)
          pick()
          o.noAmese = R.claimed() == 5 and keys[0x81F1] == true and #asked == 0 and logged('claiming directly') and logged('speech (no dialog service)')
          -- nothing waiting: the direct claim claims nothing and says why
          dialog = nil
          pick()
          o.nothing = R.claimed() == 5 and logged('direct claim none (0 lines)')
          -- a choice window without an open function counts as missing (direct), not as a window to retry
          dialog = {aaske = {}, amese = REAL_DIALOG.amese}
          pick()
          o.noOpen = logged('claiming directly') and #asked == 0 and not logged('topic: dialog refused')
          -- control: with the plugin back the menu opens and nothing is claimed directly
          dialog = REAL_DIALOG
          pick({4})
          o.control = #asked == 1 and not logged('claiming directly')
          -- a direct claim that raises is logged and does not stop the poll
          dialog = nil
          local wn = P.writsNeeded
          P.writsNeeded = function() error('boom') end
          pick()
          o.raised = logged('topic: claim failed') and R.claimed() == 5
          P.writsNeeded = wn
          dialog = REAL_DIALOG
          pick({4})
          o.afterRaise = #asked == 1
          return o.none, o.noAaske, o.noAmese, o.nothing, o.control, o.raised, o.afterRaise, o.noOpen
        ''')
        check('F1: without the dialog plugin the topic byte claims directly: rank 2 -> 3 and the Apprentice\'s Writ, his lines logged '
              '("speech (no dialog service)"), no menu, the byte cleared', got[0] is True)
        check('F1: a plugin without the choice window (aaske) also claims directly, and speak() uses the dialogue window it has', got[1] is True)
        check('F1: a plugin without the dialogue window (amese) claims directly (rank 5, the Journeyman\'s Writ) and speak() logs', got[2] is True)
        check('F1: nothing waiting: the direct claim hands out nothing and logs the reason ("none")', got[3] is True)
        check('F1: a call target without an open function counts as missing: the byte claims directly, no window is retried',
              got[7] is True)
        check('F1 control: with the plugin present the byte opens the menu and nothing is claimed directly', got[4] is True)
        check('F1: a direct claim that raises is logged, nothing is claimed, and the next ask (plugin back) opens the menu',
              got[5] is True and got[6] is True)

        # ---- F2: an accepted window that fails later: topicBusy is cleared (the next ask opens the menu); onClose and onError never
        # both run the next step
        got = topic_world().execute(r'''
          local o = {}
          campaign(3); handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          enter()
          -- 1. the menu is accepted, then fails (onError, no onClose): nothing claimed; the next ask opens the menu
          ASKMODE = {'error'}
          pick({0})
          o.e1 = #asked == 1 and type((asked[1] or {}).onError) == 'function' and R.claimed() == 2 and logged('dialog failed after it was accepted')
          pick({4})
          o.e1next = #asked == 1 and not logged('dialog failed')
          pick({4})
          o.e1again = #asked == 1
          -- 2. the menu closes on "Where do I stand?", then a late onError: the standing pages once, the menu again, no failure step
          pick({1, 4})
          local standing = #spoken   -- the baseline: how many standing pages a clean talk says
          ASKMODE = {'close-error'}
          pick({1, 4})
          o.e2 = #asked == 2 and #spoken == standing and not logged('dialog failed')
          -- 3. onError first, then a late onClose on "Have I earned anything new?": the failure step only; no claim
          ASKMODE = {'error-close'}
          pick({0})
          o.e3 = #asked == 1 and R.claimed() == 2 and logged('dialog failed after it was accepted') and #spoken == 0
          pick({4})
          o.e3next = #asked == 1
          -- 4. a page window accepted then failing: the chain stops (no later page, no menu again); the next ask opens the menu
          PAGEMODE = {'error'}
          pick({1})
          o.e4 = #asked == 1 and #spoken == 0 and logged('dialog failed after it was accepted')
                 and #pagesOpened == 0
          pick({4})
          o.e4next = #asked == 1
          -- 5. a page closes, then a late onError: the next page once, every page once, then the menu again
          PAGEMODE = {'close-error'}
          pick({1, 4})
          o.e5 = #spoken == standing and #asked == 2 and not logged('dialog failed')
          -- 6. every topic window (menu and pages) carries onError
          pick({3, 4})
          local all = #asked == 2 and type(asked[1].onError) == 'function' and type(asked[2].onError) == 'function' and #pagesOpened > 0
          for _, t in ipairs(pagesOpened) do all = all and type(t.onError) == 'function' end
          o.e6 = all
          -- 7. refused up front twice, then accepted: retried as before, one menu, its choice runs once
          ASKMODE = {'refuse', 'refuse'}
          pick({0})
          o.e7 = #asked == 1 and R.claimed() == 3 and keys[0x81F0] == true
          -- 8. the claim's own reward boxes (speak, queue = true) are unchanged: no onError wrapper there, as in 0.1.48
          return o.e1, o.e1next, o.e1again, o.e2, o.e3, o.e3next, o.e4, o.e4next, o.e5, o.e6, o.e7, standing
        ''')
        check('F2: the menu window accepted then failing (onError, no onClose) runs the fail step: nothing claimed, the failure logged',
              got[0] is True)
        check('F2: after that failure topicBusy is false: the next ask opens the menu, and the one after it too', got[1] is True and got[2] is True)
        check('F2: onClose then a late onError: the chosen step runs once (the ' + str(got[11]) + ' standing pages once, then the menu), '
              'the failure step does not run', got[3] is True)
        check('F2: onError then a late onClose on the claim choice: only the failure step runs (no claim, no page), and the next ask '
              'opens the menu', got[4] is True and got[5] is True)
        check('F2: a page window accepted then failing stops the talk (no later page, no menu) and clears topicBusy (the next ask opens '
              'the menu)', got[6] is True and got[7] is True)
        check('F2: a page that closes and then fails late: every page once, then the menu again (no double step)', got[8] is True)
        check('F2: every topic window (menu and pages) carries an onError function', got[9] is True)
        check('F2: a window refused up front is still retried; once accepted its choice runs once (the claim 2 -> 3)', got[10] is True)

        # ---- F4: "About Proficiency." with no Legacy campaign loaded: one short line, nothing claimed
        got = topic_world().execute(r'''
          local o = {}
          handlers.onNewGameStart()
          campaign(3)
          enter()
          pick()
          o.fresh = #asked == 0 and #spoken == 1 and spoken[1] == P.NO_CAMPAIGN and next(keys) == nil and R.claimed() == 0
                    and logged('no Legacy campaign loaded')
          pick()
          o.again = #spoken == 1 and spoken[1] == P.NO_CAMPAIGN
          -- a save that is not a Legacy campaign
          handlers.load('C:/saves/FFXII_000', {})
          enter()
          pick()
          o.other = #asked == 0 and #spoken == 1 and spoken[1] == P.NO_CAMPAIGN and next(keys) == nil
          -- the line's window fails late: topicBusy is cleared, the next ask says the line again
          PAGEMODE = {'error'}
          pick()
          o.failed = #spoken == 0 and logged('dialog failed after it was accepted')
          pick()
          o.afterFail = #spoken == 1
          -- no campaign and no dialog plugin: the direct claim hands out nothing ('off')
          dialog = nil
          pick()
          o.headless = logged('direct claim off') and next(keys) == nil and R.claimed() == 0
          -- control: a Legacy campaign loaded: the menu, not the line
          dialog = REAL_DIALOG
          handlers.load('C:/saves/LEGCY_001', {schema=1, campaign='LEGCY_001', claimedRank=2})
          enter()
          pick({4})
          o.control = #asked == 1 and not said(P.NO_CAMPAIGN)
          return o.fresh, o.again, o.other, o.failed, o.afterFail, o.headless, o.control
        ''')
        check('F4: a new game not saved yet: "About Proficiency." gets P.NO_CAMPAIGN once, no menu, nothing claimed or granted; asking '
              'again says it again', got[0] is True and got[1] is True)
        check('F4: a save that is not a Legacy campaign gets the same line', got[2] is True)
        check('F4: the line\'s window failing late clears topicBusy (the next ask says it again)', got[3] is True and got[4] is True)
        check('F4: no campaign and no dialog plugin: the direct claim hands out nothing ("off")', got[5] is True)
        check('F4 control: with a Legacy campaign loaded the menu opens, not the line', got[6] is True)
        nc = re.search(r"M\.NO_CAMPAIGN = '(.*)'\n", PROF)
        nc_lines = nc.group(1).replace("\\'", "'").split('\\n') if nc else []
        check('F4 text (0.1.50 line): P.NO_CAMPAIGN is "Save your journey and load it, kupo, then I\'ll / open your papers." '
              '(2 lines of at most 48, within 3 x 48) and claims nothing ' + str([len(x) for x in nc_lines]),
              nc_lines == ["Save your journey and load it, kupo, then I'll", 'open your papers.'] and max(map(len, nc_lines)) <= 48
              and not any(w in nc.group(1).lower() for w in ('rank', 'writ', 'earned', 'nothing new', 'claim', 'stamp')))


        self.assertGreaterEqual(len(results), 20)
        self.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
