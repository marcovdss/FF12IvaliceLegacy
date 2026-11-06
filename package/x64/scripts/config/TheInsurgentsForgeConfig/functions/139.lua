-- Forge config/functions/139.lua: Shear, when the party uses it, takes as much Magick Resist as Addle takes Magick.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/139.lua', 't', _ENV))()

return function(formula, caster, target, functions, classes, helpers)
  if caster.type ~= 0 or target.magickResist <= 0 then
    return original(formula, caster, target, functions, classes, helpers)
  end
  formula.target.magickResist = target.magickResist * 0.7
end
