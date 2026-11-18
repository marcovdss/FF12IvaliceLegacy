"""The co-op Mod Menu page (page.lua, flows.lua, statusview.lua) rebuilt for 0.1.53: the exact tree, every row's label,
values, default and help, the rules of the real Mod Menu (enforced by the test double, coop_menu_double.py), the
redraw timer's calls in every state, the player flows behind the rows, and the status texts. Host tests under lupa;
nothing is deployed and no game runs.

The class of bug this guards: the first session's log held 124 "cannot set active state ... it is not an owned value
row" lines, because the page greyed status rows that the Mod Menu does not let it grey, and the old double accepted
anything.
"""
from __future__ import annotations

import re
import unittest

from coop_menu_double import MENU_DOUBLE
from coop_page_support import LUA, PageEnv, LuaRuntime

SEAT_ACTIONS = ['TOGGLE_CONTROL', 'MOVE_UP', 'MOVE_DOWN', 'MOVE_LEFT', 'MOVE_RIGHT', 'WALK_RUN', 'FLEE', 'BATTLE_MENU',
                'MENU_UP', 'MENU_DOWN', 'MENU_LEFT', 'MENU_RIGHT', 'TARGET_PREV', 'TARGET_NEXT', 'QUICKENING', 'CONFIRM',
                'CANCEL', 'PARTY', 'PAUSE', 'IDENTIFY']
P1_ACTIONS = ['MENU_UP', 'MENU_DOWN', 'MENU_LEFT', 'MENU_RIGHT', 'CONFIRM', 'CANCEL', 'PARTY', 'PAUSE', 'IDENTIFY']
SUBSYSTEMS = ['inputIsolation', 'movement', 'party', 'camera', 'firewallMenus', 'wait', 'render', 'targetsCards']


def controls_page(i):
    actions = SEAT_ACTIONS if i > 1 else P1_ACTIONS
    rows = [(f'opCancelControls{i}', 'button')] + [(f'bind{i}_{a}', 'statusButton') for a in actions]
    return (f'controls{i}', 'submenu', rows + [(f'bindReset{i}', 'button')])


def player_page(i):
    rows = [(f'opCancelPlayer{i}', 'button'), (f'seatPad{i}', 'statusButton'), (f'seatCharacter{i}', 'carousel')]
    return (f'player{i}', 'submenu', rows + ([(f'seatPark{i}', 'toggle'), (f'seatIssue{i}', 'statusButton')] if i > 1 else []))


TREE = [
    ('status', 'statusButton'),
    ('coop', 'toggle'),
    ('safetyWarn', 'statusButton'),
    ('opCancel', 'button'),
    ('players', 'submenu', [('opCancelPlayers', 'button'), player_page(1), player_page(2), player_page(3), player_page(4),
                            ('keyboardSeat', 'carousel')]),
    ('play', 'submenu', [('sharedCamera', 'toggle'), ('guestInteractions', 'toggle'), ('townView', 'carousel'),
                         ('waitPause', 'carousel'), ('guestPause', 'toggle')]),
    ('hud', 'submenu', [
        ('hudStatus', 'statusButton'),
        ('hudPreset', 'carousel'),
        ('hudTheme', 'statusButton'),
        ('hudElements', 'submenu', [('hud_partyTimers', 'toggle'), ('hud_guestCursors', 'toggle'),
                                    ('hud_seatLines', 'toggle'), ('hud_seatDiamonds', 'toggle'),
                                    ('hud_battleLog', 'toggle'), ('hud_targetInfo', 'toggle')]),
        ('hudLog', 'submenu', [('hudLogCompact', 'carousel'), ('hudLogExpanded', 'carousel'), ('hudLogFade', 'carousel'),
                               ('hudLogExpand', 'carousel'), ('hudLogFilter', 'carousel'), ('hudLogSeat', 'carousel')]),
        ('colourMode', 'carousel')]),
    ('controls', 'submenu', [('killSwitch', 'statusButton'), ('opCancelControls', 'button')] +
     [controls_page(i) for i in range(1, 5)]),
    ('safety', 'submenu', [('safetyVision', 'statusButton'), ('safetyManifesto', 'statusButton'),
                           ('safetyRestore', 'button'), ('safetyResult', 'statusButton')]),
    ('advanced', 'submenu', [
        ('keepRunning', 'toggle'), ('moduleOn', 'toggle'), ('diagResidue', 'statusButton'),
        ('diagInteractions', 'statusButton'), ('devMode', 'toggle'),
        ('devOptions', 'submenu', [
            ('diagModule', 'statusButton'), ('statusArmed', 'statusButton'), ('diagFile', 'statusButton'),
            ('logPath', 'statusButton'), ('logLevel', 'toggle'), ('hudTestDraw', 'toggle'),
            ('subsystems', 'submenu', [(f'arm_{k}', 'toggle') for k in SUBSYSTEMS]),
            ('diagCounters', 'button'), ('diagStall', 'button')])]),
]

# The player-facing value rows: id -> (label, options, default index). The shipped defaults are the native defaults.
VALUE_ROWS = {
    'coop': ('Co-op', ['Off', 'On'], 1),
    'seatCharacter1': ('Player 1 character', ['Auto', 'Last used'], 0),
    'seatCharacter2': ('Player 2 character', ['Auto', 'Last used'], 1),
    'seatCharacter3': ('Player 3 character', ['Auto', 'Last used'], 1),
    'seatCharacter4': ('Player 4 character', ['Auto', 'Last used'], 0),
    'seatPark2': ('Player 2 takes part', ['Parked', 'Joined'], 1),
    'seatPark3': ('Player 3 takes part', ['Parked', 'Joined'], 1),
    'seatPark4': ('Player 4 takes part', ['Parked', 'Joined'], 1),
    'keyboardSeat': ('Keyboard and mouse', ['None', 'Player 1', 'Player 2', 'Player 3', 'Player 4'], 2),
    'sharedCamera': ('Shared camera', ['Off', 'On'], 1),
    'guestInteractions': ('Guests use doors, chests, NPCs and shops', ['Off', 'On'], 1),
    'townView': ('Towns show', ['Players only', 'Whole party'], 0),
    'waitPause': ('Wait mode pauses', ['Only when all are in a menu', 'When any player is in a menu'], 0),
    'guestPause': ('Guests may pause', ['No', 'Yes'], 1),
    'hudPreset': ('Battle HUD', ['Off', 'Light', 'Standard', 'Full', 'Custom'], 2),
    'hud_partyTimers': ('Party timers', ['Off', 'On'], 0),
    'hud_guestCursors': ('Guest cursors', ['Off', 'On'], 1),
    'hud_seatLines': ('Target lines', ['Off', 'On'], 1),
    'hud_seatDiamonds': ('Seat diamonds', ['Off', 'On'], 1),
    'hud_battleLog': ('Battle log', ['Off', 'On'], 1),
    'hud_targetInfo': ('Target info', ['Off', 'On'], 1),
    'hudLogCompact': ('Compact lines', [str(n) for n in range(1, 9)], 4),
    'hudLogExpanded': ('Expanded lines', [str(n) for n in range(5, 21)], 15),
    'hudLogFade': ('Fade time', [f'{n} s' for n in range(3, 16)], 3),
    'hudLogExpand': ('Expand button', ['None', 'A', 'B', 'X', 'Y', 'LB', 'RB', 'Back', 'Start', 'L3', 'R3', 'D-pad Up',
                                       'D-pad Down', 'D-pad Left', 'D-pad Right', 'LT', 'RT', 'Left stick Up',
                                       'Left stick Down', 'Left stick Left', 'Left stick Right', 'Right stick Up',
                                       'Right stick Down', 'Right stick Left', 'Right stick Right'], 0),
    'hudLogFilter': ('Log shows', ['All', 'Party', 'Foes', 'Statuses', 'One player'], 0),
    'hudLogSeat': ('Log player', ['Player 1', 'Player 2', 'Player 3', 'Player 4'], 0),
    'colourMode': ('Player colours', ['By character', 'By player'], 0),
    'keepRunning': ('Keep running in background', ['Off', 'On'], 1),
    'moduleOn': ('Start co-op with the game', ['Off', 'On'], 1),
    'devMode': ('Developer options', ['Off', 'On'], 0),
    'logLevel': ('Log detail', ['Normal', 'Verbose'], 0),
    'hudTestDraw': ('Overlay test card', ['Off', 'On'], 0),
}
VALUE_ROWS.update({f'arm_{k}': (label, ['Disarmed', 'Armed'], 1) for k, label in zip(SUBSYSTEMS, [
    'Input isolation', 'Movement', 'Party', 'Camera', 'Firewall menus', 'Wait', 'Render', 'Targets cards'])})


def walk(rows):
    for r in rows:
        yield r
        if len(r) == 3:
            yield from walk(r[2])


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class TreeTest(unittest.TestCase):
    def setUp(self):
        self.env = PageEnv()

    def test_the_exact_tree(self):
        self.assertEqual(self.env.tree(), TREE)

    def test_the_row_counts(self):
        """About twenty player-facing rows on the first levels (status, co-op, the three player pages, the keyboard, the
        five rules, the HUD page), with the Elements and Battle log pages, Controls and Advanced behind submenus."""
        tree = self.env.tree()
        top = {r[0]: r for r in tree}
        self.assertEqual([r[0] for r in tree if r[1] == 'submenu'], ['players', 'play', 'hud', 'controls', 'safety', 'advanced'])

        def leaves(rows):
            return [r for r in rows if r[1] not in ('submenu', 'button')]

        first_level = (leaves(tree) + leaves(top['players'][2]) + leaves(top['play'][2]) + leaves(top['hud'][2]) +
                       [x for r in top['players'][2] if r[1] == 'submenu' for x in leaves(r[2])])
        self.assertEqual(len(first_level), 2 + 1 + 1 + 11 + 5 + 4 + 3, 'status, co-op, the hidden settings warning; keyboard; P1 two rows and 3 x 3 player rows; 5 rules; 4 HUD rows (status, preset, theme, colours); 3 guest checks')
        player_facing = [r for r in walk([x for x in tree if x[0] not in ('controls', 'advanced')])
                         if r[1] not in ('submenu', 'button')]
        self.assertEqual(len(player_facing), 2 + 1 + 1 + 11 + 5 + 4 + 6 + 6 + 3 + 3,
                         'with the Elements and Battle log pages and the Safety page (three status rows, no button)')
        for page_id in ('players', 'play', 'hud', 'controls', 'safety', 'advanced'):
            for r in walk(top[page_id][2]):
                if r[1] == 'submenu':
                    self.assertLessEqual(len(r[2]), 25, r[0] + ' stays a page the Mod Menu takes')

    def test_ids_are_unique_and_registered_once(self):
        ids = [r[0] for r in walk(self.env.tree())]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(list(self.env.menu.rows.keys())), len(ids))

    def test_value_rows(self):
        env = self.env
        seen = set()
        for r in walk(env.tree()):
            if r[1] in ('toggle', 'carousel'):
                seen.add(r[0])
                self.assertIn(r[0], VALUE_ROWS, f'{r[0]} has no expectation')
        self.assertEqual(seen, set(VALUE_ROWS))
        for rid, (label, options, default) in VALUE_ROWS.items():
            row = env.row(rid)
            self.assertEqual(row.label, label, rid)
            self.assertEqual(env.options(rid), options, rid)
            self.assertEqual(row.default, default, rid)

    def test_toggles_list_off_before_on(self):
        """Every toggle reads Off, then On (or the matching pair): the first option is the "no" and the last the "yes"."""
        pairs = {('Off', 'On'), ('Parked', 'Joined'), ('No', 'Yes'), ('Disarmed', 'Armed'), ('Normal', 'Verbose')}
        for r in walk(self.env.tree()):
            if r[1] == 'toggle':
                self.assertIn(tuple(self.env.options(r[0])), pairs, r[0])

    def test_removed_rows_are_gone(self):
        ids = {r[0] for r in walk(self.env.tree())}
        gone = ['statusDetail', 'coopLive', 'guestsParked', 'fullRoster', 'seats', 'colours', 'sounds', 'battleHud',
                'diagnostics', 'seatToggleKey2', 'seatState2', 'seatPark1', 'hudIdentify1',
                'hudIdentify2', 'hudOn', 'visionMinimap', 'visionOverheadHp', 'visionOverheadStatus', 'visionCombatLog',
                'hudDividerVision', 'playerColour1', 'characterColour_VAAN', 'uiSounds1', 'uiSounds2', 'seatDivider1',
                'hud_partyShrink', 'hud_circlesOff', 'hud_aoeRing']
        for g in gone:
            self.assertNotIn(g, ids, g)
        self.assertFalse([i for i in ids if i.startswith(('playerColour', 'characterColour', 'uiSounds', 'vision'))])
        dividers = [r for r in walk(self.env.tree()) if r[1] == 'divider']
        self.assertEqual(dividers, [])

    def test_every_leaf_row_has_a_help_text_and_the_texts_are_right(self):
        env = self.env
        texts = {}
        for r in walk(env.tree()):
            if r[1] == 'submenu':
                continue
            row = env.row(r[0])
            help_text = row.help
            self.assertTrue(isinstance(help_text, str) and len(help_text) > 15, f'{r[0]} has no help')
            self.assertTrue(help_text.isascii() and row.label.isascii(), r[0])
            texts[r[0]] = help_text
        everything = ' '.join(texts.values()) + (LUA / 'page.lua').read_text(encoding='utf-8')
        for wrong in ('Seats page', 'Co-op right now', 'Bisect switch for the session', 'The log file has the details',
                      'Works solo and with co-op off too', 'Applies from the next launch.\n'):
            self.assertNotIn(wrong, everything, wrong)
        restart = [i for i, t in texts.items() if re.search(r'next launch|restart the game', t, re.I)]
        self.assertEqual(restart, ['safetyVision', 'safetyManifesto', 'safetyRestore', 'safetyResult', 'moduleOn'],
                         'only the start-with-the-game switch and the Safety rows (other mods apply at start) say restart')
        self.assertIn('restart the game', texts['moduleOn'])
        self.assertIn('not when co-op is Off at start', texts['keepRunning'])
        self.assertIn('Windowed and borderless only', texts['keepRunning'])
        self.assertIn('controller and mouse are ignored', texts['keepRunning'])
        self.assertIn('no restart', texts['coop'])
        self.assertIn('F8', texts['coop'])
        self.assertIn('hook.log', texts['logPath'])
        self.assertIn('only while Developer options is On', texts['arm_render'])
        self.assertIn('Seat diamonds is Off', texts['bind1_IDENTIFY'])
        self.assertIn('Seat diamonds is Off', texts['bind2_IDENTIFY'])
        self.assertIn('does the same during play', texts['seatPark2'])
        self.assertIn('seat colour', texts['colourMode'])
        self.assertIn('not a regular', texts['colourMode'])          # guests that are not regulars use the seat colour
        self.assertIn('Identify', texts['hud_seatDiamonds'])
        self.assertIn('Player 1 can always pause', texts['guestPause'])
        self.assertIn('gateways', texts['guestInteractions'])
        self.assertIn('Player 1 only', texts['sharedCamera'])
        self.assertIn('Wait', texts['waitPause'])

    def test_registration_holds_for_the_blocked_views_too(self):
        """The page registers whatever the module does; the hidden rows are the settings, never Advanced."""
        mod = self.env.page_mod
        hidden = list(mod.SETTINGS_ROWS.values())
        self.assertEqual(hidden, ['coop', 'players', 'play', 'hud', 'controls', 'keepRunning'])
        self.assertNotIn('advanced', hidden)
        self.assertNotIn('moduleOn', hidden, 'the start-with-the-game switch stays reachable to bring the module back')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class MenuRulesTest(unittest.TestCase):
    """The instrument: the double refuses what the real Mod Menu refuses (so the page tests below mean something)."""

    def setUp(self):
        self.env = PageEnv()
        self.mm = self.env.menu

    def refused(self, call, *args):
        with self.assertRaises(Exception) as cm:
            call('IvaliceLegacyCoop', *args)
        return str(cm.exception)

    def test_set_active_only_on_value_rows(self):
        self.mm.setActive('IvaliceLegacyCoop', 'coop', True)                 # a toggle: fine
        self.mm.setActive('IvaliceLegacyCoop', 'hudPreset', False)           # a carousel: fine
        for row in ('status', 'seatPad2', 'bind2_CONFIRM', 'killSwitch', 'opCancel', 'players', 'diagCounters'):
            text = self.refused(self.mm.setActive, row, True)
            self.assertIn('it is not an owned value row', text, row)
            self.assertIn(f"IvaliceLegacyCoop.{row}", text)
        self.assertEqual(len(self.env.calls.refusals), 7)

    def test_the_other_calls_check_their_row_type_and_values(self):
        mm = self.mm
        self.assertIn('not a status row', self.refused(mm.setStatus, 'coop', 'x'))
        self.assertIn('not a value row', self.refused(mm.setValue, 'status', 0))
        self.assertIn('has no option 2', self.refused(mm.setValue, 'coop', 2))
        self.assertIn('not a value row', self.refused(mm.getValue, 'seatPad2'))
        self.assertIn('is not a registered row', self.refused(mm.setVisible, self.env.lua.table_from({'nowhere': True})))
        mm.setValue('IvaliceLegacyCoop', 'coop', 0)
        self.assertEqual(mm.getValue('IvaliceLegacyCoop', 'coop'), 0, 'getValue returns what was set')

    def test_registration_rules(self):
        lua = self.env.lua
        run = lua.eval('''function(src, ...) return assert(load(src, "@t", "t", _ENV))(...) end''')
        def register(rows):
            return run(MENU_DOUBLE + 'local calls = {}; local mm = newMenu(calls); return pcall(mm.addTable, ...)',
                       lua.table_from({'id': 'x', 'schema': rows}))
        ok = lua.eval('function() return {{id = "a", label = "A", type = "toggle", options = {"Off", "On"}, default = 0, callback = function() end}} end')
        self.assertEqual(register(ok())[0], True)
        dup = lua.eval('function() local r = {id = "a", label = "A", type = "toggle", options = {"Off", "On"}, default = 0, callback = function() end}; return {r, r} end')
        self.assertEqual(register(dup())[0], False, 'an id twice')
        bad = lua.eval('function() return {{id = "a", label = "A", type = "toggle", options = {"Off", "On"}, default = 2, callback = function() end}} end')
        self.assertEqual(register(bad())[0], False, 'a default outside the options')
        nocb = lua.eval('function() return {{id = "a", label = "A", type = "toggle", options = {"Off", "On"}, default = 0}} end')
        self.assertEqual(register(nocb())[0], False, 'a value row without a callback')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class RefreshTest(unittest.TestCase):
    """The page's redraw timer against the enforcing Mod Menu, in every state the page can be in: zero refusals."""

    def setUp(self):
        self.env = PageEnv()
        self.P, self.calls, self.mm = self.env.P, self.env.calls, self.env.menu

    def view(self, kind, text='x', **extra):
        self.P.view = self.env.lua.table_from({'kind': kind, 'text': text, **extra})

    def settle(self):
        for _ in range(3):
            self.P.refresh()

    def test_the_first_refresh_and_the_start_have_no_refusals(self):
        self.assertEqual(list(self.calls.refusals.values()), [])
        self.P.refresh()
        self.assertEqual(list(self.calls.refusals.values()), [])

    def test_set_active_goes_only_to_toggles_and_carousels(self):
        env = self.env
        for ready in (False, True):
            if ready:
                env.go_ready()
            self.settle()
            sent = [i for i in env.menu.rows if env.menu.rows[i].active is not None]
            self.assertTrue(sent, 'some value rows were greyed or released')
            for i in sent:
                self.assertIn(env.menu.rows[i].type, ('toggle', 'carousel'), i)
        self.assertEqual(list(self.calls.refusals.values()), [])

    def test_greying_follows_the_module_and_the_file(self):
        env, rows = self.env, self.env.menu.rows
        self.settle()
        for rid in ('sharedCamera', 'hudPreset', 'coop', 'keepRunning'):
            self.assertIs(rows[rid].active, False, f'{rid}: no module yet')
        for rid in ('moduleOn', 'devMode'):
            self.assertIsNone(rows[rid].active, f'{rid} is never greyed: it can bring the module back')
        env.go_ready()
        self.settle()
        for rid in ('sharedCamera', 'hudPreset', 'coop', 'keepRunning', 'seatPark2'):
            self.assertIs(rows[rid].active, True, rid)
        env.store.readOnly = True
        self.settle()
        self.assertIs(rows['sharedCamera'].active, False, 'a read-only file greys the settings')
        self.assertIs(rows['coop'].active, True, 'the live switch is a command, not a setting: not greyed by the file')

    def test_every_view_and_flag_redraws_without_refusals(self):
        env = self.env
        env.go_ready()
        for kind in ('ok', 'starting', 'missing', 'unsupported', 'blocked', 'disabled', 'fault', 'switched_off'):
            self.view(kind, residue=(kind == 'ok'), interactionsFailed=(kind == 'ok'))
            self.settle()
        env.store.state.diagnostics.developer = True
        env.store.state.battleHud.log.filter = 'MINE'
        env.flows.pending = env.lua.table()
        env.flows.killed = True
        self.settle()
        env.flows.pending = None
        env.flows.killed = False
        env.store.state.diagnostics.developer = False
        self.settle()
        self.assertEqual(list(self.calls.refusals.values()), [])

    def test_an_unchanged_page_sends_nothing(self):
        self.env.go_ready()
        self.view('ok', 'Ready')
        self.settle()
        counts = {k: v for k, v in self.calls.counts.items()}
        self.settle()
        self.assertEqual({k: v for k, v in self.calls.counts.items()}, counts)

    def test_values_follow_the_store(self):
        env, rows, st = self.env, self.env.menu.rows, self.env.store
        st.state.play.waitPause = 'ANY'
        st.state.play.townView = 'PARTY'
        st.state.play.guestPause = False
        st.state.seats[3].parked = True
        st.state.seats[4].character = 'AUTO'
        st.state.keyboardSeat = 4
        self.env.flows.killed = True
        self.settle()
        self.assertEqual([rows[i].value for i in ('waitPause', 'townView', 'guestPause', 'seatPark3', 'seatCharacter4',
                                                  'keyboardSeat', 'coop')], [1, 1, 0, 0, 0, 4, 0])

    def test_the_developer_page_and_the_status_rows_are_shown_or_hidden(self):
        env, rows = self.env, self.env.menu.rows
        self.view('ok')
        self.settle()
        self.assertIs(rows['devOptions'].visible, False)
        env.store.state.diagnostics.developer = True
        self.settle()
        self.assertIs(rows['devOptions'].visible, True)
        self.view('missing')
        self.settle()
        self.assertIs(rows['players'].visible, False)
        self.assertIs(rows['keepRunning'].visible, False)
        self.view('disabled')
        self.settle()
        self.assertIs(rows['players'].visible, True)
        self.assertIsNone(rows['advanced'].visible, 'Advanced is never hidden')


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class RowBehaviourTest(unittest.TestCase):
    """Each player-facing row writes its own field and nothing else."""

    def setUp(self):
        self.env = PageEnv()
        self.P, self.st = self.env.P, self.env.store

    def change(self, rid, value):
        self.P.onChange('IvaliceLegacyCoop', rid, value)

    def test_the_play_rows(self):
        st = self.st.state
        self.assertEqual((st.play.sharedCamera, st.play.guestInteractions, st.play.townView, st.play.waitPause,
                          st.play.guestPause), (True, True, 'PLAYERS', 'ALL', True))
        self.change('sharedCamera', 0)
        self.change('guestInteractions', 0)
        self.change('townView', 1)
        self.change('waitPause', 1)
        self.change('guestPause', 0)
        st = self.st.state
        self.assertEqual((st.play.sharedCamera, st.play.guestInteractions, st.play.townView, st.play.waitPause,
                          st.play.guestPause), (False, False, 'PARTY', 'ANY', False))
        decode = self.env.lua.eval("""function(C, T) local r = C.decode('ilc_settings', T.encode(T.state, 1))
            return {r.shared_camera, r.guest_interactions, r.town_view, r.wait_pause, r.guest_pause} end""")
        self.assertEqual([decode(self.env.C, self.st)[k] for k in range(1, 6)], [0, 0, 1, 1, 0])
        self.change('waitPause', 0)
        self.assertEqual(self.st.state.play.waitPause, 'ALL')

    def test_the_hud_theme_row_cycles_the_themes(self):
        """Phase 8: the HUD theme row names the chosen theme; a press moves to the next one (the built-ins without the
        module; native's list, player themes included, when it answers). The default is stored as ''."""
        P, st = self.P, self.st
        self.assertEqual(st.state.battleHud.theme, '')
        self.assertEqual(P.themeText(), 'Bold Modern')
        seen = []
        for _ in range(3):
            P.nextTheme()
            seen.append(st.state.battleHud.theme)
        self.assertEqual(seen, ['legacy-humanist', 'legacy-crisp', ''], 'wraps back to the default')
        decode = self.env.lua.eval("""function(C, T) local r = C.decode('ilc_settings', T.encode(T.state, 1))
            return r.hud_theme end""")
        st.state.battleHud.theme = 'legacy-crisp'
        raw = decode(self.env.C, st)
        raw = raw.encode('latin-1') if isinstance(raw, str) else bytes(raw)
        self.assertEqual(raw, b'legacy-crisp' + bytes(20))
        self.assertEqual(P.themeText(), 'Crisp Vanilla')
        # Native's list with a player theme in it.
        self.env.go_ready()
        entries = self.env.lua.eval("""function() return {count = 4, refused = 0, scans = 1, entries = {
            {id = 'legacy-bold', name = 'Bold Modern', flags = 3}, {id = 'legacy-humanist', name = 'Refined Vanilla', flags = 1},
            {id = 'legacy-crisp', name = 'Crisp Vanilla', flags = 1}, {id = 'my-theme', name = 'My Theme', flags = 0}}} end""")
        self.env.bridge.get_themes = self.env.lua.eval('function(f) return function() return f() end end')(entries)
        P.themesAt = None
        P.themeList()   # read native's list once (cached), then the store pushes offline as in the other row tests
        self.env.bridge.is_ready = self.env.lua.eval('function() return false end')
        P.nextTheme()
        self.assertEqual(st.state.battleHud.theme, 'my-theme')
        self.assertEqual(P.themeText(), 'My Theme', 'native has not applied the choice yet: the chosen name')
        st.state.battleHud.theme = 'gone-theme'
        self.assertEqual(P.themeText(), 'gone-theme (not found; using default)')

    def test_the_hud_theme_row_says_when_native_fell_back(self):
        """Nit: native's list flags the theme it draws (ACTIVE 4) and the one the applied settings ask for (SELECTED 8).
        Chosen, selected and drawn: its name. Chosen and selected but the default (DEFAULT 2) drawn (broken after the
        scan, or its textures failed): "<name> (using default)"."""
        P, st = self.P, self.st
        lists = self.env.lua.eval("""function(flags_bold, flags_mine) return {count = 4, refused = 0, scans = 1, entries = {
            {id = 'legacy-bold', name = 'Bold Modern', flags = flags_bold},
            {id = 'legacy-humanist', name = 'Refined Vanilla', flags = 1},
            {id = 'legacy-crisp', name = 'Crisp Vanilla', flags = 1},
            {id = 'my-theme', name = 'My Theme', flags = flags_mine}}} end""")
        st.state.battleHud.theme = 'my-theme'
        for bold, mine, text in ((1 | 2 | 4, 8, 'My Theme (using default)'),   # asked for, default drawn
                                 (1 | 2, 4 | 8, 'My Theme'),                    # asked for and drawn
                                 (1 | 2 | 4 | 8, 0, 'My Theme')):               # not applied yet: no claim
            with self.subTest(bold=bold, mine=mine):
                P.themes = lists(bold, mine)
                P.themesAt = 0
                P.now = 0
                self.assertEqual(P.themeText(), text)
        st.state.battleHud.theme = ''
        P.themes = lists(1 | 2 | 4 | 8, 0)
        self.assertEqual(P.themeText(), 'Bold Modern')

    def test_using_default_only_when_the_default_is_drawn(self):
        """Review (12.9): "(using default)" only when the ACTIVE theme carries the DEFAULT flag (2). Chosen and selected
        while another theme that is not the default is drawn (a switch in flight): the chosen name, no claim."""
        P, st = self.P, self.st
        lists = self.env.lua.eval("""function(fb, fh, fc, fm) return {count = 4, refused = 0, scans = 1, entries = {
            {id = 'legacy-bold', name = 'Bold Modern', flags = fb},
            {id = 'legacy-humanist', name = 'Refined Vanilla', flags = fh},
            {id = 'legacy-crisp', name = 'Crisp Vanilla', flags = fc},
            {id = 'my-theme', name = 'My Theme', flags = fm}}} end""")
        for chosen, flags, text in (
                ('my-theme', (1 | 2 | 4, 1, 1, 8), 'My Theme (using default)'),        # control: the default drawn
                ('my-theme', (1 | 2, 1 | 4, 1, 8), 'My Theme'),                        # Refined drawn: not the default
                ('legacy-crisp', (1 | 2, 1 | 4, 1 | 8, 0), 'Crisp Vanilla'),           # a built-in, same rule
                ('legacy-crisp', (1 | 2 | 4, 1, 1 | 8, 0), 'Crisp Vanilla (using default)'),
                ('my-theme', (1 | 4, 1, 1, 8), 'My Theme')):                           # ACTIVE without DEFAULT
            with self.subTest(chosen=chosen, flags=flags):
                st.state.battleHud.theme = chosen
                P.themes = lists(*flags)
                P.themesAt = 0
                P.now = 0
                self.assertEqual(P.themeText(), text)

    def test_a_stored_theme_id_is_checked(self):
        valid = self.env.lua.eval("function(M, v) return M.validThemeId(v, 32) end")
        store_mod = self.env.module('store')
        for v, ok in (('', True), ('legacy-crisp', True), ('my_theme-2', True), ('Bad Theme', False), ('x' * 33, False)):
            self.assertEqual(valid(store_mod, v), ok, v)

    def test_the_wait_default_lists_first_and_is_all(self):
        self.assertEqual(self.env.options('waitPause')[0], 'Only when all are in a menu')
        self.assertEqual(self.st.state.play.waitPause, 'ALL')
        self.assertTrue(self.st.state.play.guestPause, 'guests may pause: Yes by default')

    def test_joined_and_parked(self):
        self.change('seatPark3', 0)
        self.assertEqual([self.st.state.seats[i].parked for i in (2, 3, 4)], [False, True, False])
        self.change('seatPark3', 1)
        self.assertIs(self.st.state.seats[3].parked, False)

    def test_character_auto_and_last_used(self):
        env = self.env
        self.change('seatCharacter4', 1)                         # nobody is in the game: nothing to remember
        self.assertEqual(self.st.state.seats[4].character, 'AUTO')
        self.assertIn('no character to remember', env.last_note())
        env.flows.seats = env.lt({'seats': {1: {'playing': True, 'liveMember': 1}, 2: {'playing': True, 'liveMember': 4},
                                            4: {'playing': True, 'liveMember': 0}}})
        self.change('seatCharacter4', 1)
        self.assertEqual(self.st.state.seats[4].character, 'VAAN', "the character the seat plays now")
        self.change('seatCharacter4', 0)
        self.assertEqual(self.st.state.seats[4].character, 'AUTO')
        self.assertEqual(self.env.P.rows.seatCharacter2.get(self.st.state), 1, 'Basch is stored: Last used')

    def test_keyboard_seat_is_none_or_a_player_one_to_four(self):
        self.change('keyboardSeat', 4)
        self.assertEqual(self.st.state.keyboardSeat, 4)
        self.change('keyboardSeat', 1)
        self.assertEqual(self.st.state.keyboardSeat, 1)
        self.assertEqual(self.env.P.rows.keyboardSeat.get(self.st.state), 1)
        self.change('keyboardSeat', 0)
        self.assertEqual(self.st.state.keyboardSeat, 0, 'None: nobody plays with the keyboard and mouse')
        self.assertEqual(self.env.P.rows.keyboardSeat.get(self.st.state), 0)

    def test_the_coop_switch_is_live_and_needs_the_module(self):
        env = self.env
        self.assertEqual(env.P.rows.coop.get(self.st.state), 1)
        self.change('coop', 0)                                   # the module is not running: refused, nothing changes
        self.assertIn('not running', env.last_note())
        self.assertIs(env.flows.killed, False)
        env.flows.killed = True
        self.assertEqual(env.P.rows.coop.get(self.st.state), 0, 'Off first: killed reads Off')

    def test_start_with_the_game_and_keep_running(self):
        self.change('moduleOn', 0)
        self.assertIs(self.st.state.module.enabledAtStart, False)
        self.change('keepRunning', 0)
        self.assertIs(self.st.state.keepRunning, False)
        self.assertIs(self.st.state.diagnostics.developer, False)

    def test_developer_rows(self):
        self.change('devMode', 1)
        self.change('arm_render', 0)
        self.change('logLevel', 1)
        self.change('hudTestDraw', 1)
        d = self.st.state.diagnostics
        self.assertEqual((d.developer, d.disarm.render, d.hudTestDraw, self.st.state.logLevel),
                         (True, True, True, 'VERBOSE'))
        self.assertIs(d.disarm.camera, False)

    def test_the_controls_pages_use_proper_pad_names(self):
        env = self.env
        flows = env.flows
        names = {5: 'LB', 6: 'RB', 7: 'Back', 8: 'Start', 11: 'D-pad Up', 12: 'D-pad Down', 13: 'D-pad Left',
                 14: 'D-pad Right', 15: 'LT', 16: 'RT', 17: 'Left stick Up', 24: 'Right stick Right', 9: 'L3'}
        for code, want in names.items():
            binding = env.lua.table_from({1: env.lua.table_from({'kind': 'PAD', 'code': code})})
            self.assertEqual(flows.binding_text(binding), want)
        labels = [env.row('hudLogExpand').options[k] for k in range(1, 26)]
        self.assertEqual(labels[0], 'None')
        self.assertEqual(len(labels), 25)
        for want in ('LB', 'RB', 'D-pad Up', 'Start', 'Back', 'Left stick Left', 'Right stick Down', 'R3'):
            self.assertIn(want, labels)
        for bad in ('Lb', 'Rb', 'Dpad up', 'Ls up', 'Rs left', 'Dpad right', 'Lt', 'Start '):
            self.assertNotIn(bad, labels)
        self.assertEqual(self.env.P.rows.bind2_CONFIRM.status(self.st.state), 'Space, Enter, Left mouse, A')
        self.assertEqual(self.env.P.rows.bind1_IDENTIFY.status(None), 'Unbound')
        self.assertEqual(self.env.P.rows.bind2_IDENTIFY.status(None), 'T, Back')

    def test_the_reset_button_restores_a_players_controls(self):
        self.st.change(self.env.lua.eval('function(st) st.seats[3].bindings.CONFIRM = {} end'), None)
        self.assertEqual(self.env.P.rows.bind3_CONFIRM.status(self.st.state), 'Unbound')
        self.change('bindReset3', None)
        self.assertNotEqual(self.env.P.rows.bind3_CONFIRM.status(self.st.state), 'Unbound')

    def test_the_status_text_names_the_players(self):
        env = self.env
        self.P.view = env.lua.table_from({'kind': 'ok', 'text': 'Ready'})
        self.assertEqual(self.P.statusText(), 'Ready', 'no seats read yet')
        env.flows.seats = env.lt({'seats': {1: {'playing': True}, 2: {'playing': True}, 3: {'parked': True},
                                            4: {'benched': True}}})
        self.assertEqual(self.P.statusText(), 'Ready, 2 players')
        env.flows.seats = env.lt({'seats': {1: {'playing': True}}})
        self.assertEqual(self.P.statusText(), 'Ready, 1 player')
        self.P.view = env.lua.table_from({'kind': 'ok', 'text': 'Starting'})
        self.assertEqual(self.P.statusText(), 'Starting')
        self.P.view = env.lua.table_from({'kind': 'disabled', 'text': 'Off (setting)'})
        self.assertEqual(self.P.statusText(), 'Off (setting)')

    def test_the_status_press_explains_who_plays_and_what_is_off(self):
        env = self.env
        env.go_ready(appliedSubsystems=0xFF & ~env.C.enums.subsystem.RENDER)
        self.P.view = env.lua.table_from({'kind': 'ok', 'text': 'Ready'})
        env.flows.seats = env.lt({'seats': {1: {'playing': True, 'liveMember': 0}, 2: {'playing': True, 'liveMember': 4}}})
        self.st.state.diagnostics.developer = True
        self.P.explain()
        note = env.last_note()
        self.assertIn('Ready, 2 players', note)
        self.assertIn('Player 1: Host, playing as Vaan', note)
        self.assertIn('Player 2: Playing as Basch', note)
        self.assertIn('Parts off: Render (developer override).', note)
        self.assertNotIn('Settings are waiting', note)

    def test_the_dry_armed_status_is_not_called_ready_without_saying_why(self):
        text = self.env.S.REASONS.DRY_ARMED.text
        self.assertIn('developer override', text)
        self.assertNotIn('dry-armed', text)

    def test_status_advice_names_the_current_rows(self):
        S = self.env.S
        self.assertNotIn('Co-op on to', S.SWITCH_ADVICE)
        self.assertIn('Co-op back On', S.SWITCH_ADVICE)
        self.assertIn('Start co-op with the game', S.ADVICE.disabled)
        self.assertNotIn('The log file', S.ADVICE.fault)
        self.assertNotIn('Co-op module switch', S.ADVICE.disabled)


class GuestIssueTest(unittest.TestCase):
    """0.1.54: the page says why a joined guest is silent, from the settings alone."""

    def setUp(self):
        self.env = PageEnv()
        self.F, self.st = self.env.flows, self.env.store

    def seat(self, i):
        return self.st.state.seats[i]

    def test_a_default_guest_is_fine_once_it_has_a_pad(self):
        self.seat(2).device = self.env.lt({'kind': 'XINPUT', 'slot': 1})
        self.st.state.keyboardSeat = 0
        self.assertIsNone(self.F.seat_issue(2))

    def test_no_controller(self):
        self.st.state.keyboardSeat = 0
        self.assertEqual(self.F.seat_issue(3), 'No controller: press Controller to pair one')

    def test_the_keyboard_counts_as_a_controller(self):
        self.st.state.keyboardSeat = 2
        self.assertIsNone(self.F.seat_issue(2))

    def test_no_battle_menu_key(self):
        self.seat(2).device = self.env.lt({'kind': 'XINPUT', 'slot': 1})
        self.seat(2).bindings.BATTLE_MENU = self.env.lt([])
        self.assertEqual(self.F.seat_issue(2), "No battle-menu key: reset this player's controls")
        self.seat(2).bindings.BATTLE_MENU = self.env.lt([{'kind': 'KEY', 'code': 70}])    # a key does not help a pad
        self.st.state.keyboardSeat = 0
        self.assertEqual(self.F.seat_issue(2), "No battle-menu key: reset this player's controls")

    def test_the_keyboard_on_a_seat_without_controls(self):
        self.st.state.keyboardSeat = 4
        for action in list(self.seat(4).bindings.keys()):
            self.seat(4).bindings[action] = self.env.lt([])
        self.assertEqual(self.F.seat_issue(4), 'Keyboard is on Player 4, who has no controls')

    def test_a_parked_guest_and_the_host_are_not_flagged(self):
        self.st.state.keyboardSeat = 0
        self.seat(3).parked = True
        self.assertIsNone(self.F.seat_issue(3))
        self.assertIsNone(self.F.seat_issue(1))

    def test_the_player_page_row_and_the_status_press_show_it(self):
        env = self.env
        self.st.state.keyboardSeat = 0
        env.P.refresh()
        self.assertEqual(env.calls.status['seatIssue2'], 'No controller: press Controller to pair one')
        self.seat(2).device = env.lt({'kind': 'XINPUT', 'slot': 1})
        env.P.refresh()
        self.assertEqual(env.calls.status['seatIssue2'], 'OK')
        env.go_ready()
        env.P.view = env.lua.table_from({'kind': 'ok', 'text': 'Ready'})
        env.P.explain()
        note = env.last_note()
        self.assertIn('Player 3 may be silent. No controller: press Controller to pair one.', note)
        self.assertNotIn('Player 2 may be silent', note)


class TimerRefreshTest(unittest.TestCase):
    """0.1.54: the 100 ms timer polls the bridge but redraws only when something changed or once a second."""

    def setUp(self):
        self.env = PageEnv()
        self.P, self.calls = self.env.P, self.env.calls
        self.refreshes = 0
        real = self.P.refresh
        env = self.env

        def counted():
            self.refreshes += 1
            return real()
        self.P.refresh = counted

    def ticks(self, n):
        for _ in range(n):
            self.P.tick()

    def test_a_quiet_page_redraws_once_a_second(self):
        self.ticks(1)
        before = self.refreshes
        self.ticks(30)
        self.assertEqual(self.refreshes - before, 3, 'every tenth step, not every step')

    def test_a_change_or_a_press_redraws_at_once(self):
        self.ticks(2)
        before = self.refreshes
        self.P.onChange('IvaliceLegacyCoop', 'sharedCamera', 0)
        self.ticks(1)
        self.assertEqual(self.refreshes - before, 1)
        self.ticks(1)
        self.assertEqual(self.refreshes - before, 1, 'and then quiet again')

    def test_a_new_module_state_redraws_at_once(self):
        self.ticks(2)
        before = self.refreshes
        self.P.statusSeen = None
        self.env.S.classify = self.env.lua.eval("function() return {kind = 'ok', text = 'Ready', detail = 'x'} end")
        self.ticks(1)
        self.assertEqual(self.refreshes - before, 1)
        self.ticks(1)
        self.assertEqual(self.refreshes - before, 1)

    def test_a_running_controller_step_redraws_every_step(self):
        self.ticks(2)
        before = self.refreshes
        self.env.flows.pending = self.env.lt({'kind': 'pair', 'deadline': 10 ** 9})
        self.ticks(3)
        self.assertEqual(self.refreshes - before, 3)


VISION = 'scripts/config/TheInsurgentsVisionConfig.json'
MANIFESTO = 'scripts/config/TheInsurgentsManifestoConfig.lua'
COMPANIONS = 'scripts/config/TheInsurgentsCompanionsConfig.lua'
INSIGHT = 'scripts/config/HuntersInsightConfig.lua'
VISION_TEXT = ('{\r\n    "customFieldOfView": {\r\n        "parameter": 60.0,\r\n        "state": true\r\n    },\r\n'
               '    "noCombatLog": {\r\n        "state": %s\r\n    },\r\n    "noMinimap": {\r\n        "state": true\r\n    }\r\n}\r\n')
MANIFESTO_TEXT = ('local configStates = {\r\n  trueInvisibility = true,\r\n  noAutoPause = %s,\r\n  noAutoSave = false\r\n}\r\n\r\n'
                  'local configParameters = {\r\n  customAutoSheatheDelay = 1\r\n}\r\n\r\nreturn configStates, configParameters\r\n')
COMPANIONS_TEXT = ('local configStates = {\r\n  partyInTowns = %s,\r\n  partyEquipmentInTowns = false,\r\n  esperWithFullParty = true,\r\n'
                   '  collectGuestEquipment = %s\r\n}\r\n\r\nlocal configParameters = {\r\n  --0:Leader Only, 1:Full Party\r\n'
                   '  partyInTowns = %s,\r\n\r\n  --0:Hide, 1:Show\r\n  partyEquipmentInTowns = 0\r\n}\r\n\r\n'
                   'return configStates, configParameters\r\n')
INSIGHT_TEXT = ('local config = {\n  x = 0,\n  scale = 0.75,\n  alwaysShowItems = %s\n}\n\nreturn config\n\n--[[ NOTES:\n'
                '  alwaysShowItems = true\n  scale = 1.0\n]]\n')
LOAD = 'function(src) local f = assert(load(src, "=x", "t", {})); return f() end'


def settings_files(vision='false', manifesto='false', towns='true', guest='true', param='1', insight='false'):
    return {VISION: VISION_TEXT % vision, MANIFESTO: MANIFESTO_TEXT % manifesto,
            COMPANIONS: COMPANIONS_TEXT % (towns, guest, param), INSIGHT: INSIGHT_TEXT % insight}


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class SafetyPageTest(unittest.TestCase):
    """The Safety page: warnings for the two options that break co-op, and "Restore Legacy settings". Everything is text
    in rows (no dialog), the restore needs a second press, and only the required keys of the four files are written."""

    def make(self, **files):
        env = PageEnv(files=settings_files(**files))
        env.P.readManifesto()                       # what start-up does
        return env

    def settle(self, env, n=3):
        for _ in range(n):
            env.P.refresh()

    def restore(self, env):
        press = env.P.rows.safetyRestore.apply
        press(None)
        press(None)

    def test_no_warning_when_both_options_are_off(self):
        env = self.make()
        self.assertEqual(env.P.visionText(), 'Off')
        self.assertEqual(env.P.manifestoText(), 'Off')
        self.assertEqual(env.P.warnText(), 'None')
        self.settle(env)
        self.assertIs(env.menu.rows['safetyWarn'].visible, False)
        self.assertEqual(len(env.notes), 0)

    def test_vision_no_combat_log_warns_in_plain_words(self):
        env = self.make()
        env.vision_doc.noCombatLog.state = True
        env.P.readVision()
        self.assertEqual(env.P.visionText(), 'On: breaks co-op')
        self.assertEqual(env.P.warnText(), 'Vision No Combat Log is on')
        self.settle(env)
        self.assertIs(env.menu.rows['safetyWarn'].visible, True)
        self.assertEqual(list(env.calls.refusals.values()), [])
        self.assertIn('No Combat Log', env.row('safetyVision').help)
        self.assertIn('co-op cannot work', env.row('safetyVision').help)
        self.assertIn('Safety page', env.row('safetyWarn').help)
        env.vision_doc.noCombatLog.state = False
        env.P.readVision()
        self.settle(env)
        self.assertIs(env.menu.rows['safetyWarn'].visible, False, 'the row hides again once the option is off')

    def test_the_modules_live_report_also_warns(self):
        env = self.make()
        env.go_ready(hudReport=env.C.enums.hud_report.VISION_NO_COMBAT_LOG)
        self.assertEqual(env.P.visionText(), 'On: breaks co-op')

    def test_manifesto_no_auto_pause_warns(self):
        env = self.make(manifesto='true')
        self.assertEqual(env.P.manifestoText(), 'On: breaks co-op')
        self.assertEqual(env.P.warnText(), 'Manifesto No Auto Pause is on')
        self.assertIn('No Auto Pause', env.row('safetyManifesto').help)
        self.assertIn('controllers', env.row('safetyManifesto').help)
        self.assertEqual([p.id for p in env.P.safetyProblems().values()], ['manifesto'])

    def test_both_options_on_say_so(self):
        env = self.make(manifesto='true')
        env.vision_doc.noCombatLog.state = True
        env.P.readVision()
        self.assertEqual(env.P.warnText(), 'Two settings break co-op')
        self.assertEqual([p.id for p in env.P.safetyProblems().values()], ['vision', 'manifesto'])

    def test_missing_files_are_not_a_warning(self):
        env = PageEnv(files={})
        env.P.readManifesto()
        self.assertEqual(env.P.manifestoText(), 'Not found')
        self.assertEqual(env.P.warnText(), 'None')

    def test_a_store_without_a_reader_does_not_break_the_page(self):
        env = PageEnv()                             # no filesystem at all
        env.P.readManifesto()
        self.assertEqual(env.P.manifestoText(), 'Not found')
        self.assertEqual(env.P.safetyText(), 'Not used yet')
        self.restore(env)
        self.assertEqual(env.P.safetyText(), 'Restored. No file needed changing')

    def test_the_restore_asks_twice_and_never_opens_a_window(self):
        env = self.make(manifesto='true', guest='false')
        press = env.P.rows.safetyRestore.apply
        press(None)
        self.assertEqual(env.P.safetyText(), 'Press again to restore')
        self.assertEqual(env.files[MANIFESTO], MANIFESTO_TEXT % 'true', 'one press changes nothing')
        press(None)
        self.assertEqual(env.P.safetyText(), 'Restored. Restart the game to finish')
        self.assertEqual(len(env.notes), 0, 'no dialog: the result is text in a row')
        self.assertEqual(env.dialogs.opened, 0)

    def test_the_confirmation_runs_out(self):
        env = self.make(manifesto='true')
        press = env.P.rows.safetyRestore.apply
        press(None)
        env.P.now = env.page_mod.SAFETY_CONFIRM_MS + 100
        self.assertEqual(env.P.safetyText(), 'Not used yet', 'the arming expired')
        press(None)                                 # a first press again, not a restore
        self.assertEqual(env.files[MANIFESTO], MANIFESTO_TEXT % 'true')
        self.assertEqual(env.P.safetyText(), 'Press again to restore')

    def test_the_restore_writes_only_the_required_keys(self):
        env = self.make(vision='true', manifesto='true', towns='false', guest='false', param='0', insight='true')
        self.restore(env)
        self.assertEqual(env.files[VISION], VISION_TEXT % 'false', 'only noCombatLog changed; the other states stay')
        self.assertEqual(env.files[MANIFESTO], MANIFESTO_TEXT % 'false', 'noAutoSave and the others are untouched')
        self.assertEqual(env.files[COMPANIONS], COMPANIONS_TEXT % ('true', 'true', '1'),
                         'the state and the Full Party parameter; comments and CRLF kept')
        self.assertEqual(env.files[INSIGHT], INSIGHT_TEXT % 'false', 'the key inside the notes comment is not touched')
        self.assertEqual(env.P.safetyText(), 'Restored. Restart the game to finish')

    def test_the_restored_files_still_load_as_lua(self):
        env = self.make(manifesto='true', towns='false', guest='false', param='0', insight='true')
        self.restore(env)
        load = env.lua.eval(LOAD)
        states, params = load(env.files[COMPANIONS])
        self.assertIs(states.partyInTowns, True)
        self.assertIs(states.collectGuestEquipment, True)
        self.assertIs(states.partyEquipmentInTowns, False)
        self.assertEqual(params.partyInTowns, 1)
        self.assertEqual(params.partyEquipmentInTowns, 0)
        self.assertIs(load(env.files[MANIFESTO])[0].noAutoPause, False)
        self.assertIs(load(env.files[INSIGHT]).alwaysShowItems, False)

    def test_settings_already_right_report_no_restart(self):
        env = self.make()
        self.restore(env)
        self.assertEqual(env.P.safetyText(), 'Restored. No file needed changing')
        self.assertEqual(env.files[COMPANIONS], COMPANIONS_TEXT % ('true', 'true', '1'))

    def test_a_missing_mod_is_skipped(self):
        env = PageEnv(files={MANIFESTO: MANIFESTO_TEXT % 'true'})
        env.P.readManifesto()
        self.restore(env)
        self.assertEqual(env.files[MANIFESTO], MANIFESTO_TEXT % 'false')
        self.assertEqual(env.P.safetyText(), 'Restored. Restart the game to finish')
        self.assertIsNone(env.files[VISION])

    def test_a_file_that_does_not_look_right_is_left_alone(self):
        odd = 'return {noAutoSave = false}\n'
        env = PageEnv(files={**settings_files(), MANIFESTO: odd})
        env.P.readManifesto()
        self.restore(env)
        self.assertEqual(env.files[MANIFESTO], odd, 'a key that is not there: nothing is written')
        self.assertEqual(env.P.safetyText(), 'Not fully restored; hook.log has the details')

    def test_a_write_that_does_not_read_back_is_undone(self):
        env = self.make(manifesto='true')
        before = env.files[MANIFESTO]
        env.store.write_text = env.lua.eval('function(p, d) return true end')   # claims success, writes nothing
        self.restore(env)
        self.assertEqual(env.files[MANIFESTO], before)
        self.assertEqual(env.P.safetyText(), 'Not fully restored; hook.log has the details')

    def test_a_write_that_fails_is_reported(self):
        env = self.make(manifesto='true')
        env.store.write_text = env.lua.eval('function(p, d) return false end')
        self.restore(env)
        self.assertEqual(env.P.safetyText(), 'Not fully restored; hook.log has the details')

    def test_the_manifesto_warning_stays_until_the_restart(self):
        env = self.make(manifesto='true')
        self.restore(env)
        self.assertEqual(env.P.manifestoText(), 'On until restart', 'the file is fixed but the running game is not')
        self.assertEqual(env.P.warnText(), 'Manifesto No Auto Pause is on')

    def test_the_restore_clears_the_developer_override_and_nothing_else(self):
        env = self.make()
        env.store.change(env.lua.eval(
            'function(st) st.module.enabledAtStart = false; st.diagnostics.developer = true; '
            'st.diagnostics.hudTestDraw = true; st.diagnostics.disarm.render = true; st.diagnostics.disarm.camera = true; '
            'st.play.sharedCamera = false; st.keepRunning = false end'), None)
        self.restore(env)
        st = env.store.state
        self.assertIs(st.module.enabledAtStart, False, 'whether co-op is on is the player\'s choice')
        self.assertIs(st.diagnostics.developer, False)
        self.assertIs(st.diagnostics.hudTestDraw, False)
        self.assertIs(st.diagnostics.disarm.render, False)
        self.assertIs(st.diagnostics.disarm.camera, False)
        self.assertIs(st.play.sharedCamera, False, 'player choices are not part of the restore')
        self.assertIs(st.keepRunning, False)

    def test_the_restore_leaves_the_coop_switch_and_seats_alone(self):
        env = self.make()
        env.go_ready()
        env.lua.execute('_G.killedTo = nil')
        env.flows.set_killed = env.lua.eval('function(flag) _G.killedTo = flag; return true end')
        env.flows.killed = True
        env.store.change(env.lua.eval(
            'function(st) st.seats[2].parked = true; st.keyboardSeat = 3; st.module.enabledAtStart = false end'), None)
        self.restore(env)
        self.assertIsNone(env.lua.globals().killedTo, 'the on/off switch is not sent')
        self.assertIs(env.flows.killed, True)
        self.assertIs(env.store.state.seats[2].parked, True)
        self.assertEqual(env.store.state.keyboardSeat, 3)
        self.assertIs(env.store.state.module.enabledAtStart, False)
        self.assertNotIn('co-op on', env.page_mod.RESTORE_HELP.replace('Whether co-op is on', ''))

    def test_the_safety_rows_stay_when_the_module_is_blocked(self):
        env = self.make()
        env.P.view = env.lua.table_from({'kind': 'missing', 'text': 'x'})
        self.settle(env)
        self.assertIsNone(env.menu.rows['safety'].visible, 'the Safety page is never hidden')
        self.assertIsNone(env.menu.rows['safetyRestore'].visible)

    def test_the_helpers_read_and_set_values_outside_comments(self):
        mod = PageEnv().page_mod
        text = '-- noAutoPause = true\nlocal t = {\n  noAutoPause = false, other = 3\n}\n--[[\n  noAutoPause = true\n]]\n'
        self.assertIs(mod.lua_get(text, 'noAutoPause'), False)
        self.assertEqual(mod.lua_get(text, 'other'), 3)
        self.assertIsNone(mod.lua_get(text, 'absent'))
        out, matched, changed = mod.lua_set(text, 'noAutoPause', True)
        self.assertEqual((matched, changed), (1, 1))
        self.assertEqual(out, text.replace('  noAutoPause = false,', '  noAutoPause = true,'))
        out, matched, changed = mod.lua_set(text, 'other', False)
        self.assertEqual(matched, 0, 'a boolean is never written over a number')
        self.assertEqual(out, text)
        self.assertEqual(mod.json_get('{"a": {"state": false}, "b": {"x": 1, "state": true}}', 'b'), True)
        out, matched, changed = mod.json_set('{"a": {"state": false}, "b": {"state": true}}', 'b', False)
        self.assertEqual((out, matched, changed), ('{"a": {"state": false}, "b": {"state": false}}', 1, 1))


if __name__ == '__main__':
    unittest.main()