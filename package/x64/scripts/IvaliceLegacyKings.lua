-- Ivalice Legacy: Kings of the zone (scripts/IvaliceLegacy/kings_runtime.lua). Loaded once by the FF12 LUA Loader.
-- Nothing is patched unless both hook sites hold the original bytes and the game is the Legacy build.
local chunk, err = loadfile('scripts/IvaliceLegacy/kings_runtime.lua', 't', _ENV)
if not chunk then
  print('Legacy Kings: runtime not loaded (' .. tostring(err) .. ')')
  return
end
local ok, K = pcall(chunk)
if not ok or type(K) ~= 'table' then
  print('Legacy Kings: runtime not loaded (' .. tostring(K) .. ')')
  return
end
local registered, why = pcall(K.register)
if not registered then
  print('Legacy Kings: not started (' .. tostring(why) .. ')')
  return
end
rawset(_G, 'IvaliceLegacyKings', K)
