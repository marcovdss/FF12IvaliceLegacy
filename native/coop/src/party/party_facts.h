// Ivalice Legacy co-op module: game facts the party subsystem reads, writes or calls (FFXII_TZA.exe 1.0.4.0).
//
// Globals and functions are VAs of the pinned build (the image base is fixed at 0x120000); the co-op thread and the
// game-thread code relocate them to the image base they were given (a host test passes a synthetic image). Offsets
// inside game objects are plain numbers.
#pragma once

#include <stdint.h>

namespace ilc::party::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;              // VA: the fixed image base

// Party block (the battle state): layouts, leader, mode bits.
constexpr uintptr_t PARTY_BLOCK_PTR = 0x2EBF190;      // VA: pointer to the party block
constexpr uint32_t LAYOUT_DEFAULT = 0x5A48;           // nine u16 party ids: the roster (menus read it; towns never
                                                      // filter it)
constexpr uint32_t LAYOUT_CURRENT = 0x5A5A;           // nine u16 party ids: the members that have a body
constexpr uint32_t LAYOUT_UI = 0x5A6C;                // the HUD's copy
constexpr uint32_t LEADER = 0x5AA4;                   // u32 leader party id (the engine reads the low byte)
constexpr uint32_t MODE_BITS = 0x5B04;                // u32: bit 0 summon, bit 1 chocobo, bits 2/3 scripted modes
constexpr uint32_t MODE_BLOCKING = 0x0F;              // any of them: no leader change or swap now
constexpr uint32_t SLOTS = 4;                         // active party slots
constexpr uint16_t EMPTY = 0xFFFF;
// Member records, one per party id, whether or not the member has a body: block + MEMBER_RECORDS + id * size. A
// bound actor's +0x698 points at its member's record (the battle record below).
constexpr uint32_t MEMBER_RECORDS = 0x08;
constexpr uint32_t MEMBER_RECORD_SIZE = 0x1C8;

// The leader setter: void(u8 member in cl). It writes LEADER without any check and refreshes the controlled actor
// as a pending handle, which the main update applies once at the end of the same frame.
constexpr uintptr_t LEADER_SETTER = 0x327850;         // VA
// Field ready: int(void), non-zero while the field accepts player control.
constexpr uintptr_t FIELD_READY = 0x265680;           // VA

// Frame state checked before the setter runs.
constexpr uintptr_t PENDING_AREA = 0x1EED064;         // VA: i32, -1 = no area change requested
constexpr uintptr_t LOCK_MASK = 0x22CB720;            // VA: u32 control lock bits
constexpr uint32_t LOCK_TRANSITION = 1u << 12;        // an area transition is in progress
constexpr uint32_t LOCK_ROSTER_EDIT = 1u << 19;       // a script is adding or removing a party member
constexpr uintptr_t FRONTEND_OPEN = 0x2092734;        // VA: u32, non-zero while a frontend menu is open
constexpr uintptr_t PENDING_HANDLE_FLAG = 0x22C7FF8;  // VA: u8, a controlled-actor change waits for the frame end
constexpr uintptr_t LOCATION_ID = 0x21654C4;          // VA: u32 current location id
constexpr uintptr_t LOCATION_FLAGS = 0x21654E2;       // VA: u8 location flags
constexpr uint8_t LOCATION_RELOAD = 0x20;             // set while the current area is being reloaded

// Actor and battle record.
constexpr uint32_t ACTOR_FLAGS = 0x00;                // u64; bit 46 = an execution motion plays
constexpr uint64_t ACTOR_EXECUTION_BIT = 1ull << 46;  // NOTADDR
constexpr uint32_t ACTOR_HANDLE = 0x08;               // u32
constexpr uint32_t ACTOR_ROOT = 0x10;                 // pointer to the scene root
constexpr uint32_t ACTOR_BATTLE = 0x698;              // pointer to the battle record
constexpr uint32_t ROOT_ACTOR = 0x30;                 // pointer back to the actor
constexpr uint32_t ROOT_PHYSICS = 0xB8;               // pointer to the physics object
constexpr uint32_t PHYS_HEADING = 0xA4;               // float: commanded heading
constexpr uint32_t PHYS_VISUAL = 0xA8;                // float: the body's facing
constexpr uint32_t BATTLE_HP = 0x48;                  // u32
constexpr uint32_t BATTLE_STATUS_LASTING = 0x3C;      // u32
constexpr uint32_t BATTLE_STATUS_TIMED = 0x64;        // u32
constexpr uint32_t STATUS_CANNOT_LEAD = 0x80000003u;  // NOTADDR: KO, Stone, X-Zone (the leader-cycle skips them)

}  // namespace ilc::party::facts
