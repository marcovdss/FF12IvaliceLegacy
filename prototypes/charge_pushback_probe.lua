-- Mechanism test, not a design: does lowering a foe's current charge time during a hit persist and delay it?
-- Only party ordinary Attack (action 150) on a foe that is mid-charge qualifies. Qualifying hits alternate
-- control/write within one load, so every session carries its own control. A write lowers the foe's current
-- charge by a quarter of its required charge, never to zero, at most WRITE_LIMIT times per load.
local M = {}
M.version = '0.1.0'

local ATTACK = 150
local WRITE_LIMIT = 12
local LINE_LIMIT = 60

local function integer(v, lo, hi)
  return type(v)=='number' and v%1==0 and v>=lo and v<=hi
end

function M.new(deps)
  assert(type(deps)=='table' and type(deps.emit)=='function', 'probe requires a log adapter')
  local qualifying, writes, lines = 0, 0, 0
  local probe = {}

  function probe:reset()
    qualifying, writes, lines = 0, 0, 0
  end

  -- s: action, casterType, targetType, clock, unit (target battle unit work address), current, required, last,
  -- set(value) and get() for the target's current charge time.
  function probe:hit(s)
    if lines >= LINE_LIMIT or type(s) ~= 'table' then return false end
    if s.action ~= ATTACK or s.casterType ~= 0 or s.targetType ~= 1 then return false end
    for _, k in ipairs({'unit', 'current', 'required', 'last'}) do
      if not integer(s[k], 0, 0xFFFFFFFFFFFF) then return false end
    end
    -- Mid-charge: the native trace shows last == current while a unit charges toward required.
    if s.unit == 0 or s.current <= 0 or s.current >= s.required or s.last ~= s.current then return false end
    qualifying, lines = qualifying + 1, lines + 1
    local push = s.required // 4
    local arm, written, readback = 'control', s.current, s.current
    if qualifying % 2 == 0 and writes < WRITE_LIMIT and s.current > push then
      arm, written = 'write', s.current - push
      s.set(written)
      readback = s.get()
      writes = writes + 1
    end
    deps.emit(string.format(
      'Legacy charge pushback: n=%d arm=%s clock=%s unit=0x%X before=%d/%d written=%d readback=%s writes=%d bonuses=charge-test',
      qualifying, arm, tostring(s.clock), s.unit, s.current, s.required, written, tostring(readback), writes))
    return true
  end

  return probe
end

return M
