// Ivalice Legacy co-op module: the fail-open detour template every subsystem's handlers are built from.
//
// The dispatcher (patch_engine.cpp) already guarantees, before any handler runs: the site's gate is open, the
// mode lets it run (LIVE, or a CORE row), no fault is latched for its subsystem, and the liveness record is a
// coherent, fresh sample with armed == 1 and a non-zero epoch. This header adds what every subsystem needs on top:
//
//  * record_slot<T>: the subsystem's latest published state ("record"), written by one thread (the co-op thread)
//    under a seqlock whose sequence only grows (withdraw is a write too) and copied out by detours on game threads. No allocation and no reclamation problem: the
//    reader copies the bytes and discards a torn copy. A reader never waits.
//  * fail_open<Record, Slot, Body>: the handler shape. It re-checks that the subsystem may act now
//    (subsystem_live), copies the record, and requires the record to belong to the epoch the dispatcher sampled.
//    Anything missing, torn or stale -> RUN_ORIGINAL (stock), counted per row as "declined". Only then Body runs,
//    with the copy. Body decides RUN_ORIGINAL or RESUME itself and must not block, allocate or raise.
//
// An epoch match is not a valid world. The session epoch also numbers the invalid stretches between worlds
// (load, title screen, cutscene), and the liveness record is fresh through them. A Body (or a game-thread op)
// that touches world objects must also check the session record's GATE_WORLD (and its own gates); world-requiring
// game-thread ops are refused by the queue outside a valid world (runtime/game_thread.h, OPF_ANY_WORLD).
//
// The contract for a Body: read only the saved registers and the record copy; reach game memory through the
// fault-safe probe. A Body on a MinHook row (mid-function) never calls game functions (those are UT-01 jobs,
// src/runtime/game_thread.h). A Body on a call-type row (call_island.h) runs at a call boundary with static
// unwind data and may call a game function when its contract needs one (MV-04 calls the game's facing setter);
// it must still not block, allocate or raise. The stub saves
// the general registers, the flags and XMM0-5, never the YMM upper halves: UCRT memcpy/memset may use AVX and
// clobber them. Harmless at call-type sites; a Body at a mid-function site must not call UCRT string functions.
// record_slot copies with __builtin_memcpy of a fixed size; GCC may still emit a UCRT memcpy call for a large
// record, so the same rule applies to the slot's read at a mid-function site: keep such records small.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <type_traits>

#include "patch/patch_engine.h"

namespace ilc::patch {

// Every record starts with this head.
struct record_head {
    uint32_t epoch;       // the world the co-op thread built the record for; must equal the dispatcher's sample
    uint32_t flags;       // subsystem-defined
    uint64_t generation;  // the session generation it was built from (subsystem-defined use)
};

template <typename T>
struct record_slot {
    static_assert(std::is_trivially_copyable_v<T>, "records are copied as bytes");
    static_assert(sizeof(T) <= 512, "records are copied onto a game thread's stack");

    // seq only ever grows (by 2 per write): odd = the writer is inside, even = stable. It is never reset, so a
    // reader preempted across any number of writes (withdraw and republish included) always sees a different seq
    // and discards its copy. `valid` is written inside the same seqlock: 0 = never published or withdrawn.
    volatile uint32_t seq;
    volatile uint32_t valid;
    T value;

    // One writer (the co-op thread).
    void publish(const T& next)
    {
        const uint32_t odd = begin_write();
        __builtin_memcpy(const_cast<T*>(&value), &next, sizeof(T));
        valid = 1;
        end_write(odd);
    }

    // Withdraw the record (e.g. the subsystem disarms itself): readers fall back to stock.
    void withdraw()
    {
        const uint32_t odd = begin_write();
        valid = 0;
        end_write(odd);
    }

    // Any thread, wait-free. False if never published, withdrawn or torn.
    bool read(T& out) const
    {
        const uint32_t first = __atomic_load_n(&seq, __ATOMIC_ACQUIRE);
        if ((first & 1u) != 0) {
            return false;
        }
        const uint32_t ok = valid;
        __builtin_memcpy(&out, const_cast<const T*>(&value), sizeof(T));
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        return __atomic_load_n(&seq, __ATOMIC_RELAXED) == first && ok == 1;
    }

private:
    uint32_t begin_write()
    {
        const uint32_t odd = (seq + 1u) | 1u;  // seq + 1 (the writer leaves it even); odd even if it was not
        __atomic_store_n(&seq, odd, __ATOMIC_RELAXED);
        __atomic_thread_fence(__ATOMIC_RELEASE);
        return odd;
    }

    void end_write(uint32_t odd)
    {
        __atomic_store_n(&seq, odd + 1u, __ATOMIC_RELEASE);
    }
};

template <typename Record, record_slot<Record>& Slot, int32_t (*Body)(ctx*, const Record&)>
int32_t fail_open(ctx* c, uint32_t epoch)
{
    static_assert(offsetof(Record, head) == 0, "a record starts with its record_head");
    const uint32_t row = static_cast<uint32_t>(c->index);  // the stub's row (dispatch has checked it)
    if (row >= SITE_COUNT || !subsystem_live(SITES[row].subsystem)) {
        note_declined(row);
        return RUN_ORIGINAL;
    }
    Record copy;
    if (!Slot.read(copy) || copy.head.epoch != epoch || copy.head.epoch == 0) {
        note_declined(row);
        return RUN_ORIGINAL;
    }
    return Body(c, copy);
}

}  // namespace ilc::patch
