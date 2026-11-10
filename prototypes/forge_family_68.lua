-- Forge config/functions/68.lua: Family Sunder (prototypes/family_identities.lua). The axe damage
-- function sets this hit's Defence term first, so the dent this hit makes lands on every later hit.
-- Hand-bombs share function 68; the category gate inside the wrap excludes them.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/68.lua', 't', _ENV))()
local Family = rawget(_G, 'IvaliceLegacyFamily')
if not Family then
  local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' or mod.register() ~= true then
    print('Legacy Family: module not loaded (' .. tostring(mod) .. '); Forge 68 unchanged')
    return original
  end
  Family = mod
  rawset(_G, 'IvaliceLegacyFamily', Family)
end
return Family.wrap68(original)
