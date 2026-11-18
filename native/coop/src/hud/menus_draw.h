// Ivalice Legacy co-op module: the co-op battle menus drawn in the game's own window style (the HUD menu pass).
//
// Player 1's battle menu is a co-op menu (the stock command window is gated), so it is drawn here too, at the native
// command window's place: the field layout's group 14 (header, list origin) and group 11 (a strip near it, used for
// the help line; the game's own top-left help line is group 7), with the game's
// own blue glass (bt_win_0_c), cursor hand (cursol_4_p), selection bar (seat colour), scroll arrows, gambit ON/OFF
// plate and the target footer's shoulder-button icons. A guest's menu is the same window with a thin seat-colour edge
// on its left side; it slides out of the guest's own row of the party list, to the left of it, and the guests'
// windows are stacked in one column so no two windows ever overlap each other, Player 1's window and help strip, the
// party list, Target Info or the battle log.
//
// Two halves, two threads:
//  * co-op thread, step(): after the menus ran their beat (frontend::step) and the HUD snapshot was sampled, the menus
//    are turned into a `menu_view`: per seat what kind of window is up, its title, a window of rows around the cursor
//    with names already resolved from the game's own text (action names, character names, foe names with the A..Z
//    letter), greys, MP costs and item counts, the Gambits state, the target group footer, the help line, the seat
//    colour and when the window opened. The view is published through a seqlock. Names are read through the
//    fault-safe reader (never a game call).
//  * game thread, pass(): the frame driver's menu pass (last of all passes) copies the view out and draws it. The
//    drawing is a pure function of (view, HUD snapshot, frame time, layout mode): draw() reads nothing else.
//
// Coordinates are the HUD layout's 1920x1080 authoring units (the frame hook draws in the HUD root's space, which the
// game maps to the screen like its own windows), so every native position below holds at every resolution.
//
// Fail-safe: no view, a torn or stale view, a view of another epoch or a menu pass without room in the frame draws no
// menu (the frame driver still presents the layer; the menus are idle only when the driver says so). Room: the driver
// keeps the open windows' floor back while the earlier passes draw (floor_cost), and a window that is still left out is
// reported by seat (last_dropped -> presenter::note_dropped): that seat's menu takes no input and holds no Wait.
#pragma once

#include <stdint.h>

#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/snapshot.h"
#include "ilc/bridge_abi.h"
#include "menu/catalog.h"
#include "menu/gambits.h"
#include "menu/model.h"
#include "patch/detour.h"
#include "targets/targets.h"

namespace ilc::hud::menus_draw {

constexpr uint32_t SEATS = 4;
constexpr uint32_t VIEW_ROWS = 8;       // rows carried per window (a window around the cursor)
constexpr uint32_t ROW_TEXT = 24;       // characters per row name, NUL included
constexpr uint32_t ROW_VALUE = 8;       // MP cost or item count, NUL included
constexpr uint32_t TITLE_TEXT = 32;
constexpr uint32_t HELP_TEXT = 128;      // the whole help line (the footer carousels it, item 8); 64 cut long lines
constexpr uint64_t VIEW_STALE_MS = 250; // an older view is not drawn
constexpr uint64_t SLIDE_MS = 150;      // the native open table's duration (0x915a58: 150, offset -40)
constexpr int32_t SLIDE_DISTANCE = 40;  // ... and its offset, in authoring units

// The menu pass's share of the frame (the canvas is shared by every pass; the menus come last, so they fit what is
// left and never more than this).
constexpr uint32_t COMMAND_CAP = 120;
constexpr uint32_t TEXT_CAP = 1400;

// The floor of the open windows: what the frame share keeps of them just before it would leave a window out (no
// connector, no help glass or line, rows at their minimum). The frame driver keeps it back for the menu pass while
// the passes before it draw (canvas::reserve), so no window is ever dropped for room. Worst case, every seat open
// (Player 1 + three guests) with every optional piece: these bounds (checked against the cost model in
// menus_draw.cpp).
constexpr uint32_t FLOOR_COMMANDS_MAX = 113;
constexpr uint32_t FLOOR_TEXT_MAX = 738;
static_assert(FLOOR_COMMANDS_MAX <= COMMAND_CAP && COMMAND_CAP <= MAX_COMMANDS,
              "the menu share holds four floor windows, and fits the frame");
static_assert(FLOOR_TEXT_MAX <= TEXT_CAP && TEXT_CAP <= TEXT_POOL, "the menu text share holds four floor windows");

enum window_kind : uint8_t {
    WK_NONE = 0,
    WK_LIST = 1,           // a page of the battle menu
    WK_TARGET = 2,         // a target page (the pending action's candidates of one group)
    WK_SELECTOR = 3,       // Change Leader, Change Character or the KO recovery
};

enum row_flag : uint8_t {
    RF_GREY = 0x01,        // not usable (MP, status, seal, none left, not selectable)
    RF_PAGE = 0x02,        // opens a page
    RF_TOGGLE = 0x04,      // the Gambits row: the ON/OFF plate is drawn
    RF_ON = 0x08,          // ... gambits on
    RF_KNOWN = 0x10,       // ... the state was read
    RF_ISSUER = 0x20,      // a target row that is the seat's own character
};

struct view_row {
    char text[ROW_TEXT];   // ASCII
    char value[ROW_VALUE]; // ASCII, right-aligned when drawn ("" = none)
    uint8_t flags;         // row_flag
    uint8_t reserved[7];
};
static_assert(sizeof(view_row) == 40, "view_row layout (host tests read it)");

struct seat_view {
    uint8_t kind;          // window_kind
    uint8_t selector;      // frontend selector_kind while WK_SELECTOR
    uint8_t row_count;     // rows used in `rows`
    uint8_t cursor;        // index into `rows` of the highlighted row (0xFF none)
    uint16_t first;        // rows[0] is item `first` of the page ...
    uint16_t total;        // ... of `total` items (scroll arrows)
    uint16_t member;       // the seat's character (party id), targets::MEMBER_NONE unknown
    uint8_t page;          // menu::page_id (WK_LIST)
    uint8_t group;         // menu::target_group (WK_TARGET)
    uint8_t groups;        // groups with a selectable candidate (bit = target_group)
    uint8_t depth;         // pages open (WK_LIST)
    uint8_t colour_index;  // ilc_colour the seat colour came from
    uint8_t reserved;
    uint32_t colour;       // seat colour, packed rgba (0x80 = 1.0)
    uint32_t reserved2;
    uint64_t opened_ms;    // when the window opened (the slide-in starts there)
    char title[TITLE_TEXT];
    char name[ROW_TEXT];   // the character's name (Player 1's header)
    char help[HELP_TEXT];
    view_row rows[VIEW_ROWS];
};
static_assert(sizeof(seat_view) == 536, "seat_view layout (host tests read it)");

enum view_flag : uint32_t {
    VIEW_REDUCED = 0x01,   // the party list is the reduced layout (the rows' anchors follow it)
};

struct menu_view {
    patch::record_head head;   // epoch = session epoch, flags = view_flag, generation = build counter
    uint64_t built_ms;
    uint32_t open_mask;        // bit per seat with a window up
    uint32_t reserved;
    seat_view seats[SEATS];
};
static_assert(sizeof(menu_view) == 32 + SEATS * sizeof(seat_view), "menu_view layout");

// ---- geometry (pure) ------------------------------------------------------------------------------------------------
struct box {
    int16_t x, y, w, h;
};

// Player 1's native command window, from the field layout (stock values; the reduced party list leaves them alone).
struct p1_geometry {
    int16_t list_x, list_y;    // list origin: G14.x + E0.x, G14.y + E0.y + E0.h - 14
    box help;                  // help strip: G11 + G11 E0
    box header;                // group 14's rect
    box name;                  // G14 E1 (the character's name)
    box leader;                // G15 E0 ('LEADER') at G14 E2
    box flag;                  // G15 E1 (the leader flag)
    box footer_l1, footer_r1;  // G30 footer: the shoulder-button icons (G31 E0, G32 E0)
};
p1_geometry p1_native();

// The party list row `index` of `rows` (HUD order, top to bottom), stock or reduced, with the party rows' own anchors
// (party_rows.h): from the row's leftmost piece (the party-target hand) to the screen's right edge, one pitch high.
box party_row(uint32_t index, uint32_t rows, bool reduced);
// The party list as a whole (every row), for the overlap rule.
box party_list(uint32_t rows, bool reduced);

// Areas the menus keep clear of: Target Info (top-left) and the battle log expanded to 20 lines (top-right), from the
// couch layout table (layout.h) without the boss gauge; layout() uses the snapshot's boss flag.
box target_info_zone();
box log_zone();
// Player 1's window at its tallest (the zone the guests keep clear of, whether or not it is open).
box p1_window_zone();

struct window_layout {
    uint8_t seat;
    uint8_t kind;
    uint8_t visible;           // rows shown
    uint8_t start;             // first shown row (index into the view's rows)
    uint8_t row_index;         // the party row it slides out of (0xFF: P1's native place, or no row)
    uint8_t pushed;            // moved up from its row to keep clear of another window
    uint8_t connector;         // the seat-colour link to its row is drawn
    uint8_t help_glass;        // P1: the help strip's glass is drawn
    box frame;                 // the glass
    box rows;                  // the row area (rows bottom-anchored in it)
    box help;                  // the help line
    int16_t pitch;
    int16_t text_scale;
    uint32_t commands;         // canvas commands this window costs as laid out
    uint32_t text_bytes;       // encoded text bytes it costs
    uint32_t reserved;
};
static_assert(sizeof(window_layout) == 48, "window_layout layout (host tests read it)");

struct frame_layout {
    uint32_t count;
    uint32_t commands;
    uint32_t text_bytes;
    uint32_t dropped;          // windows left out (no room at all)
    window_layout w[SEATS];
};

// Lays out every open window of `v` for this frame: rows shown, place, what fits in `commands`/`text` of the frame.
void layout(const menu_view& v, const hud_snapshot& snap, uint32_t commands, uint32_t text, frame_layout* out);

// ---- drawing (game thread; pure) --------------------------------------------------------------------------------------
// Draws `v` into `cv`. Reads nothing but its arguments. Returns the windows drawn.
uint32_t draw(canvas& cv, const menu_view& v, const hud_snapshot& snap, uint64_t now_ms, uint8_t layout_mode);

// Draws `v` into `cv` as draw() does; *dropped = bit per seat whose open window was left out of the frame, or lost a
// command to a full canvas (nullptr: not wanted).
uint32_t draw_seats(canvas& cv, const menu_view& v, const hud_snapshot& snap, uint64_t now_ms, uint8_t layout_mode,
                    uint32_t* dropped);

struct footprint {
    uint32_t commands;
    uint32_t text;
};
// The open windows' floor in `v` (see FLOOR_COMMANDS_MAX).
footprint floor_cost(const menu_view& v, const hud_snapshot& snap);

// The frame driver, before the first pass: the latest view copied out for this frame's menu pass (a torn copy is
// retried once, else the last good copy of this epoch is used while fresh); returns its open windows' floor (zero
// without a usable view).
footprint prepare(const driver::pass_input& in);
// The frame driver's menu pass: the view prepare() copied out this frame (or copied out now), then draw_seats().
// False when there was no usable view (none, withdrawn, another epoch or stale): nothing of the menus was drawn and
// the frame must not count as presenting the menu layer.
bool pass(canvas& cv, const driver::pass_input& in);
// The seats whose open window the last menu pass left out (bit per seat); 0 when it drew every window or ran on no
// view. Game thread (the frame driver, after the passes).
uint32_t last_dropped();

// ---- the view (co-op thread) ----------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void configure_from_settings(const ilc_settings& s);   // seat colours (by seat or by character)
void step(uint64_t now_ms);                             // build and publish the view (or withdraw)
void stop();

// The colour of a seat holding `member` (party id), packed rgba (0x80 = 1.0); *index = the ilc_colour used.
uint32_t seat_colour(uint32_t seat, uint32_t member, uint8_t* index);

// ---- publication -------------------------------------------------------------------------------------------------------
void publish(const menu_view& v);
void withdraw();
bool read(menu_view& out);   // any thread, wait-free; false if none, withdrawn or torn

// ---- pure helpers (also used by host tests) -----------------------------------------------------------------------
// The game's single-byte text (st2e characters, colour tokens skipped) -> ASCII; returns the length.
uint32_t decode_game_text(const uint8_t* in, uint32_t in_len, char* out, uint32_t cap);
// A page row's label (roles other than actions), a page's title, a target group's name; nullptr when none.
const char* role_label(const menu::entry& e);
const char* page_title(uint32_t page);
const char* group_name(uint32_t group);

// Names the view needs, resolved from the game's text (live) or from a host test double.
struct names {
    bool (*action)(void* ctx, uint32_t action, char* out, uint32_t cap);
    bool (*member)(void* ctx, uint32_t member, char* out, uint32_t cap);
    bool (*foe)(void* ctx, uint64_t name_text, char* out, uint32_t cap);
    void* ctx;
    // An action's help line from the game's help text (ASCII, fitted to `cap`); nullptr or false = our own line.
    bool (*help)(void* ctx, uint32_t action, char* out, uint32_t cap);
};
// One seat's window from its menu state (pure given `n`). selector: the frontend's selector (kind SEL_NONE = none);
// cands: the target candidates (WK_TARGET).
struct seat_source {
    const menu::menu_model* model;
    const menu::gambit_state* gambits;
    const targets::candidate_list* cands;
    const void* selector;      // const frontend::selector*
    uint16_t member;
    uint8_t seat;
    uint8_t reserved[5];
};
void build_seat(const seat_source& src, const names& n, seat_view* out);

struct counters {
    volatile uint64_t built;          // views published
    volatile uint64_t withdrawn;
    volatile uint64_t name_misses;    // names that could not be read (shown as '?')
    volatile uint64_t drawn_frames;   // menu passes that drew at least one window
    volatile uint64_t drawn_windows;
    volatile uint64_t stale;          // menu passes without a usable view
    volatile uint64_t torn;           // copies retried
    volatile uint64_t squeezed;       // windows shown with fewer rows than they had (frame share)
    volatile uint64_t dropped;        // windows not drawn at all (no room)
};
const counters* stats();

// The live name reader with its cache (co-op thread, after step): kind 1 an action, 2 a character (party id), 3 a foe's
// name text pointer. A name that cannot be read gives "?" and false. Shared with the battle log.
bool live_name(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap);
// Our own built-in help lines, one by index (nullptr past the last): host tests check each fits the window footer.
const char* builtin_help(uint32_t index);

// ---- host tests -------------------------------------------------------------------------------------------------------
void test_reset();
void test_set_image_base(uintptr_t image_base);
// The view from the test instead of the co-op menus (nullptr: the live frontend).
void test_set_view_source(const menu_view* v);
bool test_build(uint64_t now_ms, menu_view* out);   // what step() would publish, not published
// The reduced-layout flag of the view (-1: the live relayout state).
void test_force_reduced(int32_t reduced);
// Seats the menu pass reports left out whatever it drew (0 = none): the per-seat presenter drill.
void test_force_dropped(uint32_t mask);
// The live name reader without its cache: kind 1 an action, 2 a character (party id), 3 a foe's name text pointer,
// 4 an action's help line.
bool test_live_name(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap);

}  // namespace ilc::hud::menus_draw
