-- Ivalice Legacy: patch the native save namespace before game startup proceeds.
-- This file belongs in scripts/init and requires FF12 LUA Loader 2.0.1.
-- No save files, sidecars or executable files are written by this script.
-- init(false) disables this script only; it does not stop the game. A FATAL
-- activation failure therefore requires stopping before loading or saving.

local PREFERRED_BASE = 0x120000

local function ascii(text)
  local result = {}
  for i = 1, #text do result[i] = text:byte(i) end
  return result
end

local function wide(text)
  local result = {}
  for i = 1, #text do
    result[#result + 1] = text:byte(i)
    result[#result + 1] = 0
  end
  return result
end

local TARGETS = {
  { name = "save file format", rva = 0xD12200 - PREFERRED_BASE,
    original = wide("%sFFXII_%03d\0"), replacement = wide("%sLEGCY_%03d\0") },
  { name = "save enumeration prefix", rva = 0xD113D4 - PREFERRED_BASE,
    original = ascii("FFXII_\0"), replacement = ascii("LEGCY_\0") },
}

local function same(a, b)
  if #a ~= #b then return false end
  for i = 1, #a do if a[i] ~= b[i] then return false end end
  return true
end

local function readBytes(address, count)
  local ok, raw = pcall(memory.readArray, address, count)
  if not ok then error("memory read failed", 0) end
  if type(raw) ~= "table" and type(raw) ~= "string" then
    error("memory reader returned an unsupported value", 0)
  end
  if #raw ~= count then error("memory reader returned a short result", 0) end
  local result = {}
  for i = 1, count do
    local value = type(raw) == "string" and raw:byte(i) or raw[i]
    if type(value) ~= "number" or value % 1 ~= 0 or value < 0 or value > 255 then
      error("memory reader returned an invalid byte", 0)
    end
    result[i] = value
  end
  return result
end

local function expect(address, bytes, name)
  if not same(readBytes(address, #bytes), bytes) then error("unsupported " .. name, 0) end
end

local function writeBytes(address, bytes)
  local ok, result = pcall(memory.writeArray, address, bytes)
  if not ok or result ~= true then error("memory writer rejected the patch", 0) end
  if not same(readBytes(address, #bytes), bytes) then error("memory write readback failed", 0) end
end

local function rollback(attempted)
  local verified = true
  for i = #attempted, 1, -1 do
    local entry = attempted[i]
    local ok, current = pcall(readBytes, entry.address, #entry.before)
    local ours = ok
    if ok then
      for j = 1, #current do
        if current[j] ~= entry.before[j] and current[j] ~= entry.target.replacement[j] then
          ours = false
          break
        end
      end
    end
    if ours then
      pcall(writeBytes, entry.address, entry.before)
      -- A writer may report false after making the complete restoration. The
      -- exact readback, not the return code alone, establishes the final state.
      local readable, after = pcall(readBytes, entry.address, #entry.before)
      if not readable or not same(after, entry.before) then verified = false end
    else
      -- Never overwrite an unknown concurrent patch or unreadable address.
      verified = false
    end
  end
  return verified
end

function init(baseAddress)
  local attempted = {}
  local already = true
  local ok, failure = pcall(function()
    if type(baseAddress) ~= "number" or baseAddress % 1 ~= 0 or baseAddress <= 0
        or baseAddress > 0x7FFF00000000 then error("invalid module base", 0) end
    if memory == nil or type(memory.readArray) ~= "function" or type(memory.writeArray) ~= "function" then
      error("required memory API unavailable", 0)
    end
    -- Build-time SHA-256 pins the complete executable; these runtime fields
    -- reject a changed mapping before touching either namespace string.
    expect(baseAddress, ascii("MZ"), "DOS signature")
    expect(baseAddress + 0x3C, {0x80, 0x01, 0, 0}, "PE location")
    expect(baseAddress + 0x180, {0x50, 0x45, 0, 0, 0x64, 0x86}, "PE signature/machine")
    expect(baseAddress + 0x188, {0xD7, 0x7F, 0xCA, 0x5D}, "PE timestamp")
    expect(baseAddress + 0x198, {0x0B, 0x02}, "PE format")
    expect(baseAddress + 0x1D0, {0, 0x60, 0xF7, 0x02}, "image size")
    local snapshots = {}
    for _, target in ipairs(TARGETS) do
      local address = baseAddress + target.rva
      local before = readBytes(address, #target.original)
      if not same(before, target.original) and not same(before, target.replacement) then
        error("unexpected " .. target.name .. " bytes", 0)
      end
      snapshots[#snapshots + 1] = {address = address, before = before, target = target}
    end
    for _, entry in ipairs(snapshots) do
      if not same(entry.before, entry.target.replacement) then
        already = false
        expect(entry.address, entry.before, entry.target.name .. " changed during startup")
        -- Record before calling the writer, which may throw after a partial write.
        attempted[#attempted + 1] = entry
        writeBytes(entry.address, entry.target.replacement)
      end
    end
    for _, entry in ipairs(snapshots) do
      expect(entry.address, entry.target.replacement, entry.target.name .. " readback")
    end
  end)
  if not ok then
    local restored = rollback(attempted)
    print("Ivalice Legacy: FATAL: save isolation NOT active: " .. tostring(failure)
      .. (restored and "; initial bytes preserved." or "; rollback not verified, restart required.")
      .. " Stop before loading or saving; the game has NOT been blocked by this script.")
    return false
  end
  if already then
    print("Ivalice Legacy: separate save files already active (LEGCY_###); early-init readback verified.")
  else
    print("Ivalice Legacy: using separate save files (LEGCY_###); early-init readback verified.")
  end
  return true
end
