// Ivalice Legacy co-op module: fault-safe copies to and from a caller's buffer.
//
// Both functions copy with one hand-written loop whose load and store instructions are covered by a vectored
// exception handler. An access violation or in-page error on either instruction resumes at the loop's failure
// exit, so a bad caller buffer gives `false` instead of a crash. The handler acts only on faults whose RIP is
// inside that loop, and never on a guard-page hit: a guard page belongs to its owner (a thread stack, for
// example). The caller's range is therefore checked with VirtualQuery first, and committed pages with
// PAGE_GUARD or PAGE_NOACCESS are refused without being touched.
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

}  // namespace ilc
