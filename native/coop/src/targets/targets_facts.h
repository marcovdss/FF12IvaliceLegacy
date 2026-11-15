// Ivalice Legacy co-op module: game facts behind a guest's target picker (FFXII_TZA.exe 1.0.4.0, fixed image base).
//
// Globals are VAs of the pinned build; readers relocate them to the image base they were given. Offsets inside game
// objects are plain numbers. Everything here is read-only.
#pragma once

#include <stdint.h>

namespace ilc::targets::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;               // VA: the fixed image base

// Actor manager: u32 count, then one entry pointer per actor index; the entry's +0x30 is the actor.
constexpr uintptr_t ACTOR_MANAGER_PTR = 0x2098E18;     // VA: pointer to the actor manager
constexpr uint32_t MANAGER_COUNT = 0x00;
constexpr uint32_t MANAGER_ENTRIES = 0x08;
constexpr uint32_t MANAGER_COUNT_MAX = 4096;
constexpr uint32_t ENTRY_ACTOR = 0x30;

// Actor.
constexpr uint32_t ACTOR_FLAGS = 0x00;                 // u64: bit 0 active, bit 1 hidden
constexpr uint64_t ACTOR_ACTIVE = 0x01;
constexpr uint64_t ACTOR_HIDDEN = 0x02;
constexpr uint32_t ACTOR_HANDLE = 0x08;                // u32: generation, registry and index (index = low 16 bits)
constexpr uint32_t ACTOR_UNIT = 0x10;                  // pointer to the actor's unit block
constexpr uint32_t ACTOR_POSITION = 0xD0;              // float[3]
constexpr uint32_t ACTOR_BATTLE = 0x698;               // pointer to the battle record
constexpr uint32_t ACTOR_BATTLE_EXT = 0x6A0;           // pointer to the extended battle block (u8 flags at +0)
// Unit block.
constexpr uint32_t UNIT_TYPE = 0x0E;                   // u8, low nibble
constexpr uint32_t UNIT_NAME = 0xF8;                   // pointer to the name text (shared by every copy of a foe)
constexpr uint32_t UNIT_INSTANCE = 0x100;              // u32, low 16 bits: the game's own copy letter (1 = A)
// Extended battle block flags that make an actor untargetable: despawned, dead, inactive, inactive-untargetable.
constexpr uint8_t EXT_BLOCKED = 0x08 | 0x10 | 0x20 | 0x40;
// Battle record.
constexpr uint32_t BATTLE_HP = 0x48;                   // u32
constexpr uint32_t BATTLE_STATUS_LASTING = 0x3C;       // u32
constexpr uint32_t BATTLE_STATUS_TIMED = 0x64;         // u32

// Unit types: 1, 4 (mimics) and 7 (hunts) are attackable foes; 3 is a non-hostile unit (the Neutrals group).
constexpr uint8_t UNIT_FOE = 1;
constexpr uint8_t UNIT_MIMIC = 4;
constexpr uint8_t UNIT_HUNT = 7;
constexpr uint8_t UNIT_NEUTRAL = 3;

// The native target range: horizontal (X/Z) distance, boundary included.
constexpr float TARGET_RANGE = 24.0f;

// Party layouts in the battle state (9 u16 party ids each): 0 default, 1 current, 2 HUD, 3 menu. Default and menu
// keep the reserve members in slots 4..8.
constexpr uintptr_t BATTLE_STATE_PTR = 0x2EBF190;      // VA
constexpr uint32_t LAYOUT_TABLE = 0x5A48;
constexpr uint32_t LAYOUT_STRIDE = 0x12;
constexpr uint32_t LAYOUT_ENTRIES = 9;
constexpr uint32_t LAYOUT_DEFAULT = 0;
constexpr uint32_t LAYOUT_CURRENT = 1;
constexpr uint32_t LAYOUT_HUD = 2;
constexpr uint32_t LAYOUT_MENU = 3;
constexpr uint16_t MEMBER_NONE = 0xFFFF;
constexpr uint16_t ESPER_FIRST = 27;                   // party ids 27..39: Espers (temporary, never reserve)
constexpr uint16_t ESPER_LAST = 39;

// Statuses.
constexpr uint32_t ST_KO = 1u << 0;
constexpr uint32_t ST_STONE = 1u << 1;

}  // namespace ilc::targets::facts
