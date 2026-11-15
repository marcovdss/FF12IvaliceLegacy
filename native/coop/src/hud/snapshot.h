// Ivalice Legacy co-op module: the HUD snapshot (HudSnapshot), the one view of the game the HUD passes draw from.
//
// The co-op thread samples the game once per beat into a plain struct and publishes it through a seqlock; the frame
// driver (driver.h) copies it out at the frame hook and hands it to every element pass. The passes never walk game
// pointers: whatever they show is in this struct, so the same passes can later draw from a snapshot that came from
// elsewhere (the online seam) without knowing it.
//
// What is in it:
//  * party rows in the order the HUD shows them (the battle state's HUD layout), each with its party id, the seat
//    that holds it, HP and max HP, both status masks, the remaining ticks of every timed status, level, weakness
//    mask, the actor's handle and its current target;
//  * the leader;
//  * targets: every distinct actor a seat's menu points at on a target page and every actor a party member targets,
//    read the same way (foes included), with the Anti-Libra augment bit raw;
//  * per seat, the target its menu points at (or pointed at last) and since when, for "the last player who
//    targeted"; for P1, while the field target mode (the native Target button) holds focus on a target that resolves
//    and P1's menu is not on a target page, that aimed target instead (target_info_native.h), until the mode ends;
//  * Libra: whether an active party member has the Libra status (known only once the party was read);
//  * the status tick scale and the layout mode (couch; online is a seam: an enum value nothing selects yet).
//
// Coherence. Every record is read bracketed: masks and HP, then the durations and the rest, then masks and HP again;
// a record that moved under the read is read again (SAMPLE_TRIES) and otherwise left out, never mixed. The party
// header (HUD layout and leader) is read before and after the rows, with the same rule for the whole party. The
// published struct is a seqlock: a reader that raced a publish gets `false` and never a mix of two snapshots.
//
// Nothing here writes game memory or calls a game function; reads go through the fault-safe reader.
#pragma once

#include <stdint.h>

#include "patch/detour.h"

namespace ilc::hud {

// How the HUD is laid out: one shared TV (couch, phase 1), or each screen showing only its own player's menu and
// target (online). Online is a seam: the value exists so the passes take a mode, nothing selects it in this build.
enum layout_mode : uint8_t {
    LAYOUT_COUCH = 0,
    LAYOUT_ONLINE = 1,
    LAYOUT_COUNT
};

constexpr uint32_t SNAP_SEATS = 4;
constexpr uint32_t SNAP_MEMBERS = 4;      // the active party rows
constexpr uint32_t SNAP_TARGETS = 8;      // distinct targets kept (seat targets first, then party members' targets)
constexpr uint32_t SNAP_STATUS_IDS = 32;
constexpr uint64_t SNAPSHOT_STALE_MS = 250;   // the driver presents nothing from an older snapshot

enum snapshot_flag : uint32_t {
    SNAP_WORLD = 0x01,         // a valid world, co-op on: the HUD may be drawn
    SNAP_PARTY = 0x02,         // the party header and rows were read coherently
    SNAP_LIBRA_KNOWN = 0x04,   // the Libra bit below is meaningful
    SNAP_LIBRA = 0x08,         // an active party member has the Libra status
    SNAP_TICKS = 0x10,         // tick_scale was read
    SNAP_BOSS_GAUGE = 0x20,    // the native boss gauge is shown: the corner headers move down (layout.h)
};

enum unit_flag : uint8_t {
    UNIT_PRESENT = 0x01,       // the record was read coherently (HP, statuses, durations, level, weakness)
    UNIT_ACTOR = 0x02,         // the actor was found: handle and target are valid
    UNIT_LEADER = 0x04,        // the native leader
    UNIT_ANTI_LIBRA = 0x08,    // the Anti-Libra augment is set on the record (raw: Target Info applies the rule)
    UNIT_PARTY = 0x10,         // an active party member (a target that is an ally carries it too)
};

struct hud_unit {
    uint32_t handle;           // 0 = no actor
    uint16_t member;           // party id, MEMBER_NONE for a foe
    uint8_t flags;             // unit_flag
    uint8_t seat;              // 1..4 = the seat that holds this character, 0 none
    uint32_t hp;
    uint32_t hp_max;
    uint32_t status_timed;
    uint32_t status_lasting;
    uint32_t target;           // the actor's current target handle (0 none)
    uint8_t level;
    uint8_t weakness;
    uint8_t unit_type;         // low nibble of the unit block's type (targets), 0 for party rows
    uint8_t reserved;
    int32_t duration[SNAP_STATUS_IDS];   // remaining ticks of each timed status (0 when the bit is not timed)
};
static_assert(sizeof(hud_unit) == 160, "hud_unit layout");

struct seat_target {
    uint32_t handle;           // the seat's menu target (current, or the last one it pointed at), 0 none
    uint8_t group;             // its menu target group
    uint8_t on_page;           // 1 while the seat's menu is on a target page pointing at it
    uint16_t reserved;
    uint64_t since_ms;         // when the seat moved onto it
};
static_assert(sizeof(seat_target) == 16, "seat_target layout");

struct hud_snapshot {
    patch::record_head head;   // epoch = session epoch, flags = snapshot_flag, generation = sample counter
    uint64_t sampled_ms;
    uint8_t layout;            // layout_mode
    uint8_t member_count;      // rows used in `members` (HUD order)
    uint16_t leader;           // party id of the native leader (MEMBER_NONE)
    uint32_t tick_scale;       // status ticks: about 300 * tick_scale per second (0 unknown)
    hud_unit members[SNAP_MEMBERS];
    uint8_t target_count;
    uint8_t reserved[7];
    hud_unit targets[SNAP_TARGETS];
    seat_target seats[SNAP_SEATS];
};
static_assert(sizeof(hud_snapshot) == 2024, "hud_snapshot layout");

namespace snapshot {

// ---- co-op thread ---------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void step(uint64_t now_ms);    // sample and publish (or withdraw: no world, co-op off, RENDER off)
void stop();                   // withdraw

// The layout this session uses. Couch in this build; online is the seam.
uint8_t layout_for_session();
// Whether the native boss gauge is shown (the chain in snapshot_facts.h); false when anything is unreadable.
bool boss_gauge_shown(uintptr_t image_base);

// ---- publication (co-op thread writes, the frame hook reads) ------------------------------------------------------
void publish(const hud_snapshot& s);
void withdraw();
// Any thread, wait-free: false if none, withdrawn or torn (the caller may try again; never waits).
bool read(hud_snapshot& out);
// Cheap check without the copy: the epoch and the time of the last publish (false if none or withdrawn).
bool meta(uint32_t* epoch, uint64_t* sampled_ms);

struct counters {
    volatile uint64_t samples;        // snapshots published
    volatile uint64_t withdrawn;      // withdraws (no world, co-op off, RENDER off, stop)
    volatile uint64_t party_torn;     // the party header moved under every try: rows left out
    volatile uint64_t record_retries; // records read again because they moved
    volatile uint64_t records_torn;   // records left out after SAMPLE_TRIES
    volatile uint64_t targets_dropped;// distinct targets beyond SNAP_TARGETS
};
const counters* stats();

// ---- host tests ------------------------------------------------------------------------------------------------
void test_reset();
void test_set_image_base(uintptr_t image_base);
// The seat's menu target from the test instead of the co-op menus (handle 0 = none). enable 0 = the live menus.
void test_set_seat_target(uint32_t seat, uint32_t handle, uint8_t group, uint8_t on_page);
void test_use_seat_targets(bool on);
// Called between the two halves of each bracketed read (stage 1: the party header; stage 2: a record, with its
// address): a test moves the game under the sampler there.
using between_fn = void (*)(uint32_t stage, uint64_t address);
void test_set_between(between_fn fn);
// Builds a snapshot from the game now (as step does) without publishing it; false if there is no world.
bool test_sample(uint64_t now_ms, hud_snapshot* out);

}  // namespace snapshot
}  // namespace ilc::hud
