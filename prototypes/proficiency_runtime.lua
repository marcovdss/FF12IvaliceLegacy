-- Proficiency runtime shared by the Forge adapters (165 physical hits, 170 magick, 110 and 127 healing, 182 statuses,
-- 240 guard reactions, 22 evasion, 222 target initialization; 34, 94 and 231 for accessories). Loaded once and kept in _G so all adapters see one campaign state.
-- Montblanc hands out the ranks. Since 0.5.3 the party asks him: his menu's "About Proficiency." choice (rbn_a14.ebp,
-- ivalice_legacy/montblanc.py) sets a byte this runtime polls in the Clan hall (location 302), and the topic runs here
-- through the Lua loader dialog plugin: whether a new rank is waiting, where the party stands and what the next rank
-- needs, the Writs, and what Proficiency is. The rewarded rank is stored beside the Legacy save. Leaving the hall no longer hands anything out; with a
-- rank waiting he only calls after the party once per visit.
local R = {}
local Proficiency = assert(loadfile('scripts/IvaliceLegacy/proficiency.lua', 't', _ENV))()
local ClanRank = assert(loadfile('scripts/IvaliceLegacy/clan_rank.lua', 't', _ENV))()
local P = Proficiency
-- The 66 hybrid lines: a character with two jobs uses its pair's line (ids 12-77) instead of either parent's.
local Hybrid = assert(loadfile('scripts/IvaliceLegacy/hybrid_proficiency.lua', 't', _ENV))()
Hybrid.attach(P)
R.Hybrid = Hybrid
R.Proficiency = P
-- MP economy (mp_economy.lua): one Magick cast hands back at most half of what it cost. Missing module = no cap.
local okEconomy, Economy = pcall(function() return assert(loadfile('scripts/IvaliceLegacy/mp_economy.lua', 't', _ENV))() end)
if not okEconomy then Economy = nil end
R.Economy = Economy
-- Accessory effects (accessory_effects.lua): wild cards outside Proficiency, dispatched from the same hooks. A missing or
-- broken module means no accessory effects; Proficiency is unaffected.
local okAccessory, Accessory = pcall(function()
  return assert(loadfile('scripts/IvaliceLegacy/accessory_effects.lua', 't', _ENV))()
end)
if not okAccessory or type(Accessory) ~= 'table' then Accessory = nil end
R.Accessory = Accessory

local CLAN_HALL = 302
local LOCATION = 0x021654C4
local SAVE_ID = 'IvaliceLegacyProficiency'
local ELEMENTS = {'fire', 'lightning', 'ice', 'earth', 'water', 'wind', 'holy', 'dark'}
local FOE_MEMORY = 30000000  -- clock units (microseconds, so 30 s) a foe stays "engaged" after its last hit

local function expected(address, text, wide)
  local count = #text * (wide and 2 or 1)
  local raw = memory.readArray(address, count)
  if type(raw) ~= 'table' and type(raw) ~= 'string' then return false end
  if #raw ~= count then return false end
  for i = 1, count do
    local byte = type(raw) == 'string' and raw:byte(i) or raw[i]
    local value = wide and (i % 2 == 0 and 0 or text:byte((i + 1) // 2)) or text:byte(i)
    if byte ~= value then return false end
  end
  return true
end

local function legacyMode()
  return expected(0x120000, 'MZ', false)
    and expected(0x120180, 'PE\0\0\x64\x86', false)
    and expected(0xD113D4, 'LEGCY_\0', false)
    and expected(0xD12200, '%sLEGCY_%03d\0', true)
end

local campaign, claimed, location, logged, rankLogged = nil, 0, nil, 0, false
local freshGame = false  -- a New Game started this session: a Legacy campaign with no save file yet
local foes = {}
local writs, writsKnown = {false, false, false}, false
local LOG_LIMIT = 1000
local function log(line)
  if logged < LOG_LIMIT then logged = logged + 1; print('Legacy Proficiency: ' .. line) end
end
R.log = log
Hybrid.log = log   -- the hybrid module writes its about-to-act firing counter here
if Accessory then
  Accessory.log = log
  local ok = pcall(Accessory.attach, Hybrid, P)
  if not ok then Accessory = nil; R.Accessory = nil end
end

-- Key items through the game's own inventory code: 0x2FD970 is the owned-bit getter (id, kind 0) and 0x2FD420 the
-- setter (id, amount, mode). Mode 1 sets the amount outright and never reads the setter's stack-only notify
-- argument; the setter updates the owned bit and the ordered key-item list (0x325F40) together.
local KEY_GET, KEY_SET = 0x2FD970, 0x2FD420
local function owns(id)
  local ok, v = pcall(memory.execute, KEY_GET, memory.arg.s32, memory.arg.u32, id, memory.arg.s32, 0)
  return ok and v == 1
end
local function grant(id)
  local ok = pcall(memory.execute, KEY_SET, memory.arg.s32, memory.arg.u32, id, memory.arg.s32, 1, memory.arg.s32, 1)
  return ok and owns(id)
end
-- Montblanc hands the Writs over at ranks 1, 5 and 9, so a Writ in Key Items proves at least that rank was claimed.
-- This repairs a side file that lost the claimed rank (0.3.10 and earlier rejected a side file saved under another
-- slot's name, and the next save then wrote rank 0).
local WRIT_FLOOR = {1, 5, 9}
local function refreshWrits()
  for k = 1, 3 do writs[k] = owns(P.WRIT_IDS[k]) end
  writsKnown = true
  if campaign then
    for k = 3, 1, -1 do
      if writs[k] then
        if claimed < WRIT_FLOOR[k] then
          log(string.format('campaign=%s claimed rank %d restored to %d from the Writs carried', campaign, claimed,
            WRIT_FLOOR[k]))
          claimed = WRIT_FLOOR[k]
        end
        break
      end
    end
  end
end
-- The rank Proficiency acts on: the claimed rank, capped by the Writs actually carried.
local function effective()
  if campaign == nil then return 0 end
  if not writsKnown then
    refreshWrits()
    local e = P.effective(claimed, writs)
    if e < claimed then
      log(string.format('campaign=%s claimed rank %d but Writs I=%s II=%s III=%s: acting on rank %d until Montblanc hands them over',
        campaign, claimed, tostring(writs[1]), tostring(writs[2]), tostring(writs[3]), e))
    end
  end
  if claimed < 1 then return 0 end
  return P.effective(claimed, writs)
end
function R.active() return effective() >= 1 end
-- A Legacy campaign is loaded, whatever its Clan rank. Rules that belong to the campaign rather than to Proficiency (the
-- MP economy's surcharge) use this, so a new campaign pays them before Montblanc's first reward.
function R.inCampaign() return (campaign ~= nil or freshGame) and legacyMode() end
function R.claimed() return claimed end
function R.effective() return effective() end

-- The Clan Primer tip "Clan Proficiency" (Traveler's Tips) waits on one flag bit of the menu-info block
-- ([0x20927C0] + 0x334C, 256 bits, read by the tip check 0x575E80 through 0x315620). No native tip check uses these
-- bits; the bit follows the Apprentice's Writ through the native setter 0x315A20(index, on).
local TIP_STATE_BASE, TIP_FLAGS, SET_TIP_FLAG = 0x020927C0, 0x334C, 0x315A20
R.TIP_FLAG = 254  -- 255 (0xFF) means "no flag test" in a tip check; 0.3.1 used it by mistake
local STRAY_FLAG = 255
local flagsLogged = false
local function syncTip()
  if campaign == nil then return end
  local state = memory.u64[TIP_STATE_BASE]
  if type(state) ~= 'number' or state <= 0 then return end
  local at = state + TIP_FLAGS
  if not flagsLogged then
    flagsLogged = true
    local raw = memory.readArray(at, 32)
    local hex = {}
    for i = 1, 32 do
      local b = type(raw) == 'string' and raw:byte(i) or (type(raw) == 'table' and raw[i]) or nil
      hex[i] = b and string.format('%02X', b) or '??'
    end
    log('campaign=' .. campaign .. ' menu flags before Proficiency: ' .. table.concat(hex))
  end
  -- 0.3.1 set bit 255 (no native or Legacy use): clear it once.
  local stray = memory.u8[at + (STRAY_FLAG >> 3)]
  if type(stray) == 'number' and (stray >> (STRAY_FLAG & 7)) & 1 == 1 then
    local ok = pcall(memory.execute, SET_TIP_FLAG, memory.arg.void, memory.arg.u32, STRAY_FLAG, memory.arg.s32, 0)
    log(string.format('campaign=%s cleared stray flag %d%s', campaign, STRAY_FLAG, ok and '' or ': setter failed'))
  end
  local byte = memory.u8[at + (R.TIP_FLAG >> 3)]
  if type(byte) ~= 'number' then return end
  local now = (byte >> (R.TIP_FLAG & 7)) & 1 == 1
  local want = writs[1] == true
  if now ~= want then
    local ok = pcall(memory.execute, SET_TIP_FLAG, memory.arg.void, memory.arg.u32, R.TIP_FLAG, memory.arg.s32, want and 1 or 0)
    log(string.format('campaign=%s Proficiency tip %s (flag %d)%s', campaign, want and 'shown' or 'hidden', R.TIP_FLAG,
      ok and '' or ': setter failed'))
  end
end

local function clanState()
  local ok, clan = pcall(ClanRank.read, {
    u8 = function(a) return memory.u8[a] end, u32 = function(a) return memory.u32[a] end,
    marks = function() return memory.execute(ClanRank.MARKS_FN, arg.s32) end})
  if ok and type(clan) == 'table' then return clan end
  return nil
end
local function clanRank()
  local clan = clanState()
  return clan and clan.rank or nil
end

-- A call target's open is a function, or a value whose metatable has __call.
local function callable(value)
  if type(value) == 'function' then return true end
  local ok, mt = pcall(getmetatable, value)
  return ok and type(mt) == 'table' and type(rawget(mt, '__call')) == 'function'
end

-- The dialog plugin's call target `kind` (dialog.aaske, dialog.amese) and its open function, or nil when the plugin is
-- not there. 0.1.50: any indexable target counts (a table or the plugin's userdata) when its open, read under pcall, is
-- callable.
local function dialogTarget(kind)
  if dialog == nil then return nil end
  local okService, service = pcall(function() return dialog[kind] end)
  if not okService or service == nil then return nil end
  local okOpen, openFn = pcall(function() return service.open end)
  if not okOpen or not callable(openFn) then return nil end
  return service, openFn
end

local function speak(boxes)
  local amese, openFn = dialogTarget('amese')   -- 0.1.50: the topic's own test; a target without open only logs
  if not amese then
    for _, box in ipairs(boxes) do log('speech (no dialog service): ' .. box:gsub('\n', ' ')) end
    return
  end
  for _, box in ipairs(boxes) do
    local ok, h, reason = pcall(openFn, {speaker = 'Montblanc', body = box, queue = true, lockPlayer = true})
    if not ok or not h then log('dialog refused: ' .. tostring(ok and reason or h)) end
  end
end

-- Asked for a new rank: hand over every Writ the rank calls for FIRST, then reward the rank. A Writ that cannot be
-- granted stops the claim, so a Proficiency never runs without its Writ. Returns how many boxes Montblanc spoke and why:
-- 'spoke', 'none' (nothing new), 'writ' (a Writ could not be granted), 'rank' (the Clan rank could not be read) or
-- 'off' (no Legacy campaign). Only 'none' means there is nothing to hand out.
local function claim()
  if not campaign or not legacyMode() then return 0, 'off' end
  local rank = clanRank()
  if type(rank) ~= 'number' then log('clan rank unreadable; nothing claimed') return 0, 'rank' end
  local from = claimed
  local to = math.max(rank, claimed)
  refreshWrits()
  local late = {}
  for _, k in ipairs(P.writsNeeded(to)) do
    if not writs[k] then
      if not grant(P.WRIT_IDS[k]) then
        log(string.format('campaign=%s Writ %d (0x%04X) could not be granted; nothing claimed', campaign, k, P.WRIT_IDS[k]))
        refreshWrits()
        return 0, 'writ'
      end
      writs[k] = true
      log(string.format('campaign=%s Writ %d (0x%04X) granted', campaign, k, P.WRIT_IDS[k]))
      if not P.speechGives(from, to, k) then late[#late + 1] = k end
    end
  end
  local boxes = {}
  for _, k in ipairs(late) do for _, b in ipairs(P.writSpeech(k)) do boxes[#boxes + 1] = b end end
  if to > from then
    claimed = to
    local L = P.levels(claimed)
    log(string.format('campaign=%s rank %d -> %d claimed; levels I=%d II=%d III=%d', campaign, from, to, L[1], L[2], L[3]))
    for _, b in ipairs(P.speech(from, to)) do boxes[#boxes + 1] = b end
  end
  refreshWrits()
  syncTip()
  if #boxes > 0 then speak(boxes) end
  return #boxes, #boxes > 0 and 'spoke' or 'none'
end

-- ---------- Montblanc's Proficiency topic ----------
-- The hall script's "About Proficiency." choice writes 1 to script variable class 4, offset 0x99: the byte at
-- 0x02099DF0 + 0x99 (Clan Centurio Equipment Progression uses the same byte for its own Montblanc talk; the two mods
-- replace the same script file, so they never run together). Polled only inside the hall.
local TOPIC_FLAG = 0x02099DF0 + 0x99
local TOPIC_POLL_MS, TOPIC_RETRY_MS, TOPIC_RETRIES, TOPIC_BUSY_MS = 250, 200, 25, 300
local topicGeneration, topicBusy, reminded = 0, false, false

local function after(ms, fn)
  if type(event.executeAfterMs) == 'function' then event.executeAfterMs(ms, fn) else fn() end
end

-- 0.1.51: a talk belongs to the topicGeneration it started in. watchTopic moves the generation on at every map change
-- and load, so a talk cut off by one is an older chain: its callbacks, steps, retries and next pages are dropped and
-- change nothing. Only the talk of the current generation ends itself, so an older chain never clears a newer talk's
-- topicBusy.
local function endTalk(generation)
  if generation == topicGeneration then topicBusy = false end
end

-- 0.1.50: every later topic step (a window's close or failure, a retry, the next page, the menu's choice, the talk coming
-- back to the menu) runs under pcall: an error is logged and ends the talk, so topicBusy never stays set. 0.1.51: a step
-- of an older chain does not run.
local function step(generation, name, fn, ...)
  if generation ~= topicGeneration then
    log('topic: ' .. name .. ' of an earlier talk dropped')
    return false
  end
  local ok, err = pcall(fn, ...)
  if not ok then
    endTalk(generation)
    log('topic: ' .. name .. ' failed: ' .. tostring(err))
  end
  return ok
end

-- The dialog plugin's reasons for a window that could open a moment later: a dialogue still closing or owning the
-- native background, a busy slot, no free slot, or a game not taking windows yet (0.1.50).
local BUSY_REASONS = {'is still closing', 'already owns the native background', 'is busy', 'no free window slot',
                      'will not accept a window now'}
local function busy(reason)
  if type(reason) ~= 'string' then return false end
  for _, text in ipairs(BUSY_REASONS) do if reason:find(text, 1, true) then return true end end
  return false
end

-- Open one dialogue; if the previous window is still closing, try again shortly. `fail` runs if it never opens.
-- 0.1.49: an accepted window can still fail later on the game thread (the loader's onError); `fail` runs then too.
-- The window's onClose and onError share one settled flag: whichever comes first runs, the other is ignored, so the
-- next step never runs twice. 0.1.50: an onError that reports a busy or conflict reason opens the window once more after
-- TOPIC_BUSY_MS before failing (the settled flag still lets one step run); every callback body runs under step().
-- 0.1.51: the window belongs to the talk of `generation`; a close by the game ("gameClosed") ends the talk instead of
-- running the next step, and the busy retry does not open once the window has settled.
local function open(kind, options, fail, tries, generation)
  local service, openFn = dialogTarget(kind)
  if not service then
    log('topic: dialog.' .. kind .. ' unavailable')
    if fail then fail() end
    return
  end
  if not tries then
    local settled, retried, onClose = false, false, options.onClose
    options.onClose = function(...)
      if settled then return end
      settled = true
      if select(2, ...) == 'gameClosed' then
        log('topic: the game closed the window; the talk ends')
        endTalk(generation)
        return
      end
      if onClose then step(generation, 'window close', onClose, ...) end
    end
    options.onError = function(reason)
      if settled then return end
      if not retried and busy(reason) then
        retried = true
        log('topic: dialog busy after it was accepted (' .. tostring(reason) .. '): trying once more')
        after(TOPIC_BUSY_MS, function()
          if settled then return end   -- a late onClose already ran the step: never a second window
          step(generation, 'window retry', open, kind, options, fail, 1, generation)
        end)
        return
      end
      settled = true
      log('topic: dialog failed after it was accepted: ' .. tostring(reason))
      if fail then step(generation, 'window failure', fail) end
    end
  end
  local ok, handle, reason = pcall(openFn, options)
  if ok and handle then return end
  tries = (tries or 0) + 1
  if tries > TOPIC_RETRIES then
    log('topic: dialog refused: ' .. tostring(ok and reason or handle))
    if fail then fail() end
    return
  end
  after(TOPIC_RETRY_MS, function() step(generation, 'window retry', open, kind, options, fail, tries, generation) end)
end

-- Montblanc says the boxes one after another, then `done` runs. 0.1.51: the pages belong to the talk of `generation`.
local function pages(boxes, done, generation)
  local i = 0
  local function nextBox()
    i = i + 1
    if i > #boxes then if done then done() end return end
    open('amese', {speaker = 'Montblanc', body = boxes[i], lockPlayer = true,
                   onClose = function() after(60, function() step(generation, 'next page', nextBox) end) end},
         function() endTalk(generation) end, nil, generation)
  end
  nextBox()
end

local topic
-- What Montblanc's lines read (proficiency.lua: greeting, standing, nothingNew, writStatus): the Clan rank and the save's
-- points, marks and Sky Pirate's Den entries with the game's rank table (clan_rank.lua, no side effects), the next rank's
-- row, the rewarded rank and the Writs in the Key Items. Read when he speaks; nothing is written.
local function topicState()
  local clan = clanState()
  refreshWrits()
  local s = {claimed = claimed, writs = writs, rank = clan and clan.rank or nil, have = clan}
  if clan and type(ClanRank.nextNeeds) == 'function' and type(clan.rank) == 'number' then
    local ok, need = pcall(ClanRank.nextNeeds, clan, math.max(clan.rank, claimed))
    if ok then s.need = need end
  end
  return s
end

topic = function()
  -- 0.1.51: the talk carries the generation it started in; its windows, pages and steps are dropped once the
  -- generation moves on (a map change or a load). A page's `done` re-enters here inside a step of the same talk.
  local generation = topicGeneration
  -- 0.1.50: a game that is not Ivalice Legacy stays silent and claims nothing; this is tested first
  if not legacyMode() then endTalk(generation) return end
  if not campaign then
    -- no Legacy campaign loaded (a new game, saved or not, until that save is loaded; or another save): one short line,
    -- nothing claimed
    log('topic: no Legacy campaign loaded')
    pages({P.NO_CAMPAIGN}, function() endTalk(generation) end, generation)
    return
  end
  -- the most used first: claim, standing, Writs, what Proficiency is, and "That's all." (also Cancel)
  local T = P.TOPIC
  open('aaske', {speaker = 'Montblanc', body = P.greeting(topicState()),
                 choice = P.TOPIC_CHOICES, cancel = T.bye, closeOnCancel = true, lockPlayer = true,
                 onClose = function(index)
                   after(60, function() step(generation, 'menu choice', function()
                     if index == T.claim then
                       local ok, said, why = pcall(claim)
                       if not ok then log('topic: claim failed: ' .. tostring(said)); said, why = 0, 'error' end
                       if type(said) == 'number' and said > 0 then
                         endTalk(generation)   -- claim() queued Montblanc's reward boxes; the talk ends with them
                       elseif why == 'none' then
                         pages(P.nothingNew(topicState()), topic, generation)
                       else
                         -- the papers could not be handled now (a Writ not granted, the rank unreadable): never "nothing new"
                         log('topic: claim not completed (' .. tostring(why) .. ')')
                         pages({P.CLAIM_WAIT}, topic, generation)
                       end
                     elseif index == T.stand then pages(P.standing(topicState()), topic, generation)
                     elseif index == T.writs then pages(P.writStatus(topicState()), topic, generation)
                     elseif index == T.about then pages(P.TOPIC_PAGES, topic, generation)
                     else endTalk(generation) end
                   end) end)
                 end}, function() endTalk(generation) end, nil, generation)
end

local function topicTick(generation)
  if generation ~= topicGeneration or location ~= CLAN_HALL then return end
  local ok, value = pcall(function() return memory.u8[TOPIC_FLAG] end)
  if ok and value == 1 then
    pcall(function() memory.u8[TOPIC_FLAG] = 0 end)
    if not topicBusy and not (dialogTarget('aaske') and dialogTarget('amese')) then
      -- 0.1.49: no dialog plugin, so no talk; the question still claims the rank (speak() logs his lines, as in 0.1.47)
      log(string.format('campaign=%s Montblanc topic asked without the dialog plugin: claiming directly', tostring(campaign)))
      local okClaim, said, why = pcall(claim)
      if not okClaim then log('topic: claim failed: ' .. tostring(said))
      else log('topic: direct claim ' .. tostring(why) .. ' (' .. tostring(said) .. ' lines)') end
    elseif not topicBusy then
      topicBusy = true
      log(string.format('campaign=%s Montblanc topic opened', tostring(campaign)))
      -- the hall script is closing its own menu: give it a moment before the first window
      after(300, function()
        if generation ~= topicGeneration then return end   -- 0.1.51: the map changed before the talk began
        local okTopic, err = pcall(topic) if not okTopic then endTalk(generation) log('topic failed: ' .. tostring(err)) end
      end)
    end
  end
  after(TOPIC_POLL_MS, function() topicTick(generation) end)
end

local function watchTopic()
  topicGeneration = topicGeneration + 1   -- any earlier poll chain stops
  if location ~= CLAN_HALL or type(event.executeAfterMs) ~= 'function' then return end
  local generation = topicGeneration
  topicBusy, reminded = false, false
  pcall(function() memory.u8[TOPIC_FLAG] = 0 end)   -- a stale request from another visit is not a request
  event.executeAfterMs(TOPIC_POLL_MS, function() topicTick(generation) end)
end

-- Leaving with a rank waiting: one line, once per visit, and nothing is handed out.
local function remind()
  if reminded or not campaign or not legacyMode() then return end
  local rank = clanRank()
  if type(rank) ~= 'number' or rank <= claimed then return end
  reminded = true
  speak({P.REMINDER})
end

-- ---------- gear descriptions ----------
-- The Equipment menu shows stage I on weapons, II on body armour and III on helmets for the selected character
-- (not shields, accessories or ammunition). It is added when the description is shown, so it always matches the character
-- on screen: the help-window updater copies the hovered item's description and stores the text pointer at
-- 0x29266C (mov [rdi+0x18], rbx; The Insurgent's Descriptive Inventory swaps rbx just before, at 0x292667). The
-- patch there appends a ready-made slot-specific suffix for the selected party member (menu slot [0x209AC30]+0xDE0 -> 0x282EA0
-- -> 0x31C4C0) to a copy of that text. Lua keeps one native-encoded suffix per party member, rebuilt only when a
-- member's job or the effective rank changes. Each item maps to one stage byte; each member has three suffix pointers.
-- Equipment only (0.3.8): every menu asks for its help text through 0x293110/0x293100 -> 0x293170, which sends it
-- synchronously to this handler: through 0x247510 (opener return 0x2932A4) or, the first time a menu shows help,
-- through window creation 0x244F50 -> init message -> 0x292AD9 (opener return 0x293286). Either way the text
-- (r14) is the opener's buffer at its rsp+0x60, so [r14-0x68] is the opener's return and [r14+0x228] the menu's
-- (0.3.9; 0.3.8 used fixed rsp offsets, which missed the first item). Only the Equipment menu's item calls pass:
-- 0x3FE2D5 (gear list, return 0x3FE2DA) and 0x3FF973 (equipped slots, return 0x3FF978). Inventory (0x563E42),
-- shops and every other caller keep the plain text. Any unexpected frame also keeps the plain text.
local GEAR_SITE = 0x0029266C
local GEAR_ORIGINAL = {0x33, 0xC9, 0x48, 0x89, 0x5F, 0x18}  -- xor ecx,ecx ; mov [rdi+0x18],rbx
local GEAR_PATCH = [[
  jmp %legacy_prof_code%
  nop
]]
local GEAR_CODE = [[
legacy_prof_code:
  push r12
  push r13
  sub rsp,0x20
  xor eax,eax
  xor r13d,r13d
  mov rdx,r14
  sub rdx,rsp
  cmp rdx,0x10000
  jae legacy_prof_framed
  mov rax,[r14-0x68]
  mov r13,[r14+0x228]
legacy_prof_framed:
  mov r12,[legacy_prof_probe]
  test r12,r12
  je legacy_prof_probed
  mov rcx,[rsp+0xA8]
  mov [r12],rcx
  mov [r12+0x08],rax
  mov [r12+0x10],r13
  movzx ecx,word ptr [r14-0x0A]
  mov [r12+0x18],ecx
  inc dword ptr [r12+0x1C]
legacy_prof_probed:
  cmp rax,0x002932A4
  je legacy_prof_opener
  cmp rax,0x00293286
  jne legacy_prof_done
legacy_prof_opener:
  cmp r13,0x003FE2DA
  je legacy_prof_equipment
  cmp r13,0x003FF978
  jne legacy_prof_done
legacy_prof_equipment:
  mov r12,[legacy_prof_bits]
  test r12,r12
  je legacy_prof_done
  movzx eax,word ptr [r14-0x0A]
  sub eax,0x1000
  jb legacy_prof_done
  cmp eax,0x400
  jae legacy_prof_done
  movzx r13d,byte ptr [r12+rax]
  test r13d,r13d
  je legacy_prof_done
  dec r13d
  mov rax,[0x0209AC30]
  test rax,rax
  je legacy_prof_done
  movzx ecx,word ptr [rax+0x00000DE0]
  cmp ecx,0x08
  ja legacy_prof_done
  call 0x00282EA0
  cmp eax,0x08
  ja legacy_prof_done
  mov ecx,eax
  call 0x0031C4C0
  movzx eax,al
  cmp eax,0x40
  jae legacy_prof_done
  imul eax,eax,0x03
  add eax,r13d
  mov r12,[legacy_prof_table]
  test r12,r12
  je legacy_prof_done
  mov r13,[r12+rax*8]
  test r13,r13
  je legacy_prof_done
  mov r12,[legacy_prof_buffer]
  test r12,r12
  je legacy_prof_done
  xor ecx,ecx
legacy_prof_text:
  mov al,[rbx+rcx]
  test al,al
  je legacy_prof_suffix
  mov [r12+rcx],al
  inc ecx
  cmp ecx,0x00000600
  jb legacy_prof_text
  jmp legacy_prof_done
legacy_prof_suffix:
  xor edx,edx
legacy_prof_append:
  mov al,[r13+rdx]
  mov [r12+rcx],al
  test al,al
  je legacy_prof_use
  inc ecx
  inc edx
  cmp edx,0x000001F0
  jb legacy_prof_append
  mov byte ptr [r12+rcx],0x00
legacy_prof_use:
  mov rbx,r12
legacy_prof_done:
  add rsp,0x20
  pop r13
  pop r12
  xor ecx,ecx
  mov [rdi+0x18],rbx
  jmp 0x00292672

  .align 0x10
legacy_prof_bits:
  .dq 0x00
legacy_prof_table:
  .dq 0x00
legacy_prof_buffer:
  .dq 0x00
legacy_prof_probe:
  .dq 0x00
]]
local GEAR_SYMBOLS = {'legacy_prof_code', 'legacy_prof_bits', 'legacy_prof_table', 'legacy_prof_buffer', 'legacy_prof_probe'}
local MEMBERS, SUFFIX_SIZE, GEAR_POLL_MS = 16, 0x200, 500
local MEMBER_KEEP, KEEP_JOB1, KEEP_JOB2 = 0x320A40, 0x1C3, 0x1C4
local gear = {installed = false, failed = false, keys = {}, slots = {}, flip = {}}

local function label(name, value)
  return '\n{scale:82}{rgb:90,90,90}' .. name .. ':{lowspace}{rgb:255,255,255}' .. value
end
-- One added line per item: I on weapons, II on body armour, III on helmets. The effect remains job-based.
local STAGES = {'I', 'II', 'III'}
function R.gearStage(category)
  if type(category) ~= 'number' or category % 1 ~= 0 then return nil end
  if category >= 0 and category <= 17 then return 1 end
  if category == 20 then return 2 end
  if category == 19 then return 3 end
  return nil
end
function R.gearSuffix(job, rank, stage)
  if not STAGES[stage] then return nil end
  local lines = P.gearLines(job, rank)
  local g = P.GEAR[job]
  if not lines or not g then return nil end
  local level = P.levels(rank)[stage]
  local head = g.line .. ' ' .. STAGES[stage] .. ' ' .. level .. '/4'
  return label(head, lines[stage + 1] or 'Unlocks at a higher Clan rank')
end

-- Help box position (0.3.13 for Equipment, 0.4.3 for every menu). Equipment (0x3FCEE0), Inventory (0x564430) and the
-- shop (0x57BEE0) build the help text of every owned item, and hand the widest to the help window (0x2932F0, window
-- +0xCC); the window's handler then slides the WHOLE box left until that widest text fits. One item with a long
-- description (1532 px, seen in game 2026-11-03) pushed every description of that menu off the left edge.
-- The handler's one read of the width is at 0x29244C; its comparison at 0x292452 is now xor eax,eax (the width counts
-- as 0, and the jle after it is always taken), so the box stays where the layout puts it, in every menu. A long line
-- runs further right, still on screen, as confirmed in play for Equipment. eax is reloaded at 0x29245F.
local WIDTH_SITE, WIDTH_ORIGINAL = 0x00292452, {0x3B, 0xC1}
function R.installWidthFix()
  local raw = memory.readArray(WIDTH_SITE, 2)
  local b1 = type(raw) == 'string' and raw:byte(1) or (type(raw) == 'table' and raw[1]) or nil
  local b2 = type(raw) == 'string' and raw:byte(2) or (type(raw) == 'table' and raw[2]) or nil
  if b1 ~= WIDTH_ORIGINAL[1] or b2 ~= WIDTH_ORIGINAL[2] then
    log('Help box width fix not applied: unexpected code at 0x292452')
    return false
  end
  local ok, err = pcall(memory.assemble, 'xor eax,eax', WIDTH_SITE)
  log(ok and 'Help box width fix applied' or ('Help box width fix rejected (' .. tostring(err) .. ')'))
  return ok
end

local function installGear()
  if gear.installed or gear.failed then return gear.installed end
  local raw = memory.readArray(GEAR_SITE, #GEAR_ORIGINAL)
  local same = type(raw) == 'table' or type(raw) == 'string'
  for i = 1, #GEAR_ORIGINAL do
    local b = type(raw) == 'string' and raw:byte(i) or (type(raw) == 'table' and raw[i]) or nil
    if b ~= GEAR_ORIGINAL[i] then same = false end
  end
  if not same then
    gear.failed = true
    log('gear text not installed: unexpected code at 0x29266C (another mod patches it?)')
    return false
  end
  local bits = memory.alloc(0x400)
  local table_ = memory.alloc(0x40 * 3 * 8)
  local buffer = memory.alloc(0x800)
  memory.writeArray(bits, {string.rep('\0', 0x400):byte(1, -1)})
  memory.writeArray(table_, {string.rep('\0', 0x40 * 3 * 8):byte(1, -1)})
  -- One stage byte per equipment row; zero keeps the original description.
  local count = 0
  if type(bpack) == 'table' then
    for i = 0, 0x3FF do
      local ok, category = pcall(function() local r = bpack.section13[i]; return r and r.category end)
      if not ok or type(category) ~= 'number' then break end
      local stage = R.gearStage(category)
      if stage then
        memory.u8[bits + i] = stage
        count = count + 1
      end
    end
  end
  -- The code block first; the site is patched only once the block and its data are in place.
  local ok, err = pcall(memory.assemble, GEAR_CODE, GEAR_SYMBOLS)
  local slots = ok and {memory.getSymbol('legacy_prof_bits'), memory.getSymbol('legacy_prof_table'),
    memory.getSymbol('legacy_prof_buffer'), memory.getSymbol('legacy_prof_probe')} or {}
  if not ok or not (slots[1] and slots[1] ~= 0 and slots[2] and slots[2] ~= 0 and slots[3] and slots[3] ~= 0) then
    gear.failed = true
    log('gear text not installed: code block rejected (' .. tostring(err) .. ')')
    return false
  end
  memory.u64[slots[1]] = bits
  memory.u64[slots[2]] = table_
  memory.u64[slots[3]] = buffer
  -- Caller record (0.3.8): the three return addresses the gate checks, the item and a call count.
  local probe = memory.alloc(0x40)
  memory.writeArray(probe, {string.rep('\0', 0x40):byte(1, -1)})
  if slots[4] and slots[4] ~= 0 then memory.u64[slots[4]] = probe; gear.probe = probe end
  gear.table = table_
  ok, err = pcall(memory.assemble, GEAR_PATCH, GEAR_SITE)
  if not ok then
    gear.failed = true
    log('gear text not installed: site patch rejected (' .. tostring(err) .. ')')
    return false
  end
  gear.installed = true
  log(string.format('gear text hook installed (%d items)', count))
  return true
end

-- Three suffixes per member; two buffers each so the hook never reads a half-written text.
local function setSuffix(member, stage, text)
  local slot = member * 3 + stage - 1
  local at = gear.table + slot * 8
  if not text then memory.u64[at] = 0; return end
  local bytes = message.convert(text)
  if type(bytes) ~= 'string' or #bytes + 1 > SUFFIX_SIZE then memory.u64[at] = 0; return end
  gear.slots[slot] = gear.slots[slot] or {memory.alloc(SUFFIX_SIZE), memory.alloc(SUFFIX_SIZE)}
  gear.flip[slot] = 3 - (gear.flip[slot] or 2)
  local buf = gear.slots[slot][gear.flip[slot]]
  local arr = {bytes:byte(1, -1)}
  arr[#arr + 1] = 0
  memory.writeArray(buf, arr)
  memory.u64[at] = buf
end

-- Logs each distinct description caller once (cap 20), so a menu that should or should not show the line can be
-- named from hook.log without a special test visit.
local EQUIPMENT_CALLERS = {[0x3FE2DA] = true, [0x3FF978] = true}
local function probeTick()
  local at = gear.probe
  if not at then return end
  local calls = memory.u32[at + 0x1C]
  if type(calls) ~= 'number' or calls == 0 or calls == gear.lastCalls then return end
  gear.lastCalls = calls
  local handler, sender, caller = memory.u64[at] or 0, memory.u64[at + 0x08] or 0, memory.u64[at + 0x10] or 0
  local item = (memory.u32[at + 0x18] or 0) & 0xFFFF
  local key = string.format('%X:%X:%X', handler, sender, caller)
  gear.seen = gear.seen or {}
  if gear.seen[key] or (gear.probeLogs or 0) >= 20 then return end
  gear.seen[key] = true
  gear.probeLogs = (gear.probeLogs or 0) + 1
  local framed = sender == 0x2932A4 or sender == 0x293286
  log(string.format('gear caller: menu=0x%X item=0x%04X %s', caller, item,
    not framed and string.format('unexpected frame (0x%X 0x%X): plain text', handler, sender)
    or (EQUIPMENT_CALLERS[caller] and 'Equipment: line allowed' or 'other menu: plain text')))
end

local function gearTick()
  if gear.installed then pcall(probeTick) end
  if campaign and legacyMode() and installGear() then
    local rank = effective()
    for member = 0, MEMBERS - 1 do
      local ok, keep = pcall(memory.execute, MEMBER_KEEP, memory.arg.pointer, memory.arg.s32, member)
      local job = ok and type(keep) == 'number' and keep > 0 and memory.u8[keep + KEEP_JOB1] or nil
      local job2 = job and memory.u8[keep + KEEP_JOB2] or nil
      if type(job) == 'number' and type(job2) == 'number' and job2 ~= P.NO_JOB then job = R.lineOf(job, job2) end
      if type(job) ~= 'number' or not P.GEAR[job] then job = nil end
      local key = job and (job .. ':' .. rank) or 'none'
      if gear.keys[member] ~= key then
        gear.keys[member] = key
        for stage = 1, 3 do
          setSuffix(member, stage, job and R.gearSuffix(job, rank, stage) or nil)
        end
      end
    end
  end
  event.executeAfterMs(GEAR_POLL_MS, gearTick)
end

-- ---------- solo scenes (lives here until the next main-package release) ----------
-- The Insurgent's Companions shows the whole party in towns (config partyInTowns; runtime byte pit_ot: 1 Full
-- Party, 0 Leader Only, read live by its patches). The Royal Palace counts as a town, and the solo-Vaan sneak-in
-- there (rrp_a02, "Now's your chance!") restarts forever while companions stand in its trigger. While the party
-- is in the palace (locations 567-572: the palace maps' own exits, named in The Planetary Age's zone list) the
-- byte is held at Leader Only; the configured value comes back on leaving. Without that mod nothing happens.
local PALACE_FIRST, PALACE_LAST = 567, 572
-- Another script's symbols are not visible here (0.3.9 found none in game), so the byte is found through the mod's
-- own patch: 0x2838CC holds "jmp pit3_code", and pit3_code starts "cmp byte ptr [pit_ot],0" (80 3D rel32 00, or
-- 80 3C 25 abs32 00). Anything else: the mod is absent or changed, and the guard stays idle.
local PIT_SITE = 0x002838CC
local function s32(a)
  local v = memory.u8[a] | (memory.u8[a + 1] << 8) | (memory.u8[a + 2] << 16) | (memory.u8[a + 3] << 24)
  return v >= 0x80000000 and v - 0x100000000 or v
end
-- (0.1.23: no memory.getSymbol('pit_ot') first. It never resolves across scripts and the loader logged
-- "Symbol pit_ot is not registered" on every map jump.)
local function companionsByte()
  if memory.u8[PIT_SITE] ~= 0xE9 then return nil end
  local code = PIT_SITE + 5 + s32(PIT_SITE + 1)
  if memory.u8[code] ~= 0x80 then return nil end
  if memory.u8[code + 1] == 0x3D and memory.u8[code + 6] == 0x00 then return code + 7 + s32(code + 2) end
  if memory.u8[code + 1] == 0x3C and memory.u8[code + 2] == 0x25 and memory.u8[code + 7] == 0x00 then
    return s32(code + 3) & 0xFFFFFFFF
  end
  return nil
end
local solo = {held = false, saved = nil, missing = false}
local function soloGuard(at)
  local ok, sym = pcall(companionsByte)
  if not ok or type(sym) ~= 'number' or sym <= 0 then
    if not solo.missing then solo.missing = true; log('palace guard idle: The Insurgent\'s Companions not found') end
    return
  end
  local inPalace = type(at) == 'number' and at >= PALACE_FIRST and at <= PALACE_LAST
  if inPalace and not solo.held then
    local value = memory.u8[sym]
    if value ~= 0 and value ~= 1 then
      log(string.format('palace guard idle: unexpected Companions setting %s at 0x%X', tostring(value), sym))
      return
    end
    solo.saved, solo.held = memory.u8[sym], true
    if solo.saved ~= 0 then memory.u8[sym] = 0 end
    log(string.format('palace guard: companions in towns held at Leader Only (location %d, was %s)', at,
      tostring(solo.saved)))
  elseif not inPalace and solo.held then
    memory.u8[sym] = solo.saved or 1
    solo.held = false
    log(string.format('palace guard: companions in towns back to %s (location %s)', tostring(solo.saved or 1),
      tostring(at)))
  end
end

local pendingCharge = {}
local function reset()
  campaign, claimed, logged, rankLogged, foes = nil, 0, 0, false, {}
  freshGame = false
  pendingCharge = {}
  if R.resetBudgets then R.resetBudgets() end
  if R.resetPushes then R.resetPushes() end
  writs, writsKnown, flagsLogged = {false, false, false}, false, false
  gear.keys = {}
  -- The native text hook stays installed across save loads. Remove old campaign pointers immediately; a
  -- non-Legacy save never enters gearTick's rebuild path, and must not inherit the previous character's text.
  if gear.table then
    for member = 0, MEMBERS - 1 do
      for stage = 1, 3 do setSuffix(member, stage, nil) end
    end
  end
  P.reset()
  if Accessory then Accessory.reset() end
end

-- ---------- session logs (lives here until the next main-package release) ----------
-- The loader rewrites hook.log at every launch, so a fight is lost once the game restarts. This keeps the last five
-- sessions beside it: at startup hook-previous-4.log -> 5, ..., hook-session.log -> hook-previous-1.log; then
-- hook.log is copied to hook-session.log every 30 seconds and on exit (a crash loses at most 30 seconds).
local SESSION, PREVIOUS, KEEP, COPY_MS = 'hook-session.log', 'hook-previous-%d.log', 5, 30000
local function readAll(path)
  local f = io.open(path, 'rb')
  if not f then return nil end
  local data = f:read('a')
  f:close()
  return data
end
local function writeAll(path, data)
  local f = io.open(path, 'wb')
  if not f then return false end
  f:write(data)
  f:close()
  return true
end
local function remove(path)
  if type(os) == 'table' and type(os.remove) == 'function' then pcall(os.remove, path) else writeAll(path, '') end
end
local function moveLog(from, to)
  local data = readAll(from)
  if data == nil or #data == 0 then return end
  if writeAll(to, data) then remove(from) end
end
function R.rotateLogs()
  remove(string.format(PREVIOUS, KEEP))
  for n = KEEP - 1, 1, -1 do moveLog(string.format(PREVIOUS, n), string.format(PREVIOUS, n + 1)) end
  moveLog(SESSION, string.format(PREVIOUS, 1))
end
function R.copyLog()
  local data = readAll('hook.log')
  if data and #data > 0 then writeAll(SESSION, data) end
end
local function copyTick()
  pcall(R.copyLog)
  event.executeAfterMs(COPY_MS, copyTick)
end

function R.register()
  if type(memory) == 'table' and type(memory.assemble) == 'function' then pcall(R.installWidthFix) end
  if type(io) == 'table' and type(io.open) == 'function' and readAll('hook.log') ~= nil then
    local ok = pcall(R.rotateLogs)
    log(ok and string.format('session logs: previous sessions kept as hook-previous-1..%d.log', KEEP)
      or 'session logs: rotation failed')
    if type(event.executeAfterMs) == 'function' then event.executeAfterMs(COPY_MS, copyTick) end
    if type(event.registerEventAsync) == 'function' then
      event.registerEventAsync('exit', function() pcall(R.copyLog) end)
    end
  end
  local ok = event.registerEventSync('onSaveLoad', function() reset() return true end) == true
  ok = event.registerEventSync('onNewGameStart', function() reset(); freshGame = true return true end) == true and ok
  ok = event.registerLoadHandler(SAVE_ID, function(path, data)
    reset()
    if type(path) ~= 'string' or path:find('[%z\r\n]') then return end
    local name = path:match('([^/\\]+)$')
    if not (name and name:match('^LEGCY_%d%d%d$') and legacyMode()) then return end
    campaign = name
    -- The side file sits beside this save, so it is this save's data even when it was written while another slot's
    -- campaign was loaded (saving into a new slot keeps the old name).
    if type(data) == 'table' and data.schema == 1 and type(data.claimedRank) == 'number'
        and data.claimedRank % 1 == 0 and data.claimedRank >= 0 and data.claimedRank <= P.MAX_RANK then
      claimed = data.claimedRank
      if data.campaign ~= name then
        log(string.format('side file of %s was written as %s; using its rank %d', name, tostring(data.campaign), claimed))
      end
    end
    location = memory.u32[LOCATION]
    pcall(soloGuard, location)
    watchTopic()
    local L = P.levels(claimed)
    log(string.format('campaign=%s claimed rank %d; levels I=%d II=%d III=%d', name, claimed, L[1], L[2], L[3]))
  end) == true and ok
  ok = event.registerSaveHandler(SAVE_ID, function()
    if not campaign then return nil end
    return {schema = 1, campaign = campaign, claimedRank = claimed}
  end) == true and ok
  ok = event.registerEventAsync('onMapJump', function(to)
    pcall(soloGuard, to)
    local from = location
    location = to
    foes = {}
    -- Battle identifiers are reused by the next map. Old chains and Stop budgets must not follow those slots.
    P.reset()
    if Accessory then Accessory.reset() end
    pendingCharge = {}
    writsKnown = false  -- the Writs are read again on the next use (a key item can come or go between maps)
    if campaign and not rankLogged then
      rankLogged = true
      log(string.format('campaign=%s clan rank %s (claimed %d), location %d', campaign, tostring(clanRank()), claimed, to))
    end
    if from == CLAN_HALL and to ~= CLAN_HALL then pcall(remind) end
    watchTopic()
    if campaign and legacyMode() then effective(); refreshWrits(); syncTip() end
  end) == true and ok
  if type(event.executeAfterMs) == 'function' then event.executeAfterMs(GEAR_POLL_MS, gearTick) end
  return ok
end

-- ---------- snapshots ----------
local function statusSet(flags)
  local out = {}
  for name in pairs(P.STATUS) do if flags[name] == 1 then out[name] = true end end
  for _, name in ipairs(P.HINDERED) do if flags[name] == 1 then out[name] = true end end
  return out
end
R.statusSet = statusSet

-- A foe's status snapshot (0.1.44, decision 3): the statuses, plus the pseudo-flag `broken` while a party member's cut on it
-- is under 10 s old, so every "foe has an ailment" test reads Broken too. `foe` is the foe's keep.
local function foeStatusSet(flags, foe, now)
  local out = statusSet(flags)
  if R.isBroken(foe, now) then out.broken = true end
  return out
end
R.foeStatusSet = foeStatusSet

local function clock() return event.getClock and event.getClock() or 0 end

-- Casts. An area spell reaches its targets one after another, about a second apart (Cura on the party, logged
-- 2026-11-10), so a time window cannot tell "the next target" from "the next cast". Forge's onCastPre (222) runs
-- before each target's effects; the call for the spell's initial target starts a new cast for that caster. Every
-- target of that cast then shares its id. P.CAST_WINDOW stays only as the fallback when no id was seen.
-- Only Magicks (battle menu category 1) are casts here: 222 also runs in onHitPre, and a counter or weapon hit landing
-- between a spell's targets must not split that spell.
local castSeq = {}
local function isMagick(action)
  if type(action) ~= 'number' or type(bpack) ~= 'table' then return false end
  local ok, record = pcall(function() return bpack.section14[action] end)
  return ok and type(record) == 'table' and record.category == 1
end
function R.startCast(formula, caster, target)
  if caster.type ~= 0 or caster.address == 0 or target.address == 0 or formula.causeType == 2 then return end
  if formula.initialTarget.address ~= target.address or not isMagick(formula.action) then return end
  castSeq[caster.address] = (castSeq[caster.address] or 0) + 1
end
-- The cast id of this action, or nil when it is not a Magick (weapon hits, items, Technicks are never one cast).
function R.castId(caster, action)
  if action ~= nil and not isMagick(action) then return nil end
  return castSeq[caster.address]
end

-- Per-cast MP return budget, keyed by the caster: every target's Proficiency MP comes out of the same budget.
-- Not a Magick (items, Technicks) = no budget, no cap.
local budgets = {}
function R.castBudget(caster, action, now)
  if not Economy or type(action) ~= 'number' or type(bpack) ~= 'table' then return nil end
  local ok, record = pcall(function() return bpack.section14[action] end)
  if not ok or not Economy.priced(record) then return nil end
  local b, id = budgets[caster.address], castSeq[caster.address]
  if b and b.action == action then
    if id ~= nil and b.cast == id then return b end
    if id == nil and b.cast == nil and now - b.at < P.CAST_WINDOW then return b end
  end
  -- The cap is half of what the cast really cost this caster (mp_economy 0.2.0: Channeling, Parsimony, Spellspring and
  -- Offering lower what the game charges, so they lower the cap too).
  local paid = record.mpOrMistCost
  if type(Economy.paid) == 'function' then
    paid = Economy.paid(record.mpOrMistCost, Economy.reader({caster.permanentAugments, caster.temporaryAugments}))
  end
  b = {action = action, at = now, cast = id, left = Economy.returnCap(record.mpOrMistCost, caster.magickPower, paid)}
  budgets[caster.address] = b
  return b
end
function R.resetBudgets() budgets = {} end

function R.partyActor(unit)
  local id = unit.identifier
  local root = memory.u64[0x02EBF190]
  if type(root) ~= 'number' or root <= 0 or type(id) ~= 'number' or id % 1 ~= 0 or id < 0 or id > 5
    or unit.address ~= root + 8 + id * 0x1C8 then return nil end
  return id
end

function R.party(classes, helpers)
  local out = {}
  for i = 0, 3 do
    local id = helpers.getActivePartyMember(i)
    if type(id) == 'number' and id >= 0 and id <= 39 then
      local addr = helpers.getBattleUnitKeep(id)
      local k = classes.getBattleUnitKeep(addr)
      if k.currentHp > 0 and k.maxHp > 0 then
        out[#out + 1] = {address = addr, job = R.lineOf(k.job1, k.job2),
                         hpPct = 100 * k.currentHp // k.maxHp, hp = k.currentHp, maxHp = k.maxHp, mp = k.currentMp, maxMp = k.maxMp,
                         status = statusSet(k.temporaryStatusEffects)}
      end
    end
  end
  return out
end

function R.seeFoe(address)
  if type(address) == 'number' and address > 0 then foes[address] = clock() end
end

-- Other engaged, living foes (not `except`), most recently seen first.
function R.foes(classes, except)
  local now, out, stale = clock(), {}, {}
  for addr, seen in pairs(foes) do
    if now - seen > FOE_MEMORY then stale[#stale + 1] = addr
    elseif addr ~= except then
      local k = classes.getBattleUnitKeep(addr)
      if k.type == 1 and k.currentHp > 0 then
        out[#out + 1] = {address = addr, seen = seen, status = statusSet(k.temporaryStatusEffects)}
      end
    end
  end
  for _, addr in ipairs(stale) do foes[addr] = nil end
  table.sort(out, function(x, y) return x.seen > y.seen end)
  return out
end

-- Snapshot for one party member (`unit` = its battle unit keep, `flags` = its status flags).
-- The Proficiency line of a character: its job's, or its job pair's hybrid line (nil = an invalid pair).
function R.lineOf(job1, job2)
  if job2 == nil or job2 == P.NO_JOB then return job1 end
  return Hybrid.id(job1, job2)
end
-- Whether a line reacts to an event at all (the hooks skip everything else).
function R.listens(job, event)
  local rule = job and P.rules[job]
  return rule ~= nil and rule[event] ~= nil
end

-- 2026-11-16 (Marco, "Haste is haste"): the Hermes Sandals and Pixie Shoes guards are gone. A status a unit wears
-- counts everywhere the game asks for it: Haste for the Zanshin and hybrid Hasted stages, Float for Highwind. Hermes
-- no longer wears Haste; it grants one at the start of a fight (accessory_effects.lua, the fightStart event).
-- Second Accessory (deployed with Legacy) keeps each party member's second accessory in its own memory: global symbol
-- sa_saveMem, a u16 per member (stride 4), indexed by the battle-unit keep's identifier byte (+0x04), as its own
-- assembly and Duplicate Augment Detector read it. Resolved once; anything missing means "no second accessory".
local secondAccessoryMem
function R.secondAccessory(keep)
  if secondAccessoryMem == nil then
    local ok, mem = pcall(memory.getSymbol, 'sa_saveMem')
    secondAccessoryMem = ok and type(mem) == 'number' and mem ~= 0 and mem or false
  end
  if not secondAccessoryMem then return nil end
  local index = keep.identifier
  if type(index) ~= 'number' or index < 0 or index >= 40 then return nil end
  return memory.u16[secondAccessoryMem + index * 4]
end
-- The accessories a party unit wears, both slots, each id once (an id in both slots counts once). {} when unreadable.
function R.worn(unit, classes)
  local ok, main, second = pcall(function()
    local keep = classes.getBattleUnitKeep(unit.address)
    return keep.accessory, R.secondAccessory(keep)
  end)
  if not ok then return {} end
  local out = {}
  if type(main) == 'number' then out[1] = main end
  if type(second) == 'number' and second ~= main then out[#out + 1] = second end
  return out
end
function R.wears(unit, id, classes)
  for _, worn in ipairs(R.worn(unit, classes)) do if worn == id then return true end end
  return false
end

function R.base(unit, flags, classes, helpers)
  if unit.type ~= 0 then return nil end
  -- A character with a second job uses its pair's hybrid line, never either parent's.
  local job = R.lineOf(unit.job1, unit.job2)
  if not job then return nil end
  local actor = R.partyActor(unit)
  if not actor then return nil end
  local status = statusSet(flags)
  return {job = job, actorId = actor, self = unit.address, party = R.party(classes, helpers),
          casterStatus = status, casterHpPct = unit.maxHp > 0 and 100 * unit.currentHp // unit.maxHp or 100, casterHp = unit.currentHp,
          casterMp = unit.currentMp, casterMaxMp = unit.maxMp, selfMaxHp = unit.maxHp,
          roll = function(percent) return helpers.getRandomNumber(100) < percent end,
          canLand = function(address, id)
            if not address then return true end
            local keep = classes.getBattleUnitKeep(address)
            return R.mayAdd(keep, id, keep.temporaryStatusEffects, helpers)
          end}
end

function R.element(action)
  local mask, rec = 0, bpack.section14[action]
  for i, name in ipairs(ELEMENTS) do if rec.elements[name] == 1 then mask = mask | (1 << (i - 1)) end end
  return mask, rec.mpOrMistCost
end

-- ---------- applying ----------
-- Forge's end-of-hit step and its add helper apply a status outright: the checks live in the on-hit functions 181/182.
-- So everything Proficiency adds passes the same checks here, or immune foes would be Stopped, Slowed and Immobilized.
function R.mayAdd(keep, id, flags, helpers)
  local record = bpack.section15[id]
  if keep.permanentStatusEffectImmunities[id] == 1 or keep.permanentStatusEffects[id] == 1 then return false end
  if keep.temporaryStatusEffects[id] == 1 and record.flags1.isRefreshable == 0 then return false end
  if keep.type == 1 and record.flags1.isNotApplicableToFoes == 1 then return false end
  if record.flags1.isNegative == 1 and (keep.temporaryAugments.statusImmunity == 1 or keep.permanentAugments.statusImmunity == 1) then
    return false
  end
  return not helpers.getStatusEffectsMatch(record.absentStatusEffects, flags)
end

-- A formula may already have a longer grant queued before the status reaches the unit. Forge 323/324 resolves
-- a duration of -1 to the caster's full duration when it commits the formula; an unmarked slot is not a grant.
local function queuedDuration(plus, source, id, helpers)
  if not plus or plus.addedStatusEffects[id] ~= 1 then return nil end
  local d = plus.statusEffectDurations[id]
  if d == -1 then return helpers.getStatusEffectDuration(source, id) end
  return d
end

-- The add helper overwrites the timer. Preserve a longer running OR queued status. nil = leave it.
function R.duration(keep, source, id, scale, helpers, plus)
  local d = math.max(1, math.floor(helpers.getStatusEffectDuration(source, id) * scale))
  if keep.temporaryStatusEffects[id] == 1 and keep.statusEffectDurations[id] >= d then return nil end
  local queued = queuedDuration(plus, source, id, helpers)
  if queued and queued >= d then return nil end
  return d
end

local function grant(plus, keep, source, id, scale, helpers)
  if not R.mayAdd(keep, id, plus.statusEffects, helpers) then return false end
  local d = R.duration(keep, source, id, scale, helpers, plus)
  if not d then return false end
  plus.addedStatusEffects[id] = 1
  plus.statusEffectDurations[id] = d
  return true
end
-- A party member's effect landed status `id` on a foe: the accessory memory keeps who landed it.
local function landed(keep, id, by)
  if Accessory and type(keep) == 'table' and keep.type == 1 then
    pcall(Accessory.noteAilment, keep.address, id, by, clock())
  end
end

-- Black Mage: when a spell lands the caster's timer is already at its end and is reset afterwards, so the bonus waits
-- for the next charge to start (at most 10 s).
local CHARGE_POLL_MS, CHARGE_WAIT = 100, 10000000
local chargePolling = false
local function chargeTick()
  local now, any = clock(), false
  for address, p in pairs(pendingCharge) do
    local w = p.classes.getBattleUnitWork(p.helpers.getBattleUnitWork(address))
    if w.address == 0 or now > p.expires then pendingCharge[address] = nil
    else
      local cur, req = w.currentChargeTime, w.requiredChargeTime
      if cur > 0 and cur < req and (p.above or cur < p.last) then
        w.currentChargeTime = math.min(req - 1, cur + math.floor(req * p.fraction))
        log(string.format('next action charge +%d%% (%d of %d)', math.floor(p.fraction * 100 + 0.5), cur, req))
        pendingCharge[address] = nil
      else
        p.last = cur
        any = true
      end
    end
  end
  chargePolling = any
  if any then event.executeAfterMs(CHARGE_POLL_MS, chargeTick) end
end
function R.armCharge(address, fraction, classes, helpers)
  local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(address))
  if w.address == 0 then return end
  -- A bonus already waiting for the same next action (a Proficiency stage and an accessory on one hit) is never cut:
  -- the larger one stays.
  local waiting = pendingCharge[address]
  if waiting and clock() <= waiting.expires and waiting.fraction > fraction then fraction = waiting.fraction end
  pendingCharge[address] = {fraction = fraction, expires = clock() + CHARGE_WAIT, classes = classes, helpers = helpers,
                            above = w.currentChargeTime >= w.requiredChargeTime, last = w.currentChargeTime}
  if not chargePolling and type(event.executeAfterMs) == 'function' then
    chargePolling = true
    event.executeAfterMs(CHARGE_POLL_MS, chargeTick)
  end
end

-- Charge push-back budget per foe: every Proficiency push on one foe together removes at most PUSH_SHARE of its charge
-- per charge cycle (bosses, marks and Kings: PUSH_SHARE_TOUGH), so a party of pushers cannot keep it from ever acting.
-- A new cycle starts when the foe acts (its own hit or spell lands) or its charge is found below where we left it.
R.PUSH_SHARE, R.PUSH_SHARE_TOUGH = 0.25, 0.125
local okKinds, KINDS = pcall(function() return assert(loadfile('scripts/DynamicFoes/Foes.lua', 't', _ENV))() end)
if not okKinds or type(KINDS) ~= 'table' then KINDS = {} end
local TOUGH = {boss = true, mark = true, rare = true}
local pushes = {}
-- `w` (the foe's battle unit work, when known) tells a real action from a counterattack: a counter lands while the charge
-- is still filling and does not restart it, so it must not hand out a fresh budget (0.1.35 review). A real action shows as
-- a full charge or a charge that fell below where the last push left it. Unreadable work resets, as before.
function R.foeActed(address, w)
  local rec = pushes[address]
  if rec and type(w) == 'table' then
    local cur, req = w.currentChargeTime, w.requiredChargeTime
    if type(cur) == 'number' and type(req) == 'number' and req > 0 and cur > 0 and cur < req and cur >= rec.after then return end
  end
  pushes[address] = nil
end
function R.resetPushes() pushes = {} R.acting = {} end
-- 0.1.45: the push record also keeps the foe's fingerprint (maxHp, level), like the Broken, dent, cadence and lure tables,
-- so a foe at a recycled address in a later fight never inherits a spent budget. `foe` is the foe's battle unit keep; a
-- caller that cannot give one leaves the fingerprint nil and the address-only rule applies.
function R.foePrint(foe)
  if type(foe) == 'table' and type(foe.maxHp) == 'number' then return foe.maxHp, foe.level end
  return nil, nil
end
function R.samePrint(rec, hp, level)
  return hp == nil or rec.hp == nil or (rec.hp == hp and rec.level == level)
end
-- 0.1.45: a foe's turn ends its Staggered record. Every foe action reaches one of the hooks that call this (165 and 170 as
-- before, now also 240 for an evaded or parried attack, 181 for a status or buff spell, 110 and 127 for a heal), so no
-- spent record survives to be read stale. R.foeActed still tells a counterattack from a real action by the charge.
function R.foeActs(caster, classes, helpers)
  local okWork, work = pcall(function()
    local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(caster.address))
    return w.address ~= 0 and w or nil
  end)
  R.foeActed(caster.address, okWork and work or nil)
end
local function pushShare(w)
  local ok, name = pcall(function() return w.ardUnit.name end)
  return (ok and TOUGH[KINDS[name]]) and R.PUSH_SHARE_TOUGH or R.PUSH_SHARE
end
-- `by` (a party member's keep address) is remembered when its push uses up the budget: that member Staggered the foe.
function R.pushAllowance(address, w, wanted, by, foe)
  if type(wanted) ~= 'number' or wanted <= 0 then return 0 end
  local cur, req = w.currentChargeTime, w.requiredChargeTime
  local hp, level = R.foePrint(foe)
  local rec = pushes[address]
  if not rec or cur < rec.after or not R.samePrint(rec, hp, level) then rec = {spent = 0}; pushes[address] = rec end
  if hp and rec.hp == nil then rec.hp, rec.level = hp, level end
  local cap = math.floor(req * pushShare(w))
  local allowed = math.max(0, math.min(wanted, cap - rec.spent))
  rec.spent, rec.after = rec.spent + allowed, cur - allowed
  if allowed > 0 and rec.spent >= cap then rec.by = type(by) == 'number' and by or nil end
  return allowed
end

-- Staggered (0.1.44): a foe whose push budget for its current turn is used up is Staggered until it acts (R.foeActed clears
-- the record; 0.1.45: on every foe turn). The budget itself does not change and pushes still stop at it. `w` is the foe's
-- battle unit work, `foe` (optional) its keep for the fingerprint. A charge found below where the last push left it is a
-- new cycle. Returns the state and who Staggered it (a keep address or nil).
function R.isStaggered(address, w, foe)
  local rec = pushes[address]
  if not rec or type(w) ~= 'table' then return false, nil end
  local hp, level = R.foePrint(foe)
  if not R.samePrint(rec, hp, level) then pushes[address] = nil return false, nil end   -- another foe at this address
  local ok, cur, req = pcall(function() return w.currentChargeTime, w.requiredChargeTime end)
  if not ok or type(cur) ~= 'number' or type(req) ~= 'number' or req <= 0 or cur < rec.after then return false, nil end
  local cap = math.floor(req * pushShare(w))
  if cap <= 0 or rec.spent < cap then return false, nil end
  return true, rec.by
end
-- The same for a foe by address (reads its battle unit work through the Forge tables); false when it cannot be read.
function R.foeStaggered(address, classes, helpers)
  local ok, staggered, by = pcall(function()
    local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(address))
    if w.address == 0 then return false end
    return R.isStaggered(address, w, classes.getBattleUnitKeep(address))
  end)
  if not ok then return false, nil end
  return staggered == true, by
end
-- 0.1.46: one foe action reaches hook 240 once for every target and every hit (an area attack, a multi-hit). The first call
-- of the action reads Staggered and ends the record (R.foeActs); the later calls of the same action reuse that reading, so
-- every target and every hit hears the state the action started with. The same action: the same foe (address and
-- fingerprint), the same action id and first target, each call within ACTION_WINDOW of the one before, and (0.1.47) the
-- foe's charge (current and required, as R.foeActed reads it) unchanged since the latch, so a quick counter, whose charge
-- has moved on, is read afresh. 0.1.47: a push record made mid-action (a guard push on an earlier target) does not undo
-- the action's reading: the call reads afresh, ends the record again and answers `last.staggered or fresh`; the charge may
-- then differ from the latch only by that push (it sits where the record's last push left it). Anything else, or no
-- clock, or a charge that cannot be read, reads afresh and ends the record again (the per-call rule of 0.1.45).
R.ACTION_WINDOW = 1000000   -- 1 s between two calls of one action (event clock, microseconds)
Hybrid.ACTION_WINDOW = R.ACTION_WINDOW   -- the same window groups the hits of one foe attack for the struck stages
R.acting = {}
function R.foeCharge(address, classes, helpers)
  local ok, cur, req = pcall(function()
    local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(address))
    if w.address == 0 then return nil end
    return w.currentChargeTime, w.requiredChargeTime
  end)
  if ok and type(cur) == 'number' and type(req) == 'number' then return cur, req end
  return nil, nil
end
function R.foeAction(formula, caster, classes, helpers)
  local address, now = caster.address, clock()
  local okA, action, first = pcall(function() return formula.action, formula.initialTargetPointer end)
  local hp, level = R.foePrint(caster)
  local cur, req = R.foeCharge(address, classes, helpers)
  local last = R.acting[address]
  local carried = false
  if last and okA and type(now) == 'number' and now > 0 and now >= last.at and now - last.at <= R.ACTION_WINDOW
     and last.action == action and last.first == first and last.hp == hp and last.level == level and cur ~= nil then
    local rec = pushes[address]
    local still = cur == last.cur and req == last.req
    if rec == last.rec and still then
      last.at = now
      return last.staggered, last.by
    end
    carried = still or (type(rec) == 'table' and req == last.req and cur == rec.after)
  end
  local staggered, by = R.foeStaggered(address, classes, helpers)
  R.foeActs(caster, classes, helpers)
  if carried and last.staggered then staggered, by = true, last.by end
  -- id: what one foe action is, for the struck and counter stages (a multi-hit attack is one blow). A fresh action takes a
  -- new id; a carried one (the same action continuing after a push on it) keeps its id.
  R.actionSerial = (R.actionSerial or 0) + 1
  R.acting[address] = okA and {at = now, action = action, first = first, hp = hp, level = level, rec = pushes[address],
                               cur = cur, req = req, staggered = staggered, by = by,
                               id = carried and last.id or R.actionSerial} or nil
  return staggered, by
end

-- Push a foe's charge back by `fraction` of what it needs, inside the shared budget. Returns the charge before and after
-- when something moved. Only a foe that is charging, and whose charge sits where it was last seen, moves.
function R.pushFoe(w, address, fraction, by, foe)
  local cur, req, last = w.currentChargeTime, w.requiredChargeTime, w.lastChargeTime
  if cur > 0 and cur < req and last == cur then
    local wanted = cur - math.max(1, cur - math.floor(req * fraction))
    local allowed = R.pushAllowance(address, w, wanted, by, foe)
    w.currentChargeTime = cur - allowed
    if allowed > 0 then return cur, cur - allowed end
  end
  return nil
end

-- Whether a foe is a boss, mark or rare game (the kinds table of Dynamic Foes, as the push budget reads it).
function R.isTough(address, classes, helpers)
  local ok, tough = pcall(function()
    local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(address))
    return w.address ~= 0 and TOUGH[KINDS[w.ardUnit.name]] == true
  end)
  return ok and tough == true
end

local function pushCharge(classes, helpers, unit, fraction, by)
  local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(unit))
  if w.address == 0 then return end
  local okKeep, keep = pcall(classes.getBattleUnitKeep, unit)
  R.pushFoe(w, unit, fraction, by, okKeep and keep or nil)
end

-- Forge's modifyHp/modifyMp change the pool silently; the game's own hits show their numbers through Forge 324's
-- showNumberText. Proficiency's heals, drains and MP returns call the same helper with what actually changed, so a
-- heal shows green and nothing shows at a full pool. Number types: 0 MP loss, 1 HP damage, 2 HP heal, 3 MP gain.
-- (Before 0.5.1 these effects happened without any number on screen; play report, 2026-11-10.)
function R.changeShown(classes, helpers, unit, field, amount, change)
  local okBefore, before = pcall(function() return classes.getBattleUnitKeep(unit)[field] end)
  change()
  pcall(function()
    local shown = amount
    if okBefore and type(before) == 'number' then shown = classes.getBattleUnitKeep(unit)[field] - before end
    if shown == 0 or type(helpers.showNumberText) ~= 'function' then return end
    local work = helpers.getBattleUnitWork(unit)
    if type(work) ~= 'number' or work == 0 then return end
    local kind = field == 'currentHp' and (shown > 0 and 2 or 1) or (shown > 0 and 3 or 0)
    helpers.showNumberText(work, shown, kind)
  end)
end

-- Apply an action table for party member `member`. Formula fields are used only when `formula` is given and the
-- member is that formula's caster; everything in a.others goes through Forge helpers with the member as source.
function R.apply(a, formula, member, classes, helpers, target)
  -- A spell's drain from a foe is MP the spell hands back too: it comes out of the same budget (a foe's MP pool is
  -- deep, so a drain per foe on an area spell paid for the spell many times over). Clamped before anything is written.
  if a.mpBudget and a.drainMp then
    local paid = math.min(a.drainMp, math.max(0, a.mpBudget.left))
    a.mpBudget.left = a.mpBudget.left - paid
    a.drainMp = paid > 0 and paid or nil
  end
  if formula then
    if a.damage and formula.target.removedHp ~= -1 then
      formula.target.removedHp = math.floor(formula.target.removedHp * a.damage)
    end
    for id, scale in pairs(a.casterAdd or {}) do grant(formula.caster, member, member.address, id, scale, helpers) end
    for id, scale in pairs(a.targetAdd or {}) do
      if grant(formula.target, target, member.address, id, scale, helpers) then landed(target, id, member.address) end
    end
    -- Renewing restarts the timer: the helper writes it even when the status is already on.
    for _, id in ipairs(a.targetRenew or {}) do
      if target.temporaryStatusEffects[id] == 1 then
        formula.target.addedStatusEffects[id] = 1
        -- Pursuit II-3/4: the restarted timer runs a little longer than a normal one (renewScale, e.g. 1.10).
        if a.renewScale then
          local longer = math.max(1, math.floor(helpers.getStatusEffectDuration(member.address, id) * a.renewScale))
          local queued = queuedDuration(formula.target, member.address, id, helpers)
          if not queued or queued < longer then formula.target.statusEffectDurations[id] = longer end
        end
      end
    end
    for id, fraction in pairs(a.targetExtend or {}) do
      local full = helpers.getStatusEffectDuration(member.address, id)
      local now = target.statusEffectDurations[id]
      if target.temporaryStatusEffects[id] == 1 and now < full then
        local extended = math.min(full, now + math.max(1, math.floor(full * fraction)))
        local queued = queuedDuration(formula.target, member.address, id, helpers)
        if not queued or queued < extended then
          formula.target.addedStatusEffects[id] = 1
          formula.target.statusEffectDurations[id] = extended
        end
      end
    end
    for _, id in ipairs(a.casterRemove or {}) do formula.caster.removedStatusEffects[id] = 1 end
    for _, id in ipairs(a.targetRemove or {}) do formula.target.removedStatusEffects[id] = 1 end
    -- MP moves ADD to whatever an earlier stage already queued on this formula (a native MP drain, another Legacy
    -- stage), as Family Spellbreak does; replacing them would erase that stage's MP change.
    local function queued(side, field)
      return side.mpUpdateState == 1 and (side[field] or 0) or 0
    end
    if a.drainMp then
      local lost, gained = queued(formula.target, 'removedMp'), queued(formula.caster, 'addedMp')
      local casterLoss = queued(formula.caster, 'removedMp')
      formula.target.mpUpdateState, formula.target.addedMp, formula.target.removedMp = 1, queued(formula.target, 'addedMp'), lost + a.drainMp
      formula.caster.mpUpdateState, formula.caster.addedMp, formula.caster.removedMp = 1, gained + a.drainMp, casterLoss
    elseif a.targetMpLoss then
      local lost = queued(formula.target, 'removedMp')
      formula.target.mpUpdateState, formula.target.addedMp, formula.target.removedMp = 1, queued(formula.target, 'addedMp'), lost + a.targetMpLoss
    end
    -- A foe the drain really empties is Silenced: the drain may have been shortened by the cast's MP budget above.
    if a.leechSilence and (a.drainMp or 0) >= a.leechSilence.need then
      grant(formula.target, target, member.address, P.STATUS.silence, a.leechSilence.scale, helpers)
    end
    if a.healHp and formula.caster.hpUpdateState ~= 1 then
      formula.caster.hpUpdateState, formula.caster.addedHp, formula.caster.removedHp = 1, a.healHp, 0
    end
    if a.chargeTo then
      local w = formula.target.battleUnitWork
      local wanted = w.currentChargeTime - a.chargeTo
      if target and target.address and target.address ~= 0 then
        w.currentChargeTime = w.currentChargeTime - R.pushAllowance(target.address, w, wanted, member.address, target)
      else
        w.currentChargeTime = a.chargeTo
      end
    end
  end
  if a.casterCharge then R.armCharge(member.address, a.casterCharge, classes, helpers) end
  -- Warrior Cleave II-2..4: the hit Breaks the foe (the shared Broken clock family_identities keeps, as a Brave spear does).
  if a.breakFoe and type(target) == 'table' and target.type == 1 then
    local family = rawget(_G, 'IvaliceLegacyFamily')
    if type(family) == 'table' and type(family.noteBroken) == 'function' then
      pcall(family.noteBroken, target, clock(), member.address)
    end
  end
  -- MP a spell hands back is capped by its cast's budget; MP taken from a foe's own pool by a reaction is free.
  local freeMp = 0
  if a.mpBudget then
    for _, o in ipairs(a.others or {}) do
      if o.op == 'mp' and type(o.amount) == 'number' and o.amount < 0 then freeMp = freeMp - o.amount end
    end
  end
  for _, o in ipairs(a.others or {}) do
    if a.mpBudget and o.op == 'mp' and type(o.amount) == 'number' and o.amount > 0 then
      local free = math.min(freeMp, o.amount)
      freeMp = freeMp - free
      local paid = math.min(o.amount - free, math.max(0, a.mpBudget.left))
      a.mpBudget.left = a.mpBudget.left - paid
      o.amount = free + paid
    end
    if o.op == 'add' then
      local keep = classes.getBattleUnitKeep(o.unit)
      if R.mayAdd(keep, o.id, keep.temporaryStatusEffects, helpers) then
        local d = R.duration(keep, member.address, o.id, o.scale or 1, helpers)
        if d then
          helpers.addStatusEffect(member.address, o.unit, o.id, d, helpers.getStatusEffectTickDuration(o.id))
          landed(keep, o.id, member.address)
        end
      end
    elseif o.op == 'remove' then helpers.removeStatusEffect(o.unit, o.id)
    elseif o.op == 'mp' then if o.amount ~= 0 then R.changeShown(classes, helpers, o.unit, 'currentMp', o.amount,
        function() helpers.modifyMp(o.unit, o.amount) end) end
    elseif o.op == 'hp' then R.changeShown(classes, helpers, o.unit, 'currentHp', o.amount,
        function() helpers.modifyHp(member.address, o.unit, o.amount, 0, 0, 0) end)
    elseif o.op == 'push' then pushCharge(classes, helpers, o.unit, o.fraction, member.address)
    elseif o.op == 'quick' then R.armCharge(o.unit, o.fraction, classes, helpers) end
  end
end

local errorLogged = {}
function R.run(name, fn)
  local ok, err = pcall(fn)
  if not ok and not errorLogged[name] then
    errorLogged[name] = true
    pcall(print, 'Legacy Proficiency: ' .. name .. ' error=' .. tostring(err):gsub('[%z\r\n]', ' '))
  end
end

function R.report(kind, s, a)
  local keys = {}
  for k in pairs(a) do keys[#keys + 1] = k end
  table.sort(keys)
  log(string.format('%s actor=%s job=%s L=%d/%d/%d %s', kind, tostring(s.actorId), tostring(s.job), s.L[1], s.L[2],
    s.L[3], table.concat(keys, ',')))
end

-- ---------- hooks (each Forge adapter calls one after its original function) ----------

-- 222 initializes the target in BOTH onCastPre and onHitPre, before formula effects. Watching the live flag
-- here clears an expired Stop's budget before a new Stop spell can hide that gap. A still-active Stop is untouched;
-- our own extensions therefore never reset their cap. No native statuses or timers are written here.
function R.hook222(formula, caster, target)
  R.startCast(formula, caster, target)
  if target.type == 1 and target.address ~= 0 then
    P.observeStop(formula.target.identifier, target.temporaryStatusEffects[P.STATUS.stop] == 1)
  end
end

-- World distance between the formula's caster and target (Ultima's Seitengrat reads the same fields). nil = unknown.
function R.distance(formula)
  local function at(plus)
    local ok, p = pcall(function() return plus.battleUnitWork.battleActorWork.battleActorModel.battleActorKeep.currentPosition end)
    if not ok or p == nil then return nil end
    local ok2, x, y, z = pcall(function() return p.x, p.y, p.z end)
    if not ok2 or type(x) ~= 'number' or type(y) ~= 'number' or type(z) ~= 'number' then return nil end
    return x, y, z
  end
  local cx, cy, cz = at(formula.caster)
  local tx, ty, tz = at(formula.target)
  if not cx or not tx then return nil end
  return math.sqrt((tx - cx) ^ 2 + (ty - cy) ^ 2 + (tz - cz) ^ 2)
end

-- 165: every physical hit after damage. Party member hitting a foe gives 'hit'; a foe dropping a party member to
-- critical HP gives the Knight's 'ally' reaction.
-- Combo follow-ups: the same member hitting the same foe again within COMBO_WINDOW (logged combos land 0.13-0.2 s apart,
-- separate attacks 0.75 s or more).
R.COMBO_WINDOW = 350000
local lastHit = {}

-- Fists, Cadence (0.1.34, audit D2): Family tells us when a streak completes (Family.onFullCadence(caster, target), called
-- by family_identities after the 165 hook of the same hit), and the fighter's Counter line hears it as ONE counter.
-- Fists never parry, so without this a fists Mystic's Ki Flow, and every hybrid counter stage, would never pay once counters
-- pay only on a real swing. The caster owns fists by wielding them. The Forge classes and helpers are those of the 165 call
-- that completed the streak.
local cadenceEnv = nil
function R.fullCadence(caster, target)
  local env = cadenceEnv
  if not env or not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local line = R.lineOf(caster.job1, caster.job2)
  if not line or not (line == P.JOB.mystic or line >= Hybrid.FIRST_ID) or not R.listens(line, 'guard') then return end
  local s = R.base(caster, caster.temporaryStatusEffects, env.classes, env.helpers)
  if not s then return end
  s.attacker, s.attackerStatus, s.attackerMp = target.address, foeStatusSet(target.temporaryStatusEffects, target, clock()), target.currentMp
  s.attackerMaxMp = target.maxMp
  s.attackerStaggered = R.foeStaggered(target.address, env.classes, env.helpers)
  s.attackerTough = R.isTough(target.address, env.classes, env.helpers)
  s.foes = R.foes(env.classes, target.address)
  s.outcome = 'counter'
  local a = P.decide('guard', s, effective())
  if a then R.apply(a, nil, caster, env.classes, env.helpers); R.report('cadence-counter', s, a) end
end
-- Broken (0.1.43): family_identities.lua keeps the clock; a missing or failing module means no foe is Broken. 0.1.44: the
-- argument is the foe's keep (the entry carries its maxHp and level), and brokenBy says who cut it.
function R.isBroken(target, now)
  local family = rawget(_G, 'IvaliceLegacyFamily')
  if type(family) ~= 'table' or type(family.isBroken) ~= 'function' then return false end
  local ok, v = pcall(family.isBroken, target, now)
  return ok and v == true
end
function R.brokenBy(target, now)
  local family = rawget(_G, 'IvaliceLegacyFamily')
  if type(family) ~= 'table' or type(family.brokenBy) ~= 'function' then return nil end
  local ok, by = pcall(family.brokenBy, target, now)
  return ok and by or nil
end
-- Channel (staff, 0.1.44): a damage spell cast with Faith Breaks the foe. The family module decides and keeps the clock.
function R.channel(formula, caster, target)
  local family = rawget(_G, 'IvaliceLegacyFamily')
  if type(family) ~= 'table' or type(family.channel) ~= 'function' then return end
  local ok, noted = pcall(family.channel, formula, caster, target)
  if ok and noted == true then log(string.format('channel actor=%s foe=%s broken', tostring(caster.identifier), tostring(target.address))) end
end
-- Whether an ally (not `caster`) Broke or Staggered this foe: what Hyper Wrist reads.
function R.alliedHold(caster, target, now, work)
  local by = R.brokenBy(target, now)
  local okS, staggered, staggeredBy = pcall(R.isStaggered, target.address, work, target)
  return by ~= nil and by ~= caster.address,
         okS and staggered == true and staggeredBy ~= nil and staggeredBy ~= caster.address
end

function R.bindFamily()
  local family = rawget(_G, 'IvaliceLegacyFamily')
  if type(family) == 'table' and family.onFullCadence == nil then
    family.onFullCadence = function(caster, target) R.run('fullCadence', function() R.fullCadence(caster, target) end) end
  end
end
function R.hook165(formula, caster, target, classes, helpers)
  R.bindFamily()
  cadenceEnv = {classes = classes, helpers = helpers}
  if target.type == 1 then R.seeFoe(target.address) end
  if caster.type == 1 then
    R.seeFoe(caster.address)
    R.foeActs(caster, classes, helpers)
  end
  if not R.active() then return end
  if caster.type == 0 and target.type == 1 then
    local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
    if not s then return end
    local damage, work = formula.target.removedHp, formula.target.battleUnitWork
    if damage == -1 then return end  -- the target was immune: nothing landed
    local category, weaponCt = nil, nil
    if type(caster.weapon) == 'number' and caster.weapon >= 0x1000 and caster.weapon - 0x1000 < bpack.section13.count then
      category = bpack.section13[caster.weapon - 0x1000].category
      weaponCt = bpack.section13[caster.weapon - 0x1000].chargeTime
    end
    s.targetId, s.foe, s.target, s.damage = formula.target.identifier, true, target.address, damage
    s.weapon, s.weaponCt = caster.weapon, weaponCt
    local now, prev = clock(), lastHit[caster.address]
    s.comboFollow = prev ~= nil and prev.target == target.address and now - prev.at < R.COMBO_WINDOW
    s.critical = formula.criticalState == 1
    lastHit[caster.address] = {at = now, target = target.address}
    s.kill, s.targetHp = damage >= target.currentHp, target.currentHp
    s.targetHpPct = target.maxHp > 0 and 100 * target.currentHp // target.maxHp or 100
    s.distance = R.distance(formula)
    s.bystander = formula.initialTargetPointer ~= 0 and formula.initialTargetPointer ~= target.address
    s.category, s.targetStatus, s.targetMp = category, foeStatusSet(formula.target.statusEffects, target, now), target.currentMp
    s.targetMaxMp = target.maxMp
    s.broken = R.isBroken(target, now)
    if s.job >= Hybrid.FIRST_ID then s.tough = R.isTough(target.address, classes, helpers) end
    if work.address ~= 0 then
      s.charge = {current = work.currentChargeTime, required = work.requiredChargeTime, last = work.lastChargeTime}
      s.staggered = R.isStaggered(target.address, work, target)
    end
    s.foes = R.foes(classes, target.address)
    local a = P.decide('hit', s, effective())
    if a then R.apply(a, formula, caster, classes, helpers, target); R.report('hit', s, a) end
  elseif caster.type == 1 and target.type == 0 and formula.target.removedHp ~= -1 and target.maxHp > 0 then
    local quarter = target.maxHp // 4
    local dropped = target.currentHp > quarter and target.currentHp - formula.target.removedHp <= quarter
    -- The struck member's own line (hybrids): reactions to being hit.
    local mine = formula.target.removedHp > 0 and R.listens(R.lineOf(target.job1, target.job2), 'struck')
      and R.base(target, formula.target.statusEffects, classes, helpers) or nil
    if mine then
      mine.attacker, mine.attackerStatus, mine.attackerMp = caster.address, foeStatusSet(caster.temporaryStatusEffects, caster, clock()), caster.currentMp
      mine.attackerMaxMp, mine.attackerTough = caster.maxMp, R.isTough(caster.address, classes, helpers)
      mine.damage, mine.dropCritical = formula.target.removedHp, dropped
      -- One foe attack reaches this hook once per hit: the clock and action id let the line count a multi-hit as one blow.
      mine.now = clock()
      local okAction, action = pcall(function() return formula.action end)
      if okAction then mine.action = action end
      local record = R.acting[caster.address]
      mine.actionId = record and record.id or nil
      mine.foes = R.foes(classes, caster.address)
      local a = P.decide('struck', mine, effective())
      if a then R.apply(a, nil, target, classes, helpers); R.report('struck', mine, a) end
    end
    if dropped then
      for _, p in ipairs(R.party(classes, helpers)) do
        if R.listens(p.job, 'ally') and p.address ~= target.address then
          local k = classes.getBattleUnitKeep(p.address)
          local s = R.base(k, k.temporaryStatusEffects, classes, helpers)
          if s then
            s.ally = target.address
            s.attacker, s.attackerStatus, s.attackerMp = caster.address, foeStatusSet(caster.temporaryStatusEffects, caster, clock()), caster.currentMp
            s.attackerMaxMp, s.attackerTough = caster.maxMp, R.isTough(caster.address, classes, helpers)
          end
          local a = s and P.decide('ally', s, effective())
          if a then R.apply(a, nil, k, classes, helpers); R.report('ally', s, a) end
        end
      end
    end
  end
end

-- 240: after the hit/dodge/parry/block/counter decision. A party member attacked by a foe gives 'guard'.
-- Forge 41-43 roll shield (outcomes 3/4), then the weapon's evade (1/2), then the body's evade (5); 2 and 4 carry a
-- counter. The weapon roll is the parry: an Invisible attacker cancels shield and weapon evade (Forge 33), and a
-- sword's evade sits in evadeWeapon (play log 2026-11-10: Basch's sword evadeWeapon=28). The body roll is the dodge:
-- Immobilize cancels evadeParry. Before Proficiency 0.5.1 this map had parry and dodge the wrong way round.
local OUTCOME = {[1] = 'parry', [2] = 'parry', [3] = 'block', [4] = 'block', [5] = 'dodge'}
function R.hook240(formula, caster, target, classes, helpers)
  if caster.type == 1 then R.seeFoe(caster.address) end
  -- 0.1.45: every foe attack reaches 240, hit or evaded, so it ends the foe's Staggered record here. 0.1.46: once per foe
  -- action (R.foeAction): the state is read at the action's first call, before the record ends, and every target and every
  -- hit of that action hears the same reading (Hex Round I). The early exits below still end the record.
  local staggered = false
  if caster.type == 1 then staggered = R.foeAction(formula, caster, classes, helpers) end
  if not R.active() or caster.type ~= 1 or target.type ~= 0 then return end
  local s = R.base(target, formula.target.statusEffects, classes, helpers)
  if not s then return end
  s.attacker = caster.address
  s.attackerStatus, s.attackerMp = foeStatusSet(caster.temporaryStatusEffects, caster, clock()), caster.currentMp
  s.attackerMaxMp, s.attackerTough = caster.maxMp, R.isTough(caster.address, classes, helpers)
  s.attackerStaggered = staggered == true
  -- The same action identity and fallback as hook 165, so a counter and a later hit of one foe action count as one blow.
  s.now = clock()
  local okAction, action = pcall(function() return formula.action end)
  if okAction then s.action = action end
  local record = R.acting[caster.address]
  s.actionId = record and record.id or nil
  s.foes = R.foes(classes, caster.address)
  -- A counter pays only when the blow was answered with a swing (0.1.34, audit B2/D1b): Forge 41-43 write outcome 2 (weapon
  -- parry + counter) or 4 (shield block + counter). A counter roll without a parry or block does nothing visible.
  local swing = formula.outcomeType == 2 or formula.outcomeType == 4
  if s.job == P.JOB.mystic then
    if not swing then return end
    s.outcome = 'counter'
  elseif s.job >= Hybrid.FIRST_ID and swing then
    -- The parry or block the counter rode on (outcome 2 = parry, 4 = block): hybrid guard stages hear it too.
    s.outcome, s.guardOutcome = 'counter', OUTCOME[formula.outcomeType]
  else
    s.outcome = OUTCOME[formula.outcomeType]
    if not s.outcome then return end
  end
  local a = P.decide('guard', s, effective())
  if a then R.apply(a, nil, target, classes, helpers); R.report('guard', s, a) end
end

-- 110: healing amount. A party White Mage healing an ally past full HP gives 'heal'.
function R.hook110(formula, caster, target, classes, helpers, _, item)
  if caster.type == 1 then R.foeActs(caster, classes, helpers) end   -- 0.1.45: a foe's heal is its turn
  if not R.active() or caster.type ~= 0 or target.type ~= 0 or formula.target.addedHp == -1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or not R.listens(s.job, 'heal') then return end
  s.target = target.address
  s.overflow = math.floor(formula.target.addedHp) - (target.maxHp - target.currentHp)
  s.item = item
  s.healAmount = math.floor(formula.target.addedHp)
  s.targetStatus = statusSet(target.temporaryStatusEffects)
  s.targetHpPct = target.maxHp > 0 and 100 * target.currentHp // target.maxHp or 100
  s.foes = R.foes(classes)
  if not item then s.castId, s.action = R.castId(caster, formula.action), formula.action end
  local a = P.decide('heal', s, effective())
  if a and not item then a.mpBudget = R.castBudget(caster, formula.action, clock()) end
  if a then R.apply(a, formula, caster, classes, helpers, target); R.report('heal', s, a) end
end

-- 127: the Potion family, same reaction from White Mage I-2.
function R.hook127(formula, caster, target, classes, helpers)
  R.hook110(formula, caster, target, classes, helpers, nil, true)
end

-- 170: magick damage (after the damage is known). A party Black Mage gives 'magick'.
function R.hook170(formula, caster, target, classes, helpers)
  if target.type == 1 then R.seeFoe(target.address) end
  if caster.type == 1 then R.seeFoe(caster.address); R.foeActed(caster.address) end
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  R.channel(formula, caster, target)
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or not R.listens(s.job, 'magick') then return end
  s.element, s.mpCost = R.element(formula.action)
  s.castId, s.action = R.castId(caster, formula.action), formula.action
  s.foe, s.target, s.damage = true, target.address, formula.target.removedHp
  s.targetId, s.targetMp, s.targetMaxMp = formula.target.identifier, target.currentMp, target.maxMp
  if s.job >= Hybrid.FIRST_ID then
    s.tough = R.isTough(target.address, classes, helpers)
    -- the foe's charge when the spell lands, so "about to act" can be read on spells too
    local work = formula.target.battleUnitWork
    if work and work.address ~= 0 then
      s.charge = {current = work.currentChargeTime, required = work.requiredChargeTime, last = work.lastChargeTime}
      s.staggered = R.isStaggered(target.address, work, target)
    end
  end
  s.kill = s.damage ~= -1 and s.damage >= target.currentHp
  s.targetHpPct = target.maxHp > 0 and 100 * target.currentHp // target.maxHp or 100
  s.now = clock()
  s.targetStatus = foeStatusSet(formula.target.statusEffects, target, s.now)
  -- Resonance I (decision 10): another member's hit or spell of this element on the foe in the last 5 s
  s.partyElement = Accessory ~= nil and Accessory.partyElement(target.address, caster.address, s.element, s.now) == true
  s.foes = R.foes(classes, target.address)
  local a = P.decide('magick', s, effective())
  if a then a.mpBudget = R.castBudget(caster, formula.action, s.now) end
  if a then R.apply(a, formula, caster, classes, helpers, target); R.report('magick', s, a) end
end

-- 182: on-hit statuses. `before` = the target's added-status flags before the original ran.
local DISEASE = 16
local MEASURE = 16   -- section 13 category of measures
local function categoryOf(unit)
  if type(unit.weapon) ~= 'number' or unit.weapon < 0x1000 or unit.weapon - 0x1000 >= bpack.section13.count then return nil end
  return bpack.section13[unit.weapon - 0x1000].category
end
-- The Chemist's measure lands a buff on an ally: it can spread to a second ally (0.1.34, audit D12). Forge formula 27
-- (measures) runs 182 for the ally target like any other hit.
local ATTACK_ACTION = 150   -- the Attack command: only the measure's own hit counts, not an item or spell it carries
function R.measureBuff(formula, caster, target, classes, helpers, before)
  if formula.action ~= ATTACK_ACTION then return end
  if not R.listens(R.lineOf(caster.job1, caster.job2), 'buff') or categoryOf(caster) ~= MEASURE then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s then return end
  s.landed = {}
  for _, name in ipairs(P.BUFFS) do
    local id = P.STATUS[name]
    if formula.target.addedStatusEffects[id] == 1 and not before[id] then s.landed[#s.landed + 1] = id end
  end
  if #s.landed == 0 then return end
  s.target = target.address
  local a = P.decide('buff', s, effective())
  if a then R.apply(a, nil, caster, classes, helpers); R.report('buff', s, a) end
end

function R.hook182(formula, caster, target, classes, helpers, before)
  if not R.active() or caster.type ~= 0 then return end
  if target.type == 0 then return R.measureBuff(formula, caster, target, classes, helpers, before) end
  if target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or not R.listens(s.job, 'status') then return end
  s.landed, s.landedFull = {}, {}
  for _, name in ipairs(P.AILMENTS) do
    local id = P.STATUS[name] or (name == 'disease' and DISEASE or nil)
    if id and formula.target.addedStatusEffects[id] == 1 and not before[id] then s.landed[#s.landed + 1] = id end
  end
  -- Hybrid status stages hear the wider set (Stop, Sleep, Confuse too), the same as on the spell route.
  for _, name in ipairs(P.LANDED) do
    local id = P.STATUS[name]
    if id and formula.target.addedStatusEffects[id] == 1 and not before[id] then s.landedFull[#s.landedFull + 1] = id end
  end
  if #s.landed == 0 and #s.landedFull == 0 then return end
  s.targetMaxMp = target.maxMp
  if s.job >= Hybrid.FIRST_ID then s.tough = R.isTough(target.address, classes, helpers) end
  s.foe, s.target, s.damage = true, target.address, formula.target.removedHp
  s.targetStatus, s.targetMp = foeStatusSet(formula.target.statusEffects, target, clock()), target.currentMp
  s.staggered = R.foeStaggered(target.address, classes, helpers)
  s.foes = R.foes(classes, target.address)
  -- Damage spells run 182 after 170 for their status riders (Bio, Dark...): one cast, one budget.
  s.castId, s.action, s.now = R.castId(caster, formula.action), formula.action, clock()
  local a = P.decide('status', s, effective())
  if a then a.mpBudget = R.castBudget(caster, formula.action, s.now) end
  if a then R.apply(a, formula, caster, classes, helpers, target); R.report('status', s, a) end
end

-- 181: status spells (formulas 2, 3, 44 end here; Legacy's Magicks adapter wraps it for Decoy first). A party member's
-- status spell that newly lands statuses gives 'cast' (on a foe or an ally). `before` = added flags before it ran.
function R.hook181(formula, caster, target, classes, helpers, before)
  if caster.type == 1 then R.foeActs(caster, classes, helpers) end   -- 0.1.45: a foe's status or buff spell is its turn
  if not R.active() or caster.type ~= 0 or (target.type ~= 0 and target.type ~= 1) then return end
  if not R.listens(R.lineOf(caster.job1, caster.job2), 'cast') then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s then return end
  s.landed = {}
  for id = 0, 31 do
    if formula.target.addedStatusEffects[id] == 1 and not before[id] then s.landed[#s.landed + 1] = id end
  end
  if #s.landed == 0 then return end
  if target.type == 1 then R.seeFoe(target.address) end
  s.allyTarget, s.target, s.targetId, s.action, s.now = target.type == 0, target.address, formula.target.identifier,
    formula.action, clock()
  s.castId = R.castId(caster, formula.action)
  s.foe = target.type == 1
  s.targetStatus, s.targetMp = (target.type == 1 and foeStatusSet or statusSet)(formula.target.statusEffects, target, s.now), target.currentMp
  if target.type == 1 then s.staggered = R.foeStaggered(target.address, classes, helpers) end
  s.targetMaxMp = target.maxMp
  if s.job >= Hybrid.FIRST_ID and target.type == 1 then s.tough = R.isTough(target.address, classes, helpers) end
  s.targetHpPct = target.maxHp > 0 and 100 * target.currentHp // target.maxHp or 100
  s.foes = R.foes(classes, target.address)
  local a = P.decide('cast', s, effective())
  if a then a.mpBudget = R.castBudget(caster, formula.action, s.now) end
  if a then R.apply(a, formula, caster, classes, helpers, target); R.report('cast', s, a) end
end

-- 22: evasion setup of a physical attack. A party Samurai's attack during Haste gives 'aim'.
function R.hook22(formula, caster, target, classes, helpers)
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or not R.listens(s.job, 'aim') then return end
  local a = P.decide('aim', s, effective())
  if a and a.evade then
    if a.evade.weapon then formula.evadeWeapon = 0 end
    if a.evade.shield then formula.evadeShield = 0 end
    if a.evade.parry then formula.evadeParry = 0 end
  end
end

-- ---------- accessories (accessory_effects.lua) ----------
-- A party member's accessory snapshot: no job, no Clan rank; nil when it wears nothing with an effect.
function R.accessoryBase(unit, flags, classes, helpers)
  if not Accessory or unit.type ~= 0 or not R.inCampaign() then return nil end
  local worn = R.worn(unit, classes)
  local ids = Accessory.worn(worn[1], worn[2])
  if #ids == 0 then return nil end
  local actor = R.partyActor(unit)
  if not actor then return nil end
  local status = statusSet(flags)
  return {ids = ids, actorId = actor, self = unit.address, party = R.party(classes, helpers), casterStatus = status,
          casterHpPct = unit.maxHp > 0 and 100 * unit.currentHp // unit.maxHp or 100,
          casterHp = unit.currentHp, casterMp = unit.currentMp, casterMaxMp = unit.maxMp, selfMaxHp = unit.maxHp,
          targetStatus = {}, now = clock(),
          roll = function(percent) return helpers.getRandomNumber(100) < percent end,
          canLand = function(address, id)
            if not address then return true end
            local keep = classes.getBattleUnitKeep(address)
            return R.mayAdd(keep, id, keep.temporaryStatusEffects, helpers)
          end}
end

local function accessoryReport(ev, s, a)
  local keys = {}
  for k in pairs(a) do if k ~= 'fired' then keys[#keys + 1] = k end end
  table.sort(keys)
  local fired = {}
  for _, id in ipairs(a.fired or {}) do fired[#fired + 1] = tostring(id) end
  log(string.format('accessory %s actor=%s item=%s %s', ev, tostring(s.actorId), table.concat(fired, '/'),
    table.concat(keys, ',')))
end
local function decideAccessory(ev, s, formula, member, classes, helpers, target)
  local a = Accessory.decide(ev, s)
  if a then R.apply(a, formula, member, classes, helpers, target); accessoryReport(ev, s, a) end
end

-- The element of a weapon hit, as Forge 99 picks it: the ammunition's row for bows, crossbows, guns and hand-bombs.
function R.weaponElement(unit)
  local ok, mask = pcall(function()
    local function row(id)
      if type(id) ~= 'number' or id < 0x1000 or id - 0x1000 >= bpack.section13.count then return nil end
      return bpack.section13[id - 0x1000]
    end
    local r = row(unit.weapon)
    if r and r.offhandCategory >= 23 and r.offhandCategory <= 26 then r = row(unit.offhand) end
    if not r then return 0 end
    local m = 0
    for i, name in ipairs(ELEMENTS) do if r.elements[name] == 1 then m = m | (1 << (i - 1)) end end
    return m
  end)
  return ok and type(mask) == 'number' and mask or 0
end

-- The first foe contact of a new fight (accessory_effects.lua keeps the fight clock, a 30 s gap starts a new one): every
-- living party member's accessory stages hear 'fightStart' once, the same loop the 'ally' event uses. Hermes Sandals'
-- opening Haste is the only stage that listens. Sent from 165 and 170 (a party hit or spell, a foe's damaging blow) and
-- from 222 (any foe action on the party).
local function fightStart(classes, helpers)
  if not Accessory.fightStarted() then return end
  for _, p in ipairs(R.party(classes, helpers)) do
    local k = classes.getBattleUnitKeep(p.address)
    local s = R.accessoryBase(k, k.temporaryStatusEffects, classes, helpers)
    if s then decideAccessory('fightStart', s, nil, k, classes, helpers) end
  end
end

-- 165: a party member's hit on a foe ('hit'); a foe's physical hit that drops a party member to critical HP ('struck'
-- for that member, 'ally' for the others).
function R.accessory165(formula, caster, target, classes, helpers)
  if not Accessory or not R.inCampaign() then return end
  local now = clock()
  if caster.type == 0 and target.type == 1 then
    if formula.target.removedHp == -1 then return end
    Accessory.touch(now)
    fightStart(classes, helpers)
    local status = statusSet(formula.target.statusEffects)
    Accessory.noteStatus(target.address, status)
    -- Spotter reads a combo follow-up or a critical hit; the memory lives in the accessory module (every party hit counts)
    local follow = Accessory.noteHit(caster.address, target.address, now, R.COMBO_WINDOW)
    local s = R.accessoryBase(caster, formula.caster.statusEffects, classes, helpers)
    if s then
      s.foe, s.target, s.targetId, s.targetStatus = true, target.address, formula.target.identifier, status
      s.damage = formula.target.removedHp
      s.hitCombo, s.hitCritical = follow, formula.criticalState == 1
      s.allyBroken, s.allyStaggered = R.alliedHold(caster, target, now, formula.target.battleUnitWork)
      decideAccessory('hit', s, formula, caster, classes, helpers, target)
    end
    if formula.target.removedHp > 0 then Accessory.noteElement(target.address, caster.address, R.weaponElement(caster), now) end
  elseif caster.type == 1 and target.type == 0 and target.maxHp > 0 then
    local removed = formula.target.removedHp
    if type(removed) ~= 'number' or removed <= 0 then return end
    Accessory.touch(now)
    fightStart(classes, helpers)
    local quarter = target.maxHp // 4
    -- dropped to critical HP and still standing: a killing blow is not a rescue moment and spends no Vanish
    if not (target.currentHp > quarter and target.currentHp - removed <= quarter and target.currentHp - removed > 0) then return end
    local attackerStatus = statusSet(caster.temporaryStatusEffects)
    local mine = R.accessoryBase(target, formula.target.statusEffects, classes, helpers)
    if mine then
      mine.attacker, mine.attackerStatus, mine.dropCritical, mine.damage = caster.address, attackerStatus, true, removed
      decideAccessory('struck', mine, nil, target, classes, helpers)
    end
    for _, p in ipairs(R.party(classes, helpers)) do
      if p.address ~= target.address then
        local k = classes.getBattleUnitKeep(p.address)
        local s = R.accessoryBase(k, k.temporaryStatusEffects, classes, helpers)
        if s then
          s.ally, s.attacker, s.attackerStatus = target.address, caster.address, attackerStatus
          decideAccessory('ally', s, nil, k, classes, helpers)
        end
      end
    end
  end
end

-- 222: the first foe action on the party opens the fight, whatever it is (a damage or status spell, a hit that misses or is
-- blocked, an item effect). 165 only hears a physical hit that deals damage, 170 and 181 only a party caster, so a fight
-- opened by a foe's spell would otherwise leave a Hermes Sandals wearer un-Hasted until someone landed a hit. Forge 222
-- runs for every target of every action before its effects; Accessory.fightStarted() makes the dispatch once per fight.
function R.accessory222(formula, caster, target, classes, helpers)
  if not Accessory or caster.type ~= 1 or target.type ~= 0 or target.address == 0 or not R.inCampaign() then return end
  Accessory.touch(clock())
  fightStart(classes, helpers)
end

-- 170: a party member's damage spell on a foe ('magick'): Burst and Spellcatcher's payoff; its element is remembered.
function R.accessory170(formula, caster, target, classes, helpers)
  if not Accessory or caster.type ~= 0 or target.type ~= 1 or not R.inCampaign() then return end
  local damage = formula.target.removedHp
  if damage == -1 then return end
  local now = clock()
  Accessory.touch(now)
  fightStart(classes, helpers)
  local okE, mask = pcall(R.element, formula.action)
  mask = okE and type(mask) == 'number' and mask or 0
  local s = R.accessoryBase(caster, formula.caster.statusEffects, classes, helpers)
  if s then
    s.foe, s.target, s.targetId, s.element, s.now = true, target.address, formula.target.identifier, mask, now
    s.castId, s.action, s.damage = R.castId(caster, formula.action), formula.action, damage
    s.targetStatus = statusSet(formula.target.statusEffects)
    decideAccessory('magick', s, formula, caster, classes, helpers, target)
  end
  if type(damage) == 'number' and damage > 0 then Accessory.noteElement(target.address, caster.address, mask, now) end
end

-- 110 / 127: healing that overflows a Mistflow Ring wearer's max HP spills to the most hurt other ally.
function R.accessory110(formula, caster, target, classes, helpers)
  if not Accessory or target.type ~= 0 or not R.inCampaign() then return end
  local healed = formula.target.addedHp
  if type(healed) ~= 'number' or healed <= 0 then return end
  local overflow = math.floor(healed) - (target.maxHp - target.currentHp)
  if overflow <= 0 then return end
  local s = R.accessoryBase(target, target.temporaryStatusEffects, classes, helpers)
  if not s or not Accessory.wears(s, Accessory.ID.mistflow) then return end
  local spill = Accessory.spill(s, overflow)
  if not spill then return end
  local a = {others = {{op = 'hp', unit = spill.unit, amount = spill.amount}}, fired = {Accessory.ID.mistflow}}
  R.apply(a, nil, target, classes, helpers)
  accessoryReport('spill', s, a)
end
R.accessory127 = R.accessory110

-- The ailments a party member's status spell or on-hit status newly landed on a foe (`before` = flags before Forge).
local function newlyLanded(formula, before)
  local out = {}
  for _, name in ipairs(P.LANDED) do
    local id = P.STATUS[name] or (name == 'disease' and P.DISEASE or nil)
    if id and formula.target.addedStatusEffects[id] == 1 and not before[id] then out[#out + 1] = id end
  end
  return out
end

-- 181: status spells (and reversed status items). The lander of each ailment is remembered. An Indigo Pendant wearer's
-- Vitality-checked spells (formulas 3 and 72, the ones Saboteur lifts) land ailments that last half as long; a status the
-- foe already holds for longer, or a longer one a Proficiency stage queued, keeps its time. Every other formula that ends
-- here (reversed items among them) keeps Forge's duration.
R.INDIGO_FORMULAS = {[3] = true, [72] = true}
function R.accessory181(formula, caster, target, classes, helpers, before)
  if not Accessory or caster.type ~= 0 or target.type ~= 1 or not R.inCampaign() then return end
  local now = clock()
  local ids = newlyLanded(formula, before or {})
  for _, id in ipairs(ids) do Accessory.noteAilment(target.address, id, caster.address, now) end
  if #ids == 0 or not R.INDIGO_FORMULAS[formula.identifier] or not R.wears(caster, Accessory.ID.indigo, classes) then return end
  for _, id in ipairs(ids) do
    local half = math.max(1, math.floor(helpers.getStatusEffectDuration(caster.address, id) * Accessory.INDIGO_SCALE))
    local running = target.temporaryStatusEffects[id] == 1 and target.statusEffectDurations[id] or 0
    local queued = formula.target.statusEffectDurations[id]   -- -1 = Forge's full duration; else a stage's grant
    queued = type(queued) == 'number' and queued ~= -1 and queued or 0
    formula.target.statusEffectDurations[id] = math.max(half, type(running) == 'number' and running or 0, queued)
  end
  log(string.format('accessory indigo actor=%s statuses=%d half duration', tostring(R.partyActor(caster)), #ids))
end

-- 181, before Proficiency hears it: a reversed cure (formula 34, Nihopalaoa's Medicine Reverse) lands its statuses with
-- no Vitality check. On a boss, mark, rare game or King it now rolls like a Vitality-checked spell: the item's accuracy
-- + the caster's Magick - the target's Vitality (100 with Saboteur), hit when the 0-99 roll is below it (Forge 40). A
-- miss takes back every status the cure would have added (any bit, Petrify, Doom and Berserk included) and shows as
-- a miss, as Forge 181's own. Ordinary foes keep the native sure hit.
R.REVERSED_CURE, R.KING_FIRST, R.KING_LAST, R.HP_CRITICAL = 34, 293, 372, 29
function R.reversedCure181(formula, caster, target, classes, helpers, before)
  if formula.identifier ~= R.REVERSED_CURE or caster.type ~= 0 or target.type ~= 1 or not R.inCampaign() then return end
  local ids, seen = {}, before or {}
  for id = 0, 31 do
    if id ~= R.HP_CRITICAL and formula.target.addedStatusEffects[id] == 1 and not seen[id] then ids[#ids + 1] = id end
  end
  if #ids == 0 then return end
  local tough = R.isTough(target.address, classes, helpers)
  if not tough then
    local ok, entry = pcall(function()
      local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(target.address))
      return w.address ~= 0 and w.ardClass.bestiaryIdentifier or nil
    end)
    tough = ok and type(entry) == 'number' and entry >= R.KING_FIRST and entry <= R.KING_LAST
  end
  if not tough then return end
  local accuracy = 100
  if formula.caster.augments.saboteur ~= 1 then
    accuracy = bpack.section14[formula.action].accuracyRate + caster.magickPower - target.vitality
  end
  if helpers.getRandomNumber(100) < accuracy then return end
  for _, id in ipairs(ids) do formula.target.addedStatusEffects[id] = 0 end
  formula.skipState, formula.outcomeType = 1, 6
  log(string.format('reversed cure resisted: actor=%s accuracy=%d statuses=%d', tostring(R.partyActor(caster)), accuracy, #ids))
end

-- 182: on-hit statuses. The lander of each ailment is remembered.
function R.accessory182(formula, caster, target, classes, helpers, before)
  if not Accessory or caster.type ~= 0 or target.type ~= 1 or not R.inCampaign() then return end
  local now = clock()
  for _, id in ipairs(newlyLanded(formula, before or {})) do Accessory.noteAilment(target.address, id, caster.address, now) end
end

-- Forge 34 (Cameo Belt, Spotter): true when another party member marked this foe and this attack rolls a dodge; the
-- mark is spent. Formula 26 crossbows, guns and measures (27) and anything else that never reads the dodge leave the
-- mark alone (the few crossbows on formula 25 do roll a dodge, so they spend it).
R.DODGE_FORMULAS = {[20] = true, [21] = true, [22] = true, [23] = true, [24] = true, [25] = true, [28] = true,
                    [29] = true, [64] = true, [65] = true, [66] = true, [103] = true, [104] = true}
function R.spotter34(formula, caster, target, classes, helpers)
  if not Accessory or caster.type ~= 0 or target.type ~= 1 or not R.inCampaign() then return false end
  if type(target.address) ~= 'number' or target.address == 0 or not R.DODGE_FORMULAS[formula.identifier] then return false end
  return Accessory.spotted(target.address, caster.address, clock())
end

-- Forge 231 (Rubicante Ring, Spellcatcher): a FOE's spell bounced off a party wearer (Forge 231 has no caster check, so
-- an ally's heal bouncing off the wearer also sets outcome 8; it arms nothing).
function R.reflect231(formula, caster, target, classes, helpers)
  if not Accessory or formula.outcomeType ~= 8 or caster.type ~= 1 or target.type ~= 0 or not R.inCampaign() then return end
  local s = R.accessoryBase(target, target.temporaryStatusEffects, classes, helpers)
  if not s or not Accessory.wears(s, Accessory.ID.rubicante) then return end
  s.attacker = caster.address
  local a = Accessory.reflected(s)
  if a then accessoryReport('reflected', s, a) end
end

-- Forge 94 (Barbariccia Ring, Stormcaller): the wearer's magick keeps the weather and terrain bonuses.
local WEATHER, WIND = 0x02099DA8, 0x02099DAC
function R.weather94(formula, caster, target, classes, helpers)
  if not Accessory or caster.type ~= 0 or not R.inCampaign() then return end
  if not isMagick(formula.action) or formula.caster.augments.weatherproof ~= 1 or formula.target.augments.weatherproof == 1
      or not R.wears(caster, Accessory.ID.barbariccia, classes) then return end
  local action = bpack.section14[formula.action]
  local terrain = helpers.getTerrainType(formula.target.battleUnitWork.address)
  local factor = Accessory.weatherBonus(function(name) return action.elements[name] == 1 end, memory.s32[WEATHER],
    memory.s32[WIND], terrain)
  if factor > 1 then formula.power = formula.power * factor end
end

-- Wrap one Forge function: the original always runs first; the hook can never stop Forge.
function R.wrap(n, original)
  local hook = R['hook' .. n]
  return function(formula, caster, target, functions, classes, helpers)
    local before = nil
    if n == 182 or n == 181 then
      before = {}
      for i = 0, 31 do if formula.target.addedStatusEffects[i] == 1 then before[i] = true end end
    end
    original(formula, caster, target, functions, classes, helpers)
    -- a reversed cure's Vitality roll on bosses comes before Proficiency hears what landed
    if n == 181 then
      R.run('reversedCure181', function() R.reversedCure181(formula, caster, target, classes, helpers, before) end)
    end
    R.run('hook' .. n, function() hook(formula, caster, target, classes, helpers, before) end)
    -- the wearer's accessories, after the Proficiency hook of the same call
    local worn = R['accessory' .. n]
    if worn then R.run('accessory' .. n, function() worn(formula, caster, target, classes, helpers, before) end) end
  end
end

return R
