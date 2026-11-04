-- Ivalice Legacy: Kings of the zone. Every rare game (Bestiary entries 293-372) rules its zone: it is there on every
-- visit while it lives, and once it has been defeated it never appears again.
--
-- Two small native hooks, each installed only when the original bytes are found:
--   0x3504BE  script command 0x5A9 (the engine's "100% Spawn Rare Monster" debug switch), after the call at
--             0x3504B9 that Bountiful Bundle may patch: in a King's home location the answer is 1 while that King
--             lives. Everywhere else, and once the King is dead, the native (or Bountiful) answer stands.
--   0x510178  btlAtelSetUnit, before 0x23A3B0: when a field script sets a unit whose class is a dead King's
--             Bestiary entry, in one of that King's home locations, the unit becomes 0xFF ("no unit", the engine's
--             own value for an unset spawner), so the King is never placed there again. The class table's +0x52
--             counts from 0 (Nekhbet 330) while the defeat counts and K.HOMES count from 1 (Nekhbet 331).
-- "Dead" is the game's own Bestiary defeat count, which the save keeps: the save image is at 0x2164280 and entry n's
-- count is the byte at +0xB603+n. Kings defeated before this file existed count as dead too.
local K = {version = '0.1.1'}

K.FIRST, K.LAST = 293, 372
K.KILLS = 0x0216F883          -- 0x2164280 (save image) + 0xB603; entry n at K.KILLS + n
K.LOCATION = 0x021654C4       -- current location id (save image + 0x1244)
K.UNIT_TABLE, K.CLASS_TABLE, K.TABLE_BASE = 0x02EBF120, 0x02EBF150, 0x01F83530
K.RARE_SITE, K.RARE_RETURN = 0x003504BE, 0x003504C3
K.UNIT_SITE, K.UNIT_RETURN = 0x00510178, 0x0051017D
K.ORIGINAL = {0x8B, 0xD0, 0x48, 0x8B, 0xCB}  -- mov edx,eax ; mov rcx,rbx (both sites)
K.DATA_SIZE, K.ENTRY_AT, K.ENTRY_SIZE = 0x800, 0x40, 8
-- Data block: +0 on (u8), +4 home count, +8 presence answers forced, +0xC last vetoed entry, +0x10 vetoes,
-- +0x14 last forced location, +0x40 homes {u32 location, u16 Bestiary entry, u16 presence}.

-- Home locations of every King (field maps whose scripts set the King's roster row; location ids from the map
-- names). Presence 0: the map has no 0x5A9 call, or it is one of the five locations Bountiful Bundle never forces
-- (133, 137, 247, 902, 903), whose spawners mix the King with ordinary foes; there the natural spawn rules stay.
K.HOMES = {  -- {location, Bestiary entry, presence: 1 forced while alive, 0 natural spawn rules}
  {505, 293, 1},  -- Aspidochelon (srb_b03)
  {358, 294, 1},  -- Aerieel (lus_a02)
  {492, 295, 1},  -- Thalassinon (fon_c02)
  {185, 296, 1},  -- Anubys (rui_a02)
  {230, 297, 1},  -- Greeden (dst_a04)
  {410, 298, 1},  -- Barmuu (rwg_c01)
  {536, 299, 1},  -- Bull Croc (ozm_c01)
  {1119, 300, 1},  -- Vishno (rbl_e02)
  {844, 301, 1},  -- Gavial (grm_e07)
  {506, 302, 1},  -- Etherian (srb_b04)
  {210, 303, 1},  -- Wary Wolf (mnt_a02)
  {717, 304, 1},  -- Melt (hne_a06)
  {346, 305, 1},  -- Kaiser Wolf (wdl_a02)
  {193, 306, 1},  -- Cubus (rui_b02)
  {347, 307, 1},  -- Lindbur Wolf (wdl_a03)
  {380, 308, 1},  -- Pineapple (ene_a02)
  {541, 309, 1},  -- Dreadguard (mfr_a03)
  {901, 310, 1},  -- Bombshell (lus_f01)
  {540, 311, 1},  -- Crypt Bunny (mfr_a02)
  {398, 312, 1},  -- Megabomb (enw_b01)
  {154, 313, 1},  -- Spee (frs_a04)
  {598, 314, 1},  -- Matriarch Bomb (mrm_b02)
  {254, 315, 1},  -- Rain Dancer (sav_d02)
  {256, 315, 1},  -- Rain Dancer (sav_d04)
  {266, 315, 1},  -- Rain Dancer (sav_f03)
  {228, 316, 1},  -- Ripe Rampager (dst_a02)
  {313, 317, 1},  -- Razorfin (grm_a03)
  {526, 318, 1},  -- Killbug (ozm_a02)
  {480, 319, 1},  -- Apsara (fon_a03)
  {891, 320, 1},  -- Minibug (mic_e01)
  {163, 321, 1},  -- Rageclaw (frs_c02)
  {895, 322, 1},  -- Ithuno (mic_e05)
  {448, 323, 1},  -- Biding Mantis (gol_a02)
  {139, 324, 1},  -- Kris (pla_a03)
  {162, 325, 1},  -- Wood Toad (frs_c01)
  {217, 326, 1},  -- Dheed (mnt_b03)
  {147, 327, 1},  -- Tarasque (pla_b04)
  {536, 328, 1},  -- Aeros (ozm_c01)
  {137, 329, 0},  -- Grimalkin (pla_a01)
  {133, 330, 0},  -- Terror Tyrant (dst_c02)
  {229, 331, 1},  -- Nekhbet (dst_a03)
  {511, 332, 1},  -- Abelisk (rwf_a03)
  {726, 333, 1},  -- Glaring Eye (hne_c02)
  {728, 333, 1},  -- Glaring Eye (hne_c04)
  {346, 334, 1},  -- Dustia (wdl_a02)
  {411, 335, 0},  -- Cultsworn Lich (rwg_c02)
  {891, 336, 1},  -- Ishteen (mic_e01)
  {892, 336, 1},  -- Ishteen (mic_e02)
  {947, 337, 0},  -- Evil Spirit (gil_i09)
  {972, 337, 0},  -- Evil Spirit (gil_j21)
  {973, 337, 0},  -- Evil Spirit (gil_j22)
  {984, 337, 0},  -- Evil Spirit (gil_k06)
  {986, 337, 0},  -- Evil Spirit (gil_k08)
  {1002, 337, 0},  -- Evil Spirit (gil_l01)
  {1004, 337, 0},  -- Evil Spirit (gil_l03)
  {1005, 337, 0},  -- Evil Spirit (gil_l04)
  {430, 338, 1},  -- Vorres (bds_c01)
  {548, 339, 1},  -- Juggernaut (mfr_c01)
  {609, 340, 1},  -- Negalmuur (mrm_d04)
  {578, 341, 1},  -- Molen (ztc_a03)
  {332, 342, 0},  -- Larva Eater (gil_g01)
  {335, 342, 0},  -- Larva Eater (gil_g04)
  {621, 342, 0},  -- Larva Eater (gil_e01)
  {624, 342, 0},  -- Larva Eater (gil_e04)
  {626, 342, 0},  -- Larva Eater (gil_e06)
  {926, 342, 0},  -- Larva Eater (gil_f01)
  {930, 342, 0},  -- Larva Eater (gil_f05)
  {939, 342, 1},  -- Larva Eater (gil_i01)
  {940, 342, 1},  -- Larva Eater (gil_i02)
  {952, 342, 1},  -- Larva Eater (gil_j01)
  {953, 342, 1},  -- Larva Eater (gil_j02)
  {971, 342, 1},  -- Larva Eater (gil_j20)
  {979, 342, 1},  -- Larva Eater (gil_k01)
  {996, 342, 1},  -- Larva Eater (gil_k18)
  {1141, 343, 1},  -- Tower (rbl_n02)
  {175, 344, 1},  -- Arioch (bog_b01)
  {216, 345, 0},  -- Grey Molter (mnt_b02)
  {348, 346, 1},  -- Fideliant (wdl_a04)
  {447, 347, 1},  -- Midgardsormr (gol_a01)
  {940, 348, 1},  -- Crystal Knight (gil_i02)
  {247, 349, 0},  -- Nazarnir (sav_c01)
  {453, 350, 1},  -- Grave Lord (gol_b01)
  {393, 351, 1},  -- Victanir (enw_a04)
  {412, 352, 1},  -- Zombie Lord (rwg_c03)
  {374, 353, 1},  -- Gemhorn (lus_d03)
  {849, 354, 1},  -- Drowned (grm_e12)
  {1128, 355, 1},  -- Luxollid (rbl_h03)
  {177, 356, 1},  -- Velelu (bog_b03)
  {1130, 357, 1},  -- Vagrant Soul (rbl_i02)
  {902, 358, 0},  -- Disma (lus_f02)
  {903, 358, 0},  -- Disma (lus_f03)
  {391, 359, 1},  -- Imdugud (enw_a02)
  {470, 360, 1},  -- Ancbolder (pbc_b03)
  {387, 361, 1},  -- Bull Chocobo (ene_a09)
  {192, 362, 1},  -- Wendice (rui_b01)
  {482, 363, 1},  -- Skullash (fon_a05)
  {470, 364, 1},  -- Anchag (pbc_b03)
  {606, 365, 1},  -- Myath (mrm_d01)
  {498, 366, 1},  -- Bluesang (srb_a02)
  {421, 367, 1},  -- Helvinek (bds_a01)
  {422, 367, 1},  -- Helvinek (bds_a02)
  {426, 367, 1},  -- Helvinek (bds_b01)
  {430, 367, 1},  -- Helvinek (bds_c01)
  {431, 367, 1},  -- Helvinek (bds_c02)
  {1138, 368, 1},  -- Avenger (rbl_m01)
  {517, 369, 1},  -- Pallicant (rwf_b01)
  {581, 370, 1},  -- Alteci (ztc_b01)
  {459, 371, 1},  -- Phyllo (gol_d01)
  {385, 372, 1},  -- Urutan Exile (ene_a07)
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
    if presence ~= 0 and presence ~= 1 then return false, 'presence ' .. i end
    local key = loc * 1000 + entry
    if seen[key] then return false, 'duplicate ' .. i end
    seen[key] = true
  end
  if #homes * K.ENTRY_SIZE + K.ENTRY_AT > K.DATA_SIZE then return false, 'too many homes' end
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
  return b
end

K.RARE_CODE = [[
legacy_kings_rare:
  mov r8,[legacy_kings_data]
  test r8,r8
  je legacy_kings_rare_done
  cmp byte ptr [r8],0x00
  je legacy_kings_rare_done
  mov r11d,0x021654C4
  mov ecx,[r11]
  mov r9d,[r8+0x04]
  lea r10,[r8+0x40]
legacy_kings_rare_loop:
  test r9d,r9d
  je legacy_kings_rare_done
  cmp [r10],ecx
  jne legacy_kings_rare_next
  cmp word ptr [r10+0x06],0x00
  je legacy_kings_rare_next
  movzx edx,word ptr [r10+0x04]
  mov r11d,0x0216F883
  cmp byte ptr [r11+rdx],0x00
  jne legacy_kings_rare_next
  mov eax,0x01
  inc dword ptr [r8+0x08]
  mov [r8+0x14],ecx
  jmp legacy_kings_rare_done
legacy_kings_rare_next:
  add r10,0x08
  dec r9d
  jmp legacy_kings_rare_loop
legacy_kings_rare_done:
  mov edx,eax
  mov rcx,rbx
  jmp 0x003504C3

legacy_kings_unit:
  mov r8,[legacy_kings_data]
  test r8,r8
  je legacy_kings_unit_done
  cmp byte ptr [r8],0x00
  je legacy_kings_unit_done
  cmp eax,0xFF
  jae legacy_kings_unit_done
  mov r9d,0x02EBF120
  mov r9,[r9]
  test r9,r9
  je legacy_kings_unit_done
  cmp eax,[r9+0x04]
  jae legacy_kings_unit_done
  movzx r10d,word ptr [r9+0x08]
  imul r10d,eax
  mov r11d,[r9+0x0C]
  add r10,r11
  mov r11d,0x01F83530
  mov r11,[r11]
  add r10,r11
  movzx ecx,word ptr [r10]
  mov r9d,0x02EBF150
  mov r9,[r9]
  test r9,r9
  je legacy_kings_unit_done
  cmp ecx,[r9+0x04]
  jae legacy_kings_unit_done
  movzx r10d,word ptr [r9+0x08]
  imul r10d,ecx
  mov r11d,[r9+0x0C]
  add r10,r11
  mov r11d,0x01F83530
  mov r11,[r11]
  add r10,r11
  movzx ecx,word ptr [r10+0x52]
  inc ecx
  cmp ecx,0x125
  jb legacy_kings_unit_done
  cmp ecx,0x174
  ja legacy_kings_unit_done
  mov r11d,0x0216F883
  cmp byte ptr [r11+rcx],0x00
  je legacy_kings_unit_done
  mov r11d,0x021654C4
  mov edx,[r11]
  mov r9d,[r8+0x04]
  lea r10,[r8+0x40]
legacy_kings_unit_loop:
  test r9d,r9d
  je legacy_kings_unit_done
  cmp [r10],edx
  jne legacy_kings_unit_next
  movzx r11d,word ptr [r10+0x04]
  cmp r11d,ecx
  jne legacy_kings_unit_next
  mov [r8+0x0C],ecx
  inc dword ptr [r8+0x10]
  mov eax,0xFF
  jmp legacy_kings_unit_done
legacy_kings_unit_next:
  add r10,0x08
  dec r9d
  jmp legacy_kings_unit_loop
legacy_kings_unit_done:
  mov edx,eax
  mov rcx,rbx
  jmp 0x0051017D

  .align 0x10
legacy_kings_data:
  .dq 0x00
]]
-- Every absolute address is loaded into a register first: an assembler may encode "[imm]" as rip-relative for
-- registers other than rax (Keystone does), which would read the wrong address.
K.SYMBOLS = {'legacy_kings_rare', 'legacy_kings_unit', 'legacy_kings_data'}
K.RARE_PATCH = 'jmp %legacy_kings_rare%'
K.UNIT_PATCH = 'jmp %legacy_kings_unit%'

local state = {installed = false, data = nil, forced = 0, vetoes = 0}
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
  for _, site in ipairs({K.RARE_SITE, K.UNIT_SITE}) do
    if not matches(site, K.ORIGINAL) then
      state.failed = true
      log(string.format('not installed: unexpected code at 0x%X (another mod patches it?)', site))
      return false
    end
  end
  local data = memory.alloc(K.DATA_SIZE)
  memory.writeArray(data, K.dataBytes(K.HOMES, false))
  local done, err = pcall(memory.assemble, K.RARE_CODE, K.SYMBOLS)
  local slot = done and memory.getSymbol('legacy_kings_data') or nil
  if not done or not slot or slot == 0 then
    state.failed = true; log('not installed: code block rejected (' .. tostring(err) .. ')'); return false
  end
  memory.u64[slot] = data
  -- Sites last, once the block and its data are in place.
  for _, p in ipairs({{K.RARE_PATCH, K.RARE_SITE}, {K.UNIT_PATCH, K.UNIT_SITE}}) do
    done, err = pcall(memory.assemble, p[1], p[2])
    if not done then
      state.failed = true
      log(string.format('site 0x%X rejected (%s); Kings stay off', p[2], tostring(err)))
      return false
    end
  end
  state.installed, state.data = true, data
  log(string.format('%s installed: %d homes', K.version, #K.HOMES))
  return true
end

function K.setOn(on)
  if state.installed then memory.u8[state.data] = on and 1 or 0 end
end

function K.alive(entry) return memory.u8[K.KILLS + entry] == 0 end

-- One line per map: the Kings of this location, alive or dead, and what the hooks did since the last map.
function K.report(location)
  if not state.installed then return end
  local forced, vetoes = memory.u32[state.data + 0x08], memory.u32[state.data + 0x10]
  if forced ~= state.forced or vetoes ~= state.vetoes then
    log(string.format('since last map: presence forced %d, Kings removed %d (last entry %d)',
      forced - state.forced, vetoes - state.vetoes, memory.u32[state.data + 0x0C]))
    state.forced, state.vetoes = forced, vetoes
  end
  local here = {}
  for _, h in ipairs(K.HOMES) do
    if h[1] == location then
      here[#here + 1] = string.format('%d %s%s', h[2], K.alive(h[2]) and 'alive' or 'defeated',
        h[3] == 1 and '' or ' (natural spawn)')
    end
  end
  if #here > 0 then log(string.format('location %d: %s', location, table.concat(here, ', '))) end
end

-- On from the start: the hooks read the live location and defeat counts at each decision, so a save loaded at
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
