-- Ivalice Legacy: the enemy layer (scripts/IvaliceLegacy/foe_layer.lua). Loaded once by the FF12 LUA Loader.
-- It waits for Instanced Foes' ready flag, then loads foe_crown, story_bosses, foe_tint and the generated crown_table
-- and logs one line. Dynamic Bosses stays untouched; its settings file and kind list are only read. Nothing happens
-- without Instanced Foes.
local chunk, err = loadfile('scripts/IvaliceLegacy/foe_layer.lua', 't', _ENV)
if not chunk then
  print('Legacy foes: not loaded (' .. tostring(err) .. ')')
  return
end
local ok, L = pcall(chunk)
if not ok or type(L) ~= 'table' then
  print('Legacy foes: not loaded (' .. tostring(L) .. ')')
  return
end
local registered, why = pcall(L.register)
if not registered then
  print('Legacy foes: not started (' .. tostring(why) .. ')')
  return
end
rawset(_G, 'IvaliceLegacyFoes', L)
