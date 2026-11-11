-- Family identities: every weapon family's own style of play, always on for any wielder.
-- Design: reports/LEGACY-WEAPON-FAMILIARITY-2026-11-09.md (the approved Stage 1 1b table, finished).
-- Proficiency stays separate: it follows the character's job; a family identity follows the weapon.
--
-- One decision per family, each at the smallest Forge stage that expresses it. The original Forge
-- function always runs first (except Counterflow, which must move a roll's input before the roll).
-- Every decision is pcall-guarded: a broken reader never stops Forge or changes a native hit.
--
--   Sunder    Axe          each hit dents the target's Defence for the fight (fam function 68)
--   Pierce    Spear        every hit ignores a part of Defence (combine 113)
--   Unseen    Ninja sword  hits ignore weapon and shield evasion (accuracy setup 22)
--   True aim  Gun          a gunner's shots never miss, Blind or not (accuracy roll 44; replaces Marksman, whose
--                          evadeParry write was read by no function of formula 27)
--   Afflict   Crossbow     on-hit status chance doubled (rate copy 171)
--   Spellbreak Mace        every hit burns the target's MP (post-damage 165)
--   Mend      Rod          healing spells heal more while a rod is held (heal amount 110)
--   Counterflow Pole       the wielder counters without Counter, twice as often with it, and every blow
--                          taken without answering raises the next chance (counter roll 8)
--   Chain     Katana       combos run two hits longer (combo engine 10)
--   Cadence   Fists        consecutive Attacks on the same target hit harder and charge faster (post-damage 165)
--
-- One shared defence rule lives here too, because it needs the same gate and deps:
--   Guard fatigue  any party member: each attack it blocks, parries or dodges in a row leaves it 25% less
--                  evasion against the next; a hit that lands resets it (evade rolls 41, 42, 43)
-- and three family costs that live on the same evade rolls (every family cuts both ways, Marco 2026-11-10):
--   Braced    Sword        each parry or block pushes the wielder's own next charge back 10%
--   Open      Katana       after a katana combo, the next attack on the Samurai meets no evasion
--   Long haft Spear        a spear never parries (its weapon evade, the parry roll, is 0), except to counter (needs the Counter augment)
--   Reckless  Axe          each axe Attack lowers the wielder's own Defense 5% (floor -25%) against foes' hits, back to
--                          normal 10 s after the last swing; tracked here, applied in damage_formula (axe Attack: 68)
-- and the rod's cost: a heal that overflows draws Lure to the caster (heal amount 110).
--
-- Swords (evade), greatswords (sweep), hammers (Disable), hand-bombs (Slow/Oil), bows (crit),
-- daggers (speed), measures (buffs), staves (element potency) and guns' defence bypass are
-- already live as data or native behaviour; this module adds the ten missing behaviours.
--
-- Forge hands every function three different objects, and the decisions below read each for what it is:
--   caster, target    the battle unit keeps (live stats: type, identifier, weapon, defense, currentMp ...)
--   formula           this hit's work record (power, modifier, evade*, onHitRate, counterChance ...)
--   formula.target    this hit's result for the target (removedHp, addedHp, removedMp, and the stat slots
--                     such as defense that the engine applies after the hit; -1 = no change)
-- Forge's class objects raise on any field they do not define, so a wrong object is a dead identity,
-- never a crash (each decision is pcall-guarded). 0.2.0 is the first version that fires in game: 0.1.x
-- adapters never passed the shared deps, and four decisions read the wrong object.
local M = {}
M.version = '0.5.0'

M.ATTACK = 150
M.SUMMON_MIN = 27  -- identifiers 27-39 are the Espers; guests and party wield, summons do not

M.PIERCE_FACTOR = 0.7        -- Spear: 30% of Defence ignored per hit (Expose's relative weight)
M.MEND_FACTOR = 1.2          -- Rod: healing spells +20% (about half of Faith, always on)
M.COUNTERFLOW_FACTOR = 2     -- Pole: twice the counter chance the Counter licence grants
M.COUNTERFLOW_BASE = 2       -- Pole without Counter: Speed / 2, the chance the Counter augment itself grants
M.COUNTERFLOW_STEP = 10      -- every blow taken without countering adds 10 to the next chance (flow)
M.COUNTERFLOW_CAP = 75       -- without the Counter augment the chance never passes 75; with it, 100
M.COUNTERFLOW_WINDOW = 20000000  -- flow fades after 20 s without a blow (event clock, microseconds)
M.CHAIN_EXTRA = 1            -- Katana: one extra hit once a combo rolls (0.5.0; was 2)
M.CHAIN_MAX = 11             -- the engine's own PC cap (function 10)
M.AFFLICT_FACTOR = 2         -- Crossbow: double the on-hit status chance
M.AFFLICT_CAP = 100
M.SPELLBREAK_SHARE = 0.1     -- Mace: burn 10% of the hit's damage as MP
M.SPELLBREAK_MIN = 3
M.SPELLBREAK_CAP = 0.1       -- never more than 10% of the target's max MP in one hit
M.SUNDER_STEP = 0.05         -- Axe: -5% of the fight's starting Defence per hit
M.SUNDER_FLOOR = 0.5         -- never below half of it, however long the fight runs
M.CADENCE_STEP = 0.06        -- Fists: +6% damage per consecutive Attack on the same target
M.CADENCE_HASTE = 0.05       -- and the next action charges 5% faster per step
M.CADENCE_CAP = 5            -- at most five steps (+30% damage, next charge 25% faster)
M.CADENCE_WINDOW = 10000000  -- 10 s without striking that target breaks the streak
M.COMBO_WINDOW = 350000      -- hits closer than this are one Attack's combo (Proficiency's own window)
M.FATIGUE_CUT = 0.25         -- Guard fatigue: each avoid in a row cuts shield, weapon and parry evasion by 25%
M.FATIGUE_MAX = 4            -- four in a row and the next attack meets no evasion at all
M.FATIGUE_WINDOW = 10000000  -- 10 s without being attacked and the guard is fresh again
M.BRACED_PUSH = 0.10         -- Sword: a parry or block costs 10% of the wielder's own charge
M.OPEN_WINDOW = 10000000     -- Katana: the opening a combo leaves lasts until the next Attack, 10 s at most
M.RECKLESS_STEP = 0.05       -- Axe: the wielder's Defense counts 5% lower per fresh axe Attack
M.RECKLESS_FLOOR = 0.75      -- never below 75% (-25%)
M.RECKLESS_WINDOW = 10000000 -- forgotten after 10 s without an axe Attack
M.LURE = 17                  -- Forge status bit (helpers/statusEffectBits)
M.LURE_SCALE = 0.25          -- Rod: the Lure an overflowing heal draws lasts a quarter of its full duration
M.COUNTER_PARRY_EVADE = 30   -- weapon evade a rolled counter gets so the swing can happen (Forge 41: roll < evade); the pole's top band

-- Per-fight dent memory for Sunder, keyed by target address with a maxHp/level fingerprint, so a
-- recycled address in a later fight re-baselines instead of inheriting a stale dent. Cadence keeps
-- one streak per caster, Counterflow one flow per defender. All clear on save load, new game and map jump.
local dents = {}
local cadence = {}
local flows = {}
local fatigue = {}
-- Keeps Counterflow has lent the Counter augment to (see wrap8). Deliberately NOT cleared by M.reset: party keep
-- addresses outlive a map jump or a load, and the loan must still be taken back when that member stops holding a pole.
local lent = {}
-- Samurai left open by a katana combo: keep address -> clock when it opened (consumed by one attack on it).
local open = {}
local pendingCharge = {}
-- Reckless: party axe wielder's swings taken so far, keep address -> {steps, at}. Lua state only: nothing is written to
-- the unit, nothing is saved; the damage formula reads it when a foe hits the wielder.
local reckless = {}
function M.reset()
  dents = {} cadence = {} flows = {} fatigue = {} pendingCharge = {} open = {} reckless = {}
end

-- Log at most REPORT_LIMIT lines per identity per load, so a frequent one (guns) cannot hide the rest.
-- 24 ran out within two minutes of play (2026-11-10); 150 covers a long session for the rarer effects.
local REPORT_LIMIT = 150
local reported = {}
-- Identities whose wrap already logged a failure this load: a broken reader prints one line, not one per hit.
local failed = {}
local function clearLog() reported = {} failed = {} end

-- One throttled error line per failed wrap pcall (per identity, per load), so "nothing applied" and "dead
-- identity" read differently in the log. Never raises.
function M.fail(deps, identity, err)
  if failed[identity] then return end
  failed[identity] = true
  local report = deps and deps.report
  if report then pcall(report, 'error', identity, (tostring(err):gsub('[%z\r\n]', ' '))) end
end

-- 'weapon=<id> offhand=<id>' for the family lines, so play shows what was actually held (dual wield, shields).
function M.who(unit)
  local ok, text = pcall(function() return 'weapon=' .. tostring(unit.weapon) .. ' offhand=' .. tostring(unit.offhand) end)
  return ok and text or 'weapon=? offhand=?'
end

-- The deps every adapter shares, built once by M.register(). A wrap given no deps uses these, read at
-- call time, so an adapter never has to thread them through.
local registered = nil

local function int(v) return type(v) == 'number' and v % 1 == 0 and v >= 0 end

-- A character-controlled wielder: the party and guests, never foes, never Esper summons.
function M.isWielder(unit)
  return type(unit) == 'table' and unit.type == 0 and int(unit.identifier) and unit.identifier < M.SUMMON_MIN
end

-- The common gate: a Legacy campaign in memory, a wielder, and (unless wantAttack is false) an
-- ordinary Attack with the named family's weapon.
function M.applies(formula, caster, category, deps, wantAttack)
  deps = deps or registered
  if deps.active() ~= true then return false end
  if not M.isWielder(caster) then return false end
  if wantAttack ~= false and formula.action ~= M.ATTACK then return false end
  return deps.category(caster) == category
end

-- ---------- Sunder: the axe grind ----------

-- Dent one target's Defence toward half of what it started the fight with. Returns what changed or
-- nil. `target` is the foe's keep (its live Defence); `result` is formula.target, whose defense slot the
-- engine applies after the hit, exactly as Expose (function 138) lowers Defence. Called after the damage
-- function ran, so this hit still meets full Defence; the next one reads the dented keep.
function M.dent(target, result)
  if target.type ~= 1 then return nil end
  local before = target.defense
  if type(before) ~= 'number' or before <= 0 then return nil end
  local address = target.address
  if type(address) ~= 'number' or address <= 0 then return nil end
  local entry = dents[address]
  -- A defence above the recorded baseline means the fight reset the unit (new battle at this address,
  -- or a native restore): re-baseline. A lower value is our own grind, or Expose, and keeps the entry.
  if not entry or entry.hp ~= target.maxHp or entry.level ~= target.level or entry.baseline < before then
    entry = {baseline = before, hp = target.maxHp, level = target.level}
    dents[address] = entry
  end
  local floor = math.ceil(entry.baseline * M.SUNDER_FLOOR)
  if before <= floor then return nil end
  local after = math.max(math.floor(before - math.max(1, entry.baseline * M.SUNDER_STEP)), floor)
  -- Another stage of this hit may already have queued a lower Defence (an Expose rider): keep the lower.
  local queued = result.defense
  if type(queued) == 'number' and queued >= 0 and queued <= after then return nil end
  result.defense = after
  return before, after
end

-- ---------- wraps (each Forge adapter composes one of these around its original) ----------

-- Reckless: the axe's own cost, a mirror of Sunder on the wielder. Each fresh axe Attack on a foe (a combo's
-- follow-up hits within COMBO_WINDOW share their Attack) adds a step; each step makes the wielder's Defense count 5% lower
-- against foes' hits, up to -25%, and the steps are forgotten after 10 s without an axe Attack, on load, new game and map
-- jump. The dent is applied by damage_formula (M.recklessDefense) on a foe's hit on the wielder, never by writing a stat:
-- Forge 323 ignores caster stat slots for party members, and a keep write would need refreshStats outside the hit.
-- Returns the new dent in percent when it grew, else nil.
local function recklessSteps(address, now)
  local entry = reckless[address]
  if entry and now > 0 and now - entry.at > M.RECKLESS_WINDOW then
    reckless[address] = nil
    entry = nil
  end
  return entry
end

local function maxSteps() return math.ceil((1 - M.RECKLESS_FLOOR) / M.RECKLESS_STEP - 1e-9) end

function M.recklessStep(caster, deps)
  local address = caster.address
  if type(address) ~= 'number' or address <= 0 then return nil end
  local now = deps.clock and deps.clock() or 0
  if type(now) ~= 'number' then now = 0 end
  local entry = recklessSteps(address, now)
  if not entry then
    entry = {steps = 0, at = now, fresh = true}
    reckless[address] = entry
  end
  local fresh = entry.steps == 0 or now == 0 or now - entry.at >= M.COMBO_WINDOW
  entry.at = now
  if not fresh or entry.steps >= maxSteps() then return nil end
  entry.steps = entry.steps + 1
  return math.floor(entry.steps * M.RECKLESS_STEP * 100 + 0.5)
end

-- The wielder's dent as a fraction (0 to 0.25), 0 when there is none, it has expired or the wielder no longer holds an axe.
function M.recklessDent(unit, deps)
  deps = deps or registered
  if not M.isWielder(unit) or deps.active() ~= true or deps.category(unit) ~= 'axe' then return 0 end
  local now = deps.clock and deps.clock() or 0
  if type(now) ~= 'number' then now = 0 end
  local entry = recklessSteps(unit.address, now)
  if not entry then return 0 end
  return math.min(entry.steps * M.RECKLESS_STEP, 1 - M.RECKLESS_FLOOR)
end

-- The Defense a foe's hit meets on `unit`: `defense` lowered by the unit's Reckless dent. Logs a dented hit.
-- Called by damage_formula (Forge 113 and 126) before its floor rule, only for a foe striking a party member.
function M.recklessDefense(unit, defense, deps)
  deps = deps or registered
  if type(defense) ~= 'number' or defense <= 0 then return defense end
  local dent = M.recklessDent(unit, deps)
  if dent <= 0 then return defense end
  local after = defense * (1 - dent)
  if deps.report then
    pcall(deps.report, 'reckless-hit', unit.identifier, 'def ' .. defense .. '->' .. after, M.who(unit))
  end
  return after
end

-- Axe, fam function 68: original sets this hit's power and Defence term, then the dent lands for
-- everything that comes after. Hand-bombs share function 68 and are excluded by the category gate.
-- Reckless runs after Sunder in the same gate.
function M.wrap68(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, before, after = pcall(function()
      if not M.applies(formula, caster, 'axe', deps) then return nil end
      return M.dent(target, formula.target)
    end)
    if ok and before and deps.report then
      pcall(deps.report, 'sunder', caster.identifier, target.identifier, before, after, M.who(caster))
    elseif not ok then
      M.fail(deps, 'sunder', before)
    end
    local okR, dent = pcall(function()
      if target.type ~= 1 or not M.applies(formula, caster, 'axe', deps) then return nil end
      return M.recklessStep(caster, deps)
    end)
    if okR and dent then
      if deps.report then pcall(deps.report, 'reckless', caster.identifier, 'dent=' .. dent .. '%', M.who(caster)) end
    elseif not okR then
      M.fail(deps, 'reckless', dent)
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Spear, combine 113: scale the Defence term before the inner chain reads it, restore afterwards so
-- every later observer sees the native fields. Sits outside the damage-floor wrapper, so the floor
-- judges the pierced Defence (and then almost never binds).
function M.pierceWrap(inner, deps)
  assert(type(inner) == 'function', 'inner function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local ok, pierced = pcall(function()
      return M.applies(formula, caster, 'spear', deps) and formula.outcomeType ~= 10
        and type(formula.modifier) == 'number' and formula.modifier > 0
    end)
    if not ok then M.fail(deps, 'pierce', pierced) end
    if not ok or not pierced then return inner(formula, caster, target, functions, classes, helpers) end
    local before = formula.modifier
    formula.modifier = before * M.PIERCE_FACTOR
    local result = table.pack(inner(formula, caster, target, functions, classes, helpers))
    formula.modifier = before
    if deps.report then
      local okD, damage = pcall(function() return formula.target.removedHp end)
      pcall(deps.report, 'pierce', caster.identifier, target.identifier, before, 'removed=' .. tostring(okD and damage or '?'),
            M.who(caster))
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Ninja sword, accuracy setup 22: the original copies the target's evasion into the formula; Unseen then drops
-- weapon and shield evasion (the foe can't parry or block it). Runs after the Proficiency adapter in the same file,
-- which uses the same fields for the Samurai's Haste aim. Logs only when it actually removed something.
local ZERO = {'evadeWeapon', 'evadeShield'}
function M.wrap22(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, removed = pcall(function()
      if not M.applies(formula, caster, 'ninjaSword', deps) then return nil end
      local gone = {}
      for _, field in ipairs(ZERO) do
        local v = formula[field]
        if type(v) == 'number' and v > 0 then gone[#gone + 1] = field .. '=' .. v end
        formula[field] = 0
      end
      return #gone > 0 and table.concat(gone, ' ') or nil
    end)
    if ok and removed and deps.report then
      pcall(deps.report, 'unseen', caster.identifier, target.identifier, removed, M.who(caster))
    elseif not ok then
      M.fail(deps, 'unseen', removed)
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Gun, accuracy roll 44 (formula 27's whole evade stage: Forge 44 is only `accuracyRate <= random(100)`, outcome 6).
-- True aim: a party gunner's Attack never misses, and Blind (Forge 33 halves accuracyRate) does not spoil it. The
-- roll runs unchanged and a miss it produced is taken back, so only a miss is ever undone and the log line means
-- "a miss was prevented". The body's dodge is not touched (a gun has no evade roll at all; see the Guard fatigue note).
function M.wrap44(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local okGate, before = pcall(function()
      if not M.applies(formula, caster, 'gun', deps) then return nil end
      return {skip = formula.skipState, outcome = formula.outcomeType, rate = formula.accuracyRate}
    end)
    if not okGate then M.fail(deps, 'true-aim', before) end
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    if okGate and before then
      local ok, prevented = pcall(function()
        if formula.skipState == 1 and formula.outcomeType == 6 and before.skip ~= 1 then
          formula.skipState, formula.outcomeType = before.skip, before.outcome
          return true
        end
        return false
      end)
      if ok and prevented and deps.report then
        pcall(deps.report, 'true-aim', caster.identifier, target.identifier, 'rate=' .. tostring(before.rate), M.who(caster))
      elseif not ok then
        M.fail(deps, 'true-aim', prevented)
      end
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Crossbow, rate copy 171: the original loads the formula's on-hit rate from weapon or bolt; Afflict
-- doubles it (cap 100) before function 175 rolls. Any hit delivering the crossbow's status counts,
-- so no action gate: the delivery is the family's nature.
function M.wrap171(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, before = pcall(function()
      if not M.applies(formula, caster, 'crossbow', deps, false) then return nil end
      local rate = formula.onHitRate
      if type(rate) ~= 'number' or rate <= 0 then return nil end
      return rate
    end)
    if ok and before then
      formula.onHitRate = math.min(M.AFFLICT_CAP, before * M.AFFLICT_FACTOR)
      if deps.report then pcall(deps.report, 'afflict', caster.identifier, before, formula.onHitRate, M.who(caster)) end
    elseif not ok then
      M.fail(deps, 'afflict', before)
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Mace and fists, post-damage 165: two decisions that both need the final damage of a landed hit,
-- so they share the outermost wrap of the 165 adapter. Spellbreak burns MP as a share of the hit —
-- on top of whatever MP loss an earlier stage (the Green Mage's leech) already queued, never past
-- the foe's remaining MP, capped by the foe's own pool. Cadence is the fists' ramp: consecutive
-- hits on the same target land harder, and switching targets restarts the count.
-- One landed fists Attack hit on `target`: returns {steps, fresh}. steps is how many earlier Attacks of the
-- streak landed on this same foe (0 on the first, at most CADENCE_CAP); fresh is false for a combo's
-- follow-up hits, which share their Attack's steps and neither add a step nor hasten again. The foe is its
-- keep address with a maxHp fingerprint, so a recycled address is a new foe. Without a clock (0) every
-- hit counts as fresh and the window never closes.
function M.cadenceStep(caster, target, deps)
  local now = deps.clock and deps.clock() or 0
  if type(now) ~= 'number' then now = 0 end
  local m = cadence[caster.address]
  local same = m and m.target == target.address and m.hp == target.maxHp
    and (now == 0 or now - m.at <= M.CADENCE_WINDOW)
  if not same then
    m = {target = target.address, hp = target.maxHp, hits = 0, at = now}
    cadence[caster.address] = m
  end
  local fresh = m.hits == 0 or now == 0 or now - m.at >= M.COMBO_WINDOW
  if fresh then m.hits = m.hits + 1 end
  m.at = now
  -- completed: the Attack that fills the streak (step CADENCE_CAP), once per streak
  local completed = fresh and m.hits - 1 >= M.CADENCE_CAP and not m.completed
  if completed then m.completed = true end
  return {steps = math.min(m.hits - 1, M.CADENCE_CAP), fresh = fresh, completed = completed}
end

function M.wrap165(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, kind, value = pcall(function()
      if not M.isWielder(caster) or deps.active() ~= true or formula.action ~= M.ATTACK then
        return nil
      end
      if target.type ~= 1 then return nil end
      local damage = formula.target.removedHp
      if type(damage) ~= 'number' or damage <= 0 then return nil end
      local category = deps.category(caster)
      if category == 'mace' then
        local maxMp = target.maxMp
        if type(maxMp) ~= 'number' or maxMp <= 0 or target.currentMp <= 0 then return nil end
        local queued = (formula.target.mpUpdateState == 1) and (formula.target.removedMp or 0) or 0
        local room = target.currentMp - queued
        if room <= 0 then return nil end
        local wanted = math.max(M.SPELLBREAK_MIN, math.floor(damage * M.SPELLBREAK_SHARE))
        local cap = math.floor(maxMp * M.SPELLBREAK_CAP)
        -- the caller adds the burn to the queued loss, so a Green Mage's leech and the burn stack
        return 'spellbreak', math.min(wanted, cap, room)
      elseif category == 'unarmed' then
        return 'cadence', M.cadenceStep(caster, target, deps)
      end
      return nil
    end)
    if ok and kind == 'spellbreak' and (value or 0) > 0 then
      -- add the burn to any queued MP loss on the target; caster-side gains are not ours to touch
      -- an MP gain queued on the foe by another stage stays (324 nets addedMp - removedMp)
      local queued = (formula.target.mpUpdateState == 1) and (formula.target.removedMp or 0) or 0
      formula.target.mpUpdateState = 1
      formula.target.removedMp = queued + value
      if deps.report then
        pcall(deps.report, 'spellbreak', caster.identifier, formula.target.identifier, value,
              'damage=' .. tostring(formula.target.removedHp), M.who(caster))
      end
    elseif ok and kind == 'cadence' and value and value.steps > 0 then
      -- scale the settled damage of this hit; a fresh Attack also hastens the fighter's next charge
      formula.target.removedHp = math.floor(formula.target.removedHp * (1 + value.steps * M.CADENCE_STEP))
      local haste = value.fresh and value.steps * M.CADENCE_HASTE or 0
      if haste > 0 and deps.armCharge then pcall(deps.armCharge, caster.address, haste, classes, helpers) end
      if deps.report then
        pcall(deps.report, 'cadence', caster.identifier, target.identifier, value.steps,
              value.fresh and 'fresh' or 'combo', formula.target.removedHp, M.who(caster))
      end
      -- a completed streak is one counter for Ki Flow and the hybrid counter stages (Proficiency sets the callback)
      if value.completed and type(M.onFullCadence) == 'function' then
        local okCb, err = pcall(M.onFullCadence, caster, target)
        if not okCb then M.fail(deps, 'cadence-callback', err) end
        if deps.report then pcall(deps.report, 'cadence-full', caster.identifier, target.identifier, okCb and 'ok' or 'failed') end
      end
    elseif not ok then
      M.fail(deps, 'wrap165', kind)
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Rod, heal amount 110: the wielder's healing spells land for more. Items heal through function 127,
-- not here, so the Chemist's potions stay native. Function 110 sends a heal to addedHp only on its heal
-- branch (target not reversed; the undead the other way round) and a backfire to removedHp; Mend asks the
-- same question of formula.target afterwards, so it never amplifies a backfire. Composes inside the
-- Proficiency adapter, whose overflow logic then sees the boosted heal.
local function healBranch(result)
  local reversed = result.statusEffects.reverse == 1
  if result.classification == 13 then return reversed end
  return not reversed
end

-- Queue Lure on the caster when `heal` is more than the target's missing HP. 323 commits formula.caster's status
-- slots for any unit type. A longer Lure already running or queued is kept; a caster that can't take it is left alone.
-- Returns the duration queued, or nil.
function M.lure(formula, caster, target, helpers, heal)
  local missing = target.maxHp - target.currentHp
  if heal <= missing then return nil end
  local id = M.LURE
  if caster.permanentStatusEffectImmunities[id] == 1 or caster.permanentStatusEffects[id] == 1 then return nil end
  local plus = formula.caster
  local duration = math.max(1, math.floor(helpers.getStatusEffectDuration(caster.address, id) * M.LURE_SCALE))
  if caster.temporaryStatusEffects[id] == 1 and caster.statusEffectDurations[id] >= duration then return nil end
  if plus.addedStatusEffects[id] == 1 then
    local queued = plus.statusEffectDurations[id]
    if queued == -1 or queued >= duration then return nil end
  end
  plus.addedStatusEffects[id] = 1
  plus.statusEffectDurations[id] = duration
  return duration
end

function M.mendWrap(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local ok, gate = pcall(function()
      return M.applies(formula, caster, 'rod', deps, false) and target.type == 0
    end)
    if not ok or not gate then return original(formula, caster, target, functions, classes, helpers) end
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    -- Scale what the original landed, not the input; a zero heal stays zero.
    local okAfter, heal = pcall(function()
      return healBranch(formula.target) and formula.target.addedHp or nil
    end)
    if okAfter and type(heal) == 'number' and heal > 0 then
      formula.target.addedHp = math.floor(heal * M.MEND_FACTOR)
      if deps.report then pcall(deps.report, 'mend', caster.identifier, heal, formula.target.addedHp, M.who(caster)) end
      -- The rod's cost: a heal that overflows draws Lure to the caster, for a quarter of its full duration.
      local okL, lured = pcall(M.lure, formula, caster, target, helpers, math.floor(formula.target.addedHp))
      if okL and lured and deps.report then
        pcall(deps.report, 'lure', caster.identifier, target.identifier, lured, M.who(caster))
      elseif not okL then
        M.fail(deps, 'lure', lured)
      end
    elseif not okAfter then
      M.fail(deps, 'mend', heal)
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Pole, counter roll 8: the original rolls the defender's counter chance, so Counterflow moves the
-- input before the roll. The defender holds the pole, which makes this the one wrap that gates on the
-- target's keep; the attacker may be anyone who was unwise enough to strike a Mystic.
-- A pole counters at the chance the Counter licence grants (Speed / 2); with the licence that chance doubles.
-- Flow: every blow the wielder takes without countering adds COUNTERFLOW_STEP to the next chance, and a
-- counter spends it. Forge 8 rolls only while the defender stands in battle stance, so blows taken
-- mid-action still build flow for the next one. Stop, Sleep and Disable forbid counters and build nothing.
--
-- The loan (0.3.1): a counter roll alone is not a counter. In 0.2.1-0.3.0 the roll succeeded (counterState 1,
-- and Proficiency's counter lines fired: Hell Knight's Immobilize landed) but the game never swung, because the
-- engine performs a counter only for a unit that holds the Counter augment (Marco, 2026-11-10: "no Counter text").
-- So a pole wielder without the licence is lent the augment's bit in its temporary augments while it holds the
-- pole, and the bit is taken back the first time it is struck holding anything else (that blow's stale chance is
-- zeroed before the roll). The licence itself lives in the permanent augments and is never touched.
-- A licence is the permanent Counter augment, or a temporary one this module did not lend (a hybrid reaction, say).
local function realCounter(keep)
  if keep.permanentAugments.counter == 1 then return true end
  return keep.temporaryAugments.counter == 1 and not lent[keep.address]
end

local function takeBack(keep, formula)
  if not lent[keep.address] then return end
  if keep.permanentAugments.counter == 1 then lent[keep.address] = nil return end
  lent[keep.address] = nil
  keep.temporaryAugments.counter = 0
  -- Forge 7 already read the lent bit for this blow: a member who no longer holds a pole must not counter on it.
  if formula and formula.target.augments.counter == 1 then formula.counterChance = 0 end
end

function M.wrap8(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local ok, before, after, flow = pcall(function()
      local defender = target
      if not M.isWielder(defender) then return nil end
      local pole = deps.active() == true and deps.category(defender) == 'pole'
      if not pole then takeBack(defender, formula) return nil end
      local chance = formula.counterChance
      if type(chance) ~= 'number' or chance < 0 then return nil end
      local status = formula.target.statusEffects
      if status.stop == 1 or status.sleep == 1 or status.disable == 1 then return nil end
      local own = defender.speed // M.COUNTERFLOW_BASE
      if type(own) ~= 'number' or own <= 0 then return nil end
      local base, cap
      if realCounter(defender) then
        base, cap = (chance > 0 and chance or own) * M.COUNTERFLOW_FACTOR, 100
      else
        base, cap = own, M.COUNTERFLOW_CAP
        if defender.temporaryAugments.counter ~= 1 then
          defender.temporaryAugments.counter = 1
          lent[defender.address] = true
        end
      end
      local now = deps.clock and deps.clock() or 0
      if type(now) ~= 'number' then now = 0 end
      local f = flows[defender.address]
      if not f or (now > 0 and now - f.at > M.COUNTERFLOW_WINDOW) then
        f = {stacks = 0, at = now}
        flows[defender.address] = f
      end
      f.at = now
      return chance, math.min(cap, base + f.stacks * M.COUNTERFLOW_STEP), f
    end)
    if not ok then M.fail(deps, 'counterflow', before) end
    if ok and before and after then formula.counterChance = after end
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    if ok and before and after then
      local okState, state = pcall(function() return formula.counterState end)
      local countered = okState and state == 1
      local stacks = flow.stacks
      flow.stacks = countered and 0 or math.min(flow.stacks + 1, 100)
      if deps.report then
        local okStance, stance = pcall(function()
          return formula.target.battleUnitWork.battleFlags.isInBattleStance
        end)
        local okOut, outcome = pcall(function() return formula.outcomeType end)
        pcall(deps.report, countered and 'counter' or 'counterflow', target.identifier, before, after,
              okState and state or '?', 'flow=' .. stacks, 'stance=' .. tostring(okStance and stance or '?'),
              lent[target.address] and 'lent' or 'licence', 'outcome=' .. tostring(okOut and outcome or '?'),
              M.who(target))
      end
    end
    return table.unpack(result, 1, result.n)
  end
end

function M.lentCounters() local n = 0 for _ in pairs(lent) do n = n + 1 end return n end

-- Guard fatigue, evade rolls 41 (melee, with the counter outcomes), 42 (bows: formula 25, so the three crossbows on 25
-- too) and 43 (crossbows: formula 26). Guns (formula 27) have no evade roll at all, only Forge 44's accuracy check:
-- by now Forge 22/25, 33 and 34 have settled this attack's shield, weapon and parry evasion (Shield Block
-- licences included). Each roll runs one after another, so a shield plus a sword stacked to about two
-- thirds avoided, and a Decoy Knight took almost nothing (Basch, 2026-11-10: 26 of 81 Attacks landed, for
-- about 2% of his HP). Fatigue does not lower the first roll: a wall still turns a streak, but every avoid in
-- a row leaves 25% less of each evasion for the next attack, and four in a row leave none. A hit that lands
-- resets it, as does 10 s without being attacked. A plain accuracy miss (outcome 6) changes nothing.
-- Party members only: foes evade natively. 1-5 are the evade outcomes Forge 41-43 write (2 and 4 with a counter).
local EVADED = {[1] = true, [2] = true, [3] = true, [4] = true, [5] = true}

-- Braced: move the wielder's own charge back by BRACED_PUSH of what it needs, only while it is charging (a full or
-- empty timer is left alone). The same write Proficiency uses on foes' charge, here on the party member's own.
function M.brace(address, classes, helpers)
  local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(address))
  if w.address == 0 then return nil end
  local cur, req = w.currentChargeTime, w.requiredChargeTime
  if not (cur > 0 and cur < req) then return nil end
  local after = math.max(1, cur - math.floor(req * M.BRACED_PUSH))
  w.currentChargeTime = after
  return cur, after
end
local FIELDS = {'evadeShield', 'evadeWeapon', 'evadeParry'}
function M.guardWrap(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local ok, entry, stacks, shown, category, stance, parried = pcall(function()
      if not M.isWielder(target) or deps.active() ~= true then return nil end
      local now = deps.clock and deps.clock() or 0
      if type(now) ~= 'number' then now = 0 end
      local e = fatigue[target.address]
      if not e or (now > 0 and now - e.at > M.FATIGUE_WINDOW) then
        e = {stacks = 0, at = now}
        fatigue[target.address] = e
      end
      e.at = now
      local n = math.min(e.stacks, M.FATIGUE_MAX)
      local keep = math.max(0, 1 - n * M.FATIGUE_CUT)
      local parts = {}
      for _, field in ipairs(FIELDS) do
        local v = formula[field]
        if n > 0 and type(v) == 'number' and v > 0 then
          formula[field] = math.floor(v * keep)
          parts[#parts + 1] = field .. '=' .. v .. '>' .. formula[field]
        end
      end
      local category = deps.category(target)
      -- Long haft: a spear has no parry, except to counter. Forge's weapon roll (evadeWeapon, outcomes 1/2) is the
      -- parry; the body's roll (evadeParry, outcome 5) is a dodge and stays, as do shield blocks. A spear that rolled
      -- a counter (counterState 1 from Forge 8, which only a Counter licence lets it roll) keeps its parry, so it can
      -- parry and counter (outcome 2).
      if category == 'spear' and formula.counterState ~= 1 and type(formula.evadeWeapon) == 'number'
         and formula.evadeWeapon > 0 then
        parts[#parts + 1] = 'spear evadeWeapon=' .. formula.evadeWeapon .. '>0'
        formula.evadeWeapon = 0
      end
      -- Open: the attack after a katana combo meets no evasion at all. Forge 41-43 roll evasion only while the
      -- target stands in battle stance; an attack on a Samurai mid-action meets no roll and lands anyway, so it
      -- must not spend the opening. A stale opening (window passed) is dropped either way.
      local okS, inStance = pcall(function() return formula.target.battleUnitWork.battleFlags.isInBattleStance end)
      local stance = okS and inStance or '?'
      local opened = open[target.address]
      if opened and not (now == 0 or opened == 0 or now - opened <= M.OPEN_WINDOW) then
        open[target.address] = nil
      elseif opened and stance == 1 then
        open[target.address] = nil
        for _, field in ipairs(FIELDS) do formula[field] = 0 end
        parts[#parts + 1] = 'open'
        parts.opened = true
      end
      -- Counter-parry: "never parries, except to counter". Forge 41 rolls the weapon evade for a counter swing
      -- (outcome 2), so a defender whose weapon evade is 0 (a spear after Long haft, a bow, a hand-bomb, any weapon
      -- row with no evade) could never answer a blow it rolled a counter for. When Forge 8 has rolled one, that
      -- roll gets weapon evade 30 (the pole's top band), never lowered. Fists are left alone (their counters come from the Cadence streak) and an
      -- opened Samurai meets no evasion at all.
      if formula.counterState == 1 and category and category ~= 'unarmed' and not parts.opened then
        local evade = formula.evadeWeapon
        if category == 'spear' or category == 'bow' or category == 'handBomb' or evade == 0 then
          -- only ever raised: a weapon that already evades better keeps its own value
          if type(evade) == 'number' and evade < M.COUNTER_PARRY_EVADE then
            formula.evadeWeapon = M.COUNTER_PARRY_EVADE
            parts[#parts + 1] = 'counter-parry'
            parts.parried = true
          end
        end
      end
      return e, n, #parts > 0 and table.concat(parts, ' ') or nil, category, stance, parts.parried
    end)
    if not ok then M.fail(deps, 'guard', entry) end
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    if ok and entry then
      local okOut, outcome, skipped = pcall(function() return formula.outcomeType, formula.skipState end)
      if okOut then
        if skipped == 1 and EVADED[outcome] then
          entry.stacks = math.min(entry.stacks + 1, M.FATIGUE_MAX)
        elseif outcome ~= 6 then
          entry.stacks = 0
        end
      end
      if parried and deps.report then
        pcall(deps.report, 'counter-parry', target.identifier, M.who(target))
      end
      if shown and deps.report then
        pcall(deps.report, 'fatigue', target.identifier, stacks, shown, okOut and outcome or '?',
              'stance=' .. tostring(stance), M.who(target))
      end
      -- A swing that answers a blow (parry or block with a counter): the outcome the counter lines can't show.
      if okOut and skipped == 1 and (outcome == 2 or outcome == 4) and deps.report then
        pcall(deps.report, 'counter-swing', target.identifier, 'outcome=' .. outcome, category or '?', M.who(target))
      end
      -- Braced: a sword wielder that parries (weapon roll, outcomes 1/2) or blocks (shield, 3/4) loses 10% of its own
      -- charge. A dodge (5) is footwork and costs nothing. (0.4.0 counted dodges and missed sword parries.)
      if okOut and skipped == 1 and category == 'sword' and outcome >= 1 and outcome <= 4 then
        local okB, before, after = pcall(M.brace, target.address, classes, helpers)
        if okB and before and deps.report then pcall(deps.report, 'braced', target.identifier, before, after) end
      end
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Katana, combo engine 10: the original rolls the combo and stores its length on the caster's keep;
-- Chain extends a rolled katana combo by two hits, inside the engine's own cap. Function 10 also runs
-- for every follow-up hit of a running combo (battleUnitWork.comboCount > 0 counts the hits so far),
-- and the stored length must be extended once, on the hit that rolled it, or every combo would climb
-- to the cap. Combo rate stays the weapon's own data (already the highest of any family).
function M.wrap10(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local okFresh, fresh = pcall(function() return formula.caster.battleUnitWork.comboCount == 0 end)
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, before = pcall(function()
      if not okFresh or not fresh or formula.comboState ~= 1 then return nil end
      if not M.applies(formula, caster, 'katana', deps) then return nil end
      local flags = formula.caster.keep.battleFlags
      local count = flags.comboCount
      if type(count) ~= 'number' or count <= 0 then return nil end
      return count
    end)
    if okFresh and fresh then pcall(function()
      if M.applies(formula, caster, 'katana', deps) then open[caster.address] = nil end
    end) end
    if ok and before then
      local after = math.min(M.CHAIN_MAX, before + M.CHAIN_EXTRA)
      if after > before then
        formula.caster.keep.battleFlags.comboCount = after
        if deps.report then pcall(deps.report, 'chain', caster.identifier, before, after, M.who(caster)) end
      end
      -- Open: the combo commits the Samurai; the next attack aimed at it finds no guard.
      local now = deps.clock and deps.clock() or 0
      open[caster.address] = type(now) == 'number' and now or 0
    end
    return table.unpack(result, 1, result.n)
  end
end

-- ---------- shared runtime for the game adapters (not exercised by the tests) ----------

-- Byte-exact memory signature check (the Pursuit gate): true only while a Legacy campaign is loaded.
local function expected(address, text, wide)
  local count = #text * (wide and 2 or 1)
  local raw = memory.readArray(address, count)
  if type(raw) ~= 'table' and type(raw) ~= 'string' then return false end
  if #raw ~= count then return false end
  for i = 1, count do
    local byte = type(raw) == 'string' and raw:byte(i) or raw[i]
    local want = wide and (i % 2 == 0 and 0 or text:byte((i + 1) // 2)) or text:byte(i)
    if byte ~= want then return false end
  end
  return true
end

local function legacyMode()
  return expected(0x120000, 'MZ', false)
    and expected(0x120180, 'PE\0\0\x64\x86', false)
    and expected(0xD113D4, 'LEGCY_\0', false)
    and expected(0xD12200, '%sLEGCY_%03d\0', true)
end

-- Cadence's haste: the same charge write Proficiency's 'next action faster' uses (proven in play, log
-- "next action charge"). When the Attack lands the fighter's timer is at or past its end and is reset
-- afterwards, so the bonus waits for the next charge to start, then moves it forward (at most 10 s wait).
local CHARGE_POLL_MS, CHARGE_WAIT = 100, 10000000
local chargePolling = false
local function chargeTick()
  local now, any = event.getClock(), false
  for address, p in pairs(pendingCharge) do
    local okW, w = pcall(function() return p.classes.getBattleUnitWork(p.helpers.getBattleUnitWork(address)) end)
    if not okW or w.address == 0 or now > p.expires then pendingCharge[address] = nil
    else
      local cur, req = w.currentChargeTime, w.requiredChargeTime
      if cur > 0 and cur < req and (p.above or cur < p.last) then
        w.currentChargeTime = math.min(req - 1, cur + math.floor(req * p.fraction))
        if registered and registered.report then
          pcall(registered.report, 'cadence-charge', math.floor(p.fraction * 100 + 0.5), cur, req)
        end
        pendingCharge[address] = nil
      else
        p.last = cur
        any = true
      end
    end
  end
  chargePolling = any
  if any then event.executeAfterMs(CHARGE_POLL_MS, chargeTick) end
end
function M.armCharge(address, fraction, classes, helpers)
  if type(event) ~= 'table' or type(event.executeAfterMs) ~= 'function' or type(event.getClock) ~= 'function' then
    return false
  end
  local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(address))
  if w.address == 0 then return false end
  pendingCharge[address] = {fraction = fraction, expires = event.getClock() + CHARGE_WAIT, classes = classes,
                            helpers = helpers, above = w.currentChargeTime >= w.requiredChargeTime,
                            last = w.currentChargeTime}
  if not chargePolling then
    chargePolling = true
    event.executeAfterMs(CHARGE_POLL_MS, chargeTick)
  end
  return true
end

-- Section 13's category byte, and the name the module's decisions speak in.
M.CATEGORY_NAMES = {'unarmed', 'sword', 'greatsword', 'katana', 'ninjaSword', 'spear', 'pole', 'bow',
                    'crossbow', 'gun', 'axe', 'hammer', 'dagger', 'rod', 'staff', 'mace', 'measure', 'handBomb'}

-- One line per family in the Clan Primer's Traveler's Tips (patches/families_tip.json, built from this table's
-- wording by ivalice_legacy/clan_primer.py). Identity first, then the cost where a family has one.
-- The game language Legacy supports is English.
M.FAMILY_LINES = {
  unarmed = {'Cadence', 'each Attack in a row on one foe deals +6% more (up to +30%) and quickens the next action; a full streak counts as a counter for Ki Flow'},
  sword = {'Guard', 'the best weapon evade; a parry or block pushes your next charge back 10%'},
  greatsword = {'Sweep', 'attacking a foe cleaves a half circle around it for half damage'},
  katana = {'Chain', 'combos run one hit longer; after a combo, the next attack on you meets no evasion'},
  ninjaSword = {'Unseen', 'your hits can\'t be parried or blocked (the dodge still applies); Blind 10%, five also carry a second status at 10%; Glancing against armor'},
  spear = {'Pierce', 'every hit ignores 30% of Defense; a spear never parries, except to counter, and a counter needs the Counter augment'},
  pole = {'Counterflow', 'counters even without the Counter augment (the Battle Harness or Magepower Shishak), and blows taken build the next counter; strikes Magick Resist; its evade lets counters swing'},
  bow = {'Volley', 'the keenest critical chance among the ranged weapons'},
  crossbow = {'Afflict', 'the bolt\'s status chance doubled, up to 100%; charge 30'},
  gun = {'True aim', 'a gunner\'s shots never miss, Blind or not, and ignore Defense; damage depends on the gun alone'},
  axe = {'Sunder', 'every hit dents the foe\'s Defense 5% of its start for the rest of the fight, down to half; Reckless dents your own Defense 5% a swing, down to a quarter off'},
  hammer = {'Concussion', 'Disable 15% on hit, 30% knockback chance; a slow swing (charge 40)'},
  dagger = {'Swift', 'the fastest charge, frequent combos, a little evade; Glancing against armor'},
  rod = {'Mend', 'healing spells heal +20% while it is held; a heal that overflows draws Lure to the caster'},
  staff = {'Channel', 'element potency, and the most Magick of the caster weapons'},
  mace = {'Spellbreak', 'hits burn foe MP equal to 10% of the damage, at most 10% of its pool'},
  measure = {'Tempo', 'each measure grants one status to an ally it strikes: Protect, Shell, Bravery, Invisible, Haste or Bubble'},
  handBomb = {'Barrage', 'the bombs you load decide the status, 25% on hit; Chaos Bombs (Confuse) and Stun Bombs (Stop) set up Time Mage Detonate'},
}
function M.familyLine(name)
  if type(name) ~= 'string' then return nil end
  return M.FAMILY_LINES[name]
end

-- Build the shared deps and register the resets once; every adapter calls this on load.
function M.register()
  if registered then return true end
  if type(event) ~= 'table' or type(event.registerEventSync) ~= 'function' then return false end
  local saveLoad = event.registerEventSync('onSaveLoad', function() M.reset() clearLog() return true end)
  local newGame = event.registerEventSync('onNewGameStart', function() M.reset() clearLog() return true end)
  local jump = type(event.registerEventAsync) == 'function'
    and event.registerEventAsync('onMapJump', function() M.reset() return true end) or false
  if saveLoad ~= true or newGame ~= true or jump ~= true then
    -- A partially registered reset is harmless (it only clears module state); retry on the next load.
    return false
  end
  registered = {
    active = function() return legacyMode() end,
    clock = function() return type(event.getClock) == 'function' and event.getClock() or 0 end,
    armCharge = M.armCharge,
    category = function(unit)
      local weapon = unit.weapon
      if type(weapon) ~= 'number' or weapon < 0x1000 or weapon >= 0xFFFF then return nil end
      local record = bpack.section13[weapon - 0x1000]
      local index = record and record.category
      if type(index) ~= 'number' then return nil end
      return M.CATEGORY_NAMES[index + 1]
    end,
    report = function(kind, ...)
      local n = reported[kind] or 0
      if n >= REPORT_LIMIT then return end
      reported[kind] = n + 1
      local parts = {}
      for i = 1, select('#', ...) do parts[i] = tostring((select(i, ...))) end
      print('Legacy Family: ' .. kind .. ' ' .. table.concat(parts, ' '))
    end,
  }
  pcall(print, 'Legacy Family: identities ' .. M.version .. ' ready (ten families, true aim, reckless, lure; bonuses=on)')
  return true
end

function M.deps() return registered end

return M
