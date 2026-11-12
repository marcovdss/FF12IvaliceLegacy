// Ivalice Legacy co-op module: the session rules, as pure functions of what one pass observed.
//
// Nothing here reads memory, keeps state or knows the time: the session core (session.h) feeds it a world sample
// and the seat identities and gets back gates and decisions, so every rule can be tested as a table.
//
// Seats are indexed from 0 (= P1, the host). Arrays are ILC_SEATS long so the layout never changes, but only seats
// below ILC_ACTIVE_SEATS_MAX ever take part: every function ignores the rest.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "session/game_facts.h"
#include "session/world.h"

namespace ilc::session {

constexpr uint32_t SEATS = ILC_SEATS;
constexpr uint32_t ACTIVE_SEATS = ILC_ACTIVE_SEATS_MAX;
static_assert(ACTIVE_SEATS >= 2 && ACTIVE_SEATS <= SEATS, "seat cap");
constexpr int8_t NO_SEAT = -1;

// Which character each seat controls. member[0] is P1's; a guest with no character, or a seat that is not in the
// session, holds MEMBER_NONE. in_session bit s: seat s takes part (P1 always does).
struct identity {
    uint16_t member[SEATS];
    uint8_t in_session;
    uint8_t reserved[7];
};
static_assert(sizeof(identity) == 16, "identity layout");

bool same_identity(const identity& a, const identity& b);
identity make_identity(uint16_t p1, const uint16_t* guests, uint32_t guest_count, uint8_t in_session);

bool member_in_layout(uint16_t member, const uint16_t* layout);  // first four entries
// The member is in both the current party and the HUD party.
bool member_in_both(uint16_t member, const uint16_t* current, const uint16_t* ui);

// The character P1 controls now: the native leader if it is a named, non-Esper party member present in both
// layouts and not a guest's character; otherwise P1's assigned character.
uint16_t p1_member_now(const identity& id, const uint16_t* current, const uint16_t* ui, uint32_t leader);

// After a party or leader change: who controls whom, keeping every seat whose character is still there, giving a
// displaced seat P1's former character, and filling the rest from the party in native order. False when the party
// holds no regular character in both layouts.
bool resolve_identity(const identity& configured, const uint16_t* current, const uint16_t* ui, uint32_t leader,
                      identity* out);
// The identity to adopt from a settled live party, or false when nothing must change (a summon, a cutscene, a
// load, an open menu, a party the two layouts disagree on, a non-regular leader, or the same identity).
bool identity_from_party(const identity& current_identity, const world_sample& w, identity* out);

// A coherent temporary Esper formation: its party id, or MEMBER_NONE.
uint16_t summoned_esper(const uint16_t* current, const uint16_t* ui);
// The seat whose character owns the active summon, or NO_SEAT.
int8_t summon_owner_seat(const identity& id, const uint16_t* current, const uint16_t* ui, uint32_t leader);
// P1 is down and the native game promoted a guest's character to leader: that guest's seat, or NO_SEAT.
int8_t ko_promoted_seat(const identity& id, uint32_t leader, tri p1_alive, bool p1_present);

// The world as the session sees it this pass.
enum world_reason : uint8_t {
    WR_UNAVAILABLE = 1,       // the game's structures are not there (title, boot, game over screen)
    WR_P1_ABSENT = 2,         // P1's character is not in both party layouts
    WR_P1_NOT_MATERIALIZED = 3,
    WR_LOCATION_SETTLING = 4, // current and HUD area ids disagree (a load is finishing)
    WR_NO_GUEST = 5,          // field, but no guest character is in the party
    WR_FIELD = 6,
    WR_MENU = 7,              // native battle command menu
    WR_MODAL = 8,             // native modal interaction
    WR_TOWN = 9,
    WR_CHOCOBO = 10,
    WR_CUTSCENE = 11,
    WR_SUMMON = 12,           // field, an Esper is out
    WR_P1_NOT_LEADER = 13,
};

struct world_view {
    uint8_t loading;          // P1 (or a summon owner) is not structurally present: nothing may act
    uint8_t cutscene;         // structurally present but the player has no control (and no menu or modal)
    uint8_t p1_gameplay;      // P1 can play
    uint8_t gameplay;         // P1 and every guest character in session are present
    uint8_t p1_is_leader;
    uint8_t p1_menu;          // P1's battle menu may open
    uint8_t movement;         // characters may be steered (no menu, no modal, control on)
    uint8_t camera;           // the shared camera may run
    uint8_t guest_menu[SEATS];  // a guest's battle menu may open (index 0 unused)
    int8_t summon_seat;       // NO_SEAT or the seat owning an active summon
    int8_t leader_seat;       // the seat whose character is the native leader, or NO_SEAT
    uint8_t reason;           // world_reason
    uint8_t signature_valid;  // a coherent world signature exists
    uint16_t p1_member;       // p1_member_now()
    uint16_t reserved;
    uint32_t location;        // the coherent area id (signature), LOCATION_NONE without one
    uint64_t battle_state;    // the signature's other half
};
static_assert(sizeof(world_view) == 32, "world_view layout (host tests read it)");

// materialized: party actors the actor scan found, or -1 when not known (no check).
void evaluate_world(const identity& id, const world_sample& w, int32_t materialized, world_view* out);

// The world is valid for co-op: a coherent signature, not loading, no cutscene.
bool world_valid(const world_view& v);

// Session movement gates for one guest and for P1 (the native boundaries: no menu, no modal, no load, no cutscene).
bool guest_may_move(const world_view& v, const identity& id, const world_sample& w, uint32_t present_seats);
bool p1_may_move(const world_view& v, const world_sample& w);

// Lifecycle: is co-op logically on, may any guest move, may the shared camera frame the party?
struct lifecycle {
    uint8_t logical;
    uint8_t movement;
    uint8_t shared_camera;
    uint8_t reserved;
};
// alive/present for seats 1.. (guests); p1_alive for P1. A KO guest keeps the session; an unknown sample neither
// proves nor removes anything.
lifecycle lifecycle_gates(bool enabled, tri p1_alive, const tri* guest_alive, const bool* guest_present,
                          uint32_t guests);

// Does this seat steer its character this pass?
struct steer_facts {
    uint8_t enabled;          // the movement master for the seat (co-op movement on and the seat under manual control)
    int8_t alive;             // tri
    uint8_t actor_present;
    uint8_t identity_current; // the seat's character did not change this pass
    uint8_t layout_coherent;  // its character is in both layouts
    uint8_t allow_leader;     // a KO promotion made it the native leader on purpose
    uint16_t member;          // MEMBER_NONE: no character
    uint32_t leader;          // native leader party id (0xFFFF none)
};
bool seat_steers(const steer_facts& f);

// Keep the current owner through one gap: the actor mapping is briefly unknown, or one status sample is torn.
// Only the same seat's same character in the same world, still under manual control, with co-op on.
struct owner_ref {
    uint16_t member;          // MEMBER_NONE: no owner
    uint16_t reserved;
    uint32_t world_epoch;
};
bool keep_through_mapping_gap(bool mapping_unknown, bool coop_active, bool manual, owner_ref current,
                              uint16_t seat_member, uint32_t leader, uint32_t world_epoch, bool allow_leader);
bool keep_through_status_gap(tri alive, bool coop_active, bool manual, owner_ref current, bool candidate_is_current,
                             uint16_t seat_member, uint32_t leader, uint32_t world_epoch, bool allow_leader);

// A seat's cached actor survives a scan gap only if its character did not change, is still in both layouts and
// the actor was not proved gone.
bool cache_survives(bool member_changed, uint16_t member, const uint16_t* current, const uint16_t* ui,
                    tri actor_valid);

// The followers' movement hooks stay armed while any guest owns movement.
bool guests_master(bool base, uint32_t owned_seats);

// Ownership vs input under a custom Wait menu: ownership may continue while input is held still.
struct movement_flags {
    uint8_t ownership;
    uint8_t input;
    uint8_t reserved[2];
};
movement_flags movement_control(bool lifecycle_movement, bool session_movement, bool custom_wait, bool active_mode);

// Camera rules.
bool camera_gate(bool lifecycle_camera, bool session_camera, bool gameplay, bool loading, bool interaction_owner);
struct reacquire {
    uint8_t active;
    uint8_t pending;
    uint16_t reserved;
    uint32_t ready_samples;
};
constexpr uint32_t CAMERA_READY_SAMPLES = 3;
reacquire camera_reacquire(bool requested, bool was_requested, reacquire previous, bool native_ready,
                           uint32_t required = CAMERA_READY_SAMPLES);
bool camera_positions_ready(const float* xyz, uint32_t count);

// The whole party is down: every active actor gave a coherent dead sample. An unknown sample never counts as
// dead (it may be an actor being rebuilt).
bool party_down(const tri* alive, uint32_t count);

}  // namespace ilc::session
