-- Clan training ledger: which Clan lines each party member has unlocked for
-- each full job identity, kept per campaign in the Lua loader's JSON sidecar
-- (event.registerSaveHandler / registerLoadHandler). A save without our
-- sidecar loads as an empty ledger, so progress never leaks between saves.
local M = {}

M.SCHEMA = 1
M.HANDLER_ID = "IvaliceLegacyClan"
M.MAX_LINES = 64

local function integer(v, lo, hi)
  return math.type(v) == "integer" and v >= lo and v <= hi
end

-- Canonical class key: "8" for a base job, "a:b" (a < b) for a pair.
function M.classKey(job1, job2)
  if not integer(job1, 0, 11) then return nil end
  if job2 == nil or job2 == 255 then return string.format("%d", job1) end
  if not integer(job2, 0, 11) or job1 == job2 then return nil end
  if job2 < job1 then job1, job2 = job2, job1 end
  return string.format("%d:%d", job1, job2)
end

local function validKey(key)
  local a, b = key:match("^(%d+):(%d+)$")
  if a then
    a, b = math.tointeger(tonumber(a)), math.tointeger(tonumber(b))
    return a ~= nil and b ~= nil and a < b and b <= 11 and key == string.format("%d:%d", a, b)
  end
  local single = math.tointeger(tonumber(key))
  return single ~= nil and single >= 0 and single <= 11 and key == string.format("%d", single)
end

local function validLine(id)
  return type(id) == "string" and #id <= 64 and id:match("^[a-z0-9_.-]+$") ~= nil
end

-- Plain dense array check (no holes, no extra keys).
local function denseArray(t)
  if type(t) ~= "table" or getmetatable(t) ~= nil then return nil end
  local count = 0
  for k in pairs(t) do
    if math.type(k) ~= "integer" or k < 1 then return nil end
    count = count + 1
  end
  for i = 1, count do if t[i] == nil then return nil end end
  return count
end

function M.empty()
  return {schema = M.SCHEMA, characters = {}}
end

-- Returns a fresh validated ledger, or nil and a reason.
function M.validate(data)
  if type(data) ~= "table" then return nil, "not a table" end
  if data.schema ~= M.SCHEMA then return nil, "unsupported schema" end
  if type(data.characters) ~= "table" then return nil, "characters missing" end
  local ledger = M.empty()
  -- The sidecar's JSON round trip may return numeric-looking keys as
  -- integers, so both forms are accepted and normalized to strings.
  for actorKey, classes in pairs(data.characters) do
    local id = math.type(actorKey) == "integer" and actorKey
      or (type(actorKey) == "string" and math.tointeger(tonumber(actorKey))) or nil
    if id == nil or id < 0 or id > 5 or (type(actorKey) == "string" and actorKey ~= string.format("%d", id)) then
      return nil, "bad character key"
    end
    local actor = string.format("%d", id)
    if type(classes) ~= "table" then return nil, "bad class table" end
    local copy = {}
    for rawKey, lines in pairs(classes) do
      local key = math.type(rawKey) == "integer" and string.format("%d", rawKey) or rawKey
      if type(key) ~= "string" or not validKey(key) then return nil, "bad class key" end
      local count = denseArray(lines)
      if count == nil or count > M.MAX_LINES then return nil, "bad line list" end
      local seen, list = {}, {}
      for i = 1, count do
        local line = lines[i]
        if not validLine(line) or seen[line] then return nil, "bad or duplicate line" end
        seen[line] = true
        list[i] = line
      end
      if count > 0 then copy[key] = list end
    end
    if next(copy) then ledger.characters[actor] = copy end
  end
  return ledger
end

function M.owns(ledger, actorId, classKey, lineId)
  if type(ledger) ~= "table" or not integer(actorId, 0, 5) then return false end
  local classes = ledger.characters[string.format("%d", actorId)]
  local lines = classes and classes[classKey]
  if not lines then return false end
  for _, line in ipairs(lines) do
    if line == lineId then return true end
  end
  return false
end

-- Returns a new ledger with the line added; the input is not modified.
function M.grant(ledger, actorId, classKey, lineId)
  assert(integer(actorId, 0, 5), "actor must be a main party member 0-5")
  assert(type(classKey) == "string" and validKey(classKey), "invalid class key")
  assert(validLine(lineId), "invalid line id")
  local copy = assert(M.validate(ledger))
  local actor = string.format("%d", actorId)
  copy.characters[actor] = copy.characters[actor] or {}
  local lines = copy.characters[actor][classKey] or {}
  for _, line in ipairs(lines) do
    if line == lineId then return copy end
  end
  assert(#lines < M.MAX_LINES, "too many lines for one class")
  lines[#lines + 1] = lineId
  copy.characters[actor][classKey] = lines
  return copy
end

-- Binds the ledger to the loader's save/load handlers.
-- deps.isLegacySave(path) -> true for this campaign's LEGCY_ddd saves
-- deps.log(text)
function M.session(deps)
  local session = {ledger = nil, campaign = nil, unreadable = false}

  function session.load(path, data)
    session.ledger, session.campaign, session.unreadable = nil, nil, false
    if not deps.isLegacySave(path) then return end
    session.campaign = path:match("([^/\\]+)$")
    if data == nil then
      session.ledger = M.empty()
      return
    end
    local ledger, reason = M.validate(data)
    if ledger then
      session.ledger = ledger
    else
      -- Keep the stored sidecar untouched (save returns nil) until a new
      -- grant replaces it, so an unreadable ledger is never silently wiped.
      session.ledger, session.unreadable = M.empty(), true
      deps.log("Clan ledger for " .. session.campaign .. " unreadable (" .. reason .. "); kept on disk, not loaded")
    end
  end

  function session.newGame()
    session.ledger, session.campaign, session.unreadable = M.empty(), nil, false
  end

  function session.grant(actorId, classKey, lineId)
    if session.ledger == nil then return false end
    session.ledger = M.grant(session.ledger, actorId, classKey, lineId)
    session.unreadable = false
    return true
  end

  function session.owns(actorId, classKey, lineId)
    return M.owns(session.ledger, actorId, classKey, lineId)
  end

  -- Returning nil keeps whatever the sidecar already holds.
  function session.save(path)
    if not deps.isLegacySave(path) or session.ledger == nil or session.unreadable then return nil end
    session.campaign = path:match("([^/\\]+)$")
    return assert(M.validate(session.ledger))
  end

  return session
end

return M
