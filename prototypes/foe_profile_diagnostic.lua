-- Diagnostic prototype: logs each distinct foe's live level, stats and status masks once.
-- No bonuses, no writes to game state, no save data, no loader registration.
local M = {}
M.version = '0.1.4'

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
  local seen, count, charges, hits, actions, lastClock, errorLogged = {}, 0, 0, 0, 0, nil, false
  local LIMIT = 400
  local observer = {}

  function observer:reset()
    seen, count, charges, hits, actions, lastClock = {}, 0, 0, 0, 0, nil
  end

  -- Charge-timer samples (current/required/last/idle) for the attacker and target of a hit, to learn how
  -- the native charge works before any mechanic writes to it. Read-only, capped per load.
  local CHARGE_LIMIT = 120
  function observer:charge(s)
    if charges >= CHARGE_LIMIT or type(s) ~= 'table' then return false end
    for _, side in ipairs({'caster', 'target'}) do
      local c = s[side]
      if type(c) ~= 'table' then return false end
      for _, k in ipairs({'current', 'required', 'last', 'idle'}) do
        if not integer(c[k], 0, 0xFFFFFFFF) then return false end
      end
    end
    charges = charges + 1
    local c, t = s.caster, s.target
    deps.emit(string.format(
      'Legacy charge probe: clock=%s action=%s casterType=%s caster=%d/%d/%d/%d targetType=%s target=%d/%d/%d/%d bonuses=off',
      tostring(s.clock), tostring(s.action), tostring(s.casterType), c.current, c.required, c.last, c.idle,
      tostring(s.targetType), t.current, t.required, t.last, t.idle))
    return true
  end

  -- Base damage of each hit (Forge 113's removedHp, before crit/status factors) with the target's level and max HP,
  -- to measure fight length and damage taken against the band targets. Read-only, capped per load.
  local HIT_LIMIT = 300
  function observer:hit(s)
    if hits >= HIT_LIMIT or type(s) ~= 'table' then return false end
    -- Forge computes damage as power * multiplier, so it is usually fractional; log it rounded down.
    if type(s.removed) ~= 'number' or s.removed ~= s.removed or s.removed < 0 or s.removed > 0xFFFFFFFF then return false end
    s.removed = math.floor(s.removed)
    for _, k in ipairs({'removed', 'targetLevel', 'targetMaxHp'}) do
      if not integer(s[k], 0, 0xFFFFFFFF) then return false end
    end
    local function side(kind, name)
      if kind == 1 then return integer(name, 0, 0xFFFF) and ('foe:' .. name) or 'foe:?' end
      return kind == 0 and 'party' or 'other'
    end
    hits = hits + 1
    deps.emit(string.format('Legacy hit: story=%s action=%s caster=%s target=%s lv=%d maxHp=%d removed=%d bonuses=off',
      integer(s.story, 0, 0xFFFF) and tostring(s.story) or '?', tostring(s.action), side(s.casterType, s.casterName),
      side(s.targetType, s.targetName), s.targetLevel, s.targetMaxHp, s.removed))
    if hits == HIT_LIMIT then deps.emit('Legacy hit: limit reached for this load') end
    return true
  end

  -- Observations at Forge's cast boundary, including non-damaging casts. This is
  -- not a trace of AI-rule evaluation, movement, cancelled or native-only actions.
  local ACTION_LIMIT = 300
  function observer:action(s)
    if actions >= ACTION_LIMIT or type(s) ~= 'table' or s.foe ~= true then return false end
    if not integer(s.nameId, 0, 0xFFFF) or not integer(s.action, 0, 0xFFFF) or
       not integer(s.maxHp, 1, 0xFFFFFFFF) or not integer(s.currentHp, 0, s.maxHp) or
       not integer(s.clock, 0, 0x7FFFFFFFFFFFFFFF) or (lastClock and s.clock < lastClock) then return false end
    local function optional(v, hi)
      return integer(v, 0, hi) and tostring(v) or '?'
    end
    local target = 'other'
    if s.targetType == 0 then target = 'party:' .. optional(s.targetId, 0xFF)
    elseif s.targetType == 1 then target = 'foe:' .. optional(s.targetName, 0xFFFF) end
    actions, lastClock = actions + 1, s.clock
    deps.emit(string.format(
      'Legacy action: clock=%s callback=onCastEnd foe=%d unit=%s area=? location=%s action=%d target=%s hpPct=%.2f maxHp=%d bonuses=off',
      tostring(s.clock), s.nameId, optional(s.unitId, 0xFF), optional(s.location, 0x7FFF),
      s.action, target, 100 * s.currentHp / s.maxHp, s.maxHp))
    if actions == ACTION_LIMIT then deps.emit('Legacy action: limit reached for this load') end
    return true
  end

  function observer:wrap_action(original)
    assert(type(original)=='function', 'original function required')
    return function(...)
      local result = table.pack(original(...))
      local ok, err = pcall(function(...) return self:action(deps.action(...)) end, ...)
      if not ok and not errorLogged then
        errorLogged = true
        pcall(deps.emit, 'Legacy action: observer error='..tostring(err):gsub('[%z\r\n]', ' ')..' bonuses=off')
      end
      return table.unpack(result, 1, result.n)
    end
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
      if ok and deps.charge then ok, err = pcall(function(...) return self:charge(deps.charge(...)) end, ...) end
      if ok and deps.hit then ok, err = pcall(function(...) return self:hit(deps.hit(...)) end, ...) end
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
