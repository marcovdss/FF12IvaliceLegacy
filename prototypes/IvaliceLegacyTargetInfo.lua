-- Ivalice Legacy: smaller native TARGET INFO window (scripts/IvaliceLegacy/target_info_scale.lua).
-- Loaded once by the FF12 LUA Loader. Only the vanilla target-info layout is ever changed.
local chunk, err = loadfile('scripts/IvaliceLegacy/target_info_scale.lua', 't', _ENV)
if not chunk then
  print('Legacy target info: not loaded (' .. tostring(err) .. ')')
  return
end
local ok, T = pcall(chunk)
if not ok or type(T) ~= 'table' then
  print('Legacy target info: not loaded (' .. tostring(T) .. ')')
  return
end
local registered, why = pcall(T.register, 0.75)
if not registered then
  print('Legacy target info: not started (' .. tostring(why) .. ')')
  return
end
print('Legacy target info: 0.1.0 ready (scale 0.75)')
rawset(_G, 'IvaliceLegacyTargetInfo', T)
