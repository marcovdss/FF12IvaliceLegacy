// Ivalice Legacy co-op module: seat assignment rules (pure).
//
// Who may take which character, and what a "change character" request turns into. No memory reads, no clock, no
// thread: the co-op thread plans with these rules from the published session record and a world read, and the
// game thread runs the same rules again on what it reads right before it changes anything, so a request that went
// stale while it waited is refused instead of applied to a party that moved on.
//
// The rules (Ivalice Together's seat behaviour, written anew for three seats):
//  * Picker: a seat's preferred character is one of the six regular characters; a guest may not prefer Player 1's
//    current character (Vaan included only when Player 1 is not Vaan). Story guests are never offered: they are
//    reached only through recovery when nobody else can be controlled.
//  * Player 1 always keeps a seat and a character, and drives the native leader. A change only ever exchanges
//    characters between seats or hands a seat a member nobody controls; it never leaves Player 1 without one.
//  * A seat that asks for a character another seat holds swaps with it (swap when taken). A benched seat (playing,
//    but no character was free) may only claim a free member: it never takes one away from another seat.
//  * Player 1 asking for a guest's character, or a guest asking for Player 1's, is a leader change: the guest
//    gets Player 1's previous character in the same frame. Player 1 asking for a member nobody controls is a plain
//    leader change.
//  * Only members of the active party (both the current and the HUD layout) can be taken, only while they can
//    lead (not KO, Stone or X-Zone) and are not in an attack motion; a character handed to another seat in a swap
//    must be able to lead too.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

namespace ilc::party {

constexpr uint32_t SEATS = ILC_SEATS;
constexpr uint32_t ACTIVE_SEATS = ILC_ACTIVE_SEATS_MAX;
constexpr uint32_t SLOTS = 4;            // active party slots
constexpr uint16_t MEMBER_NONE = 0xFFFF;
constexpr uint16_t REGULARS = 6;         // party ids 0..5
constexpr uint8_t NO_SEAT = 0xFF;

constexpr bool is_regular(uint32_t member) { return member < REGULARS; }

// The seats as the session published them. flags: the session's seat flags (session.h, SEATF_*).
struct seats_view {
    uint16_t member[SEATS];
    uint16_t flags[SEATS];
};

// The active party as one pass read it. able/busy per party slot: 1 = known yes; anything unknown counts as 0 for
// able and 1 for busy (a change needs proof).
struct party_view {
    uint16_t current[SLOTS];
    uint16_t ui[SLOTS];
    uint32_t leader;          // the native leader party id (0xFFFF none)
    uint8_t able[SLOTS];      // the slot's member can lead: alive, not KO, Stone or X-Zone
    uint8_t busy[SLOTS];      // the slot's member is in an execution (attack) motion
};

enum change_kind : uint8_t {
    KIND_NONE = 0,
    KIND_ALREADY = 1,         // the seat already controls that member
    KIND_LEADER = 2,          // Player 1 takes a member nobody controls (leader change)
    KIND_SWAP_P1 = 3,         // Player 1 and a guest exchange characters (leader change)
    KIND_SWAP_GUESTS = 4,     // two guests exchange characters
    KIND_TAKE_FREE = 5,       // a guest takes a member nobody controls; its previous one goes back to the AI
};

// Refusals (also the game-thread operation's own result codes, <= -16).
enum change_refusal : int32_t {
    CHANGE_OK = 0,
    REFUSE_BAD_SEAT = -16,          // not a seat of this session, or not in the session
    REFUSE_NOT_REGULAR = -17,       // only the six regular characters can be picked
    REFUSE_NOT_IN_PARTY = -18,      // the member is not in the active party (both layouts)
    REFUSE_UNAVAILABLE = -19,       // the member (or the one handed over in a swap) cannot lead now
    REFUSE_BUSY = -20,              // in an attack motion
    REFUSE_BENCHED = -21,           // a benched seat may only claim a free member
    REFUSE_P1_UNSTABLE = -22,       // Player 1's character is not the native leader (KO promotion, a pending change)
    REFUSE_DUPLICATE = -23,         // the session's seats are not one member each (a stale record)
};

struct change_plan {
    uint8_t kind;             // change_kind
    uint8_t seat;             // the requesting seat
    uint8_t other;            // the seat that gives the member up (swaps), NO_SEAT otherwise
    uint8_t target_slot;      // party slot of the requested member
    uint16_t target;          // the requested member
    uint16_t released;        // the requester's previous member (MEMBER_NONE when benched)
    uint16_t new_leader;      // the leader after the change (MEMBER_NONE: no leader change)
    uint16_t reserved;
    uint32_t mask;            // bit s: seat s gets after[s]
    uint16_t after[SEATS];    // every seat's member after the change
};
static_assert(sizeof(change_plan) == 24, "change_plan layout (host tests read it)");

// A seat takes part and controls nothing because no member was free for it.
bool benched(const seats_view& s, uint32_t seat);
// The picker: may `seat` prefer `member` while Player 1 controls `p1_member`?
bool picker_allows(uint32_t seat, uint32_t member, uint32_t p1_member);
// Party slot 0..3 of a member in both layouts, or -1.
int32_t slot_in_party(const party_view& p, uint32_t member);
// CHANGE_OK and the plan, or a refusal (the plan is then zeroed apart from seat/target).
int32_t plan_change(const seats_view& s, const party_view& p, uint32_t seat, uint32_t target, change_plan* out);
// Two plans describe the same change (the game thread's re-plan against the one the co-op thread queued).
bool same_plan(const change_plan& a, const change_plan& b);

}  // namespace ilc::party
