-- Forge config/functions/126.lua: Legacy's damage floor on formula 23 (axe, hammer, hand-bomb), which damages through
-- Forge 126 instead of 113. Same rule as the 113 adapter's: attack - defense, never below a quarter of the attack
-- (foes on the party: a half). If the module is missing or broken, damage stays Forge's own.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/126.lua', 't', _ENV))()
local chunk = loadfile('scripts/IvaliceLegacy/damage_formula.lua', 't', _ENV)
local ok, Damage = false, nil
if chunk then ok, Damage = pcall(chunk) end
if not ok or type(Damage) ~= 'table' or type(Damage.wrap) ~= 'function' then
  print('Legacy damage: formula not loaded; Forge 126 native attack - defense')
  return original
end
print('Legacy damage: formula ' .. Damage.version .. ' on Forge 126 (axe, hammer, hand-bomb)')
return Damage.wrap(original)
