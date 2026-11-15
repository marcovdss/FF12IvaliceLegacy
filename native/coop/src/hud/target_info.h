// Ivalice Legacy co-op module: the expanded Target Info (top-left), drawn by the frame driver's Target Info pass.
//
// What it shows: one target, the one the last player who targeted aimed at (each seat's menu target and since when
// come from the HudSnapshot), edged in that seat's colour. Native FF12 style: outlined text straight on the world,
// no panel, its "TARGET INFO" header on the same line as the battle log's header at the top-right.
//
//     TARGET INFO ----------------------------------  [LIBRA]      header (corner style, shared with the log)
//     Lv 18  Hyena C                       BOSS                    level and name (red: 10+ levels above the party)
//     HP  1644 / 2782   59%                                        numbers and percentage need Libra
//     [=============ghost====       ]                              thin bar, damage ghost (always)
//     Weak [wind][ice]   Half [..]  Null [..]                      needs Libra; Half/Null once their source exists
//     Status [poison]14 [slow]6 [sleep]x                           always; immune ones greyed (need Libra)
//     DROPS ----------------------------------------
//     Fire Stone / ??? / Green Beret / Hi-Potion  steal            Hunter's Insight knowledge, not Libra-gated
//
// Libra gating: a party target always reveals; a foe reveals when an active party member has Libra, unless
// it has the Anti-Libra augment and the native Anti-Libra test (CD-01) is stock. Revealed = exact HP numbers and
// percentage, the Weak/Half/Null line and the immune statuses; the badge shows Libra (or the red X for Anti-Libra).
// Without it: name, level, the HP bar, the statuses on the foe and the drops.
//
// Drops follow Hunter's Insight: an item is named once the party obtained it from that bestiary entry (dropped,
// stolen or poached), else "???"; steals already taken from this foe are dimmed. The knowledge is HI's own save
// block, found through HI's code at CD-02; without HI the drops list is not shown. Item names come from the game's
// loaded name text, so Legacy's renamed items read correctly.
//
// Threads. The co-op thread composes a `view` once per beat (after the snapshot sampler) from the latest HudSnapshot
// and a few game reads for the target it shows (name, loot, steal state, status block, HI knowledge, item names),
// and publishes it in a seqlock slot. The pass, on the game thread inside the frame hook, copies the view and draws
// it: it never reads game memory, never calls a game function and never waits. The online seam: a view that came
// from elsewhere draws the same.
//
// The native TARGET INFO window. While ours is live (element on, co-op on, the pass drew within LIVE_MS and the
// co-op menu layer was presented within LIVE_MS), TI-01 turns every request to open the native window into its own
// close request, so it never opens (and Hunter's Insight's block, attached to it, with it). Otherwise TI-01 is
// stock: with RENDER disarmed, the element off or the module off, the native window opens again and Legacy's
// target_info_scale.lua scales it on its first open, as before.
#pragma once

#include <stdint.h>

#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/snapshot.h"
#include "patch/detour.h"

namespace ilc::hud::target_info {

// ---- the corner header (Target Info top-left, battle log top-right: one line, one style) -------------------------
// Authoring units of the HUD layout (1920x1080), scaled uniformly with the screen. The anchors are the native TARGET
// INFO header (G22 E14) at Legacy's 0.75 window scale: x = -20 * 0.75 + 82 * 0.75, y = 38 * 0.75 + 28 * 0.75.
namespace corner {
constexpr int16_t AUTHOR_W = 1920;
constexpr int16_t AUTHOR_H = 1080;
constexpr int16_t MARGIN_X = 47;        // from the left edge (TI) and from the right edge (log)
constexpr int16_t HEADER_Y = 50;        // top of the header text on both sides
constexpr uint16_t HEADER_SCALE = 75;   // the native header text (scale 100) at 0.75
constexpr int16_t RULE_DY = 20;         // the rule under the header text
constexpr uint16_t RULE_W = 420;
constexpr uint16_t RULE_H = 2;
constexpr uint16_t ADVANCE_X100 = 1400; // estimated glyph advance at scale 100, x100 (no text metrics: [U])

enum side : uint8_t { LEFT = 0, RIGHT = 1 };

struct box {
    int32_t x, y, w, h;
};
// The header's box (text top to rule bottom) in authoring units, or on a screen of the given size (uniform scale,
// letterboxed to 16:9 the way the HUD layout is).
box header_box(side s);
box header_box_on(side s, uint32_t screen_w, uint32_t screen_h);
// Estimated width of `chars` characters at `scale` (x100), authoring units.
int32_t text_width(uint32_t chars, uint16_t scale);
// The header: label text and the thin rule under it, on its side. alpha 0..0x80.
void header(canvas& cv, side s, int16_t y, const char* label, uint8_t alpha);
}  // namespace corner

// ---- the view (co-op thread -> pass) ------------------------------------------------------------------------------
constexpr uint32_t NAME_MAX = 32;          // bytes incl. NUL
constexpr uint32_t ITEM_TEXT = 24;         // bytes incl. NUL
constexpr uint32_t DROP_LINES = 8;
constexpr uint32_t STATUS_SLOTS = 10;
constexpr uint64_t LIVE_MS = 250;          // the pass (and the menu layer) presented within this: native TI kept closed
constexpr uint64_t KO_FADE_MS = 300;
constexpr uint64_t GONE_HOLD_MS = 750;     // a target that left the snapshot stays this long, then fades
constexpr uint64_t GHOST_HOLD_MS = 250;    // the damage ghost holds, then eases down
constexpr uint64_t GHOST_EASE_MS = 400;
constexpr uint32_t DANGER_LEVELS = 10;     // a foe this many levels above the party's highest has a red name
constexpr uint32_t MAX_COMMANDS_TI = 72;   // the pass's share of the frame's commands

enum view_flag : uint32_t {
    VIEW_SHOWN = 0x0001,       // a target is shown
    VIEW_PARTY = 0x0002,       // the target is a party member
    VIEW_REVEAL = 0x0004,      // Libra-gated rows may show
    VIEW_LIBRA = 0x0008,       // revealed by Libra (the badge)
    VIEW_ANTI_LIBRA = 0x0010,  // Anti-Libra hides the gated rows (the red X)
    VIEW_NAME = 0x0020,        // the name was read
    VIEW_DROPS = 0x0040,       // the knowledge source was found: the drops list shows
    VIEW_DANGER = 0x0080,      // red name (dangerous foe)
    VIEW_BOSS = 0x0100,        // BOSS mark (seam: no source yet, never set)
    VIEW_FAMILY = 0x0200,      // family line (seam: no source yet, never set)
    VIEW_ELEMENTS_EXT = 0x0400,// Half/Null masks known (seam: no source yet, never set)
    VIEW_IMMUNE = 0x0800,      // the status block mask was read
    VIEW_KO = 0x1000,          // KO: fading out
    VIEW_GONE = 0x2000,        // left the snapshot: held, then fading
    VIEW_TIMERS = 0x4000,      // the tick scale is known: status seconds are meaningful
    VIEW_HP = 0x8000,          // HP was read (the bar shows)
};

enum drop_kind : uint8_t { DROP = 0, STEAL = 1, SPECIAL = 2, POACH = 3 };
enum drop_state : uint8_t { ITEM_KNOWN = 0, ITEM_UNKNOWN = 1 };
enum drop_flag : uint8_t { DROP_TAKEN = 0x01, DROP_RARE = 0x02, DROP_GIL = 0x04 };

struct drop_line {
    uint8_t kind;              // drop_kind
    uint8_t state;             // drop_state
    uint8_t slot;
    uint8_t flags;             // drop_flag
    char text[ITEM_TEXT];      // ASCII; "???" when unknown
};
static_assert(sizeof(drop_line) == 28, "drop_line layout");

enum status_flag : uint8_t { STATUS_ACTIVE = 0x01, STATUS_IMMUNE = 0x02, STATUS_TIMED = 0x04 };

struct view {
    patch::record_head head;   // epoch = the snapshot's; flags = view_flag; generation = build counter
    uint64_t built_ms;
    uint64_t fade_from_ms;     // KO or gone: when the fade (or the hold before it) started; 0 = none
    uint32_t handle;
    uint8_t seat;              // 1..4: the seat whose target this is (its colour edges the block); 0 none
    uint8_t level;
    uint8_t weakness;          // element bits (battle_4_c 10.0 cell order)
    uint8_t half;              // seam
    uint8_t null_;             // seam
    uint8_t status_count;
    uint8_t drop_count;
    uint8_t reserved;
    uint32_t seat_colour;      // packed rgba of that seat (0x80 = 1.0)
    uint32_t hp;
    uint32_t hp_max;
    uint32_t ghost;            // damage ghost HP (>= hp)
    uint8_t status_id[STATUS_SLOTS];
    uint8_t status_flags[STATUS_SLOTS];
    uint16_t status_secs[STATUS_SLOTS];   // seconds left (STATUS_TIMED), else 0
    char name[NAME_MAX];
    drop_line drops[DROP_LINES];
};
static_assert(sizeof(view) <= 512, "the pass copies a view onto the game thread's stack");

// ---- pure rules (tested directly) -----------------------------------------------------------------------------------
// The seat whose target the shared TI shows: the seat that moved onto a target most recently (since_ms), whose
// target is still in the snapshot; ties go to the lower seat. Returns the seat index 0..3 and the target's index in
// snap.targets, or -1.
int32_t select_seat(const hud_snapshot& snap, int32_t* target_index);

struct gate_in {
    uint8_t party;             // the target is a party member
    uint8_t libra_known;
    uint8_t libra;             // an active party member has Libra
    uint8_t anti_libra;        // the target carries the Anti-Libra augment
    uint8_t anti_libra_stock;  // the native Anti-Libra test is stock (not patched out)
};
// view_flag bits: VIEW_REVEAL, VIEW_LIBRA, VIEW_ANTI_LIBRA.
uint32_t gate(const gate_in& in);

// Damage ghost: holds GHOST_HOLD_MS after HP dropped, then eases to HP over GHOST_EASE_MS; rises with HP at once.
struct ghost_state {
    uint32_t handle;
    uint32_t ghost;            // the ghost's value
    uint32_t from;             // the value the ease started from
    uint32_t last_hp;
    uint64_t hit_ms;           // the last drop
};
uint32_t ghost_step(ghost_state* g, uint32_t handle, uint32_t hp, uint64_t now);

// Drops from the loot record (LOOT_SIZE bytes), the HI knowledge bytes (pages 0..3; null = not found), the steal
// mask and a name lookup. Order: drops, steals, special drops, poaches; empty slots left out; at most DROP_LINES.
using name_fn = bool (*)(void* ctx, uint16_t item, char* out, uint32_t cap);
uint32_t build_drops(const uint8_t* loot, const uint8_t* known, uint16_t steal_left, name_fn names, void* ctx,
                     drop_line* out, uint32_t cap);

// Game text (single-byte encoding) -> ASCII the canvas can encode. Control tokens skipped, unknown glyphs '?',
// newlines as spaces, trimmed. Returns the length written (NUL-terminated within cap).
uint32_t decode_text(const uint8_t* text, uint32_t len, char* out, uint32_t cap);

// The status row: active statuses (timed or lasting, KO, HP Critical and Libra left out) by status id, then (when
// revealed) the immune ones not active, greyed; at most STATUS_SLOTS. seconds = ticks / (300 * tick_scale).
uint32_t build_statuses(const hud_unit& u, uint32_t block, bool show_immune, uint32_t tick_scale, uint8_t* ids,
                        uint8_t* flags, uint16_t* secs);

// Alpha (0..0x80) of the block at `now` from its fade state.
uint8_t fade_alpha(const view& v, uint64_t now);

// Draws the view into the canvas (the pass's body; pure: the view and now only). Returns the commands added.
// dy: the corner headers' drop (layout::header_dy: the boss gauge), applied to everything it draws.
uint32_t compose(canvas& cv, const view& v, uint64_t now, int16_t dy = 0);

// TI-01: whether the native window's open request becomes a close request now.
struct native_in {
    uint8_t enabled;           // the element switch
    uint8_t coop_on;           // co-op on (not after the kill switch)
    uint8_t attached;          // the row's handler was set
    uint8_t p1_owned;          // the co-op menus own P1 (firewall gate armed). Clear: P1 may be on the stock battle
                               // menu, whose target list needs the native window
    uint64_t now;
    uint64_t last_drawn;       // the pass last ran with a view of this epoch (0 never)
    uint64_t last_stamp;       // the menu layer was last presented (0 never)
};
bool hide_native(const native_in& in);
// The same predicate with the module's own inputs now (TI-01's handler and the native window's give-back,
// target_info_native.h, both decide with it).
bool native_hidden_now(uint64_t now);

// ---- the module side ------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base);   // TI-01's handler; false (row stays stock) if the row is missing
void step(uint64_t now_ms);          // co-op thread, after hud::snapshot::step
void stop();                         // withdraw the view
void set_enabled(bool on);           // the element switch (the Battle HUD page); on by default
bool enabled();

// The frame driver's Target Info pass (game thread).
void draw(canvas& cv, const driver::pass_input& in);

// TI-01's handler (also called by host tests with a synthetic ctx).
int32_t ti01(patch::ctx* c, uint32_t epoch);

// An item's name from the game's loaded text (co-op thread; the sampler's own path). Shared with the battle log.
bool item_name_now(uint16_t item, char* out, uint32_t cap);

// ---- views ------------------------------------------------------------------------------------------------------
bool latest(view& out);              // any thread, wait-free

enum hi_state : uint8_t {
    HI_UNKNOWN = 0,                  // not looked yet
    HI_ABSENT = 1,                   // CD-02 is stock: HI is not running
    HI_FOREIGN = 2,                  // CD-02 rewritten, but not into a block we recognise
    HI_FOUND = 3,
};

struct counters {
    volatile uint64_t built;         // views published with a target
    volatile uint64_t empty;         // views published with nothing to show
    volatile uint64_t withdrawn;
    volatile uint64_t drawn;         // pass frames that drew a target
    volatile uint64_t skipped_epoch; // pass frames whose view was of another epoch, torn or absent
    volatile uint64_t native_closed; // TI-01 calls turned into the close request
    volatile uint64_t native_stock;  // TI-01 calls left alone
    volatile uint64_t name_misses;   // item ids without a name
    volatile uint64_t hi_scans;      // HI discoveries tried
};
const counters* stats();

struct status_view {
    uint8_t attached;
    uint8_t enabled;
    uint8_t hi;                      // hi_state
    uint8_t text_ready;              // the name text and item tables were usable at the last lookup
    uint8_t anti_libra_stock;        // CD-01 stock at the last read
    uint8_t reserved[3];
};
status_view status();

// ---- host tests ---------------------------------------------------------------------------------------------------
void test_reset();
void test_set_image_base(uintptr_t image_base);
void test_set_attached(bool on);
// Compose and publish a view from this snapshot now (the co-op thread's step without reading the published one).
bool test_build(const hud_snapshot& snap, uint64_t now_ms, view* out);
void test_publish(const view& v);
void test_set_last(uint64_t drawn_ms, uint64_t stamp_ms);
void test_set_p1_owned(int32_t owned);   // -1: the firewall record; 0 / 1 forced
// Item name lookup through the game's text (the sampler's own path).
bool test_item_name(uint16_t item, char* out, uint32_t cap);
// HI discovery now; returns hi_state and the save block address.
uint32_t test_find_hi(uint64_t* save_out, uint64_t* settings_out);
// Writes a code block with the two shapes HI discovery reads (1 = written and read back by the same readers).
constexpr uint32_t TEST_HI_SAVE_AT = 0x1A;
constexpr uint32_t TEST_HI_FLAGS_AT = 0x80;
uint32_t test_hi_code(uint8_t* out, uint32_t cap, uint64_t save, int32_t flag_disp);

}  // namespace ilc::hud::target_info
