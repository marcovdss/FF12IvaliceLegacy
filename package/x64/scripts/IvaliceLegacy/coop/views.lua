-- Ivalice Legacy co-op: plain-Lua views of the native replies the Mod Menu page reads (status, seats, devices, events).
--
-- Like store.lua, this file never names a native record field. Fields are addressed by their position in the generated
-- schema (field order is part of the ABI), and tests/test_coop_lua_positions.py checks the kind and range at each
-- position, so a rename in the schema cannot silently break the page. The views use our own vocabulary.
local M = {}

-- Field positions inside the schema structs (0-based, the header is 0 where there is one).
local STATUS = {state = 1, reason = 2, buildFlags = 3, acceptedGeneration = 5, acceptedDigest = 6, appliedGeneration = 7,
                appliedDigest = 8, stagedPending = 9, eventsPending = 11, lastRejectCode = 18, lastRejectField = 19,
                appliedSubsystems = 21, reasonDetail = 22, bootFlags = 23}
local SEATS = {sessionState = 1, epoch = 2, count = 3, seats = 5}
local SEATVIEW = {seat = 0, flags = 1, configuredCharacter = 2, liveMember = 3, deviceKind = 4, xinputSlot = 5, colour = 6,
                  uiSounds = 7, generation = 8}
local DEVICES = {serviceState = 1, inventorySession = 2, ageMs = 3, count = 4, entries = 5}
local ENTRY = {entryId = 0, kind = 1, xinputSlot = 2, connected = 3, ownerSeat = 4, flags = 5, sdl = 7}
local EVENT = {seq = 0, kind = 1, seat = 2, generation = 4, code = 5, field = 6, location = 7, a = 8, b = 9}
local IDENTITY = {guid = 0, uid = 1, ordinal = 2, hasUid = 3, nameLen = 4, name = 5}
M.POSITIONS = {status = STATUS, seats = SEATS, seatView = SEATVIEW, devices = DEVICES, entry = ENTRY, event = EVENT,
               identity = IDENTITY}
M.STRUCTS = {status = 'ilc_status', seats = 'ilc_seats_reply', seatView = 'ilc_seat_view', devices = 'ilc_devices_reply',
             entry = 'ilc_device_entry', event = 'ilc_event', identity = 'ilc_device_identity'}

local function hex(s) return (s:gsub('.', function(c) return string.format('%02x', c:byte()) end)) end

-- C: the codec. Returns the view functions.
function M.new(C)
  local E = C.enums
  local V = {}

  local function get(kind, rec, key)
    return rec[C.field_at(M.STRUCTS[kind], M.POSITIONS[kind][key])]
  end

  -- {guid (hex), uid (hex, optional), ordinal, name}: the identity of an SDL pad in our state vocabulary
  function V.identity(rec)
    local out = {ordinal = get('identity', rec, 'ordinal'), guid = hex(get('identity', rec, 'guid'))}
    if get('identity', rec, 'hasUid') == 1 then out.uid = hex(get('identity', rec, 'uid')) end
    out.name = get('identity', rec, 'name'):sub(1, get('identity', rec, 'nameLen'))
    return out
  end

  function V.status(rec)
    local out = {}
    for k in pairs(STATUS) do out[k] = get('status', rec, k) end
    out.stateName = C.name_of('state', out.state)
    return out
  end

  -- seats: {sessionState, epoch, seats = {[1..4] = {seat, flags, configuredCharacter, liveMember, deviceKind, ...}}}
  function V.seats(rec)
    local out = {sessionState = get('seats', rec, 'sessionState'), epoch = get('seats', rec, 'epoch'), seats = {}}
    local list = get('seats', rec, 'seats')
    for i = 1, math.min(get('seats', rec, 'count'), #list) do
      local v = {}
      for k in pairs(SEATVIEW) do v[k] = get('seatView', list[i], k) end
      local f = v.flags
      v.hasDevice, v.hasKeyboard = f & E.seat_flag.HAS_DEVICE ~= 0, f & E.seat_flag.HAS_KBM ~= 0
      v.parked, v.playing, v.locked = f & E.seat_flag.PARKED ~= 0, f & E.seat_flag.PLAYING ~= 0, f & E.seat_flag.LOCKED ~= 0
      v.live, v.benched = f & E.seat_flag.LIVE ~= 0, f & E.seat_flag.BENCHED ~= 0
      out.seats[v.seat] = v
    end
    return out
  end

  -- devices: {serviceState, session, ageMs, ready, entries = {{id, kind, slot, connected, owner, p1Locked, identity, name}}}
  function V.devices(rec)
    local out = {serviceState = get('devices', rec, 'serviceState'), session = get('devices', rec, 'inventorySession'),
                 ageMs = get('devices', rec, 'ageMs'), entries = {}}
    out.ready = out.serviceState == E.service_state.READY
    local list = get('devices', rec, 'entries')
    for i = 1, math.min(get('devices', rec, 'count'), #list) do
      local r = list[i]
      local e = {id = get('entry', r, 'entryId'), kind = C.name_of('device_kind', get('entry', r, 'kind')),
                 slot = get('entry', r, 'xinputSlot'), connected = get('entry', r, 'connected') == 1,
                 owner = get('entry', r, 'ownerSeat'), p1Locked = get('entry', r, 'flags') & 1 ~= 0}
      if e.kind == 'SDL' then
        e.identity = V.identity(get('entry', r, 'sdl'))
        e.name = e.identity.name ~= '' and e.identity.name or 'Controller'
      else
        e.name = 'XInput ' .. (e.slot + 1)
      end
      out.entries[#out.entries + 1] = e
    end
    return out
  end

  -- event: {seq, kind (name), seat, generation, code, field, location, a, b}
  function V.event(rec)
    local out = {}
    for k in pairs(EVENT) do out[k] = get('event', rec, k) end
    out.kindName = C.name_of('event_kind', out.kind)
    return out
  end

  -- PAIRING_RESULT: a = outcome | other seat << 8 | learned XInput slot << 16 (0xFF none); b = entry id
  function V.pairing(ev)
    local slot = (ev.a >> 16) & 0xFF
    return {outcome = C.name_of('pairing_outcome', ev.a & 0xFF), otherSeat = (ev.a >> 8) & 0xFF,
            learnedSlot = slot ~= 0xFF and slot or nil, entry = ev.b, request = ev.generation, seat = ev.seat}
  end

  -- CAPTURE_RESULT: a = outcome | action << 8 | slot << 16; b = binding kind | code << 8 (when captured)
  function V.capture(ev)
    return {outcome = C.name_of('capture_outcome', ev.a & 0xFF), action = C.name_of('action', (ev.a >> 8) & 0xFF),
            slot = (ev.a >> 16) & 0xFF, kind = C.name_of('binding_kind', ev.b & 0xFF), code = (ev.b >> 8) & 0xFF,
            request = ev.generation, seat = ev.seat}
  end

  return V
end

return M
