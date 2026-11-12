// Ivalice Legacy co-op module: call-site hooks by rel32 rewrite ("call islands").
//
// A PATCH row whose hook window holds a `call rel32` (the 14 CALL rows, and the four `mov rcx, reg; call` rows
// whose call sits 3 bytes in) is not hooked with MinHook. MinHook would relocate the call into its buffer as
// `call [rip+2]`, so the game callee would run with its return address in memory that has no unwind data. Here
// the game's own E8 stays where it is and only its rel32 changes:
//
//   game:   call island_k                      (rel32 retargeted; every other byte stays pinned)
//   island: jmp qword [rip+0] ; dq entry_k      (a jump-only island within +-2 GiB of the image; never a return
//                                               address, so an async fault there unwinds as a leaf to site+5)
//   entry_k: jmp qword [slot_k]                (in our DLL; no unwind data, it is a leaf jump)
//            slot_k = the original callee while the row's gate is closed: the callee returns straight to site+5,
//                     so no address of ours is on the stack while game code runs (stock, disarmed, DRY);
//                     slot_k = slow_k while the gate is open.
//   slow_k: an ordinary function with static unwind data (.pdata/.xdata in our DLL, nothing registered at run
//           time). It saves the machine state into a patch::ctx and calls the dispatcher. RtlVirtualUnwind walks
//           from the handler through the dispatcher and slow_k straight to site+5 in the game function.
//
// Handler contract for call rows (same handler_fn and set_handler as every row; the registers are those at the
// callee's entry, ctx->rsp points at the return address site+5):
//   RUN_ORIGINAL          tail-jump to the callee with the original return address (pre-hook)
//   RESUME (ctx->resume)  skip the callee and continue at ctx->resume as if it had returned (RSP = entry + 8);
//                         set ctx->rax / xmm0 to fake its result. resume == 0 means RUN_ORIGINAL
//   CALL_AROUND           call the callee from inside slow_k (the shadow space and ten stack-argument qwords are
//                         copied), then run the row's after-handler (set_after) with the registers the callee
//                         returned; it may edit them and set ctx->resume (0 = return to site+5). Without an
//                         after-handler this is RUN_ORIGINAL. While the callee runs, its return address is inside
//                         slow_k, which has unwind data, so a walk still reaches site+5.
//
// Install, byte checks, rollback and arming are the patch layer's (patch_engine.cpp); this file holds the
// island mechanics it calls inside the same all-or-nothing transaction:
//   prepare -> islands allocated near the image and written (no image byte yet)
//   commit  -> every other thread suspended (static arrays: nothing is allocated meanwhile), no instruction
//              pointer inside a rel32 being written, the 4 bytes written, instruction cache flushed, resumed.
//              All or nothing inside that one freeze: if a write fails, the rows already written get their pinned
//              rel32 back before the threads resume; a row whose restore fails stays committed (revert retries
//              it and the island page is kept)
//   revert  -> the same freeze, the pinned rel32 written back; islands stay mapped (a thread may be on one)
//   release -> host tests and never-committed installs only: islands freed
#pragma once

#include <stdint.h>

#include "patch/site_table.h"

namespace ilc::patch::island {

constexpr uint32_t NO_CALL = 0xFFu;

// Offset of the E8 inside a PATCH row's window, or NO_CALL.
constexpr uint32_t call_offset(const site& s)
{
    if (s.policy != ILC_SITE_POLICY_PATCH) {
        return NO_CALL;
    }
    const auto b = [&s](uint32_t k) { return static_cast<uint8_t>(s.expected[k]); };
    if (s.kind == ILC_SITE_KIND_CALL && s.length == 5 && s.hook_len == 5 && b(0) == 0xE8) {
        return 0;
    }
    // mov rcx, r64 (REX.W 8B, ModRM 11 001 rrr); call rel32
    if (s.kind == ILC_SITE_KIND_DETOUR && s.length == 8 && s.hook_len == 8 && b(0) == 0x48 && b(1) == 0x8B &&
        (b(2) & 0xF8) == 0xC8 && b(3) == 0xE8) {
        return 3;
    }
    return NO_CALL;
}

constexpr bool is_call_row(uint32_t row)
{
    return row < SITE_COUNT && call_offset(SITES[row]) != NO_CALL;
}

constexpr uint32_t count_call_rows()
{
    uint32_t n = 0;
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        n += is_call_row(row) ? 1 : 0;
    }
    return n;
}

constexpr uint32_t ROWS = count_call_rows();  // keep in step with ILC_CALL_ROWS in call_stubs.cpp
static_assert(ROWS == 18, "the call-type rows: 14 CALL rows plus four `mov rcx, reg; call` rows");

// Call index k (0..ROWS-1, in site-table order) <-> row.
constexpr uint32_t row_of(uint32_t k)
{
    for (uint32_t row = 0, n = 0; row < SITE_COUNT; ++row) {
        if (is_call_row(row) && n++ == k) {
            return row;
        }
    }
    return SITE_COUNT;
}

constexpr int32_t index_of(uint32_t row)
{
    int32_t n = 0;
    for (uint32_t r = 0; r < SITE_COUNT && r <= row; ++r) {
        if (is_call_row(r)) {
            if (r == row) {
                return n;
            }
            ++n;
        }
    }
    return -1;
}

// The callee the pinned bytes name (preflight has checked them): site + offset + 5 + rel32.
constexpr uint64_t callee_rva(uint32_t row)
{
    const site& s = SITES[row];
    const uint32_t at = call_offset(s);
    uint32_t rel = 0;
    for (uint32_t k = 0; k < 4; ++k) {
        rel |= static_cast<uint32_t>(static_cast<uint8_t>(s.expected[at + 1 + k])) << (8 * k);
    }
    return static_cast<uint64_t>(static_cast<int64_t>(s.rva + at + 5) + static_cast<int32_t>(rel));
}

constexpr uint32_t ISLAND_STRIDE = 16;  // FF 25 00000000 <abs64> CC CC

enum result : int32_t {
    ISLAND_OK = 0,
    ISLAND_NO_MEMORY = 1,    // no free region near the image, or VirtualAlloc/VirtualProtect failed
    ISLAND_OUT_OF_RANGE = 2, // an island would be beyond rel32 reach of a site
    ISLAND_BUSY = 3,         // a thread kept its instruction pointer inside a rel32 for every attempt
    ISLAND_FREEZE_FAILED = 4,// thread snapshot failed, too many threads, or a thread could not be suspended
    ISLAND_WRITE_FAILED = 5, // VirtualProtect on the image refused
};

// All under the patch layer's lock, from install()/rollback()/test_release_all().
int32_t prepare(uintptr_t base, const bool skip[SITE_COUNT], uint32_t fail_row);  // fail_row: test injection
int32_t commit(uintptr_t base);
int32_t revert(uintptr_t base, uint32_t keep_row);  // keep_row: test injection (its rel32 is left in place)
void release();                                      // free the islands (nothing may point at them)
bool prepared(uint32_t row);
bool committed();
bool holds_island(uintptr_t base, uint32_t row);    // E8 -> island -> entry(row) at the call
uint64_t island_address(uint32_t row);              // 0 unless prepared
void* entry(uint32_t row);                          // entry_k (what the island jumps to)
void* slow(uint32_t row);                           // slow_k
uint64_t callee(uint32_t row);                      // base + callee_rva, valid after prepare
// Gate sync: slot_k = slow_k (open) or the callee (closed). Any thread; one aligned pointer store.
void sync(uint32_t row, bool open);

struct stats_view {
    uint64_t region;          // island page, 0 = none
    uint32_t commits;         // rel32 sets written (freeze rounds that wrote)
    uint32_t reverts;
    uint32_t busy_attempts;   // freeze attempts that found a thread inside a rel32
    uint32_t threads_frozen;  // in the last freeze
};
stats_view stats();

// Host tests: make the commit's rel32 write for `row` fail, and the undo of `restore_row` after it (SITE_COUNT =
// none). Cleared by release().
void test_fail_write(uint32_t row, uint32_t restore_row);

}  // namespace ilc::patch::island
