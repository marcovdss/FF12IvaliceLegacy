-- Offline prototype only. No loader registration, actor/equipment writes or default rules.
local M = {}
local STATUS_BITS = {blind=7, silence=9, slow=15}
local function integer(v, lo, hi)
  return type(v)=='number' and v%1==0 and v>=lo and v<=hi
end

local function denseArray(value)
  if type(value)~='table' or getmetatable(value)~=nil then return false end
  local count,highest=0,0
  for key in next,value do
    if not integer(key,1,math.maxinteger) then return false end
    count=count+1
    if key>highest then highest=key end
  end
  return count==highest
end

function M.classKey(first, second)
  if not integer(first,0,11) then return nil end
  if second==nil or second==255 then return string.format('%d',first) end
  if not integer(second,0,11) or second==first then return nil end
  if second<first then first,second=second,first end
  return string.format('%d:%d',first,second)
end

local function validClassKey(key)
  if type(key)~='string' then return false end
  local first,second=key:match('^(%d+):(%d+)$')
  if not first then first=key:match('^(%d+)$') end
  return first~=nil and M.classKey(tonumber(first),second and tonumber(second))==key
end

function M.resolve(caster, context, ledger, rules, slot)
  if (type(caster)~='table' and type(caster)~='userdata') or
     type(context)~='table' or slot~='weapon' or context.kind~='main-party' or
     not integer(context.actorId,0,5) or context.actorId~=caster.identifier or
     caster.weapon~=4206 then return nil end
  local key=M.classKey(caster.job1,caster.job2)
  if not key or type(ledger)~='table' or ledger.schema~=1 or
     type(ledger.characters)~='table' or not denseArray(rules) then return nil end
  local character=ledger.characters[string.format('%d',context.actorId)]
  local owned=type(character)=='table' and character[key] or nil
  if not denseArray(owned) then return nil end
  local granted={}
  for _,id in ipairs(owned) do
    if type(id)~='string' or granted[id] then return nil end
    granted[id]=true
  end
  local result,seen=nil,{}
  for _,rule in ipairs(rules) do
    if type(rule)~='table' or type(rule.id)~='string' or #rule.id>96 or
       not rule.id:match('^[a-z][a-z0-9_.-]*$') or seen[rule.id] or
       not validClassKey(rule.classKey) or not STATUS_BITS[rule.status] or
       not integer(rule.onHitRate,1,100) then return nil end
    seen[rule.id]=true
    if granted[rule.id] and rule.classKey==key then
      if result then return nil end -- no implicit stacking or arbitrary winner
      result={ruleId=rule.id,classKey=key,statusMask=1<<STATUS_BITS[rule.status],
              status=rule.status,onHitRate=rule.onHitRate}
    end
  end
  return result
end

local function decision(formula, caster, deps)
  -- Restrict this proof to ordinary Attack with the native Dagger formula.
  if formula.action~=150 or formula.identifier~=21 then return nil end
  -- A new roll must not increase the rate of an existing native status payload.
  if deps.nativeRate(caster)~=0 or deps.nativeMask(caster)~=0 then return nil end
  return M.resolve(caster,deps.context(caster),deps.ledger,deps.rules,'weapon')
end

function M.wrap171(original, deps)
  return function(formula,caster,target,functions,classes,helpers)
    original(formula,caster,target,functions,classes,helpers)
    local effect=decision(formula,caster,deps)
    if effect then formula.onHitRate=effect.onHitRate end
  end
end

function M.wrap182(original, deps)
  return function(formula,caster,target,functions,classes,helpers)
    local effect=decision(formula,caster,deps)
    if not effect or formula.onHitRate~=effect.onHitRate then
      return original(formula,caster,target,functions,classes,helpers)
    end
    local proxy=setmetatable({}, {
      __index=function(_,name)
        if name=='getEquipmentStatusEffects' then
          return function(character)
            local base=helpers.getEquipmentStatusEffects(character)
            if character==caster then return base|effect.statusMask end
            return base
          end
        end
        return helpers[name]
      end,
      __newindex=function() error('read-only per-call helper proxy') end
    })
    return original(formula,caster,target,functions,classes,proxy)
  end
end

return M
