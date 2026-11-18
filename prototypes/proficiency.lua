-- Proficiency: each job's Clan line, earned from Montblanc one Clan rank at a time and applied to whatever the
-- character wields. 12 ranks = 3 stages x 4 levels: ranks 1-4 raise stage I, 5-8 stage II, 9-12 stage III.
-- The line is chosen by the character's CURRENT job. This module only decides; the Forge adapters apply the result
-- through Forge's own end-of-hit step (statuses, durations, HP/MP), the charge time, or helpers on other units.
-- Every level is built from operations already seen working in play; where a designed effect needed an engine detail
-- nobody has proven, the level does the nearest thing those operations allow.
local M = {}
M.version = '0.5.9'

M.NO_JOB = 255
M.JOB = {whiteMage = 0, lancer = 1, gunner = 2, greenMage = 3, knight = 4, mystic = 5, timeMage = 6, warrior = 7,
         hunter = 8, blackMage = 9, samurai = 10, chemist = 11}
M.STATUS = {ko = 0, stop = 3, sleep = 4, confuse = 5, blind = 7, poison = 8, silence = 9, sap = 10, oil = 11, disable = 13,
            immobilize = 14, slow = 15, disease = 16, lure = 17, protect = 18, shell = 19, haste = 20, bravery = 21,
            faith = 22, reflect = 23, invisible = 24, regen = 25, float = 26, berserk = 27, bubble = 28, hpCritical = 29,
            libra = 30}
M.HINDERED = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil', 'stop'}
M.BUFFS = {'protect', 'shell', 'haste', 'bravery', 'faith', 'reflect', 'regen', 'float', 'bubble', 'invisible', 'berserk'}
-- Ailments with a running timer, shortest first (status table durations: Disable 20, Immobilize 50, Poison 50, Slow 60).
-- Blind, Silence, Sap, Oil and Disease have no timer, so renewing them would do nothing.
M.TIMED = {'disable', 'immobilize', 'poison', 'slow'}
-- "A foe with an ailment" tests for the base lines (0.1.44, decision 3): the ailments, Stop, and Broken (a pseudo-flag the
-- runtime puts on a foe snapshot while a party member's cut on it is under 10 s old).
M.HINDERED_TEST = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil', 'stop', 'broken'}
M.AILMENTS = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil'}
-- "An ailment you land" (hybrid `status` stages): the same set on the weapon route (hook 182) and the spell route
-- (hook 181). It is the ailments plus Stop, Sleep and Confuse; the base Chemist's Contagion keeps reading the nine ailments.
M.LANDED = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil', 'stop', 'sleep', 'confuse'}
M.LANDED_ID = {}
for _, name in ipairs(M.LANDED) do M.LANDED_ID[M.STATUS[name]] = name end
M.GREATSWORD = 2
M.MAX_RANK = 12
-- Legendary weapons: any job wields them at base power; full power needs Clan mastery in the job that owns the family.
M.MASTER_DAMAGE = 1.25
-- BaseJobs 0.6.0 (2026-11-10 decision): hammers moved from Warrior to Time Mage, measures from Time Mage to Chemist.
M.FAMILY_OWNER = {[1] = M.JOB.knight, [2] = M.JOB.warrior, [3] = M.JOB.samurai, [4] = M.JOB.samurai, [5] = M.JOB.lancer,
                  [6] = M.JOB.mystic, [7] = M.JOB.hunter, [8] = M.JOB.chemist, [9] = M.JOB.gunner, [10] = M.JOB.warrior,
                  [11] = M.JOB.timeMage, [12] = M.JOB.hunter, [13] = M.JOB.whiteMage, [14] = M.JOB.blackMage,
                  [15] = M.JOB.greenMage, [16] = M.JOB.chemist, [17] = M.JOB.timeMage}
M.LEGENDARY = {[4130] = true, [4266] = true, [4267] = true, [4268] = true, [4270] = true, [4272] = true, [4288] = true,
               [4290] = true, [4291] = true, [4292] = true, [4293] = true, [4294] = true, [4295] = true}
M.DISEASE = 16
M.CAST_WINDOW = 200000   -- clock units (microseconds): fallback only; area spells reach targets ~1 s apart, so the
                         -- runtime's cast id (Forge 222, initial target) decides what is one cast
M.HIT_MP_SHARE = 2       -- MP a weapon hit hands anyone from its damage: at most this % of the receiver's max MP
M.SUPPRESSION_CT_BASE, M.SUPPRESSION_CT_CAP = 24, 2   -- Gunner: push x weapon CT/24, at most x2
M.STOP_EXTENDS = 4       -- Time Mage III-3: at most this many extensions per Stop, so a Stop always ends

local function int(v, lo, hi) return type(v) == 'number' and v % 1 == 0 and v >= lo and v <= hi end
local function has(set, list) for _, n in ipairs(list) do if set[n] then return n end end end

function M.levels(claimedRank)
  if not int(claimedRank, 0, M.MAX_RANK) then return {0, 0, 0} end
  local out = {}
  for i = 1, 3 do out[i] = math.max(0, math.min(4, claimedRank - 4 * (i - 1))) end
  return out
end

-- The Writs are key items (Apprentice's, Journeyman's, Master's). A stage only works while its Writ is owned:
-- the Writ is handed over first and the Proficiency follows it.
M.WRIT_IDS = {0x81F0, 0x81F1, 0x81F2}
M.STAGE_START = {1, 5, 9}
function M.writCap(owned)
  for k = 1, 3 do
    if not (type(owned) == 'table' and owned[k] == true) then return M.STAGE_START[k] - 1 end
  end
  return M.MAX_RANK
end
function M.effective(claimedRank, owned)
  if not int(claimedRank, 0, M.MAX_RANK) then return 0 end
  return math.min(claimedRank, M.writCap(owned))
end
-- Writs a claimed rank calls for: stage k needs Writ k once its first level is reached.
function M.writsNeeded(claimedRank)
  local L, out = M.levels(claimedRank), {}
  for k = 1, 3 do if L[k] >= 1 then out[#out + 1] = k end end
  return out
end

-- Per-character memory (chains), cleared on load.
local mem = {}
function M.reset() mem = {} end
local function slot(actor, job)
  if not mem[actor] or (job ~= nil and mem[actor].job ~= job) then mem[actor] = {job = job} end
  return mem[actor]
end

-- Action helpers. a.targetAdd/casterAdd = {[statusId] = durationScale}; a.others = {{op, unit, ...}}.
local function add(t, field, id, scale)
  t[field] = t[field] or {}
  -- Multiple passives may grant the same status in one decision; keep the longest before it reaches Forge.
  t[field][id] = math.max(t[field][id] or 0, scale or 1)
end
local function push(t, field, v) t[field] = t[field] or {}; t[field][#t[field] + 1] = v end
local function other(t, op) push(t, 'others', op) end

-- s (all events): job, actorId, L = {I, II, III}, party = {{address, job, hpPct, mp, maxMp, status}},
-- foes = {{address, status}} (other engaged foes, alive), casterStatus, casterHpPct, roll(percent), self = address.
-- 'hit' adds: targetId, foe, kill, bystander, category, damage, targetStatus, targetMp, charge, target (address).
local rules = {}
M.rules = rules
-- Hybrid lines (hybrid_proficiency.lua) add rules for ids 12-77 and their two parent jobs here.
M.PARENTS = {}
local HELP = {slot = function(actor) return slot(actor) end, add = add, push = push, other = other}

rules[M.JOB.hunter] = {hit = function(s, a)
  if not s.foe then return end
  local L = s.L
  local prey = has(s.targetStatus, M.HINDERED_TEST)   -- an ailment, or Broken (0.1.44)
  local m = slot(s.actorId)
  if prey and L[1] >= 1 then
    local factor = L[1] >= 4 and 1.25 or 1.15
    if L[1] >= 2 and m.prey == s.targetId then factor = factor + 0.05 end
    a.damage = (a.damage or 1) * factor
    m.prey = s.targetId
    if L[1] >= 3 and s.kill then a.healPct = 10 end
  elseif not prey then
    m.prey = nil
  end
  if prey and L[2] >= 1 then
    -- Renewing an ailment already on the foe: the most this can do is restart its timer.
    local renewed = 0
    local most = L[2] >= 4 and 99 or (L[2] >= 2 and 2 or 1)
    for _, name in ipairs(M.TIMED) do
      if s.targetStatus[name] and renewed < most then
        push(a, 'targetRenew', M.STATUS[name]); renewed = renewed + 1
      end
    end
    -- II-3/4 (decision 10): the restarted timers last 10% longer than a normal one (this replaced Libra).
    if L[2] >= 3 and renewed > 0 then a.renewScale = 1.10 end
  end
  if m.slowNext and L[3] >= 2 then
    if not s.targetStatus.slow then add(a, 'targetAdd', M.STATUS.slow, 0.5) end
    m.slowNext = nil
  end
  if L[3] >= 1 and prey and s.kill then
    add(a, 'casterAdd', M.STATUS.haste, (L[3] >= 4 and 1.5 or 1) * 0.5)
    m.slowNext = true
    if L[3] >= 3 and s.foes[1] and not s.foes[1].status.slow then
      other(a, {op = 'add', unit = s.foes[1].address, id = M.STATUS.slow, scale = 0.5})
    end
  end
end}

rules[M.JOB.warrior] = {hit = function(s, a)
  if not s.foe then return end
  local L, brave = s.L, s.casterStatus.bravery
  if L[1] >= 1 and (s.kill or (L[1] >= 2 and s.bystander and s.category == M.GREATSWORD)) then
    add(a, 'casterAdd', M.STATUS.bravery, L[1] >= 3 and 1.5 or 1)
    if L[1] >= 4 and s.kill and brave then
      push(a, 'casterRemove', M.STATUS.slow); push(a, 'casterRemove', M.STATUS.disable); push(a, 'casterRemove', M.STATUS.immobilize)
    end
  end
  local low = s.casterHpPct < 50 or (L[2] >= 3 and s.casterHpPct < 75)
  if L[2] >= 1 and low then
    if not brave and not (a.casterAdd and a.casterAdd[M.STATUS.bravery]) then add(a, 'casterAdd', M.STATUS.bravery) end
    -- II-2..4 (2026-11-16, Reckless is gone): the hit also Breaks the foe, through the shared Broken state (the Pierce route)
    if L[2] >= 2 then a.breakFoe = true end
    if L[2] >= 4 then
      for _, p in ipairs(s.party) do
        if p.address ~= s.self and not p.status.bravery then other(a, {op = 'add', unit = p.address, id = M.STATUS.bravery, scale = 0.5}) break end
      end
    end
  end
  if L[3] >= 1 and s.casterStatus.hpCritical and not s.targetStatus.sap then add(a, 'targetAdd', M.STATUS.sap) end
  if L[3] >= 2 and s.targetStatus.sap then a.damage = (a.damage or 1) * 1.15 end
  if L[3] >= 3 and s.kill and s.targetStatus.sap then
    for i = 1, (L[3] >= 4 and 2 or 1) do
      local f = s.foes[i]
      if f then other(a, {op = 'add', unit = f.address, id = M.STATUS.sap}) end
    end
  end
end}

rules[M.JOB.lancer] = {hit = function(s, a)
  if not s.foe then return end
  local L, strip = s.L, {}
  -- Foes are immune to Float and none cast it, so it cannot be the whole of the first level.
  if L[1] >= 1 and s.targetStatus.float then strip[#strip + 1] = 'float' end
  for i, name in ipairs({'haste', 'faith', 'regen', 'bravery'}) do
    if L[1] >= i and s.targetStatus[name] then strip[#strip + 1] = name end
  end
  if L[2] >= 1 and (has(s.targetStatus, M.HINDERED_TEST) or s.broken) then   -- an ailment, or Broken (0.1.43)
    if s.targetStatus.protect then strip[#strip + 1] = 'protect' end
    if s.targetStatus.shell and (L[2] >= 2 or not s.targetStatus.protect) then strip[#strip + 1] = 'shell' end
    if L[2] >= 3 and s.targetStatus.reflect then strip[#strip + 1] = 'reflect' end
    if L[2] >= 4 and s.targetStatus.bubble then strip[#strip + 1] = 'bubble' end
    if L[2] >= 4 and s.targetStatus.berserk then strip[#strip + 1] = 'berserk' end
  end
  for _, name in ipairs(strip) do push(a, 'targetRemove', M.STATUS[name]) end
  if L[3] >= 1 and not s.targetStatus.immobilize then
    local left = false
    for _, b in ipairs(M.BUFFS) do
      if s.targetStatus[b] then
        local removed = false
        for _, n in ipairs(strip) do if n == b then removed = true end end
        if not removed then left = true end
      end
    end
    if not left then add(a, 'targetAdd', M.STATUS.immobilize, L[3] >= 2 and 0.75 or 0.5) end
  end
  if L[3] >= 3 and s.targetStatus.immobilize then a.damage = (a.damage or 1) * 1.15 end
  if L[3] >= 4 and s.targetStatus.immobilize and not s.targetStatus.slow then add(a, 'targetAdd', M.STATUS.slow, 0.5) end
end}

rules[M.JOB.samurai] = {
  hit = function(s, a)
    local L, m = s.L, slot(s.actorId)
    if s.foe and m.boost then a.damage = (a.damage or 1) * 1.20; m.boost = nil end
    if s.foe and L[3] >= 1 and s.kill and s.casterStatus.haste then
      add(a, 'casterAdd', M.STATUS.haste, L[3] >= 4 and 0.75 or 0.5)
      if L[3] >= 2 then add(a, 'casterAdd', M.STATUS.bravery, 0.5) end
      if L[3] >= 3 then m.boost = true end
    end
    if s.foe and L[2] >= 3 and s.casterStatus.haste and s.targetStatus.protect then a.damage = (a.damage or 1) / 0.75 end
    if s.foe and L[2] >= 4 and s.casterStatus.haste and s.charge then
      local c = s.charge
      if int(c.current, 1, 0x7FFFFFFF) and int(c.required, 1, 0x7FFFFFFF) and c.current < c.required and c.last == c.current then
        a.chargeTo = math.max(1, c.current - c.required // 20)
      end
    end
    if L[1] < 1 or not s.foe then return end
    -- Zanshin I (0.5.1, 2026-11-10): a combo, or from 2/4 a critical hit, gives Haste, once per Attack. It used
    -- to count hits in a row on one foe, which is the fists' Cadence trigger; the blade's own moment is the combo.
    -- A combo's follow-up hits arrive with s.comboFollow; a fresh hit opens a new Attack.
    if not s.comboFollow then m.fired = nil end
    if m.fired or not (s.comboFollow or (L[1] >= 2 and s.critical)) then return end
    m.fired = true
    -- 0.1.35 (study S-1): Zanshin I grants Haste, it does not refresh it. A Samurai who is already Hasted gets neither the
    -- Haste nor the stage-4 bonus, so a long Haste window ends and Zanshin III (a kill while Hasted) is what extends it.
    if s.casterStatus.haste then return end
    add(a, 'casterAdd', M.STATUS.haste, L[1] >= 3 and 0.75 or 0.5)
    if L[1] >= 4 then a.damage = (a.damage or 1) * 1.15 end
  end,
  -- Evasion setup (Forge 22) for the Samurai's own attacks during Haste.
  aim = function(s, a)
    if s.L[2] >= 1 and s.casterStatus.haste then
      -- 0.1.35 (study S-2): II-1 is the dodge (the body roll, evadeParry), II-2 adds the parry and the block (the weapon
      -- and shield rolls). A ninja sword's Unseen already ignores parry and block, so II-1 makes it unevadable.
      a.evade = {parry = true, weapon = s.L[2] >= 2, shield = s.L[2] >= 2}
    end
  end,
}

rules[M.JOB.gunner] = {hit = function(s, a)
  if not s.foe then return end
  local L, m, c = s.L, slot(s.actorId), s.charge
  if m.target == s.targetId then m.count = (m.count or 0) + 1 elseif m.carry then m.target, m.count, m.carry = s.targetId, m.count, nil
  else m.target, m.count = s.targetId, 1 end
  local frac = 0
  if L[1] >= 1 then frac = L[1] >= 4 and 0.15 or 0.10 end
  if L[1] >= 2 and s.targetStatus.slow then frac = frac + 0.05 end
  local late = c and int(c.current, 1, 0x7FFFFFFF) and int(c.required, 1, 0x7FFFFFFF)
    and c.current < c.required and c.last == c.current and c.current * 4 >= c.required * 3
  if late and L[2] >= 1 then
    frac = frac + 0.10
    if L[2] >= 2 and not s.targetStatus.silence and s.roll(L[2] >= 3 and 25 or 15) then
      add(a, 'targetAdd', M.STATUS.silence, 0.5)
      if L[2] >= 4 and s.canLand(s.target, M.STATUS.silence) then
        for _, p in ipairs(s.party) do
          if p.address ~= s.self and not p.status.faith then other(a, {op = 'add', unit = p.address, id = M.STATUS.faith, scale = 0.5}) break end
        end
      end
    end
  end
  if L[3] >= 1 and m.count >= (L[3] >= 2 and 2 or 3) then
    frac = frac + 0.05
    if L[3] >= 3 and not s.targetStatus.slow then add(a, 'targetAdd', M.STATUS.slow, 0.5) end
    if L[3] >= 4 and s.kill then m.carry = true end
  end
  -- 0.1.34 (audit D15): the push scales with the weapon's charge time, CT/24 capped at double, so the slow gun carries
  -- Suppression. An unknown charge time (no weapon data) leaves the push as it was.
  if frac > 0 and int(s.weaponCt, 1, 255) then frac = frac * math.min(M.SUPPRESSION_CT_CAP, s.weaponCt / M.SUPPRESSION_CT_BASE) end
  if frac > 0 and c and int(c.current, 1, 0x7FFFFFFF) and int(c.required, 1, 0x7FFFFFFF)
      and c.current < c.required and c.last == c.current then
    a.chargeTo = math.max(1, c.current - math.floor(c.required * frac))
  end
  if L[1] >= 3 and s.foes[1] then other(a, {op = 'push', unit = s.foes[1].address, fraction = 0.05}) end
end}

rules[M.JOB.timeMage] = {hit = function(s, a)
  if not s.foe or s.targetStatus.stop then return end
  local L = s.L
  -- 0.1.34 (audit D10b): Disabled foes count from I-1, so a hammer's Disable sets up Detonate at once.
  -- 0.1.35 (study T-1 A): Confused foes count from I-3, so a hand-bomb's Chaos Bombs set up Detonate.
  local held = s.targetStatus.slow or s.targetStatus.disable or (L[1] >= 2 and s.targetStatus.immobilize)
    or (L[1] >= 3 and s.targetStatus.confuse)
  if L[1] >= 1 and held and s.roll(L[1] >= 4 and 25 or 15) then
    add(a, 'targetAdd', M.STATUS.stop, 0.5)
    if L[2] >= 1 and s.canLand(s.target, M.STATUS.stop) then
      for i = 1, (L[2] >= 2 and 2 or 1) do
        local f = s.foes[i]
        if f then
          -- Slow cancels Haste by itself (status table), so level 3 gives something new instead.
          if not f.status.slow then other(a, {op = 'add', unit = f.address, id = M.STATUS.slow, scale = L[2] >= 4 and 1.5 or 1}) end
          if L[2] >= 3 then other(a, {op = 'push', unit = f.address, fraction = 0.10}) end
        end
      end
    end
  end
end}

-- Observe the target before any action can apply a new Stop. This also runs for spells, so an expired Stop's
-- four-hit budget is cleared even when the next action reapplies Stop before anyone lands an unstopped hit.
function M.observeStop(targetId, stopped)
  if targetId ~= nil and not stopped and mem.stopHits then mem.stopHits[targetId] = nil end
end

-- One Stop is stretched at most M.STOP_EXTENDS times, whoever stretches it (Detonate III, or a hybrid's `extend stop`).
-- `s.stopCounted` makes every source in one event count once, and a decision made twice (see decide) spend one extension.
function M.stopExtend(s)
  if s.targetId == nil then return false end
  mem.stopHits = mem.stopHits or {}
  local n = mem.stopHits[s.targetId] or 0
  if s.stopCounted or n < M.STOP_EXTENDS then
    if not s.stopCounted then mem.stopHits[s.targetId], s.stopCounted = n + 1, true end
    return true
  end
  return false
end

-- Time Mage III works for the whole party: any member hitting a Stopped foe.
local function timeMageAura(s, a)
  local best = 0
  for _, p in ipairs(s.party) do if p.job == M.JOB.timeMage then best = math.max(best, s.L[3]) end end
  if best < 1 or not s.foe then return end
  if not s.targetStatus.stop then
    M.observeStop(s.targetId, false)
    return
  end
  if not s.casterStatus.haste then add(a, 'casterAdd', M.STATUS.haste, (best >= 4 and 0.75 or 0.5)) end
  if best >= 2 then a.damage = (a.damage or 1) * 1.15 end
  if best >= 3 and s.targetId and M.stopExtend(s) then a.targetExtend = {[M.STATUS.stop] = 0.10} end
end

-- Damage-based drain is finalized after all hit bonuses, as healing and splash are. The same drain feeds
-- the caster's recovery, shared MP, and the empty-MP Silence condition.
-- Leech: a foe counts as drained dry only when it has a pool (max MP above 0) and nothing left in it. A unit with no
-- MP pool at all, or an unknown pool, is never dry.
local function dryFoe(mp, maxMp) return mp == 0 and type(maxMp) == 'number' and maxMp > 0 end
M.dryFoe = dryFoe

local function manaLeech(s, a, damage)
  local L = s.L
  -- Leech (0.5.9): a foe known to have no MP left cannot be drained, so the same share heals your HP instead, once the
  -- per-hit cap has had its say (a.leechHp, finalized in decide). The MP riders (Silence, the shared drain) need MP.
  if dryFoe(s.targetMp, s.targetMaxMp) then
    a.leechHp = math.max(1, (damage * (L[1] >= 4 and 8 or 5)) // 100)
    return
  end
  local drain = math.min(math.max(1, (damage * (L[1] >= 4 and 8 or 5)) // 100), s.targetMp)
  a.drainMp = drain
  -- "A foe drained down to 0 MP is Silenced": decide() grants it once the per-hit MP cap has had its say, because a
  -- drain the cap cuts short does not empty the foe (the shared drain below is not cut, so it Silences here).
  if L[1] >= 2 and not s.targetStatus.silence then a.leechSilence = 1 end
  if L[1] >= 3 then a.healHp = drain end
  if L[2] >= 1 and s.casterMp and s.casterMaxMp and s.casterMp >= s.casterMaxMp then
    a.drainMp = nil
    if a.leechSilence and drain == s.targetMp then add(a, 'targetAdd', M.STATUS.silence) end
    a.leechSilence = nil
    -- The drain is shared, never multiplied: allies get what the foe lost. Level 4 makes the foe lose 50% more.
    a.targetMpLoss = L[2] >= 4 and math.min(s.targetMp, drain + drain // 2) or drain
    local sorted = {}
    for _, p in ipairs(s.party) do if p.address ~= s.self and p.maxMp > 0 then sorted[#sorted + 1] = p end end
    table.sort(sorted, function(x, y) return x.mp / x.maxMp < y.mp / y.maxMp end)
    local n = math.min(L[2] >= 3 and 2 or 1, #sorted)
    for i = 1, n do
      local p = sorted[i]
      local share = drain // n + (i == 1 and drain % n or 0)
      if share > 0 then other(a, {op = 'mp', unit = p.address, amount = share, fromDamage = true}) end
      if L[2] >= 2 and p.status.silence then other(a, {op = 'remove', unit = p.address, id = M.STATUS.silence}) end
    end
  end
end

rules[M.JOB.greenMage] = {hit = function(s, a)
  if not s.foe or not int(s.damage, 1, 99999) then return end
  local L = s.L
  if L[1] < 1 then return end
  if L[3] >= 1 and s.charge then
    local c = s.charge
    if int(c.current, 1, 0x7FFFFFFF) and int(c.required, 1, 0x7FFFFFFF) and c.current < c.required and c.last == c.current then
      a.chargeTo = math.max(1, c.current - math.floor(c.required * (0.04 + 0.02 * (L[3] - 1))))
    end
  end
  if not (dryFoe(s.targetMp, s.targetMaxMp) or int(s.targetMp, 1, 99999)) then return end   -- 0 leeches HP; an unknown pool pays nothing
  a.manaLeech = true
end}

-- Knight: reactions when an attack on the Knight is parried, blocked or dodged (Forge 240), and when an ally is
-- struck to critical HP (Forge 165, target = party member).
rules[M.JOB.knight] = {
  guard = function(s, a)
    local L = s.L
    local blocked = s.outcome == 'parry' or s.outcome == 'block' or (L[1] >= 2 and s.outcome == 'dodge')
    if not blocked then return end
    if L[1] >= 1 then other(a, {op = 'add', unit = s.self, id = M.STATUS.lure, scale = L[1] >= 3 and 0.75 or 0.5}) end
    if L[1] >= 4 and s.outcome ~= 'dodge' and s.attacker then other(a, {op = 'push', unit = s.attacker, fraction = 0.10}) end
    if L[2] >= 1 and (s.outcome == 'parry' or s.outcome == 'block') then
      local sorted = {}
      for _, p in ipairs(s.party) do if p.address ~= s.self then sorted[#sorted + 1] = p end end
      table.sort(sorted, function(x, y) return x.hpPct < y.hpPct end)
      for i = 1, (L[2] >= 3 and 2 or 1) do
        local p = sorted[i]
        if p then
          other(a, {op = 'add', unit = p.address, id = M.STATUS.protect, scale = 0.5})
          if L[2] >= 2 then other(a, {op = 'add', unit = p.address, id = M.STATUS.shell, scale = 0.5}) end
          if L[2] >= 4 and p.status.immobilize then other(a, {op = 'remove', unit = p.address, id = M.STATUS.immobilize}) end
        end
      end
    end
  end,
  ally = function(s, a)  -- s.self is the Knight; an ally just dropped to critical HP
    if s.L[3] < 1 then return end
    other(a, {op = 'add', unit = s.self, id = M.STATUS.lure, scale = s.L[3] >= 2 and 0.75 or 0.5})
    if s.L[3] >= 3 then other(a, {op = 'add', unit = s.self, id = M.STATUS.bravery, scale = 0.5}) end
    if s.L[3] >= 4 and s.ally then other(a, {op = 'add', unit = s.ally, id = M.STATUS.protect, scale = 0.5}) end
  end,
}

-- Mystic: when it counters an attack (Forge 240 sees the counter roll).
rules[M.JOB.mystic] = {guard = function(s, a)
  if s.outcome ~= 'counter' then return end
  local L = s.L
  if L[1] >= 1 then
    local heal = math.max(1, s.selfMaxHp * (L[1] >= 4 and 8 or 5) // 100)
    other(a, {op = 'hp', unit = s.self, amount = heal})
    if L[1] >= 2 then
      for _, p in ipairs(s.party) do
        if p.address ~= s.self and p.hpPct < 100 then other(a, {op = 'hp', unit = p.address, amount = heal // 2}) break end
      end
    end
    if L[1] >= 3 and s.attacker then other(a, {op = 'push', unit = s.attacker, fraction = 0.10}) end
  end
  if L[2] >= 1 then other(a, {op = 'mp', unit = s.self, amount = L[2] >= 4 and 8 or 4}) end
  if L[2] >= 2 and s.casterStatus.blind then other(a, {op = 'remove', unit = s.self, id = M.STATUS.blind}) end
  if L[2] >= 3 and s.casterStatus.sap then other(a, {op = 'remove', unit = s.self, id = M.STATUS.sap}) end
  if L[3] >= 1 then
    local cured = 0
    for _, n in ipairs(M.AILMENTS) do
      if s.casterStatus[n] and cured < (L[3] >= 4 and 2 or 1) then
        other(a, {op = 'remove', unit = s.self, id = M.STATUS[n]}); cured = cured + 1
      end
    end
    if L[3] >= 2 then other(a, {op = 'add', unit = s.self, id = M.STATUS.protect, scale = 0.5}) end
    -- Haste only when not already Hasted (the opening Haste Hermes Sandals grant counts), like Zanshin I: a counter streak
    -- no longer keeps renewing it.
    if L[3] >= 3 and not s.casterStatus.haste then
      other(a, {op = 'add', unit = s.self, id = M.STATUS.haste, scale = 0.5})
    end
  end
end}

-- White Mage: healing past full HP (Forge 110, s.overflow > 0 on the healed ally s.target).
rules[M.JOB.whiteMage] = {heal = function(s, a)
  if not (s.overflow and s.overflow > 0) then return end
  local L = s.L
  if L[1] < 1 or (s.item and L[1] < 2) then return end
  local regen = L[1] >= 4 and 1.5 or 1
  add(a, 'targetAdd', M.STATUS.regen, L[2] >= 4 and regen * 1.25 or regen)  -- Regen cures Sap by itself (status table)
  -- MP back from overflow: healing spells only (items cost gil, not MP: no gil-for-MP exchange).
  if L[1] >= 3 and not s.item then other(a, {op = 'mp', unit = s.self, amount = math.max(1, s.overflow // 20)}) end
  -- Regen for the others belongs to the cast: a Curaga reaching four allies gives it once (items have no cast id).
  local m = slot(s.actorId)
  local again = s.castId ~= nil and m.overflowCast == s.castId
  if s.castId ~= nil then m.overflowCast = s.castId end
  local reach = again and 0 or (L[2] >= 3 and 99 or (L[2] >= 2 and 2 or (L[2] >= 1 and 1 or 0)))
  local n = 0
  for _, p in ipairs(s.party) do
    if n >= reach then break end
    if p.address ~= s.target then
      other(a, {op = 'add', unit = p.address, id = M.STATUS.regen, scale = L[2] >= 4 and regen * 1.25 or regen}); n = n + 1
    end
  end
  if L[3] >= 1 then add(a, 'targetAdd', M.STATUS.shell, L[3] >= 4 and 0.75 or 0.5) end
  if L[3] >= 2 then add(a, 'targetAdd', M.STATUS.protect, 0.5) end
  if L[3] >= 3 then add(a, 'targetAdd', M.STATUS.faith, 0.5) end
end}

-- Black Mage: magick damage (Forge 170). s.element = element mask of the spell (0 = none).
rules[M.JOB.blackMage] = {magick = function(s, a)
  if not int(s.element, 1, 0xFF) then return end
  local L, m = s.L, slot(s.actorId)
  -- Forge calls this once per target: a spell hitting three foes is one cast, not three.
  local first
  if s.castId ~= nil then
    first = not (m.castId == s.castId and m.castElement == s.element)
  else
    first = not (m.castAt and s.now and s.now - m.castAt < M.CAST_WINDOW and m.castElement == s.element)
  end
  if first then
    local ended = false
    if m.element == s.element then m.chain, m.strayed = (m.chain or 1) + 1, nil
    elseif L[1] >= 2 and m.element and not m.strayed then m.strayed = true
    else
      ended = (m.chain or 0) >= 2
      m.element, m.chain, m.strayed = s.element, 1, nil
    end
    m.castAt, m.castId, m.castElement = s.now, s.castId, s.element
    m.full = m.element == s.element and m.chain % 3 == 0
    -- 0.1.44 (decision 10): another member's hit or spell of this element on the foe in the last 5 s counts too
    if L[1] >= 1 and ((m.element == s.element and m.chain >= 2) or s.partyElement == true or (L[1] >= 3 and ended)) then
      a.casterCharge = L[1] >= 4 and 0.30 or 0.20
    end
  end
  if L[2] >= 1 and m.full then
    if first then
      local refund = math.max(1, (s.mpCost or 0) * (L[2] >= 4 and 50 or (L[2] >= 3 and 40 or 30)) // 100)
      other(a, {op = 'mp', unit = s.self, amount = refund})
    end
    if L[2] >= 2 then a.damage = (a.damage or 1) * 1.15 end
    if L[3] >= 1 then
      if not s.targetStatus.oil then add(a, 'targetAdd', M.STATUS.oil, 0.5) end
      local near = s.foes[1]
      if near and first then
        if L[3] >= 2 and not near.status.oil then other(a, {op = 'add', unit = near.address, id = M.STATUS.oil, scale = 0.5}) end
        if L[3] >= 3 then other(a, {op = 'hp', unit = near.address, damagePct = L[3] >= 4 and -35 or -25}) end
      end
    end
  end
end}

-- Chemist: statuses its attacks land (Forge 182, s.landed = list of status ids being added to the target).
rules[M.JOB.chemist] = {hit = function(s, a)
  local L = s.L
  if not s.foe or L[2] < 1 or not int(s.damage, 1, 99999) then return end
  local hurt = {}
  for _, p in ipairs(s.party) do if p.address ~= s.self and p.hpPct < 100 then hurt[#hurt + 1] = p end end
  table.sort(hurt, function(x, y) return x.hpPct < y.hpPct end)
  for i = 1, (L[2] >= 4 and 2 or 1) do
    local p = hurt[i]
    if p then
      other(a, {op = 'hp', unit = p.address, damagePct = L[2] >= 2 and 20 or 10})
      -- II-3/4 (decision 11): the healed ally is cured of one ailment (this replaced Protect).
      if L[2] >= 3 then
        for _, n in ipairs(M.AILMENTS) do
          if p.status[n] then other(a, {op = 'remove', unit = p.address, id = M.STATUS[n]}); break end
        end
      end
    end
  end
end, buff = function(s, a)
  -- 0.1.34 (audit D12): a buff the Chemist's measure lands on one ally spreads to a second ally at Contagion's chance.
  local L = s.L
  if not s.landed or #s.landed == 0 or L[1] < 1 then return end
  local names = {}
  for _, id in ipairs(s.landed) do
    for _, name in ipairs(M.BUFFS) do if M.STATUS[name] == id then names[#names + 1] = {id = id, name = name} end end
  end
  if #names == 0 then return end
  local pick, missing
  for pass = 1, 2 do   -- another ally first, the Chemist last
    for _, p in ipairs(s.party) do
      if not pick and p.address ~= s.target and (p.address == s.self) == (pass == 2) then
        local need = {}
        for _, n in ipairs(names) do
          if not p.status[n.name] and s.canLand(p.address, n.id) then need[#need + 1] = n.id end
        end
        if #need > 0 then pick, missing = p, need end
      end
    end
  end
  if not pick or not s.roll(L[1] >= 4 and 50 or 35) then return end
  for _, id in ipairs(missing) do other(a, {op = 'add', unit = pick.address, id = id, scale = L[1] >= 2 and 1 or 0.75}) end
end, status = function(s, a)
  if not s.foe or not s.landed or #s.landed == 0 then return end
  local L = s.L
  if L[1] < 1 then return end
  local chance = L[1] >= 4 and 50 or 35
  local reach = L[3] >= 4 and 3 or (L[3] >= 1 and 2 or 1)
  local stripped = {}
  for i = 1, reach do
    local f = s.foes[i]
    if not f then break end
    for _, id in ipairs(s.landed) do
      local easy = L[1] >= 3 and (id == M.STATUS.oil or id == M.STATUS.poison)
      if s.canLand(f.address, id) and s.roll(easy and chance + 15 or chance) then
        other(a, {op = 'add', unit = f.address, id = id, scale = L[1] >= 2 and 1 or 0.75})
        if L[3] >= 2 then other(a, {op = 'hp', unit = f.address, amount = -math.max(1, (s.damage or 0) // 10)}) end
        -- III-3/4 (decision 10): a foe the spread reaches loses its Regen (this replaced Libra).
        if L[3] >= 3 and f.status.regen and not stripped[f.address] then
          stripped[f.address] = true
          other(a, {op = 'remove', unit = f.address, id = M.STATUS.regen})
        end
      end
    end
  end
end}

-- Decide one event for one party member. Returns an action table or nil.
-- Legendary mastery: the owning job, or a hybrid with that job as a parent.
function M.masters(job, owner)
  if owner == nil then return false end
  if job == owner then return true end
  local p = M.PARENTS[job]
  return p ~= nil and (p[1] == owner or p[2] == owner)
end

function M.decide(event, s, claimedRank)
  if type(s) ~= 'table' or not int(s.job, 0, 77) or not (s.job <= 11 or rules[s.job]) then return nil end
  s.L = M.levels(claimedRank)
  if s.L[1] < 1 then return nil end
  -- Chain counters and armed bonuses belong to the current job, not the job used on the previous action.
  if s.actorId ~= nil then slot(s.actorId, s.job) end
  s.party, s.foes = s.party or {}, s.foes or {}
  s.casterStatus, s.targetStatus = s.casterStatus or {}, s.targetStatus or {}
  s.casterHpPct = s.casterHpPct or 100
  s.canLand = s.canLand or function() return true end
  -- A hit decided twice (see below) keeps the rolls of the first pass, or a second roll could undo the first.
  local native, rolls, rolled = s.roll or function() return false end, {}, 0
  s.roll = function(percent)
    rolled = rolled + 1
    if rolls[rolled] == nil then rolls[rolled] = native(percent) end
    return rolls[rolled]
  end
  local r = rules[s.job]
  local function pass()
    local a = {}
    if r and r[event] then r[event](s, a, HELP) end
    if event == 'hit' then
      timeMageAura(s, a)
      if s.L[3] >= 4 and s.foe and M.LEGENDARY[s.weapon] and M.masters(s.job, M.FAMILY_OWNER[s.category]) then
        a.damage = (a.damage or 1) * M.MASTER_DAMAGE
      end
    end
    -- A combo's follow-up hits (same foe, a fraction of a second later) give no MP or HP back: once per attack.
    if event == 'hit' and s.comboFollow then
      a.manaLeech, a.drainPct, a.healPct, a.healHp, a.leechHp = nil, nil, nil, nil, nil
      local kept = {}
      for _, o in ipairs(a.others or {}) do
        local gain = (o.op == 'mp' and ((o.amount or 0) > 0 or (o.mpPct or 0) > 0))
          or (o.op == 'hp' and ((o.amount or 0) > 0 or (o.damagePct or 0) > 0))
        if not gain then kept[#kept + 1] = o end
      end
      a.others = kept[1] and kept or nil
    end
    local final = int(s.damage, 1, 9999999) and math.floor(s.damage * (a.damage or 1)) or 0
    if a.manaLeech then
      a.manaLeech = nil
      if final > 0 then manaLeech(s, a, final) end
    end
    if a.drainPct then
      if final > 0 and int(s.targetMp, 1, 99999) then a.drainMp = math.min(math.max(1, final * a.drainPct // 100), s.targetMp)
      elseif final > 0 and dryFoe(s.targetMp, s.targetMaxMp) then a.leechHp = (a.leechHp or 0) + math.max(1, final * a.drainPct // 100) end
      a.drainPct = nil
    end
    -- drainToHeal (hybrid): at full MP the drain heals the most hurt ally for a share of the damage instead.
    if a.drainHeal then
      local d = a.drainHeal
      a.drainHeal = nil
      if not s.comboFollow and final > 0 and a.drainMp and a.drainMp > 0 and int(s.casterMaxMp, 1, 99999)
          and (s.casterMp or 0) >= s.casterMaxMp then
        other(a, {op = 'hp', unit = d.unit, amount = math.max(1, final * d.pct // 100)})
      end
    end
    if a.healPct then
      if final > 0 then a.healHp = math.max(1, final * a.healPct // 100) end
      a.healPct = nil
    end
    -- Leech: the HP a drain against a foe with no MP pays. A weapon hit pays at most HIT_MP_SHARE percent of your max HP
    -- (a spell has no cap, like its own heal); with your max HP unknown a hit pays nothing. It joins the hit's own heal.
    if a.leechHp then
      local leech = a.leechHp
      a.leechHp = nil
      if event == 'hit' then
        leech = int(s.selfMaxHp, 1, 9999999) and math.min(leech, math.max(1, s.selfMaxHp * M.HIT_MP_SHARE // 100)) or 0
      end
      if leech > 0 then a.healHp = (a.healHp or 0) + leech end
    end
    -- Audit 2026-11-16: an effect worth a share of the damage has nothing to share when no damage is known (the status SPELL
    -- route, hook 181, carries none): drop it instead of paying the 1-point floor below.
    if final == 0 and a.others then
      local kept = {}
      for _, o in ipairs(a.others) do
        if not (o.mpPct or o.damagePct) then kept[#kept + 1] = o end
      end
      a.others = kept[1] and kept or nil
    end
    for _, o in ipairs(a.others or {}) do
      if o.mpPct then
        o.amount, o.mpPct, o.fromDamage = math.max(1, final * o.mpPct // 100), nil, true
      end
      if o.damagePct then
        local size = math.max(1, final * math.abs(o.damagePct) // 100)
        o.amount, o.damagePct = o.damagePct < 0 and -size or size, nil
      end
    end
    -- MP a weapon hit hands out from its damage is capped per hit: damage grows far faster than MP pools.
    if event == 'hit' then
      local function cap(maxMp) return math.max(1, maxMp * M.HIT_MP_SHARE // 100) end
      if a.drainMp and int(s.casterMaxMp, 1, 99999) then a.drainMp = math.min(a.drainMp, cap(s.casterMaxMp)) end
      for _, o in ipairs(a.others or {}) do
        if o.op == 'mp' and o.fromDamage and type(o.amount) == 'number' and o.amount > 0 then
          for _, p in ipairs(s.party) do
            if p.address == o.unit and int(p.maxMp, 1, 99999) then o.amount = math.min(o.amount, cap(p.maxMp)) end
          end
        end
      end
    end
    -- leechSilence (hybrid): a foe the drain empties is Silenced. Decided after the per-hit MP cap, so only the MP that
    -- really moves counts; the runtime checks it again against the cast's MP budget.
    if a.leechSilence then
      local scale, mp = a.leechSilence, s.targetMp
      a.leechSilence = nil
      if a.drainMp and int(mp, 1, 99999) and a.drainMp >= mp and not s.targetStatus.silence then
        a.leechSilence = {scale = scale, need = mp}
      end
    end
    return a
  end
  local saved
  if event == 'hit' and s.actorId ~= nil then
    saved = {}
    for k, v in pairs(slot(s.actorId)) do saved[k] = v end
  end
  local a = pass()
  -- A hit only a bonus makes lethal is still a kill: decide again knowing that, from the same chain state.
  if saved and a.damage and not s.kill and int(s.damage, 1, 9999999) and int(s.targetHp, 1, 9999999)
      and math.floor(s.damage * a.damage) >= s.targetHp then
    mem[s.actorId], s.kill, rolled = saved, true, 0
    a = pass()
  end
  if next(a) == nil then return nil end
  return a
end

-- What each job's Proficiency does, for the gear descriptions: line name, then for each stage the text at levels 1-4
-- (the whole effect at that level, as the rules above do it). '\n' starts the item's one extra line: the stage header
-- and the first sentence share a line, the second line explains the details.
M.GEAR = {
  [M.JOB.whiteMage] = {line = 'Overflow',
    {'Healing spells that heal past full HP give the target Regen',
     'Healing past full HP gives the target Regen\nPotions and other healing items count too',
     'Healing past full HP gives the target Regen, items too\nSpells also return 5% of the excess to you as MP',
     'Healing past full HP gives the target Regen, items too\nRegen lasts 50% longer. Spells return 5% of the excess as MP'},
    {'Overflow Regen also reaches 1 other ally',
     'Overflow Regen also reaches 2 other allies',
     'Overflow Regen reaches the whole party',
     'Overflow Regen reaches the whole party\nand lasts a further 25% longer'},
    {'An overflowing heal also gives the target Shell\nShell at half its normal duration',
     'An overflowing heal also gives Shell and Protect\nBoth at half their normal duration',
     'An overflowing heal also gives Shell, Protect and Faith\nAll three at half their normal duration',
     'An overflowing heal also gives Shell, Protect and Faith\nShell at 3/4 duration, Protect and Faith at half'}},
  [M.JOB.lancer] = {line = 'Grounding',
    {'Your hits strip Float and Haste from the foe',
     'Your hits strip Float, Haste and Faith from the foe',
     'Your hits strip Float, Haste, Faith and Regen from the foe',
     'Your hits strip Float, Haste, Faith, Regen and Bravery\nfrom the foe'},
    {'Hits on an ailing foe, or one with Defense or Magick\nResist cut in 10 s (Broken) strip Protect, or Shell if none',
     'Hits on an ailing or Broken foe strip Protect and Shell\nBroken means its Defense or Magick Resist was cut in 10 s',
     'Hits on an ailing or Broken foe strip Protect and Shell\nReflect is stripped too',
     'Hits on an ailing or Broken foe strip Protect and Shell\nReflect, Bubble and Berserk are stripped too'},
    {'A foe your hit leaves with no buffs is Immobilized\nImmobilize at half its normal duration',
     'A foe your hit leaves with no buffs is Immobilized\nImmobilize at 3/4 of its normal duration',
     'A foe your hit leaves with no buffs is Immobilized\nat 3/4 duration. Hits on Immobilized foes deal 15% more',
     'A foe your hit leaves with no buffs is Immobilized\nHits on Immobilized foes deal 15% more and Slow them'}},
  [M.JOB.gunner] = {line = 'Suppression',
    {"Your hits push a foe's charge back 10%\nA slower weapon pushes harder, up to twice as much",
     "Your hits push a foe's charge back 10%, or 15% if Slowed\nA slower weapon pushes harder, up to twice as much",
     "Hits push a foe's charge back 10%, or 15% if Slowed\nAnother foe nearby is pushed back 5%. Slow weapons push more",
     "Hits push a foe's charge back 15%, or 20% if Slowed\nAnother foe nearby is pushed back 5%. Slow weapons push more"},
    {'Hits on a foe about to act push it back 10% more\nAbout to act means its charge is at least 3/4 full',
     'Hits on a foe about to act push it back 10% more\nand have a 15% chance to Silence it at half duration',
     'Hits on a foe about to act push it back 10% more\nand have a 25% chance to Silence it at half duration',
     'Hits on a foe about to act push it back 10% more\nand may Silence it, 25% of the time, giving an ally Faith'},
    {'From your 3rd hit in a row on one foe, each hit pushes\nits charge back 5% more',
     'From your 2nd hit in a row on one foe, each hit pushes\nits charge back 5% more',
     'From your 2nd hit in a row on one foe, each hit pushes\nits charge back 5% more and Slows it at half duration',
     'From your 2nd hit in a row on one foe, each hit pushes\nit back 5% more and Slows it. A kill carries the count on'}},
  [M.JOB.greenMage] = {line = 'Mana Leech',
    {'Hits leech 5% of the damage as MP, up to 2% of your max MP',
     'Hits leech 5% of the damage as MP, up to 2% of your max MP\nA foe drained down to 0 MP is Silenced',
     'Hits leech 5% of the damage as MP, up to 2% of your max MP\nYou heal 5% of the damage. A foe drained to 0 MP is Silenced',
     'Hits leech 8% of the damage as MP, up to 2% of your max MP\nYou heal 8% of the damage. A foe drained to 0 MP is Silenced'},
    {'While your MP is full, drained MP goes to the ally\nwho is lowest on MP instead',
     'At full MP, drained MP goes to the ally lowest on MP\nand cures that ally of Silence',
     'At full MP, the 2 allies lowest on MP share the drain\nand are cured of Silence',
     'At full MP, the 2 allies lowest on MP share the drain\nand are cured of Silence. The foe loses 50% more MP'},
    {"Your hits push the foe's charge time back 4%",
     "Your hits push the foe's charge time back 6%",
     "Your hits push the foe's charge time back 8%",
     "Your hits push the foe's charge time back 10%"}},
  [M.JOB.knight] = {line = 'Bulwark',
    {'Parrying or blocking an attack gives you Lure\nFoes turn on you. Lure lasts half its normal duration',
     'Parrying, blocking or dodging an attack gives you Lure\nFoes turn on you. Lure lasts half its normal duration',
     'Parrying, blocking or dodging an attack gives you Lure\nFoes turn on you. Lure lasts 3/4 of its normal duration',
     "Parrying, blocking or dodging gives you Lure at 3/4 duration\nA parry or block also pushes the attacker's charge back 10%"},
    {'A parry or block gives the most hurt ally Protect\nProtect at half its normal duration',
     'A parry or block gives the most hurt ally Protect and Shell\nBoth at half their normal duration',
     'A parry or block gives the 2 most hurt allies\nProtect and Shell at half their normal duration',
     'A parry or block gives the 2 most hurt allies Protect\nand Shell at half duration, and frees them of Immobilize'},
    {'When a physical hit drops an ally to critical HP\nyou gain Lure, so foes turn on you, at half duration',
     'When a physical hit drops an ally to critical HP\nyou gain Lure, so foes turn on you, at 3/4 duration',
     'When a physical hit drops an ally to critical HP\nyou gain Lure at 3/4 duration and Bravery at half',
     'When a physical hit drops an ally to critical HP\nyou gain Lure and Bravery, and that ally gains Protect'}},
  [M.JOB.mystic] = {line = 'Ki Flow',
    {'Each counterattack heals you for 5% of your max HP',
     'Each counter heals you for 5% of your max HP\nOne hurt ally is healed for half that',
     "Each counter heals you for 5% of your max HP\nA hurt ally gets half. The foe's charge is pushed back 10%",
     "Each counter heals you for 8% of your max HP\nA hurt ally gets half. The foe's charge is pushed back 10%"},
    {'Each counterattack restores 4 MP',
     'Each counter restores 4 MP and cures your Blind',
     'Each counter restores 4 MP and cures your Blind and Sap',
     'Each counter restores 8 MP and cures your Blind and Sap'},
    {'Each counterattack cures you of one ailment',
     'Each counter cures you of one ailment\nand gives you Protect at half duration',
     'Each counter cures one of your ailments and gives you\nProtect, and Haste if not Hasted, both at half duration',
     'Each counter cures two of your ailments and gives you\nProtect, and Haste if not Hasted, both at half duration'}},
  [M.JOB.timeMage] = {line = 'Detonate',
    {'Hits on a Slowed or Disabled foe may Stop it\nwith a 15% chance per hit, at half duration',
     'Hits on a Slowed, Disabled or Immobilized foe may Stop it\nwith a 15% chance per hit, at half duration',
     'Hits on a Slowed, Disabled, Immobilized or Confused foe\nmay Stop it, with a 15% chance per hit, at half duration',
     'Hits on a Slowed, Disabled, Immobilized or Confused foe\nmay Stop it, with a 25% chance per hit, at half duration'},
    {'When Detonate Stops a foe, another foe is Slowed',
     'When Detonate Stops a foe, 2 other foes are Slowed',
     'When Detonate Stops a foe, 2 other foes are Slowed\nand their charge is pushed back 10%',
     'When Detonate Stops a foe, 2 other foes are Slowed\nSlow lasts 50% longer, and their charge is pushed back 10%'},
    {'A party member who hits a Stopped foe gains Haste\nif not already Hasted, at half duration',
     'Party hits on a Stopped foe deal 15% more damage\nand give the hitter Haste, if not Hasted, at half duration',
     'Party hits on a Stopped foe deal 15% more damage and\ngive the hitter Haste at half duration and lengthen the Stop',
     'Party hits on a Stopped foe deal 15% more damage and\ngive the hitter Haste at 3/4 duration, and lengthen the Stop'}},
  [M.JOB.warrior] = {line = 'Cleave',
    {'Killing a foe gives you Bravery',
     'Killing a foe gives you Bravery\nA greatsword swing that hits a second foe does too',
     'Kills and two-foe greatsword swings give you Bravery\nBravery lasts 50% longer',
     'Kills and two-foe greatsword swings give Bravery, 50% longer\nA kill while Brave also cures Slow, Disable and Immobilize'},
    {'While below 50% HP, your hits give you Bravery',
     'While below 50% HP, your hits give you Bravery\nand Break the foe',
     'While below 75% HP, your hits give you Bravery\nand Break the foe',
     'While below 75% HP, your hits give you Bravery and Break\nthe foe. They also give one ally Bravery at half duration'},
    {'While you are at critical HP, your hits inflict Sap',
     'At critical HP, your hits inflict Sap\nYour hits deal 15% more damage to Sapped foes',
     'At critical HP, your hits inflict Sap. Sapped foes take\n15% more from you. Killing one spreads Sap to a second foe',
     'At critical HP, your hits inflict Sap. Sapped foes take\n15% more from you. Killing one spreads Sap to two foes'}},
  [M.JOB.hunter] = {line = 'Pursuit',
    {'Your hits deal 15% more damage to an ailing or Broken foe\nBroken means its Defense or Magick Resist was cut in 10 s',
     'Your hits deal 15% more damage to an ailing or Broken foe\nConsecutive hits on that foe add another flat 5%',
     'Hits on an ailing or Broken foe deal 15% more damage, or\n20% on follow-up hits. A kill heals you 10% of the damage',
     'Hits on an ailing or Broken foe deal 25% more damage, or\n30% on follow-up hits. A kill heals you 10% of the damage'},
    {'Hits on a foe with a timed ailment restart one of its timers\nTimed ailments are Disable, Immobilize, Poison and Slow',
     'Hits on a foe with a timed ailment restart two of its timers\nTimed ailments are Disable, Immobilize, Poison and Slow',
     'Hits on a foe with a timed ailment restart two of its timers\nand stretch each 10% longer than a normal timer',
     'Hits on a foe with a timed ailment restart all its timers\nand stretch each 10% longer than a normal timer'},
    {'Killing an ailing or Broken foe gives you Haste\nHaste at half its normal duration',
     'Killing an ailing or Broken foe gives you Haste\nand Slows the next foe you hit, both at half duration',
     'Killing an ailing or Broken foe gives you Haste and Slows\nyour next target and one other foe, all at half duration',
     'Killing an ailing or Broken foe gives you Haste\nat 3/4 duration, and Slows your next target and another foe'}},
  [M.JOB.blackMage] = {line = 'Resonance',
    {'Repeating an element, or one an ally just hit the foe with,\nmakes your next action 20% faster',
     'Repeating an element, or one an ally just hit the foe with,\nmakes your next action 20% faster, forgiving a stray spell',
     'Repeating an element, or one an ally just hit the foe with,\nor ending a chain, makes your next action 20% faster',
     'Repeating an element, or one an ally just hit the foe with,\nor ending a chain, makes your next action 30% faster'},
    {'Every 3rd cast of the same element in a row\nrefunds 30% of its MP cost',
     'Every 3rd same-element cast refunds 30% of its MP\nand deals 15% more damage',
     'Every 3rd same-element cast refunds 40% of its MP\nand deals 15% more damage',
     'Every 3rd same-element cast refunds half its MP cost\nand deals 15% more damage'},
    {'Every 3rd same-element cast Oils the target\nOil at half its normal duration',
     'Every 3rd same-element cast Oils the target\nand one other foe, both at half duration',
     'Every 3rd cast Oils the target and one other foe\nThat other foe also takes 25% of the damage',
     'Every 3rd cast Oils the target and one other foe\nThat other foe also takes 35% of the damage'}},
  [M.JOB.samurai] = {line = 'Zanshin',
    {'Landing a combo gives you Haste, if not Hasted\nHaste at half its normal duration',
     'A combo or a critical hit gives you Haste if not Hasted\nHaste at half its normal duration',
     'A combo or a critical hit gives you Haste if not Hasted\nHaste at 3/4 of its normal duration',
     'A combo or a critical hit gives you Haste if not Hasted\nHaste at 3/4 duration, and that hit deals 15% more damage'},
    {'While Hasted, your attacks cannot be dodged',
     'While Hasted, your attacks cannot be dodged,\nparried or blocked',
     'While Hasted, your attacks cannot be evaded\nand go straight through Protect',
     "While Hasted, your attacks cannot be evaded and go\nstraight through Protect and push the foe's charge back 5%"},
    {'A kill while Hasted renews your Haste at half duration',
     'A kill while Hasted renews your Haste\nand gives you Bravery, both at half duration',
     'A kill while Hasted renews your Haste and gives Bravery\nboth at half duration. Your next hit deals 20% more damage',
     'A kill while Hasted renews Haste at 3/4 duration, gives\nBravery at half duration. Your next hit deals 20% more'}},
  [M.JOB.chemist] = {line = 'Contagion',
    {'A status your attack lands may spread to another foe\nwith a 35% chance, at 3/4 duration. Measure buffs spread too',
     'A status your attack lands may spread to another foe\nwith a 35% chance. Buffs from your measure spread to an ally',
     'A status your attack lands may spread to another foe\nwith a 35% chance, or 50% for Oil and Poison',
     'A status your attack lands may spread to another foe\nwith a 50% chance, or 65% for Oil and Poison'},
    {'Your hits heal the most hurt ally for 10% of the damage',
     'Your hits heal the most hurt ally for 20% of the damage',
     "Your hits heal the most hurt ally for 20% of the damage\nand cure one of that ally's ailments",
     'Your hits heal the 2 most hurt allies for 20% of the damage\nand cure one ailment on each'},
    {'Contagion can spread to 2 foes',
     'Contagion can spread to 2 foes\nEach foe it spreads to takes 10% of the damage',
     'Contagion can spread to 2 foes\nEach takes 10% of the damage and loses its Regen',
     'Contagion can spread to 3 foes\nEach takes 10% of the damage and loses its Regen'}},
}
M.GEAR_LIMIT = 60       -- characters on the stage header's line (the header itself takes about 16 more)
M.GEAR_LINE_LIMIT = 60  -- characters on the one extra line
-- The gear lines for one job at the effective rank: header, then stages I-III (nil = nothing to show).
function M.gearLines(job, effectiveRank)
  local g = M.GEAR[job]
  if not g then return nil end
  local L = M.levels(effectiveRank)
  local out = {g.line .. '  I ' .. L[1] .. '/4  II ' .. L[2] .. '/4  III ' .. L[3] .. '/4'}
  for k = 1, 3 do
    local text = L[k] >= 1 and g[k][L[k]] or ''
    out[k + 1] = text ~= '' and text or nil
  end
  if L[1] < 1 then out[1] = g.line .. '  (no Writ yet: see Montblanc)' end
  return out
end

-- Montblanc's lines. Every box fits the native dialogue window: at most 3 lines of 48 characters ({color:...} markup
-- does not count). Numbers are plain digits.
local WRIT = {"Apprentice's Writ", "Journeyman's Writ", "Master's Writ"}
local function gold(text) return '{color:gold}' .. text .. '{color:none}' end

-- Montblanc's Proficiency topic (asked for in the Clan hall; the runtime shows these through the dialog plugin).
M.TOPIC_PAGES = {
  'Clan Centurio trains every member in their\njob, kupo. That training is ' .. gold('Proficiency') .. '.',
  'Each job has its own line of three stages,\nand each stage grows through four levels.\nEvery new Clan rank brings a new level.',
  "Learned two jobs? Then you fight with that\npair's own line, not either job's alone.\nEvery pairing has one, kupo!",
  'The line follows your job, not your gear.\nChange jobs freely: what the clan taught\nyou stays taught, whatever you wield.',
  'To see a stage at work, open Equipment.\nWeapons show stage I, body armour stage II\nand helmets stage III for that character.',
  "Master all three stages at rank 12, and a\nlegendary weapon of your job's own kind\nstrikes a quarter harder, kupo!",
}
M.REMINDER = 'Kupo! Before you go: your Clan rank has\nrisen. Ask me about ' .. gold('Proficiency') .. '!'
-- "Have I earned anything new?" when the claim could not be handled now (a Writ could not be granted, or the Clan rank
-- could not be read): a neutral line, never "nothing new".
M.CLAIM_WAIT = 'Give me a moment to find your papers, kupo.\nAsk me again shortly.'
-- "About Proficiency." with no Legacy campaign loaded: one short line, and nothing is claimed. A campaign is known only
-- once its save is loaded, so a new game asks for both until then, saved or not (0.1.50); another save gets it too.
M.NO_CAMPAIGN = 'Save your journey and load it, kupo, then I\'ll\nopen your papers.'
-- The topic menu, most used first; the last choice is also Cancel. M.TOPIC names each choice's 0-based index.
M.TOPIC_CHOICES = {'Have I earned anything new?', 'Where do I stand?', 'Tell me about the Writs.', 'What is Proficiency?',
                   "That's all."}
M.TOPIC = {claim = 0, stand = 1, writs = 2, about = 3, bye = 4}

local MASTER = 'You hold everything the clan teaches. A\nmaster hunter, kupo!'
local AHEAD = "Your rank is ahead of my papers! Ask me\nwhat you've earned, kupo."
local function u32(v) return int(v, 0, 0xFFFFFFFF) end

-- The topic state the helpers below read (all optional; anything unreadable is left out):
--   rank     the Clan rank the game computes now (1-12)        claimed  the rank Montblanc has rewarded (0-12)
--   writs    {[k] = true} for each Writ in the Key Items
--   have     {points, marks, den} from the save                need     {rank, points, marks, den}: the next rank's row
local function claimedOf(state) return int(state.claimed, 0, M.MAX_RANK) and state.claimed or 0 end
local function rankOf(state) return int(state.rank, 1, M.MAX_RANK) and state.rank or nil end

-- The opening line: every rank rewarded, a rank waiting, or nothing waiting.
function M.greeting(state)
  state = type(state) == 'table' and state or {}
  local claimed, rank = claimedOf(state), rankOf(state)
  if claimed >= M.MAX_RANK then return 'My finest hunter! What can Clan Centurio\ndo for you today?' end
  if rank and rank > claimed then return "Kupo! Your rank has gone up since we last\nspoke. Shall we see what you've earned?" end
  return 'Proficiency, kupo? What would you like\nto know?'
end

-- What the next rank needs (the game's rank table row in state.need, the save's counts in state.have). `short` gives the
-- answer to "Have I earned anything new?" when nothing is. Returns boxes, or nil when either table is unreadable.
function M.nextNeeds(state, short)
  state = type(state) == 'table' and state or {}
  local need, have = state.need, state.have
  if type(need) ~= 'table' or not int(need.rank, 2, M.MAX_RANK) or not u32(need.points) or not int(need.marks, 0, 0xFF)
     or not int(need.den, 0, 30) then return nil end
  if type(have) ~= 'table' or not u32(have.points) or not int(have.marks, 0, 0xFF) or not int(have.den, 0, 30) then return nil end
  local boxes
  if short then
    boxes = {string.format('Nothing new yet. Rank %d needs %d clan\npoints and %d marks hunted, kupo!',
                           need.rank, need.points, need.marks)}
  else
    boxes = {string.format('Rank %d needs %d clan points and\n%d marks hunted. You have %d and %d.',
                           need.rank, need.points, need.marks, have.points, have.marks)}
  end
  -- only rank 12 asks for Sky Pirate's Den entries in the game's table (30 of 30)
  if need.den > 0 then
    boxes[#boxes + 1] = string.format("...and a full Sky Pirate's Den. You have %d\nof %d entries.", have.den, need.den)
  end
  return boxes
end

-- "Where do I stand?": the rank, the rewarded rank, the stage levels, then what comes next.
function M.standing(state)
  state = type(state) == 'table' and state or {}
  local claimed, rank = claimedOf(state), rankOf(state)
  local first
  if rank and claimed > 0 then first = string.format("Clan rank %d, and I've rewarded you up to\nrank %d.", rank, claimed)
  elseif rank then first = string.format("Clan rank %d. I haven't rewarded\nany rank yet. Ask what you've earned!", rank)
  elseif claimed > 0 then first = string.format("Your Clan rank is a mystery to me! I've\nrewarded you up to rank %d.", claimed)
  else first = "Your Clan rank is a mystery to me! I\nhaven't rewarded any rank yet." end
  local L = M.levels(claimed)
  local boxes = {first, string.format('Stage I is at %d/4, stage II at %d/4\nand stage III at %d/4.', L[1], L[2], L[3])}
  if claimed >= M.MAX_RANK then
    boxes[#boxes + 1] = MASTER
  elseif rank and rank > claimed then
    -- at rank 0 rewarded, the first box already says to ask
    if claimed > 0 then boxes[#boxes + 1] = AHEAD end
  else
    local needs = M.nextNeeds(state)
    if needs then
      for _, b in ipairs(needs) do boxes[#boxes + 1] = b end
    else
      boxes[#boxes + 1] = string.format('Reach Clan rank %d and ask me again,\nkupo! There is more to learn.', claimed + 1)
    end
  end
  return boxes
end

-- "Have I earned anything new?" when nothing is: the next rank's needs, or the master line at rank 12.
function M.nothingNew(state)
  state = type(state) == 'table' and state or {}
  if claimedOf(state) >= M.MAX_RANK then return {MASTER} end
  return M.nextNeeds(state, true) or {'Nothing new yet, kupo! Raise your Clan\nrank and come see me again.'}
end

-- "Tell me about the Writs.": the three Writs, where to keep them, and what the party carries now. The stamps on the
-- highest Writ carried are its stage's level at the rewarded rank.
function M.writStatus(state)
  state = type(state) == 'table' and state or {}
  local claimed = claimedOf(state)
  local writs = type(state.writs) == 'table' and state.writs or {}
  local boxes = {
    gold(WRIT[1]) .. ': stage I, ranks 1-4.\n' .. gold(WRIT[2]) .. ': stage II, ranks 5-8.\n' ..
      gold(WRIT[3]) .. ': stage III, ranks 9-12.',
    'Keep them in your Key Items. A stage only\nanswers to a hunter who carries its Writ.',
  }
  local top
  for k = 3, 1, -1 do if writs[k] == true then top = k break end end
  if top then
    boxes[#boxes + 1] = string.format('You carry the %s, with\n%d of 4 stamps.', gold(WRIT[top]), M.levels(claimed)[top])
  elseif claimed == 0 then
    boxes[#boxes + 1] = "You carry no Writ yet. Ask what you've\nearned and I'll start your papers!"
  end
  for _, k in ipairs(M.writsNeeded(claimed)) do
    if writs[k] ~= true then
      boxes[#boxes + 1] = "Lost a Writ? Ask what you've earned and\nI'll write you a new one, kupo."
      break
    end
  end
  return boxes
end

-- Montblanc's lines for newly rewarded ranks (from+1 .. to). Returns a list of dialogue boxes. One "kupo" per Writ handed
-- over, none on a plain stamp.
function M.speech(from, to)
  if not int(from, 0, M.MAX_RANK) or not int(to, 0, M.MAX_RANK) or to <= from then return {} end
  local boxes = {}
  if to - from > 1 then
    boxes[#boxes + 1] = 'Kupo! I was filing your training papers and\nfound these waiting for you. You\'ve earned\nevery one!'
  end
  local stage = (to - 1) // 4 + 1
  local level = to - 4 * (stage - 1)
  local roman = ({'I', 'II', 'III'})[stage]
  if from < 1 and to >= 1 then
    boxes[#boxes + 1] = 'Kupo! Perfect timing! Clan Centurio trains\nevery member in their job. Take this\n' .. gold(WRIT[1]) .. '!'
    boxes[#boxes + 1] = 'Whatever job you take up, the Writ unlocks its\n' .. gold('Proficiency') .. ': your weapons and armour fight\nthe way that job does, whatever you wield!'
    boxes[#boxes + 1] = 'Rise through the ranks and I\'ll stamp it for\nyou each time. A Writ full of stamps is a\nhunter\'s pride!'
  end
  if from < 5 and to >= 5 then
    boxes[#boxes + 1] = 'Kupo-po! That apprentice\'s paper can\'t hold\nanother stamp! Here, a ' .. gold(WRIT[2]) .. '.'
    boxes[#boxes + 1] = 'Every job you know now opens its second\n' .. gold('Proficiency') .. '. Go show those marks what a\nCenturio journeyman can do!'
  end
  if from < 9 and to >= 9 then
    boxes[#boxes + 1] = 'Kupo... You\'ve come so far. I had this made\nfor very few hunters: a ' .. gold(WRIT[3]) .. '.'
    boxes[#boxes + 1] = 'Your third ' .. gold('Proficiency') .. ' is open. The clan has\nnothing left to teach you, only stamps\nleft to give!'
  end
  if to == M.MAX_RANK then
    boxes[#boxes + 1] = 'And the last stamp on your ' .. gold(WRIT[3]) .. '!\nEvery ' .. gold('Proficiency') .. ' at its peak. Even I\'m a\nlittle jealous, kupo!'
    boxes[#boxes + 1] = 'Take up a legendary weapon of your job\'s\nown kind. Now you\'ll feel what mastery is!'
  elseif level > 1 then
    boxes[#boxes + 1] = 'Your ' .. gold(WRIT[stage]) .. ' gets a fresh stamp!\n' .. gold('Proficiency ' .. roman .. ' ' .. level .. '/4') .. ' grows stronger for\nevery job you take up!'
  end
  return boxes
end

-- A Writ handed over late: its rank was rewarded before the Writs became key items, or the item went missing.
function M.writSpeech(stage)
  if not int(stage, 1, 3) then return {} end
  return {'Kupo! Wait, I almost forgot your\n' .. gold(WRIT[stage]) .. '! Your ' .. gold('Proficiency') ..
          ' only\nanswers to a hunter who carries it!'}
end
-- Whether M.speech(from, to) already hands over Writ k.
function M.speechGives(from, to, stage)
  return int(from, 0, M.MAX_RANK) and int(to, 0, M.MAX_RANK) and int(stage, 1, 3)
    and from < M.STAGE_START[stage] and to >= M.STAGE_START[stage]
end

return M
