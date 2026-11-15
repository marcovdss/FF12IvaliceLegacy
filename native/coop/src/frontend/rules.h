// Ivalice Legacy co-op module: the co-op battle menus' pure rules (no thread, no clock, no game memory).
//
// Four small machines the frontend layer (frontend.h) runs every beat, kept here so host tests can tabulate them:
//
//  * commands(): one seat's action edges as menu commands. Guests open their menu with BATTLE_MENU (pad X); their
//    CONFIRM in the field stays a world interaction. Player 1 opens with CONFIRM (pad A) unless A goes to a
//    highlighted world prompt this beat, or with BATTLE_MENU where P1 has it bound. Open: MENU_UP/MENU_DOWN move
//    (with the input layer's repeat; an opposite pair held together cancels), pages jump with TARGET_PREV/NEXT
//    (LB/RB) or MENU_LEFT/RIGHT for guests and with MENU_LEFT/RIGHT for P1 (P1's LB/RB are bound to those),
//    CONFIRM confirms, CANCEL goes back (closing at the root), BATTLE_MENU closes. On a target page LB/RB (and
//    MENU_LEFT/RIGHT) cycle the target groups backwards/forwards and up/down move between candidates. PARTY with
//    the menu closed asks for the native Party screen; UP or DOWN alone opens the seat's character selector.
//
//  * suspension: external frontends (the map, the main menu, a shop or dialog a guest drives, the pause screen,
//    and the native Party screen) suspend the co-op menus without losing where each player was. A native screen
//    that stays up for ADOPT_MS is adopted: the menus are hidden and take no input. Once it has closed and stayed
//    closed for RESUME_MS (with control back and no modal), they come back exactly as they were. A load, a
//    cutscene or a change of who controls which character aborts the suspension instead: every co-op menu closes.
//    A Party request (below) suspends at once with its own source.
//
//  * party request: a seat's PARTY outside any native screen opens Player 1's stock Party screen for it (the
//    game has one). The request suspends the co-op menus, waits for the Wait engine to give back its pause and for
//    any stock command tree to close, then asks the firewall for one PARTY pulse from that seat. It completes when
//    the native screen is seen open after the pulse (the suspension then follows that screen like any external
//    frontend). It is cancelled by the pause screen, by any other native screen opening first, by a modal, by a
//    load, cutscene or loss of control, and after PARTY_TIMEOUT_MS: a request never leaves the menus stranded or
//    Wait requested.
//
//  * recovery: when every active party member is down, the game's own recovery or game-over screen owns the
//    battle UI (all native): the co-op menus close, the firewall's gate opens and Wait is never requested. The latch
//    holds until the party is up again and the native screen the recovery used has closed for RESUME_MS (or, if
//    no native screen ever showed, RECOVERY_QUIET_MS after the party came back).
#pragma once

#include <stdint.h>

namespace ilc::frontend {

// ---- commands ---------------------------------------------------------------------------------------------------
enum command : uint32_t {
    CMD_OPEN = 0x0001,
    CMD_CLOSE = 0x0002,
    CMD_UP = 0x0004,
    CMD_DOWN = 0x0008,
    CMD_PAGE_UP = 0x0010,
    CMD_PAGE_DOWN = 0x0020,
    CMD_GROUP_PREV = 0x0040,
    CMD_GROUP_NEXT = 0x0080,
    CMD_CONFIRM = 0x0100,
    CMD_BACK = 0x0200,
    CMD_PARTY = 0x0400,
    CMD_SELECT = 0x0800,        // the menu closed: UP or DOWN alone opens the seat's character selector
};

struct seat_edges {
    uint32_t pressed;   // ilc_action bits pressed since the last beat
    uint32_t repeated;  // ... repeat pulses (held menu directions)
    uint32_t held;      // ... held at the newest tick
};
static_assert(sizeof(seat_edges) == 12, "seat_edges layout (host tests build it)");

// mode: menu::menu_mode. prompt_ready: P1's A goes to a highlighted world prompt (firewall::p1_prompt_ready).
uint32_t commands(bool player1, uint8_t mode, bool prompt_ready, const seat_edges& e);

// ---- the character selectors ----------------------------------------------------------------------------------------
// With its battle menu closed, a seat's UP or DOWN opens its character selector: Player 1's Change Leader (the stock
// leader select stays shut), a guest's Change Character. It lists the active party's regular characters in HUD order
// that can lead (not KO or Stone); a character another seat holds is an exchange and is offered only while the seat's
// own character can lead (it is handed over). UP/DOWN move (wrapping), CONFIRM chooses (choosing one's own closes),
// CANCEL or the battle-menu button close. The choice goes to the seat-change transaction (party::request_change):
// one game-thread job that writes the leader and the steering in the same callback.
// The KO recovery is the same list for Player 1 while its own character is down (and still the native leader):
// only characters no seat holds, never its own; it cannot be dismissed, and it stays up until a living character
// leads. Without one to offer, the stock leader select runs instead.
constexpr uint32_t SELECTOR_SLOTS = 4;
constexpr uint16_t SEL_NO_MEMBER = 0xFFFF;
constexpr uint8_t SEL_NO_SEAT = 0xFF;
constexpr uint16_t SEL_REGULARS = 6;    // party ids 0..5

enum selector_kind : uint8_t {
    SEL_NONE = 0,
    SEL_LEADER = 1,             // Player 1's Change Leader
    SEL_CHARACTER = 2,          // a guest's Change Character
    SEL_KO = 3,                 // Player 1's KO recovery
};

struct selector_member {
    uint16_t member;            // party id (SEL_NO_MEMBER: an empty slot)
    uint8_t able;               // can lead: present, HP > 0, no KO or Stone
    uint8_t holder;             // the session seat (0..) controlling it, SEL_NO_SEAT none
};
static_assert(sizeof(selector_member) == 4, "selector_member layout");

struct selector_in {
    selector_member slot[SELECTOR_SLOTS];  // the active party, HUD order
    uint16_t own;               // the seat's current character
    uint8_t own_able;           // the seat's own character can lead
    uint8_t seat;               // session seat (0 = Player 1)
};
static_assert(sizeof(selector_in) == 20, "selector_in layout");

struct selector {
    uint8_t kind;               // selector_kind
    uint8_t count;              // choices
    uint8_t index;              // the highlighted choice
    uint8_t reserved;
    uint16_t member[SELECTOR_SLOTS];       // the choices, HUD order
};
static_assert(sizeof(selector) == 12, "selector layout");

enum selector_event : int32_t {
    SELE_NONE = 0,
    SELE_MOVED = 1,
    SELE_CHOSEN = 2,            // *chosen = the member (the selector closes, except the KO recovery)
    SELE_CLOSED = 3,
};

// (Re)builds the choices for `kind`. A new kind starts on the seat's own character (or the first choice); a refresh
// keeps the highlighted member when it is still offered. Returns the number of choices.
uint32_t selector_build(selector* s, uint8_t kind, const selector_in& in);
// Applies menu commands (CMD_UP/DOWN/CONFIRM/BACK/CLOSE, as commands() gives them for an open menu).
int32_t selector_command(selector* s, uint32_t cmds, uint16_t* chosen);
void selector_close(selector* s);

// ---- external frontends -----------------------------------------------------------------------------------------
constexpr uint64_t ADOPT_MS = 40;       // a native screen this old is adopted
constexpr uint64_t RESUME_MS = 200;     // closed this long (with control, no modal): the co-op menus come back

enum suspend_source : uint8_t {
    SRC_NONE = 0,
    SRC_EXTERNAL = 1,   // a native screen the players opened (or a script did)
    SRC_PARTY = 2,      // the native Party request
};

struct suspension {
    uint8_t suspended;
    uint8_t source;             // suspend_source
    uint8_t observed_open;      // the native screen was seen open while suspended
    uint8_t reserved;
    uint32_t roles;             // the role signature when it began (who controls which character)
    uint64_t since;             // when it began
    uint64_t open_since;        // a native screen first seen at (0 none)
    uint64_t close_since;       // the adopted screen first seen closed at (0 none)
};
static_assert(sizeof(suspension) == 32, "suspension layout");

struct screen_in {
    uint8_t native_open;        // a stock frontend is up, or the pause screen
    uint8_t modal;              // a modal interaction is up
    uint8_t control;            // the player has control
    uint8_t settling;           // loading, a cutscene, no world
    uint8_t party_transfer;     // a Party request is in flight (it owns the acknowledgement)
    uint8_t reserved[3];
    uint32_t roles;             // the current role signature
    uint32_t reserved2;
};
static_assert(sizeof(screen_in) == 16, "screen_in layout");

enum suspend_event : int32_t {
    SE_NONE = 0,
    SE_SUSPENDED = 1,           // adopted an external frontend
    SE_RESUMED = 2,             // the co-op menus are back as they were
    SE_ABORTED = 3,             // a load, cutscene or role change: every co-op menu must close
};

void suspension_begin(suspension* s, uint8_t source, uint32_t roles, uint64_t now);
void suspension_clear(suspension* s);
int32_t suspension_step(suspension* s, const screen_in& in, uint64_t now);

// ---- the native Party request -------------------------------------------------------------------------------------
constexpr uint64_t PARTY_TIMEOUT_MS = 1250;

enum party_state : uint8_t {
    PR_IDLE = 0,
    PR_PENDING = 1,             // waiting for Wait's release and any stock command tree to close
    PR_PULSED = 2,              // the PARTY pulse was asked for: waiting for the screen
};

enum party_result : uint8_t {
    PRR_NONE = 0,
    PRR_OPENED = 1,
    PRR_CANCEL_PAUSE = 2,
    PRR_CANCEL_UNEXPECTED = 3,  // another native screen opened before the pulse
    PRR_CANCEL_MODAL = 4,
    PRR_CANCEL_GATE = 5,        // load, cutscene, no control
    PRR_CANCEL_TIMEOUT = 6,
};

struct party_request {
    uint8_t state;              // party_state
    uint8_t seat;               // 1.. (session seat + 1)
    uint8_t drain;              // a stock command tree was open: wait until it closes and no native screen is up
    uint8_t last;               // party_result of the last finished request
    uint32_t reserved;
    uint64_t started;
    uint64_t deadline;
};
static_assert(sizeof(party_request) == 24, "party_request layout");

struct party_in {
    uint8_t native_open;        // a stock frontend is up (the pause screen excluded)
    uint8_t paused;             // the pause screen is up
    uint8_t modal;
    uint8_t gate_ok;            // control, no load, no cutscene
    uint8_t tree_active;        // P1's stock command tree is the active menu
    uint8_t wait_owned;         // the Wait engine still holds its pause
    uint8_t reserved[2];
};
static_assert(sizeof(party_in) == 8, "party_in layout");

struct party_out {
    uint8_t result;             // party_result this beat (PRR_NONE while in flight or idle)
    uint8_t pulse;              // ask the firewall for the PARTY pulse now (once per request)
    uint8_t reserved[2];
};
static_assert(sizeof(party_out) == 4, "party_out layout");

bool party_begin(party_request* p, uint8_t seat, uint64_t now);   // false while one is in flight
party_out party_step(party_request* p, const party_in& in, uint64_t now);
void party_cancel(party_request* p, uint8_t reason);

// ---- the presenter guard while the menus are suspended -------------------------------------------------------------
// The overlay draws the co-op menu layer from the field/battle HUD, so the presenter dies 250 ms into any screen that
// stops the HUD (the main menu, the map, the pause screen). That must not close menus the suspension promised to
// bring back. Without a live presenter:
//   * HOLD while the menus are suspended for a native screen (for the Party request, only once its screen was seen)
//     or while a native screen is up and may still be adopted: the models stay as they were, the suspension machine
//     keeps running (adopt, resume wait, abort), and nothing is owned (no P1 frontends, no input, no Wait, no draw);
//   * IDLE otherwise (every menu closes), and always in a load, a cutscene, a party-down recovery or a Party request
//     still in flight.
// A suspension that resumes while the presenter is still dead ends in IDLE: menus come back only when drawn.
enum presenter_mode : uint8_t {
    PM_RUN = 0,       // the presenter is live: the layer runs
    PM_HOLD = 1,      // keep the suspended menus, own nothing
    PM_IDLE = 2,      // close every menu, own nothing
};

struct presenter_in {
    uint8_t live;           // presenter::live(now)
    uint8_t suspended;      // suspension.suspended
    uint8_t source;         // suspension.source
    uint8_t observed_open;  // suspension.observed_open
    uint8_t native_open;    // a stock frontend or the pause screen is up
    uint8_t settling;       // loading, a cutscene, no world
    uint8_t down;           // the party-down recovery applies
    uint8_t party_busy;     // a Party request is in flight
};
static_assert(sizeof(presenter_in) == 8, "presenter_in layout");

uint8_t presenter_mode_of(const presenter_in& in);

// ---- the party-down recovery ------------------------------------------------------------------------------------------
constexpr uint64_t RECOVERY_QUIET_MS = 2000;

struct recovery {
    uint8_t latched;
    uint8_t observed;           // the recovery's native screen was seen
    uint8_t reserved[6];
    uint64_t up_since;          // the party came back at (0 while down)
    uint64_t close_since;       // the native screen closed at (0 while open)
};
static_assert(sizeof(recovery) == 24, "recovery layout");

// True while the recovery owns the battle UI.
bool recovery_step(recovery* r, bool party_down, bool native_open, uint64_t now);

}  // namespace ilc::frontend
