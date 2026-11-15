// Ivalice Legacy co-op module: the AoE-only ring rule (a native area ring per aiming seat, never for Attack).
//
// The game's area ring is a node in the UI's area list ([ui +0x10f40], one node per target, key +0x6c = target
// handle, +0x48 CLUT, +0x50 bit 0 live). Natively it appears only while the stock target list hovers a target:
// 0x2789f0 calls 0x27dc10(caster, target, action, flag), which releases every other ring (0x290cd0), asks
// 0x31ab80(caster, target, action, &info) for the action's area (info +0 type, +8 kind, +0x10 radius) and, for kind 1
// (a circle) with the flag clear and a live target, calls 0x28ea20(target, type, radius) -> 0x2911d0, which creates or
// updates the target's node and returns it. The ring stays until something releases it or the target stops being
// valid (the per-frame tick 0x3f30d0).
//
// The rule (the HUD design): a ring shows only while a seat aims an area-of-effect magick or technick, to show its
// reach. In this module that is: the seat's co-op menu is on a target page with a pending action that
//   * came from a magick school page or the Technicks page (Attack, Quickenings, Summons and items never do),
//   * is not Attack (150) — Legacy's Attack Area Experiment gives Attack a radius-4 circle for every weapon; a
//     greatsword cleave is not an aimed area (the cleave gate; attack_area_filter.lua is untouched),
//   * has area kind 1 in its action row (0x31a990's rule: radius 0 or bit 12 -> none/5, bit 24 -> cone 3, bit 25 -> 4,
//     bit 21 -> 2, else 1), and the game's own 0x31ab80 agrees when the ring is made,
// and the ring sits on the target that page points at. One ring per seat; two seats on one target share the game's one
// node (the most recent seat's colour wins). The ring takes the seat's colour (+0x48, the same palette index as the
// seat's target lines) and goes when the page closes, the action or target changes, or the element is switched off.
//
// Who does what. The co-op thread computes each seat's wanted ring and publishes it (a small record); while anything
// is wanted or still applied it pushes a game-thread job (OP_HUD_RINGS, one in flight) that makes the game state match
// the record: releases rings no seat wants any more (only nodes keyed by a target we applied), and creates or refreshes
// the wanted ones through the game's own functions. The UT-01 tick gives every applied ring back (released) on the first
// frame it is no longer wanted (F8, TARGETS_CARDS off, DRY, the element switch). The stock target list (only when the co-op
// menus are not presented) keeps its native behaviour except for the cleave gate: RG-01, a call row on 0x2789f0,
// passes the "no ring" flag for action 150 (0x27dc10 then still releases the other rings, as for any non-area action).
// Subsystem: TARGETS_CARDS ("target rings, TARGET INFO and cards").
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/detour.h"
#include "patch/patch_engine.h"
#include "runtime/game_thread.h"

namespace ilc::hud::rings {

// ---- game facts (VAs at the pinned base 0x120000) ----------------------------------------------------------------------
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t SITE_RING_TRIGGER = 0x2789F0;   // RG-01: `call 0x27dc10` in the stock target list
constexpr uint64_t FN_RING_TRIGGER = 0x27DC10;     // (caster, target, action, flag)
constexpr uint64_t FN_AREA_INFO = 0x31AB80;        // int (caster, target, action, info*): < 0 = no info
constexpr uint64_t FN_TARGET_LIVE = 0x273710;      // int (handle): alive, kind 1, no KO/Stone
constexpr uint64_t FN_RING_CREATE = 0x28EA20;      // node* (target, type, float radius) -> 0x2911d0
constexpr uint64_t UI_PTR = 0x209BE80;             // VA: pointer to the UI manager
constexpr uint32_t UI_AREA_LIST = 0x10F40;         // first area-ring node
constexpr uint32_t RING_CLUT = 0x48;               // u32
constexpr uint32_t RING_FLAGS = 0x50;              // u32: bit 0 live (cleared = fades out and is freed)
constexpr uint32_t RING_KEY = 0x6C;                // u32 target handle (0 once released)
constexpr uint32_t RING_NEXT = 0x70;
constexpr uint32_t RING_WALK_MAX = 64;
constexpr uint32_t INFO_TYPE = 0x00;               // area info (0x18 bytes): u32 line type
constexpr uint32_t INFO_KIND = 0x08;               // u32 area kind
constexpr uint32_t INFO_RADIUS = 0x10;             // f32 radius
constexpr uint32_t INFO_BYTES = 0x40;              // what we hand it (it writes 0x18)
constexpr uint32_t ROW_AREA = 0x06;                // action row: u8 radius
constexpr uint32_t ROW_FLAGS = 0x0C;               // u32 flags (bits 1, 12, 21, 24, 25 decide the area kind)
constexpr uint32_t STOCK_NO_RING = 2;              // 0x27dc10's flag argument: set = no ring (the row flag &2)

constexpr uint16_t ACTION_ATTACK = 150;
constexpr uint16_t OP_HUD_RINGS = 27;              // game-thread operation number (runtime/game_thread.h table)
constexpr uint32_t JOB_TTL_MS = 250;
constexpr uint64_t REFRESH_MS = 300;               // while a ring is wanted, the job re-asserts it this often
constexpr uint32_t SEATS = 4;

// ---- the rule (pure) -----------------------------------------------------------------------------------------------------
enum area_kind_id : uint8_t {
    AREA_NONE = 0,
    AREA_CIRCLE = 1,     // the only kind that gets a ring
    AREA_CIRCLE_ALT = 2, // bit 21
    AREA_CONE = 3,       // bit 24 (radius, arc)
    AREA_KIND_4 = 4,     // bit 25
    AREA_KIND_5 = 5,     // no radius (or bit 12) with bit 1
};
// 0x31a990's rule on an action row's radius byte and flags.
uint8_t area_kind(uint8_t radius, uint32_t flags);

enum family_id : uint8_t {
    FAMILY_OTHER = 0,    // items, Quickenings, Summons, anything else
    FAMILY_MAGICK = 1,
    FAMILY_TECHNICK = 2,
    FAMILY_ATTACK = 3,
};
// The family of the co-op menu page an action was chosen on (menu::page_id).
uint8_t family_of_page(uint8_t page);
// Ring or not: a magick or technick, not Attack, area kind 1.
bool ring_allowed(uint16_t action, uint8_t family, uint8_t kind);
// The stock target list (RG-01): everything native except Attack.
bool stock_ring_allowed(uint16_t action);

struct seat_ring {
    uint32_t caster;     // the seat's character handle
    uint32_t target;     // 0 = no ring wanted
    uint16_t action;
    uint8_t clut;        // the seat's palette
    uint8_t order;       // 0..3: the seats sorted by when they aimed (higher = more recent, applied last)
};
static_assert(sizeof(seat_ring) == 12, "seat_ring layout (host tests build it)");

struct rings_record {
    patch::record_head head;   // epoch = session epoch
    uint8_t enabled;           // the element switch
    uint8_t reserved[7];
    seat_ring seats[SEATS];
};
static_assert(sizeof(rings_record) == 72, "rings_record layout (host tests build it)");

// One seat's wanted ring from what its menu shows (pure; the co-op thread feeds it).
struct seat_aim {
    uint8_t held;          // the seat holds a character with an actor handle
    uint8_t mode;          // menu::menu_mode
    uint8_t page;          // the list page the pending action came from (menu::page_id)
    uint8_t pending_kind;  // menu::entry_kind of the pending entry
    uint16_t action;
    uint8_t area_kind;     // from the action row (AREA_NONE if unread)
    uint8_t on_page;       // the snapshot's seat target is on its target page
    uint32_t caster;
    uint32_t target;
};
static_assert(sizeof(seat_aim) == 16, "seat_aim layout (host tests build it)");
// The target the seat's ring sits on, or 0.
uint32_t wanted_target(const seat_aim& a);

// ---- the game functions the job calls (relocated, or the host test's doubles) ----------------------------------------------
struct game_fns {
    int32_t (*area_info)(uint32_t caster, uint32_t target, uint32_t action, void* info);
    int32_t (*target_live)(uint32_t target);
    uint64_t (*create)(uint32_t target, uint32_t type, float radius);
};

// ---- module ---------------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base);                    // the operation (functions' first bytes checked) and RG-01's handler
void configure_from_settings(const ilc_settings& s);  // seat/character colours and the colour mode
void step(uint64_t now_ms);                           // co-op thread (after the snapshot and the frontend)
void stop();
void tick(bool live);                                 // UT-01, game thread: give the rings back when not wanted
void set_enabled(bool on);                            // the element switch (on by default)
bool enabled();

int32_t trigger_call(patch::ctx* c, uint32_t epoch);  // RG-01 (registers at 0x27dc10's entry)
// The operation's body (host tests call it directly): 1 = matched the record, 2 = nothing wanted (all released).
int32_t run_job(uint64_t ui_manager, uint32_t epoch);

struct counters {
    volatile uint64_t samples;
    volatile uint64_t pushed;
    volatile uint64_t push_refused;
    volatile uint64_t jobs;            // operations run
    volatile uint64_t created;         // rings created or refreshed by the game's factory
    volatile uint64_t refused;         // wanted rings the game refused (no info, kind != 1, target not live, no node)
    volatile uint64_t released;        // nodes we released
    volatile uint64_t give_backs;      // ticks that released every applied ring
    volatile uint64_t cleave_gated;    // stock Attack hovers given the no-ring flag
};
const counters* stats();

struct applied_ring {
    uint32_t target;     // 0 = none
    uint8_t clut;
    uint8_t reserved[3];
};
struct status_view {
    uint8_t attached;    // bit 0 the operation, bit 1 RG-01
    uint8_t enabled;
    uint8_t applied;     // rings currently applied
    uint8_t reserved;
    int32_t last_job;
};
status_view status();

// ---- host tests -----------------------------------------------------------------------------------------------------------
void test_reset();
void test_publish(const rings_record& r);
bool test_read(rings_record* out);
void test_set_image_base(uintptr_t base);
void test_set_fns(const game_fns* fns);
void test_set_attached(uint32_t mask);
void test_force_live(int32_t live);   // -1 = the real TARGETS_CARDS state
const applied_ring* test_applied();   // [SEATS]
uint32_t test_release(uint64_t ui_manager, uint32_t target);
void test_set_aims(const seat_aim* aims);   // nullptr = the live menus
int32_t test_row();

}  // namespace ilc::hud::rings
