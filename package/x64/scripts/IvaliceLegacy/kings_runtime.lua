-- Ivalice Legacy: Kings of the zone. Every rare game (Bestiary entries 293-372) rules its zone: it is there on every
-- visit until it has been defeated once. After that it follows the game's own conditions (rolls, play time, chains and
-- so on), so it can return, but rarely.
--
-- Two small native pieces, each installed only when the original game is found:
--   0x3504BE    script command 0x5A9 (the engine's "100% Spawn Rare Monster" debug switch), after the call at 0x3504B9
--               that Bountiful Bundle may patch. Every asking thread that takes part in a King's chain answers for that
--               King alone (K.SPAWNERS): yes while that King has never been defeated, the game's own answer afterwards.
--               This includes the five Kings whose King row is held by a second thread (Ripe Rampager, Razorfin,
--               Dreadguard, Crypt Bunny, Glaring Eye): their asking "writer" threads are mapped too, so the latch that
--               brings the King in (a native roll OR this switch) settles deterministically while the King lives and
--               keeps its native roll after defeat. Spawners and maps outside the table keep the per-location answer
--               (K.HOMES presence 2), exactly as 0.7.0 gave them. The pairs are the 2026-11-08 effective-script study
--               of all 230 switch-asking threads in the 103 King maps (reports/LEGACY-KINGS-SPAWN-MAP-2026-11-08.md).
--   0x1F28B70   the handler of btlAtelCanselInitialSpawnFlag in the script command table. Presence 3 only (Grey Molter,
--               whose map has no switch and whose entry is a play-time window): the command does nothing for the
--               undefeated King in its home, on the inference that a unit enters by default unless this command ran.
--               0.7.0 keyed this on the asking thread's actor class, but at cancel time a never-entered spawner has no
--               unit flag and no class row yet, so that key could never fire; 0.8.0 keys on (location, thread index),
--               the same identity the 0x5A9 hook uses (K.CANCEL_SKIPS). For every other spawner, and after a defeat,
--               the game's handler 0x5102D0 runs.
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
local K = {version = '0.8.0'}

K.FIRST, K.LAST = 293, 372
K.KILLS = 0x0216F883          -- 0x2164280 (save image) + 0xB603; entry n at K.KILLS + n
K.LOCATION = 0x021654C4       -- current location id (save image + 0x1244)
K.RARE_SITE, K.RARE_RETURN = 0x003504BE, 0x003504C3
K.ORIGINAL = {0x8B, 0xD0, 0x48, 0x8B, 0xCB}  -- mov edx,eax ; mov rcx,rbx
K.CANCEL_SLOT, K.CANCEL_HANDLER = 0x01F28B70, 0x005102D0   -- set 3 table 0x1F281B0 + (0x4D + 1) * 32
K.DATA_SIZE, K.ENTRY_AT, K.ENTRY_SIZE = 0x1000, 0x40, 8
K.SPAWNER_AT = 0x600
K.CANCEL_COUNT = 0x2C
-- Data block: +0 on (u8), +4 home count, +8 presence answers forced, +0xC cancels skipped, +0x14 last forced location,
-- +0x18 times the switch was asked, +0x1C last Bestiary entry read from an asker, +0x20 askers with no class,
-- +0x24 recognised-spawner count, +0x28 times one of them asked, +0x2C cancel-skip pair count,
-- +0x40 homes {u32 location, u16 Bestiary entry, u16 presence},
-- +0x600 recognised spawners {u32 location, u16 thread index, u16 Bestiary entry},
-- then the cancel-skip pairs in the same shape, directly after them.

-- Home locations of every King (field maps whose scripts set the King's roster row; location ids from the map names).
-- Presence 0: natural spawn rules (Aeros, Ishteen's mic_e01 twin, Larva Eater's gil_e/f/g spawners). 1: forced until
-- defeated, a recognised own spawner only (no home uses this now). 2: any unmapped asker in this location gets yes
-- until defeated. 3: no switch on the map: its spawn cancel does nothing until defeated (K.CANCEL_SKIPS).
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

-- Every asking thread that takes part in a King's chain: the King's own spawners (including every spawner of the
-- multi-spawner Kings), and the writers of the five indirect Kings. The thread index is u16 [thread] (stored at
-- 0x2665EB). Each pair answers for its own King alone: yes until that King is defeated, then the game's own answer,
-- so a defeated King stays out beside a living neighbour. Generated from kings-force-map.json, 2026-11-08; each
-- (location, Bestiary) pair exists in K.HOMES with presence 2. Threads outside the table (trophy sequences, ordinary
-- spawners that ask for their own gates) keep the location answer. Each map's trophy thread also asks, sets no unit,
-- and keeps the location answer.
K.SPAWNERS = {  -- {location, thread, Bestiary entry}
  {133, 31, 330},  -- Terror Tyrant (dst_c02)
  {137, 23, 329},  -- Grimalkin (pla_a01)
  {137, 24, 329},  -- Grimalkin (pla_a01)
  {137, 25, 329},  -- Grimalkin (pla_a01)
  {139, 25, 324},  -- Kris (pla_a03)
  {147, 22, 327},  -- Tarasque (pla_b04)
  {154, 19, 313},  -- Spee (frs_a04)
  {162, 24, 325},  -- Wood Toad (frs_c01)
  {163, 27, 321},  -- Rageclaw (frs_c02)
  {175, 26, 344},  -- Arioch (bog_b01)
  {177, 12, 356},  -- Velelu (bog_b03)
  {185, 32, 296},  -- Anubys (rui_a02)
  {192, 53, 362},  -- Wendice (rui_b01)
  {193, 18, 306},  -- Cubus (rui_b02)
  {210, 23, 303},  -- Wary Wolf (mnt_a02)
  {210, 24, 303},  -- Wary Wolf (mnt_a02)
  {210, 25, 303},  -- Wary Wolf (mnt_a02)
  {210, 26, 303},  -- Wary Wolf (mnt_a02)
  {217, 19, 326},  -- Dheed (mnt_b03)
  {228, 30, 316},  -- Ripe Rampager (dst_a02)
  {229, 21, 331},  -- Nekhbet (dst_a03)
  {230, 25, 297},  -- Greeden (dst_a04)
  {247, 41, 349},  -- Nazarnir (sav_c01)
  {254, 16, 315},  -- Rain Dancer (sav_d02)
  {256, 20, 315},  -- Rain Dancer (sav_d04)
  {266, 24, 315},  -- Rain Dancer (sav_f03)
  {313, 0, 317},  -- Razorfin (grm_a03)
  {346, 21, 334},  -- Dustia (wdl_a02)
  {346, 22, 305},  -- Kaiser Wolf (wdl_a02)
  {347, 28, 307},  -- Lindbur Wolf (wdl_a03)
  {348, 20, 346},  -- Fideliant (wdl_a04)
  {358, 21, 294},  -- Aerieel (lus_a02)
  {374, 29, 353},  -- Gemhorn (lus_d03)
  {380, 28, 308},  -- Pineapple (ene_a02)
  {385, 29, 372},  -- Urutan Exile (ene_a07)
  {387, 15, 361},  -- Bull Chocobo (ene_a09)
  {391, 28, 359},  -- Imdugud (enw_a02)
  {393, 28, 351},  -- Victanir (enw_a04)
  {398, 19, 312},  -- Megabomb (enw_b01)
  {410, 32, 298},  -- Barmuu (rwg_c01)
  {411, 29, 335},  -- Cultsworn Lich (rwg_c02)
  {412, 31, 352},  -- Zombie Lord (rwg_c03)
  {421, 19, 367},  -- Helvinek (bds_a01)
  {422, 26, 367},  -- Helvinek (bds_a02)
  {426, 30, 367},  -- Helvinek (bds_b01)
  {430, 25, 338},  -- Vorres (bds_c01)
  {430, 26, 367},  -- Helvinek (bds_c01)
  {431, 19, 367},  -- Helvinek (bds_c02)
  {447, 26, 347},  -- Midgardsormr (gol_a01)
  {448, 32, 323},  -- Biding Mantis (gol_a02)
  {453, 25, 350},  -- Grave Lord (gol_b01)
  {459, 21, 371},  -- Phyllo (gol_d01)
  {470, 22, 364},  -- Anchag (pbc_b03)
  {470, 23, 360},  -- Ancbolder (pbc_b03)
  {480, 21, 319},  -- Apsara (fon_a03)
  {482, 21, 363},  -- Skullash (fon_a05)
  {492, 29, 295},  -- Thalassinon (fon_c02)
  {498, 37, 366},  -- Bluesang (srb_a02)
  {505, 35, 293},  -- Aspidochelon (srb_b03)
  {506, 40, 302},  -- Etherian (srb_b04)
  {511, 21, 332},  -- Abelisk (rwf_a03)
  {517, 19, 369},  -- Pallicant (rwf_b01)
  {526, 23, 318},  -- Killbug (ozm_a02)
  {536, 48, 299},  -- Bull Croc (ozm_c01)
  {540, 32, 311},  -- Crypt Bunny (mfr_a02)
  {540, 33, 311},  -- Crypt Bunny (mfr_a02)
  {540, 34, 311},  -- Crypt Bunny (mfr_a02)
  {540, 35, 311},  -- Crypt Bunny (mfr_a02)
  {540, 36, 311},  -- Crypt Bunny (mfr_a02)
  {541, 0, 309},  -- Dreadguard (mfr_a03)
  {548, 27, 339},  -- Juggernaut (mfr_c01)
  {578, 25, 341},  -- Molen (ztc_a03)
  {581, 26, 370},  -- Alteci (ztc_b01)
  {598, 29, 314},  -- Matriarch Bomb (mrm_b02)
  {598, 30, 314},  -- Matriarch Bomb (mrm_b02)
  {598, 31, 314},  -- Matriarch Bomb (mrm_b02)
  {598, 32, 314},  -- Matriarch Bomb (mrm_b02)
  {606, 27, 365},  -- Myath (mrm_d01)
  {609, 21, 340},  -- Negalmuur (mrm_d04)
  {717, 27, 304},  -- Melt (hne_a06)
  {726, 23, 333},  -- Glaring Eye (hne_c02)
  {726, 24, 333},  -- Glaring Eye (hne_c02)
  {726, 25, 333},  -- Glaring Eye (hne_c02)
  {726, 26, 333},  -- Glaring Eye (hne_c02)
  {726, 27, 333},  -- Glaring Eye (hne_c02)
  {728, 24, 333},  -- Glaring Eye (hne_c04)
  {728, 25, 333},  -- Glaring Eye (hne_c04)
  {728, 26, 333},  -- Glaring Eye (hne_c04)
  {728, 27, 333},  -- Glaring Eye (hne_c04)
  {728, 28, 333},  -- Glaring Eye (hne_c04)
  {844, 23, 301},  -- Gavial (grm_e07)
  {849, 22, 354},  -- Drowned (grm_e12)
  {891, 18, 320},  -- Minibug (mic_e01)
  {892, 39, 336},  -- Ishteen (mic_e02)
  {895, 16, 322},  -- Ithuno (mic_e05)
  {901, 12, 310},  -- Bombshell (lus_f01)
  {902, 35, 358},  -- Disma (lus_f02)
  {902, 36, 358},  -- Disma (lus_f02)
  {902, 37, 358},  -- Disma (lus_f02)
  {902, 38, 358},  -- Disma (lus_f02)
  {903, 17, 358},  -- Disma (lus_f03)
  {903, 18, 358},  -- Disma (lus_f03)
  {903, 19, 358},  -- Disma (lus_f03)
  {903, 20, 358},  -- Disma (lus_f03)
  {903, 21, 358},  -- Disma (lus_f03)
  {939, 24, 342},  -- Larva Eater (gil_i01)
  {940, 32, 342},  -- Larva Eater (gil_i02)
  {940, 33, 348},  -- Crystal Knight (gil_i02)
  {947, 24, 337},  -- Evil Spirit (gil_i09)
  {947, 25, 337},  -- Evil Spirit (gil_i09)
  {947, 26, 337},  -- Evil Spirit (gil_i09)
  {947, 27, 337},  -- Evil Spirit (gil_i09)
  {947, 28, 337},  -- Evil Spirit (gil_i09)
  {947, 29, 337},  -- Evil Spirit (gil_i09)
  {947, 30, 337},  -- Evil Spirit (gil_i09)
  {947, 31, 337},  -- Evil Spirit (gil_i09)
  {947, 32, 337},  -- Evil Spirit (gil_i09)
  {947, 33, 337},  -- Evil Spirit (gil_i09)
  {947, 34, 337},  -- Evil Spirit (gil_i09)
  {947, 35, 337},  -- Evil Spirit (gil_i09)
  {952, 24, 342},  -- Larva Eater (gil_j01)
  {953, 24, 342},  -- Larva Eater (gil_j02)
  {971, 24, 342},  -- Larva Eater (gil_j20)
  {972, 24, 337},  -- Evil Spirit (gil_j21)
  {972, 25, 337},  -- Evil Spirit (gil_j21)
  {972, 26, 337},  -- Evil Spirit (gil_j21)
  {972, 27, 337},  -- Evil Spirit (gil_j21)
  {972, 28, 337},  -- Evil Spirit (gil_j21)
  {972, 29, 337},  -- Evil Spirit (gil_j21)
  {972, 30, 337},  -- Evil Spirit (gil_j21)
  {972, 31, 337},  -- Evil Spirit (gil_j21)
  {972, 32, 337},  -- Evil Spirit (gil_j21)
  {972, 33, 337},  -- Evil Spirit (gil_j21)
  {972, 34, 337},  -- Evil Spirit (gil_j21)
  {972, 35, 337},  -- Evil Spirit (gil_j21)
  {973, 24, 337},  -- Evil Spirit (gil_j22)
  {973, 25, 337},  -- Evil Spirit (gil_j22)
  {973, 26, 337},  -- Evil Spirit (gil_j22)
  {973, 27, 337},  -- Evil Spirit (gil_j22)
  {973, 28, 337},  -- Evil Spirit (gil_j22)
  {973, 29, 337},  -- Evil Spirit (gil_j22)
  {973, 30, 337},  -- Evil Spirit (gil_j22)
  {973, 31, 337},  -- Evil Spirit (gil_j22)
  {973, 32, 337},  -- Evil Spirit (gil_j22)
  {973, 33, 337},  -- Evil Spirit (gil_j22)
  {973, 34, 337},  -- Evil Spirit (gil_j22)
  {973, 35, 337},  -- Evil Spirit (gil_j22)
  {979, 24, 342},  -- Larva Eater (gil_k01)
  {984, 27, 337},  -- Evil Spirit (gil_k06)
  {984, 28, 337},  -- Evil Spirit (gil_k06)
  {984, 29, 337},  -- Evil Spirit (gil_k06)
  {984, 30, 337},  -- Evil Spirit (gil_k06)
  {984, 31, 337},  -- Evil Spirit (gil_k06)
  {984, 32, 337},  -- Evil Spirit (gil_k06)
  {984, 33, 337},  -- Evil Spirit (gil_k06)
  {986, 24, 337},  -- Evil Spirit (gil_k08)
  {986, 25, 337},  -- Evil Spirit (gil_k08)
  {986, 26, 337},  -- Evil Spirit (gil_k08)
  {986, 27, 337},  -- Evil Spirit (gil_k08)
  {986, 28, 337},  -- Evil Spirit (gil_k08)
  {986, 29, 337},  -- Evil Spirit (gil_k08)
  {986, 30, 337},  -- Evil Spirit (gil_k08)
  {986, 31, 337},  -- Evil Spirit (gil_k08)
  {996, 24, 342},  -- Larva Eater (gil_k18)
  {1002, 24, 337},  -- Evil Spirit (gil_l01)
  {1002, 25, 337},  -- Evil Spirit (gil_l01)
  {1002, 26, 337},  -- Evil Spirit (gil_l01)
  {1002, 27, 337},  -- Evil Spirit (gil_l01)
  {1002, 28, 337},  -- Evil Spirit (gil_l01)
  {1002, 29, 337},  -- Evil Spirit (gil_l01)
  {1002, 30, 337},  -- Evil Spirit (gil_l01)
  {1002, 31, 337},  -- Evil Spirit (gil_l01)
  {1004, 24, 337},  -- Evil Spirit (gil_l03)
  {1004, 25, 337},  -- Evil Spirit (gil_l03)
  {1004, 26, 337},  -- Evil Spirit (gil_l03)
  {1004, 27, 337},  -- Evil Spirit (gil_l03)
  {1004, 28, 337},  -- Evil Spirit (gil_l03)
  {1004, 29, 337},  -- Evil Spirit (gil_l03)
  {1004, 30, 337},  -- Evil Spirit (gil_l03)
  {1004, 31, 337},  -- Evil Spirit (gil_l03)
  {1005, 24, 337},  -- Evil Spirit (gil_l04)
  {1005, 25, 337},  -- Evil Spirit (gil_l04)
  {1005, 26, 337},  -- Evil Spirit (gil_l04)
  {1005, 27, 337},  -- Evil Spirit (gil_l04)
  {1005, 28, 337},  -- Evil Spirit (gil_l04)
  {1005, 29, 337},  -- Evil Spirit (gil_l04)
  {1005, 30, 337},  -- Evil Spirit (gil_l04)
  {1005, 31, 337},  -- Evil Spirit (gil_l04)
  {1119, 21, 300},  -- Vishno (rbl_e02)
  {1128, 41, 355},  -- Luxollid (rbl_h03)
  {1130, 44, 357},  -- Vagrant Soul (rbl_i02)
  {1138, 31, 368},  -- Avenger (rbl_m01)
  {1141, 56, 343},  -- Tower (rbl_n02)
}

-- The presence-3 pairs: spawner threads whose cancel is skipped while their King is undefeated. Grey Molter is the
-- only such King; its thread never asks the switch, so it cannot appear in K.SPAWNERS.
K.CANCEL_SKIPS = {  -- {location, thread, Bestiary entry}
  {216, 20, 345},  -- Grey Molter: the only presence-3 King
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

-- Validates the tables: every entry a King, every location a plain id, no duplicates, every pair a home,
-- cancel skips only presence-3 homes, and the block big enough for all three tables.
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
  local presence3 = {}
  for _, h in ipairs(homes) do
    if h[3] == 3 then presence3[h[1] * 1000 + h[2]] = true end
  end
  local seenThreads, after = {}, K.SPAWNER_AT + #K.SPAWNERS * K.ENTRY_SIZE
  for i, s in ipairs(K.SPAWNERS) do
    if homes == K.HOMES and not seen[s[1] * 1000 + s[3]] then return false, 'spawner without a home ' .. i end
    local key = s[1] * 0x10000 + s[2]
    if seenThreads[key] then return false, 'duplicate spawner ' .. i end
    seenThreads[key] = true
  end
  for i, s in ipairs(K.CANCEL_SKIPS) do
    if homes == K.HOMES and not seen[s[1] * 1000 + s[3]] then return false, 'cancel skip without a home ' .. i end
    if homes == K.HOMES and not presence3[s[1] * 1000 + s[3]] then return false, 'cancel skip not presence 3 ' .. i end
    local key = s[1] * 0x10000 + s[2]
    if seenThreads[key] then return false, 'duplicate cancel skip ' .. i end
    seenThreads[key] = true
  end
  if after + #K.CANCEL_SKIPS * K.ENTRY_SIZE > K.DATA_SIZE then return false, 'too many spawners' end
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
  put(K.CANCEL_COUNT, #K.CANCEL_SKIPS, 4)
  for i, s in ipairs(K.CANCEL_SKIPS) do
    local at = K.SPAWNER_AT + #K.SPAWNERS * K.ENTRY_SIZE + (i - 1) * K.ENTRY_SIZE
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
  movzx r11d,word ptr [rdx]
  mov ecx,0x021654C4
  mov eax,[rcx]
  mov r9d,[r8+0x24]
  shl r9d,3
  lea r10,[r8+0x600]
  add r10,r9
  mov r9d,[r8+0x2C]
legacy_kings_cancel_loop:
  test r9d,r9d
  je legacy_kings_cancel_run
  cmp dword ptr [r10],eax
  jne legacy_kings_cancel_next
  cmp word ptr [r10+0x04],r11w
  jne legacy_kings_cancel_next
  movzx ecx,word ptr [r10+0x06]
  mov r10d,0x0216F883
  cmp byte ptr [r10+rcx],0x01
  ja legacy_kings_cancel_run
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
-- It keys on the thread's own index (u16 [rdx], stored at 0x2665EB) and the live location - the same identity the rare
-- hook uses - because a spawner that has never entered has no unit flag and no class row yet (2026-11-07 RE study, Q3).
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
