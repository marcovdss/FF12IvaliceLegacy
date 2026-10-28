-- Greatsword-only cleave for the Attack area experiment. The area lives on Attack (action 150) itself, which every
-- weapon and many foes share, so this decides per target which Attack hits keep the sweep: a party member's
-- greatsword sweeps; every other Attack hits only the unit it was aimed at. Decides only; the Forge adapter applies it.
local M = {}
M.version = '0.1.0'

local ATTACK = 150
local GREATSWORD = 2 -- section 13 weapon category
local LIMIT = 100

local function integer(v, lo, hi)
  return type(v)=='number' and v%1==0 and v>=lo and v<=hi
end

local function side(kind)
  return kind == 0 and 'party' or kind == 1 and 'foe' or tostring(kind)
end

function M.new(deps)
  assert(type(deps)=='table' and type(deps.emit)=='function', 'filter requires a log adapter')
  local lines = 0
  local filter = {}

  function filter:reset()
    lines = 0
  end

  -- s: action, initial (aimed-at unit address, 0 when unknown), target (this unit's address), casterType, casterId,
  -- category (caster's main-hand weapon category, nil when unknown), targetType, targetId, clock.
  -- Returns nil for anything that isn't an Attack hit on a bystander, else 'sweep' or 'skip'.
  function filter:decide(s)
    if type(s) ~= 'table' or s.action ~= ATTACK then return nil end
    if not integer(s.target, 1, 0x7FFFFFFFFFFF) then return nil end
    -- Unknown aim: leave the hit alone rather than risk cancelling the aimed-at unit.
    if not integer(s.initial, 1, 0x7FFFFFFFFFFF) then
      if lines < LIMIT then
        lines = lines + 1
        deps.emit(string.format('Legacy cleave: clock=%s caster=%s:%s target=%s:%s aim=unknown decision=native bonuses=area-test',
          tostring(s.clock), side(s.casterType), tostring(s.casterId), side(s.targetType), tostring(s.targetId)))
      end
      return nil
    end
    if s.initial == s.target then return nil end
    local decision = (s.casterType == 0 and s.category == GREATSWORD) and 'sweep' or 'skip'
    if lines < LIMIT then
      lines = lines + 1
      deps.emit(string.format('Legacy cleave: clock=%s caster=%s:%s category=%s target=%s:%s decision=%s bonuses=area-test',
        tostring(s.clock), side(s.casterType), tostring(s.casterId), tostring(s.category), side(s.targetType),
        tostring(s.targetId), decision))
    end
    return decision
  end

  return filter
end

return M
