-- Forge config/functions/231.lua (Reflect): Ivalice Legacy 0.1.41.
-- Rubicante Ring (4437), Spellcatcher: when a FOE's spell bounces off a party wearer's Reflect (Forge sets outcome 8),
-- the wearer's next damage spell within 10 s deals +25% (accessory_effects.lua, through the Proficiency runtime).
-- Forge 231 has no caster check, so an ally's heal bouncing off the wearer sets outcome 8 too; the runtime arms nothing
-- for it. Forge's own function always runs first and decides the bounce; anything unreadable leaves it as it is.
local N = 231
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

if type(R.reflect231) ~= 'function' then return original end
return function(formula, caster, target, functions, classes, helpers)
  original(formula, caster, target, functions, classes, helpers)
  R.run('reflect231', function() R.reflect231(formula, caster, target, classes, helpers) end)
end
