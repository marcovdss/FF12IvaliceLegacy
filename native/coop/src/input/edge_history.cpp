// Ivalice Legacy co-op module: per-consumer edge cursors (see edge_history.h).
#include "input/edge_history.h"

#include <string.h>

namespace ilc::edges {
namespace {

constexpr uint32_t MASK = HISTORY - 1;
static_assert((HISTORY & MASK) == 0, "power of two");

struct seat_edges {
    uint32_t pad_pressed;
    uint32_t pad_released;
    uint32_t pad_held;
    uint32_t act_pressed;
    uint32_t act_released;
    uint32_t act_repeated;
    uint32_t act_held;
};

struct entry {
    volatile uint32_t ver;  // odd while the writer fills it
    uint32_t run;
    uint32_t seq;
    uint32_t reserved;
    seat_edges seat[SEATS];
};

entry g_ring[HISTORY];
volatile uint32_t g_run = 0;
volatile uint32_t g_newest = 0;  // seq of the newest complete entry, 0 = none

// A coherent copy of the entry for `seq` in `run`. 1 = copied; 0 = no tick with that number (gap); -1 = lost
// (overwritten by a newer tick or being overwritten now).
int copy_entry(uint32_t run, uint32_t seq, entry* out)
{
    const entry& e = g_ring[seq & MASK];
    const uint32_t first = __atomic_load_n(&e.ver, __ATOMIC_ACQUIRE);
    if (first & 1u) {
        return -1;
    }
    out->run = e.run;
    out->seq = e.seq;
    memcpy(out->seat, e.seat, sizeof(out->seat));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    if (__atomic_load_n(&e.ver, __ATOMIC_RELAXED) != first) {
        return -1;
    }
    if (out->run != run) {
        return 0;
    }
    const int32_t age = static_cast<int32_t>(out->seq - seq);
    if (age == 0) {
        return 1;
    }
    return age > 0 ? -1 : 0;
}

}  // namespace

void reset(uint32_t run)
{
    __atomic_store_n(&g_newest, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_run, run, __ATOMIC_RELEASE);
}

void record(uint32_t seq, const ilc_seat_input_frame* frames, const ilc_seat_action_frame* actions)
{
    const uint32_t run = __atomic_load_n(&g_run, __ATOMIC_RELAXED);
    if (run == 0 || seq == 0) {
        return;
    }
    entry& e = g_ring[seq & MASK];
    const uint32_t odd = (e.ver + 1u) | 1u;
    __atomic_store_n(&e.ver, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    e.run = run;
    e.seq = seq;
    for (uint32_t s = 0; s < SEATS; ++s) {
        seat_edges& d = e.seat[s];
        d.pad_pressed = frames[s].pressed;
        d.pad_released = frames[s].released;
        d.pad_held = frames[s].held;
        d.act_pressed = actions[s].pressed;
        d.act_released = actions[s].released;
        d.act_repeated = actions[s].repeated;
        d.act_held = actions[s].held;
    }
    __atomic_store_n(&e.ver, odd + 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_newest, seq, __ATOMIC_RELEASE);
}

bool since(edge_cursor& cursor, uint32_t seat, edge_delta* out)
{
    if (seat < 1 || seat > SEATS) {
        return false;
    }
    const uint32_t run = __atomic_load_n(&g_run, __ATOMIC_ACQUIRE);
    const uint32_t newest = __atomic_load_n(&g_newest, __ATOMIC_ACQUIRE);
    if (run == 0 || newest == 0) {
        return false;
    }
    memset(out, 0, sizeof(*out));
    const uint32_t s = seat - 1;
    entry top;
    if (copy_entry(run, newest, &top) != 1) {
        return false;  // the service restarted between the two loads; try again next time
    }
    out->to_seq = newest;
    out->pad_held = top.seat[s].pad_held;
    out->act_held = top.seat[s].act_held;
    if (cursor.run != run) {
        memset(&cursor, 0, sizeof(cursor));
        cursor.run = run;
    }
    const uint32_t last = cursor.seq[s];
    cursor.seq[s] = newest;
    if (last == 0 || static_cast<int32_t>(newest - last) < 0) {
        out->reseeded = 1;  // first use (or a number from before a restart): no edges, never a replay
        return true;
    }
    uint32_t first = last + 1;
    if (newest - last > HISTORY) {
        out->overrun = 1;
        first = newest - HISTORY + 1;
    }
    for (uint32_t k = first; static_cast<int32_t>(newest - k) >= 0; ++k) {
        entry e;
        const int got = copy_entry(run, k, &e);
        if (got < 0) {
            out->overrun = 1;  // overwritten while we were behind
            continue;
        }
        if (got == 0) {
            continue;  // no tick carried this number
        }
        const seat_edges& d = e.seat[s];
        if (out->ticks == 0) {
            out->from_seq = k;
        }
        ++out->ticks;
        out->pad_pressed |= d.pad_pressed;
        out->pad_released |= d.pad_released;
        out->act_pressed |= d.act_pressed;
        out->act_released |= d.act_released;
        out->act_repeated |= d.act_repeated;
    }
    return true;
}

uint32_t newest_seq()
{
    return __atomic_load_n(&g_newest, __ATOMIC_ACQUIRE);
}

uint32_t current_run()
{
    return __atomic_load_n(&g_run, __ATOMIC_ACQUIRE);
}

void test_reset()
{
    memset(g_ring, 0, sizeof(g_ring));
    __atomic_store_n(&g_newest, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_run, 0u, __ATOMIC_RELEASE);
}

}  // namespace ilc::edges
