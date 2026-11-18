// Ivalice Legacy co-op module: fault-safe byte copy (see probe.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "runtime/probe.h"

// ilc_probe_copy_raw(dst = RCX, src = RDX, count = R8) -> EAX 0 = copied, 1 = faulted.
// A leaf routine: it touches no stack and no non-volatile register, so it needs no unwind data. Only the two
// instructions between ilc_probe_guard_begin and ilc_probe_guard_end may fault; the handler sends such a
// fault to ilc_probe_fault.
asm(".text\n"
    ".p2align 4\n"
    ".globl ilc_probe_copy_raw\n"
    ".def ilc_probe_copy_raw; .scl 2; .type 32; .endef\n"
    "ilc_probe_copy_raw:\n"
    "    xorl %eax, %eax\n"
    "    testq %r8, %r8\n"
    "    jz 2f\n"
    "1:\n"
    ".globl ilc_probe_guard_begin\n"
    "ilc_probe_guard_begin:\n"
    "    movzbl (%rdx), %r9d\n"
    "    movb %r9b, (%rcx)\n"
    ".globl ilc_probe_guard_end\n"
    "ilc_probe_guard_end:\n"
    "    incq %rdx\n"
    "    incq %rcx\n"
    "    decq %r8\n"
    "    jnz 1b\n"
    "2:\n"
    "    ret\n"
    ".globl ilc_probe_fault\n"
    "ilc_probe_fault:\n"
    "    movl $1, %eax\n"
    "    ret\n");

// ilc_probe_load_raw(dst = RCX, src = RDX, width = R8: 1, 2, 4 or 8) -> EAX 0 = loaded, 1 = faulted.
// One load of exactly that width (a naturally aligned load is a single access the game's own writes cannot tear),
// then one store to the module's buffer. Only the instructions between ilc_probe_load_begin and ilc_probe_load_end
// may fault; the handler sends such a fault to ilc_probe_fault. A leaf like the copy loop: no stack, no
// non-volatile register.
asm(".text\n"
    ".p2align 4\n"
    ".globl ilc_probe_load_raw\n"
    ".def ilc_probe_load_raw; .scl 2; .type 32; .endef\n"
    "ilc_probe_load_raw:\n"
    "    cmpq $8, %r8\n"
    "    je .Lilc_load8\n"
    "    cmpq $4, %r8\n"
    "    je .Lilc_load4\n"
    "    cmpq $2, %r8\n"
    "    je .Lilc_load2\n"
    "    cmpq $1, %r8\n"
    "    jne ilc_probe_fault\n"
    ".globl ilc_probe_load_begin\n"
    "ilc_probe_load_begin:\n"
    "    movzbl (%rdx), %eax\n"
    "    movb %al, (%rcx)\n"
    "    jmp .Lilc_load_done\n"
    ".Lilc_load2:\n"
    "    movzwl (%rdx), %eax\n"
    "    movw %ax, (%rcx)\n"
    "    jmp .Lilc_load_done\n"
    ".Lilc_load4:\n"
    "    movl (%rdx), %eax\n"
    "    movl %eax, (%rcx)\n"
    "    jmp .Lilc_load_done\n"
    ".Lilc_load8:\n"
    "    movq (%rdx), %rax\n"
    "    movq %rax, (%rcx)\n"
    ".globl ilc_probe_load_end\n"
    "ilc_probe_load_end:\n"
    ".Lilc_load_done:\n"
    "    xorl %eax, %eax\n"
    "    ret\n");

extern "C" {
int ilc_probe_copy_raw(void* dst, const void* src, size_t count);
int ilc_probe_load_raw(void* dst, const void* src, size_t width);
extern const char ilc_probe_guard_begin[];
extern const char ilc_probe_guard_end[];
extern const char ilc_probe_load_begin[];
extern const char ilc_probe_load_end[];
extern const char ilc_probe_fault[];
}

namespace {

enum : long { NOT_INSTALLED = 0, INSTALLING = 1, READY = 2, FAILED = 3 };

long g_state = NOT_INSTALLED;
void* g_handler = nullptr;

LONG CALLBACK probe_handler(EXCEPTION_POINTERS* info)
{
    // Guard-page hits are not ours to handle: the guard belongs to its owner. caller_range_usable() keeps the
    // probe away from guard pages in the first place.
    const DWORD code = info->ExceptionRecord->ExceptionCode;
    if (code != EXCEPTION_ACCESS_VIOLATION && code != EXCEPTION_IN_PAGE_ERROR && code != EXCEPTION_GUARD_PAGE) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    const DWORD64 rip = info->ContextRecord->Rip;
    const bool in_copy = rip >= reinterpret_cast<DWORD64>(ilc_probe_guard_begin) &&
                         rip < reinterpret_cast<DWORD64>(ilc_probe_guard_end);
    const bool in_load = rip >= reinterpret_cast<DWORD64>(ilc_probe_load_begin) &&
                         rip < reinterpret_cast<DWORD64>(ilc_probe_load_end);
    if (!in_copy && !in_load) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    if (code == EXCEPTION_GUARD_PAGE) {
        // Only a checked read (read_game's cached region, copy_from_checked) can get here: the page became a guard
        // page after its region was probed. The system cleared the guard bit on this hit; it belongs to the page's
        // owner (a thread stack grows through it), so it is set again before the read fails.
        const ULONG_PTR at = info->ExceptionRecord->NumberParameters >= 2
                                 ? info->ExceptionRecord->ExceptionInformation[1]
                                 : 0;
        MEMORY_BASIC_INFORMATION page;
        DWORD old = 0;
        if (at != 0 && VirtualQuery(reinterpret_cast<const void*>(at), &page, sizeof(page)) == sizeof(page) &&
            page.State == MEM_COMMIT && (page.Protect & PAGE_GUARD) == 0) {
            VirtualProtect(reinterpret_cast<void*>(at & ~static_cast<ULONG_PTR>(0xFFF)), 1, page.Protect | PAGE_GUARD,
                           &old);
        }
    }
    info->ContextRecord->Rip = reinterpret_cast<DWORD64>(ilc_probe_fault);
    return EXCEPTION_CONTINUE_EXECUTION;
}

bool ensure_handler()
{
    long state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE);
    if (state == READY) {
        return true;
    }
    long expected = NOT_INSTALLED;
    if (__atomic_compare_exchange_n(&g_state, &expected, INSTALLING, false, __ATOMIC_ACQ_REL,
                                    __ATOMIC_ACQUIRE)) {
        void* handler = AddVectoredExceptionHandler(1, probe_handler);
        g_handler = handler;
        __atomic_store_n(&g_state, handler != nullptr ? READY : FAILED, __ATOMIC_RELEASE);
    }
    // Another thread is installing: AddVectoredExceptionHandler takes microseconds.
    while ((state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE)) == INSTALLING) {
        __builtin_ia32_pause();
    }
    return state == READY;
}

// Removes the handler when the module unloads, so no handler ever points into unmapped code.
struct handler_teardown {
    ~handler_teardown()
    {
        if (__atomic_load_n(&g_state, __ATOMIC_ACQUIRE) == READY && g_handler != nullptr) {
            RemoveVectoredExceptionHandler(g_handler);
            g_handler = nullptr;
            __atomic_store_n(&g_state, NOT_INSTALLED, __ATOMIC_RELEASE);
        }
    }
};

handler_teardown g_teardown;

volatile uint64_t g_queries = 0;

// Per call site: VirtualQuery calls and hot-cache misses, keyed by the return address of the probe entry point (an
// offset in this module; 1 = outside it). A fixed open-addressed table filled by compare-and-swap; a site that finds
// it full is counted as lost. Read by the developer report (probe_sites) to name the sites that keep querying.
extern "C" IMAGE_DOS_HEADER __ImageBase;
struct site_slot {
    volatile uint32_t rva;
    uint32_t reserved;
    volatile uint64_t queries;
    volatile uint64_t misses;
};
site_slot g_sites[ilc::PROBE_SITE_SLOTS];
volatile uint64_t g_sites_lost = 0;

void note_site(const void* site, uint64_t queries, uint64_t misses)
{
    const uintptr_t a = reinterpret_cast<uintptr_t>(site);
    const uintptr_t base = reinterpret_cast<uintptr_t>(&__ImageBase);
    const uint32_t rva = a > base && a - base < 0x10000000u ? static_cast<uint32_t>(a - base) : 1u;  // NOTADDR: bound
    const uint32_t start = (rva * 2654435761u) >> 26;   // NOTADDR: Knuth's multiplicative hash, 6 bits
    for (uint32_t k = 0; k < ilc::PROBE_SITE_SLOTS; ++k) {
        site_slot& s = g_sites[(start + k) % ilc::PROBE_SITE_SLOTS];
        uint32_t seen = __atomic_load_n(&s.rva, __ATOMIC_ACQUIRE);
        if (seen == 0) {
            uint32_t expected = 0;
            if (__atomic_compare_exchange_n(&s.rva, &expected, rva, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
                seen = rva;
            } else {
                seen = expected;
            }
        }
        if (seen == rva) {
            if (queries != 0) {
                __atomic_add_fetch(&s.queries, queries, __ATOMIC_RELAXED);
            }
            if (misses != 0) {
                __atomic_add_fetch(&s.misses, misses, __ATOMIC_RELAXED);
            }
            return;
        }
    }
    __atomic_add_fetch(&g_sites_lost, 1, __ATOMIC_RELAXED);
}

void count_query(const void* site)
{
    __atomic_add_fetch(&g_queries, 1, __ATOMIC_RELAXED);
    note_site(site, 1, 0);
}

// One VirtualQuery: the region around `address` and whether the probe may read it (committed, neither PAGE_GUARD nor
// PAGE_NOACCESS). False if the query itself failed (no region known).
bool query_region(const void* address, uintptr_t* begin, uintptr_t* end, bool* usable, const void* site)
{
    MEMORY_BASIC_INFORMATION info;
    count_query(site);
    if (VirtualQuery(address, &info, sizeof(info)) != sizeof(info)) {
        return false;
    }
    *usable = info.State == MEM_COMMIT && (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) == 0;
    *begin = reinterpret_cast<uintptr_t>(info.BaseAddress);
    *end = *begin + info.RegionSize;
    return *end > *begin;
}

// Every page of [address, address + count) is committed and neither PAGE_GUARD nor PAGE_NOACCESS. Read or
// write permission is left to the probe: a fault there is an ordinary access violation and is caught.
bool caller_range_usable(const void* address, size_t count, const void* site)
{
    const uintptr_t start = reinterpret_cast<uintptr_t>(address);
    const uintptr_t end = start + count;
    if (end < start) {
        return false;  // wraps around the address space
    }
    uintptr_t at = start;
    while (at < end) {
        MEMORY_BASIC_INFORMATION info;
        count_query(site);
        if (VirtualQuery(reinterpret_cast<const void*>(at), &info, sizeof(info)) != sizeof(info)) {
            return false;
        }
        if (info.State != MEM_COMMIT || (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) != 0) {
            return false;
        }
        const uintptr_t region_end = reinterpret_cast<uintptr_t>(info.BaseAddress) + info.RegionSize;
        if (region_end <= at) {
            return false;
        }
        at = region_end;
    }
    return true;
}

bool probe_copy(void* dst, const void* src, size_t count, const void* caller_side, const void* site)
{
    if (count == 0) {
        return true;
    }
    if (!caller_range_usable(caller_side, count, site) || !ensure_handler()) {
        return false;
    }
    return ilc_probe_copy_raw(dst, src, count) == 0;
}

}  // namespace

namespace ilc {

bool copy_from_caller(void* dst, const void* src, size_t count)
{
    return probe_copy(dst, src, count, src, __builtin_return_address(0));
}

bool copy_to_caller(void* dst, const void* src, size_t count)
{
    return probe_copy(dst, src, count, dst, __builtin_return_address(0));
}

bool probe_region(const void* address, uintptr_t* begin, uintptr_t* end)
{
    bool usable = false;
    return query_region(address, begin, end, &usable, __builtin_return_address(0)) && usable;
}

bool load_from_caller(void* dst, const void* src, size_t width)
{
    if ((width != 1 && width != 2 && width != 4 && width != 8) ||
        (reinterpret_cast<uintptr_t>(src) & (width - 1)) != 0) {
        return false;
    }
    if (!caller_range_usable(src, width, __builtin_return_address(0)) || !ensure_handler()) {
        return false;
    }
    return ilc_probe_load_raw(dst, src, width) == 0;
}

bool probe_fault_site(uint64_t rip)
{
    return (rip >= reinterpret_cast<uint64_t>(ilc_probe_guard_begin) &&
            rip < reinterpret_cast<uint64_t>(ilc_probe_guard_end)) ||
           (rip >= reinterpret_cast<uint64_t>(ilc_probe_load_begin) &&
            rip < reinterpret_cast<uint64_t>(ilc_probe_load_end));
}

bool load_from_checked(void* dst, const void* src, size_t width)
{
    if ((width != 1 && width != 2 && width != 4 && width != 8) ||
        (reinterpret_cast<uintptr_t>(src) & (width - 1)) != 0 || !ensure_handler()) {
        return false;
    }
    return ilc_probe_load_raw(dst, src, width) == 0;
}

bool copy_from_checked(void* dst, const void* src, size_t count)
{
    if (count == 0) {
        return true;
    }
    if (!ensure_handler()) {
        return false;
    }
    return ilc_probe_copy_raw(dst, src, count) == 0;
}

// ---- hot-path reads (probe.h) ---------------------------------------------------------------------------------------
namespace {

// One per thread that reads through read_game/load_game/store_game/game_region. `owner` is claimed once
// (compare-and-swap from 0); after that only the owning thread touches the rest of its slot, so nothing here takes a
// lock. A thread id the system reuses inherits the slot, which is harmless: the entries are only regions, probed again
// within HOT_TTL_MS. An entry is a region VirtualQuery described: readable (a read inside it skips the query) or not
// (a read starting inside it fails without one: free, reserved, guard or no-access memory).
struct alignas(64) hot_cache {
    uint32_t generation;          // g_hot_generation when the entries were taken
    uint32_t used;                // entries in use
    uint32_t next;                // the entry the next miss replaces once all are used
    uint32_t reserved;
    uint64_t filled_ms;           // GetTickCount64 when the first entry went in (HOT_TTL_MS from then)
    volatile uint64_t hits;       // written by the owner only
    volatile uint64_t misses;
    volatile uint64_t failed;     // reads refused by an unreadable entry
    uintptr_t begin[HOT_REGIONS];
    uintptr_t end[HOT_REGIONS];
    uint8_t bad[HOT_REGIONS];     // 1 = the region is not readable
};

volatile uint32_t g_hot_owner[HOT_SLOTS] = {};  // thread id per slot (0 = free)
hot_cache g_hot[HOT_SLOTS];
volatile uint32_t g_hot_generation = 1;
volatile uint32_t g_hot_enabled = 1;

hot_cache* hot_mine()
{
    if (__atomic_load_n(&g_hot_enabled, __ATOMIC_RELAXED) == 0) {
        return nullptr;
    }
    const uint32_t tid = GetCurrentThreadId();
    for (uint32_t k = 0; k < HOT_SLOTS; ++k) {
        if (__atomic_load_n(&g_hot_owner[k], __ATOMIC_ACQUIRE) == tid) {
            return &g_hot[k];
        }
    }
    for (uint32_t k = 0; k < HOT_SLOTS; ++k) {
        uint32_t expected = 0;
        if (__atomic_load_n(&g_hot_owner[k], __ATOMIC_RELAXED) == 0 &&
            __atomic_compare_exchange_n(&g_hot_owner[k], &expected, tid, false, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
            g_hot[k].used = 0;
            g_hot[k].next = 0;
            g_hot[k].generation = 0;  // filled on first use
            return &g_hot[k];
        }
    }
    return nullptr;  // every slot taken: the uncached path
}

// Drops every entry when the generation moved on or the entries are older than HOT_TTL_MS.
void hot_refresh(hot_cache& c)
{
    const uint32_t generation = __atomic_load_n(&g_hot_generation, __ATOMIC_ACQUIRE);
    if (c.used == 0) {
        c.generation = generation;
        return;
    }
    const uint64_t now = GetTickCount64();
    if (c.generation != generation || now < c.filled_ms || now - c.filled_ms > HOT_TTL_MS) {
        c.used = 0;
        c.next = 0;
        c.generation = generation;
    }
}

// The entry whose region holds address `a`, or -1.
int32_t hot_find(const hot_cache& c, uintptr_t a)
{
    for (uint32_t k = 0; k < c.used; ++k) {
        if (a >= c.begin[k] && a < c.end[k]) {
            return static_cast<int32_t>(k);
        }
    }
    return -1;
}

void hot_drop(hot_cache& c, int32_t k)
{
    if (k < 0 || static_cast<uint32_t>(k) >= c.used) {
        return;
    }
    const uint32_t last = c.used - 1;
    c.begin[k] = c.begin[last];
    c.end[k] = c.end[last];
    c.bad[k] = c.bad[last];
    c.used = last;
    c.next = 0;
}

// Queries the region around `a` and keeps it when it is readable. The entry; -1 when the query failed; -2 when the
// region is not readable (not kept: memory the game commits there later must be readable at once, so an unreadable
// region is asked about again on every read that starts in it).
int32_t hot_probe(hot_cache& c, uintptr_t a, const void* site)
{
    uintptr_t begin = 0;
    uintptr_t end = 0;
    bool usable = false;
    if (!query_region(reinterpret_cast<const void*>(a), &begin, &end, &usable, site)) {
        return -1;
    }
    if (!usable) {
        return -2;
    }
    if (c.used == 0) {
        c.filled_ms = GetTickCount64();
    }
    uint32_t k = c.used;
    if (k < HOT_REGIONS) {
        ++c.used;
    } else {
        k = c.next;
        c.next = (c.next + 1) % HOT_REGIONS;
    }
    c.begin[k] = begin;
    c.end[k] = end;
    c.bad[k] = usable ? 0 : 1;
    return static_cast<int32_t>(k);
}

enum hot_answer { HOT_READ, HOT_REFUSE, HOT_FULL };

// Whether [a, e) may be accessed without a VirtualQuery: HOT_READ (inside a readable region), HOT_REFUSE (starts in an
// unreadable one), HOT_FULL (the uncached path decides: no cache, a region boundary inside the range, or no answer).
hot_answer hot_decide(hot_cache* c, uintptr_t a, uintptr_t e, const void* site, int32_t* entry)
{
    *entry = -1;
    if (c == nullptr || e <= a) {
        return HOT_FULL;
    }
    hot_refresh(*c);
    int32_t k = hot_find(*c, a);
    if (k >= 0) {
        c->hits = c->hits + 1;
    } else {
        c->misses = c->misses + 1;
        note_site(site, 0, 1);
        k = hot_probe(*c, a, site);
        if (k == -2) {
            c->failed = c->failed + 1;
            return HOT_REFUSE;   // what the fully checked path would answer, without its second query
        }
        if (k < 0) {
            return HOT_FULL;
        }
    }
    *entry = k;
    if (c->bad[k] != 0) {
        c->failed = c->failed + 1;
        return HOT_REFUSE;
    }
    return e <= c->end[k] ? HOT_READ : HOT_FULL;
}

// The copy of read_game/store_game: `game` is the game-side address.
bool hot_copy(void* dst, const void* src, size_t count, const void* game, bool write, const void* site)
{
    if (count == 0) {
        return true;
    }
    const uintptr_t a = reinterpret_cast<uintptr_t>(game);
    hot_cache* c = hot_mine();
    int32_t k = -1;
    switch (hot_decide(c, a, a + count, site, &k)) {
    case HOT_READ:
        if (ensure_handler() && ilc_probe_copy_raw(dst, src, count) == 0) {
            return true;
        }
        hot_drop(*c, k);  // freed or re-protected since (or not writable): checked afresh
        break;
    case HOT_REFUSE:
        return false;
    case HOT_FULL:
        break;
    }
    return probe_copy(dst, src, count, write ? dst : src, site);
}

}  // namespace

bool read_game(void* dst, const void* src, size_t count)
{
    return hot_copy(dst, src, count, src, false, __builtin_return_address(0));
}

bool store_game(void* dst, const void* src, size_t count)
{
    return hot_copy(dst, src, count, dst, true, __builtin_return_address(0));
}

bool load_game(void* dst, const void* src, size_t width)
{
    const uintptr_t a = reinterpret_cast<uintptr_t>(src);
    if ((width != 1 && width != 2 && width != 4 && width != 8) || (a & (width - 1)) != 0) {
        return false;
    }
    const void* site = __builtin_return_address(0);
    hot_cache* c = hot_mine();
    int32_t k = -1;
    switch (hot_decide(c, a, a + width, site, &k)) {   // aligned: never two regions
    case HOT_READ:
        if (ensure_handler() && ilc_probe_load_raw(dst, src, width) == 0) {
            return true;
        }
        hot_drop(*c, k);
        break;
    case HOT_REFUSE:
        return false;
    case HOT_FULL:
        break;
    }
    if (!caller_range_usable(src, width, site) || !ensure_handler()) {
        return false;
    }
    return ilc_probe_load_raw(dst, src, width) == 0;
}

bool game_region(const void* address, uintptr_t* begin, uintptr_t* end)
{
    const uintptr_t a = reinterpret_cast<uintptr_t>(address);
    const void* site = __builtin_return_address(0);
    hot_cache* c = hot_mine();
    int32_t k = -1;
    const hot_answer h = hot_decide(c, a, a + 1, site, &k);
    if (h == HOT_REFUSE) {
        return false;
    }
    if (h == HOT_READ) {
        *begin = c->begin[k];
        *end = c->end[k];
        return true;
    }
    bool usable = false;
    return query_region(address, begin, end, &usable, site) && usable;
}

void invalidate_game_regions()
{
    __atomic_add_fetch(&g_hot_generation, 1, __ATOMIC_ACQ_REL);
}

probe_counters probe_stats()
{
    probe_counters out = {};
    out.queries = __atomic_load_n(&g_queries, __ATOMIC_RELAXED);
    for (uint32_t k = 0; k < HOT_SLOTS; ++k) {
        if (__atomic_load_n(&g_hot_owner[k], __ATOMIC_ACQUIRE) != 0) {
            ++out.hot_threads;
            out.hot_hits += __atomic_load_n(&g_hot[k].hits, __ATOMIC_RELAXED);
            out.hot_misses += __atomic_load_n(&g_hot[k].misses, __ATOMIC_RELAXED);
            out.hot_failed += __atomic_load_n(&g_hot[k].failed, __ATOMIC_RELAXED);
        }
    }
    out.generation = __atomic_load_n(&g_hot_generation, __ATOMIC_RELAXED);
    out.sites_lost = __atomic_load_n(&g_sites_lost, __ATOMIC_RELAXED);
    return out;
}

uint32_t probe_sites(probe_site* out, uint32_t cap)
{
    // The `cap` sites with the most queries + misses (a selection over the fixed table).
    uint8_t taken[PROBE_SITE_SLOTS] = {};
    uint32_t n = 0;
    for (; n < cap; ++n) {
        int32_t best = -1;
        uint64_t best_total = 0;
        for (uint32_t k = 0; k < PROBE_SITE_SLOTS; ++k) {
            if (taken[k] != 0 || __atomic_load_n(&g_sites[k].rva, __ATOMIC_ACQUIRE) == 0) {
                continue;
            }
            const uint64_t total = __atomic_load_n(&g_sites[k].queries, __ATOMIC_RELAXED) +
                                   __atomic_load_n(&g_sites[k].misses, __ATOMIC_RELAXED);
            if (best < 0 || total > best_total) {
                best = static_cast<int32_t>(k);
                best_total = total;
            }
        }
        if (best < 0) {
            break;
        }
        taken[best] = 1;
        out[n].rva = __atomic_load_n(&g_sites[best].rva, __ATOMIC_RELAXED);
        out[n].queries = __atomic_load_n(&g_sites[best].queries, __ATOMIC_RELAXED);
        out[n].misses = __atomic_load_n(&g_sites[best].misses, __ATOMIC_RELAXED);
    }
    return n;
}

void test_set_hot_reads(bool on)
{
    __atomic_store_n(&g_hot_enabled, on ? 1u : 0u, __ATOMIC_RELAXED);
    invalidate_game_regions();
}

}  // namespace ilc
