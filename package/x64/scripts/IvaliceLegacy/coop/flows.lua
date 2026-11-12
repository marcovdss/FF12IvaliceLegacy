-- Ivalice Legacy co-op: the player flows behind the Mod Menu rows (controller pairing, the guarded change of Player 1's
-- pad, the swap offer, binding capture, parking, character picks, the co-op on/off action).
--
-- Rules this module keeps:
--   * it never touches the Mod Menu (no setValue/setActive/setStatus/setVisible, no modmenu at all): the page redraws from
--     the answers below on its own timer, so a callback can never edit the row it runs from;
--   * a flow is a request to native (a COMMAND with a request id), a window for the player, and the native event that
--     answers it. Settings are changed only through the store, which pushes and saves them;
--   * pairing and capture proofs happen in native and never edit settings: this module commits the result after one more
--     check (the pad is still listed, is connected, and is not Player 1's or another guest's);
--   * every native event that does not carry our current request id is ignored (a stale answer changes nothing).
local M = {}

M.GUARD_MS = 24000              -- how long we wait for a pairing or capture answer (native gives up at 20 s)
M.CONFIRM_MS = 14000            -- after PROVISIONAL: native waits 10 s for the confirming press, then up to 5 s to commit
M.SLOW_POLL_MS = 1000           -- seats and controller list are read about once a second
M.MAX_BINDINGS = 4

local function title(s)
  local t = tostring(s):lower():gsub('_', ' ')
  return (t:gsub('^%l', string.upper))
end
M.title = title

-- Windows virtual-key code -> short name (only the common keys; others show their code).
local function keyName(vk)
  if vk >= 0x70 and vk <= 0x7B then return 'F' .. (vk - 0x6F) end
  if vk >= 0x41 and vk <= 0x5A then return string.char(vk) end
  if vk >= 0x30 and vk <= 0x39 then return string.char(vk) end
  if vk >= 0x60 and vk <= 0x69 then return 'Numpad ' .. (vk - 0x60) end
  local names = {[0x08] = 'Backspace', [0x09] = 'Tab', [0x0D] = 'Enter', [0x1B] = 'Esc', [0x20] = 'Space', [0x21] = 'Page up',
                 [0x22] = 'Page down', [0x23] = 'End', [0x24] = 'Home', [0x25] = 'Left', [0x26] = 'Up', [0x27] = 'Right',
                 [0x28] = 'Down', [0x2D] = 'Insert', [0x2E] = 'Delete', [0xA0] = 'Left Shift', [0xA1] = 'Right Shift',
                 [0xA2] = 'Left Ctrl', [0xA3] = 'Right Ctrl', [0xA4] = 'Left Alt', [0xA5] = 'Right Alt'}
  return names[vk] or string.format('Key 0x%02X', vk)
end

-- deps: store, bridge, codec, views, dialogs, log(text), seed (first request id base, tests)
function M.create(deps)
  local store, bridge, C, V, D = deps.store, deps.bridge, deps.codec, deps.views, deps.dialogs
  local E = C.enums
  local ACTIVE = C.schema.const.ACTIVE_SEATS_MAX or 3      -- seats with a human player (fail closed: 3)
  local log = deps.log or function() end
  local F = {now = 0, killed = false, pending = nil, devices = nil, seats = nil, identified = {}, lastSlowPoll = nil}

  local base = deps.seed
  if base == nil then base = (type(os) == 'table' and os.time and os.time()) or 0 end
  local nextRequest = (base % 0x7FFFFF) * 256
  local function newRequest()
    nextRequest = nextRequest + 1
    return nextRequest
  end

  local function seatLabel(i) return 'Player ' .. i end

  ---- text -------------------------------------------------------------------------------------------------------------
  function F.binding_text(list)
    if not list or #list == 0 then return 'Unbound' end
    local parts = {}
    for _, b in ipairs(list) do
      if b.kind == 'KEY' then parts[#parts + 1] = keyName(b.code)
      elseif b.kind == 'MOUSE' then parts[#parts + 1] = 'Mouse ' .. b.code
      elseif b.kind == 'PAD' then parts[#parts + 1] = title(C.name_of('pad_control', b.code) or ('control ' .. b.code))
      else parts[#parts + 1] = '?' end
    end
    return table.concat(parts, ', ')
  end

  local function errText(err)
    if err == 'NOT_READY' then return 'The co-op input service is not running yet.' end
    if err == 'BUSY' then return 'Co-op is busy. Try again in a moment.' end
    return 'The co-op module refused it (' .. tostring(err) .. ').'
  end

  ---- native requests ----------------------------------------------------------------------------------------------------
  -- Returns the request id, or nil and the error name.
  local function send(name, seat, a0, a1)
    local rid = newRequest()
    local r, err = bridge.command(name, rid, seat or 0, a0 or 0, a1 or 0)
    if not r then
      log(string.format('command %s refused: %s', name, tostring(err)))
      return nil, err
    end
    return rid
  end

  -- Best effort: a command that only speeds up what a settings push will do anyway.
  local function nudge(name, seat, a0, a1)
    if bridge.is_ready() then send(name, seat, a0, a1) end
  end

  function F.refresh_devices()
    if not bridge.is_ready() then return nil end
    local r = bridge.get_devices()
    if not r then return nil end
    F.devices = V.devices(r)
    F.devicesAt = F.now
    return F.devices
  end

  function F.refresh_seats()
    if not bridge.is_ready() then return nil end
    local r = bridge.get_seats()
    if not r then return nil end
    F.seats = V.seats(r)
    return F.seats
  end

  ---- how a seat's controller and state read on the page -----------------------------------------------------------------
  local function stateDevice(seat) return store.state.seats[seat].device end

  -- Does this listed pad stand for that stored device? Directly, or through a learned XInput<->SDL alias.
  local function matches(device, entry)
    if device.kind == 'NONE' then return false end
    if store.same_device(device, store.device_from_entry(entry)) then return true end
    for _, a in ipairs(store.state.aliases) do
      if device.kind == 'XINPUT' and entry.kind == 'SDL' and a.slot == device.slot and store.same_identity(a.sdl, entry.identity) then
        return true
      end
      if device.kind == 'SDL' and entry.kind == 'XINPUT' and a.slot == entry.slot and store.same_identity(a.sdl, device.sdl) then
        return true
      end
    end
    return false
  end

  function F.device_text(seat)
    local text
    local devs = F.devices
    if devs and devs.ready then
      for _, e in ipairs(devs.entries) do
        if e.connected and e.owner == seat then text = D.clean(e.name) break end
      end
    end
    if not text then
      local d = stateDevice(seat)
      local known = F.identified[seat]
      if d.kind == 'NONE' then
        text = 'None'
      elseif d.kind == 'XINPUT' then
        text = (known and store.same_device(known.device, d)) and known.name or ('XInput ' .. (d.slot + 1))
      else
        text = D.clean(d.sdl and d.sdl.name or 'Controller')
        if devs and devs.ready then
          local listed = false
          for _, e in ipairs(devs.entries) do
            if e.connected and matches(d, e) then listed = true break end
          end
          if not listed then text = text .. ' (disconnected)' end
        end
      end
    end
    if seat == 1 then text = text .. ' (locked)' end
    return text
  end

  function F.seat_state_text(seat)
    local s = F.seats and F.seats.seats[seat]
    if not s then return 'No game running' end
    local base
    if s.parked then base = 'Parked'
    elseif s.benched then base = 'Waiting for a character'
    elseif s.playing then base = 'Playing'
    elseif s.hasDevice or s.hasKeyboard then base = 'Ready'
    else base = 'No controller' end
    if seat == 1 then base = 'Host' .. (s.playing and ', playing' or '') end
    local who = s.liveMember
    local names = {[0] = 'Vaan', 'Ashe', 'Fran', 'Balthier', 'Basch', 'Penelo'}
    if names[who] and (s.playing or seat == 1) then base = base .. ' as ' .. names[who] end
    return base
  end

  ---- the one running operation (pairing or capture) ---------------------------------------------------------------------
  function F.has_pending() return F.pending ~= nil end

  local function closePanel(p)
    if p and p.panel then p.panel.close() end
  end

  local function finish(note)
    local p = F.pending
    F.pending = nil
    closePanel(p)
    if note then D.notify(note) end
  end

  local function secondsLeft(p) return math.max(0, math.ceil((p.deadline - F.now) / 1000)) end

  local function pendingText(p)
    local s = secondsLeft(p)
    if p.kind == 'capture' then
      return string.format('%s, %s: press the new button or key now. (%d s)', seatLabel(p.seat), title(p.action), s)
    end
    if p.phase == 'confirm' then
      return string.format('%s: press A on %s again to confirm. If you do not, the change is undone. (%d s)',
                           seatLabel(p.seat), p.name, s)
    end
    if p.change then
      return string.format('%s: press A on %s to start the change. Your current controller keeps working. (%d s)',
                           seatLabel(p.seat), p.name, s)
    end
    return string.format('%s: release A, then press A on %s. (%d s)', seatLabel(p.seat), p.name, s)
  end

  function F.pending_text()
    return F.pending and pendingText(F.pending) or nil
  end

  local function openPanel(p)
    p.shown = secondsLeft(p)
    p.panel = D.panel(pendingText(p), 3)
  end

  local function refreshPanel(p)
    local s = secondsLeft(p)
    if p.panel and s ~= p.shown then
      p.shown = s
      p.panel.setText(pendingText(p))
    end
  end

  -- The player (or the 24 s guard) ends the operation: tell native, close the window.
  function F.cancel(reason)
    local p = F.pending
    if not p then return end
    if p.kind == 'pair' then nudge('PAIRING_CANCEL', p.seat) else nudge('CAPTURE_CANCEL', p.seat) end
    finish(reason)
  end

  ---- commit a proven pad -------------------------------------------------------------------------------------------------
  local function findEntry(devs, id)
    for _, e in ipairs(devs.entries) do if e.id == id then return e end end
  end

  -- Re-checks the pad, then stores it for the seat (and the alias the proof learned). Returns true or false and why.
  local function commitPad(seat, entry, learnedSlot)
    local devs = F.refresh_devices()
    local now = devs and findEntry(devs, entry.id)
    if not devs or not devs.ready or not now or not now.connected then return false, 'that controller is no longer connected' end
    if seat ~= 1 and (now.p1Locked or now.owner == 1) then return false, 'that controller is Player 1\'s and stays locked' end
    if now.owner ~= 0 and now.owner ~= seat then
      return false, string.format('that controller is now used by %s', seatLabel(now.owner))
    end
    return store.change(function(st)
      st.seats[seat].device = store.device_from_entry(now)
      if learnedSlot and now.kind == 'SDL' then store.add_alias(st, learnedSlot, now.identity) end
    end, bridge)
  end

  ---- swap offer ---------------------------------------------------------------------------------------------------
  local function doSwap(seat, other, entry)
    local before = F.device_text(seat):gsub(' %(.*%)$', '')       -- what the picker held: the other seat gets it
    local ok, why = store.change(function(st)
      if not matches(st.seats[other].device, entry) then return false, 'that controller changed hands' end
      st.seats[seat].device, st.seats[other].device = store.copy(st.seats[other].device), store.copy(st.seats[seat].device)
    end, bridge)
    if ok then
      D.notify(string.format('%s now uses %s. %s now uses %s.', seatLabel(seat), D.clean(entry.name), seatLabel(other),
                             before == 'None' and 'no controller' or before))
    else
      D.notify('Controllers not swapped: ' .. tostring(why))
    end
  end

  function F.offer_swap(seat, other, entry)
    D.ask(string.format('That controller is %s\'s. Swap controllers with %s?\n%s would get %s\'s old controller.',
                        seatLabel(other), seatLabel(other), seatLabel(other), seatLabel(seat)),
          {'Swap', 'Keep as it is'}, function(i) if i == 0 then doSwap(seat, other, entry) end end)
  end

  ---- pairing -------------------------------------------------------------------------------------------------------------
  local function startPairing(seat, entry, devs)
    local rid, err = send('PAIRING_START', seat, devs.session, entry.id)
    if not rid then D.notify(errText(err)) return end
    local p = {kind = 'pair', rid = rid, seat = seat, entry = entry, name = D.clean(entry.name), phase = 'press',
               deadline = F.now + M.GUARD_MS, change = seat == 1 and entry.owner ~= 1}
    F.pending = p
    openPanel(p)
  end

  local function pickEntry(seat, entry, devs)
    if seat ~= 1 then
      if entry.p1Locked or entry.owner == 1 then
        D.notify('That controller is Player 1\'s and stays locked.')
        return
      end
      if entry.owner ~= 0 and entry.owner ~= seat then
        F.offer_swap(seat, entry.owner, entry)
        return
      end
      startPairing(seat, entry, devs)
      return
    end
    -- Player 1
    if entry.owner == 1 then
      startPairing(1, entry, devs)                       -- identify only: native never moves Player 1's pad here
      return
    end
    if entry.owner ~= 0 then
      D.notify(string.format('That controller is %s\'s. Set %s to None first, then choose it for Player 1.',
                             seatLabel(entry.owner), seatLabel(entry.owner)))
      return
    end
    D.ask(string.format('Change Player 1 to %s?\nYour current controller keeps working until the new one is proven: press A '
                        .. 'on it, then press A on it again to confirm. If the second press does not come, the change is '
                        .. 'undone.', D.clean(entry.name)),
          {'Start', 'Cancel'}, function(i) if i == 0 then startPairing(1, entry, devs) end end)
  end

  -- Opens the controller list for a seat (the row's press).
  function F.open_pairing(seat)
    if F.pending then D.notify('Finish or cancel the current controller step first.') return end
    if not bridge.is_ready() then D.notify('The co-op module is not running.') return end
    local devs = F.refresh_devices()
    if not devs then D.notify('Could not read the controller list.') return end
    if not devs.ready then
      D.notify('Controller discovery is not running yet. It starts with the game. Try again once a game is loaded.')
      return
    end
    local labels, entries, seen = {}, {}, {}
    if seat ~= 1 then labels[#labels + 1] = 'None (no controller)'; entries[#labels] = {none = true} end
    local count = {}
    for _, e in ipairs(devs.entries) do
      if e.connected then count[D.clean(e.name)] = (count[D.clean(e.name)] or 0) + 1 end
    end
    for _, e in ipairs(devs.entries) do
      if e.connected then
        local label = D.clean(e.name)
        if count[label] > 1 then
          seen[label] = (seen[label] or 0) + 1
          label = label .. ' #' .. seen[label]
        end
        if e.owner == seat then label = label .. ' (current)'
        elseif e.p1Locked or e.owner == 1 then label = label .. ' (Player 1, locked)'
        elseif e.owner ~= 0 then label = label .. ' (' .. seatLabel(e.owner) .. ')' end
        labels[#labels + 1] = label
        entries[#labels] = {entry = e}
      end
    end
    if next(count) == nil and seat == 1 then
      D.notify('No controllers found. Plug one in, then open this list again.')
      return
    end
    labels[#labels + 1] = 'Cancel'
    local head
    if seat == 1 then
      head = 'Player 1 controller.\nPick the one you are using to see its name, or pick another to switch to it.'
    else
      head = string.format('Choose %s\'s controller.', seatLabel(seat))
    end
    D.ask(head .. '\nNow: ' .. F.device_text(seat), labels, function(i)
      local chosen = i and entries[i + 1]
      if not chosen then return end
      if chosen.none then
        local ok, why = store.change(function(st) st.seats[seat].device = {kind = 'NONE', slot = 0} end, bridge)
        if not ok then D.notify('Controller not changed: ' .. tostring(why)) end
        return
      end
      pickEntry(seat, chosen.entry, devs)
    end)
  end

  local PAIR_NOTES = {
    TIMEOUT = 'No answer in time. Nothing was changed.',
    HELD_A = 'A was already held down. Release every button, then try again.',
    DISCONNECTED = 'That controller was unplugged.',
    TWO_PADS = 'More than one controller answered at once. Press A on the chosen controller only.',
    P1_LOCKED = 'That is Player 1\'s controller. It stays locked.',
    STALE_SESSION = 'The controller list changed. Open it again.',
    NOT_FOUND = 'The controller list changed. Open it again.',
    REVERTED = 'The controller change was undone. Your previous controller is back.',
  }

  local function onPairing(ev)
    local p = F.pending
    local r = V.pairing(ev)
    if not p or p.kind ~= 'pair' or r.request ~= p.rid then
      log('ignored a stale pairing answer ' .. tostring(r.outcome))
      return
    end
    local out = r.outcome
    if out == 'PROVISIONAL' then
      p.phase, p.deadline = 'confirm', F.now + M.CONFIRM_MS
      if p.panel then p.panel.setText(pendingText(p)) end
      p.shown = secondsLeft(p)
      return
    end
    if out == 'CANCELLED' then finish() return end
    if out == 'OWNED_BY_SEAT' then
      local other = r.otherSeat
      finish()
      if p.seat ~= 1 and other >= 1 and other <= ACTIVE then
        if other == 1 then D.notify('That is Player 1\'s controller. It stays locked.') else F.offer_swap(p.seat, other, p.entry) end
      else
        D.notify(string.format('That controller belongs to %s.', seatLabel(other)))
      end
      return
    end
    if out == 'READY' then
      finish()
      local ok, why = commitPad(p.seat, p.entry, r.learnedSlot)
      if ok then D.notify(string.format('%s now uses %s.', seatLabel(p.seat), p.name))
      else D.notify('Controller not saved: ' .. tostring(why)) end
      return
    end
    if out == 'IDENTIFIED' then
      finish()
      F.identified[p.seat] = {name = p.name, device = store.copy(stateDevice(p.seat))}
      if r.learnedSlot and p.entry.kind == 'SDL' then
        store.change(function(st) store.add_alias(st, r.learnedSlot, p.entry.identity) end, bridge)
      end
      D.notify(string.format('%s\'s controller is %s.', seatLabel(p.seat), p.name))
      return
    end
    if out == 'CONFIRMED' then
      finish()
      local ok, why = commitPad(p.seat, p.entry, r.learnedSlot)
      if ok then D.notify(string.format('%s now uses %s.', seatLabel(p.seat), p.name))
      else D.notify('The new controller could not be saved (' .. tostring(why) .. '). Your previous controller comes back by itself within about 5 seconds.') end
      return
    end
    finish(PAIR_NOTES[out] or ('Controller step ended (' .. tostring(out) .. ').'))
  end

  ---- binding capture ------------------------------------------------------------------------------------------------------
  local CAPTURE_NOTES = {
    TIMEOUT = 'No button or key was pressed in time. Nothing was changed.',
    NO_INPUT = 'That input cannot be used here. Keys work only for the keyboard seat.',
  }

  local function startCapture(seat, action, slot)
    local rid, err = send('CAPTURE_START', seat, E.action[action], slot)
    if not rid then D.notify(errText(err)) return end
    local p = {kind = 'capture', rid = rid, seat = seat, action = action, slot = slot, deadline = F.now + M.GUARD_MS}
    F.pending = p
    openPanel(p)
  end

  -- Opens the capture question for one action of one seat.
  function F.open_capture(seat, action)
    if F.pending then D.notify('Finish or cancel the current controller step first.') return end
    if not bridge.is_ready() then D.notify('The co-op module is not running.') return end
    local list = store.state.seats[seat].bindings[action] or {}
    local labels, modes = {}, {}
    if #list == 0 then
      labels = {'Set a binding', 'Cancel'}; modes = {'replace'}
    elseif #list < M.MAX_BINDINGS then
      labels = {'Replace the first', 'Add another', 'Cancel'}; modes = {'replace', 'add'}
    else
      labels = {'Replace the first', 'Cancel'}; modes = {'replace'}
    end
    D.ask(string.format('%s, %s.\nNow: %s', seatLabel(seat), title(action), F.binding_text(list)), labels, function(i)
      local mode = i and modes[i + 1]
      if not mode then return end
      startCapture(seat, action, mode == 'add' and #list or 0)
    end)
  end

  local function onCapture(ev)
    local p = F.pending
    local r = V.capture(ev)
    if not p or p.kind ~= 'capture' or r.request ~= p.rid then
      log('ignored a stale capture answer ' .. tostring(r.outcome))
      return
    end
    if r.outcome == 'CANCELLED' then finish() return end
    if r.outcome ~= 'CAPTURED' then finish(CAPTURE_NOTES[r.outcome] or 'Nothing was captured.') return end
    finish()
    local ok, why = store.change(function(st)
      local list = st.seats[p.seat].bindings[p.action]
      if not list then list = {}; st.seats[p.seat].bindings[p.action] = list end
      local new = {kind = r.kind, code = r.code}
      for i = #list, 1, -1 do
        if list[i].kind == new.kind and list[i].code == new.code then table.remove(list, i) end
      end
      if p.slot < #list then list[p.slot + 1] = new
      elseif #list < M.MAX_BINDINGS then list[#list + 1] = new
      else list[#list] = new end
    end, bridge)
    if ok then
      D.notify(string.format('%s, %s: %s.', seatLabel(p.seat), title(p.action),
                             F.binding_text(store.state.seats[p.seat].bindings[p.action])))
    else
      D.notify('Binding not saved: ' .. tostring(why))
    end
  end

  ---- parking (all guests, or one seat) ---------------------------------------------------------------------------------
  -- seat nil = all guests. The setting is the record; the command only makes it take hold before the push is applied.
  function F.set_parked(seat, parked)
    local ok, why = store.change(function(st)
      for i = 2, ACTIVE do
        if seat == nil or seat == i then st.seats[i].parked = parked end
      end
    end, bridge)
    if ok then nudge('PARK', seat or 0, parked and 1 or 0) end
    return ok, why
  end

  ---- character picks (every seat) ---------------------------------------------------------------------
  -- Choosing a character another seat holds swaps the two seats; a guest can never take Player 1's character.
  function F.set_character(seat, name)
    local moves = {}
    local ok, why = store.change(function(st)
      if name ~= 'AUTO' and name ~= 'NONE' then
        for other = 1, ACTIVE do
          if other ~= seat and st.seats[other].character == name then
            if other == 1 then
              return false, string.format('Player 1 is playing %s. Pick someone else, or change Player 1 first.', title(name))
            end
            st.seats[other].character = st.seats[seat].character
            moves[#moves + 1] = {other, st.seats[other].character}
          end
        end
      end
      st.seats[seat].character = name
      table.insert(moves, 1, {seat, name})
    end, bridge)
    if ok then
      for _, m in ipairs(moves) do
        local c = E.character[m[2]]
        if c and c <= 5 then nudge('MOVE_SEAT_NOW', m[1], c) end
      end
    end
    return ok, why
  end

  ---- co-op on/off now (the menu action) -----------------------------------------------------------------------------------
  function F.set_killed(flag)
    if not bridge.is_ready() then D.notify('The co-op module is not running.') return false end
    local rid, err = send('KILL_SWITCH', 0, flag and 1 or 0)
    if not rid then D.notify(errText(err)) return false end
    F.killed = flag
    return true
  end

  ---- events and the clock -------------------------------------------------------------------------------------------------------
  function F.on_events(list)
    for _, raw in ipairs(list or {}) do
      local ok, err = pcall(function()
        local ev = V.event(raw)
        local kind = ev.kindName
        if kind == 'PAIRING_RESULT' then
          onPairing(ev)
        elseif kind == 'CAPTURE_RESULT' then
          onCapture(ev)
        elseif kind == 'COMMAND_REJECTED' then
          local p = F.pending
          if p and ev.generation == p.rid then
            finish('The co-op module refused that step (' .. tostring(bridge.describe_error(ev.code)) .. ').')
          else
            log('a command was rejected: ' .. tostring(ev.code))
          end
        end
      end)
      if not ok then log('co-op event failed: ' .. tostring(err)) end
    end
  end

  -- Page clock in ms. Drives the countdown, the 24 s guard and the slow reads.
  function F.tick(now)
    F.now = now
    local p = F.pending
    if p then
      if now >= p.deadline then
        F.cancel(p.kind == 'pair' and 'No answer in time. Nothing was changed.'
                 or 'No button or key was pressed in time. Nothing was changed.')
      else
        refreshPanel(p)
      end
    end
    if bridge.is_ready() and (F.lastSlowPoll == nil or now - F.lastSlowPoll >= M.SLOW_POLL_MS) then
      F.lastSlowPoll = now
      F.refresh_seats()
      F.refresh_devices()
    end
  end

  return F
end

return M
