-- MP economy: a stronger caster pays more for each spell, and no spell can pay for itself.
--
-- The party has many ways to win MP back (Proficiency lines that return a share of an overflowing heal, of max MP, or of
-- the heal to an ally), while spell costs stayed native. A Seer whose Cura heals 1,500 past full HP got 75 MP back from
-- a spell that costs 18 to 26: infinite casting. Two rules close that loop.
--
-- 1. Cost grows with Magick. After a party member casts a Magick (battle menu category 1, with an MP cost), the caster pays
--    a surcharge so the total is paid x Magick / DIVISOR, never below what the game charged and never above CAP times it.
--    Charged once per cast, at the end of the cast, from the caster's current MP (never below 0). The listed cost and the
--    action table stay native: foes cast the same rows and Together reads the cost.
-- 2. A spell's MP returns are capped. Everything a single cast hands back as MP (to the caster or to allies, from every
--    target it reaches) is at most RETURN_SHARE of what that cast cost in total. Drains taken from a foe's own MP and
--    returns from items (Potions cost gil, not MP) are not capped.
--
-- 0.2.0: "paid" is the MP the game itself charges this caster, not the table's listed cost. The game lowers the listed
-- cost with the Channeling augments (a percentage each, from the augment table), makes it free with Spellspring, halves
-- it with Parsimony and takes gil instead of MP with Offering (native rule as Ivalice Together's menu reads it,
-- native_command_metadata.effective_cost, float32 steps included). 0.1.0 scaled the listed cost, so a Parsimony caster
-- paid half the cost plus a full-size surcharge, an Offering caster paid gil plus an MP surcharge, and Penelo's Cura
-- (26 listed, 18 charged) cost 18 + 26.
local M = {version = '0.2.0'}

M.MAGICK_CATEGORY = 1      -- battle menu category of Magicks (Technicks 2, items 3, Quickenings 6, Espers 5)
M.DIVISOR = 30             -- Magick at which a spell costs exactly its listed price
M.CAP = 3                  -- the most a spell can cost, as a multiple of its listed price
M.RETURN_SHARE = 0.5       -- the most one cast can give back as MP, as a share of what it cost

-- Augment bits (Forge classes/getAugments.lua) of the native MP-cost rule.
M.AUGMENTS = {offering = 32, parsimony = 36, spellspring = 51, channeling3 = 91, channeling2 = 92, channeling1 = 93}
M.CHANNELING = {'channeling3', 'channeling2', 'channeling1'}

-- The game does these steps in float32; rounding to float32 keeps every cost identical to the native one
-- (30 MP with 50% Channeling is 14 natively, 15 in double precision).
local f32 = function(x) return x end
if type(string.pack) == 'function' then
  f32 = function(x) return (string.unpack('<f', string.pack('<f', x))) end
end
local function trunc(x) return x >= 0 and math.floor(x) or math.ceil(x) end

-- The MP the game charges for a listed cost. `has(name)` tells whether the caster has that augment; `param(bit)` is the
-- augment table's parameter for that augment bit. Offering pays in gil and Spellspring is free: both are 0 MP.
function M.paid(cost, has, param)
  if type(cost) ~= 'number' or cost <= 0 then return 0 end
  if type(has) ~= 'function' then return cost end
  if has('offering') then return 0 end
  local reduction = 0
  for _, name in ipairs(M.CHANNELING) do
    if has(name) then
      local p = type(param) == 'function' and param(M.AUGMENTS[name]) or 0
      if type(p) == 'number' then reduction = reduction + p end
    end
  end
  local amount = trunc(f32(cost - f32(f32(cost / 100) * reduction)))
  if has('spellspring') then return 0 end
  if has('parsimony') then amount = trunc(amount / 2) end
  return math.max(0, amount)
end

-- An augment reader over Forge unit views (formula.caster.augments, keep.permanentAugments, keep.temporaryAugments):
-- an augment counts when any view has it. Parameters come from battle pack section 58, as Forge's own functions read.
function M.reader(views)
  local function has(name)
    for _, view in ipairs(views or {}) do
      local ok, v = pcall(function() return view[name] end)
      if ok and v == 1 then return true end
    end
    return false
  end
  local function param(bit)
    local ok, v = pcall(function() return bpack.section58[bit].parameter end)
    return ok and type(v) == 'number' and v or 0
  end
  return has, param
end

-- The multiple of the listed cost a caster with this Magick pays.
function M.factor(magick)
  if type(magick) ~= 'number' or magick <= M.DIVISOR then return 1 end
  return math.min(M.CAP, magick / M.DIVISOR)
end

-- Total MP a cast costs a caster with this Magick, from what the game charged (`paid`, default the listed cost).
function M.total(cost, magick, paid)
  local base = paid or cost
  if type(base) ~= 'number' or base <= 0 then return 0 end
  return math.floor(base * M.factor(magick) + 0.5)
end

-- MP charged on top of what the game charged.
function M.surcharge(cost, magick, paid)
  local base = paid or cost
  if type(base) ~= 'number' or base <= 0 then return 0 end
  return M.total(cost, magick, base) - base
end

-- The most MP one cast may hand back.
function M.returnCap(cost, magick, paid)
  return math.floor(M.total(cost, magick, paid) * M.RETURN_SHARE)
end

-- Is this action record a Magick the economy prices?
function M.priced(record)
  return type(record) == 'table' and record.category == M.MAGICK_CATEGORY and type(record.mpOrMistCost) == 'number'
    and record.mpOrMistCost > 0
end

return M
