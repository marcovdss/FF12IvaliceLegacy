// Ivalice Legacy co-op module: the session core.
//
// It runs on the co-op thread (the boot's heartbeat loop), never on a game thread. Each step:
//   1. reads the world (world.h) through the fault-safe reader, and the active party's actors (a full actor scan
//      at most every SCAN_INTERVAL_MS, or at once when the party layout changes or a cached actor fails its
//      check; between scans each cached actor is re-validated);
//   2. resolves which character each seat controls (adopting a party or leader change made by the game or by
//      another mod), evaluates the world and the gates (policy.h);
//   3. moves the epochs: the world epoch counts coherent worlds; the session epoch (the one the liveness record,
//      game-thread jobs and published records carry) also moves at every boundary in either direction, so
//      nothing prepared before a map jump, load, title screen or game over can act after it;
//   4. builds the seat table: per seat its character, party slot, actor, physics object, handle, incarnation,
//      alive/movable state and whether it steers this pass (handoff.h plans ownership changes, every old owner
//      released before a new one is acquired, each acquire revalidated against the live actor first);
//   5. publishes one session record (record_slot, wait-free for detours) and the session generation (the
//      game-thread queue rejects jobs made for an older seat layout).
//
// Contract for readers: an epoch match does NOT mean the world is valid. A new session epoch starts both when a
// world becomes valid and when it stops being valid, so a load, the title screen or a cutscene is an epoch of its
// own in which the record is fresh but GATE_WORLD is clear. Detours and game-thread ops that touch world objects
// must check GATE_WORLD (and their own gates), never the epoch alone. The game-thread queue enforces this for
// world-requiring ops (set_world each step; runtime/game_thread.h).
//
// Seats: arrays are ILC_SEATS long; only seats below ILC_ACTIVE_SEATS_MAX are ever filled. A seat beyond the cap
// stays empty whatever the settings say.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/detour.h"
#include "session/handoff.h"
#include "session/policy.h"

namespace ilc::session {

constexpr uint64_t SCAN_INTERVAL_MS = 250;   // full actor scans while nothing changes
constexpr uint32_t FIRST_EPOCH = 1;          // the session epoch before any world (never 0 once started)

enum phase : uint8_t {
    PHASE_STOPPED = 0,    // the session core has not started
    PHASE_NO_WORLD = 1,   // title screen, boot, or the game's structures are gone (game over, quitting)
    PHASE_LOADING = 2,    // structures present, party or area still settling
    PHASE_CUTSCENE = 3,   // the player has no control
    PHASE_MENU = 4,       // a native menu or modal interaction owns the screen
    PHASE_FIELD = 5,      // play
    PHASE_PARTY_DOWN = 6, // every active party actor is down: the game's own recovery or game over screen runs
};
// GET_SEATS reports the phase as ilc_session_state: the values are the same.
static_assert(static_cast<uint32_t>(PHASE_STOPPED) == static_cast<uint32_t>(ILC_SESSION_NOT_RUNNING), "phase value");
static_assert(static_cast<uint32_t>(PHASE_NO_WORLD) == static_cast<uint32_t>(ILC_SESSION_NO_WORLD), "phase value");
static_assert(static_cast<uint32_t>(PHASE_LOADING) == static_cast<uint32_t>(ILC_SESSION_LOADING), "phase value");
static_assert(static_cast<uint32_t>(PHASE_CUTSCENE) == static_cast<uint32_t>(ILC_SESSION_CUTSCENE), "phase value");
static_assert(static_cast<uint32_t>(PHASE_MENU) == static_cast<uint32_t>(ILC_SESSION_MENU), "phase value");
static_assert(static_cast<uint32_t>(PHASE_FIELD) == static_cast<uint32_t>(ILC_SESSION_FIELD), "phase value");
static_assert(static_cast<uint32_t>(PHASE_PARTY_DOWN) == static_cast<uint32_t>(ILC_SESSION_PARTY_DOWN), "phase value");
static_assert(ILC_ENUM_COUNT(ILC_SESSION_STATES) == 7, "every phase has a session state");

// Gate bits of the session record (record_head.flags).
enum gate_bit : uint32_t {
    GATE_WORLD = 0x0001,          // coherent world, not loading, no cutscene
    GATE_P1_GAMEPLAY = 0x0002,
    GATE_GAMEPLAY = 0x0004,       // P1 and every guest character in session are present
    GATE_MOVEMENT = 0x0008,       // characters may be steered (session rule; per-seat steering is in the seat flags)
    GATE_CAMERA = 0x0010,
    GATE_P1_MENU = 0x0020,
    GATE_GUEST_MENU = 0x0040,     // at least one guest menu may open (per seat: SEATF_MENU)
    GATE_LOGICAL = 0x0080,        // co-op is on and some guest is under manual control
    GATE_SHARED_CAMERA = 0x0100,  // at least two live roles to frame
    GATE_GUESTS_MOVE = 0x0200,    // the follower movement hooks must stay armed (some guest steers)
    GATE_INTERACTION = 0x0400,    // guests may interact with the world (towns included)
    GATE_PARTY_DOWN = 0x0800,     // hands off: the game's recovery owns input
};

// Facts of the session record.
enum fact_bit : uint32_t {
    FACT_TOWN = 0x01,
    FACT_CHOCOBO = 0x02,
    FACT_MENU = 0x04,
    FACT_MODAL = 0x08,
    FACT_CONTROL = 0x10,
    FACT_SUMMON = 0x20,
    FACT_SCAN_OK = 0x40,          // the last actor scan found the manager
};

// Seat flags.
enum seat_flag : uint16_t {
    SEATF_IN_SESSION = 0x0001,    // the seat takes part (P1 always; a guest that is configured and playing)
    SEATF_MANUAL = 0x0002,        // under manual control (playing, not parked, not toggled off)
    SEATF_COHERENT = 0x0004,      // its character is in both party layouts
    SEATF_ACTOR = 0x0008,         // actor and physics pointers are valid for this pass
    SEATF_ALIVE = 0x0010,         // a coherent alive sample (clear + SEATF_ALIVE_KNOWN = down)
    SEATF_ALIVE_KNOWN = 0x0020,
    SEATF_WALKS = 0x0040,         // no status stops it walking (valid with SEATF_ACTOR)
    SEATF_STEERS = 0x0080,        // owns its movement this pass (committed owner)
    SEATF_LEADER = 0x0100,        // its character is the native leader
    SEATF_MENU = 0x0200,          // its battle menu may open
    SEATF_KO_PROMOTED = 0x0400,   // the game made it leader because P1 is down
};

struct seat_entry {
    uint16_t flags;            // seat_flag
    uint16_t member;           // party id, MEMBER_NONE
    uint8_t party_slot;        // 0..3, 0xFF unknown
    uint8_t reserved[3];
    uint32_t handle;
    uint32_t actor_generation; // 0 = no incarnation
    uint64_t actor;            // compare only; never dereference outside the probe
    uint64_t phys;
};
static_assert(sizeof(seat_entry) == 32, "seat_entry layout");

// The published session record: what detours and other threads read (wait-free, via record_slot).
struct session_record {
    patch::record_head head;   // epoch = session epoch, flags = gate bits, generation = session generation
    uint32_t world_epoch;
    uint32_t facts;            // fact_bit
    uint32_t location;         // coherent area id, LOCATION_NONE
    uint16_t leader;           // native leader party id (MEMBER_NONE)
    uint8_t phase;
    uint8_t reason;            // world_reason
    seat_entry seat[SEATS];
};
static_assert(offsetof(session_record, head) == 0 && sizeof(session_record) == 32 + 32 * SEATS,
              "session_record layout");

// Settings as the session needs them. character: ilc_character (0..5, AUTO, NONE).
struct seat_config {
    uint8_t character[SEATS];
    uint8_t has_input;         // bit s: the seat owns a pad or the keyboard (seats at or above the cap never count)
    uint8_t parked;            // bit s: parked in the settings
    uint8_t enabled;           // co-op on (the kill switch clears it)
    uint8_t reserved;
};
static_assert(sizeof(seat_config) == 8, "seat_config layout");

// ---- co-op thread -------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);   // epoch FIRST_EPOCH, phase NO_WORLD, record published
void configure(const seat_config& cfg);              // applied at the next step
void configure_from_settings(const ilc_settings& s); // character + playing from the applied settings record
void set_live_parked(uint32_t parked_seats);         // the input service's live park bits (bit s = seat s)
void set_live_enabled(bool enabled);                 // the kill switch: false = co-op off from the next step
void step(uint64_t now_ms);
void stop();                                         // withdraws the record; detours fall back to stock

// ---- any thread -----------------------------------------------------------------------------------------------
uint32_t epoch();                                    // the session epoch (0 before start)
uint64_t generation();
bool read(session_record* out);                      // the latest published record
patch::record_slot<session_record>& slot();

// Effects the session planned that need a game-thread job (asking a released character to follow). The
// movement subsystem drains them; until it exists they are counted and dropped.
struct pending_effect {
    uint32_t epoch;
    uint32_t reserved;
    effect fx;
};
uint32_t take_effects(pending_effect* out, uint32_t max);

struct counters {
    volatile uint64_t steps;
    volatile uint64_t scans;
    volatile uint64_t epoch_bumps;
    volatile uint64_t generation_bumps;
    volatile uint64_t identity_adoptions;
    volatile uint64_t acquires_refused;
    volatile uint64_t effects_dropped;
    volatile uint64_t reads;
    volatile uint64_t read_failures;
};
const counters* stats();

// ---- host tests ------------------------------------------------------------------------------------------------
struct debug_view {
    world_sample world;
    world_view view;
    identity configured;
    identity live;
    lifecycle life;
    uint32_t world_epoch;
    uint32_t session_epoch;
    uint64_t generation;
    uint32_t effects_total;
    uint32_t phase;
    party_actors party;
    ownership owners;
};
void test_debug(debug_view* out);
void test_reset();
// -1 = follow the patch layer (Berserk steerable while the movement subsystem is live); 0 / 1 = forced.
void test_force_movement_live(int32_t value);

}  // namespace ilc::session
