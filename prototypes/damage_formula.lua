-- Damage formula: every hit keeps Forge's attack - defense, with a floor of a quarter of the attack.
--
-- Forge 113 deals power x (attack - defense) and nothing when defense is higher. Legacy's foes carry more defense than
-- the base game (SFF stats and DynamicFoes' draw), so weapons and spells against high-defense foes fell to zero: an
-- armoured boss could not be hurt by most of the party. Here a hit never drops below power x attack / FLOOR_DIVISOR;
-- wherever attack - defense is already larger, the hit is Forge's own, untouched. Defense keeps its full weight in every
-- ordinary matchup (fights stay hard); it just can no longer erase a hit. Hits that ignore defense (guns, measures) carry
-- no defense term and are untouched.
--
-- 0.3.0 applies the same floor to foes' hits: a tank whose armour matched a foe's attack took about 0.3% of HP a swing
-- (Barmuu on Basch, def 32), so armoured members felt no pressure. Foe hits above the floor are unchanged.
--
-- 0.1.0 used attack^2 / (attack + defense); in play it roughly doubled damage at the common attack-to-defense ratios
-- (second Demon Wall in 99 s, Barmuu in 36 s), so 0.2.0 keeps the subtraction and only replaces the zeros.
local M = {version = '0.3.0', FLOOR_DIVISOR = 4}

-- The attack term for an attack value against a defense value, or nil when Forge's own subtraction already applies.
function M.floor(attack, defense)
  if type(attack) ~= 'number' or type(defense) ~= 'number' or attack <= 0 or defense <= 0 then return nil end
  local least = attack / M.FLOOR_DIVISOR
  if attack - defense >= least then return nil end
  return least
end

-- Wrap Forge 113. Only hits below the floor change, the party's and foes' alike; formula.multiplier and formula.modifier are restored
-- afterwards, so every later Forge function and observer sees the native fields.
function M.wrap(original)
  return function(formula, caster, target, functions, classes, helpers)
    if formula.outcomeType == 10 then
      return original(formula, caster, target, functions, classes, helpers)
    end
    local attack, defense = formula.multiplier, formula.modifier
    local term = M.floor(attack, defense)
    if not term then return original(formula, caster, target, functions, classes, helpers) end
    formula.multiplier, formula.modifier = term, 0
    local result = table.pack(pcall(original, formula, caster, target, functions, classes, helpers))
    formula.multiplier, formula.modifier = attack, defense
    if not result[1] then error(result[2], 0) end
    return table.unpack(result, 2, result.n)
  end
end

return M
