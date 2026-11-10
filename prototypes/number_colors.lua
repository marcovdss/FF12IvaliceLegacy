-- Ivalice Legacy: battle number colours, diagnostic step 0.2.0 (Marco, 2026-11-10: "Allied DMG is Blue, Foe is Red.
-- HP Recovery should be green, and MP recovery some other color. What if Allied DMG is White and MP recovery is blue?").
--
-- 0.1.0 lesson (2026-11-10 play): the palette entry's colour dword is neutral grey (7F7F7F) for every entry, so
-- judging colours from it refused every change ("Legacy numbers: unchanged"). The visible colour is a font-gradient
-- index stored in the entry's fourth byte, which says nothing about the colour by itself. So 0.2.0 stops guessing
-- and asks the player: it dumps every palette entry's eight bytes once per load, and makes exactly one deliberate
-- change - MP recovery, on both sides, drawn in the foe-damage style. The colour Marco reports for MP recovery names
-- that damage colour, and the dump's fourth bytes point at the white entry for the follow-up.
--
-- How the game picks a number's colour (FFXII_TZA.exe 1.0.4.0, read statically; image base 0x120000):
--   0x3283D0 (absolute; the target Forge's showNumberText calls) folds its two flags into a type, heal*2 + mp:
--   0 HP damage, 1 MP damage, 2 HP heal, 3 MP heal, and adds 0 or 4 by the unit's side. It reads a style from
--   TYPE_TABLE[type + side] (.data, eight dwords, native 2 4 1 3 | 6 8 5 7).
--   RVA 0x3E6700 turns the style into a palette index through STYLE_COLOUR (.rdata: style 0 -> 0x00, 1/3/5/7 ->
--   0x13, 2/4 -> 0x11, 6/8 -> 0x12), then into a palette entry (count at PALETTE_COUNT, entries of 8 bytes at the
--   pointer in PALETTE_PTR; the entry's colour dword is grey, its fourth byte is the gradient index).
local M = {version = '0.2.0'}

M.TYPE_TABLE = 0x120000 + 0x1DCD300
M.STYLE_COLOUR = 0x120000 + 0xC027A8
M.PALETTE_COUNT = 0x120000 + 0x203E6B8
M.PALETTE_PTR = 0x120000 + 0x203E6C8
M.NATIVE = {2, 4, 1, 3, 6, 8, 5, 7}
M.NATIVE_STYLE_COLOUR = {[0] = 0x00, 0x13, 0x11, 0x13, 0x11, 0x13, 0x12, 0x13, 0x12}
M.DUMP_LIMIT = 0x40        -- palette entries dumped; the number colours live below this
M.FOE_DAMAGE_STYLE = 6     -- style 6 draws in palette colour 0x12: one side's damage numbers

-- One diagnostic dump per load: palette entries (both dwords each), the style->colour table, the type table.
-- Read-only. Returns true when rows were emitted, false when the palette is not loaded yet.
function M.dump(mem, emit)
  local count = mem.u32[M.PALETTE_COUNT]
  local base = mem.u32[M.PALETTE_PTR] | (mem.u32[M.PALETTE_PTR + 4] << 32)
  if base == 0 or count <= 0x13 then return false end
  local shown = math.min(count, M.DUMP_LIMIT)
  for start = 0, shown - 1, 4 do
    local parts = {}
    for i = start, math.min(start + 3, shown - 1) do
      parts[#parts + 1] = string.format('%02X=%08X %08X', i,
        mem.u32[base + 8 * i], mem.u32[base + 8 * i + 4])
    end
    emit('Legacy numbers: palette dump ' .. table.concat(parts, ' | '))
  end
  local styles = {}
  for s = 0, 8 do styles[#styles + 1] = string.format('%d=%08X', s, mem.u32[M.STYLE_COLOUR + 4 * s]) end
  emit('Legacy numbers: palette dump style->colour ' .. table.concat(styles, ' '))
  local types = {}
  for i = 0, 7 do types[#types + 1] = string.format('%d=%d', i, mem.u32[M.TYPE_TABLE + 4 * i]) end
  emit('Legacy numbers: palette dump type->style ' .. table.concat(types, ' ') ..
       string.format(' (entries=%d, dumped=%d)', count, shown))
  return true
end

-- The diagnostic plan: only the two MP-recovery slots change, both to the foe-damage style. mem: {u32 = get/set}
-- (memory.u32 in game). Returns the new eight styles and a description, or nil and why nothing changes.
function M.plan(mem)
  for i = 0, 7 do
    if mem.u32[M.TYPE_TABLE + 4 * i] ~= M.NATIVE[i + 1] then
      return nil, 'number table is not the native one (another mod, or already applied)'
    end
  end
  for style = 0, 8 do
    if mem.u32[M.STYLE_COLOUR + 4 * style] ~= M.NATIVE_STYLE_COLOUR[style] then
      return nil, 'style colour table differs from FFXII_TZA 1.0.4.0'
    end
  end
  local count, base = mem.u32[M.PALETTE_COUNT], mem.u32[M.PALETTE_PTR] | (mem.u32[M.PALETTE_PTR + 4] << 32)
  if base == 0 or count <= 0x13 then return nil, 'palette not loaded yet' end
  local styles = {}
  for i = 1, 8 do styles[i] = M.NATIVE[i] end
  styles[4], styles[8] = M.FOE_DAMAGE_STYLE, M.FOE_DAMAGE_STYLE
  return styles, 'diagnostic: MP recovery (both sides) now drawn in the foe-damage colour'
end

function M.apply(mem, styles)
  for i = 0, 7 do mem.u32[M.TYPE_TABLE + 4 * i] = styles[i + 1] end
end

-- Try now, and again after a load or a map change until the palette exists (the game builds it with the UI).
-- The dump runs once per load, even when the write refuses, so the evidence survives every outcome.
function M.register(log)
  log = log or print
  local dumped, done, tries = false, false, 0
  local function attempt()
    if done then return true end
    if not dumped then
      local okDump, emitted = pcall(M.dump, memory, log)
      if not okDump then
        dumped = true
        log('Legacy numbers: palette dump failed (' .. tostring(emitted) .. ')')
      elseif emitted == true then
        dumped = true
      else
        -- The palette is not up yet; the dump retries with the next attempt, before the write.
        log('Legacy numbers: palette dump skipped (palette not loaded yet)')
      end
    end
    local ok, styles, text = pcall(M.plan, memory)
    if ok and type(styles) == 'table' then
      M.apply(memory, styles)
      done = true
      log('Legacy numbers: ' .. M.version .. ' applied (' .. text .. ')')
    elseif ok and text == 'palette not loaded yet' then
      tries = tries + 1
      if tries <= 60 and type(event) == 'table' and type(event.executeAfterMs) == 'function' then
        event.executeAfterMs(5000, attempt)
      end
    else
      done = true
      log('Legacy numbers: unchanged (' .. (ok and tostring(text) or tostring(styles)) .. ')')
    end
    return true
  end
  if type(event) == 'table' then
    if type(event.registerEventSync) == 'function' then
      event.registerEventSync('onSaveLoad', function()
        dumped, tries = false, 0
        attempt()
        return true
      end)
    end
    if type(event.registerEventAsync) == 'function' then
      event.registerEventAsync('onMapJump', function() attempt() return true end)
    end
  end
  attempt()
  return true
end

return M
