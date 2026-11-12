// Ivalice Legacy co-op module, host tests only: image-twin drills and the game-thread tick exports.
//
//  * Parked threads: real threads of the test process, created suspended and never run while parked; a test sets
//    each one's RIP to an address inside a hook window of the image twin. The patch layer's quiescence probe and
//    MinHook's freeze then see threads sitting in displaced bytes exactly as they would in the game. Released by
//    restoring the thread's original start context, so it starts normally and exits.
//  * Allocation counters: this DLL's own import table entries for the heap, VirtualAlloc/Free, the CRT allocator,
//    SuspendThread/ResumeThread and OpenThread/CloseHandle are pointed at counting wrappers. Everything linked into
//    the DLL (MinHook, the patch layer) calls through them, so the drills count every allocation, every leak and
//    every allocation made while any thread is suspended. The drills' own suspend/resume calls go through
//    GetProcAddress pointers and are never counted.
//  * Quiesce plan: a table of "at this probe phase and attempt, move parked thread i to RIP x", run by the patch
//    layer's test hook between probes.
//  * Game-thread queue, tick and record exports, with test operations and handlers.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "patch/detour.h"
#include "patch/patch_engine.h"
#include "runtime/game_thread.h"
#include "runtime/tick.h"

using namespace ilc;
using namespace ilc::patch;

namespace {

// ---- parked threads ------------------------------------------------------------------------------------------
constexpr uint32_t PARK_MAX = 128;

struct parked {
    HANDLE thread;
    DWORD id;
    alignas(16) CONTEXT start;  // the context it was created with (full), restored on release
};
parked g_parked[PARK_MAX];
uint32_t g_parked_count = 0;

using resume_fn = DWORD(WINAPI*)(HANDLE);
resume_fn g_raw_resume = nullptr;  // never through the import table: not counted

DWORD WINAPI parked_start(void*)
{
    return 0;  // a released thread starts here and exits at once
}

bool context_of(HANDLE thread, CONTEXT* out, DWORD flags)
{
    memset(out, 0, sizeof(*out));
    out->ContextFlags = flags;
    return GetThreadContext(thread, out) != FALSE;
}

// ---- allocation counters -------------------------------------------------------------------------------------
struct alloc_counters {
    volatile int64_t heap_alloc, heap_free, heap_realloc, heap_create, heap_destroy;
    volatile int64_t heap_live_foreign;      // live blocks on heaps this DLL did not create
    volatile int64_t virtual_alloc, virtual_free;
    volatile int64_t crt_alloc, crt_free;    // malloc/calloc/realloc/free
    volatile int64_t allocs_while_suspended; // any of the above allocations while a suspension is outstanding
    volatile int64_t suspend_now, suspend_max, suspends, resumes;
    volatile int64_t thread_opens, handle_closes;
    volatile int64_t installed;              // import slots redirected
};
alloc_counters g_c;

constexpr uint32_t OWN_HEAPS = 8;
HANDLE volatile g_own_heaps[OWN_HEAPS];

bool own_heap(HANDLE heap)
{
    for (uint32_t k = 0; k < OWN_HEAPS; ++k) {
        if (g_own_heaps[k] == heap && heap != nullptr) {
            return true;
        }
    }
    return false;
}

void add(volatile int64_t& v, int64_t d)
{
    __atomic_add_fetch(&v, d, __ATOMIC_RELAXED);
}

void note_alloc()
{
    if (__atomic_load_n(&g_c.suspend_now, __ATOMIC_RELAXED) > 0) {
        add(g_c.allocs_while_suspended, 1);
    }
}

using heap_alloc_fn = LPVOID(WINAPI*)(HANDLE, DWORD, SIZE_T);
using heap_realloc_fn = LPVOID(WINAPI*)(HANDLE, DWORD, LPVOID, SIZE_T);
using heap_free_fn = BOOL(WINAPI*)(HANDLE, DWORD, LPVOID);
using heap_create_fn = HANDLE(WINAPI*)(DWORD, SIZE_T, SIZE_T);
using heap_destroy_fn = BOOL(WINAPI*)(HANDLE);
using virtual_alloc_fn = LPVOID(WINAPI*)(LPVOID, SIZE_T, DWORD, DWORD);
using virtual_free_fn = BOOL(WINAPI*)(LPVOID, SIZE_T, DWORD);
using suspend_fn = DWORD(WINAPI*)(HANDLE);
using open_thread_fn = HANDLE(WINAPI*)(DWORD, BOOL, DWORD);
using close_fn = BOOL(WINAPI*)(HANDLE);
using malloc_fn = void* (*)(size_t);
using calloc_fn = void* (*)(size_t, size_t);
using realloc_fn = void* (*)(void*, size_t);
using free_fn = void (*)(void*);

heap_alloc_fn r_heap_alloc;
heap_realloc_fn r_heap_realloc;
heap_free_fn r_heap_free;
heap_create_fn r_heap_create;
heap_destroy_fn r_heap_destroy;
virtual_alloc_fn r_virtual_alloc;
virtual_free_fn r_virtual_free;
suspend_fn r_suspend, r_resume;
open_thread_fn r_open_thread;
close_fn r_close;
malloc_fn r_malloc;
calloc_fn r_calloc;
realloc_fn r_realloc;
free_fn r_free;

LPVOID WINAPI w_heap_alloc(HANDLE h, DWORD f, SIZE_T n)
{
    note_alloc();
    LPVOID p = r_heap_alloc(h, f, n);
    if (p) {
        add(g_c.heap_alloc, 1);
        if (!own_heap(h)) {
            add(g_c.heap_live_foreign, 1);
        }
    }
    return p;
}
LPVOID WINAPI w_heap_realloc(HANDLE h, DWORD f, LPVOID p, SIZE_T n)
{
    note_alloc();
    LPVOID q = r_heap_realloc(h, f, p, n);
    if (q) {
        add(g_c.heap_realloc, 1);
    }
    return q;
}
BOOL WINAPI w_heap_free(HANDLE h, DWORD f, LPVOID p)
{
    const BOOL ok = r_heap_free(h, f, p);
    if (ok && p) {
        add(g_c.heap_free, 1);
        if (!own_heap(h)) {
            add(g_c.heap_live_foreign, -1);
        }
    }
    return ok;
}
HANDLE WINAPI w_heap_create(DWORD o, SIZE_T i, SIZE_T m)
{
    note_alloc();
    HANDLE h = r_heap_create(o, i, m);
    if (h) {
        add(g_c.heap_create, 1);
        for (uint32_t k = 0; k < OWN_HEAPS; ++k) {
            HANDLE expected = nullptr;
            if (__atomic_compare_exchange_n(&g_own_heaps[k], &expected, h, false, __ATOMIC_ACQ_REL,
                                            __ATOMIC_RELAXED)) {
                break;
            }
        }
    }
    return h;
}
BOOL WINAPI w_heap_destroy(HANDLE h)
{
    const BOOL ok = r_heap_destroy(h);
    if (ok) {
        add(g_c.heap_destroy, 1);
        for (uint32_t k = 0; k < OWN_HEAPS; ++k) {
            HANDLE expected = h;
            __atomic_compare_exchange_n(&g_own_heaps[k], &expected, nullptr, false, __ATOMIC_ACQ_REL,
                                        __ATOMIC_RELAXED);
        }
    }
    return ok;
}
LPVOID WINAPI w_virtual_alloc(LPVOID a, SIZE_T n, DWORD t, DWORD p)
{
    note_alloc();
    LPVOID r = r_virtual_alloc(a, n, t, p);
    if (r && (t & MEM_RESERVE)) {
        add(g_c.virtual_alloc, 1);
    }
    return r;
}
BOOL WINAPI w_virtual_free(LPVOID a, SIZE_T n, DWORD t)
{
    const BOOL ok = r_virtual_free(a, n, t);
    if (ok && (t & MEM_RELEASE)) {
        add(g_c.virtual_free, 1);
    }
    return ok;
}
DWORD WINAPI w_suspend(HANDLE t)
{
    const DWORD r = r_suspend(t);
    if (r != static_cast<DWORD>(-1)) {
        add(g_c.suspends, 1);
        const int64_t now = __atomic_add_fetch(&g_c.suspend_now, 1, __ATOMIC_RELAXED);
        int64_t seen = g_c.suspend_max;
        while (now > seen && !__atomic_compare_exchange_n(&g_c.suspend_max, &seen, now, true, __ATOMIC_RELAXED,
                                                          __ATOMIC_RELAXED)) {
        }
    }
    return r;
}
DWORD WINAPI w_resume(HANDLE t)
{
    const DWORD r = r_resume(t);
    if (r != static_cast<DWORD>(-1)) {
        add(g_c.resumes, 1);
        add(g_c.suspend_now, -1);
    }
    return r;
}
HANDLE WINAPI w_open_thread(DWORD a, BOOL i, DWORD id)
{
    HANDLE h = r_open_thread(a, i, id);
    if (h) {
        add(g_c.thread_opens, 1);
    }
    return h;
}
BOOL WINAPI w_close(HANDLE h)
{
    const BOOL ok = r_close(h);
    if (ok) {
        add(g_c.handle_closes, 1);
    }
    return ok;
}
void* w_malloc(size_t n)
{
    note_alloc();
    void* p = r_malloc(n);
    if (p) {
        add(g_c.crt_alloc, 1);
    }
    return p;
}
void* w_calloc(size_t a, size_t b)
{
    note_alloc();
    void* p = r_calloc(a, b);
    if (p) {
        add(g_c.crt_alloc, 1);
    }
    return p;
}
void* w_realloc(void* p, size_t n)
{
    note_alloc();
    void* q = r_realloc(p, n);
    if (q && !p) {
        add(g_c.crt_alloc, 1);
    }
    return q;
}
void w_free(void* p)
{
    if (p) {
        add(g_c.crt_free, 1);
    }
    r_free(p);
}

struct redirect {
    const char* name;
    void* wrapper;
    void** real;
};

// Points this DLL's import slot for each named function at its wrapper; stores the original. Returns how many
// slots were redirected.
int32_t redirect_imports(const redirect* list, uint32_t count)
{
    HMODULE self = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&redirect_imports), &self)) {
        return -1;
    }
    auto* base = reinterpret_cast<uint8_t*>(self);
    auto* nt = reinterpret_cast<IMAGE_NT_HEADERS64*>(base + reinterpret_cast<IMAGE_DOS_HEADER*>(base)->e_lfanew);
    const IMAGE_DATA_DIRECTORY dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    int32_t done = 0;
    for (auto* d = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + dir.VirtualAddress); d->Name != 0; ++d) {
        auto* names = reinterpret_cast<IMAGE_THUNK_DATA64*>(base + d->OriginalFirstThunk);
        auto* slots = reinterpret_cast<IMAGE_THUNK_DATA64*>(base + d->FirstThunk);
        for (; names->u1.AddressOfData != 0; ++names, ++slots) {
            if (IMAGE_SNAP_BY_ORDINAL64(names->u1.Ordinal)) {
                continue;
            }
            const char* name = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base + names->u1.AddressOfData)->Name;
            for (uint32_t k = 0; k < count; ++k) {
                if (strcmp(name, list[k].name) != 0) {
                    continue;
                }
                DWORD old;
                if (!VirtualProtect(&slots->u1.Function, 8, PAGE_READWRITE, &old)) {
                    return -1;
                }
                if (*list[k].real == nullptr) {
                    *list[k].real = reinterpret_cast<void*>(slots->u1.Function);
                }
                slots->u1.Function = reinterpret_cast<ULONGLONG>(list[k].wrapper);
                VirtualProtect(&slots->u1.Function, 8, old, &old);
                ++done;
            }
        }
    }
    return done;
}

// ---- quiesce plan --------------------------------------------------------------------------------------------
struct plan_step {
    uint32_t phase, attempt, index;
    uint64_t rip;
    uint32_t used;
};
constexpr uint32_t PLAN_MAX = 256;
plan_step g_plan[PLAN_MAX];
uint32_t g_plan_count = 0;
volatile uint32_t g_hook_calls[3];
uint64_t g_poke_address = 0;   // QUIESCE_PHASE_BEFORE_APPLY: write g_poke_byte here
uint8_t g_poke_byte = 0;

void set_rip(uint32_t index, uint64_t rip)
{
    CONTEXT c;
    if (index < g_parked_count && context_of(g_parked[index].thread, &c, CONTEXT_CONTROL)) {
        c.Rip = rip;
        SetThreadContext(g_parked[index].thread, &c);
    }
}

void plan_hook(uint32_t phase, uint32_t attempt)
{
    if (phase < 3) {
        __atomic_add_fetch(&g_hook_calls[phase], 1u, __ATOMIC_RELAXED);
    }
    if (phase == QUIESCE_PHASE_BEFORE_APPLY && g_poke_address != 0) {
        DWORD old;
        if (VirtualProtect(reinterpret_cast<void*>(g_poke_address), 1, PAGE_EXECUTE_READWRITE, &old)) {
            *reinterpret_cast<volatile uint8_t*>(g_poke_address) = g_poke_byte;
            VirtualProtect(reinterpret_cast<void*>(g_poke_address), 1, old, &old);
        }
    }
    for (uint32_t k = 0; k < g_plan_count; ++k) {
        plan_step& s = g_plan[k];
        if (s.phase == phase && (s.attempt == 0xFFFFFFFFu || s.attempt == attempt)) {
            set_rip(s.index, s.rip);
            ++s.used;
        }
    }
}

// ---- game-thread test operations -----------------------------------------------------------------------------
constexpr uint32_t LOG_MAX = 65536;
struct op_log {
    volatile uint32_t count;
    uint32_t reserved;
    uint64_t arg0[LOG_MAX];
    uint32_t ticket[LOG_MAX];
};
op_log g_log;
uint32_t g_bump_to_epoch = 0;

int32_t op_record(const gtq::job& j)
{
    const uint32_t n = g_log.count;
    if (n < LOG_MAX) {
        g_log.arg0[n] = j.arg[0];
        g_log.ticket[n] = j.ticket;
        __atomic_store_n(&g_log.count, n + 1, __ATOMIC_RELEASE);
    }
    return 1;
}

int32_t op_refuse(const gtq::job&)
{
    return -16;
}

int32_t op_bad_result(const gtq::job&)
{
    return 0;
}

// Changes the world while the drain runs: the next job must see the new epoch.
int32_t op_bump_epoch(const gtq::job& j)
{
    op_record(j);
    heartbeat(g_bump_to_epoch);
    return 1;
}

// Re-enters the tick (as a game call that reaches UT-01 again would).
int32_t op_reenter(const gtq::job& j)
{
    op_record(j);
    ctx c;
    memset(&c, 0, sizeof(c));
    c.index = static_cast<uint64_t>(tick::ut01_row());
    tick::ut01(&c, 1);
    return 1;
}

// ---- fail-open template under test ---------------------------------------------------------------------------
struct test_record {
    record_head head;
    uint64_t rax;     // the handler puts this in RAX
};
record_slot<test_record> g_test_slot;

int32_t test_body(ctx* c, const test_record& r)
{
    c->rax = r.rax;
    return RUN_ORIGINAL;
}

constexpr handler_fn TEST_FAIL_OPEN = fail_open<test_record, g_test_slot, test_body>;

// A handler that must never run (dry-arm drills): it would wreck the state and resume somewhere bogus.
int32_t clobber_handler(ctx* c, uint32_t)
{
    c->rax ^= 0x5A5A5A5A5A5A5A5Aull;
    c->rcx = 0;
    c->resume = 0x1;  // NOTADDR: never a valid continuation
    return RESUME;
}

uint64_t volatile g_fixed_now = 0;
uint64_t fixed_clock()
{
    return g_fixed_now;
}

}  // namespace

extern "C" {

// ---- parked threads ------------------------------------------------------------------------------------------
int32_t ILCT_ParkCreate(uint32_t count)
{
    if (g_raw_resume == nullptr) {
        g_raw_resume = reinterpret_cast<resume_fn>(
            reinterpret_cast<void*>(GetProcAddress(GetModuleHandleW(L"kernel32.dll"), "ResumeThread")));
    }
    for (uint32_t k = 0; k < count && g_parked_count < PARK_MAX; ++k) {
        parked& p = g_parked[g_parked_count];
        p.thread = CreateThread(nullptr, 64 * 1024, parked_start, nullptr,
                                CREATE_SUSPENDED | STACK_SIZE_PARAM_IS_A_RESERVATION, &p.id);
        if (p.thread == nullptr || !context_of(p.thread, &p.start, CONTEXT_FULL)) {
            return -1;
        }
        ++g_parked_count;
    }
    return static_cast<int32_t>(g_parked_count);
}

int32_t ILCT_ParkSetRip(uint32_t index, uint64_t rip)
{
    if (index >= g_parked_count) {
        return 0;
    }
    set_rip(index, rip);
    return 1;
}

uint64_t ILCT_ParkGetRip(uint32_t index)
{
    CONTEXT c;
    if (index >= g_parked_count || !context_of(g_parked[index].thread, &c, CONTEXT_CONTROL)) {
        return 0;
    }
    return c.Rip;
}

uint32_t ILCT_ParkThreadId(uint32_t index)
{
    return index < g_parked_count ? g_parked[index].id : 0;
}

// Restores every parked thread's start context, lets it run (it returns at once) and waits for it.
int32_t ILCT_ParkReleaseAll(void)
{
    int32_t released = 0;
    for (uint32_t k = 0; k < g_parked_count; ++k) {
        parked& p = g_parked[k];
        p.start.ContextFlags = CONTEXT_FULL;
        SetThreadContext(p.thread, &p.start);
        while (g_raw_resume(p.thread) > 1) {
        }
        if (WaitForSingleObject(p.thread, 5000) == WAIT_OBJECT_0) {
            ++released;
        }
        CloseHandle(p.thread);
        memset(&p, 0, sizeof(p));
    }
    g_parked_count = 0;
    return released;
}

// ---- counters ------------------------------------------------------------------------------------------------
int32_t ILCT_CountersInstall(void)
{
    static const redirect list[] = {
        {"HeapAlloc", reinterpret_cast<void*>(w_heap_alloc), reinterpret_cast<void**>(&r_heap_alloc)},
        {"HeapReAlloc", reinterpret_cast<void*>(w_heap_realloc), reinterpret_cast<void**>(&r_heap_realloc)},
        {"HeapFree", reinterpret_cast<void*>(w_heap_free), reinterpret_cast<void**>(&r_heap_free)},
        {"HeapCreate", reinterpret_cast<void*>(w_heap_create), reinterpret_cast<void**>(&r_heap_create)},
        {"HeapDestroy", reinterpret_cast<void*>(w_heap_destroy), reinterpret_cast<void**>(&r_heap_destroy)},
        {"VirtualAlloc", reinterpret_cast<void*>(w_virtual_alloc), reinterpret_cast<void**>(&r_virtual_alloc)},
        {"VirtualFree", reinterpret_cast<void*>(w_virtual_free), reinterpret_cast<void**>(&r_virtual_free)},
        {"SuspendThread", reinterpret_cast<void*>(w_suspend), reinterpret_cast<void**>(&r_suspend)},
        {"ResumeThread", reinterpret_cast<void*>(w_resume), reinterpret_cast<void**>(&r_resume)},
        {"OpenThread", reinterpret_cast<void*>(w_open_thread), reinterpret_cast<void**>(&r_open_thread)},
        {"CloseHandle", reinterpret_cast<void*>(w_close), reinterpret_cast<void**>(&r_close)},
        {"malloc", reinterpret_cast<void*>(w_malloc), reinterpret_cast<void**>(&r_malloc)},
        {"calloc", reinterpret_cast<void*>(w_calloc), reinterpret_cast<void**>(&r_calloc)},
        {"realloc", reinterpret_cast<void*>(w_realloc), reinterpret_cast<void**>(&r_realloc)},
        {"free", reinterpret_cast<void*>(w_free), reinterpret_cast<void**>(&r_free)},
    };
    if (g_c.installed > 0) {
        return static_cast<int32_t>(g_c.installed);
    }
    const int32_t n = redirect_imports(list, sizeof(list) / sizeof(list[0]));
    g_c.installed = n;
    return n;
}

void ILCT_CountersRead(alloc_counters* out)
{
    memcpy(out, const_cast<const alloc_counters*>(&g_c), sizeof(*out));
}

void ILCT_CountersReset(void)
{
    const int64_t installed = g_c.installed;
    const int64_t suspended = g_c.suspend_now;
    memset(&g_c, 0, sizeof(g_c));
    g_c.installed = installed;
    g_c.suspend_now = suspended;
}

// ---- quiesce plan --------------------------------------------------------------------------------------------
int32_t ILCT_QuiescePlanAdd(uint32_t phase, uint32_t attempt, uint32_t index, uint64_t rip)
{
    if (g_plan_count == PLAN_MAX) {
        return 0;
    }
    g_plan[g_plan_count++] = plan_step{phase, attempt, index, rip, 0};
    test_set_quiesce_hook(plan_hook);
    return 1;
}

void ILCT_QuiescePokeBeforeApply(uint64_t address, uint32_t value)
{
    g_poke_address = address;
    g_poke_byte = static_cast<uint8_t>(value);
    test_set_quiesce_hook(plan_hook);
}

uint32_t ILCT_QuiescePlanUsed(uint32_t step)
{
    return step < g_plan_count ? g_plan[step].used : 0;
}

uint32_t ILCT_QuiesceHookCalls(uint32_t phase)
{
    return phase < 3 ? g_hook_calls[phase] : 0;
}

void ILCT_QuiescePlanClear(void)
{
    test_set_quiesce_hook(nullptr);
    g_plan_count = 0;
    g_poke_address = 0;
    for (uint32_t k = 0; k < 3; ++k) {
        g_hook_calls[k] = 0;
    }
}

// ---- patch-layer extras --------------------------------------------------------------------------------------
void ILCT_PatchSetMode(uint32_t mode)
{
    set_mode(mode);
}

uint32_t ILCT_PatchMode(void)
{
    return current_mode();
}

uint32_t ILCT_PatchSubsystems(void)
{
    return subsystems_mask();
}

void ILCT_PatchFault(uint32_t bits)
{
    fault_subsystem(bits);
}

int32_t ILCT_PatchSetClobber(uint32_t row)
{
    return set_handler(row, clobber_handler) ? 1 : 0;
}

void ILCT_PatchSeqPoke(uint32_t seq)
{
    test_seq_poke(seq);
}

// Installs a fixed module clock (both the dispatcher and the queue read it) and sets its value.
void ILCT_SetFixedClock(uint64_t now)
{
    g_fixed_now = now;
    *static_cast<uint64_t (**)()>(test_clock_slot()) = fixed_clock;
}

int32_t ILCT_LivenessSample(liveness_view* out)
{
    *out = sample_liveness();
    return out->fresh ? 1 : 0;
}

// Holds (1) or releases (0) the patch layer's lock on the calling thread (deferral tests).
void ILCT_PatchHoldLock(int32_t hold)
{
    test_hold_lock(hold != 0);
}

// ---- game-thread queue ---------------------------------------------------------------------------------------
enum : uint16_t { OP_RECORD = 1, OP_REFUSE = 2, OP_MOVEMENT = 3, OP_BUMP_EPOCH = 4, OP_REENTER = 5, OP_BAD_RESULT = 6 };

int32_t ILCT_GtqSetupTestOps(uint32_t bump_to_epoch)
{
    g_bump_to_epoch = bump_to_epoch;
    int32_t ok = 1;
    ok &= gtq::register_op(OP_RECORD, ILC_SITE_SUBSYS_CORE, op_record) ? 1 : 0;
    ok &= gtq::register_op(OP_REFUSE, ILC_SITE_SUBSYS_CORE, op_refuse) ? 1 : 0;
    ok &= gtq::register_op(OP_MOVEMENT, ILC_SITE_SUBSYS_MOVEMENT, op_record) ? 1 : 0;
    ok &= gtq::register_op(OP_BUMP_EPOCH, ILC_SITE_SUBSYS_CORE, op_bump_epoch) ? 1 : 0;
    ok &= gtq::register_op(OP_REENTER, ILC_SITE_SUBSYS_CORE, op_reenter) ? 1 : 0;
    ok &= gtq::register_op(OP_BAD_RESULT, ILC_SITE_SUBSYS_CORE, op_bad_result) ? 1 : 0;
    return ok;
}

int32_t ILCT_GtqRegister(uint32_t op, uint32_t subsystem)
{
    return gtq::register_op(static_cast<uint16_t>(op), subsystem, op_record) ? 1 : 0;
}

void ILCT_GtqSeal(void)
{
    gtq::seal();
}

int32_t ILCT_GtqPush(uint32_t op, uint32_t issuer, uint32_t epoch, uint64_t generation, uint32_t ttl_ms,
                     uint64_t arg0, uint32_t* ticket)
{
    const uint64_t args[gtq::ARG_COUNT] = {arg0, 0, 0, 0};
    return gtq::push(static_cast<uint16_t>(op), static_cast<uint8_t>(issuer), epoch, generation, ttl_ms, args,
                     ticket);
}

int32_t ILCT_GtqPushRaw(uint32_t op, uint32_t epoch, uint64_t generation, uint32_t ttl_ms, uint64_t enqueued_ms,
                        uint64_t arg0, uint32_t* ticket)
{
    gtq::job j;
    memset(&j, 0, sizeof(j));
    j.op = static_cast<uint16_t>(op);
    j.epoch = epoch;
    j.generation = generation;
    j.ttl_ms = ttl_ms;
    j.enqueued_ms = enqueued_ms;
    j.arg[0] = arg0;
    return gtq::test_push_raw(j, ticket);
}

int32_t ILCT_GtqResult(uint32_t ticket, gtq::completion* out)
{
    return gtq::result(ticket, out);
}

void ILCT_GtqSetGeneration(uint64_t generation)
{
    gtq::set_generation(generation);
}

uint32_t ILCT_GtqDrain(void)
{
    return gtq::drain();
}

uint32_t ILCT_GtqFlush(int32_t reason)
{
    return gtq::flush(reason);
}

void ILCT_GtqStats(gtq::counters* out)
{
    memcpy(out, const_cast<const gtq::counters*>(gtq::stats()), sizeof(*out));
}

void ILCT_GtqReset(void)
{
    gtq::test_reset();
    g_log.count = 0;
}

uint32_t ILCT_LogCount(void)
{
    return g_log.count;
}

uint64_t ILCT_LogArg(uint32_t k)
{
    return k < LOG_MAX ? g_log.arg0[k] : 0;
}

uint32_t ILCT_LogTicket(uint32_t k)
{
    return k < LOG_MAX ? g_log.ticket[k] : 0;
}

// ---- tick ----------------------------------------------------------------------------------------------------
int32_t ILCT_TickAttach(void)
{
    return tick::attach() ? 1 : 0;
}

// Runs the UT-01 handler directly (as the dispatcher would, with this epoch) on the calling thread.
int32_t ILCT_TickRun(uint32_t epoch)
{
    ctx c;
    memset(&c, 0, sizeof(c));
    c.index = static_cast<uint64_t>(tick::ut01_row());
    return tick::ut01(&c, epoch);
}

void ILCT_TickStats(tick::counters* out)
{
    memcpy(out, const_cast<const tick::counters*>(tick::stats()), sizeof(*out));
}

void ILCT_TickReset(void)
{
    tick::test_reset();
}

int32_t ILCT_TickRow(void)
{
    return tick::ut01_row();
}

// ---- fail-open template --------------------------------------------------------------------------------------
void ILCT_RecordPublish(uint32_t epoch, uint64_t generation, uint64_t rax)
{
    test_record r;
    memset(&r, 0, sizeof(r));
    r.head.epoch = epoch;
    r.head.generation = generation;
    r.rax = rax;
    g_test_slot.publish(r);
}

void ILCT_RecordWithdraw(void)
{
    g_test_slot.withdraw();
}

void ILCT_RecordSeqPoke(uint32_t seq)
{
    g_test_slot.seq = seq;
}

int32_t ILCT_RecordSetHandler(uint32_t row)
{
    return set_handler(row, TEST_FAIL_OPEN) ? 1 : 0;
}

// Calls the fail-open handler directly for `row` with `epoch`; returns the handler result, RAX in *rax.
int32_t ILCT_RecordInvoke(uint32_t row, uint32_t epoch, uint64_t* rax)
{
    ctx c;
    memset(&c, 0, sizeof(c));
    c.index = row;
    c.rax = *rax;
    const int32_t r = TEST_FAIL_OPEN(&c, epoch);
    *rax = c.rax;
    return r;
}

uint32_t ILCT_RecordSeq(void)
{
    return g_test_slot.seq;
}

// A reader preempted between its first sequence load and its copy, while the writer withdraws and republishes.
// Steps exactly as record_slot::read does, with the writer's calls placed in the gap. out[0] = whether that reader
// would accept its copy, out[1] = its first seq, out[2] = the seq it sees after, out[3] = the rax it copied,
// out[4] = what a fresh read() returns afterwards (rax, or 0 if refused).
void ILCT_RecordAbaDrill(uint64_t* out)
{
    test_record a;
    memset(&a, 0, sizeof(a));
    a.head.epoch = 7;
    a.rax = 0xAAAA;
    g_test_slot.publish(a);
    const uint32_t first = __atomic_load_n(&g_test_slot.seq, __ATOMIC_ACQUIRE);
    // --- preempted here: the writer withdraws, then publishes a different record ---
    g_test_slot.withdraw();
    test_record b = a;
    b.rax = 0xBBBB;
    g_test_slot.publish(b);
    // --- resumed ---
    const uint32_t ok = g_test_slot.valid;
    test_record copy;
    memcpy(&copy, const_cast<const test_record*>(&g_test_slot.value), sizeof(copy));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    const uint32_t second = __atomic_load_n(&g_test_slot.seq, __ATOMIC_RELAXED);
    out[0] = (first & 1u) == 0 && second == first && ok == 1;
    out[1] = first;
    out[2] = second;
    out[3] = copy.rax;
    test_record fresh;
    out[4] = g_test_slot.read(fresh) ? fresh.rax : 0;
}

// Stress: one writer publishes records whose 32 words all hold the same counter (and withdraws every third time);
// readers call the real read() and count accepted copies whose words disagree (torn).
struct big_record {
    record_head head;
    uint64_t word[32];
};
record_slot<big_record> g_big_slot;

void ILCT_BigWriter(uint32_t iterations)
{
    big_record r;
    memset(&r, 0, sizeof(r));
    for (uint32_t n = 1; n <= iterations; ++n) {
        r.head.epoch = 1;
        for (uint64_t& w : r.word) {
            w = n;
        }
        g_big_slot.publish(r);
        if (n % 3 == 0) {
            g_big_slot.withdraw();
        }
    }
}

void ILCT_BigReader(uint32_t iterations, uint64_t* accepted, uint64_t* torn)
{
    big_record r;
    *accepted = 0;
    *torn = 0;
    for (uint32_t k = 0; k < iterations; ++k) {
        if (!g_big_slot.read(r)) {
            continue;
        }
        ++*accepted;
        for (uint64_t w : r.word) {
            if (w != r.word[0]) {
                ++*torn;
                break;
            }
        }
    }
}

uint64_t ILCT_PatchOriginal(uint32_t row)
{
    return reinterpret_cast<uint64_t>(test_original(row));
}

struct ilct_gt_addresses {
    uint64_t log_count;
    uint64_t log_arg0;
    uint64_t gtq_stats;
    uint64_t tick_stats;
    uint64_t test_slot;
    uint64_t fixed_now;
    uint64_t completions;
};

void ILCT_GtAddresses(ilct_gt_addresses* out)
{
    out->log_count = reinterpret_cast<uint64_t>(&g_log.count);
    out->log_arg0 = reinterpret_cast<uint64_t>(&g_log.arg0[0]);
    out->gtq_stats = reinterpret_cast<uint64_t>(gtq::stats());
    out->tick_stats = reinterpret_cast<uint64_t>(tick::stats());
    out->test_slot = reinterpret_cast<uint64_t>(&g_test_slot);
    out->fixed_now = reinterpret_cast<uint64_t>(&g_fixed_now);
    out->completions = reinterpret_cast<uint64_t>(gtq::test_completion_table());
}

}  // extern "C"
