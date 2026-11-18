// Ivalice Legacy co-op overlay: the HUD elements around the seat windows, composed into the quad batch (pure).
//
// The HUD mockup v2 (workspace hud-redesign-20261116, Marco 2026-11-16 "Check the mockup thats our goal"), authored
// at 1920x1080 (the mockup's 1366x768 times 1.406). Outlined text straight on the world, thin bars, no panels. Every
// face, size, colour and mark comes from the active theme (theme.h, marks.h); the geometry below is the engine's.
//
//  * party_list (bottom-right; replaces the native panel, hidden by its draw bit: hide.h): per row a seat diamond
//    with its numeral and a thin seat-colour edge (a gambit ally: no diamond), a gold G while gambits are on, the
//    name, HP big and max small, MP, the HP gauge with the damage ghost, Player 1's controlled row on the vanilla blue
//    band, the action line (the action only: "Attack", "Cure"; never its target, the target lines show that;
//    "Choosing..." while that seat's menu is open; the action gauge), status tokens with seconds left, low HP glowing
//    red, and the weapon-state word
//    (Guard, Cadence, Flow) while live. Rows are bottom-anchored: 1..4 rows end on PARTY_BOTTOM.
//  * target_info (top-left; replaces the native window, hidden by its draw bit): an edge in the seat colour of the
//    last player who targeted, "Target Info" and the Libra / Anti-Libra badge, level and name (red when 10+ above the
//    party) with the King / Crowned / Mark tag, the HP bar with its ghost, HP numbers and percentage (Libra), Weak /
//    Half / Null (Libra), statuses with seconds (immune ones greyed and struck), the Legacy states line (always),
//    drops with Hunter's Insight knowledge and STEAL. Never a charge gauge (the removed cast bar).
//  * battle_log (top-right; replaces the native log: its text skipped at HD-01, its second window hidden): "Battle
//    Log" with the chain ("Chain 12 - Lv 2") on the right, then the lines newest at the bottom, each with a glowing
//    dot (seat colour, silver gambit ally, copper foe, green reward) and the seat numeral, the line in runs coloured as
//    number_colors.lua colours numbers (dealt white, taken red, HP healed green), tags (CRIT, WEAK, ..., the proc tag
//    from the script channel) in gold. Faster fading than the old layer (style::LOG_LIFE_MS).
// (The top-centre Wait line, "Wait" and the holders' diamonds, was removed on Marco's word, 2026-11-16: "not needed,
// its only holding screen space". The Wait rule itself is unchanged: wait/wait.h.)
//
// Geometry: the constants below. The party list starts at PARTY_X, right of the seat slots (P4's window ends at 1172:
// scene.h), so the slots and the list never meet in either party size (host tests: HudLayoutTest). Target Info and
// the log drop BOSS_DY while the native boss gauge shows.
#pragma once

#include <stdint.h>

#include "hud/battle_log.h"
#include "hud/menus_draw.h"
#include "hud/party_rows.h"
#include "hud/snapshot.h"
#include "hud/target_info.h"
#include "overlay/batch.h"
#include "overlay/feed.h"
#include "overlay/states.h"

namespace ilc::overlay::hud_scene {

// ---- geometry (authoring units) ---------------------------------------------------------------------------------------
constexpr float PARTY_X = 1290.0f;         // the list's left edge (the seat diamond)
constexpr float PARTY_W = 604.0f;          // to x 1894
constexpr float PARTY_BOTTOM = 1066.0f;    // the last row's bottom
constexpr float PARTY_PITCH = 59.0f;       // row to row
constexpr float PARTY_ROW_H = 56.0f;
// Offsets inside a row (from PARTY_X and the row's top).
constexpr float ROW_EDGE_X = 25.0f;
constexpr float ROW_G_X = 37.0f;
constexpr float ROW_NAME_X = 63.0f;
constexpr float ROW_HP_RIGHT = 371.0f;   // the HP number ends over the gauge's end
constexpr float ROW_MAX_X = 375.0f;
constexpr float ROW_MP_LABEL_X = 472.0f;
constexpr float ROW_MP_RIGHT = 574.0f;
constexpr float ROW_GAUGE_X = 219.0f;
constexpr float ROW_GAUGE_Y = 32.0f;
constexpr float ROW_GAUGE_W = 152.0f;
constexpr float ROW_GAUGE_H = 4.0f;          // the stand-in's height (the theme's gauge.height is drawn, at most:)
constexpr float ROW_GAUGE_H_MAX = 8.0f;
constexpr float ROW_LINE_Y = 37.0f;         // the action line's top
constexpr float ROW_WORD_X = 478.0f;
constexpr float ACTION_BAR_W = 48.0f;
constexpr float ACTION_BAR_H = 4.0f;
constexpr uint32_t ROW_STATUSES = 3;        // status tokens on a row's action line

constexpr float TI_X = 56.0f;               // Target Info's text column
constexpr float TI_EDGE_X = 39.0f;
constexpr float TI_TOP = 42.0f;             // the header's top
constexpr float TI_W = 590.0f;              // the header rule
constexpr float TI_BAR_W = 422.0f;
constexpr float TI_BAR_H = 6.0f;
constexpr float TI_HP_TEXT_X = 492.0f;
constexpr float TI_LINE = 32.0f;            // the pitch of the lines below the bar
constexpr uint32_t TI_STATUSES = 10;

constexpr float LOG_X = 1274.0f;            // the log's left edge (the dots)
constexpr float LOG_RIGHT = 1893.0f;
constexpr float LOG_TOP = 42.0f;            // the header's top
constexpr float LOG_LINES_Y = 82.0f;        // the first line's top
constexpr float LOG_PITCH = 30.0f;
constexpr float LOG_TEXT_DX = 20.0f;        // dot -> numeral / text

constexpr float BOSS_DY = 54.0f;            // the native boss gauge shows: Target Info and the log move down
constexpr uint64_t LOG_LIFE_MS = 4000;      // the overlay's faster log fade (the view's own setting wins)

// ---- per-element budgets (quads; QUAD_CAP holds them all plus the seat windows and the test card) ---------------------
constexpr uint32_t BUDGET_PARTY = 900;
constexpr uint32_t BUDGET_TARGET = 800;
constexpr uint32_t BUDGET_LOG = 1100;

enum element : uint32_t {
    EL_PARTY = 0,
    EL_TARGET = 1,
    EL_LOG = 2,
    EL_COUNT = 3,
};

// What the elements draw from (any pointer may be null: that element, or that part, is left out).
struct input {
    const hud::hud_snapshot* snap;               // the party rows (required for the party list)
    const hud::party_rows::rows_record* rows;    // seat colours, gambits, Player 1's stock "choosing"
    const feed::record* feed;                    // names, MP, actions, ghost, chain
    const hud::menus_draw::menu_view* menus;     // whose co-op menu is open ("Choosing...")
    const hud::target_info::view* ti;
    const hud::battle_log::view* log;
    const states::record* states;                // the Legacy script states (null or stale: none shown)
    uint64_t now;
    uint8_t boss_gauge;                          // the native boss gauge shows
    uint8_t party_on;                            // the party list element is switched on (0: it draws nothing)
    uint8_t reserved[6];
    uint64_t ti_since;                           // when Target Info's target last changed (its name's carousel)
};

struct output {
    uint32_t drawn;                // bit per element drawn this frame
    uint32_t refused;              // bit per element left out (its budget or the batch ran out: rolled back)
    uint32_t quads[EL_COUNT];      // quads each element used
};

// Each element: draws into the batch, rolled back whole if it would pass its budget or the batch refuses a quad.
// Returns true when something was drawn.
bool party_list(batch& b, const input& in, uint32_t budget = BUDGET_PARTY);
bool target_info(batch& b, const input& in, uint32_t budget = BUDGET_TARGET);
bool battle_log(batch& b, const input& in, uint32_t budget = BUDGET_LOG);
// All three in order (the log first, so a short batch keeps the party list and Target Info last of all).
output compose(batch& b, const input& in);

// ---- pure helpers (tested directly) ----------------------------------------------------------------------------------
// Row k of n (bottom-anchored) and its top.
float party_row_top(uint32_t index, uint32_t rows);
// The seat of a party row (snapshot hud_unit::seat, 1..4) holds an open co-op menu in the view.
bool seat_choosing(const hud::menus_draw::menu_view* menus, uint8_t seat);
// The seat colour of seat 1..4 as rgba (the rows record's resolved colour; a default when unknown).
uint32_t seat_rgba(const hud::party_rows::rows_record* rows, uint8_t seat);
// The battle log line's dot colour.
uint32_t dot_rgba(const hud::battle_log::view& v, uint8_t dot);
// A log tone as rgba (seat tones read the view's seat colours).
uint32_t tone_rgba(const hud::battle_log::view& v, uint8_t tone);
// The HP warning step of a party row: HP_NORMAL above half, HP_WARN at half or less, HP_CRITICAL at the game's critical
// state (party_rows::low_hp: the HP Critical status, or below a fifth of max, Bubble doubling max), HP_DOWN at 0 HP or KO.
enum hp_step : uint8_t { HP_NORMAL = 0, HP_WARNING = 1, HP_CRIT = 2, HP_DOWN = 3 };
uint8_t hp_step_of(uint32_t hp, uint32_t hp_max, uint32_t status);
// The max HP the HP steps, the gauge and the "/max" text use: doubled under Bubble (as party_rows::low_hp does).
uint32_t effective_max(uint32_t hp_max, uint32_t status);
// A drop line shown as rare (DROP_RARE: the rare and very rare drop slots 2-3, or a special drop).
bool drop_rare(const hud::target_info::drop_line& d);
// "6210 / 9800  63%" etc.: digits into a buffer, returns the length.
uint32_t format_u(uint32_t v, char* out, uint32_t cap);

}  // namespace ilc::overlay::hud_scene
