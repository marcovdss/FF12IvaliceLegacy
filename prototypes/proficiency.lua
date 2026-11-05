-- Proficiency: each job's Clan line, earned from Montblanc one Clan rank at a time and applied to whatever the
-- character wields. 12 ranks = 3 stages x 4 levels: ranks 1-4 raise stage I, 5-8 stage II, 9-12 stage III.
-- The line is chosen by the character's CURRENT job. This module only decides; the Forge adapters apply the result
-- through Forge's own end-of-hit step (statuses, durations, HP/MP), the charge time, or helpers on other units.
-- Every level is built from operations already seen working in play; where a designed effect needed an engine detail
-- nobody has proven, the level does the nearest thing those operations allow (reports/LEGACY-PROFICIENCY-040.md).
local M = {}
M.version = '0.4.1'

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
M.FAMILY_OWNER = {[1] = M.JOB.knight, [2] = M.JOB.warrior, [3] = M.JOB.samurai, [4] = M.JOB.samurai, [5] = M.JOB.lancer,
                  [6] = M.JOB.mystic, [7] = M.JOB.hunter, [8] = M.JOB.chemist, [9] = M.JOB.gunner, [10] = M.JOB.warrior,
                  [11] = M.JOB.warrior, [12] = M.JOB.hunter, [13] = M.JOB.whiteMage, [14] = M.JOB.blackMage,
                  [15] = M.JOB.greenMage, [16] = M.JOB.timeMage, [17] = M.JOB.timeMage}
M.LEGENDARY = {[4130] = true, [4266] = true, [4267] = true, [4268] = true, [4270] = true, [4272] = true, [4288] = true,
               [4290] = true, [4291] = true, [4292] = true, [4293] = true, [4294] = true, [4295] = true}
M.DISEASE = 16
M.CAST_WINDOW = 200000   -- clock units (microseconds): one spell reaches all its targets within this
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
local function slot(actor) mem[actor] = mem[actor] or {}; return mem[actor] end

-- Action helpers. a.targetAdd/casterAdd = {[statusId] = durationScale}; a.others = {{op, unit, ...}}.
local function add(t, field, id, scale) t[field] = t[field] or {}; t[field][id] = scale or 1 end
local function push(t, field, v) t[field] = t[field] or {}; t[field][#t[field] + 1] = v end
local function other(t, op) push(t, 'others', op) end

-- s (all events): job, actorId, L = {I, II, III}, party = {{address, job, hpPct, mp, maxMp, status}},
-- foes = {{address, status}} (other engaged foes, alive), casterStatus, casterHpPct, roll(percent), self = address.
-- 'hit' adds: targetId, foe, kill, bystander, category, damage, targetStatus, targetMp, charge, target (address).
local rules = {}

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
    if L[1] >= 3 and s.kill and int(s.damage, 1, 99999) then a.healHp = math.max(1, s.damage // 10) end
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
  if m.slowNext and L[3] >= 2 and not s.targetStatus.slow then add(a, 'targetAdd', M.STATUS.slow, 0.5); m.slowNext = nil end
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
  if L[2] >= 1 and low and not brave then
    add(a, 'casterAdd', M.STATUS.bravery)
    if L[2] >= 2 then add(a, 'casterAdd', M.STATUS.protect, 0.5) end
    if L[2] >= 4 then
      for _, p in ipairs(s.party) do
        if p.address ~= s.self and not p.status.bravery then other(a, {op = 'add', unit = p.address, id = M.STATUS.bravery, scale = 0.5}) break end
      end
    end
  end
  if L[3] >= 1 and s.casterStatus.hpCritical and not s.targetStatus.sap then add(a, 'targetAdd', M.STATUS.sap, L[3] >= 4 and 1.5 or 1) end
  if L[3] >= 2 and s.targetStatus.sap then a.damage = (a.damage or 1) * 1.15 end
  if L[3] >= 3 and s.kill and s.targetStatus.sap and s.foes[1] then
    other(a, {op = 'add', unit = s.foes[1].address, id = M.STATUS.sap, scale = L[3] >= 4 and 1.5 or 1})
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
      add(a, 'casterAdd', M.STATUS.haste, 0.5)
      if L[3] >= 2 then add(a, 'casterAdd', M.STATUS.bravery, 0.5) end
      if L[3] >= 3 then m.boost = true end
      if L[3] >= 4 then m.carry = true end
    end
    if s.foe and L[2] >= 3 and s.casterStatus.haste and s.targetStatus.protect then a.damage = (a.damage or 1) / 0.75 end
    if s.foe and L[2] >= 4 and s.casterStatus.haste and s.charge then
      local c = s.charge
      if int(c.current, 1, 0x7FFFFFFF) and int(c.required, 1, 0x7FFFFFFF) and c.current < c.required and c.last == c.current then
        a.chargeTo = math.max(1, c.current - c.required // 20)
      end
    end
    if L[1] < 1 then return end
    if m.target == s.targetId or m.carry then m.count = (m.count or 0) + 1; m.carry = nil
    elseif L[1] >= 3 and m.target and not m.switched then m.count, m.switched = (m.count or 0) + 1, true
    else m.count, m.switched = 1, nil end
    m.target = s.targetId
    if m.count >= (L[1] >= 2 and 2 or 3) then
      m.count, m.switched = 0, nil
      -- Haste cancels Slow by itself (status table), and misses never break the chain: only changing target does.
      add(a, 'casterAdd', M.STATUS.haste, L[1] >= 4 and 0.75 or 0.5)
    end
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
      if L[2] >= 4 then
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
    if L[2] >= 1 then
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

-- Time Mage III works for the whole party: any member hitting a Stopped foe.
local function timeMageAura(s, a)
  local best = 0
  for _, p in ipairs(s.party) do if p.job == M.JOB.timeMage then best = math.max(best, s.L[3]) end end
  if best < 1 or not s.foe then return end
  if not s.targetStatus.stop then
    if mem.stopHits then mem.stopHits[s.targetId] = nil end
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

rules[M.JOB.greenMage] = {hit = function(s, a)
  if not s.foe or not int(s.targetMp, 1, 99999) or not int(s.damage, 1, 99999) then return end
  local L = s.L
  if L[1] < 1 then return end
  local drain = math.min(math.max(1, (s.damage * (L[1] >= 4 and 8 or 5)) // 100), s.targetMp)
  a.drainMp = drain
  if L[1] >= 2 and drain == s.targetMp and not s.targetStatus.silence then add(a, 'targetAdd', M.STATUS.silence) end
  if L[1] >= 3 then a.healHp = drain end
  if L[2] >= 1 and s.casterMp and s.casterMaxMp and s.casterMp >= s.casterMaxMp then
    a.drainMp = nil
    a.targetMpLoss = drain
    local given = 0
    local sorted = {}
    for _, p in ipairs(s.party) do if p.address ~= s.self and p.maxMp > 0 then sorted[#sorted + 1] = p end end
    table.sort(sorted, function(x, y) return x.mp / x.maxMp < y.mp / y.maxMp end)
    for i = 1, (L[2] >= 3 and 2 or 1) do
      local p = sorted[i]
      if p then
        other(a, {op = 'mp', unit = p.address, amount = L[2] >= 4 and drain + drain // 2 or drain})
        if L[2] >= 2 and p.status.silence then other(a, {op = 'remove', unit = p.address, id = M.STATUS.silence}) end
        given = given + 1
      end
    end
  end
  if L[3] >= 1 and s.charge then
    local c = s.charge
    if int(c.current, 1, 0x7FFFFFFF) and int(c.required, 1, 0x7FFFFFFF) and c.current < c.required and c.last == c.current then
      a.chargeTo = math.max(1, c.current - math.floor(c.required * (0.04 + 0.02 * (L[3] - 1))))
    end
  end
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
  add(a, 'targetAdd', M.STATUS.regen, regen)  -- Regen cures Sap by itself (status table)
  if L[1] >= 3 then other(a, {op = 'mp', unit = s.self, amount = math.max(1, s.overflow // 20)}) end
  local reach = L[2] >= 3 and 99 or (L[2] >= 2 and 2 or (L[2] >= 1 and 1 or 0))
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
  local first = not (m.castAt and s.now and s.now - m.castAt < M.CAST_WINDOW and m.castElement == s.element)
  if first then
    local ended = false
    if m.element == s.element then m.chain, m.strayed = (m.chain or 1) + 1, nil
    elseif L[1] >= 2 and m.element and not m.strayed then m.strayed = true
    else
      ended = (m.chain or 0) >= 2
      m.element, m.chain, m.strayed = s.element, 1, nil
    end
    m.castAt, m.castElement = s.now, s.element
    m.full = m.element == s.element and m.chain % 3 == 0
    if L[1] >= 1 and ((m.element == s.element and m.chain >= 2) or (L[1] >= 3 and ended)) then
      a.casterCharge = L[1] >= 4 and 0.30 or 0.20
    end
  end
  if L[2] >= 1 and m.full then
    if first then
      local refund = math.max(1, (s.mpCost or 0) * (L[2] >= 4 and 50 or 30) // 100)
      if L[2] >= 3 then refund = refund + math.max(1, (s.mpCost or 0) // 10) end
      other(a, {op = 'mp', unit = s.self, amount = refund})
    end
    if L[2] >= 2 then a.damage = (a.damage or 1) * 1.15 end
    if L[3] >= 1 then
      local oil = L[3] >= 4 and 0.75 or 0.5
      if not s.targetStatus.oil then add(a, 'targetAdd', M.STATUS.oil, oil) end
      local near = s.foes[1]
      if near and first then
        if L[3] >= 2 and not near.status.oil then other(a, {op = 'add', unit = near.address, id = M.STATUS.oil, scale = oil}) end
        if L[3] >= 3 and int(s.damage, 1, 99999) then other(a, {op = 'hp', unit = near.address, amount = -math.max(1, s.damage // 4)}) end
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
      other(a, {op = 'hp', unit = p.address, amount = math.max(1, s.damage * (L[2] >= 2 and 20 or 10) // 100)})
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
      if s.roll(easy and chance + 15 or chance) then
        other(a, {op = 'add', unit = f.address, id = id, scale = L[1] >= 2 and 1 or 0.75})
        if L[3] >= 2 then other(a, {op = 'hp', unit = f.address, amount = -math.max(1, (s.damage or 0) // 10)}) end
        if L[3] >= 3 and not s.casterStatus.libra then add(a, 'casterAdd', M.STATUS.libra) end
      end
    end
  end
end}

-- Decide one event for one party member. Returns an action table or nil.
function M.decide(event, s, claimedRank)
  if type(s) ~= 'table' or not int(s.job, 0, 11) then return nil end
  s.L = M.levels(claimedRank)
  if s.L[1] < 1 then return nil end
  s.party, s.foes = s.party or {}, s.foes or {}
  s.casterStatus, s.targetStatus = s.casterStatus or {}, s.targetStatus or {}
  s.casterHpPct = s.casterHpPct or 100
  s.roll = s.roll or function() return false end
  local r = rules[s.job]
  local function pass()
    local a = {}
    if r and r[event] then r[event](s, a) end
    if event == 'hit' then
      timeMageAura(s, a)
      if s.L[3] >= 4 and s.foe and M.LEGENDARY[s.weapon] and M.FAMILY_OWNER[s.category] == s.job then
        a.damage = (a.damage or 1) * M.MASTER_DAMAGE
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
    mem[s.actorId], s.kill = saved, true
    a = pass()
  end
  if next(a) == nil then return nil end
  return a
end

-- What each job's Proficiency does, for the gear descriptions: line name, then for each stage the sentence at
-- levels 1-4 (what the rules above do at that level).
M.GEAR = {
  [M.JOB.whiteMage] = {line = 'Overflow',
    {'Healing past full HP gives the target Regen', 'Healing or potions past full HP give the target Regen',
     'Overflow (potions too): Regen, 5% of the overflow back as MP', 'Overflow (potions too): Regen (+50%), 5% of the overflow as MP'},
    {'Overflow Regen also reaches 1 more ally', 'Overflow Regen also reaches 2 more allies',
     'Overflow Regen reaches the whole party', 'Overflow Regen reaches the whole party, +25% duration'},
    {'Overflow also gives Shell (half duration)', 'Overflow also gives Shell and Protect (half duration)',
     'Overflow also gives Shell, Protect and Faith (half duration)', 'Overflow gives Shell (3/4), Protect and Faith (half duration)'}},
  [M.JOB.lancer] = {line = 'Grounding',
    {'Hits remove Float and Haste', 'Hits remove Float, Haste and Faith', 'Hits remove Float, Haste, Faith and Regen',
     'Hits remove Float, Haste, Faith, Regen and Bravery'},
    {'Hits on foes with an ailment strip Protect or Shell', 'Hits on foes with an ailment strip Protect and Shell',
     'Hits on foes with an ailment strip Protect, Shell, Reflect', 'Ailing foes lose Protect, Shell, Reflect, Bubble and Berserk'},
    {'A foe left with no buffs is Immobilized (half duration)', 'A foe left with no buffs is Immobilized (3/4 duration)',
     'Unbuffed foes Immobilized (3/4), +15% damage to Immobilized', 'Unbuffed foes Immobilized (3/4), +15% to them, and Slowed'}},
  [M.JOB.gunner] = {line = 'Suppression',
    {"Hits push the target's charge back 10%", "Hits push charge back 10%, Slowed foes 15%",
     'Push charge 10% (Slowed 15%), another foe 5%', 'Push charge 15% (Slowed 20%), another foe 5%'},
    {'Foes about to act are pushed back 10% more', 'Foes about to act: +10% push, 15% chance of Silence (half)',
     'Foes about to act: +10% push, 25% chance of Silence (half)', 'About to act: +10% push, 25% Silence, which gives an ally Faith'},
    {'3 hits in a row on one foe: +5% more push', '2 hits in a row on one foe: +5% more push',
     '2 hits in a row: +5% push and Slow (half duration)', '2-hit chains: +5% push, Slow, the chain survives a kill'}},
  [M.JOB.greenMage] = {line = 'Mana Leech',
    {'Hits drain MP equal to 5% of the damage', 'Drain 5% of damage as MP, a foe drained to 0 MP is Silenced',
     'Drain 5% as MP, healing as much HP, 0 MP foes are Silenced', 'Drain 8% as MP, healing as much HP, 0 MP foes are Silenced'},
    {'At full MP, drained MP goes to the ally lowest on MP', 'Full MP: drain goes to the lowest-MP ally, cures their Silence',
     'Full MP: drain goes to 2 low-MP allies, cures Silence', 'Full MP: 150% of the drain goes to 2 allies, cures Silence'},
    {"Hits push the target's charge back 4%", "Hits push the target's charge back 6%",
     "Hits push the target's charge back 8%", "Hits push the target's charge back 10%"}},
  [M.JOB.knight] = {line = 'Bulwark',
    {'A parry or block Lures the attacker (half duration)', 'A parry, block or dodge Lures the attacker (half duration)',
     'A parry, block or dodge Lures the attacker (3/4 duration)', 'Parry, block or dodge Lures (3/4), parry or block pushes charge'},
    {'Parry or block: Protect on the lowest-HP ally (half)', 'Parry or block: Protect and Shell on the lowest-HP ally (half)',
     'Parry or block: Protect and Shell on the 2 lowest-HP allies', 'Parry or block: Protect, Shell, frees 2 allies from Immobilize'},
    {'An ally at critical HP: the Knight Lures foes (half duration)', 'An ally at critical HP: the Knight Lures foes (3/4 duration)',
     'Ally at critical HP: the Knight Lures foes, gains Bravery (half)', 'Ally critical: Knight Lures, gains Bravery, Protects that ally'}},
  [M.JOB.mystic] = {line = 'Ki Flow',
    {'Each counter heals the Mystic 5% of max HP', 'Counters heal 5% of max HP, and half that to a hurt ally',
     'Counters heal 5% (half to an ally), push the attacker back 10%', 'Counters heal 8% (half to an ally), push the attacker back 10%'},
    {'Each counter restores 4 MP', 'Counters restore 4 MP and cure Blind', 'Counters restore 4 MP and cure Blind and Sap',
     'Counters restore 8 MP and cure Blind and Sap'},
    {'Counters cure 1 ailment on the Mystic', 'Counters cure 1 ailment, give Protect (half duration)',
     'Counters cure 1 ailment, give Protect and Haste (half)', 'Counters cure 2 ailments, give Protect and Haste (half)'}},
  [M.JOB.timeMage] = {line = 'Detonate',
    {'Hits have 15% to Stop a Slowed foe (half duration)', 'Hits: 15% to Stop a Slowed or Immobilized foe (half)',
     'Hits: 15% to Stop a Slowed, Immobilized or Disabled foe', 'Hits: 25% to Stop a Slowed, Immobilized or Disabled foe'},
    {'A Detonate Stop also Slows another foe', 'A Detonate Stop also Slows 2 other foes',
     'Detonate Stop: Slow 2 other foes and push their charge 10%', 'Detonate Stop: Slow (+50%) 2 other foes, push their charge 10%'},
    {'Party hits on a Stopped foe give the hitter Haste (half)', 'Party hits on Stopped foes: Haste (half), +15% damage',
     'Party hits on Stopped foes: Haste, +15%, each hit extends Stop', 'Party hits on Stopped: Haste (3/4), +15%, each hit extends Stop'}},
  [M.JOB.warrior] = {line = 'Cleave',
    {'A kill grants Bravery', 'A kill or a greatsword sweep grants Bravery',
     'A kill or a greatsword sweep grants Bravery (+50% duration)', 'Kill or sweep: Bravery (+50%), kills under it cure Slow/Disable'},
    {'Below 50% HP, hits grant Bravery', 'Below 50% HP, hits grant Bravery and Protect (half)',
     'Below 75% HP, hits grant Bravery and Protect (half)', 'Below 75% HP: Bravery, Protect (half), and an ally Bravery (half)'},
    {'Hits at critical HP inflict Sap', 'Critical-HP hits inflict Sap, +15% damage to Sapped foes',
     'Critical-HP Sap, +15% vs Sapped, a kill spreads Sap', 'Critical-HP Sap (+50% duration), +15% vs Sapped, spreads'}},
  [M.JOB.hunter] = {line = 'Pursuit',
    {'+15% damage to foes with an ailment', '+15% to foes with an ailment, +5% more on the next hit in a row',
     '+15% (+5% in a row), killing that foe heals 10% of the damage', '+25% (+5% in a row), killing that foe heals 10% of the damage'},
    {'Hits on foes with an ailment renew one timed ailment', 'Hits on foes with an ailment renew two timed ailments',
     'Hits renew two timed ailments and reveal foes (Libra)', 'Hits renew every timed ailment and reveal foes (Libra)'},
    {'Killing a foe with an ailment grants Haste (half duration)', 'That kill: Haste (half), the next foe hit is Slowed (half)',
     'That kill: Haste (half), next foe hit and another foe Slowed', 'That kill: Haste (3/4), next foe hit and another foe Slowed'}},
  [M.JOB.blackMage] = {line = 'Resonance',
    {'Same element twice in a row: the next action charges 20% faster', 'Same element twice: next action +20%, 1 other spell keeps chain',
     'Same element twice: next action +20%, also when the chain ends', 'Same element twice: next action +30%, also when the chain ends'},
    {'Every 3rd same-element cast refunds 30% of its MP', 'Every 3rd same-element cast: refunds 30% MP, +15% damage',
     'Every 3rd same-element cast: refunds 40% MP, +15% damage', 'Every 3rd same-element cast: refunds 60% MP, +15% damage'},
    {'Every 3rd same-element cast Oils the target (half duration)', 'Every 3rd same-element cast Oils the target and another foe',
     'Every 3rd cast: Oil on both, the other foe takes 25% of the damage', 'Every 3rd: Oil on both (3/4), other foe takes 25% of the damage'}},
  [M.JOB.samurai] = {line = 'Zanshin',
    {'3 hits in a row on one foe grant Haste (half duration)', '2 hits in a row on one foe grant Haste (half duration)',
     '2 hits in a row: Haste (half), the chain survives one switch', '2 hits in a row: Haste (3/4), the chain survives one switch'},
    {'During Haste, attacks ignore weapon and shield evasion', 'During Haste, attacks ignore evasion and parry',
     'During Haste, attacks ignore evasion, parry and Protect', 'During Haste: ignore evasion, parry, Protect, push charge 5%'},
    {'A kill during Haste renews Haste (half duration)', 'A kill during Haste renews Haste and gives Bravery (half)',
     'Kill in Haste: Haste, Bravery, the next hit deals +20%', 'Kill in Haste: Haste, Bravery, next hit +20%, the chain carries'}},
  [M.JOB.chemist] = {line = 'Contagion',
    {'Statuses you land: 35% to spread to another foe (3/4)', 'Statuses you land: 35% to spread to another foe',
     'Landed statuses: 35% to spread (Oil and Poison 50%)', 'Landed statuses: 50% to spread (Oil and Poison 65%)'},
    {'Hits heal the most hurt ally for 10% of the damage', 'Hits heal the most hurt ally for 20% of the damage',
     'Hits heal the most hurt ally 20% of the damage, add Protect', 'Hits heal the 2 most hurt allies 20% of the damage, add Protect'},
    {'Spreads reach 2 other foes', 'Spreads reach 2 foes and deal 10% of the damage',
     'Spreads reach 2 foes, deal 10% of the damage, reveal (Libra)', 'Spreads reach 3 foes, deal 10% of the damage, reveal (Libra)'}},
}
M.GEAR_LIMIT = 66
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
    boxes[#boxes + 1] = 'Kupo! Before you go! Every member of Clan\nCenturio trains in the way of their own job.\nTake this ' .. gold(WRIT[1]) .. ', kupo!'
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
