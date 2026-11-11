-- Forge config/functions/22.lua: Proficiency (prototypes/proficiency_runtime.lua). The original always runs first;
-- the shared runtime is loaded once for all Proficiency adapters.
local N = 22
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
-- Family Unseen (prototypes/family_identities.lua): ninja swords ignore weapon and shield evasion.
-- Outside Proficiency, so the Samurai's aim stays its own decision. (Guns: True aim is on Forge 44.)
local Family = rawget(_G, 'IvaliceLegacyFamily')
if not Family then
  local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if ok and type(mod) == 'table' and mod.register() == true then
    Family = mod
    rawset(_G, 'IvaliceLegacyFamily', Family)
  end
end
if not Family then
  print('Legacy Family: module not loaded; Forge 22 carries Proficiency only')
  return R.wrap(N, original)
end
return Family.wrap22(R.wrap(N, original))
