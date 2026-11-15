// Ivalice Legacy co-op module: a guest's target candidates and its target cursor.
//
// When a guest's menu waits for a target, the picker offers four groups: Foes and Neutrals (world actors near the
// guest's character), Party (the active party, in HUD order) and Reserve (party members outside the active
// formation, for the actions that may target them). The rules, Ivalice Together's guest picker rewritten for Legacy:
//  * Foes are units of type 1, 4 (mimics) and 7 (hunts); Neutrals are type 3. Either must be active, not hidden, not
//    flagged untargetable by its extended battle block (despawned, dead, inactive), alive (HP > 0, no KO bit), have a
//    handle, and stand within 24 units of the guest's character, measured on the ground (X/Z), boundary included.
//    The unit type decides, never the aggression flag or the handle's generation. Nearest first.
//  * Each foe carries the letter the game itself gives a copy (instance 1..26 = A..Z, larger numbers as numbers);
//    without one, copies sharing a name get A, B, ... in scan order.
//  * Party members are always listed; whether one is selectable depends on the pending action: a revival only
//    targets KO members, a KO member needs an action allowed on KO allies (rule bit 6), a stoned member one allowed
//    on stoned allies (rule bit 7).
//  * Reserve members come from the native default and menu layouts (slots 4..8). Both must agree on membership and
//    contain every active member (an Esper in the active party is ignored); otherwise a party change is in flight
//    and no reserve target is offered.
//
// The cursor keeps the guest on the same candidate across refreshes (by handle, or by party id for the reserve).
// Opening a party page starts on the issuer, except for a remedy (an item or magick that cures a status), which
// starts on the first party member carrying a status it cures. While the cursor rests on the issuer it stays there
// when the party reorders. LB/RB cycle the groups that have selectable candidates (model_cycle_group).
//
// Everything here is pure except targets_live.cpp, which fills the inputs from the game through the fault-safe
// reader. Nothing writes game memory.
#pragma once

#include <stdint.h>

#include "menu/catalog.h"
#include "menu/model.h"

namespace ilc::targets {

constexpr uint32_t ACTOR_CAP = 256;      // world actors considered per refresh (nearest first after filtering)
constexpr uint32_t CANDIDATE_CAP = 96;   // candidates kept, all groups together
constexpr uint32_t PARTY_SLOTS = 4;
constexpr uint32_t RESERVE_SLOTS = 5;    // layout slots 4..8
constexpr uint32_t LAYOUT_ENTRIES = 9;   // party ids per native layout
constexpr uint16_t MEMBER_NONE = 0xFFFF;

// One world actor as the scan read it.
enum snapshot_flag : uint8_t {
    SNAP_ACTIVE = 0x01,
    SNAP_HIDDEN = 0x02,
    SNAP_BLOCKED = 0x04,     // the extended battle block says untargetable
    SNAP_RECORDS = 0x08,     // the battle record and the extended block are present
};

struct actor_snapshot {
    uint32_t handle;
    uint8_t unit_type;       // low nibble of the unit block's type
    uint8_t flags;           // snapshot_flag
    uint16_t instance;       // the game's own copy number (0 or 0xFFFF = none)
    uint32_t hp;
    uint32_t status;         // timed | lasting
    float x, y, z;
    uint32_t reserved;
    uint64_t actor;
    uint64_t name;           // name text pointer (shared by copies of one foe)
};
static_assert(sizeof(actor_snapshot) == 48, "actor_snapshot layout (host tests build it)");

// One active party member, in HUD order.
struct party_member {
    uint32_t handle;
    uint16_t member;         // party id
    uint8_t present;         // 1 = the actor and its record were read
    uint8_t reserved;
    uint32_t hp;
    uint32_t status;
    uint64_t actor;
};
static_assert(sizeof(party_member) == 24, "party_member layout");

struct picker_input {
    float issuer_x, issuer_y, issuer_z;
    uint32_t issuer_handle;
    uint32_t actor_count;
    uint32_t party_count;
    uint32_t reserve_count;
    uint32_t reserved;
    party_member party[PARTY_SLOTS];
    uint16_t reserve[RESERVE_SLOTS];  // party ids
    uint16_t reserved2[3];
    actor_snapshot actors[ACTOR_CAP];
};

// What the pending action allows (from its catalog entry and action rule).
struct action_rules {
    uint16_t action;
    uint16_t rules;          // action rule bits: 0x40 KO allies, 0x80 stoned allies
    uint8_t groups;          // target groups the action offers (bit = menu::target_group)
    uint8_t revive;          // 1 = a revival: only KO members are selectable
    uint8_t reserved[2];
};
static_assert(sizeof(action_rules) == 8, "action_rules layout");

enum candidate_flag : uint8_t {
    CF_SELECTABLE = 0x01,
    CF_ISSUER = 0x02,        // the guest's own character
};

struct candidate {
    uint32_t handle;         // 0 for a reserve member
    uint16_t member;         // party id (party and reserve), MEMBER_NONE for world actors
    uint8_t group;           // menu::target_group
    uint8_t flags;           // candidate_flag
    uint8_t letter;          // 'A'..'Z' or 0
    uint8_t reserved;
    uint16_t number;         // a copy number past Z, or 0
    float distance;          // ground distance from the issuer (world actors), 0 otherwise
    uint32_t status;
    uint64_t actor;          // compare only
    uint64_t name;           // name text pointer (world actors), 0 otherwise
};
static_assert(sizeof(candidate) == 40, "candidate layout (host tests read it)");

struct candidate_list {
    uint16_t first[menu::TG_COUNT];
    uint16_t count[menu::TG_COUNT];      // every candidate of the group
    uint16_t selectable[menu::TG_COUNT]; // ... of which selectable
    uint16_t used;
    uint8_t truncated;                   // more candidates than CANDIDATE_CAP
    uint8_t reserved;
    uint32_t reserved2;
    candidate c[CANDIDATE_CAP];
};

// Build every group for one pending action. Within a group: Foes/Neutrals nearest first; Party in HUD order;
// Reserve in layout order.
void build_candidates(const picker_input& in, const action_rules& act, candidate_list* out);

// The k-th selectable candidate of a group, or nullptr.
const candidate* selectable_at(const candidate_list& l, uint32_t group, uint32_t k);
// Groups with at least one selectable candidate (bit = target_group).
uint8_t available_groups(const candidate_list& l);

// The statuses an action cures (by action id; 0 = not a remedy).
uint32_t remedy_mask(uint32_t action);

// ---- the cursor -----------------------------------------------------------------------------------------------------
struct target_cursor {
    uint16_t action;         // the pending action it belongs to (0xFFFF none)
    uint8_t group;           // the group it belongs to (TG_COUNT none)
    uint8_t fresh;           // the next refresh picks the default (issuer or remedy) instead of keeping
    uint8_t issuer_locked;   // the cursor rests on the issuer: it follows the issuer
    uint8_t reserved;
    uint16_t member;         // identity of the selected candidate: party id (reserve) ...
    uint32_t handle;         // ... or handle
};
static_assert(sizeof(target_cursor) == 12, "target_cursor layout");

void cursor_reset(target_cursor* c);

enum sync_result : int32_t {
    SYNC_IDLE = 0,           // the model is not choosing a target
    SYNC_OK = 1,
    SYNC_EMPTY = 2,          // no group of the action has a selectable candidate
};

// Bring the model's target page in line with fresh candidates: the group (moved to the first group with candidates
// when the current one has none), the count and the selected index. Run after every candidate refresh and after any
// target-mode event of the model.
int32_t sync(menu::menu_model* m, target_cursor* c, const candidate_list& l);

// Record a cursor move made by the model (navigate / group cycle) so the next refresh keeps it.
void note_selection(const menu::menu_model& m, target_cursor* c, const candidate_list& l);

}  // namespace ilc::targets
