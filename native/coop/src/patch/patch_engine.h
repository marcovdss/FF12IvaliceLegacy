// Ivalice Legacy co-op module: the patch layer around MinHook.
//
// MinHook (third_party/minhook, pinned) does the hook mechanics: a buffer near each site, the trampoline (its own
// decoder relocates the displaced instructions), the 5-byte jmp, thread suspension and instruction-pointer
// fix-up. This layer owns the rules around it:
//
//  * Preflight, read-only: the module base must be 0x120000, the PE timestamp 0x5DCA7FD7 and SizeOfImage
//    0x2F76000; every PATCH, REQUIRE and GATE row must hold its pinned bytes (for PATCH rows: every byte MinHook
//    relocates, hook tail included), on committed executable pages, with no INTERIOR flag and no overlap between
//    a hook window and another checked row. Any refusal returns a reason and the failing row; nothing is written
//    and MinHook is not even initialised. A GATE mismatch skips the one row it gates; an INFO mismatch is counted.
//  * Quiescence: before MinHook is touched, the layer suspends every other thread of the process (thread ids
//    and handles in fixed static arrays: nothing is allocated while a thread is suspended), reads each one's RIP,
//    and resumes them all. If any RIP lies inside a hook window it waits QUIESCE_DELAY_MS and tries again, up to
//    QUIESCE_ATTEMPTS probes; if a thread is still there it refuses with QUIESCE_BUSY, nothing written and MinHook
//    untouched (state stays IDLE, a later install may try again). A thread that enters a window after a clean
//    probe is still safe: MinHook's own freeze moves its RIP into the trampoline (and back on rollback).
//    No hook window holds a call before its last instruction (host test), so no return address can point inside.
//  * Install, all or nothing: MH_CreateHook for every PATCH row (detour = that row's stub). If one create fails,
//    every hook is removed (none was ever enabled). Then MH_QueueEnableHook for all and one MH_ApplyQueued, which
//    suspends the other threads and moves any instruction pointer out of the overwritten bytes. Afterwards every
//    site must hold E9 -> MinHook's relay (FF 25 00000000 <our stub>) with its remaining window bytes untouched.
//    Just before the enable, every PATCH and REQUIRE row is compared again and every GATE row must still decide
//    the same way (another module may have written one while the hooks were being created); a change aborts on
//    the not-enabled path (SITE_CHANGED).
//    On any failure: disable all (one ApplyQueued, then one by one if needed), check that every window holds its
//    pinned bytes again, point every stub whose site is verified restored straight at it (a row that did not
//    restore keeps MinHook's trampoline as its continuation), and keep MinHook's buffers mapped: a
//    thread may be inside a callee that a trampoline called, with its return address in that buffer. Freeing it
//    is safe only while no thread can have run the hooks (create-phase failures, and host tests).
//  * Arm state: per site (site_armed), per subsystem (the subsystems_armed mask; CORE rows have no bit), a global
//    armed flag, the mode, the fault latch and a registered handler together open one gate byte per site. The
//    mode starts DRY (first boot, "dry-armed"): every non-CORE gate stays closed whatever else is set, so every
//    detour runs stock; only the CORE tick (UT-01) may run, for the module's own housekeeping. LIVE lets
//    the other gates open. A fault latched for a subsystem (or for everything) closes its rows in the dispatcher
//    at once, without the lock, and in the gates at the next recompute. A closed gate is the stub's fast
//    path: four instructions that touch no register or flag and jump to the trampoline (stock). There is no
//    hot reinstall: after a rolled-back install, install() refuses.
//  * Call-type rows (a `call rel32` inside the hook window: 18 rows) do not use MinHook at all. Their rel32 is
//    retargeted to a jump-only island near the image, the island jumps to a stub with static unwind data, and
//    a closed gate sends the call straight to the original callee. See call_island.h. They take part in the
//    same transaction: preflight, quiescence, islands prepared with the MinHook creates, rel32s committed after
//    MinHook's enable inside their own thread freeze, verified with the rest, and reverted by every rollback.
//  * Fail-open detour template (the stub's slow path): saves every general register, the flags and XMM0-5 into
//    a `ctx`, then dispatch() re-checks the gate, the mode, the fault latch and the liveness record (a coherent
//    seqlock sample, armed exactly 1, epoch non-zero, last heartbeat no older than 2 s by the module clock); stale,
//    torn or closed -> the trampoline, otherwise the handler may change the saved registers and either run the
//    original instructions or resume at ctx->resume. A stale sample is not a fault: that one call runs stock and
//    the next fresh heartbeat opens the path again (no latch, no re-patching). src/patch/detour.h builds the
//    per-subsystem template on top (published records, epoch match, fault latch).
//
// Nothing calls this yet: FF12Hinit stays empty until the module boot wires it. Host tests drive it through the test
// build's ILCT_Patch* exports. The calling thread must not be a game thread; install() is not reentrant.
#pragma once

#include <stdint.h>

#include "patch/site_table.h"

namespace ilc::patch {

enum result : int32_t {
    OK = 0,
    // preflight
    BAD_BASE = 1,           // module base is not 0x120000
    HEADERS_UNREADABLE = 2, // the PE headers could not be read
    BAD_PE = 3,             // not an AMD64 PE32+ image
    BAD_TIMESTAMP = 4,
    BAD_SIZE = 5,
    SITE_UNREADABLE = 6,    // a checked row is not on committed, readable pages (PATCH: executable)
    SITE_MISMATCH = 7,      // a PATCH or REQUIRE row differs from its pinned bytes
    SITE_INTERIOR = 8,      // a PATCH row carries the INTERIOR flag
    SITE_OVERLAP = 9,       // a hook window overlaps another checked row
    // install
    ALREADY_INSTALLED = 20,
    INSTALL_RETIRED = 21,   // an earlier install was rolled back: no hot reinstall
    MH_INIT_FAILED = 22,
    CREATE_FAILED = 23,     // MH_CreateHook refused a row (detail = MH_STATUS)
    QUEUE_FAILED = 24,
    APPLY_FAILED = 25,      // MH_ApplyQueued failed (detail = MH_STATUS)
    VERIFY_FAILED = 26,     // a site does not hold MinHook's jump to our stub after enabling
    ROLLBACK_FAILED = 27,   // after rollback a window still differs from its pinned bytes (critical)
    NOTHING_TO_PATCH = 28,
    QUIESCE_BUSY = 29,      // a thread stayed inside a hook window for every probe (site = that row): nothing written
    QUIESCE_FAILED = 30,    // the thread snapshot failed or the process has more than QUIESCE_MAX_THREADS threads
    SITE_CHANGED = 31,      // a PATCH or REQUIRE row changed, or a GATE row's decision flipped, between preflight
                            // and the enable (site = row); nothing enabled
    ISLAND_FAILED = 32,     // a call island could not be prepared (site = row, detail = island::result); nothing
                            // enabled
    ISLAND_COMMIT_FAILED = 33,  // the call-site rel32s could not be written (detail = island::result); rolled back
};

constexpr uint32_t QUIESCE_ATTEMPTS = 8;      // probes per install (the first plus 7 retries)
constexpr uint32_t QUIESCE_DELAY_MS = 2;      // between probes; install() never runs on a game thread
constexpr uint32_t QUIESCE_MAX_THREADS = 1024;

enum mode : uint32_t {
    MODE_DRY = 0,   // first boot: hooks installed, every non-CORE detour stock (the CORE tick does housekeeping only)
    MODE_LIVE = 1,
};
constexpr uint32_t FAULT_CORE = 0x40000000u;  // NOTADDR: fault bit for CORE rows (no subsystem bit of their own)
constexpr uint32_t FAULT_ALL = 0xFFFFFFFFu;   // NOTADDR: every row

enum state : uint32_t {
    STATE_IDLE = 0,
    STATE_INSTALLED = 1,
    STATE_RETIRED = 2,      // rolled back; buffers kept, stubs point at the restored sites
};

struct report {
    int32_t result;         // enum result
    int32_t site;           // failing row index, or -1
    int32_t detail;         // MH_STATUS for MinHook failures, preflight result for ROLLBACK/VERIFY context
    uint32_t state;         // enum state after the call
    uint32_t patch_rows;    // PATCH rows in the table
    uint32_t hooked;        // rows hooked by this call (0 unless OK)
    uint32_t gated_off;     // PATCH rows skipped because their GATE row did not match
    uint32_t info_mismatch; // INFO rows that differ (information only)
    uint32_t mh_calls;      // MinHook API calls made by this call (0 = MinHook untouched)
    uint32_t rolled_back;   // hooks disabled by a rollback in this call
    uint32_t retained;      // hooks whose buffers a rollback kept mapped
    uint32_t quiesce_retries;  // busy probes before the result (0 = the first probe was clean)
    uint32_t threads_probed;   // threads suspended and inspected by the last probe
    uint32_t threads_busy;     // of those, inside a hook window (last probe)
    uint32_t threads_vanished; // listed by the snapshot but gone before they could be opened (last probe); a thread
                               // that exists but cannot be opened or suspended counts as busy
    uint32_t reserved;
};
static_assert(sizeof(report) == 64, "report layout (host tests read it)");

// The saved machine state handed to a handler. Offsets are fixed: the stub's assembly writes them.
struct ctx {
    uint64_t xmm[6][2];     // 0x00 XMM0..XMM5
    uint64_t resume;        // 0x60 handler sets it and returns RESUME to continue there instead of the original
    uint64_t reserved;      // 0x68 call rows: the exit mode the dispatcher chose (stub-internal; handlers ignore it)
    uint64_t rax, rcx, rdx, rbx, rsp, rbp, rsi, rdi;  // 0x70 (rsp = value at the site; changes are ignored)
    uint64_t r8, r9, r10, r11, r12, r13, r14, r15;    // 0xB0
    uint64_t rflags;        // 0xF0
    uint64_t index;         // 0xF8 site row; overwritten by the continuation on the way out
};
static_assert(sizeof(ctx) == 0x100 && __builtin_offsetof(ctx, rax) == 0x70 && __builtin_offsetof(ctx, r8) == 0xB0 &&
              __builtin_offsetof(ctx, rflags) == 0xF0 && __builtin_offsetof(ctx, index) == 0xF8, "stub layout");

// RESUME on a call row: skip the callee and continue at ctx->resume as if it had returned. CALL_AROUND (call rows
// only, needs set_after): call the callee, then the after-handler. See call_island.h.
enum handler_result : int32_t { RUN_ORIGINAL = 0, RESUME = 1, CALL_AROUND = 2 };
using handler_fn = int32_t (*)(ctx* c, uint32_t epoch);
// Runs after the callee on a call row whose handler returned CALL_AROUND. ctx holds the registers the callee
// returned (rax, xmm0, ...); it may edit them and set ctx->resume (0 = continue at the return address).
using after_fn = void (*)(ctx* c, uint32_t epoch);

// The liveness record. `armed` is written under the layer lock; epoch and heartbeat_ms by one writer (the co-op
// thread's tick) inside a seqlock: seq is odd while they change. Readers on game threads never wait: an odd or
// changed seq is a torn sample and counts as stale (stock for that one call).
struct liveness {
    volatile uint32_t armed;         // exactly 1 = armed; anything else = stock
    volatile uint32_t epoch;         // 0 = no world established yet = stock
    volatile uint64_t heartbeat_ms;  // module clock at the co-op thread's last tick
    volatile uint32_t seq;           // seqlock over epoch + heartbeat_ms (even = stable)
    uint32_t reserved;
};
static_assert(__builtin_offsetof(liveness, epoch) == 4 && __builtin_offsetof(liveness, heartbeat_ms) == 8 &&
              __builtin_offsetof(liveness, seq) == 16, "liveness layout (host tests write it)");
constexpr uint64_t LIVENESS_STALE_MS = 2000;

// One coherent sample of the liveness record, as a game thread sees it.
struct liveness_view {
    uint32_t armed;
    uint32_t epoch;
    uint64_t heartbeat_ms;
    uint64_t now_ms;
    bool coherent;  // the seqlock sample was not torn
    bool fresh;     // coherent, armed == 1, epoch != 0 and now - heartbeat <= LIVENESS_STALE_MS
};

struct site_stats {
    volatile uint64_t entered;  // slow-path entries (gate was open)
    volatile uint64_t stock;    // ... that ran the original because a check failed
    volatile uint64_t handled;  // ... that ran the handler
    volatile uint64_t declined; // ... handler ran and chose stock (no record, stale record epoch, subsystem off)
};

int32_t preflight(uintptr_t base, report* out);
int32_t install(uintptr_t base, report* out);
uint32_t current_state();

// Arm state. Each call recomputes the gates.
void apply_subsystems(uint32_t mask);              // ilc_settings.subsystems_armed at the safe point
bool set_site_armed(uint32_t row, bool armed);     // false for a non-PATCH row
bool set_handler(uint32_t row, handler_fn handler);
bool set_after(uint32_t row, after_fn after);      // false unless a call row
bool is_call_row(uint32_t row);                    // hooked through a call island (call_island.h)
void set_armed(bool armed);                        // liveness.armed (1 or 0)
void heartbeat(uint32_t epoch);                    // co-op thread tick (one writer): epoch and the module clock
void set_mode(uint32_t mode);                      // MODE_DRY or MODE_LIVE (others ignored)
uint32_t current_mode();
// Game-thread variants: never block. try_apply_subsystems() only tries the layer lock (false = busy, try again
// next tick). fault_subsystem() latches a bit (FAULT_CORE, FAULT_ALL or a subsystem bit) with one atomic OR; the
// dispatcher honours it at once and the gates follow at the next recompute.
bool try_apply_subsystems(uint32_t mask);
void fault_subsystem(uint32_t bits);
uint32_t faulted_mask();
uint32_t subsystems_mask();
// True if a job or record for this subsystem may act now: LIVE, armed == 1, the bit armed (CORE: always) and not
// faulted. Lock-free; any thread.
bool subsystem_live(uint32_t subsystem);
// Wait-free liveness sample for game threads (see liveness_view).
liveness_view sample_liveness();
uint64_t now_ms();                                 // the module clock
void note_declined(uint32_t row);                  // a handler chose stock (counted per row)

// Introspection for host tests (and later the Status page).
void* stub_address(uint32_t row);
void* trampoline(uint32_t row);    // nullptr unless hooked (or retired: then the site itself)
uint8_t gate(uint32_t row);
const liveness* liveness_record();
const site_stats* stats(uint32_t row);

// Host tests only (no export reaches these in the shipped DLL).
enum test_stage : uint32_t {
    TEST_NONE = 0,
    TEST_FAIL_CREATE = 1,
    TEST_FAIL_APPLY = 2,
    TEST_FAIL_VERIFY = 3,
    TEST_FAIL_DISABLE = 4,          // `row` fails its verify, and the enabled rollback never queues its disable:
                                    // its jump stays in place
    TEST_CHANGE_BEFORE_APPLY = 5,   // call the quiesce hook (QUIESCE_PHASE_BEFORE_APPLY, attempt = row) just before
                                    // the last look, so a test can write a row in between
};
void test_inject(uint32_t stage, uint32_t row);   // fail at that stage for that row (APPLY: row ignored)
// Called by install() between quiescence probes (phase QUIESCE_PHASE_BUSY, after a busy probe has resumed every
// thread) and after the clean probe (QUIESCE_PHASE_CLEAN, just before MinHook). Lets the image-twin drills move
// parked threads. nullptr in the shipped DLL.
enum quiesce_phase : uint32_t { QUIESCE_PHASE_BUSY = 0, QUIESCE_PHASE_CLEAN = 1, QUIESCE_PHASE_BEFORE_APPLY = 2 };
using quiesce_hook_fn = void (*)(uint32_t phase, uint32_t attempt);
void test_set_quiesce_hook(quiesce_hook_fn hook);
void test_seq_poke(uint32_t seq);                  // writes liveness.seq (torn-sample tests)
void test_hold_lock(bool hold);
void* test_original(uint32_t row);                 // MinHook's trampoline for the row, as MH_CreateHook returned it                    // acquire / release the layer lock (try-lock tests)
int32_t test_release_all();                        // disable + remove every hook, MH_Uninitialize, back to IDLE
void* test_clock_slot();                           // address of the module clock function pointer
volatile uint8_t* test_gate_array();
void* const* test_trampoline_array();

}  // namespace ilc::patch
