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

-- How an action is named for the player (the schema names read badly: "Toggle control", "Walk run").
local ACTION_LABELS = {TOGGLE_CONTROL = 'Join/leave key', WALK_RUN = 'Walk/run', TARGET_PREV = 'Previous target',
                       TARGET_NEXT = 'Next target'}
function M.action_label(action) return ACTION_LABELS[action] or title(action) end
local actionLabel = M.action_label

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
  local F = {now = 0, action_label = M.action_label, killed = false, pending = nil, devices = nil, seats = nil, identified = {}, lastSlowPoll = nil}

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
      elseif b.kind == 'MOUSE' then
        parts[#parts + 1] = b.code == 1 and 'Left mouse' or b.code == 2 and 'Right mouse' or ('Mouse ' .. b.code)
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
      if p.seat == 0 then
        return string.format('Co-op on/off key: press the new key now (Esc cancels), or the right mouse button. (%d s)', s)
      end
      return string.format('%s, %s: press the new button or key now. (%d s)', seatLabel(p.seat), actionLabel(p.action), s)
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
  local KILL_NOTES = {
    TIMEOUT = 'No key was pressed in time. Nothing was changed. Keys are only seen while the game window is in front.',
    NO_INPUT = 'That input cannot be used for the co-op on/off key. Use a keyboard key or the right mouse button.',
  }

  -- Seat 0 is the co-op on/off key. Its capture takes keyboard keys and the right mouse button only.
  local function startCapture(seat, action, slot)
    local rid, err
    if seat == 0 then
      rid, err = send('CAPTURE_START', 0, 0, slot)
      if not rid and err == 'BAD_FIELD' then
        D.notify('This version of the co-op module cannot capture the on/off key. Use Co-op right now on the main page.')
        return
      end
    else
      rid, err = send('CAPTURE_START', seat, E.action[action], slot)
    end
    if not rid then D.notify(errText(err)) return end
    local p = {kind = 'capture', rid = rid, seat = seat, action = action, slot = slot, deadline = F.now + M.GUARD_MS}
    F.pending = p
    openPanel(p)
  end

  -- The join/leave keys and the co-op on/off key are hotkeys read for every seat, so one key (or the right mouse button)
  -- on two of them would fire both. Pad buttons belong to one pad and cannot clash. Returns who already holds the
  -- binding: {kill = true} or {seat = n}, or nil. `skip` leaves out the list being edited ({kill = true} or {seat = n}).
  local function sameBinding(a, b) return a.kind == b.kind and a.code == b.code end
  function F.hotkey_holder(st, b, skip)
    if b.kind ~= 'KEY' and b.kind ~= 'MOUSE' then return nil end
    for i = 2, ACTIVE do
      if not (skip and skip.seat == i) then
        for _, x in ipairs(st.seats[i].bindings.TOGGLE_CONTROL or {}) do
          if sameBinding(x, b) then return {seat = i} end
        end
      end
    end
    if not (skip and skip.kill) then
      for _, x in ipairs(st.killSwitch or {}) do
        if sameBinding(x, b) then return {kill = true} end
      end
    end
    return nil
  end

  local function holderName(h) return h.kill and 'the co-op on/off key' or (seatLabel(h.seat) .. '\'s join/leave key') end

  -- Every action in schema order, so the first match is always the same one.
  local actionOrder = {}
  for name, idx in pairs(E.action) do actionOrder[#actionOrder + 1] = {name = name, idx = idx} end
  table.sort(actionOrder, function(a, b) return a.idx < b.idx end)

  -- The seat that owns the keyboard (and the mouse), or nil when none plays.
  local function keyboardSeatOf(st)
    local k = st.keyboardSeat
    if math.type(k) == 'integer' and k >= 1 and k <= ACTIVE then return k end
    return nil
  end

  -- The first action of `bindings` (one seat's map) that holds keyboard or mouse binding b.
  local function actionHolding(bindings, b)
    if b.kind ~= 'KEY' and b.kind ~= 'MOUSE' then return nil end
    for _, a in ipairs(actionOrder) do
      for _, x in ipairs(bindings[a.name] or {}) do
        if sameBinding(x, b) then return a.name end
      end
    end
    return nil
  end

  -- An on/off key that is also a key of the keyboard player would fire both on one press.
  local function keyboardClash(st, b)
    local ks = keyboardSeatOf(st)
    local action = ks and actionHolding(st.seats[ks].bindings, b)
    if not action then return nil end
    return string.format('%s is already %s\'s %s control. Pick another, or change that one first.', F.binding_text({b}),
                         seatLabel(ks), actionLabel(action):lower())
  end

  -- The first keyboard or mouse binding of `bindings` (one seat's map, its own join/leave key left out) that is also a
  -- hotkey held elsewhere: another seat's join/leave key or an on/off key. Returns binding, action name, holder.
  local function seatHotkeyClash(st, seat, bindings)
    for _, a in ipairs(actionOrder) do
      if a.name ~= 'TOGGLE_CONTROL' then
        for _, b in ipairs(bindings[a.name] or {}) do
          local h = F.hotkey_holder(st, b, {seat = seat})
          if h then return b, a.name, h end
        end
      end
    end
    return nil
  end

  -- Why a seat may not take the keyboard: one of its keys is an on/off key or another player's join/leave key. nil
  -- when it may.
  function F.keyboard_seat_conflict(st, seat)
    if math.type(seat) ~= 'integer' or seat < 1 or seat > ACTIVE then return nil end
    local b, action, h = seatHotkeyClash(st, seat, st.seats[seat].bindings)
    if not b then return nil end
    return string.format('%s uses %s for %s, which is also %s. Change that one first.', seatLabel(seat),
                         F.binding_text({b}), actionLabel(action):lower(), holderName(h))
  end

  -- The module's own refusal of a record, in words for the player. Lua's refusals come first; this covers what only
  -- the module can see (a key twice in the on/off list, a clash it checks itself).
  -- The module reports a clash at a field and a location (store.lastRejectField / lastRejectLocation, read right after
  -- the refusal; the text alone is the fallback; the location packs the seat (or the kill switch) above the binding index):
  --   on/off key (kill_switch)         the same key twice in the list, a player's join/leave key, or a key or mouse button
  --                                    of the keyboard player
  --   a guest seat's bindings          that seat's join/leave key equals another seat's join/leave key, or a key or mouse
  --                                    button of the keyboard player
  --   Player 1's bindings (location 0) the same binding twice in one action
  function F.explain_refusal(why)
    local text = tostring(why or '')
    if not text:find('CONFLICT', 1, true) then return text end
    local field = tostring(store.lastRejectField or text)
    local where = store.lastRejectLocation and (store.lastRejectLocation >> 8)   -- location = seat or kill switch << 8 | binding
    local index = store.lastRejectLocation and (store.lastRejectLocation & 0xFF)
    if field:find('kill_switch', 1, true) then
      return 'That on/off key clashes with another key: another on/off key, a join/leave key, or a key or mouse button ' ..
             'of the keyboard player.'
    end
    if field:find('bindings', 1, true) then
      -- the join/leave sentence only for a join/leave slot of a guest seat (the first BINDINGS_PER_ACTION indexes)
      local perAction = C.schema.const.BINDINGS_PER_ACTION
      if where == 0 or (index and index >= perAction) then
        return 'That key is already used by another control of the same player.'
      end
      return "That join/leave key clashes with another player's join/leave key, or with a key or mouse button of the " ..
             'keyboard player.'
    end
    return text
  end

  local function clashText(b, h)
    return string.format('%s is already %s. Pick another, or change that one first.', F.binding_text({b}), holderName(h))
  end

  -- Why a seat's default controls cannot come back right now (one of its default hotkeys is taken), or nil.
  function F.reset_conflict(st, seat, bindings)
    for _, b in ipairs(bindings.TOGGLE_CONTROL or {}) do
      local h = F.hotkey_holder(st, b, {seat = seat})
      if h then
        return string.format('%s is now %s. Change that first.', F.binding_text({b}), holderName(h))
      end
    end
    local ks = keyboardSeatOf(st)
    if seat == ks then
      local b, action, h = seatHotkeyClash(st, seat, bindings)
      if b then
        return string.format('%s is %s and a default key of this player (%s). Change that first.', F.binding_text({b}),
                             holderName(h), actionLabel(action):lower())
      end
    elseif ks and seat >= 2 then
      for _, b in ipairs(bindings.TOGGLE_CONTROL or {}) do
        local action = actionHolding(st.seats[ks].bindings, b)
        if action then
          return string.format('%s is a default join/leave key of this player, but %s uses it for %s. Change that first.',
                               F.binding_text({b}), seatLabel(ks), actionLabel(action):lower())
        end
      end
    end
  end

  -- Keyboard keys (not the mouse buttons' own codes) and the right mouse button.
  local function killInput(b)
    if b.kind == 'MOUSE' then return b.code == 2 end
    return b.kind == 'KEY' and not (b.code == 1 or b.code == 2 or (b.code >= 4 and b.code <= 6))
  end

  -- What to ask for a binding list: set when empty, replace or add, replace only when full.
  local function captureChoices(list)
    if #list == 0 then return {'Set a binding', 'Cancel'}, {'replace'} end
    if #list < M.MAX_BINDINGS then return {'Replace the first', 'Add another', 'Cancel'}, {'replace', 'add'} end
    return {'Replace the first', 'Cancel'}, {'replace'}
  end

  local function readOnlyNote()
    D.notify('Settings are read-only this session, so controls cannot be changed.')
  end

  -- Opens the capture question for one action of one seat.
  function F.open_capture(seat, action)
    if F.pending then D.notify('Finish or cancel the current controller step first.') return end
    if not bridge.is_ready() then D.notify('The co-op module is not running.') return end
    if store.readOnly then readOnlyNote() return end
    local list = store.state.seats[seat].bindings[action] or {}
    local labels, modes = captureChoices(list)
    D.ask(string.format('%s, %s.\nNow: %s', seatLabel(seat), actionLabel(action), F.binding_text(list)), labels, function(i)
      local mode = i and modes[i + 1]
      if not mode then return end
      startCapture(seat, action, mode == 'add' and #list or 0)
    end)
  end

  -- The co-op on/off key (a few slots, keyboard and right mouse button only).
  function F.open_kill_capture()
    if F.pending then D.notify('Finish or cancel the current controller step first.') return end
    if not bridge.is_ready() then D.notify('The co-op module is not running.') return end
    if store.readOnly then readOnlyNote() return end
    local list = store.state.killSwitch or {}
    local labels, modes = captureChoices(list)
    D.ask('Co-op on/off key.\nNow: ' .. F.binding_text(list) .. '\nPress a keyboard key, or the right mouse button. Esc cancels.', labels,
          function(i)
            local mode = i and modes[i + 1]
            if not mode then return end
            startCapture(0, 'KILL_SWITCH', mode == 'add' and #list or 0)
          end)
  end

  -- Put `new` into a list at the asked slot: replace that slot, or add at the end (the last slot when full). Any other
  -- copy of `new` in the list is dropped, so a binding is never held twice (and setting a slot to what it already
  -- holds changes nothing).
  local function place(list, slot, new)
    local at
    if slot < #list then at = slot + 1
    elseif #list < M.MAX_BINDINGS then at = #list + 1
    else at = #list end
    list[at] = new
    local kept = {}
    for i, b in ipairs(list) do
      if i == at or not sameBinding(b, new) then kept[#kept + 1] = b end
    end
    for i = 1, math.max(#list, #kept) do list[i] = kept[i] end
  end

  local function onKillCapture(p, r)
    if r.outcome == 'CANCELLED' then finish() return end
    if r.outcome ~= 'CAPTURED' then finish(KILL_NOTES[r.outcome] or 'Nothing was captured.') return end
    if not r.killSwitch then
      log('a capture answer for another action came back with the on/off key request')
      finish('Nothing was captured.')
      return
    end
    finish()
    local new = {kind = r.kind, code = r.code}
    if new.kind == 'KEY' and new.code == 0x1B then            -- Esc is the way out of this capture, never an on/off key
      D.notify('Cancelled. Nothing was changed.')
      return
    end
    local ok, why = store.change(function(st)
      if not killInput(new) then
        return false, 'That cannot be the co-op on/off key. Use a keyboard key or the right mouse button.'
      end
      local h = F.hotkey_holder(st, new, {kill = true})
      if h then return false, clashText(new, h) end
      local clash = keyboardClash(st, new)
      if clash then return false, clash end
      local list = st.killSwitch
      for i, b in ipairs(list) do
        if sameBinding(b, new) and i ~= p.slot + 1 then
          return false, F.binding_text({new}) .. ' is already one of the co-op on/off keys.'
        end
      end
      place(list, p.slot, new)
    end, bridge)
    if ok then
      D.notify('Co-op on/off key: ' .. F.binding_text(store.state.killSwitch) .. '.')
    else
      D.notify('Key not saved: ' .. F.explain_refusal(why))
    end
  end

  local function onCapture(ev)
    local p = F.pending
    local r = V.capture(ev)
    if not p or p.kind ~= 'capture' or r.request ~= p.rid then
      log('ignored a stale capture answer ' .. tostring(r.outcome))
      return
    end
    if p.seat == 0 then onKillCapture(p, r) return end
    if r.outcome == 'CANCELLED' then finish() return end
    if r.outcome ~= 'CAPTURED' then finish(CAPTURE_NOTES[r.outcome] or 'Nothing was captured.') return end
    if r.killSwitch then
      log('an on/off key answer came back with a seat request')
      finish('Nothing was captured.')
      return
    end
    finish()
    local ok, why = store.change(function(st)
      local list = st.seats[p.seat].bindings[p.action]
      if not list then list = {}; st.seats[p.seat].bindings[p.action] = list end
      local new = {kind = r.kind, code = r.code}
      local ks = keyboardSeatOf(st)
      if p.action == 'TOGGLE_CONTROL' then
        local h = F.hotkey_holder(st, new, {seat = p.seat})
        if h then return false, clashText(new, h) end
        if ks and p.seat ~= ks then                          -- a guest's join/leave key is read from the keyboard too
          local clash = keyboardClash(st, new)
          if clash then return false, clash end
        end
      elseif p.seat == ks and (new.kind == 'KEY' or new.kind == 'MOUSE') then
        local h = F.hotkey_holder(st, new, {seat = p.seat})  -- the on/off key and every other seat's join/leave key
        if h then return false, clashText(new, h) end
      end
      place(list, p.slot, new)
    end, bridge)
    if ok then
      D.notify(string.format('%s, %s: %s.', seatLabel(p.seat), actionLabel(p.action),
                             F.binding_text(store.state.seats[p.seat].bindings[p.action])))
    else
      D.notify('Binding not saved: ' .. F.explain_refusal(why))
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

  ---- developer rows (Diagnostics) ----------------------------------------------------------------------------------------
  M.STALL_MS = 3000
  M.STALL_WAIT_MS = 15000        -- how long a requested stall is waited for before it is forgotten

  -- Writes the module's counters to the log: a header line (module time, module state, generation), then one line per
  -- field, name=value, in schema order. Returns true and the number of values, or false.
  function F.dump_counters()
    if not bridge.is_ready() then D.notify('The co-op module is not running.') return false end
    local r, err = bridge.get_counters()
    if not r then D.notify('The counters could not be read (' .. tostring(err) .. ').') return false end
    local want = C.schema.const.COUNTERS_LAYOUT
    if want and r.layout ~= want then
      D.notify(string.format('The module sends counters in a layout this script does not know (%s, expected %s).',
                             tostring(r.layout), tostring(want)))
      return false
    end
    local status = bridge.get_status()
    local state = status and V.status(status).stateName or '?'
    log(string.format('co-op counters: time=%s ms, module state=%s, generation=%s', tostring(r.now_ms), tostring(state),
                      tostring(store.generation)))
    local count = 0
    for _, f in ipairs(C.schema.structs.ilc_counters.fields) do
      if f.name ~= 'header' and f.kind == 'VAL' then
        log(string.format('%s=%s', f.name, tostring(r[f.name])))
        count = count + 1
      end
    end
    return true, count
  end

  local STALL_TEXT = {
    CONFLICT = 'The developer setting has not been applied yet. Wait a moment and try again.',
    NOT_READY = 'The co-op module is not running, so there is nothing to stall.',
    BUSY = 'A stall is already running. Wait for it to end.',
  }

  -- Asks first, then stalls the co-op thread for STALL_MS (the module answers with a result event when it wakes).
  function F.ask_stall()
    if not store.state.diagnostics.developer then D.notify('Turn Developer options on first.') return end
    if not bridge.is_ready() then D.notify('The co-op module is not running.') return end
    D.ask(string.format('The co-op module stops for %d seconds; the game plays as stock meanwhile.', M.STALL_MS // 1000),
          {'Stall', 'Cancel'}, function(i) if i == 0 then F.start_stall() end end)
  end

  function F.start_stall()
    local rid, err = send('STALL_COOP_THREAD', 0, M.STALL_MS, 0)
    if not rid then D.notify(STALL_TEXT[err] or errText(err)) return end
    F.stall = {rid = rid, deadline = F.now + M.STALL_WAIT_MS}
    log(string.format('co-op stall requested: %d ms', M.STALL_MS))
  end

  local function onStallResult(ev)
    local s = F.stall
    if not s or ev.generation ~= s.rid then return false end
    F.stall = nil
    log(string.format('co-op stall done: %s ms', tostring(ev.b)))
    local ok = F.dump_counters()
    D.notify(string.format('The stall ended after %s ms.%s', tostring(ev.b), ok and ' The counters were written to the log.' or ''))
    return true
  end

  local function onStallRejected(ev)
    local s = F.stall
    if not s or ev.generation ~= s.rid then return false end
    F.stall = nil
    local name = tostring(bridge.describe_error(ev.code))
    log('co-op stall refused: ' .. name)
    D.notify(STALL_TEXT[name] or errText(name))
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
        elseif kind == 'COMMAND_RESULT' then
          onStallResult(ev)
        elseif kind == 'COMMAND_REJECTED' then
          local p = F.pending
          if onStallRejected(ev) then
            -- handled
          elseif p and ev.generation == p.rid then
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
    if F.stall and now >= F.stall.deadline then
      log('co-op stall: no result came back')
      F.stall = nil
    end
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
