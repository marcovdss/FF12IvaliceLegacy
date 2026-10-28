-- Offline candidate for Forge's supported config/functions/113.lua path (damage result, every physical and
-- magick hit). Wraps the unchanged Forge function; the observer only reads the target and prints one line per new
-- foe. The charge pushback probe is the one write: a bounded mechanism test (charge_pushback_probe.lua).
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/113.lua', 't', _ENV))()
local Diagnostic = assert(loadfile('scripts/IvaliceLegacy/foe_profile_diagnostic.lua', 't', _ENV))()
-- The two test probes are optional: if a file is missing or fails to load, that probe is off and damage is untouched.
local function optional(path)
  local chunk = loadfile(path, 't', _ENV)
  if not chunk then return nil end
  local ok, module = pcall(chunk)
  return ok and type(module) == 'table' and module or nil
end
local Pushback = optional('scripts/IvaliceLegacy/charge_pushback_probe.lua')
local Area = optional('scripts/IvaliceLegacy/attack_area_probe.lua')

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

-- Charge timers of both sides (battle unit work +0x75C idle, +0x768 current, +0x76C required, +0x770 last;
-- Forge classes/getBattleUnitWork.lua). Read after the original function, never written.
local function timers(work)
  if type(work) ~= 'table' and type(work) ~= 'userdata' then return nil end
  if work.address == 0 then return nil end
  return {current = work.currentChargeTime, required = work.requiredChargeTime,
          last = work.lastChargeTime, idle = work.idleChargeTime}
end

local function charge(formula, caster, target)
  return {clock = event.getClock and event.getClock() or nil, action = formula.action,
          casterType = caster.type, targetType = target.type,
          caster = timers(formula.caster.battleUnitWork), target = timers(formula.target.battleUnitWork)}
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

-- Charge pushback mechanism test: the target's current charge, its address and a setter. Runs after the
-- read-only observer, so the charge probe line still shows the native value before any write.
local function pushback(formula, caster, target)
  local work = formula.target.battleUnitWork
  if type(work) ~= 'table' and type(work) ~= 'userdata' then return nil end
  return {action = formula.action, casterType = caster.type, targetType = target.type,
          clock = event.getClock and event.getClock() or nil, unit = work.address,
          current = work.currentChargeTime, required = work.requiredChargeTime, last = work.lastChargeTime,
          set = function(value) work.currentChargeTime = value end,
          get = function() return work.currentChargeTime end}
end

-- Attack area experiment evidence: the caster's weapon category (section 13, as the Pursuit pilot reads it) and its
-- current target focus, to tell the targeted unit from bystanders caught by the area. Read-only.
local function area(formula, caster, target)
  local weapon, category = caster.weapon, nil
  if type(weapon) == 'number' and weapon >= 0x1000 and weapon < 0xFFFF then
    local record = bpack.section13[weapon - 0x1000]
    category = record and record.category
  end
  local work = formula.caster.battleUnitWork
  local focus = (type(work) == 'table' or type(work) == 'userdata') and work.address ~= 0
    and work.currentTargetFocusIdentifier or nil
  return {action = formula.action, clock = event.getClock and event.getClock() or nil,
          casterType = caster.type, casterId = caster.identifier, weapon = weapon, category = category, focus = focus,
          targetType = target.type, targetId = formula.target.identifier, removed = formula.target.removedHp}
end

local observer = Diagnostic.new({profile = profile, charge = charge, hit = hit, emit = function(line) print(line) end})
local probe = Pushback and Pushback.new({emit = function(line) print(line) end})
local areaProbe = Area and Area.new({emit = function(line) print(line) end})
local resetRegistered = event.registerEventSync('onSaveLoad', function()
  observer:reset()
  if probe then probe:reset() end
  if areaProbe then areaProbe:reset() end
  return true
end)
if resetRegistered ~= true then
  print('Legacy foe profile: callback registration failed; observer disabled; bonuses=off')
  return original
end
print('Legacy foe profile: diagnostic ' .. Diagnostic.version .. ' Forge113 observer ready; bonuses=off')
print(probe and ('Legacy charge pushback: probe ' .. Pushback.version ..
  ' armed (party Attack on a mid-charge foe, every 2nd hit, -1/4 of required, max 12 per load); bonuses=charge-test')
  or 'Legacy charge pushback: probe not loaded; off')
print(areaProbe and ('Legacy attack area: probe ' .. Area.version ..
  ' ready (Attack hits: weapon category, targeted or bystander); bonuses=area-test')
  or 'Legacy attack area: probe not loaded; off')
local observed = observer:wrap(original)
-- Each probe runs in its own pcall, so a failure in one never stops the other or interrupts Forge.
local errorLogged = {}
local function guarded(name, f, ...)
  local ok, err = pcall(f, ...)
  if not ok and not errorLogged[name] then
    errorLogged[name] = true
    pcall(print, 'Legacy '..name..': probe error='..tostring(err):gsub('[%z\r\n]', ' ')..' bonuses=test')
  end
end
return function(...)
  local result = table.pack(observed(...))
  if probe then
    guarded('charge pushback', function(...)
      local s = pushback(...)
      if s then probe:hit(s) end
    end, ...)
  end
  if areaProbe then guarded('attack area', function(...) areaProbe:hit(area(...)) end, ...) end
  return table.unpack(result, 1, result.n)
end
