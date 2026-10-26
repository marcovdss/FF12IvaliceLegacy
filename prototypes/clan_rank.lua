-- Clan rank, marks and points from the game's own data, read without side effects.
-- Static facts (FFXII_TZA.exe, no ASLR):
-- - 0x253970 stores save image 0x2164280 + 0x8200 as the Clan block pointer (0x20927C0).
-- - Clan points: u32 at block+0x3868 (save image 0xBA68).
-- - Rank 0x315390 walks the .rdata table at 0xD105D0 from rank 12 down: 12 rows of
--   {u32 points, u8 marks, u8 den, u16 text}; the first row the save meets is the rank.
-- - Marks 0x316480 counts hunts that reached their required stage; read-only.
-- - Den count 0x316670 counts 30 bits at block+0x3844, but first calls 0x316930,
--   which writes Sky Pirate's Den state. So 0x315390/0x316670 are never called here.
local M = {}

M.BLOCK = 0x2164280 + 0x8200
M.POINTS = M.BLOCK + 0x3868
M.DEN_BITS = M.BLOCK + 0x3844
M.DEN_COUNT = 30
M.RANK_TABLE = 0xD105D0
M.RANKS = 12
M.MARKS_FN = 0x316480

-- Rows pinned from the Steam build so a different executable is refused.
local PINNED = {
  [1] = {0, 0, 0}, [2] = {700, 2, 0}, [7] = {100000, 14, 0}, [12] = {1000000, 44, 30},
}

local function count(v, hi)
  return type(v) == 'number' and v % 1 == 0 and v >= 0 and v <= hi
end

-- mem = {u8=function(address), u32=function(address), marks=function()}
function M.read(mem)
  local rows = {}
  for r = 1, M.RANKS do
    local a = M.RANK_TABLE + 8 * (r - 1)
    rows[r] = {mem.u32(a), mem.u8(a + 4), mem.u8(a + 5)}
  end
  for r, want in pairs(PINNED) do
    local row = rows[r]
    if row[1] ~= want[1] or row[2] ~= want[2] or row[3] ~= want[3] then return nil, 'rank table' end
  end
  local points, marks = mem.u32(M.POINTS), mem.marks()
  if not count(points, 0xFFFFFFFF) then return nil, 'points' end
  if not count(marks, 0xFF) then return nil, 'marks' end
  local den = 0
  for i = 0, M.DEN_COUNT - 1 do
    local byte = mem.u8(M.DEN_BITS + i // 8)
    if not count(byte, 0xFF) then return nil, 'den' end
    if (byte >> (i % 8)) & 1 == 1 then den = den + 1 end
  end
  local rank = 1
  for r = M.RANKS, 1, -1 do
    local row = rows[r]
    if points >= row[1] and marks >= row[2] and den >= row[3] then rank = r break end
  end
  return {rank = rank, marks = marks, points = points, den = den}
end

return M
