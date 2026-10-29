-- Proficiency: each job's Clan line, earned from Montblanc one Clan rank at a time and applied to whatever the
-- character wields. 12 ranks = 3 stages x 4 levels: ranks 1-4 raise stage I, 5-8 stage II, 9-12 stage III.
-- The line is chosen by the character's CURRENT job. This module only decides; the Forge adapters apply the result
-- through Forge's own end-of-hit step (statuses, durations, HP/MP), the charge time, or helpers on other units.
-- Levels whose mechanism isn't proven yet return nothing (listed in M.PENDING).
local M = {}
M.version = '0.2.0'

M.NO_JOB = 255
M.JOB = {whiteMage = 0, lancer = 1, gunner = 2, greenMage = 3, knight = 4, mystic = 5, timeMage = 6, warrior = 7,
         hunter = 8, blackMage = 9, samurai = 10, chemist = 11}
M.STATUS = {ko = 0, stop = 3, blind = 7, poison = 8, silence = 9, sap = 10, oil = 11, disable = 13,
            immobilize = 14, slow = 15, lure = 17, protect = 18, shell = 19, haste = 20, bravery = 21, faith = 22,
            reflect = 23, invisible = 24, regen = 25, float = 26, bubble = 28, hpCritical = 29, libra = 30}
M.HINDERED = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil', 'stop'}
M.BUFFS = {'protect', 'shell', 'haste', 'bravery', 'faith', 'reflect', 'regen', 'float', 'bubble', 'invisible'}
M.AILMENTS = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil'}
M.GREATSWORD = 2
M.MAX_RANK = 12
-- Levels that wait on an unproven mechanism (no effect yet).
M.PENDING = {
  'Knight I-4, II-4, III-2, III-4', 'Lancer II (all: needs break detection), III-4', 'Warrior II-2',
  'Samurai I-3, III-3', 'Hunter I-2, I-3, II-1, II-2, II-4, III-3', 'Gunner I-2, II (all: spell interrupt)',
  'Chemist II (all: item sharing)', 'Mystic II-3', 'White Mage I-2', 'Black Mage III (all: forced weakness)',
}

local function int(v, lo, hi) return type(v) == 'number' and v % 1 == 0 and v >= lo and v <= hi end
local function has(set, list) for _, n in ipairs(list) do if set[n] then return n end end end

function M.levels(claimedRank)
  if not int(claimedRank, 0, M.MAX_RANK) then return {0, 0, 0} end
  local out = {}
  for i = 1, 3 do out[i] = math.max(0, math.min(4, claimedRank - 4 * (i - 1))) end
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
  if prey and L[1] >= 1 then a.damage = (a.damage or 1) * (L[1] >= 4 and 1.25 or 1.15) end
  if L[2] >= 3 and prey and not s.targetStatus.libra then add(a, 'targetAdd', M.STATUS.libra) end
  local m = slot(s.actorId)
  if m.slowNext and L[3] >= 2 and not s.targetStatus.slow then add(a, 'targetAdd', M.STATUS.slow, 0.5); m.slowNext = nil end
  if L[3] >= 1 and prey and s.kill then
    add(a, 'casterAdd', M.STATUS.haste, (L[3] >= 4 and 1.5 or 1) * 0.5)
    m.slowNext = true
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
  for i, name in ipairs({'float', 'haste', 'faith', 'regen'}) do
    if L[1] >= i and s.targetStatus[name] then strip[#strip + 1] = name end
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
end}

rules[M.JOB.samurai] = {
  hit = function(s, a)
    local L, m = s.L, slot(s.actorId)
    if s.foe and L[3] >= 1 and s.kill and s.casterStatus.haste then
      add(a, 'casterAdd', M.STATUS.haste, 0.5)
      if L[3] >= 2 then add(a, 'casterAdd', M.STATUS.bravery, 0.5) end
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
    else m.target, m.count = s.targetId, 1 end
    m.target = s.targetId
    if m.count >= (L[1] >= 2 and 2 or 3) then
      m.count = 0
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
        if f and not f.status.slow then
          other(a, {op = 'add', unit = f.address, id = M.STATUS.slow, scale = L[2] >= 4 and 1.5 or 1})
          if L[2] >= 3 and f.status.haste then other(a, {op = 'remove', unit = f.address, id = M.STATUS.haste}) end
        end
      end
    end
  end
end}

-- Time Mage III works for the whole party: any member hitting a Stopped foe.
local function timeMageAura(s, a)
  local best = 0
  for _, p in ipairs(s.party) do if p.job == M.JOB.timeMage then best = math.max(best, s.L[3]) end end
  if best < 1 or not s.foe or not s.targetStatus.stop then return end
  if not s.casterStatus.haste then add(a, 'casterAdd', M.STATUS.haste, (best >= 4 and 0.75 or 0.5)) end
  if best >= 2 then a.damage = (a.damage or 1) * 1.15 end
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
    if L[2] >= 1 and (s.outcome == 'parry' or s.outcome == 'block') then
      local sorted = {}
      for _, p in ipairs(s.party) do if p.address ~= s.self then sorted[#sorted + 1] = p end end
      table.sort(sorted, function(x, y) return x.hpPct < y.hpPct end)
      for i = 1, (L[2] >= 3 and 2 or 1) do
        local p = sorted[i]
        if p then
          other(a, {op = 'add', unit = p.address, id = M.STATUS.protect, scale = 0.5})
          if L[2] >= 2 then other(a, {op = 'add', unit = p.address, id = M.STATUS.shell, scale = 0.5}) end
        end
      end
    end
  end,
  ally = function(s, a)  -- s.self is the Knight; an ally just dropped to critical HP
    if s.L[3] < 1 then return end
    other(a, {op = 'add', unit = s.self, id = M.STATUS.lure, scale = 0.5})
    if s.L[3] >= 3 then other(a, {op = 'add', unit = s.self, id = M.STATUS.bravery, scale = 0.5}) end
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
  if L[1] < 1 then return end
  local regen = L[1] >= 4 and 1.5 or 1
  add(a, 'targetAdd', M.STATUS.regen, regen)
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
  if m.element == s.element then m.chain = (m.chain or 1) + 1 else m.element, m.chain = s.element, 1 end
  if L[1] >= 1 and m.chain >= 2 then a.casterCharge = L[1] >= 4 and 0.30 or 0.20 end
  if L[2] >= 1 and m.chain == 3 then
    local refund = math.max(1, (s.mpCost or 0) * (L[2] >= 4 and 50 or 30) // 100)
    if L[2] >= 3 then refund = refund + math.max(1, (s.mpCost or 0) // 10) end
    other(a, {op = 'mp', unit = s.self, amount = refund})
    if L[2] >= 2 then a.damage = (a.damage or 1) * 1.15 end
  end
end}

-- Chemist: statuses its attacks land (Forge 182, s.landed = list of status ids being added to the target).
rules[M.JOB.chemist] = {status = function(s, a)
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
        if L[3] >= 3 then other(a, {op = 'add', unit = f.address, id = M.STATUS.libra}) end
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
  local a = {}
  local r = rules[s.job]
  if r and r[event] then r[event](s, a) end
  if event == 'hit' then timeMageAura(s, a) end
  if next(a) == nil then return nil end
  return a
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

return M
