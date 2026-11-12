// Ivalice Legacy co-op module: fault-safe reads of game memory for the session core.
//
// Every read goes through the fault-safe probe (runtime/probe.h): an unmapped, freed, guard or no-access address
// gives `false`, never a fault. Game state changes under our feet (the game thread writes while the co-op thread
// reads), so callers treat a failed or implausible read as UNKNOWN, never as a fact.
//
// Scalars (u8/u16/u32/u64/ptr) are read with one load of their own width when naturally aligned, so a field the
// game is writing reads as the old or the new value, never a mix (HP 0x100 -> 0xFF must not read as 0). Byte
// ranges (layouts, positions) go through the byte copy; their callers check coherence themselves.
//
// A reader is used for one pass (one session tick) on one thread. It caches the few readable regions it has
// already checked in that pass, so the actor scan does not ask the kernel about the same heap region thousands of
// times; a cached region is still read through the fault handler, and the cache dies with the pass.
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "session/game_facts.h"

namespace ilc::session {

class game_reader {
public:
    // image_base: where the game image is mapped (the game: 0x120000; a host test: its synthetic image).
    explicit game_reader(uintptr_t image_base) : base_(image_base) {}

    bool read(uintptr_t address, void* out, size_t count);

    // A global of the pinned build, given as its VA.
    uintptr_t global(uintptr_t va) const { return base_ + (va - facts::IMAGE_VA); }

    bool u8(uintptr_t address, uint8_t* out) { return load(address, out, 1); }
    bool u16(uintptr_t address, uint16_t* out) { return load(address, out, 2); }
    bool u32(uintptr_t address, uint32_t* out) { return load(address, out, 4); }
    bool u64(uintptr_t address, uint64_t* out) { return load(address, out, 8); }
    bool ptr(uintptr_t address, uintptr_t* out)
    {
        uint64_t v = 0;
        const bool ok = load(address, &v, 8);
        *out = ok ? static_cast<uintptr_t>(v) : 0;
        return ok;
    }

    uint32_t single_loads() const { return single_loads_; }

    uint32_t reads() const { return reads_; }
    uint32_t failures() const { return failures_; }
    uint32_t region_queries() const { return queries_; }

private:
    static constexpr uint32_t CACHE = 8;
    struct region {
        uintptr_t begin;
        uintptr_t end;
    };
    bool cached(uintptr_t address, size_t count) const;
    bool checked_region(uintptr_t address, size_t count);  // cached, or queried now and cached
    bool load(uintptr_t address, void* out, size_t width);  // one access of that width (aligned), else read()

    uintptr_t base_;
    region cache_[CACHE] = {};
    uint32_t cache_used_ = 0;
    uint32_t cache_next_ = 0;
    uint32_t reads_ = 0;
    uint32_t failures_ = 0;
    uint32_t queries_ = 0;
    uint32_t single_loads_ = 0;
};

}  // namespace ilc::session
