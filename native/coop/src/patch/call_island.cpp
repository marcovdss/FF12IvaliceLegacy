// Ivalice Legacy co-op module: call islands (see call_island.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>

#include <string.h>

#include "patch/call_island.h"

extern "C" {
void* volatile ilc_call_slot[ilc::patch::island::ROWS];  // read by ilc_call_entry_k
extern void* const ilc_call_entry_table[];
extern void* const ilc_call_slow_table[];
}

namespace ilc::patch::island {
namespace {

constexpr uint32_t MAX_THREADS = 1024;
constexpr uint32_t FREEZE_ATTEMPTS = 8;
constexpr uint32_t FREEZE_DELAY_MS = 1;
constexpr uintptr_t GRANULE = 0x10000;
constexpr size_t REGION = 0x1000;
constexpr uint64_t REACH = 0x7FFF0000ull;  // NOTADDR: rel32 reach less a 64 KiB margin

uint8_t* g_region = nullptr;
uintptr_t g_base = 0;
bool g_prepared[ROWS];
bool g_committed[ROWS];
uint64_t g_callee[ROWS];
stats_view g_stats = {};

uint32_t g_fail_write_row = SITE_COUNT;    // test injection: this row's next rel32 write fails
uint32_t g_fail_restore_row = SITE_COUNT;  // test injection: this row's restore after a failed commit fails

// Freeze working set: static, nothing is allocated while threads are suspended.
DWORD g_ids[MAX_THREADS];
HANDLE g_handles[MAX_THREADS];

uint64_t call_va(uintptr_t base, uint32_t row)
{
    return base + SITES[row].rva + call_offset(SITES[row]);
}

uint8_t* island_of(uint32_t k)
{
    return g_region + static_cast<size_t>(k) * ISLAND_STRIDE;
}

bool rel32_reaches(uint64_t from_next, uint64_t to)
{
    const int64_t d = static_cast<int64_t>(to - from_next);
    return d >= INT32_MIN && d <= INT32_MAX;
}

// A free, 64 KiB-aligned spot within rel32 reach of every call site we will write.
uint8_t* allocate_near(uint64_t lo_site, uint64_t hi_site)
{
    // Every displacement is (island - (call + 5)); keep a page of margin on both ends.
    const uint64_t floor = hi_site > REACH ? hi_site - REACH : GRANULE;
    const uint64_t ceiling = lo_site + REACH - REGION;
    // Search outward from just above the image end, then below the image.
    const uint64_t start = (hi_site + GRANULE) & ~(GRANULE - 1);
    for (uint64_t at = start; at < ceiling; at += GRANULE) {
        MEMORY_BASIC_INFORMATION info;
        if (VirtualQuery(reinterpret_cast<void*>(at), &info, sizeof(info)) != sizeof(info)) {
            break;
        }
        if (info.State != MEM_FREE) {
            const uint64_t next = reinterpret_cast<uint64_t>(info.BaseAddress) + info.RegionSize;
            at = ((next + GRANULE - 1) & ~(GRANULE - 1)) - GRANULE;
            continue;
        }
        void* p = VirtualAlloc(reinterpret_cast<void*>(at), REGION, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (p != nullptr) {
            return static_cast<uint8_t*>(p);
        }
    }
    for (uint64_t at = (lo_site & ~(GRANULE - 1)) - GRANULE; at >= floor && at >= GRANULE; at -= GRANULE) {
        MEMORY_BASIC_INFORMATION info;
        if (VirtualQuery(reinterpret_cast<void*>(at), &info, sizeof(info)) != sizeof(info) ||
            info.State != MEM_FREE) {
            continue;
        }
        void* p = VirtualAlloc(reinterpret_cast<void*>(at), REGION, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
        if (p != nullptr) {
            return static_cast<uint8_t*>(p);
        }
    }
    return nullptr;
}

// Suspends every other thread of the process; true if none has its instruction pointer strictly inside a rel32
// listed in `inside` (a thread exactly at a call instruction is fine: it will run the whole new instruction).
// On false every thread is resumed again. `count` receives the number suspended.
enum freeze_result { FREEZE_OK, FREEZE_BUSY, FREEZE_FAILED };

uint32_t g_frozen = 0;

void thaw()
{
    for (uint32_t k = 0; k < g_frozen; ++k) {
        if (g_handles[k] != nullptr) {
            ResumeThread(g_handles[k]);
            CloseHandle(g_handles[k]);
            g_handles[k] = nullptr;
        }
    }
    g_frozen = 0;
}

freeze_result freeze(const uint64_t* rel_start, uint32_t rels)
{
    const DWORD self = GetCurrentThreadId();
    const DWORD pid = GetCurrentProcessId();
    uint32_t count = 0;
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        return FREEZE_FAILED;
    }
    THREADENTRY32 entry;
    entry.dwSize = sizeof(entry);
    bool listed = Thread32First(snapshot, &entry) != FALSE;
    bool overflow = false;
    while (listed) {
        if (entry.dwSize >= offsetof(THREADENTRY32, th32OwnerProcessID) + sizeof(DWORD) &&
            entry.th32OwnerProcessID == pid && entry.th32ThreadID != self) {
            if (count == MAX_THREADS) {
                overflow = true;
                break;
            }
            g_ids[count++] = entry.th32ThreadID;
        }
        entry.dwSize = sizeof(entry);
        listed = Thread32Next(snapshot, &entry) != FALSE;
    }
    CloseHandle(snapshot);
    if (overflow) {
        return FREEZE_FAILED;
    }
    bool failed = false;
    g_frozen = count;
    for (uint32_t k = 0; k < count; ++k) {
        HANDLE thread = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE,
                                   g_ids[k]);
        const bool gone = thread == nullptr && GetLastError() == ERROR_INVALID_PARAMETER;
        if (thread != nullptr && SuspendThread(thread) == static_cast<DWORD>(-1)) {
            CloseHandle(thread);
            thread = nullptr;
        }
        g_handles[k] = thread;
        if (thread == nullptr && !gone) {
            failed = true;  // exists, but its position is unknown
        }
    }
    if (failed) {
        thaw();
        return FREEZE_FAILED;
    }
    g_stats.threads_frozen = count;
    for (uint32_t k = 0; k < count; ++k) {
        if (g_handles[k] == nullptr) {
            continue;
        }
        alignas(16) CONTEXT context;
        memset(&context, 0, sizeof(context));
        context.ContextFlags = CONTEXT_CONTROL;
        if (!GetThreadContext(g_handles[k], &context)) {
            thaw();
            return FREEZE_FAILED;
        }
        for (uint32_t r = 0; r < rels; ++r) {
            if (context.Rip > rel_start[r] && context.Rip < rel_start[r] + 5) {
                thaw();
                return FREEZE_BUSY;
            }
        }
    }
    return FREEZE_OK;
}

bool write_image(uint64_t address, const uint8_t* bytes, uint32_t n)
{
    DWORD old;
    if (!VirtualProtect(reinterpret_cast<void*>(address), n, PAGE_EXECUTE_READWRITE, &old)) {
        return false;
    }
    memcpy(reinterpret_cast<void*>(address), bytes, n);
    DWORD ignored;
    VirtualProtect(reinterpret_cast<void*>(address), n, old, &ignored);
    return true;
}

int32_t pinned_rel(uint32_t row);

bool write_rel(uint32_t k, int32_t rel, uint32_t fail_row)
{
    if (row_of(k) == fail_row) {
        return false;  // test injection
    }
    return write_image(call_va(g_base, row_of(k)) + 1, reinterpret_cast<const uint8_t*>(&rel), 4);
}

// Writes the rel32 of every row in `rows` (value from `pick`) inside one freeze. `done[k]` = row k now holds the
// new value. If a write fails and `undo` is set, the rows already written in this freeze get their pinned rel32
// back before the threads resume (all or nothing); a row whose undo also fails stays in `done` (it still points
// at its island, so the caller keeps the page).
template <typename Pick>
int32_t freeze_and_write(const bool rows[ROWS], Pick pick, bool done[ROWS], bool undo)
{
    for (uint32_t k = 0; k < ROWS; ++k) {
        done[k] = false;
    }
    uint64_t starts[ROWS];
    uint32_t n = 0;
    for (uint32_t k = 0; k < ROWS; ++k) {
        if (rows[k]) {
            starts[n++] = call_va(g_base, row_of(k));
        }
    }
    if (n == 0) {
        return ISLAND_OK;
    }
    for (uint32_t attempt = 0; attempt < FREEZE_ATTEMPTS; ++attempt) {
        const freeze_result f = freeze(starts, n);
        if (f == FREEZE_FAILED) {
            return ISLAND_FREEZE_FAILED;
        }
        if (f == FREEZE_BUSY) {
            ++g_stats.busy_attempts;
            Sleep(FREEZE_DELAY_MS);
            continue;
        }
        bool ok = true;
        for (uint32_t k = 0; k < ROWS && ok; ++k) {
            if (rows[k]) {
                ok = write_rel(k, pick(k), g_fail_write_row);
                done[k] = ok;
            }
        }
        if (!ok && undo) {
            for (uint32_t k = 0; k < ROWS; ++k) {
                if (done[k] && write_rel(k, pinned_rel(row_of(k)), g_fail_restore_row)) {
                    done[k] = false;
                }
            }
        }
        FlushInstructionCache(GetCurrentProcess(), nullptr, 0);
        thaw();
        return ok ? ISLAND_OK : ISLAND_WRITE_FAILED;
    }
    return ISLAND_BUSY;
}

int32_t pinned_rel(uint32_t row)
{
    return static_cast<int32_t>(callee_rva(row) - (SITES[row].rva + call_offset(SITES[row]) + 5));
}

}  // namespace

int32_t prepare(uintptr_t base, const bool skip[SITE_COUNT], uint32_t fail_row)
{
    g_base = base;
    uint64_t lo = UINT64_MAX, hi = 0;
    uint32_t wanted = 0;
    for (uint32_t k = 0; k < ROWS; ++k) {
        const uint32_t row = row_of(k);
        g_prepared[k] = false;
        g_committed[k] = false;
        if (skip[row]) {
            continue;
        }
        const uint64_t at = call_va(base, row);
        lo = at < lo ? at : lo;
        hi = at > hi ? at : hi;
        ++wanted;
    }
    if (wanted == 0) {
        return ISLAND_OK;
    }
    if (g_region == nullptr) {
        g_region = allocate_near(lo, hi);
        if (g_region == nullptr) {
            return ISLAND_NO_MEMORY;
        }
        memset(g_region, 0xCC, REGION);
    } else {
        DWORD old;
        if (!VirtualProtect(g_region, REGION, PAGE_READWRITE, &old)) {
            return ISLAND_NO_MEMORY;
        }
    }
    g_stats.region = reinterpret_cast<uint64_t>(g_region);
    for (uint32_t k = 0; k < ROWS; ++k) {
        const uint32_t row = row_of(k);
        if (skip[row]) {
            continue;
        }
        if (row == fail_row) {
            return ISLAND_NO_MEMORY;  // test injection: this row's island "could not be made"
        }
        uint8_t* island = island_of(k);
        if (!rel32_reaches(call_va(base, row) + 5, reinterpret_cast<uint64_t>(island))) {
            return ISLAND_OUT_OF_RANGE;
        }
        static const uint8_t jmp_abs[6] = {0xFF, 0x25, 0x00, 0x00, 0x00, 0x00};
        memcpy(island, jmp_abs, sizeof(jmp_abs));
        const uint64_t target = reinterpret_cast<uint64_t>(ilc_call_entry_table[k]);
        memcpy(island + 6, &target, 8);
        g_callee[k] = base + callee_rva(row);
        __atomic_store_n(&ilc_call_slot[k], reinterpret_cast<void*>(g_callee[k]), __ATOMIC_RELEASE);
        g_prepared[k] = true;
    }
    DWORD old;
    if (!VirtualProtect(g_region, REGION, PAGE_EXECUTE_READ, &old)) {
        return ISLAND_NO_MEMORY;
    }
    FlushInstructionCache(GetCurrentProcess(), g_region, REGION);
    return ISLAND_OK;
}

int32_t commit(uintptr_t base)
{
    (void)base;
    bool done[ROWS];
    const int32_t r = freeze_and_write(g_prepared, [](uint32_t k) {
        const uint32_t row = row_of(k);
        return static_cast<int32_t>(reinterpret_cast<int64_t>(island_of(k)) -
                                    static_cast<int64_t>(call_va(g_base, row) + 5));
    }, done, true);
    // Per row: every rel32 that still points at an island counts as committed, so a revert covers it and the
    // island page is kept, even when the commit as a whole failed.
    bool any = false;
    for (uint32_t k = 0; k < ROWS; ++k) {
        g_committed[k] = done[k];
        any = any || done[k];
    }
    g_stats.commits += (r == ISLAND_OK && any) ? 1 : 0;
    return r;
}

int32_t revert(uintptr_t base, uint32_t keep_row)
{
    (void)base;
    bool rows[ROWS];
    bool any = false;
    for (uint32_t k = 0; k < ROWS; ++k) {
        rows[k] = g_committed[k] && row_of(k) != keep_row;
        any = any || rows[k];
    }
    for (uint32_t k = 0; k < ROWS; ++k) {
        // A thread that still reaches an island (it read the old rel32) goes straight to the callee.
        if (g_prepared[k]) {
            __atomic_store_n(&ilc_call_slot[k], reinterpret_cast<void*>(g_callee[k]), __ATOMIC_RELEASE);
        }
    }
    if (!any) {
        return ISLAND_OK;
    }
    bool done[ROWS];
    const int32_t r = freeze_and_write(rows, [](uint32_t k) { return pinned_rel(row_of(k)); }, done, false);
    for (uint32_t k = 0; k < ROWS; ++k) {
        g_committed[k] = g_committed[k] && !done[k];  // a row that could not be restored stays committed
    }
    g_stats.reverts += r == ISLAND_OK ? 1 : 0;
    return r;
}

void release()
{
    for (uint32_t k = 0; k < ROWS; ++k) {
        g_prepared[k] = false;
        g_committed[k] = false;
        g_callee[k] = 0;
        ilc_call_slot[k] = nullptr;
    }
    if (g_region != nullptr) {
        VirtualFree(g_region, 0, MEM_RELEASE);
        g_region = nullptr;
    }
    g_stats = {};
    g_fail_write_row = SITE_COUNT;
    g_fail_restore_row = SITE_COUNT;
}

bool prepared(uint32_t row)
{
    const int32_t k = index_of(row);
    return k >= 0 && g_prepared[k];
}

bool committed()
{
    for (uint32_t k = 0; k < ROWS; ++k) {
        if (g_committed[k]) {
            return true;
        }
    }
    return false;
}

bool holds_island(uintptr_t base, uint32_t row)
{
    const int32_t k = index_of(row);
    if (k < 0 || !g_prepared[k] || g_region == nullptr) {
        return false;
    }
    const uint8_t* call = reinterpret_cast<const uint8_t*>(call_va(base, row));
    int32_t rel;
    memcpy(&rel, call + 1, 4);
    if (call[0] != 0xE8 || call_va(base, row) + 5 + static_cast<int64_t>(rel) != reinterpret_cast<uint64_t>(island_of(k))) {
        return false;
    }
    const uint8_t* island = island_of(k);
    uint64_t target;
    memcpy(&target, island + 6, 8);
    return island[0] == 0xFF && island[1] == 0x25 && island[2] == 0 && island[3] == 0 && island[4] == 0 &&
           island[5] == 0 && target == reinterpret_cast<uint64_t>(ilc_call_entry_table[k]);
}

uint64_t island_address(uint32_t row)
{
    const int32_t k = index_of(row);
    return k >= 0 && g_prepared[k] ? reinterpret_cast<uint64_t>(island_of(k)) : 0;
}

void* entry(uint32_t row)
{
    const int32_t k = index_of(row);
    return k >= 0 ? ilc_call_entry_table[k] : nullptr;
}

void* slow(uint32_t row)
{
    const int32_t k = index_of(row);
    return k >= 0 ? ilc_call_slow_table[k] : nullptr;
}

uint64_t callee(uint32_t row)
{
    const int32_t k = index_of(row);
    return k >= 0 ? g_callee[k] : 0;
}

void sync(uint32_t row, bool open)
{
    const int32_t k = index_of(row);
    if (k < 0 || !g_prepared[k]) {
        return;
    }
    void* target = open ? ilc_call_slow_table[k] : reinterpret_cast<void*>(g_callee[k]);
    __atomic_store_n(&ilc_call_slot[k], target, __ATOMIC_RELEASE);
}

void test_fail_write(uint32_t row, uint32_t restore_row)
{
    g_fail_write_row = row;
    g_fail_restore_row = restore_row;
}

stats_view stats()
{
    return g_stats;
}

}  // namespace ilc::patch::island
