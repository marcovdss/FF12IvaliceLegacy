-- Proficiency runtime shared by the Forge adapters (165 physical hits, 170 magick, 110 and 127 healing, 182 statuses,
-- 240 guard reactions, 22 evasion). Loaded once and kept in _G so all adapters see one campaign state.
-- Montblanc hands out the ranks: leaving the Clan hall (location 302) with an unrewarded rank makes him speak in
-- native dialogue windows (Lua loader dialog plugin), and the rewarded rank is stored beside the Legacy save.
local R = {}
local Proficiency = assert(loadfile('scripts/IvaliceLegacy/proficiency.lua', 't', _ENV))()
local ClanRank = assert(loadfile('scripts/IvaliceLegacy/clan_rank.lua', 't', _ENV))()
local P = Proficiency
R.Proficiency = P

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
local foes = {}
local writs, writsKnown = {false, false, false}, false
local LOG_LIMIT = 1000
local function log(line)
  if logged < LOG_LIMIT then logged = logged + 1; print('Legacy Proficiency: ' .. line) end
end
R.log = log

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
function R.claimed() return claimed end
function R.effective() return effective() end

-- The Clan Primer tip "Clan Proficiency" (Traveler's Tips) waits on one flag bit of the menu-info block
-- ([0x20927C0] + 0x334C, 256 bits, read by the tip check 0x575E80 through 0x315620). No native tip check uses these
-- bits; the bit follows the Apprentice's Writ through the native setter 0x315A20(index, on).
local MENU_INFO, TIP_FLAGS, SET_TIP_FLAG = 0x020927C0, 0x334C, 0x315A20
R.TIP_FLAG = 254  -- 255 (0xFF) means "no flag test" in a tip check; 0.3.1 used it by mistake
local STRAY_FLAG = 255
local flagsLogged = false
local function syncTip()
  if campaign == nil then return end
  local state = memory.u64[MENU_INFO]
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

local function clanRank()
  local ok, clan = pcall(ClanRank.read, {
    u8 = function(a) return memory.u8[a] end, u32 = function(a) return memory.u32[a] end,
    marks = function() return memory.execute(ClanRank.MARKS_FN, arg.s32) end})
  if ok and type(clan) == 'table' then return clan.rank end
  return nil
end

local function speak(boxes)
  if type(dialog) ~= 'table' or type(dialog.amese) ~= 'table' then
    for _, box in ipairs(boxes) do log('speech (no dialog service): ' .. box:gsub('\n', ' ')) end
    return
  end
  for _, box in ipairs(boxes) do
    local h, reason = dialog.amese.open{speaker = 'Montblanc', body = box, queue = true, lockPlayer = true}
    if not h then log('dialog refused: ' .. tostring(reason)) end
  end
end

-- Leaving the hall: hand over every Writ the rank calls for FIRST, then reward the rank. A Writ that cannot be
-- granted stops the claim, so a Proficiency never runs without its Writ.
local function claim()
  if not campaign or not legacyMode() then return end
  local rank = clanRank()
  if type(rank) ~= 'number' then log('clan rank unreadable; nothing claimed') return end
  local from = claimed
  local to = math.max(rank, claimed)
  refreshWrits()
  local late = {}
  for _, k in ipairs(P.writsNeeded(to)) do
    if not writs[k] then
      if not grant(P.WRIT_IDS[k]) then
        log(string.format('campaign=%s Writ %d (0x%04X) could not be granted; nothing claimed', campaign, k, P.WRIT_IDS[k]))
        refreshWrits()
        return
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
end

-- ---------- gear descriptions ----------
-- The Equipment menu shows the selected character's Proficiency under each weapon, shield and armour description
-- (not accessories or ammunition). It is added when the description is shown, so it always matches the character
-- on screen: the help-window updater copies the hovered item's description and stores the text pointer at
-- 0x29266C (mov [rdi+0x18], rbx; The Insurgent's Descriptive Inventory swaps rbx just before, at 0x292667). The
-- patch there appends a ready-made suffix for the selected party member (menu slot [0x209AC30]+0xDE0 -> 0x282EA0
-- -> 0x31C4C0) to a copy of that text. Lua keeps one native-encoded suffix per party member, rebuilt only when a
-- member's job or the effective rank changes.
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
  bt dword ptr [r12],eax
  jnc legacy_prof_done
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
local MEMBER_KEEP, KEEP_JOB1 = 0x320A40, 0x1C3
local gear = {installed = false, failed = false, keys = {}, slots = {}, flip = {}}

local function label(name, value)
  return '\n{scale:82}{rgb:90,90,90}' .. name .. ':{lowspace}{rgb:255,255,255}' .. value
end
-- ONE added line (0.3.12): the Equipment help box (180,816 1400x216 at 1920x1080) holds four lines, weapons
-- already use three, and a fifth line throws the whole text off the left edge (seen in game, 0.3.11). The line
-- names the highest stage begun and its effect, e.g. "Pursuit I 1/4: +15% damage to foes with an ailment"
-- (longest 82 characters, inside the box); without the Apprentice's Writ it reads "Proficiency: Pursuit (no Writ yet...)".
local STAGES = {'I', 'II', 'III'}
function R.gearSuffix(job, rank)
  local lines = P.gearLines(job, rank)
  local g = P.GEAR[job]
  if not lines or not g then return nil end
  local L = P.levels(rank)
  for k = 3, 1, -1 do
    if L[k] >= 1 then
      local head = g.line .. ' ' .. STAGES[k] .. ' ' .. L[k] .. '/4'
      if lines[k + 1] then return label(head, lines[k + 1]) end
      return label('Proficiency', head)
    end
  end
  return label('Proficiency', lines[1])
end

-- Equipment help box position (0.3.13). Opening Equipment, the game builds the help text of every owned item in four
-- categories (0x3FCEE0) and hands the widest to the help window (0x3FF6CD mov ecx,eax -> call 0x2932F0, window
-- +0xCC); the window then slides the WHOLE box left until that widest text fits. One item with a long description
-- (1532 px against the 1400 px box, seen in game 2026-11-03) pushed every Equipment description off the left edge.
-- The width handed over is now 0 (xor ecx,ecx), so the box stays where the layout puts it; a long line runs further
-- right, still on screen. Equipment only: the other callers of 0x2932F0 are untouched. Confirmed live by Marco.
local WIDTH_SITE, WIDTH_ORIGINAL = 0x003FF6CD, {0x8B, 0xC8}
function R.installWidthFix()
  local raw = memory.readArray(WIDTH_SITE, 2)
  local b1 = type(raw) == 'string' and raw:byte(1) or (type(raw) == 'table' and raw[1]) or nil
  local b2 = type(raw) == 'string' and raw:byte(2) or (type(raw) == 'table' and raw[2]) or nil
  if b1 ~= WIDTH_ORIGINAL[1] or b2 ~= WIDTH_ORIGINAL[2] then
    log('Equipment box width fix not applied: unexpected code at 0x3FF6CD')
    return false
  end
  local ok, err = pcall(memory.assemble, 'xor ecx,ecx', WIDTH_SITE)
  log(ok and 'Equipment box width fix applied' or ('Equipment box width fix rejected (' .. tostring(err) .. ')'))
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
  local bits = memory.alloc(0x80)
  local table_ = memory.alloc(0x40 * 8)
  local buffer = memory.alloc(0x800)
  memory.writeArray(bits, {string.rep('\0', 0x80):byte(1, -1)})
  memory.writeArray(table_, {string.rep('\0', 0x200):byte(1, -1)})
  -- Weapons, shields, helms and armour: equipment categories 0-20 (accessories 21+, ammunition 23+ excluded).
  local count = 0
  if type(bpack) == 'table' then
    for i = 0, 0x3FF do
      local ok, category = pcall(function() local r = bpack.section13[i]; return r and r.category end)
      if not ok or type(category) ~= 'number' then break end
      if category <= 20 then
        memory.u8[bits + (i >> 3)] = memory.u8[bits + (i >> 3)] | (1 << (i & 7))
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

-- One suffix per party member; two buffers each so the hook never reads a half-written text.
local function setSuffix(member, text)
  local at = gear.table + member * 8
  if not text then memory.u64[at] = 0; return end
  local bytes = message.convert(text)
  if type(bytes) ~= 'string' or #bytes + 1 > SUFFIX_SIZE then memory.u64[at] = 0; return end
  gear.slots[member] = gear.slots[member] or {memory.alloc(SUFFIX_SIZE), memory.alloc(SUFFIX_SIZE)}
  gear.flip[member] = 3 - (gear.flip[member] or 2)
  local buf = gear.slots[member][gear.flip[member]]
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
      if type(job) ~= 'number' or not P.GEAR[job] then job = nil end
      local key = job and (job .. ':' .. rank) or 'none'
      if gear.keys[member] ~= key then
        gear.keys[member] = key
        setSuffix(member, job and R.gearSuffix(job, rank) or nil)
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
local function companionsByte()
  local ok, sym = pcall(memory.getSymbol, 'pit_ot')
  if ok and type(sym) == 'number' and sym > 0 then return sym end
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
  pendingCharge = {}
  writs, writsKnown, flagsLogged = {false, false, false}, false, false
  gear.keys = {}
  P.reset()
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
  ok = event.registerEventSync('onNewGameStart', function() reset() return true end) == true and ok
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
    writsKnown = false  -- the Writs are read again on the next use (a key item can come or go between maps)
    if campaign and not rankLogged then
      rankLogged = true
      log(string.format('campaign=%s clan rank %s (claimed %d), location %d', campaign, tostring(clanRank()), claimed, to))
    end
    if from == CLAN_HALL and to ~= CLAN_HALL then claim() end
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

local function clock() return event.getClock and event.getClock() or 0 end

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
        out[#out + 1] = {address = addr, job = k.job2 == P.NO_JOB and k.job1 or -1,
                         hpPct = 100 * k.currentHp // k.maxHp, mp = k.currentMp, maxMp = k.maxMp,
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
function R.base(unit, flags, classes, helpers)
  if unit.type ~= 0 or unit.job2 ~= P.NO_JOB then return nil end  -- hybrids get their own lines later
  local actor = R.partyActor(unit)
  if not actor then return nil end
  return {job = unit.job1, actorId = actor, self = unit.address, party = R.party(classes, helpers),
          casterStatus = statusSet(flags), casterHpPct = unit.maxHp > 0 and 100 * unit.currentHp // unit.maxHp or 100,
          casterMp = unit.currentMp, casterMaxMp = unit.maxMp, selfMaxHp = unit.maxHp,
          roll = function(percent) return helpers.getRandomNumber(100) < percent end}
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
  if keep.type == 1 and record.flags1.isNotApplicableToFoes == 1 then return false end
  if record.flags1.isNegative == 1 and (keep.temporaryAugments.statusImmunity == 1 or keep.permanentAugments.statusImmunity == 1) then
    return false
  end
  return not helpers.getStatusEffectsMatch(record.absentStatusEffects, flags)
end

-- The add helper overwrites the timer, so a short proc must not cut a longer status already running. nil = leave it.
function R.duration(keep, source, id, scale, helpers)
  local d = math.max(1, math.floor(helpers.getStatusEffectDuration(source, id) * scale))
  if keep.temporaryStatusEffects[id] == 1 and keep.statusEffectDurations[id] >= d then return nil end
  return d
end

local function grant(plus, keep, source, id, scale, helpers)
  if not R.mayAdd(keep, id, plus.statusEffects, helpers) then return end
  local d = R.duration(keep, source, id, scale, helpers)
  if not d then return end
  plus.addedStatusEffects[id] = 1
  plus.statusEffectDurations[id] = d
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
  pendingCharge[address] = {fraction = fraction, expires = clock() + CHARGE_WAIT, classes = classes, helpers = helpers,
                            above = w.currentChargeTime >= w.requiredChargeTime, last = w.currentChargeTime}
  if not chargePolling and type(event.executeAfterMs) == 'function' then
    chargePolling = true
    event.executeAfterMs(CHARGE_POLL_MS, chargeTick)
  end
end

local function pushCharge(classes, helpers, unit, fraction)
  local w = classes.getBattleUnitWork(helpers.getBattleUnitWork(unit))
  if w.address == 0 then return end
  local cur, req, last = w.currentChargeTime, w.requiredChargeTime, w.lastChargeTime
  if cur > 0 and cur < req and last == cur then w.currentChargeTime = math.max(1, cur - math.floor(req * fraction)) end
end

-- Apply an action table for party member `member`. Formula fields are used only when `formula` is given and the
-- member is that formula's caster; everything in a.others goes through Forge helpers with the member as source.
function R.apply(a, formula, member, classes, helpers, target)
  if formula then
    if a.damage and formula.target.removedHp ~= -1 then
      formula.target.removedHp = math.floor(formula.target.removedHp * a.damage)
    end
    for id, scale in pairs(a.casterAdd or {}) do grant(formula.caster, member, member.address, id, scale, helpers) end
    for id, scale in pairs(a.targetAdd or {}) do grant(formula.target, target, member.address, id, scale, helpers) end
    -- Renewing restarts the timer: the helper writes it even when the status is already on.
    for _, id in ipairs(a.targetRenew or {}) do
      if target.temporaryStatusEffects[id] == 1 then formula.target.addedStatusEffects[id] = 1 end
    end
    for id, fraction in pairs(a.targetExtend or {}) do
      local full = helpers.getStatusEffectDuration(member.address, id)
      local now = target.statusEffectDurations[id]
      if target.temporaryStatusEffects[id] == 1 and now < full then
        formula.target.addedStatusEffects[id] = 1
        formula.target.statusEffectDurations[id] = math.min(full, now + math.max(1, math.floor(full * fraction)))
      end
    end
    for _, id in ipairs(a.casterRemove or {}) do formula.caster.removedStatusEffects[id] = 1 end
    for _, id in ipairs(a.targetRemove or {}) do formula.target.removedStatusEffects[id] = 1 end
    if a.drainMp then
      formula.target.mpUpdateState, formula.target.addedMp, formula.target.removedMp = 1, 0, a.drainMp
      formula.caster.mpUpdateState, formula.caster.addedMp, formula.caster.removedMp = 1, a.drainMp, 0
    elseif a.targetMpLoss then
      formula.target.mpUpdateState, formula.target.addedMp, formula.target.removedMp = 1, 0, a.targetMpLoss
    end
    if a.healHp and formula.caster.hpUpdateState ~= 1 then
      formula.caster.hpUpdateState, formula.caster.addedHp, formula.caster.removedHp = 1, a.healHp, 0
    end
    if a.chargeTo then formula.target.battleUnitWork.currentChargeTime = a.chargeTo end
  end
  if a.casterCharge then R.armCharge(member.address, a.casterCharge, classes, helpers) end
  for _, o in ipairs(a.others or {}) do
    if o.op == 'add' then
      local keep = classes.getBattleUnitKeep(o.unit)
      if R.mayAdd(keep, o.id, keep.temporaryStatusEffects, helpers) then
        local d = R.duration(keep, member.address, o.id, o.scale or 1, helpers)
        if d then helpers.addStatusEffect(member.address, o.unit, o.id, d, helpers.getStatusEffectTickDuration(o.id)) end
      end
    elseif o.op == 'remove' then helpers.removeStatusEffect(o.unit, o.id)
    elseif o.op == 'mp' then helpers.modifyMp(o.unit, o.amount)
    elseif o.op == 'hp' then helpers.modifyHp(member.address, o.unit, o.amount, 0, 0, 0)
    elseif o.op == 'push' then pushCharge(classes, helpers, o.unit, o.fraction) end
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

-- 165: every physical hit after damage. Party member hitting a foe gives 'hit'; a foe dropping a party member to
-- critical HP gives the Knight's 'ally' reaction.
function R.hook165(formula, caster, target, classes, helpers)
  if target.type == 1 then R.seeFoe(target.address) end
  if caster.type == 1 then R.seeFoe(caster.address) end
  if not R.active() then return end
  if caster.type == 0 and target.type == 1 then
    local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
    if not s then return end
    local damage, work = formula.target.removedHp, formula.target.battleUnitWork
    if damage == -1 then return end  -- the target was immune: nothing landed
    local category = nil
    if type(caster.weapon) == 'number' and caster.weapon >= 0x1000 and caster.weapon - 0x1000 < bpack.section13.count then
      category = bpack.section13[caster.weapon - 0x1000].category
    end
    s.targetId, s.foe, s.target, s.damage = formula.target.identifier, true, target.address, damage
    s.weapon = caster.weapon
    s.kill, s.targetHp = damage >= target.currentHp, target.currentHp
    s.bystander = formula.initialTargetPointer ~= 0 and formula.initialTargetPointer ~= target.address
    s.category, s.targetStatus, s.targetMp = category, statusSet(formula.target.statusEffects), target.currentMp
    if work.address ~= 0 then
      s.charge = {current = work.currentChargeTime, required = work.requiredChargeTime, last = work.lastChargeTime}
    end
    s.foes = R.foes(classes, target.address)
    local a = P.decide('hit', s, effective())
    if a then R.apply(a, formula, caster, classes, helpers, target); R.report('hit', s, a) end
  elseif caster.type == 1 and target.type == 0 and formula.target.removedHp ~= -1 and target.maxHp > 0 then
    local quarter = target.maxHp // 4
    if target.currentHp > quarter and target.currentHp - formula.target.removedHp <= quarter then
      for _, p in ipairs(R.party(classes, helpers)) do
        if p.job == P.JOB.knight and p.address ~= target.address then
          local k = classes.getBattleUnitKeep(p.address)
          local s = R.base(k, k.temporaryStatusEffects, classes, helpers)
          if s then s.ally = target.address end
          local a = s and P.decide('ally', s, effective())
          if a then R.apply(a, nil, k, classes, helpers); R.report('ally', s, a) end
        end
      end
    end
  end
end

-- 240: after the hit/dodge/parry/block/counter decision. A party member attacked by a foe gives 'guard'.
local OUTCOME = {[1] = 'dodge', [2] = 'dodge', [3] = 'block', [4] = 'block', [5] = 'parry'}
function R.hook240(formula, caster, target, classes, helpers)
  if caster.type == 1 then R.seeFoe(caster.address) end
  if not R.active() or caster.type ~= 1 or target.type ~= 0 then return end
  local s = R.base(target, formula.target.statusEffects, classes, helpers)
  if not s then return end
  s.attacker = caster.address
  if s.job == P.JOB.mystic then
    if formula.counterState ~= 1 then return end
    s.outcome = 'counter'
  else
    s.outcome = OUTCOME[formula.outcomeType]
    if not s.outcome then return end
  end
  local a = P.decide('guard', s, effective())
  if a then R.apply(a, nil, target, classes, helpers); R.report('guard', s, a) end
end

-- 110: healing amount. A party White Mage healing an ally past full HP gives 'heal'.
function R.hook110(formula, caster, target, classes, helpers, _, item)
  if not R.active() or caster.type ~= 0 or target.type ~= 0 or formula.target.addedHp == -1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.whiteMage then return end
  s.target = target.address
  s.overflow = math.floor(formula.target.addedHp) - (target.maxHp - target.currentHp)
  s.item = item
  local a = P.decide('heal', s, effective())
  if a then R.apply(a, formula, caster, classes, helpers, target); R.report('heal', s, a) end
end

-- 127: the Potion family, same reaction from White Mage I-2.
function R.hook127(formula, caster, target, classes, helpers)
  R.hook110(formula, caster, target, classes, helpers, nil, true)
end

-- 170: magick damage (after the damage is known). A party Black Mage gives 'magick'.
function R.hook170(formula, caster, target, classes, helpers)
  if target.type == 1 then R.seeFoe(target.address) end
  if caster.type == 1 then R.seeFoe(caster.address) end
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.blackMage then return end
  s.element, s.mpCost = R.element(formula.action)
  s.foe, s.target, s.damage = true, target.address, formula.target.removedHp
  s.now = clock()
  s.targetStatus = statusSet(formula.target.statusEffects)
  s.foes = R.foes(classes, target.address)
  local a = P.decide('magick', s, effective())
  if a then R.apply(a, formula, caster, classes, helpers, target); R.report('magick', s, a) end
end

-- 182: on-hit statuses. `before` = the target's added-status flags before the original ran.
local DISEASE = 16
function R.hook182(formula, caster, target, classes, helpers, before)
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.chemist then return end
  s.landed = {}
  for _, name in ipairs(P.AILMENTS) do
    local id = P.STATUS[name] or (name == 'disease' and DISEASE or nil)
    if id and formula.target.addedStatusEffects[id] == 1 and not before[id] then s.landed[#s.landed + 1] = id end
  end
  if #s.landed == 0 then return end
  s.foe, s.target, s.damage = true, target.address, formula.target.removedHp
  s.foes = R.foes(classes, target.address)
  local a = P.decide('status', s, effective())
  if a then R.apply(a, formula, caster, classes, helpers, target); R.report('status', s, a) end
end

-- 22: evasion setup of a physical attack. A party Samurai's attack during Haste gives 'aim'.
function R.hook22(formula, caster, target, classes, helpers)
  if not R.active() or caster.type ~= 0 or target.type ~= 1 then return end
  local s = R.base(caster, formula.caster.statusEffects, classes, helpers)
  if not s or s.job ~= P.JOB.samurai then return end
  local a = P.decide('aim', s, effective())
  if a and a.evade then
    if a.evade.weapon then formula.evadeWeapon = 0 end
    if a.evade.shield then formula.evadeShield = 0 end
    if a.evade.parry then formula.evadeParry = 0 end
  end
end

-- Wrap one Forge function: the original always runs first; the hook can never stop Forge.
function R.wrap(n, original)
  local hook = R['hook' .. n]
  return function(formula, caster, target, functions, classes, helpers)
    local before = nil
    if n == 182 then
      before = {}
      for i = 0, 31 do if formula.target.addedStatusEffects[i] == 1 then before[i] = true end end
    end
    original(formula, caster, target, functions, classes, helpers)
    R.run('hook' .. n, function() hook(formula, caster, target, classes, helpers, before) end)
  end
end

return R
