// Ivalice Legacy co-op module: the co-op battle menus at run time (subsystem FIREWALL_MENUS; Wait requests for WAIT).
//
// The co-op thread runs this once per beat, right after the session step and the liveness heartbeat and before
// guest interactions, input isolation and the firewall, so whatever it decides is in those records the same beat:
//
//  1. Each playing seat's action edges (its own edge cursor; nothing from another epoch is ever replayed) become
//     menu commands (rules.h), applied to that seat's menu model (menu/model.h). Player 1's battle menu IS a co-op
//     menu: P1 opens it with A unless A goes to a highlighted world prompt, and the stock battle and leader menus
//     stay shut by the firewall's gate while the co-op menus own them. Catalogs are rebuilt from the game's own
//     tables and the character's live state (a few times a second, and at once when a menu opens); target pages
//     pick their candidates from the live world (targets/targets.h); a confirmed action, a Gambits toggle or the
//     owner's Dismiss becomes a request (submit/submit.h). A guest's CONFIRM inside its open menu is reported to
//     guest interactions so it is not also taken as a world interaction.
//  2. External frontends (map, main menu, shops and dialogs, the pause screen, the native Party screen) suspend the
//     co-op menus cleanly: adopted after ADOPT_MS, resumed RESUME_MS after they close, aborted (every menu closes)
//     by a load, a cutscene or a change of who controls which character (rules.h).
//  3. A seat's PARTY outside native screens runs the native Party request (rules.h): the firewall pulses PARTY from
//     that seat once Wait has given its pause back. P1's own PARTY reaches the game natively whenever no co-op menu
//     is open (the firewall withholds it only while one is, and then it becomes a request too).
//  4. All regulars down: the game's own recovery or game-over screen owns the battle UI (all native): every co-op
//     menu closes, the report releases P1's stock frontends and no pause is requested.
//  5. The menu report goes to the firewall every beat (firewall::post_menus): while the co-op menus own the battle
//     UI it carries p1_menus, so the frontend gate stays armed; a layer that stops posting hands P1's stock menus
//     back within two beats (firewall.h).
//  6. The Wait request (wait/wait.h): every participating seat's menu open, nothing suspended, no Party request,
//     no recovery, plain field play. The game thread decides the rest (Wait mode, stock frontends, ownership).
//
// The co-op menus own P1's battle UI (p1_menus) only while: the menus' subsystem may act, co-op is switched on, the
// game functions passed their check (submit::enabled), the game's tables were read, the world is valid, P1 has a
// character, and no recovery runs. Anything else posts an empty report: the stock menus are P1's again.
//
//  7. The presenter guard (presenter.h): all of the above runs only while the overlay drew the co-op menu layer within
//     presenter::LIVE_MS. Per seat: a guest's menu whose window the overlay left out of its last frame takes no input,
//     counts as closed for Wait and blocks nothing of P1's; left out for over LIVE_MS it closes. Player 1's own window
//     left out closes at once and P1 gets the stock battle menu and leader select back (P1_GIVEBACK_MS). Otherwise the
//     layer idles: no menu or selector opens (guests included), P1 keeps every stock
//     frontend, nothing is requested and no pause is asked for. The HUD frame driver (hud/driver.h) writes the stamp.
//     One exception (rules.h, presenter_mode_of): menus suspended for a native screen that stopped the HUD (main
//     menu, map, pause) are held, not closed: they keep their place and own nothing until the screen has closed and
//     the layer is drawn again; a suspension that resumes with nothing drawn closes them.
//  8. The character selectors (rules.h): UP or DOWN alone with the menu closed opens P1's Change Leader or a guest's
//     Change Character; a choice is the seat-change transaction (party::request_change). While P1's character is down
//     and still leads, the KO recovery offers the living characters nobody holds; while it has one, the stock leader
//     select stays refused (leader_gate), otherwise P1's battle UI goes back to stock and the game's own KO path runs.
//     P1's character down while another member already leads (a KO promotion): no co-op selector and an empty P1
//     part of the report (no p1_menus, no leader_gate), so P1's buttons, the battle menu and the stock leader select
//     are the game's and P1 is never gated on a fallen character.
//  9. A native Quickening chain is reported (passthrough, and each guest's link by its party slot) even when idle.
// 10. Player 1's menu on the overlay (rules.h p1_arm_step, set_p1_native below): P1 is on the game's own frontends
//     until the overlay has presented the layer for a run of frames; then P1's battle menu, leader select and KO
//     selector are seat 0's co-op menus, drawn by the overlay like the guests'. Any break (presenter not live, a
//     refused frame, the layer idle, a give-back) puts P1 back on the vanilla menu in the same beat.
//
// Nothing here calls a game function or writes game memory; reads go through the fault-safe reader.
#pragma once

#include <stdint.h>

#include "frontend/presenter.h"
#include "frontend/rules.h"
#include "menu/catalog.h"
#include "menu/menus.h"
#include "menu/model.h"
#include "menu/tables.h"
#include "session/session.h"
#include "targets/targets.h"

namespace ilc::frontend {

constexpr uint32_t SEATS = 4;
constexpr uint64_t TABLES_EVERY_MS = 2000;   // the game's tables are read again this often (and on a new epoch)
constexpr uint64_t SHARED_EVERY_MS = 250;    // inventory, ownership and seals
constexpr uint64_t SEAT_EVERY_MS = 250;      // each open menu's character (a closed menu refreshes when it opens)
constexpr uint64_t PICK_EVERY_MS = 100;      // target candidates while a target page is up
constexpr uint64_t REOPEN_QUIET_MS = 80;     // a menu that just closed cannot reopen before this
constexpr uint64_t RELEASE_GUARD_MS = 150;   // after a close the menu still owns the seat's directions this long
constexpr uint32_t PAGE_SIZE = 8;            // rows per page jump
// Player 1's own window left out of a presented frame (presenter.h): the stock battle menu and leader select are P1's
// at once, and stay P1's until a presented frame shows seat 0 again and this long has passed since the give-back
// (an open co-op menu that keeps being left out must not take P1's buttons back on every press).
constexpr uint64_t P1_GIVEBACK_MS = 4 * presenter::LIVE_MS;

// ---- co-op thread -------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void step(uint64_t now_ms);
void stop();   // closes every menu, posts an empty report, withdraws the Wait request

// ---- views (host tests, diagnostics) ------------------------------------------------------------------------
struct seat_view {
    uint8_t mode;              // menu::menu_mode
    uint8_t eligible;          // the seat may use a battle menu this beat
    uint8_t member_known;
    uint8_t group;             // target group while on a target page
    uint16_t member;
    uint16_t target_index;
    uint16_t target_count;
    uint16_t page;             // the page on top (menu::PG_NONE when closed)
    uint16_t cursor;
    uint8_t selector_kind;     // selector_kind (SEL_NONE: no selector up)
    uint8_t selector_index;
    uint32_t last_commands;    // command bits applied this beat
    int32_t last_submit;       // the last submit::handle_event status (0 none)
    uint32_t opens;
    uint32_t submits;
    uint64_t reopen_at;
    uint64_t guard_until;
};
static_assert(sizeof(seat_view) == 48, "seat_view layout (host tests read it)");

struct state_view {
    uint8_t owns_p1;           // the report's p1_menus
    uint8_t recovery;
    uint8_t tables_ready;
    uint8_t wait_requested;
    uint8_t party_state;       // party_state
    uint8_t party_seat;
    uint8_t party_last;        // party_result
    uint8_t interaction_mask;  // the seats reported to guest interactions
    suspension susp;
    uint32_t epoch;
    uint32_t roles;
    int32_t tables_status;
    uint8_t presenter_live;    // the co-op menus are drawn (presenter.h): without it the layer idles
    uint8_t p1_ko;             // P1's character is down and still leads (the KO recovery applies)
    uint8_t ko_live;           // ... and the co-op KO selector has a character to offer (stock id 3 refused)
    uint8_t quickening;        // the quickening bits last reported
    uint8_t report[16];        // the last firewall::menu_report posted
};
static_assert(sizeof(state_view) == 72, "state_view layout");

void read_seat(uint32_t seat, seat_view* out);
void read_state(state_view* out);
const targets::candidate_list* candidates(uint32_t seat);   // host tests: what the seat was shown
const selector* selector_of(uint32_t seat);                   // host tests: the seat's character selector
// The seat's battle menu (model, catalog, Gambits state) as this beat left it: the HUD's menu view reads it on the co-op
// thread, after step().
const menu::seat_menu* seat_menu_of(uint32_t seat);

struct counters {
    volatile uint64_t steps;
    volatile uint64_t opens;
    volatile uint64_t closes;
    volatile uint64_t hard_closes;       // closed by the layer (character change, abort, recovery, co-op off)
    volatile uint64_t submits;           // submit requests made (any status)
    volatile uint64_t suspended;
    volatile uint64_t resumed;
    volatile uint64_t aborted;
    volatile uint64_t party_requests;
    volatile uint64_t party_opened;
    volatile uint64_t party_cancelled;
    volatile uint64_t party_pulses;
    volatile uint64_t recoveries;
    volatile uint64_t table_reads;
    volatile uint64_t table_failures;
    volatile uint64_t seat_refreshes;
    volatile uint64_t picks;
    volatile uint64_t selector_opens;     // Change Leader / Change Character opened
    volatile uint64_t selector_choices;   // a character chosen in a selector (the KO recovery included)
    volatile uint64_t selector_requests;  // ... that asked for a seat change (another character than its own)
    volatile uint64_t ko_opens;           // the KO recovery opened
    volatile uint64_t p1_givebacks;       // P1's window left out of a presented frame: P1's stock frontends given back
};
const counters* stats();

// Player 1 on the game's own battle frontends. While on: the layer never owns P1 (no p1_menus, no leader gate; no
// co-op menu, selector or KO recovery for seat 0, so P1's buttons and the stock battle menu, leader select and KO path
// are the game's), and the Wait rule counts only the guests (P1's stock menu pauses as the game does). P1's own battle
// frontend (SCREEN_P1_BATTLE) up is never an external screen for the guests' menus and never driven by the guests'
// pulses, on or off: while the layer owns P1 the gate refuses it, so one that is up is P1's own. Any thread.
//
// Since 0.1.59 (Marco, 2026-11-16: "Both: P1 menu + party list", one uniform window per seat) the switch follows the
// arming rule (rules.h p1_arm_step) every beat: off (P1's menu drawn by our overlay) only after a run of presented
// frames, back on at once whenever the overlay stops presenting. The module starts with it on. test_reset() turns the
// arming off and the switch off (the host tests of P1's co-op menu); set_p1_arming(true) brings the rule back.
void set_p1_native(bool on);
bool p1_native();
void set_p1_arming(bool on);
bool p1_arming();
void read_p1_arm(p1_arm* out);   // the arming state (host tests, diagnostics)

// ---- host tests ------------------------------------------------------------------------------------------------
// The layer's inputs from the game, replaceable by doubles (nullptr: the live readers).
enum screen_bit : uint32_t {
    SCREEN_PAUSED = 0x1,
    SCREEN_TREE = 0x2,
    SCREEN_QUICKENING = 0x4,
    SCREEN_P1_BATTLE = 0x8,   // P1's own battle frontend is up: the battle menu (1), leader select (3), tree (4)
    SCREEN_P1_LEADER = 0x10,  // ... and it is the leader select (3): a race needs the leader gate too
};
struct sources {
    int32_t (*tables)(menu::catalog_tables* out);                          // menu::read_tables
    int32_t (*shared)(menu::shared_state* out);                            // menu::read_shared
    int32_t (*seat)(uint32_t seat, const session::seat_entry& e, uint8_t esper_owner, menu::seat_state* out);
    int32_t (*picker)(uint32_t seat, const session::session_record& rec, targets::picker_input* out);
    uint32_t (*screen)(const session::session_record& rec);                // screen_bit
};
void test_set_sources(const sources* s);
void test_set_image_base(uintptr_t image_base);
void test_reset();
// The overlay_live the arming rule reads: -1 overlay::live_track; 0 / 1 forced; 2 live while the presenter's last frame is
// within overlay::LIVE_MS (the host tests' frame clock stands for OV-01's stamps).
void test_set_overlay_live(int32_t state);
uint32_t roles_of(const session::session_record& rec);   // the role signature (exposed for tests)

}  // namespace ilc::frontend
