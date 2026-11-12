-- Ivalice Legacy: smaller native TARGET INFO window.
--
-- The window object lives at [[0x209BE80] + 0x9FD0] (UI manager -> HUD window -> target info),
-- non-null only while the window is open. Its layout group (+0x58) belongs to a loaded menu
-- resource pack: 0x14-byte group records (count, index<<2, flags, x, y, w, h, u32, entries),
-- each followed by variable-length entries (len, type, ...; rect at +4). Type 1 entries embed
-- another group of the same pack by index (u32 at +0x0C); type 5 is text (font scale byte at
-- +0x10); type 7 is a gauge (inner sizes at +0x20/+0x22 and +0x34/+0x36, offsets at +0x28/+0x2A).
--
-- The layout is scaled once per session, only when the root group still has its vanilla
-- signature, so it can never be applied twice or to unrelated data.

local M = {}

M.UI_ROOT_PTR = 0x209BE80
M.TARGET_INFO_WINDOW_OFFSET = 0x9FD0
M.WINDOW_GROUP_OFFSET = 0x58
M.GROUP_RECORD_SIZE = 0x14
M.ROOT_SIGNATURE = {count = 19, index = 22, w = 1400, h = 320}

local function scaled(value, factor)
  return math.floor(value * factor + 0.5)
end

-- mem: {u8 = get/set table, s16 = ..., u16 = ..., u32 = ...} (memory.* in game, fakes in tests).
local function scale_rect(mem, address, factor)
  mem.s16[address + 4] = scaled(mem.s16[address + 4], factor)
  mem.s16[address + 6] = scaled(mem.s16[address + 6], factor)
  mem.u16[address + 8] = scaled(mem.u16[address + 8], factor)
  mem.u16[address + 10] = scaled(mem.u16[address + 10], factor)
end

local function scale_group(mem, table_base, group, factor, visited)
  if visited[group] then return 0 end
  visited[group] = true
  scale_rect(mem, group, factor)
  local count = mem.u8[group]
  local entry = mem.u32[group + 0x10]
  local touched = 1
  for _ = 1, count do
    local length = mem.u8[entry]
    if length == 0 then break end
    local kind = mem.u8[entry + 1]
    scale_rect(mem, entry, factor)
    if kind == 5 then
      mem.u8[entry + 0x10] = scaled(mem.u8[entry + 0x10], factor)
    elseif kind == 7 and length >= 0x38 then
      for _, offset in ipairs({0x20, 0x22, 0x28, 0x2A, 0x34, 0x36}) do
        mem.u16[entry + offset] = scaled(mem.u16[entry + offset], factor)
      end
    elseif kind == 1 then
      local index = mem.u32[entry + 0x0C]
      local child = table_base + index * M.GROUP_RECORD_SIZE
      if mem.u8[child + 1] // 4 == index then
        touched = touched + scale_group(mem, table_base, child, factor, visited)
      end
    end
    entry = entry + length
  end
  return touched
end

function M.matches_vanilla(mem, group)
  local s = M.ROOT_SIGNATURE
  return mem.u8[group] == s.count and mem.u8[group + 1] // 4 == s.index
    and mem.u16[group + 8] == s.w and mem.u16[group + 10] == s.h
end

-- Returns the number of groups scaled, or nil plus a reason.
function M.scale_layout(mem, group, factor)
  if not (factor and factor > 0.3 and factor < 1.0) then return nil, 'factor out of range' end
  if not M.matches_vanilla(mem, group) then return nil, 'layout is not the vanilla target info' end
  local table_base = group - (mem.u8[group + 1] // 4) * M.GROUP_RECORD_SIZE
  return scale_group(mem, table_base, group, factor, {})
end

function M.register(factor)
  factor = factor or 0.75
  local scaled_group = nil
  local mem = {u8 = memory.u8, s16 = memory.s16, u16 = memory.u16, u32 = memory.u32}
  event.registerEventAsync('onFlip', function()
    local manager = memory.u64[M.UI_ROOT_PTR]
    if manager == 0 then return end
    local window = memory.u64[manager + M.TARGET_INFO_WINDOW_OFFSET]
    if window == 0 then return end
    local group = memory.u64[window + M.WINDOW_GROUP_OFFSET]
    if group == 0 or group == scaled_group then return end
    scaled_group = group
    local count, why = M.scale_layout(mem, group, factor)
    if count then
      print(('Legacy target info: scaled to %.2f (%d groups)'):format(factor, count))
    else
      print('Legacy target info: left unchanged (' .. tostring(why) .. ')')
    end
  end)
  return true
end

return M
