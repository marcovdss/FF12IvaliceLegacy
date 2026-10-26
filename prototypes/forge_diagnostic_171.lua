-- Offline candidate for Forge's supported config/functions/171.lua path.
-- This observer grants no effects and registers no save-data writer.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/171.lua', 't', _ENV))()
local Diagnostic = assert(loadfile('scripts/IvaliceLegacy/clan_diagnostic.lua', 't', _ENV))()

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

local observer = Diagnostic.new({
  legacyMode = function()
    -- Forge's own consumers and this candidate require the pinned fixed mapping.
    return expected(0x120000, 'MZ', false)
      and expected(0x120180, 'PE\0\0\x64\x86', false)
      and expected(0xD113D4, 'LEGCY_\0', false)
      and expected(0xD12200, '%sLEGCY_%03d\0', true)
  end,
  identity = function(caster)
    local id = caster.identifier
    local root = memory.u64[0x02EBF190]
    if type(root) ~= 'number' or root <= 0 or root % 1 ~= 0
      or type(id) ~= 'number' or id % 1 ~= 0 or id < 0 or id > 5
      or caster.address ~= root + 8 + id * 0x1C8 then return nil end
    return {actorId=id, kind='main-party'}
  end,
  emit = function(line) print(line) end,
})

-- Unknown callback order is deliberately conservative. If this callback comes
-- after the path callback, the session stays closed until another valid load.
-- No namespace-only inference is sufficient to accept a loaded campaign.
local resetRegistered = event.registerEventSync('onSaveLoad', function()
  observer:reset()
  print('Ivalice Legacy diagnostic: native load reset; bonuses=off')
  return true
end)
-- Clan rank, marks and points, logged once per load so they can be checked
-- against the Clan Primer during normal play. See clan_rank.lua for sources.
-- Optional: a missing or broken reader only changes the log line.
local clanChunk = loadfile('scripts/IvaliceLegacy/clan_rank.lua', 't', _ENV)
local clanLoaded, ClanRank = pcall(clanChunk or error)
if not clanLoaded or type(ClanRank) ~= 'table' then ClanRank = nil end
local clanMemory = ClanRank and {
  u8 = function(address) return memory.u8[address] end,
  u32 = function(address) return memory.u32[address] end,
  marks = function() return memory.execute(ClanRank.MARKS_FN, arg.s32) end,
}

local function clanLine()
  if not ClanRank then return 'clan=missing' end
  local ok, clan, why = pcall(ClanRank.read, clanMemory)
  if not ok then return 'clan=unreadable' end
  if not clan then return 'clan=refused(' .. tostring(why) .. ')' end
  return string.format('clanRank=%d marks=%d clanPoints=%d den=%d',
    clan.rank, clan.marks, clan.points, clan.den)
end

local pathRegistered = event.registerLoadHandler('IvaliceLegacyDiagnostic', function(path)
  observer:reset()
  local ok, accepted = pcall(observer.load, observer, path)
  if not ok or accepted ~= true then
    observer:reset()
    print('Ivalice Legacy diagnostic: load path rejected; bonuses=off')
    return
  end
  print('Legacy diagnostic: ' .. clanLine() .. ' bonuses=off')
end)
local newGameRegistered = event.registerEventSync('onNewGameStart', function()
  observer:reset()
  print('Ivalice Legacy diagnostic: new campaign reset; bonuses=off')
  return true
end)
if resetRegistered ~= true or pathRegistered ~= true or newGameRegistered ~= true then
  print('Ivalice Legacy diagnostic: callback registration failed; observer disabled; bonuses=off')
  return original
end
print('Ivalice Legacy diagnostic: Forge171 observer ready; bonuses=off')
return observer:wrap(original)
