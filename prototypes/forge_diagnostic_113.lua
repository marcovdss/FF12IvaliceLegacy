-- Forge config/functions/113.lua (damage result, every physical and magick hit): Legacy's damage floor and spear
-- Pierce around the unchanged Forge function, plus the foe-profile observer, which only reads the target and prints
-- one line per new foe. 0.1.23 removed the charge-pushback and attack-area test probes (the pushback probe wrote foe
-- charge timers in live play).
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/113.lua', 't', _ENV))()
local Diagnostic = assert(loadfile('scripts/IvaliceLegacy/foe_profile_diagnostic.lua', 't', _ENV))()
-- Optional module: if the file is missing or fails to load, damage stays Forge's own.
local function optional(path)
  local chunk = loadfile(path, 't', _ENV)
  if not chunk then return nil end
  local ok, module = pcall(chunk)
  return ok and type(module) == 'table' and module or nil
end
-- The damage formula (damage_formula.lua): attack - defense, never below a quarter of the attack, for every hit. If the file is
-- missing or broken, damage stays Forge's native attack - defense.
local Damage = optional('scripts/IvaliceLegacy/damage_formula.lua')
if Damage and type(Damage.wrap) == 'function' then
  original = Damage.wrap(original)
else
  Damage = nil
end
-- Family Pierce (prototypes/family_identities.lua): a spear hit ignores part of Defence, outside the
-- floor wrapper so the floor judges the pierced Defence, and restores the native fields afterwards.
local Family = rawget(_G, 'IvaliceLegacyFamily')
if not Family then
  local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if ok and type(mod) == 'table' and mod.register() == true then
    Family = mod
    rawset(_G, 'IvaliceLegacyFamily', Family)
  end
end
if Family then
  original = Family.pierceWrap(original)
else
  print('Legacy Family: module not loaded; Forge 113 carries the damage formula only')
end

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

-- One hit after Forge 113 ran: base damage dealt and the target's level/max HP; foe names from the area unit table.
local function hit(formula, caster, target)
  local function name(side, unit)
    if unit.type ~= 1 then return nil end
    local work = side.battleUnitWork
    if work.address == 0 then return nil end
    return work.ardUnit.name
  end
  return {story = story(), action = formula.action, casterType = caster.type, targetType = target.type,
          casterName = name(formula.caster, caster), targetName = name(formula.target, target),
          targetLevel = target.level, targetMaxHp = target.maxHp, removed = formula.target.removedHp}
end

-- 0.1.32: the per-hit charge samples are no longer logged (they crowded everything else out of the session log).
local observer = Diagnostic.new({profile = profile, hit = hit, emit = function(line) print(line) end})
local resetRegistered = event.registerEventSync('onSaveLoad', function()
  observer:reset()
  return true
end)
if resetRegistered ~= true then
  print('Legacy foe profile: callback registration failed; observer disabled; bonuses=off')
  return original
end
print('Legacy foe profile: diagnostic ' .. Diagnostic.version .. ' Forge113 observer ready; bonuses=off')
print(Damage and ('Legacy damage: formula ' .. Damage.version .. ' ready (attack - defense; floor attack/' .. Damage.FLOOR_DIVISOR .. ', foes on the party attack/' .. Damage.FOE_FLOOR_DIVISOR .. ')')
  or 'Legacy damage: formula not loaded; native attack - defense')
local observed = observer:wrap(original)
return observed
