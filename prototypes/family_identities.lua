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
--   Marksman  Gun          shots cannot be parried (accuracy setup 22)
--   Afflict   Crossbow     on-hit status chance doubled (rate copy 171)
--   Spellbreak Mace        every hit burns the target's MP (post-damage 165)
--   Mend      Rod          healing spells heal more while a rod is held (heal amount 110)
--   Counterflow Pole       the wielder counters twice as often (counter roll 8)
--   Chain     Katana       combos run two hits longer (combo engine 10)
--   Cadence   Fists        consecutive hits on the same target hit harder (post-damage 165)
--
-- Swords (evade), greatswords (sweep), hammers (Disable), hand-bombs (Slow/Oil), bows (crit),
-- daggers (speed), measures (buffs), staves (element potency) and guns' defence bypass are
-- already live as data or native behaviour; this module adds the ten missing behaviours.
local M = {}
M.version = '0.1.1'

M.ATTACK = 150
M.SUMMON_MIN = 27  -- identifiers 27-39 are the Espers; guests and party wield, summons do not

M.PIERCE_FACTOR = 0.7        -- Spear: 30% of Defence ignored per hit (Expose's relative weight)
M.MEND_FACTOR = 1.2          -- Rod: healing spells +20% (about half of Faith, always on)
M.COUNTERFLOW_FACTOR = 2     -- Pole: twice the counter chance the Counter licence grants
M.CHAIN_EXTRA = 2            -- Katana: two extra hits once a combo rolls
M.CHAIN_MAX = 11             -- the engine's own PC cap (function 10)
M.AFFLICT_FACTOR = 2         -- Crossbow: double the on-hit status chance
M.AFFLICT_CAP = 100
M.SPELLBREAK_SHARE = 0.1     -- Mace: burn 10% of the hit's damage as MP
M.SPELLBREAK_MIN = 3
M.SPELLBREAK_CAP = 0.1       -- never more than 10% of the target's max MP in one hit
M.SUNDER_STEP = 0.05         -- Axe: -5% of the fight's starting Defence per hit
M.SUNDER_FLOOR = 0.5         -- never below half of it, however long the fight runs
M.CADENCE_STEP = 0.06        -- Fists: +6% damage per consecutive hit on the same target
M.CADENCE_CAP = 5            -- at most five steps (+30%); a target switch restarts the count

-- Per-fight dent memory for Sunder, keyed by target address with a maxHp/level fingerprint, so a
-- recycled address in a later fight re-baselines instead of inheriting a stale dent. Cadence keeps
-- one streak per caster. Both clear on save load, new game and map jump.
local dents = {}
local cadence = {}
function M.reset() dents = {} cadence = {} end

local REPORT_LIMIT = 32
local reported = 0

local function int(v) return type(v) == 'number' and v % 1 == 0 and v >= 0 end

-- A character-controlled wielder: the party and guests, never foes, never Esper summons.
function M.isWielder(unit)
  return type(unit) == 'table' and unit.type == 0 and int(unit.identifier) and unit.identifier < M.SUMMON_MIN
end

-- The common gate: a Legacy campaign in memory, a wielder, and (unless wantAttack is false) an
-- ordinary Attack with the named family's weapon.
function M.applies(formula, caster, category, deps, wantAttack)
  if deps.active() ~= true then return false end
  if not M.isWielder(caster) then return false end
  if wantAttack ~= false and formula.action ~= M.ATTACK then return false end
  return deps.category(caster) == category
end

-- ---------- Sunder: the axe grind ----------

-- Dent one target's Defence toward half of what it started the fight with. Returns what changed or
-- nil. Called after the damage function ran, so this hit still meets full Defence; the next one does not.
function M.dent(target)
  local before = target.defense
  if type(before) ~= 'number' or before <= 0 or target.type ~= 1 then return nil end
  local address = target.address
  if type(address) ~= 'number' or address <= 0 then return nil end
  local entry = dents[address]
  -- A defence above the recorded baseline means the fight reset the unit (new battle at this address,
  -- or a native restore): re-baseline. A lower value is our own grind, or Expose, and keeps the entry.
  if not entry or entry.hp ~= target.maxHp or entry.level ~= target.level or entry.baseline < before then
    entry = {baseline = before, hp = target.maxHp, level = target.level}
    dents[address] = entry
  end
  local floor = entry.baseline * M.SUNDER_FLOOR
  if before <= floor then return nil end
  local after = math.max(before - entry.baseline * M.SUNDER_STEP, floor)
  target.defense = after
  return before, after
end

-- ---------- wraps (each Forge adapter composes one of these around its original) ----------

-- Axe, fam function 68: original sets this hit's power and Defence term, then the dent lands for
-- everything that comes after. Hand-bombs share function 68 and are excluded by the category gate.
function M.wrap68(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, before, after = pcall(function()
      if not M.applies(formula, caster, 'axe', deps) then return nil end
      return M.dent(formula.target)
    end)
    if ok and before and deps.report then
      pcall(deps.report, 'sunder', caster.identifier, formula.target.identifier, before, after)
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
    local ok, pierced = pcall(function()
      return M.applies(formula, caster, 'spear', deps) and formula.outcomeType ~= 10
        and type(formula.modifier) == 'number' and formula.modifier > 0
    end)
    if not ok or not pierced then return inner(formula, caster, target, functions, classes, helpers) end
    local before = formula.modifier
    formula.modifier = before * M.PIERCE_FACTOR
    local result = table.pack(inner(formula, caster, target, functions, classes, helpers))
    formula.modifier = before
    if deps.report then pcall(deps.report, 'pierce', caster.identifier, formula.target.identifier, before) end
    return table.unpack(result, 1, result.n)
  end
end

-- Ninja sword and gun, accuracy setup 22: the original copies the target's evasion into the formula;
-- Unseen then drops weapon and shield evasion (parries stay), Marksman drops parries. Runs after the
-- Proficiency adapter in the same file, which uses the same fields for the Samurai's Haste aim.
local ZERO = {ninjaSword = {'evadeWeapon', 'evadeShield'}, gun = {'evadeParry'}}
function M.wrap22(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, kind = pcall(function()
      if M.applies(formula, caster, 'ninjaSword', deps) then return 'ninjaSword' end
      if M.applies(formula, caster, 'gun', deps) then return 'gun' end
      return nil
    end)
    if ok and kind then
      for _, field in ipairs(ZERO[kind]) do formula[field] = 0 end
      if deps.report then pcall(deps.report, kind == 'gun' and 'marksman' or 'unseen', caster.identifier) end
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
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, before = pcall(function()
      if not M.applies(formula, caster, 'crossbow', deps, false) then return nil end
      local rate = formula.onHitRate
      if type(rate) ~= 'number' or rate <= 0 then return nil end
      return rate
    end)
    if ok and before then
      formula.onHitRate = math.min(M.AFFLICT_CAP, before * M.AFFLICT_FACTOR)
      if deps.report then pcall(deps.report, 'afflict', caster.identifier, before, formula.onHitRate) end
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Mace and fists, post-damage 165: two decisions that both need the final damage of a landed hit,
-- so they share the outermost wrap of the 165 adapter. Spellbreak burns MP as a share of the hit —
-- on top of whatever MP loss an earlier stage (the Green Mage's leech) already queued, never past
-- the foe's remaining MP, capped by the foe's own pool. Cadence is the fists' ramp: consecutive
-- hits on the same target land harder, and switching targets restarts the count.
function M.wrap165(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
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
        local keep = type(classes) == 'table' and type(classes.getBattleUnitKeep) == 'function'
          and classes.getBattleUnitKeep(target.address) or nil
        local maxMp = keep and keep.maxMp or nil
        if type(maxMp) ~= 'number' or maxMp <= 0 or target.currentMp <= 0 then return nil end
        local queued = (formula.target.mpUpdateState == 1) and (formula.target.removedMp or 0) or 0
        local room = target.currentMp - queued
        if room <= 0 then return nil end
        local wanted = math.max(M.SPELLBREAK_MIN, math.floor(damage * M.SPELLBREAK_SHARE))
        local cap = math.floor(maxMp * M.SPELLBREAK_CAP)
        -- the caller adds the burn to the queued loss, so a Green Mage's leech and the burn stack
        return 'spellbreak', math.min(wanted, cap, room)
      elseif category == 'unarmed' then
        -- Damage known, so the ramp applies to the hit that earns it, like Proficiency's own scaling.
        local m = cadence[caster.address]
        if not m or m.targetId ~= formula.target.identifier then
          m = {targetId = formula.target.identifier, count = 0}
          cadence[caster.address] = m
        end
        m.count = m.count + 1
        local steps = math.min(m.count - 1, M.CADENCE_CAP)
        return 'cadence', steps > 0 and 1 + steps * M.CADENCE_STEP or nil
      end
      return nil
    end)
    if ok and kind == 'spellbreak' and (value or 0) > 0 then
      -- add the burn to any queued MP loss on the target; caster-side gains are not ours to touch
      local queued = (formula.target.mpUpdateState == 1) and (formula.target.removedMp or 0) or 0
      formula.target.mpUpdateState, formula.target.addedMp = 1, 0
      formula.target.removedMp = queued + value
      if deps.report then pcall(deps.report, 'spellbreak', caster.identifier, formula.target.identifier, value) end
    elseif ok and kind == 'cadence' and value then
      -- scale the settled damage of this hit
      formula.target.removedHp = math.floor(formula.target.removedHp * value)
      if deps.report then pcall(deps.report, 'cadence', caster.identifier, formula.target.identifier, value) end
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Rod, heal amount 110: the wielder's healing spells land for more. Items heal through function 127,
-- not here, so the Chemist's potions stay native. Skips a reversed target so Mend never amplifies a
-- backfire. Composes inside the Proficiency adapter, whose overflow logic then sees the boosted heal.
function M.mendWrap(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local ok, gate = pcall(function()
      return M.applies(formula, caster, 'rod', deps, false)
        and target.type == 0 and target.statusEffects.reverse ~= 1
    end)
    if not ok or not gate then return original(formula, caster, target, functions, classes, helpers) end
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    -- Scale what the original landed, not the input; a zero heal stays zero.
    local okAfter, heal = pcall(function() return formula.target.addedHp end)
    if okAfter and type(heal) == 'number' and heal > 0 then
      formula.target.addedHp = math.floor(heal * M.MEND_FACTOR)
      if deps.report then pcall(deps.report, 'mend', caster.identifier, heal, formula.target.addedHp) end
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Pole, counter roll 8: the original rolls the defender's counter chance, so Counterflow moves the
-- input before the roll. The defender holds the pole, which makes this the one wrap that gates on the
-- formula's target; the attacker may be anyone who was unwise enough to strike a Mystic.
function M.wrap8(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local ok, before = pcall(function()
      local defender = formula.target
      if not M.isWielder(defender) or deps.active() ~= true then return nil end
      if deps.category(defender) ~= 'pole' then return nil end
      local chance = formula.counterChance
      if type(chance) ~= 'number' or chance <= 0 then return nil end
      return chance
    end)
    if ok and before then formula.counterChance = math.min(100, before * M.COUNTERFLOW_FACTOR) end
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    if ok and before and deps.report then pcall(deps.report, 'counterflow', formula.target.identifier, before) end
    return table.unpack(result, 1, result.n)
  end
end

-- Katana, combo engine 10: the original rolls the combo and stores its length on the caster's keep;
-- Chain extends a rolled katana combo by two hits, inside the engine's own cap. Combo rate stays the
-- weapon's own data (already the highest of any family).
function M.wrap10(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, before = pcall(function()
      if not M.applies(formula, caster, 'katana', deps) then return nil end
      local flags = formula.caster.keep.battleFlags
      local count = flags.comboCount
      if type(count) ~= 'number' or count <= 0 then return nil end
      return count
    end)
    if ok and before then
      local after = math.min(M.CHAIN_MAX, before + M.CHAIN_EXTRA)
      if after > before then
        formula.caster.keep.battleFlags.comboCount = after
        if deps.report then pcall(deps.report, 'chain', caster.identifier, before, after) end
      end
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

local deps = nil

-- Section 13's category byte, and the name the module's decisions speak in.
M.CATEGORY_NAMES = {'unarmed', 'sword', 'greatsword', 'katana', 'ninjaSword', 'spear', 'pole', 'bow',
                    'crossbow', 'gun', 'axe', 'hammer', 'dagger', 'rod', 'staff', 'mace', 'measure', 'handBomb'}

-- Build the shared deps and register the resets once; every adapter calls this on load.
function M.register()
  if deps then return true end
  if type(event) ~= 'table' or type(event.registerEventSync) ~= 'function' then return false end
  local saveLoad = event.registerEventSync('onSaveLoad', function() M.reset() reported = 0 return true end)
  local newGame = event.registerEventSync('onNewGameStart', function() M.reset() reported = 0 return true end)
  local jump = type(event.registerEventAsync) == 'function'
    and event.registerEventAsync('onMapJump', function() M.reset() return true end) or false
  if saveLoad ~= true or newGame ~= true or jump ~= true then
    -- A partially registered reset is harmless (it only clears module state); retry on the next load.
    return false
  end
  deps = {
    active = function() return legacyMode() end,
    category = function(unit)
      local weapon = unit.weapon
      if type(weapon) ~= 'number' or weapon < 0x1000 or weapon >= 0xFFFF then return nil end
      local record = bpack.section13[weapon - 0x1000]
      local index = record and record.category
      if type(index) ~= 'number' then return nil end
      return M.CATEGORY_NAMES[index + 1]
    end,
    report = function(kind, ...)
      if reported >= REPORT_LIMIT then return end
      reported = reported + 1
      print('Legacy Family: ' .. kind .. ' ' .. table.concat({ ... }, ' '))
    end,
  }
  pcall(print, 'Legacy Family: identities ' .. M.version .. ' ready (ten families; bonuses=on)')
  return true
end

function M.deps() return deps end

return M
