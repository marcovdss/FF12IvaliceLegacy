-- Ivalice Legacy co-op: the Mod Config page (Mod Config > Ivalice Legacy > Co-op).
--
-- The tree (0.1.53), the player-facing part first:
--   Status . Co-op . Players > (Player 2..4 > controller, character, joined; keyboard and mouse) . Play (five rules) .
--   HUD > (status, preset, Elements >, Battle log >, player colours) . Controls > (on/off key, one page per player) .
--   Advanced > (keep running, start with the game, developer options > ...).
--
-- Rules the page keeps. Mod Menu callbacks run on the script thread; an older menu version is reported to have crashed
-- when a callback edited its own row, and we do not know which loader version fixed that, so as a defensive rule:
--   * a callback only validates, asks native, pushes and saves (through flows.lua and store.lua); it never calls
--     setValue/setActive/setStatus/setVisible, and neither do the modules it calls;
--   * a 100 ms timer polls the bridge, feeds native events to the flows, and redraws every row from the store and the
--     flows, touching a row only when its value changed;
--   * setActive goes only to rows the Mod Menu owns as value rows (toggles and carousels); it refuses it on status and
--     button rows. Status rows answer a press politely when the module is not running (flows.lua);
--   * a refused change keeps the stored value and explains why in a dialog; the timer then redraws the old value;
--   * the page registers even when the native module is missing, blocked or unsupported: then the settings pages are
--     hidden and the Status row explains why (statusview.lua).
local M = {}

M.MOD_ID = 'IvaliceLegacyCoop'
M.TICK_MS = 100      -- the page timer's step (ms): the bridge is polled every step
-- The Mod Menu API has no open/closed signal, so the redraw cannot follow the menu. It runs at once after a press or a
-- change, while a controller step runs, and whenever the module's state, the player count or the settings file's note
-- changed; otherwise only every REFRESH_EVERY steps (1 s), which also keeps the rows right a moment after the page opens.
M.REFRESH_EVERY = 10
-- rows and pages hidden while the module is missing, blocked, unsupported or has faulted (Advanced stays: it holds
-- the "start with the game" switch that can bring the module back)
M.RESIDUE_TEXT = 'Old Ivalice Together menu files found; they do nothing. Remove them in Vortex.'
M.INTERACTIONS_TEXT = 'Guests cannot use doors, chests, NPCs or loot this session (the hooks did not attach). ' ..
                      "Everything else works. The loader's hook.log has the details."
M.SETTINGS_ROWS = {'coop', 'players', 'play', 'hud', 'controls', 'keepRunning'}

-- The Battle HUD preset carousel. Off turns the HUD off (the elements keep their state); the others switch it on and set
-- every element bit. Custom is only ever shown, never applied. The 13 element bits are the store's HUD_ELEMENTS.
--   Light     the shrunk native party list with seat edge and number, low HP glow, guest cursors and the area ring while
--             aiming; nothing else is drawn, so the game's own target window and combat log stay
--   Standard  Light plus damage ghost, action line, target lines and seat diamonds in seat colours, Target Info, battle log
--   Full      Standard plus status timers (every element: the shipped default)
-- Target circles stay hidden (circlesOff) in all three: rings show only while an area magick or technick is aimed.
M.HUD_PRESET_LABELS = {'Off', 'Light', 'Standard', 'Full', 'Custom'}
M.HUD_PRESETS = {
  Light = {'partyShrink', 'seatEdge', 'lowHpGlow', 'guestCursors', 'aoeRing', 'circlesOff'},
  Standard = {'partyShrink', 'seatEdge', 'lowHpGlow', 'guestCursors', 'aoeRing', 'circlesOff', 'damageGhost', 'actionLine',
              'seatLines', 'seatDiamonds', 'targetInfo', 'battleLog'},
  Full = {'partyShrink', 'seatEdge', 'lowHpGlow', 'guestCursors', 'aoeRing', 'circlesOff', 'damageGhost', 'actionLine',
          'seatLines', 'seatDiamonds', 'targetInfo', 'battleLog', 'partyTimers'},
}
M.HUD_PRESET_HELP = "Off: the game's own battle HUD (the co-op battle menus stay: guests need them to act). " ..
                    'Light: the smaller party list with seat colours, nothing drawn over the world. ' ..
                    'Standard: Light plus damage ghost, action line, target lines, seat diamonds, Target Info and the battle log. ' ..
                    'Full: Standard plus status timers. Custom: you changed an element under Elements. ' ..
                    'The game\'s target circles stay hidden in every preset; a ring shows while you aim an area spell.'

-- The HUD theme (Phase 8): the look of the co-op HUD and windows. The three built-ins, then the player themes native
-- found valid under scripts/config/IvaliceLegacy/themes/<id>/ (GET_THEMES). The list changes while the game runs, and
-- a Mod Menu carousel's options are fixed when the page is built, so the row is a status button: it shows the chosen
-- theme's name and a press moves to the next theme (wrapping). The store keeps the id ('' = the default, Bold Modern).
M.BUILTIN_THEMES = {{id = 'legacy-bold', name = 'Bold Modern'}, {id = 'legacy-humanist', name = 'Refined Vanilla'},
                    {id = 'legacy-crisp', name = 'Crisp Vanilla'}}
M.THEMES_EVERY = 10   -- timer ticks between theme list reads (1 s; native rescans the folder while it is asked)
M.THEME_HELP = 'The look of the co-op HUD and command windows. Press to switch to the next theme: Bold Modern (the ' ..
               'default), Refined Vanilla, Crisp Vanilla, then any valid player themes in ' ..
               'scripts/config/IvaliceLegacy/themes. A theme the game cannot read is not offered; one that breaks ' ..
               'later draws Bold Modern and is named in hook.log. Applies at once.'

-- The elements a player may switch one by one (the others are set by the preset only).
M.HUD_ELEMENT_ROWS = {
  {key = 'partyTimers', label = 'Party timers',
   help = 'The time left on each status, next to its icon on the party list. Part of the Full preset.'},
  {key = 'guestCursors', label = 'Guest cursors',
   help = "Each guest's hand on the party list while they pick an ally to target."},
  {key = 'seatLines', label = 'Target lines',
   help = "Each player's target line takes the seat colour; gambit allies stay silver, foes keep the game's colours."},
  {key = 'seatDiamonds', label = 'Seat diamonds',
   help = "A numbered diamond over each player's character at the start of a fight, on a swap or drop-in, and on Identify. " ..
          'Identify (see Controls) does nothing while this is Off.'},
  {key = 'battleLog', label = 'Battle log',
   help = "The battle log in the top-right corner, in place of the game's combat log. Off: the game's combat log."},
  {key = 'targetInfo', label = 'Target info',
   help = "The expanded Target Info in the top-left corner. Off: the game's own target window."},
}
M.ELEMENTS_HELP = 'Applies while the Battle HUD preset is not Off.'

-- Insurgent's Vision owns the minimap and overhead toggles; its settings file is only read, to name the reason when
-- its "No Combat Log" removes the call the co-op HUD is drawn from.
M.VISION_PATH = 'scripts/config/TheInsurgentsVisionConfig.json'
M.VISION_EVERY = 50   -- timer ticks between reads of Vision's file (5 s)
M.LOG_FILTERS = {'ALL', 'PARTY', 'FOES', 'STATUSES', 'MINE'}
M.LOG_FILTER_LABELS = {'All', 'Party', 'Foes', 'Statuses', 'One player'}
M.TEST_DRAW_HELP = "Draws the co-op overlay's test card: a labelled card and a line of text in each corner, tick " ..
                   "marks along the screen edges and the frame time, to check the overlay is visible. It hides " ..
                   "with the game's HUD. Leave Off for play."

-- The Safety page. Two other mods' options break co-op, and "Restore Legacy settings" puts back what Legacy relies on.
--   * Insurgent's Vision "No Combat Log" removes the call the co-op HUD and battle menus are drawn from;
--   * Insurgent's Manifesto "No Auto Pause" patches the part of the game co-op reads controllers from.
-- Everything here is text in a row and never a window: no modal pause, in a fight or out of one. The restore asks for a
-- second press within SAFETY_CONFIRM_MS instead of opening a question.
M.MANIFESTO_PATH = 'scripts/config/TheInsurgentsManifestoConfig.lua'
M.SAFETY_CONFIRM_MS = 6000
M.VISION_WARNING = "Insurgent's Vision has No Combat Log turned on. It removes the call the co-op HUD and the co-op " ..
                   "battle menus are drawn from, so co-op cannot work. Turn it off on Vision's page, or use Restore " ..
                   'Legacy settings and restart the game.'
M.MANIFESTO_WARNING = "Insurgent's Manifesto has No Auto Pause turned on. It changes the same part of the game that co-op " ..
                      'reads controllers from, so co-op input cannot work. Turn it off in its settings file, or use ' ..
                      'Restore Legacy settings and restart the game.'
-- The settings Legacy relies on, per file. kind: 'lua' (key = true/false or a number) or 'json' (key.state).
-- Only these keys are written, never anything else in the file; a file that does not look as expected is left alone.
M.REQUIRED_FILES = {
  {name = 'Vision', path = M.VISION_PATH, kind = 'json', keys = {{'noCombatLog', false}}},
  {name = 'Manifesto', path = M.MANIFESTO_PATH, kind = 'lua', keys = {{'noAutoPause', false}}},
  {name = 'Companions', path = 'scripts/config/TheInsurgentsCompanionsConfig.lua', kind = 'lua',
   keys = {{'partyInTowns', true}, {'partyInTowns', 1}, {'esperWithFullParty', true}, {'collectGuestEquipment', true},
           {'partyEquipmentInTowns', false}, {'partyEquipmentInTowns', 0}}},
  {name = "Hunter's Insight", path = 'scripts/config/HuntersInsightConfig.lua', kind = 'lua',
   keys = {{'alwaysShowItems', false}}},
}
M.RESTORE_HELP = 'Puts back what Legacy relies on: no developer override for co-op, and the Legacy values in the ' ..
                 "settings files of Vision, Manifesto, Companions and Hunter's Insight. Whether co-op is on, your " ..
                 'seats, controls and HUD choices are not changed. Press twice within a few seconds to confirm. ' ..
                 "The other mods' files apply when you restart the game."
M.WARN_HELP = "A setting from another mod breaks co-op. Open the Safety page to see which one, and to restore Legacy's settings."

-- Lua settings files: `key = true|false|<integer>` outside comments. The comment rules are the simple ones these small
-- files use: a line starting with "--", and a block from a line with "--[[" to the next line with "]]" (a line that opens
-- a block is skipped whole).
local function luaLines(text, each)
  local inBlock = false
  return (text:gsub('([^\n]*)(\n?)', function(line, nl)
    if line == '' and nl == '' then return nil end
    local opens = line:find('--[[', 1, true)
    if inBlock then
      if line:find(']]', 1, true) then inBlock = false end
      return nil
    end
    if opens then
      if not line:find(']]', opens, true) then inBlock = true end
      return nil
    end
    if line:match('^%s*%-%-') then return nil end
    local out = each(line)
    if out == nil then return nil end
    return out .. nl
  end))
end

local function luaValueText(v) return type(v) == 'boolean' and tostring(v) or tostring(math.tointeger(v) or v) end

-- The value of a key in a Lua settings file: true, false, an integer, or nil when it is not there.
function M.lua_get(text, key)
  local found
  luaLines(text, function(line)
    if found ~= nil then return nil end
    local v = line:match('%f[%w_]' .. key .. '%s*=%s*([%w_]+)')
    if v == 'true' then found = true elseif v == 'false' then found = false elseif v and v:match('^%d+$') then found = tonumber(v) end
    return nil
  end)
  return found
end

-- Sets `key = value` where the old value has the same kind (boolean for boolean, integer for number). Returns the new
-- text, the number of places that matched and the number it changed.
function M.lua_set(text, key, value)
  local matched, changed = 0, 0
  local want = luaValueText(value)
  local out = luaLines(text, function(line)
    local new = line:gsub('(%f[%w_]' .. key .. '%s*=%s*)([%w_]+)', function(head, old)
      local same = (type(value) == 'boolean' and (old == 'true' or old == 'false')) or
                   (type(value) == 'number' and old:match('^%d+$') ~= nil)
      if not same then return nil end
      matched = matched + 1
      if old == want then return nil end
      changed = changed + 1
      return head .. want
    end)
    return new ~= line and new or nil
  end)
  return out, matched, changed
end

-- Insurgent's JSON files keep `"key": {"state": true}`.
function M.json_get(text, key)
  local v = text:match('"' .. key .. '"%s*:%s*{[^}]-"state"%s*:%s*(%a+)')
  if v == 'true' then return true elseif v == 'false' then return false end
  return nil
end

function M.json_set(text, key, value)
  local matched, changed = 0, 0
  local want = tostring(value)
  local out = text:gsub('("' .. key .. '"%s*:%s*{[^}]-"state"%s*:%s*)(%a+)', function(head, old)
    if old ~= 'true' and old ~= 'false' then return nil end
    matched = matched + 1
    if old == want then return nil end
    changed = changed + 1
    return head .. want
  end)
  return out, matched, changed
end

M.COMPACT_LINES = {1, 8}     -- the schema's ranges (checked by the tests): battle log lines in the compact view
M.EXPANDED_LINES = {5, 20}
M.FADE_SECONDS = {3, 15}

-- Help for the binding rows, by action (the rest read "Press, then press the new button or key.").
M.ACTION_HELP = {
  TOGGLE_CONTROL = "The button or key that drops this player in and out during play. It parks or returns the seat for now; " ..
                   'the Joined setting under Players is not changed. Press to change it.',
  IDENTIFY = "Flashes this player's seat diamond over their character. Does nothing while Seat diamonds is Off. " ..
             'Player 1 has none at first: every button of your controller also reaches the game.',
}

local function title(s)
  local t = s:lower():gsub('_', ' ')
  return (t:gsub('^%l', string.upper))
end

local function indexOf(list, v)
  for i, x in ipairs(list) do if x == v then return i - 1 end end
  return 0
end

local function clampIndex(v, lo, hi) return math.max(lo, math.min(hi, v)) - lo end

local function numbers(lo, hi, suffix)
  local out = {}
  for n = lo, hi do out[#out + 1] = tostring(n) .. (suffix or '') end
  return out
end

local function member(list, v)
  for _, x in ipairs(list) do if x == v then return true end end
  return false
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
      P.dirty = true
      local ok, err = pcall(fn)
      if not ok then
        log('co-op page action failed: ' .. tostring(err))
        pcall(notify, 'That did not work (internal error).')
      end
    end
  end

  local function padName(name)
    return F.pad_name and F.pad_name(name) or title(name)
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

  local function submenu(list, id, label, sub, visible)
    local row = {id = id, label = label, type = 'submenu', sub = sub}
    if visible == false then row.visible = false end
    list[#list + 1] = row
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

  ---- the Battle HUD preset ------------------------------------------------------------------------------------------------
  -- The preset the stored state stands for: 0 Off, 1..3 Light/Standard/Full, 4 Custom (any other set of elements).
  function P.presetIndex(st)
    local h = (st or store.state).battleHud
    if not h.enabled then return 0 end
    for i = 2, 4 do
      local name = M.HUD_PRESET_LABELS[i]
      local set = M.HUD_PRESETS[name]
      if set then
        local same = true
        for _, k in ipairs(store.HUD_ELEMENTS) do
          if (h.elements[k] == true) ~= member(set, k) then same = false break end
        end
        if same then return i - 1 end
      end
    end
    return 4
  end

  function P.applyPreset(v)
    if v == 4 or v == nil then return true end                  -- Custom is shown, never chosen
    return store.change(function(st)
      if v == 0 then st.battleHud.enabled = false return end
      local set = M.HUD_PRESETS[M.HUD_PRESET_LABELS[v + 1]]
      if not set then return false, 'unknown preset' end
      st.battleHud.enabled = true
      for _, k in ipairs(store.HUD_ELEMENTS) do st.battleHud.elements[k] = member(set, k) end
    end, bridge)
  end

  ---- the HUD theme ---------------------------------------------------------------------------------------------------------
  -- The theme list: native's (built-ins first, then valid player themes), or the built-ins alone without the module.
  function P.themeList()
    if bridge.is_ready() and bridge.get_themes and (P.themesAt == nil or (P.now or 0) - P.themesAt >= M.THEMES_EVERY * M.TICK_MS) then
      P.themesAt = P.now or 0
      local ok, r = pcall(bridge.get_themes)
      if ok and type(r) == 'table' and type(r.entries) == 'table' and #r.entries > 0 then P.themes = r end
    end
    if P.themes and #P.themes.entries > 0 then return P.themes.entries end
    return M.BUILTIN_THEMES
  end

  local function themeId(st)
    local id = ((st or store.state).battleHud or {}).theme
    if type(id) ~= 'string' or id == '' then return M.BUILTIN_THEMES[1].id end
    return id
  end

  -- The theme the HUD draws with. Native's list flags the theme it draws (ACTIVE), the one the applied settings ask
  -- for (SELECTED) and the built-in default (DEFAULT): when native has applied our choice and draws the default
  -- instead, it fell back (the chosen theme broke after the scan listed it, or its textures failed; hook.log says why).
  -- A theme broken before the scan is never listed: "<id> (not found; using default)". Without the module, before
  -- native has applied the choice, or while it draws a theme that is not the default (a switch in flight), the chosen
  -- theme's name.
  function P.themeText()
    local id = themeId()
    local flag = E.theme_flag or {}
    local IS_DEFAULT, IS_ACTIVE, IS_SELECTED = flag.DEFAULT or 2, flag.ACTIVE or 4, flag.SELECTED or 8
    local chosen, active, selected
    for _, e in ipairs(P.themeList()) do
      if e.id == id then chosen = e end
      if math.type(e.flags) == 'integer' then
        if (e.flags & IS_ACTIVE) ~= 0 then active = e end
        if (e.flags & IS_SELECTED) ~= 0 then selected = e end
      end
    end
    local function nameOf(e) return e.name ~= '' and e.name or e.id end
    if chosen and active and active ~= chosen and selected == chosen and (active.flags & IS_DEFAULT) ~= 0 then
      return nameOf(chosen) .. ' (using default)'
    end
    if chosen then return nameOf(chosen) end
    return id .. ' (not found; using default)'
  end

  -- The next theme after the chosen one ('' for the default keeps the settings file clean).
  function P.nextTheme()
    local list, id = P.themeList(), themeId()
    local at = 0
    for i, e in ipairs(list) do if e.id == id then at = i end end
    local nxt = list[(at % #list) + 1]
    local value = nxt.id == M.BUILTIN_THEMES[1].id and '' or nxt.id
    local ok, why = store.change(function(st) st.battleHud.theme = value end, bridge)
    if ok == false and why then notify('Theme not changed: ' .. tostring(why)) end
  end

  ---- schema of the page ------------------------------------------------------------------------------------------------
  function P.build()
    P.rows, P.order, P.cancelIds = {}, {}, {}
    local main, players, play, hud, elements, logPage, controls, advanced, dev, subs = {}, {}, {}, {}, {}, {}, {}, {}, {}, {}
    local safety = {}

    -- Status and the live co-op switch
    statusRow(main, 'status', 'Status', function() return P.statusText() end, function() P.explain() end,
              'State of co-op. Press for what it means, who plays, and what to do.')
    toggle(main, 'coop', 'Co-op', {'Off', 'On'},
           function() return F.killed and 0 or 1 end, nil,
           'On: everything of co-op works. Off: switches co-op off for everyone at once, with no restart: guests become ' ..
           'ordinary allies and the shared camera is released. The on/off key (F8 by default; see Controls) does the same during play.',
           function(v) return F.set_killed(v == 0) end)

    -- Players: guests P2..P4 (Player 1 is the host), then the keyboard
    cancelRow(players, 'opCancelPlayers')
    for i = 1, ACTIVE do
      local rows = {}
      cancelRow(rows, 'opCancelPlayer' .. i)
      statusRow(rows, 'seatPad' .. i, seatLabel(i) .. ' controller', function() return F.device_text(i) end,
                function() F.open_pairing(i) end,
                i == 1 and 'Press to see which controller is yours, or to switch to another (confirmed on the new one).'
                       or 'Press to pair a controller: choose it by name, then press A on it.')
      carousel(rows, 'seatCharacter' .. i, seatLabel(i) .. ' character', {'Auto', 'Last used'},
               function(st) return st.seats[i].character == 'AUTO' and 0 or 1 end, nil,
               'Auto: this player takes the first free regular character. Last used: this player keeps the character ' ..
               'they control now and is given it back when free (it may apply only from the next start). Choosing ' ..
               'Last used needs the player to be in the game.' ..
               (i == 1 and ' Player 1 is the host and keeps the current leader on Auto.' or ''),
               function(v) if v == 0 then return F.set_character(i, 'AUTO') end return F.set_last_used(i) end)
      if i > 1 then
        toggle(rows, 'seatPark' .. i, seatLabel(i) .. ' takes part', {'Parked', 'Joined'},
               function(st) return st.seats[i].parked and 0 or 1 end, nil,
               'Joined: this player takes part with their controller. Parked: the seat becomes an ordinary ally and its ' ..
               "controller is kept. The player's join/leave key (see Controls) does the same during play.",
               function(v) return F.set_parked(i, v == 0) end)
      end
      if i > 1 then
        statusRow(rows, 'seatIssue' .. i, seatLabel(i) .. ' check', function() return F.seat_issue(i) or 'OK' end,
                  function() notify(seatLabel(i) .. ': ' .. (F.seat_issue(i) or 'nothing blocks this player.')) end,
                  'Why a joined player may not be able to act: no controller, or no battle-menu key.')
      end
      submenu(players, 'player' .. i, seatLabel(i), rows)
    end
    local owners = {}
    for i = 1, ACTIVE do owners[#owners + 1] = seatLabel(i) end
    local keyboardOwners = {'None'}
    for _, name in ipairs(owners) do keyboardOwners[#keyboardOwners + 1] = name end
    carousel(players, 'keyboardSeat', 'Keyboard and mouse', keyboardOwners,
             function(st)
               local k = st.keyboardSeat
               if math.type(k) ~= 'integer' or k < 1 or k > ACTIVE then return 0 end
               return k
             end,
             function(st, v)
               local clash = F.keyboard_seat_conflict(st, v)
               if clash then return false, clash end
               st.keyboardSeat = v
             end,
             'Which player uses the keyboard and mouse (one at a time). None: no player plays with them; the on/off ' ..
             'and join/leave keys still work. Refused while one of that player\'s keys is also the on/off key or ' ..
             "another player's join/leave key.")

    -- Play: five rules of the party
    toggle(play, 'sharedCamera', 'Shared camera', {'Off', 'On'},
           function(st) return st.play.sharedCamera and 1 or 0 end,
           function(st, v) st.play.sharedCamera = v == 1 end,
           "On: one camera frames every player. Off: the game's own camera follows Player 1 only.")
    toggle(play, 'guestInteractions', 'Guests use doors, chests, NPCs and shops', {'Off', 'On'},
           function(st) return st.play.guestInteractions and 1 or 0 end,
           function(st, v) st.play.guestInteractions = v == 1 end,
           'On: guests can open doors, chests and crystals, talk to NPCs and shop. Off: only Player 1 can; gateways and ' ..
           'loot a guest walks over still work. A guest already in a shop finishes there.')
    carousel(play, 'townView', 'Towns show', {'Players only', 'Whole party'},
             function(st) return st.play.townView == 'PARTY' and 1 or 0 end,
             function(st, v) st.play.townView = v == 1 and 'PARTY' or 'PLAYERS' end,
             "Players only: in towns only the characters players control are shown. Whole party: nothing is hidden; the " ..
             'game (or a Companions mod) decides who appears. A town you are standing in may only change when you next enter it.')
    carousel(play, 'waitPause', 'Wait mode pauses', {'Only when all are in a menu', 'When any player is in a menu'},
             function(st) return st.play.waitPause == 'ANY' and 1 or 0 end,
             function(st, v) st.play.waitPause = v == 1 and 'ANY' or 'ALL' end,
             "Only for the game's Wait battle mode. All: time stops only while every playing player is in a menu. Any: " ..
             'time stops as soon as one player is.')
    toggle(play, 'guestPause', 'Guests may pause', {'No', 'Yes'},
           function(st) return st.play.guestPause and 1 or 0 end,
           function(st, v) st.play.guestPause = v == 1 end,
           "Yes: a guest can press Pause and open the Party menu. No: a guest's Pause and Party do nothing, even inside " ..
           'a screen the guest is driving (a shop, a dialog). Player 1 can always pause.')

    -- HUD
    statusRow(hud, 'hudStatus', 'HUD status', function() return P.hudStatusText() end, function() P.explainHud() end,
              'Whether the co-op HUD is drawing, and if not, why. Press for what to do.')
    carousel(hud, 'hudPreset', 'Battle HUD', M.HUD_PRESET_LABELS,
             function(st) return P.presetIndex(st) end, nil, M.HUD_PRESET_HELP, P.applyPreset)
    statusRow(hud, 'hudTheme', 'HUD theme', function() return P.themeText() end, function() P.nextTheme() end,
              M.THEME_HELP)
    for _, r in ipairs(M.HUD_ELEMENT_ROWS) do
      toggle(elements, 'hud_' .. r.key, r.label, {'Off', 'On'},
             function(st) return st.battleHud.elements[r.key] and 1 or 0 end,
             function(st, v) st.battleHud.elements[r.key] = v == 1 end, r.help .. ' ' .. M.ELEMENTS_HELP)
    end
    submenu(hud, 'hudElements', 'Elements', elements)

    local cLo, cHi = M.COMPACT_LINES[1], M.COMPACT_LINES[2]
    local eLo, eHi = M.EXPANDED_LINES[1], M.EXPANDED_LINES[2]
    local fLo, fHi = M.FADE_SECONDS[1], M.FADE_SECONDS[2]
    carousel(logPage, 'hudLogCompact', 'Compact lines', numbers(cLo, cHi),
             function(st) return clampIndex(st.battleHud.log.compactLines, cLo, cHi) end,
             function(st, v)
               if cLo + v > st.battleHud.log.expandedLines then
                 return false, 'The compact log cannot have more lines than the expanded log.'
               end
               st.battleHud.log.compactLines = cLo + v
             end,
             'How many lines the battle log shows in its compact (everyday) view. At most as many as the expanded view.')
    carousel(logPage, 'hudLogExpanded', 'Expanded lines', numbers(eLo, eHi),
             function(st) return clampIndex(st.battleHud.log.expandedLines, eLo, eHi) end,
             function(st, v)
               if eLo + v < st.battleHud.log.compactLines then
                 return false, 'The expanded log cannot have fewer lines than the compact log.'
               end
               st.battleHud.log.expandedLines = eLo + v
             end,
             'How many lines the battle log shows in its expanded view (the expand button switches to it).')
    carousel(logPage, 'hudLogFade', 'Fade time', numbers(fLo, fHi, ' s'),
             function(st) return clampIndex(st.battleHud.log.fadeSeconds, fLo, fHi) end,
             function(st, v) st.battleHud.log.fadeSeconds = fLo + v end,
             'Seconds an ordinary log line stays before it fades out. Important lines stay twice as long.')
    carousel(logPage, 'hudLogExpand', 'Expand button', P.expandLabels(),
             function(st) return P.expandIndex(st.battleHud.log.expandButton) end,
             function(st, v) st.battleHud.log.expandButton = P.expandOptions()[v + 1] or 'NONE' end,
             "A controller button that switches the log to the expanded view. Any player's controller can press it; " ..
             "Player 1's press also reaches the game, so pick a button you do not use.")
    carousel(logPage, 'hudLogFilter', 'Log shows', M.LOG_FILTER_LABELS,
             function(st) return indexOf(M.LOG_FILTERS, st.battleHud.log.filter) end,
             function(st, v) st.battleHud.log.filter = M.LOG_FILTERS[v + 1] or 'ALL' end,
             'Which lines the log shows. One player: only the player chosen below.')
    carousel(logPage, 'hudLogSeat', 'Log player', owners,
             function(st) return clampIndex(st.battleHud.log.filterSeat or 1, 1, ACTIVE) end,
             function(st, v) st.battleHud.log.filterSeat = v + 1 end,
             'The player whose lines "One player" shows. Shown only while the log shows One player.').visible =
      store.state.battleHud.log.filter == 'MINE'
    submenu(hud, 'hudLog', 'Battle log', logPage)

    carousel(hud, 'colourMode', 'Player colours', {'By character', 'By player'},
             function(st) return st.colours.mode == 'PLAYER' and 1 or 0 end,
             function(st, v) st.colours.mode = v == 1 and 'PLAYER' or 'CHARACTER' end,
             'By character: a character keeps its colour whoever plays it. By player: a player keeps the colour of their ' ..
             'seat (sapphire, emerald, bronze and amethyst by default) whichever character they play. A guest who is not a ' ..
             'regular character uses the seat colour in both.')

    -- Controls: the on/off key, then one page per player
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
                    function() F.open_capture(i, a.name) end, M.ACTION_HELP[a.name] or 'Press, then press the new button or key.')
        end
      end
      add(rows, {id = 'bindReset' .. i, label = "Reset this player's controls", type = 'button', callback = P.onChange,
                 help = "Puts this player's controls back to the defaults."}, nil, nil)
      P.rows['bindReset' .. i].button = function(st)
        local d = store.defaults()
        local clash = F.reset_conflict(st, i, d.seats[i].bindings)
        if clash then return false, clash end
        st.seats[i].bindings = d.seats[i].bindings
      end
      submenu(controls, 'controls' .. i, seatLabel(i) .. ' controls', rows)
    end

    -- Advanced: keep running, start with the game, developer options
    toggle(advanced, 'keepRunning', 'Keep running in background', {'Off', 'On'},
           function(st) return st.keepRunning and 1 or 0 end,
           function(st, v) st.keepRunning = v == 1 end,
           'Windowed and borderless only: the game keeps running when you switch to another window. While it is ' ..
           'unfocused, your controller and mouse are ignored until you come back. Works with the Co-op switch Off too, ' ..
           'but not when co-op is Off at start. Exclusive fullscreen pauses as usual.')
    toggle(advanced, 'moduleOn', 'Start co-op with the game', {'Off', 'On'},
           function(st) return st.module.enabledAtStart and 1 or 0 end,
           function(st, v) st.module.enabledAtStart = v == 1 end,
           'Whether the co-op module loads when the game starts. Applies from the next launch: restart the game after a ' ..
           'change. Off: nothing of co-op is installed and the game plays as stock.')
    statusRow(advanced, 'diagResidue', 'Old Together menu files', function() return P.view.residue and 'Found, harmless' or 'None' end,
              function() notify(M.RESIDUE_TEXT) end, 'Press for what to do.').visible = false
    statusRow(advanced, 'diagInteractions', 'Guest interactions', function()
                return P.view.interactionsFailed and 'Unavailable' or 'Ready' end,
              function() notify(M.INTERACTIONS_TEXT) end, 'Shown only when they could not start.').visible = false
    toggle(advanced, 'devMode', 'Developer options', {'Off', 'On'},
           function(st) return st.diagnostics.developer and 1 or 0 end,
           function(st, v) st.diagnostics.developer = v == 1 end,
           'On: shows the developer page and makes its subsystem switches count (they are ignored while this is Off). ' ..
           'Also allows the test stall. For testing only; Off is how co-op is meant to run.')

    -- Developer options (hidden until the toggle above is On)
    for _, k in ipairs(store.SUBSYSTEMS) do
      toggle(subs, 'arm_' .. k, title((k:gsub('(%u)', ' %1'))), {'Disarmed', 'Armed'},
             function(st) return st.diagnostics.disarm[k] and 0 or 1 end,
             function(st, v) st.diagnostics.disarm[k] = v ~= 1 end,
             'Developer override: Disarmed keeps this part of co-op off for every session, and only while Developer ' ..
             'options is On. It is saved with the settings and sent to the module with them.')
    end
    statusRow(dev, 'diagModule', 'Module', function() return P.moduleText() end, nil, "The module's own state and boot reason.")
    statusRow(dev, 'statusArmed', 'Parts running', function() return P.armedText() end, nil,
              'How many of the eight subsystems the module reports as running.')
    statusRow(dev, 'diagFile', 'Settings file', function() return P.fileText() end, nil,
              'Whether coop.json was read, repaired, upgraded or is read-only.')
    statusRow(dev, 'logPath', 'Log file', function() return deps.logPath or 'x64/hook.log' end, nil,
              "The loader's hook.log. The co-op module writes its lines there, each starting with Ivalice Legacy Co-op.")
    toggle(dev, 'logLevel', 'Log detail', {'Normal', 'Verbose'},
           function(st) return st.logLevel == 'VERBOSE' and 1 or 0 end,
           function(st, v) st.logLevel = v == 1 and 'VERBOSE' or 'NORMAL' end,
           "How much the co-op module writes to hook.log: Normal (start-up, refusals, faults) or Verbose (every applied settings record too).")
    toggle(dev, 'hudTestDraw', 'Overlay test card', {'Off', 'On'},
           function(st) return st.diagnostics.hudTestDraw and 1 or 0 end,
           function(st, v) st.diagnostics.hudTestDraw = v == 1 end, M.TEST_DRAW_HELP)
    submenu(dev, 'subsystems', 'Subsystem switches', subs)
    dev[#dev + 1] = {id = 'diagCounters', label = 'Write counters to the log', type = 'button',
                     help = "Writes every counter of the co-op module to hook.log, one line each.",
                     callback = guard(function()
                       local ok, count = F.dump_counters()
                       if ok then notify(string.format('%d counters were written to hook.log.', count)) end
                     end)}
    dev[#dev + 1] = {id = 'diagStall', label = 'Stall the co-op thread (test)', type = 'button',
                     help = 'Stops the co-op module for 3 seconds on purpose. The game should play as stock meanwhile.',
                     callback = guard(function() F.ask_stall() end)}
    submenu(advanced, 'devOptions', 'Developer', dev, false)
    P.devIds = {'devOptions'}

    -- Safety: the two options of other mods that break co-op, and the restore (never hidden: it can fix a blocked module)
    -- (no press action on the status rows: the full sentence is in the help bar, and no window ever opens here)
    statusRow(safety, 'safetyVision', 'Vision: No Combat Log', function() return P.visionText() end, nil, M.VISION_WARNING)
    statusRow(safety, 'safetyManifesto', 'Manifesto: No Auto Pause', function() return P.manifestoText() end, nil,
              M.MANIFESTO_WARNING)
    add(safety, {id = 'safetyRestore', label = 'Restore Legacy settings', type = 'button', callback = P.onChange,
                 help = M.RESTORE_HELP}, nil, nil, function() return P.safetyPress() end)
    statusRow(safety, 'safetyResult', 'Restore result', function() return P.safetyText() end, nil,
              'What the last restore did. The settings of other mods apply when you restart the game.')
    -- on the first page, only while a problem exists
    statusRow(main, 'safetyWarn', 'Settings warning', function() return P.warnText() end, nil, M.WARN_HELP).visible = false

    cancelRow(main, 'opCancel')
    submenu(main, 'players', 'Players', players)
    submenu(main, 'play', 'Play', play)
    submenu(main, 'hud', 'HUD', hud)
    submenu(main, 'controls', 'Controls', controls)
    submenu(main, 'safety', 'Safety', safety)
    submenu(main, 'advanced', 'Advanced', advanced)
    return {id = M.MOD_ID, name = 'Co-op', group = {id = 'IvaliceLegacy', name = 'Ivalice Legacy',
            description = 'Ivalice Legacy settings.'}, schema = main}
  end

  ---- text ------------------------------------------------------------------------------------------------------------------
  -- "Ready, 2 players" once the module runs and the seats were read; the module's refusal beats everything.
  function P.statusText()
    if P.view.kind == 'ok' and store.lastRejection then return 'Settings refused by the module' end
    local text = P.view.text
    if P.view.kind == 'ok' and text:find('^Ready') then
      local n = F.player_count and F.player_count()
      if n then text = string.format('%s, %d player%s', text, n, n == 1 and '' or 's') end
    end
    return text
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
    for i, n in ipairs(P.expandOptions()) do out[i] = i == 1 and 'None' or padName(n) end
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

  -- The Battle HUD's status line: native's report (status hud_report) first, Vision's file second.
  function P.hudReport()
    local n = P.native
    return (bridge.status.state == 'ready' and n and n.hudReport) or 0
  end

  local function reportHas(name) return (P.hudReport() & ((E.hud_report or {})[name] or 0)) ~= 0 end

  ---- Safety ------------------------------------------------------------------------------------------------------------
  P.safety = {}      -- armedAt: tick time of the first press of the restore; note: what the last restore did

  local function readText(path)
    local text = store.read_text and store.read_text(path)
    return type(text) == 'string' and text or nil
  end

  -- Manifesto reads its file once, when the game starts, so the first reading this session is what is running.
  function P.readManifesto()
    local text = readText(M.MANIFESTO_PATH)
    P.manifestoFile = nil
    if text then P.manifestoFile = M.lua_get(text, 'noAutoPause') end
    if P.manifestoBoot == nil then P.manifestoBoot = P.manifestoFile end
  end

  -- Vision's state is live in the module's report; its file is the fallback. Manifesto: what it started with.
  function P.visionBad() return reportHas('VISION_NO_COMBAT_LOG') or P.visionState('noCombatLog') == true end
  function P.manifestoBad() return P.manifestoBoot == true end

  function P.visionText()
    if P.visionBad() then return 'On: breaks co-op' end
    return P.vision == nil and 'Not found' or 'Off'
  end

  function P.manifestoText()
    if P.manifestoBoot == true then return P.manifestoFile == false and 'On until restart' or 'On: breaks co-op' end
    return P.manifestoBoot == nil and 'Not found' or 'Off'
  end

  function P.safetyProblems()
    local out = {}
    if P.visionBad() then out[#out + 1] = {id = 'vision', text = M.VISION_WARNING} end
    if P.manifestoBad() then out[#out + 1] = {id = 'manifesto', text = M.MANIFESTO_WARNING} end
    return out
  end

  function P.warnText()
    local list = P.safetyProblems()
    if #list == 0 then return 'None' end
    if #list > 1 then return 'Two settings break co-op' end
    return list[1].id == 'vision' and 'Vision No Combat Log is on' or 'Manifesto No Auto Pause is on'
  end

  function P.safetyText()
    local s = P.safety
    if s.armedAt and (P.now or 0) - s.armedAt <= M.SAFETY_CONFIRM_MS then return 'Press again to restore' end
    return s.note or 'Not used yet'
  end

  -- Restores one file's required keys. Returns 'absent' (not installed), 'same', 'changed', or 'failed' and why. The file
  -- is only written when every key was found, the result still loads (Lua files) and it reads back as written.
  function P.restoreFile(spec)
    local text = readText(spec.path)
    if text == nil then return 'absent' end
    local setter = spec.kind == 'json' and M.json_set or M.lua_set
    local out, total = text, 0
    for _, kv in ipairs(spec.keys) do
      local matched, changed
      out, matched, changed = setter(out, kv[1], kv[2])
      if matched == 0 then return 'failed', kv[1] .. ' was not found' end
      total = total + changed
    end
    if total == 0 then return 'same' end
    if spec.kind == 'lua' and not load(out, '=settings', 't', {}) then return 'failed', 'the result is not valid Lua' end
    if not (store.write_text and store.write_text(spec.path, out)) then return 'failed', 'the file could not be written' end
    if readText(spec.path) ~= out then
      store.write_text(spec.path, text)
      return 'failed', 'the file did not read back as written'
    end
    return 'changed'
  end

  -- What the restore does: no developer override for co-op (whether co-op is on, the seats and every player choice stay
  -- as they are), then the required keys of the four settings files. Never a window; the outcome is the result row's text and the lines in hook.log.
  function P.restoreSettings()
    local changed, failed = {}, {}
    local ok, why = store.change(function(st)
      st.diagnostics.developer = false
      st.diagnostics.hudTestDraw = false
      for k in pairs(st.diagnostics.disarm) do st.diagnostics.disarm[k] = false end
    end, bridge)
    if ok == false then failed[#failed + 1] = 'co-op settings (' .. tostring(why) .. ')' end
    for _, spec in ipairs(M.REQUIRED_FILES) do
      local r, detail = P.restoreFile(spec)
      if r == 'changed' then changed[#changed + 1] = spec.name
      elseif r == 'failed' then failed[#failed + 1] = spec.name .. ' (' .. detail .. ')' end
    end
    log('Restore Legacy settings: changed ' .. (#changed > 0 and table.concat(changed, ', ') or 'no file') ..
        (#failed > 0 and ('; failed: ' .. table.concat(failed, '; ')) or ''))
    P.readVision()
    P.readManifesto()
    if #failed > 0 then return 'Not fully restored; hook.log has the details' end
    if #changed > 0 then return 'Restored. Restart the game to finish' end
    return 'Restored. No file needed changing'
  end

  -- The restore button: the first press arms it, a second press within SAFETY_CONFIRM_MS does it.
  function P.safetyPress()
    local s, now = P.safety, P.now or 0
    if not (s.armedAt and now - s.armedAt <= M.SAFETY_CONFIRM_MS) then
      s.armedAt = now
      return true
    end
    s.armedAt = nil
    s.note = P.restoreSettings()
    return true
  end

  -- Why the module reports RENDER_OFF, in words: the developer override, the co-op switch, a part that is not running yet
  -- or that stopped. Returns a short reason and a longer explanation.
  function P.renderOffReason()
    local n, st = P.native, store.state
    if st.diagnostics.developer and st.diagnostics.disarm.render then
      return 'Render switched off by developer override',
             "Rendering is switched off by the developer override (Advanced > Developer > Subsystem switches > Render, " ..
             'with Developer options On). Turn Developer options Off, or set Render to Armed.'
    end
    if n and n.coopSwitch ~= nil and n.coopSwitch ~= 0 then
      return 'Co-op is switched off', 'The co-op switch is Off, which stops the co-op HUD. Turn Co-op On (main page) or press the on/off key.'
    end
    if n and E.subsystem and n.appliedSubsystems and (n.appliedSubsystems & E.subsystem.RENDER) == 0 then
      return 'Render is not running',
             'The module is not running its rendering part: it is still starting, or it stopped after an error. ' ..
             "The loader's hook.log has the details."
    end
    return 'The HUD could not start', "The HUD hook did not attach or stopped after an error. The loader's hook.log has the details."
  end

  function P.hudStatusText()
    if reportHas('VISION_NO_COMBAT_LOG') or P.visionState('noCombatLog') == true then
      return "Turn off Vision's No Combat Log"
    end
    if bridge.status.state ~= 'ready' or not P.native then return 'n/a' end
    if reportHas('FRAME_HOOK_OFF') then return 'Not drawing: another mod holds the HUD hook' end
    if not store.state.battleHud.enabled then return 'Off' end
    if reportHas('RENDER_OFF') then return 'Not drawing: ' .. (P.renderOffReason():gsub('^%u', string.lower)) end
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
      lines[#lines + 1] = "Another mod changed the code the co-op HUD is drawn from, so the game's own HUD is used. " ..
                          "The loader's hook.log has the details."
    elseif not store.state.battleHud.enabled then
      lines[#lines + 1] = "The Battle HUD preset is Off: the game's own HUD is used. The co-op battle menus stay."
    elseif reportHas('RENDER_OFF') then
      local _, long = P.renderOffReason()
      lines[#lines + 1] = long .. " The game's own HUD and battle menus are used meanwhile."
    end
    if reportHas('PARTY_LAYOUT_FOREIGN') then
      lines[#lines + 1] = 'Another mod changed the party list layout, so it is left as that mod set it.'
    end
    notify(table.concat(lines, '\n'))
  end

  -- "n of 8": how many subsystems the module runs, and which are off (names, in the schema's order).
  function P.subsystemsOff()
    local n = P.native
    if bridge.status.state ~= 'ready' or not n or not E.subsystem then return nil end
    local list = {}
    for _, k in ipairs(store.SUBSYSTEMS) do
      local bit = E.subsystem[store.SUBSYSTEM_BIT[k]]
      if bit and (n.appliedSubsystems & bit) == 0 then list[#list + 1] = title((k:gsub('(%u)', ' %1'))) end
    end
    return list
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

  -- The Status row's press: the state, what it means and what to do, then who plays, the parts that are off, and any
  -- pending note (a refusal, the settings file, a change waiting to apply).
  function P.explain()
    local v = P.view
    local lines = {P.statusText()}
    if v.detail then lines[#lines + 1] = v.detail end
    if v.advice then lines[#lines + 1] = v.advice end
    if v.kind == 'ok' or v.kind == 'switched_off' then
      if F.seats then
        for i = 1, ACTIVE do lines[#lines + 1] = seatLabel(i) .. ': ' .. F.seat_state_text(i) end
      end
      for i = 2, ACTIVE do
        local issue = F.seat_issue and F.seat_issue(i)
        if issue then lines[#lines + 1] = seatLabel(i) .. ' may be silent. ' .. issue .. '.' end
      end
      local off = P.subsystemsOff()
      if off and #off > 0 then
        lines[#lines + 1] = 'Parts off: ' .. table.concat(off, ', ') ..
                            (store.state.diagnostics.developer and ' (developer override).' or ' (co-op switched off).')
      end
      if store.lastRejection then lines[#lines + 1] = 'Refused: ' .. store.lastRejection end
      local n = P.native
      if n and n.state ~= E.state.INERT and n.appliedGeneration ~= n.acceptedGeneration then
        lines[#lines + 1] = 'Settings are waiting to apply.'
      end
    end
    local note = store.status_text()
    if note then lines[#lines + 1] = 'Settings file: ' .. note .. '.' end
    if store.readOnly then lines[#lines + 1] = 'The settings file is read-only this session, so changes are refused.' end
    notify(table.concat(lines, '\n'))
  end

  ---- callbacks -------------------------------------------------------------------------------------------------------------
  local function onChange(modId, fieldId, value)
    P.dirty = true
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
  -- Value rows that stay usable whatever the module does (they can bring it back, or only change the file).
  local ALWAYS_ACTIVE = {moduleOn = true, devMode = true}
  -- Value rows that need the module but not a writable settings file (they send a command, not a setting).
  local NEEDS_MODULE_ONLY = {coop = true}

  function P.refresh()
    local ready = bridge.is_ready()
    local active = ready and not store.readOnly
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
        -- only the Mod Menu's value rows (toggle, carousel) can be greyed: it refuses setActive on status and button rows
        if not ALWAYS_ACTIVE[id] then
          local key = id .. '#active'
          local on = NEEDS_MODULE_ONLY[id] and ready or active
          if P.cache[key] ~= on then
            P.cache[key] = on
            modmenu.setActive(M.MOD_ID, id, on)
          end
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
    local warn = #P.safetyProblems() > 0
    if P.cache.warn ~= warn then
      P.cache.warn = warn
      changes.safetyWarn = warn
    end
    local logSeat = store.state.battleHud.log.filter == 'MINE'
    if P.cache.logSeat ~= logSeat then
      P.cache.logSeat = logSeat
      changes.hudLogSeat = logSeat
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
        -- The kill switch may also be pressed in game (F8): the Co-op toggle follows the module's state whenever that
        -- state changes (a command sent from this page keeps its value until the module reports).
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
      P.readManifesto()
    end
    F.tick(P.now)
    P.view = S.classify(bridge.status, P.native, C)
    store.flush(P.now)
    P.sinceRefresh = (P.sinceRefresh or M.REFRESH_EVERY) + 1
    local v = P.view
    local fp = table.concat({tostring(v.kind), tostring(v.text), tostring(v.detail), tostring(v.advice),
                             tostring(F.player_count and F.player_count()), tostring(store.status_text()),
                             tostring(store.lastRejection), F.killed and 1 or 0, F.pending and 1 or 0}, '|')
    if P.dirty or F.pending or fp ~= P.lastFingerprint or P.sinceRefresh >= M.REFRESH_EVERY then
      P.dirty, P.lastFingerprint, P.sinceRefresh = false, fp, 0
      P.refresh()
    end
  end

  function P.start()
    P.view = S.classify(bridge.status, nil, C)
    P.readVision()
    P.readManifesto()
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
