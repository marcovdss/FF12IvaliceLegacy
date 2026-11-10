-- Forge config/functions/165.lua: Proficiency (prototypes/proficiency_runtime.lua). The original always runs first;
-- the shared runtime is loaded once for all Proficiency adapters.
local N = 165
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/' .. N .. '.lua', 't', _ENV))()
local balanceChunk = loadfile('scripts/IvaliceLegacy/combat_balance.lua', 't', _ENV)
local balanceOk, balance = pcall(balanceChunk or error)
if balanceOk and type(balance) == 'table' and type(balance.wrap165) == 'function' then
  original = balance.wrap165(original)
else
  print('Legacy combat balance: greatsword reduction unavailable; native damage retained')
end
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
local wrapped = R.wrap(N, original)
-- Family Spellbreak (prototypes/family_identities.lua): a mace hit burns MP as a share of the damage
-- it finally dealt, so it reads the result after the balance cut and Proficiency's scaling.
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
  print('Legacy Family: module not loaded; Forge 165 carries balance and Proficiency only')
  return wrapped
end
return Family.wrap165(wrapped)
