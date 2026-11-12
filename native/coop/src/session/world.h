// Ivalice Legacy co-op module: what the session core reads from the game in one pass.
//
// Everything here is a read through game_reader (fault-safe). Nothing writes game memory. A read that fails, or
// a value that cannot be trusted (a torn status pair, a manager entry that points elsewhere), is reported as
// UNKNOWN, which the policy layer treats as "no new fact": it never invents a death, a departure or a new actor.
#pragma once

#include <stdint.h>

#include "session/reader.h"

namespace ilc::session {

// Three-valued facts. UNKNOWN is a transient read gap, not a "no".
enum tri : int8_t { T_UNKNOWN = -1, T_NO = 0, T_YES = 1 };

constexpr tri tri_of(bool b) { return b ? T_YES : T_NO; }

// The raw world facts of one pass.
struct world_sample {
    uint8_t roots;           // 1: the battle state, location and control blocks are all present and were read
    uint8_t control_enabled; // the player may control the leader (0 during cutscenes and scripted dialogue)
    uint8_t menu_open;       // the native battle command menu is open
    uint8_t modal_active;    // a native modal interaction (dialog, shop, chest message) owns the screen
    uint8_t town;            // the area is a town (runtime flag or area metadata)
    uint8_t chocobo;         // the party is mounted
    uint8_t location_read;   // both location words were read
    uint8_t reserved0;
    uint32_t leader;         // the leader word as read: 0xFFFF = none (also when unread)
    uint32_t reserved1;
    uint64_t battle_state;   // the battle state pointer (0 = none)
    uint16_t current[facts::LAYOUT_ENTRIES];  // current party layout (MEMBER_NONE = empty)
    uint16_t ui[facts::LAYOUT_ENTRIES];       // HUD party layout
    uint32_t location_current;                // LOCATION_NONE when unread
    uint32_t location_ui;
    uint8_t reserved2[4];
};
static_assert(sizeof(world_sample) == 72, "world_sample layout (host tests read it)");

void sample_world(game_reader& r, world_sample* out);

// One active party actor as the actor scan found it.
struct party_actor {
    uint32_t index;   // actor manager index (= the low 16 bits of its handle)
    uint32_t handle;  // the actor's handle when the scan read it
    uint64_t actor;
    uint64_t phys;
};
static_assert(sizeof(party_actor) == 24, "party_actor layout");

// The active party by party slot 0..3. A slot with no actor, or with two actors claiming it (a rebuild in
// progress), is empty (actor 0).
struct party_actors {
    party_actor slot[4];
    uint32_t found;      // non-empty slots
    uint32_t scanned;    // manager entries visited
    uint8_t manager_ok;  // the manager pointer and count were read and plausible
    uint8_t reserved[7];
};
static_assert(sizeof(party_actors) == 112, "party_actors layout");

void scan_party(game_reader& r, party_actors* out);

// O(1) re-check of one cached slot between scans: its manager entry still points at the same actor, the handle is
// unchanged and the entry still claims this party slot. False on any difference or read failure (rescan).
bool party_slot_holds(game_reader& r, uint32_t slot, const party_actor& cached);

// Is this actor still an active party actor? YES: its manager entry, handle index and party slot agree and its
// pointers are present. NO: the manager is stable and no entry vouches for it, or its slot left the party.
// UNKNOWN: the manager is missing, or a read failed somewhere it could have been listed.
tri actor_in_party(game_reader& r, uintptr_t actor);

// The actor's root and physics object (0 when unreadable).
uintptr_t actor_physics(game_reader& r, uintptr_t actor);

// One coherent status sample: the timed word read before and after the lasting word must agree.
bool battle_status(game_reader& r, uintptr_t battle, uint32_t* status_out);

tri actor_alive(game_reader& r, uintptr_t actor);          // HP > 0 and no KO bit
tri actor_walks(game_reader& r, uintptr_t actor);          // no status that stops walking
tri actor_command_blocked(game_reader& r, uintptr_t actor); // a status that refuses ordinary commands
tri actor_berserk(game_reader& r, uintptr_t actor);
// May the game keep this character as leader? HP > 0 and no status that refuses commands, from one coherent
// sample (status bracketed by HP and the battle pointer, 3 tries, else UNKNOWN). Berserk counts as blocking unless
// berserk_steerable: only while the movement subsystem is live, whose manual-command gate patch lets P1's
// character take commands under Berserk.
tri actor_leads(game_reader& r, uintptr_t actor, bool berserk_steerable);

}  // namespace ilc::session
