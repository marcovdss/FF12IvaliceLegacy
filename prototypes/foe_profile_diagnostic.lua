-- Diagnostic prototype: logs each distinct foe's live level, stats and status masks once.
-- No bonuses, no writes to game state, no save data, no loader registration.
local M = {}

local STATUS = {'ko','stone','petrify','stop','sleep','confuse','doom','blind','poison','silence','sap','oil',
  'reverse','disable','immobilize','slow','disease','lure','protect','shell','haste','bravery','faith','reflect',
  'invisible','regen','float','berserk','bubble','hpCritical','libra','xZone'}

local function integer(v, lo, hi)
  return type(v)=='number' and v%1==0 and v>=lo and v<=hi
end

local function names(mask)
  local out = {}
  for i, name in ipairs(STATUS) do
    if (mask >> (i-1)) & 1 == 1 then out[#out+1] = name end
  end
  return #out > 0 and table.concat(out, ',') or '-'
end

M.names = names

function M.new(deps)
  assert(type(deps)=='table' and type(deps.profile)=='function' and type(deps.emit)=='function',
    'diagnostic requires profile and log adapters')
  local seen, count, errorLogged = {}, 0, false
  local LIMIT = 400
  local observer = {}

  function observer:reset()
    seen, count = {}, 0
  end

  function observer:observe(formula, caster, target)
    if count >= LIMIT then return false end
    local p = deps.profile(formula, caster, target)
    if type(p)~='table' or p.foe~=true then return false end
    for _, k in ipairs({'nameId','level','maxHp','immune','innate'}) do
      if not integer(p[k], 0, 0xFFFFFFFF) then return false end
    end
    local key = string.format('%d/%d/%d', p.nameId, p.level, p.maxHp)
    if seen[key] then return false end
    seen[key], count = true, count+1
    deps.emit(string.format(
      'Legacy foe profile: story=%s name=%d bestiary=%s class=%s genus=%s level=%d maxHp=%d str=%s mag=%s vit=%s spd=%s '..
      'atk=%s def=%s mres=%s immune=0x%08X[%s] innate=0x%08X[%s] bonuses=off',
      integer(p.story, 0, 0xFFFF) and tostring(p.story) or '?',
      p.nameId, tostring(p.bestiary), tostring(p.classification), tostring(p.genus), p.level, p.maxHp,
      tostring(p.str), tostring(p.mag), tostring(p.vit), tostring(p.spd), tostring(p.atk), tostring(p.def),
      tostring(p.mres), p.immune, names(p.immune), p.innate, names(p.innate)))
    if count == LIMIT then deps.emit('Legacy foe profile: limit reached; further foes not logged') end
    return true
  end

  function observer:wrap(original)
    assert(type(original)=='function', 'original function required')
    return function(...)
      local result = table.pack(original(...))
      -- Logging failure must never interrupt Forge or change its return values.
      local ok, err = pcall(self.observe, self, ...)
      if not ok and not errorLogged then
        errorLogged = true
        pcall(deps.emit, 'Legacy foe profile: observer error='..tostring(err):gsub('[%z\r\n]', ' ')..' bonuses=off')
      end
      return table.unpack(result, 1, result.n)
    end
  end

  return observer
end

return M
