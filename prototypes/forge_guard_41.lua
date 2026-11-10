-- Forge config/functions/41.lua: Guard fatigue (prototypes/family_identities.lua) on the melee evade roll.
-- A party member who keeps avoiding attacks meets each next one with less evasion; any other target is untouched.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/41.lua', 't', _ENV))()
local Family = rawget(_G, 'IvaliceLegacyFamily')
if not Family then
  local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' or mod.register() ~= true then
    print('Legacy Family: module not loaded (' .. tostring(mod) .. '); Forge 41 unchanged')
    return original
  end
  Family = mod
  rawset(_G, 'IvaliceLegacyFamily', Family)
end
if type(Family.guardWrap) ~= 'function' then return original end
return Family.guardWrap(original)
