-- Diagnostic prototype: no bonuses, ownership, save writes or loader registration.
local M = {}
local function integer(v, lo, hi)
  return type(v)=='number' and v%1==0 and v>=lo and v<=hi
end
local function classKey(a,b)
  if not integer(a,0,11) then return nil end
  if b==nil or b==255 then return string.format('%d',a) end
  if not integer(b,0,11) or a==b then return nil end
  if b<a then a,b=b,a end
  return string.format('%d:%d',a,b)
end

function M.new(deps)
  assert(type(deps)=='table' and type(deps.legacyMode)=='function'
    and type(deps.identity)=='function' and type(deps.emit)=='function',
    'diagnostic requires mode, canonical identity and log adapters')
  local campaign, seen, count = nil, {}, 0
  local observer={}
  function observer:load(path)
    -- Every attempted load invalidates the old identity, including malformed paths.
    campaign,seen,count=nil,{},0
    if type(path)~='string' or path:find('[%z\r\n]') then return false end
    local name=path:match('([^/\\]+)$')
    if not name or not name:match('^LEGCY_%d%d%d$') then return false end
    if deps.legacyMode()~=true then return false end
    campaign=name
    deps.emit('Legacy diagnostic: campaign='..campaign..' bonuses=off')
    return true
  end
  function observer:reset()
    campaign,seen,count=nil,{},0
  end
  function observer:observe(formula,caster)
    if deps.legacyMode()~=true then self:reset(); return false end
    if not campaign or count>=256 or formula.action~=150 then return false end
    local context=deps.identity(caster)
    if type(context)~='table' or context.kind~='main-party'
      or not integer(context.actorId,0,5) or context.actorId~=caster.identifier then return false end
    local key=classKey(caster.job1,caster.job2)
    if not key or not integer(caster.weapon,0x1000,0xFFFE)
      or not integer(formula.identifier,0,255) then return false end
    local signature=string.format('%d/%s/%d/%d',context.actorId,key,caster.weapon,formula.identifier)
    if seen[signature] then return false end
    seen[signature],count=true,count+1
    deps.emit(string.format('Legacy diagnostic: campaign=%s actor=%d class=%s weapon=%d formula=%d pilot=%s bonuses=off',
      campaign,context.actorId,key,caster.weapon,formula.identifier,
      (key=='8' or key=='11') and 'base' or 'control'))
    return true
  end
  function observer:wrap(original)
    assert(type(original)=='function','original function required')
    return function(...)
      local result=table.pack(original(...))
      -- Instrument failure must not interrupt Forge or replace its return values.
      pcall(self.observe,self,...)
      return table.unpack(result,1,result.n)
    end
  end
  return observer
end
return M
