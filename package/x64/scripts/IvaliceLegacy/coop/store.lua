-- Ivalice Legacy co-op: the settings store (scripts/config/IvaliceLegacy/coop.json) and its push to the native module.
--
-- * Lua is the only writer of coop.json. Native never parses it live (it reads the module-enable flag once at start).
-- * The file is schema 1. Unknown keys are kept at every level of the structural containers (module, colours, each seat,
--   its bindings, diagnostics.arm), an older schema is migrated step by step (the old file is kept as coop.json.v<n>), a
--   newer schema makes the store read-only, a file that does not parse is copied to a rotating coop.json.bad[-n] sidecar
--   (ten slots, the oldest is dropped) and replaced by defaults. Nothing here can raise on bad data.
-- * Seeding is runtime-only: the package never ships coop.json (the package carries no settings file). A missing file is created from the
--   native defaults, with the module switch Off when Together is present.
-- * Defaults come from the native schema's default record, so a fresh store pushes exactly what native starts with.
-- * The in-memory state uses our own names. Record fields are addressed by their position in the schema (field order is
--   part of the ABI), never by name, so a rename of a native field cannot break this file; tests/test_coop_lua_positions.py
--   checks the kind and range at each position.
-- * Every accepted change is pushed with generation + 1 and then saved. A change the native module refuses is not kept.
local M = {}

M.SCHEMA = 1
-- Migrations: M.MIGRATIONS[n] turns a schema-n document into schema n+1 (it may edit and return its argument). Schema 1 is
-- the first release, so the table is empty; the chain machinery is exercised by tests with a pretend schema 2.
M.MIGRATIONS = {}
M.BAD_SLOTS = 10
M.PATH = 'scripts/config/IvaliceLegacy/coop.json'
M.CHARACTERS = {'VAAN', 'ASHE', 'FRAN', 'BALTHIER', 'BASCH', 'PENELO'}
M.COLOURS = {'SAPPHIRE', 'RUBY', 'PINK', 'EMERALD', 'GOLD', 'AMETHYST', 'BRONZE'}
M.SUBSYSTEMS = {'inputIsolation', 'movement', 'party', 'camera', 'firewallMenus', 'wait', 'render', 'targetsCards'}
-- our key -> the schema's `subsystem` enum name (the bit comes from the schema, not from this file)
M.SUBSYSTEM_BIT = {inputIsolation = 'INPUT_ISOLATION', movement = 'MOVEMENT', party = 'PARTY', camera = 'CAMERA',
                   firewallMenus = 'FIREWALL_MENUS', wait = 'WAIT', render = 'RENDER', targetsCards = 'TARGETS_CARDS'}

-- Field positions inside the schema structs (0-based, header = 0 where there is one).
local SET = {moduleEnabled = 1, fullRoster = 2, keyboardSeat = 3, colourMode = 4, logLevel = 5, networkMode = 6,
             aliasCount = 7, characterColour = 9, killSwitch = 11, economy = 12, seats = 13, aliases = 14,
             subsystemsArmed = 15}
local SEAT = {character = 0, colour = 1, uiSounds = 2, parked = 3, device = 5, bindings = 6}
local ECO = {present = 0, major = 1, minor = 2, patch = 3, divisor = 4, cap = 5}
local DEV = {kind = 0, slot = 1, sdl = 3}
local IDN = {guid = 0, uid = 1, ordinal = 2, hasUid = 3, nameLen = 4, name = 5}
local ALIAS = {slot = 0, sdl = 2}
M.POSITIONS = {settings = SET, seat = SEAT, economy = ECO, device = DEV, identity = IDN, alias = ALIAS}

local function copy(v)
  if type(v) ~= 'table' then return v end
  local t = {}
  for k, x in pairs(v) do t[k] = copy(x) end
  return t
end
M.copy = copy

local function same(a, b)
  if type(a) ~= type(b) then return false end
  if type(a) ~= 'table' then return a == b end
  for k, v in pairs(a) do if not same(v, b[k]) then return false end end
  for k in pairs(b) do if a[k] == nil then return false end end
  return true
end
M.same = same

local function hex(s) return (s:gsub('.', function(c) return string.format('%02x', c:byte()) end)) end
local function unhex(h, n)
  if type(h) ~= 'string' or #h ~= n * 2 or h:find('%X') then return nil end
  return (h:gsub('%x%x', function(x) return string.char(tonumber(x, 16)) end))
end

-- Unknown keys are kept, but only down to MAX_DEPTH levels: the native reader refuses documents nested 40 deep, so a
-- preserved subtree must never push the file past that. Deeper parts are dropped.
M.MAX_DEPTH = 24
local function capDepth(v, depth)
  if type(v) ~= 'table' then return v end
  local t = {}
  for k, x in pairs(v) do
    if type(x) ~= 'table' then t[k] = x
    elseif depth < M.MAX_DEPTH then t[k] = capDepth(x, depth + 1) end
  end
  return t
end

-- Integer-valued floats (what a JSON reader may hand back for 3 or 3.0) become integers; nothing else changes.
local function coerce(v)
  if type(v) == 'table' then
    for k, x in pairs(v) do v[k] = coerce(x) end
  elseif math.type(v) == 'float' then
    return math.tointeger(v) or v
  end
  return v
end

-- opts: codec (required); config (loader table with loadJson/saveJson); fs (filesystem plugin, optional);
-- io (raw file access, optional); path; log(fn) optional; economy() optional -> {major, minor, patch, divisor, cap};
-- together() optional -> true when Ivalice Together is installed (first-run seed only: module switch Off);
-- schema / migrations (tests only: a pretend newer schema and its upgrade steps).
function M.create(opts)
  local C = opts.codec
  local schema, E = C.schema, C.enums
  local config = opts.config
  local fs, rawio = opts.fs, opts.io
  local path = opts.path or M.PATH
  local log = opts.log or function() end
  local CURRENT = opts.schema or M.SCHEMA
  local ACTIVE = schema.const.ACTIVE_SEATS_MAX or 3     -- seats with a human player (fail closed: 3)
  local migrations = opts.migrations or M.MIGRATIONS
  local T = {path = path, fileStatus = 'new', unsaved = false, copy = copy, same = same,
             ACTIVE_SEATS = ACTIVE, CHARACTERS = M.CHARACTERS, COLOURS = M.COLOURS, SUBSYSTEMS = M.SUBSYSTEMS}

  local function fname(sname, idx) return (C.field_at(sname, idx)) end
  local N = {
    settings = function(k) return fname('ilc_settings', SET[k]) end,
    seat = function(k) return fname('ilc_seat_settings', SEAT[k]) end,
    economy = function(k) return fname('ilc_economy', ECO[k]) end,
    device = function(k) return fname('ilc_seat_device', DEV[k]) end,
    identity = function(k) return fname('ilc_device_identity', IDN[k]) end,
    alias = function(k) return fname('ilc_device_alias', ALIAS[k]) end,
  }

  local function enumName(ename, v) return C.name_of(ename, v) end
  local function enumVal(ename, name) return E[ename][name] end
  local function charName(v) return enumName('character', v) end
  local function isCharacter(name) return type(name) == 'string' and E.character[name] ~= nil end

  ---- record <-> state -------------------------------------------------------------------------------------------
  local function identityFromRecord(r)
    local out = {ordinal = r[N.identity('ordinal')], guid = hex(r[N.identity('guid')])}
    if r[N.identity('hasUid')] == 1 then out.uid = hex(r[N.identity('uid')]) end
    out.name = r[N.identity('name')]:sub(1, r[N.identity('nameLen')])
    return out
  end

  local function identityToRecord(s)
    local name = type(s.name) == 'string' and s.name or ''
    local rec = {[N.identity('guid')] = unhex(s.guid, 16) or string.rep('\0', 16),
                 [N.identity('ordinal')] = s.ordinal or 0,
                 [N.identity('nameLen')] = #name, [N.identity('name')] = name}
    local uid = unhex(s.uid, 16)
    rec[N.identity('hasUid')] = uid and 1 or 0
    rec[N.identity('uid')] = uid or ''
    return rec
  end

  local function bindingsFromRecord(list)
    local out = {}
    local perAction = schema.const.BINDINGS_PER_ACTION
    for action, idx in pairs(E.action) do
      local binds = {}
      for slot = 0, perAction - 1 do
        local b = list[idx * perAction + slot + 1]
        if b.kind ~= E.binding_kind.NONE then binds[#binds + 1] = {kind = enumName('binding_kind', b.kind), code = b.code} end
      end
      out[action] = binds
    end
    return out
  end

  local function bindingsToRecord(map)
    local list = {}
    local perAction = schema.const.BINDINGS_PER_ACTION
    for i = 1, schema.const.SEAT_BINDINGS do list[i] = {kind = 0, code = 0} end
    for action, idx in pairs(E.action) do
      for slot, b in ipairs(map[action] or {}) do
        if slot <= perAction then list[idx * perAction + slot] = {kind = enumVal('binding_kind', b.kind) or 0, code = b.code or 0} end
      end
    end
    return list
  end

  local function killFromRecord(list)
    local out = {}
    for _, b in ipairs(list) do
      if b.kind ~= 0 then out[#out + 1] = {kind = enumName('binding_kind', b.kind), code = b.code} end
    end
    return out
  end

  local function killToRecord(list)
    local out = {}
    for i = 1, schema.const.KILL_SWITCH_BINDINGS do
      local b = list[i]
      out[i] = b and {kind = enumVal('binding_kind', b.kind) or 0, code = b.code or 0} or {kind = 0, code = 0}
    end
    return out
  end

  local function deviceFromRecord(r)
    local d = {kind = enumName('device_kind', r[N.device('kind')]), slot = r[N.device('slot')]}
    if r[N.device('kind')] == E.device_kind.SDL then d.sdl = identityFromRecord(r[N.device('sdl')]) end
    return d
  end

  local function deviceToRecord(d)
    local r = {[N.device('kind')] = enumVal('device_kind', d.kind) or 0, [N.device('slot')] = d.slot or 0}
    if d.sdl then r[N.device('sdl')] = identityToRecord(d.sdl) end
    return r
  end

  local function fromRecord(rec)
    local st = {schema = CURRENT}
    st.module = {enabledAtStart = rec[N.settings('moduleEnabled')] == 1}
    st.fullRoster = rec[N.settings('fullRoster')] == 1
    st.keyboardSeat = rec[N.settings('keyboardSeat')]
    st.colours = {mode = enumName('colour_mode', rec[N.settings('colourMode')]), characters = {}}
    for i, name in ipairs(M.CHARACTERS) do
      st.colours.characters[name] = enumName('colour', rec[N.settings('characterColour')][i])
    end
    st.logLevel = rec[N.settings('logLevel')] == 1 and 'VERBOSE' or 'NORMAL'
    st.killSwitch = killFromRecord(rec[N.settings('killSwitch')])
    st.seats = {}
    for i, s in ipairs(rec[N.settings('seats')]) do
      st.seats[i] = {character = charName(s[N.seat('character')]), colour = enumName('colour', s[N.seat('colour')]),
                     uiSounds = s[N.seat('uiSounds')] == 1, parked = s[N.seat('parked')] == 1,
                     device = deviceFromRecord(s[N.seat('device')]), bindings = bindingsFromRecord(s[N.seat('bindings')])}
    end
    st.aliases = {}
    for i = 1, rec[N.settings('aliasCount')] do
      local a = rec[N.settings('aliases')][i]
      st.aliases[i] = {slot = a[N.alias('slot')], sdl = identityFromRecord(a[N.alias('sdl')])}
    end
    st.diagnostics = {arm = {}}
    local mask = rec[N.settings('subsystemsArmed')]
    for _, k in ipairs(M.SUBSYSTEMS) do st.diagnostics.arm[k] = (mask & E.subsystem[M.SUBSYSTEM_BIT[k]]) ~= 0 end
    return st
  end

  local defaultsState = fromRecord(C.decode('ilc_settings', C.defaults()))
  function T.defaults() return copy(defaultsState) end

  -- state -> record table. extras.economy = {major, minor, patch, divisor, cap} when the Legacy MP rule is present.
  function T.to_record(st, generation, extras)
    local rec = {header = C.header('SETTINGS', C.size('ilc_settings'), generation)}
    rec[N.settings('moduleEnabled')] = st.module.enabledAtStart and 1 or 0
    rec[N.settings('fullRoster')] = st.fullRoster and 1 or 0
    rec[N.settings('keyboardSeat')] = st.keyboardSeat
    rec[N.settings('colourMode')] = enumVal('colour_mode', st.colours.mode)
    rec[N.settings('logLevel')] = st.logLevel == 'VERBOSE' and 1 or 0
    rec[N.settings('networkMode')] = 0
    local mask = 0
    for _, k in ipairs(M.SUBSYSTEMS) do
      if st.diagnostics.arm[k] then mask = mask | E.subsystem[M.SUBSYSTEM_BIT[k]] end
    end
    rec[N.settings('subsystemsArmed')] = mask
    local cc = {}
    for i, name in ipairs(M.CHARACTERS) do cc[i] = enumVal('colour', st.colours.characters[name]) end
    rec[N.settings('characterColour')] = cc
    rec[N.settings('killSwitch')] = killToRecord(st.killSwitch)
    local eco = extras and extras.economy
    rec[N.settings('economy')] = eco and {
      [N.economy('present')] = 1, [N.economy('major')] = eco.major, [N.economy('minor')] = eco.minor,
      [N.economy('patch')] = eco.patch, [N.economy('divisor')] = eco.divisor, [N.economy('cap')] = eco.cap} or {}
    local seats = {}
    for i = 1, schema.const.SEATS do
      local s = st.seats[i]
      seats[i] = {[N.seat('character')] = enumVal('character', s.character), [N.seat('colour')] = enumVal('colour', s.colour),
                  [N.seat('uiSounds')] = s.uiSounds and 1 or 0, [N.seat('parked')] = s.parked and 1 or 0,
                  [N.seat('device')] = deviceToRecord(s.device), [N.seat('bindings')] = bindingsToRecord(s.bindings)}
    end
    rec[N.settings('seats')] = seats
    local aliases = {}
    for i, a in ipairs(st.aliases) do
      aliases[i] = {[N.alias('slot')] = a.slot, [N.alias('sdl')] = identityToRecord(a.sdl)}
    end
    rec[N.settings('aliasCount')] = #aliases
    rec[N.settings('aliases')] = aliases
    return rec
  end

  function T.encode(st, generation, extras) return C.encode('ilc_settings', T.to_record(st, generation, extras)) end

  ---- device helpers for the pairing flows ------------------------------------------------------------------------------
  -- views.lua entry ({kind, slot, identity}) -> the state's device table. Names are reduced to printable ASCII so the
  -- stored value always passes the loader's own UTF-8 check.
  function T.device_from_entry(e)
    if e.kind == 'SDL' then
      local id = copy(e.identity)
      id.name = (id.name or ''):gsub('[^ -~]', '?'):sub(1, schema.const.NAME_MAX)
      return {kind = 'SDL', slot = 0, sdl = id}
    end
    return {kind = 'XINPUT', slot = e.slot}
  end

  local function sameIdentity(a, b)
    if a.uid and b.uid then return a.uid == b.uid end
    return a.guid == b.guid and a.ordinal == b.ordinal
  end
  T.same_identity = sameIdentity

  function T.same_device(a, b)
    if not a or not b or a.kind ~= b.kind then return false end
    if a.kind == 'XINPUT' then return a.slot == b.slot end
    if a.kind == 'SDL' then return sameIdentity(a.sdl, b.sdl) end
    return true
  end

  -- Remember that XInput slot `slot` and the SDL pad `identity` are the same physical pad (the pairing proof learned it).
  -- An older alias for the slot or for the pad is replaced; the list is capped, the oldest goes first.
  function T.add_alias(st, slot, identity)
    local keep = {}
    for _, a in ipairs(st.aliases) do
      if a.slot ~= slot and not sameIdentity(a.sdl, identity) then keep[#keep + 1] = a end
    end
    local id = copy(identity)
    id.name = (id.name or ''):gsub('[^ -~]', '?'):sub(1, schema.const.NAME_MAX)
    keep[#keep + 1] = {slot = slot, sdl = id}
    while #keep > schema.const.ALIASES_MAX do table.remove(keep, 1) end
    st.aliases = keep
  end

  ---- validation of loaded JSON: every bad value falls back to its default --------------------------------------------
  local function isBool(v) return type(v) == 'boolean' end
  local function isInt(v, lo, hi) return math.type(v) == 'integer' and v >= lo and v <= hi end
  local function validBinding(b)
    return type(b) == 'table' and E.binding_kind[b.kind] ~= nil and b.kind ~= 'NONE' and isInt(b.code, 0, 255)
  end
  local function validIdentity(s)
    if type(s) ~= 'table' or not unhex(s.guid, 16) or not isInt(s.ordinal, 0, 255) then return false end
    if s.uid ~= nil and not unhex(s.uid, 16) then return false end
    if s.name ~= nil and (type(s.name) ~= 'string' or #s.name > schema.const.NAME_MAX or not utf8.len(s.name)) then return false end
    return true
  end
  local function validDevice(d)
    if type(d) ~= 'table' or E.device_kind[d.kind] == nil or not isInt(d.slot or 0, 0, 3) then return false end
    if d.kind == 'SDL' then return validIdentity(d.sdl) end
    return true
  end

  -- Overlay `loaded` on a copy of the defaults. Returns the state and the number of values that were repaired.
  local function normalize(loaded)
    local st, fixes = T.defaults(), 0
    local function take(ok, apply)
      if ok then apply() else fixes = fixes + 1 end
    end
    local function present(v) return v ~= nil end
    if type(loaded.module) == 'table' and present(loaded.module.enabledAtStart) then
      take(isBool(loaded.module.enabledAtStart), function() st.module.enabledAtStart = loaded.module.enabledAtStart end)
    end
    if present(loaded.fullRoster) then take(isBool(loaded.fullRoster), function() st.fullRoster = loaded.fullRoster end) end
    if present(loaded.keyboardSeat) then take(isInt(loaded.keyboardSeat, 0, ACTIVE), function() st.keyboardSeat = loaded.keyboardSeat end) end
    if present(loaded.logLevel) then
      take(loaded.logLevel == 'NORMAL' or loaded.logLevel == 'VERBOSE', function() st.logLevel = loaded.logLevel end)
    end
    if type(loaded.colours) == 'table' then
      local c = loaded.colours
      if present(c.mode) then take(E.colour_mode[c.mode] ~= nil, function() st.colours.mode = c.mode end) end
      if type(c.characters) == 'table' then
        for _, name in ipairs(M.CHARACTERS) do
          local v = c.characters[name]
          if present(v) then take(E.colour[v] ~= nil, function() st.colours.characters[name] = v end) end
        end
      end
    end
    if type(loaded.killSwitch) == 'table' then
      local list, ok = {}, #loaded.killSwitch <= schema.const.KILL_SWITCH_BINDINGS
      for _, b in ipairs(loaded.killSwitch) do ok = ok and validBinding(b); list[#list + 1] = {kind = b.kind, code = b.code} end
      take(ok, function() st.killSwitch = list end)
    end
    if type(loaded.seats) == 'table' then
      for i = 1, schema.const.SEATS do
        local s, d = loaded.seats[i], st.seats[i]
        if type(s) == 'table' then
          if present(s.character) then
            take(isCharacter(s.character), function() d.character = s.character end)
          end
          if present(s.colour) then take(E.colour[s.colour] ~= nil, function() d.colour = s.colour end) end
          if present(s.uiSounds) then take(isBool(s.uiSounds), function() d.uiSounds = s.uiSounds end) end
          if present(s.parked) then take(isBool(s.parked), function() d.parked = s.parked end) end
          if present(s.device) then take(validDevice(s.device), function() d.device = copy(s.device) end) end
          if type(s.bindings) == 'table' then
            for action in pairs(E.action) do
              local list = s.bindings[action]
              if present(list) then
                local ok = type(list) == 'table' and #list <= schema.const.BINDINGS_PER_ACTION
                if ok then for _, b in ipairs(list) do ok = ok and validBinding(b) end end
                take(ok, function()
                  local out = {}
                  for _, b in ipairs(list) do out[#out + 1] = {kind = b.kind, code = b.code} end
                  d.bindings[action] = out
                end)
              end
            end
          end
        else
          if present(s) then fixes = fixes + 1 end
        end
      end
    end
    -- seats without a human player keep no controller: a stored device there is dropped
    for i = ACTIVE + 1, schema.const.SEATS do
      local d = st.seats[i]
      if d.device.kind ~= 'NONE' then fixes = fixes + 1; d.device = {kind = 'NONE', slot = 0} end
    end
    if type(loaded.aliases) == 'table' then
      local list, ok = {}, #loaded.aliases <= schema.const.ALIASES_MAX
      for _, a in ipairs(loaded.aliases) do
        ok = ok and type(a) == 'table' and isInt(a.slot, 0, 3) and validIdentity(a.sdl)
        if ok then list[#list + 1] = copy(a) end
      end
      take(ok, function() st.aliases = list end)
    end
    if type(loaded.diagnostics) == 'table' and type(loaded.diagnostics.arm) == 'table' then
      for _, k in ipairs(M.SUBSYSTEMS) do
        local v = loaded.diagnostics.arm[k]
        if present(v) then take(isBool(v), function() st.diagnostics.arm[k] = v end) end
      end
    end
    return st, fixes
  end

  -- Whole-state sanity for a proposed change: the same domains, and the range check native will apply.
  function T.check(st, extras)
    local loaded = copy(st)
    local _, fixes = normalize(loaded)
    if fixes > 0 then return false, 'a value is out of range' end
    local errs = C.range_errors('ilc_settings', T.to_record(st, 1, extras))
    if #errs > 0 then return false, 'out of range: ' .. errs[1] end
    return true
  end

  ---- files ----------------------------------------------------------------------------------------------------------
  -- Returns the bytes, or nil and why: 'notfound' (the file does not exist), 'error' (it could not be read), or
  -- 'unknown' (no reader available, so a missing file cannot be told from an unreadable one).
  local function readRaw(p)
    if fs and fs.readFile then
      local ok, data, _, code = pcall(fs.readFile, p)
      if not ok then return nil, 'error' end
      if type(data) == 'string' then return data end
      local notFound = fs.error and fs.error.NOT_FOUND or 1
      if code == notFound then return nil, 'notfound' end
      return nil, 'error'
    end
    if rawio and rawio.open then
      local ok, f, _, errno = pcall(rawio.open, p, 'rb')
      if ok and f then
        local data = f:read('a')
        f:close()
        return data
      end
      if ok and (errno == nil or errno == 2) then return nil, 'notfound' end
      return nil, 'error'
    end
    return nil, 'unknown'
  end

  local function writeRaw(p, data)
    if fs and fs.writeFile then
      local ok, res = pcall(fs.writeFile, p, data)
      return ok and res == true
    end
    if rawio and rawio.open then
      local ok, f = pcall(rawio.open, p, 'wb')
      if ok and f then
        f:write(data)
        f:close()
        return true
      end
    end
    return false
  end

  local function exists(p)
    if fs and fs.exists then
      local ok, res = pcall(fs.exists, p)
      return ok and res == true
    end
    return readRaw(p) ~= nil
  end

  -- Rotating sidecars: coop.json.bad, .bad-1 ... .bad-<BAD_SLOTS-1>. The first free slot takes the new copy. When every slot
  -- is taken the oldest is dropped: slots shift down one place and the newest goes in the last slot. If a slot cannot be
  -- read (so a shift would lose data) only the last slot is replaced. Returns the path that holds the new copy, or nil.
  local function badName(i) return i == 0 and (path .. '.bad') or (path .. '.bad-' .. i) end

  local function keepBad(raw)
    local slots = M.BAD_SLOTS
    for i = 0, slots - 1 do
      if not exists(badName(i)) then
        if writeRaw(badName(i), raw) then return badName(i) end
        return nil
      end
    end
    local held = {}
    for i = 1, slots - 1 do
      held[i] = readRaw(badName(i))
      if held[i] == nil then
        if writeRaw(badName(slots - 1), raw) then return badName(slots - 1) end
        return nil
      end
    end
    for i = 1, slots - 1 do
      if not writeRaw(badName(i - 1), held[i]) then return nil end
    end
    if writeRaw(badName(slots - 1), raw) then return badName(slots - 1) end
    return nil
  end

  -- The on-disk document: the last document read or written, with the state laid over it. Only the structural containers
  -- are merged (their unknown keys survive); every record the schema owns (devices, aliases, binding lists, kill switch)
  -- is replaced wholesale, so a key the schema no longer uses cannot come back from an old copy.
  local MERGE = {[''] = true, module = true, colours = true, ['colours.characters'] = true, seats = true,
                 ['seats.#'] = true, ['seats.#.bindings'] = true, diagnostics = true, ['diagnostics.arm'] = true}
  local function isMap(t) return type(t) == 'table' and next(t) ~= nil and #t == 0 end
  local function overlay(base, st, where)
    local out = {}
    if isMap(base) then for k, v in pairs(base) do out[k] = copy(v) end end
    if where == 'seats' then                                   -- an array: merge element by element
      if type(base) == 'table' and #base > 0 then for i, v in ipairs(base) do out[i] = copy(v) end end
      for i, v in ipairs(st) do out[i] = overlay(base and base[i], v, 'seats.#') end
      return out
    end
    for k, v in pairs(st) do
      local sub = (where == '' and k or where .. '.' .. k)
      if type(v) == 'table' and MERGE[sub] then out[k] = overlay(out[k], v, sub) else out[k] = copy(v) end
    end
    return out
  end

  local function jsonKeys(st) return overlay(T.raw, st, '') end

  -- The loader's config API has no rename, and a rename over the Vortex-deployed link would replace the link, not the
  -- staged file. So: write a scratch copy and read it back (the live file is untouched if that fails), then write the live
  -- file and read it back. A failure keeps the previous file when the scratch stage fails, and reports 'unsaved'.
  function T.save()
    if T.readOnly then return false, 'settings are read-only this session' end
    local data = jsonKeys(T.state)
    if fs and fs.mkdirs then pcall(fs.mkdirs, (path:gsub('/[^/]*$', ''))) end
    local tmp = path .. '.tmp'
    local useTmp = fs and fs.remove
    if useTmp then
      local ok = config.saveJson(tmp, data)
      local back = ok and config.loadJson(tmp)
      if not ok or type(back) ~= 'table' then
        pcall(fs.remove, tmp)
        T.unsaved = true
        return false, 'could not write a scratch copy'
      end
    end
    local ok = config.saveJson(path, data)
    local back = ok and config.loadJson(path)
    if not ok or type(back) ~= 'table' or back.schema ~= CURRENT then
      T.unsaved = true                    -- the scratch copy stays: load() can recover from it
      return false, 'could not write coop.json'
    end
    if useTmp then pcall(fs.remove, tmp) end
    T.unsaved = false
    T.raw = data
    return true
  end

  local function usable(t)
    return type(t) == 'table' and #t == 0 and next(t) ~= nil and math.type(t.schema) == 'integer'
  end

  -- The state a brand-new file starts from: the native defaults, with the module switch Off when Together is installed.
  -- (Native itself keeps its own default, On, when the file is absent and refuses to arm next to Together on its own, so
  -- this only makes the menu and the file say what will happen.)
  local function seeded()
    local st = T.defaults()
    if opts.together then
      local ok, present = pcall(opts.together)
      if ok and present then st.module.enabledAtStart = false end
    end
    return st
  end
  T.seeded = seeded

  -- Walk a document from `from` up to CURRENT. Returns the migrated copy, or nil and why.
  local function migrate(doc, from)
    local cur = copy(doc)
    for n = from, CURRENT - 1 do
      local step = migrations[n]
      if type(step) ~= 'function' then return nil, 'no migration from schema ' .. n end
      local ok, out = pcall(step, cur)
      if not ok or type(out) ~= 'table' then return nil, 'migration from schema ' .. n .. ' failed' end
      cur = out
      cur.schema = n + 1
    end
    return cur
  end

  -- Load the file. Always leaves T.state usable. fileStatus: new | ok | repaired | migrated | reset | recovered | readonly |
  -- unreadable (the file exists but could not be read: nothing is saved this session).
  function T.load()
    T.raw, T.readOnly, T.repairs, T.badPath, T.migratedFrom = {}, false, 0, nil, nil
    local raw, why = readRaw(path)
    if raw == nil and why == 'error' then
      T.fileStatus, T.readOnly = 'unreadable', true
      T.state = T.defaults()
      log('coop.json could not be read; settings are read-only this session')
      return T.state
    end
    local loaded = coerce(config.loadJson(path))
    local tmpPath = path .. '.tmp'
    local recovered = false
    local function reset()
      T.fileStatus = 'reset'
      T.state = seeded()
      T.raw = {}
      T.save()
      log('coop.json was unreadable; defaults restored' .. (T.badPath and (', kept as ' .. T.badPath) or ''))
      return T.state
    end
    if not usable(loaded) then
      if raw == nil then                                -- no file (or no way to tell it from an unreadable one)
        local scratch = coerce(config.loadJson(tmpPath)) -- a save that was interrupted after the scratch write
        if usable(scratch) and scratch.schema == CURRENT then
          loaded, recovered = scratch, true
        else
          T.fileStatus = 'new'
          T.state = seeded()
          T.save()
          return T.state
        end
      else
        T.badPath = keepBad(raw)
        local scratch = coerce(config.loadJson(tmpPath))
        if usable(scratch) and scratch.schema == CURRENT then
          loaded, recovered = scratch, true
        else
          return reset()
        end
      end
    end
    if math.type(loaded.schema) == 'integer' and loaded.schema > CURRENT then
      T.fileStatus, T.readOnly = 'readonly', true
      T.state = normalize(loaded)
      T.raw = capDepth(loaded, 1)
      return T.state
    end
    if math.type(loaded.schema) == 'integer' and loaded.schema >= 1 and loaded.schema < CURRENT then
      local from = loaded.schema
      local moved, mwhy = migrate(loaded, from)
      if not moved then
        T.badPath = T.badPath or (raw and keepBad(raw)) or nil
        log('coop.json could not be upgraded: ' .. tostring(mwhy))
        return reset()
      end
      local sidecar = path .. '.v' .. from              -- the file as it was, kept once
      if raw and not exists(sidecar) then writeRaw(sidecar, raw) end
      loaded, T.migratedFrom = moved, from
    end
    if loaded.schema ~= CURRENT then
      T.badPath = T.badPath or (raw and keepBad(raw)) or nil
      return reset()
    end
    local st, fixes = normalize(loaded)
    T.state, T.repairs, T.raw = st, fixes, capDepth(loaded, 1)
    T.fileStatus = T.migratedFrom and 'migrated' or recovered and 'recovered' or (fixes > 0 and 'repaired' or 'ok')
    if fixes > 0 and not recovered then T.badPath = T.badPath or (raw and keepBad(raw)) or nil end
    if fixes > 0 or recovered or T.migratedFrom then T.save() end
    return st
  end

  function T.status_text()
    local s = T.fileStatus
    if s == 'reset' then return 'settings reset (file unreadable)' end
    if s == 'repaired' then return 'settings repaired (' .. T.repairs .. ' value(s) reset)' end
    if s == 'migrated' then return 'settings upgraded from version ' .. tostring(T.migratedFrom) end
    if s == 'recovered' then return 'settings recovered from the last save' end
    if s == 'unreadable' then return 'settings file could not be read (nothing is saved this session)' end
    if s == 'readonly' then return 'settings are read-only (file is from a newer version)' end
    if T.unsaved then return 'settings not saved' end
    return nil
  end

  ---- push to native --------------------------------------------------------------------------------------------------
  T.generation = 0          -- the last generation this script sent or found in sync
  T.next, T.fails = {}, {}  -- retry gates (page-clock ms) and consecutive failures, per kind: 'sync', 'save'

  -- After a failure, wait 1 s, 2 s, 4 s ... (capped at 30 s) before trying again. `now` is the caller's clock in ms.
  M.BACKOFF_MS, M.BACKOFF_CAP_MS = 1000, 30000
  local function failed(kind, now)
    local n = (T.fails[kind] or 0) + 1
    T.fails[kind] = n
    if now then T.next[kind] = now + math.min(M.BACKOFF_MS * 2 ^ (n - 1), M.BACKOFF_CAP_MS) end
  end
  local function succeeded(kind)
    T.fails[kind], T.next[kind] = nil, nil
  end
  local function gated(kind, now)
    return now ~= nil and T.next[kind] ~= nil and now < T.next[kind]
  end
  T.pushed = false
  T.lastRejection = nil

  local function extras() return {economy = opts.economy and opts.economy() or nil} end

  local function rejection(bridge, err)
    local text = tostring(err)
    local status = bridge.get_status()
    if status and status.last_reject_code ~= 0 then
      local field = C.field_name(status.last_reject_field)
      text = string.format('%s (%s)', bridge.describe_error(status.last_reject_code), field)
    end
    return text
  end

  -- STALE_GENERATION, GENERATION_CONFLICT and FAULT leave the module's state unknown to us (a FAULT may have staged the
  -- record, or left a partial reply). Re-read the module's view, then try again once.
  local RECOVERABLE = {STALE_GENERATION = true, GENERATION_CONFLICT = true, FAULT = true}

  -- The one place that writes settings to native. The bridge is ready, so HELLO has been read. Asks the module what it
  -- holds first: nothing is sent when it already holds this exact content (a restarted script, or a FAULT that had
  -- staged the record); otherwise sends accepted generation + 1. Returns true, 'in sync'|'pushed' or false, error name.
  local function send(st, bridge)
    local ex = extras()
    local digest = C.digest(T.encode(st, 1, ex))
    local err
    for _ = 1, 2 do
      local status = bridge.get_status()
      local base = T.generation
      if status then
        if status.accepted_generation > 0 and status.accepted_digest == digest then
          T.generation = status.accepted_generation
          return true, 'in sync'
        end
        base = math.max(status.accepted_generation, T.generation)
      end
      local ack
      ack, err = bridge.set_settings(T.to_record(st, base + 1, ex))
      if ack then
        T.generation = ack.accepted_generation
        return true, 'pushed'
      end
      if not RECOVERABLE[err] then return false, err end
      local hello = bridge.get_hello()
      if hello then T.generation = math.max(T.generation, hello.accepted_generation) end
    end
    return false, err
  end

  -- Called on every script start once the bridge is ready, and again if the module restarted.
  function T.sync(bridge, now)
    if not bridge.is_ready() then return false, 'bridge not ready' end
    if gated('sync', now) then return false, 'backing off' end
    local ok, how = send(T.state, bridge)
    if not ok then
      T.lastRejection = rejection(bridge, how)
      failed('sync', now)
      return false, T.lastRejection
    end
    succeeded('sync')
    T.pushed, T.lastRejection = true, nil
    return true, how
  end

  -- Apply a change. mutate(copy) edits a copy of the state. Only per-field ranges are checked here; cross-field rules
  -- belong to the native module, and its refusal is shown to the player. A refused change is not kept. Then it is saved.
  -- Returns true, or false and a reason for the player.
  -- mutate may refuse by returning false and a reason.
  function T.change(mutate, bridge)
    if T.readOnly then return false, 'settings are read-only this session' end
    local st = copy(T.state)
    local accepted, refusal = mutate(st)
    if accepted == false then return false, refusal or 'not allowed' end
    local ok, why = T.check(st, extras())
    if not ok then return false, why end
    if same(st, T.state) then return true end
    if bridge and bridge.is_ready() then
      local sent, how = send(st, bridge)
      if not sent then
        T.lastRejection = rejection(bridge, how)
        return false, 'the co-op module refused it: ' .. T.lastRejection
      end
      T.pushed, T.lastRejection = true, nil
      succeeded('sync')
    else
      T.pushed = false
    end
    T.state = st
    T.save()
    return true
  end

  -- Rewrite the file once from the state (the page asks for it when native found the file invalid at start-up although
  -- this script read it fine). Never on a read-only store. Returns true when the file was written.
  function T.force_save()
    if T.readOnly then return false end
    return (T.save())
  end

  -- At exit or on a timer: retry a failed save.
  function T.flush(now)
    if not T.unsaved or T.readOnly then return true end
    if gated('save', now) then return false end
    local ok = T.save()
    if ok then succeeded('save') else failed('save', now) end
    return ok
  end

  return T
end

return M
