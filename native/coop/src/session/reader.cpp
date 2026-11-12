// Ivalice Legacy co-op module: fault-safe game reads (see reader.h).
#include "session/reader.h"

#include "runtime/probe.h"

namespace ilc::session {

bool game_reader::cached(uintptr_t address, size_t count) const
{
    for (uint32_t i = 0; i < cache_used_; ++i) {
        if (address >= cache_[i].begin && address + count <= cache_[i].end) {
            return true;
        }
    }
    return false;
}

bool game_reader::checked_region(uintptr_t address, size_t count)
{
    if (cached(address, count)) {
        return true;
    }
    ++queries_;
    uintptr_t begin = 0;
    uintptr_t end = 0;
    if (!probe_region(reinterpret_cast<const void*>(address), &begin, &end) || address + count > end) {
        return false;
    }
    cache_[cache_next_] = {begin, end};
    cache_next_ = (cache_next_ + 1) % CACHE;
    if (cache_used_ < CACHE) {
        ++cache_used_;
    }
    return true;
}

bool game_reader::load(uintptr_t address, void* out, size_t width)
{
    if (address == 0 || (address & (width - 1)) != 0) {
        return read(address, out, width);  // misaligned (never a game field) or null: the byte path decides
    }
    ++reads_;
    // An aligned scalar never spans two regions (regions are page-granular).
    const bool ok = checked_region(address, width) &&
                    load_from_checked(out, reinterpret_cast<const void*>(address), width);
    if (ok) {
        ++single_loads_;
    } else {
        ++failures_;
    }
    return ok;
}

bool game_reader::read(uintptr_t address, void* out, size_t count)
{
    ++reads_;
    if (count == 0) {
        return true;
    }
    if (address == 0 || address + count < address) {
        ++failures_;
        return false;
    }
    bool ok = false;
    if (cached(address, count)) {
        ok = copy_from_checked(out, reinterpret_cast<const void*>(address), count);
    } else {
        ++queries_;
        uintptr_t begin = 0;
        uintptr_t end = 0;
        if (probe_region(reinterpret_cast<const void*>(address), &begin, &end)) {
            if (address + count <= end) {
                cache_[cache_next_] = {begin, end};
                cache_next_ = (cache_next_ + 1) % CACHE;
                if (cache_used_ < CACHE) {
                    ++cache_used_;
                }
                ok = copy_from_checked(out, reinterpret_cast<const void*>(address), count);
            } else {
                // Spans two regions: the probe checks every page itself.
                ok = copy_from_caller(out, reinterpret_cast<const void*>(address), count);
            }
        }
    }
    if (!ok) {
        ++failures_;
    }
    return ok;
}

}  // namespace ilc::session
