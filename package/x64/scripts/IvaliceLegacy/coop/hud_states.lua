-- Ivalice Legacy co-op: the Legacy script states for the HUD overlay (the script-to-screen channel).
--
-- Every PERIOD_MS, from a timer (never a sync hook), this reads the Legacy systems' own view of the states players
-- coordinate on (Broken with seconds left, Sunder's dent, guard fatigue, the party's Guard / Cadence / Flow, the
-- identity procs) and pushes one bounded record through the bridge (PUSH_HUD_STATES). The co-op module shows it on
-- Target Info, the party rows and the battle log while it is fresh; when this stops pushing, those pieces simply go
-- away. Nothing here changes a rule: it only reads.
--
-- Where the view comes from: the Family module (family_identities.lua) lives in the FORGE script's Lua state; this
-- script has its own state (the loader gives every scripts/*.lua one), so a global could never be seen from here. The
-- Forge side publishes the view into a small block of memory and registers its address as the global symbol
-- ilc_hud_share (family_identities.hudSharePublish, on a timer of its own); M.share_reader below resolves the symbol and
-- copies the block (a sequence word read before and after, and a checksum, so a half-written block is never used).
--
-- Fail closed: no bridge, no published block (the Forge side not running, or Legacy's Family files not installed), a
-- block older than STALE_US, no clock, or a refused push: nothing is shown and the next tick tries again; three
-- refusals in a row stop it for the session (one log line). A build or encode error never stops the timer.
local M = {}

M.PERIOD_MS = 250
M.MAX_FAILURES = 3
M.STALE_US = 2000000          -- a block published longer ago than this is ignored (the Forge side stopped)
M.SHARE_SYMBOL = 'ilc_hud_share'
-- The block layout: the same constants and a full description sit at the end of family_identities.lua (the writer).
M.SHARE = {MAGIC = 0x31534C49, SEQ = 4, SUM = 8, SIZE_AT = 12, CLOCK = 16, COUNTS = 24, FOES = 28, WORDS = 156,
           PROCS = 172, SIZE = 300, HUD_FOES = 8, HUD_WORDS = 4, HUD_PROCS = 8}

local function whole(v, hi)   -- a non-negative whole number no greater than hi (anything else is 0)
  if type(v) ~= 'number' or v ~= v then return 0 end
  return math.min(hi, math.max(0, math.floor(v)))
end

-- A reader of the shared block. env: memory (the loader's memory table), clock() (microseconds), codec (for the tag
-- names). Returns function() -> view | nil, reason. The view has the shape family_identities.hudStates returns.
function M.share_reader(env)
  local S = M.SHARE
  local mem = env.memory
  local tagName = {}
  for name, code in pairs((env.codec and env.codec.enums and env.codec.enums.hud_tag) or {}) do
    if type(code) == 'number' then tagName[code] = name end
  end
  local base
  local function bytesOf(raw) return {string.byte(raw, 1, -1)} end
  return function()
    if type(mem) ~= 'table' then return nil, 'no memory' end
    if not base then
      local ok, address = pcall(mem.getSymbol, M.SHARE_SYMBOL)
      if not ok or type(address) ~= 'number' or address == 0 then return nil, 'no symbol' end
      base = address
    end
    local bytes, seq
    for _ = 1, 3 do
      local first = mem.readU32(base + S.SEQ)
      if first % 2 == 0 then
        local copy = mem.readArray(base, S.SIZE)
        if type(copy) == 'table' and #copy == S.SIZE and mem.readU32(base + S.SEQ) == first then
          bytes, seq = copy, first
          break
        end
      end
    end
    if not bytes then return nil, 'unstable or unreadable' end
    local chars = {}
    for i = 1, S.SIZE, 100 do chars[#chars + 1] = string.char(table.unpack(bytes, i, math.min(S.SIZE, i + 99))) end
    local raw = table.concat(chars)
    local magic, copySeq, sum, size, clock = string.unpack('<I4I4I4I4i8', raw)
    if magic ~= S.MAGIC or size ~= S.SIZE or copySeq ~= seq then return nil, 'no block' end
    local expect = 0
    for i = S.CLOCK + 1, S.SIZE do expect = (expect + bytes[i] * ((i - 1) % 251 + 1)) % 0x100000000 end
    if expect ~= sum then return nil, 'checksum' end
    local now = env.clock()
    if type(now) ~= 'number' or now <= 0 then return nil, 'no clock' end
    local age = now - clock
    if age < 0 or age > M.STALE_US then return nil, 'stale' end
    local foeN, wordN, procN = string.unpack('<BBB', raw, S.COUNTS + 1)
    if foeN > S.HUD_FOES or wordN > S.HUD_WORDS or procN > S.HUD_PROCS then return nil, 'counts' end
    local view = {foes = {}, words = {}, procs = {}}
    for i = 0, foeN - 1 do
      local record, flags, broken_s, dent_pct, tired = string.unpack('<i8BBBB', raw, S.FOES + i * 16 + 1)
      view.foes[#view.foes + 1] = {record = record, flags = flags, broken_s = broken_s, dent_pct = dent_pct,
                                   tired = tired}
    end
    for i = 0, wordN - 1 do
      local member, kind, value = string.unpack('<BBB', raw, S.WORDS + i * 4 + 1)
      view.words[#view.words + 1] = {member = member, kind = kind, value = value}
    end
    for i = 0, procN - 1 do
      local record, code, age_ms = string.unpack('<i8BxxxI4', raw, S.PROCS + i * 16 + 1)
      if tagName[code] then
        view.procs[#view.procs + 1] = {record = record, tag = tagName[code], age_ms = age_ms + age // 1000}
      end
    end
    return view
  end
end

-- opts: bridge (the coop bridge), codec, after(ms, fn), clock() (microseconds), view() (-> a hudStates-shaped view or
-- nil; see M.share_reader), log(text).
function M.create(opts)
  local C = opts.codec
  local E = C.enums
  local P = {pushes = 0, refused = 0, errors = 0, generation = 0}
  local failures, stopped, armed, logged = 0, false, false, false

  -- The family view -> the wire record (bounded by the schema's counts; unknown tags are dropped; every number is
  -- clamped to a whole number in range, so the encoder never sees a value it would refuse).
  function P.record(view)
    local const = C.schema.const
    P.generation = (P.generation % 0xFFFFFFFF) + 1
    local rec = {header = C.header('HUD_STATES', C.size('ilc_hud_states'), P.generation), foes = {}, words = {},
                 procs = {}}
    for _, f in ipairs(view.foes or {}) do
      if #rec.foes >= const.HUD_FOES then break end
      if type(f.record) == 'number' and f.record > 0 then
        rec.foes[#rec.foes + 1] = {record = f.record, flags = whole(f.flags, 255), broken_s = whole(f.broken_s, 60),
                                   dent_pct = whole(f.dent_pct, 50), tired = whole(f.tired, 4),
                                   spotter_seat = 0, crown_element = 255}
      end
    end
    for _, w in ipairs(view.words or {}) do
      if #rec.words >= const.HUD_WORDS then break end
      if type(w.member) == 'number' and w.member >= 0 and w.member < 40 and whole(w.kind, 255) > 0 then
        rec.words[#rec.words + 1] = {member = math.floor(w.member), kind = whole(w.kind, 255),
                                     value = whole(w.value, 255)}
      end
    end
    for _, p in ipairs(view.procs or {}) do
      if #rec.procs >= const.HUD_PROCS then break end
      local tag = E.hud_tag and E.hud_tag[p.tag]
      if tag and tag > 0 and type(p.record) == 'number' and p.record > 0 then
        rec.procs[#rec.procs + 1] = {record = p.record, tag = tag, age_ms = whole(p.age_ms, const.HUD_PROC_AGE_MAX_MS)}
      end
    end
    rec.foe_count, rec.word_count, rec.proc_count = #rec.foes, #rec.words, #rec.procs
    return rec
  end

  local function note(text)   -- one log line per session for an unexpected error
    P.errors = P.errors + 1
    if not logged then
      logged = true
      opts.log('Legacy co-op HUD states: ' .. text)
    end
  end

  -- One tick's work. Returns true when it stopped the timer for good (three refused pushes in a row).
  local function work()
    local bridge = opts.bridge
    if not (bridge and bridge.is_ready()) then return false end
    local okView, view = pcall(opts.view)
    if not okView or type(view) ~= 'table' then return false end
    local okRec, rec = pcall(P.record, view)
    if not okRec then note('record not built (' .. tostring(rec) .. ')') return false end
    local okPush, _, err = pcall(function() return bridge.push_hud_states(rec) end)
    if not okPush then note('push failed (' .. tostring(_) .. ')') return false end
    if err then
      failures = failures + 1
      P.refused = P.refused + 1
      if failures >= M.MAX_FAILURES then
        stopped = true
        opts.log('Legacy co-op HUD states: stopped after ' .. failures .. ' refused pushes (' .. tostring(err) .. ')')
        return true
      end
    else
      failures = 0
      P.pushes = P.pushes + 1
    end
    return false
  end

  function P.tick()
    armed = false
    if stopped then return end
    local okWork, halted = pcall(work)
    if not okWork then note('tick failed (' .. tostring(halted) .. ')') halted = false end
    if halted then return end
    P.start()
  end

  function P.start()
    if armed or stopped or not opts.after then return end
    armed = true
    opts.after(M.PERIOD_MS, P.tick)
  end

  return P
end

return M
