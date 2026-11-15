"""Review fixes of Ivalice Legacy 0.1.51: the busy retry of a Montblanc topic window does not open a second window once a
late onClose has settled it; a talk carries the generation it started in (the map changes and loads move it on), so the
callbacks, steps, retries and next pages of a talk cut off by a map change are dropped and never touch a newer talk or its
busy flag; an onClose with the reason "gameClosed" stops the talk. Every change carries a control. Static: the prototypes
with mocked dialogue windows whose callbacks arrive later or are fired by hand; nothing here touches a game or a save."""
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
class Review0151Tests(unittest.TestCase):
    def test_every_change_and_its_control(self):
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        RT51 = rd('prototypes/proficiency_runtime.lua')
        ROWS = [(0, 0, 0), (700, 2, 0), (8000, 4, 0), (20000, 8, 0), (30000, 10, 0), (40000, 12, 0), (100000, 14, 0),
                (200000, 16, 0), (250000, 24, 0), (300000, 28, 0), (500000, 32, 0), (1000000, 44, 30)]   # the game's table

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


        # The dialogue world with three more window modes ('busy', 'conflict': accepted, then onError with the plugin's busy or conflict
        # reason, no onClose; 'busy-close': that onError, then a late onClose from the same handle) and two helpers: topicBusyNow()
        # reads the runtime's topicBusy (an upvalue of watchTopic, through debug.getupvalue), notLegacy()/legacyAgain() make the exe
        # check fail or pass.
        TOPIC_WORLD_H = TOPIC_WORLD
        _old = "    if mode == 'error' then later(function() if t.onError then t.onError('late failure') end end)\n"
        assert TOPIC_WORLD_H.count(_old) == 1
        TOPIC_WORLD_H = TOPIC_WORLD_H.replace(_old, _old +
            "    elseif mode == 'busy' then later(function() if t.onError then t.onError('the previous dialogue is still closing') end end)\n"
            "    elseif mode == 'conflict' then later(function() if t.onError then t.onError('a dialogue already owns the native background') end end)\n"
            "    elseif mode == 'busy-close' then\n"
            "      later(function() if t.onError then t.onError('the previous dialogue is still closing') end; later(function() if t.onClose then t.onClose(closeArg) end end) end)\n")
        TOPIC_WORLD_H += r'''
          local function up(f, name)
            local i = 1
            while true do
              local n, v = debug.getupvalue(f, i)
              if n == nil then return nil, false end
              if n == name then return v, true end
              i = i + 1
            end
          end
          function topicBusyNow()
            local wt = up(R.register, 'watchTopic')
            local v, found = up(wt, 'topicBusy')
            assert(found, 'topicBusy not found')
            return v
          end
          local ra = memory.readArray
          function notLegacy() memory.readArray = function(a, n) if a == 0xD113D4 then return ('FFXII_\0'):sub(1, n) end return ra(a, n) end end
          function legacyAgain() memory.readArray = ra end
        '''


        def topic_world_h():
            w = LuaRuntime(unpack_returned_tuples=True)
            w.globals().sources = w.table_from({f'scripts/IvaliceLegacy/{n}.lua': (ROOT / f'prototypes/{n}.lua').read_text(encoding='utf-8')
                                                for n in ('proficiency', 'proficiency_runtime', 'hybrid_proficiency', 'clan_rank')})
            w.globals().ROWS = w.table_from([w.table_from(list(r)) for r in ROWS])
            w.execute(TOPIC_WORLD_H)
            return w


        # The busy-retry world with three more window modes and helpers:
        #   'hold'            accepted and shown; its callbacks are kept in HELD (not delivered) so a scenario fires them later, by hand
        #   'gameClosed'      shown, then onClose(nil, "gameClosed") (the game took the window down)
        #   'busy-close-now'  accepted, then onError (busy) and, on the same tick, a late onClose from the same handle: the onClose
        #                     arrives before the busy retry fires
        # leave() moves to another map (the generation moves on), generationNow() reads topicGeneration.
        TOPIC_WORLD_I = TOPIC_WORLD_H
        _old = "    if mode == 'error' then later(function() if t.onError then t.onError('late failure') end end)\n"
        assert TOPIC_WORLD_I.count(_old) == 1
        TOPIC_WORLD_I = TOPIC_WORLD_I.replace(_old, _old +
            "    elseif mode == 'hold' then opened(); HELD[#HELD + 1] = {t = t, arg = closeArg}\n"
            "    elseif mode == 'gameClosed' then opened(); later(function() if t.onClose then t.onClose(nil, 'gameClosed') end end)\n"
            "    elseif mode == 'busy-close-now' then\n"
            "      later(function() if t.onError then t.onError('the previous dialogue is still closing') end; if t.onClose then t.onClose(closeArg, 'dismissed') end end)\n")
        TOPIC_WORLD_I += r'''
          HELD = {}
          function generationNow()
            local wt = up(R.register, 'watchTopic')
            local v, found = up(wt, 'topicGeneration')
            assert(found, 'topicGeneration not found')
            return v
          end
          function leave() handlers.onMapJump(1) end
          -- ask, and keep the menu open (its callbacks held)
          function holdMenu(answer)
            ASKMODE = {'hold'}
            fresh(); ASK = {answer}; memory.u8[FLAG] = 1; run()
            return HELD[#HELD]
          end
          -- a talk cut off by a map change, then a new talk in the hall that stays open: returns the old and the new window
          function cutThenNew(answer)
            local old = holdMenu(answer)
            leave(); enter()
            local new = holdMenu(4)
            return old, new
          end
          function begin()
            campaign(3); handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
            enter()
          end
        '''


        def topic_world_i():
            w = LuaRuntime(unpack_returned_tuples=True)
            w.globals().sources = w.table_from({f'scripts/IvaliceLegacy/{n}.lua': (ROOT / f'prototypes/{n}.lua').read_text(encoding='utf-8')
                                                for n in ('proficiency', 'proficiency_runtime', 'hybrid_proficiency', 'clan_rank')})
            w.globals().ROWS = w.table_from([w.table_from(list(r)) for r in ROWS])
            w.execute(TOPIC_WORLD_I)
            return w


        # ---- 1: the busy retry returns when the window has already settled (a late onClose came first): no second window
        got = topic_world_i().execute(r'''
          local o = {}
          begin()
          -- busy, and the late onClose (That's all.) on the same tick, before the retry fires
          ASKMODE = {'busy-close-now'}
          pick({4})
          o.bye = #asked == 1 and logged('trying once more') and not logged('dialog failed') and topicBusyNow() == false
          -- the same with the claim: it runs once, and no second menu opens
          ASKMODE = {'busy-close-now'}
          pick({0})
          o.claim = #asked == 1 and R.claimed() == 3 and keys[0x81F0] == true and #spoken > 0 and topicBusyNow() == false
          pick({4})
          o.next = #asked == 1 and topicBusyNow() == false
          -- control: busy, and the late onClose only after the retry fired: the retried window does open (2), the step runs once
          ASKMODE = {'busy-close'}
          pick({4, 4})
          o.control = #asked == 2 and topicBusyNow() == false
          -- busy, then the retried window refused up front, then accepted: the refusal retries of the retried window keep going
          ASKMODE = {'busy', 'refuse'}
          pick({4, 4})
          o.busyRefused = #asked == 2 and logged('trying once more') and not logged('dialog failed') and topicBusyNow() == false
          return o.bye, o.claim, o.next, o.control, o.busyRefused
        ''')
        check('1: busy, then a late onClose before the retry fires: the retry does not open a second window (one menu), the step runs, '
              'topicBusy == false', got[0] is True)
        check('1: the same on the claim: the claim runs once (2 -> 3, the Apprentice\'s Writ), one menu only; the next ask opens the menu',
              got[1] is True and got[2] is True)
        check('1 control: when the late onClose comes after the retry fired, the retried window opens (2 menus) and the step runs once',
              got[3] is True)
        check('1: busy, then the retried window refused up front, then accepted: the retried window\'s refusal retries go on and the talk '
              'ends normally (2 menus shown, topicBusy == false)', got[4] is True)

        # ---- 3: a map change mid-talk, then a new talk; the old talk's callbacks fire late and change nothing
        got = topic_world_i().execute(r'''
          local o = {}
          begin()
          local g0 = generationNow()
          local old, new = cutThenNew(0)
          o.setup = old ~= nil and new ~= nil and old ~= new and generationNow() == g0 + 2 and #asked == 1 and topicBusyNow() == true
          -- the old menu's answer (the claim) arrives late
          fresh()
          old.t.onClose(0, 'dismissed'); run()
          o.claim = R.claimed() == 2 and keys[0x81F0] == nil and #asked == 0 and #spoken == 0 and topicBusyNow() == true
                    and logged('window close of an earlier talk dropped') and not logged('claim')
          -- the new talk still works: its answer ends it, and the next ask claims
          new.t.onClose(4, 'dismissed'); run()
          o.newEnds = topicBusyNow() == false
          pick({0})
          o.newClaims = #asked == 1 and R.claimed() == 3 and keys[0x81F0] == true and topicBusyNow() == false
          return o.setup, o.claim, o.newEnds, o.newClaims
        ''')
        check('3 setup: a talk held open, the map changes (generation +2: away and back), a new talk opens and is busy', got[0] is True)
        check('3: the old menu\'s claim answer arriving late is dropped: nothing claimed or granted, no window, nothing said, and the new '
              'talk stays busy', got[1] is True)
        check('3: the new talk then ends on its own answer, and the next ask claims (2 -> 3)', got[2] is True and got[3] is True)

        got = topic_world_i().execute(r'''
          local o = {}
          -- an old onClose with "gameClosed" (the window the map change took down, delivered late): the new talk stays busy
          begin()
          local old, new = cutThenNew(0)
          fresh(); old.t.onClose(nil, 'gameClosed'); run()
          o.gameClosed = topicBusyNow() == true and #asked == 0 and logged('the game closed the window') and R.claimed() == 2
          -- an old busy onError: the retry is dropped, no window opens, the new talk stays busy
          begin()
          old, new = cutThenNew(0)
          fresh(); old.t.onError('the previous dialogue is still closing'); run()
          o.busy = logged('trying once more') and logged('window retry of an earlier talk dropped') and #asked == 0 and topicBusyNow() == true
          -- an old terminal onError: its failure step is dropped, the new talk stays busy
          begin()
          old, new = cutThenNew(0)
          fresh(); old.t.onError('late failure'); run()
          o.failure = logged('window failure of an earlier talk dropped') and topicBusyNow() == true
          -- an old failing step (P.standing raises) is dropped before it runs: no failure logged, the new talk stays busy
          begin()
          old, new = cutThenNew(1)
          local real = P.standing
          P.standing = function() error('standing boom') end
          fresh(); old.t.onClose(1, 'dismissed'); run()
          P.standing = real
          o.failingStep = not logged('standing boom') and not logged('menu choice failed') and topicBusyNow() == true and #spoken == 0
          -- after each, the new talk ends on its own answer
          new.t.onClose(4, 'dismissed'); run()
          o.newEnds = topicBusyNow() == false
          return o.gameClosed, o.busy, o.failure, o.failingStep, o.newEnds
        ''')
        check('3: an old onClose with "gameClosed" arriving late does not clear the new talk\'s topicBusy', got[0] is True)
        check('3: an old busy onError arriving late: its retry is dropped, no window opens, the new talk stays busy', got[1] is True)
        check('3: an old terminal onError arriving late: its failure step is dropped, the new talk stays busy', got[2] is True)
        check('3: an old step that would fail (P.standing raises) is dropped before it runs: nothing logged as failed, nothing said, and the '
              'new talk\'s topicBusy is not cleared', got[3] is True)
        check('3: the new talk then ends on its own answer', got[4] is True)

        got = topic_world_i().execute(r'''
          local o = {}
          -- an old page continuation: the old talk is on its first "Where do I stand?" page when the map changes
          begin()
          ASKMODE = {'ok'}; PAGEMODE = {'hold'}
          fresh(); ASK = {1}; memory.u8[FLAG] = 1; run()
          local page = HELD[#HELD]
          o.onPage = #asked == 1 and #spoken == 1 and topicBusyNow() == true
          leave(); enter()
          local new = holdMenu(4)
          fresh(); page.t.onClose(nil, 'dismissed'); run()
          o.page = #spoken == 0 and #asked == 0 and logged('window close of an earlier talk dropped') and topicBusyNow() == true
          new.t.onClose(4, 'dismissed'); run()
          o.pageNewEnds = topicBusyNow() == false
          -- the old menu's choice already scheduled (its 60 ms step pending) when the map changes: dropped, nothing claimed
          local m = holdMenu(0)
          m.t.onClose(0, 'dismissed')
          leave(); run()
          o.pending = R.claimed() == 2 and keys[0x81F0] == nil and logged('menu choice of an earlier talk dropped')
          enter()
          pick({0})
          o.pendingThenClaim = #asked == 1 and R.claimed() == 3
          -- a page closed by the player just before the map changes (its 60 ms next-page step pending): dropped, no next page
          ASKMODE = {'ok'}; PAGEMODE = {'hold'}
          fresh(); ASK = {1}; memory.u8[FLAG] = 1; run()
          local p2 = HELD[#HELD]
          fresh(); p2.t.onClose(nil, 'dismissed')
          leave(); run()
          local others = 0
          for _, b in ipairs(spoken) do if b ~= P.REMINDER then others = others + 1 end end
          o.pendingPage = others == 0 and #asked == 0 and logged('next page of an earlier talk dropped')
          return o.onPage, o.page, o.pageNewEnds, o.pending, o.pendingThenClaim, o.pendingPage
        ''')
        check('3: an old page\'s close arriving after the map change: the next page is dropped (nothing said, the menu not reopened), the '
              'new talk stays busy, then ends on its own answer', got[0] is True and got[1] is True and got[2] is True)
        check('3: the menu\'s choice already scheduled when the map changes is dropped (nothing claimed); back in the hall the next ask '
              'claims', got[3] is True and got[4] is True)
        check('3: a page closed just before the map changes (its next page pending) is dropped: no next page, no menu again', got[5] is True)

        got = topic_world_i().execute(r'''
          local o = {}
          begin()
          -- the poll has seen the byte and scheduled the talk's start (300 ms) when the map changes: the start runs nothing
          fresh(); ASK = {}; memory.u8[FLAG] = 1
          run(1)
          o.scheduled = topicBusyNow() == true and #asked == 0
          leave(); run()
          o.start = #asked == 0 and (#spoken == 0 or (#spoken == 1 and spoken[1] == P.REMINDER))   -- leaving with a rank waiting: the reminder only
          enter()
          pick({4})
          o.startNext = #asked == 1 and topicBusyNow() == false
          -- an up-front refusal whose retry is pending when the map changes: the retry is dropped, no old window opens
          ASKMODE = {'refuse'}
          fresh(); ASK = {}; memory.u8[FLAG] = 1
          run(2)
          o.refusedPending = #asked == 0
          leave(); enter(); run()
          o.refusal = #asked == 0 and logged('window retry of an earlier talk dropped')
          pick({4})
          o.refusalNext = #asked == 1 and topicBusyNow() == false
          return o.scheduled, o.start, o.startNext, o.refusedPending, o.refusal, o.refusalNext
        ''')
        check('3: the talk\'s delayed start pending when the map changes opens nothing; back in the hall the next ask opens the menu',
              got[0] is True and got[1] is True and got[2] is True)
        check('3: an up-front refusal\'s retry pending when the map changes is dropped (no window from the old talk); the next ask works',
              got[3] is True and got[4] is True and got[5] is True)

        got = topic_world_i().execute(r'''
          local o = {}
          begin()
          -- control: without a map change the same held answer runs (the claim 2 -> 3): the instrument sees a live talk
          local h = holdMenu(0)
          fresh(); h.t.onClose(0, 'dismissed'); run()
          o.live = R.claimed() == 3 and keys[0x81F0] == true and topicBusyNow() == false and not logged('earlier talk')
          -- "gameClosed" on a page of the current talk: the chain stops (no next page, no menu again) and the talk ends
          pick({1, 4})
          local pagesAll = #spoken
          PAGEMODE = {'gameClosed'}
          pick({1, 4})
          o.page = #spoken == 1 and #asked == 1 and logged('the game closed the window; the talk ends') and topicBusyNow() == false
                   and pagesAll > 1
          pick({4})
          o.pageNext = #asked == 1 and topicBusyNow() == false
          -- "gameClosed" on the menu: no choice step, the talk ends
          ASKMODE = {'gameClosed'}
          pick({0})
          o.menu = #asked == 1 and #spoken == 0 and topicBusyNow() == false and logged('the game closed the window')
          -- control: a page closed by the player ("dismissed") goes on to the next page and back to the menu
          PAGEMODE = {'ok'}
          pick({1, 4})
          o.dismissed = #spoken == pagesAll and #asked == 2 and topicBusyNow() == false
          return o.live, o.page, o.pageNext, o.menu, o.dismissed
        ''')
        check('3 control: with no map change the same held answer runs (the claim 2 -> 3) and nothing is dropped', got[0] is True)
        check('3: "gameClosed" on a page of the current talk stops the chain (one page, no menu again), logs it and clears topicBusy; the '
              'next ask opens the menu', got[1] is True and got[2] is True)
        check('3: "gameClosed" on the menu runs no choice and clears topicBusy', got[3] is True)
        check('3 control: a page dismissed by the player goes on to every page and back to the menu', got[4] is True)

        # ---- code checks (0.1.51)
        topic51 = RT51[RT51.index('local function endTalk'):RT51.index('local function watchTopic')]
        check('1 code: the delayed busy-retry closure starts with `if settled then return end`',
              "        after(TOPIC_BUSY_MS, function()\n          if settled then return end" in RT51)
        check('3 code: step() takes the generation first and drops an older one before running; endTalk() clears topicBusy only for the '
              'current generation; topic() captures topicGeneration when it starts; the poll\'s start checks its generation',
              'local function step(generation, name, fn, ...)\n  if generation ~= topicGeneration then\n' in RT51
              and 'local function endTalk(generation)\n  if generation == topicGeneration then topicBusy = false end\nend' in RT51
              and 'topic = function()\n' in RT51 and '  local generation = topicGeneration\n' in topic51
              and "        if generation ~= topicGeneration then return end   -- 0.1.51: the map changed before the talk began\n" in RT51
              and "if select(2, ...) == 'gameClosed' then" in RT51)
        check('3 code: no topic path clears topicBusy except through endTalk (the poll sets it, watchTopic resets it per visit)',
              topic51.count('topicBusy = false') == 1 and 'topicBusy = true' in topic51
              and all(f"step('{n}'" not in RT51 for n in ('window close', 'window retry', 'window failure', 'next page', 'menu choice')))

        self.assertGreaterEqual(len(results), 20)
        self.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
