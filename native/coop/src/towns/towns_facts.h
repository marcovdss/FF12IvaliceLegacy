// Ivalice Legacy co-op module: the game facts the town visibility feature reads (FFXII_TZA.exe 1.0.4.0, fixed base).
//
// Globals are VAs of the pinned build: the co-op thread's reader relocates them to the image base it was given (a
// host test passes a synthetic image); the game-thread handler and operation use the pinned base directly. Offsets
// inside game objects are plain numbers.
#pragma once

#include <stdint.h>

namespace ilc::towns::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;            // VA: the fixed image base

// Party block and its layouts. At TW-01 the layout builder holds rdi = party block + LAYOUTS.
constexpr uintptr_t PARTY_BLOCK_PTR = 0x2EBF190;    // VA: pointer to the party block
constexpr uint32_t LAYOUTS = 0x5A48;                // nine-slot u16 layouts, 0x12 bytes apart
constexpr uint32_t L_DEFAULT = 0x00;                // the full roster (menus read it, never filtered)
constexpr uint32_t L_CURRENT = 0x12;                // which member each slot actor shows
constexpr uint32_t L_UI = 0x24;                     // the HUD's copy of current
constexpr uint32_t L_LEADER = 0x5C;                 // u8 leader party id (0xFF none); the engine stores a dword
constexpr uint32_t L_SPAN = 0x60;                   // bytes from L_DEFAULT up to and including the leader byte
constexpr uint32_t MODE_BITS = 0x5B04;              // u32 party mode flags
constexpr uint32_t MODE_SPECIAL = 0x0C;             // bits 2/3: the two scripted single-body modes (field modes 4/5)
constexpr uint32_t SLOTS = 4;                       // active party slots (slot actors)
constexpr uint16_t EMPTY = 0xFFFF;
constexpr uint8_t NO_LEADER = 0xFF;

// The engine's town predicate (0x377C20): location id, then the town bit of that area's entry.
constexpr uintptr_t LOCATION_ID = 0x21654C4;        // VA: u32 current location id (negative reads as 0)
constexpr uintptr_t AREA_TABLE_PTR = 0x2ADD100;     // VA: pointer to the area table
constexpr uint32_t AREA_COUNT = 0x04;               // u32 entry count
constexpr uint32_t AREA_ENTRY_SIZE = 3;
constexpr uint32_t AREA_TOWN_BYTE = 0x09;           // byte table + 9 + 3 * id ...
constexpr uint8_t AREA_TOWN_BIT = 0x80;             // ... bit 7 = town

// Party re-materialize: 0x37CA80() stores a new streaming task in PARTY_TASK_PTR (unconditionally) and rebuilds and
// binds the layout at once. No arguments. If the task pool is full it does nothing and the pointer stays 0.
constexpr uintptr_t REMATERIALIZE = 0x37CA80;       // VA
constexpr uintptr_t PARTY_TASK_PTR = 0x2EBF400;     // VA: non-zero while a re-materialize task runs

// The town arm of the layout builder's mode switch. Stock bytes mean nothing rewrote it (no Companions "Party In
// Towns" patch): the stock town collapse leaves one body, so guests cannot get one and rebuilds are pointless.
constexpr uintptr_t TOWN_ARM = 0x326768;            // VA
constexpr uint32_t TOWN_ARM_LEN = 9;

}  // namespace ilc::towns::facts
