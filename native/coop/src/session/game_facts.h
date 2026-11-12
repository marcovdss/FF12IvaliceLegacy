// Ivalice Legacy co-op module: the game structures the session core reads (FFXII_TZA.exe 1.0.4.0, fixed image base).
//
// Every global below is a VA of the pinned build; the reader adds (VA - IMAGE_VA) to the image base it was given,
// so a host test can point it at a synthetic image. Offsets inside game objects are plain numbers. These are facts
// about the game, read-only: the session core never writes any of them.
#pragma once

#include <stdint.h>

namespace ilc::session::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;  // VA: the fixed image base the globals below are relative to

// Battle state: party layouts, leader, mode bits.
constexpr uintptr_t BATTLE_STATE_PTR = 0x2EBF190;   // VA: pointer to the battle state
constexpr uint32_t LAYOUT_TABLE = 0x5A48;           // four layouts of 9 u16 party ids each
constexpr uint32_t LAYOUT_STRIDE = 0x12;
constexpr uint32_t LAYOUT_ENTRIES = 9;
constexpr uint32_t LAYOUT_CURRENT = 1;              // the party the world uses
constexpr uint32_t LAYOUT_UI = 2;                   // the party the HUD shows
constexpr uint32_t LEADER_MEMBER = 0x5AA4;          // u32 party id of the controlled leader, 0xFFFF none
constexpr uint32_t MODE_BITS = 0x5B04;              // u32; bit 1 = mounted on a chocobo
constexpr uint32_t MODE_CHOCOBO = 0x02;

// Player control lock (cutscenes and scripted dialogue clear it).
constexpr uintptr_t CONTROL_PTR = 0x2099D70;        // VA: pointer to the control block
constexpr uint32_t CONTROL_ENABLED = 0x14;          // u8

// Native battle command menu.
constexpr uintptr_t COMMAND_ACTOR = 0x2092730;      // VA: u32, non-zero while a command menu has an actor
constexpr uintptr_t COMMAND_FLAG = 0x2092734;       // VA: u32, 1 while that menu is open

// Location: current and UI area ids agree once a load has finished.
constexpr uintptr_t LOCATION_PTR = 0x2092758;       // VA: pointer to the location block
constexpr uint32_t LOCATION_CURRENT = 0x1244;       // u32
constexpr uint32_t LOCATION_UI = 0x125C;            // u32
constexpr uint32_t LOCATION_FLAGS = 0x1262;         // u8; bit 0 = town
constexpr uint32_t LOCATION_TOWN = 0x01;
constexpr uint32_t LOCATION_NONE = 0xFFFFFFFFu;     // NOTADDR: no area
constexpr uintptr_t AREA_TABLE_PTR = 0x2ADD100;     // VA: pointer to the area metadata table
constexpr uint32_t AREA_TABLE_COUNT = 4;            // u32 entry count
constexpr uint32_t AREA_TABLE_ENTRIES = 0x0A;       // 3-byte entries; bit 0 of the first byte = town
constexpr uint32_t AREA_ENTRY_SIZE = 3;

// Native modal interaction (a world dialog the game owns).
constexpr uintptr_t UI_MANAGER_PTR = 0x209BE80;     // VA: pointer to the UI manager
constexpr uint32_t UI_MODAL_FLAGS = 0x10F68;        // u32; 0x40 while a modal object is attached
constexpr uint32_t UI_MODAL_OBJECT = 0x10F80;       // pointer
constexpr uint32_t UI_MODAL_OBJECT_FLAGS = 0x1D7A;  // u16; 0x08 while it is active
constexpr uint32_t UI_MODAL_MANAGER_BIT = 0x40;
constexpr uint32_t UI_MODAL_OBJECT_BIT = 0x08;

// Actor manager: count, then one entry pointer per actor index.
constexpr uintptr_t ACTOR_MANAGER_PTR = 0x2098E18;  // VA: pointer to the actor manager
constexpr uint32_t ACTOR_COUNT = 0x00;              // u32
constexpr uint32_t ACTOR_ENTRIES = 0x08;            // pointer per index
constexpr uint32_t ACTOR_COUNT_MAX = 4096;          // a larger count is a torn or foreign read
constexpr uint32_t ENTRY_PARTY_SLOT = 0x12;         // u8, 0..3 for the active party
constexpr uint32_t ENTRY_ACTOR = 0x30;              // pointer to the actor

// Actor.
constexpr uint32_t ACTOR_HANDLE = 0x08;             // u32: class bits << 16 | actor index
constexpr uint32_t ACTOR_ROOT = 0x10;               // pointer to the scene root
constexpr uint32_t ACTOR_POSITION = 0xD0;           // float[3]
constexpr uint32_t ACTOR_BATTLE = 0x698;            // pointer to the battle record (HP, status)
constexpr uint32_t ACTOR_ACTION_STATE = 0x6B4;      // u8
constexpr uint32_t ACTOR_FOLLOW_LIST_A = 0x6E8;     // pointer: the follower list the actor is evaluated with
constexpr uint32_t ACTOR_FOLLOW_LIST_B = 0x6F0;     // pointer
constexpr uint32_t ACTOR_DESCRIPTOR = 0x708;        // pointer; u16 action id at +0
constexpr uint32_t ACTOR_TARGET = 0x710;            // u32
constexpr uint32_t ACTOR_ACTION = 0x714;            // u16
constexpr uint32_t ROOT_PHYSICS = 0xB8;             // pointer to the physics object (position at +0)
constexpr uint32_t HANDLE_INDEX_MASK = 0xFFFF;

// The two native follower lists (static data in the image).
constexpr uintptr_t FOLLOW_LIST_PRIMARY = 0x1E09350;   // VA
constexpr uintptr_t FOLLOW_LIST_SECONDARY = 0x1EEC3C0; // VA

// Native action ids around following the leader.
constexpr uint16_t ACTION_FOLLOW = 0x4124;
constexpr uint16_t ACTION_IDLE = 0x4021;

// Battle record.
constexpr uint32_t BATTLE_HP = 0x48;                // u32
constexpr uint32_t BATTLE_STATUS_LASTING = 0x3C;    // u32 status bits
constexpr uint32_t BATTLE_STATUS_TIMED = 0x64;      // u32 status bits
constexpr uint32_t STATUS_SAMPLE_TRIES = 3;

// Status bits (shared by both status words).
constexpr uint32_t ST_KO = 1u << 0;
constexpr uint32_t ST_STONE = 1u << 1;
constexpr uint32_t ST_STOP = 1u << 3;
constexpr uint32_t ST_SLEEP = 1u << 4;
constexpr uint32_t ST_CONFUSE = 1u << 5;
constexpr uint32_t ST_DISABLE = 1u << 13;
constexpr uint32_t ST_IMMOBILIZE = 1u << 14;
constexpr uint32_t ST_BERSERK = 1u << 27;
constexpr uint32_t ST_XZONE = 1u << 31;
// Statuses that stop a body from walking. Confuse and Disable restrict commands, not locomotion.
constexpr uint32_t ST_NO_WALK = ST_KO | ST_STONE | ST_STOP | ST_SLEEP | ST_IMMOBILIZE | ST_XZONE;
// Statuses under which the game refuses an ordinary command (and so a follow request).
constexpr uint32_t ST_NO_COMMAND = ST_KO | ST_STONE | ST_STOP | ST_SLEEP | ST_CONFUSE | ST_DISABLE | ST_BERSERK | ST_XZONE;

// Party ids.
constexpr uint16_t MEMBER_NONE = 0xFFFF;
constexpr uint16_t REGULARS = 6;                    // 0..5: the six regular characters
constexpr uint16_t ESPER_FIRST = 27;                // 27..39: Espers (a summon replaces the party temporarily)
constexpr uint16_t ESPER_LAST = 39;

constexpr bool is_regular(uint32_t member) { return member < REGULARS; }
constexpr bool is_esper(uint32_t member) { return member >= ESPER_FIRST && member <= ESPER_LAST; }
// Party ids the game's roster names: regulars, story guests 6..14, the chocobo 26 and the Espers.
constexpr bool is_named(uint32_t member) { return member <= 14 || (member >= 26 && member <= ESPER_LAST); }

}  // namespace ilc::session::facts
