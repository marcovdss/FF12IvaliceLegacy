-- Forge config/functions/94.lua (weather and terrain): Ivalice Legacy 0.1.41.
-- Barbariccia Ring (4438), Stormcaller: a party wearer's own magick keeps the weather and terrain bonuses (x1.2) and
-- skips the penalties (x0.5). Forge 94 ignores weather and terrain for any Weatherproof caster or target; for a wearer
-- casting on a target that is not Weatherproof, the bonuses are applied after it (accessory_effects.lua). As a target,
-- the wearer stays Weatherproof as before. Anything unreadable leaves Forge's own result.
local N = 94
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/' .. N .. '.lua', 't', _ENV))()
local R = rawget(_G, 'IvaliceLegacyProficiency')
if not R then
  local chunk = loadfile('scripts/IvaliceLegacy/proficiency_runtime.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' or mod.register() ~= true then
    print('Legacy Proficiency: runtime not loaded (' .. tostring(mod) .. '); Forge ' .. N .. ' unchanged')
    return original
  end
  R = mod
  rawset(_G, 'IvaliceLegacyProficiency', R)
  print('Legacy Proficiency: ' .. R.Proficiency.version .. ' ready')
end

if type(R.weather94) ~= 'function' then return original end
return function(formula, caster, target, functions, classes, helpers)
  original(formula, caster, target, functions, classes, helpers)
  R.run('weather94', function() R.weather94(formula, caster, target, classes, helpers) end)
end
