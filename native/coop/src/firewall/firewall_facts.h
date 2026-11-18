// Ivalice Legacy co-op module: the game facts the input firewall and the frontend gate use (FFXII_TZA.exe 1.0.4.0,
// fixed image base).
//
// Globals and code addresses are VAs of the pinned build (the image base is fixed at 0x120000; the co-op thread's
// reader relocates globals to the image base it was given, so host tests can pass a synthetic image). Offsets
// inside game objects are plain numbers. These are facts about the game, nothing of ours.
#pragma once

#include <stdint.h>

namespace ilc::firewall::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;               // VA: the fixed image base

// ---- normalized buttons ("semantic ids") -------------------------------------------------------------------------
// The pad get-button 0x79D670 returns byte +0x43C + (id - 0x0B) for ids 0x0B..0x1B and 0 for anything else.
constexpr uint32_t SEM_FIRST = 0x0B;
constexpr uint32_t SEM_LAST = 0x1B;
constexpr uint32_t SEM_COMMAND = 0x0B;                 // pad X; desktop M1
constexpr uint32_t SEM_CONFIRM = 0x0C;                 // pad A; desktop M2
constexpr uint32_t SEM_CANCEL = 0x0D;                  // pad B; desktop M3
constexpr uint32_t SEM_PARTY = 0x0E;                   // pad Y
constexpr uint32_t SEM_LB = 0x0F;
constexpr uint32_t SEM_RB = 0x10;
constexpr uint32_t SEM_PAUSE = 0x14;                   // START
constexpr uint32_t SEM_UP = 0x17;
constexpr uint32_t SEM_RIGHT = 0x18;
constexpr uint32_t SEM_DOWN = 0x19;
constexpr uint32_t SEM_LEFT = 0x1A;

// ---- device objects --------------------------------------------------------------------------------------------
constexpr uint64_t VT_PAD_BASE = 0x1CB24C8;            // VA: pad base vtable (+0x18 = 0x79D670, +0x20 = 0x79D730)
constexpr uint64_t VT_PAD_DINPUT = 0x1CB2638;          // VA: DirectInput pad vtable (same get-button/get-axis)
constexpr uint64_t VT_PAD_XINPUT = 0x1CB2678;          // VA: XInput pad vtable (same)
constexpr uint64_t VT_PAD_STEAM = 0x1CB26B8;           // VA: Steam pad vtable (same)
constexpr uint64_t VT_DESKTOP = 0x1CB23F8;             // VA: desktop/mouse vtable (get-button 0x79D650, axis 0x79D6A0)
constexpr uint32_t DEV_XINPUT_INDEX = 0x458;           // u32, XInput objects only: the polled user index
constexpr uint32_t DEV_DESKTOP_BUTTONS = 0x1F4;        // u8[3]: M1, M2, M3 (ids 0x0B..0x0D)
constexpr uint32_t DEV_DESKTOP_WHEEL = 0x1F0;          // i32: signed wheel delta (axis ids 3, 6, 9)
constexpr uint32_t DEV_DESKTOP_LOOK = 0x1E8;           // i32 x2: raw mouse X, Y

// ---- hook continuations (each a bare `ret` the handler resumes at with its result in rax/xmm0) ------------------
constexpr uintptr_t GET_BUTTON_RET = 0x79D686;         // VA: `ret` of the pad get-button (after its movzx)
constexpr uintptr_t GET_AXIS_RET = 0x79D765;           // VA: `ret` of the pad get-axis (after the first divss)
constexpr uintptr_t DESKTOP_BUTTON_RET = 0x79D666;     // VA: `ret` right after FW-03's load
constexpr uintptr_t DESKTOP_WHEEL_RET = 0x79D6F9;      // VA: `ret` of the wheel case (after FW-04's divss)
constexpr uintptr_t RETURN_ZERO = 0x28FD6C;            // VA: `xor eax, eax; ret` (tail of the queue function)

// ---- stock frontends (the request dispatcher map)----------------------------------------------------------------
constexpr uint32_t FRONTEND_ID_MASK = 0x7FFF;          // every boundary masks its id this way
constexpr uint32_t QUEUE_ID_MASK = 0x7F;               // the command-tree queue carries flags above bit 6
constexpr uint32_t FRONTEND_BATTLE = 1;                // P1's battle command menu (reason 0 in Wait)
constexpr uint32_t FRONTEND_LEADER = 3;                // leader select, the automatic KO path included
constexpr uint32_t FRONTEND_TREE_ACTIVE = 4;           // command-tree variant that pauses in Active
constexpr uint64_t CB_COMMAND_TREE = 0x279E20;         // VA: P1's command-tree callback (ids 1/4)
constexpr uint64_t CB_LEADER_WINDOW = 0x2985D0;        // VA: the leader window's callback (id 3)
constexpr uint64_t CB_EMPTY = 0x5C5230;                // VA: the object initializer's default (empty) callback

// ---- UI state the co-op thread reads ---------------------------------------------------------------------------
constexpr uintptr_t UI_MANAGER = 0x209BE80;            // VA: pointer to the UI manager
constexpr uint32_t UI_TREE_SLOT = 0xAB30;              // the command tree's object slot (callback at +0)
constexpr uint32_t UI_TARGET_INFO = 0x9FD0;            // pointer, non-null only while TARGET INFO is open
constexpr uintptr_t ACTIVE_MENU = 0x208EE48;           // VA: pointer to the active menu object
constexpr uintptr_t PAUSE_PRIMARY = 0x209AD28;         // VA: pointer, non-null while the native pause is up
constexpr uintptr_t PAUSE_SECONDARY = 0x209AD30;       // VA: pointer, same
constexpr uintptr_t SETTINGS = 0x1F82D20;              // VA: pointer to the game settings
constexpr uint32_t SETTINGS_DECIDE = 0x64;             // u32: 0 = Circle confirms, 1 = Cross confirms
constexpr uint32_t DECIDE_CIRCLE = 0;
constexpr uint32_t DECIDE_CROSS = 1;

// P1's world prompt: the interaction descriptor and the prompt output the field builds every frame.
constexpr uintptr_t PROMPT_CAN_USE = 0x209A2AA;        // VA: u8, 1 once a scan published a usable target
constexpr uintptr_t PROMPT_DESCRIPTOR = 0x209A2B0;     // VA: 24 bytes (state u32 at +0x0C, handle u32 at +0x10)
constexpr uintptr_t PROMPT_OUTPUT = 0x2B59D00;         // VA: 24 bytes (state u32 at +0x10, handle u32 at +0x14)
constexpr uint32_t DESC_STATE = 0x0C;
constexpr uint32_t DESC_HANDLE = 0x10;
constexpr uint32_t OUTPUT_STATE = 0x10;
constexpr uint32_t OUTPUT_HANDLE = 0x14;
constexpr uint32_t PROMPT_HIGHLIGHTED = 1;             // in range and shown (the keyboard's Enter interacts here)
constexpr uint32_t PROMPT_SELECTED = 2;
constexpr uint32_t ENTRY_UNIT_TYPE = 0x0E;             // registry entry: u8, low nibble = unit type
constexpr uint32_t HANDLE_REGISTRY_SHIFT = 16;         // handle bits 16..19 = registry (0 = actors)
constexpr uint32_t ENTRY_ACTOR = 0x30;                 // registry entry: pointer to the actor (actor entries)
constexpr uint32_t ENTRY_NAME_KEY = 0x102;             // registry entry: i16 name key (434 = a treasure chest)
constexpr int16_t TREASURE_NAME_KEY = 434;
constexpr uint32_t ACTOR_HANDLE = 0x08;                // actor: u32 handle

}  // namespace ilc::firewall::facts
