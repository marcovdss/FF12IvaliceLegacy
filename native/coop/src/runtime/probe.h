// Ivalice Legacy co-op module: fault-safe copies to and from a caller's buffer.
//
// Both functions copy with one hand-written loop whose load and store instructions are covered by a vectored
// exception handler. An access violation or in-page error on either instruction resumes at the loop's failure
// exit, so a bad caller buffer gives `false` instead of a crash. The handler acts only on faults whose RIP is
// inside that loop. A guard page belongs to its owner (a thread stack, for example), so the caller's range is
// checked with VirtualQuery first, and committed pages with PAGE_GUARD or PAGE_NOACCESS are refused without being
// touched. Only the checked reads below (which skip that VirtualQuery) can still hit a guard page that appeared
// after their region was probed: the handler then sets the guard bit again (the system cleared it on the hit) and
// the read fails.
//
// The handler is installed on first use (process-wide, first in the chain) and removed when the module
// unloads. If it cannot be installed, nothing is copied and the result is false (fail closed).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ilc {

// dst is the module's own memory; src is the caller's.
bool copy_from_caller(void* dst, const void* src, size_t count);

// dst is the caller's memory; src is the module's own. On false, dst may hold a partial copy.
bool copy_to_caller(void* dst, const void* src, size_t count);

// For readers that make many small reads in one pass (the session core): the committed region around `address`
// if it is readable for the probe (committed, neither PAGE_GUARD nor PAGE_NOACCESS), as [*begin, *end).
bool probe_region(const void* address, uintptr_t* begin, uintptr_t* end);

// copy_from_caller without the VirtualQuery: the caller has just checked the range with probe_region. A fault is
// still caught (the region may have been freed since); a guard page is not, which is why the range must come from
// probe_region and be used only briefly.
bool copy_from_checked(void* dst, const void* src, size_t count);

// One naturally aligned load of 1, 2, 4 or 8 bytes as a single instruction, so a value the game writes
// concurrently is read either before or after the write, never half of each (the byte loop above can tear a
// u32 or u64). False for any other width, a misaligned address, or a fault. load_from_checked skips the
// VirtualQuery like copy_from_checked.
bool load_from_caller(void* dst, const void* src, size_t width);
bool load_from_checked(void* dst, const void* src, size_t width);

// True if a fault at `rip` is one of the probe's own guarded instructions (its handler resumes those). Other
// vectored handlers (runtime/guard.h) leave such faults alone.
bool probe_fault_site(uint64_t rip);

// ---- hot-path reads of game memory ------------------------------------------------------------------------------
// VirtualQuery is not cheap: it walks the page tables to size the region, so one call on a large committed region
// (the game's heaps, its image data) costs tens of microseconds to milliseconds (about 50 us at 16 MiB, 2.6 ms at
// 256 MiB, measured on the dev machine). The detours that run every frame or per actor read game memory through
// these instead of copy_from_caller/load_from_caller: each thread that uses them gets a small cache of probed
// regions (probe_region), and a read inside a cached region skips the VirtualQuery (copy_from_checked /
// load_from_checked: a fault is still caught). A cached region is probed again after HOT_TTL_MS, after a fault in
// it, and after invalidate_game_regions() (the session calls it at every new epoch: an area change frees memory).
// A read that starts in a region VirtualQuery calls unreadable (free, reserved, guard, no access) fails after that one
// query (the region is not kept: the game may commit memory there later). A thread past the HOT_SLOTS that claimed a cache, a read that
// spans two regions, and every read while the cache is switched off (host tests) take the uncached path. Same results as copy_from_caller/load_from_caller: false
// for an unreadable address, never a crash. Never for the caller's buffers of the bridge (those stay fully checked).
constexpr uint32_t HOT_SLOTS = 8;     // threads with a cache of their own
constexpr uint32_t HOT_REGIONS = 32;  // regions per thread (readable ones and unreadable ones)
constexpr uint64_t HOT_TTL_MS = 10000; // a cached region is probed again after this long (0.1.57: 2 s re-probed the
                                       // session reader's ~20 regions ten times a second; faults and epochs still drop)
bool read_game(void* dst, const void* src, size_t count);
bool load_game(void* dst, const void* src, size_t width);
// copy_to_caller for the same hot paths (dst is game memory): a write to a page that is not writable faults, is
// caught and gives false.
bool store_game(void* dst, const void* src, size_t count);
void invalidate_game_regions();
// probe_region through the same cache: the committed region around `address` (a cached one when it holds it).
bool game_region(const void* address, uintptr_t* begin, uintptr_t* end);

struct probe_counters {
    uint64_t queries;      // VirtualQuery calls the probe made (every path)
    uint64_t hot_hits;     // read_game/load_game reads served from a cached region
    uint64_t hot_misses;   // ... that probed a region first (or took the uncached path)
    uint32_t hot_threads;  // caches claimed
    uint32_t generation;   // invalidate_game_regions() count + 1
    uint64_t hot_failed;   // reads refused because their region is not readable (one query, no second one)
    uint64_t sites_lost;   // site notes that found the site table full
};
probe_counters probe_stats();

// Per call site (the return address of the probe entry point, as an offset in this module; 1 = outside it): VirtualQuery
// calls and hot-cache misses. probe_sites writes the `cap` busiest, most first, and returns how many.
constexpr uint32_t PROBE_SITE_SLOTS = 64;
struct probe_site {
    uint32_t rva;
    uint32_t reserved;
    uint64_t queries;
    uint64_t misses;
};
uint32_t probe_sites(probe_site* out, uint32_t cap);

// Host tests only: off = every read_game/load_game takes the uncached path (the 0.1.53 behaviour).
void test_set_hot_reads(bool on);

}  // namespace ilc
