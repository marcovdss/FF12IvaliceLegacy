-- Ivalice Legacy co-op: the Mod Config page (Mod Config > Ivalice Legacy > Co-op).
--
-- Rules the page keeps. Mod Menu callbacks run on the script thread; an older menu version is reported to have crashed
-- when a callback edited its own row, and we do not know which loader version fixed that, so as a defensive rule:
--   * a callback only validates, asks native, pushes and saves (through flows.lua and store.lua); it never calls
--     setValue/setActive/setStatus/setVisible, and neither do the modules it calls;
--   * a 100 ms timer polls the bridge, feeds native events to the flows, and redraws every row from the store and the
--     flows, touching a row only when its value changed;
--   * a refused change keeps the stored value and explains why in a dialog; the timer then redraws the old value;
--   * the page registers even when the native module is missing, blocked or unsupported: then the settings pages are
--     hidden and the Status rows explain why (statusview.lua).
local M = {}

M.MOD_ID = 'IvaliceLegacyCoop'
M.TICK_MS = 100
M.CHARACTER_OPTIONS = {'AUTO', 'NONE', 'VAAN', 'ASHE', 'FRAN', 'BALTHIER', 'BASCH', 'PENELO'}
-- rows and pages hidden while the module is missing, blocked, unsupported or has faulted
M.RESIDUE_TEXT = 'Old Ivalice Together menu files found; they do nothing. Remove them in Vortex.'
M.INTERACTIONS_TEXT = 'Guests cannot use doors, chests, NPCs or loot this session (the hooks did not attach). ' ..
                      'Everything else works. The log file has the details.'
M.SETTINGS_ROWS = {'coopLive', 'guestsParked', 'fullRoster', 'keepRunning', 'seats', 'controls', 'colours', 'sounds',
                   'battleHud'}

-- Battle HUD page. Insurgent's Vision owns the minimap and overhead toggles; its settings file is read (never written)
-- so the page can show their state. Its "No Combat Log" must stay off: the co-op HUD's frame hook sits on that call.
M.VISION_PATH = 'scripts/config/TheInsurgentsVisionConfig.json'
M.VISION_EVERY = 50   -- timer ticks between reads of Vision's file (5 s)
M.VISION_ROWS = {
  {key = 'noMinimap', id = 'visionMinimap', label = 'Minimap', on = 'Off (Vision)', off = 'Shown'},
  {key = 'noOverheadHpBars', id = 'visionOverheadHp', label = 'Overhead HP bars', on = 'Off (Vision)', off = 'Shown'},
  {key = 'noOverheadStatusEffects', id = 'visionOverheadStatus', label = 'Overhead statuses', on = 'Off (Vision)',
   off = 'Shown'},
  {key = 'noCombatLog', id = 'visionCombatLog', label = 'Vision No Combat Log', on = 'ON: turn it off', off = 'Off (correct)'},
}
M.HUD_ROWS = {
  {key = 'partyShrink', label = 'Smaller party list', group = 'party',
   help = 'The native party list at three quarters size in the bottom-right corner. Shows from the next area load.'},
  {key = 'seatEdge', label = 'Seat edge and number', group = 'party',
   help = 'A thin edge in the seat colour and the player number (G for a gambit ally) on each party row.'},
  {key = 'lowHpGlow', label = 'Low HP glow', group = 'party', help = 'The HP number glows red while a member is in danger.'},
  {key = 'damageGhost', label = 'Damage ghost', group = 'party', help = 'HP just lost stays visible on the gauge for a moment.'},
  {key = 'actionLine', label = 'Action line', group = 'party',
   help = 'Shows "Choosing..." while a player is in a battle menu, and which ally an action aims at.'},
  {key = 'targetInfo', label = 'Target Info', group = 'info',
   help = "The expanded Target Info in the top-left corner. Off: the game's own target window."},
  {key = 'battleLog', label = 'Battle log', group = 'info',
   help = "The battle log in the top-right corner, in place of the game's combat log. Off: the game's combat log."},
  {key = 'seatLines', label = 'Target lines in seat colours', group = 'world',
   help = "Each player's target line takes the seat colour; gambit allies stay silver, foes keep the game's colours."},
  {key = 'seatDiamonds', label = 'Seat diamonds', group = 'world',
   help = "A numbered diamond over each player's character at the start of a fight, on a swap or drop-in, and on Identify."},
  {key = 'aoeRing', label = 'Area ring while aiming', group = 'world',
   help = 'A ring only while a player aims an area magick or technick. Attack never gets one, whatever the weapon.'},
  {key = 'circlesOff', label = 'Hide target circles', group = 'world',
   help = "Hides the game's target circles. If Insurgent's Vision already hides them, its setting is used."},
}
M.HUD_GROUPS = {party = 'Party list', info = 'Target Info and battle log', world = 'In the world'}
M.LOG_FILTERS = {'ALL', 'PARTY', 'FOES', 'STATUSES', 'MINE'}
M.LOG_FILTER_LABELS = {'All', 'Party', 'Foes', 'Statuses', 'One player'}
M.TEST_DRAW_HELP = "Draws four test lines in the players' colours, a status icon and a bar with the game's own font " ..
                   "and HUD art, to check the co-op overlay. Off for play."

local function title(s)
  local t = s:lower():gsub('_', ' ')
  return (t:gsub('^%l', string.upper))
end

local function indexOf(list, v)
  for i, x in ipairs(list) do if x == v then return i - 1 end end
  return 0
end

-- deps: modmenu, store, bridge, codec, views, flows, dialogs, statusview; optional: after(ms, fn), log(text), logPath,
-- vision() -> Insurgent's Vision settings table or nil (read only)
function M.create(deps)
  local modmenu, store, bridge, C = deps.modmenu, deps.store, deps.bridge, deps.codec
  local V, F, D, S = deps.views, deps.flows, deps.dialogs, deps.statusview
  local E = C.enums
  local ACTIVE = C.schema.const.ACTIVE_SEATS_MAX or 3      -- seats with a human player (fail closed: 3)
  local log = deps.log or function() end
  local P = {rows = {}, order = {}, cache = {}, native = nil, view = {kind = 'starting', text = 'Starting'},
             cancelIds = {}, devIds = {}}

  local function notify(text) D.notify(text) end

  -- A press runs under pcall: a failure in a flow must never reach the Mod Menu.
  local function guard(fn)
    return function()
      local ok, err = pcall(fn)
      if not ok then
        log('co-op page action failed: ' .. tostring(err))
        pcall(notify, 'That did not work (internal error).')
      end
    end
  end

  local function charList(withNone)
    local list = {}
    for _, n in ipairs(M.CHARACTER_OPTIONS) do if withNone or n ~= 'NONE' then list[#list + 1] = n end end
    return list
  end

  local function labelsOf(list)
    local out = {}
    for _, n in ipairs(list) do out[#out + 1] = title(n) end
    return out
  end

  ---- row registry ----------------------------------------------------------------------------------------------------
  -- add(list, row, get, set, apply): get(state) -> menu value; set(state, value) edits the proposed state; apply(value)
  -- (instead of set) runs a flow that returns true, or false and a reason.
  local function add(list, row, get, set, apply)
    list[#list + 1] = row
    P.rows[row.id] = {row = row, get = get, set = set, apply = apply}
    P.order[#P.order + 1] = row.id
    return row
  end

  local function toggle(list, id, label, options, get, set, help, apply)
    return add(list, {id = id, label = label, type = 'toggle', options = options, default = get(store.state) or 0, help = help,
                      callback = P.onChange}, get, set, apply)
  end

  local function carousel(list, id, label, options, get, set, help, apply)
    return add(list, {id = id, label = label, type = 'carousel', options = options, default = get(store.state) or 0, help = help,
                      callback = P.onChange}, get, set, apply)
  end

  local function statusRow(list, id, label, get, onPress, help)
    local row = {id = id, label = label, type = 'statusButton', status = get(store.state), help = help,
                 callback = onPress and guard(onPress) or nil}
    list[#list + 1] = row
    P.rows[id] = {row = row, status = get, kind = 'status'}
    P.order[#P.order + 1] = id
    return row
  end

  -- "Cancel" for the one running controller step; shown only while there is one.
  local function cancelRow(list, id)
    list[#list + 1] = {id = id, label = 'Cancel the controller step', type = 'button', visible = false,
                       help = 'Stops the pairing or capture that is waiting for a button press.',
                       callback = guard(function() F.cancel() end)}
    P.cancelIds[#P.cancelIds + 1] = id
  end

  local function seatLabel(i) return 'Player ' .. i end
  local P1_MASK = C.schema.const.P1_ACTION_MASK

  ---- schema of the page ------------------------------------------------------------------------------------------------
  function P.build()
    P.rows, P.order, P.cancelIds = {}, {}, {}
    local main, seats, controls, colours, sounds, diag, subs, hud = {}, {}, {}, {}, {}, {}, {}, {}

    -- Status and the co-op module
    statusRow(main, 'status', 'Status', function() return P.statusText() end, function() P.explain() end,
              'State of the co-op module. Press for what it means and what to do.')
    statusRow(main, 'statusDetail', 'Details', function() return P.detailText() end)
    toggle(main, 'moduleOn', 'Co-op module', {'Off', 'On'},
           function(st) return st.module.enabledAtStart and 1 or 0 end,
           function(st, v) st.module.enabledAtStart = v == 1 end,
           'Whether the co-op module starts with the game. Applies from the next launch.')
    toggle(main, 'coopLive', 'Co-op right now', {'On', 'Off'},
           function() return F.killed and 1 or 0 end, nil,
           'Switches co-op off or on immediately, without a restart: guests turn into ordinary allies and the shared camera is released.',
           function(v) return F.set_killed(v == 1) end)
    toggle(main, 'guestsParked', 'Guests', {'Playing', 'Parked'},
           function(st) local all = true; for i = 2, ACTIVE do all = all and st.seats[i].parked end return all and 1 or 0 end,
           nil, 'Parked guests are ordinary allies; their controllers are kept for when they return. Park one seat on the Seats page.',
           function(v) return F.set_parked(nil, v == 1) end)
    toggle(main, 'fullRoster', 'Full roster at start', {'Off', 'On'},
           function(st) return st.fullRoster and 1 or 0 end,
           function(st, v) st.fullRoster = v == 1 end, 'Applies from the next launch.')
    toggle(main, 'keepRunning', 'Keep running in background', {'Off', 'On'},
           function(st) return st.keepRunning and 1 or 0 end,
           function(st, v) st.keepRunning = v == 1 end,
           'Windowed and borderless only: the game keeps running when you switch to another window. While it is ' ..
           'unfocused, your controller and mouse are ignored until you come back. Works solo and with co-op off too. ' ..
           'Exclusive fullscreen pauses as usual.')
    cancelRow(main, 'opCancel')

    -- Seats
    cancelRow(seats, 'opCancelSeats')
    for i = 1, ACTIVE do
      local withNone = i > 1
      seats[#seats + 1] = {id = 'seatDivider' .. i, type = 'divider', label = seatLabel(i)}
      statusRow(seats, 'seatState' .. i, seatLabel(i) .. ' now', function() return F.seat_state_text(i) end, nil,
                i == 1 and 'You are the host.' or 'What this seat is doing in the game.')
      carousel(seats, 'seatCharacter' .. i, seatLabel(i) .. ' character', labelsOf(charList(withNone)),
               function(st) return indexOf(charList(withNone), st.seats[i].character) end, nil,
               i == 1 and 'The character Player 1 plays. Auto keeps the current leader. Picking a guest\'s character swaps you.'
                      or 'Takes effect now if the character is in your party, otherwise next start. Picking another guest\'s character swaps the two.',
               function(v) return F.set_character(i, charList(withNone)[v + 1] or 'AUTO') end)
      statusRow(seats, 'seatPad' .. i, seatLabel(i) .. ' controller', function() return F.device_text(i) end,
                function() F.open_pairing(i) end,
                i == 1 and 'Press to see which controller is yours, or to switch to another (confirmed on the new one).'
                       or 'Press to pair a controller: choose it by name, then press A on it.')
      if i > 1 then
        toggle(seats, 'seatPark' .. i, seatLabel(i) .. ' joined', {'Playing', 'Parked'},
               function(st) return st.seats[i].parked and 1 or 0 end, nil,
               'Parked: this seat becomes an ordinary ally and its controller is kept.',
               function(v) return F.set_parked(i, v == 1) end)
        statusRow(seats, 'seatToggleKey' .. i, seatLabel(i) .. ' join/leave key',
                  function(st) return F.binding_text((st or store.state).seats[i].bindings.TOGGLE_CONTROL) end,
                  function() F.open_capture(i, 'TOGGLE_CONTROL') end,
                  'The button or key that drops this player in and out during play. Press to change it.')
      end
    end
    seats[#seats + 1] = {id = 'seatDividerKeyboard', type = 'divider', label = 'Keyboard and mouse'}
    local owners = {'None'}
    for i = 1, ACTIVE do owners[#owners + 1] = seatLabel(i) end
    carousel(seats, 'keyboardSeat', 'Keyboard/mouse seat', owners,
             function(st) return st.keyboardSeat end,
             function(st, v)
               local clash = F.keyboard_seat_conflict(st, v)
               if clash then return false, clash end
               st.keyboardSeat = v
             end,
             'Which player uses the keyboard and mouse.')

    -- Controls
    statusRow(controls, 'killSwitch', 'Co-op on/off key', function(st) return F.binding_text((st or store.state).killSwitch) end,
              function() F.open_kill_capture() end,
              'The key that turns co-op off and on during play. A keyboard key or the right mouse button. Press to change it.')
    cancelRow(controls, 'opCancelControls')
    local actions = {}
    for name, idx in pairs(E.action) do actions[#actions + 1] = {name = name, idx = idx} end
    table.sort(actions, function(a, b) return a.idx < b.idx end)
    for i = 1, ACTIVE do
      local rows = {}
      cancelRow(rows, 'opCancelControls' .. i)
      for _, a in ipairs(actions) do
        if i > 1 or (P1_MASK >> a.idx) & 1 == 1 then
          statusRow(rows, 'bind' .. i .. '_' .. a.name, F.action_label(a.name),
                    function(st) return F.binding_text((st or store.state).seats[i].bindings[a.name]) end,
                    function() F.open_capture(i, a.name) end, 'Press, then press the new button or key.')
        end
      end
      add(rows, {id = 'bindReset' .. i, label = 'Reset this seat controls', type = 'button', callback = P.onChange,
                 help = 'Puts this player\'s controls back to the defaults.'}, nil, nil)
      P.rows['bindReset' .. i].button = function(st)
        local d = store.defaults()
        local clash = F.reset_conflict(st, i, d.seats[i].bindings)
        if clash then return false, clash end
        st.seats[i].bindings = d.seats[i].bindings
      end
      controls[#controls + 1] = {id = 'controls' .. i, label = seatLabel(i) .. ' controls', type = 'submenu', sub = rows}
    end

    -- Colours
    toggle(colours, 'colourMode', 'Colours follow', {'Character', 'Player'},
           function(st) return st.colours.mode == 'PLAYER' and 1 or 0 end,
           function(st, v) st.colours.mode = v == 1 and 'PLAYER' or 'CHARACTER' end,
           'Character: a character keeps its colour whoever plays it. Player: a player keeps one colour whichever ' ..
           'character they play.')
    for i = 1, ACTIVE do
      carousel(colours, 'playerColour' .. i, seatLabel(i) .. ' colour', labelsOf(store.COLOURS),
               function(st) return indexOf(store.COLOURS, st.seats[i].colour) end,
               function(st, v) st.seats[i].colour = store.COLOURS[v + 1] end,
               "This player's colour for their party row, menus and target lines, when colours follow Player.")
    end
    for _, name in ipairs(store.CHARACTERS) do
      carousel(colours, 'characterColour_' .. name, title(name) .. ' colour', labelsOf(store.COLOURS),
               function(st) return indexOf(store.COLOURS, st.colours.characters[name]) end,
               function(st, v) st.colours.characters[name] = store.COLOURS[v + 1] end,
               "This character's colour for their party row, menus and target lines, when colours follow Character.")
    end

    -- Battle HUD
    statusRow(hud, 'hudStatus', 'HUD status', function() return P.hudStatusText() end, function() P.explainHud() end,
              'Whether the co-op HUD can draw. Press for what to do.')
    toggle(hud, 'hudOn', 'Battle HUD', {'Off', 'On'},
           function(st) return st.battleHud.enabled and 1 or 0 end,
           function(st, v) st.battleHud.enabled = v == 1 end,
           "Off: the game's own battle HUD everywhere. The co-op battle menus stay: guests need them to act.")
    local lastGroup
    for _, r in ipairs(M.HUD_ROWS) do
      if r.group ~= lastGroup then
        lastGroup = r.group
        hud[#hud + 1] = {id = 'hudDivider_' .. r.group, type = 'divider', label = M.HUD_GROUPS[r.group]}
      end
      toggle(hud, 'hud_' .. r.key, r.label, {'Off', 'On'},
             function(st) return st.battleHud.elements[r.key] and 1 or 0 end,
             function(st, v) st.battleHud.elements[r.key] = v == 1 end, r.help)
      if r.key == 'battleLog' then
        carousel(hud, 'hudLogExpand', 'Log expand button', P.expandLabels(),
                 function(st) return P.expandIndex(st.battleHud.log.expandButton) end,
                 function(st, v) st.battleHud.log.expandButton = P.expandOptions()[v + 1] or 'NONE' end,
                 "A controller button that shows 20 lines of the log instead of 5. Any player's controller can press it; " ..
                 "Player 1's press also reaches the game, so pick a button you do not use.")
        carousel(hud, 'hudLogFilter', 'Log shows', M.LOG_FILTER_LABELS,
                 function(st) return indexOf(M.LOG_FILTERS, st.battleHud.log.filter) end,
                 function(st, v) st.battleHud.log.filter = M.LOG_FILTERS[v + 1] or 'ALL' end,
                 'Which lines the log shows.')
        local seatsList = {}
        for i = 1, ACTIVE do seatsList[#seatsList + 1] = seatLabel(i) end
        carousel(hud, 'hudLogSeat', 'Log player', seatsList,
                 function(st) return (st.battleHud.log.filterSeat or 1) - 1 end,
                 function(st, v) st.battleHud.log.filterSeat = v + 1 end,
                 'The player whose lines "One player" shows.')
      end
    end
    hud[#hud + 1] = {id = 'hudDividerIdentify', type = 'divider', label = 'Identify button'}
    for i = 1, ACTIVE do
      statusRow(hud, 'hudIdentify' .. i, seatLabel(i) .. ' Identify',
                function(st) return F.binding_text((st or store.state).seats[i].bindings.IDENTIFY) end,
                function() F.open_capture(i, 'IDENTIFY') end,
                i == 1 and 'Flashes your seat diamond. Unbound at first: every button of your controller also reaches the game.'
                       or "Flashes this player's seat diamond over their character. Press to change it.")
    end
    hud[#hud + 1] = {id = 'hudDividerVision', type = 'divider', label = "Insurgent's Vision (read only)"}
    for _, v in ipairs(M.VISION_ROWS) do
      statusRow(hud, v.id, v.label, function() return P.visionText(v) end, nil,
                v.key == 'noCombatLog' and "Must stay off: the co-op HUD is drawn from the call it removes. Change it on Vision's page."
                                         or "Set on Insurgent's Vision's own page.")
    end
    hud[#hud + 1] = {id = 'hudDividerDiag', type = 'divider', label = 'Diagnostics'}
    toggle(hud, 'hudTestDraw', 'HUD test draw', {'Off', 'On'},
           function(st) return st.diagnostics.hudTestDraw and 1 or 0 end,
           function(st, v) st.diagnostics.hudTestDraw = v == 1 end, M.TEST_DRAW_HELP)

    -- Battle menu sounds
    for i = 1, ACTIVE do
      toggle(sounds, 'uiSounds' .. i, seatLabel(i), {'Off', 'On'},
             function(st) return st.seats[i].uiSounds and 1 or 0 end,
             function(st, v) st.seats[i].uiSounds = v == 1 end,
             "The sounds of this player's battle menus: cursor, confirm, open and close. Off mutes them.")
    end

    -- Subsystem switches (hidden until the developer toggle is on)
    for _, k in ipairs(store.SUBSYSTEMS) do
      toggle(subs, 'arm_' .. k, title((k:gsub('(%u)', ' %1'))), {'Disarmed', 'Armed'},
             function(st) return st.diagnostics.arm[k] and 1 or 0 end,
             function(st, v) st.diagnostics.arm[k] = v == 1 end,
             'Bisect switch for the session. Sent to the module with the other settings.')
    end

    -- Diagnostics
    toggle(diag, 'logLevel', 'Log level', {'Normal', 'Verbose'},
           function(st) return st.logLevel == 'VERBOSE' and 1 or 0 end,
           function(st, v) st.logLevel = v == 1 and 'VERBOSE' or 'NORMAL' end)
    statusRow(diag, 'logPath', 'Log file', function() return deps.logPath or 'x64/hook.log' end)
    statusRow(diag, 'diagModule', 'Module', function() return P.moduleText() end, nil,
              'The module\'s own state and boot reason.')
    statusRow(diag, 'statusArmed', 'Armed subsystems', function() return P.armedText() end, nil,
              'How many subsystems the module reports as armed.')
    statusRow(diag, 'diagResidue', 'Old Together menu files', function() return P.view.residue and 'Found, harmless' or 'None' end,
              function() notify(M.RESIDUE_TEXT) end, 'Press for what to do.').visible = false
    statusRow(diag, 'diagInteractions', 'Guest interactions', function()
                return P.view.interactionsFailed and 'Unavailable' or 'Ready' end,
              function() notify(M.INTERACTIONS_TEXT) end, 'Shown only when they could not start.').visible = false
    statusRow(diag, 'diagFile', 'Settings file', function() return P.fileText() end, nil,
              'Whether coop.json was read, repaired, upgraded or is read-only.')
    toggle(diag, 'devMode', 'Developer options', {'Hidden', 'Shown'},
           function(st) return st.diagnostics.developer and 1 or 0 end,
           function(st, v) st.diagnostics.developer = v == 1 end,
           'Shows the developer rows below. Saved with the settings and sent to the module. For testing only.')
    diag[#diag + 1] = {id = 'subsystems', label = 'Subsystem switches', type = 'submenu', visible = false, sub = subs}
    diag[#diag + 1] = {id = 'diagCounters', label = 'Write counters to the log', type = 'button', visible = false,
                       help = 'Writes every counter of the co-op module to the log file, one line each.',
                       callback = guard(function()
                         local ok, count = F.dump_counters()
                         if ok then notify(string.format('%d counters were written to the log file.', count)) end
                       end)}
    diag[#diag + 1] = {id = 'diagStall', label = 'Stall the co-op thread (test)', type = 'button', visible = false,
                       help = 'Stops the co-op module for 3 seconds on purpose. The game should play as stock meanwhile.',
                       callback = guard(function() F.ask_stall() end)}
    P.devIds = {'subsystems', 'diagCounters', 'diagStall'}

    main[#main + 1] = {id = 'seats', label = 'Seats', type = 'submenu', sub = seats}
    main[#main + 1] = {id = 'controls', label = 'Controls', type = 'submenu', sub = controls}
    main[#main + 1] = {id = 'colours', label = 'Colours', type = 'submenu', sub = colours}
    main[#main + 1] = {id = 'sounds', label = 'Menu sounds in battle', type = 'submenu', sub = sounds}
    main[#main + 1] = {id = 'battleHud', label = 'Battle HUD', type = 'submenu', sub = hud}
    main[#main + 1] = {id = 'diagnostics', label = 'Diagnostics', type = 'submenu', sub = diag}
    return {id = M.MOD_ID, name = 'Co-op', group = {id = 'IvaliceLegacy', name = 'Ivalice Legacy',
            description = 'Ivalice Legacy settings.'}, schema = main}
  end

  ---- text ------------------------------------------------------------------------------------------------------------------
  function P.statusText()
    if P.view.kind == 'ok' and store.lastRejection then return 'Settings refused by the module' end
    return P.view.text
  end

  -- The log expand button's options: None, then every pad control in schema order.
  function P.expandOptions()
    if P.expandList then return P.expandList end
    local list = {}
    for name, v in pairs(E.pad_control) do list[#list + 1] = {name = name, v = v} end
    table.sort(list, function(a, b) return a.v < b.v end)
    local out = {'NONE'}
    for _, x in ipairs(list) do out[#out + 1] = x.name end
    P.expandList = out
    return out
  end

  function P.expandLabels()
    local out = {}
    for i, n in ipairs(P.expandOptions()) do out[i] = i == 1 and 'None' or title(n) end
    return out
  end

  function P.expandIndex(name) return indexOf(P.expandOptions(), name or 'NONE') end

  -- Insurgent's Vision's settings (read only), re-read every VISION_EVERY ticks. nil: not installed or unreadable.
  function P.readVision()
    if not deps.vision then P.vision = nil return end
    local ok, t = pcall(deps.vision)
    P.vision = ok and type(t) == 'table' and t or nil
  end

  -- true / false for a Vision option's state, nil when unknown.
  function P.visionState(key)
    local o = P.vision and P.vision[key]
    if type(o) == 'table' and type(o.state) == 'boolean' then return o.state end
    return nil
  end

  function P.visionText(row)
    local state = P.visionState(row.key)
    if state == nil then return P.vision and 'Unknown' or 'Vision not found' end
    return state and row.on or row.off
  end

  -- The Battle HUD's status line: native's report (status hud_report) first, Vision's file second.
  function P.hudReport()
    local n = P.native
    return (bridge.status.state == 'ready' and n and n.hudReport) or 0
  end

  local function reportHas(name) return (P.hudReport() & ((E.hud_report or {})[name] or 0)) ~= 0 end

  function P.hudStatusText()
    if reportHas('VISION_NO_COMBAT_LOG') or P.visionState('noCombatLog') == true then
      return "Turn off Vision's No Combat Log"
    end
    if bridge.status.state ~= 'ready' or not P.native then return 'n/a' end
    if reportHas('FRAME_HOOK_OFF') then return 'Not drawing (frame hook off)' end
    if reportHas('RENDER_OFF') then return 'Not drawing (rendering off)' end
    if not store.state.battleHud.enabled then return 'Off' end
    local notes = {}
    if reportHas('PARTY_LAYOUT_FOREIGN') then notes[#notes + 1] = 'party list kept as another mod set it' end
    if reportHas('CIRCLES_BY_VISION') then notes[#notes + 1] = 'circles hidden by Vision' end
    if reportHas('CIRCLES_FOREIGN') then notes[#notes + 1] = 'circles left to another mod' end
    return #notes > 0 and ('On; ' .. table.concat(notes, '; ')) or 'On'
  end

  function P.explainHud()
    local lines = {P.hudStatusText()}
    if reportHas('VISION_NO_COMBAT_LOG') or P.visionState('noCombatLog') == true then
      lines[#lines + 1] = 'Insurgent\'s Vision "No Combat Log" removes the call the co-op HUD is drawn from, so neither ' ..
                          'the HUD nor the co-op battle menus can show. Turn it off on Vision\'s page; the co-op battle log ' ..
                          'already replaces the game\'s combat log.'
    elseif reportHas('FRAME_HOOK_OFF') then
      lines[#lines + 1] = 'Another mod changed the code the co-op HUD is drawn from, so the game\'s own HUD is used. ' ..
                          'The log file has the details.'
    elseif reportHas('RENDER_OFF') then
      lines[#lines + 1] = 'The co-op HUD is not being drawn: its rendering is switched off, was stopped after an error, ' ..
                          'or could not start. The game\'s own HUD and battle menus are used. The log file has the details.'
    end
    if reportHas('PARTY_LAYOUT_FOREIGN') then
      lines[#lines + 1] = 'Another mod changed the party list layout, so it is left as that mod set it.'
    end
    notify(table.concat(lines, '\n'))
  end

  function P.armedText()
    local n = P.native
    if bridge.status.state ~= 'ready' or not n then return 'n/a' end
    local count, total = 0, 0
    for _, bit in pairs(E.subsystem) do
      total = total + 1
      if (n.appliedSubsystems & bit) ~= 0 then count = count + 1 end
    end
    return string.format('%d of %d', count, total)
  end

  function P.moduleText()
    local n = P.native
    if bridge.status.state ~= 'ready' or not n then return 'not connected' end
    local text = title(n.stateName or '?')
    if n.reason ~= 0 then text = text .. ' (' .. (P.view.reason or ('reason ' .. n.reason)) .. ')' end
    return text
  end

  function P.fileText()
    return store.status_text() or (store.fileStatus == 'new' and 'created' or 'OK')
  end

  function P.detailText()
    local note = store.status_text()
    if P.view.kind ~= 'ok' then
      local parts = {}
      if P.view.detail then parts[#parts + 1] = P.view.detail end
      if note then parts[#parts + 1] = note end
      return #parts > 0 and table.concat(parts, '; ') or ''
    end
    local parts = {}
    if store.lastRejection then parts[#parts + 1] = 'refused: ' .. store.lastRejection end
    if note then parts[#parts + 1] = note end
    local n = P.native
    if n and n.state ~= E.state.INERT and n.appliedGeneration ~= n.acceptedGeneration then
      parts[#parts + 1] = 'settings waiting to apply'
    end
    if P.view.detail then parts[#parts + 1] = P.view.detail end
    return #parts > 0 and table.concat(parts, '; ') or 'OK'
  end

  -- The Status row's press: say what the state means and what to do about it.
  function P.explain()
    local v = P.view
    local lines = {v.text}
    if v.detail then lines[#lines + 1] = v.detail end
    if v.advice then lines[#lines + 1] = v.advice end
    if store.readOnly then lines[#lines + 1] = 'The settings file is read-only this session, so changes are refused.' end
    notify(table.concat(lines, '\n'))
  end

  ---- callbacks -------------------------------------------------------------------------------------------------------------
  local function onChange(modId, fieldId, value)
    local entry = P.rows[fieldId]
    if not entry then return end
    local ok, why
    if entry.button then
      ok, why = store.change(entry.button, bridge)
    elseif entry.apply then
      ok, why = entry.apply(value)
    else
      ok, why = store.change(function(st) return entry.set(st, value) end, bridge)
    end
    if ok == false and why then notify('Setting not changed: ' .. tostring(why)) end
    -- no setValue here: the timer redraws from the store
  end

  function P.onChange(modId, fieldId, value)
    local ok, err = pcall(onChange, modId, fieldId, value)
    if not ok then
      log('co-op page callback failed: ' .. tostring(err))
      pcall(notify, 'Setting not changed: internal error')
    end
  end

  ---- refresh (timer) -------------------------------------------------------------------------------------------------
  local ALWAYS_ACTIVE = {status = true, statusDetail = true, moduleOn = true, logPath = true, devMode = true,
                         diagModule = true, statusArmed = true, diagFile = true, diagResidue = true, hudStatus = true,
                         visionMinimap = true, visionOverheadHp = true, visionOverheadStatus = true, visionCombatLog = true}

  function P.refresh()
    local active = bridge.is_ready() and not store.readOnly
    for _, id in ipairs(P.order) do
      local e = P.rows[id]
      if e.kind == 'status' then
        local text = e.status(store.state)
        if P.cache[id] ~= text then
          P.cache[id] = text
          modmenu.setStatus(M.MOD_ID, id, text)
        end
      elseif e.get then
        local want = e.get(store.state)
        if modmenu.getValue(M.MOD_ID, id) ~= want then modmenu.setValue(M.MOD_ID, id, want) end
      end
      if not ALWAYS_ACTIVE[id] and not e.button then
        local key = id .. '#active'
        local want = active
        if e.kind == 'status' then want = bridge.is_ready() end
        if P.cache[key] ~= want then
          P.cache[key] = want
          modmenu.setActive(M.MOD_ID, id, want)
        end
      end
    end
    -- which rows and pages are shown
    local show = not S.blocks_settings(P.view.kind)
    local changes = {}
    if P.cache.show ~= show then
      P.cache.show = show
      for _, id in ipairs(M.SETTINGS_ROWS) do changes[id] = show end
    end
    local pending = F.has_pending()
    if P.cache.pending ~= pending then
      P.cache.pending = pending
      for _, id in ipairs(P.cancelIds) do changes[id] = pending end
    end
    local residue = P.view.residue == true
    if P.cache.residue ~= residue then
      P.cache.residue = residue
      changes.diagResidue = residue
    end
    local interactionsFailed = P.view.interactionsFailed == true
    if P.cache.interactionsFailed ~= interactionsFailed then
      P.cache.interactionsFailed = interactionsFailed
      changes.diagInteractions = interactionsFailed
    end
    local dev = store.state.diagnostics.developer == true
    if P.cache.dev ~= dev then
      P.cache.dev = dev
      for _, id in ipairs(P.devIds) do changes[id] = dev end
    end
    if next(changes) ~= nil then modmenu.setVisible(M.MOD_ID, changes) end
  end

  -- Native read coop.json at start-up and called it invalid (not JSON, wrong schema, wrong type), although this script
  -- read it fine. Rewrite it once from the state so the next start works. A newer-schema file (detail 3) is left alone.
  function P.forceSaveIfInvalid()
    local n = P.native
    if P.forcedSave or not n or not E.reason or n.reason ~= E.reason.SETTINGS_INVALID then return end
    local d = n.reasonDetail
    if d ~= 1 and d ~= 2 and d ~= 4 then return end
    P.forcedSave = true
    log('native found coop.json invalid at start-up (detail ' .. tostring(d) .. '); rewriting it once')
    store.force_save()
  end

  -- One timer step: poll native, hand its events to the flows, then redraw.
  function P.tick()
    P.now = (P.now or 0) + M.TICK_MS
    if bridge.status.state == 'starting' then bridge.start() end   -- a no-op while its own retry timer is armed
    if bridge.is_ready() then
      if not store.pushed then store.sync(bridge, P.now) end
      local r = bridge.poll()
      if r then
        P.lastStatus = r
        P.native = V.status(r.status)
        -- The kill switch may also be pressed in game (F8): the Co-op right now toggle follows the module's state
        -- whenever that state changes (a command sent from this page keeps its value until the module reports).
        local switch = P.native.coopSwitch
        if switch ~= nil and switch ~= P.lastSwitch then
          F.killed = switch ~= 0
          P.lastSwitch = switch
        end
        F.on_events(r.events)
      end
    else
      P.native = nil
    end
    P.forceSaveIfInvalid()
    P.visionTicks = (P.visionTicks or 0) + 1
    if P.visionTicks >= M.VISION_EVERY then
      P.visionTicks = 0
      P.readVision()
    end
    F.tick(P.now)
    P.view = S.classify(bridge.status, P.native, C)
    store.flush(P.now)
    P.refresh()
  end

  function P.start()
    P.view = S.classify(bridge.status, nil, C)
    P.readVision()
    local ok = modmenu.addTable(P.build())
    if not ok then
      log('Mod Menu page was not registered')
      return false
    end
    P.refresh()
    local function loop()
      pcall(P.tick)
      if deps.after then deps.after(M.TICK_MS, loop) end
    end
    if deps.after then deps.after(M.TICK_MS, loop) end
    return true
  end

  return P
end

return M
