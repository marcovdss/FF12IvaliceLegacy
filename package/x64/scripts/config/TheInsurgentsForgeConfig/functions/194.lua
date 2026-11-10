-- Achilles: a successful party cast chooses an element not already weak.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/194.lua', 't', _ENV))()
local ok, rules = pcall(function()
  return assert(loadfile('scripts/IvaliceLegacy/technicks.lua', 't', _ENV))()
end)
if not ok or type(rules) ~= 'table' or type(rules.wrap) ~= 'function' then
  print('Legacy Technicks: rules unavailable; Achilles stays native')
  return original
end
return rules.wrap(194, original)
