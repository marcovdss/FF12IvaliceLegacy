-- Ivalice Legacy: battle number colours (scripts/IvaliceLegacy/number_colors.lua). Loaded once by the FF12 LUA Loader.
-- Diagnostic 0.2.0: dump the palette once per load, and draw MP recovery in the foe-damage colour; Marco's report of
-- that colour names it, and the dump points at the white entry for the follow-up.
local chunk, err = loadfile('scripts/IvaliceLegacy/number_colors.lua', 't', _ENV)
if not chunk then
  print('Legacy numbers: not loaded (' .. tostring(err) .. ')')
  return
end
local ok, N = pcall(chunk)
if not ok or type(N) ~= 'table' then
  print('Legacy numbers: not loaded (' .. tostring(N) .. ')')
  return
end
local started, why = pcall(N.register)
if not started then print('Legacy numbers: not started (' .. tostring(why) .. ')') end
rawset(_G, 'IvaliceLegacyNumberColors', N)
