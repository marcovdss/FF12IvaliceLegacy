-- Proficiency: each job's Clan line, earned from Montblanc one Clan rank at a time and applied to whatever the
-- character wields. 12 ranks = 3 stages x 4 levels: ranks 1-4 raise stage I, 5-8 stage II, 9-12 stage III.
-- The line is chosen by the character's CURRENT job. This module only decides; the Forge adapter applies the result
-- through Forge's own end-of-hit step (statuses, durations, HP/MP) or the target's charge time.
local M = {}
M.version = '0.1.0'

M.NO_JOB = 255
-- Native job slots (BaseJobs names).
M.JOB = {whiteMage = 0, lancer = 1, gunner = 2, greenMage = 3, knight = 4, mystic = 5, timeMage = 6, warrior = 7,
         hunter = 8, blackMage = 9, samurai = 10, chemist = 11}
M.STATUS = {stop = 3, immobilize = 14, disable = 13, slow = 15, haste = 20, bravery = 21, faith = 22, regen = 25,
            float = 26, silence = 9}
-- Ailments that count as "hindered" for Pursuit (Sleep already scales physical damage natively).
M.HINDERED = {'blind', 'poison', 'silence', 'slow', 'sap', 'disease', 'immobilize', 'disable', 'oil', 'stop'}
M.GREATSWORD = 2
M.MAX_RANK = 12

local function int(v, lo, hi) return type(v) == 'number' and v % 1 == 0 and v >= lo and v <= hi end

-- Levels 0-4 of stages I, II, III for a claimed Clan rank.
function M.levels(claimedRank)
  if not int(claimedRank, 0, M.MAX_RANK) then return {0, 0, 0} end
  local out = {}
  for i = 1, 3 do out[i] = math.max(0, math.min(4, claimedRank - 4 * (i - 1))) end
  return out
end

-- Stage I lines built in 0.1.0. Each returns an action table or nil.
-- s: job, level (stage I level 1-4), actorId, targetId, foe (bool), kill (bool), bystander (bool), category,
--    damage (pending HP damage), targetStatus / casterStatus (name -> true), targetMp, charge {current, required,
--    last} or nil, roll (function(percent) -> bool).
local lines = {}

-- Hunter, Pursuit I: more damage to a target hindered by an ailment. L1-3 +15%, L4 +25%.
-- (L2 stat breaks and L3 no-flee wait on their mechanism.)
lines[M.JOB.hunter] = function(s)
  if not s.foe then return nil end
  for _, name in ipairs(M.HINDERED) do
    if s.targetStatus[name] then return {damage = s.level >= 4 and 1.25 or 1.15, why = 'pursuit:' .. name} end
  end
end

-- Warrior, Cleave I: a kill grants Bravery; L2 a sweep that also hits a bystander grants it; L3 longer Bravery;
-- L4 a kill while under Bravery shakes off Slow, Disable and Immobilize.
lines[M.JOB.warrior] = function(s)
  if not s.foe then return nil end
  local grant = s.kill or (s.level >= 2 and s.bystander and s.category == M.GREATSWORD)
  if not grant then return nil end
  local a = {casterAdd = {[M.STATUS.bravery] = s.level >= 3 and 1.5 or 1}, why = 'cleave'}
  if s.level >= 4 and s.kill and s.casterStatus.bravery then
    a.casterRemove = {M.STATUS.slow, M.STATUS.disable, M.STATUS.immobilize}
  end
  return a
end

-- Lancer, Grounding I: hits strip Float; L2 also Haste; L3 also Faith; L4 also Regen.
lines[M.JOB.lancer] = function(s)
  if not s.foe then return nil end
  local strip = {}
  for i, name in ipairs({'float', 'haste', 'faith', 'regen'}) do
    if s.level >= i and s.targetStatus[name] then strip[#strip + 1] = M.STATUS[name] end
  end
  if #strip == 0 then return nil end
  return {targetRemove = strip, why = 'grounding'}
end

-- Samurai, Zanshin I: three hits in a row on one target grant a brief Haste; L2 two hits; L4 longer Haste.
-- (L3 "survives a miss" waits: a miss never reaches this hook.)
local chains = {}
lines[M.JOB.samurai] = function(s)
  local chain = chains[s.actorId]
  if chain and chain.target == s.targetId then chain.count = chain.count + 1
  else chain = {target = s.targetId, count = 1}; chains[s.actorId] = chain end
  local need = s.level >= 2 and 2 or 3
  if chain.count < need then return nil end
  chain.count = 0
  return {casterAdd = {[M.STATUS.haste] = s.level >= 4 and 1.5 or 1}, why = 'zanshin'}
end

-- Gunner, Suppression I: hits push back the target's charge time (a foe charging its next action).
-- L1-3 a tenth of its required charge, L4 three twentieths. (L2 bosses, L3 a second foe wait.)
lines[M.JOB.gunner] = function(s)
  local c = s.charge
  if not s.foe or not c or not int(c.current, 1, 0x7FFFFFFF) or not int(c.required, 1, 0x7FFFFFFF) then return nil end
  if c.current >= c.required or c.last ~= c.current then return nil end
  local push = (s.level >= 4 and 3 * c.required // 20) or c.required // 10
  return {chargeTo = math.max(1, c.current - push), why = 'suppression'}
end

-- Time Mage, Detonate I: hits can briefly Stop a Slowed target; L2 also Immobilized; L3 also Disabled;
-- L4 higher chance.
lines[M.JOB.timeMage] = function(s)
  if not s.foe or s.targetStatus.stop then return nil end
  local held = s.targetStatus.slow or (s.level >= 2 and s.targetStatus.immobilize)
    or (s.level >= 3 and s.targetStatus.disable)
  if not held or not s.roll(s.level >= 4 and 25 or 15) then return nil end
  return {targetAdd = {[M.STATUS.stop] = 0.5}, why = 'detonate'}
end

-- Green Mage, Mana Leech I: hits drain a little MP to her; L2 draining a foe to 0 MP Silences it; L3 the drain
-- also heals her that much HP; L4 drains more.
lines[M.JOB.greenMage] = function(s)
  if not s.foe or not int(s.targetMp, 1, 99999) or not int(s.damage, 1, 99999) then return nil end
  local want = math.max(1, (s.damage * (s.level >= 4 and 8 or 5)) // 100)
  local drain = math.min(want, s.targetMp)
  local a = {drainMp = drain, why = 'manaLeech'}
  if s.level >= 2 and drain == s.targetMp and not s.targetStatus.silence then
    a.targetAdd = {[M.STATUS.silence] = 1}
  end
  if s.level >= 3 then a.healHp = drain end
  return a
end

function M.lineFor(job) return lines[job] end

-- Decide one physical hit by a party member. Returns an action table or nil.
function M.decide(s, claimedRank)
  if type(s) ~= 'table' or not int(s.job, 0, 11) then return nil end
  local level = M.levels(claimedRank)[1]
  if level < 1 then return nil end
  local line = lines[s.job]
  if not line then return nil end
  s.level = level
  return line(s)
end

function M.reset() chains = {} end

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
