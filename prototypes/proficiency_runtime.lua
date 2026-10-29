-- Proficiency runtime shared by the Forge adapters (165 physical hits, 170 magick, 110 healing, 182 statuses,
-- 240 guard reactions, 22 evasion). Loaded once and kept in _G so all adapters see one campaign state.
-- Montblanc hands out the ranks: leaving the Clan hall (location 302) with an unrewarded rank makes him speak in
-- native dialogue windows (Lua loader dialog plugin), and the rewarded rank is stored beside the Legacy save.
local R = {}
local Proficiency = assert(loadfile('scripts/IvaliceLegacy/proficiency.lua', 't', _ENV))()
local ClanRank = assert(loadfile('scripts/IvaliceLegacy/clan_rank.lua', 't', _ENV))()
local P = Proficiency
R.Proficiency = P

local CLAN_HALL = 302
local LOCATION = 0x021654C4
local SAVE_ID = 'IvaliceLegacyProficiency'
local ELEMENTS = {'fire', 'lightning', 'ice', 'earth', 'water', 'wind', 'holy', 'dark'}
local FOE_MEMORY = 8000000  -- clock units (about 30 s) a foe stays "engaged" after its last hit

local function expected(address, text, wide)
  local count = #text * (wide and 2 or 1)
  local raw = memory.readArray(address, count)
  if type(raw) ~= 'table' and type(raw) ~= 'string' then return false end
  if #raw ~= count then return false end
  for i = 1, count do
    local byte = type(raw) == 'string' and raw:byte(i) or raw[i]
    local value = wide and (i % 2 == 0 and 0 or text:byte((i + 1) // 2)) or text:byte(i)
    if byte ~= value then return false end
  end
  return true
end

local function legacyMode()
  return expected(0x120000, 'MZ', false)
    and expected(0x120180, 'PE\0\0\x64\x86', false)
    and expected(0xD113D4, 'LEGCY_\0', false)
    and expected(0xD12200, '%sLEGCY_%03d\0', true)
end

local campaign, claimed, location, logged, rankLogged = nil, 0, nil, 0, false
local foes = {}
local LOG_LIMIT = 120
local function log(line)
  if logged < LOG_LIMIT then logged = logged + 1; print('Legacy Proficiency: ' .. line) end
end
R.log = log
function R.active() return campaign ~= nil and claimed >= 1 end
function R.claimed() return claimed end

local function clanRank()
  local ok, clan = pcall(ClanRank.read, {
    u8 = function(a) return memory.u8[a] end, u32 = function(a) return memory.u32[a] end,
    marks = function() return memory.execute(ClanRank.MARKS_FN, arg.s32) end})
  if ok and type(clan) == 'table' then return clan.rank end
  return nil
end

local function speak(boxes)
  if type(dialog) ~= 'table' or type(dialog.amese) ~= 'table' then
    for _, box in ipairs(boxes) do log('speech (no dialog service): ' .. box:gsub('\n', ' ')) end
    return
  end
  for _, box in ipairs(boxes) do
    local h, reason = dialog.amese.open{speaker = 'Montblanc', body = box, queue = true, lockPlayer = true}
    if not h then log('dialog refused: ' .. tostring(reason)) end
  end
end

local function claim()
  if not campaign or not legacyMode() then return end
  local rank = clanRank()
  if type(rank) ~= 'number' then log('clan rank unreadable; nothing claimed') return end
  if rank <= claimed then return end
  local from = claimed
  claimed = rank
  local L = P.levels(claimed)
  log(string.format('campaign=%s rank %d -> %d claimed; levels I=%d II=%d III=%d', campaign, from, rank, L[1], L[2], L[3]))
  speak(P.speech(from, rank))
end

local function reset()
  campaign, claimed, logged, rankLogged, foes = nil, 0, 0, false, {}
  P.reset()
end

function R.register()
  local ok = event.registerEventSync('onSaveLoad', function() reset() return true end) == true
  ok = event.registerEventSync('onNewGameStart', function() reset() return true end) == true and ok
  ok = event.registerLoadHandler(SAVE_ID, function(path, data)
    reset()
    if type(path) ~= 'string' or path:find('[%z\r\n]') then return end
    local name = path:match('([^/\\]+)$')
    if not (name and name:match('^LEGCY_%d%d%d$') and legacyMode()) then return end
    campaign = name
    if type(data) == 'table' and data.schema == 1 and data.campaign == name and type(data.claimedRank) == 'number'
        and data.claimedRank % 1 == 0 and data.claimedRank >= 0 and data.claimedRank <= P.MAX_RANK then
      claimed = data.claimedRank
    end
    location = memory.u32[LOCATION]
    local L = P.levels(claimed)
    log(string.format('campaign=%s claimed rank %d; levels I=%d II=%d III=%d', name, claimed, L[1], L[2], L[3]))
  end) == true and ok
  ok = event.registerSaveHandler(SAVE_ID, function()
    if not campaign then return nil end
    return {schema = 1, campaign = campaign, claimedRank = claimed}
  end) == true and ok
  ok = event.registerEventAsync('onMapJump', function(to)
    local from = location
    location = to
    foes = {}
    if campaign and not rankLogged then
      rankLogged = true
      log(string.format('campaign=%s clan rank %s (claimed %d), location %d', campaign, tostring(clanRank()), claimed, to))
    end
    if from == CLAN_HALL and to ~= CLAN_HALL then claim() end
  end) == true and ok
  return ok
end

-- ---------- snapshots ----------
local function statusSet(flags)
  local out = {}
  for name in pairs(P.STATUS) do if flags[name] == 1 then out[name] = true end end
  for _, name in ipairs(P.HINDERED) do if flags[name] == 1 then out[name] = true end end
  return out
end
R.statusSet = statusSet

local function clock() return event.getClock and event.getClock() or 0 end

function R.partyActor(unit)
  local id = unit.identifier
  local root = memory.u64[0x02EBF190]
  if type(root) ~= 'number' or root <= 0 or type(id) ~= 'number' or id % 1 ~= 0 or id < 0 or id > 5
    or unit.address ~= root + 8 + id * 0x1C8 then return nil end
  return id
end

function R.party(classes, helpers)
  local out = {}
  for i = 0, 3 do
    local id = helpers.getActivePartyMember(i)
    if type(id) == 'number' and id >= 0 and id <= 39 then
      local addr = helpers.getBattleUnitKeep(id)
      local k = classes.getBattleUnitKeep(addr)
      if k.currentHp > 0 and k.maxHp > 0 then
        out[#out + 1] = {address = addr, job = k.job2 == P.NO_JOB and k.job1 or -1,
                         hpPct = 100 * k.currentHp // k.maxHp, mp = k.currentMp, maxMp = k.maxMp,
                         status = statusSet(k.temporaryStatusEffects)}
      end
    end
  end
  return out
end

function R.seeFoe(address)
  if type(address) == 'number' and address > 0 then foes[address] = clock() end
end

-- Other engaged, living foes (not `except`), most recently seen first.
function R.foes(classes, except)
  local now, out, stale = clock(), {}, {}
  for addr, seen in pairs(foes) do
    if now - seen > FOE_MEMORY then stale[#stale + 1] = addr
    elseif addr ~= except then
      local k = classes.getBattleUnitKeep(addr)
      if k.type == 1 and k.currentHp > 0 then
        out[#out + 1] = {address = addr, seen = seen, status = statusSet(k.temporaryStatusEffects)}
      end
    end
  end
  for _, addr in ipairs(stale) do foes[addr] = nil end
  table.sort(out, function(x, y) return x.seen > y.seen end)
  return out
end

-- Snapshot for one party member (`unit` = its battle unit keep, `flags` = its status flags).
function R.base(unit, flags, classes, helpers)
  if unit.type ~= 0 or unit.job2 ~= P.NO_JOB then return nil end  -- hybrids get their own lines later
  local actor = R.partyActor(unit)
  if not actor then return nil end
  return {job = unit.job1, actorId = actor, self = unit.address, party = R.party(classes, helpers),
          casterStatus = statusSet(flags), casterHpPct = unit.maxHp > 0 and 100 * unit.currentHp // unit.maxHp or 100,
          casterMp = unit.currentMp, casterMaxMp = unit.maxMp, selfMaxHp = unit.maxHp,
          roll = function(percent) return helpers.getRandomNumber(100) < percent end}
end

function R.element(action)
  local mask, rec = 0, bpack.section14[action]
  for i, name in ipairs(ELEMENTS) do if rec.elements[name] == 1 then mask = mask | (1 << (i - 1)) end end
  return mask, rec.mpOrMistCost
end

-- ---------- applying ----------
local function grant(plus, unit, helpers, id, scale)
  plus.addedStatusEffects[id] = 1
  if scale ~= 1 then
    plus.statusEffectDurations[id] = math.max(1, math.floor(helpers.getStatusEffectDuration(unit, id) * scale))
  end
end

local function pushCharge(classes, helpers, unit, fraction)
  local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(unit))
  if w.address == 0 then return end
  local cur, req, last = w.currentChargeTime, w.requiredChargeTime, w.lastChargeTime
  if cur > 0 and cur < req and last == cur then w.currentChargeTime = math.max(1, cur - math.floor(req * fraction)) end
end

-- Apply an action table for party member `member`. Formula fields are used only when `formula` is given and the
-- member is that formula's caster; everything in a.others goes through Forge helpers with the member as source.
function R.apply(a, formula, member, classes, helpers)
  if formula then
    if a.damage and formula.target.removedHp ~= -1 then
      formula.target.removedHp = math.floor(formula.target.removedHp * a.damage)
    end
    for id, scale in pairs(a.casterAdd or {}) do grant(formula.caster, member.address, helpers, id, scale) end
    for id, scale in pairs(a.targetAdd or {}) do grant(formula.target, member.address, helpers, id, scale) end
    for _, id in ipairs(a.casterRemove or {}) do formula.caster.removedStatusEffects[id] = 1 end
    for _, id in ipairs(a.targetRemove or {}) do formula.target.removedStatusEffects[id] = 1 end
    if a.drainMp then
      formula.target.mpUpdateState, formula.target.addedMp, formula.target.removedMp = 1, 0, a.drainMp
      formula.caster.mpUpdateState, formula.caster.addedMp, formula.caster.removedMp = 1, a.drainMp, 0
    elseif a.targetMpLoss then
      formula.target.mpUpdateState, formula.target.addedMp, formula.target.removedMp = 1, 0, a.targetMpLoss
    end
    if a.healHp and formula.caster.hpUpdateState ~= 1 then
      formula.caster.hpUpdateState, formula.caster.addedHp, formula.caster.removedHp = 1, a.healHp, 0
    end
    if a.chargeTo then formula.target.battleUnitWork.currentChargeTime = a.chargeTo end
  end
  if a.casterCharge then
    local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(member.address))
    if w.address ~= 0 and w.requiredChargeTime > 0 then
      w.currentChargeTime = math.min(w.requiredChargeTime - 1,
        w.currentChargeTime + math.floor(w.requiredChargeTime * a.casterCharge))
    end
  end
  for _, o in ipairs(a.others or {}) do
    if o.op == 'add' then
      local d = helpers.getStatusEffectDuration(member.address, o.id)
      helpers.addStatusEffect(member.address, o.unit, o.id, math.max(1, math.floor(d * (o.scale or 1))),
        helpers.getStatusEffectTickDuration(o.id))
    elseif o.op == 'remove' then helpers.removeStatusEffect(o.unit, o.id)
    elseif o.op == 'mp' then helpers.modifyMp(o.unit, o.amount)
    elseif o.op == 'hp' then helpers.modifyHp(member.address, o.unit, o.amount, 0, 0, 0)
    elseif o.op == 'push' then pushCharge(classes, helpers, o.unit, o.fraction) end
  end
end

local errorLogged = {}
function R.run(name, fn)
  local ok, err = pcall(fn)
  if not ok and not errorLogged[name] then
    errorLogged[name] = true
    pcall(print, 'Legacy Proficiency: ' .. name .. ' error=' .. tostring(err):gsub('[%z\r\n]', ' '))
  end
end

function R.report(kind, s, a)
  local keys = {}
  for k in pairs(a) do keys[#keys + 1] = k end
  table.sort(keys)
  log(string.format('%s actor=%s job=%s L=%d/%d/%d %s', kind, tostring(s.actorId), tostring(s.job), s.L[1], s.L[2],
    s.L[3], table.concat(keys, ',')))
end

-- ---------- hooks (each Forge adapter calls one after its original function) ----------

-- 165: every physical hit after damage. Party member hitting a foe gives 'hit'; a foe dropping a party member to
-- critical HP gives the Knight's 'ally' reaction.
function R.hook165(formula, caster, target, classes, helpers)
  if target.type == 1 then R.seeFoe(target.address) end
  if caster.type == 1 then R.seeFoe(caster.address) end
  if not R.active() then return end
  if caster.type == 0 and target.type == 1 then
    local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
    if not s then return end
    local damage, work = formula.target.removedHp, formula.target.battleUnitWork
    local category = nil
    if type(caster.weapon) == 'number' and caster.weapon >= 0x1000 and caster.weapon - 0x1000 < bpack.section13.count then
      category = bpack.section13[caster.weapon - 0x1000].category
    end
    s.targetId, s.foe, s.target, s.damage = formula.target.identifier, true, target.address, damage
    s.kill = damage ~= -1 and damage >= target.currentHp
    s.bystander = formula.initialTargetPointer ~= 0 and formula.initialTargetPointer ~= target.address
    s.category, s.targetStatus, s.targetMp = category, statusSet(formula.target.statusEffects), target.currentMp
    if work.address ~= 0 then
      s.charge = {current = work.currentChargeTime, required = work.requiredChargeTime, last = work.lastChargeTime}
    end
    s.foes = R.foes(classes, target.address)
    local a = P.decide('hit', s, claimed)
    if a then R.apply(a, formula, caster, classes, helpers); R.report('hit', s, a) end
  elseif caster.type == 1 and target.type == 0 and formula.target.removedHp ~= -1 and target.maxHp > 0 then
    local quarter = target.maxHp // 4
    if target.currentHp > quarter and target.currentHp - formula.target.removedHp <= quarter then
      for _, p in ipairs(R.party(classes, helpers)) do
        if p.job == P.JOB.knight and p.address ~= target.address then
          local k = classes.getBattleUnitKeep(p.address)
          local s = R.base(k, k.temporaryStatusEffects, classes, helpers)
          local a = s and P.decide('ally', s, claimed)
          if a then R.apply(a, nil, k, classes, helpers); R.report('ally', s, a) end
        end
      end
    end
  end
end

-- 240: after the hit/dodge/parry/block/counter decision. A party member attacked by a foe gives 'guard'.
local OUTCOME = {[1] = 'dodge', [2] = 'dodge', [3] = 'block', [4] = 'block', [5] = 'parry'}
function R.hook240(formula, caster, target, classes, helpers)
  if not R.active() or caster.type ~= 1 or target.type ~= 0 then return end
  local s = R.base(target, formula.target.statusEffects, classes, helpers)
  if not s then return end
  s.attacker = caster.address
  if s.job == P.JOB.mystic then
    if formula.counterState ~= 1 then return end
    s.outcome = 'counter'
  else
    s.outcome = OUTCOME[formula.outcomeType]
    if not s.outcome then return end
  end
  local a = P.decide('guard', s, claimed)
  if a then R.apply(a, nil, target, classes, helpers); R.report('guard', s, a) end
end

-- 110: healing amount. A party White Mage healing an ally past full HP gives 'heal'.
function R.hook110(formula, caster, target, classes, helpers)
  if not R.active() or caster.type ~= 0 or target.type ~= 0 or formula.target.addedHp == -1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.whiteMage then return end
  s.target = target.address
  s.overflow = math.floor(formula.target.addedHp) - (target.maxHp - target.currentHp)
  local a = P.decide('heal', s, claimed)
  if a then R.apply(a, formula, caster, classes, helpers); R.report('heal', s, a) end
end

-- 170: magick damage (after the damage is known). A party Black Mage gives 'magick'.
function R.hook170(formula, caster, target, classes, helpers)
  if target.type == 1 then R.seeFoe(target.address) end
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.blackMage then return end
  s.element, s.mpCost = R.element(formula.action)
  s.foe, s.target = true, target.address
  local a = P.decide('magick', s, claimed)
  if a then R.apply(a, formula, caster, classes, helpers); R.report('magick', s, a) end
end

-- 182: on-hit statuses. `before` = the target's added-status flags before the original ran.
local DISEASE = 16
function R.hook182(formula, caster, target, classes, helpers, before)
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.chemist then return end
  s.landed = {}
  for _, name in ipairs(P.AILMENTS) do
    local id = P.STATUS[name] or (name == 'disease' and DISEASE or nil)
    if id and formula.target.addedStatusEffects[id] == 1 and not before[id] then s.landed[#s.landed + 1] = id end
  end
  if #s.landed == 0 then return end
  s.foe, s.target, s.damage = true, target.address, formula.target.removedHp
  s.foes = R.foes(classes, target.address)
  local a = P.decide('status', s, claimed)
  if a then R.apply(a, formula, caster, classes, helpers); R.report('status', s, a) end
end

-- 22: evasion setup of a physical attack. A party Samurai's attack during Haste gives 'aim'.
function R.hook22(formula, caster, target, classes, helpers)
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.samurai then return end
  local a = P.decide('aim', s, claimed)
  if a and a.evade then
    if a.evade.weapon then formula.evadeWeapon = 0 end
    if a.evade.shield then formula.evadeShield = 0 end
    if a.evade.parry then formula.evadeParry = 0 end
  end
end

-- Wrap one Forge function: the original always runs first; the hook can never stop Forge.
function R.wrap(n, original)
  local hook = R['hook' .. n]
  return function(formula, caster, target, functions, classes, helpers)
    local before = nil
    if n == 182 then
      before = {}
      for i = 0, 31 do if formula.target.addedStatusEffects[i] == 1 then before[i] = true end end
    end
    original(formula, caster, target, functions, classes, helpers)
    R.run('hook' .. n, function() hook(formula, caster, target, classes, helpers, before) end)
  end
end

return R
