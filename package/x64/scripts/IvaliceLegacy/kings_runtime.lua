-- Ivalice Legacy: Kings of the zone. Every rare game (Bestiary entries 293-372) rules its zone: it is there on every
-- visit until it has been defeated once. After that it follows the game's own conditions (rolls, play time, chains and
-- so on), so it can return, but rarely.
--
-- Two small native pieces, each installed only when the original game is found:
--   0x3504BE    script command 0x5A9 (the engine's "100% Spawn Rare Monster" debug switch), after the call at 0x3504B9
--               that Bountiful Bundle may patch. In a King's home it answers 1 to whoever asks while that King has never
--               been defeated (presence 2), the same answer Bountiful's option and the debug menu give. A spawner
--               recognised as a King's own answers for that King alone, so a defeated King stays out beside a living
--               neighbour. 0.0.15-0.0.17 answered only recognised spawners, and in game nothing was recognised (no yes in
--               a whole session, Greeden missing), so presence no longer depends on it. The log line "switch asked"
--               records what the hook sees, to find out why. On a map with two Kings the asker is told apart by its
--               thread index (K.SPAWNERS), so a defeated King stays out beside a living neighbour. Spawners that only the switch brings in stop asking
--               (patches/kings.json), so ordinary foes are not added. Hunt Club Kings are forced too, before the quest:
--               a dangerous monster in a low-level area is wanted. Their trophy state (save image + 0x200 + 0xC90 + slot:
--               0 quest not started, 1 active, 2 trophy earned) keeps them gone once the trophy is earned. Accepting the
--               quest sets every slot to 1, so a King killed before it can return once on its natural roll.
--   0x1F28B70   the handler of btlAtelCanselInitialSpawnFlag in the script command table. Presence 3 only (Grey Molter,
--               whose map has no switch and whose entry is a play-time window): the command does nothing for the
--               undefeated King in its home, on the inference that a unit enters by default unless this command ran.
--               For every other spawner, and after a defeat, the game's handler 0x5102D0 runs.
-- Scripts (patches/kings.json, same-length edits, natural conditions intact when the switch says no): Cultsworn Lich and
-- Evil Spirit are made to ask the switch. Several spawners can hold one King (Wary Wolf, Matriarch Bomb, Grimalkin, Disma,
-- Evil Spirit): each selection tests their shared one-King flag first, flag < (natural roll OR switch), so the first
-- spawner the party triggers takes the King and the rest keep their ordinary foes. A spawner the game restores from
-- memory to an ordinary group has an ordinary class and so gets the native answer: that entry may have no King.
-- Keep Bountiful Bundle's "Always Spawn Rare Game" off: it makes the native answer always 1, which brings every defeated
-- King back on every visit. Never replace a spawner's unit: every spawner goes on to set its direction, level and so on
-- through the unit's work pointer, so a spawner given "no unit" (0xFF) crashes the game.
-- "Defeated" is the game's own Bestiary count, which the save keeps: the save image is at 0x2164280 and entry n's byte is
-- at +0xB603+n. The class table's +0x52 counts from 0 (Nekhbet 330) while these count from 1 (331). The byte is encoded:
-- 0 unknown, 1 recorded without a kill (0x315A90), otherwise kills + 1 (getter 0x315434, increment 0x31582D). So a King
-- is defeated only when its byte is 2 or more.
local K = {version = '0.7.0'}

K.FIRST, K.LAST = 293, 372
K.KILLS = 0x0216F883          -- 0x2164280 (save image) + 0xB603; entry n at K.KILLS + n
K.LOCATION = 0x021654C4       -- current location id (save image + 0x1244)
K.RARE_SITE, K.RARE_RETURN = 0x003504BE, 0x003504C3
K.ORIGINAL = {0x8B, 0xD0, 0x48, 0x8B, 0xCB}  -- mov edx,eax ; mov rcx,rbx
K.CANCEL_SLOT, K.CANCEL_HANDLER = 0x01F28B70, 0x005102D0   -- set 3 table 0x1F281B0 + (0x4D + 1) * 32
K.DATA_SIZE, K.ENTRY_AT, K.ENTRY_SIZE = 0x800, 0x40, 8
K.SPAWNER_AT = 0x600
-- Data block: +0 on (u8), +4 home count, +8 presence answers forced, +0xC cancels skipped, +0x14 last forced location,
-- +0x18 times the switch was asked, +0x1C last Bestiary entry read from an asker, +0x20 askers with no class,
-- +0x24 two-King spawner count, +0x28 times one of them asked, +0x40 homes {u32 location, u16 Bestiary entry, u16 presence},
-- +0x600 two-King spawners {u32 location, u16 thread index, u16 Bestiary entry}.

-- Home locations of every King (field maps whose scripts set the King's roster row; location ids from the map names).
-- Presence 0: natural spawn rules (Larva Eater's gil_e/f/g spawners have no way in at all). 1: forced until defeated,
-- a recognised own spawner only (no home uses this now). 2: anyone asking in this location gets yes until defeated.
-- 3: no switch on the map: its spawn cancel does nothing until defeated.
K.HOMES = {  -- {location, Bestiary entry, presence}
  {505, 293, 2},  -- Aspidochelon (srb_b03)
  {358, 294, 2},  -- Aerieel (lus_a02)
  {492, 295, 2},  -- Thalassinon (fon_c02)
  {185, 296, 2},  -- Anubys (rui_a02)
  {230, 297, 2},  -- Greeden (dst_a04)
  {410, 298, 2},  -- Barmuu (rwg_c01)
  {536, 299, 2},  -- Bull Croc (ozm_c01)
  {1119, 300, 2},  -- Vishno (rbl_e02)
  {844, 301, 2},  -- Gavial (grm_e07)
  {506, 302, 2},  -- Etherian (srb_b04)
  {210, 303, 2},  -- Wary Wolf (mnt_a02)
  {717, 304, 2},  -- Melt (hne_a06)
  {346, 305, 2},  -- Kaiser Wolf (wdl_a02)
  {193, 306, 2},  -- Cubus (rui_b02)
  {347, 307, 2},  -- Lindbur Wolf (wdl_a03)
  {380, 308, 2},  -- Pineapple (ene_a02)
  {541, 309, 2},  -- Dreadguard (mfr_a03)
  {901, 310, 2},  -- Bombshell (lus_f01)
  {540, 311, 2},  -- Crypt Bunny (mfr_a02)
  {398, 312, 2},  -- Megabomb (enw_b01)
  {154, 313, 2},  -- Spee (frs_a04)
  {598, 314, 2},  -- Matriarch Bomb (mrm_b02)
  {254, 315, 2},  -- Rain Dancer (sav_d02)
  {256, 315, 2},  -- Rain Dancer (sav_d04)
  {266, 315, 2},  -- Rain Dancer (sav_f03)
  {228, 316, 2},  -- Ripe Rampager (dst_a02)
  {313, 317, 2},  -- Razorfin (grm_a03)
  {526, 318, 2},  -- Killbug (ozm_a02)
  {480, 319, 2},  -- Apsara (fon_a03)
  {891, 320, 2},  -- Minibug (mic_e01)
  {163, 321, 2},  -- Rageclaw (frs_c02)
  {895, 322, 2},  -- Ithuno (mic_e05)
  {448, 323, 2},  -- Biding Mantis (gol_a02)
  {139, 324, 2},  -- Kris (pla_a03)
  {162, 325, 2},  -- Wood Toad (frs_c01)
  {217, 326, 2},  -- Dheed (mnt_b03)
  {147, 327, 2},  -- Tarasque (pla_b04)
  {536, 328, 0},  -- Aeros (ozm_c01)
  {137, 329, 2},  -- Grimalkin (pla_a01)
  {133, 330, 2},  -- Terror Tyrant (dst_c02)
  {229, 331, 2},  -- Nekhbet (dst_a03)
  {511, 332, 2},  -- Abelisk (rwf_a03)
  {726, 333, 2},  -- Glaring Eye (hne_c02)
  {728, 333, 2},  -- Glaring Eye (hne_c04)
  {346, 334, 2},  -- Dustia (wdl_a02)
  {411, 335, 2},  -- Cultsworn Lich (rwg_c02)
  {891, 336, 0},  -- Ishteen (mic_e01)
  {892, 336, 2},  -- Ishteen (mic_e02)
  {947, 337, 2},  -- Evil Spirit (gil_i09)
  {972, 337, 2},  -- Evil Spirit (gil_j21)
  {973, 337, 2},  -- Evil Spirit (gil_j22)
  {984, 337, 2},  -- Evil Spirit (gil_k06)
  {986, 337, 2},  -- Evil Spirit (gil_k08)
  {1002, 337, 2},  -- Evil Spirit (gil_l01)
  {1004, 337, 2},  -- Evil Spirit (gil_l03)
  {1005, 337, 2},  -- Evil Spirit (gil_l04)
  {430, 338, 2},  -- Vorres (bds_c01)
  {548, 339, 2},  -- Juggernaut (mfr_c01)
  {609, 340, 2},  -- Negalmuur (mrm_d04)
  {578, 341, 2},  -- Molen (ztc_a03)
  {332, 342, 0},  -- Larva Eater (gil_g01)
  {335, 342, 0},  -- Larva Eater (gil_g04)
  {621, 342, 0},  -- Larva Eater (gil_e01)
  {624, 342, 0},  -- Larva Eater (gil_e04)
  {626, 342, 0},  -- Larva Eater (gil_e06)
  {926, 342, 0},  -- Larva Eater (gil_f01)
  {930, 342, 0},  -- Larva Eater (gil_f05)
  {939, 342, 2},  -- Larva Eater (gil_i01)
  {940, 342, 2},  -- Larva Eater (gil_i02)
  {952, 342, 2},  -- Larva Eater (gil_j01)
  {953, 342, 2},  -- Larva Eater (gil_j02)
  {971, 342, 2},  -- Larva Eater (gil_j20)
  {979, 342, 2},  -- Larva Eater (gil_k01)
  {996, 342, 2},  -- Larva Eater (gil_k18)
  {1141, 343, 2},  -- Tower (rbl_n02)
  {175, 344, 2},  -- Arioch (bog_b01)
  {216, 345, 3},  -- Grey Molter (mnt_b02)
  {348, 346, 2},  -- Fideliant (wdl_a04)
  {447, 347, 2},  -- Midgardsormr (gol_a01)
  {940, 348, 2},  -- Crystal Knight (gil_i02)
  {247, 349, 2},  -- Nazarnir (sav_c01)
  {453, 350, 2},  -- Grave Lord (gol_b01)
  {393, 351, 2},  -- Victanir (enw_a04)
  {412, 352, 2},  -- Zombie Lord (rwg_c03)
  {374, 353, 2},  -- Gemhorn (lus_d03)
  {849, 354, 2},  -- Drowned (grm_e12)
  {1128, 355, 2},  -- Luxollid (rbl_h03)
  {177, 356, 2},  -- Velelu (bog_b03)
  {1130, 357, 2},  -- Vagrant Soul (rbl_i02)
  {902, 358, 2},  -- Disma (lus_f02)
  {903, 358, 2},  -- Disma (lus_f03)
  {391, 359, 2},  -- Imdugud (enw_a02)
  {470, 360, 2},  -- Ancbolder (pbc_b03)
  {387, 361, 2},  -- Bull Chocobo (ene_a09)
  {192, 362, 2},  -- Wendice (rui_b01)
  {482, 363, 2},  -- Skullash (fon_a05)
  {470, 364, 2},  -- Anchag (pbc_b03)
  {606, 365, 2},  -- Myath (mrm_d01)
  {498, 366, 2},  -- Bluesang (srb_a02)
  {421, 367, 2},  -- Helvinek (bds_a01)
  {422, 367, 2},  -- Helvinek (bds_a02)
  {426, 367, 2},  -- Helvinek (bds_b01)
  {430, 367, 2},  -- Helvinek (bds_c01)
  {431, 367, 2},  -- Helvinek (bds_c02)
  {1138, 368, 2},  -- Avenger (rbl_m01)
  {517, 369, 2},  -- Pallicant (rwf_b01)
  {581, 370, 2},  -- Alteci (ztc_b01)
  {459, 371, 2},  -- Phyllo (gol_d01)
  {385, 372, 2},  -- Urutan Exile (ene_a07)
}

-- The maps with two Kings. Presence 2 says yes while any King of the location lives, so each of these spawners answers for
-- its own King alone: yes until that King is defeated, then the game's own answer. The thread index is u16 [thread]
-- (stored at 0x2665EB). These are the threads whose scripts run command 0x5A9 and set that King's unit (read from the
-- effective scripts, 2026-11-05). Each map's trophy thread also asks, sets no unit, and keeps the location answer.
K.SPAWNERS = {  -- {location, thread, Bestiary entry}
  {346, 21, 334},  -- Dustia (wdl_a02)
  {346, 22, 305},  -- Kaiser Wolf (wdl_a02)
  {430, 25, 338},  -- Vorres (bds_c01)
  {430, 26, 367},  -- Helvinek (bds_c01)
  {940, 32, 342},  -- Larva Eater (gil_i02)
  {940, 33, 348},  -- Crystal Knight (gil_i02)
  {470, 22, 364},  -- Anchag (pbc_b03)
  {470, 23, 360},  -- Ancbolder (pbc_b03)
}

local logged, LOG_LIMIT = 0, 200
local function log(line)
  if logged < LOG_LIMIT then logged = logged + 1; print('Legacy Kings: ' .. line) end
end
K.log = log

local function bytesAt(address, count)
  local ok, raw = pcall(memory.readArray, address, count)
  if not ok or (type(raw) ~= 'table' and type(raw) ~= 'string') or #raw ~= count then return nil end
  local out = {}
  for i = 1, count do out[i] = type(raw) == 'string' and raw:byte(i) or raw[i] end
  return out
end

local function matches(address, expected)
  local got = bytesAt(address, #expected)
  if not got then return false end
  for i = 1, #expected do if got[i] ~= expected[i] then return false end end
  return true
end

-- Same exe and save namespace checks as Proficiency: only the Legacy build of the game (LEGCY_ saves).
local function text(s, wide)
  local out = {}
  for i = 1, #s do
    out[#out + 1] = s:byte(i)
    if wide then out[#out + 1] = 0 end
  end
  return out
end
function K.legacyMode()
  return matches(0x120000, text('MZ')) and matches(0x120180, {0x50, 0x45, 0, 0, 0x64, 0x86})
    and matches(0xD113D4, text('LEGCY_\0')) and matches(0xD12200, text('%sLEGCY_%03d\0', true))
end

-- Validates the table: every entry a King, every location a plain id, no duplicates.
function K.validate(homes)
  local seen = {}
  for i, h in ipairs(homes) do
    local loc, entry, presence = h[1], h[2], h[3]
    if type(loc) ~= 'number' or loc % 1 ~= 0 or loc < 1 or loc > 0xFFFF then return false, 'location ' .. i end
    if type(entry) ~= 'number' or entry % 1 ~= 0 or entry < K.FIRST or entry > K.LAST then return false, 'entry ' .. i end
    if presence ~= 0 and presence ~= 1 and presence ~= 2 and presence ~= 3 then return false, 'presence ' .. i end
    local key = loc * 1000 + entry
    if seen[key] then return false, 'duplicate ' .. i end
    seen[key] = true
  end
  if #homes * K.ENTRY_SIZE + K.ENTRY_AT > K.SPAWNER_AT then return false, 'too many homes' end
  if #K.SPAWNERS * K.ENTRY_SIZE + K.SPAWNER_AT > K.DATA_SIZE then return false, 'too many spawners' end
  for i, s in ipairs(K.SPAWNERS) do
    if homes == K.HOMES and not seen[s[1] * 1000 + s[3]] then return false, 'spawner without a home ' .. i end
  end
  return true
end

-- The data block as bytes (pure: tested offline).
function K.dataBytes(homes, on)
  local b = {}
  for i = 1, K.DATA_SIZE do b[i] = 0 end
  local function put(at, value, size)
    for k = 0, size - 1 do b[at + k + 1] = (value >> (8 * k)) & 0xFF end
  end
  put(0, on and 1 or 0, 1)
  put(4, #homes, 4)
  for i, h in ipairs(homes) do
    local at = K.ENTRY_AT + (i - 1) * K.ENTRY_SIZE
    put(at, h[1], 4); put(at + 4, h[2], 2); put(at + 6, h[3], 2)
  end
  put(0x24, #K.SPAWNERS, 4)
  for i, s in ipairs(K.SPAWNERS) do
    local at = K.SPAWNER_AT + (i - 1) * K.ENTRY_SIZE
    put(at, s[1], 4); put(at + 4, s[2], 2); put(at + 6, s[3], 2)
  end
  return b
end

K.RARE_CODE = [[
legacy_kings_rare:
  mov r8,[legacy_kings_data]
  test r8,r8
  je legacy_kings_rare_done
  cmp byte ptr [r8],0x00
  je legacy_kings_rare_done
  xor ecx,ecx
  mov r9,[rbx+0x30]
  test r9,r9
  je legacy_kings_rare_homes
  test byte ptr [r9],0x04
  je legacy_kings_rare_homes
  mov r9,[r9+0xE68]
  test r9,r9
  je legacy_kings_rare_homes
  movzx ecx,word ptr [r9+0x52]
  inc ecx
legacy_kings_rare_homes:
  inc dword ptr [r8+0x18]
  test ecx,ecx
  je legacy_kings_rare_no_class
  mov [r8+0x1C],ecx
  jmp legacy_kings_rare_lookup
legacy_kings_rare_no_class:
  inc dword ptr [r8+0x20]
legacy_kings_rare_lookup:
  mov r11d,0x021654C4
  mov edx,[r11]
  movzx r11d,word ptr [rbx]
  mov r9d,[r8+0x24]
  lea r10,[r8+0x600]
legacy_kings_rare_spawner:
  test r9d,r9d
  je legacy_kings_rare_own_start
  cmp [r10],edx
  jne legacy_kings_rare_spawner_next
  cmp word ptr [r10+0x04],r11w
  jne legacy_kings_rare_spawner_next
  inc dword ptr [r8+0x28]
  movzx ecx,word ptr [r10+0x06]
  mov r11d,0x0216F883
  cmp byte ptr [r11+rcx],0x01
  ja legacy_kings_rare_done
  jmp legacy_kings_rare_force
legacy_kings_rare_spawner_next:
  add r10,0x08
  dec r9d
  jmp legacy_kings_rare_spawner
legacy_kings_rare_own_start:
  mov r9d,[r8+0x04]
  lea r10,[r8+0x40]
legacy_kings_rare_own:
  test r9d,r9d
  je legacy_kings_rare_others
  cmp [r10],edx
  jne legacy_kings_rare_own_next
  movzx r11d,word ptr [r10+0x04]
  cmp r11d,ecx
  je legacy_kings_rare_own_found
legacy_kings_rare_own_next:
  add r10,0x08
  dec r9d
  jmp legacy_kings_rare_own
legacy_kings_rare_own_found:
  cmp word ptr [r10+0x06],0x00
  je legacy_kings_rare_done
  mov r11d,0x0216F883
  cmp byte ptr [r11+rcx],0x01
  ja legacy_kings_rare_done
  jmp legacy_kings_rare_force
legacy_kings_rare_others:
  mov r9d,[r8+0x04]
  lea r10,[r8+0x40]
legacy_kings_rare_loop:
  test r9d,r9d
  je legacy_kings_rare_done
  cmp [r10],edx
  jne legacy_kings_rare_next
  cmp word ptr [r10+0x06],0x02
  jne legacy_kings_rare_next
  movzx ecx,word ptr [r10+0x04]
  mov r11d,0x0216F883
  cmp byte ptr [r11+rcx],0x01
  jbe legacy_kings_rare_force
legacy_kings_rare_next:
  add r10,0x08
  dec r9d
  jmp legacy_kings_rare_loop
legacy_kings_rare_force:
  mov eax,0x01
  inc dword ptr [r8+0x08]
  mov [r8+0x14],edx
legacy_kings_rare_done:
  mov edx,eax
  mov rcx,rbx
  jmp 0x003504C3

legacy_kings_cancel:
  mov r8,[legacy_kings_data]
  test r8,r8
  je legacy_kings_cancel_run
  cmp byte ptr [r8],0x00
  je legacy_kings_cancel_run
  test rdx,rdx
  je legacy_kings_cancel_run
  mov r9,[rdx+0x30]
  test r9,r9
  je legacy_kings_cancel_run
  test byte ptr [r9],0x04
  je legacy_kings_cancel_run
  mov r9,[r9+0xE68]
  test r9,r9
  je legacy_kings_cancel_run
  movzx ecx,word ptr [r9+0x52]
  inc ecx
  cmp ecx,0x125
  jb legacy_kings_cancel_run
  cmp ecx,0x174
  ja legacy_kings_cancel_run
  mov r11d,0x0216F883
  cmp byte ptr [r11+rcx],0x01
  ja legacy_kings_cancel_run
  mov r11d,0x021654C4
  mov eax,[r11]
  mov r9d,[r8+0x04]
  lea r10,[r8+0x40]
legacy_kings_cancel_loop:
  test r9d,r9d
  je legacy_kings_cancel_run
  cmp [r10],eax
  jne legacy_kings_cancel_next
  cmp word ptr [r10+0x04],cx
  jne legacy_kings_cancel_next
  cmp word ptr [r10+0x06],0x03
  jne legacy_kings_cancel_run
  inc dword ptr [r8+0x0C]
  ret
legacy_kings_cancel_next:
  add r10,0x08
  dec r9d
  jmp legacy_kings_cancel_loop
legacy_kings_cancel_run:
  jmp 0x005102D0

  .align 0x10
legacy_kings_data:
  .dq 0x00
]]
-- Every absolute address is loaded into a register first: an assembler may encode "[imm]" as rip-relative for
-- registers other than rax (Keystone does), which would read the wrong address. rbx is the handler's calling thread
-- (+0x30 its actor; flag 4 = has a unit; +0xE68 its class). The tail call at 0x26B4E0 reads only rcx and edx.
-- legacy_kings_cancel replaces a handler (rdx = thread): the game's handler reads only rdx, so r8-r11, rax and rcx are free.
K.SYMBOLS = {'legacy_kings_rare', 'legacy_kings_cancel', 'legacy_kings_data'}
K.RARE_PATCH = 'jmp %legacy_kings_rare%'

local state = {installed = false, data = nil, forced = 0, kept = 0, asked = 0, noClass = 0, own = 0}
K.state = state

function K.install()
  if state.installed or state.failed then return state.installed end
  local ok, why = K.validate(K.HOMES)
  if not ok then state.failed = true; log('not installed: bad home table (' .. why .. ')'); return false end
  if not K.legacyMode() then
    -- scripts/init (IvaliceLegacySaves) normally runs first; one more try comes with the first map change.
    if state.waited then state.failed = true end
    state.waited = true
    log('not installed: not the Legacy build of the game' .. (state.failed and '' or ' (yet)'))
    return false
  end
  if not matches(K.RARE_SITE, K.ORIGINAL) then
    state.failed = true
    log(string.format('not installed: unexpected code at 0x%X (another mod patches it?)', K.RARE_SITE))
    return false
  end
  if memory.u64[K.CANCEL_SLOT] ~= K.CANCEL_HANDLER then
    state.failed = true
    log(string.format('not installed: unexpected command table at 0x%X (another mod uses it?)', K.CANCEL_SLOT))
    return false
  end
  local data = memory.alloc(K.DATA_SIZE)
  memory.writeArray(data, K.dataBytes(K.HOMES, false))
  local done, err = pcall(memory.assemble, K.RARE_CODE, K.SYMBOLS)
  local slot = done and memory.getSymbol('legacy_kings_data') or nil
  local keep = done and memory.getSymbol('legacy_kings_cancel') or nil
  if not done or not slot or slot == 0 or not keep or keep == 0 then
    state.failed = true; log('not installed: code block rejected (' .. tostring(err) .. ')'); return false
  end
  memory.u64[slot] = data
  -- The site and the table slot last, once the block and its data are in place. Both stay inert until K.setOn.
  -- memory.assemble reports most failures by returning 0, not by throwing; and the site must really have changed.
  local placed
  done, placed = pcall(memory.assemble, K.RARE_PATCH, K.RARE_SITE)
  if not done or not placed or placed == 0 or matches(K.RARE_SITE, K.ORIGINAL) then
    state.failed = true
    log(string.format('site 0x%X rejected (%s); Kings stay off', K.RARE_SITE, tostring(placed)))
    return false
  end
  memory.u64[K.CANCEL_SLOT] = keep
  if memory.u64[K.CANCEL_SLOT] ~= keep then
    state.failed = true
    log(string.format('command table 0x%X kept its old value; Kings stay off', K.CANCEL_SLOT))
    return false
  end
  state.installed, state.data = true, data
  log(string.format('%s installed: %d homes', K.version, #K.HOMES))
  return true
end

function K.setOn(on)
  if state.installed then memory.u8[state.data] = on and 1 or 0 end
end

function K.alive(entry) return memory.u8[K.KILLS + entry] < 2 end

-- One line per map: the Kings of this location, alive or defeated, and what the hook did since the last map.
function K.report(location)
  if not state.installed then return end
  local forced, kept = memory.u32[state.data + 0x08], memory.u32[state.data + 0x0C]
  local asked, noClass = memory.u32[state.data + 0x18], memory.u32[state.data + 0x20]
  local own = memory.u32[state.data + 0x28]
  if asked ~= state.asked or kept ~= state.kept then
    log(string.format('since last map: switch asked %d (no class %d, last entry %d, two-King spawners %d), forced %d, cancels skipped %d',
      asked - state.asked, noClass - state.noClass, memory.u32[state.data + 0x1C], own - state.own, forced - state.forced, kept - state.kept))
    state.forced, state.kept, state.asked, state.noClass, state.own = forced, kept, asked, noClass, own
  end
  local here = {}
  for _, h in ipairs(K.HOMES) do
    if h[1] == location then
      here[#here + 1] = string.format('%d %s%s', h[2], K.alive(h[2]) and 'alive' or 'defeated',
        h[3] ~= 0 and '' or ' (natural spawn)')
    end
  end
  if #here > 0 then log(string.format('location %d: %s', location, table.concat(here, ', '))) end
end

-- On from the start: the hook reads the live location and defeat counts at each decision, so a save loaded at
-- any point is handled from its first map. The map report is diagnostics only.
function K.register()
  local installed = K.install()
  if installed then K.setOn(true) end
  if not installed and state.failed then return false end
  if type(event) == 'table' and event.registerEventAsync then
    event.registerEventAsync('onMapJump', function(to)
      if not state.installed and not state.failed and K.install() then K.setOn(true) end
      pcall(K.report, to)
    end)
  end
  return installed
end

return K
