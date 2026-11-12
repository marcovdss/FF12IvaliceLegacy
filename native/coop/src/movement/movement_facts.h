// Ivalice Legacy co-op module: game facts the movement subsystem needs beyond the session's (FFXII_TZA.exe 1.0.4.0).
//
// Globals are VAs of the pinned build (the image base is fixed at 0x120000). Offsets inside objects are plain
// numbers. Sites are given as VAs of the instructions named in the site table.
#pragma once

#include <stdint.h>

namespace ilc::movement::facts {

constexpr uintptr_t CAMERA_VIEW_MATRIX = 0x1F7E528;  // VA: 16 floats, row-major view matrix

// Actor.
constexpr uint32_t ACTOR_FLAGS = 0x00;               // u64; bit 46 = an execution motion plays
constexpr uint64_t ACTOR_EXECUTION_BIT = 1ull << 46; // NOTADDR
constexpr uint32_t ACTOR_STANCE = 0x02;              // u8; bit 7 = fighting stance (weapon drawn)
constexpr uint8_t ACTOR_STANCE_ARMED = 0x80;
constexpr uint32_t ACTOR_ACTION_SHADOW = 0x72C;      // u16: the action setter's copy of +0x714
constexpr uint32_t ACTOR_TARGET_SHADOW = 0x728;      // u32: the setter's copy of the target +0x710
constexpr uint32_t ACTOR_VISUAL = 0x15C;             // float: the facing the actor sync stores

// Scene root.
constexpr uint32_t ROOT_ACTOR = 0x30;                // pointer back to the actor
constexpr uint32_t ROOT_PHYSICS = 0xB8;              // pointer to the physics object

// Physics object.
constexpr uint32_t PHYS_PITCH = 0xA0;                // float: steering pitch (the steering path reads it)
constexpr uint32_t PHYS_HEADING = 0xA4;              // float: commanded heading
constexpr uint32_t PHYS_VISUAL = 0xA8;               // float: the body's facing
constexpr uint32_t PHYS_MAGNITUDE = 0xD0;            // float: locomotion command per frame

// Code addresses (VA) the detours continue at.
constexpr uintptr_t FOLLOWER_LIST_EMPTY_EXIT = 0x305CF7;  // VA: xor eax,eax; add rsp,0x50; pop r13; pop rdi; pop rbp; ret
constexpr uintptr_t APPROACH_NO_EXIT = 0x325DA5;          // VA: xor eax,eax; add rsp,0x20; pop rdi; ret
constexpr uint32_t APPROACH_SAVED_RBX = 0x30;             // [rsp+0x30] holds the caller's rbx at MV-06
constexpr uintptr_t VISUAL_FACING_SETTER = 0x2E5E10;     // VA: (root rcx, facing xmm1, pitch xmm2, flags r9d)
constexpr uint32_t PHYS_VISUAL_PITCH = 0xAC;              // float: the setter's second angle (kept as it is)

}  // namespace ilc::movement::facts
