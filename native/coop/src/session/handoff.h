// Ivalice Legacy co-op module: who owns which character, and how a released character goes back to the game.
//
// Pure planning, like policy.h: callers pass observations and the time and get back ordered effects. Executing an
// effect (publishing ownership, asking the game to make a character follow its leader) belongs to the caller,
// which must check the same token again at that moment.
//
// Tokens. A token names one incarnation of one character: the seat that owns it, the party id, the actor and
// physics pointers, the native handle, an actor generation and the world epoch it was seen in. A pointer can be
// reused by a new actor after a load, so a token is only ever compared whole.
//
// Releasing. A character that leaves manual control is handed back to the game's own follower logic: one request
// at a time, at most RELEASE_ATTEMPTS requests, each confirmed only by a later sample, inside a bounded window.
// A character that cannot follow yet (KO, petrified, briefly the leader, mid-action) keeps a write-free intent that
// waits as long as needed without spending attempts. A different world, a new incarnation, a cutscene, a reserve
// swap or a layout change cancels the intent.
#pragma once

#include <stdint.h>

#include "session/policy.h"

namespace ilc::session {

struct owner_token {
    uint8_t seat;              // 0..ACTIVE_SEATS-1
    uint8_t reserved0;
    uint16_t member;           // party id
    uint32_t actor_generation; // counts incarnations of this party id (see generation_tracker)
    uint32_t world_epoch;
    uint32_t handle;           // native handle (0 = not known)
    uint64_t actor;
    uint64_t phys;
};
static_assert(sizeof(owner_token) == 32, "owner_token layout (host tests build it)");

bool same_token(const owner_token& a, const owner_token& b);
// Same character incarnation, whichever seat owns it.
bool same_incarnation(const owner_token& a, const owner_token& b);

constexpr int32_t NONE_I32 = -1;  // "not read" for the observation fields below

// What one sample showed about a released (or about to be released) character.
struct follower_sample {
    owner_token of;            // the incarnation the sample was taken for (the lookup key)
    owner_token token;         // what the sample found there (differs when a new incarnation took its place)
    uint64_t sample;           // sample number (monotonic per pass)
    uint8_t available;         // the action fields below were read for exactly this incarnation
    int8_t alive;              // tri
    uint8_t materialized;      // the actor is (or may still be) this incarnation
    uint8_t layout_coherent;   // its party id is in both layouts
    uint8_t cutscene;          // a cutscene or load blocks every request
    int8_t command_blocked;    // tri: a status the game refuses commands under
    uint8_t follows_list;      // the actor is attached to one of the native follower lists
    uint8_t reserved0;
    uint32_t leader;           // native leader party id (0xFFFF none / unknown)
    int32_t action_state;      // NONE_I32 when not read
    int64_t target;            // NONE_I32 when not read
    int32_t action;            // NONE_I32 when not read
    int32_t descriptor_action; // NONE_I32 when not read
};
static_assert(sizeof(follower_sample) == 104, "follower_sample layout (host tests build it)");

bool sample_confirms_follow(const follower_sample& s);  // the game already treats it as a follower
bool sample_allows_request(const follower_sample& s);   // idle, untargeted, ready to be asked
bool sample_cancels(const follower_sample& s);          // the incarnation is gone from this world

constexpr uint32_t RELEASE_ATTEMPTS = 4;
constexpr uint64_t RELEASE_RETRY_MS = 100;
constexpr uint64_t RELEASE_WINDOW_MS = 1000;

struct release_intent {
    owner_token token;
    uint64_t created_ms;
    uint64_t deadline_ms;
    uint64_t next_attempt_ms;
    uint64_t requested_sample;  // valid when has_request
    uint32_t attempts;
    uint8_t has_request;
    uint8_t waiting;            // a reason to wait passively was seen (no attempt is spent while it holds)
    uint8_t reserved[2];
};
static_assert(sizeof(release_intent) == 72, "release_intent layout (host tests build it)");

constexpr uint32_t INTENTS_MAX = 8;  // one per character incarnation; more than the party can hold

struct ownership {
    owner_token owner[SEATS];
    uint8_t has_owner[SEATS];
    uint32_t intent_count;
    release_intent intent[INTENTS_MAX];
};
static_assert(sizeof(ownership) == 128 + 8 + 72 * INTENTS_MAX, "ownership layout (host tests build it)");

enum effect_kind : uint8_t {
    FX_RELEASE = 1,        // stop steering the old owner (publish it as not owned)
    FX_CLEAR = 2,          // clear the old owner's per-seat extras (loot, HUD slot, interaction entry)
    FX_ASK_FOLLOW = 3,     // ask the game to make the character follow its leader (a game-thread job)
    FX_ACQUIRE = 4,        // start steering the new owner (revalidate the token first)
    FX_FOLLOW_CONFIRMED = 5,
    FX_FOLLOW_ABANDONED = 6,
};

enum effect_reason : uint8_t {
    WHY_NONE = 0,
    WHY_TRANSFER = 1,       // ACQUIRE of an incarnation another seat just released
    WHY_WORLD = 2,          // the world epoch changed
    WHY_GENERATION = 3,     // a new incarnation replaced it
    WHY_INELIGIBLE = 4,     // cutscene, reserve, layout change
    WHY_ALREADY_FOLLOWING = 5,
    WHY_ACCEPTED = 6,       // a later sample shows the follow took
    WHY_BOUNDED = 7,        // attempts or window used up
    WHY_ELIGIBLE = 8,
    WHY_SHUTDOWN = 9,
    WHY_BOUNDARY = 10,      // a lifecycle boundary ended every intent
};

struct effect {
    uint8_t kind;
    uint8_t reason;
    uint8_t reserved[6];
    owner_token token;
};
static_assert(sizeof(effect) == 40, "effect layout");

constexpr uint32_t EFFECTS_MAX = 48;
struct effect_list {
    uint32_t count;
    uint32_t dropped;  // effects that did not fit (never expected: bounded by seats and intents)
    effect item[EFFECTS_MAX];
};

struct sample_set {
    const follower_sample* items;
    uint32_t count;
    const follower_sample* find(const owner_token& incarnation) const;
};

// The desired owner per seat (has_desired), whether each may be acquired now, and the observations of the
// incarnations involved. Every old owner is released and cleared before any new one is acquired.
void plan_ownership(ownership& state, const owner_token* desired, const uint8_t* has_desired, const uint8_t* eligible,
                    const sample_set& samples, uint64_t now_ms, uint32_t world_epoch, effect_list& out);
void advance_intents(ownership& state, const sample_set& samples, uint64_t now_ms, uint32_t world_epoch,
                     effect_list& out);
void end_intents(ownership& state, uint8_t reason, effect_list& out);
// The caller could not acquire `token` (its revalidation failed): drop that owner; if it was a transfer, give
// the incarnation back its release intent.
void refuse_acquire(ownership& state, const owner_token& token, bool restore_intent, uint64_t now_ms);

// World epochs: a new number after every invalid stretch or change of coherent world. 0 = no world yet.
struct world_epochs {
    uint32_t epoch;
    uint8_t invalid_since;  // an invalid sample was seen since the last valid one
    uint8_t has_signature;
    uint8_t reserved[2];
    uint64_t sig_a;
    uint64_t sig_b;
    uint32_t observe(bool valid, uint64_t a, uint64_t b);
};

// Actor generations per party id. A pointer, physics or non-zero handle change, a return from the reserve, or a
// new world starts a new incarnation; a transient scan gap (null pointers, a zero handle read) does not.
constexpr uint32_t GENERATION_MEMBERS = 16;  // party ids 0..15 (seats only ever hold regulars 0..5)
struct generation_tracker {
    struct entry {
        uint64_t actor;
        uint64_t phys;
        uint32_t handle;
        uint32_t generation;
        uint32_t next;       // last generation handed out
        uint32_t epoch;
        uint8_t known;       // a record exists
        uint8_t present;     // structurally present at the last observation
        int8_t alive;        // last known alive (tri)
        uint8_t reserved[5];
    };
    entry e[GENERATION_MEMBERS];
    // False (no token) when the party id has no slot, is not structurally present, or the actor or physics
    // pointer is missing (an UNKNOWN sample).
    bool observe(uint8_t seat, uint16_t member, uint64_t actor, uint64_t phys, uint32_t handle, uint32_t world_epoch,
                 tri alive, bool present, owner_token* out);
};

}  // namespace ilc::session
