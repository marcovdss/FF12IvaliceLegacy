"""A small synthetic battle around the INSTALLED Proficiency runtime, for tests that replay real Forge hook calls.

The runtime and the rule modules are loaded from the game folder (deployed_scripts.py); memory, events, the battle
pack and the Forge helper/class tables are stubs. Nothing here touches a game or a save.

`World(lua)` fills the Lua globals that the tests use:
  R                    the runtime, registered, with a Legacy campaign loaded at Clan rank 12 and all three Writs owned
  unit{...}            makes a battle unit keep (party member or foe) and registers it
  F{...}               makes a Forge formula
  hits, helpers, classes, LOG, works
"""
import deployed_scripts as D

MODULES = ('proficiency', 'proficiency_runtime', 'hybrid_proficiency', 'mp_economy')

SETUP = r'''
printed, boxes, handlers, keys = {}, {}, {}, {}
print = function(s) printed[#printed+1] = s end
local real_load = load
loadfile = function(p, mode, env) return sources[p] and real_load(sources[p], '@'..p, mode, env) or nil end
local wide = function(s) return (s:gsub('.', '%0\0')) end
local mem = {[0x120000]='MZ', [0x120180]='PE\0\0\x64\x86', [0xD113D4]='LEGCY_\0', [0xD12200]=wide('%sLEGCY_%03d\0')}
memory = {u8={}, u32=setmetatable({}, {__index=function() return 0 end}), u64={}, arg={s32='s32', u32='u32'},
          readArray=function(a, n) return (mem[a] or ''):sub(1, n) end,
          execute=function(fn, ret, t1, id, t2, v2, t3, v3)
            if fn == 0x2FD970 then return keys[id] and 1 or 0 end
            if fn == 0x2FD420 then keys[id] = v2 == 1; return v2 end
            return 0
          end}
dialog = {amese={open=function() return 1 end}, aaske={open=function() return 1 end}}
NOW, ticks = 1000000000, {}
event = {registerEventSync=function(name, fn) handlers[name] = fn return true end,
         registerEventAsync=function(name, fn) handlers[name] = fn return true end,
         registerLoadHandler=function(_, fn) handlers.load = fn return true end,
         registerSaveHandler=function(_, fn) handlers.save = fn return true end,
         getClock=function() return NOW end,
         executeAfterMs=function(ms, fn) ticks[#ticks+1] = fn end}
function runTicks() local t = ticks; ticks = {}; for _, fn in ipairs(t) do pcall(fn) end end
R = real_load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime', 't', _ENV)()
assert(R.register())
for _, id in ipairs({0x81F0, 0x81F1, 0x81F2}) do keys[id] = true end
handlers.load('C:/saves/LEGCY_000', {schema=1, campaign='LEGCY_000', claimedRank=RANK or 12})
assert(R.effective() == (RANK or 12), 'campaign not active: ' .. tostring(R.effective()))
P = R.Proficiency
H = R.Hybrid

STATUS = P.STATUS
local ID_NAME = {}
for name, id in pairs(STATUS) do ID_NAME[id] = name end
function flags(set)
  local f = setmetatable({}, {__index=function() return 0 end})
  for k, on in pairs(set or {}) do
    local v = on and 1 or 0
    if type(k) == 'string' then f[k] = v; f[STATUS[k] or -1] = v else f[k] = v; if ID_NAME[k] then f[ID_NAME[k]] = v end end
  end
  return f
end
local function record(t)
  t = t or {}
  return {flags1={isNegative=t.negative or 0, isNotApplicableToFoes=t.notFoes or 0, isRefreshable=1}, absentStatusEffects=flags()}
end
-- Negative statuses: the ailments and Lure's neighbours; Libra cannot sit on a foe.
local NEG = {[3]=1, [4]=1, [5]=1, [7]=1, [8]=1, [9]=1, [10]=1, [11]=1, [13]=1, [14]=1, [15]=1, [16]=1, [27]=1}
bpack = {section13={count=64}, section14={}, section58={}, section15=setmetatable({}, {__index=function(_, id)
  return record({negative=NEG[id], notFoes=(id == 30) and 1 or 0})
end})}
for i = 0, 63 do bpack.section13[i] = {category=1, chargeTime=30} end

keeps, works, LOG = {}, {}, {added={}, removed={}, mp={}, hp={}, nums={}}
function unit(t)
  local u = {type=t.type or 0, identifier=t.id or 0, address=t.address, job1=t.job1 or 0, job2=t.job2 or 255,
             maxHp=t.maxHp or 1000, currentHp=t.hp or t.maxHp or 1000, maxMp=t.maxMp or 100, currentMp=t.mp or 50,
             level=t.level or 30, weapon=t.weapon, magickPower=t.magick or 60,
             temporaryStatusEffects=flags(t.status), permanentStatusEffectImmunities=flags(),
             permanentStatusEffects=flags(), statusEffectDurations=flags(), temporaryAugments=flags(),
             permanentAugments=flags()}
  for id, d in pairs(t.has or {}) do u.temporaryStatusEffects[id] = 1; u.statusEffectDurations[id] = d end
  for _, id in ipairs(t.immune or {}) do u.permanentStatusEffectImmunities[id] = 1 end
  for k in pairs(t.augments or {}) do u.permanentAugments[k] = 1 end
  keeps[u.address] = u
  if t.type == 1 or t.charge then
    works[u.address] = {address=u.address + 1, currentChargeTime=t.charge or 0, requiredChargeTime=t.required or 30000,
                        lastChargeTime=t.charge or 0, ardUnit={name=t.name or 'imp'}}
  end
  return u
end
function party(id, t) t.id, t.type = id, 0; t.address = 1000 + 8 + id * 0x1C8; return unit(t) end
memory.u64[0x02EBF190] = 1000

helpers = {
  getActivePartyMember=function(i) return PARTY_IDS and PARTY_IDS[i + 1] or -1 end,
  getBattleUnitKeep=function(id) return 1000 + 8 + id * 0x1C8 end,
  getBattleUnitWork=function(a) return a end,
  getRandomNumber=function() return 0 end,
  getStatusEffectDuration=function() return 100 end,
  getStatusEffectTickDuration=function() return 0 end,
  getStatusEffectsMatch=function() return false end,
  addStatusEffect=function(src, u, id, d)
    LOG.added[#LOG.added + 1] = {unit=u, id=id, d=d}
    local k = keeps[u]
    if k then k.temporaryStatusEffects[id] = 1; k.statusEffectDurations[id] = d; if ID_NAME[id] then k.temporaryStatusEffects[ID_NAME[id]] = 1 end end
  end,
  removeStatusEffect=function(u, id)
    LOG.removed[#LOG.removed + 1] = {unit=u, id=id}
    local k = keeps[u]
    if k then k.temporaryStatusEffects[id] = 0; if ID_NAME[id] then k.temporaryStatusEffects[ID_NAME[id]] = 0 end end
  end,
  modifyMp=function(u, n)
    LOG.mp[#LOG.mp + 1] = {unit=u, amount=n}
    local k = keeps[u]; if k then k.currentMp = math.max(0, math.min(k.maxMp, k.currentMp + n)) end
  end,
  modifyHp=function(src, u, n)
    LOG.hp[#LOG.hp + 1] = {unit=u, amount=n}
    local k = keeps[u]; if k then k.currentHp = math.max(0, math.min(k.maxHp, k.currentHp + n)) end
  end,
}
classes = {getBattleUnitKeep=function(a) return keeps[a] end,
           getBattleUnitWork=function(a) return works[a] or {address=0} end}

function plus(t)
  t = t or {}
  return {statusEffects=flags(t.status), addedStatusEffects=flags(), removedStatusEffects=flags(),
          statusEffectDurations=setmetatable({}, {__index=function() return -1 end}),
          removedHp=t.removedHp or 0, addedHp=t.addedHp or 0, identifier=t.identifier or 0, battleUnitWork=t.work,
          hpUpdateState=0, mpUpdateState=0, addedMp=0, removedMp=0}
end
function F(t)
  t = t or {}
  local f = {caster=plus(t.caster), target=plus(t.target), action=t.action or 150, causeType=t.causeType or 0,
             criticalState=t.critical and 1 or 0, outcomeType=t.outcome or 0,
             initialTarget={address=t.initial or 0}, initialTargetPointer=t.initial or 0}
  return f
end
function mpTotal() local n = 0 for _, m in ipairs(LOG.mp) do n = n + m.amount end return n end
function mpGain() local n = 0 for _, m in ipairs(LOG.mp) do if m.amount > 0 then n = n + m.amount end end return n end
function reset() LOG.added, LOG.removed, LOG.mp, LOG.hp = {}, {}, {}, {} end
'''


def world(lua, rank=12, overrides=None, loader=None):
    """`loader(name)` returns a module's text (default: the installed file); pass one to replay another copy."""
    read = loader or D.module
    sources = {f'scripts/IvaliceLegacy/{n}.lua': read(n) for n in MODULES}
    sources['scripts/IvaliceLegacy/clan_rank.lua'] = 'return {MARKS_FN=0, read=function() return {rank=12} end}'
    sources['scripts/DynamicFoes/Foes.lua'] = 'return {bossy="boss", marky="mark", raregame="rare", imp="common"}'
    for name, text in (overrides or {}).items():
        sources[f'scripts/IvaliceLegacy/{name}.lua'] = text
    lua.globals().sources = lua.table_from(sources)
    lua.globals().RANK = rank
    lua.execute(SETUP)
