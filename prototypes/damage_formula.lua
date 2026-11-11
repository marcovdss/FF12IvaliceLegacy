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
-- 0.4.0: a foe's hit on a party member keeps at least HALF of its attack. With the quarter floor a shield Knight in
-- heavy armour took 3.3% of his HP from a landed Attack (Basch, 2026-11-10, 12 hits) where the danger rule asks 10-15%
-- for the band's tank; unarmoured, the same hits were about 13%. Armour still halves a blow, and blocks, parries,
-- Protect and the armour triangle keep their full weight above the floor. Party hits on foes keep the quarter floor.
--
-- 0.1.0 used attack^2 / (attack + defense); in play it roughly doubled damage at the common attack-to-defense ratios
-- (second Demon Wall in 99 s, Barmuu in 36 s), so 0.2.0 keeps the subtraction and only replaces the zeros.
--
-- 0.5.0: Glancing. A party dagger or ninja sword Attack keeps only attack/8 as its floor (the others keep attack/4), so
-- light blades are unchanged against ordinary Defense and bounce off armor. The same floor now also guards formula 23
-- (axe, hammer, hand-bomb), which damages through Forge 126 (adapter 126.lua wraps it with this module).
local M = {version = '0.5.0', FLOOR_DIVISOR = 4, FOE_FLOOR_DIVISOR = 2, GLANCING_DIVISOR = 8, ATTACK = 150}
M.GLANCING = {dagger = true, ninjaSword = true}

-- The attack term for an attack value against a defense value, or nil when Forge's own subtraction already applies.
function M.floor(attack, defense, divisor)
  if type(attack) ~= 'number' or type(defense) ~= 'number' or attack <= 0 or defense <= 0 then return nil end
  local least = attack / (divisor or M.FLOOR_DIVISOR)
  if attack - defense >= least then return nil end
  return least
end

-- Does this hit take the Glancing floor: a party wielder's Attack with a dagger or a ninja sword, in a Legacy campaign.
-- `deps` carries active() and category(unit) like the Family module's; without them the shared Family module is used
-- (read at call time, so load order does not matter). Anything unreadable means no Glancing.
function M.glancing(formula, caster, deps)
  local Family = rawget(_G, 'IvaliceLegacyFamily')
  deps = deps or (type(Family) == 'table' and type(Family.deps) == 'function' and Family.deps()) or nil
  if type(deps) ~= 'table' or type(deps.active) ~= 'function' or type(deps.category) ~= 'function' then return false end
  if formula.action ~= M.ATTACK or caster.type ~= 0 or deps.active() ~= true then return false end
  return M.GLANCING[deps.category(caster)] == true
end

-- The target's Reckless-dented Defense, from the shared Family module (read at call time); `defense` unchanged otherwise.
function M.reckless(target, defense, deps)
  local Family = rawget(_G, 'IvaliceLegacyFamily')
  if type(Family) ~= 'table' or type(Family.recklessDefense) ~= 'function' then return defense end
  local resolved = deps
  if resolved == nil and type(Family.deps) == 'function' then resolved = Family.deps() end
  if type(resolved) ~= 'table' then return defense end
  return Family.recklessDefense(target, defense, resolved)
end

-- Wrap Forge 113 (or 126). Only hits below the floor change, the party's and foes' alike; formula.multiplier and formula.modifier are restored
-- afterwards, so every later Forge function and observer sees the native fields.
function M.wrap(original, deps)
  return function(formula, caster, target, functions, classes, helpers)
    if formula.outcomeType == 10 then
      return original(formula, caster, target, functions, classes, helpers)
    end
    local attack, defense = formula.multiplier, formula.modifier
    -- a foe (type 1) striking a party member (type 0); a broken reader falls back to the common floor
    local okSide, foeOnParty = pcall(function() return caster.type == 1 and target.type == 0 end)
    local divisor = okSide and foeOnParty and M.FOE_FLOOR_DIVISOR or nil
    -- Reckless: a party axe wielder's own Defense counts lower against a foe's hit (family_identities.lua keeps the
    -- state). The floor rule below judges the dented Defense.
    local effective = defense
    if okSide and foeOnParty then
      local okR, dented = pcall(M.reckless, target, defense, deps)
      if okR and type(dented) == 'number' then effective = dented end
    end
    if not divisor then
      local okG, glancing = pcall(M.glancing, formula, caster, deps)
      if okG and glancing then divisor = M.GLANCING_DIVISOR end
    end
    local term = M.floor(attack, effective, divisor)
    if not term then
      if effective == defense then return original(formula, caster, target, functions, classes, helpers) end
      formula.modifier = effective
      local native = table.pack(pcall(original, formula, caster, target, functions, classes, helpers))
      formula.modifier = defense
      if not native[1] then error(native[2], 0) end
      return table.unpack(native, 2, native.n)
    end
    formula.multiplier, formula.modifier = term, 0
    local result = table.pack(pcall(original, formula, caster, target, functions, classes, helpers))
    formula.multiplier, formula.modifier = attack, defense
    if not result[1] then error(result[2], 0) end
    return table.unpack(result, 2, result.n)
  end
end

return M
