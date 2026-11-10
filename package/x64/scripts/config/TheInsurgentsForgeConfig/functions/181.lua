-- Forge config/functions/181.lua (status spells: formulas 2, 3 and 44 end here).
-- Inner: the party's Decoy lasts about two minutes (prototypes/magicks.lua). Outer: Proficiency hears the statuses a
-- party member's spell newly landed ('cast', hybrid lines). The original always runs first, so whether a status lands
-- stays Forge's own; a foe's cast, or any other status, is untouched. Either part missing leaves the other working.
local N = 181
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/181.lua', 't', _ENV))()

local M = rawget(_G, 'IvaliceLegacyMagicks')
if not M then
  local chunk = loadfile('scripts/IvaliceLegacy/magicks.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if ok and type(mod) == 'table' then
    M = mod
    rawset(_G, 'IvaliceLegacyMagicks', M)
    print('Legacy Magicks: ' .. M.version .. ' ready')
  else
    print('Legacy Magicks: table not loaded (' .. tostring(mod) .. '); Decoy stays native')
  end
end

local inner = original
if M then
  inner = function(formula, caster, target, functions, classes, helpers)
    original(formula, caster, target, functions, classes, helpers)
    if caster.type ~= 0 or formula.action ~= M.DECOY_ACTION then return end
    if formula.target.addedStatusEffects[M.DECOY_STATUS] == 1 then
      formula.target.statusEffectDurations[M.DECOY_STATUS] = M.DECOY_TICKS
    end
  end
end

local R = rawget(_G, 'IvaliceLegacyProficiency')
if not R then
  local chunk = loadfile('scripts/IvaliceLegacy/proficiency_runtime.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' or mod.register() ~= true then
    print('Legacy Proficiency: runtime not loaded (' .. tostring(mod) .. '); Forge ' .. N .. ' without Proficiency')
    return inner
  end
  R = mod
  rawset(_G, 'IvaliceLegacyProficiency', R)
  print('Legacy Proficiency: ' .. R.Proficiency.version .. ' ready')
end
return R.wrap(N, inner)
