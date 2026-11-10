-- A greatsword's basic Attack sweeps an area. Marco's 2026-11-06 balance
-- decision: halve its damage per target, keeping weapon power and skill costs.
local M = {version = '0.1.0'}

function M.wrap165(original)
  assert(type(original) == 'function', 'Missing native physical-damage stage')
  return function(formula, caster, target, functions, classes, helpers)
    local amount = formula.target.removedHp
    if formula.action == 150 and caster.type == 0 and target.type == 1
        and formula.skipState ~= 1 and type(amount) == 'number' and amount > 0 then
      local weapon = caster.weapon
      if type(weapon) == 'number' and weapon % 1 == 0 and weapon >= 0x1000
          and weapon - 0x1000 < bpack.section13.count
          and bpack.section13[weapon - 0x1000].category == 2 then
        formula.target.removedHp = math.floor(amount * 0.5)
      end
    end
    -- Native Attack Boost / Double Edged, then Proficiency and the later drain
    -- stages all see this reduced amount, so recoil and leech stay consistent.
    return original(formula, caster, target, functions, classes, helpers)
  end
end

return M
