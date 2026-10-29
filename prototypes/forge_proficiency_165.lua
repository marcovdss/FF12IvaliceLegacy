-- Forge config/functions/165.lua: Proficiency. 165 runs on every physical hit after the damage is known (it is the
-- attack-boost step), for every weapon. The wrapper runs the original unchanged, then lets the party member's current
-- job line add its effect through Forge's own end-of-hit step (323 caster / 324 target: statuses, durations, HP/MP)
-- or the target's charge time (proven to persist, 2026-10-28).
-- Montblanc hands out the ranks: when the party leaves the Clan hall with a rank not yet rewarded, he speaks in a
-- native dialogue window and the rewarded rank is stored beside the Legacy save.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/165.lua', 't', _ENV))()
local Proficiency = assert(loadfile('scripts/IvaliceLegacy/proficiency.lua', 't', _ENV))()
local ClanRank = assert(loadfile('scripts/IvaliceLegacy/clan_rank.lua', 't', _ENV))()

local CLAN_HALL = 302
local LOCATION = 0x021654C4
local SAVE_ID = 'IvaliceLegacyProficiency'

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

-- Campaign state: active only after a strict LEGCY_ddd load in Legacy mode; any other load or new game clears it.
local campaign, claimed, location, logged, rankLogged = nil, 0, nil, 0, false
local LOG_LIMIT = 64
local function log(line)
  if logged < LOG_LIMIT then logged = logged + 1; print('Legacy Proficiency: ' .. line) end
end

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

-- Leaving the Clan hall claims every rank earned since the last claim.
local function claim()
  if not campaign or not legacyMode() then return end
  local rank = clanRank()
  if type(rank) ~= 'number' then log('clan rank unreadable; nothing claimed') return end
  if rank <= claimed then return end
  local from = claimed
  claimed = rank
  local levels = Proficiency.levels(claimed)
  log(string.format('campaign=%s rank %d -> %d claimed; levels I=%d II=%d III=%d', campaign, from, rank,
    levels[1], levels[2], levels[3]))
  speak(Proficiency.speech(from, rank))
end

local function reset()
  campaign, claimed, logged, rankLogged = nil, 0, 0, false
  Proficiency.reset()
end

local ok = event.registerEventSync('onSaveLoad', function() reset() return true end) == true
ok = event.registerEventSync('onNewGameStart', function() reset() return true end) == true and ok
ok = event.registerLoadHandler(SAVE_ID, function(path, data)
  reset()
  if type(path) ~= 'string' or path:find('[%z\r\n]') then return end
  local name = path:match('([^/\\]+)$')
  if not (name and name:match('^LEGCY_%d%d%d$') and legacyMode()) then return end
  campaign = name
  -- The stored rank belongs to this campaign; a missing or foreign record starts at 0 and Montblanc re-awards
  -- the ranks already earned when the party next leaves his hall.
  if type(data) == 'table' and data.schema == 1 and data.campaign == name and type(data.claimedRank) == 'number'
      and data.claimedRank % 1 == 0 and data.claimedRank >= 0 and data.claimedRank <= Proficiency.MAX_RANK then
    claimed = data.claimedRank
  end
  location = memory.u32[LOCATION]
  local levels = Proficiency.levels(claimed)
  log(string.format('campaign=%s claimed rank %d; levels I=%d II=%d III=%d', name, claimed, levels[1], levels[2],
    levels[3]))
end) == true and ok
ok = event.registerSaveHandler(SAVE_ID, function(path)
  if not campaign then return nil end
  return {schema = 1, campaign = campaign, claimedRank = claimed}
end) == true and ok
ok = event.registerEventAsync('onMapJump', function(to)
  local from = location
  location = to
  if campaign and not rankLogged then
    rankLogged = true
    log(string.format('campaign=%s clan rank %s (claimed %d), location %d', campaign, tostring(clanRank()), claimed, to))
  end
  if from == CLAN_HALL and to ~= CLAN_HALL then claim() end
end) == true and ok
if not ok then
  print('Legacy Proficiency: callback registration failed; Proficiency off')
  return original
end
print('Legacy Proficiency: ' .. Proficiency.version .. ' ready (stage I: Hunter, Warrior, Lancer, Samurai, Gunner, '
  .. 'Time Mage, Green Mage)')

local function statuses(plus)
  local out = {}
  for name in pairs(Proficiency.STATUS) do if plus.statusEffects[name] == 1 then out[name] = true end end
  for _, name in ipairs(Proficiency.HINDERED) do if plus.statusEffects[name] == 1 then out[name] = true end end
  return out
end

local function partyActor(caster)
  local id = caster.identifier
  local root = memory.u64[0x02EBF190]
  if type(root) ~= 'number' or root <= 0 or type(id) ~= 'number' or id % 1 ~= 0 or id < 0 or id > 5
    or caster.address ~= root + 8 + id * 0x1C8 then return nil end
  return id
end

local function sample(formula, caster, target, helpers)
  if caster.type ~= 0 or caster.job2 ~= Proficiency.NO_JOB then return nil end  -- hybrids get their own lines later
  local actor = partyActor(caster)
  if not actor then return nil end
  local damage = formula.target.removedHp
  local work = formula.target.battleUnitWork
  local category = nil
  if type(caster.weapon) == 'number' and caster.weapon >= 0x1000 and caster.weapon - 0x1000 < bpack.section13.count then
    category = bpack.section13[caster.weapon - 0x1000].category
  end
  return {job = caster.job1, actorId = actor, targetId = formula.target.identifier, foe = target.type == 1,
          damage = damage, kill = damage ~= -1 and damage >= target.currentHp,
          bystander = formula.initialTargetPointer ~= 0 and formula.initialTargetPointer ~= target.address,
          category = category, targetStatus = statuses(formula.target), casterStatus = statuses(formula.caster),
          targetMp = target.currentMp,
          charge = work.address ~= 0 and {current = work.currentChargeTime, required = work.requiredChargeTime,
                                           last = work.lastChargeTime} or nil,
          roll = function(percent) return helpers.getRandomNumber(100) < percent end}
end

local function grant(plus, unit, helpers, statusId, durationScale)
  plus.addedStatusEffects[statusId] = 1
  if durationScale ~= 1 then
    plus.statusEffectDurations[statusId] = math.max(1, math.floor(helpers.getStatusEffectDuration(unit, statusId)
      * durationScale))
  end
end

local function apply(a, formula, caster, target, helpers)
  if a.damage and formula.target.removedHp ~= -1 then
    formula.target.removedHp = math.floor(formula.target.removedHp * a.damage)
  end
  for id, scale in pairs(a.casterAdd or {}) do grant(formula.caster, caster.address, helpers, id, scale) end
  for id, scale in pairs(a.targetAdd or {}) do grant(formula.target, caster.address, helpers, id, scale) end
  for _, id in ipairs(a.casterRemove or {}) do formula.caster.removedStatusEffects[id] = 1 end
  for _, id in ipairs(a.targetRemove or {}) do formula.target.removedStatusEffects[id] = 1 end
  if a.drainMp then
    formula.target.mpUpdateState, formula.target.addedMp, formula.target.removedMp = 1, 0, a.drainMp
    formula.caster.mpUpdateState, formula.caster.addedMp, formula.caster.removedMp = 1, a.drainMp, 0
  end
  if a.healHp and formula.caster.hpUpdateState ~= 1 then
    formula.caster.hpUpdateState, formula.caster.addedHp, formula.caster.removedHp = 1, a.healHp, 0
  end
  if a.chargeTo then formula.target.battleUnitWork.currentChargeTime = a.chargeTo end
end

local errorLogged = false
return function(formula, caster, target, functions, classes, helpers)
  original(formula, caster, target, functions, classes, helpers)
  if not campaign or claimed < 1 then return end
  local ok, err = pcall(function()
    local s = sample(formula, caster, target, helpers)
    if not s then return end
    local a = Proficiency.decide(s, claimed)
    if not a then return end
    apply(a, formula, caster, target, helpers)
    log(string.format('actor=%d job=%d level=%d %s target=%d', s.actorId, s.job, s.level, a.why, s.targetId))
  end)
  if not ok and not errorLogged then
    errorLogged = true
    pcall(print, 'Legacy Proficiency: error=' .. tostring(err):gsub('[%z\r\n]', ' '))
  end
end
