-- Candidate for Forge's supported config/functions/66.lua path: the Hunter
-- "Pursuit" pilot. It loads the installed original Forge66 rather than
-- redistributing it, and registers no save-data writer.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/66.lua', 't', _ENV))()
local Pursuit = assert(loadfile('scripts/IvaliceLegacy/clan_pursuit.lua', 't', _ENV))()

local function expected(address, text, wide)
  local count = #text * (wide and 2 or 1)
  local raw = memory.readArray(address, count)
  if type(raw) ~= 'table' and type(raw) ~= 'string' then return false end
  if #raw ~= count then return false end
  for i = 1, count do
    local byte = type(raw) == 'string' and raw:byte(i) or raw[i]
    local value = wide and (i % 2 == 0 and 0 or text:byte((i + 1) // 2)) or text:byte(i)
    if byte ~= value then return false end
  end
  return true
end

local function legacyMode()
  return expected(0x120000, 'MZ', false)
    and expected(0x120180, 'PE\0\0\x64\x86', false)
    and expected(0xD113D4, 'LEGCY_\0', false)
    and expected(0xD12200, '%sLEGCY_%03d\0', true)
end

-- Active only after a strict LEGCY_ddd load in Legacy mode; any native load
-- or new game closes it first (same gate as the Clan diagnostic).
local campaign = nil
local reported = 0
local REPORT_LIMIT = 32

local resetRegistered = event.registerEventSync('onSaveLoad', function()
  campaign, reported = nil, 0
  return true
end)
local pathRegistered = event.registerLoadHandler('IvaliceLegacyPursuit', function(path)
  campaign, reported = nil, 0
  if type(path) ~= 'string' or path:find('[%z\r\n]') then return end
  local name = path:match('([^/\\]+)$')
  if name and name:match('^LEGCY_%d%d%d$') and legacyMode() then
    campaign = name
    print('Legacy Pursuit: campaign=' .. name .. ' active')
  end
end)
local newGameRegistered = event.registerEventSync('onNewGameStart', function()
  campaign, reported = nil, 0
  return true
end)
if resetRegistered ~= true or pathRegistered ~= true or newGameRegistered ~= true then
  print('Legacy Pursuit: callback registration failed; pilot disabled')
  return original
end

local deps = {
  active = function() return campaign ~= nil and legacyMode() end,
  context = function(caster)
    local id = caster.identifier
    local root = memory.u64[0x02EBF190]
    if type(root) ~= 'number' or root <= 0 or root % 1 ~= 0
      or type(id) ~= 'number' or id % 1 ~= 0 or id < 0 or id > 5
      or caster.address ~= root + 8 + id * 0x1C8 then return nil end
    return {actorId = id, kind = 'main-party'}
  end,
  weaponCategory = function(caster)
    local weapon = caster.weapon
    if type(weapon) ~= 'number' or weapon < 0x1000 or weapon >= 0xFFFF then return nil end
    local record = bpack.section13[weapon - 0x1000]
    return record and record.category
  end,
  report = function(caster, ailment, before, after)
    if reported >= REPORT_LIMIT then return end
    reported = reported + 1
    print(string.format('Legacy Pursuit: campaign=%s actor=%d weapon=%d ailment=%s power=%.3f->%.3f',
      tostring(campaign), caster.identifier, caster.weapon, ailment, before, after))
  end,
}

print('Legacy Pursuit: Forge66 pilot ready (Hunter + dagger vs hindered target, +25%)')
return Pursuit.wrap66(original, deps)
