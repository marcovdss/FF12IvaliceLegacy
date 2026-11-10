-- Ivalice Legacy: weapon Hit/Amplify on the stats line (scripts/IvaliceLegacy/gear_layout.lua).
-- Loaded once by the FF12 LUA Loader. Uses Dynamic Description's public API; no game memory is written.
local chunk, err = loadfile('scripts/IvaliceLegacy/gear_layout.lua', 't', _ENV)
if not chunk then
  print('Legacy gear layout: not loaded (' .. tostring(err) .. ')')
  return
end
local ok, L = pcall(chunk)
if not ok or type(L) ~= 'table' then
  print('Legacy gear layout: not loaded (' .. tostring(L) .. ')')
  return
end
local registered, why = pcall(L.register)
if not registered then
  print('Legacy gear layout: not started (' .. tostring(why) .. ')')
  return
end
rawset(_G, 'IvaliceLegacyGearLayout', L)
