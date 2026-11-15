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
--
-- 0.6.0: Reckless. A party axe wielder's Reckless steps raise a foe's hit on them by 5% each (up to +20%), applied to
-- the attack term after the floor rule, so the cost holds where the floor decides the hit. The Defense dent of 0.5.0
-- is gone.
--
-- 0.7.0: armour type floors and physical-only Reckless. A foe's damaging physical hit on a party member keeps at least
-- 50% of its attack term in heavy body armour, 60% in light and 70% in mystic (the body armour worn, keep +0x56), so
-- armour weight matters under the floor. Foe magicks, hits the target absorbs, and a member in no known body armour keep
-- the half floor of 0.4.0. Reckless now raises only a foe's damaging physical hit, never a magick or an absorbed heal.
-- "Physical" is a formula that carries Forge 165 (the physical after-hit hook) in Forge 1.0.3's formula lists.
local M = {version = '0.7.0', FLOOR_DIVISOR = 4, FOE_FLOOR_DIVISOR = 2, GLANCING_DIVISOR = 8, ATTACK = 150}
M.GLANCING = {dagger = true, ninjaSword = true}
M.PHYSICAL = {[20] = true, [21] = true, [22] = true, [23] = true, [24] = true, [25] = true, [26] = true, [27] = true,
              [28] = true, [29] = true, [64] = true, [103] = true, [104] = true}
-- Body armour item ids by type (section 13: light 4376-4396, mystic 4397-4417, heavy 4418-4435) and the share of a
-- foe's physical attack term each type always lets through.
M.ARMOUR_TYPES = {{first = 4376, last = 4396, kind = 'light'}, {first = 4397, last = 4417, kind = 'mystic'},
                  {first = 4418, last = 4435, kind = 'heavy'}}
M.TYPE_SHARE = {heavy = 0.5, light = 0.6, mystic = 0.7}

-- The armour type of the body armour `unit` wears, or nil (none, a guest or reserve row, or unreadable).
function M.armourType(unit)
  local armour = unit.armor
  if type(armour) ~= 'number' then return nil end
  for _, range in ipairs(M.ARMOUR_TYPES) do
    if armour >= range.first and armour <= range.last then return range.kind end
  end
  return nil
end

-- Whether Forge 113/126 will turn this hit into healing (the target absorbs it): their own test, inverted.
function M.heals(formula)
  local absorb, reverse = formula.absorbState, formula.target.statusEffects.reverse
  return not ((absorb == 1 and reverse == 1) or (absorb == 0 and reverse == 0))
end

-- What a hit is: a foe (type 1) striking a party member (type 0), then whether its formula is physical and whether it
-- heals. The side is read on its own, so the half floor never depends on the other two; an unreadable formula reads as
-- "not a damaging physical hit" (no type floor, no Reckless).
function M.kind(formula, caster, target)
  local okSide, foeOnParty = pcall(function() return caster.type == 1 and target.type == 0 end)
  if not (okSide and foeOnParty == true) then return false, false, true end
  local ok, physical, heals = pcall(function() return M.PHYSICAL[formula.identifier] == true, M.heals(formula) end)
  if not ok then return true, false, true end
  return true, physical == true, heals ~= false
end

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

-- The target's Reckless damage multiplier, from the shared Family module (read at call time); 1 otherwise.
function M.reckless(target, deps)
  local Family = rawget(_G, 'IvaliceLegacyFamily')
  if type(Family) ~= 'table' or type(Family.recklessTaken) ~= 'function' then return 1 end
  local resolved = deps
  if resolved == nil and type(Family.deps) == 'function' then resolved = Family.deps() end
  if type(resolved) ~= 'table' then return 1 end
  return Family.recklessTaken(target, resolved)
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
    local foeOnParty, physical, heals = M.kind(formula, caster, target)
    local divisor = foeOnParty and M.FOE_FLOOR_DIVISOR or nil
    local bodyHit = foeOnParty and physical and not heals
    -- Armour type floors (0.7.0): a damaging physical hit keeps the share its body armour type lets through.
    if bodyHit then
      local okT, kind = pcall(M.armourType, target)
      local share = okT and kind and M.TYPE_SHARE[kind]
      if type(share) == 'number' and share > 0 then divisor = 1 / share end
    end
    -- Reckless: a party axe wielder takes more from a foe's damaging physical hit (family_identities.lua keeps the
    -- state). The factor scales the attack term the floor rule chose, so it acts whether the floor or the subtraction
    -- decides.
    local taken = 1
    if bodyHit then
      local okR, factor = pcall(M.reckless, target, deps)
      local Family = rawget(_G, 'IvaliceLegacyFamily')
      local cap = type(Family) == 'table' and type(Family.RECKLESS_CAP) == 'number' and Family.RECKLESS_CAP or 0.2
      if okR and type(factor) == 'number' and factor > 1 and factor <= 1 + cap + 1e-9 then taken = factor end
    end
    if not divisor then
      local okG, glancing = pcall(M.glancing, formula, caster, deps)
      if okG and glancing then divisor = M.GLANCING_DIVISOR end
    end
    local term = M.floor(attack, defense, divisor)
    if not term then
      if taken == 1 then return original(formula, caster, target, functions, classes, helpers) end
      formula.multiplier, formula.modifier = attack * taken, defense * taken
      local native = table.pack(pcall(original, formula, caster, target, functions, classes, helpers))
      formula.multiplier, formula.modifier = attack, defense
      if not native[1] then error(native[2], 0) end
      return table.unpack(native, 2, native.n)
    end
    formula.multiplier, formula.modifier = term * taken, 0
    local result = table.pack(pcall(original, formula, caster, target, functions, classes, helpers))
    formula.multiplier, formula.modifier = attack, defense
    if not result[1] then error(result[2], 0) end
    return table.unpack(result, 2, result.n)
  end
end

return M
