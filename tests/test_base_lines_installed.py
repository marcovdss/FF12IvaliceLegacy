"""Check of the 12 base Proficiency lines against the INSTALLED rules: every stage and every level of the gear
text (P.GEAR) is replayed through decide() and the numbers the text names are asserted.

Stage I is read at ranks 1-4 (II and III off), stage II at ranks 5-8 and stage III at ranks 9-12, so the lower stages are
at full strength when a higher one is under test: each check looks only at the effect it is about.

Skips without IVALICE_LEGACY_GAME (see deployed_scripts.py).
"""
import unittest

import deployed_scripts as D

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

LUA = r'''
local J = P.JOB
local ST = P.STATUS
local function near(a, b) return type(a) == 'number' and math.abs(a - b) < 1e-9 end
local function party(job, n, extra)
  local out = {{address=100, job=job, hpPct=100, mp=50, maxMp=100, status={}}}
  local hp = {40, 90, 70, 60, 80}
  for i = 1, (n or 3) - 1 do out[#out + 1] = {address=100 + 100 * i, job=0, hpPct=hp[i], mp=5, maxMp=100, status={}} end
  for k, v in pairs(extra or {}) do out[#out + 1] = v end
  return out
end
local function S(job, t)
  local s = {job=job, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, bystander=false, category=1,
             damage=100, targetStatus={}, casterStatus={}, targetMp=50, casterHpPct=100, casterMp=10, casterMaxMp=100,
             selfMaxHp=1000, party=party(job),
             foes={{address=901, status={}}, {address=902, status={}}, {address=903, status={}}},
             charge={current=20000, required=30000, last=20000}, roll=function() return true end}
  for k, v in pairs(t or {}) do s[k] = v end
  return s
end
local function others(a, op, unit, id)
  local n = 0
  for _, o in ipairs(a and a.others or {}) do
    if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then n = n + 1 end
  end
  return n
end
local function the(a, op, unit, id)
  for _, o in ipairs(a and a.others or {}) do
    if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then return o end
  end
end
local function has(list, id) for _, v in ipairs(list or {}) do if v == id then return true end end return false end
local function eq(got, want, what) assert(got == want, what .. ': got ' .. tostring(got) .. ' want ' .. tostring(want)) end
local function approx(got, want, what) assert(near(got, want), what .. ': got ' .. tostring(got) .. ' want ' .. tostring(want)) end
local function fresh() P.reset() end
-- stage k level L -> the claimed rank that shows it with the lower stages full
local function rank(k, L) return 4 * (k - 1) + L end

-- ===== Knight: Bulwark =====
for L = 1, 4 do
  fresh()
  local a = P.decide('guard', S(J.knight, {outcome='parry', attacker=777}), rank(1, L))
  approx(the(a, 'add', 100, ST.lure).scale, L >= 3 and 0.75 or 0.5, 'Bulwark I-' .. L .. ' Lure on parry')
  eq(others(a, 'push', 777) == 1, L == 4, 'Bulwark I-' .. L .. ' parry pushes the attacker')
  local d = P.decide('guard', S(J.knight, {outcome='dodge', attacker=777}), rank(1, L))
  eq(d ~= nil, L >= 2, 'Bulwark I-' .. L .. ' dodge gives Lure only from level 2')
  if d then eq(others(d, 'push'), 0, 'Bulwark I dodge never pushes') end
  local b = P.decide('guard', S(J.knight, {outcome='block', attacker=777}), rank(1, L))
  assert(the(b, 'add', 100, ST.lure), 'a block also gives Lure')
  -- II: the most hurt allies (party 100 self, 200 40%, 300 90%, 400 70%)
  local p = party(J.knight, 4); p[2].status = {immobilize=true}
  local g = P.decide('guard', S(J.knight, {outcome='parry', attacker=777, party=p}), rank(2, L))
  assert(the(g, 'add', 200, ST.protect) and the(g, 'add', 200, ST.protect).scale == 0.5, 'Bulwark II-' .. L .. ' Protect')
  eq(the(g, 'add', 200, ST.shell) ~= nil, L >= 2, 'Bulwark II-' .. L .. ' Shell')
  eq(others(g, 'add', 400, ST.protect) == 1, L >= 3, 'Bulwark II-' .. L .. ' second ally')
  eq(others(g, 'add', 300, ST.protect), 0, 'Bulwark II never reaches the third ally')
  eq(others(g, 'remove', 200, ST.immobilize) == 1, L == 4, 'Bulwark II-' .. L .. ' frees Immobilize')
  local dd = P.decide('guard', S(J.knight, {outcome='dodge', attacker=777, party=p}), rank(2, L))
  eq(others(dd, 'add', 200, ST.protect), 0, 'Bulwark II needs a parry or block, not a dodge')
  -- III: an ally dropped to critical HP
  local c = P.decide('ally', S(J.knight, {ally=300}), rank(3, L))
  approx(the(c, 'add', 100, ST.lure).scale, L >= 2 and 0.75 or 0.5, 'Bulwark III-' .. L .. ' Lure')
  eq(the(c, 'add', 100, ST.bravery) ~= nil, L >= 3, 'Bulwark III-' .. L .. ' Bravery')
  eq(the(c, 'add', 300, ST.protect) ~= nil, L == 4, 'Bulwark III-' .. L .. ' ally Protect')
end

-- ===== Mystic: Ki Flow (counter) =====
for L = 1, 4 do
  fresh()
  local p = party(J.mystic, 3)
  local a = P.decide('guard', S(J.mystic, {outcome='counter', attacker=777, party=p}), rank(1, L))
  eq(the(a, 'hp', 100).amount, 1000 * (L == 4 and 8 or 5) // 100, 'Ki Flow I-' .. L .. ' self heal')
  eq(the(a, 'hp', 200) ~= nil, L >= 2, 'Ki Flow I-' .. L .. ' ally heal')
  if L >= 2 then eq(the(a, 'hp', 200).amount, the(a, 'hp', 100).amount // 2, 'Ki Flow I ally gets half') end
  eq(others(a, 'push', 777) == 1, L >= 3, 'Ki Flow I-' .. L .. ' push')
  local n = P.decide('guard', S(J.mystic, {outcome='parry'}), rank(1, L))
  eq(n, nil, 'Ki Flow needs a counter, not a plain parry')
  local b = P.decide('guard', S(J.mystic, {outcome='counter', casterStatus={blind=true, sap=true}}), rank(2, L))
  eq(the(b, 'mp', 100).amount, L == 4 and 8 or 4, 'Ki Flow II-' .. L .. ' MP')
  eq(others(b, 'remove', 100, ST.blind) == 1, L >= 2, 'Ki Flow II-' .. L .. ' Blind')
  eq(others(b, 'remove', 100, ST.sap) == 1, L >= 3, 'Ki Flow II-' .. L .. ' Sap')
  local c = P.decide('guard', S(J.mystic, {outcome='counter', casterStatus={poison=true, slow=true, silence=true}}), rank(3, L))
  eq(others(c, 'remove', 100), L == 4 and 2 or 1, 'Ki Flow III-' .. L .. ' cures')
  eq(the(c, 'add', 100, ST.protect) ~= nil, L >= 2, 'Ki Flow III-' .. L .. ' Protect')
  eq(the(c, 'add', 100, ST.haste) ~= nil, L >= 3, 'Ki Flow III-' .. L .. ' Haste')
  if L >= 2 then approx(the(c, 'add', 100, ST.protect).scale, 0.5, 'Ki Flow III Protect half') end
end

-- ===== White Mage: Overflow =====
for L = 1, 4 do
  fresh()
  local big = party(J.whiteMage, 5)
  local function heal(r, t) fresh(); return P.decide('heal', S(J.whiteMage, {overflow=100, target=200, party=big, castId=L, item=t and t.item}), r) end
  local a = heal(rank(1, L))
  approx(a.targetAdd[ST.regen], L >= 4 and 1.5 or 1, 'Overflow I-' .. L .. ' Regen')
  eq(the(a, 'mp', 100) ~= nil, L >= 3, 'Overflow I-' .. L .. ' MP back')
  if L >= 3 then eq(the(a, 'mp', 100).amount, 100 // 20, 'Overflow I MP is 5% of the overflow') end
  local item = heal(rank(1, L), {item=true})
  eq(item ~= nil and item.targetAdd ~= nil and item.targetAdd[ST.regen] ~= nil, L >= 2, 'Overflow I-' .. L .. ' items')
  if item then eq(others(item, 'mp'), 0, 'items never return MP') end
  local b = heal(rank(2, L))
  eq(others(b, 'add', nil, ST.regen), L == 1 and 1 or (L == 2 and 2 or 4), 'Overflow II-' .. L .. ' reach')
  local want = L == 4 and 1.5 * 1.25 or 1.5
  approx(b.targetAdd[ST.regen], want, 'Overflow II-' .. L .. ' Regen length')
  local c = heal(rank(3, L))
  approx(c.targetAdd[ST.shell], L == 4 and 0.75 or 0.5, 'Overflow III-' .. L .. ' Shell')
  eq(c.targetAdd[ST.protect] ~= nil, L >= 2, 'Overflow III-' .. L .. ' Protect')
  eq(c.targetAdd[ST.faith] ~= nil, L >= 3, 'Overflow III-' .. L .. ' Faith')
  if L >= 2 then approx(c.targetAdd[ST.protect], 0.5, 'Overflow III Protect half') end
  eq(P.decide('heal', S(J.whiteMage, {overflow=0, target=200}), rank(3, L)), nil, 'no overflow, no effect')
end

-- ===== Lancer: Grounding =====
local BUFFS = {'float', 'haste', 'faith', 'regen', 'bravery'}
for L = 1, 4 do
  fresh()
  local all = {float=true, haste=true, faith=true, regen=true, bravery=true, protect=true, shell=true, reflect=true, bubble=true, berserk=true}
  local function removed(a, name) return has(a and a.targetRemove, ST[name]) end
  local a = P.decide('hit', S(J.lancer, {targetStatus={float=true, haste=true, faith=true, regen=true, bravery=true}}), rank(1, L))
  eq(removed(a, 'float'), true, 'Grounding I float')
  eq(removed(a, 'haste'), true, 'Grounding I haste')
  eq(removed(a, 'faith'), L >= 2, 'Grounding I-' .. L .. ' Faith')
  eq(removed(a, 'regen'), L >= 3, 'Grounding I-' .. L .. ' Regen')
  eq(removed(a, 'bravery'), L >= 4, 'Grounding I-' .. L .. ' Bravery')
  local b = P.decide('hit', S(J.lancer, {targetStatus={poison=true, protect=true, shell=true, reflect=true, bubble=true, berserk=true}}), rank(2, L))
  eq(removed(b, 'protect'), true, 'Grounding II Protect')
  eq(removed(b, 'shell'), L >= 2, 'Grounding II-' .. L .. ' Shell with Protect')
  eq(removed(b, 'reflect'), L >= 3, 'Grounding II-' .. L .. ' Reflect')
  eq(removed(b, 'bubble'), L >= 4, 'Grounding II-' .. L .. ' Bubble')
  eq(removed(b, 'berserk'), L >= 4, 'Grounding II-' .. L .. ' Berserk')
  local only = P.decide('hit', S(J.lancer, {targetStatus={poison=true, shell=true}}), rank(2, 1))
  eq(removed(only, 'shell'), true, 'Grounding II-1 strips Shell when there is no Protect')
  local calm = P.decide('hit', S(J.lancer, {targetStatus={protect=true, shell=true}}), rank(2, L))
  eq(calm == nil or not removed(calm, 'protect'), true, 'Grounding II needs an ailment on the foe')
  local c = P.decide('hit', S(J.lancer, {}), rank(3, L))
  approx(c.targetAdd[ST.immobilize], L >= 2 and 0.75 or 0.5, 'Grounding III-' .. L .. ' Immobilize')
  local kept = P.decide('hit', S(J.lancer, {targetStatus={protect=true}}), rank(3, L))   -- Protect survives a hit with no ailment on the foe
  eq(kept == nil or kept.targetAdd == nil, true, 'a foe still holding a buff is not Immobilized')
  local im = P.decide('hit', S(J.lancer, {targetStatus={immobilize=true}}), rank(3, L))
  eq(im ~= nil and im.damage ~= nil and near(im.damage, 1.15), L >= 3, 'Grounding III-' .. L .. ' +15% on Immobilized')
  eq(im ~= nil and im.targetAdd ~= nil and near(im.targetAdd[ST.slow] or -1, 0.5), L == 4, 'Grounding III-' .. L .. ' Slow')
end

-- ===== Warrior: Cleave =====
for L = 1, 4 do
  fresh()
  local a = P.decide('hit', S(J.warrior, {kill=true}), rank(1, L))
  approx(a.casterAdd[ST.bravery], L >= 3 and 1.5 or 1, 'Cleave I-' .. L .. ' kill Bravery')
  local sweep = P.decide('hit', S(J.warrior, {bystander=true, category=2}), rank(1, L))
  eq(sweep ~= nil and sweep.casterAdd ~= nil, L >= 2, 'Cleave I-' .. L .. ' two-foe greatsword swing')
  local axe = P.decide('hit', S(J.warrior, {bystander=true, category=10}), rank(1, L))
  eq(axe, nil, 'only a greatsword swing counts')
  local cure = P.decide('hit', S(J.warrior, {kill=true, casterStatus={bravery=true, slow=true, disable=true, immobilize=true}}), rank(1, L))
  eq(cure ~= nil and has(cure.casterRemove, ST.slow) and has(cure.casterRemove, ST.disable) and has(cure.casterRemove, ST.immobilize), L == 4,
     'Cleave I-' .. L .. ' kill while Brave cures')
  local low = P.decide('hit', S(J.warrior, {casterHpPct=45}), rank(2, L))
  assert(low.casterAdd[ST.bravery], 'Cleave II-' .. L .. ' Bravery below 50%')
  eq(low.clearReckless == true, L >= 2, 'Cleave II-' .. L .. ' Reckless dent')
  local mid = P.decide('hit', S(J.warrior, {casterHpPct=70}), rank(2, L))
  eq(mid ~= nil and mid.casterAdd ~= nil, L >= 3, 'Cleave II-' .. L .. ' below 75%')
  eq(the(low, 'add', 200, ST.bravery) ~= nil, L == 4, 'Cleave II-' .. L .. ' ally Bravery')
  if L == 4 then approx(the(low, 'add', 200, ST.bravery).scale, 0.5, 'Cleave II-4 ally Bravery half') end
  local crit = P.decide('hit', S(J.warrior, {casterStatus={hpCritical=true}}), rank(3, L))
  assert(crit.targetAdd[ST.sap], 'Cleave III-' .. L .. ' Sap at critical HP')
  local sap = P.decide('hit', S(J.warrior, {targetStatus={sap=true}}), rank(3, L))
  eq(sap ~= nil and sap.damage ~= nil and near(sap.damage, 1.15), L >= 2, 'Cleave III-' .. L .. ' +15% to Sapped')
  local spread = P.decide('hit', S(J.warrior, {kill=true, targetStatus={sap=true}}), rank(3, L))
  eq(others(spread, 'add', nil, ST.sap), L == 4 and 2 or (L == 3 and 1 or 0), 'Cleave III-' .. L .. ' Sap spread')
end

-- ===== Samurai: Zanshin =====
for L = 1, 4 do
  fresh()
  local a = P.decide('hit', S(J.samurai, {comboFollow=true}), rank(1, L))
  approx(a.casterAdd[ST.haste], L >= 3 and 0.75 or 0.5, 'Zanshin I-' .. L .. ' Haste')
  eq(a.damage ~= nil, L == 4, 'Zanshin I-' .. L .. ' +15%')
  fresh()
  local crit = P.decide('hit', S(J.samurai, {critical=true}), rank(1, L))
  eq(crit ~= nil, L >= 2, 'Zanshin I-' .. L .. ' critical hit')
  local hasted = P.decide('aim', S(J.samurai, {casterStatus={haste=true}}), rank(2, L))
  assert(hasted.evade.parry, 'Zanshin II-' .. L .. ' dodge')
  eq(hasted.evade.weapon == true and hasted.evade.shield == true, L >= 2, 'Zanshin II-' .. L .. ' parry and block')
  eq(P.decide('aim', S(J.samurai, {}), rank(2, L)), nil, 'Zanshin II needs Haste')
  fresh()
  local prot = P.decide('hit', S(J.samurai, {casterStatus={haste=true}, targetStatus={protect=true}}), rank(2, L))
  eq(prot ~= nil and prot.damage ~= nil and near(prot.damage, 1 / 0.75), L >= 3, 'Zanshin II-' .. L .. ' pierces Protect')
  local push = P.decide('hit', S(J.samurai, {casterStatus={haste=true}}), rank(2, L))
  eq(push ~= nil and push.chargeTo == 20000 - 30000 // 20, L == 4, 'Zanshin II-' .. L .. ' pushes the foe 5%')
  fresh()
  local k = P.decide('hit', S(J.samurai, {kill=true, casterStatus={haste=true}}), rank(3, L))
  approx(k.casterAdd[ST.haste], L == 4 and 0.75 or 0.5, 'Zanshin III-' .. L .. ' Haste')
  eq(k.casterAdd[ST.bravery] ~= nil, L >= 2, 'Zanshin III-' .. L .. ' Bravery')
  local nxt = P.decide('hit', S(J.samurai, {targetId=10}), rank(3, L))
  eq(nxt ~= nil and nxt.damage ~= nil and near(nxt.damage, 1.2), L >= 3, 'Zanshin III-' .. L .. ' next hit +20%')
  fresh()
  eq(P.decide('hit', S(J.samurai, {kill=true}), rank(3, L)) == nil or P.decide('hit', S(J.samurai, {kill=true}), rank(3, L)).casterAdd == nil, true,
     'Zanshin III needs Haste')
end

-- ===== Hunter: Pursuit =====
for L = 1, 4 do
  fresh()
  local prey = {poison=true}
  local a = P.decide('hit', S(J.hunter, {targetStatus=prey}), rank(1, L))
  approx(a.damage, L == 4 and 1.25 or 1.15, 'Pursuit I-' .. L .. ' damage')
  local b = P.decide('hit', S(J.hunter, {targetStatus=prey}), rank(1, L))
  approx(b.damage, (L == 4 and 1.25 or 1.15) + (L >= 2 and 0.05 or 0), 'Pursuit I-' .. L .. ' second hit in a row')
  -- (whether a third hit in a row adds another +5% is an open wording question: not asserted here)
  fresh()
  local kill = P.decide('hit', S(J.hunter, {targetStatus=prey, kill=true, damage=100}), rank(1, L))
  eq(kill.healPct == 10 or kill.healHp ~= nil, L >= 3, 'Pursuit I-' .. L .. ' kill heals')
  fresh()
  local t = P.decide('hit', S(J.hunter, {targetStatus={poison=true, slow=true, disable=true, immobilize=true}}), rank(2, L))
  eq(#t.targetRenew, L == 1 and 1 or (L <= 3 and 2 or 4), 'Pursuit II-' .. L .. ' timers renewed')
  eq(t.renewScale ~= nil and near(t.renewScale, 1.10), L >= 3, 'Pursuit II-' .. L .. ' 10% longer')
  fresh()
  local k = P.decide('hit', S(J.hunter, {targetStatus=prey, kill=true}), rank(3, L))
  approx(k.casterAdd[ST.haste], L == 4 and 0.75 or 0.5, 'Pursuit III-' .. L .. ' Haste')
  eq(the(k, 'add', 901, ST.slow) ~= nil, L >= 3, 'Pursuit III-' .. L .. ' second foe Slowed')
  local nextHit = P.decide('hit', S(J.hunter, {targetId=77}), rank(3, L))
  eq(nextHit ~= nil and nextHit.targetAdd ~= nil and nextHit.targetAdd[ST.slow] ~= nil, L >= 2, 'Pursuit III-' .. L .. ' next foe Slowed')
end

-- ===== Gunner: Suppression =====
for L = 1, 4 do
  fresh()
  -- weapon CT 24 -> x1: the numbers read directly
  local function push(r, t) fresh(); local a = P.decide('hit', S(J.gunner, t), r); return a end
  local base = {weaponCt=24, charge={current=20000, required=30000, last=20000}}
  local a = push(rank(1, L), base)
  eq(20000 - a.chargeTo, 30000 * (L == 4 and 0.15 or 0.10) // 1, 'Suppression I-' .. L .. ' push')
  local sl = push(rank(1, L), {weaponCt=24, targetStatus={slow=true}, charge=base.charge})
  eq(20000 - sl.chargeTo, math.floor(30000 * ((L == 4 and 0.15 or 0.10) + (L >= 2 and 0.05 or 0))), 'Suppression I-' .. L .. ' Slowed')
  eq(others(a, 'push', 901) == 1, L >= 3, 'Suppression I-' .. L .. ' nearest other foe')
  -- slow weapons push harder, at most double
  local heavy = push(rank(1, L), {weaponCt=50, charge=base.charge})
  eq(20000 - heavy.chargeTo, math.floor(30000 * (L == 4 and 0.15 or 0.10) * 2), 'Suppression I-' .. L .. ' CT 50 is double')
  local late = {weaponCt=24, charge={current=25000, required=30000, last=25000}}
  local b = push(rank(2, L), late)
  eq(25000 - b.chargeTo, math.floor(30000 * (0.15 + 0.10)), 'Suppression II-' .. L .. ' about to act')
  local early = push(rank(2, L), base)
  eq(20000 - early.chargeTo, math.floor(30000 * 0.15), 'Suppression II needs a foe at 3/4 charge')
  -- Silence chance via the roll
  local asked
  local c = push(rank(2, L), {weaponCt=24, charge=late.charge, roll=function(p) asked = p; return true end})
  eq(c.targetAdd ~= nil and c.targetAdd[ST.silence] ~= nil, L >= 2, 'Suppression II-' .. L .. ' Silence')
  if L >= 2 then eq(asked, L >= 3 and 25 or 15, 'Suppression II-' .. L .. ' Silence chance') end
  eq(others(c, 'add', 200, ST.faith) == 1, L == 4, 'Suppression II-' .. L .. ' ally Faith')
  -- III: hits in a row
  fresh()
  local need = L >= 2 and 2 or 3
  local last
  for i = 1, need do last = P.decide('hit', S(J.gunner, {weaponCt=24, targetId=55}), rank(3, L)) end
  eq(20000 - last.chargeTo, math.floor(30000 * (0.15 + 0.05)), 'Suppression III-' .. L .. ' push 5% more at hit ' .. need)
  eq(last.targetAdd ~= nil and last.targetAdd[ST.slow] ~= nil, L >= 3, 'Suppression III-' .. L .. ' Slow')
  if need == 3 then
    fresh(); local two
    for i = 1, 2 do two = P.decide('hit', S(J.gunner, {weaponCt=24, targetId=55}), rank(3, L)) end
    eq(20000 - two.chargeTo, math.floor(30000 * 0.15), 'Suppression III-1 needs three hits')
  end
end

-- ===== Green Mage: Mana Leech (see the MISMATCH note: the Silence is checked before the per-hit cap) =====
for L = 1, 4 do
  fresh()
  local big = {casterMaxMp=999}
  local function leech(r, t) fresh(); local s = S(J.greenMage, {damage=200, casterMaxMp=999, targetMp=500}); for k, v in pairs(t or {}) do s[k] = v end; return P.decide('hit', s, r) end
  local a = leech(rank(1, L))
  eq(a.drainMp, 200 * (L == 4 and 8 or 5) // 100, 'Mana Leech I-' .. L .. ' drain')
  eq(a.healHp, L >= 3 and a.drainMp or nil, 'Mana Leech I-' .. L .. ' heal')
  local capped = leech(rank(1, L), {casterMaxMp=100})
  eq(capped.drainMp, 2, 'Mana Leech I-' .. L .. ' caps at 2% of the receiver MP')
  -- II: at full MP the drain goes to the lowest-MP allies
  local rich = party(J.greenMage, 4); for i = 2, 4 do rich[i].mp = 5 * i; rich[i].maxMp = 100 end; rich[2].status = {silence=true}
  local b = leech(rank(2, L), {casterMp=999, party=rich})
  local gets = 0; for _, o in ipairs(b.others or {}) do if o.op == 'mp' then gets = gets + 1 end end
  eq(gets, L >= 3 and 2 or 1, 'Mana Leech II-' .. L .. ' recipients')
  eq(others(b, 'remove', 200, ST.silence) == 1, L >= 2, 'Mana Leech II-' .. L .. ' cures Silence')
  eq(b.drainMp, nil, 'Mana Leech II at full MP: the foe loses the MP, nothing is drained to the caster')
  -- III: charge push 4/6/8/10%
  local c = leech(rank(3, L))
  eq(20000 - c.chargeTo, math.floor(30000 * (0.04 + 0.02 * (L - 1))), 'Mana Leech III-' .. L .. ' push')
end

-- ===== Time Mage: Detonate =====
for L = 1, 4 do
  fresh()
  local asked
  local function try(r, t) fresh(); local s = S(J.timeMage, t); s.roll = function(p) asked = p; return true end; return P.decide('hit', s, r) end
  local a = try(rank(1, L), {targetStatus={slow=true}})
  approx(a.targetAdd[ST.stop], 0.5, 'Detonate I-' .. L .. ' Stop')
  eq(asked, L == 4 and 25 or 15, 'Detonate I-' .. L .. ' chance')
  eq(try(rank(1, L), {targetStatus={disable=true}}) ~= nil, true, 'Detonate I-' .. L .. ' Disabled counts')
  eq(try(rank(1, L), {targetStatus={immobilize=true}}) ~= nil, L >= 2, 'Detonate I-' .. L .. ' Immobilized counts from 2')
  eq(try(rank(1, L), {targetStatus={confuse=true}}) ~= nil, L >= 3, 'Detonate I-' .. L .. ' Confused counts from 3')
  eq(try(rank(1, L), {targetStatus={slow=true, stop=true}}), nil, 'a Stopped foe is not Stopped again here')
  local b = try(rank(2, L), {targetStatus={slow=true}})
  eq(others(b, 'add', nil, ST.slow), L == 1 and 1 or 2, 'Detonate II-' .. L .. ' other foes Slowed')
  eq(others(b, 'push') , L >= 3 and 2 or 0, 'Detonate II-' .. L .. ' push')
  approx(the(b, 'add', 901, ST.slow).scale, L == 4 and 1.5 or 1, 'Detonate II-' .. L .. ' Slow length')
  -- III: any party member hitting a Stopped foe (the Time Mage must be in the party)
  fresh()
  local party3 = party(J.timeMage, 3); party3[1].job = J.knight; party3[2].job = J.timeMage
  local c = P.decide('hit', S(J.knight, {targetStatus={stop=true}, party=party3}), rank(3, L))
  approx(c.casterAdd[ST.haste], L == 4 and 0.75 or 0.5, 'Detonate III-' .. L .. ' Haste')
  eq(c.damage ~= nil and near(c.damage, 1.15), L >= 2, 'Detonate III-' .. L .. ' +15%')
  eq(c.targetExtend ~= nil and near(c.targetExtend[ST.stop], 0.10), L >= 3, 'Detonate III-' .. L .. ' stretch')
end

-- ===== Black Mage: Resonance =====
local function cast(job, r, element, t)
  local s = S(job, {element=element, mpCost=30, castId=nil, now=(t or 0)})
  return P.decide('magick', s, r)
end
for L = 1, 4 do
  fresh()
  local clock = 0
  local function c(r, el) clock = clock + 5000000; return P.decide('magick', S(J.blackMage, {element=el, mpCost=30, now=clock}), r) end
  eq(c(rank(1, L), 1), nil, 'Resonance: the first cast does nothing')
  local a = c(rank(1, L), 1)
  approx(a.casterCharge, L == 4 and 0.30 or 0.20, 'Resonance I-' .. L .. ' charge')
  -- one off-element spell is forgiven from level 2
  fresh(); clock = 0
  c(rank(1, L), 1); c(rank(1, L), 2)
  local again = c(rank(1, L), 1)
  eq(again ~= nil and again.casterCharge ~= nil, L >= 2, 'Resonance I-' .. L .. ' forgives one off-element spell')
  fresh(); clock = 0
  c(rank(2, L), 1); c(rank(2, L), 1)
  local third = c(rank(2, L), 1)
  eq(the(third, 'mp', 100).amount, math.max(1, 30 * (L == 4 and 60 or (L == 3 and 40 or 30)) // 100), 'Resonance II-' .. L .. ' refund')
  eq(third.damage ~= nil and near(third.damage, 1.15), L >= 2, 'Resonance II-' .. L .. ' +15%')
  fresh(); clock = 0
  c(rank(3, L), 1); c(rank(3, L), 1)
  local t3 = P.decide('magick', S(J.blackMage, {element=1, mpCost=30, now=clock + 5000000, targetStatus={}}), rank(3, L))
  approx(t3.targetAdd[ST.oil], 0.5, 'Resonance III-' .. L .. ' Oil')
  eq(the(t3, 'add', 901, ST.oil) ~= nil, L >= 2, 'Resonance III-' .. L .. ' other foe Oiled')
  local splash = the(t3, 'hp', 901)
  eq(splash ~= nil and splash.amount == -math.max(1, 114 * (L == 4 and 35 or 25) // 100), L >= 3, 'Resonance III-' .. L .. ' other foe takes 25/35% of the final damage (the +15% included)')
end

-- ===== Chemist: Contagion =====
for L = 1, 4 do
  fresh()
  local asked = {}
  local function spread(r, id, t)
    fresh()
    local s = S(J.chemist, {landed={id}, targetStatus={}, damage=100})
    s.roll = function(p) asked[#asked + 1] = p; return true end
    for k, v in pairs(t or {}) do s[k] = v end
    return P.decide('status', s, r)
  end
  local a = spread(rank(1, L), ST.blind)
  eq(asked[1], L == 4 and 50 or 35, 'Contagion I-' .. L .. ' chance')
  approx(the(a, 'add', 901, ST.blind).scale, L == 1 and 0.75 or 1, 'Contagion I-' .. L .. ' length')
  asked = {}
  spread(rank(1, L), ST.poison)
  eq(asked[1], L >= 3 and (L == 4 and 65 or 50) or (L == 4 and 50 or 35), 'Contagion I-' .. L .. ' Poison chance')
  -- II: the hits heal the most hurt allies
  local hurt = party(J.chemist, 4)
  local b = P.decide('hit', S(J.chemist, {damage=100, party=hurt}), rank(2, L))
  eq(the(b, 'hp', 200).amount, 100 * (L >= 2 and 20 or 10) // 100, 'Contagion II-' .. L .. ' heal')
  eq(others(b, 'hp') , L == 4 and 2 or 1, 'Contagion II-' .. L .. ' allies healed')
  local sick = party(J.chemist, 4); sick[2].status = {poison=true}
  local d = P.decide('hit', S(J.chemist, {damage=100, party=sick}), rank(2, L))
  eq(others(d, 'remove', 200, ST.poison) == 1, L >= 3, 'Contagion II-' .. L .. ' cure')
  -- III: reach and splash
  local reach = P.decide('status', S(J.chemist, {landed={ST.blind}, damage=100}), rank(3, L))
  eq(others(reach, 'add'), L == 4 and 3 or 2, 'Contagion III-' .. L .. ' reach')
  local sp = 0; for _, o in ipairs(reach.others or {}) do if o.op == 'hp' and o.amount == -10 then sp = sp + 1 end end
  eq(sp, L >= 2 and (L == 4 and 3 or 2) or 0, 'Contagion III-' .. L .. ' 10% damage to each foe')
  local rg = P.decide('status', S(J.chemist, {landed={ST.blind}, damage=100, foes={{address=901, status={regen=true}}}}), rank(3, L))
  eq(others(rg, 'remove', 901, ST.regen) == 1, L >= 3, 'Contagion III-' .. L .. ' strips Regen')
end
return 'ok'
'''


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
@unittest.skipUnless(D.have('proficiency', 'hybrid_proficiency'), D.SKIP_REASON)
class InstalledBaseLinesTest(unittest.TestCase):
    def test_every_base_stage_and_level_does_what_its_text_says(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().P = lua.execute(D.module('proficiency'))
        lua.globals().H = lua.execute(D.module('hybrid_proficiency'))
        lua.execute('H.attach(P)')
        self.assertEqual(lua.execute(LUA), 'ok')


if __name__ == '__main__':
    unittest.main()
