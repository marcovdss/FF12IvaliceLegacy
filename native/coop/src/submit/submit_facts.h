// Ivalice Legacy co-op module: the game functions a guest's battle menu calls (FFXII_TZA.exe 1.0.4.0, fixed image
// base), with the bytes each must start with. Everything is called on the game thread from the UI-tick callback.
#pragma once

#include <stdint.h>

namespace ilc::submit::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;   // VA: the fixed image base

// int 0x311CD0(u32 issuer handle, u32 target handle, u16 action, u32 flag = 0): queue a manual action. Wraps
// 0x311B20 with party id 0. Negative when the game cannot resolve the issuer or the target.
constexpr uintptr_t FN_SUBMIT_ACTION = 0x311CD0;   // VA
// int 0x311B20(u32 issuer handle, u32 target handle = 0, u8 party id, u16 action, u32 flag = 0): the same for a
// party member outside the active formation (reserve target).
constexpr uintptr_t FN_SUBMIT_RESERVE = 0x311B20;  // VA
// void 0x3138D0(u32 flag = 0): the stock Dismiss: in summon mode, queues action 0x113 on the Esper in party slot 3.
constexpr uintptr_t FN_DISMISS = 0x3138D0;         // VA
// int 0x303BD0(actor*, u16 action): 0 = admitted; 0x12 blocked, 0x13 MP short, 0x14 charges short, 0x15 gil short.
// A null actor is admitted, and actor+0x698 is read unchecked: callers pass a live actor with a record.
constexpr uintptr_t FN_ADMISSION = 0x303BD0;       // VA
// int 0x3064F0(u8 party id, u16 action, int = 0): 1 = owned (or in a guest's fixed list) and licensed.
constexpr uintptr_t FN_AVAILABLE = 0x3064F0;       // VA
// int 0x309B40(u8 party id): gambits on (record +0 bit 2); void 0x311AF0(u8 party id, int on): set or clear it.
constexpr uintptr_t FN_GAMBITS_QUERY = 0x309B40;   // VA
constexpr uintptr_t FN_GAMBITS_SET = 0x311AF0;     // VA
// record* 0x320A40(u8 party id): [0x2EBF190] + 8 + id * 0x1C8, or null past 0x27.
constexpr uintptr_t FN_RECORD = 0x320A40;          // VA
// node* 0x291010(ui manager*, u32 source handle, u32 target handle): the combat indicator (the line from the
// issuer to its target). Null when source == target (the existing node is retired instead).
constexpr uintptr_t FN_INDICATOR = 0x291010;       // VA
// int 0x249C60(u32 sound id): a UI sound.
constexpr uintptr_t FN_UI_SOUND = 0x249C60;        // VA

constexpr uintptr_t UI_MANAGER_PTR = 0x209BE80;    // VA: pointer to the UI manager (UT-01 passes it to the tick)
constexpr uintptr_t BATTLE_STATE_PTR = 0x2EBF190;  // VA: party block (member records)
constexpr uint32_t MEMBER_RECORDS = 0x08;
constexpr uint32_t MEMBER_RECORD_SIZE = 0x1C8;
constexpr uint32_t MEMBER_LIMIT = 0x28;            // the record getter's bound
constexpr uint32_t LAYOUT_TABLE = 0x5A48;          // battle state: party layouts (9 u16 each)
constexpr uint32_t LAYOUT_STRIDE = 0x12;
constexpr uint32_t LAYOUT_CURRENT = 1;

// Actor.
constexpr uint32_t ACTOR_HANDLE = 0x08;
constexpr uint32_t ACTOR_BATTLE = 0x698;

// Combat indicator node.
constexpr uint32_t NODE_SOURCE = 0x2D0;            // u32 handle
constexpr uint32_t NODE_TARGET = 0x2D4;            // u32 handle
constexpr uint32_t NODE_FLAGS = 0x2EC;             // u32: bit 0 shown
constexpr uint32_t NODE_STATE = 0x2F0;             // u16: 1 starting, 2 active

// Statuses.
constexpr uint32_t ST_KO = 1u << 0;
constexpr uint32_t ST_STONE = 1u << 1;
constexpr uint32_t ST_BERSERK = 1u << 27;
constexpr uint32_t ST_NO_COMMAND = 0x8000203Bu;    // NOTADDR: KO, Stone, Stop, Sleep, Confuse, Disable, X-Zone

// Actions.
constexpr uint16_t ACTION_ATTACK = 0x96;
constexpr uint16_t ACTION_DISMISS = 0x113;
constexpr uint16_t ESPER_FIRST = 0x106;            // summons 0x106..0x112
constexpr uint16_t ESPER_LAST = 0x112;
constexpr uint16_t MEMBER_ESPER_FIRST = 27;        // party ids of materialized Espers
constexpr uint16_t MEMBER_ESPER_LAST = 39;
constexpr uint16_t MEMBER_NONE = 0xFFFF;

// The UI sounds our menus play (nothing else is ever passed to the sound function): Gambits on and off, the target
// group change, cursor, open/confirm, close, menu opened.
constexpr uint32_t SOUND_IDS[] = {0x12, 0x13, 0x0A, 1, 2, 3, 4};

// The first bytes of every function above, from the pinned build. attach() compares them before registering any
// operation: a function that does not start with them is not called at all (the menus' game-thread operations stay
// unregistered, and every request is refused).
constexpr uint32_t PROLOGUE = 12;
struct prologue {
    uintptr_t va;
    uint8_t bytes[PROLOGUE];
};
constexpr prologue PROLOGUES[] = {
    {FN_SUBMIT_ACTION, {0x48, 0x83, 0xEC, 0x38, 0x44, 0x89, 0x4C, 0x24, 0x20, 0x45, 0x0F, 0xB7}},
    {FN_SUBMIT_RESERVE, {0x44, 0x88, 0x44, 0x24, 0x18, 0x89, 0x54, 0x24, 0x10, 0x55, 0x57, 0x41}},
    {FN_DISMISS, {0x40, 0x56, 0x48, 0x83, 0xEC, 0x30, 0x8B, 0xF1, 0xE8, 0xC3, 0xA0, 0xFF}},
    {FN_ADMISSION, {0x48, 0x89, 0x5C, 0x24, 0x10, 0x57, 0x48, 0x83, 0xEC, 0x20, 0x0F, 0xB7}},
    {FN_AVAILABLE, {0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x57, 0x48}},
    {FN_GAMBITS_QUERY, {0x48, 0x83, 0xEC, 0x28, 0xE8, 0xF7, 0x6E, 0x01, 0x00, 0x48, 0x85, 0xC0}},
    {FN_GAMBITS_SET, {0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x8B, 0xDA, 0xE8, 0x43, 0xEF, 0x00}},
    {FN_RECORD, {0x80, 0xF9, 0x27, 0x76, 0x03, 0x33, 0xC0, 0xC3, 0x0F, 0xB6, 0xC1, 0x48}},
    {FN_INDICATOR, {0x40, 0x53, 0x56, 0x57, 0x48, 0x83, 0xEC, 0x60, 0x48, 0x8B, 0x05, 0x11}},
    {FN_UI_SOUND, {0x48, 0x83, 0xEC, 0x28, 0x83, 0x3D, 0x2D, 0x74, 0xE4, 0x01, 0x00, 0x75}},
};

}  // namespace ilc::submit::facts
