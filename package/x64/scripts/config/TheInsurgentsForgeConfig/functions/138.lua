-- Forge config/functions/138.lua: Expose, when the party uses it, takes as much Defence as Wither takes Strength.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/138.lua', 't', _ENV))()

-- Broken (0.1.43): the cut makes the foe Broken for 10 s (family_identities.lua), which Lancer Grounding II reads. Any
-- failure here leaves the original effect as it was.
-- A failed load is remembered: the module is not loaded again on every cast (0.1.44).
local loadFailed = false
local function noteBroken(formula, target, caster)
  local Family = rawget(_G, 'IvaliceLegacyFamily')
  if not Family then
    if loadFailed then return end
    local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
    local ok, mod = pcall(chunk or error)
    if not ok or type(mod) ~= 'table' or mod.register() ~= true then loadFailed = true return end
    Family = mod
    rawset(_G, 'IvaliceLegacyFamily', Family)
  end
  if formula.skipState == 1 then return end
  local deps = Family.deps()
  if deps and deps.active() == true then Family.noteBroken(target, deps.clock(), caster.address) end
end

return function(formula, caster, target, functions, classes, helpers)
  if caster.type ~= 0 or target.defense <= 0 then
    return original(formula, caster, target, functions, classes, helpers)
  end
  formula.target.defense = target.defense * 0.7
  pcall(noteBroken, formula, target, caster)
end
