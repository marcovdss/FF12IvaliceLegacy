// Ivalice Legacy co-op module: start-up, readiness and shutdown (see boot.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "boot/boot.h"
#include "boot/settings_file.h"
#include "boot/together_detect.h"
#include "bridge/bridge_internal.h"
#include "ilc/bridge_abi.h"
#include "ilc/bridge_defaults.h"
#include "input/input_service.h"
#include "movement/movement.h"
#include "patch/patch_engine.h"
#include "runtime/diag.h"
#include "runtime/game_thread.h"
#include "runtime/probe.h"
#include "runtime/tick.h"
#include "camera/camera.h"
#include "control/control.h"
#include "firewall/firewall.h"
#include "focus/focus.h"
#include "hud/battle_hud.h"
#include "hud/battle_log.h"
#include "hud/canvas.h"
#include "hud/menus_draw.h"
#include "hud/native.h"
#include "hud/party_rows.h"
#include "hud/rings.h"
#include "hud/snapshot.h"
#include "hud/target_info.h"
#include "hud/world_marks.h"
#include "frontend/frontend.h"
#include "interaction/interaction.h"
#include "isolation/isolation.h"
#include "menu/cost.h"
#include "towns/towns.h"
#include "party/party.h"
#include "session/session.h"
#include "submit/submit.h"
#include "wait/wait.h"

namespace ilc::boot {
namespace {

constexpr DWORD INIT_THREAD_STACK = 256 * 1024;
constexpr uint32_t PATCH_BUSY_ATTEMPTS = 4;   // installs tried while a thread sits in a site (each probes 8 times)
constexpr uint32_t PATCH_BUSY_WAIT_MS = 100;
constexpr uint32_t STEP_BEFORE_ARM = 0x80;
constexpr uint32_t EVENT_CAP = 32;
constexpr uint32_t DETAIL_INPUT_TIMEOUT = 0x100;
constexpr uint32_t SETTINGS_EVERY = 5;        // co-op beats between looks at the applied settings and live park
constexpr uint64_t REAP_AFTER_MS = 100;       // the game thread has not ticked for this long: reap its queue
constexpr wchar_t GUARD_PREFIX[] = L"Local\\IvaliceLegacyCoop_";

// Status: written by the FF12Hinit thread, the init thread, the watcher thread and the cleanup thread; read by
// bridge calls. Held only to copy four words.
SRWLOCK g_status_lock = SRWLOCK_INIT;
status g_status = {ILC_STATE_INERT, ILC_REASON_NONE, 0, 0};

// Arming and stopping exclude each other: the init thread arms only if cleanup has not begun, and cleanup disarms
// after taking this lock, so a late install can never re-arm a module that is shutting down.
SRWLOCK g_arm_lock = SRWLOCK_INIT;
volatile uint32_t g_stopping = 0;

volatile uint32_t g_init_once = 0;
volatile uint32_t g_cleanup_once = 0;
volatile uint32_t g_settled = 0;
volatile uint32_t g_input_started = 0;
HANDLE g_guard = nullptr;     // held for the life of the process
HANDLE g_thread = nullptr;
HANDLE g_stop = nullptr;
HANDLE g_settled_event = nullptr;
host g_host;                  // resolved once in on_init
const host* volatile g_test_host = nullptr;
host g_test_host_copy;
step_hook_fn volatile g_step_hook = nullptr;

volatile uint32_t g_event_count = 0;
uint32_t g_events[EVENT_CAP];
volatile uint32_t g_init_tid = 0;
volatile uint32_t g_input_start_tid = 0;

void record(uint32_t ev)
{
    const uint32_t i = __atomic_fetch_add(&g_event_count, 1u, __ATOMIC_ACQ_REL);
    if (i < EVENT_CAP) {
        __atomic_store_n(&g_events[i], ev, __ATOMIC_RELEASE);
    }
}

void set_status(uint32_t state, uint32_t reason, uint32_t detail)
{
    AcquireSRWLockExclusive(&g_status_lock);
    g_status.state = state;
    g_status.reason = reason;
    g_status.detail = detail;
    ReleaseSRWLockExclusive(&g_status_lock);
}

void set_flags(uint32_t set, uint32_t clear = 0)
{
    AcquireSRWLockExclusive(&g_status_lock);
    g_status.flags = (g_status.flags & ~clear) | set;
    ReleaseSRWLockExclusive(&g_status_lock);
}

bool stopping()
{
    return __atomic_load_n(&g_stopping, __ATOMIC_ACQUIRE) != 0;
}

uint32_t flags_now()
{
    AcquireSRWLockShared(&g_status_lock);
    const uint32_t f = g_status.flags;
    ReleaseSRWLockShared(&g_status_lock);
    return f;
}

// Once cleanup has begun, a step changes nothing: it must not overwrite INERT / SHUT_DOWN or BOOT_ABORTED.
void step(uint32_t s)
{
    if (stopping()) {
        return;
    }
    if (s >= ILC_BOOT_STEP_CHECKS && s <= ILC_BOOT_STEP_ARM) {
        set_status(ILC_STATE_STARTING, ILC_REASON_BOOTING, s);
    }
    step_hook_fn hook = g_step_hook;
    if (hook != nullptr) {
        hook(s);
    }
}

// ---- host ---------------------------------------------------------------------------------------------------

bool resolve_host()
{
    const host* override_host = g_test_host;
    if (override_host != nullptr) {
        g_host = *override_host;
        g_host.x64_dir[sizeof(g_host.x64_dir) / sizeof(wchar_t) - 1] = L'\0';
        g_host.guard_name[sizeof(g_host.guard_name) / sizeof(wchar_t) - 1] = L'\0';
        return true;
    }
    memset(&g_host, 0, sizeof(g_host));
    g_host.image_base = reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr));
    g_host.pid = GetCurrentProcessId();
    const DWORD cap = sizeof(g_host.x64_dir) / sizeof(wchar_t);
    const DWORD n = GetModuleFileNameW(nullptr, g_host.x64_dir, cap);
    if (n == 0 || n >= cap) {
        g_host.x64_dir[0] = L'\0';  // unknown folder: file checks become uncertain (blocked), never skipped
        return true;
    }
    for (DWORD i = n; i > 0; --i) {
        if (g_host.x64_dir[i - 1] == L'\\' || g_host.x64_dir[i - 1] == L'/') {
            g_host.x64_dir[i - 1] = L'\0';
            return true;
        }
    }
    g_host.x64_dir[0] = L'\0';
    return true;
}

// 0 = the pinned build; otherwise the UNSUPPORTED_BUILD detail.
uint32_t probe_image(uintptr_t base)
{
    if (base != IMAGE_BASE) {
        return 1;
    }
    IMAGE_DOS_HEADER dos;
    if (!copy_from_caller(&dos, reinterpret_cast<const void*>(base), sizeof(dos)) || dos.e_magic != IMAGE_DOS_SIGNATURE ||
        dos.e_lfanew <= 0 || dos.e_lfanew > 0x1000) {
        return 2;
    }
    IMAGE_NT_HEADERS64 nt;
    if (!copy_from_caller(&nt, reinterpret_cast<const void*>(base + static_cast<uintptr_t>(dos.e_lfanew)), sizeof(nt)) ||
        nt.Signature != IMAGE_NT_SIGNATURE) {
        return 2;
    }
    if (nt.FileHeader.Machine != IMAGE_FILE_MACHINE_AMD64 || nt.OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
        return 3;
    }
    if (nt.FileHeader.TimeDateStamp != IMAGE_TIMESTAMP) {
        return 4;
    }
    if (nt.OptionalHeader.SizeOfImage != IMAGE_SIZE) {
        return 5;
    }
    return 0;
}

// 0 = this copy now holds the guard; else the reason.
uint32_t take_guard(uint32_t* detail)
{
    wchar_t name[96];
    if (g_host.guard_name[0] != L'\0') {
        memcpy(name, g_host.guard_name, sizeof(name));
    } else {
        const uint32_t prefix = sizeof(GUARD_PREFIX) / sizeof(wchar_t) - 1;
        memcpy(name, GUARD_PREFIX, prefix * sizeof(wchar_t));
        char digits[12];
        uint32_t count = 0;
        uint32_t v = g_host.pid;
        do {
            digits[count++] = static_cast<char>('0' + v % 10);
            v /= 10;
        } while (v != 0);
        for (uint32_t i = 0; i < count; ++i) {
            name[prefix + i] = static_cast<wchar_t>(digits[count - 1 - i]);
        }
        name[prefix + count] = L'\0';
    }
    SetLastError(ERROR_SUCCESS);
    HANDLE guard = CreateMutexW(nullptr, FALSE, name);
    if (guard == nullptr) {
        *detail = GetLastError();
        return ILC_REASON_INSTANCE_GUARD_FAILED;
    }
    if (GetLastError() == ERROR_ALREADY_EXISTS) {
        CloseHandle(guard);
        return ILC_REASON_DUPLICATE_INSTANCE;
    }
    g_guard = guard;
    return ILC_REASON_NONE;
}

// ---- the init thread ----------------------------------------------------------------------------------------

void stop_input()
{
    if (__atomic_exchange_n(&g_input_started, 0u, __ATOMIC_ACQ_REL) != 0) {
        input::stop_live(INPUT_STOP_MS);
        set_flags(0, ILC_BOOTF_INPUT_RUNNING);
        record(EV_INPUT_STOPPED);
    }
}

uint32_t patch_reason(int32_t result)
{
    switch (result) {
    case patch::SITE_UNREADABLE:
    case patch::SITE_MISMATCH:
    case patch::SITE_INTERIOR:
    case patch::SITE_OVERLAP:
    case patch::SITE_CHANGED:
        return ILC_REASON_PATCH_CONFLICT;
    case patch::QUIESCE_BUSY:
        return ILC_REASON_PATCH_BUSY;
    default:
        return ILC_REASON_PATCH_FAILED;
    }
}

// Step a: false with the status set (or on a stop request).
bool start_input()
{
    step(ILC_BOOT_STEP_INPUT);
    if (stopping()) {
        return false;
    }
    // The service starts from the applied settings (native defaults until Lua pushes); later records reach it at
    // the settings safe point.
    static ilc_settings applied;
    bridge_copy_applied(&applied);
    input::configure_from_settings(applied);
    __atomic_store_n(&g_input_start_tid, GetCurrentThreadId(), __ATOMIC_RELEASE);
    record(EV_INPUT_START_CALL);
    if (!input::start_live()) {
        input::snapshot s;
        input::snapshot_read(&s);
        set_status(ILC_STATE_FAULT, ILC_REASON_INPUT_FAILED, s.issues);
        return false;
    }
    __atomic_store_n(&g_input_started, 1u, __ATOMIC_RELEASE);
    if (stopping()) {
        stop_input();  // cleanup began while the service was starting
        return false;
    }
    const ULONGLONG deadline = GetTickCount64() + INPUT_START_TIMEOUT_MS;
    for (;;) {
        static input::snapshot s;  // init thread only; 8 KiB stays off the stack
        if (input::snapshot_read(&s)) {
            if (s.state == input::RUN_RUNNING) {
                break;
            }
            if (s.state == input::RUN_FAILED) {
                stop_input();
                set_status(ILC_STATE_FAULT, ILC_REASON_INPUT_FAILED, s.issues);
                return false;
            }
        }
        if (stopping()) {
            stop_input();
            return false;
        }
        if (GetTickCount64() >= deadline) {
            stop_input();
            set_status(ILC_STATE_FAULT, ILC_REASON_INPUT_FAILED, DETAIL_INPUT_TIMEOUT);
            return false;
        }
        Sleep(5);
    }
    set_flags(ILC_BOOTF_INPUT_RUNNING);
    record(EV_INPUT_STARTED);
    return true;
}

bool run_steps()
{
    if (!start_input()) {
        return false;
    }

    step(ILC_BOOT_STEP_RECHECK);
    if (stopping()) {
        return false;
    }
    const uint32_t markers = detect_together(g_host.x64_dir[0] != L'\0' ? g_host.x64_dir : nullptr, g_host.pid);
    if ((markers & TOGETHER_BLOCKING) != 0) {
        stop_input();
        set_status(ILC_STATE_BLOCKED, ILC_REASON_TOGETHER_PRESENT, markers);
        return false;
    }

    step(ILC_BOOT_STEP_PATCH);
    // DRY: every non-CORE detour runs stock whatever else is set. The arm bits start closed; only an applied
    // settings record (at the safe point) opens them.
    patch::set_mode(patch::MODE_DRY);
    patch::apply_subsystems(0);
    patch::report r;
    int32_t result = patch::OK;
    uint32_t attempts = 0;
    for (uint32_t attempt = 0; attempt < PATCH_BUSY_ATTEMPTS; ++attempt) {
        if (stopping()) {
            return false;
        }
        memset(&r, 0, sizeof(r));
        ++attempts;
        result = patch::install(g_host.image_base, &r);
        if (result != patch::QUIESCE_BUSY) {
            break;
        }
        Sleep(PATCH_BUSY_WAIT_MS);
    }
    diag::note_install(r, attempts);
    if (result != patch::OK) {
        stop_input();
        const uint32_t site = r.site >= 0 ? static_cast<uint32_t>(r.site) + 1 : 0;
        set_status(ILC_STATE_FAULT, patch_reason(result), (static_cast<uint32_t>(result) & 0xFFFFu) | (site << 16));
        return false;
    }
    set_flags(ILC_BOOTF_HOOKS_INSTALLED);
    record(EV_HOOKS_INSTALLED);

    step(ILC_BOOT_STEP_ARM);
    step(STEP_BEFORE_ARM);
    AcquireSRWLockExclusive(&g_arm_lock);
    if (stopping()) {
        ReleaseSRWLockExclusive(&g_arm_lock);
        return false;
    }
    if (!tick::attach()) {
        ReleaseSRWLockExclusive(&g_arm_lock);
        stop_input();
        set_status(ILC_STATE_FAULT, ILC_REASON_PATCH_FAILED, 0);
        return false;
    }
    // Subsystem handlers and game-thread operations, then the operation table is sealed. A subsystem that cannot
    // attach is latched as faulted: its detours stay stock and its jobs are refused, the rest of the module runs.
    if (!movement::attach()) {
        patch::fault_subsystem(ILC_SUBSYS_MOVEMENT);
    }
    if (!isolation::attach()) {
        patch::fault_subsystem(ILC_SUBSYS_INPUT_ISOLATION);
    }
    if (!camera::attach()) {
        patch::fault_subsystem(ILC_SUBSYS_CAMERA);
    }
    if (!towns::attach()) {
        patch::fault_subsystem(ILC_SUBSYS_PARTY);
    }
    if (!party::attach()) {
        patch::fault_subsystem(ILC_SUBSYS_PARTY);
    }
    // Guest interactions arm all their rows or none (the rows belong to MOVEMENT and PARTY, which keep running).
    // A failure is latched in the boot flags, so the Mod Menu's diagnostics can say guests cannot interact.
    if (!interaction::attach()) {
        set_flags(ILC_BOOTF_INTERACTIONS_FAILED);
    }
    if (!firewall::attach()) {
        patch::fault_subsystem(ILC_SUBSYS_FIREWALL_MENUS);
    }
    // Keep running in background: its rows stay stock if it cannot attach (no fault bit: the rest is unaffected).
    focus::attach();
    // The co-op HUD canvas: the frame hook's native draw functions must start with the pinned bytes and the draw
    // guard must be installed, else RENDER is latched faulted (the hook stays stock; nothing else depends on it).
    if (!hud::attach(g_host.image_base)) {
        patch::fault_subsystem(ILC_SUBSYS_RENDER);
    }
    // The native HUD changes (reduced party list, target circles off): a RENDER game-thread job; without it the
    // stock HUD stays as it is.
    hud::native::attach();
    // The party list row additions: the low-HP glow's two call rows stay stock if their handlers cannot be set.
    hud::party_rows::attach(g_host.image_base);
    // The expanded Target Info: TI-01 (the native window's open call) stays stock if its handler cannot be set.
    hud::target_info::attach(g_host.image_base);
    // The battle log: its feed LG-01 stays stock if its handler cannot be set (lines then come from the HP and status
    // diffs alone).
    hud::battle_log::attach(g_host.image_base);
    // Seat marks in the world and the AoE-only ring rule: each row (and the ring operation) stays stock on its own if
    // its handler or its pinned game functions are missing.
    hud::world_marks::attach(g_host.image_base);
    hud::rings::attach(g_host.image_base);
    // The Battle HUD page's report reads the frame hook's call bytes (fault-safe, from the co-op thread).
    hud::battle_hud::attach(g_host.image_base);
    // Battle-menu requests: the game functions they call must start with the pinned bytes, else no operation is
    // registered and every menu request is refused (the firewall and the rest keep running).
    submit::attach(g_host.image_base);
    // The Wait engine calls four game functions from the UI tick: their first bytes must be the pinned ones, else it
    // never calls anything (co-op Wait just never pauses; everything else runs).
    wait::attach(g_host.image_base);
    gtq::seal();
    // The session core owns the epoch from here on: FIRST_EPOCH until a world exists, a new one at every boundary.
    static ilc_settings applied_now;
    bridge_copy_applied(&applied_now);
    session::configure_from_settings(applied_now);
    isolation::configure_from_settings(applied_now);
    control::configure_from_settings(applied_now);
    firewall::configure_from_settings(applied_now);
    submit::configure_from_settings(applied_now);
    menu::configure_from_settings(applied_now);
    focus::configure_from_settings(applied_now);
    hud::configure_from_settings(applied_now);
    hud::party_rows::configure_from_settings(applied_now);
    hud::menus_draw::configure_from_settings(applied_now);
    hud::world_marks::configure_from_settings(applied_now);
    hud::rings::configure_from_settings(applied_now);
    hud::battle_hud::configure_from_settings(applied_now);  // after the elements' own: it sets their switches
    session::start(g_host.image_base, patch::now_ms());
    movement::start(g_host.image_base, patch::now_ms());
    camera::start(g_host.image_base);
    towns::start(g_host.image_base, patch::now_ms());
    party::start(g_host.image_base, patch::now_ms());
    interaction::start(g_host.image_base, patch::now_ms());
    firewall::start(g_host.image_base, patch::now_ms());
    submit::start(g_host.image_base, patch::now_ms());
    frontend::start(g_host.image_base, patch::now_ms());
    hud::snapshot::start(g_host.image_base, patch::now_ms());
    hud::menus_draw::start(g_host.image_base, patch::now_ms());
    patch::heartbeat(session::epoch());
    patch::set_armed(true);
    AcquireSRWLockExclusive(&g_status_lock);
    g_status.flags |= ILC_BOOTF_ARMED;
    g_status.state = (g_status.flags & ILC_BOOTF_INIT_DONE) != 0 ? ILC_STATE_READY : ILC_STATE_ARMED;
    g_status.reason = ILC_REASON_DRY_ARMED;
    g_status.detail = 0;
    ReleaseSRWLockExclusive(&g_status_lock);
    ReleaseSRWLockExclusive(&g_arm_lock);
    record(EV_ARMED);
    return true;
}

DWORD WINAPI init_main(void*)
{
    __atomic_store_n(&g_init_tid, GetCurrentThreadId(), __ATOMIC_RELEASE);
    diag::note_coop_thread(GetCurrentThreadId());
    record(EV_INIT_THREAD);
    const bool armed = run_steps();
    __atomic_store_n(&g_settled, 1u, __ATOMIC_RELEASE);
    SetEvent(g_settled_event);
    if (armed) {
        // The co-op thread: one session step per beat, then the liveness heartbeat with the session's epoch.
        // Every detour falls back to stock if this stops for more than 2 s.
        uint32_t beat = 0;
        diag::loop_begin();
        while (WaitForSingleObject(g_stop, HEARTBEAT_MS) == WAIT_TIMEOUT) {
            // The developer stall (diag.h): one sleep on the stop event, so a shutdown still ends it at once. Nothing
            // heartbeats meanwhile: past the stale threshold every detour runs stock. The beat after it carries on.
            uint32_t stall_request = 0;
            const uint32_t stall_ms = diag::take_stall(&stall_request);
            if (stall_ms != 0) {
                const ULONGLONG from = GetTickCount64();
                const bool stop_seen = WaitForSingleObject(g_stop, stall_ms) == WAIT_OBJECT_0;
                const uint64_t slept = GetTickCount64() - from;
                const uint32_t slept_ms = slept > 0xFFFFFFFFull ? 0xFFFFFFFFu : static_cast<uint32_t>(slept);
                diag::note_stall_done(slept_ms);
                bridge_note_command_result(stall_request, ILC_CMD_STALL_COOP_THREAD, slept_ms);
                if (stop_seen) {
                    break;
                }
            }
            const uint64_t beat_started = diag::beat_begin();
            if (++beat % SETTINGS_EVERY == 0) {
                static ilc_settings applied;
                static uint32_t applied_generation = 0;
                bridge_copy_applied(&applied);
                if (applied.header.generation != applied_generation) {
                    applied_generation = applied.header.generation;
                    session::configure_from_settings(applied);
                    isolation::configure_from_settings(applied);
                    control::configure_from_settings(applied);
                    firewall::configure_from_settings(applied);
                    submit::configure_from_settings(applied);
                    menu::configure_from_settings(applied);
                    focus::configure_from_settings(applied);
                    hud::configure_from_settings(applied);
                    hud::party_rows::configure_from_settings(applied);
                    hud::menus_draw::configure_from_settings(applied);
                    hud::world_marks::configure_from_settings(applied);
                    hud::rings::configure_from_settings(applied);
                    hud::battle_hud::configure_from_settings(applied);
                }
            }
            const uint64_t now = patch::now_ms();
            // Kill switch, seat toggles and the ordered disarm, before the session step: their effect is in this
            // beat's records. It also hands the session the live park bits (the input service's and the toggles').
            control::step(now);
            session::step(now);
            // The liveness epoch moves right after the session step, before any subsystem publishes for it.
            patch::heartbeat(session::epoch());
            // The co-op battle menus: players' menu input, external frontends, the Party request, the recovery
            // policy, the report the firewall composes from and the Wait request. Before interaction (a guest's
            // CONFIRM in an open menu is not an interaction) and before the firewall (the report of this beat).
            frontend::step(now);
            hud::step(now);       // the canvas frame (Diagnostics test draw) for the frame hook
            hud::native::step(now);   // the reduced party list and circles off, as a game-thread job while wanted
            hud::snapshot::step(now);  // the HUD snapshot the frame driver's passes draw from (after the menus)
            hud::party_rows::step(now);  // what the party pass adds to the snapshot (after it)
            hud::target_info::step(now);  // the Target Info's view (after the snapshot)
            hud::menus_draw::step(now);  // the co-op menus as the menu pass draws them (after the snapshot)
            hud::battle_log::step(now);  // the battle log's view (after the snapshot and the menus' names)
            hud::battle_hud::step(now);  // Identify presses (before the diamonds' beat) and the page's report
            hud::world_marks::step(now);  // seat lines and diamonds (after the snapshot)
            hud::rings::step(now);  // the AoE rings the seats' target pages want (after the snapshot and the menus)
            interaction::step(now);   // guest interactions: record, requests, the hold and its menu driver
            isolation::publish(now);  // after the heartbeat: this epoch; refills the keyboard lease
            firewall::step(now);      // after isolation (P1's device) and interaction (the menu driver)
            movement::step(now);  // reads the record the session step just published
            camera::step(now);    // the shared camera: the same record
            towns::step(now);     // town visibility: the same record
            party::step(now);     // seat changes; retires a steering remap once movement has caught up
            submit::step(now);    // battle-menu requests: results, summon owner, combat indicator, menu sounds
            // Jobs the game thread cannot reach (stalled frames, disarmed or faulted subsystems) get their terminal
            // result here instead of staying PENDING.
            const uint64_t last_tick = tick::stats()->last_tick_ms;
            if (now > last_tick + REAP_AFTER_MS) {
                gtq::reap();
            }
            diag::beat_end(beat_started);
        }
        frontend::stop();
        hud::stop();
        hud::snapshot::stop();
        hud::party_rows::stop();
        hud::target_info::stop();
        hud::menus_draw::stop();
        hud::battle_log::stop();
        hud::world_marks::stop();
        hud::rings::stop();
        hud::battle_hud::stop();
        hud::native::stop();
        submit::stop();
        firewall::stop();
        interaction::stop();
        camera::stop();
        movement::stop();
        towns::stop();
        party::stop();
        isolation::withdraw();
        session::stop();
        // Cleanup disarmed before it stopped this loop; its own reap may have found the game thread draining.
        // Nothing can run any more, so whatever is still queued gets DISARMED here.
        gtq::reap();
    }
    record(EV_THREAD_EXIT);
    return 0;
}

}  // namespace

void on_init()
{
    if (__atomic_exchange_n(&g_init_once, 1u, __ATOMIC_ACQ_REL) != 0) {
        return;  // a second FF12Hinit in this copy changes nothing
    }
    set_flags(ILC_BOOTF_INIT_CALLED);
    diag::calibrate_once();
    diag::note_init_thread(GetCurrentThreadId());
    resolve_host();
    set_status(ILC_STATE_STARTING, ILC_REASON_BOOTING, ILC_BOOT_STEP_CHECKS);

    // 1. host probe
    const uint32_t build = probe_image(g_host.image_base);
    if (build != 0) {
        set_status(ILC_STATE_INERT, ILC_REASON_UNSUPPORTED_BUILD, build);
        return;
    }
    // 2. instance guard
    uint32_t detail = 0;
    const uint32_t guard = take_guard(&detail);
    if (guard != ILC_REASON_NONE) {
        set_status(ILC_STATE_INERT, guard, detail);
        return;
    }
    set_flags(ILC_BOOTF_GUARD_HELD);
    // 3. Ivalice Together
    const uint32_t markers = detect_together(g_host.x64_dir[0] != L'\0' ? g_host.x64_dir : nullptr, g_host.pid);
    if ((markers & TOGETHER_RESIDUE) != 0) {
        set_flags(ILC_BOOTF_TOGETHER_RESIDUE);
    }
    if ((markers & TOGETHER_BLOCKING) != 0) {
        set_status(ILC_STATE_BLOCKED, ILC_REASON_TOGETHER_PRESENT, markers);
        return;
    }
    // 4. the Co-op module switch (read-only; the Lua store owns the file)
    constexpr bool default_on = default_settings().module_enabled_at_start != 0;
    const settings_switch sw =
        read_module_switch(g_host.x64_dir[0] != L'\0' ? g_host.x64_dir : nullptr, default_on);
    if (sw.defaulted) {
        set_flags(ILC_BOOTF_SETTINGS_DEFAULTED);
    }
    switch (sw.outcome) {
    case SETTINGS_ON:
        break;
    case SETTINGS_OFF:
        set_status(ILC_STATE_DISABLED, ILC_REASON_MODULE_OFF, 0);
        return;
    case SETTINGS_UNREADABLE:
        set_status(ILC_STATE_DISABLED, ILC_REASON_SETTINGS_UNREADABLE, sw.detail);
        return;
    default:
        set_status(ILC_STATE_DISABLED, ILC_REASON_SETTINGS_INVALID, sw.detail);
        return;
    }
    // 5. the init thread
    g_stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    g_settled_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (g_stop == nullptr || g_settled_event == nullptr) {
        set_status(ILC_STATE_FAULT, ILC_REASON_THREAD_FAILED, GetLastError());
        return;
    }
    set_status(ILC_STATE_STARTING, ILC_REASON_BOOTING, ILC_BOOT_STEP_INPUT);
    g_thread = CreateThread(nullptr, INIT_THREAD_STACK, init_main, nullptr, 0, nullptr);
    if (g_thread == nullptr) {
        set_status(ILC_STATE_FAULT, ILC_REASON_THREAD_FAILED, GetLastError());
        return;
    }
    set_flags(ILC_BOOTF_THREAD_STARTED);
}

void on_init_done()
{
    record(EV_INIT_DONE);
    diag::note_init_done_thread(GetCurrentThreadId());
    AcquireSRWLockExclusive(&g_status_lock);
    g_status.flags |= ILC_BOOTF_INIT_DONE;
    if (g_status.state == ILC_STATE_ARMED) {
        g_status.state = ILC_STATE_READY;
    }
    ReleaseSRWLockExclusive(&g_status_lock);
}

void on_cleanup()
{
    if (__atomic_exchange_n(&g_cleanup_once, 1u, __ATOMIC_ACQ_REL) != 0) {
        return;
    }
    record(EV_CLEANUP_BEGIN);
    if (g_thread == nullptr) {
        set_flags(ILC_BOOTF_CLEANUP_DONE);  // nothing was started: status stays as it was
        record(EV_CLEANUP_END);
        return;
    }
    // 1. disarm: every detour runs stock from here on, and the init thread can no longer arm.
    AcquireSRWLockExclusive(&g_arm_lock);
    __atomic_store_n(&g_stopping, 1u, __ATOMIC_RELEASE);
    const bool was_armed = (flags_now() & ILC_BOOTF_ARMED) != 0;
    patch::set_armed(false);
    set_flags(0, ILC_BOOTF_ARMED);
    ReleaseSRWLockExclusive(&g_arm_lock);
    record(EV_DISARMED);
    // Disarmed: nothing queued for the game thread can run any more; give every waiting job its result.
    gtq::reap();
    // 2. the init / co-op thread
    SetEvent(g_stop);
    if (WaitForSingleObject(g_thread, CLEANUP_JOIN_MS) == WAIT_OBJECT_0) {
        CloseHandle(g_thread);
        g_thread = nullptr;
        record(EV_THREAD_JOINED);
    } else {
        record(EV_JOIN_TIMEOUT);  // exit goes on; the thread sees the stop flag at its next step
    }
    // 3. the input service (after the init thread, so it cannot be started again behind our back)
    stop_input();
    // The native HUD changes go back to stock (the UI tick no longer runs our code once disarmed).
    hud::native::restore_at_exit();
    // 4. the hooks stay installed: disarmed, they run stock until the process ends.
    set_status(ILC_STATE_INERT, was_armed ? ILC_REASON_SHUT_DOWN : ILC_REASON_BOOT_ABORTED, 0);
    set_flags(ILC_BOOTF_CLEANUP_DONE);
    record(EV_CLEANUP_END);
}

status current()
{
    AcquireSRWLockShared(&g_status_lock);
    status s = g_status;
    ReleaseSRWLockShared(&g_status_lock);
    // An armed module reports DRY_ARMED while nothing beyond the CORE rows can act: the layer is still DRY, or the
    // arm gates hold no subsystem that is not faulted (none armed, or co-op switched off). Otherwise LIVE.
    if (s.reason == ILC_REASON_DRY_ARMED && patch::current_mode() == patch::MODE_LIVE &&
        (patch::subsystems_mask() & ~patch::faulted_mask()) != 0) {
        s.reason = ILC_REASON_LIVE;
    }
    return s;
}

void test_set_host(const host* h)
{
    if (h == nullptr) {
        g_test_host = nullptr;
        return;
    }
    g_test_host_copy = *h;
    g_test_host = &g_test_host_copy;
}

void test_set_step_hook(step_hook_fn hook)
{
    g_step_hook = hook;
}

uint32_t test_events(uint32_t* out, uint32_t max)
{
    uint32_t n = __atomic_load_n(&g_event_count, __ATOMIC_ACQUIRE);
    n = n < EVENT_CAP ? n : EVENT_CAP;
    n = n < max ? n : max;
    for (uint32_t i = 0; i < n; ++i) {
        out[i] = __atomic_load_n(&g_events[i], __ATOMIC_ACQUIRE);
    }
    return n;
}

uint32_t test_init_thread_id()
{
    return __atomic_load_n(&g_init_tid, __ATOMIC_ACQUIRE);
}

uint32_t test_input_start_thread_id()
{
    return __atomic_load_n(&g_input_start_tid, __ATOMIC_ACQUIRE);
}

bool test_wait_settled(uint32_t timeout_ms)
{
    if (g_settled_event == nullptr) {
        return false;
    }
    return WaitForSingleObject(g_settled_event, timeout_ms) == WAIT_OBJECT_0;
}

}  // namespace ilc::boot
