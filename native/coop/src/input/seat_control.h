// Ivalice Legacy co-op module: seat commands on the input thread (pairing proof, capture, park, seat move, and
// Player 1's guarded pad change).
//
// Pure logic over the input core's state: no thread, no clock, no Windows call, and no device reads of its own
// (it looks at what the core already read this tick). Commands arrive from the bridge through the service's
// queue; outcomes leave as events (bridge_abi.h: PAIRING_RESULT, CAPTURE_RESULT, COMMAND_RESULT). Nothing here
// edits settings: a proved pad or a captured binding is reported, and the Mod Menu commits it.
//
// Pairing proof (one session at a time; a newer start cancels the older one):
//   * The target is a pad of the current inventory, named by its entry id. A start from another inventory
//     session, an unknown entry id, a pad that is Player 1's (for a guest) or another guest's ends at once
//     (STALE_SESSION, NOT_FOUND, P1_LOCKED, OWNED_BY_SEAT with that seat: the Mod Menu offers a swap).
//   * Armed only once no pad has A down, so a held A never proves anything. Then the first A press on the
//     target opens a 0.200 s window that collects every pad with A down; after it: the target alone proves it;
//     the target plus exactly one XInput slot that is only SDL's own mirror of it proves it and teaches the
//     alias (learned slot); anything else is TWO_PADS. The learned slot is checked for ownership too (a guest
//     can never take Player 1's pad through its mirror). No proof within 20 s: TIMEOUT, or HELD_A if an A was
//     held the whole time. The target going away: DISCONNECTED.
//   * Player 1 proving its own pad (or its mirror): IDENTIFIED (name/alias only). Player 1 proving another free
//     pad starts the guarded change: PROVISIONAL (the new pad drives Player 1 now, the old one stops), then a
//     fresh A press on the new pad within 10 s: CONFIRMED; the settings that name the new pad end the change.
//     No confirm within 10 s, no settings within 5 s of the confirm, the pad going away or a cancel: the change
//     is undone (REVERTED, DISCONNECTED or CANCELLED) and Player 1 is back on its configured pad.
//   * The seat being paired reads neutral actions until the session ends (nothing leaks to a menu).
// Capture: the next fresh press on the seat's pad (or a key or mouse button, if the seat owns them and the game
//   is in the foreground) after the start; what was held at the start must be released first (below 0.55), and a
//   pad press must reach 0.72. 20 s timeout.
//   The seat reads neutral actions meanwhile; the captured control stays absorbed until released.
// Park: live now for the seat (or every guest); a later settings record that changes the seat's parked field
//   takes over again. Seat move: the seat's held input is absorbed and the move waits for the session core.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "ilc/seat_input.h"
#include "input/actions.h"
#include "input/input_core.h"

namespace ilc::input {

inline constexpr uint64_t PAIRING_TIMEOUT_US = 20000000;  // 20 s on the service side
inline constexpr uint64_t PROOF_WINDOW_US = 200000;       // 0.200 s after the first A on the target
inline constexpr uint64_t CONFIRM_WINDOW_US = 10000000;   // Player 1 change: confirm on the new pad
inline constexpr uint64_t COMMIT_WINDOW_US = 5000000;     // Player 1 change: settings after the confirm
inline constexpr uint64_t CAPTURE_TIMEOUT_US = 20000000;  // 20 s
inline constexpr uint16_t CAPTURE_REST_Q16 = 36044;       // 0.55: held at the start (must be released below this)
inline constexpr uint16_t CAPTURE_FIRM_Q16 = 47185;       // 0.72: a press firm enough to capture
inline constexpr uint32_t MOVE_QUEUED = 0x100;            // COMMAND_RESULT b flag: the move waits for the session

struct command_msg {
    uint16_t command;  // ilc_command_id
    uint8_t seat;
    uint8_t reserved;
    uint32_t request_id;
    uint32_t arg0;
    uint32_t arg1;
};

struct control_event {
    uint16_t kind;  // ilc_event_kind
    uint8_t seat;
    uint8_t reserved;
    uint32_t generation;  // request id
    int32_t code;
    uint32_t a;
    uint32_t b;
};

enum pairing_stage : uint8_t {
    PAIR_IDLE = 0,
    PAIR_WAIT_RELEASE = 1,   // some pad has A down
    PAIR_ARMED = 2,          // waiting for A on the target
    PAIR_PROOF = 3,          // the 0.200 s window
    PAIR_CONFIRM_RELEASE = 4,  // Player 1 change: waiting for A up on the new pad
    PAIR_CONFIRM_PRESS = 5,    // Player 1 change: waiting for a fresh A on the new pad
    PAIR_COMMIT_WAIT = 6,      // Player 1 change confirmed: waiting for the settings
};

struct pairing_session {
    uint8_t stage;  // pairing_stage
    uint8_t seat;   // 1..4
    uint8_t ever_armed;
    uint8_t learned_slot;  // 0xFF none
    uint32_t request_id;
    uint32_t proof_entry;
    uint32_t answered;  // bit per core node with A down during the proof window
    uint64_t started_us;
    uint64_t stage_us;
};

struct capture_session {
    uint8_t active;
    uint8_t seat;
    uint8_t action;
    uint8_t slot;
    uint8_t baseline_pending;
    uint8_t reserved[3];
    uint32_t request_id;
    uint32_t pad_baseline;  // controls held at the start, cleared as they are released
    uint64_t started_us;
    key_state key_baseline;
};

struct control_state {
    pairing_session pairing;
    capture_session capture;
    uint8_t override_active;  // Player 1 provisional pad
    uint8_t reserved[3];
    seat_config override_device;
    int8_t live_park[SEATS];          // -1 follow settings, else 0/1 from a PARK command
    uint8_t reseed_now[SEATS];        // one-shot discontinuities for the action layer
    uint32_t move_request[SEATS];     // request id of the last seat move (0 none)
    uint8_t move_character[SEATS];
    uint32_t events_dropped;          // outbox overflow (counted by the service)
};

class event_sink {
public:
    virtual void emit(const control_event& e) = 0;

protected:
    ~event_sink() = default;
};

void control_reset(control_state& st);

// The applied settings changed (old -> new). Park overrides and Player 1's change react to it.
void control_on_settings(control_state& st, const core& c, const config& old_cfg, const config& new_cfg,
                         event_sink& out);

// One queued command. session = the current inventory session.
void control_command(control_state& st, const core& c, const command_msg& m, uint32_t session, uint8_t kbm_seat,
                     uint64_t now, event_sink& out);

// One tick after core_step. keys = this tick's keys (all keys while a capture runs for the kbm seat).
void control_step(control_state& st, const core& c, const key_state& keys, bool focused, uint8_t kbm_seat,
                  uint64_t now, event_sink& out);

// The service stops: every session ends as CANCELLED and Player 1's provisional pad is dropped.
void control_shutdown(control_state& st, event_sink& out);

// The configuration the core resolves devices with: settings plus park overrides and Player 1's provisional pad.
void control_effective(const control_state& st, const config& settings, config* out);

// Seats whose actions read neutral this tick (pairing or capture), and whether the kbm seat needs every key.
void control_suppressed(const control_state& st, uint8_t out[SEATS]);
bool control_wants_all_keys(const control_state& st, uint8_t kbm_seat);

}  // namespace ilc::input
