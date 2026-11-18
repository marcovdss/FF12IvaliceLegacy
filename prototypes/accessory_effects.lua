-- Accessory effects: accessories are wild cards outside Proficiency. Each effect is a stage in the hybrid stage format
-- (event, conditions, effects), but worn rather than trained: fixed values, no Clan rank, no Writ, and it never reads
-- the job. The Proficiency runtime (proficiency_runtime.lua) dispatches the wearer's stages from the same Forge hooks
-- and applies the result through R.apply, so every proven budget holds for accessories too: statuses go through the
-- immunity checks (R.mayAdd) and the duration rule (a shorter grant never cuts a longer one), charge pushes through the
-- per-foe allowance (no accessory pushes), and no accessory returns MP or grants Bravery.
--
-- House rules (Marco, 2026-11-16: "Haste is haste", "nothing hurts its own wearer"):
--   * A worn status counts everywhere the game asks for it: no accessory hides its status from any job or hybrid.
--   * An accessory grants a status at most once per fight, never refreshes it (a grant is skipped while the status is
--     held), and never grants Bravery. Hermes Sandals' opening Haste is the one such grant.
--   * No accessory debuffs or harms its own wearer, whatever their role.
--
-- Rules:
--   * Each accessory counts once, in either slot (the same id in both slots runs its stages once).
--   * One accessory damage rider per action: when two would raise one action's damage, only the larger applies.
--   * At most one accessory charge bonus per action (the larger).
--   * No accessory stage copies a Proficiency stage: the same event, the same conditions and the same first effect.
--     attach() drops any stage that copies a hybrid stage and says so in the log (the base lines are checked offline).
--
-- Party memory (per foe, cleared on load and map change): the element of the last party hit or damage spell on it and
-- who dealt it, who landed each ailment it holds, a Spotter mark, and which Oil a Gillie Boots wearer already used.
local A = {version = '0.2.0'}

A.ID = {rubicante = 4437, barbariccia = 4438, mistflow = 4440, hyperWrist = 4444, braver = 4451, elmdore = 4464,
        indigo = 4462, cameo = 4468, nishijin = 4469, shinobi = 4472, hermes = 4473, gillie = 4474}

local h = 0.5
-- Stage = {event, conditions (all must hold) or nil, effects}. Effects: {'quick', percent} (your next action charges
-- faster), {'dmg', percent}, {'foe', status, durationScale}, {'self', status, durationScale, fresh = true},
-- {'mark'} (Spotter), {'dmgNext', percent, seconds} (your next damage spell), {'heal', percent} (the wearer heals that
-- share of its own max HP, never past full).
A.STAGES = {
  -- Hermes Sandals, Opening stride: at the start of each fight the wearer gains a real, full-length Haste unless it is
  -- already Hasted. The Haste is an ordinary Haste: every job and hybrid reads it (Zanshin II and III, the Hasted hybrid
  -- stages), and when it ends their own Haste triggers take over. Once per fight, whoever the fight starts with.
  [4473] = {{'fightStart', nil, {{'self', 'haste', 1, fresh = true}}}},
  -- Elmdore Fang, Drinker: each attack you land on a foe holding Sap or Poison heals you 3% of your max HP. Once per
  -- attack: a combo's follow-up hits do not count, and neither do the other foes a sweep (a greatsword swing) reaches.
  [4464] = {{'hit', {'sapOrPoison', 'notComboFollow', 'oncePerSwing'}, {{'heal', 3}}}},
  -- Hyper Wrist, Opportunist: your hit on a foe holding an ailment an ally landed, or a foe an ally Broke or Staggered
  -- (0.1.44, decisions 2 and 3).
  [4444] = {{'hit', {'allyAilment'}, {{'quick', 10}}}},
  -- Braver Gloves, Rescue: a foe's physical hit drops an ally to critical HP.
  [4451] = {{'ally', nil, {{'quick', 25}}}},
  -- Nishijin Belt, Burst: your damage spell on the element another member hit that foe with in the last 5 s.
  [4469] = {{'magick', {'allyElement'}, {{'dmg', 20}}}},
  -- Shinobi Tabi, Vanish: once per fight, a physical hit that drops you to critical HP turns you Invisible (the timed
  -- status, which the game ends when you act and a damaging magick ends too).
  [4472] = {{'struck', {'dropCritical'}, {{'self', 'invisible', 1, fresh = true}}, once = 'fight'}},
  -- Gillie Boots, Kindling: your first hit on an Oiled foe, once per Oil, Slows it (half duration).
  [4474] = {{'hit', {'oiled', 'firstOnOil'}, {{'foe', 'slow', h}}}},
  -- Cameo Belt, Spotter: a combo follow-up or a critical hit marks the foe (0.1.43; before, any hit); the next attack on
  -- it by another member that rolls a dodge cannot be dodged (Forge 34). Shots that never roll a dodge (formula 26
  -- crossbows, guns) leave the mark for someone else.
  [4468] = {{'hit', {'comboOrCrit'}, {{'mark'}}}},
  -- Rubicante Ring, Spellcatcher: a foe's spell you bounce with Reflect powers your next damage spell within 10 s.
  [4437] = {{'reflected', nil, {{'dmgNext', 25, 10}}}},
}
A.ELEMENT_WINDOW = 5000000     -- clock units (microseconds): Burst reads an ally's element hit up to 5 s old
A.MARK_WINDOW = 6000000        -- Spotter mark
A.SWING_WINDOW = 100000         -- one Attack's hits on several foes are processed together (Drinker heals once per swing)
A.FIGHT_GAP = 30000000         -- no foe seen for 30 s: the next foe starts a new fight (Vanish and Hermes are once per fight)
A.INDIGO_SCALE = 0.5           -- Indigo Pendant: ailments its wearer's Vitality-checked spells land last half as long
A.SPILL_SHARE = 10             -- Mistflow: the overflow heals the most hurt ally, at most 10% of that ally's max HP

local foe, wearer = {}, {}
local lastHit = {}   -- party member address -> {target, at}: the member's previous hit, for combo follow-ups
local fight, lastFoe = 1, nil
local dispatched = 0     -- the fight the fightStart event was last sent for
A.log = function() end

function A.reset()
  foe, wearer = {}, {}
  lastHit = {}
  fight, lastFoe = fight + 1, nil
end

-- A foe was seen in a fight at `now`; a long quiet gap starts a new fight.
function A.touch(now)
  if type(now) ~= 'number' then return end
  if lastFoe ~= nil and now - lastFoe > A.FIGHT_GAP then fight = fight + 1 end
  lastFoe = now
end
function A.fight() return fight end
-- True once per fight: the caller then sends 'fightStart' to every party member's stages. A new fight (a 30 s gap, a
-- load or a map change) is a new number, so it is true again.
function A.fightStarted()
  if dispatched == fight then return false end
  dispatched = fight
  return true
end

local function foeMem(address)
  local m = foe[address]
  if not m then m = {ailments = {}}; foe[address] = m end
  return m
end
local function wearerMem(address)
  local m = wearer[address]
  if not m then m = {}; wearer[address] = m end
  return m
end

-- ---------- party memory ----------
-- The element mask of a party member's damaging hit or spell on a foe, and who dealt it.
function A.noteElement(target, by, mask, now)
  if type(mask) ~= 'number' or mask == 0 or type(target) ~= 'number' then return end
  foeMem(target).element = {mask = mask, by = by, at = now}
end
-- A party member landed ailment `id` on a foe (its own hit or spell, or a Proficiency effect it caused).
function A.noteAilment(target, id, by, now)
  if type(target) ~= 'number' or type(id) ~= 'number' then return end
  foeMem(target).ailments[id] = {by = by, at = now}
end
-- What a hit saw on the foe: a held ailment that is gone is forgotten, and a foe without Oil can be kindled again.
function A.noteStatus(target, status)
  local m = foe[target]
  if not m then return end
  for id in pairs(m.ailments) do
    local name = A.NAME[id]
    if name and not status[name] then m.ailments[id] = nil end
  end
  if not status.oil then m.kindled = nil end
end

-- A party member hit a foe: true when it is a combo follow-up, the same member on the same foe again within `window`
-- clock units (the Proficiency runtime's COMBO_WINDOW). Called for every party hit, whoever wears what.
function A.noteHit(by, target, now, window)
  if type(by) ~= 'number' or type(target) ~= 'number' or type(now) ~= 'number' then return false end
  local prev = lastHit[by]
  lastHit[by] = {target = target, at = now}
  return prev ~= nil and prev.target == target and type(window) == 'number' and now - prev.at < window
end

-- ---------- conditions ----------
local LOCAL = {}
-- This hit is a combo follow-up or a critical hit (not critical HP: that is the hybrid condition of the same name).
function LOCAL.comboOrCrit(s)
  return s.hitCombo == true or s.hitCritical == true
end
-- The foe holds Sap or Poison (Drinker). `targetStatus` is the foe's status set as the hit saw it.
function LOCAL.sapOrPoison(s)
  local t = s.targetStatus or {}
  return t.sap == true or t.poison == true
end
-- The wearer has not already drunk for this swing: a sweep that reaches several Sapped or Poisoned foes heals once.
function LOCAL.oncePerSwing(s)
  local drank = wearerMem(s.self).drank
  return not (drank and type(s.now) == 'number' and s.now >= drank and s.now - drank <= A.SWING_WINDOW)
end
-- This hit starts its Attack: a combo's follow-up hits (the same member on the same foe within the combo window) do not.
function LOCAL.notComboFollow(s)
  return s.hitCombo ~= true
end
-- The foe holds an ailment that another party member landed, or another member Broke or Staggered it (0.1.44: the
-- runtime sets allyBroken and allyStaggered from the family module's Broken clock and the push budget).
function LOCAL.allyAilment(s)
  if s.allyBroken == true or s.allyStaggered == true then return true end
  local m = foe[s.target]
  if not m then return false end
  for id, who in pairs(m.ailments) do
    local name = A.NAME[id]
    if name and s.targetStatus[name] and who.by ~= s.self and A.HINDERED[name] then return true end
  end
  return false
end
-- Another party member hit this foe with an element of `mask` within the window (also Resonance I's party rhythm, 0.1.44).
function A.partyElement(target, by, mask, now)
  local m = foe[target]
  local e = m and m.element
  return e ~= nil and e.by ~= by and type(mask) == 'number' and mask & e.mask ~= 0
    and type(now) == 'number' and now - e.at <= A.ELEMENT_WINDOW
end
-- Another party member hit this foe with an element of this spell within the window.
function LOCAL.allyElement(s)
  return A.partyElement(s.target, s.self, s.element, s.now)
end
-- This Oil has not been kindled yet (the Oil an ally's landing recorded, or the one the foe already held).
function LOCAL.firstOnOil(s)
  local m = foeMem(s.target)
  local landed = m.ailments[A.OIL]
  local key = landed and landed.at or 'held'
  return m.kindled ~= key, key
end

local function uses(stage, name)
  for _, c in ipairs(stage[2] or {}) do if c == name then return true end end
  return false
end

local function holds(stage, s, ev)
  for _, c in ipairs(stage[2] or {}) do
    local fn = LOCAL[c]
    local ok
    if fn then ok = fn(s) else ok = A.H.cond(c, s, {}, ev) end
    if not ok then return false end
  end
  return true
end

-- ---------- effects ----------
local function add(a, field, id, scale)
  a[field] = a[field] or {}
  a[field][id] = math.max(a[field][id] or 0, scale or 1)
end
local function other(a, o) a.others = a.others or {}; a.others[#a.others + 1] = o end
local REACTION = {struck = true, ally = true, reflected = true, fightStart = true}

local function apply(fx, ev, s, a)
  local S = A.S
  local op = fx[1]
  if op == 'quick' then a.casterCharge = math.max(a.casterCharge or 0, fx[2] / 100)
  elseif op == 'dmg' then a.accessoryDamage = math.max(a.accessoryDamage or 1, 1 + fx[2] / 100)
  elseif op == 'foe' then
    if s.target and not s.targetStatus[fx[2]] and s.canLand(s.target, S[fx[2]]) then add(a, 'targetAdd', S[fx[2]], fx[3]) end
  elseif op == 'self' then
    if not (fx.fresh and s.casterStatus[fx[2]]) then
      if REACTION[ev] then other(a, {op = 'add', unit = s.self, id = S[fx[2]], scale = fx[3] or 1})
      else add(a, 'casterAdd', S[fx[2]], fx[3]) end
    end
  elseif op == 'heal' then
    -- the wearer's own max HP, never past full: a heal at full HP writes nothing
    local max, now = s.selfMaxHp, s.casterHp
    wearerMem(s.self).drank = s.now     -- this swing has had its heal, whether or not HP was missing
    if type(max) == 'number' and type(now) == 'number' then
      local amount = math.min(math.floor(max * fx[2] / 100), max - now)
      if amount > 0 then other(a, {op = 'hp', unit = s.self, amount = amount}) end
    end
  elseif op == 'mark' then
    if s.target then foeMem(s.target).mark = {by = s.self, at = s.now} end
  elseif op == 'dmgNext' then
    wearerMem(s.self).boost = {pct = fx[2], untilAt = s.now + fx[3] * 1000000}
  else error('no accessory effect ' .. tostring(op)) end
end

-- The worn accessory ids that carry an effect, each once (`main`, `second` = the two slots).
function A.worn(main, second)
  local out, seen = {}, {}
  for _, id in ipairs({main, second}) do
    if type(id) == 'number' and not seen[id] and (A.STAGES[id] or A.PASSIVE[id]) then seen[id] = true; out[#out + 1] = id end
  end
  return out
end
A.PASSIVE = {[4438] = true, [4440] = true, [4462] = true}

function A.wears(s, id)
  for _, w in ipairs(s.ids or {}) do if w == id then return true end end
  return false
end

-- Decide the wearer's stages for one event. Returns the action table R.apply takes, or nil.
function A.decide(ev, s)
  local a = {}
  local fired = {}
  for _, id in ipairs(s.ids or {}) do
    for _, stage in ipairs(A.STAGES[id] or {}) do
      if stage[1] == ev and not stage.dropped then
        local w = wearerMem(s.self)
        local once = stage.once == 'fight' and w['once' .. id] == fight
        if not once and holds(stage, s, ev) then
          for _, fx in ipairs(stage[3]) do apply(fx, ev, s, a) end
          if stage.once == 'fight' then w['once' .. id] = fight end
          if uses(stage, 'firstOnOil') then
            local _, key = LOCAL.firstOnOil(s)
            foeMem(s.target).kindled = key
          end
          fired[#fired + 1] = id
        end
      end
    end
  end
  -- Spellcatcher's payoff: the next damage spell within its window, one cast (every target of that cast).
  if ev == 'magick' and A.wears(s, A.ID.rubicante) then
    local w = wearerMem(s.self)
    local b = w.boost
    if b and type(s.now) == 'number' and s.now <= b.untilAt then
      a.accessoryDamage = math.max(a.accessoryDamage or 1, 1 + b.pct / 100)
      w.boostCast, w.boostCastPct = s.castId, b.pct
      w.boost = nil
      fired[#fired + 1] = A.ID.rubicante
    elseif not b and s.castId ~= nil and w.boostCast == s.castId and w.boostCastPct then
      -- a later target of the cast that spent the boost
      a.accessoryDamage = math.max(a.accessoryDamage or 1, 1 + w.boostCastPct / 100)
    elseif b and type(s.now) == 'number' and s.now > b.untilAt then
      w.boost = nil
    end
    -- a later, different cast forgets the cast that spent the boost
    if not w.boost and s.castId ~= nil and w.boostCast ~= nil and w.boostCast ~= s.castId then
      w.boostCast, w.boostCastPct = nil, nil
    end
  end
  -- One accessory damage rider per action: the larger one, as a damage scale R.apply understands.
  if a.accessoryDamage then a.damage, a.accessoryDamage = a.accessoryDamage, nil end
  if next(a) == nil and #fired == 0 then return nil end
  a.fired = fired
  return a
end

-- Spotter: an attack that rolls a dodge comes from `by`; true (and the mark is spent) when another member marked the foe.
function A.spotted(target, by, now)
  local m = foe[target]
  local mark = m and m.mark
  if not mark then return false end
  if type(now) ~= 'number' or now - mark.at > A.MARK_WINDOW then m.mark = nil; return false end
  if mark.by == by then return false end
  m.mark = nil
  return true
end

-- Spellcatcher: a foe's spell bounced off the wearer.
function A.reflected(s)
  return A.decide('reflected', s)
end

-- Mistflow, Spill: healing that overflows the wearer's max HP heals the most hurt other ally for the overflow, at most
-- SPILL_SHARE % of that ally's max HP. Returns {unit, amount} or nil.
function A.spill(s, overflow)
  if type(overflow) ~= 'number' or overflow <= 0 then return nil end
  local best
  for _, p in ipairs(s.party or {}) do
    if p.address ~= s.self and p.hpPct < 100 and (not best or p.hpPct < best.hpPct) then best = p end
  end
  if not best or type(best.maxHp) ~= 'number' or best.maxHp <= 0 then return nil end
  local amount = math.min(math.floor(overflow), math.max(1, best.maxHp * A.SPILL_SHARE // 100),
                          best.maxHp - best.hp)
  if amount <= 0 then return nil end
  return {unit = best.address, amount = amount}
end

-- Barbariccia Ring, Stormcaller: the wearer's own magick keeps the weather and terrain bonuses (x1.2) Forge 94 gives
-- and skips its penalties (x0.5). Forge 94 skips every rule for a Weatherproof caster, so the adapter asks for the
-- bonuses here. `has(element)` reads the action's elements; weather, wind and terrain are the values Forge 94 reads.
A.WEATHER_BONUS = {
  {'fire', function(w, wind, t) return wind == 2 end},
  {'lightning', function(w, wind, t) return w == 0 or t == 0 end},
  {'ice', function(w, wind, t) return w == 2 or t == 2 end},
  {'earth', function(w, wind, t) return w == 1 or t == 1 end},
  {'water', function(w, wind, t) return w == 3 or t == 0 end},
  {'wind', function(w, wind, t) return wind == 2 end},
}
function A.weatherBonus(has, weather, wind, terrain)
  local factor = 1
  for _, rule in ipairs(A.WEATHER_BONUS) do
    if has(rule[1]) and rule[2](weather, wind, terrain) then factor = factor * 1.2 end
  end
  return factor
end

-- ---------- copies ----------
local function hears(spec, ev)
  if type(spec) == 'table' then
    for _, e in ipairs(spec) do if e == ev then return true end end
    return false
  end
  return spec == ev
end
local function key(conds)
  local c = {}
  for _, x in ipairs(conds or {}) do c[#c + 1] = x end
  table.sort(c)
  return table.concat(c, ',')
end
local function firstEffect(effects)
  local fx = effects[1] or {}
  return tostring(fx[1]) .. ':' .. (type(fx[2]) == 'string' and fx[2] or '')
end
-- The accessory stages that copy a hybrid stage (same event, same conditions, same first effect).
function A.copies(lines)
  local out = {}
  for id, stages in pairs(A.STAGES) do
    for k, stage in ipairs(stages) do
      for i, line in ipairs(lines) do
        for n = 1, 3 do
          local hs = line[n]
          if hs and hears(hs[1], stage[1]) and key(hs[2]) == key(stage[2]) and firstEffect(hs[5]) == firstEffect(stage[3]) then
            out[#out + 1] = {id = id, stage = k, line = i - 1, level = n}
          end
        end
      end
    end
  end
  return out
end

function A.attach(H, P)
  A.H, A.S = H, P.STATUS
  A.OIL = P.STATUS.oil
  A.NAME, A.HINDERED = {}, {}
  for name, id in pairs(P.STATUS) do A.NAME[id] = name end
  for _, name in ipairs(P.HINDERED) do A.HINDERED[name] = true end
  A.NAME[P.DISEASE or 16] = 'disease'
  for _, c in ipairs(A.copies(H.LINES)) do
    A.STAGES[c.id][c.stage].dropped = true
    A.log(string.format('accessory %d stage %d copies hybrid line %d stage %d: not used', c.id, c.stage, c.line, c.level))
  end
  return A
end

return A
