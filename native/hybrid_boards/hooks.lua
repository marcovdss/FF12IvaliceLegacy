-- Transactional patch primitive. Requiring this module changes no memory.
-- Caller must install before the affected game UI can run, and retain native
-- code/state allocations until every detour is verifiably removed.
-- Rollback also requires affected code/callbacks to be quiescent. The returned
-- patchesRestored flag proves byte restoration only, never thread quiescence
-- or safety of freeing an allocation that an in-flight call may still use.
local M = {}
local function equal(a, b)
  if type(a) ~= 'table' or #a ~= #b then return false end
  for i = 1, #b do if a[i] ~= b[i] then return false end end
  return true
end
local function bytes(value)
  if type(value) ~= 'table' or #value < 5 or #value > 32 then return false end
  for i = 1, #value do
    if math.type(value[i]) ~= 'integer' or value[i] < 0 or value[i] > 255 then return false end
  end
  return true
end

function M.install(memory, patches)
  if type(patches) ~= 'table' or #patches == 0 then return nil, 'empty patch plan' end
  local plan = {}
  for i, patch in ipairs(patches) do
    if type(patch) ~= 'table' or math.type(patch.address) ~= 'integer' or patch.address <= 0
      or patch.address > math.maxinteger - 32 or not bytes(patch.original)
      or not bytes(patch.replacement) or #patch.original ~= #patch.replacement then
      return nil, 'invalid patch plan'
    end
    local entry = {address = patch.address, original = {}, replacement = {}}
    for j = 1, #patch.original do
      entry.original[j], entry.replacement[j] = patch.original[j], patch.replacement[j]
    end
    plan[i] = entry
  end
  table.sort(plan, function(a, b) return a.address < b.address end)
  for i, patch in ipairs(plan) do
    if i > 1 and plan[i-1].address + #plan[i-1].original > patch.address then
      return nil, 'overlapping patch plan'
    end
    local ok, current = pcall(memory.readArray, patch.address, #patch.original)
    if not ok or not equal(current, patch.original) then return nil, 'original bytes differ' end
  end
  local attempted = 0
  local handle = {active = false, patchesRestored = true}
  function handle.rollback()
    local restored = true
    for i = attempted, 1, -1 do
      local patch = plan[i]
      local ok, current = pcall(memory.readArray, patch.address, #patch.original)
      if ok and equal(current, patch.original) then
        -- Already restored; no write needed.
      elseif ok and equal(current, patch.replacement) then
        pcall(memory.writeArray, patch.address, patch.original)
        local readOK, actual = pcall(memory.readArray, patch.address, #patch.original)
        if not readOK or not equal(actual, patch.original) then restored = false end
      else
        -- A partial write or another owner cannot be distinguished safely.
        -- Retain executable allocations; never free a possible branch target.
        restored = false
      end
    end
    handle.active, handle.patchesRestored = not restored, restored
    return restored
  end
  for i, patch in ipairs(plan) do
    attempted = i
    handle.patchesRestored = false
    local ok, written = pcall(memory.writeArray, patch.address, patch.replacement)
    local readOK, actual = pcall(memory.readArray, patch.address, #patch.replacement)
    if not ok or written ~= true or not readOK or not equal(actual, patch.replacement) then
      handle.rollback()
      return nil, 'patch write failed', handle
    end
  end
  handle.active = true
  return handle
end

return M
