// Ivalice Legacy co-op module: the pure rules of town visibility (no memory access, no clock, no threads).
//
// Town rule: in a town a member has a body (visible, walking, interactable) only while a player controls
// it, story allies included. "Controlled" = P1's character, the live native leader (the character P1's pad
// drives), and the character of every guest seat that is in session and under manual control. With co-op on and
// no guest seated, only P1's leader remains. Outside towns nothing changes.
//
// The filter only ever removes: it blanks the current and HUD slots of uncontrolled members and the game's own bind
// loop unbinds those slot actors, exactly as a stock town hides three of four. It never adds a member, never
// touches the default, menu or fifth layout, and writes nothing when the leader is not in the current layout (the
// stock town and Companions' Leader Only collapse, or anything unexpected): then the game decides alone.
#pragma once

#include <stdint.h>

namespace ilc::session {
struct session_record;
}

namespace ilc::towns {

constexpr uint32_t SLOTS = 4;

// Party ids of controlled characters, as bits (ids >= 64 never count; the leader is added live).
uint64_t controlled_members(const session::session_record& r);

bool keeps(uint64_t allowed, uint8_t leader, uint16_t member);

// The engine's party "town mode" (the layout builder's field mode 3): the area's town bit is set and neither
// scripted single-body mode is active. Arguments are the raw reads; table_ok = the area table pointer is set.
bool town_mode(bool table_ok, uint32_t location_raw, uint32_t area_count, uint8_t town_byte, uint32_t mode_bits);

enum filter_result : int32_t {
    FILTER_NO_LEADER = -1,  // leader none or not in current: nothing written
    // >= 0: bit k set = slot k was hidden (0 = nothing to hide)
};
// In place on the four active current/HUD slots.
int32_t filter_slots(uint64_t allowed, uint8_t leader, uint16_t current[SLOTS], uint16_t ui[SLOTS]);

// What a rebuild would change, from the co-op thread's view of the layouts.
enum need_bit : uint32_t {
    NEED_SHOW = 0x1,  // a controlled member of the roster has no body
    NEED_HIDE = 0x2,  // an uncontrolled member has a body
};
uint32_t need_of(uint64_t allowed, uint8_t leader, const uint16_t current[SLOTS], const uint16_t roster[SLOTS]);

// ---- rebuild planner (co-op thread) ----------------------------------------------------------------------------
// A rebuild (game-thread job calling the stock re-materialize) is asked for when, in town, the layout needs a show
// or a hide and the controlled set ("key": controlled members plus the leader) has held still long enough. Shows
// settle briefly (a character P1 or a guest now drives needs its body); hides wait out the session's identity
// settle, so a P1/guest swap (which keeps the same two bodies) never rebuilds. One applied rebuild per key and
// world: if the layout still needs one after that (Companions not on Full Party), the planner stops until the key
// or the world changes. Refusals back off and are capped too.
constexpr uint64_t SHOW_SETTLE_MS = 40;
constexpr uint64_t HIDE_SETTLE_MS = 300;
constexpr uint64_t MIN_INTERVAL_MS = 250;
constexpr uint32_t MAX_APPLIED = 1;
constexpr uint32_t MAX_REFUSED = 16;

struct planner_state {
    uint64_t key;
    uint64_t key_since_ms;
    uint64_t last_push_ms;
    uint32_t world_epoch;
    uint32_t applied;
    uint32_t refused;
    uint32_t ticket;
    uint8_t has_key;
    uint8_t in_flight;
    uint8_t pushed_once;
    uint8_t reserved;
    uint32_t reserved2;
};
static_assert(sizeof(planner_state) == 48, "planner_state layout");

struct planner_inputs {
    uint64_t key;
    uint64_t now_ms;
    uint32_t world_epoch;
    uint32_t need;       // need_bit
    uint8_t in_town;
    uint8_t world_ok;    // session: valid world, field phase (no menu/modal/cutscene/load), player control on
    uint8_t task_busy;   // a re-materialize task is in flight
    uint8_t degraded;    // the town arm is stock: rebuilds cannot give guests a body
    uint32_t reserved;
};
static_assert(sizeof(planner_inputs) == 32, "planner_inputs layout");

enum plan_action : int32_t {
    PLAN_PUSH = 1,
    PLAN_NOTHING = 0,
    PLAN_NOT_TOWN = -1,
    PLAN_IN_FLIGHT = -2,
    PLAN_WAIT_SETTLE = -3,
    PLAN_WAIT_INTERVAL = -4,
    PLAN_WAIT_WORLD = -5,
    PLAN_WAIT_TASK = -6,
    PLAN_GAVE_UP = -7,
    PLAN_DEGRADED = -8,
};

void planner_reset(planner_state& s);
int32_t plan(planner_state& s, const planner_inputs& in);
void planner_pushed(planner_state& s, bool accepted, uint32_t ticket, uint64_t now_ms);
// A terminal result of the job: applied (>= 1) or refused (anything else).
void planner_result(planner_state& s, int32_t result);

}  // namespace ilc::towns
