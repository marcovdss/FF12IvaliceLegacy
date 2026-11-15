// Ivalice Legacy co-op module: the game facts the Wait engine uses (FFXII_TZA.exe 1.0.4.0, fixed image base).
//
// Globals and code addresses are VAs of the pinned build; the engine relocates them to the image base it was given
// (host tests pass the image twin). Offsets inside game objects are plain numbers. Facts about the game only.
#pragma once

#include <stdint.h>

namespace ilc::wait::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;            // VA: the fixed image base

// ---- the pause-reason primitives -----------------------------------------------------------------------------
// acquire(reason, 0, 0, 0, [rsp+0x20] = -1, [rsp+0x28] = -1): sets the reason's bit and every entity's stop bits.
// A reason that is already held returns at once: the bit has no reference count and no owner.
constexpr uintptr_t FN_ACQUIRE = 0x226BE0;          // VA
// release(reason, -1): clears the bit and the stop bits; for reason 0 in Wait mode it also clears the Wait input lock.
constexpr uintptr_t FN_RELEASE = 0x225CD0;          // VA
// aggregate(): applies the combined masks of every held reason; a no-op unless the dirty byte is set.
constexpr uintptr_t FN_AGGREGATE = 0x226280;        // VA
// Clears the reason-0 Wait input lock (bit 1 of [WAIT_LOCK_OWNER] + 0x3A) if it is set; idempotent.
constexpr uintptr_t FN_CLEAR_WAIT_LOCK = 0x182DA0;  // VA
// Frontend id -> pause reason. Not called: the engine keeps its own copy of the table (reason_of), which the
// install's verify-only row and the host tests' emulation of this function hold to the stock bytes.
constexpr uintptr_t FN_REASON_OF = 0x255290;        // VA

// ---- state the engine reads -----------------------------------------------------------------------------------
constexpr uintptr_t REASONS_HELD = 0x1FD46D4;       // VA: u32, bit r = pause reason r is held
constexpr uintptr_t REASONS_DIRTY = 0x1FD46D0;      // VA: u8, the aggregate has work to do
constexpr uintptr_t SETTINGS_PTR = 0x215E6E0;       // VA: pointer to the battle settings (u32 at +0)
constexpr uint32_t SETTINGS_ACTIVE_BIT = 0x08;      // set = Active mode, clear = Wait mode
constexpr uintptr_t FRONTEND_ID = 0x2092730;        // VA: u32, the active stock frontend's id
constexpr uintptr_t FRONTEND_FLAG = 0x2092734;      // VA: u32, 1 while that frontend is up
constexpr uint32_t FRONTEND_ID_MASK = 0x7FFF;
constexpr uintptr_t GAME_MODE = 0x2064AD3;          // VA: u8, 2 = in game (5/6: teardown and title transitions)
constexpr uint8_t GAME_MODE_PLAY = 2;
constexpr uintptr_t WAIT_LOCK_OWNER = 0x1F7FFE0;    // VA: pointer; +0x3A bit 1 = the Wait input lock
constexpr uint32_t WAIT_LOCK_BYTE = 0x3A;
constexpr uint8_t WAIT_LOCK_BIT = 0x02;

constexpr int32_t REASON_WAIT = 0;                  // the reason stock Wait mode holds while P1's battle menu is open

// ---- prologue pins (the attach check; the same bytes as the site table's verify-only rows) -----------------------
constexpr uint32_t PROLOGUE_MAX = 21;
struct prologue {
    uintptr_t va;
    uint32_t length;
    uint8_t bytes[PROLOGUE_MAX];
};
constexpr prologue PROLOGUES[] = {
    {FN_ACQUIRE, 14, {0x4C, 0x89, 0x4C, 0x24, 0x20, 0x44, 0x89, 0x44, 0x24, 0x18, 0x89, 0x54, 0x24, 0x10}},
    {FN_RELEASE, 20, {0x48, 0x89, 0x5C, 0x24, 0x08, 0x89, 0x54, 0x24, 0x10, 0x55, 0x56, 0x57, 0x41, 0x54, 0x41,
                      0x55, 0x41, 0x56, 0x41, 0x57}},
    {FN_AGGREGATE, 14, {0x48, 0x8B, 0xC4, 0x48, 0x83, 0xEC, 0x78, 0x80, 0x3D, 0x42, 0xE4, 0xDA, 0x01, 0x00}},
    {FN_CLEAR_WAIT_LOCK, 21, {0x48, 0x83, 0xEC, 0x28, 0xE8, 0xE7, 0x59, 0x01, 0x00, 0x48, 0x8B, 0xC8, 0x48, 0x83,
                              0xC4, 0x28, 0xE9, 0x5B, 0x59, 0x01, 0x00}},
    {FN_REASON_OF, 11, {0x48, 0x83, 0xEC, 0x28, 0xFF, 0xC9, 0x83, 0xF9, 0x17, 0x77, 0x59}},
};

}  // namespace ilc::wait::facts
