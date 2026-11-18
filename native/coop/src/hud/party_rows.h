// Ivalice Legacy co-op module: the party list row additions (the frame driver's party-extras pass).
//
// The native party list stays the game's own (reduced by relayout.h; only the leader row keeps the native blue band).
// This element adds, anchored to each native row:
//   * a thin edge in the row's colour and the seat numeral (1-4; G for a gambit ally, dimmed and struck through when
//     its gambits are off);
//   * a damage ghost on the HP gauge: the HP lost in the last GHOST_HOLD_MS stays visible, then drains in
//     GHOST_DRAIN_MS (smoothstep, the native gauge ease);
//   * the timed statuses with the time left, with the native icons (partytop_4_c page 3, cell = status bit);
//   * the action line extension: "Choosing..." while Player 1 has the battle menu open for the row and no guest window
//     is open (the label sits where guest windows slide out: layout.h). Never the action's target: the target lines
//     show who targets what (Marco, 2026-11-16);
//   * every seat's party-target cursor on the row its target page points at (the native hand, in seat colour);
//   * the low-HP glow: the native HP number drawn red and pulsing, through the game's own digit colour argument (two
//     call rows, HD-02 and HD-03), not an overlay.
//
// Where things are drawn. The party list's rows are windows the game lays out from the field MRP: row k of n is at
// y = G40.y - gap * (n - 1 - k) (bottom-anchored), in layout units. The anchor table maps those units into the
// canvas's space (the HUD root, the 1920x1080 authoring space [U]) with one scale and the row's name origin,
// for the stock and the reduced layout. Which one applies follows relayout's state; any layout we do not recognise
// draws nothing (fail closed: the native list is unchanged).
//
// Threads. The co-op thread samples what the HudSnapshot does not carry (gambits on, the action each row performs,
// which row each seat is choosing for, the resolved row and seat colours, the layout) into a small record published
// through a seqlock (step). The game thread's pass reads the snapshot and that record only, never game memory. The
// HP colour handlers run on the game thread inside the party row update and touch only the row the game handed them.
//
// The element switch (set_enabled, the Battle HUD page wires it; on by default) and RENDER both off = the pass draws
// nothing and both call rows run stock.
#pragma once

#include <stdint.h>

#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/snapshot.h"
#include "ilc/bridge_abi.h"
#include "patch/patch_engine.h"

namespace ilc::hud::party_rows {

// ---- row geometry ---------------------------------------------------------------------------------------------------
enum geometry_id : uint8_t {
    GEOMETRY_STOCK = 0,     // the game's own party list
    GEOMETRY_REDUCED = 1,   // the reduced list (relayout.h, ours or another writer's identical values)
    GEOMETRY_COUNT,
    GEOMETRY_NONE = 0xFF,   // not a layout we know: nothing is drawn
};

// Maps relayout's layout_state to the geometry the rows use (GEOMETRY_NONE when no layout we know is live).
uint8_t geometry_of(uint32_t layout_state);

// One row's anchors in canvas units.
struct anchor {
    int16_t top;            // the row window's top
    int16_t pitch;          // row to row
    int16_t name_x;         // the member name's left edge (the native party-target cursor's tip x)
    int16_t name_y;
    int16_t edge_x, edge_y, edge_w, edge_h;        // the seat-colour edge
    int16_t numeral_x, numeral_y;                  // the seat numeral (text origin)
    uint16_t numeral_scale;                        // text size x100
    int16_t gauge_x, gauge_y, gauge_w, gauge_h;    // the HP gauge (G40 E6)
    int16_t status_x, status_y;                    // the first status icon
    int16_t status_w, status_h, status_stride;     // icon size and slot pitch
    int16_t timer_dx, timer_dy;                    // time-left text, relative to its icon
    uint16_t timer_scale;
    int16_t label_x, label_y;                      // the native ACTION label (G50 E1): "Choosing..."
    uint16_t label_scale;
    int16_t suffix_x, suffix_y;                    // after the native action gauge (G50 E0): kept, nothing drawn there
    uint16_t suffix_scale;
    int16_t cursor_x, cursor_y;                    // the party-target hand: top-left of the sprite (tip on the right)
    int16_t cursor_w, cursor_h;
};
// Row `index` of `rows` (1..4) for a geometry; false for a bad argument or GEOMETRY_NONE.
bool row_anchor(uint8_t geometry, uint32_t index, uint32_t rows, anchor* out);
// The layout-unit y of that row (G40.y - gap * (rows - 1 - index)), and its canvas y.
int32_t row_layout_y(uint8_t geometry, uint32_t index, uint32_t rows);
int32_t canvas_y(uint8_t geometry, int32_t layout_y);

// ---- rules (pure) -------------------------------------------------------------------------------------------------
constexpr uint32_t STATUS_HP_CRITICAL = 1u << 29;
constexpr uint32_t STATUS_BUBBLE = 1u << 28;
constexpr uint32_t STATUS_KO = 1u << 0;
constexpr uint32_t STATUS_PETRIFY = 2;    // status ids holding a step counter instead of ticks
constexpr uint32_t STATUS_DOOM = 6;
constexpr uint32_t MAX_STATUS_ICONS = 2;  // per row (the room right of the native MP number)
constexpr uint32_t DIM_LAST_S = 5;        // a beneficial status dims in its last seconds

// Low HP: the game's own HP Critical status (bit 29), or HP below a fifth of the maximum (the game's own test:
// maximum doubled under Bubble, integer division). Never at 0 HP or KO (the row shows KO natively).
bool low_hp(uint32_t hp, uint32_t hp_max, uint32_t status);

// The glow colour at a time: red, pulsing between full and GLOW_LOW_ALPHA over GLOW_PERIOD_MS (packed rgba, 0x80 = 1.0).
constexpr uint64_t GLOW_PERIOD_MS = 1400;
constexpr uint8_t GLOW_LOW_ALPHA = 0x4C;   // 0.6 at the trough
uint32_t glow_rgba(uint64_t now_ms);

// Time left of a timed status as text: seconds (ticks / (300 * tick_scale), rounded up) as "12", "1:05" or "12m"
// (99m at most); Doom and Petrify hold a step counter, shown as the game's own overhead count (value - 1). Returns
// the length, 0 when nothing should be shown (no ticks, tick scale unknown, bad argument).
uint32_t format_timer(int32_t ticks, uint32_t tick_scale, uint32_t status_id, char* out, uint32_t cap);
// Seconds left (rounded up), -1 when unknown.
int32_t seconds_left(int32_t ticks, uint32_t tick_scale);
bool beneficial(uint32_t status_id);
// The status icons a row shows: the statuses counting down (the snapshot keeps a duration only for those), beneficial
// first, then the soonest to run out (unknown last), then by bit; the first `cap`.
struct status_pick {
    uint8_t id;
    uint8_t reserved[3];
    int32_t seconds;        // seconds left (Doom/Petrify: the step count), -1 unknown
};
uint32_t pick_statuses(const hud_unit& u, uint32_t tick_scale, status_pick* out, uint32_t cap);

// The seat numeral: '1'..'4' for a seat, 'G' for a gambit ally, 0 for nothing.
char seat_glyph(uint8_t seat, bool party_member);

// Row and seat colours. colour_index: ilc_colour, or COLOUR_NONE.
constexpr uint8_t COLOUR_NONE = 0xFF;
// The colour a row's character takes: by seat (colour mode PLAYER) or by character (CHARACTER: the regular's own
// colour; a guest or an Esper falls back to its seat's). Unseated rows have none (gambit allies are silver).
uint8_t row_colour(uint8_t colour_mode, const uint8_t seat_colours[ILC_SEATS], const uint8_t character_colours[6],
                   uint8_t seat, uint16_t member);
rgba colour_rgba(uint8_t colour_index);   // the seat palette (0x80 scale); COLOUR_NONE = the gambit silver

// The damage ghost: per row, the HP shown behind the gauge's fill.
constexpr uint64_t GHOST_HOLD_MS = 1200;
constexpr uint64_t GHOST_DRAIN_MS = 400;
struct ghost {
    uint16_t member;        // 0xFFFF = empty
    uint16_t reserved;
    uint32_t hp_max;
    uint32_t hp;            // the last HP seen
    uint32_t from;          // the ghost's HP when it started to drain (or the HP before the hit while held)
    uint64_t hit_ms;        // the last hit
};
// Advances the ghost with this frame's HP and returns the HP the ghost shows (>= hp; == hp: no ghost).
uint32_t ghost_step(ghost& g, uint16_t member, uint32_t hp, uint32_t hp_max, uint64_t now_ms);

// ---- the co-op thread's record ----------------------------------------------------------------------------------------
enum extra_flag : uint8_t {
    EXTRA_GAMBITS_ON = 0x01,        // the member record's gambits bit
    EXTRA_ACTING = 0x02,            // the actor performs a battle action (id below the action table's bound)
    EXTRA_CHOOSING = 0x04,          // Player 1 has its battle menu open for this member, no guest window open
    EXTRA_CHOOSING_TARGET = 0x08,   // ... on a target page
    EXTRA_READ = 0x10,              // the member record was read
};
struct row_extra {
    uint16_t member;        // matched against the snapshot row (MEMBER_NONE = unused)
    uint8_t flags;          // extra_flag
    uint8_t colour;         // ilc_colour of the row, COLOUR_NONE
    uint16_t action;        // the action performed (0xFFFF none)
    uint16_t reserved;
};
static_assert(sizeof(row_extra) == 8, "row_extra layout");

struct rows_record {
    patch::record_head head;            // epoch = session epoch
    uint64_t sampled_ms;
    uint8_t geometry;                   // geometry_id
    uint8_t enabled;                    // the element switch
    uint8_t reserved[2];
    uint8_t seat_colour[SNAP_SEATS];    // ilc_colour per seat (resolved by the colour mode), COLOUR_NONE
    row_extra rows[SNAP_MEMBERS];
};
static_assert(sizeof(rows_record) == 64, "rows_record layout");

// ---- module ---------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base);                    // the HP colour call rows' handlers (their pins checked)
void configure_from_settings(const ilc_settings& s);  // seat/character colours and the colour mode
void step(uint64_t now_ms);                           // co-op thread: sample and publish (after the snapshot)
void stop();                                          // withdraw
bool read(rows_record* out);                          // any thread, wait-free: the latest record (false: none/torn)
void set_enabled(bool on);                            // the element switch (every piece)
bool enabled();
// The pieces the Battle HUD page switches one by one (battle_hud.h). A piece that is off draws nothing; GLOW off
// means the HP colour rows run stock (a row still coloured is forced once more with the native colour).
enum part : uint32_t {
    PART_EDGE = 0x01,       // seat-colour edge and numeral
    PART_GLOW = 0x02,       // low-HP glow (HD-02/HD-03)
    PART_GHOST = 0x04,      // damage ghost (still tracked while off, so turning it on shows no old hit)
    PART_ACTION = 0x08,     // action line extension
    PART_TIMERS = 0x10,     // status timers
    PART_CURSORS = 0x20,    // the seats' party-target hands
    PART_ALL = 0x3F,
};
void set_parts(uint32_t parts);
uint32_t parts();

// The pass (game thread, driver.h): draws the row additions from the snapshot and the record.
void draw(canvas& cv, const driver::pass_input& in);

// The HP colour call rows (also called by host tests with a synthetic ctx).
//   HD-02: the party row update's call of the HP digits function (registers at its entry: rcx = row window,
//          edx = HP shown, r8d = status, rbx = the party-HUD record). On a low-HP row it forces the digits to be set
//          again this frame (row +0xE0, the HP cache, set to -1 around the call and put back after it) and arms the
//          colour for HD-03. A row it coloured that is no longer low is forced once more, with the native colour.
//   HD-03: that function's call of the digit setter (rcx = first digit node, r9d = colour, rdi = row): the colour
//          argument becomes the glow when HD-02 armed it for this row.
int32_t hp_call(patch::ctx* c, uint32_t epoch);
void hp_after(patch::ctx* c, uint32_t epoch);
int32_t digits_call(patch::ctx* c, uint32_t epoch);

struct counters {
    volatile uint64_t samples;         // records published
    volatile uint64_t drawn_frames;    // frames the pass drew something
    volatile uint64_t no_geometry;     // frames skipped: the layout is not one we know
    volatile uint64_t no_record;       // frames drawn without the record (torn, other epoch, stale)
    volatile uint64_t capped;          // commands the pass's own cap dropped
    volatile uint64_t forced;          // HP digit updates forced (glow, or a last native recolour)
    volatile uint64_t glowed;          // colour arguments replaced
    volatile uint64_t hp_declined;     // HP rows that ran stock (switch, RENDER, unreadable record)
};
const counters* stats();

struct status_view {
    uint8_t attached;
    uint8_t enabled;
    uint8_t hp_rows_hooked;            // HD-02 and HD-03 both hooked (no gate or optional skip)
    uint8_t geometry;                  // as last sampled
};
status_view status();

constexpr uint32_t PASS_MAX_COMMANDS = 40;   // the pass never emits more (4 rows + 4 cursors)
constexpr uint64_t RECORD_STALE_MS = 250;

// ---- host tests ---------------------------------------------------------------------------------------------------
void test_reset();
void test_publish(const rows_record& r);
bool test_read(rows_record* out);
void test_set_image_base(uintptr_t base);
void test_set_attached(bool on);
void test_force_layout_state(int32_t state);   // relayout's state as the sampler sees it (-1 = the live one)
int32_t test_row(uint32_t which);   // 0 = HD-02, 1 = HD-03
bool test_glow_pending(uint64_t* row, uint32_t* rgba);
uint32_t test_coloured_rows();
const ghost* test_ghosts();

}  // namespace ilc::hud::party_rows
