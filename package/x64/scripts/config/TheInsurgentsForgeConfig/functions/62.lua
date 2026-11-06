-- Forge config/functions/62.lua: spell power for the party's casts (prototypes/magicks.lua). The original runs first, so
-- everything else stays Forge's own, and a foe's cast or a spell not in the table is untouched.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/62.lua', 't', _ENV))()
local M = rawget(_G, 'IvaliceLegacyMagicks')
if not M then
  local chunk = loadfile('scripts/IvaliceLegacy/magicks.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' then
    print('Legacy Magicks: table not loaded (' .. tostring(mod) .. '); spells stay native')
    return original
  end
  M = mod
  rawset(_G, 'IvaliceLegacyMagicks', M)
  print('Legacy Magicks: ' .. M.version .. ' ready')
end

return function(formula, caster, target, functions, classes, helpers)
  original(formula, caster, target, functions, classes, helpers)
  if caster.type ~= 0 then return end
  local power = M.power(formula.action)
  if not power then return end
  -- The same random bonus Forge gives: up to an eighth of the power.
  local extra = power / 8
  if extra >= 0.01 then extra = helpers.getRandomNumber(extra * 100) / 100 end
  formula.multiplier = power + extra
end
