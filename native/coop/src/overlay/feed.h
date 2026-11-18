// Ivalice Legacy co-op overlay: what the HUD elements need beyond the HUD snapshot, sampled on the co-op thread.
//
// The HudSnapshot (hud/snapshot.h) carries HP, statuses, levels, seats and targets; the party-extras record
// (hud/party_rows.h) carries gambits, the action id and Player 1's stock "choosing" state. The overlay's party list
// also needs names, MP, the action's and its target's names, a damage ghost and the chain. This record adds exactly
// that, built once per beat after the snapshot, party_rows, the menus and the battle log (boot.cpp beat order), and
// published through a seqlock slot.
// The render thread copies it next to the snapshot; it never reads game memory itself.
//
// Sources (all reads fault-safe, nothing written):
//  * names: the menus' name cache (hud/menus_draw.h live_name: members, actions, foes from their unit's name text);
//  * MP: the member record's s16 at +0x4C (menu/menu_facts.h REC_MP); MP max its s16 at +0x28 (the party-HUD filler's
//    copy, PH-V1); the overlay shows the current MP (as the mockup does), mp_max is there for a gauge;
//  * the action gauge: what the native row fills it from (PH-V2..PH-V4): actor +0x768 / +0x76c while the action state
//    (+0x6b4) is 3..8, full from 9, empty at 1..2, none without an action (+0x708 set, +0x714 a battle action). With a
//    PH-V row off: GAUGE_UNKNOWN (the name without a gauge) and mp_max -1;
//  * never the action's target (Marco, 2026-11-16: the target lines show who targets what, as in vanilla);
//  * the damage ghost: party_rows::ghost_step per row (hold 1.2 s, drain 0.4 s), keyed by the member;
//  * the chain: battle_log::chain_now() (the chain rows seen stock and the globals read this beat).
#pragma once

#include <stdint.h>

#include "hud/snapshot.h"
#include "patch/detour.h"

namespace ilc::overlay::feed {

constexpr uint32_t ROWS = hud::SNAP_MEMBERS;
constexpr uint32_t NAME = 24;                 // bytes incl. NUL
constexpr int16_t MP_UNKNOWN = -32768;
constexpr uint8_t GAUGE_UNKNOWN = 0xFF;
constexpr uint64_t STALE_MS = 250;            // the overlay draws nothing from an older record

enum row_flag : uint8_t {
    RF_MP = 0x01,              // mp was read
    RF_GAMBITS_KNOWN = 0x02,   // the gambits bit was read
    RF_GAMBITS_ON = 0x04,
    RF_ACTING = 0x08,          // the actor performs a battle action (action below the table's bound)
    RF_CHOOSING = 0x10,        // Player 1's stock battle menu is open for this member (party_rows EXTRA_CHOOSING)
};

struct row {
    uint16_t member;           // party id (hud::snapfacts MEMBER_NONE = unused), the snapshot row's
    uint16_t action;           // the action performed (0xFFFF none)
    int16_t mp;                // MP_UNKNOWN when not read
    int16_t mp_max;            // -1: no source
    uint8_t flags;             // row_flag
    uint8_t gauge;             // 0..100 or GAUGE_UNKNOWN
    uint8_t reserved[2];
    uint32_t ghost;            // the damage ghost's HP (>= the snapshot's HP; equal = no ghost)
    char name[NAME];
    char action_name[NAME];    // empty for Attack (the overlay writes "Attack") and when none
};
static_assert(sizeof(row) == 64, "row layout (host tests read it)");

struct record {
    patch::record_head head;   // epoch = session epoch, generation = build counter
    uint64_t built_ms;
    uint8_t row_count;         // = the snapshot's member_count
    uint8_t reserved_w[2];     // zero (the removed Wait line's freeze flags and holders)
    uint8_t chain_level;       // 0..3
    uint16_t chain;            // the chain counter as the game shows it (0 none)
    uint8_t chain_known;       // 1 = the chain rows were seen stock and the globals read
    uint8_t reserved;
    row rows[ROWS];
};
static_assert(sizeof(record) == 32 + ROWS * sizeof(row), "record layout");
static_assert(sizeof(record) <= 512, "a record_slot copy");

// ---- pure rules -----------------------------------------------------------------------------------------------------
// The action gauge in percent as the native party row fills it (PH-V2..PH-V4), GAUGE_UNKNOWN without an action.
uint8_t gauge_percent(bool action_set, uint16_t action, uint8_t state, int32_t charge, int32_t need);

// ---- module ---------------------------------------------------------------------------------------------------------
void start(uintptr_t image_base);
void step(uint64_t now_ms);    // co-op thread, after battle_log::step
void stop();                   // withdraw
bool read(record& out);        // any thread, wait-free; false if none, withdrawn or torn

struct counters {
    volatile uint64_t built;
    volatile uint64_t withdrawn;
    volatile uint64_t name_misses;
};
const counters* stats();

// ---- host tests -------------------------------------------------------------------------------------------------------
void test_reset();
void test_publish(const record& r);
// Builds the record from this snapshot now (the names through the given lookups: nullptr = the live cache).
bool test_build(const hud::hud_snapshot& snap, uint64_t now_ms, record* out);
void test_force_pins(int32_t ok);   // -1: compare the PH-V rows; 0 / 1 forced

}  // namespace ilc::overlay::feed
