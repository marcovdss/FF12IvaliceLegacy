-- Family identities: every weapon family's own style of play, always on for any wielder.
-- Design: the approved Stage 1 1b table, finished.
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
--                  evasion against the next; a hit that lands resets it (evade rolls 41, 42, 43). Against a
--                  Blinded attacker an avoid adds no step (0.1.43). Foes tire too (0.1.44): a foe a party member
--                  attacks wears down the same way; a Regen holder's guard is fresh after 5 s, not 10
-- and the shared states other pillars read (0.1.44): Broken (an Armour or Magick Resist cut: Sunder,
-- Expose, Shear, a Brave spear hit, a Faithful staff spell) and Staggered (a foe's push budget is spent; the runtime owns it)
-- 0.6.0 (Marco, 2026-11-16, "downsides, never punishments"): the family costs that fired on the wielder's own success are
-- gone: Braced (a sword parry pushed the wielder's charge back), Open (a katana combo left the wielder without
-- evasion), Reckless (axe swings raised the damage the wielder takes), the rod's Lure (an overflowing heal drew foes)
-- and the Long haft rule (a spear's runtime parry zeroing, an exception to its own grid). Each family keeps a visible
-- downside in its data or matchup instead (see FAMILY_LINES), answered by the player's choices.
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
M.version = '0.6.0'

M.ATTACK = 150
M.SUMMON_MIN = 27  -- identifiers 27-39 are the Espers; guests and party wield, summons do not

M.PIERCE_FACTOR = 0.7        -- Spear: 30% of Defence ignored per hit (Expose's relative weight)
M.MEND_FACTOR = 1.2          -- Rod: healing spells +20% (about half of Faith, always on)
M.COUNTERFLOW_FACTOR = 2     -- Pole: twice the counter chance the Counter licence grants
M.COUNTERFLOW_BASE = 2       -- Pole without Counter: Speed / 2, the chance the Counter augment itself grants
M.COUNTERFLOW_BOOSTED = 2    -- ...or Speed x 2 with Counter Boost (Forge 7's own pair, so the item keeps its word: 4 times the chance)
M.COUNTER_BOOST = 'counterBoost'  -- Forge's name for the Counter Boost augment (classes/getAugments.lua bit 7; read in Forge 7)
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
M.SUNDER_FLOOR = 0.5         -- never below half of it, however long the fight runs; a hit there still Breaks (0.1.46)
M.CADENCE_STEP = 0.06        -- Fists: +6% damage per consecutive Attack on the same target
M.CADENCE_HASTE = 0.05       -- and the next action charges 5% faster per step
M.CADENCE_CAP = 5            -- at most five steps (+30% damage, next charge 25% faster)
M.CADENCE_WINDOW = 10000000  -- 10 s without striking that target breaks the streak
M.COMBO_WINDOW = 350000      -- hits closer than this are one Attack's combo (Proficiency's own window)
M.FATIGUE_CUT = 0.25         -- Guard fatigue: each avoid in a row cuts shield, weapon and parry evasion by 25%
M.FATIGUE_MAX = 4            -- four in a row and the next attack meets no evasion at all
M.FATIGUE_WINDOW = 10000000  -- 10 s without being attacked and the guard is fresh again
M.BROKEN_WINDOW = 10000000   -- Broken: a party member cut a foe's Defence or Magick Resist in the last 10 s
M.REGEN_WINDOW = 5000000     -- Guard fatigue: a Regen holder's guard is fresh after 5 s without being attacked (0.1.44)
M.TIRED_STEPS = 2            -- Unseen: a foe with this many fatigue steps loses its dodge to a ninja sword (0.1.44)
M.SUNDER_STAGGER = 2         -- Sunder: on a Staggered foe each hit dents twice as deep; the floor is unchanged (0.1.44)
M.INTERRUPT_PUSH = 0.10      -- True aim: a gun hit on a foe about to act pushes its charge back 10% (0.1.44)
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
local pendingCharge = {}
-- Broken (0.1.43): foe keep address -> {at, hp, level, by, byAt, renew}: the clock of the last Defence or Magick Resist cut a party member
-- landed on it (a fresh Sunder dent, Expose, Shear; 0.1.44 also a Brave spear hit and a Faithful staff spell; 0.1.46 also an
-- axe hit on a foe already at the Sunder floor, which renews it), the foe's
-- maxHp and level, and who did it (by) and when (byAt; 0.1.48), plus the axe user whose renewal kept an ally's credit
-- (renew). The fingerprint makes a recycled address in a later fight a new foe. Lua state only;
-- Grounding II, Pursuit I, the hybrid 'ailing' stages and Hyper Wrist read it through isBroken.
local broken = {}
function M.reset()
  dents = {} cadence = {} flows = {} fatigue = {} pendingCharge = {} broken = {}
  if M.resetProcs then M.resetProcs() end
end

-- A party member just cut this foe's Defence or Magick Resist (or, 0.1.44, a Brave spear hit or a Faithful staff spell Broke
-- it). `target` is the foe's keep; `by` is the party member's keep address (nil when unknown). Without a clock (0) nothing
-- is kept, so a missing clock can never make a foe Broken for the rest of the fight.
-- 0.1.47: `keepLive` (the axe) restarts the 10 s but keeps the breaker of a live entry (the same foe, Broken under
-- BROKEN_WINDOW ago, someone recorded), so an ally's Expose stays the ally's Break for Hyper Wrist; with no live entry,
-- or one that names nobody, `by` is recorded.
-- 0.1.48: the kept breaker's credit runs from its OWN Break (byAt), not from the last renewal: an ally stays the breaker
-- only while now - byAt <= BROKEN_WINDOW. After that the axe user's hits still renew Broken, credited to the axe user
-- (by = wielder, byAt = now). `renew` remembers the axe user who renewed a kept ally, for brokenBy.
function M.noteBroken(target, now, by, keepLive)
  if type(target) ~= 'table' or type(now) ~= 'number' or now <= 0 then return false end
  local address = target.address
  if type(address) ~= 'number' or address <= 0 then return false end
  local who = type(by) == 'number' and by or nil
  local old = keepLive and broken[address]
  if old and type(old.by) == 'number' and old.by ~= who and type(old.byAt) == 'number'
     and old.hp == target.maxHp and old.level == target.level
     and now >= old.at and now - old.at <= M.BROKEN_WINDOW
     and now >= old.byAt and now - old.byAt <= M.BROKEN_WINDOW then
    broken[address] = {at = now, hp = target.maxHp, level = target.level, by = old.by, byAt = old.byAt, renew = who}
    return true
  end
  broken[address] = {at = now, hp = target.maxHp, level = target.level, by = who, byAt = now}
  return true
end

-- The live entry for this foe: at most BROKEN_WINDOW old and the same foe (maxHp and level match). Anything else is dropped.
local function brokenEntry(target, now)
  if type(target) ~= 'table' or type(now) ~= 'number' or now <= 0 then return nil end
  local address = target.address
  local entry = broken[address]
  if not entry then return nil end
  if entry.hp ~= target.maxHp or entry.level ~= target.level or now - entry.at > M.BROKEN_WINDOW then
    broken[address] = nil
    return nil
  end
  return entry
end
function M.isBroken(target, now) return brokenEntry(target, now) ~= nil end
-- Who Broke the foe (a party member's keep address); nil when nobody is recorded or the foe is not Broken.
-- Who Broke the foe. 0.1.48: an ally kept by an axe renewal is the breaker only within BROKEN_WINDOW of the ally's own
-- Break; past that the entry (still live through the renewal) names the axe user who renewed it.
function M.brokenBy(target, now)
  local entry = brokenEntry(target, now)
  if not entry then return nil end
  if type(entry.byAt) == 'number' and now - entry.byAt > M.BROKEN_WINDOW then return entry.renew end
  return entry.by
end

-- Staggered (0.1.44): the Proficiency runtime owns the charge push budget. A foe whose budget for its current turn is spent
-- is Staggered until it acts. False when the runtime is not loaded or the foe cannot be read.
function M.isStaggered(target, classes, helpers)
  local R = rawget(_G, 'IvaliceLegacyProficiency')
  if type(R) ~= 'table' or type(R.foeStaggered) ~= 'function' then return false end
  local ok, staggered = pcall(R.foeStaggered, target.address, classes, helpers)
  return ok and staggered == true
end

-- Regen held by a unit (temporary, or an Auto-Regen accessory's permanent one).
function M.holdsRegen(unit)
  local ok, held = pcall(function() return unit.temporaryStatusEffects.regen == 1 end)
  if ok and held then return true end
  local okAuto, auto = pcall(function() return unit.permanentStatusEffects.regen == 1 end)
  return okAuto and auto == true
end

-- Log at most REPORT_LIMIT lines per identity per load, so a frequent one (guns) cannot hide the rest.
-- 24 ran out within two minutes of play (2026-11-10); 150 covers a long session for the rarer effects.
local REPORT_LIMIT = 150
local reported = {}
-- Identities whose wrap already logged a failure this load: a broken reader prints one line, not one per hit.
local failed = {}
local boostLoggedReset
local function clearLog() reported = {} failed = {} if boostLoggedReset then boostLoggedReset() end end

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

-- Dent one target's Defence toward half of what it started the fight with (`steps` = 2 on a Staggered foe: twice the
-- step, the same floor). Returns before, after when it dented; nil, nil, true when the foe already sits at the floor
-- (nothing more is dented; 0.1.46: the axe still renews Broken); nil otherwise.
-- `target` is the foe's keep (its live Defence); `result` is formula.target, whose defense slot the
-- engine applies after the hit, exactly as Expose (function 138) lowers Defence. Called after the damage
-- function ran, so this hit still meets full Defence; the next one reads the dented keep.
function M.dent(target, result, steps, now)
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
  if before <= floor then return nil, nil, true end   -- at the floor: no deeper dent (0.1.46: the caller renews Broken)
  local after = math.max(math.floor(before - math.max(1, entry.baseline * M.SUNDER_STEP * (steps or 1))), floor)
  -- Another stage of this hit may already have queued a lower Defence (an Expose rider): keep the lower.
  local queued = result.defense
  if type(queued) == 'number' and queued >= 0 and queued <= after then return nil end
  result.defense = after
  entry.pct = math.floor((1 - after / entry.baseline) * 100 + 0.5)   -- the HUD's "Defense -N%" (hudStates); read nowhere else
  entry.at = type(now) == 'number' and now > 0 and now or nil        -- when it was last deepened: the HUD hides a dent that old
  return before, after
end

-- ---------- wraps (each Forge adapter composes one of these around its original) ----------

-- Forge's evade and Safety outcomes (formula.outcomeType, proc work +0x24; 0.1.48): 1-4 weapon parry or shield block
-- (with or without a counter) and 5 the body evade, from the evade roll (Forge 41/42); 10 Safety (Forge 0). An
-- unreadable outcome is not an evade (the swing counts, as before 0.1.48). 0.1.49: Forge's onHitPre middleware 280
-- already stops the hit stage on every one of these (see wrap68), so M.evaded is a defensive guard.
M.EVADED = {[1] = true, [2] = true, [3] = true, [4] = true, [5] = true, [10] = true}
function M.evaded(formula)
  local ok, outcome = pcall(function() return formula.outcomeType end)
  return ok and M.EVADED[outcome] == true
end

-- Axe, fam function 68: original sets this hit's power and Defence term, then the dent lands for
-- everything that comes after. Hand-bombs share function 68 and are excluded by the category gate.
-- An evaded, missed or Safety-stopped swing never reaches 68 (0.1.49 note). The cast stage leaves formula.outcomeType at
-- 1-4 (a weapon parry or shield block, with or without a counter), 5 (the body evade) or 6 (the plain miss) from Forge
-- 41/42, or 10 from Forge 0 (Safety). In the hit stage Forge's onHitPre middleware 280 (functions/280.lua) sets
-- skipState = 1 whenever outcomeType is not 0 (it first resets 9 to 0), and the Forge runner then skips the formula's
-- whole onHit list, 68 included. So the dent and the Broken renewal were already per landed swing before 0.1.48; the
-- M.evaded test below is a defensive guard, kept in case a later Forge runs 68 after an evade.
-- A renewal keeps an ally as breaker only within 10 s of the ally's own Break (noteBroken's keepLive).
function M.wrap68(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, before, after, twice, floored = pcall(function()
      if not M.applies(formula, caster, 'axe', deps) then return nil end
      -- defensive guard (0.1.48; 0.1.49 note): Forge 280 already keeps parried, blocked, dodged, missed and
      -- Safety-stopped swings out of 68; should one reach it, it neither dents nor renews Broken
      if M.evaded(formula) then return nil end
      -- 0.1.44: a Staggered foe takes the dent twice as deep (the floor stays at half)
      local twice = M.isStaggered(target, classes, helpers)
      local before, after, floored = M.dent(target, formula.target, twice and M.SUNDER_STAGGER or 1, deps.clock and deps.clock() or 0)
      -- a fresh dent Breaks the foe; 0.1.46: so does a hit on a foe already at the floor (it renews Broken, no deeper dent).
      -- 0.1.47: either way a live Broken keeps its breaker; only its 10 s restart. 0.1.48: an ally stays the breaker only
      -- within 10 s of the ally's own Break.
      if before or floored then M.noteBroken(target, deps.clock and deps.clock() or 0, caster.address, true) end
      if before then M.noteProc('SUNDER', caster.address, deps.clock and deps.clock() or 0) end   -- the log tag only
      return before, after, twice, floored
    end)
    if ok and before and deps.report then
      pcall(deps.report, 'sunder', caster.identifier, target.identifier, before, after, M.who(caster))
      if twice then pcall(deps.report, 'sunder-staggered', caster.identifier, target.identifier, before, after) end
    elseif ok and floored and deps.report then
      pcall(deps.report, 'sunder-floor', caster.identifier, target.identifier, 'broken renewed', M.who(caster))
    elseif not ok then
      M.fail(deps, 'sunder', before)
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
    -- 0.1.44 Brave: while the wielder holds Bravery each landed hit Breaks the foe (the Broken clock, no extra Defence loss)
    local okB, braveHit = pcall(function()
      if target.type ~= 1 or formula.caster.statusEffects.bravery ~= 1 then return false end
      local removed = formula.target.removedHp
      return type(removed) == 'number' and removed > 0
    end)
    if okB and braveHit then
      M.noteBroken(target, deps.clock and deps.clock() or 0, caster.address)
      if deps.report then pcall(deps.report, 'pierce-broken', caster.identifier, target.identifier, M.who(caster)) end
    end   -- an unreadable status table counts as not Brave
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
local ZERO_DODGE = {'evadeWeapon', 'evadeShield', 'evadeParry'}
local HELD = {'slow', 'immobilize', 'stop'}
-- 0.1.44: why a foe's dodge is gone to a ninja sword, or nil. Held = Slow, Immobilize or Stop; tired = TIRED_STEPS or more
-- guard-fatigue steps (the foe has avoided that many attacks in a row).
function M.heldOrTired(target, deps)
  local okHeld, held = pcall(function()
    for _, name in ipairs(HELD) do if target.temporaryStatusEffects[name] == 1 then return name end end
  end)
  if okHeld and held then return held end
  local now = deps.clock and deps.clock() or 0
  if M.fatigueSteps(target, now) >= M.TIRED_STEPS then return 'tired' end
  return nil
end
function M.wrap22(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    local ok, removed = pcall(function()
      if not M.applies(formula, caster, 'ninjaSword', deps) then return nil end
      local gone = {}
      local reason = target.type == 1 and M.heldOrTired(target, deps) or nil
      for _, field in ipairs(reason and ZERO_DODGE or ZERO) do
        local v = formula[field]
        if type(v) == 'number' and v > 0 then gone[#gone + 1] = field .. '=' .. v end
        formula[field] = 0
      end
      return #gone > 0 and (table.concat(gone, ' ') .. (reason and (' ' .. reason) or '')) or nil
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
      elseif category == 'gun' then
        -- True aim interrupt (0.1.44): the foe is about to act when its charge is at least 3/4 full, the test the hybrid
        -- 'about' condition uses. The push itself runs in the Proficiency runtime, inside the shared push budget.
        local w = formula.target.battleUnitWork
        if type(w) ~= 'table' or w.address == 0 then return nil end
        local cur, req = w.currentChargeTime, w.requiredChargeTime
        if type(cur) ~= 'number' or type(req) ~= 'number' or req <= 0 or not (cur < req and cur * 4 >= req * 3) then return nil end
        return 'interrupt', w
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
      -- 0.6.0: the Proficiency runtime's own charge route when it is loaded, so a Cadence bonus and a Proficiency "next
      -- action faster" bonus waiting for the same action never stack: the larger one wins. Without it, the module's own.
      local Rt = rawget(_G, 'IvaliceLegacyProficiency')
      local arm = (type(Rt) == 'table' and type(Rt.armCharge) == 'function') and Rt.armCharge or deps.armCharge
      if haste > 0 and arm then pcall(arm, caster.address, haste, classes, helpers) end
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
    elseif ok and kind == 'interrupt' and value then
      local Rt = rawget(_G, 'IvaliceLegacyProficiency')
      local okP, before, after = pcall(function()
        if type(Rt) ~= 'table' or type(Rt.pushFoe) ~= 'function' then return nil end
        return Rt.pushFoe(value, target.address, M.INTERRUPT_PUSH, caster.address, target)
      end)
      if okP and before and deps.report then
        pcall(deps.report, 'true-aim-push', caster.identifier, target.identifier, before, after, M.who(caster))
      elseif not okP then
        M.fail(deps, 'true-aim-push', before)
      end
    elseif not ok then
      M.fail(deps, 'wrap165', kind)
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Channel (0.1.44), staff, damage spells (Forge 170, called by the Proficiency runtime): while the caster holds Faith each
-- damage spell that lands Breaks the foe's Magick Resist (the Broken clock; there is no extra loss). Faith counts when the
-- spell's own status table or the unit's temporary table shows it. Returns true when the foe was marked.
function M.channel(formula, caster, target, deps)
  deps = deps or registered
  if not deps or not M.applies(formula, caster, 'staff', deps, false) then return false end
  if type(target) ~= 'table' or target.type ~= 1 then return false end
  local damage = formula.target.removedHp
  if type(damage) ~= 'number' or damage <= 0 then return false end
  local okF, faith = pcall(function() return formula.caster.statusEffects.faith == 1 end)
  if not (okF and faith) then
    local okT, held = pcall(function() return caster.temporaryStatusEffects.faith == 1 end)
    if not (okT and held) then return false end
  end
  return M.noteBroken(target, deps.clock and deps.clock() or 0, caster.address)
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
-- engine performs a counter only for a unit that holds the Counter augment (play feedback, 2026-11-10: "no Counter text").
-- So a pole wielder without the licence is lent the augment's bit in its temporary augments while it holds the
-- pole, and the bit is taken back the first time it is struck holding anything else (that blow's stale chance is
-- zeroed before the roll). The licence itself lives in the permanent augments and is never touched.
-- A licence is the permanent Counter augment, or a temporary one this module did not lend (a hybrid reaction, say).
local function realCounter(keep)
  if keep.permanentAugments.counter == 1 then return true end
  return keep.temporaryAugments.counter == 1 and not lent[keep.address]
end

-- Counter Boost held by a unit. Forge 7 decides the counter chance from formula.target.augments.counterBoost, the unit's
-- effective augments for this hit, so that view counts; the keep's permanent (an item such as Genji Armor or the Black
-- Belt) and temporary tables count too. Any of them true means held. A view that cannot be read is skipped; when none can
-- be read (a wrong field name in some Forge build) the answer is false, Counterflow falls back to its plain base, and the
-- log says so once per load. The first answer is logged once, with the view that gave it, so the field name is confirmed
-- in play. (The Counter licence test, realCounter, stays on the keep: it must tell a licence from the loan this module
-- makes, and the effective view cannot.)
local boostLogged = false
boostLoggedReset = function() boostLogged = false end
function M.hasCounterBoost(keep, deps, formula)
  local name = M.COUNTER_BOOST
  local sources = {
    {'permanent', function() return keep.permanentAugments[name] end},
    {'temporary', function() return keep.temporaryAugments[name] end},
    {'formula', function() return formula.target.augments[name] end},
  }
  local held, readable, source = false, 0, 'none'
  for _, s in ipairs(sources) do
    local ok, v = pcall(s[2])
    if ok and type(v) == 'number' then
      readable = readable + 1
      if v == 1 and not held then held, source = true, s[1] end
    end
  end
  deps = deps or registered
  if not boostLogged and deps and deps.report then
    boostLogged = true
    pcall(deps.report, 'counterboost-field', 'name=' .. name,
          readable > 0 and ('read=ok held=' .. tostring(held)) or 'read=FAILED', 'source=' .. source,
          'views=' .. readable .. 'of3')
  end
  return held
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
        -- Counter Boost without the licence: Speed x 2 instead of Speed / 2, as Forge 7 gives a licensed holder (0.6.0)
        base, cap = own, M.COUNTERFLOW_CAP
        if M.hasCounterBoost(defender, deps, formula) then base = defender.speed * M.COUNTERFLOW_BOOSTED end
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
-- Party members, and (0.1.44) the foes a party member attacks: a foe's evasion wears down the same way, so evasion is a
-- delay and not a wall. Only the fatigue applies to a foe (no counter-parry). A Regen holder's
-- guard is fresh after 5 s without being attacked instead of 10 s. 1-5 are the evade outcomes Forge 41-43 write (2 and 4
-- with a counter).
local EVADED = {[1] = true, [2] = true, [3] = true, [4] = true, [5] = true}

local FIELDS = {'evadeShield', 'evadeWeapon', 'evadeParry'}
-- Guard-fatigue steps a unit carries now (0 when fresh, expired, or another foe at a recycled address).
function M.fatigueSteps(target, now)
  local e = fatigue[target.address]
  if not e then return 0 end
  if target.type == 1 and (e.hp ~= target.maxHp or e.level ~= target.level) then return 0 end
  if type(now) == 'number' and now > 0 and now - e.at > M.FATIGUE_WINDOW then return 0 end
  return math.min(e.stacks, M.FATIGUE_MAX)
end
function M.guardWrap(original, deps)
  assert(type(original) == 'function', 'original function required')
  return function(formula, caster, target, functions, classes, helpers)
    local deps = deps or registered
    local ok, entry, stacks, shown, category, stance, parried, mine = pcall(function()
      -- 0.1.45: the cheap eligibility checks come first (a party defender, or a party attacker on a foe); the campaign
      -- test last, so a foe's attack on a foe never reaches it
      local mine = M.isWielder(target)
      -- 0.1.44: a foe a party member attacks tires too; its entry carries the foe's fingerprint
      if not mine and not (target.type == 1 and M.isWielder(caster)) then return nil end
      if deps.active() ~= true then return nil end
      local now = deps.clock and deps.clock() or 0
      if type(now) ~= 'number' then now = 0 end
      local window = (mine and M.holdsRegen(target)) and M.REGEN_WINDOW or M.FATIGUE_WINDOW
      local e = fatigue[target.address]
      if not e or (now > 0 and now - e.at > window) or (not mine and (e.hp ~= target.maxHp or e.level ~= target.level)) then
        e = {stacks = 0, at = now, hp = (not mine) and target.maxHp or nil, level = (not mine) and target.level or nil}
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
      local category = mine and deps.category(target) or nil
      -- The attack's stance, for the log: Forge 41-43 roll evasion only while the target stands in battle stance.
      local okS, inStance = pcall(function() return formula.target.battleUnitWork.battleFlags.isInBattleStance end)
      local stance = okS and inStance or '?'
      -- Counter-parry: "never parries, except to counter". Forge 41 rolls the weapon evade for a counter swing
      -- (outcome 2), so a defender whose weapon evade is 0 (a spear, a bow, a hand-bomb, any weapon
      -- row with no evade) could never answer a blow it rolled a counter for. When Forge 8 has rolled one, that
      -- roll gets weapon evade 30 (the pole's top band), never lowered. Fists are left alone (their counters come from the Cadence streak).
      if formula.counterState == 1 and category and category ~= 'unarmed' then
        local evade = formula.evadeWeapon
        -- Hybrid rework, decision 3 (2026-11-11): every weapon but fists (axe 6, rod 6, hammer 2-5, crossbow 5, staff, measure,
        -- gun...), not only the evade-0 rows, rolls its counter against 30. Only ever raised: a weapon that already evades
        -- better keeps its own value. The roll is evadeWeapon against random(100): 30 is a 30% chance, not a certainty.
        if type(evade) == 'number' and evade < M.COUNTER_PARRY_EVADE then
          formula.evadeWeapon = M.COUNTER_PARRY_EVADE
          parts[#parts + 1] = 'counter-parry'
          parts.parried = true
        end
      end
      return e, n, #parts > 0 and table.concat(parts, ' ') or nil, category, stance, parts.parried, mine
    end)
    if not ok then M.fail(deps, 'guard', entry) end
    local result = table.pack(original(formula, caster, target, functions, classes, helpers))
    if ok and entry then
      local okOut, outcome, skipped = pcall(function() return formula.outcomeType, formula.skipState end)
      if okOut then
        if skipped == 1 and EVADED[outcome] then
          -- 0.1.43: an avoid against a Blinded attacker wears nothing down (and resets nothing). A status that cannot be
          -- read counts as not Blinded, so the original rule stands.
          local okBl, blind = pcall(function() return formula.caster.statusEffects.blind == 1 end)
          if okBl and blind then
            if deps.report then
              pcall(deps.report, mine and 'fatigue-blind' or 'foe-fatigue-blind', target.identifier, outcome, M.who(target))
            end
          else
            entry.stacks = math.min(entry.stacks + 1, M.FATIGUE_MAX)
          end
        elseif outcome ~= 6 then
          entry.stacks = 0
        end
      end
      if parried and deps.report then
        pcall(deps.report, 'counter-parry', target.identifier, M.who(target))
      end
      if shown and deps.report then
        pcall(deps.report, mine and 'fatigue' or 'foe-fatigue', target.identifier, stacks, shown, okOut and outcome or '?',
              'stance=' .. tostring(stance), M.who(target))
      end
      -- A swing that answers a blow (parry or block with a counter): the outcome the counter lines can't show.
      if okOut and mine and skipped == 1 and (outcome == 2 or outcome == 4) and deps.report then
        pcall(deps.report, 'counter-swing', target.identifier, 'outcome=' .. outcome, category or '?', M.who(target))
      end
    end
    return table.unpack(result, 1, result.n)
  end
end

-- Katana, combo engine 10: the original rolls the combo and stores its length on the caster's keep;
-- Chain extends a rolled katana combo by one hit, inside the engine's own cap. Function 10 also runs
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
    if ok and before then
      local after = math.min(M.CHAIN_MAX, before + M.CHAIN_EXTRA)
      if after > before then
        formula.caster.keep.battleFlags.comboCount = after
        if deps.report then pcall(deps.report, 'chain', caster.identifier, before, after, M.who(caster)) end
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
  unarmed = {'Cadence', "Each Attack in a row on the same foe hits 6% harder, up to 30%, and makes your next action charge 5% faster per step. A full streak counts as a counter for Ki Flow. Switching foes restarts the ramp."},
  sword = {'Guard', "Most swords carry high evade, 17 to 38, so a sword wielder parries often. A sword has no answer to armor or to a foe's evasion."},
  greatsword = {'Sweep', "An Attack hits its target in full and every foe around it for half of that hit. The swing is slow, most greatswords can't reach flying foes, and an axe or a spear does more against a lone armored foe."},
  katana = {'Chain', "The highest combo rate of any family, and a combo runs one hit longer. Every combo hit meets the foe's full Defense, and a katana leaves no hand for a shield."},
  ninjaSword = {'Unseen', "Your hits cannot be parried or blocked, and a foe that is Slowed, Immobilized, Stopped or tired cannot dodge them. Every blade carries Blind at 10%, and five also carry a second status at 10%. Against a heavily armored foe your hits keep less of their force."},
  spear = {'Pierce', "Every hit ignores 30% of Defense, and while you are Brave each hit Breaks the foe. Spears reach far, but most have no parry of their own."},
  pole = {'Counterflow', "You counter even without the Counter augment (the Battle Harness and the Magepower Shishak carry it), and every blow you take builds the next counter. The pole strikes Magick Resist, and its evade lets counters swing."},
  bow = {'Volley', "Bows have the keenest critical chance among the ranged weapons."},
  crossbow = {'Afflict', "The bolt's status chance is doubled, up to 100%. Bolts take a little longer to load."},
  gun = {'True aim', "A gunner's shots never miss, Blind or not, and ignore Defense. Strength doesn't raise them, only the gun's own power, and a gun is the slowest weapon to charge. A shot at a foe about to act pushes it back 10%."},
  axe = {'Sunder', "Every hit Breaks the foe and dents its Defense by 5% of its starting value for the fight. The dent is twice as deep on a Staggered foe and stops at half, but the Breaks go on. Its swings land unevenly and it barely parries."},
  hammer = {'Concussion', "Every hit has a 15% chance to Disable the foe, 20% for the Scorpion Tail, and a 30% chance to knock it back. The swing is slow."},
  dagger = {'Swift', "The fastest charge, frequent combos and a little evade. Against a heavily armored foe your hits keep less of their force."},
  rod = {'Mend', "Healing spells heal 20% more while you hold a rod. A rod has the weakest attack and the least Magick of the caster weapons."},
  staff = {'Channel', "Staves raise element potency and give the most Magick of the caster weapons. While you hold Faith, each damage spell that lands Breaks the foe."},
  mace = {'Spellbreak', "Each hit burns MP from the foe equal to 10% of the damage, and never more than 10% of its pool."},
  measure = {'Tempo', "Each measure grants the ally it strikes one status, which is Protect, Shell, Bravery, Invisible, Haste or Bubble depending on the tier."},
  handBomb = {'Barrage', "The bombs you load decide the status, which lands with a 25% chance on hit. Chaos Bombs, which Confuse, and Stun Bombs, which Stop, set up a Time Mage's Detonate."},
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
  pcall(print, 'Legacy Family: identities ' .. M.version .. ' ready (ten families, true aim; bonuses=on)')
  pcall(M.hudShareStart)   -- the co-op HUD's view of this state (a timer on this script thread; see the end of the file)
  return true
end

function M.deps() return registered end

-- ---------- the HUD's view (the co-op overlay; read only, never consulted by any rule above) ----------
-- Procs as log tags: the last HUD_PROCS identity procs, each {tag, address of the unit whose hit fired it, clock}. A ring
-- the HUD publisher reads; nothing here reads it back.
M.HUD_PROCS = 8
M.HUD_FOES = 8
M.HUD_WORDS = 4
M.DENT_HUD_WINDOW = 60000000   -- microseconds since the last Sunder hit that deepened the dent: shown on the HUD this long
local procs = {}
function M.resetProcs() procs = {} end
function M.noteProc(tag, address, now)
  if type(tag) ~= 'string' or type(address) ~= 'number' or address <= 0 or type(now) ~= 'number' or now <= 0 then
    return
  end
  procs[#procs + 1] = {tag = tag, address = address, at = now}
  if #procs > M.HUD_PROCS then table.remove(procs, 1) end
end

local function ceilSeconds(us) return math.max(0, math.ceil(us / 1000000)) end

-- The states the HUD shows, at clock `now` (event clock, microseconds), from this module's own tables: foes (Broken
-- with seconds left, Sunder's dent, guard fatigue) and party words (guard fatigue, Cadence, Counterflow) for party
-- records under `root` (the battle state: party record = root + 8 + id * 0x1C8), plus the procs of the last 5 s.
-- Entries past their windows are left out (they are also what every rule above would ignore). Pure: no memory read.
function M.hudStates(now, root)
  local out = {foes = {}, words = {}, procs = {}}
  if type(now) ~= 'number' or now <= 0 then return out end
  local foes, order = {}, {}
  local function foe(address)
    local f = foes[address]
    if not f then
      f = {record = address, flags = 0, broken_s = 0, dent_pct = 0, tired = 0}
      foes[address] = f
      order[#order + 1] = address
    end
    return f
  end
  local function member(address)
    if type(root) ~= 'number' or root <= 0 then return nil end
    local off = address - root - 8
    if off < 0 or off % 0x1C8 ~= 0 then return nil end
    local id = off // 0x1C8
    if id > 39 then return nil end
    return id
  end
  for address, e in pairs(broken) do
    local age = now - e.at
    if age >= 0 and age <= M.BROKEN_WINDOW then
      local f = foe(address)
      f.flags = f.flags | 0x01
      f.broken_s = math.min(60, ceilSeconds(M.BROKEN_WINDOW - age))
    end
  end
  for address, e in pairs(dents) do
    -- Aged out of the HUD (not out of the rule: dent() still keeps its baseline): a later foe at a reused address with
    -- the same fingerprint must not show the previous foe's "Defense -N%".
    if type(e.pct) == 'number' and e.pct > 0 and type(e.at) == 'number' and now >= e.at
       and now - e.at <= M.DENT_HUD_WINDOW then
      foe(address).dent_pct = math.min(50, e.pct)
    end
  end
  local words = {}
  for address, e in pairs(fatigue) do
    if now - e.at <= M.FATIGUE_WINDOW and e.stacks > 0 then
      local steps = math.min(e.stacks, M.FATIGUE_MAX)
      if e.hp then
        foe(address).tired = steps                         -- a foe's entry carries its fingerprint
      else
        local id = member(address)
        if id then words[#words + 1] = {member = id, kind = 1, value = steps} end
      end
    end
  end
  for address, m in pairs(cadence) do
    local id = member(address)
    if id and m.hits > 1 and now - m.at <= M.CADENCE_WINDOW then
      words[#words + 1] = {member = id, kind = 2, value = math.min(m.hits - 1, M.CADENCE_CAP)}
    end
  end
  for address, f in pairs(flows) do
    local id = member(address)
    if id and f.stacks > 0 and now - f.at <= M.COUNTERFLOW_WINDOW then
      words[#words + 1] = {member = id, kind = 3, value = math.min(255, f.stacks * M.COUNTERFLOW_STEP)}
    end
  end
  table.sort(order)
  for _, address in ipairs(order) do
    if #out.foes >= M.HUD_FOES then break end
    out.foes[#out.foes + 1] = foes[address]
  end
  -- One word per member: the first found (guard first, then Cadence, then Flow).
  local seen = {}
  for _, w in ipairs(words) do
    if not seen[w.member] and #out.words < 4 then
      seen[w.member] = true
      out.words[#out.words + 1] = w
    end
  end
  for _, p in ipairs(procs) do
    local age = now - p.at
    if age >= 0 and age <= 5000000 then
      out.procs[#out.procs + 1] = {record = p.address, tag = p.tag, age_ms = age // 1000}
    end
  end
  return out
end

-- ---------- the HUD's shared block (this Forge state -> the co-op state) ----------
-- The loader gives every scripts/*.lua its own Lua state, so the co-op script cannot read this module through a global:
-- the module lives in the Forge state (the adapters load it there). A timer on THIS state's script thread (never a
-- formula hook) packs hudStates into one small block of memory and registers its address as the global symbol
-- ilc_hud_share; the co-op script (coop/hud_states.lua) resolves the symbol, copies the block and builds the wire
-- record. Read only: nothing here changes a rule, and a failure only stops the publishing.
--
-- Block (little endian; the reader in coop/hud_states.lua carries the same constants, tests/test_coop_hud_states_lua.py
-- checks both against each other):
--   +0  u32 magic 'ILS1'      +4  u32 sequence (even = stable; odd = being written; +2 per publish)
--   +8  u32 checksum of bytes 16..SIZE-1 (sum of byte * (offset % 251 + 1), mod 2^32)
--   +12 u32 SIZE              +16 i64 event clock of the publish (microseconds)
--   +24 u8 foe count, u8 word count, u8 proc count, u8 0
--   +28 foes x8, 16 bytes each: i64 record, u8 flags, u8 broken_s, u8 dent_pct, u8 tired, 4 zero bytes
--   +156 words x4, 4 bytes each: u8 member, u8 kind, u8 value, u8 0        (unused entries are all zero)
--   +172 procs x8, 16 bytes each: i64 record, u8 tag code, 3 zero bytes, u32 age_ms
-- Unused entries are all zero, as the wire record requires.
M.HUD_SHARE_SYMBOL = 'ilc_hud_share'
M.HUD_SHARE_PERIOD_MS = 250
M.HUD_SHARE_ROOT = 0x02EBF190   -- the battle state pointer: party record = [ROOT] + 8 + id * 0x1C8
M.HUD_SHARE = {MAGIC = 0x31534C49, SEQ = 4, SUM = 8, SIZE_AT = 12, CLOCK = 16, COUNTS = 24, FOES = 28, WORDS = 156,
               PROCS = 172, SIZE = 300}
-- The wire tag codes (ilc_hud_tag in the co-op schema).
M.HUD_TAG_CODES = {SUNDER = 1, BREAK = 2, CADENCE = 3, COUNTERFLOW = 4, GUARD = 5, UNSEEN = 6, PIERCE = 7, MEND = 8,
                   PROFICIENCY = 9, SPOTTED = 10, KINDLE = 11}

function M.hudShareChecksum(bytes, first)
  local sum = 0
  for i = first + 1, #bytes do
    sum = (sum + bytes[i] * ((i - 1) % 251 + 1)) % 0x100000000
  end
  return sum
end

local function clamp(v, hi)
  if type(v) ~= 'number' or v ~= v then return 0 end
  return math.max(0, math.min(hi, math.floor(v)))
end

-- The block bytes (a table of SIZE byte values, sequence 0) for a hudStates view taken at clock `now`.
function M.hudSharePack(view, now)
  local S = M.HUD_SHARE
  local parts = {}
  local foes = {}
  for _, f in ipairs(view.foes or {}) do
    if #foes >= M.HUD_FOES then break end
    if type(f.record) == 'number' and f.record > 0 then foes[#foes + 1] = f end
  end
  local words = {}
  for _, w in ipairs(view.words or {}) do
    if #words >= M.HUD_WORDS then break end
    if type(w.member) == 'number' and w.member >= 0 and w.member < 40 and clamp(w.kind, 255) > 0 then
      words[#words + 1] = w
    end
  end
  local procList = {}
  for _, p in ipairs(view.procs or {}) do
    if #procList >= M.HUD_PROCS then break end
    if M.HUD_TAG_CODES[p.tag] and type(p.record) == 'number' and p.record > 0 then procList[#procList + 1] = p end
  end
  parts[#parts + 1] = string.pack('<I4I4I4I4i8', S.MAGIC, 0, 0, S.SIZE, math.floor(now))
  parts[#parts + 1] = string.pack('<BBBB', #foes, #words, #procList, 0)
  for i = 1, M.HUD_FOES do
    local f = foes[i]
    if f then
      parts[#parts + 1] = string.pack('<i8BBBBI4', math.floor(f.record), clamp(f.flags, 255), clamp(f.broken_s, 60),
                                      clamp(f.dent_pct, 50), clamp(f.tired, 4), 0)
    else
      parts[#parts + 1] = string.rep('\0', 16)
    end
  end
  for i = 1, M.HUD_WORDS do
    local w = words[i]
    if w then
      parts[#parts + 1] = string.pack('<BBBB', math.floor(w.member), clamp(w.kind, 255), clamp(w.value, 255), 0)
    else
      parts[#parts + 1] = string.rep('\0', 4)
    end
  end
  for i = 1, M.HUD_PROCS do
    local p = procList[i]
    if p then
      parts[#parts + 1] = string.pack('<i8BBBBI4', math.floor(p.record), M.HUD_TAG_CODES[p.tag], 0, 0, 0,
                                      clamp(p.age_ms, 5000))
    else
      parts[#parts + 1] = string.rep('\0', 16)
    end
  end
  local raw = table.concat(parts)
  assert(#raw == S.SIZE, 'hud share block size')
  local bytes = {}
  for i = 1, #raw, 100 do
    for _, b in ipairs({string.byte(raw, i, math.min(#raw, i + 99))}) do bytes[#bytes + 1] = b end
  end
  local sum = M.hudShareChecksum(bytes, S.CLOCK)
  for k = 0, 3 do bytes[S.SUM + k + 1] = (sum >> (8 * k)) & 0xFF end
  return bytes
end

local share = {base = nil, seq = 0, failed = false, armed = false, logged = false}
function M.hudShareState() return share end

local function shareLog(text)
  if share.logged then return end
  share.logged = true
  pcall(print, 'Legacy Family: HUD share ' .. text)
end

-- One publish (the timer's body). Allocates the block and registers its symbol on the first call. Returns true when a
-- block was written. Read only on the game: it reads the battle state pointer and nothing else.
function M.hudSharePublish()
  local S = M.HUD_SHARE
  if not share.base then
    local base
    if type(memory.allocData) == 'function' then base = memory.allocData(S.SIZE) end
    if not base or base == 0 then base = memory.alloc(S.SIZE) end
    if not base or base == 0 or memory.registerGlobalSymbol(M.HUD_SHARE_SYMBOL, base) ~= true then
      share.failed = true
      shareLog('not started (no block or symbol)')
      return false
    end
    share.base = base
    memory.writeU32(base, S.MAGIC)
  end
  local now = type(event.getClock) == 'function' and event.getClock() or 0
  if type(now) ~= 'number' or now <= 0 then return false end
  local ok, root = pcall(function() return memory.u64[M.HUD_SHARE_ROOT] end)
  local view = M.hudStates(now, ok and root or nil)
  local bytes = M.hudSharePack(view, now)
  local base = share.base
  memory.writeU32(base + S.SEQ, (share.seq + 1) % 0x100000000)                    -- odd: being written
  local body = {}
  for i = 9, #bytes do body[#body + 1] = bytes[i] end
  memory.writeArray(base + 8, body)
  share.seq = (share.seq + 2) % 0x100000000
  memory.writeU32(base + S.SEQ, share.seq)                                        -- even: stable
  return true
end

-- Arm the publisher timer on this state's own script thread (idempotent; without the timer API: nothing). The timer
-- always re-arms, whatever a tick does.
function M.hudShareStart()
  if share.armed or share.failed then return false end
  if type(event) ~= 'table' or type(event.executeAfterMs) ~= 'function' or type(memory) ~= 'table' then return false end
  share.armed = true
  event.executeAfterMs(M.HUD_SHARE_PERIOD_MS, function()
    share.armed = false
    local ok, err = pcall(M.hudSharePublish)
    if not ok then shareLog('tick failed: ' .. tostring(err)) end
    M.hudShareStart()
  end)
  return true
end

return M
