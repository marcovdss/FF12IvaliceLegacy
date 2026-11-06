-- Forge config/functions/138.lua: Expose, when the party uses it, takes as much Defence as Wither takes Strength.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/138.lua', 't', _ENV))()

return function(formula, caster, target, functions, classes, helpers)
  if caster.type ~= 0 or target.defense <= 0 then
    return original(formula, caster, target, functions, classes, helpers)
  end
  formula.target.defense = target.defense * 0.7
end
