// Ivalice Legacy co-op module: the patch layer around MinHook (see patch_engine.h for the rules).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <tlhelp32.h>

#include <string.h>

#include <MinHook.h>

#include "patch/call_island.h"
#include "patch/patch_engine.h"
#include "runtime/probe.h"

using namespace ilc::patch;

// Read by the stubs (patch_stubs.cpp): one gate byte and one continuation per site row.
extern "C" {
volatile uint8_t ilc_patch_gate[SITE_COUNT];
void* volatile ilc_patch_tramp[SITE_COUNT];
extern const char ilc_patch_stub_base[];
uint64_t ilc_patch_dispatch(ctx* c);
uint64_t ilc_call_dispatch(ctx* c);
uint64_t ilc_call_after(ctx* c);
}

namespace {

constexpr uint32_t STUB_STRIDE = 32;  // patch_stubs.cpp aligns every stub to 32 bytes

uint64_t default_clock()
{
    return GetTickCount64();
}

// Everything below is changed only under g_lock; the stubs and dispatch() read it without the lock.
SRWLOCK g_lock = SRWLOCK_INIT;
uint64_t (*volatile g_clock)() = default_clock;
liveness g_live = {0, 0, 0, 0, 0};
site_stats g_stats[SITE_COUNT];
handler_fn volatile g_handlers[SITE_COUNT];
after_fn volatile g_after[SITE_COUNT];   // call rows only
bool g_site_armed[SITE_COUNT];
bool g_hooked[SITE_COUNT];   // MH_CreateHook succeeded for the row and the hook is ours
volatile uint32_t g_subsystems = 0;  // closed until an applied settings record opens them
volatile uint32_t g_mode = MODE_DRY;   // first boot is dry-armed
volatile uint32_t g_faulted = 0;       // latched fault bits (atomic OR; cleared only by test_release_all)
uint32_t g_state = STATE_IDLE;
uintptr_t g_base = 0;
bool g_mh_initialized = false;
uint32_t g_inject_stage = TEST_NONE;
uint32_t g_inject_row = 0;
uint32_t g_mh_calls = 0;
quiesce_hook_fn volatile g_quiesce_hook = nullptr;
void* g_original[SITE_COUNT];  // MinHook's trampoline per row, as MH_CreateHook returned it

// The quiescence probe's working set: static, so nothing is allocated while threads are suspended.
DWORD g_probe_ids[QUIESCE_MAX_THREADS];
HANDLE g_probe_handles[QUIESCE_MAX_THREADS];

struct lock_guard {
    lock_guard() { AcquireSRWLockExclusive(&g_lock); }
    ~lock_guard() { ReleaseSRWLockExclusive(&g_lock); }
    lock_guard(const lock_guard&) = delete;
    lock_guard& operator=(const lock_guard&) = delete;
};

void* site_va(uintptr_t base, const site& s)
{
    return reinterpret_cast<void*>(base + s.rva);
}

bool is_patch(uint32_t row)
{
    return row < SITE_COUNT && SITES[row].policy == ILC_SITE_POLICY_PATCH;
}

bool island_row(uint32_t row)
{
    return island::is_call_row(row);
}

// MinHook owns the row (hooked and not a call row).
bool mh_row(uint32_t row)
{
    return g_hooked[row] && !island_row(row);
}

// ---- MinHook calls, counted -----------------------------------------------------------------------------------
MH_STATUS mh_initialize() { ++g_mh_calls; return MH_Initialize(); }
MH_STATUS mh_uninitialize() { ++g_mh_calls; return MH_Uninitialize(); }
MH_STATUS mh_create(void* target, void* detour, void** original) { ++g_mh_calls; return MH_CreateHook(target, detour, original); }
MH_STATUS mh_remove(void* target) { ++g_mh_calls; return MH_RemoveHook(target); }
MH_STATUS mh_queue_enable(void* target) { ++g_mh_calls; return MH_QueueEnableHook(target); }
MH_STATUS mh_queue_disable(void* target) { ++g_mh_calls; return MH_QueueDisableHook(target); }
MH_STATUS mh_apply() { ++g_mh_calls; return MH_ApplyQueued(); }
// MH_DisableHook is never used. In MinHook 1.3.4 EnableHook() freezes the threads with ACTION_ENABLE whether it
// enables or disables, so a disable leaves any thread that sits in the trampoline there (found by the image-twin
// drills). The queued path (MH_QueueDisableHook + MH_ApplyQueued, ACTION_APPLY_QUEUED) and MH_RemoveHook on an
// enabled hook (ACTION_DISABLE) move such a thread back to the original bytes; the layer only uses those.

// ---- reading the image ---------------------------------------------------------------------------------------
// True if [address, address + size) lies on committed pages without PAGE_GUARD/PAGE_NOACCESS, all executable
// when `executable` is set.
bool range_usable(const void* address, size_t size, bool executable)
{
    const uintptr_t end = reinterpret_cast<uintptr_t>(address) + size;
    uintptr_t at = reinterpret_cast<uintptr_t>(address);
    while (at < end) {
        MEMORY_BASIC_INFORMATION info;
        if (VirtualQuery(reinterpret_cast<void*>(at), &info, sizeof(info)) != sizeof(info) ||
            info.State != MEM_COMMIT || (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) != 0) {
            return false;
        }
        const DWORD exec = PAGE_EXECUTE | PAGE_EXECUTE_READ | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
        if (executable && (info.Protect & exec) == 0) {
            return false;
        }
        at = reinterpret_cast<uintptr_t>(info.BaseAddress) + info.RegionSize;
    }
    return true;
}

bool read_bytes(void* dst, const void* src, size_t size, bool executable)
{
    return range_usable(src, size, executable) && ilc::copy_from_caller(dst, src, size);
}

// Every checked byte of a call row except the four rel32 bytes still holds its pinned value.
bool call_row_rest_pinned(uintptr_t base, const site& s)
{
    uint8_t buffer[64];
    const uint32_t n = checked_len(s);
    if (n > sizeof(buffer) || !read_bytes(buffer, site_va(base, s), n, true)) {
        return false;
    }
    const uint32_t rel = island::call_offset(s) + 1;
    for (uint32_t k = 0; k < n; ++k) {
        if ((k < rel || k >= rel + 4) && buffer[k] != checked_byte(s, k)) {
            return false;
        }
    }
    return true;
}

// 1 = the row's checked bytes match; 0 = they differ; -1 = unreadable.
int row_matches(uintptr_t base, const site& s, uint32_t from = 0)
{
    uint8_t buffer[64];
    const uint32_t n = checked_len(s);
    if (n > sizeof(buffer)) {
        return -1;
    }
    if (!read_bytes(buffer, site_va(base, s), n, s.policy == ILC_SITE_POLICY_PATCH)) {
        return -1;
    }
    for (uint32_t k = from; k < n; ++k) {
        if (buffer[k] != checked_byte(s, k)) {
            return 0;
        }
    }
    return 1;
}

void clear_report(report* out)
{
    memset(out, 0, sizeof(*out));
    out->site = -1;
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        out->patch_rows += SITES[row].policy == ILC_SITE_POLICY_PATCH ? 1 : 0;
    }
}

int32_t finish(report* out, int32_t result, int32_t row = -1, int32_t detail = 0)
{
    out->result = result;
    out->site = row;
    out->detail = detail;
    out->state = g_state;
    out->mh_calls = g_mh_calls;
    return result;
}

// Preflight without the lock (callers hold it). `skip` receives the PATCH rows whose GATE row did not match.
int32_t preflight_locked(uintptr_t base, report* out, bool skip[SITE_COUNT])
{
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        skip[row] = false;
    }
    if (base != IMAGE_BASE) {
        return finish(out, BAD_BASE);
    }
    uint8_t headers[0x400];
    if (!read_bytes(headers, reinterpret_cast<void*>(base), sizeof(headers), false)) {
        return finish(out, HEADERS_UNREADABLE);
    }
    uint32_t lfanew;
    memcpy(&lfanew, headers + 0x3C, 4);
    if (headers[0] != 'M' || headers[1] != 'Z' || lfanew < 0x40 || lfanew > sizeof(headers) - 0x78 ||
        memcmp(headers + lfanew, "PE\0\0", 4) != 0) {
        return finish(out, BAD_PE);
    }
    uint16_t machine, magic;
    uint32_t timestamp, size_of_image;
    memcpy(&machine, headers + lfanew + 4, 2);
    memcpy(&timestamp, headers + lfanew + 8, 4);
    memcpy(&magic, headers + lfanew + 24, 2);
    memcpy(&size_of_image, headers + lfanew + 24 + 56, 4);
    if (machine != IMAGE_FILE_MACHINE_AMD64 || magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
        return finish(out, BAD_PE);
    }
    if (timestamp != IMAGE_TIMESTAMP) {
        return finish(out, BAD_TIMESTAMP);
    }
    if (size_of_image != IMAGE_SIZE) {
        return finish(out, BAD_SIZE);
    }

    // GATE rows first: a mismatch (or an unreadable gate) skips the row it gates.
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        const site& s = SITES[row];
        if (s.policy == ILC_SITE_POLICY_GATE && row_matches(base, s) != 1 && s.gate < SITE_COUNT) {
            skip[s.gate] = true;
        }
    }
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        const site& s = SITES[row];
        if (s.policy == ILC_SITE_POLICY_INFO || (s.policy == ILC_SITE_POLICY_PATCH && skip[row])) {
            out->info_mismatch += row_matches(base, s) == 1 ? 0 : 1;
            out->gated_off += skip[row] ? 1 : 0;
            continue;
        }
        if (s.policy == ILC_SITE_POLICY_GATE) {
            continue;
        }
        if (s.policy == ILC_SITE_POLICY_PATCH && (s.flags & (ILC_SITE_FLAG_INTERIOR | ILC_SITE_FLAG_DROPPED)) != 0) {
            return finish(out, SITE_INTERIOR, static_cast<int32_t>(row));
        }
        const int match = row_matches(base, s);
        if (match < 0) {
            return finish(out, SITE_UNREADABLE, static_cast<int32_t>(row));
        }
        if (match == 0) {
            return finish(out, SITE_MISMATCH, static_cast<int32_t>(row));
        }
    }
    // A hook window (the bytes MinHook relocates) must not overlap any other checked row.
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        const site& a = SITES[row];
        if (a.policy != ILC_SITE_POLICY_PATCH || skip[row]) {
            continue;
        }
        const uint32_t a0 = a.rva, a1 = a.rva + a.hook_len;
        for (uint32_t other = 0; other < SITE_COUNT; ++other) {
            const site& b = SITES[other];
            if (other == row || b.policy == ILC_SITE_POLICY_INFO || (b.policy == ILC_SITE_POLICY_PATCH && skip[other])) {
                continue;
            }
            const uint32_t b0 = b.rva, b1 = b.rva + (b.policy == ILC_SITE_POLICY_PATCH ? b.hook_len : checked_len(b));
            if (a0 < b1 && b0 < a1) {
                return finish(out, SITE_OVERLAP, static_cast<int32_t>(row), static_cast<int32_t>(other));
            }
        }
    }
    return finish(out, OK);
}

uint32_t fault_bit(const site& s)
{
    return s.subsystem == ILC_SITE_SUBSYS_CORE ? FAULT_CORE : s.subsystem;
}

void recompute_gates()
{
    const bool armed = g_live.armed == 1 && g_state == STATE_INSTALLED;
    const bool live = g_mode == MODE_LIVE;
    const uint32_t faulted = __atomic_load_n(&g_faulted, __ATOMIC_ACQUIRE);
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        const site& s = SITES[row];
        const bool core = s.subsystem == ILC_SITE_SUBSYS_CORE;
        const bool subsystem = core || (g_subsystems & s.subsystem) != 0;
        const bool open = armed && g_hooked[row] && g_site_armed[row] && subsystem && g_handlers[row] != nullptr &&
                          (live || core) && (faulted & fault_bit(s)) == 0;
        ilc_patch_gate[row] = open ? 1 : 0;
        if (island_row(row)) {
            island::sync(row, open);
        }
    }
}

void close_all_gates()
{
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        ilc_patch_gate[row] = 0;
        if (island_row(row)) {
            island::sync(row, false);
        }
    }
}

// The site holds E9 -> relay, the relay is MinHook's FF 25 00000000 <our stub>, and every other checked byte is
// still the pinned original.
bool site_holds_our_jump(uintptr_t base, uint32_t row)
{
    const site& s = SITES[row];
    if (island_row(row)) {
        return island::holds_island(base, row) && call_row_rest_pinned(base, s);
    }
    uint8_t jump[HOOK_JUMP];
    if (!read_bytes(jump, site_va(base, s), sizeof(jump), true) || jump[0] != 0xE9) {
        return false;
    }
    int32_t rel;
    memcpy(&rel, jump + 1, 4);
    const uintptr_t relay = base + s.rva + HOOK_JUMP + static_cast<intptr_t>(rel);
    uint8_t abs[14];
    if (!read_bytes(abs, reinterpret_cast<void*>(relay), sizeof(abs), true)) {
        return false;
    }
    static const uint8_t jmp_abs[6] = {0xFF, 0x25, 0x00, 0x00, 0x00, 0x00};
    uint64_t target;
    memcpy(&target, abs + 6, 8);
    if (memcmp(abs, jmp_abs, sizeof(jmp_abs)) != 0 || target != reinterpret_cast<uint64_t>(stub_address(row))) {
        return false;
    }
    return row_matches(base, s, HOOK_JUMP) == 1;
}

// ---- quiescence ------------------------------------------------------------------------------------------------
struct probe_result {
    int32_t result;     // OK, QUIESCE_BUSY or QUIESCE_FAILED
    int32_t busy_row;   // first row whose window holds a thread, or -1
    uint32_t probed;
    uint32_t busy;
    uint32_t vanished;
};

// The row whose hook window holds `rip`, or -1. Only rows this install will hook count.
int32_t window_holding(uintptr_t base, const bool skip[SITE_COUNT], uint64_t rip)
{
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        const site& s = SITES[row];
        if (s.policy != ILC_SITE_POLICY_PATCH || skip[row]) {
            continue;
        }
        const uint64_t start = base + s.rva;
        if (rip >= start && rip < start + s.hook_len) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

// One probe: list the other threads (before suspending anything), suspend them all, read each RIP, resume them
// all. Between the first SuspendThread and the last ResumeThread only OpenThread/SuspendThread/GetThreadContext/
// ResumeThread/CloseHandle run, on static arrays: no heap, no lock.
probe_result quiesce_probe(uintptr_t base, const bool skip[SITE_COUNT])
{
    probe_result r = {OK, -1, 0, 0, 0};
    const DWORD self = GetCurrentThreadId();
    const DWORD pid = GetCurrentProcessId();
    uint32_t count = 0;
    HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snapshot == INVALID_HANDLE_VALUE) {
        r.result = QUIESCE_FAILED;
        return r;
    }
    THREADENTRY32 entry;
    entry.dwSize = sizeof(entry);
    bool listed = Thread32First(snapshot, &entry) != FALSE;
    bool overflow = false;
    while (listed) {
        if (entry.dwSize >= offsetof(THREADENTRY32, th32OwnerProcessID) + sizeof(DWORD) &&
            entry.th32OwnerProcessID == pid && entry.th32ThreadID != self) {
            if (count == QUIESCE_MAX_THREADS) {
                overflow = true;
                break;
            }
            g_probe_ids[count++] = entry.th32ThreadID;
        }
        entry.dwSize = sizeof(entry);
        listed = Thread32Next(snapshot, &entry) != FALSE;
    }
    CloseHandle(snapshot);
    if (overflow) {
        r.result = QUIESCE_FAILED;
        return r;
    }

    // Suspend everything first, so the RIPs are one consistent picture.
    for (uint32_t k = 0; k < count; ++k) {
        HANDLE thread = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE,
                                   g_probe_ids[k]);
        const bool gone = thread == nullptr && GetLastError() == ERROR_INVALID_PARAMETER;
        if (thread != nullptr && SuspendThread(thread) == static_cast<DWORD>(-1)) {
            CloseHandle(thread);
            thread = nullptr;
        }
        g_probe_handles[k] = thread;
        if (thread == nullptr && gone) {
            ++r.vanished;  // exited after the snapshot (an exiting thread cannot be inside game code)
        } else if (thread == nullptr) {
            ++r.busy;  // exists but cannot be opened or suspended: its RIP is unknown, so it counts as inside
        }
    }
    for (uint32_t k = 0; k < count; ++k) {
        if (g_probe_handles[k] == nullptr) {
            continue;
        }
        ++r.probed;
        alignas(16) CONTEXT context;
        memset(&context, 0, sizeof(context));
        context.ContextFlags = CONTEXT_CONTROL;
        int32_t row = -2;  // unreadable context: treated as busy (position unknown)
        if (GetThreadContext(g_probe_handles[k], &context)) {
            row = window_holding(base, skip, context.Rip);
        }
        if (row != -1) {
            ++r.busy;
            if (r.busy_row < 0) {
                r.busy_row = row;
            }
        }
    }
    for (uint32_t k = 0; k < count; ++k) {
        if (g_probe_handles[k] != nullptr) {
            ResumeThread(g_probe_handles[k]);
            CloseHandle(g_probe_handles[k]);
            g_probe_handles[k] = nullptr;
        }
    }
    if (r.busy != 0) {
        r.result = QUIESCE_BUSY;
    }
    return r;
}

// Undo an install. `enabled` = MH_ApplyQueued was called, so threads may have run the hooks: disable them all,
// check the bytes, send every stub to its restored site and keep the buffers. Otherwise nothing was ever
// enabled: remove the hooks (their buffers are freed) and uninitialise MinHook.
// `foreign_row` (not-enabled path only): a row another module wrote; it is not ours to verify.
int32_t rollback(uintptr_t base, report* out, bool enabled, int32_t foreign_row = -1)
{
    close_all_gates();
    uint32_t created = 0;
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        created += g_hooked[row] ? 1 : 0;
    }
    bool restored = true;
    if (enabled) {
        bool queued = true;
        for (uint32_t row = 0; row < SITE_COUNT; ++row) {
            if (!mh_row(row) || (g_inject_stage == TEST_FAIL_DISABLE && g_inject_row == row)) {
                continue;  // (test) the injected row's disable is never queued: its jump stays
            }
            if (mh_queue_disable(site_va(base, SITES[row])) != MH_OK) {
                queued = false;
            }
        }
        if (!queued || mh_apply() != MH_OK) {
            // One row at a time, still through the queue (see above): queue its disable and apply. A row that is
            // already off is a no-op for ApplyQueued.
            for (uint32_t row = 0; row < SITE_COUNT; ++row) {
                if (mh_row(row) && !(g_inject_stage == TEST_FAIL_DISABLE && g_inject_row == row)) {
                    mh_queue_disable(site_va(base, SITES[row]));
                    mh_apply();
                }
            }
        }
        // Call rows: the pinned rel32s go back inside one thread freeze. The islands stay mapped if any rel32 was
        // ever written (a thread may be on an island); every slot points at its callee.
        const bool ever_committed = island::committed();
        island::revert(base, g_inject_stage == TEST_FAIL_DISABLE ? g_inject_row : SITE_COUNT);
        if (!ever_committed) {
            island::release();  // no rel32 ever pointed at an island
        }
        for (uint32_t row = 0; row < SITE_COUNT; ++row) {
            if (g_hooked[row]) {
                const bool back = row_matches(base, SITES[row]) == 1;
                restored = restored && back;
                // A thread still inside the stub's fast path continues at the restored original code, but only
                // where the original bytes are verified back. Where they are not (the jump may still lead to this
                // stub), the continuation stays MinHook's trampoline: its buffer stays mapped, so it stays valid,
                // and pointing at the site would loop site -> stub -> site forever. A call row's continuation is
                // its callee either way (its stub never jumps back to the site).
                if (back && !island_row(row)) {
                    ilc_patch_tramp[row] = site_va(base, SITES[row]);
                }
            }
        }
        out->rolled_back = created;
        out->retained = created;
    } else {
        for (uint32_t row = 0; row < SITE_COUNT; ++row) {
            if (g_hooked[row]) {
                if (!island_row(row)) {
                    mh_remove(site_va(base, SITES[row]));
                }
                ilc_patch_tramp[row] = nullptr;
                g_hooked[row] = false;
                restored = restored && (static_cast<int32_t>(row) == foreign_row || row_matches(base, SITES[row]) == 1);
            }
        }
        island::release();  // nothing was enabled: no rel32 points at an island
        if (g_mh_initialized && mh_uninitialize() == MH_OK) {
            g_mh_initialized = false;
        }
        out->rolled_back = created;
    }
    g_state = STATE_RETIRED;
    return restored ? OK : ROLLBACK_FAILED;
}

}  // namespace

namespace ilc::patch {

int32_t preflight(uintptr_t base, report* out)
{
    lock_guard lock;
    clear_report(out);
    g_mh_calls = 0;
    static bool skip[SITE_COUNT];
    return preflight_locked(base, out, skip);
}

int32_t install(uintptr_t base, report* out)
{
    lock_guard lock;
    clear_report(out);
    g_mh_calls = 0;
    if (g_state == STATE_INSTALLED) {
        return finish(out, ALREADY_INSTALLED);
    }
    if (g_state == STATE_RETIRED) {
        return finish(out, INSTALL_RETIRED);
    }
    static bool skip[SITE_COUNT];
    const int32_t checked = preflight_locked(base, out, skip);
    if (checked != OK) {
        return checked;  // read-only so far: MinHook untouched, nothing written
    }
    uint32_t wanted = 0;
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        wanted += is_patch(row) && !skip[row] ? 1 : 0;
    }
    if (wanted == 0) {
        return finish(out, NOTHING_TO_PATCH);
    }

    // 0. Quiescence: no other thread may sit inside a window we will overwrite. Retry a few times; refuse if one
    //    stays there. Nothing has been written and MinHook is untouched, so the state stays IDLE.
    probe_result probe = {OK, -1, 0, 0, 0};
    for (uint32_t attempt = 0; attempt < QUIESCE_ATTEMPTS; ++attempt) {
        probe = quiesce_probe(base, skip);
        out->threads_probed = probe.probed;
        out->threads_busy = probe.busy;
        out->threads_vanished = probe.vanished;
        if (probe.result != QUIESCE_BUSY) {
            break;
        }
        ++out->quiesce_retries;
        if (const quiesce_hook_fn hook = g_quiesce_hook) {
            hook(QUIESCE_PHASE_BUSY, attempt);
        }
        if (attempt + 1 < QUIESCE_ATTEMPTS) {
            Sleep(QUIESCE_DELAY_MS);
        }
    }
    if (probe.result != OK) {
        if (probe.result == QUIESCE_BUSY) {
            --out->quiesce_retries;  // the last busy probe is the refusal, not a retry
        }
        return finish(out, probe.result, probe.busy_row);
    }
    if (const quiesce_hook_fn hook = g_quiesce_hook) {
        hook(QUIESCE_PHASE_CLEAN, out->quiesce_retries);
    }

    // 1. Create every hook. MinHook allocates its buffers and writes trampolines; the game image is untouched.
    const MH_STATUS init = mh_initialize();
    if (init != MH_OK) {
        g_state = STATE_RETIRED;
        return finish(out, MH_INIT_FAILED, -1, init);
    }
    g_mh_initialized = true;
    g_base = base;
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        if (!is_patch(row) || skip[row] || island_row(row)) {
            continue;
        }
        void* original = nullptr;
        MH_STATUS created = MH_UNKNOWN;
        if (!(g_inject_stage == TEST_FAIL_CREATE && g_inject_row == row)) {
            created = mh_create(site_va(base, SITES[row]), stub_address(row), &original);
        }
        if (created != MH_OK) {
            const int32_t undone = rollback(base, out, false);
            return finish(out, undone == OK ? CREATE_FAILED : ROLLBACK_FAILED, static_cast<int32_t>(row), created);
        }
        g_hooked[row] = true;
        ilc_patch_tramp[row] = original;  // unreachable until the jumps are written
        g_original[row] = original;
    }
    // 1a. Call rows: islands near the image, each jumping to its stub; the image is still untouched.
    {
        const uint32_t fail_row = g_inject_stage == TEST_FAIL_CREATE ? g_inject_row : SITE_COUNT;
        const int32_t made = island::prepare(base, skip, fail_row);
        for (uint32_t row = 0; row < SITE_COUNT; ++row) {
            if (island_row(row) && island::prepared(row)) {
                g_hooked[row] = true;
                ilc_patch_tramp[row] = reinterpret_cast<void*>(island::callee(row));
                g_original[row] = ilc_patch_tramp[row];
            }
        }
        if (made != island::ISLAND_OK) {
            int32_t failed = -1;
            for (uint32_t row = 0; row < SITE_COUNT && failed < 0; ++row) {
                if (island_row(row) && !skip[row] && !island::prepared(row)) {
                    failed = static_cast<int32_t>(row);
                }
            }
            const int32_t undone = rollback(base, out, false);
            return finish(out, undone == OK ? ISLAND_FAILED : ROLLBACK_FAILED, failed, made);
        }
    }

    // 2. Enable them together: MinHook suspends the other threads, fixes their instruction pointers and writes
    //    the jumps. MinHook's own heap holds its thread list, so nothing allocates while threads are suspended.
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        if (mh_row(row)) {
            const MH_STATUS queued = mh_queue_enable(site_va(base, SITES[row]));
            if (queued != MH_OK) {
                const int32_t undone = rollback(base, out, false);
                return finish(out, undone == OK ? QUEUE_FAILED : ROLLBACK_FAILED, static_cast<int32_t>(row), queued);
            }
        }
    }
    // 2a. Last look before the jumps go in: creating the hooks took time, and another module may have written a
    //     site meanwhile. Any PATCH row that no longer holds its pinned bytes aborts on the not-enabled path.
    //     REQUIRE rows must still be stock, and every GATE row must still decide what it decided at preflight
    //     (a concurrent writer at the gate, e.g. a mod rewriting the approach to a gated site).
    if (g_inject_stage == TEST_CHANGE_BEFORE_APPLY) {
        if (const quiesce_hook_fn hook = g_quiesce_hook) {
            hook(QUIESCE_PHASE_BEFORE_APPLY, g_inject_row);
        }
    }
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        const site& s = SITES[row];
        bool changed = false;
        if (g_hooked[row] || s.policy == ILC_SITE_POLICY_REQUIRE) {
            changed = row_matches(base, s) != 1;
        } else if (s.policy == ILC_SITE_POLICY_GATE && s.gate < SITE_COUNT) {
            changed = (row_matches(base, s) != 1) != skip[s.gate];
        }
        if (changed) {
            const int32_t undone = rollback(base, out, false, static_cast<int32_t>(row));
            return finish(out, undone == OK ? SITE_CHANGED : ROLLBACK_FAILED, static_cast<int32_t>(row));
        }
    }
    MH_STATUS applied = mh_apply();
    if (applied == MH_OK && g_inject_stage == TEST_FAIL_APPLY) {
        applied = MH_ERROR_MEMORY_PROTECT;
    }
    if (applied != MH_OK) {
        const int32_t undone = rollback(base, out, true);
        return finish(out, undone == OK ? APPLY_FAILED : ROLLBACK_FAILED, -1, applied);
    }
    // 2b. Call rows: the rel32s, all in one freeze of the other threads (no instruction pointer inside one).
    const int32_t committed = island::commit(base);
    if (committed != island::ISLAND_OK) {
        const int32_t undone = rollback(base, out, true);
        return finish(out, undone == OK ? ISLAND_COMMIT_FAILED : ROLLBACK_FAILED, -1, committed);
    }

    // 3. Every site must now hold MinHook's jump to our stub, with the rest of its window untouched.
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        if (!g_hooked[row]) {
            continue;
        }
        const bool injected = (g_inject_stage == TEST_FAIL_VERIFY || g_inject_stage == TEST_FAIL_DISABLE) &&
                              g_inject_row == row;
        if (injected || !site_holds_our_jump(base, row)) {
            const int32_t undone = rollback(base, out, true);
            return finish(out, undone == OK ? VERIFY_FAILED : ROLLBACK_FAILED, static_cast<int32_t>(row));
        }
    }
    g_state = STATE_INSTALLED;
    out->hooked = wanted;
    recompute_gates();
    return finish(out, OK);
}

uint32_t current_state()
{
    return g_state;
}

void apply_subsystems(uint32_t mask)
{
    lock_guard lock;
    g_subsystems = mask & static_cast<uint32_t>(ILC_ENUM_OR(ILC_SUBSYSTEMS));
    recompute_gates();
}

bool set_site_armed(uint32_t row, bool armed)
{
    if (!is_patch(row)) {
        return false;
    }
    lock_guard lock;
    g_site_armed[row] = armed;
    recompute_gates();
    return true;
}

bool set_handler(uint32_t row, handler_fn handler)
{
    if (!is_patch(row)) {
        return false;
    }
    lock_guard lock;
    __atomic_store_n(&g_handlers[row], handler, __ATOMIC_RELEASE);
    recompute_gates();
    return true;
}

bool set_after(uint32_t row, after_fn after)
{
    if (!is_patch(row) || !island_row(row)) {
        return false;
    }
    __atomic_store_n(&g_after[row], after, __ATOMIC_RELEASE);
    return true;
}

bool is_call_row(uint32_t row)
{
    return island_row(row);
}

void set_armed(bool armed)
{
    lock_guard lock;
    g_live.armed = armed ? 1u : 0u;
    recompute_gates();
}

void heartbeat(uint32_t epoch)
{
    // One writer. seq goes odd, the pair changes, seq goes even: a reader that saw either odd or two different
    // values discards its sample.
    const uint32_t odd = (g_live.seq + 1u) | 1u;  // the writer always leaves seq even, so this is seq + 1
    __atomic_store_n(&g_live.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    g_live.epoch = epoch;
    g_live.heartbeat_ms = g_clock();
    __atomic_store_n(&g_live.seq, odd + 1u, __ATOMIC_RELEASE);
}

void set_mode(uint32_t mode)
{
    if (mode != MODE_DRY && mode != MODE_LIVE) {
        return;
    }
    lock_guard lock;
    g_mode = mode;
    recompute_gates();
}

uint32_t current_mode()
{
    return g_mode;
}

bool try_apply_subsystems(uint32_t mask)
{
    if (!TryAcquireSRWLockExclusive(&g_lock)) {
        return false;
    }
    g_subsystems = mask & static_cast<uint32_t>(ILC_ENUM_OR(ILC_SUBSYSTEMS));
    recompute_gates();
    ReleaseSRWLockExclusive(&g_lock);
    return true;
}

void fault_subsystem(uint32_t bits)
{
    __atomic_or_fetch(&g_faulted, bits, __ATOMIC_ACQ_REL);
    // The dispatcher reads g_faulted on every slow-path entry; close the fast-path gates now if the lock is free,
    // otherwise the next recompute does it.
    if (TryAcquireSRWLockExclusive(&g_lock)) {
        recompute_gates();
        ReleaseSRWLockExclusive(&g_lock);
    }
}

uint32_t faulted_mask()
{
    return __atomic_load_n(&g_faulted, __ATOMIC_ACQUIRE);
}

uint32_t subsystems_mask()
{
    return g_subsystems;
}

bool subsystem_live(uint32_t subsystem)
{
    if (g_mode != MODE_LIVE || g_live.armed != 1 || g_state != STATE_INSTALLED) {
        return false;
    }
    const uint32_t faulted = __atomic_load_n(&g_faulted, __ATOMIC_ACQUIRE);
    if (subsystem == ILC_SITE_SUBSYS_CORE) {
        return (faulted & FAULT_CORE) == 0;
    }
    return (subsystem & ~static_cast<uint32_t>(ILC_ENUM_OR(ILC_SUBSYSTEMS))) == 0 && (g_subsystems & subsystem) == subsystem &&
           (faulted & subsystem) == 0;
}

liveness_view sample_liveness()
{
    liveness_view v;
    const uint32_t first = __atomic_load_n(&g_live.seq, __ATOMIC_ACQUIRE);
    v.epoch = g_live.epoch;
    v.heartbeat_ms = g_live.heartbeat_ms;
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    const uint32_t second = __atomic_load_n(&g_live.seq, __ATOMIC_RELAXED);
    v.armed = g_live.armed;
    v.coherent = first == second && (first & 1u) == 0;
    v.now_ms = g_clock();
    v.fresh = v.coherent && v.armed == 1 && v.epoch != 0 && v.now_ms >= v.heartbeat_ms &&
              v.now_ms - v.heartbeat_ms <= LIVENESS_STALE_MS;
    return v;
}

uint64_t now_ms()
{
    return g_clock();
}

void note_declined(uint32_t row)
{
    if (row < SITE_COUNT) {
        __atomic_add_fetch(&g_stats[row].declined, 1, __ATOMIC_RELAXED);
    }
}

void* stub_address(uint32_t row)
{
    if (island_row(row)) {
        return island::entry(row);
    }
    return const_cast<char*>(ilc_patch_stub_base) + static_cast<size_t>(row) * STUB_STRIDE;
}

void* trampoline(uint32_t row)
{
    return row < SITE_COUNT ? ilc_patch_tramp[row] : nullptr;
}

uint8_t gate(uint32_t row)
{
    return row < SITE_COUNT ? ilc_patch_gate[row] : 0;
}

const liveness* liveness_record()
{
    return &g_live;
}

const site_stats* stats(uint32_t row)
{
    return row < SITE_COUNT ? &g_stats[row] : nullptr;
}

void test_inject(uint32_t stage, uint32_t row)
{
    lock_guard lock;
    g_inject_stage = stage;
    g_inject_row = row;
}

void* test_original(uint32_t row)
{
    return row < SITE_COUNT ? g_original[row] : nullptr;
}

void test_set_quiesce_hook(quiesce_hook_fn hook)
{
    lock_guard lock;
    g_quiesce_hook = hook;
}

void test_seq_poke(uint32_t seq)
{
    __atomic_store_n(&g_live.seq, seq, __ATOMIC_RELEASE);
}

void test_hold_lock(bool hold)
{
    if (hold) {
        AcquireSRWLockExclusive(&g_lock);
    } else {
        ReleaseSRWLockExclusive(&g_lock);
    }
}

int32_t test_release_all()
{
    lock_guard lock;
    close_all_gates();
    bool restored = true;
    if (island::committed()) {
        island::revert(g_base, SITE_COUNT);
    }
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        if (g_hooked[row] && island_row(row)) {
            restored = restored && row_matches(g_base, SITES[row]) == 1;
        }
    }
    island::release();
    if (g_mh_initialized) {
        for (uint32_t row = 0; row < SITE_COUNT; ++row) {
            if (mh_row(row)) {
                mh_remove(site_va(g_base, SITES[row]));  // an enabled hook: removed with ACTION_DISABLE (see above)
                restored = restored && row_matches(g_base, SITES[row]) == 1;
            }
        }
        mh_uninitialize();
        g_mh_initialized = false;
    }
    for (uint32_t row = 0; row < SITE_COUNT; ++row) {
        g_hooked[row] = false;
        g_site_armed[row] = false;
        g_handlers[row] = nullptr;
        g_after[row] = nullptr;
        ilc_patch_tramp[row] = nullptr;
        g_original[row] = nullptr;
        g_stats[row].entered = 0;
        g_stats[row].stock = 0;
        g_stats[row].handled = 0;
        g_stats[row].declined = 0;
    }
    g_live.armed = 0;
    g_live.epoch = 0;
    g_live.heartbeat_ms = 0;
    g_live.seq = 0;
    g_subsystems = static_cast<uint32_t>(ILC_ENUM_OR(ILC_SUBSYSTEMS));
    g_mode = MODE_DRY;
    g_faulted = 0;
    g_quiesce_hook = nullptr;
    g_state = STATE_IDLE;
    g_base = 0;
    g_inject_stage = TEST_NONE;
    g_clock = default_clock;
    return restored ? OK : ROLLBACK_FAILED;
}

void* test_clock_slot()
{
    return const_cast<uint64_t (**)()>(&g_clock);
}

volatile uint8_t* test_gate_array()
{
    return ilc_patch_gate;
}

void* const* test_trampoline_array()
{
    return const_cast<void* const*>(ilc_patch_tramp);
}

}  // namespace ilc::patch

namespace {

// The checks every slow path repeats without the lock: a gate byte may lag a mode change or a latched fault.
// True = the handler may run, with the sampled epoch.
bool may_run(uint32_t row, handler_fn handler, uint32_t* epoch)
{
    const site& s = SITES[row];
    bool live = ilc_patch_gate[row] != 0 && handler != nullptr &&
                (g_mode == MODE_LIVE || s.subsystem == ILC_SITE_SUBSYS_CORE) &&
                (__atomic_load_n(&g_faulted, __ATOMIC_ACQUIRE) & fault_bit(s)) == 0;
    *epoch = 0;
    if (live) {
        const liveness_view v = sample_liveness();
        live = v.fresh;
        *epoch = v.epoch;
    }
    return live;
}

enum call_exit : uint64_t { EXIT_TAIL = 0, EXIT_RETURN = 1, EXIT_AROUND = 2 };

}  // namespace

// The MinHook rows' slow path. Runs on whichever game thread reached the site: no lock, no allocation, no I/O.
extern "C" uint64_t ilc_patch_dispatch(ctx* c)
{
    const uint32_t row = static_cast<uint32_t>(c->index);  // written by our own stub, always < SITE_COUNT
    if (row >= SITE_COUNT) {
        __builtin_trap();
    }
    const uint64_t stock = reinterpret_cast<uint64_t>(ilc_patch_tramp[row]);
    site_stats& st = g_stats[row];
    __atomic_add_fetch(&st.entered, 1, __ATOMIC_RELAXED);
    const handler_fn handler = __atomic_load_n(&g_handlers[row], __ATOMIC_ACQUIRE);
    uint32_t epoch;
    if (!may_run(row, handler, &epoch)) {
        __atomic_add_fetch(&st.stock, 1, __ATOMIC_RELAXED);
        return stock;
    }
    __atomic_add_fetch(&st.handled, 1, __ATOMIC_RELAXED);
    if (handler(c, epoch) == RESUME && c->resume != 0) {
        return c->resume;
    }
    return stock;
}

// The call rows' slow path (call_stubs.cpp): ctx->index arrives as the call index k and leaves as the row. The
// return value is the continuation; ctx->reserved the exit mode (TAIL: jump there with the return address kept;
// RETURN: pop it and continue there; AROUND: call there, then ilc_call_after).
extern "C" uint64_t ilc_call_dispatch(ctx* c)
{
    const uint32_t k = static_cast<uint32_t>(c->index);
    if (k >= island::ROWS) {
        __builtin_trap();
    }
    const uint32_t row = island::row_of(k);
    c->index = row;
    c->reserved = EXIT_TAIL;
    const uint64_t stock = island::callee(row);
    site_stats& st = g_stats[row];
    __atomic_add_fetch(&st.entered, 1, __ATOMIC_RELAXED);
    const handler_fn handler = __atomic_load_n(&g_handlers[row], __ATOMIC_ACQUIRE);
    uint32_t epoch;
    if (!may_run(row, handler, &epoch)) {
        __atomic_add_fetch(&st.stock, 1, __ATOMIC_RELAXED);
        return stock;
    }
    __atomic_add_fetch(&st.handled, 1, __ATOMIC_RELAXED);
    const int32_t decision = handler(c, epoch);
    if (decision == RESUME && c->resume != 0) {
        c->reserved = EXIT_RETURN;
        return c->resume;
    }
    if (decision == CALL_AROUND && __atomic_load_n(&g_after[row], __ATOMIC_ACQUIRE) != nullptr) {
        c->reserved = EXIT_AROUND;
        return stock;
    }
    return stock;
}

// After the callee returned (CALL_AROUND): the row's after-handler sees what it returned. Returns where to
// continue (the original return address unless the after-handler set ctx->resume).
extern "C" uint64_t ilc_call_after(ctx* c)
{
    const uint32_t row = static_cast<uint32_t>(c->index);
    const uint64_t back = *reinterpret_cast<const uint64_t*>(c->rsp);
    if (row >= SITE_COUNT) {
        return back;
    }
    const after_fn after = __atomic_load_n(&g_after[row], __ATOMIC_ACQUIRE);
    c->resume = 0;
    if (after != nullptr) {
        after(c, sample_liveness().epoch);
    }
    return c->resume != 0 ? c->resume : back;
}
