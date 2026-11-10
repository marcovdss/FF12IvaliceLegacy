-- Forge config/functions/8.lua: Family Counterflow (prototypes/family_identities.lua). The pole
-- wielder's counter chance doubles before the original rolls it; any other defender is untouched.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/8.lua', 't', _ENV))()
local Family = rawget(_G, 'IvaliceLegacyFamily')
if not Family then
  local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' or mod.register() ~= true then
    print('Legacy Family: module not loaded (' .. tostring(mod) .. '); Forge 8 unchanged')
    return original
  end
  Family = mod
  rawset(_G, 'IvaliceLegacyFamily', Family)
end
return Family.wrap8(original)
