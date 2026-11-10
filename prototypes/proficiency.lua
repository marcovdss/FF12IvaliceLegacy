-- Proficiency: each job's Clan line, earned from Montblanc one Clan rank at a time and applied to whatever the
-- character wields. 12 ranks = 3 stages x 4 levels: ranks 1-4 raise stage I, 5-8 stage II, 9-12 stage III.
-- The line is chosen by the character's CURRENT job. This module only decides; the Forge adapters apply the result
-- through Forge's own end-of-hit step (statuses, durations, HP/MP), the charge time, or helpers on other units.
-- Every level is built from operations already seen working in play; where a designed effect needed an engine detail
-- nobody has proven, the level does the nearest thing those operations allow (reports/LEGACY-PROFICIENCY-040.md).
local M = {}
M.version = '0.5.3'

M.NO_JOB = 255
M.JOB = {whiteMage = 0, lancer = 1, gunner = 2, greenMage = 3, knight = 4, mystic = 5, timeMage = 6, warrior = 7,
         hunter = 8, blackMage = 9, samurai = 10, chemist = 11}
M.STATUS = {ko = 0, stop = 3, blind = 7, poison = 8, silence = 9, sap = 10, oil = 11, disable = 13,
            immobilize = 14, slow = 15, disease = 16, lure = 17, protect = 18, shell = 19, haste = 20, bravery = 21,
            faith = 22, reflect = 23, invisible = 24, regen = 25, float = 26, berserk = 27, bubble = 28, hpCritical = 29,
            libra = 30}
M.HINDERED = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil', 'stop'}
M.BUFFS = {'protect', 'shell', 'haste', 'bravery', 'faith', 'reflect', 'regen', 'float', 'bubble', 'invisible', 'berserk'}
-- Ailments with a running timer, shortest first (status table durations: Disable 20, Immobilize 50, Poison 50, Slow 60).
-- Blind, Silence, Sap, Oil and Disease have no timer, so renewing them would do nothing.
M.TIMED = {'disable', 'immobilize', 'poison', 'slow'}
M.AILMENTS = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil'}
M.GREATSWORD = 2
M.MAX_RANK = 12
-- Legendary weapons: any job wields them at base power; full power needs Clan mastery in the job that owns the family.
M.MASTER_DAMAGE = 1.25
-- BaseJobs 0.6.0 (Marco 2026-11-10): hammers moved from Warrior to Time Mage, measures from Time Mage to Chemist.
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
  local prey = has(s.targetStatus, M.HINDERED)
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
  end
  -- Libra only works on the party (foes cannot hold it): the Hunter gets it and sees the foes' details.
  if L[2] >= 3 and prey and not s.casterStatus.libra then add(a, 'casterAdd', M.STATUS.libra) end
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
    if L[2] >= 2 then add(a, 'casterAdd', M.STATUS.protect, 0.5) end
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
  if L[2] >= 1 and has(s.targetStatus, M.HINDERED) then
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
    -- Zanshin I (0.5.1, Marco 2026-11-10): a combo, or from 2/4 a critical hit, gives Haste, once per Attack. It used
    -- to count hits in a row on one foe, which is the fists' Cadence trigger; the blade's own moment is the combo.
    -- A combo's follow-up hits arrive with s.comboFollow; a fresh hit opens a new Attack.
    if not s.comboFollow then m.fired = nil end
    if m.fired or not (s.comboFollow or (L[1] >= 2 and s.critical)) then return end
    m.fired = true
    add(a, 'casterAdd', M.STATUS.haste, L[1] >= 3 and 0.75 or 0.5)
    if L[1] >= 4 then a.damage = (a.damage or 1) * 1.15 end
  end,
  -- Evasion setup (Forge 22) for the Samurai's own attacks during Haste.
  aim = function(s, a)
    if s.L[2] >= 1 and s.casterStatus.haste then
      a.evade = {weapon = true, shield = true, parry = s.L[2] >= 2}
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
  if frac > 0 and c and int(c.current, 1, 0x7FFFFFFF) and int(c.required, 1, 0x7FFFFFFF)
      and c.current < c.required and c.last == c.current then
    a.chargeTo = math.max(1, c.current - math.floor(c.required * frac))
  end
  if L[1] >= 3 and s.foes[1] then other(a, {op = 'push', unit = s.foes[1].address, fraction = 0.05}) end
end}

rules[M.JOB.timeMage] = {hit = function(s, a)
  if not s.foe or s.targetStatus.stop then return end
  local L = s.L
  local held = s.targetStatus.slow or (L[1] >= 2 and s.targetStatus.immobilize) or (L[1] >= 3 and s.targetStatus.disable)
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
  if best >= 3 and s.targetId then
    mem.stopHits = mem.stopHits or {}
    local n = mem.stopHits[s.targetId] or 0
    if s.stopCounted or n < M.STOP_EXTENDS then
      if not s.stopCounted then mem.stopHits[s.targetId], s.stopCounted = n + 1, true end
      a.targetExtend = {[M.STATUS.stop] = 0.10}
    end
  end
end

-- Damage-based drain is finalized after all hit bonuses, as healing and splash are. The same drain feeds
-- the caster's recovery, shared MP, and the empty-MP Silence condition.
local function manaLeech(s, a, damage)
  local L = s.L
  local drain = math.min(math.max(1, (damage * (L[1] >= 4 and 8 or 5)) // 100), s.targetMp)
  a.drainMp = drain
  if L[1] >= 2 and drain == s.targetMp and not s.targetStatus.silence then add(a, 'targetAdd', M.STATUS.silence) end
  if L[1] >= 3 then a.healHp = drain end
  if L[2] >= 1 and s.casterMp and s.casterMaxMp and s.casterMp >= s.casterMaxMp then
    a.drainMp = nil
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
  if not int(s.targetMp, 1, 99999) then return end
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
    if L[3] >= 3 then other(a, {op = 'add', unit = s.self, id = M.STATUS.haste, scale = 0.5}) end
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
    if L[1] >= 1 and ((m.element == s.element and m.chain >= 2) or (L[1] >= 3 and ended)) then
      a.casterCharge = L[1] >= 4 and 0.30 or 0.20
    end
  end
  if L[2] >= 1 and m.full then
    if first then
      local refund = math.max(1, (s.mpCost or 0) * (L[2] >= 4 and 60 or (L[2] >= 3 and 40 or 30)) // 100)
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
      if L[2] >= 3 and not p.status.protect then other(a, {op = 'add', unit = p.address, id = M.STATUS.protect, scale = 0.5}) end
    end
  end
end, status = function(s, a)
  if not s.foe or not s.landed or #s.landed == 0 then return end
  local L = s.L
  if L[1] < 1 then return end
  local chance = L[1] >= 4 and 50 or 35
  local reach = L[3] >= 4 and 3 or (L[3] >= 1 and 2 or 1)
  for i = 1, reach do
    local f = s.foes[i]
    if not f then break end
    for _, id in ipairs(s.landed) do
      local easy = L[1] >= 3 and (id == M.STATUS.oil or id == M.STATUS.poison)
      if s.canLand(f.address, id) and s.roll(easy and chance + 15 or chance) then
        other(a, {op = 'add', unit = f.address, id = id, scale = L[1] >= 2 and 1 or 0.75})
        if L[3] >= 2 then other(a, {op = 'hp', unit = f.address, amount = -math.max(1, (s.damage or 0) // 10)}) end
        if L[3] >= 3 and not s.casterStatus.libra then add(a, 'casterAdd', M.STATUS.libra) end
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
      a.manaLeech, a.drainPct, a.healPct, a.healHp = nil, nil, nil, nil
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
      if final > 0 and int(s.targetMp, 1, 99999) then a.drainMp = math.min(math.max(1, final * a.drainPct // 100), s.targetMp) end
      a.drainPct = nil
    end
    if a.healPct then
      if final > 0 then a.healHp = math.max(1, final * a.healPct // 100) end
      a.healPct = nil
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
     'Healing past full HP (items too) gives the target Regen\nHealing spells return 5% of the overflow as MP',
     'Healing past full HP (items too) gives the target Regen\nRegen lasts 50% longer, spells return 5% of it as MP'},
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
     'Your hits strip Float, Haste, Faith, Regen and Bravery'},
    {'Hits on a foe with an ailment strip its Protect\n(or its Shell, if it has no Protect)',
     'Hits on a foe with an ailment strip Protect and Shell',
     'Hits on a foe with an ailment strip Protect and Shell\nReflect is stripped too',
     'Hits on a foe with an ailment strip Protect and Shell\nReflect, Bubble and Berserk are stripped too'},
    {'A foe your hit leaves with no buffs is Immobilized\nImmobilize at half its normal duration',
     'A foe your hit leaves with no buffs is Immobilized\nImmobilize at 3/4 of its normal duration',
     'A foe your hit leaves with no buffs is Immobilized (3/4)\nYour hits deal +15% damage to Immobilized foes',
     'A foe your hit leaves with no buffs is Immobilized (3/4)\nHits on Immobilized foes deal +15% and Slow them (half)'}},
  [M.JOB.gunner] = {line = 'Suppression',
    {"Your hits push the foe's charge time back 10%",
     "Your hits push the foe's charge time back 10%\n15% instead if the foe is Slowed",
     "Your hits push the foe's charge back 10% (Slowed: 15%)\nThe nearest other foe is pushed back 5% as well",
     "Your hits push the foe's charge back 15% (Slowed: 20%)\nThe nearest other foe is pushed back 5% as well"},
    {'A foe that is about to act is pushed back 10% more\nAbout to act: its charge is at least 3/4 full',
     'A foe about to act is pushed back 10% more\nand has a 15% chance to be Silenced (half duration)',
     'A foe about to act is pushed back 10% more\nand has a 25% chance to be Silenced (half duration)',
     'A foe about to act is pushed back 10% more, 25% Silence\nWhen that Silence lands, one ally gains Faith (half)'},
    {'3 hits in a row on the same foe push it back 5% more',
     '2 hits in a row on the same foe push it back 5% more',
     '2 hits in a row on the same foe push it back 5% more\nand Slow it (half duration)',
     '2 hits in a row on one foe: 5% more push and Slow (half)\nKilling that foe carries the count to your next target'}},
  [M.JOB.greenMage] = {line = 'Mana Leech',
    {'Hits drain 5% of damage as MP (max 2% of your MP)',
     'Hits drain 5% of damage as MP (max 2% of your MP)\nA foe drained down to 0 MP is Silenced',
     'Hits drain 5% of damage as MP (max 2% of your MP)\nYou heal 5% of the damage as HP, 0 MP foes are Silenced',
     'Hits drain 8% of damage as MP (max 2% of your MP)\nYou heal 8% of the damage as HP, 0 MP foes are Silenced'},
    {'While your MP is full, drained MP goes to the ally\nwho is lowest on MP instead',
     'At full MP, drained MP goes to the ally lowest on MP\nand cures that ally of Silence',
     'At full MP, the 2 allies lowest on MP share the drain\nand are cured of Silence',
     'At full MP, the 2 allies lowest on MP share the drain\nand are cured of Silence, the foe loses 50% more MP'},
    {"Your hits push the foe's charge time back 4%",
     "Your hits push the foe's charge time back 6%",
     "Your hits push the foe's charge time back 8%",
     "Your hits push the foe's charge time back 10%"}},
  [M.JOB.knight] = {line = 'Bulwark',
    {'Parrying or blocking an attack gives you Lure\nFoes turn on you (Lure at half duration)',
     'Parrying, blocking or dodging an attack gives you Lure\nFoes turn on you (Lure at half duration)',
     'Parrying, blocking or dodging an attack gives you Lure\nFoes turn on you (Lure at 3/4 duration)',
     'Parrying, blocking or dodging gives you Lure (3/4)\nA parry or block also pushes the attacker back 10%'},
    {'A parry or block gives the most hurt ally Protect\nProtect at half its normal duration',
     'A parry or block gives the most hurt ally Protect and Shell\nBoth at half their normal duration',
     'A parry or block gives the 2 most hurt allies\nProtect and Shell (half duration)',
     'A parry or block gives the 2 most hurt allies Protect\nand Shell (half), and frees them from Immobilize'},
    {'When an ally drops to critical HP, you gain Lure\nFoes turn on you (Lure at half duration)',
     'When an ally drops to critical HP, you gain Lure\nFoes turn on you (Lure at 3/4 duration)',
     'When an ally drops to critical HP, you gain Lure (3/4)\nand Bravery (half duration)',
     'Ally at critical HP: you gain Lure (3/4) and Bravery (half)\nThat ally gains Protect (half duration)'}},
  [M.JOB.mystic] = {line = 'Ki Flow',
    {'Each counterattack heals you for 5% of your max HP',
     'Each counter heals you for 5% of your max HP\nOne hurt ally is healed for half that',
     "Each counter heals you 5% of max HP (half to an ally)\nand pushes the attacker's charge back 10%",
     "Each counter heals you 8% of max HP (half to an ally)\nand pushes the attacker's charge back 10%"},
    {'Each counterattack restores 4 MP',
     'Each counter restores 4 MP and cures your Blind',
     'Each counter restores 4 MP and cures your Blind and Sap',
     'Each counter restores 8 MP and cures your Blind and Sap'},
    {'Each counterattack cures you of one ailment',
     'Each counter cures you of one ailment\nand gives you Protect (half duration)',
     'Each counter cures you of one ailment\nand gives you Protect and Haste (half duration)',
     'Each counter cures you of two ailments\nand gives you Protect and Haste (half duration)'}},
  [M.JOB.timeMage] = {line = 'Detonate',
    {'Your hits on a Slowed foe have a 15% chance to Stop it\nStop at half its normal duration',
     'Hits on a Slowed or Immobilized foe: 15% chance to Stop it\nStop at half its normal duration',
     'Hits on a Slowed, Immobilized or Disabled foe: 15% Stop\nStop at half its normal duration',
     'Hits on a Slowed, Immobilized or Disabled foe: 25% Stop\nStop at half its normal duration'},
    {'When Detonate Stops a foe, another foe is Slowed',
     'When Detonate Stops a foe, 2 other foes are Slowed',
     'When Detonate Stops a foe, 2 other foes are Slowed\nand their charge is pushed back 10%',
     'When Detonate Stops a foe, 2 other foes are Slowed\n(Slow lasts 50% longer) and pushed back 10%'},
    {'Any party member who hits a Stopped foe gains Haste\nHaste at half duration. Works for the whole party',
     'Party hits on a Stopped foe deal +15% damage\nand give the hitter Haste (half duration)',
     'Party hits on a Stopped foe: +15% damage, Haste (half)\nand stretch the Stop a little, up to 4 times per Stop',
     'Party hits on a Stopped foe: +15% damage, Haste (3/4)\nand stretch the Stop a little, up to 4 times per Stop'}},
  [M.JOB.warrior] = {line = 'Cleave',
    {'Killing a foe gives you Bravery',
     'Killing a foe gives you Bravery\nA greatsword swing that hits a second foe does too',
     'Kills and two-foe greatsword swings give you Bravery\nBravery lasts 50% longer',
     'Kills and two-foe greatsword swings give Bravery (+50%)\nA kill while Brave cures your Slow, Disable, Immobilize'},
    {'While below 50% HP, your hits give you Bravery',
     'While below 50% HP, your hits give you Bravery\nand Protect (half duration)',
     'While below 75% HP, your hits give you Bravery\nand Protect (half duration)',
     'Below 75% HP, hits give you Bravery and Protect (half)\nand give one ally Bravery (half duration)'},
    {'While you are at critical HP, your hits inflict Sap',
     'At critical HP, your hits inflict Sap\nYour hits deal +15% damage to Sapped foes',
     'At critical HP your hits Sap, +15% damage to Sapped foes\nKilling a Sapped foe spreads Sap to another foe',
     'At critical HP your hits Sap, +15% damage to Sapped foes\nKilling a Sapped foe spreads Sap to 2 other foes'}},
  [M.JOB.hunter] = {line = 'Pursuit',
    {'Your hits deal +15% damage to a foe with an ailment\nAilments: Poison, Slow, Blind, Silence, Sap, Oil...',
     'Your hits deal +15% damage to a foe with an ailment\nEach further hit in a row on that foe adds +5% more',
     'Hits on a foe with an ailment: +15% (+5% more in a row)\nKilling that foe heals you for 10% of the damage dealt',
     'Hits on a foe with an ailment: +25% (+5% more in a row)\nKilling that foe heals you for 10% of the damage dealt'},
    {'Hits on a foe with an ailment restart one ailment timer\nTimed ailments: Disable, Immobilize, Poison, Slow',
     'Hits on a foe with an ailment restart two ailment timers\nTimed ailments: Disable, Immobilize, Poison, Slow',
     "Hits on a foe with an ailment restart two ailment timers\nand give you Libra, so you can see the foes' details",
     "Hits on a foe with an ailment restart all its timers\nand give you Libra, so you can see the foes' details"},
    {'Killing a foe with an ailment gives you Haste\nHaste at half its normal duration',
     'Killing a foe with an ailment gives you Haste (half)\nThe next foe you hit is Slowed (half duration)',
     'Killing a foe with an ailment gives you Haste (half)\nYour next target and one other foe are Slowed (half)',
     'Killing a foe with an ailment gives you Haste (3/4)\nYour next target and one other foe are Slowed (half)'}},
  [M.JOB.blackMage] = {line = 'Resonance',
    {'Casting the same element twice in a row makes\nyour next action charge 20% faster',
     'Same element twice: next action charges 20% faster\nOne spell of another element does not break the chain',
     'Same element twice: next action charges 20% faster\neven as a chain ends. One off-element spell is forgiven',
     'Same element twice: next action charges 30% faster\neven as a chain ends. One off-element spell is forgiven'},
    {'Every 3rd cast of the same element in a row\nrefunds 30% of its MP cost',
     'Every 3rd same-element cast refunds 30% of its MP\nand deals +15% damage',
     'Every 3rd same-element cast refunds 40% of its MP\nand deals +15% damage',
     'Every 3rd same-element cast refunds 60% of its MP\nand deals +15% damage'},
    {'Every 3rd same-element cast Oils the target\nOil at half its normal duration',
     'Every 3rd same-element cast Oils the target\nand one other foe (half duration)',
     'Every 3rd cast Oils the target and one other foe\nThat other foe also takes 25% of the damage',
     'Every 3rd cast Oils the target and one other foe\nThat other foe also takes 35% of the damage'}},
  [M.JOB.samurai] = {line = 'Zanshin',
    {'Landing a combo gives you Haste\nHaste at half its normal duration',
     'A combo or a critical hit gives you Haste\nHaste at half its normal duration',
     'A combo or a critical hit gives you Haste\nHaste at 3/4 of its normal duration',
     'A combo or a critical hit gives you Haste (3/4)\nand that hit deals +15% damage'},
    {'While Hasted, your attacks cannot be parried\nor blocked',
     'While Hasted, your attacks cannot be parried,\nblocked or dodged',
     'While Hasted, your attacks cannot be evaded\nand go straight through Protect',
     "While Hasted, attacks cannot be evaded, pierce Protect\nand push the foe's charge back 5%"},
    {'A kill while Hasted renews your Haste (half duration)',
     'A kill while Hasted renews your Haste\nand gives you Bravery (both at half duration)',
     'A kill while Hasted renews Haste, gives Bravery (half)\nYour next hit deals +20% damage',
     'A kill while Hasted: Haste (3/4), Bravery (half)\nand your next hit deals +20% damage'}},
  [M.JOB.chemist] = {line = 'Contagion',
    {'A status your attack lands has a 35% chance to spread\nto another foe (at 3/4 duration)',
     'A status your attack lands has a 35% chance to spread\nto another foe, at full duration',
     'A status your attack lands has a 35% chance to spread\nOil and Poison spread more easily: 50%',
     'A status your attack lands has a 50% chance to spread\nOil and Poison spread more easily: 65%'},
    {'Your hits heal the most hurt ally\nfor 10% of the damage dealt',
     'Your hits heal the most hurt ally\nfor 20% of the damage dealt',
     'Your hits heal the most hurt ally for 20% of the damage\nand give that ally Protect (half duration)',
     'Your hits heal the 2 most hurt allies for 20% of damage\nand give them Protect (half duration)'},
    {'Contagion can spread to 2 foes',
     'Contagion can spread to 2 foes\nEach foe it spreads to takes 10% of the damage',
     "Contagion spreads to 2 foes, dealing 10% of the damage\nA spread gives you Libra to see the foes' details",
     "Contagion spreads to 3 foes, dealing 10% of the damage\nA spread gives you Libra to see the foes' details"}},
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

-- Montblanc's lines for newly rewarded ranks (from+1 .. to). Returns a list of dialogue boxes.
local WRIT = {"Apprentice's Writ", "Journeyman's Writ", "Master's Writ"}
local function gold(text) return '{color:gold}' .. text .. '{color:none}' end
-- Montblanc's Proficiency topic (asked for in the Clan hall; the runtime shows these through the dialog plugin).
M.TOPIC_PAGES = {
  'Your current job has three ' .. gold('Proficiencies') .. ',\nkupo! Each grows through four levels as\nyou earn higher Clan ranks.',
  'Proficiency I appears on your weapon,\nII on your armor, and III on your helmet.\nThe passives follow your job, not the item.',
  gold("Apprentice's Writ") .. ' - ranks 1 to 4.\n' .. gold("Journeyman's Writ") .. ' - ranks 5 to 8.\n' .. gold("Master's Writ") .. ' - ranks 9 to 12, kupo!',
  'When your Clan rank rises, come and ask\nme if you have earned anything new.\nI hand out nothing unasked, kupo!',
}
M.REMINDER = 'Kupo! Before you go: your Clan rank has\nrisen. Ask me about ' .. gold('Proficiency') .. '!'
function M.speech(from, to)
  if not int(from, 0, M.MAX_RANK) or not int(to, 0, M.MAX_RANK) or to <= from then return {} end
  local boxes = {}
  if to - from > 1 then
    boxes[#boxes + 1] = 'Kupo! I was filing your training papers and\nfound these waiting for you. You\'ve earned\nevery one, kupo!'
  end
  local stage = (to - 1) // 4 + 1
  local level = to - 4 * (stage - 1)
  local roman = ({'I', 'II', 'III'})[stage]
  if from < 1 and to >= 1 then
    boxes[#boxes + 1] = 'Kupo! You asked at the right time! Clan\nCenturio trains every member in their job.\nTake this ' .. gold(WRIT[1]) .. ', kupo!'
    boxes[#boxes + 1] = 'Whatever job you take up, the Writ unlocks its\n' .. gold('Proficiency') .. ': your weapons and armour fight\nthe way that job does, whatever you wield!'
    boxes[#boxes + 1] = 'Rise through the ranks and I\'ll stamp it for\nyou each time. A Writ full of stamps is a\nhunter\'s pride, kupo!'
  end
  if from < 5 and to >= 5 then
    boxes[#boxes + 1] = 'Kupo-po! That apprentice\'s paper can\'t hold\nanother stamp! Here, a ' .. gold(WRIT[2]) .. '.'
    boxes[#boxes + 1] = 'Every job you know now opens its second\n' .. gold('Proficiency') .. '. Go show those marks what a\nCenturio journeyman can do, kupo!'
  end
  if from < 9 and to >= 9 then
    boxes[#boxes + 1] = 'Kupo... You\'ve come so far. I had this made\nfor very few hunters: a ' .. gold(WRIT[3]) .. '.'
    boxes[#boxes + 1] = 'Your third ' .. gold('Proficiency') .. ' is open, kupo. The\nclan has nothing left to teach you, only\nstamps left to give!'
  end
  if to == M.MAX_RANK then
    boxes[#boxes + 1] = 'And the final stamp on your ' .. gold(WRIT[3]) .. '!\nEvery ' .. gold('Proficiency') .. ' at its peak, kupo. Even\nI\'m a little jealous!'
  elseif level > 1 then
    boxes[#boxes + 1] = 'Kupo! Your ' .. gold(WRIT[stage]) .. ' gets a fresh\nstamp! ' .. gold('Proficiency ' .. roman .. ' ' .. level .. '/4') .. ' grows stronger\nfor every job you take up!'
  end
  return boxes
end

-- A Writ handed over late: its rank was rewarded before the Writs became key items, or the item went missing.
function M.writSpeech(stage)
  if not int(stage, 1, 3) then return {} end
  return {'Kupo! Wait, I almost forgot your\n' .. gold(WRIT[stage]) .. '! Your ' .. gold('Proficiency') ..
          ' only\nanswers to a hunter who carries it, kupo!'}
end
-- Whether M.speech(from, to) already hands over Writ k.
function M.speechGives(from, to, stage)
  return int(from, 0, M.MAX_RANK) and int(to, 0, M.MAX_RANK) and int(stage, 1, 3)
    and from < M.STAGE_START[stage] and to >= M.STAGE_START[stage]
end

return M
