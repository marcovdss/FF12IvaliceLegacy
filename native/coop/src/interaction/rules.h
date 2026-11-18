// Ivalice Legacy co-op module: the pure rules of guest interactions (no memory reads, no clock, no thread).
//
// interaction.cpp (co-op thread) and hooks.cpp (game thread) gather the facts and call these; host tests state each
// rule as a table. Behaviour follows Ivalice Together's follower interactions (ported, written anew).
#pragma once

#include <stdint.h>

namespace ilc::interaction {

// A guest's own character may start an interaction: not drawing or sheathing a weapon, no execution motion, idle
// or walking, and no action other than "none", idle or "follow the leader" (the action a party member walking with
// the party carries). A drawn weapon is fine (the game holsters it). The action is the u16 at actor +0x714.
bool press_allowed(uint64_t actor_flags, uint32_t actor_state, uint32_t actor_action);

// Player 1's leader is free for an object interaction a guest starts (the game animates P1's leader for doors,
// crystals and switches): no weapon handling and no execution motion. Its queued actions are not inputs.
bool leader_allows(uint64_t actor_flags);

// The shop menu is open: the frontend id is the shop's and a frontend is active.
bool shop_open(uint32_t frontend_id, uint32_t frontend_active);

// The descriptor a guest's object interaction is fed to the consumer with: a close distance, the object's
// registry and index from its handle, the state, the handle, and the four ready bytes set on top of the live ones.
void forced_descriptor(uint32_t handle, uint32_t state, uint32_t live_ready, uint32_t out[6]);
// The descriptor the exact NPC scan starts from ("nothing found yet", talk state, ready bytes set).
void reset_descriptor(uint32_t out[6]);
// The state a guest's object interaction uses: a sleeping mimic is woken (state 10); otherwise the state the
// guest's own scan published, which must be a usable one (>= 2): 0 = not usable.
uint32_t object_state(uint32_t published_state, bool sleeping_mimic);

// Which publication slot names the guest's current object: the highest slot with a non-zero state, -1 for none.
int32_t current_slot(const uint32_t state[8]);

// The target the consumer is about to run on, as read immediately before the call.
struct target_view {
    uint8_t present;          // the registry table still holds an entry at the descriptor's index
    uint8_t type;             // entry +0x18
    uint8_t script_context;   // entry +0x60
    uint8_t reserved;
    uint32_t flags;           // entry +0x1C
    uint64_t script;          // entry +0x40
    uint64_t script_table;    // entry +0x48
};

enum target_verdict : int32_t {
    TARGET_OK = 0,
    TARGET_GONE = 1,          // no entry: the consumer would dereference a null target
    TARGET_TORN = 2,          // a sub-object the consumer dereferences is missing (teardown in progress)
    TARGET_NO_CONTEXT = 3,    // the script request would be refused: nothing to start
};
// Mirrors what the consumer dereferences for this descriptor state: a type-1 target has no script path; any other
// type needs +0x40, and when the state takes the script request (talk state with the talk flag, any other state
// with the other flag) also +0x48 and a script context.
int32_t target_ready(const target_view& t, uint32_t descriptor_state);

// ---- the hold: the guest who started an interaction keeps it until its dialog or menu has closed -----------------
constexpr uint64_t HOLD_RELEASE_MS = 80;     // no dialog or menu signal for this long after one was seen
constexpr uint64_t HOLD_OPEN_MS = 1000;      // nothing opened this long after the interaction started

enum hold_end : uint32_t {
    HOLD_KEEP = 0,
    HOLD_DONE = 1,            // the dialog or menu closed
    HOLD_NEVER_OPENED = 2,    // an object interaction without a dialog (a door, an instant pickup)
    HOLD_OWNER_GONE = 3,      // the guest left, was parked, or lost its pad
    HOLD_WORLD_CHANGED = 4,   // load, title, area change
};

struct hold_signals {
    uint8_t talking;          // the talk target is in conversation
    uint8_t menu_queued;      // a menu request waits for the field tick (dialog -> shop)
    uint8_t frontend;         // a full-screen frontend is open (the shop among them)
    uint8_t modal;            // a native modal (message box, chest notice) is shown
    uint8_t owner_ok;         // the owner seat is still in session, manual, with its pad
    uint8_t world_ok;         // a world exists and is not loading
    uint8_t reserved[2];
    uint32_t location;        // the area id, 0xFFFFFFFF unknown
};

struct hold_state {
    uint8_t active;
    uint8_t seat;             // the owner seat
    uint8_t opened;           // a signal was seen: the owner drives the native menus
    uint8_t reserved;
    uint32_t location;        // the area the interaction started in (0xFFFFFFFF unknown)
    uint32_t target_handle;
    uint32_t reserved2;
    uint64_t start_ms;
    uint64_t last_signal_ms;
};

void hold_begin(hold_state& h, uint32_t seat, uint32_t target_handle, uint32_t location, uint64_t now_ms);
// One beat: HOLD_KEEP, or why the hold ended (the state is cleared then).
uint32_t hold_step(hold_state& h, const hold_signals& s, uint64_t now_ms);
// The seat that drives the native menus now: the owner once its dialog or menu has opened, else 0 (player 1).
uint32_t hold_driver(const hold_state& h);

}  // namespace ilc::interaction
