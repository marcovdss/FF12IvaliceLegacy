-- Ivalice Legacy: the tint of an ordinary foe (scripts/IvaliceLegacy/foe_tint.lua).
-- Each foe gets a slightly different brightness with a faint warm or cool lean, so a pack of one species is never
-- exactly one colour. The model colour is multiplied: L is 1 +- U(0.02, 0.04) and w is U(-0.012, 0.012), giving
-- rgb = L(1 + w), L, L(1 - w). The lean is at most 1.2%: a foe is never red, green or purple. Only foe.color is written
-- (never the effect or its colour), and a foe that already has a colour of its own (a crowned element, another mod's
-- tint) is left alone.
local T = {version = '0.1.0'}

T.BRIGHT_LOW, T.BRIGHT_HIGH = 0.02, 0.04
T.LEAN = 0.012
T.NEUTRAL = 0.005

-- uniform(lo, hi) -> number; sign() -> 1 or -1
function T.roll(uniform, sign)
  local level = 1 + sign() * uniform(T.BRIGHT_LOW, T.BRIGHT_HIGH)
  local lean = uniform(-T.LEAN, T.LEAN)
  return level * (1 + lean), level, level * (1 - lean)
end

function T.neutral(color)
  return math.abs(color.r - 1) <= T.NEUTRAL and math.abs(color.g - 1) <= T.NEUTRAL
    and math.abs(color.b - 1) <= T.NEUTRAL
end

-- true when the colour was written
function T.apply(foe, uniform, sign)
  local color = foe.color
  if not color or not T.neutral(color) then return false end
  local r, g, b = T.roll(uniform, sign)
  color.r, color.g, color.b = r, g, b
  return true
end

return T
