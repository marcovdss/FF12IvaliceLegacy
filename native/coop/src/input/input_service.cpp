// Ivalice Legacy co-op module: input service lifecycle and publication.
//
// One writer at a time: the live service thread, or a host test in manual mode. The writer owns the core and
// publishes a whole snapshot after each tick under a sequence lock (odd while writing). Readers copy and retry
// if the sequence moved; they never block the writer and the writer never waits for them.
//
// A tick: settings -> queued seat commands -> the configuration the core resolves with (settings plus park
// overrides and Player 1's provisional pad) -> devices (core_step) -> keyboard -> pairing/capture -> actions ->
// publish. Seat commands and their outcomes cross threads through two small queues under one SRW lock that is
// held for a copy only; the bridge pulls the outcomes, so the service thread never touches the bridge lock.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "input/edge_history.h"
#include "input/pad_mirror.h"
#include "input/input_live.h"
#include "input/input_service.h"
#include "input/sha256.h"

namespace ilc::input {
namespace {

enum mode : uint32_t { MODE_NONE = 0, MODE_LIVE = 1, MODE_MANUAL = 2 };

constexpr uint64_t TICK_100NS = 80000;  // 8 ms: 125 Hz
constexpr DWORD THREAD_STACK = 256 * 1024;
constexpr uint32_t READ_TRIES = 4096;

constinit SRWLOCK g_life = SRWLOCK_INIT;  // start/stop/mode transitions
uint32_t g_mode = MODE_NONE;
HANDLE g_thread = nullptr;
HANDLE g_stop = nullptr;

constinit SRWLOCK g_cfg_lock = SRWLOCK_INIT;
config g_cfg_staged;
uint32_t g_cfg_serial = 0;
uint32_t g_cfg_applied_serial = 0;
uint8_t g_knob_fast[SEATS] = {};
uint8_t g_knob_swap = 0;

// Seat commands in, outcomes out.
constexpr uint32_t INBOX = 16;
constexpr uint32_t OUTBOX = 32;
constinit SRWLOCK g_ctl_lock = SRWLOCK_INIT;
uint32_t g_accepting = 0;
command_msg g_inbox[INBOX];
uint32_t g_inbox_count = 0;
control_event g_outbox[OUTBOX];
uint32_t g_out_head = 0;
uint32_t g_out_count = 0;
uint32_t g_out_dropped = 0;

// Writer-owned.
core g_core;
snapshot g_work;
config g_settings;       // the applied settings as configured
config g_effective;      // what the core resolves devices with
uint32_t g_effective_set = 0;
control_state g_ctl;
action_layer g_actions;
uint8_t g_fast[SEATS] = {};
uint8_t g_swap = 0;
xinput_api* g_manual_xinput = nullptr;
sdl_api* g_manual_sdl = nullptr;
keyboard_api* g_manual_keyboard = nullptr;
uint64_t g_manual_now = 0;

// Published.
uint32_t g_pub_seq = 0;
snapshot g_pub;

uint64_t qpc_now_us()
{
    static LARGE_INTEGER frequency = {};
    if (frequency.QuadPart == 0) {
        QueryPerformanceFrequency(&frequency);
    }
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    const uint64_t ticks = static_cast<uint64_t>(now.QuadPart);
    const uint64_t f = static_cast<uint64_t>(frequency.QuadPart);
    return (ticks / f) * 1000000u + ((ticks % f) * 1000000u) / f;
}

uint64_t clock_now_us()
{
    return __atomic_load_n(&g_mode, __ATOMIC_ACQUIRE) == MODE_MANUAL ? __atomic_load_n(&g_manual_now, __ATOMIC_RELAXED)
                                                                     : qpc_now_us();
}

uint32_t make_session()
{
    struct {
        uint64_t qpc;
        uint64_t tick;
        uint32_t pid;
        uint32_t tid;
        const void* stack;
    } seed = {qpc_now_us(), GetTickCount64(), GetCurrentProcessId(), GetCurrentThreadId(), &seed};
    uint8_t digest[32];
    sha256(&seed, sizeof(seed), digest);
    uint32_t session;
    memcpy(&session, digest, sizeof(session));
    return session != 0 ? session : 1;
}

void publish(const snapshot& s)
{
    const uint32_t q = __atomic_load_n(&g_pub_seq, __ATOMIC_RELAXED);
    __atomic_store_n(&g_pub_seq, q + 1, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    memcpy(&g_pub, &s, sizeof(g_pub));
    __atomic_store_n(&g_pub_seq, q + 2, __ATOMIC_RELEASE);
}

class outbox_sink final : public event_sink {
public:
    void emit(const control_event& e) override
    {
        AcquireSRWLockExclusive(&g_ctl_lock);
        if (g_out_count == OUTBOX) {
            g_out_head = (g_out_head + 1) % OUTBOX;  // drop the oldest, count it
            --g_out_count;
            ++g_out_dropped;
        }
        g_outbox[(g_out_head + g_out_count) % OUTBOX] = e;
        ++g_out_count;
        ReleaseSRWLockExclusive(&g_ctl_lock);
    }
};

constinit outbox_sink g_sink;

void take_config()
{
    static config next;  // writer-owned scratch (too large for the stack frame of every tick)
    bool changed = false;
    AcquireSRWLockExclusive(&g_cfg_lock);
    if (g_cfg_serial != g_cfg_applied_serial) {
        next = g_cfg_staged;
        changed = true;
        g_cfg_applied_serial = g_cfg_serial;
    }
    memcpy(g_fast, g_knob_fast, sizeof(g_fast));
    g_swap = g_knob_swap;
    ReleaseSRWLockExclusive(&g_cfg_lock);
    if (changed) {
        control_on_settings(g_ctl, g_core, g_settings, next, g_sink);
        g_settings = next;
    }
}

void take_commands(uint64_t now)
{
    command_msg batch[INBOX];
    AcquireSRWLockExclusive(&g_ctl_lock);
    const uint32_t n = g_inbox_count;
    memcpy(batch, g_inbox, n * sizeof(command_msg));
    g_inbox_count = 0;
    ReleaseSRWLockExclusive(&g_ctl_lock);
    for (uint32_t i = 0; i < n; ++i) {
        control_command(g_ctl, g_core, batch[i], g_work.session, g_settings.kbm_seat, now, g_sink);
    }
}

void set_accepting(bool on)
{
    AcquireSRWLockExclusive(&g_ctl_lock);
    __atomic_store_n(&g_accepting, on ? 1u : 0u, __ATOMIC_RELEASE);
    ReleaseSRWLockExclusive(&g_ctl_lock);
}

// The service stops: no more commands; queued starts and running sessions end as CANCELLED.
void stop_commands()
{
    command_msg batch[INBOX];
    AcquireSRWLockExclusive(&g_ctl_lock);
    __atomic_store_n(&g_accepting, 0u, __ATOMIC_RELEASE);
    const uint32_t n = g_inbox_count;
    memcpy(batch, g_inbox, n * sizeof(command_msg));
    g_inbox_count = 0;
    ReleaseSRWLockExclusive(&g_ctl_lock);
    control_shutdown(g_ctl, g_sink);
    for (uint32_t i = 0; i < n; ++i) {
        control_event e;
        memset(&e, 0, sizeof(e));
        e.seat = batch[i].seat;
        e.generation = batch[i].request_id;
        if (batch[i].command == ILC_CMD_PAIRING_START) {
            e.kind = ILC_EV_PAIRING_RESULT;
            e.a = ILC_PAIR_CANCELLED | (0xFFu << 16);
            e.b = batch[i].arg1;
        } else if (batch[i].command == ILC_CMD_CAPTURE_START) {
            e.kind = ILC_EV_CAPTURE_RESULT;
            e.a = ILC_CAPT_CANCELLED | ((batch[i].arg0 & 0xFFu) << 8) | ((batch[i].arg1 & 0xFFu) << 16);
        } else {
            continue;  // park, seat move and cancels need no answer once the service is gone
        }
        g_sink.emit(e);
    }
}

void read_keys(keyboard_api* keyboard, uint8_t kbm_seat, bool* focused, key_state* keys)
{
    memset(keys, 0, sizeof(*keys));
    *focused = keyboard != nullptr && keyboard->focused();
    if (!*focused || kbm_seat < 1 || kbm_seat > SEATS) {
        return;  // keys are read only in the foreground, and only for the seat that owns them
    }
    key_state wanted;
    actions_wanted_keys(g_settings, &wanted);
    const bool all = control_wants_all_keys(g_ctl, kbm_seat);
    for (uint32_t vk = 1; vk < 255; ++vk) {
        if ((all || key_is_down(wanted, vk)) && keyboard->down(static_cast<uint8_t>(vk))) {
            key_set(*keys, vk);
        }
    }
}

void begin_run(uint32_t backends, uint32_t issues, uint32_t session)
{
    core_reset(g_core);
    g_cfg_applied_serial = 0;  // re-apply whatever is staged
    memset(&g_settings, 0, sizeof(g_settings));
    memset(&g_effective, 0, sizeof(g_effective));
    g_effective_set = 0;
    control_reset(g_ctl);
    actions_reset(g_actions);
    AcquireSRWLockExclusive(&g_ctl_lock);
    g_inbox_count = 0;  // a command from an earlier run never reaches this one
    ReleaseSRWLockExclusive(&g_ctl_lock);
    memset(&g_work, 0, sizeof(g_work));
    g_work.state = RUN_RUNNING;
    g_work.backends = backends;
    g_work.issues = issues;
    g_work.session = session;
    edges::reset(session);  // consumers' cursors from an earlier run re-seed
    pads::reset();
    for (uint32_t s = 0; s < SEATS; ++s) {
        g_work.frames[s] = g_core.frames[s];
        g_work.actions[s].seat = static_cast<uint8_t>(s + 1);
    }
    set_accepting(true);
}

void tick(uint64_t now, xinput_api* xinput, sdl_api* sdl, keyboard_api* keyboard)
{
    take_config();
    take_commands(now);
    config effective;
    control_effective(g_ctl, g_settings, &effective);
    if (!g_effective_set || memcmp(&effective, &g_effective, sizeof(effective)) != 0) {
        core_set_config(g_core, effective);
        g_effective = effective;
        g_effective_set = 1;
    }
    core_step(g_core, now, xinput, sdl);

    bool focused = false;
    key_state keys;
    read_keys(keyboard, g_settings.kbm_seat, &focused, &keys);
    control_step(g_ctl, g_core, keys, focused, g_settings.kbm_seat, now, g_sink);

    action_inputs in;
    memset(&in, 0, sizeof(in));
    in.cfg = &g_settings;
    in.frames = g_core.frames;
    in.keys = keys;
    in.focused = focused ? 1 : 0;
    in.decide_swap = g_swap;
    memcpy(in.fast, g_fast, sizeof(in.fast));
    control_suppressed(g_ctl, in.suppress);
    memcpy(in.reseed, g_ctl.reseed_now, sizeof(in.reseed));
    memset(g_ctl.reseed_now, 0, sizeof(g_ctl.reseed_now));
    actions_step(g_actions, in, now, g_core.seq, g_work.actions);
    edges::record(g_core.seq, g_core.frames, g_work.actions);  // each consumer's cursor sees this tick's edges
    pads::record(g_core.frames, g_work.actions);  // the small per-seat copy detours read

    g_work.seq = g_core.seq;
    g_work.time_us = now;
    memcpy(g_work.frames, g_core.frames, sizeof(g_work.frames));
    g_work.inventory_count = core_inventory(g_core, g_work.inventory, ILC_DEVICES_MAX);
    g_work.counters = g_core.counters;
    control_view& v = g_work.control;
    v.pairing_stage = g_ctl.pairing.stage;
    v.pairing_seat = g_ctl.pairing.stage != PAIR_IDLE ? g_ctl.pairing.seat : 0;
    v.capture_seat = g_ctl.capture.active ? g_ctl.capture.seat : 0;
    v.override_active = g_ctl.override_active;
    v.parked = 0;
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (g_effective.seats[s].parked) {
            v.parked |= static_cast<uint8_t>(1u << s);
        }
    }
    v.focused = focused ? 1 : 0;
    memcpy(v.move_request, g_ctl.move_request, sizeof(v.move_request));
    memcpy(v.move_character, g_ctl.move_character, sizeof(v.move_character));
    AcquireSRWLockExclusive(&g_ctl_lock);
    v.events_dropped = g_out_dropped;
    ReleaseSRWLockExclusive(&g_ctl_lock);
    publish(g_work);
}

void publish_state(uint32_t state, uint32_t issues)
{
    g_work.state = state;
    g_work.issues |= issues;
    g_work.inventory_count = 0;
    g_work.time_us = qpc_now_us();
    publish(g_work);
}

// SDL's device-change detection (joystick and HIDAPI hot-plug) uses message-only windows owned by this thread
// when video is off; their messages only arrive if the thread pumps them. Never blocks; bounded per tick.
constexpr uint32_t MESSAGES_PER_TICK = 64;

void drain_thread_messages()
{
    MSG msg;
    for (uint32_t n = 0; n < MESSAGES_PER_TICK && PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE); ++n) {
        DispatchMessageW(&msg);
    }
}

DWORD WINAPI service_thread(void*)
{
    uint32_t issues = 0;
    live_backends backends = live_open(&issues);
    if (backends.xinput == nullptr && backends.sdl == nullptr) {
        live_close();
        memset(&g_work, 0, sizeof(g_work));
        publish_state(RUN_FAILED, issues);
        return 0;
    }
    HANDLE timer = CreateWaitableTimerExW(nullptr, nullptr, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (timer == nullptr) {
        issues |= ISSUE_TIMER;
        timer = CreateWaitableTimerExW(nullptr, nullptr, 0, TIMER_ALL_ACCESS);
    }
    const uint32_t bits = (backends.xinput ? BACKEND_XINPUT : 0u) | (backends.sdl ? BACKEND_SDL : 0u);
    begin_run(bits, issues, make_session());
    keyboard_api* keyboard = live_keyboard();
    for (;;) {
        tick(qpc_now_us(), backends.xinput, backends.sdl, keyboard);
        drain_thread_messages();
        DWORD wait;
        if (timer != nullptr) {
            LARGE_INTEGER due;
            due.QuadPart = -static_cast<LONGLONG>(TICK_100NS);
            SetWaitableTimer(timer, &due, 0, nullptr, nullptr, FALSE);
            HANDLE handles[2] = {g_stop, timer};
            wait = WaitForMultipleObjects(2, handles, FALSE, INFINITE);
        } else {
            wait = WaitForSingleObject(g_stop, 8);
        }
        if (wait == WAIT_OBJECT_0 || wait == WAIT_FAILED) {
            break;
        }
    }
    stop_commands();
    core_close_all(g_core, backends.sdl);
    live_close();
    if (timer != nullptr) {
        CloseHandle(timer);
    }
    publish_state(RUN_STOPPED, 0);
    return 0;
}

class local_frame_source final : public seat_frame_source {
public:
    bool latest(uint32_t seat, ilc_seat_input_frame* out) override
    {
        if (seat < 1 || seat > SEATS) {
            return false;
        }
        static_assert(sizeof(snapshot) < 8192, "snapshot copy stays small");
        snapshot s;
        if (!snapshot_read(&s) || s.state != RUN_RUNNING) {
            return false;
        }
        *out = s.frames[seat - 1];
        return true;
    }
};

constinit local_frame_source g_local_frames;

class local_action_source final : public seat_action_source {
public:
    bool latest(uint32_t seat, ilc_seat_action_frame* out) override
    {
        if (seat < 1 || seat > SEATS) {
            return false;
        }
        snapshot s;
        if (!snapshot_read(&s) || s.state != RUN_RUNNING) {
            return false;
        }
        *out = s.actions[seat - 1];
        return true;
    }
};

constinit local_action_source g_local_actions;

}  // namespace

bool snapshot_read(snapshot* out)
{
    for (uint32_t i = 0; i < READ_TRIES; ++i) {
        const uint32_t before = __atomic_load_n(&g_pub_seq, __ATOMIC_ACQUIRE);
        if (before & 1u) {
            YieldProcessor();
            continue;
        }
        memcpy(out, &g_pub, sizeof(*out));
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if (__atomic_load_n(&g_pub_seq, __ATOMIC_RELAXED) == before) {
            if (before == 0) {
                memset(out, 0, sizeof(*out));
                return false;
            }
            return true;
        }
    }
    memset(out, 0, sizeof(*out));
    return false;
}

void configure(const config& cfg)
{
    AcquireSRWLockExclusive(&g_cfg_lock);
    g_cfg_staged = cfg;
    ++g_cfg_serial;
    if (g_cfg_serial == 0) {
        g_cfg_serial = 1;
    }
    ReleaseSRWLockExclusive(&g_cfg_lock);
}

void configure_from_settings(const ilc_settings& settings)
{
    config cfg;
    config_from_settings(settings, &cfg);
    configure(cfg);
}

bool try_configure_from_settings(const ilc_settings& settings)
{
    static config cfg;  // the game thread is the only caller: no 8 KiB on its stack
    config_from_settings(settings, &cfg);
    if (!TryAcquireSRWLockExclusive(&g_cfg_lock)) {
        return false;
    }
    g_cfg_staged = cfg;
    ++g_cfg_serial;
    if (g_cfg_serial == 0) {
        g_cfg_serial = 1;
    }
    ReleaseSRWLockExclusive(&g_cfg_lock);
    return true;
}

uint32_t devices_view(ilc_devices_reply* reply)
{
    snapshot s;
    reply->service_state = ILC_SERVICE_NOT_READY;
    reply->inventory_session = 0;
    reply->age_ms = 0;
    reply->count = 0;
    if (!snapshot_read(&s) || s.state != RUN_RUNNING) {
        return 0;
    }
    const uint64_t now = clock_now_us();
    const uint64_t age = now > s.time_us ? (now - s.time_us) / 1000u : 0;
    reply->service_state = ILC_SERVICE_READY;
    reply->inventory_session = s.session;
    reply->age_ms = age > 0xFFFFFFFFu ? 0xFFFFFFFFu : static_cast<uint32_t>(age);
    const uint32_t count = s.inventory_count <= ILC_DEVICES_MAX ? s.inventory_count : ILC_DEVICES_MAX;
    memcpy(reply->entries, s.inventory, count * sizeof(ilc_device_entry));
    reply->count = count;
    return count;
}

bool start_live()
{
    AcquireSRWLockExclusive(&g_life);
    bool ok = false;
    if (g_mode == MODE_NONE) {
        g_stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (g_stop != nullptr) {
            __atomic_store_n(&g_mode, MODE_LIVE, __ATOMIC_RELEASE);
            g_thread = CreateThread(nullptr, THREAD_STACK, service_thread, nullptr, 0, nullptr);
            if (g_thread != nullptr) {
                ok = true;
            } else {
                CloseHandle(g_stop);
                g_stop = nullptr;
                __atomic_store_n(&g_mode, MODE_NONE, __ATOMIC_RELEASE);
                memset(&g_work, 0, sizeof(g_work));
                publish_state(RUN_FAILED, ISSUE_THREAD);
            }
        }
    }
    ReleaseSRWLockExclusive(&g_life);
    return ok;
}

bool stop_live(uint32_t timeout_ms)
{
    AcquireSRWLockExclusive(&g_life);
    bool ok = false;
    if (g_mode == MODE_LIVE && g_thread != nullptr) {
        SetEvent(g_stop);
        if (WaitForSingleObject(g_thread, timeout_ms) == WAIT_OBJECT_0) {
            CloseHandle(g_thread);
            CloseHandle(g_stop);
            g_thread = nullptr;
            g_stop = nullptr;
            __atomic_store_n(&g_mode, MODE_NONE, __ATOMIC_RELEASE);
            ok = true;
        }
    }
    ReleaseSRWLockExclusive(&g_life);
    return ok;
}

void manual_begin(xinput_api* xinput, sdl_api* sdl, uint32_t session)
{
    AcquireSRWLockExclusive(&g_life);
    if (g_mode == MODE_NONE) {
        g_manual_xinput = xinput;
        g_manual_sdl = sdl;
        g_manual_now = 0;
        __atomic_store_n(&g_mode, MODE_MANUAL, __ATOMIC_RELEASE);
        const uint32_t bits = BACKEND_MANUAL | (xinput ? BACKEND_XINPUT : 0u) | (sdl ? BACKEND_SDL : 0u);
        begin_run(bits, 0, session != 0 ? session : 1);
    }
    ReleaseSRWLockExclusive(&g_life);
}

void manual_step(uint64_t now_us)
{
    if (__atomic_load_n(&g_mode, __ATOMIC_ACQUIRE) != MODE_MANUAL) {
        return;
    }
    __atomic_store_n(&g_manual_now, now_us, __ATOMIC_RELAXED);
    tick(now_us, g_manual_xinput, g_manual_sdl, g_manual_keyboard);
}

void manual_end()
{
    AcquireSRWLockExclusive(&g_life);
    if (g_mode == MODE_MANUAL) {
        stop_commands();
        core_close_all(g_core, g_manual_sdl);
        g_work.time_us = g_manual_now;
        g_work.state = RUN_STOPPED;
        g_work.inventory_count = 0;
        publish(g_work);
        g_manual_xinput = nullptr;
        g_manual_sdl = nullptr;
        __atomic_store_n(&g_mode, MODE_NONE, __ATOMIC_RELEASE);
    }
    ReleaseSRWLockExclusive(&g_life);
}

seat_frame_source& local_frames()
{
    return g_local_frames;
}

seat_action_source& local_actions()
{
    return g_local_actions;
}

int32_t post_command(const command_msg& m)
{
    int32_t rc = 0;
    AcquireSRWLockExclusive(&g_ctl_lock);
    if (!__atomic_load_n(&g_accepting, __ATOMIC_ACQUIRE)) {
        rc = ILC_E_NOT_READY;
    } else if (g_inbox_count == INBOX) {
        rc = ILC_E_BUSY;
    } else {
        g_inbox[g_inbox_count++] = m;
    }
    ReleaseSRWLockExclusive(&g_ctl_lock);
    return rc;
}

uint32_t drain_events(control_event* out, uint32_t max)
{
    AcquireSRWLockExclusive(&g_ctl_lock);
    const uint32_t n = g_out_count < max ? g_out_count : max;
    for (uint32_t i = 0; i < n; ++i) {
        out[i] = g_outbox[(g_out_head + i) % OUTBOX];
    }
    g_out_head = (g_out_head + n) % OUTBOX;
    g_out_count -= n;
    ReleaseSRWLockExclusive(&g_ctl_lock);
    return n;
}

void set_fast(uint32_t seat, bool on)
{
    if (seat < 1 || seat > SEATS) {
        return;
    }
    AcquireSRWLockExclusive(&g_cfg_lock);
    g_knob_fast[seat - 1] = on ? 1 : 0;
    ReleaseSRWLockExclusive(&g_cfg_lock);
}

void set_decide_swap(bool circle_confirms)
{
    AcquireSRWLockExclusive(&g_cfg_lock);
    g_knob_swap = circle_confirms ? 1 : 0;
    ReleaseSRWLockExclusive(&g_cfg_lock);
}

void manual_keyboard(keyboard_api* keyboard)
{
    g_manual_keyboard = keyboard;
}

}  // namespace ilc::input
