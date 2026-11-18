-- A greatsword's basic Attack sweeps an area (the cleave filter at stage 228 keeps the foes around the target).
--
-- 0.3.0 (Marco, 2026-11-16: "better to just do half damage to every foe around it from total, so changing weapons at
-- the right moment becomes player agency"): the aimed target takes the full hit. Every foe the sweep reaches takes half
-- of that hit's total (the aimed hit as this stage received it), not half of its own hit, so its own Defense does not
-- matter. A landed hit never drops below 1 (Excalipur hits for 1, and half of 1 would be no hit at all).
-- 0.2.0 halved every hit of the swing, the aimed target included, so the text ("half damage around it") was true of
-- nobody and a lone boss was hit at half strength.
--
-- Facts the engine has not confirmed yet, and what this does about each (all three print one line in game):
--  * The hit order of the aimed and the swept foes in one swing is unknown. The aimed hit records its amount; a swept
--    hit that arrives within SWING_WINDOW of a record of the same caster uses it. A swept hit with no record falls back
--    to half of its own hit (the old rule, never stronger than the new one). Two swings settle the order: if a
--    swept hit came first and the aimed hit follows within the window, twice, the module stays on the fallback for the
--    session ("sweep order=swept-first"); if a swept hit used a record, twice, it is "aimed-first". One observation
--    only logs "order seen". Correct in both orders.
--  * formula.initialTargetPointer at this stage. Equal to the target's address means the aimed foe; another nonzero
--    address means a swept foe; 0 means the engine named no aim (a counter, say): the first such hit of a swing is the
--    aimed foe (so a lone swing is never cut), and any later one within SWING_WINDOW takes half of the recorded hit. A build where the field cannot be read keeps the 0.2.0 rule (half of every hit).
local M = {version = '0.3.0'}

M.SWEEP_SHARE = 0.5
M.SWING_WINDOW = 100000   -- microseconds: an aimed hit and the swept hits of its swing are processed together
M.EVIDENCE = 2            -- matching observations before the session's hit order is locked

local swings = {}         -- caster address -> {at, amount}: the last aimed hit of a greatsword swing
local unmatched = {}      -- caster address -> clock of a swept hit that found no record (swept-first evidence)
local order = nil         -- nil unknown, 'aimed-first' or 'swept-first' (locked after M.EVIDENCE matching swings)
local aimedEvidence, sweptEvidence = 0, 0
local logged = {}

local function say(key, text)
  if logged[key] then return end
  logged[key] = true
  if type(print) == 'function' then pcall(print, 'Legacy balance: ' .. text) end
end

local function clock()
  if type(event) == 'table' and type(event.getClock) == 'function' then
    local ok, now = pcall(event.getClock)
    if ok and type(now) == 'number' then return now end
  end
  return 0
end

-- 'aimed', 'swept', 'none' (no aim named: counts as aimed) or 'unreadable'.
function M.role(formula, target)
  local ok, initial = pcall(function() return formula.initialTargetPointer end)
  if not ok or type(initial) ~= 'number' then return 'unreadable', initial end
  if initial == 0 then return 'none', initial end
  if initial == target.address then return 'aimed', initial end
  return 'swept', initial
end

-- The new removedHp for a greatsword Attack hit of `amount`, and the rule that decided it.
function M.decide(formula, caster, target, amount, now)
  local role, initial = M.role(formula, target)
  say('role-' .. role, string.format('sweep role=%s initial=%s target=%s', role, tostring(initial), tostring(target.address)))
  local who = caster.address
  if role == 'unreadable' then
    return math.max(1, math.floor(amount * M.SWEEP_SHARE)), 'unreadable-half'
  end
  -- the caster's last recorded aimed hit, if it is still the same swing
  local rec = swings[who]
  local inSwing = now > 0 and rec ~= nil and now >= rec.at and now - rec.at <= M.SWING_WINDOW
  if role == 'none' and inSwing then
    -- No aim named, but a hit of this caster was already taken as the aimed one within the swing window: this is a
    -- swept hit whose aim the engine did not name. It takes half of the recorded amount, never the full hit.
    return math.max(1, math.floor(rec.amount * M.SWEEP_SHARE)), 'none-of-aimed'
  end
  if role == 'aimed' or role == 'none' then
    local u = unmatched[who]
    if order == nil and u and now > 0 and now >= u and now - u <= M.SWING_WINDOW then
      sweptEvidence = sweptEvidence + 1
      say('order-seen-swept', string.format('sweep order seen swept-first gap=%d (%d of %d)', now - u, sweptEvidence, M.EVIDENCE))
      if sweptEvidence >= M.EVIDENCE then
        order = 'swept-first'
        say('order', string.format('sweep order=swept-first gap=%d', now - u))
      end
    end
    unmatched[who] = nil
    swings[who] = now > 0 and {at = now, amount = amount} or nil
    return amount, role
  end
  local usable = order ~= 'swept-first' and inSwing
  if usable then
    if order == nil and not rec.counted then
      rec.counted = true              -- one observation per swing, however many foes it swept
      aimedEvidence = aimedEvidence + 1
      say('order-seen-aimed', string.format('sweep order seen aimed-first gap=%d (%d of %d)', now - rec.at, aimedEvidence, M.EVIDENCE))
      if aimedEvidence >= M.EVIDENCE then
        order = 'aimed-first'
        say('order', string.format('sweep order=aimed-first gap=%d', now - rec.at))
      end
    end
    return math.max(1, math.floor(rec.amount * M.SWEEP_SHARE)), 'swept-of-aimed'
  end
  if order ~= 'aimed-first' then unmatched[who] = now end
  return math.max(1, math.floor(amount * M.SWEEP_SHARE)), 'swept-of-own'
end

-- For tests and for a new campaign.
function M.reset()
  swings, unmatched, order, logged, aimedEvidence, sweptEvidence = {}, {}, nil, {}, 0, 0
end

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
        local ok, new = pcall(M.decide, formula, caster, target, amount, clock())
        if ok and type(new) == 'number' then formula.target.removedHp = new end
      end
    end
    -- Native Attack Boost / Double Edged, then Proficiency and the later drain
    -- stages all see this amount, so recoil and leech stay consistent.
    return original(formula, caster, target, functions, classes, helpers)
  end
end

return M
