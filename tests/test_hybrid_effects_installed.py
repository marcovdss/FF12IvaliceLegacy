"""Check of the 66 hybrid Proficiency lines against the INSTALLED rules (proficiency.lua and
hybrid_proficiency.lua as the game folder holds them).

Every effect of every stage is replayed on its own: the stage's real event and conditions, one effect kept (with its
own first level), decided at levels 1 to 4. Below its first level the effect must do nothing; from it, the result must
be what the effect's wording says, written here independently of the engine (the oracle table). A stage that carries
two linked effects (drain + drainToHeal / leechSilence, hp + hpOverflowRegen) is replayed whole for those.

Skips without IVALICE_LEGACY_GAME (see deployed_scripts.py).
"""
import unittest

import deployed_scripts as D

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

LUA = r'''
local ORIG = {}
H.stageText = function() return '' end   -- the gear text is not under test here, and a lone gated effect has none
for i, l in ipairs(H.LINES) do ORIG[i] = l end
local FILLER = {'aim', nil, 'Never', 's', {{'mp', 1}}}
local STATUS = P.STATUS
local ALL_BUFFS = {protect=true, shell=true, haste=true, bravery=true, faith=true, regen=true, reflect=true}

local function at(v, L) if type(v) == 'table' then return v[L] end return v end
local function near(a, b) return type(a) == 'number' and math.abs(a - b) < 1e-9 end
local function find(a, op, unit, id)
  for _, o in ipairs(a and a.others or {}) do
    if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then return o end
  end
end
local function count(a, op, id)
  local n = 0
  for _, o in ipairs(a and a.others or {}) do if o.op == op and (id == nil or o.id == id) then n = n + 1 end end
  return n
end
local function listHas(list, id) for _, v in ipairs(list or {}) do if v == id then return true end end return false end

local REACTION = {guard=true, counter=true, struck=true, ally=true}
local DECIDE_AS = {hit='hit', magick='magick', heal='heal', guard='guard', counter='guard', struck='struck',
                   ally='ally', status='status', cast='cast', castFoe='cast', castAlly='cast'}

local function S(job, t)
  local s = {job=job, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, damage=100,
             targetStatus={}, casterStatus={}, targetMp=500, targetMaxMp=1000, casterHpPct=100, casterHp=1000,
             casterMp=500, casterMaxMp=1000, selfMaxHp=1000, targetHpPct=80,
             party={{address=100, job=job, hpPct=100, hp=1000, maxHp=1000, mp=500, maxMp=1000, status={}},
                    {address=200, job=0, hpPct=40, hp=400, maxHp=1000, mp=5, maxMp=100, status={poison=true, slow=true}},
                    {address=300, job=6, hpPct=90, hp=900, maxHp=1000, mp=80, maxMp=100, status={}}},
             foes={{address=901, status={}}, {address=902, status={}}},
             charge={current=25000, required=30000, last=25000}, roll=function() return true end}
  for k, v in pairs(t or {}) do s[k] = v end
  if s.otherFoe then s.foes[1].status = s.otherFoe end
  if s.otherAilment then s.party[3].status = {poison=true} end
  return s
end

-- A snapshot that satisfies the stage's conditions (its event is `ev`), plus what the effect needs to have something to act on.
local serial = 100
local pickAilment
local function snapshot(stage, ev, fx)
  serial = serial + 1
  local ts, cs = {}, {}
  local t = {targetId=serial, kill=false}
  local conds = {}
  for _, c in ipairs(stage[2] or {}) do conds[c] = true end
  -- the ailments that satisfy 'ailing' must not be the one the effect is about to inflict
  local avoid = (fx[1] == 'foe' or fx[1] == 'other' or fx[1] == 'poisonHasted') and (fx[2] or 'poison') or nil
  if fx[1] == 'poisonHasted' then avoid = 'poison' end
  pickAilment = {}
  for _, n in ipairs({'poison', 'blind', 'silence', 'sap', 'disease'}) do
    if n ~= avoid then pickAilment[#pickAilment + 1] = n end
  end
  for c in pairs(conds) do
    if c == 'buffed' then for k in pairs(ALL_BUFFS) do ts[k] = true end
    elseif c == 'ailing' or c == 'allyAiling' then ts[pickAilment[1]] = true
    elseif c == 'ailing2' then ts[pickAilment[1]], ts[pickAilment[2]] = true, true
    elseif c == 'ailing3' then ts[pickAilment[1]], ts[pickAilment[2]], ts[pickAilment[3]] = true, true, true
    elseif c == 'slowed' or c == 'held' or c == 'detonable' then ts.slow = true
    elseif c == 'stopped' then ts.stop = true
    elseif c == 'immobilized' then ts.immobilize = true
    elseif c == 'oiled' then ts.oil = true
    elseif c == 'diseased' then ts.disease = true
    elseif c == 'blinded' then ts.blind = true
    elseif c == 'disabled' then ts.disable = true
    elseif c == 'sleeping' then ts.sleep = true
    elseif c == 'foeFaith' then ts.faith = true
    elseif c == 'allyWarded' then ts.bubble = true
    elseif c == 'allyHasted' then ts.haste = true
    elseif c == 'tough' then t.tough, t.attackerTough = true, true
    elseif c == 'emptyMp' then t.targetMp, t.attackerMp, t.attackerMaxMp = 0, 0, 1000
    elseif c == 'lowMp' then t.targetMp, t.attackerMp, t.attackerMaxMp = 10, 10, 1000
    elseif c == 'staggered' then t.staggered, t.attackerStaggered = true, true
    elseif c == 'brave' then cs.bravery = true
    elseif c == 'faithful' then cs.faith = true
    elseif c == 'lureOrFaith' or c == 'imbuedOrFaith' then cs.faith = true
    elseif c == 'wounded' or c == 'allyCritical' then t.targetHpPct = 20
    elseif c == 'about' then t.charge = {current=25000, required=30000, last=25000}
    elseif c == 'hasted' then cs.haste = true
    elseif c == 'lured' then cs.lure = true
    elseif c == 'floating' then cs.float = true
    elseif c == 'invisible' then cs.invisible = true
    elseif c == 'low50' then t.casterHpPct, t.casterHp = 40, 400
    elseif c == 'critical' then t.casterHpPct, t.casterHp = 20, 200
    elseif c == 'dropCritical' then t.dropCritical = true
    elseif c == 'fullMp' then t.casterMp = 1000
    elseif c == 'overflow' then t.overflow = 50
    elseif c == 'item' then t.item = true
    elseif c == 'selfTarget' then t.target = 100
    elseif c == 'kill' then t.kill = true
    elseif c == 'far' then t.distance = 20
    elseif c == 'near' then t.distance = 5
    elseif c == 'slowCast' then t.landed = {15}
    elseif c == 'hasteCast' then t.landed = {20}
    end
  end
  local op = fx[1]
  -- What the effect acts on.
  if op == 'strip' or op == 'stripOther' or op == 'allyRemove' then
    for _, n in ipairs(fx[2]) do ts[n] = true end
  end
  if op == 'steal' or op == 'giftBuff' then ts.haste = true end
  if op == 'stripOther' then t.otherFoe = {} for _, n in ipairs(fx[2]) do t.otherFoe[n] = true end end
  if op == 'cureOther' then t.otherAilment = true end
  if op == 'overflowMp' or op == 'hpOverflowRegen' and false then t.overflow = 50 end
  if op == 'extend' then ts[fx[2]] = true end
  if op == 'consume' or op == 'dmgPoisoned' or op == 'renew' or op == 'renewFoe' then ts.poison = true end
  if op == 'otherCopy' then ts.poison = true end
  if op == 'renew' or op == 'renewFoe' then ts.slow = true end
  if op == 'poisonHasted' then cs.haste = true end
  if op == 'cure' then cs.poison, cs.slow = true, true end
  if (op == 'cureAlly' or op == 'allyRemove') and ev == 'heal' then ts.poison, ts.slow = true, true end
  t.targetStatus, t.attackerStatus, t.casterStatus = ts, ts, cs
  t.attacker, t.attackerMp = 901, t.attackerMp or 500
  t.attackerMaxMp = t.attackerMaxMp or 1000
  t.element, t.now = 1, 9000000000
  if ev == 'heal' then
    t.foe, t.target, t.healAmount, t.overflow = false, (t.target == 100 and 100 or 200), 300, t.overflow or 0
    t.damage = nil
  end
  if ev == 'guard' then t.outcome = 'parry' end
  if ev == 'counter' then t.outcome, t.guardOutcome = 'counter', 'parry' end
  if ev == 'ally' then t.ally = 200 end
  if ev == 'status' then t.landed = t.landed or {8} end
  if ev == 'cast' or ev == 'castFoe' then t.landed, t.allyTarget, t.action = t.landed or {15}, false, 60 + serial end
  if ev == 'castAlly' then t.landed, t.allyTarget, t.target, t.action = t.landed or {20}, true, 200, 60 + serial end
  return t, ts, cs
end

local function prime(id, stage, ev, t)
  local clock = 0
  local function play(e, extra)
    clock = clock + 1000000000
    local s = S(id, {targetId=t.targetId, element=1, foe=true, target=900, healAmount=300, overflow=0, attacker=901,
                     outcome=e == 'guard' and 'parry' or nil, now=clock})
    if e == 'heal' then s.foe, s.target = false, 200 end
    if e == 'counterRoll' then e, s.outcome, s.guardOutcome = 'guard', 'counter', 'parry' end
    for k, v in pairs(extra or {}) do s[k] = v end
    P.decide(e, s, 4)
  end
  for _, c in ipairs(stage[2] or {}) do
    if c == 'afterHeal' then play('heal')
    elseif c == 'afterMagick' or c == 'imbued' then play('magick')
    elseif c == 'afterHit' then play('hit', {targetId=t.targetId + 5000})
    elseif c == 'afterGuard' then play('guard')
    elseif c == 'afterCounter' then play('counterRoll')
    elseif c == 'chain2' then play('hit')
    elseif c == 'chain3' then play('hit'); play('hit')
    elseif c == 'spellTarget' then play('magick')
    elseif c == 'sameElement' then play('magick')
    elseif c == 'newElement' then play('magick'); t.element = 2
    elseif c == 'alternate3' then
      if ev == 'magick' then play('heal'); play('magick', {targetId=t.targetId + 1}); play('heal')
      else play('magick'); play('heal'); play('magick', {targetId=t.targetId + 1}) end
    end
  end
end

-- ---------- the oracle: what each effect's wording means, per event ----------
local O = {}
O.dmg = function(c) if REACTION[c.ev] then return nil end return near(c.a.damage, 1 + c.v / 100) or 'damage ' .. tostring(c.a.damage) end
O.self = function(c)
  local id, scale = STATUS[c.fx[2]], at(c.fx[3], c.L) or 1
  if REACTION[c.ev] then return find(c.a, 'add', 100, id) and find(c.a, 'add', 100, id).scale == scale or 'no self add' end
  return c.a.casterAdd and near(c.a.casterAdd[id], scale) or 'no casterAdd'
end
O.foe = function(c)
  local id, scale = STATUS[c.fx[2]], at(c.fx[3], c.L) or 1
  if REACTION[c.ev] then local o = find(c.a, 'add', 901, id); return o and o.scale == scale or 'no foe add' end
  if c.ev == 'heal' then return 'foe grant on a heal event has no foe' end
  return c.a.targetAdd and near(c.a.targetAdd[id], scale) or 'no targetAdd'
end
O.other = function(c)
  local o = find(c.a, 'add', 901, STATUS[c.fx[2]]); return o and o.scale == (at(c.fx[3], c.L) or 1) or 'no other-foe add'
end
O.ally = function(c)
  local id, scale = STATUS[c.fx[2]], at(c.fx[3], c.L) or 1
  if c.ev == 'heal' or c.ev == 'castAlly' then return c.a.targetAdd and near(c.a.targetAdd[id], scale) or 'no targetAdd on the healed ally' end
  local o = find(c.a, 'add', 200, id); return o and o.scale == scale or 'no most-hurt-ally add'
end
O.party = function(c)
  local id, scale = STATUS[c.fx[2]], at(c.fx[3], c.L) or 1
  for _, u in ipairs({100, 200, 300}) do
    local o = find(c.a, 'add', u, id)
    if not (o and o.scale == scale) then return 'party member ' .. u .. ' missed' end
  end
end
O.strip = function(c)
  for _, n in ipairs(c.fx[2]) do
    if REACTION[c.ev] then if not find(c.a, 'remove', 901, STATUS[n]) then return 'not stripped: ' .. n end
    elseif not listHas(c.a.targetRemove, STATUS[n]) then return 'not stripped: ' .. n end
  end
end
O.allyRemove = function(c)
  for _, n in ipairs(c.fx[2]) do
    if c.ev == 'heal' then if not listHas(c.a.targetRemove, STATUS[n]) then return 'not removed: ' .. n end
    elseif not find(c.a, 'remove', 200, STATUS[n]) then return 'not removed: ' .. n end
  end
end
O.stripOther = function(c)
  for _, n in ipairs(c.fx[2]) do if not find(c.a, 'remove', 901, STATUS[n]) then return 'not stripped: ' .. n end end
end
O.allyHeal = function(c)
  local o = find(c.a, 'hp', 200); return o and o.amount == math.max(1, 100 * c.v // 100) or 'ally heal ' .. tostring(o and o.amount)
end
O.allyHp = function(c) local o = find(c.a, 'hp', 200); return o and o.amount == 1000 * c.v // 100 or 'ally hp' end
O.hp = function(c) local o = find(c.a, 'hp', 100); return o and o.amount == 1000 * c.v // 100 or 'self hp' end
O.mp = function(c)
  local itemOnly = c.ev == 'heal' and c.item and not c.itemStage
  local o = find(c.a, 'mp', 100)
  if itemOnly then return o == nil or 'item heal returned MP' end
  return o and o.amount == 1000 * c.v // 100 or 'self mp ' .. tostring(o and o.amount)
end
O.overflowMp = function(c)
  local itemOnly = c.ev == 'heal' and c.item and not c.itemStage
  local o = find(c.a, 'mp', 100)
  if itemOnly then return o == nil or 'item heal returned MP' end
  return o and o.amount == 50 * c.v // 100 or 'overflow mp ' .. tostring(o and o.amount)
end
local function cap(maxMp) return math.max(1, maxMp * 2 // 100) end
O.allyMp = function(c)
  local itemOnly = c.ev == 'heal' and c.item and not c.itemStage
  if c.ev == 'heal' then
    local o = find(c.a, 'mp', 200)
    if itemOnly then return o == nil or 'item heal returned MP' end
    return o and o.amount == 300 * c.v // 100 or 'allyMp heal ' .. tostring(o and o.amount)
  end
  -- a hit's damage-based MP is capped at 2% of the receiver's max MP (ally 200 has 100)
  local want = math.max(1, 100 * c.v // 100)
  if c.ev == 'hit' then want = math.min(want, cap(100)) end
  local o = find(c.a, 'mp', 200); return o and o.amount == want or 'allyMp ' .. tostring(o and o.amount)
end
O.cure = function(c) return count(c.a, 'remove') == math.min(c.v, 2) or 'cured ' .. count(c.a, 'remove') end
O.cureAlly = function(c)
  if c.ev == 'heal' then return #(c.a.targetRemove or {}) == math.min(c.v, 2) or 'cured ' .. #(c.a.targetRemove or {}) end
  return count(c.a, 'remove') == math.min(c.v, 2) or 'cured ' .. count(c.a, 'remove')
end
O.cureOther = function(c) return count(c.a, 'remove') >= 1 or 'nobody cured' end
O.quick = function(c) return near(c.a.casterCharge, c.v / 100) or 'casterCharge ' .. tostring(c.a.casterCharge) end
O.allyQuick = function(c) local o = find(c.a, 'quick', 200); return o and near(o.fraction, c.v / 100) or 'no ally quick' end
O.push = function(c)
  if REACTION[c.ev] then local o = find(c.a, 'push', 901); return o and near(o.fraction, c.v / 100) or 'no attacker push' end
  if c.ev == 'hit' then return c.a.chargeTo == 25000 - math.floor(30000 * c.v / 100) or 'chargeTo ' .. tostring(c.a.chargeTo) end
  local o = find(c.a, 'push', 900); return o and near(o.fraction, c.v / 100) or 'no target push'
end
O.pushOther = function(c) local o = find(c.a, 'push', 901); return o and near(o.fraction, c.v / 100) or 'no other-foe push' end
O.renew = function(c) return listHas(c.a.targetRenew, STATUS.poison) and listHas(c.a.targetRenew, STATUS.slow) or 'timers not renewed' end
O.renewFoe = function(c)
  if REACTION[c.ev] then return find(c.a, 'add', 901, STATUS.poison) ~= nil or 'timers not renewed' end
  return 'renewFoe has no meaning off a reaction'
end
O.extend = function(c)
  return c.a.targetExtend and near(c.a.targetExtend[STATUS[c.fx[2]]], c.fx[3] / 100) or 'no targetExtend'
end
O.steal = function(c)
  if REACTION[c.ev] then return 'steal on a reaction has no code' end
  local n
  for _, b in ipairs(P.BUFFS) do if c.t.targetStatus[b] and b ~= 'reflect' and b ~= 'berserk' then n = b break end end
  return listHas(c.a.targetRemove, STATUS[n]) and c.a.casterAdd and c.a.casterAdd[STATUS[n]] == 0.5 or 'buff not stolen'
end
local function firstOf(order, set, skip)
  for _, n in ipairs(order) do if set[n] and n ~= skip then return n end end
end
O.otherCopy = function(c)
  local n = firstOf(P.HINDERED, c.t.targetStatus, 'stop')
  local o = n and find(c.a, 'add', 901, STATUS[n]); return o and near(o.scale, c.v) or 'ailment not spread'
end
O.stopChance = function(c)
  if c.rolled ~= c.v then return 'rolled ' .. tostring(c.rolled) .. ' not ' .. c.v end
  if REACTION[c.ev] then local o = find(c.a, 'add', 901, STATUS.stop); return o and o.scale == 0.5 or 'no stop' end
  return c.a.targetAdd and c.a.targetAdd[STATUS.stop] == 0.5 or 'no stop'
end
O.dmgNext = function(c)
  local s = S(c.job, {targetId=c.t.targetId + 7000})
  local b = P.decide('hit', s, c.L)
  return b and near(b.damage, 1 + c.v / 100) or 'next hit damage ' .. tostring(b and b.damage)
end
O.splash = function(c) local o = find(c.a, 'hp', 901); return o and o.amount == -math.max(1, 100 * c.v // 100) or 'splash' end
O.drain = function(c)
  local want = math.min(math.max(1, 100 * c.v // 100), 500)
  if c.ev == 'hit' then want = math.min(want, math.max(1, 1000 * 2 // 100)) end
  return c.a.drainMp == want or 'drainMp ' .. tostring(c.a.drainMp)
end
O.heal = function(c)
  if REACTION[c.ev] then return 'heal on a reaction has no formula to carry it' end
  return c.a.healHp == math.max(1, 100 * c.v // 100) or 'healHp ' .. tostring(c.a.healHp)
end
O.consume = function(c)
  local n = firstOf(P.AILMENTS, c.t.targetStatus)
  return listHas(c.a.targetRemove, STATUS[n]) and near(c.a.damage, 1 + c.v / 100) or 'ailment not consumed'
end
O.nearHeal = function(c) local o = find(c.a, 'hp', 300); return o and o.amount == 300 * c.v // 100 or 'nearHeal' end
O.drainNear = function(c) local o = find(c.a, 'hp', 901); return o and o.amount == -(300 * c.v // 100) or 'drainNear' end
O.partyHealPct = function(c) local o = find(c.a, 'hp', 300); return o and o.amount == 300 * c.v // 100 or 'partyHeal' end
O.poisonHasted = function(c) return c.a.targetAdd and c.a.targetAdd[STATUS.poison] == 0.5 or 'no poison' end
O.dmgPoisoned = function(c) return near(c.a.damage, 1 + c.v / 100) or 'damage' end
O.giftBuff = function(c)
  local scale = at(c.fx[2], c.L) or 0.5
  local n
  for _, b in ipairs(P.BUFFS) do if c.t.targetStatus[b] and b ~= 'reflect' and b ~= 'berserk' then n = b break end end
  local gone = REACTION[c.ev] and find(c.a, 'remove', 901, STATUS[n]) or listHas(c.a.targetRemove, STATUS[n])
  local o = find(c.a, 'add', 200, STATUS[n])
  return (gone and o and near(o.scale, scale)) or 'buff not moved'
end
O.transferAilment = function(c)
  if not REACTION[c.ev] then return 'transferAilment off a reaction has no code' end
  return (find(c.a, 'remove', 200, STATUS.poison) and find(c.a, 'add', 901, STATUS.poison)) or 'ailment not moved'
end
O.drainFoe = function(c)
  if not REACTION[c.ev] then return 'drainFoe off a reaction has no code' end
  local want = math.min(math.max(1, 1000 * c.v // 100), 500)
  local a, b = find(c.a, 'mp', 901), find(c.a, 'mp', 100)
  return (a and b and a.amount == -want and b.amount == want) or 'foe mp not drained'
end
O.hpOverflowRegen = function(c) local o = find(c.a, 'add', 200, STATUS.regen); return o and near(o.scale, at(c.fx[2], c.L) or 1) or 'no regen' end
O.evade = function(c)
  local s = S(c.job, {casterStatus=c.t.casterStatus, casterHpPct=c.t.casterHpPct, casterMp=c.t.casterMp})
  local a = P.decide('aim', s, c.L)
  return (a and a.evade and a.evade.weapon and a.evade.shield and a.evade.parry) or 'no evade'
end
-- Linked effects: replayed with the stage whole.
O.drainToHeal = false
O.leechSilence = false

local results, failures, tested = {}, {}, 0
local function run(line, k, stage, ev, fx, L, whole)
  local clone = whole and stage or {stage[1], stage[2], stage[3], stage[4], {fx}}
  H.LINES[1] = {line='Test', clone, FILLER, FILLER}
  H.attach(P)
  P.reset()
  local id = H.FIRST_ID
  local t, ts, cs = snapshot(stage, ev, fx)
  local rolled
  t.roll = function(p) rolled = p; return true end
  prime(id, stage, ev, t)
  local itemStage = H.itemStage(stage)
  if ev == 'heal' and not t.item and fx[1] ~= 'mp' then end
  local a = P.decide(DECIDE_AS[ev], S(id, t), L)
  return a, t, rolled, itemStage, id
end

for i = 1, #ORIG do
  for k = 1, 3 do
    local stage = ORIG[i][k]
    local events = type(stage[1]) == 'table' and stage[1] or {stage[1]}
    for _, ev in ipairs(events) do
      for e, fx in ipairs(stage[5]) do
        local op = fx[1]
        local first = fx.f or 1
        local key = ORIG[i].line .. ' ' .. k .. ' ' .. ev .. ' ' .. op
        if op == 'drainToHeal' or op == 'leechSilence' or op == 'hpOverflowRegen' then
          -- linked to a sibling effect of the same stage: the whole stage is replayed
          for L = 1, 4 do
            tested = tested + 1
            local why
            H.LINES[1] = {line='Test', stage, FILLER, FILLER}
            H.attach(P); P.reset()
            local id = H.FIRST_ID
            local t = snapshot(stage, ev, fx)
            t.casterMp = 1000                              -- full MP (drainToHeal's condition)
            if op == 'leechSilence' then t.targetMp = 3 end  -- a foe the drain empties
            if op == 'hpOverflowRegen' then t.casterHp, t.casterHpPct = 990, 99 end  -- a heal that overflows
            prime(id, stage, ev, t)
            local a = P.decide(DECIDE_AS[ev], S(id, t), L)
            local live = L >= first
            if op == 'drainToHeal' then
              local o = a and find(a, 'hp', 200)
              if live and not (o and o.amount == 10 and a.drainMp and a.drainMp > 0) then why = 'no heal from the drain' end
              if not live and o then why = 'healed below its first level' end
            elseif op == 'leechSilence' then
              local ls = a and a.leechSilence
              if live and not (ls and ls.need == 3 and near(ls.scale, at(fx[2], L))) then why = 'no silence for a drained-dry foe' end
              if not live and ls then why = 'silenced below its first level' end
              -- a foe the drain does not empty is not silenced
              local u = snapshot(stage, ev, fx); u.casterMp = 1000; u.targetMp = 500
              H.attach(P); P.reset(); prime(id, stage, ev, u)
              local b = P.decide(DECIDE_AS[ev], S(id, u), L)
              if b and b.leechSilence then why = 'silenced a foe that still had MP' end
            else
              local o = a and find(a, 'add', 200, STATUS.regen)
              if live and not (o and near(o.scale, at(fx[2], L))) then why = 'no regen for the overflow' end
              if not live and o then why = 'regen below its first level' end
              -- a heal that does not overflow gives nothing
              local u = snapshot(stage, ev, fx); u.casterHp, u.casterHpPct = 400, 40
              H.attach(P); P.reset(); prime(id, stage, ev, u)
              local b = P.decide(DECIDE_AS[ev], S(id, u), L)
              if b and find(b, 'add', 200, STATUS.regen) then why = 'regen without an overflow' end
            end
            if why then failures[#failures + 1] = key .. '	' .. L .. '	' .. why end
          end
        else
          local oracle = O[op]
          for L = 1, 4 do
            tested = tested + 1
            local a, t, rolled, itemStage, id = run(ORIG[i].line, k, stage, ev, fx, L, false)
            local why
            if L < first then
              if a ~= nil then why = 'acted below its first level' end
            else
              if not oracle then why = 'no oracle for ' .. op
              elseif a == nil and op ~= 'dmgNext' and op ~= 'evade' then why = 'did nothing'
              else
                local v = at(fx[2], L)
                local res = oracle({a=a, ev=ev, fx=fx, L=L, v=v, t=t, job=id, rolled=rolled, item=t.item, itemStage=itemStage})
                if res ~= nil and res ~= true and type(res) ~= 'table' then why = type(res) == 'string' and res or 'wrong result' end
              end
            end
            if why then failures[#failures + 1] = key .. '	' .. L .. '	' .. why end
          end
        end
      end
    end
  end
end
return table.concat(failures, '\n'), tested
'''


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
@unittest.skipUnless(D.have('proficiency', 'hybrid_proficiency'), D.SKIP_REASON)
class InstalledHybridEffectTest(unittest.TestCase):
    # (line, stage, event, effect) -> why the wording and the code differ. A fix makes the key disappear and this list
    # must shrink with it.
    KNOWN = {}

    def test_every_hybrid_effect_does_what_its_wording_says_at_every_level(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        g = lua.globals()
        g.P = lua.execute(D.module('proficiency'))
        g.H = lua.execute(D.module('hybrid_proficiency'))
        failures, tested = lua.execute(LUA)
        self.assertGreater(tested, 2000)
        found = {}
        for row in filter(None, failures.split('\n')):
            key = row.split('	')[0]
            found.setdefault(key, []).append(row)
        unexpected = {k: v for k, v in found.items() if k not in self.KNOWN}
        self.assertEqual(unexpected, {}, 'effects whose result differs from their wording')
        self.assertEqual(sorted(set(self.KNOWN) - set(found)), [], 'known mismatches that no longer fail: shrink KNOWN')


if __name__ == '__main__':
    unittest.main()
