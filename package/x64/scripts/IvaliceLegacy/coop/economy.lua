-- Ivalice Legacy co-op: the MP economy's parameters for the co-op module's battle menus.
--
-- Legacy's MP economy (scripts/IvaliceLegacy/mp_economy.lua 0.3.0) charges a spell what the game charges: its CAP is 1, so
-- there is no Magick surcharge any more (earlier versions charged up to CAP times the price). The co-op menus show the
-- MP a cast really takes, so the module still reads the rule's version, DIVISOR and CAP. They travel in
-- the settings record (ilc_economy: DIVISOR and CAP times 100). Without the file, or with values the record cannot
-- carry, the economy is reported absent and the menus show the game's own costs.
local M = {}

M.PATH = 'scripts/IvaliceLegacy/mp_economy.lua'

local function byte(v)
  v = tonumber(v)
  if v == nil or v ~= math.floor(v) or v < 0 or v > 255 then return nil end
  return math.tointeger(v)
end

local function x100(v, lo, hi)
  if type(v) ~= 'number' or v ~= v then return nil end
  local n = math.floor(v * 100 + 0.5)
  if n < lo or n > hi then return nil end
  return math.tointeger(n)
end

-- The economy module's table -> {major, minor, patch, divisor, cap} (x100), or nil.
function M.params(E)
  if type(E) ~= 'table' or type(E.version) ~= 'string' then return nil end
  local a, b, c = E.version:match('^(%d+)%.(%d+)%.(%d+)$')
  local major, minor, patch = byte(a), byte(b), byte(c)
  local divisor, cap = x100(E.DIVISOR, 100, 25500), x100(E.CAP, 100, 1600)
  if not (major and minor and patch and divisor and cap) then return nil end
  return {major = major, minor = minor, patch = patch, divisor = divisor, cap = cap}
end

-- A provider for store.create's `economy` option: reads the file once (through `loader`, default loadfile) and
-- answers the same table every time. Any failure answers nil.
function M.provider(loader, env)
  local done, found = false, nil
  return function()
    if not done then
      done = true
      local ok, value = pcall(function()
        local chunk = (loader or loadfile)(M.PATH, 't', env)
        if not chunk then return nil end
        return M.params(chunk())
      end)
      found = ok and value or nil
    end
    return found
  end
end

return M
