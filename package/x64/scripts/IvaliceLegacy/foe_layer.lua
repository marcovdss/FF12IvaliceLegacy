-- Ivalice Legacy: the enemy layer (scripts/IvaliceLegacy/foe_layer.lua). Loaded by scripts/IvaliceLegacyFoes.lua.
--
-- Legacy's own layer on the Instanced Foes API. Dynamic Bosses 1.0.2 and Instanced Foes stay exactly as their authors
-- ship them; Legacy is built on them and owns no copy of their files. Nothing is taken from Dynamic Bosses' source:
-- its behaviour is read from its documentation, its settings file (read-only) and its kind list Foes.lua (data only).
--
-- Every spawn (SPAWN, RESPAWN_NEW_UNIT) is queued, claimed once, and handled in a settle pass one millisecond later,
-- after every other mod's spawn listener has run, whatever the order they were registered in. The pass finds each foe
-- again by its life id and then:
--   * a common foe Dynamic Bosses crowned (permanent Protect, Shell, Bravery and Faith): offence to 1.25 x the party,
--     and its AI back on a guarded script (foe_crown.lua);
--   * a main-story boss (story_bosses.lua): Legacy's crown, written here (foe_crown.lua); a boss with an entry in the
--     generated crown_table.lua takes its authored crown (fixed kind and aura, its statuses, offence mirror, Defence);
--   * any other foe Dynamic Bosses does not roll itself, by its kind in Foes.lua (decision D-1): every kind rolls HP and
--     MP x1-1.5 and size x0.95-1.10; only commons also roll Vitality and Defence (marks, bosses and Kings keep both)
--     and get a faint tint; Kings get the threat floor;
--   * every hostile foe of the main game (not Trial Mode): its EXP is scaled so that, under Full Party EXP Share, each
--     member earns what a native active member would (see L.shareExp).
-- Legacy ships Dynamic Bosses' settings with randomizeCommons 0 and ignore bosses/marks/rare games 1, so Dynamic
-- Bosses only crowns commons; this layer re-reads that file at start and on every map change and rolls a kind only
-- when Dynamic Bosses does not, so nothing is rolled twice.
local L = {version = '0.1.0'}

local function log(text) print('Legacy foes: ' .. text) end
L.log = log

-- ---------------------------------------------------------------- facts (VA, as the Lua Loader addresses them)
-- Current location id: save image 0x2164280 + 0x200 + 0x1044 (the native getter 0x3148F0 reads the u32 at the block
-- 0x2EF2B0 returns, +0x1044, and clamps a negative id to 0).
L.LOCATION = 0x021654C4
-- The native town predicate 0x377C20: the area table pointer at VA 0x2ADD100, u32 count at +4, and for location n the
-- u16 at table + 8 + 3n; bit 15 is the town bit. An id past the count, or no table, is not a town.
L.AREA_TABLE = 0x02ADD100
-- Story progress: the word variable of the VM's variable group 3 (the reader 0x264640, called from the group
-- dispatcher 0x321190 case 3): valid while the byte at VA 0x2098E20 has bit 0 set and the table pointer at VA
-- 0x2098E58 is non-null; the value is the u16 at index 0 of that table.
L.STORY_FLAG, L.STORY_TABLE = 0x02098E20, 0x02098E58
-- Trial Mode stages are the locations of the tri_a01..tri_t05 maps. Static check of the Steam exe (1.0.4.0) against its
-- location-to-map table (VA 0x1EE23A0): those maps fill exactly the ids 1155..1311 and no other location. L.trialPredicate
-- stays as the hook for a better test. Unreadable location: unknown, which blocks the story crown.
L.TRIAL_FIRST, L.TRIAL_LAST = 1155, 1311
-- Dynamic Bosses publishes a ready flag on Instanced Foes' pattern; the name is inferred, and only logged.
L.DYNAMIC_FLAG = 'fehdead_dynamicbosses.ready'
-- No verified story value marks "the very beginning", so no opening is exempt (0 = nothing exempt).
L.QUIET_BELOW_STORY = 0

L.DYNAMIC_SETTINGS = 'scripts/config/DynamicFoes/modMenu.json'
L.DYNAMIC_KINDS = 'scripts/DynamicFoes/Foes.lua'
L.OWN_SETTINGS = 'scripts/config/IvaliceLegacy/foes.json'
L.OWNER = 'IvaliceLegacy.Foes'

L.VARIATION = {low = 1, high = 1.5}      -- HP and MP of every kind; Vitality and Defence of commons only
L.SIZE = {low = 0.95, high = 1.10}
L.KING_FLOOR = {offence = 1.25, other = 1}

-- EXP: the divisor a native party has (three active members) and the bounds of the scale factor.
L.EXP = {native = 3, low = 0.25, high = 9}
-- () -> the number of members Full Party EXP Share divides by, and () -> the guests among them; both nil while Full Party
-- EXP Share is not detected. The defaults are L.readEligible and L.readGuests below; a test may inject others.
L.eligibleReader = nil
L.guestReader = nil
L.memberReader = nil     -- () -> the active non-guest members; Instanced Foes' party count is the fallback

L.settings = {crownStoryBosses = 1, storyHp = 0, foeVariation = 1, foeTint = 1, kingThreat = 1, expShare = 1}
L.dynamic = nil          -- Dynamic Bosses' settings, nil while unreadable
L.kinds = nil            -- Foes.lua
L.crownTable = {}        -- crown_table.lua: authored story-boss crowns by unit name id
L.rng = {
  uniform = function(low, high) return low + (high - low) * math.random() end,
  int = function(low, high) return math.random(low, high) end,
  sign = function() return math.random(0, 1) == 0 and -1 or 1 end,
}
L.moduleLoader = function(name)
  local chunk, err = loadfile('scripts/IvaliceLegacy/' .. name .. '.lua', 't', _ENV)
  if not chunk then error(err) end
  return chunk()
end

-- ---------------------------------------------------------------- the world
function L.location()
  local value = memory.s32[L.LOCATION]
  if type(value) ~= 'number' then return nil end
  return value < 0 and 0 or value
end

function L.isTown(location)
  if not location then return false end
  local table = memory.u64[L.AREA_TABLE]
  if type(table) ~= 'number' or table == 0 then return false end
  local count = memory.u32[table + 4]
  if location >= count then return false end
  local word = memory.u8[table + 8 + 3 * location] | (memory.u8[table + 9 + 3 * location] << 8)
  return (word >> 15) & 1 == 1
end

-- L.trialPredicate is the hook for a verified Trial Mode test (location -> boolean); until one is installed the id
-- range decides. A predicate that errors or answers a non-boolean counts as unknown, which fails closed.
L.trialPredicate = nil

function L.isTrial(location)
  if not location then return nil end
  if type(L.trialPredicate) == 'function' then
    local ok, answer = pcall(L.trialPredicate, location)
    if ok and type(answer) == 'boolean' then return answer end
    return nil
  end
  return location >= L.TRIAL_FIRST and location <= L.TRIAL_LAST
end

function L.story()
  if memory.u8[L.STORY_FLAG] & 1 ~= 1 then return nil end
  local table = memory.u64[L.STORY_TABLE]
  if type(table) ~= 'number' or table == 0 then return nil end
  return memory.u16[table]
end

-- ---------------------------------------------------------------- settings
local function integerIn(value, low, high, fallback)
  if math.type(value) == 'float' and value == math.floor(value) then value = math.tointeger(value) end
  if math.type(value) ~= 'integer' or value < low or value > high then return fallback end
  return value
end

-- Dynamic Bosses' file, read only. nil when it cannot be read as a table with the five keys this layer depends on.
function L.readDynamic()
  local data = config and config.loadJson and config.loadJson(L.DYNAMIC_SETTINGS)
  if type(data) ~= 'table' then return nil end
  local read = {
    randomizeCommons = integerIn(data.randomizeCommons, 0, 1),
    ignoreBosses = integerIn(data.ignoreBosses, 0, 1),
    ignoreMarks = integerIn(data.ignoreMarks, 0, 1),
    ignoreRareGames = integerIn(data.ignoreRareGames, 0, 1),
    ignoreAllies = integerIn(data.ignoreAllies, 0, 1),
  }
  for _, key in ipairs({'randomizeCommons', 'ignoreBosses', 'ignoreMarks', 'ignoreRareGames'}) do
    if read[key] == nil then return nil end
  end
  return read
end

function L.refreshDynamic()
  local read = L.readDynamic()
  if not read then
    if L.dynamic ~= false then log('Dynamic Bosses settings are unreadable; foe rolls and the story crown are off until they are') end
    L.dynamic = false
    return false
  end
  local before = L.dynamic
  L.dynamic = read
  if not before or before == false or before.ignoreBosses ~= read.ignoreBosses then
    if read.ignoreBosses == 0 then
      log('Dynamic Bosses rolls bosses itself (ignoreBosses 0): the story crown stays off so nothing is rolled twice')
    end
  end
  return true
end

-- Does Dynamic Bosses roll this kind itself? (rolling = no second roll here). Allies are never touched here.
function L.dynamicRolls(kind)
  local d = L.dynamic
  if not d then return true end
  if kind == 'common' then return d.randomizeCommons == 1 end
  if kind == 'mark' then return d.ignoreMarks == 0 end
  if kind == 'boss' then return d.ignoreBosses == 0 end
  if kind == 'rare' then return d.ignoreRareGames == 0 end
  return true
end

function L.loadOwnSettings()
  local data = config and config.loadJson and config.loadJson(L.OWN_SETTINGS)
  if type(data) ~= 'table' then return end
  L.settings.crownStoryBosses = integerIn(data.crownStoryBosses, 0, 1, L.settings.crownStoryBosses)
  L.settings.storyHp = integerIn(data.storyHp, 0, 2, L.settings.storyHp)
  L.settings.foeVariation = integerIn(data.foeVariation, 0, 1, L.settings.foeVariation)
  L.settings.foeTint = integerIn(data.foeTint, 0, 1, L.settings.foeTint)
  L.settings.kingThreat = integerIn(data.kingThreat, 0, 1, L.settings.kingThreat)
  L.settings.expShare = integerIn(data.expShare, 0, 1, L.settings.expShare)
end

function L.saveOwnSettings()
  if config and config.saveJson then
    local ok, failure = pcall(config.saveJson, L.OWN_SETTINGS, {
      crownStoryBosses = L.settings.crownStoryBosses, storyHp = L.settings.storyHp,
      foeVariation = L.settings.foeVariation, foeTint = L.settings.foeTint, kingThreat = L.settings.kingThreat,
      expShare = L.settings.expShare,
    })
    if not ok or failure == false then log('settings were not saved (' .. tostring(failure) .. ')') end
  end
end

-- ---------------------------------------------------------------- one foe
-- HP and MP for every kind this layer rolls; Vitality and Defence only for commons (decision D-1: a mark, a boss or a
-- King is carved by hand and keeps both exactly as carved).
local function draw(foe, kind)
  local stats, extra = foe.defaultStats, foe.additiveStats
  local names = {{'maxHp', 1000000000}, {'maxMp', 999}}
  if kind == 'common' then
    names[#names + 1] = {'vitality', 99}
    names[#names + 1] = {'defense', 99}
  end
  local V = L.VARIATION
  for _, entry in ipairs(names) do
    local name, cap = entry[1], entry[2]
    local times = L.rng.uniform(V.low, V.high)
    stats[name] = L.crown.clamp(L.crown.round(stats[name] * times), 0, cap)
    if extra then extra[name] = L.crown.clamp(L.crown.round(extra[name] * times), 0, cap) end
  end
end

local function resize(foe)
  local times = L.rng.uniform(L.SIZE.low, L.SIZE.high)
  local unit = foe.unit
  for _, axis in ipairs({'sizeX', 'sizeY', 'sizeZ'}) do
    unit[axis] = L.crown.clamp(L.crown.round(unit[axis] * times), 1, 65535)
  end
end

function L.kingThreat(foe, party)
  local stats, F = foe.defaultStats, L.KING_FLOOR
  for _, name in ipairs(L.crown.OFFENCE) do
    local want = L.crown.round(party[name] * F.offence)
    if want > stats[name] then stats[name] = L.crown.clamp(want, 0, 99) end
  end
  for _, name in ipairs({'vitality', 'speed'}) do
    local want = L.crown.round(party[name] * F.other)
    if want > stats[name] then stats[name] = L.crown.clamp(want, 0, 99) end
  end
end

L.counts = {crowned = 0, story = 0, drawn = 0, kings = 0, tinted = 0, exp = 0}
local logged = 0
local function note(text)
  if logged < 24 then logged = logged + 1; log(text) end
end

-- ---------------------------------------------------------------- EXP share
-- Full Party EXP Share divides each foe's EXP pool over every eligible member (reserves too), where a native party
-- divides it over its active members: three, plus guests. Multiplying the foe's EXP by eligible / (min(members, 3) +
-- guests) gives each member what a native active member would earn, whatever the party size (Legacy has four members).
-- The bounds keep a misread count from producing an absurd reward.
local function wholeNumber(value, low, high)
  if type(value) ~= 'number' or value ~= value or value ~= math.floor(value) then return nil end
  if value < low or value > high then return nil end
  return value
end

-- Only a whole eligible count 1..9, whole members >= 1 and whole guests 0..3 (nil = none) are believed; anything else
-- is no answer, and a foe's EXP is then left alone.
function L.expFactor(eligible, members, guests)
  local E = L.EXP
  eligible, members = wholeNumber(eligible, 1, 9), wholeNumber(members, 1, 99)
  if not eligible or not members then return nil end
  if guests == nil then guests = 0 else guests = wholeNumber(guests, 0, 3) end
  if not guests then return nil end
  local factor = eligible / (math.min(members, E.native) + guests)
  if factor ~= factor then return nil end
  return L.crown.clamp(factor, E.low, E.high)
end

-- ---- the party layout (static facts of the Steam exe 1.0.4.0, base 0x120000; nothing here was run)
-- Full Party EXP Share (FPES) divides a foe's EXP pool by the number of members its routine at 0x2F8BB0 counts. It
-- patches 0x2F8C96 to a jump (0xE9) and the loop bound at 0x2F8CE4 to 9, so both bytes tell it is live. The party block
-- is the u64 at 0x02EBF190; layout 0 is nine u16 slots at block + 0x5A48 (layout k at + 0x12 * k), 0xFFFF = empty, the low
-- byte = member id. A member record is block + 8 + id * 0x1C8 (id 0..0x27); its two status words at +0x3C and +0x64 are
-- tested with 0x80000003 (KO, stone and the like). Ids 7..0x19 are guests, ids 0x1A..0x27 never count.
L.FPES_LOOP_BYTE, L.FPES_JUMP_BYTE = 0x2F8CE4, 0x2F8C96
L.PARTY_BLOCK = 0x02EBF190
L.LAYOUT0, L.LAYOUT_SLOTS = 0x5A48, 9
L.MEMBER_FIRST, L.MEMBER_STRIDE, L.MEMBER_MAX_ID = 8, 0x1C8, 0x27
L.MEMBER_ID, L.MEMBER_KIND = 4, 5
L.MEMBER_STATUS_A, L.MEMBER_STATUS_B, L.STATUS_MASK = 0x3C, 0x64, 0x80000003
L.GUEST_FIRST, L.GUEST_LAST = 7, 0x19

function L.fpesActive()
  return memory.u8[L.FPES_LOOP_BYTE] == 9 and memory.u8[L.FPES_JUMP_BYTE] == 0xE9
end

-- (eligible, guests, members) as the party layout stands right now, or nil. eligible counts every member FPES divides
-- by (guests included); guests are those with ids 7..0x19; members = eligible - guests, the non-guest members that pass
-- the same status filter (the native divisor is built from this, so a member who is down cannot lower it).
-- Each member id counts once, and a slot counts only when its record is real (the byte at +4 is the id and the byte at
-- +5 is 0): an unreadable or zeroed block therefore cannot invent nine members. A party with no non-guest member is not
-- believed either. This is a snapshot taken when the foe spawns; FPES applies its filter again when the foe dies, so a
-- member who falls in between can make the real share differ a little.
function L.readParty()
  if not L.fpesActive() then return nil end
  local block = memory.u64[L.PARTY_BLOCK]
  if type(block) ~= 'number' or block == 0 then return nil end
  local seen, eligible, guests = {}, 0, 0
  for slot = 0, L.LAYOUT_SLOTS - 1 do
    local raw = memory.u16[block + L.LAYOUT0 + 2 * slot]
    local id = raw ~= 0xFFFF and raw & 0xFF or nil
    if id and id <= L.GUEST_LAST and not seen[id] then
      local record = block + L.MEMBER_FIRST + id * L.MEMBER_STRIDE
      if memory.u8[record + L.MEMBER_ID] == id and memory.u8[record + L.MEMBER_KIND] == 0 then
        seen[id] = true
        local status = memory.u32[record + L.MEMBER_STATUS_A] | memory.u32[record + L.MEMBER_STATUS_B]
        if status & L.STATUS_MASK == 0 then
          eligible = eligible + 1
          if id >= L.GUEST_FIRST then guests = guests + 1 end
        end
      end
    end
  end
  local members = eligible - guests
  if eligible < 1 or members < 1 then return nil end
  return eligible, guests, members
end

function L.readEligible() return (L.readParty()) end
function L.readGuests() local _, guests = L.readParty(); return guests or 0 end
function L.readMembers() local _, _, members = L.readParty(); return members end
L.eligibleReader, L.guestReader, L.memberReader = L.readEligible, L.readGuests, L.readMembers

-- Is the EXP share able to act? (an injected reader counts as able; the defaults need FPES live)
function L.expAvailable()
  if type(L.eligibleReader) ~= 'function' then return false end
  if L.eligibleReader ~= L.readEligible then return true end
  return L.fpesActive()
end

-- members comes from the same layout as eligible (see L.readParty); only when it has no answer is the count of
-- main-character units Instanced Foes finds used. With no reader, no FPES or any unreadable value, nothing is returned
-- and foe EXP is left as it is, so nothing is ever scaled from a guess.
function L.expInputs()
  local reader = L.eligibleReader
  if type(reader) ~= 'function' then return nil end
  local ok, eligible = pcall(reader)
  if not ok or eligible == nil then return nil end
  local members
  if type(L.memberReader) == 'function' then
    local read, value = pcall(L.memberReader)
    if read and type(value) == 'number' then members = value end
  end
  if members == nil then
    local party = L.foes.party()
    if not party then return nil end
    members = party.count
  end
  local guests = 0
  if type(L.guestReader) == 'function' then
    local read, value = pcall(L.guestReader)
    if not read or type(value) ~= 'number' then return nil end
    guests = value
  end
  return eligible, members, guests
end

local EXP_CAP = 4294967295

-- true when the foe's EXP was changed
function L.shareExp(foe)
  if L.settings.expShare ~= 1 then return false end
  local eligible, members, guests = L.expInputs()
  local factor = L.expFactor(eligible, members, guests)
  if not factor and type(guests) == 'number' and guests > 3 and not L.loggedGuests then
    L.loggedGuests = true
    log('EXP share is off: ' .. tostring(guests) .. ' guests are in the party (at most 3 are believed)')
  end
  if not factor or factor == 1 then return false end
  local stats, extra = foe.defaultStats, foe.additiveStats
  if type(stats.experience) ~= 'number' then return false end
  local function scaled(value)
    if value ~= value then return value end
    local made = L.crown.clamp(L.crown.round(value * factor), 0, EXP_CAP)
    if value > 0 and made < 1 then made = 1 end          -- a foe that gives EXP never gives none
    return made
  end
  stats.experience = scaled(stats.experience)
  if extra and type(extra.experience) == 'number' then extra.experience = scaled(extra.experience) end
  L.counts.exp = L.counts.exp + 1
  return true
end

-- Returns what was done, for the log and the tests.
function L.process(foe, context)
  if not foe.keep or foe.keep.isFoe ~= 1 then return 'ally' end
  if context.town then return 'town' end
  -- Marco, 2026-11-17: Trial Mode is not touched yet. No draw, crown, size, tint, offence fix or EXP change there; an
  -- unreadable location counts as a possible Trial stage and is left alone too.
  if context.trial ~= false then return 'trial' end
  -- EXP first, so the single refresh at the end of the foe's turn (the story crown refreshes itself) takes it.
  local ok, shared = pcall(L.shareExp, foe)
  if not ok then log('EXP share skipped for unit ' .. tostring(foe.unit and foe.unit.name) .. ': ' .. tostring(shared)) end
  local result = L.shape(foe, context)
  if ok and shared then result = result .. ' + EXP shared' end
  return result
end

-- The crown, roll and tint of a hostile main-game foe (town, ally and Trial are filtered by L.process).
function L.shape(foe, context)
  local nameId = foe.unit.name
  local kind = L.kinds and L.kinds[nameId]
  local settings = L.settings

  if L.dynamic and L.dynamic.ignoreBosses == 1 and settings.crownStoryBosses == 1 and L.story_bosses.is(foe, context) then
    local party = L.foes.party()
    if not party then return 'no party' end
    local made = L.crown.crownStoryBoss(foe, party, {health = settings.storyHp, random = L.rng.int,
                                                      uniform = L.rng.uniform, entry = L.crownTable[nameId]})
    pcall(foe.refresh)                 -- take the new maximum first, then fill it
    L.crown.fillHealth(foe)
    L.counts.story = L.counts.story + 1
    note(string.format('story boss crowned: unit %d, kind %s%s, spell %s, %d AI slot(s)', nameId,
      L.crown.KINDS[made.kind].name, made.authored and ' (authored)' or '', tostring(made.spell), made.slots))
    return 'story'
  end

  if kind == 'common' and L.crown.crownedByDynamic(foe) then
    local party = L.foes.party()
    if not party then return 'no party' end
    local made = L.crown.fixCrowned(foe, party)
    L.counts.crowned = L.counts.crowned + 1
    note(string.format('crowned common: unit %d, offence %.2f x party, spell %s, %d AI slot(s)', nameId,
      L.crown.STAT_FLOOR, tostring(made.spell), made.slots))
    return 'crowned'
  end

  if not kind or kind == 'ally' or L.dynamicRolls(kind) then return 'left to Dynamic Bosses or unlisted' end
  local did = {}
  if settings.foeVariation == 1 then
    draw(foe, kind)
    resize(foe)
    L.counts.drawn = L.counts.drawn + 1
    did[#did + 1] = 'rolled'
    if kind == 'rare' and settings.kingThreat == 1 then
      local party = L.foes.party()
      if party then L.kingThreat(foe, party); L.counts.kings = L.counts.kings + 1; did[#did + 1] = 'king threat' end
    end
  end
  if kind == 'common' and settings.foeTint == 1 and L.tint.apply(foe, L.rng.uniform, L.rng.sign) then
    L.counts.tinted = L.counts.tinted + 1
    did[#did + 1] = 'tinted'
  end
  return #did > 0 and table.concat(did, ', ') or 'nothing'
end

-- ---------------------------------------------------------------- queue and settle pass
L.pending, L.done, L.scheduled = {}, {}, false

function L.enqueue(lifeId)
  if L.done[lifeId] then return false end
  L.done[lifeId] = true
  L.pending[#L.pending + 1] = lifeId
  return true
end

function L.currentContext()
  local location = L.location()
  return {location = location, town = L.isTown(location), trial = L.isTrial(location), story = L.story()}
end

-- Dynamic Bosses crowns on the game thread while this pass runs on the script thread, so a foe may be seen with its
-- crown half written (some statuses, or all four but no AI slot yet). Such a foe is put back once, for the next pass;
-- the second time it is acted on as it stands, so nothing waits forever.
L.requeued = {}

local function foeMap(foes)
  local map = {}
  for _, foe in ipairs(foes.list()) do
    local ok, id = pcall(function() return foe.lifeId end)
    if ok and id ~= nil then map[id] = foe end
  end
  return map
end

-- One queued life id: nil when the foe is gone or was put back, else the result of L.process.
-- Is Dynamic Bosses possibly not done with this foe? A crown half written, or a common with no crown status yet (its
-- listener may run after this pass was queued): the foe waits one pass, never more.
function L.mayStillBeCrowned(foe)
  local progress = L.crown.crownProgress(foe)
  if progress == 'partial' then return true end
  if progress == 'absent' then
    local kind = L.kinds and L.kinds[foe.unit.name]
    return kind == 'common'
  end
  return false
end

local function handle(lifeId, byLife, context)
  local foes = L.foes
  if not foes.isPresent(lifeId) then return nil end
  local foe = byLife[lifeId]
  if not foe then return nil end
  if not L.requeued[lifeId] and L.mayStillBeCrowned(foe) then
    L.requeued[lifeId] = true
    L.pending[#L.pending + 1] = lifeId
    return nil, true
  end
  if L.claimAt and not foe.claim(L.claimAt) then return nil end
  local ok, result = pcall(L.process, foe, context)
  if not ok then
    log('foe ' .. tostring(lifeId) .. ' left as it was: ' .. tostring(result))
    return nil
  end
  -- The story crown refreshes the foe itself (it must fill the new maximum), so it is refreshed once.
  if result ~= 'ally' and result ~= 'town' and result ~= 'trial' and result:sub(1, 5) ~= 'story' then
    local fine, failure = pcall(foe.refresh)
    if not fine then log('refresh failed for ' .. tostring(lifeId) .. ': ' .. tostring(failure)) end
  end
  return result
end

function L.settle()
  L.scheduled = false
  local queue = L.pending
  L.pending = {}
  if #queue == 0 then return end
  L.refreshDynamic()                 -- Dynamic Bosses' file is cheap to read, and the player may have changed it
  local context = L.currentContext()
  if context.story and context.story < L.QUIET_BELOW_STORY then return end
  local foes = L.foes
  local refresh = foes.autoRefresh
  foes.autoRefresh = false
  local listed, byLife = pcall(foeMap, foes)
  if not listed then
    log('the foe list could not be read: ' .. tostring(byLife))
    byLife = {}
  end
  local again = false
  for _, lifeId in ipairs(queue) do
    local ok, result, requeued = pcall(handle, lifeId, byLife, context)
    if not ok then log('foe ' .. tostring(lifeId) .. ' skipped: ' .. tostring(result)) end
    if ok and requeued then again = true end
  end
  foes.autoRefresh = refresh
  if again then L.schedule() end
end

function L.schedule()
  if L.scheduled then return end
  L.scheduled = true
  event.executeAfterMs(1, L.settle)
end

function L.onSpawn(foe, reason)
  if reason ~= L.foes.SPAWN and reason ~= L.foes.RESPAWN_NEW_UNIT then return end
  if L.enqueue(foe.lifeId) then L.schedule() end
end

-- ---------------------------------------------------------------- Mod Menu
L.MENU_ID = 'IvaliceLegacyFoes'

function L.page()
  local function changed(_, field, value) L.settings[field] = value end
  local function row(id, label, help, default)
    return {id = id, label = label, type = 'toggle', options = {'Off', 'On'}, default = default, help = help,
            callback = function(modId, fieldId, value) L.settings[fieldId] = value end}
  end
  return {
    id = L.MENU_ID, name = 'Foes', group = {id = 'IvaliceLegacy', name = 'Ivalice Legacy'},
    save = {label = 'Save settings', callback = function(_, values)
      for key, value in pairs(values) do if L.settings[key] ~= nil then L.settings[key] = value end end
      L.saveOwnSettings()
    end},
    schema = {
      row('crownStoryBosses', 'Crown story bosses',
        'On: every main-story boss is crowned: more HP, an element, an aura, magick, better loot. Off: never. Foes in Trial Mode are always left as they are.',
        L.settings.crownStoryBosses),
      {id = 'storyHp', label = 'Story boss health', type = 'carousel', options = {'Normal', 'Moderate', 'Very high'},
       default = L.settings.storyHp, help = 'Story bosses always have twice their own HP; this multiplies it again.',
       callback = changed},
      row('foeVariation', 'Foe variation',
        'On: each foe rolls its own HP, MP and size, and ordinary foes also roll Vitality and Defence. Marks, bosses and Kings keep both, and Kings threaten the party.',
        L.settings.foeVariation),
      row('foeTint', 'Foe tint', 'On: ordinary foes of one species differ slightly in brightness.', L.settings.foeTint),
      row('kingThreat', 'King threat', 'On: a King hits at least as hard as the party average times 1.25.',
        L.settings.kingThreat),
      row('expShare', L.expAvailable() and 'EXP share' or 'EXP share (not active)',
        'On: foe EXP is scaled so every party member earns what a native active member would. Off: foe EXP is left as it is.'
        .. (L.expAvailable() and '' or ' This has no effect now: Full Party EXP Share is not detected.'),
        L.settings.expShare),
    },
  }
end

-- ---------------------------------------------------------------- start
function L.loadKinds()
  local chunk = loadfile(L.DYNAMIC_KINDS, 't', {})
  if not chunk then return nil end
  local ok, data = pcall(chunk)
  return ok and type(data) == 'table' and data or nil
end

-- The generated table of authored story-boss crowns. A missing or damaged file leaves every boss on the default crown.
function L.loadCrownTable()
  local ok, data = pcall(L.moduleLoader, 'crown_table')
  if ok and type(data) == 'table' and type(data.entries) == 'table' then
    L.crownTable = data.entries
    local count = 0
    for _ in pairs(data.entries) do count = count + 1 end
    log('crown table: ' .. count .. ' authored boss crown(s)')
  else
    L.crownTable = {}
    log('crown table unavailable (' .. tostring(ok and 'not a table' or data) .. '): story bosses take the default crown')
  end
end

function L.begin(foes)
  L.foes = foes
  L.crown = L.moduleLoader('foe_crown')
  L.story_bosses = L.moduleLoader('story_bosses')
  L.tint = L.moduleLoader('foe_tint')
  L.loadCrownTable()
  L.loadOwnSettings()
  L.kinds = L.loadKinds()
  if not L.kinds then log('the kind list of Dynamic Bosses is missing: foe rolls and the offence fix of crowned commons are off (the story crown still works)') end
  L.refreshDynamic()
  local at = foes.reserve(L.OWNER, 8)
  L.claimAt = (type(at) == 'number' and at >= 0) and at or nil
  if not L.claimAt then log('no user data block: foes are claimed by life id only') end
  foes.onSpawn(L.onSpawn)
  if event.registerEventAsync then
    event.registerEventAsync('onMapJump', function(location)
      L.refreshDynamic()
    end)
  end
  if modmenu and modmenu.addTable then
    local ok = modmenu.addTable(L.page())
    if not ok then log('Mod Menu page was not registered') end
  end
  local context = L.currentContext()
  log(string.format('ready (location %s%s%s, story %s, dynamic bosses %s)', tostring(context.location),
    context.town and ', town' or '', context.trial and ', trial' or '', tostring(context.story),
    L.dynamic and 'settings read' or 'settings unreadable'))
end

function L.register()
  local foes = require('scripts.InstancedFoes.Helper')
  local started = false
  event.registerFlagCallback(foes.READY_FLAG, function(_, ready)
    if ready and not started then
      started = true
      local ok, failure = pcall(L.begin, foes)
      if not ok then log('not started (' .. tostring(failure) .. ')') end
    end
  end)
  event.registerFlagCallback(L.DYNAMIC_FLAG, function(_, ready)
    if ready then log('Dynamic Bosses reports ready') end
  end)
end

return L
