-- Forge config/functions/10.lua: Family Chain (prototypes/family_identities.lua). A rolled katana
-- combo runs two hits longer inside the engine's own cap; every other combo stays native.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/10.lua', 't', _ENV))()
local Family = rawget(_G, 'IvaliceLegacyFamily')
if not Family then
  local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' or mod.register() ~= true then
    print('Legacy Family: module not loaded (' .. tostring(mod) .. '); Forge 10 unchanged')
    return original
  end
  Family = mod
  rawset(_G, 'IvaliceLegacyFamily', Family)
end
return Family.wrap10(original)
