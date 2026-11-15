-- Forge config/functions/34.lua (evasion modifiers before the hit roll): Ivalice Legacy 0.1.41.
-- Cameo Belt (4468), Spotter: a foe a party wearer hits is marked for 6 s (accessory_effects.lua, set from the hit hook
-- through the Proficiency runtime). The next attack on it by ANOTHER party member that rolls a dodge cannot be dodged,
-- and the mark is spent: after Forge's own function has run (Accuracy Boost, Evasion Boost, the shield boosts and the
-- model gate), the target's dodge (evadeParry, the body's side-step, outcome 5) is set to 0; parries (evadeWeapon) and
-- blocks (evadeShield) still count. Zeroing after the original matters: run before it, Evasion Boost could add its
-- +25/+30 back. Shots that never roll a dodge (formula 26 crossbows, guns) leave the mark alone, and so does an attack
-- whose dodge Forge already set to 0. The wearer's own attacks are not changed.
-- Census (read-only): the wearer's first hit on each foe species (an unreadable species: on each unit) logs one line
-- with the dodge before Forge (in) and after its model gate (gated). gated=0 on a foe with in>0 means that model cannot
-- dodge, so a mark adds nothing there. Anything unreadable, or the runtime missing, leaves Forge's own result.
local N = 34
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/' .. N .. '.lua', 't', _ENV))()
local CAMEO_BELT = 4468

-- Second Accessory keeps the second slot in its own memory (global symbol sa_saveMem, u16 per party member, stride 4,
-- indexed by the keep's identifier byte), as its assembly and Duplicate Augment Detector read it.
local secondMem
local function secondAccessory(unit)
  if secondMem == nil then
    local ok, mem = pcall(memory.getSymbol, 'sa_saveMem')
    secondMem = ok and type(mem) == 'number' and mem ~= 0 and mem or false
  end
  local index = unit.identifier
  if not secondMem or type(index) ~= 'number' or index < 0 or index >= 40 then return nil end
  return memory.u16[secondMem + index * 4]
end

local function wearer(caster)
  if caster.type ~= 0 or type(caster.address) ~= 'number' or caster.address == 0 then return false end
  return caster.accessory == CAMEO_BELT or secondAccessory(caster) == CAMEO_BELT
end

local logged = {}
local function census(target, classes, helpers, before, gated)
  if target.type == 0 or type(target.address) ~= 'number' or target.address == 0 then return end
  local work = helpers.getBattleUnitWork(target.address)
  if type(work) ~= 'number' or work == 0 then return end
  local okName, species = pcall(function() return classes.getBattleUnitWork(work).ardUnit.name end)
  -- an unreadable species is logged once per unit, never folded into one shared key
  local key = (okName and species ~= nil) and species or ('unit@' .. tostring(target.address))
  if logged[key] then return end
  logged[key] = true
  if not (okName and species ~= nil) then species = '?' end
  print(string.format('Legacy Cameo: species=%s target=%s evadeParry in=%s gated=%s', tostring(species),
                      tostring(target.identifier), tostring(before), tostring(gated)))
end

return function(formula, caster, target, functions, classes, helpers)
  local before = formula.evadeParry
  original(formula, caster, target, functions, classes, helpers)
  local okWearer, own = pcall(wearer, caster)
  if okWearer and own then pcall(census, target, classes, helpers, before, formula.evadeParry) end
  -- a mark another member set (a second Cameo wearer included) is spent here; the marker's own attack never spends it
  if type(formula.evadeParry) ~= 'number' or formula.evadeParry <= 0 then return end   -- nothing to remove: keep the mark
  local R = rawget(_G, 'IvaliceLegacyProficiency')
  if type(R) ~= 'table' or type(R.spotter34) ~= 'function' then return end
  local ok, spotted = pcall(R.spotter34, formula, caster, target, classes, helpers)
  if ok and spotted == true then formula.evadeParry = 0 end
end
