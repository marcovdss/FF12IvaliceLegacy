-- Horology: retain clock peaks and use Legacy's modest low-digit floor.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/133.lua', 't', _ENV))()
local ok, rules = pcall(function()
  return assert(loadfile('scripts/IvaliceLegacy/technicks.lua', 't', _ENV))()
end)
if not ok or type(rules) ~= 'table' or type(rules.wrap) ~= 'function' then
  print('Legacy Technicks: rules unavailable; Horology stays native')
  return original
end
return rules.wrap(133, original)
