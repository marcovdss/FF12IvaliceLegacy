// Ivalice Legacy co-op module: game facts the HUD snapshot sampler and the frame driver read (FFXII_TZA.exe 1.0.4.0,
// VAs at the pinned base 0x120000). Read only: nothing here is ever written.
#pragma once

#include <stdint.h>

namespace ilc::hud::snapfacts {

constexpr uint64_t PINNED_BASE = 0x120000;

// ---- the frame driver -----------------------------------------------------------------------------------------------
constexpr uint64_t RENDER_FLAGS = 0x22C7F90;   // VA: u32; bit 3 = the game draws its UI (clear = UI hidden)
constexpr uint32_t RENDER_UI = 1u << 3;

// ---- battle state: party layouts, leader, member records ---------------------------------------------------------
constexpr uint64_t BATTLE_STATE_PTR = 0x2EBF190;   // VA: pointer to the battle state
constexpr uint32_t LAYOUT_TABLE = 0x5A48;          // four layouts of 9 u16 party ids each
constexpr uint32_t LAYOUT_STRIDE = 0x12;
constexpr uint32_t LAYOUT_HUD = 2;                 // the party the HUD shows, in row order (= state + 0x5A6C)
constexpr uint32_t LEADER_MEMBER = 0x5AA4;         // u32 party id of the controlled leader (0xFFFF none)
constexpr uint32_t MEMBER_RECORDS = 0x08;          // state + 0x08 + id * 0x1C8 (ids 0..0x27)
constexpr uint32_t MEMBER_RECORD_SIZE = 0x1C8;
constexpr uint32_t MEMBER_IDS = 0x28;
constexpr uint16_t MEMBER_NONE = 0xFFFF;

// ---- a battle record (party member or foe; actor + 0x698 points at it) ---------------------------------------------
constexpr uint32_t REC_HP_MAX = 0x24;              // u32
constexpr uint32_t REC_STATUS_LASTING = 0x3C;      // u32 status bits (no countdown)
constexpr uint32_t REC_WEAKNESS = 0x40;            // u8 element-weakness mask
constexpr uint32_t REC_HP = 0x48;                  // u32
constexpr uint32_t REC_STATUS_TIMED = 0x64;        // u32 status bits (temporary: they count down)
constexpr uint32_t REC_AUGMENTS_TIMED = 0x68;      // 12 bytes, augment n = byte n >> 3, bit n & 7
constexpr uint32_t REC_AUGMENTS_LASTING = 0x78;    // 12 bytes
constexpr uint32_t REC_DURATIONS = 0xBC;           // s32[32]: remaining ticks per status id (bit order of the masks)
constexpr uint32_t REC_LEVEL = 0x1C2;              // u8
constexpr uint32_t ANTI_LIBRA_BYTE = 5;            // augment byte 5, mask 0x02: Anti-Libra (the native test reads it)
constexpr uint8_t ANTI_LIBRA_MASK = 0x02;
constexpr uint32_t STATUS_LIBRA = 1u << 30;        // the Libra status (a party member with it reveals foe details)
constexpr uint32_t STATUS_IDS = 32;
constexpr uint32_t SAMPLE_TRIES = 3;               // a record whose masks or HP move under the read is read again

// ---- actors -------------------------------------------------------------------------------------------------------
constexpr uint32_t ACTOR_HANDLE = 0x08;            // u32
constexpr uint32_t ACTOR_UNIT = 0x10;              // pointer to the unit block
constexpr uint32_t UNIT_TYPE = 0x0E;               // u8, low nibble
constexpr uint32_t ACTOR_BATTLE = 0x698;           // pointer to the battle record
constexpr uint32_t ACTOR_TARGET = 0x710;           // u32 handle of the actor's current target (0 none)

// ---- the native boss gauge (couch layout: the corner headers move down while it shows) ----------------------------
// [VA] -> manager; manager + 0xC0 -> owner; owner + 0xC8 / + 0xD0 -> the gauge's two UI objects; an object's u32 at
// +0x130 has bit 0x10 while it is drawn. Read only (the same chain the reverse-engineered HUD notes record).
constexpr uint64_t BOSS_GAUGE_MANAGER = 0x2B62D88;  // VA
constexpr uint32_t BOSS_GAUGE_OWNER = 0xC0;
constexpr uint32_t BOSS_GAUGE_OBJECT_A = 0xC8;
constexpr uint32_t BOSS_GAUGE_OBJECT_B = 0xD0;
constexpr uint32_t BOSS_GAUGE_FLAGS = 0x130;
constexpr uint32_t BOSS_GAUGE_SHOWN = 0x10;         // NOTADDR: render flag bit

// ---- status timing --------------------------------------------------------------------------------------------------
// u32: 1, or 2 once the 60 fps timing is on. A timed status loses 10 ticks per unit tick, so seconds are about
// ticks / (300 * scale).
constexpr uint64_t TICK_SCALE = 0x1DFE0B4;         // VA

}  // namespace ilc::hud::snapfacts
