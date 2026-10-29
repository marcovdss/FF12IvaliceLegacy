-- Forge config/functions/110.lua: Proficiency (prototypes/proficiency_runtime.lua). The original always runs first;
-- the shared runtime is loaded once for all Proficiency adapters.
local N = 110
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
return R.wrap(N, original)
