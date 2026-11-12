// Ivalice Legacy co-op module: the TW-01 handler (see towns.h).
//
// Site 0x326A34 (`mov r14,[rsp+0x38]`, five position-independent bytes) is where every arm of the layout builder's
// mode switch joins, Companions' "Party In Towns" jump included, right before the bind loop. Register contract
// there (every path): rdi = party block + 0x5A48, r15 = rdi + 0x12, r13d = 0xFFFF, ebp = 0. The handler is a
// fail_open Body: it reads the saved registers and the town record copy, reaches game memory only through the
// fault-safe probe, never calls a game function, changes no register, and always runs the original instruction
// (MinHook's trampoline continues at 0x326A39). The only writes are the current/HUD slots it blanks.
#include <string.h>

#include "patch/call_island.h"
#include "patch/detour.h"
#include "runtime/probe.h"
#include "towns/towns.h"
#include "towns/towns_facts.h"

namespace ilc::towns {

namespace detail {
extern patch::record_slot<town_record> g_slot;
extern counters g_counters;
extern volatile uintptr_t g_image_base;
bool town_mode_now(uintptr_t base, uintptr_t* block_out, bool* ok);
}  // namespace detail

namespace {

using patch::ctx;
using patch::RUN_ORIGINAL;

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t r = 0; r < patch::SITE_COUNT; ++r) {
        if (same_id(patch::SITES[r].id, id)) {
            return static_cast<int32_t>(r);
        }
    }
    return -1;
}

constexpr int32_t TW01 = find_row("TW-01");
static_assert(TW01 >= 0 && patch::SITES[TW01].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[TW01].subsystem == ILC_SITE_SUBSYS_PARTY &&
                  patch::SITES[TW01].kind == ILC_SITE_KIND_DETOUR && patch::SITES[TW01].hook_len == 5,
              "TW-01 is a five-byte PARTY detour");
static_assert(!patch::island::is_call_row(static_cast<uint32_t>(TW01)), "TW-01 is a MinHook row, not a call row");
static_assert(sizeof(town_record) <= 32, "a small record: copied inline at a mid-function site");

void bump(volatile uint64_t& counter, uint64_t by = 1)
{
    __atomic_add_fetch(&counter, by, __ATOMIC_RELAXED);
}

int32_t tw01_body(ctx* c, const town_record& r)
{
    if ((r.head.flags & TOWNF_ACTIVE) == 0) {
        return RUN_ORIGINAL;
    }
    const uint64_t layouts = c->rdi;
    // Only the layout builder's own state: anything else that lands here is left alone.
    if (c->r15 != layouts + facts::L_CURRENT || (c->r13 & 0xFFFFu) != 0xFFFFu || static_cast<uint32_t>(c->rbp) != 0) {
        bump(detail::g_counters.contract_failed);
        return RUN_ORIGINAL;
    }
    uintptr_t block = 0;
    bool ok = false;
    const uintptr_t base = __atomic_load_n(&detail::g_image_base, __ATOMIC_ACQUIRE);
    const bool town = detail::town_mode_now(base, &block, &ok);
    if (!ok || layouts != block + facts::LAYOUTS) {
        bump(detail::g_counters.contract_failed);
        return RUN_ORIGINAL;
    }
    if (!town) {
        return RUN_ORIGINAL;
    }
    uint8_t raw[facts::L_SPAN];
    if (!copy_from_caller(raw, reinterpret_cast<const void*>(layouts), sizeof(raw))) {
        return RUN_ORIGINAL;
    }
    uint16_t current[SLOTS];
    uint16_t ui[SLOTS];
    __builtin_memcpy(current, raw + facts::L_CURRENT, sizeof(current));
    __builtin_memcpy(ui, raw + facts::L_UI, sizeof(ui));
    const int32_t hidden = filter_slots(r.allowed, raw[facts::L_LEADER], current, ui);
    if (hidden == FILTER_NO_LEADER) {
        bump(detail::g_counters.no_leader);
        return RUN_ORIGINAL;
    }
    if (hidden == 0) {
        return RUN_ORIGINAL;
    }
    // HUD first: if the second write failed, current would still name the member and the bind loop keeps it.
    if (copy_to_caller(reinterpret_cast<void*>(layouts + facts::L_UI), ui, sizeof(ui))) {
        (void)copy_to_caller(reinterpret_cast<void*>(layouts + facts::L_CURRENT), current, sizeof(current));
    }
    bump(detail::g_counters.filtered);
    bump(detail::g_counters.hidden_slots, static_cast<uint64_t>(__builtin_popcount(static_cast<uint32_t>(hidden))));
    return RUN_ORIGINAL;
}

}  // namespace

int32_t tw01(ctx* c, uint32_t epoch)
{
    return patch::fail_open<town_record, detail::g_slot, tw01_body>(c, epoch);
}

int32_t row()
{
    return TW01;
}

bool attach()
{
    if (!gtq::register_op(OP_TOWN_REBUILD, ILC_SITE_SUBSYS_PARTY, op_rebuild)) {
        return false;
    }
    return patch::set_handler(static_cast<uint32_t>(TW01), tw01) &&
           patch::set_site_armed(static_cast<uint32_t>(TW01), true);
}

}  // namespace ilc::towns
