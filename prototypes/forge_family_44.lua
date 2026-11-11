-- Forge config/functions/44.lua: Family True aim (prototypes/family_identities.lua). 44 is the whole accuracy stage of
-- formula 27 (guns): `accuracyRate <= random(100)` is a miss, and Blind halves the rate. A party gunner's Attack never
-- misses; every other caster rolls natively.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/44.lua', 't', _ENV))()
local Family = rawget(_G, 'IvaliceLegacyFamily')
if not Family then
  local chunk = loadfile('scripts/IvaliceLegacy/family_identities.lua', 't', _ENV)
  local ok, mod = pcall(chunk or error)
  if not ok or type(mod) ~= 'table' or mod.register() ~= true then
    print('Legacy Family: module not loaded (' .. tostring(mod) .. '); Forge 44 unchanged')
    return original
  end
  Family = mod
  rawset(_G, 'IvaliceLegacyFamily', Family)
end
if type(Family.wrap44) ~= 'function' then return original end
return Family.wrap44(original)
