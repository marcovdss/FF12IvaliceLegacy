-- Three command-specific utility improvements; all other Technicks keep their
-- existing mechanics. Action IDs, targeting, MP, range and native gates stay.
local M = {version = '0.1.0'}

function M.wrap(id, original)
  assert(type(original) == 'function', 'Missing original Forge function')
  return function(formula, caster, target, functions, classes, helpers)
    if id == 194 and formula.action == 162 and caster.type == 0 and target.type == 1
        and formula.skipState ~= 1 then
      -- Forge 324 commits Achilles to this same keep affinity map. Filter the
      -- current weak bits, so a successful roll cannot select an old weakness.
      local choices = {}
      for element = 0, 7 do
        if target.permanentElementalAffinities.weak[element] == 0 then choices[#choices + 1] = element end
      end
      if #choices > 0 then
        local chosen = choices[helpers.getRandomNumber(#choices) + 1]
        local proxy = setmetatable({getRandomNumber = function(limit)
          if limit == 8 then return chosen end
          return helpers.getRandomNumber(limit)
        end}, {__index = helpers})
        return original(formula, caster, target, functions, classes, proxy)
      end
    end
    local result = original(formula, caster, target, functions, classes, helpers)
    if formula.skipState == 1 or caster.type ~= 0 then return result end
    if id == 132 and formula.action == 158 and target.type == 0
        and formula.target.statusEffects.hpCritical == 1
        and formula.target.statusEffects.ko ~= 1 and (target.currentHp or 0) > 0 then
      -- Current First Aid always uses the reduced roll: its mean is 10% and
      -- its upper bound about 20%. Guarantee that mean, preserving better rolls.
      local minimum = math.max(1, formula.target.maxHp // 10)
      formula.target.addedHp = math.max(formula.target.addedHp or 0, minimum)
    elseif id == 133 and formula.action == 160 and target.type == 1 then
      -- Clock peaks (up to 81 x level) remain. A low digit still pays a modest
      -- 10 x level per foe; over the whole clock cycle the mean rises about 9%.
      formula.target.removedHp = math.max(formula.target.removedHp or 0, caster.level * 10)
    end
    return result
  end
end

return M
