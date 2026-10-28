-- Forge config/functions/228.lua for the Attack area experiment. 228 is the onCastPre/onHitPre check that silently
-- skips units an action can't touch (KO, Stone, X-Zone: skipState 1, outcome 9, no "Miss"). The wrapper runs it
-- unchanged, then skips Attack bystanders the cleave filter rejects the same way, so only greatswords sweep.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/228.lua', 't', _ENV))()
local chunk = loadfile('scripts/IvaliceLegacy/attack_area_filter.lua', 't', _ENV)
local ok, Filter = false, nil
if chunk then ok, Filter = pcall(chunk) end
if not ok or type(Filter) ~= 'table' then Filter = nil end

local ATTACK = 150
local filter = Filter and Filter.new({emit = function(line) print(line) end})
local registered = event.registerEventSync('onSaveLoad', function()
  if filter then filter:reset() end
  return true
end)
print(filter
  and ('Legacy cleave: filter ' .. Filter.version .. ' ready (Attack sweeps for party greatswords only)'
       .. (registered == true and '' or '; reset callback failed') .. '; bonuses=area-test')
  or 'Legacy cleave: filter not loaded; Attack hits only its aimed-at unit; bonuses=area-test')

local function sample(formula, caster, target)
  local category = nil
  local weapon = caster.weapon
  if type(weapon) == 'number' and weapon >= 0x1000 and weapon - 0x1000 < bpack.section13.count then
    category = bpack.section13[weapon - 0x1000].category
  end
  return {action = ATTACK, initial = formula.initialTargetPointer, target = target.address,
          casterType = caster.type, casterId = caster.identifier, category = category,
          targetType = target.type, targetId = formula.target.identifier,
          clock = event.getClock and event.getClock() or nil}
end

local errorLogged = false
return function(formula, caster, target, ...)
  original(formula, caster, target, ...)
  if formula.skipState == 1 or formula.action ~= ATTACK then return end
  local decided, decision
  if filter then
    decided, decision = pcall(function() return filter:decide(sample(formula, caster, target)) end)
  else
    -- No filter module: back to single-target Attack, skipping only units that weren't aimed at.
    decided, decision = pcall(function()
      local initial = formula.initialTargetPointer
      return initial ~= 0 and initial ~= target.address and 'skip' or nil
    end)
  end
  if not decided then
    -- A broken filter must not stop Attack itself: leave the hit as the native 228 left it.
    if not errorLogged then
      errorLogged = true
      pcall(print, 'Legacy cleave: filter error=' .. tostring(decision):gsub('[%z\r\n]', ' ') .. ' bonuses=area-test')
    end
    return
  end
  if decision == 'skip' then
    formula.skipState = 1
    formula.outcomeType = 9
  end
end
