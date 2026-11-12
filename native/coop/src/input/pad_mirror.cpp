// Ivalice Legacy co-op module: per-seat pad mirror (see pad_mirror.h).
#include "input/pad_mirror.h"

#include <string.h>

namespace ilc::pads {
namespace {

struct slot {
    volatile uint32_t ver;  // odd while written; only grows
    uint32_t reserved;
    pad_view v;
};

slot g_slots[ILC_SEATS];

void write(uint32_t s, const pad_view& v)
{
    slot& e = g_slots[s];
    const uint32_t odd = (e.ver + 1u) | 1u;
    __atomic_store_n(&e.ver, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    __builtin_memcpy(&e.v, &v, sizeof(v));
    __atomic_store_n(&e.ver, odd + 1u, __ATOMIC_RELEASE);
}

}  // namespace

void record(const ilc_seat_input_frame* frames, const ilc_seat_action_frame* actions)
{
    for (uint32_t s = 0; s < ILC_SEATS; ++s) {
        pad_view v;
        v.seq = frames[s].seq;
        v.generation = frames[s].source_generation;
        v.source = frames[s].source;
        v.xinput_slot = frames[s].xinput_slot;
        v.flags = frames[s].flags;
        v.reserved = 0;
        v.act_held = actions[s].held;
        v.pad = frames[s].pad;
        memset(v.reserved2, 0, sizeof(v.reserved2));
        write(s, v);
    }
}

void reset()
{
    pad_view zero = {};
    for (uint32_t s = 0; s < ILC_SEATS; ++s) {
        write(s, zero);
    }
}

bool read(uint32_t seat, pad_view* out)
{
    if (seat < 1 || seat > ILC_SEATS) {
        return false;
    }
    const slot& e = g_slots[seat - 1];
    const uint32_t first = __atomic_load_n(&e.ver, __ATOMIC_ACQUIRE);
    if (first & 1u) {
        return false;
    }
    __builtin_memcpy(out, const_cast<const pad_view*>(&e.v), sizeof(*out));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&e.ver, __ATOMIC_RELAXED) == first && out->seq != 0;
}

void test_hold(uint32_t seat, bool odd)
{
    if (seat >= 1 && seat <= ILC_SEATS) {
        slot& e = g_slots[seat - 1];
        const uint32_t v = e.ver;
        __atomic_store_n(&e.ver, odd ? (v | 1u) : ((v + 1u) & ~1u), __ATOMIC_RELEASE);
    }
}

void test_write(uint32_t seat, const pad_view& v)
{
    if (seat >= 1 && seat <= ILC_SEATS) {
        write(seat - 1, v);
    }
}

}  // namespace ilc::pads
