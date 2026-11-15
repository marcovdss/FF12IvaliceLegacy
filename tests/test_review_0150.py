"""Review fixes of Ivalice Legacy 0.1.50: Montblanc's no-campaign line is true after a new game that was saved but not
loaded again (it asks you to save and load); a game that is not Ivalice Legacy stays silent; every later step of the topic
(a window's close or failure, a retry, the next page, the menu's choice, the talk coming back to the menu) runs under pcall,
so an error is logged and the talk ends; any indexable dialog target whose open is callable counts, and the reward boxes use
the same test; a topic window that reports a busy or conflict reason after it was accepted is opened once more. Every change
carries a control. Static: the prototypes with mocked dialogue windows whose callbacks arrive later; nothing here touches a
game or a save."""
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
class Review0150Tests(unittest.TestCase):
    def test_every_change_and_its_control(self):
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        RT50 = rd('prototypes/proficiency_runtime.lua')
        PROF50 = rd('prototypes/proficiency.lua')
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


        # ---- M1: a new game that has been saved but not loaded again still has no campaign: the line asks for both, claims nothing
        got = topic_world_h().execute(r'''
          local o = {}
          handlers.onNewGameStart()
          campaign(3)
          enter()
          local side = handlers.save()          -- the save handler: no campaign yet, so no side file (unchanged lifecycle)
          pick()
          o.saved = side == nil and #asked == 0 and #spoken == 1 and spoken[1] == P.NO_CAMPAIGN and next(keys) == nil
                    and R.claimed() == 0 and logged('no Legacy campaign loaded') and topicBusyNow() == false
          pick()
          o.again = #spoken == 1 and spoken[1] == P.NO_CAMPAIGN and next(keys) == nil
          -- control: that save loaded again is a campaign: the menu, not the line
          handlers.load('C:/saves/LEGCY_000', nil)
          enter()
          pick({4})
          o.loaded = #asked == 1 and not said(P.NO_CAMPAIGN) and topicBusyNow() == false
          return o.saved, o.again, o.loaded
        ''')
        check('M1: a new game saved but not loaded again: "About Proficiency." gets P.NO_CAMPAIGN, no side file is written, nothing is '
              'claimed or granted, and topicBusy is cleared', got[0] is True)
        check('M1: asking again says the same line and still claims nothing', got[1] is True)
        check('M1 control: once that save is loaded the menu opens, not the line', got[2] is True)
        nc50 = re.search(r"M\.NO_CAMPAIGN = '(.*)'\n", PROF50)
        nc50_lines = nc50.group(1).replace("\\'", "'").split('\\n') if nc50 else []
        check('M1 text: P.NO_CAMPAIGN is verbatim "Save your journey and load it, kupo, then I\'ll / open your papers." ('
              + str([len(x) for x in nc50_lines]) + ' characters, within 3 x 48) and promises nothing',
              nc50_lines == ["Save your journey and load it, kupo, then I'll", 'open your papers.']
              and [len(x) for x in nc50_lines] == [46, 17]
              and not any(w in nc50.group(1).lower() for w in ('rank', 'writ', 'earned', 'nothing new', 'claim', 'stamp')))
        check('M1: the campaign lifecycle is unchanged: `campaign` is still set only by the load handler, the save handler still writes '
              'nothing without one, and New Game still only resets',
              RT50.count('campaign = name') == 1
              and '    if not campaign then return nil end\n' in RT50
              and "event.registerEventSync('onNewGameStart', function() reset(); freshGame = true return true end)" in RT50
              and [x.strip() for x in re.findall(r'\n\s*campaign = [^\n]*', RT50)] == ['campaign = name'])

        # ---- L1: legacyMode() first (silent under a game that is not Ivalice Legacy), then no campaign gives the line
        got = topic_world_h().execute(r'''
          local o = {}
          handlers.onNewGameStart()
          campaign(3)
          enter()
          notLegacy()
          pick()
          o.silent = #asked == 0 and #spoken == 0 and next(keys) == nil and R.claimed() == 0 and not logged('no Legacy campaign loaded')
                     and topicBusyNow() == false
          pick()
          o.silentAgain = #asked == 0 and #spoken == 0 and topicBusyNow() == false
          -- no plugin under that exe: the direct claim hands nothing out and says nothing
          dialog = nil
          pick()
          o.headless = logged('direct claim off') and #spoken == 0 and not logged('speech (no dialog service)') and next(keys) == nil
          dialog = REAL_DIALOG
          -- the Legacy exe again, still no campaign: the line
          legacyAgain()
          pick()
          o.line = #asked == 0 and #spoken == 1 and spoken[1] == P.NO_CAMPAIGN and next(keys) == nil
          return o.silent, o.silentAgain, o.headless, o.line
        ''')
        check('L1: a game that is not Ivalice Legacy, no campaign: "About Proficiency." stays silent (no window, no line), claims '
              'nothing, and topicBusy is cleared', got[0] is True and got[1] is True)
        check('L1: that game without the dialog plugin: the direct claim hands nothing out ("off") and nothing is spoken or logged as speech',
              got[2] is True)
        check('L1: the Legacy game with no campaign loaded gets the line', got[3] is True)
        topic_src = RT50[RT50.index('topic = function()'):RT50.index('local function topicTick')]
        check('L1: topic() tests legacyMode() before the campaign (0.1.51 shape: the talk ends through endTalk)',
              0 <= topic_src.find('if not legacyMode() then endTalk(generation) return end') < topic_src.find('if not campaign then'))

        # ---- L3: every later topic step runs under pcall; an error is logged and clears topicBusy
        got = topic_world_h().execute(r'''
          local o = {}
          campaign(3); handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          enter()
          -- a throwing P.standing ("Where do I stand?")
          local real = P.standing
          P.standing = function() error('standing boom') end
          pick({1})
          o.standing = logged('topic: menu choice failed') and logged('standing boom') and topicBusyNow() == false and #spoken == 0
          P.standing = real
          pick({4})
          o.standingNext = #asked == 1 and topicBusyNow() == false
          -- the talk coming back to the menu (done -> topic -> greeting) throws
          local greet, calls = P.greeting, 0
          P.greeting = function(s) calls = calls + 1 if calls > 1 then error('greeting boom') end return greet(s) end
          pick({3})
          o.reentry = logged('topic: next page failed') and logged('greeting boom') and topicBusyNow() == false and #asked == 1
          P.greeting = greet
          pick({4})
          o.reentryNext = #asked == 1
          -- a throwing P.nothingNew (claim with nothing new: the line is read from topicState)
          campaign(2)
          local nn = P.nothingNew
          P.nothingNew = function() error('nothing boom') end
          handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          enter()
          P.nothingNew = nn
          pick({0})   -- the first ask hands over the Apprentice's Writ; the second has nothing new
          P.nothingNew = function() error('nothing boom') end
          pick({0})
          o.nothing = logged('topic: menu choice failed') and logged('nothing boom') and topicBusyNow() == false
          P.nothingNew = nn
          pick({4})
          o.nothingNext = #asked == 1
          -- a window's onClose body throws (here the scheduler, when the menu's close schedules the choice)
          local ex = event.executeAfterMs
          local armed = false
          ASKMODE = {}
          fresh(); ASK = {4}; memory.u8[FLAG] = 1
          run(2)   -- the poll sees the byte and opens the menu
          -- only the 60 ms step the menu's close schedules raises (the poll and the gear tick use other delays)
          event.executeAfterMs = function(ms, fn) if armed and ms == 60 then armed = false error('scheduler boom') end return ex(ms, fn) end
          armed = true
          run()
          event.executeAfterMs = ex
          o.close = logged('scheduler boom') and topicBusyNow() == false
          pick({4})
          o.closeNext = #asked == 1
          -- control: a clean talk logs no step failure
          pick({1, 4})
          o.control = #asked == 2 and #spoken > 0 and not logged('failed') and topicBusyNow() == false
          return o.standing, o.standingNext, o.reentry, o.reentryNext, o.nothing, o.nothingNext, o.close, o.closeNext, o.control
        ''')
        check('L3: a throwing P.standing is logged ("menu choice failed"), says nothing, and leaves topicBusy == false', got[0] is True)
        check('L3: after it the next ask opens the menu', got[1] is True)
        check('L3: the done re-entry into topic (greeting) throwing is logged ("next page failed") and leaves topicBusy == false; the '
              'next ask opens the menu', got[2] is True and got[3] is True)
        check('L3: a throwing P.nothingNew is logged and leaves topicBusy == false; the next ask opens the menu', got[4] is True and got[5] is True)
        check('L3: an error inside a window\'s onClose body is logged and leaves topicBusy == false; the next ask opens the menu',
              got[6] is True and got[7] is True)
        check('L3 control: a clean talk logs no step failure and ends with topicBusy == false', got[8] is True)
        check('L3 code: the menu\'s choice, the next page, the window close and failure and both retries run through step() '
              '(0.1.51 shape: each step carries the talk\'s generation)',
              "after(60, function() step(generation, 'menu choice', function()" in RT50
              and "onClose = function() after(60, function() step(generation, 'next page', nextBox) end) end}" in RT50
              and "if onClose then step(generation, 'window close', onClose, ...) end" in RT50
              and "if fail then step(generation, 'window failure', fail) end" in RT50
              and "after(TOPIC_RETRY_MS, function() step(generation, 'window retry', open, kind, options, fail, tries, generation) end)" in RT50
              and "step(generation, 'window retry', open, kind, options, fail, 1, generation)" in RT50)

        # ---- L4: dialogTarget accepts any indexable value whose open (read under pcall) is callable; speak() uses it
        got = topic_world_h().execute(r'''
          local o = {}
          campaign(3); handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          enter()
          -- userdata-like: the targets (and dialog itself) answer through a metatable __index; rawget finds no open
          local function proxy(t) return setmetatable({}, {__index = function(_, k) return t[k] end}) end
          dialog = proxy({aaske = proxy({open = REAL_DIALOG.aaske.open}), amese = proxy({open = REAL_DIALOG.amese.open})})
          pick({0})
          o.proxy = #asked == 1 and not logged('claiming directly') and R.claimed() == 3 and keys[0x81F0] == true and #spoken > 0
                    and topicBusyNow() == false
          -- an open that is a callable table (__call) counts too
          campaign(4)
          local callT = setmetatable({}, {__call = function(_, t) return REAL_DIALOG.aaske.open(t) end})
          dialog = {aaske = {open = callT}, amese = REAL_DIALOG.amese}
          pick({0})
          o.callable = #asked == 1 and not logged('claiming directly') and R.claimed() == 4
          -- an amese without open, with a rank waiting: the direct claim grants, and speak() only logs (never raises)
          campaign(5)
          dialog = {aaske = REAL_DIALOG.aaske, amese = {}}
          pick()
          o.noOpen = R.claimed() == 5 and keys[0x81F1] == true and logged('claiming directly') and logged('speech (no dialog service)')
                     and logged('direct claim spoke') and not logged('claim failed') and #asked == 0
          -- dialog not indexable, and a dialog whose index raises: missing, no raise (the direct claim runs)
          dialog = 5
          pick()
          o.number = logged('claiming directly') and not logged('claim failed')
          dialog = setmetatable({}, {__index = function() error('index boom') end})
          pick()
          o.indexRaises = logged('claiming directly') and not logged('claim failed') and not logged('index boom')
          -- an open that is a plain table without __call is not callable: missing
          dialog = {aaske = {open = {}}, amese = REAL_DIALOG.amese}
          pick()
          o.tableOpen = logged('claiming directly') and #asked == 0
          -- control: the plain plugin tables still open the menu
          dialog = REAL_DIALOG
          pick({4})
          o.control = #asked == 1 and not logged('claiming directly')
          return o.proxy, o.callable, o.noOpen, o.number, o.indexRaises, o.tableOpen, o.control
        ''')
        # an amese whose open raises during the reward boxes (a fresh world: the Writs carried above floor the claimed rank)
        got_r = topic_world_h().execute(r'''
          campaign(3); handlers.load('C:/saves/LEGCY_001', {schema=1, campaign='LEGCY_001', claimedRank=2})
          enter()
          local raising = {open = function(t) if t.queue then error('open boom') end return REAL_DIALOG.amese.open(t) end}
          dialog = {aaske = REAL_DIALOG.aaske, amese = raising}
          pick({0})
          return R.claimed() == 3 and keys[0x81F0] == true and logged('dialog refused') and logged('open boom')
                 and not logged('claim failed') and not logged('menu choice failed') and topicBusyNow() == false
        ''')
        got = tuple(got[:3]) + (got_r,) + tuple(got[3:])
        # a real userdata plugin: Python objects reach Lua as userdata (lupa); a missing attribute raises when indexed, as a
        # userdata without that field may
        class _PyTarget:
            def __init__(self, fn=None):
                if fn is not None:
                    self.open = fn


        class _PyDialog:
            def __init__(self, **k):
                self.__dict__.update(k)


        w_u = topic_world_h()
        w_u.globals().PYDIALOG = _PyDialog(aaske=_PyTarget(w_u.eval('REAL_DIALOG.aaske.open')), amese=_PyTarget(w_u.eval('REAL_DIALOG.amese.open')))
        w_u.globals().PYDIALOG_NOOPEN = _PyDialog(aaske=_PyTarget(w_u.eval('REAL_DIALOG.aaske.open')), amese=_PyTarget())
        got_u = w_u.execute(r"""
          local o = {}
          campaign(3); handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          enter()
          dialog = PYDIALOG
          pick({0})
          o.menu = type(dialog) == 'userdata' and type(dialog.aaske) == 'userdata' and #asked == 1 and not logged('claiming directly')
                   and R.claimed() == 3 and keys[0x81F0] == true and #spoken > 0 and topicBusyNow() == false
          -- a userdata amese without open (indexing it raises), with a rank waiting: direct claim, speak() only logs
          campaign(4)
          dialog = PYDIALOG_NOOPEN
          pick()
          o.noOpen = R.claimed() == 4 and logged('claiming directly') and logged('speech (no dialog service)') and logged('direct claim spoke')
                     and not logged('claim failed') and #asked == 0
          return o.menu, o.noOpen
        """)
        check('L4: a userdata-like plugin (targets answering through a metatable __index) opens the menu, and the claim\'s reward boxes '
              'use it', got[0] is True)
        check('L4: a real userdata plugin (Python objects through lupa) opens the menu, and the claim\'s reward boxes use it',
              got_u[0] is True)
        check('L4: a userdata amese without open (indexing raises): the direct claim grants and speak() only logs; nothing raises',
              got_u[1] is True)
        check('L4: an open that is a callable table (__call) counts as callable', got[1] is True)
        check('L4: an amese without open: the direct claim grants the Journeyman\'s Writ (rank 5) and speak() only logs; nothing raises',
              got[2] is True)
        check('L4: an amese whose open raises while the reward boxes queue: the claim completes and the refusal is logged', got[3] is True)
        check('L4: a dialog that is not indexable, or whose index raises, counts as missing without raising', got[4] is True and got[5] is True)
        check('L4: an open that is a plain table is not callable (counts as missing)', got[6] is True)
        check('L4 control: the plain plugin tables open the menu', got[7] is True)
        check('L4 code: dialogTarget reads dialog[kind] and its open under pcall and returns the open function; speak() uses '
              'dialogTarget(\'amese\') and calls open under pcall',
              'local okService, service = pcall(function() return dialog[kind] end)' in RT50
              and 'local okOpen, openFn = pcall(function() return service.open end)' in RT50 and 'return service, openFn' in RT50
              and "local amese, openFn = dialogTarget('amese')" in RT50
              and "local ok, h, reason = pcall(openFn, {speaker = 'Montblanc', body = box, queue = true, lockPlayer = true})" in RT50
              and 'dialog.amese.open' not in RT50 and RT50.index('local function dialogTarget') < RT50.index('local function speak'))

        # ---- nit: one deferred retry when an accepted topic window reports a busy or conflict reason
        got = topic_world_h().execute(r'''
          local o = {}
          campaign(3); handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=2})
          enter()
          -- a second ask while the boxes still close: the menu reports busy, opens once more, and its choice runs once
          ASKMODE = {'busy'}
          pick({0, 0})   -- the busy window's answer is never delivered; the player answers the retried one
          o.retried = #asked == 2 and logged('trying once more') and not logged('dialog failed') and R.claimed() == 3
                      and keys[0x81F0] == true and topicBusyNow() == false
          -- busy twice: fails after the one retry (nothing claimed), and the next ask works
          campaign(4)
          ASKMODE = {'busy', 'busy'}
          pick({0, 0})
          o.twice = #asked == 2 and logged('trying once more') and logged('dialog failed after it was accepted') and R.claimed() == 3
                    and topicBusyNow() == false
          pick({4})
          o.twiceNext = #asked == 1
          -- a page reporting the background conflict: retried once; every page once, then the menu
          pick({1, 4})
          local standing = #spoken
          PAGEMODE = {'conflict'}
          pick({1, 4})
          o.page = #spoken == standing and #asked == 2 and logged('trying once more') and not logged('dialog failed')
          -- a reason that is not busy or conflict is not retried
          ASKMODE = {'error'}
          pick({0})
          o.noRetry = #asked == 1 and not logged('trying once more') and logged('dialog failed after it was accepted') and R.claimed() == 3
          -- busy, then a late onClose from that same handle, then the retried window's onClose: the step runs once
          ASKMODE = {'busy-close'}
          pick({1, 1, 4})
          o.once = #spoken == standing and logged('trying once more') and topicBusyNow() == false
          return o.retried, o.twice, o.twiceNext, o.page, o.noRetry, o.once, standing
        ''')
        check('nit: a second ask while boxes are still closing gets a reply: the menu\'s busy onError opens it once more, and its choice '
              'runs once (the claim 2 -> 3)', got[0] is True)
        check('nit: busy again after the one retry fails (nothing claimed) and clears topicBusy; the next ask opens the menu',
              got[1] is True and got[2] is True)
        check('nit: a page reporting the native-background conflict is retried once: every page once (' + str(got[6]) + '), then the menu',
              got[3] is True)
        check('nit: an onError reason that is not busy or conflict ("late failure") is not retried', got[4] is True)
        check('nit: busy, then a late onClose from the same handle, then the retried window\'s onClose: the settled flag lets the step run '
              'once', got[5] is True)

        self.assertGreaterEqual(len(results), 25)
        self.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
