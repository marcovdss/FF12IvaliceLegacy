-- Ivalice Legacy: separate save files.
--
-- While Ivalice Legacy is installed, the game saves and loads LEGCY_000, LEGCY_001, ... instead of
-- FFXII_000, FFXII_001, ... in the usual save folder. Saves from other playthroughs are not shown and are
-- never written. Without the mod installed, nothing changes and the game uses its normal saves.
--
-- The game builds every save file name from two strings in FFXII_TZA.exe (Steam 1.0.4.0):
--   0x00D12200  UTF-16 "%sFFXII_%03d"  folder + prefix + slot number, used to open save files
--   0x00D113D4  ASCII  "FFXII_"        prefix copied by the save listing code
-- Both prefixes are replaced in memory only (the executable on disk is never modified). Every byte is checked
-- first; if anything differs (another game version, or another mod already changed them) nothing is touched.
-- Save data kept by other Lua mods follows automatically: it is stored next to the actual save file.

local OLD_PREFIX = "FFXII_"
local NEW_PREFIX = "LEGCY_"

local function ascii(text)
  local bytes = {}
  for i = 1, #text do
    bytes[#bytes + 1] = text:byte(i)
  end
  return bytes
end

local function utf16(text)
  local bytes = {}
  for i = 1, #text do
    bytes[#bytes + 1] = text:byte(i)
    bytes[#bytes + 1] = 0
  end
  return bytes
end

local TARGETS = {
  { name = "save file name", address = 0x00D12200,
    original = utf16("%s" .. OLD_PREFIX .. "%03d"), replacement = utf16("%s" .. NEW_PREFIX .. "%03d") },
  { name = "save list prefix", address = 0x00D113D4,
    original = ascii(OLD_PREFIX .. "\0"), replacement = ascii(NEW_PREFIX .. "\0") },
}

local function readsAs(address, expected)
  local current = memory.readArray(address, #expected)
  for i = 1, #expected do
    local value = type(current) == "string" and current:byte(i) or current[i]
    if value ~= expected[i] then
      return false
    end
  end
  return true
end

local function apply()
  local alreadyActive = true
  for _, target in ipairs(TARGETS) do
    if not readsAs(target.address, target.replacement) then
      alreadyActive = false
      if not readsAs(target.address, target.original) then
        print(string.format("Ivalice Legacy: %s at 0x%08X is not the expected text; save files NOT separated.",
          target.name, target.address))
        return false
      end
    end
  end
  if alreadyActive then
    print("Ivalice Legacy: separate save files already active (" .. NEW_PREFIX .. "###).")
    return true
  end
  for _, target in ipairs(TARGETS) do
    memory.writeArray(target.address, target.replacement)
  end
  for _, target in ipairs(TARGETS) do
    if not readsAs(target.address, target.replacement) then
      print(string.format("Ivalice Legacy: could not change %s; restoring the original save names.", target.name))
      for _, t in ipairs(TARGETS) do
        memory.writeArray(t.address, t.original)
      end
      return false
    end
  end
  print("Ivalice Legacy: using separate save files (" .. NEW_PREFIX .. "###).")
  return true
end

apply()
