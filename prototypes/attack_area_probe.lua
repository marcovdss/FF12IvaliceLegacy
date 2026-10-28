-- Read-only companion to the Attack area experiment: one line per hit of the basic Attack (action 150), naming the
-- caster's weapon category and whether the hit landed on the unit the caster had targeted or on a bystander caught by
-- the area. That's the evidence a later greatsword-only cleave would gate on. No writes, capped per load.
local M = {}
M.version = '0.1.0'

local ATTACK = 150
local LIMIT = 200

local function integer(v, lo, hi)
  return type(v)=='number' and v%1==0 and v>=lo and v<=hi
end

local function side(kind)
  return kind == 0 and 'party' or kind == 1 and 'foe' or tostring(kind)
end

function M.new(deps)
  assert(type(deps)=='table' and type(deps.emit)=='function', 'probe requires a log adapter')
  local lines = 0
  local probe = {}

  function probe:reset()
    lines = 0
  end

  -- s: action, clock, casterType, casterId, weapon, category, focus (the caster's current target focus), targetType,
  -- targetId, removed (Forge's pending HP damage).
  function probe:hit(s)
    if lines >= LIMIT or type(s) ~= 'table' or s.action ~= ATTACK then return false end
    local primary = '?'
    if integer(s.focus, 0, 0xFFFF) and integer(s.targetId, 0, 0xFFFF) then
      primary = s.focus == s.targetId and 'yes' or 'no'
    end
    lines = lines + 1
    deps.emit(string.format(
      'Legacy attack area: clock=%s caster=%s:%s weapon=%s category=%s focus=%s target=%s:%s primary=%s removed=%s bonuses=area-test',
      tostring(s.clock), side(s.casterType), tostring(s.casterId), tostring(s.weapon), tostring(s.category),
      tostring(s.focus), side(s.targetType), tostring(s.targetId), primary, tostring(s.removed)))
    return true
  end

  return probe
end

return M
