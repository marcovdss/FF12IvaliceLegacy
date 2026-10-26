-- Offline candidate for Forge's supported config/functions/113.lua path (damage result, every physical and
-- magick hit). Wraps the unchanged Forge function; it only reads the target and prints one line per new foe.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/113.lua', 't', _ENV))()
local Diagnostic = assert(loadfile('scripts/IvaliceLegacy/foe_profile_diagnostic.lua', 't', _ENV))()

-- Story progress: the word variable shop rows test (group 3, index 0). Mirrors the native reader 0x264640:
-- valid only while the flag byte's bit 0 is set and the table pointer is non-null.
local function story()
  if memory.u8[0x02098E20] & 1 ~= 1 then return nil end
  local table = memory.u64[0x02098E58]
  if type(table) ~= 'number' or table == 0 then return nil end
  return memory.u16[table]
end

local function profile(formula, caster, target)
  if target.type ~= 1 then return nil end
  local work = formula.target.battleUnitWork
  local address = target.address
  if type(address) ~= 'number' or address <= 0 or work.address == 0 then return nil end
  return {
    foe = true,
    nameId = work.ardUnit.name,
    bestiary = work.ardClass.bestiaryIdentifier,
    classification = work.ardClass.classification,
    genus = work.ardClass.genus,
    level = target.level,
    maxHp = target.maxHp,
    str = target.strength, mag = target.magickPower, vit = target.vitality, spd = target.speed,
    atk = target.attackPower, def = target.defense, mres = target.magickResist,
    -- Battle unit keep +0x38 / +0x3C (Forge classes/getBattleUnitKeep.lua), read raw as u32 masks.
    immune = memory.u32[address + 0x38],
    innate = memory.u32[address + 0x3C],
    story = story(),
  }
end

local observer = Diagnostic.new({profile = profile, emit = function(line) print(line) end})
local resetRegistered = event.registerEventSync('onSaveLoad', function()
  observer:reset()
  return true
end)
if resetRegistered ~= true then
  print('Legacy foe profile: callback registration failed; observer disabled; bonuses=off')
  return original
end
print('Legacy foe profile: Forge113 observer ready; bonuses=off')
return observer:wrap(original)
