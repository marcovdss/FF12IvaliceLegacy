// Ivalice Legacy co-op module: the game facts guest interactions use (FFXII_TZA.exe 1.0.4.0, fixed image base).
//
// Globals and code addresses are VAs of the pinned build (the image base is fixed at 0x120000; the co-op thread's
// reader relocates globals to the image base it was given, so host tests can pass a synthetic image). Offsets
// inside game objects are plain numbers. These are facts about the game, nothing of ours.
#pragma once

#include <stdint.h>

namespace ilc::interaction::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;               // VA: the fixed image base

// ---- interaction state the field tick rebuilds every frame -------------------------------------------------------
constexpr uintptr_t SOURCE = 0x2099D78;                // VA: entity the scans measure from (the controlled actor's)
constexpr uintptr_t CAN_USE = 0x209A2AA;               // VA: u8, 1 once a scan published a usable target
constexpr uintptr_t DESCRIPTOR = 0x209A2B0;            // VA: 24 bytes, the one interaction target (below)
constexpr uint32_t DESCRIPTOR_SIZE = 24;
constexpr uint32_t D_DISTANCE = 0x00;                  // f32
constexpr uint32_t D_REGISTRY = 0x04;                  // i32, 0..4
constexpr uint32_t D_INDEX = 0x08;                     // i32, entry index inside the registry
constexpr uint32_t D_STATE = 0x0C;                     // u32, 2 = talk
constexpr uint32_t D_HANDLE = 0x10;                    // u32
constexpr uint32_t D_READY = 0x14;                     // four bytes: trigger + three permissions, bit 0 each
constexpr uintptr_t TRIGGER = DESCRIPTOR + D_READY;    // VA: u8, the confirm trigger byte
constexpr uint32_t READY_BITS = 0x01010101;            // NOTADDR: bit 0 of each of the four ready bytes
constexpr uint32_t FORCED_DISTANCE_BITS = 0x3F1999AC;  // NOTADDR: f32 0.6, a close candidate
constexpr uint32_t RESET_DISTANCE_BITS = 0x501502F9;   // NOTADDR: f32 1e10, the scans' "nothing yet"
constexpr uint32_t MINIMUM_STATE = 2;                  // a target with no better state is used as talk/use
constexpr uint32_t MIMIC_STATE = 10;                   // a sleeping mimic is woken with this state
constexpr uint32_t TALK_STATE = 2;

// The scan publication table the prompt reads: 8 slots, written by the publisher 0x3DF760.
constexpr uint32_t PUBLISH_SLOTS = 8;

// ---- registries (five object/actor tables) -----------------------------------------------------------------------
constexpr uintptr_t REGISTRY_BASE = 0x2098E10;         // VA: registry 0 (the actor manager is its +8)
constexpr uint32_t REGISTRY_STRIDE = 0x288;
constexpr uint32_t REGISTRIES = 5;
constexpr uint32_t REG_HEAD = 0x00;                    // qword, non-zero while the registry exists
constexpr uint32_t REG_TABLE = 0x08;                   // pointer to {u32 count, entry pointers from +8}
constexpr uint32_t REG_FLAGS = 0x10;                   // u8; bit 0 = enabled
constexpr uint32_t REG_LIMIT = 0x20;                   // i32: indices below this are valid for handles
constexpr uint32_t TABLE_COUNT = 0x00;
constexpr uint32_t TABLE_ENTRIES = 0x08;

// Handles: index in bits 0..15, registry in 16..19, generation in 20..30.
constexpr uint32_t HANDLE_INDEX_MASK = 0xFFFF;
constexpr uint32_t HANDLE_REGISTRY_SHIFT = 16;
constexpr uint32_t HANDLE_GENERATION_SHIFT = 20;
constexpr uint32_t HANDLE_GENERATION_MASK = 0x7FF;

// Registry entry (an actor's manager entry, an NPC, a door, a chest, a crystal).
constexpr uint32_t E_TALK = 0x0B;                      // u8; 0x08 while in conversation (set at talk start only)
constexpr uint8_t E_TALK_ACTIVE = 0x08;
constexpr uint32_t E_RANGE = 0x0C;                     // u8; bit 1 = in range of the scan source this frame
constexpr uint8_t E_RANGE_BIT = 0x02;
constexpr uint32_t E_UNIT_TYPE = 0x0E;                 // u8; low nibble = unit type (4 = mimic)
constexpr uint32_t E_GENERATION = 0x16;                // u16
constexpr uint32_t E_TYPE = 0x18;                      // u8; 1 = no script path in the consumer
constexpr uint32_t E_FLAGS = 0x1C;                     // u32 interaction flags
constexpr uint32_t E_FLAG_TALK = 0x004;
constexpr uint32_t E_FLAG_OTHER = 0x400;
constexpr uint32_t E_FLAG_AUX = 0x2000;
constexpr uint32_t E_ACTOR = 0x30;                     // pointer: the actor (actor manager entries)
constexpr uint32_t E_SCRIPT = 0x40;                    // pointer the consumer reads for a non-type-1 target
constexpr uint32_t E_SCRIPT_TABLE = 0x48;              // pointer the script request reads
constexpr uint32_t E_SCRIPT_CONTEXT = 0x60;            // u8; 0xFF = no script context (the request is refused)
constexpr uint32_t E_PHYSICS = 0xB8;                   // pointer the exact NPC scan reads from its source
constexpr uint32_t E_SKELETON_HOST = 0xC0;             // pointer, +0x138 = the skeleton keep
constexpr uint32_t SKELETON_KEEP = 0x138;
constexpr uint32_t E_NAME_KEY = 0x102;                 // i16; 434 = a treasure chest
constexpr int16_t TREASURE_NAME_KEY = 434;
constexpr uint8_t MIMIC_UNIT_TYPE = 4;
constexpr uint32_t ACTOR_EXTENDED = 0x6A0;             // pointer; bit 5 of its first byte = a mimic asleep
constexpr uint8_t MIMIC_ASLEEP = 0x20;

// Actor (for the press rules).
constexpr uint32_t ACTOR_FLAGS = 0x00;                 // u64
constexpr uint64_t ACTOR_WEAPON_HANDLING = 1ull << 24; // NOTADDR: drawing or sheathing a weapon
constexpr uint64_t ACTOR_EXECUTION = 1ull << 46;       // NOTADDR: an execution motion plays
constexpr uint32_t ACTOR_HANDLE = 0x08;                // u32
constexpr uint32_t ACTOR_STATE = 0x6B4;                // u32 (0/1 = idle or moving)
constexpr uint32_t ACTOR_ACTION = 0x714;               // u32 (0xFFFF none, 0x4021 idle)
constexpr uint32_t ACTION_NONE = 0xFFFF;
constexpr uint32_t ACTION_IDLE = 0x4021;

// ---- world state the window and the hold read --------------------------------------------------------------------
constexpr uintptr_t CONTROLLED_HANDLE = 0x22C7FE0;     // VA: u32 handle of the actor the game controls
constexpr uintptr_t PENDING_AREA = 0x1EED064;          // VA: i32, -1 = no area change pending
constexpr uintptr_t LOCK_MASK = 0x22CB720;             // VA: u32; bit 12 = area transition in progress
constexpr uint32_t LOCK_TRANSITION = 1u << 12;
constexpr uintptr_t RENDER_FLAGS = 0x22C7F90;          // VA: u32; bit 3 = native UI rendering enabled
constexpr uint32_t RENDER_UI = 1u << 3;
constexpr uintptr_t SUPPRESSION_FLAGS = 0x2089340;     // VA: u32; 0xF00 = interactions suppressed
constexpr uint32_t SUPPRESSION_MASK = 0xF00;
constexpr uintptr_t EVENT_FLAGS = 0x1FD46D4;           // VA: u32; bit 10 = an event is busy
constexpr uint32_t EVENT_BUSY = 1u << 10;
constexpr uintptr_t FRONTEND_ID = 0x2092730;           // VA: u32 id of the open full-screen frontend (10 = shop)
constexpr uintptr_t FRONTEND_ACTIVE = 0x2092734;       // VA: u32, 1 while a frontend is open
constexpr uint32_t FRONTEND_SHOP = 10;
constexpr uintptr_t MENU_QUEUED = 0x215F1A0;           // VA: u8, 1 while a menu request waits for the field tick

// ---- game functions (VA), all on the game thread at a call boundary ----------------------------------------------
constexpr uintptr_t FN_RESOLVE = 0x3588B0;             // entry* (u32 handle): generation-checked, 0 if gone
constexpr uintptr_t FN_GENERIC_SCAN = 0x25C170;        // void (i32 registry): doors, chests, crystals
constexpr uintptr_t FN_EXACT_SCAN = 0x25B820;          // void (i32 registry): NPC talk candidates
constexpr uintptr_t FN_BUILD_HANDLE = 0x268650;        // void (): descriptor handle from registry and index
constexpr uintptr_t FN_CONSUMER = 0x268D00;            // void (): start the descriptor's interaction
constexpr uintptr_t FN_GATE = 0x26DFB0;                // int (i32 mode): the consumer's controlled-actor gate
constexpr uintptr_t FN_AUXILIARY = 0x263990;           // void* (entry*): sub-object for targets with E_FLAG_AUX
constexpr uintptr_t FN_GATEWAY = 0x25B600;             // void (): trigger volume pass for the controlled handle

// ---- continuation addresses (VA) inside the hooked functions -----------------------------------------------------
constexpr uintptr_t PUBLISHER_RET = 0x3DF78F;          // the publisher's own `ret`
constexpr uintptr_t SOURCE_AFTER = 0x25C89F;           // after `mov rax,[source]` in the object update
constexpr uintptr_t PROXIMITY_AFTER = 0x25C283;        // after `mov rsi,[source]` in the actor update
constexpr uintptr_t ENABLE_ACCEPT = 0x25C930;
constexpr uintptr_t INNER_ACCEPT = 0x25D04A;
constexpr uintptr_t OUTER_ACCEPT = 0x25D117;
constexpr uintptr_t BROADPHASE_ACCEPT = 0x25CC7D;
constexpr uintptr_t PROXIMITY_ACCEPT = 0x25C3F0;
constexpr uintptr_t PROXIMITY_REJECT = 0x25C6B9;
constexpr uintptr_t SECONDARY_ACCEPT = 0x25C509;
constexpr uintptr_t SECONDARY_REJECT = 0x25C6A1;
constexpr uint32_t SOURCE_FRAME = 0x71;                // [rbp-0x71] = the scan source at the range gates
constexpr uint32_t RANGE_FLAGS = 0x5D;                 // [rsi+0x5D] range bits at the range gates
constexpr uint8_t RANGE_INNER = 0x04;
constexpr uint8_t RANGE_OUTER = 0x02;
constexpr uintptr_t NPC_ENTRY_AFTER = 0x25BAF9;        // after `and [rdx+0xC],0xFD; mov rdi,rdx`
constexpr uintptr_t NPC_ACCEPT_OUT_OF_RANGE = 0x25BC8A;// the distance bias, then the out-of-range branch (al = 0)
constexpr uintptr_t NPC_CLEANUP_SKIP = 0x25BE0B;       // past the proximity cleanup
constexpr uint32_t PUBLISHER_TEXT_ARG = 0x28;          // [rsp+0x28] at the publisher's entry: the fifth argument

}  // namespace ilc::interaction::facts
