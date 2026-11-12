// Ivalice Legacy co-op module: start-up, readiness and shutdown.
//
// The External File Loader calls the module's lifecycle exports (efl.h). DllMain never does anything; all work
// starts here, outside the loader lock.
//
// on_init (FF12Hinit: main thread, before WinMain), synchronously and in this order; any refusal leaves the module
// inert except for the bridge, which keeps answering status:
//   1. host probe: the process image is at 0x120000, AMD64 PE32+, PE timestamp 0x5DCA7FD7, SizeOfImage 0x2F76000.
//      Anything else -> INERT / UNSUPPORTED_BUILD: nothing created, nothing read beyond the PE headers.
//   2. instance guard: a per-process named mutex. A second copy of the module (a stale duplicate deployment) finds
//      it and stays INERT / DUPLICATE_INSTANCE. A second FF12Hinit in the same copy does nothing.
//   3. Ivalice Together (together_detect.h): any blocking marker -> BLOCKED / TOGETHER_PRESENT.
//   4. the Co-op module switch, read-only from the settings file (settings_file.h): Off or unreadable -> DISABLED.
//      Off means no hook, no allocation near the image, no input thread, no init thread.
//   5. the init thread starts (STARTING / BOOTING) and on_init returns.
//
// The init thread, which then stays on as the co-op thread:
//   a. hands the applied settings to the input service and starts it (SDL_Init runs on the service's own thread);
//      waits until it runs (or fails: FAULT / INPUT_FAILED, nothing armed);
//   b. checks Ivalice Together again (BLOCKED, input stopped, nothing armed);
//   c. closes every subsystem arm bit, keeps the patch layer in DRY mode and runs the all-or-nothing hook
//      transaction (any refusal: FAULT / PATCH_CONFLICT, PATCH_BUSY or PATCH_FAILED, input stopped);
//   d. attaches the UI-tick handler (settings safe point) and arms the liveness record: ARMED / DRY_ARMED, or READY
//      once FF12HinitDone has run. Every detour still runs stock; the safe point applies settings pushed by Lua, and
//      only an applied settings record changes the subsystem arm bits. The boot never leaves DRY mode;
//   e. starts the session core (session/session.h) and, every HEARTBEAT_MS until shutdown, runs one session step
//      and heartbeats the liveness record with the session's epoch (FIRST_EPOCH until a world exists; a new
//      epoch at every map jump, load, title screen or game over). It also hands the session the applied settings
//      and the live park state, and reaps game-thread jobs the game thread can no longer reach.
//
// on_init_done (FF12HinitDone, the loader's watcher thread): the game runs; ARMED becomes READY.
// on_cleanup (FF12Hcleanup, from the loader's ExitProcess hook, never under the loader lock), in this order:
//   disarm (every detour stock at once; queued game-thread jobs rejected as DISARMED) -> stop and join the init
//   thread (it withdraws the session record) -> stop the input service. The hooks stay
//   installed: their bytes jump into this module, which stays loaded until the process ends.
#pragma once

#include <stdint.h>

namespace ilc::boot {

constexpr uintptr_t IMAGE_BASE = 0x120000;       // VA: the game's fixed image base
constexpr uint32_t IMAGE_TIMESTAMP = 0x5DCA7FD7; // NOTADDR
constexpr uint32_t IMAGE_SIZE = 0x2F76000;       // NOTADDR
constexpr uint32_t HEARTBEAT_MS = 20;
constexpr uint32_t INPUT_START_TIMEOUT_MS = 10000;
constexpr uint32_t CLEANUP_JOIN_MS = 3000;
constexpr uint32_t INPUT_STOP_MS = 2000;

struct status {
    uint32_t state;        // ilc_state
    uint32_t reason;       // ilc_reason
    uint32_t detail;       // reason_detail
    uint32_t flags;        // ilc_boot_flag bits
};

void on_init();
void on_init_done();
void on_cleanup();

// Any thread (the bridge reads it for HELLO and GET_STATUS).
status current();

// ---- host tests only ------------------------------------------------------------------------------------------
// The process the boot believes it runs in. The shipped module always uses its own process (GetModuleHandle(NULL),
// that image's folder, GetCurrentProcessId); a host test sets this before calling FF12Hinit.
struct host {
    uintptr_t image_base;
    uint32_t pid;              // for the Together mutex names
    uint32_t reserved;
    wchar_t x64_dir[512];      // the game's x64 folder
    wchar_t guard_name[96];    // the instance guard; empty = the shipped name
};
void test_set_host(const host* h);
// Called by the init thread at the start of each step (ilc_boot_step) and before arming (step 0x80).
using step_hook_fn = void (*)(uint32_t step);
void test_set_step_hook(step_hook_fn hook);
// The order of lifecycle events (event codes below), for the cleanup-ordering test.
enum event : uint32_t {
    EV_INIT_THREAD = 1, EV_INPUT_STARTED = 2, EV_HOOKS_INSTALLED = 3, EV_ARMED = 4, EV_INIT_DONE = 5,
    EV_CLEANUP_BEGIN = 6, EV_DISARMED = 7, EV_THREAD_JOINED = 8, EV_INPUT_STOPPED = 9, EV_CLEANUP_END = 10,
    EV_THREAD_EXIT = 11, EV_JOIN_TIMEOUT = 12, EV_INPUT_START_CALL = 13,
};
uint32_t test_events(uint32_t* out, uint32_t max);
uint32_t test_init_thread_id();
uint32_t test_input_start_thread_id();   // the thread that called input::start_live()
bool test_wait_settled(uint32_t timeout_ms);  // the init thread finished its steps (or exited)

}  // namespace ilc::boot
