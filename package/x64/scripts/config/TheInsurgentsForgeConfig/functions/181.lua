-- Forge config/functions/181.lua: the party's Decoy lasts about two minutes (prototypes/magicks.lua). The original runs
-- first, so whether the status lands stays Forge's own. A foe's cast, or any other status, is untouched.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/181.lua', 't', _ENV))()
local M = rawget(_G, 'IvaliceLegacyMagicks')
if not M then
  local chunk = loadfile('scripts/IvaliceLegacy/magicks.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' then
    print('Legacy Magicks: table not loaded (' .. tostring(mod) .. '); Decoy stays native')
    return original
  end
  M = mod
  rawset(_G, 'IvaliceLegacyMagicks', M)
  print('Legacy Magicks: ' .. M.version .. ' ready')
end

return function(formula, caster, target, functions, classes, helpers)
  original(formula, caster, target, functions, classes, helpers)
  if caster.type ~= 0 or formula.action ~= M.DECOY_ACTION then return end
  if formula.target.addedStatusEffects[M.DECOY_STATUS] == 1 then
    formula.target.statusEffectDurations[M.DECOY_STATUS] = M.DECOY_TICKS
  end
end
