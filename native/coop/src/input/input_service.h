// Ivalice Legacy co-op module: the input service (lifecycle, publication, and the views other code reads).
//
// Inert until started. Nothing here runs from DllMain: the live service is started by the module's own start-up
// thread (the one FF12Hinit creates) or by a host test, and only then loads XInput and initializes SDL, on the
// service thread itself. Readers on any thread get whole snapshots through a sequence lock: the service thread
// never waits for a reader.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "ilc/seat_input.h"
#include "input/actions.h"
#include "input/input_api.h"
#include "input/input_core.h"
#include "input/keyboard.h"
#include "input/seat_control.h"

namespace ilc::input {

enum run_state : uint32_t {
    RUN_STOPPED = 0,
    RUN_STARTING = 1,
    RUN_RUNNING = 2,
    RUN_FAILED = 3,  // neither XInput nor SDL could be started
};

enum backend_bit : uint32_t {
    BACKEND_XINPUT = 0x1,
    BACKEND_SDL = 0x2,
    BACKEND_MANUAL = 0x80,  // driven tick by tick by a host test
};

// Why a live backend did not start (bits, reported in the snapshot).
enum start_issue : uint32_t {
    ISSUE_XINPUT_LOAD = 0x01,    // System32 xinput1_4.dll did not load or has no XInputGetState
    ISSUE_SDL_INIT = 0x02,       // SDL_Init(GAMECONTROLLER) failed
    ISSUE_TIMER = 0x04,          // no high-resolution waitable timer; a normal one is used
    ISSUE_THREAD = 0x08,         // the service thread could not be created
};

// Seat commands as the service last saw them (for status views and tests).
struct control_view {
    uint8_t pairing_stage;  // pairing_stage
    uint8_t pairing_seat;   // 0 none
    uint8_t capture_seat;   // 0 none
    uint8_t override_active;
    uint8_t parked;         // bit s = seat s+1 parked in the configuration the core resolves with
    uint8_t focused;
    uint8_t reserved[2];
    uint32_t move_request[SEATS];
    uint8_t move_character[SEATS];
    uint32_t events_dropped;
};

struct snapshot {
    uint32_t state;      // run_state
    uint32_t backends;   // backend_bit
    uint32_t issues;     // start_issue
    uint32_t session;    // non-zero per service run
    uint32_t seq;        // core tick
    uint32_t inventory_count;
    uint64_t time_us;    // service clock at publication
    ilc_seat_input_frame frames[SEATS];
    ilc_device_entry inventory[ILC_DEVICES_MAX];
    core_counters counters;
    ilc_seat_action_frame actions[SEATS];
    control_view control;
};

// Latest snapshot; false while the service has never published (its state then reads STOPPED).
bool snapshot_read(snapshot* out);

// Seat configuration: thread-safe, applied at the start of the next tick.
void configure(const config& cfg);
void configure_from_settings(const ilc_settings& settings);
// Game-thread variant: only tries the configuration lock; false = busy, nothing staged, try again next frame.
bool try_configure_from_settings(const ilc_settings& settings);

// GET_DEVICES body: fills service_state, inventory_session, age_ms, count and the entries (not the header).
// Returns the entry count. NOT_READY with no entries while the service is not running.
uint32_t devices_view(ilc_devices_reply* reply);

// Live service (thread). start returns false if a service already runs or the thread cannot be created.
// stop signals the thread and waits up to timeout_ms; the thread closes SDL and XInput itself.
bool start_live();
bool stop_live(uint32_t timeout_ms);

// Manual service for host tests: no thread, one tick per step() on the caller's thread.
void manual_begin(xinput_api* xinput, sdl_api* sdl, uint32_t session);
void manual_step(uint64_t now_us);
void manual_end();

// The seat_frame_source the session core will read (phase 1: local devices).
seat_frame_source& local_frames();
seat_action_source& local_actions();

// Seat commands from the bridge (any thread): 0 = queued for the next tick, ILC_E_NOT_READY while the service
// is stopped, ILC_E_BUSY when the queue is full. Outcomes come back through drain_events (oldest first; when
// the outbox is full the oldest is dropped and counted).
int32_t post_command(const command_msg& m);
uint32_t drain_events(control_event* out, uint32_t max);

// Knobs for later subsystems (thread-safe, read at the next tick): fast menu repeat per seat (1..4), and
// whether the game confirms with Circle (pad A/B swap for CONFIRM/CANCEL).
void set_fast(uint32_t seat, bool on);
void set_decide_swap(bool circle_confirms);

// Host tests: the keyboard the manual service reads (null = none, the default).
void manual_keyboard(keyboard_api* keyboard);

}  // namespace ilc::input
