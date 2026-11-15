// Ivalice Legacy co-op module: the steering remap (see remap.h).
#include "party/remap.h"

namespace ilc::party {
namespace {

patch::record_slot<remap_record> g_slot;
volatile uint32_t g_published = 0;  // game thread: the last sequence published
volatile uint32_t g_retired = 0;    // co-op thread: the last sequence retired

bool newer(uint32_t a, uint32_t b)
{
    return static_cast<int32_t>(a - b) > 0;
}

}  // namespace

void remap_publish(uint32_t seq, uint32_t epoch, uint64_t generation, uint32_t mask,
                   const movement::steer_entry* slots, uint64_t now_ms)
{
    remap_record r;
    __builtin_memset(&r, 0, sizeof(r));
    r.head.epoch = epoch;
    r.head.generation = generation;
    r.seq = seq;
    r.mask = mask & ((1u << movement::SLOTS) - 1u);
    r.published_ms = now_ms;
    for (uint32_t k = 0; k < movement::SLOTS; ++k) {
        if ((r.mask & (1u << k)) != 0) {
            r.slot[k] = slots[k];
        }
    }
    g_slot.publish(r);
    __atomic_store_n(&g_published, r.seq, __ATOMIC_RELEASE);
}

bool remap_pending()
{
    return newer(__atomic_load_n(&g_published, __ATOMIC_ACQUIRE), __atomic_load_n(&g_retired, __ATOMIC_ACQUIRE));
}

bool remap_apply(movement::steer_record& r)
{
    remap_record m;
    if (!g_slot.read(m) || m.head.epoch != r.head.epoch || m.head.epoch == 0 ||
        !newer(m.seq, __atomic_load_n(&g_retired, __ATOMIC_ACQUIRE))) {
        return false;
    }
    uint64_t moved[movement::SLOTS] = {};
    for (uint32_t k = 0; k < movement::SLOTS; ++k) {
        if ((m.mask & (1u << k)) == 0) {
            continue;
        }
        r.slot[k] = m.slot[k];
        if ((m.slot[k].flags & movement::ENTRY_ACTIVE) != 0 && m.slot[k].actor != 0) {
            r.active |= 1u << k;
            moved[k] = m.slot[k].actor;
        } else {
            r.active &= ~(1u << k);
        }
    }
    for (uint32_t j = 0; j < movement::SLOTS; ++j) {
        if ((m.mask & (1u << j)) != 0) {
            continue;
        }
        for (uint32_t k = 0; k < movement::SLOTS; ++k) {
            if (moved[k] != 0 && r.slot[j].actor == moved[k]) {
                r.active &= ~(1u << j);
            }
        }
    }
    return true;
}

bool remap_read(remap_record* out)
{
    return g_slot.read(*out);
}

void remap_retire(uint32_t seq)
{
    __atomic_store_n(&g_retired, seq, __ATOMIC_RELEASE);
}

uint32_t remap_retired()
{
    return __atomic_load_n(&g_retired, __ATOMIC_ACQUIRE);
}

void remap_test_reset()
{
    g_slot.withdraw();
    __atomic_store_n(&g_published, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_retired, 0u, __ATOMIC_RELEASE);
}

}  // namespace ilc::party
