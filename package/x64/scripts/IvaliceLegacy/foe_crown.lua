-- Ivalice Legacy: the crown and the AI spell insert (scripts/IvaliceLegacy/foe_crown.lua).
--
-- Written for Legacy from documented behaviour and the Instanced Foes API only. Nothing here is taken from Dynamic
-- Bosses' files, and no Dynamic Bosses constant is used. Three jobs:
--   1. Recognise a common foe Dynamic Bosses has crowned (it holds Protect, Shell, Bravery and Faith for good) and
--      lift its Strength, Magick and Attack to 1.25 x the party average.
--   2. Give a foe one elemental spell in its AI, written into a copy of its own native Battle Logic, with a guard: a
--      rule that tests an AI flag is never copied and never gets a spell in front of it, so one-shot rules still run.
--   3. Crown a main-story boss (HP, defence, offence, Bravery/Faith, an absorbed element, an aura, a name, loot). A boss
--      with an entry in crown_table.lua (generated, keyed by its name id) takes that authored crown instead: its fixed
--      kind, its permanent statuses and immunities, its offence mirror, its Defence roll and its spell; a boss without
--      an entry keeps the default crown below.
--
-- Battle Logic facts, each read from the pinned FFXII_TZA.exe (static, nothing was run):
--   * 0x239040(slot 0..3) sets a foe's active script. It reads the unit row pointer at battle unit + 0xE60, takes the
--     u16 at row + 0x50 + 2 * slot (the AI links), indexes the bank whose pointer sits at VA 0x2EBF168
--     (u16 count, u16 unknown, then u32 offsets from +4: offset i at bank + 4 + 4 * i), turns the offset into a
--     pointer with 0x20E600 (offset + the u64 at VA 0x1F83530, which is 0 in practice) and stores it at battle
--     unit + 0x6E0. 0x2FE05B does the same for the AI opcodes 0x4112..0x4115 (switch to slot n), and bounds the id by
--     the bank count.
--   * A script is 32 bytes of group sizes, then 24-byte rules: action u16, parameter s16, two flag bytes, then three
--     cases of (target u16, condition u16, parameter s16). Case conditions 0x36xx and 0x37xx are flag tests.
local C = {version = '0.1.0'}

local log = function(text) print('Legacy foes: ' .. text) end
C.log = log

-- ---------------------------------------------------------------- facts (VA, as the Lua Loader addresses them)
C.BANK_PTR = 0x02EBF168      -- u64: pointer to the current area's Battle Logic bank
C.RESOURCE_BASE = 0x01F83530 -- u64: added to a bank offset (0 in practice)
C.ACTIVE_SCRIPT = 0x6E0      -- battle unit +: the script the AI is running (u64)
C.HEADER, C.RULE = 32, 24
C.MAX_RULES = 400            -- sanity bound on one script
C.MAX_BUILDS = 400           -- allocated copies kept for the session (they must outlive away/cached foes)

C.STAT_FLOOR = 1.25          -- offence of a crowned foe, times the party average
C.SPELL_CHANCE = 30          -- percent
C.MAX_INSERTS = 4            -- one spell rule per attack group, at most four per script

-- ---------------------------------------------------------------- kinds
-- Kinds 0-7 are the elements in the game's own order (fire, lightning, ice, earth, water, wind, holy, dark); 8 and 9
-- carry no element. spells[tier]: tier 1 below level 20, 2 below level 40, 3 above. Action ids are the game's own
-- (Fire 18, Fira 23, Firaga 28 ... Dark 76, Darkra 77, Darkga 78). Earth has no native low spell, so it casts only
-- in tier 3 (Quakeja 286). An element row whose spell is false for a tier gets the aura and the absorb but no spell.
C.ELEMENTS = {[0] = 'fire', 'lightning', 'ice', 'earth', 'water', 'wind', 'holy', 'dark'}
C.KINDS = {
  [0] = {name = 'Pyro',   element = 0, spells = {18, 23, 28}, color = {r = 2.0, g = 0.6, b = 0.3}},
  [1] = {name = 'Volt',   element = 1, spells = {19, 24, 29}, color = {r = 2.0, g = 1.8, b = 0.3}},
  [2] = {name = 'Frost',  element = 2, spells = {20, 25, 30}, color = {r = 0.5, g = 1.2, b = 2.0}},
  [3] = {name = 'Quake',  element = 3, spells = {false, false, 286}, color = {r = 1.4, g = 0.9, b = 0.4}},
  [4] = {name = 'Tide',   element = 4, spells = {21, 284, 285}, color = {r = 0.3, g = 0.8, b = 2.0}},
  [5] = {name = 'Gale',   element = 5, spells = {22, 283, 27}, color = {r = 0.6, g = 2.0, b = 1.0}},
  [6] = {name = 'Halo',   element = 6, spells = {17, 17, 17}, color = {r = 2.0, g = 2.0, b = 1.4}},
  [7] = {name = 'Gloom',  element = 7, spells = {76, 77, 78}, color = {r = 1.0, g = 0.3, b = 1.8}},
  [8] = {name = 'Venom',  spells = {26, 26, 26}, color = {r = 0.7, g = 1.8, b = 0.3}},
  [9] = {name = 'Crush',  spells = {79, 79, 80}, color = {r = 1.1, g = 0.5, b = 1.6}},
}
C.KIND_COUNT = 10
C.AURA = 8                    -- the monster effect every crowned foe gets, tinted by its kind (docs: chara\h0008.efx)

function C.tier(level)
  level = tonumber(level) or 1
  return level < 20 and 1 or (level < 40 and 2 or 3)
end

function C.spellFor(kind, level)
  local record = C.KINDS[kind]
  if not record then return nil end
  for tier = C.tier(level), 1, -1 do
    if record.spells[tier] then return record.spells[tier] end
  end
  return nil
end

-- A kind whose element the foe is weak to is never chosen: its weaknesses stay levers.
function C.chooseKind(weakSet, random)
  local choices = {}
  for kind = 0, C.KIND_COUNT - 1 do
    local element = C.KINDS[kind].element
    if not element or not weakSet or not weakSet.has(C.ELEMENTS[element]) then choices[#choices + 1] = kind end
  end
  return choices[random(1, #choices)]
end

function C.round(value) return math.floor(value + 0.5) end

local function clamp(value, low, high) return math.max(low, math.min(high, value)) end
C.clamp = clamp

-- ---------------------------------------------------------------- Battle Logic scripts
local function u16(b, at) return b[at] | (b[at + 1] << 8) end
local function s16(b, at) local v = u16(b, at); return v >= 0x8000 and v - 0x10000 or v end

local function put16(b, at, v)
  v = v & 0xFFFF
  b[at], b[at + 1] = v & 0xFF, v >> 8
end

-- Group sizes, the rule total and the script length; nil when the header does not describe a plausible script.
function C.parse(bytes)
  if #bytes < C.HEADER then return nil end
  local counts, total = {}, 0
  for g = 1, C.HEADER do
    counts[g] = bytes[g]
    total = total + bytes[g]
  end
  if total > C.MAX_RULES or #bytes < C.HEADER + total * C.RULE then return nil end
  return counts, total, C.HEADER + total * C.RULE
end

-- The guard: a rule is guarded when any case tests an AI flag or a flag variable (conditions 0x36xx and 0x37xx).
-- Those rules carry one-shot logic (write a flag once, leave a state); a spell rule must never sit in front of them
-- and a copy of one must never exist, or two copies can alternate and starve the flag write.
function C.guarded(bytes, at)
  for case = 0, 2 do
    local base = at + 6 + case * 6
    local target, condition = u16(bytes, base), u16(bytes, base + 2)
    local group = condition >> 8
    if (group == 0x36 or group == 0x37) and (target ~= 0 or condition ~= 0) then return true end
  end
  return false
end

-- A hostile rule: a real action (not an AI opcode 0x4000-0x7FFF) aimed at a Foe, with no flag test.
function C.hostile(bytes, at)
  local action = u16(bytes, at)
  if action >= 0x4000 and action < 0x8000 then return false end
  if action == 0xFFFF then return false end
  if u16(bytes, at + 6) ~= 1 then return false end
  return not C.guarded(bytes, at)
end

-- Returns the new byte list and how many rules were added (the input is not changed). One spell rule goes in front of
-- the first hostile rule of each group, as a copy of that rule with the action and chance replaced and the
-- no-consecutive-use bit cleared, so it targets what the rule would have targeted.
function C.insertSpell(bytes, spell, chance, maxInserts)
  local counts, total, length = C.parse(bytes)
  if not counts then return nil, 0 end
  chance = chance or C.SPELL_CHANCE
  maxInserts = maxInserts or C.MAX_INSERTS
  local out, added, at = {}, 0, C.HEADER + 1
  for i = 1, C.HEADER do out[i] = bytes[i] end
  for g = 1, C.HEADER do
    local inserted = false
    for r = 1, counts[g] do
      if not inserted and added < maxInserts and out[g] < 255 and C.hostile(bytes, at) and u16(bytes, at) ~= spell then
        local copy = {}
        for i = 0, C.RULE - 1 do copy[i + 1] = bytes[at + i] end
        put16(copy, 1, spell)
        put16(copy, 3, chance)
        copy[6] = copy[6] & ~0x08            -- flag byte 5, bit 3: no consecutive use
        for i = 1, C.RULE do out[#out + 1] = copy[i] end
        out[g] = out[g] + 1
        added = added + 1
        inserted = true
      end
      for i = 0, C.RULE - 1 do out[#out + 1] = bytes[at + i] end
      at = at + C.RULE
    end
  end
  if added == 0 then return nil, 0 end
  return out, added
end

local function hash(bytes)
  local h = #bytes
  for i = 1, #bytes do h = (h * 131 + bytes[i]) % 4294967291 end
  return h
end

-- The pointer of a foe's native script for slot 1..4, and its bytes. nil and a reason when anything does not hold.
function C.nativeScript(foe, slot)
  local links = foe.unit and foe.unit.battleLogicIdentifiers
  if not links then return nil, 'no AI links' end
  local id = links[slot]
  if type(id) ~= 'number' then return nil, 'no AI link for slot ' .. slot end
  local bank = memory.u64[C.BANK_PTR]
  if type(bank) ~= 'number' or bank == 0 then return nil, 'no Battle Logic bank' end
  local count = memory.u16[bank]
  if id >= count then return nil, 'AI link ' .. id .. ' is outside the bank of ' .. count end
  local base = memory.u64[C.RESOURCE_BASE]
  if type(base) ~= 'number' then return nil, 'resource base unreadable' end
  local address = base + memory.u32[bank + 4 + 4 * id]
  local header = memory.readArray(address, C.HEADER)
  if #header ~= C.HEADER then return nil, 'script unreadable' end
  local total = 0
  for i = 1, C.HEADER do total = total + header[i] end
  if total > C.MAX_RULES then return nil, 'script header implausible' end
  local length = C.HEADER + total * C.RULE
  -- Offsets exist for ids 0..count-1 only: the last script has no successor entry to compare with.
  local nextOffset = id + 1 < count and memory.u32[bank + 4 + 4 * (id + 1)] or 0
  if id + 1 < count and nextOffset ~= 0 and nextOffset - (address - base) < length then
    return nil, 'script is longer than its bank entry'
  end
  local bytes = memory.readArray(address, length)
  if #bytes ~= length then return nil, 'script unreadable' end
  return address, bytes
end

C.builds, C.buildCount, C.refused = {}, 0, false

-- The address of the native script with a spell inserted, built once and shared: a script holds no per-foe state, so
-- every foe of the species can run the same copy, exactly as they share the original.
function C.scriptWithSpell(address, bytes, spell)
  local key = address .. ':' .. spell
  local sum = hash(bytes)
  local known = C.builds[key]
  if known and known.sum == sum then return known.address, known.added end
  if C.buildCount >= C.MAX_BUILDS then
    if not C.refused then C.refused = true; log('AI spell copies are capped at ' .. C.MAX_BUILDS .. '; no more are built') end
    return nil
  end
  local built, added = C.insertSpell(bytes, spell)
  if not built then return nil, 0 end
  local block = memory.alloc(#built)
  if not block or block == 0 then return nil end
  if not memory.writeArray(block, built) then return nil end
  C.buildCount = C.buildCount + 1
  C.builds[key] = {address = block, sum = sum, added = added}
  return block, added
end

-- Point the foe's four slots at native-plus-spell scripts. replaceOwn: a non-zero slot is somebody's copy (Dynamic
-- Bosses', for a crowned common) and is replaced; otherwise a non-zero slot is left alone. spell nil resets to native.
-- When the running script is the one a slot replaces, the running pointer follows (the spawn has already used it).
function C.pointSlots(foe, spell, replaceOwn)
  local slots = foe.battleLogicSlots and foe.battleLogicSlots.slots
  if not slots then return 0, 'no slots' end
  local changed, seen = 0, {}
  for slot = 1, 4 do
    local current = slots[slot]
    if current ~= 0 and not replaceOwn then
      -- not ours to touch
    else
      local address, bytes = C.nativeScript(foe, slot)
      if address then
        local target = 0
        if spell then
          local copy = C.scriptWithSpell(address, bytes, spell)
          if copy then target = copy end
        end
        if target ~= current then
          local running = foe.buw and memory.u64[foe.buw + C.ACTIVE_SCRIPT]
          local old = current ~= 0 and current or address
          slots[slot] = target
          if running == old then memory.u64[foe.buw + C.ACTIVE_SCRIPT] = target ~= 0 and target or address end
          changed = changed + 1
        end
      elseif not seen[bytes] then
        seen[bytes] = true
        log('AI slot ' .. slot .. ' left as it is (' .. tostring(bytes) .. ')')
      end
    end
  end
  return changed
end

-- ---------------------------------------------------------------- crowned commons
local CROWN_STATUSES = {'protect', 'shell', 'bravery', 'faith'}

-- How far a Dynamic Bosses crown has got on a foe: 'absent' (none of its four statuses), 'complete' (all four and every
-- linked AI slot written) or 'partial' (anything between: its listener may still be running).
function C.crownProgress(foe)
  local statuses = foe.abilities and foe.abilities.statuses
  if not statuses then return 'absent' end
  local present = 0
  for _, name in ipairs(CROWN_STATUSES) do
    if statuses.has(name) == true then present = present + 1 end
  end
  if present == 0 then return 'absent' end
  if present < #CROWN_STATUSES then return 'partial' end
  local slots = foe.battleLogicSlots and foe.battleLogicSlots.slots
  if not slots then return 'partial' end
  -- Every slot whose native link points into the bank must be written; without a readable bank, any one will do.
  local links = foe.unit and foe.unit.battleLogicIdentifiers
  local bank = memory.u64[C.BANK_PTR]
  if links and type(bank) == 'number' and bank ~= 0 then
    local count = memory.u16[bank]
    for slot = 1, 4 do
      local id = links[slot]
      if type(id) == 'number' and id >= 0 and id < count and slots[slot] == 0 then return 'partial' end
    end
    return 'complete'
  end
  for slot = 1, 4 do
    if slots[slot] ~= 0 then return 'complete' end
  end
  return 'partial'
end

-- Dynamic Bosses' crown keeps all four for good; no ordinary foe has them as permanent statuses.
function C.crownedByDynamic(foe)
  local statuses = foe.abilities and foe.abilities.statuses
  return statuses ~= nil and statuses.has(CROWN_STATUSES) == true
end

local function kindOfAbsorb(foe)
  local ids = foe.class.affinityAbsorb.list()
  if #ids == 1 and ids[1] >= 0 and ids[1] < 8 then return ids[1] end
  return nil
end

C.OFFENCE = {'strength', 'magickPower', 'attackPower'}

function C.writeOffence(foe, party)
  local stats = foe.defaultStats
  for _, name in ipairs(C.OFFENCE) do
    stats[name] = clamp(C.round(party[name] * C.STAT_FLOOR), 0, 99)
  end
end

-- A crowned common: offence to 1.25 x party, and its AI back on a guarded script. The spell follows the element it
-- absorbs (the crown's mark); a crown with no element (or an unreadable one) goes back to its native script.
function C.fixCrowned(foe, party)
  C.writeOffence(foe, party)
  local kind = kindOfAbsorb(foe)
  local spell = kind and C.spellFor(kind, party.level) or nil
  local changed, why = C.pointSlots(foe, spell, true)
  return {kind = kind, spell = spell, slots = changed, why = why}
end

-- ---------------------------------------------------------------- story-boss crown
C.STORY = {
  health = {[0] = 1, 1.5, 2},     -- the Health option
  hpTimes = 2,
  mirror = {strength = 1.25, magickPower = 1.25, attackPower = 1.25, vitality = 1, speed = 1},
  statuses = {'bravery', 'faith'},
  immunities = {'ko', 'stone', 'petrify', 'doom'},   -- only what ends the fight outright
  lpTimes = 3,
  mpFloor = 400,
  defenceLow = 1, defenceHigh = 1,   -- a crowned boss does not roll its Defence (decision D-1)
}

-- The fields a crown_table.lua entry may carry; they are the keys of C.STORY (plus kind, spell, loot and label).
-- Anything else is ignored and reported; a field of the wrong type or range falls back to the default crown for that
-- field only, so a damaged table can never stop a crown.
C.ENTRY_FIELDS = {'label', 'kind', 'spell', 'statuses', 'immunities', 'mirror', 'hpTimes', 'mpFloor', 'defenceLow',
                  'defenceHigh', 'lpTimes', 'loot'}
C.MIRROR_STATS = {'strength', 'magickPower', 'attackPower', 'vitality', 'speed'}

local function isInteger(v) return type(v) == 'number' and v == math.floor(v) end

local function nameList(value)
  if type(value) ~= 'table' then return nil end
  local out = {}
  for _, name in ipairs(value) do
    if type(name) ~= 'string' then return nil end
    out[#out + 1] = name
  end
  return out
end

-- The crown one boss gets: the default crown with the entry's valid fields laid over it. Returns the resolved crown
-- and a list of the problems found in the entry. kind: nil = a random kind (the default crown), false = no kind (no
-- aura, absorb, name prefix or spell), or a C.KINDS index. spell: true = the kind's tier spell, false = none.
function C.resolveEntry(entry)
  local S = C.STORY
  local crown = {kind = nil, spell = true, statuses = S.statuses, immunities = S.immunities, mirror = {},
                 hpTimes = S.hpTimes, mpFloor = S.mpFloor, lpTimes = S.lpTimes, loot = 'rarestStealDrops',
                 defence = {S.defenceLow, S.defenceHigh}, authored = false}
  for name, times in pairs(S.mirror) do crown.mirror[name] = times end
  local problems = {}
  if type(entry) ~= 'table' then return crown, problems end
  crown.authored = true
  local known = {}
  for _, field in ipairs(C.ENTRY_FIELDS) do known[field] = true end
  for field in pairs(entry) do
    if not known[field] then problems[#problems + 1] = 'unknown field ' .. tostring(field) end
  end
  local function bad(field) problems[#problems + 1] = 'bad ' .. field end
  local function number(field, low, high, integer)
    local value = entry[field]
    if value == nil then return nil end
    if type(value) == 'number' and value >= low and value <= high and (not integer or isInteger(value)) then
      return integer and math.tointeger(value) or value
    end
    bad(field)
    return nil
  end

  local kind = entry.kind
  if kind == false then
    crown.kind = false
  elseif kind ~= nil then
    if isInteger(kind) and kind >= 0 and kind < C.KIND_COUNT then crown.kind = math.tointeger(kind) else bad('kind') end
  end
  if entry.spell ~= nil then
    if type(entry.spell) == 'boolean' then crown.spell = entry.spell else bad('spell') end
  end
  if entry.loot ~= nil then
    if entry.loot == 'rarestStealDrops' or entry.loot == 'native' then crown.loot = entry.loot else bad('loot') end
  end
  for _, field in ipairs({'statuses', 'immunities'}) do
    if entry[field] ~= nil then
      local list = nameList(entry[field])
      if list then crown[field] = list else bad(field) end
    end
  end
  if entry.mirror ~= nil then
    if type(entry.mirror) ~= 'table' then
      bad('mirror')
    else
      local isStat = {}
      for _, name in ipairs(C.MIRROR_STATS) do isStat[name] = true end
      for name, times in pairs(entry.mirror) do
        if isStat[name] and type(times) == 'number' and times >= 0 and times <= 3 then crown.mirror[name] = times
        else problems[#problems + 1] = 'bad mirror ' .. tostring(name) end
      end
    end
  end
  crown.hpTimes = number('hpTimes', 1, 8) or crown.hpTimes
  crown.mpFloor = number('mpFloor', 0, 999, true) or crown.mpFloor
  crown.lpTimes = number('lpTimes', 1, 3) or crown.lpTimes
  local low, high = number('defenceLow', 0.01, 3), number('defenceHigh', 0.01, 3)
  if low or high then
    low, high = low or crown.defence[1], high or crown.defence[2]
    if low <= high then crown.defence = {low, high} else bad('defence range') end
  end
  return crown, problems
end

local function scale(stats, name, times, high)
  stats[name] = clamp(C.round(stats[name] * times), 0, high)
end

-- The rarest steal (the last filled slot of three) always drops: it takes the last empty drop slot, or its own slot if
-- the drop list already holds it, and that slot is marked always-dropped. A full list with no copy is left as it is.
function C.alwaysDropRarest(foe)
  local steals, drops = foe.unit.steals, foe.unit.drops
  local item
  for i = 3, 1, -1 do
    local v = steals[i]
    if v ~= 0 and v ~= 0xFFFF then item = v; break end
  end
  if not item then return false end
  local slot
  for i = 1, 5 do if drops[i] == item then slot = i; break end end
  if not slot then
    for i = 5, 1, -1 do
      if drops[i] == 0 or drops[i] == 0xFFFF then slot = i; break end
    end
    if not slot then return false end
    drops[slot] = item
  end
  foe.abilities.alwaysDrops.add('drop' .. slot)
  return true
end

-- A new maximum keeps the foe's share of it, so the crown's extra HP is filled in once the stats have been refreshed.
function C.fillHealth(foe)
  if foe.keep then
    foe.keep.currentHp = foe.defaultStats.maxHp
    foe.keep.currentMp = foe.defaultStats.maxMp
  end
end

-- Add names to a set one by one, so a name the game's API does not know costs only itself.
local function addAll(set, names)
  local failed = {}
  for _, name in ipairs(names) do
    local ok = pcall(set.add, name)
    if not ok then failed[#failed + 1] = name end
  end
  return failed
end

-- opts: health (0..2), random(lo, hi) integer, uniform(lo, hi) float, entry (the boss's crown_table.lua entry or nil).
function C.crownStoryBoss(foe, party, opts)
  local stats, extra = foe.defaultStats, foe.additiveStats
  local crown, problems = C.resolveEntry(opts.entry)
  if #problems > 0 then
    log('crown entry for unit ' .. tostring(foe.unit and foe.unit.name) .. ': ' .. table.concat(problems, ', '))
  end
  local kind = crown.kind
  if kind == nil then kind = C.chooseKind(foe.class.affinityWeak, opts.random) end
  local record = kind and C.KINDS[kind] or nil

  for _, name in ipairs(C.MIRROR_STATS) do
    local times = crown.mirror[name]
    if times and times > 0 then
      local want = C.round(party[name] * times)
      if want > stats[name] then stats[name] = clamp(want, 0, 99) end
    end
  end
  if stats.maxMp < crown.mpFloor then stats.maxMp = crown.mpFloor end
  local hpTimes = crown.hpTimes * (C.STORY.health[opts.health or 0] or 1)
  scale(stats, 'maxHp', hpTimes, 1000000000)
  if extra then scale(extra, 'maxHp', hpTimes, 1000000000) end
  local low, high = crown.defence[1], crown.defence[2]
  local defence = low == high and low or opts.uniform(low, high)
  if defence ~= 1 then
    scale(stats, 'defense', defence, 99)
    if extra then scale(extra, 'defense', defence, 99) end
  end
  stats.licensePoints = clamp(stats.licensePoints * crown.lpTimes, 0, 255)

  if #crown.statuses > 0 then
    local failed = addAll(foe.abilities.statuses, crown.statuses)
    if #failed > 0 then log('crown statuses not set: ' .. table.concat(failed, ', ')) end
  end
  if #crown.immunities > 0 then
    local failed = addAll(foe.abilities.immunities, crown.immunities)
    if #failed > 0 then log('crown immunities not set: ' .. table.concat(failed, ', ')) end
  end
  if record then
    -- A kind whose element the boss is weak to would pit the absorb against the weakness: an authored kind keeps its
    -- aura and its name but not that absorb. (A random kind never picks such an element.)
    if record.element and not foe.class.affinityWeak.has(C.ELEMENTS[record.element]) then
      foe.class.affinityAbsorb.add(C.ELEMENTS[record.element])
    end
    foe.efx = C.AURA
    foe.efxColor = record.color
    foe.setName(record.name .. ' {species}')
  end
  if crown.loot == 'rarestStealDrops' then C.alwaysDropRarest(foe) end
  local spell = record and crown.spell and C.spellFor(kind, party.level) or nil
  local slots = spell and C.pointSlots(foe, spell, false) or 0
  return {kind = kind, spell = spell, slots = slots, authored = crown.authored}
end

return C
