-- First Aid: retain Forge's native gates and roll, then apply Legacy's floor.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/132.lua', 't', _ENV))()
local ok, rules = pcall(function()
  return assert(loadfile('scripts/IvaliceLegacy/technicks.lua', 't', _ENV))()
end)
if not ok or type(rules) ~= 'table' or type(rules.wrap) ~= 'function' then
  print('Legacy Technicks: rules unavailable; First Aid stays native')
  return original
end
return rules.wrap(132, original)
