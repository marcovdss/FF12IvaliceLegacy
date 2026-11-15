// Ivalice Legacy co-op module: the input firewall's pure rules (no thread, no clock, no game memory).
//
// Three layers, each a plain function so host tests can tabulate them:
//
//  1. derive(): what the co-op state means for native input this beat, as ten switches (the "contract"):
//       p1_replacement     P1's COMMAND, CONFIRM and CANCEL stay away from the game: P1's battle menu is the co-op
//                          menu. Only while the co-op menus own P1's frontends, P1 has control, and native input is
//                          not authorised (pause, modal, native menu, town, chocobo, Quickening, a guest driving a
//                          native menu). A pending KO-leader recovery keeps it on.
//       other_command      COMMAND on every non-P1 pad: a guest pad never opens a native menu. Stays on through
//                          loads and cutscenes; off only while native input is authorised (and never while a KO
//                          recovery is pending).
//       directional_owner  a co-op menu is open (or opening, closing, or inside its release guard): P1's directions
//                          and RB, and the guests' LB/RB and directions, belong to the co-op menus (P1's part not
//                          while p1_stock).
//       p1_lb              P1's LB belongs to P1's open co-op menu, except while the Change Leader selector is up
//                          (the game's turbo stays usable there).
//       party_blocked      PARTY (Y) is withheld while a co-op menu or a native Party request is in progress (P1's
//                          not while p1_stock).
//       quickening_command / quickening_confirm   during a native Quickening, the seat buttons that would also
//                          reach P1's chain: COMMAND (P2 takes part) and CONFIRM (P3 takes part).
//       leader_replacement the stock leader menu stays shut: P1's UP/DOWN (they open co-op's Change Leader
//                          selector) and every guest direction. P1's LEFT/RIGHT stay native (target focus, KO
//                          recovery), the player-1 parity rule; an open co-op menu still owns them through
//                          directional_owner.
//       mouse_look         a guest owns the keyboard and mouse: raw mouse look never moves P1's camera.
//       gate               the frontend gate refuses the stock battle menu (1) and the command-tree variant (4);
//                          with gate_leader also the leader select (3, the automatic KO path too); every other
//                          frontend passes.
//       gate_leader        the co-op leader selector can be shown, so the stock leader select stays shut.
//       p1_stock           P1's frontends are the game's while the co-op menus are drawn (the co-op menus do not own
//                          P1: the hand-back after P1's window was left out, or P1's character down with nothing to
//                          offer), P1's own co-op menu is not open and no native Party request runs: a guest's open
//                          menu then keeps only the guests' pads; P1's directions, RB and PARTY reach the game, so
//                          P1 drives the stock menus normally. The frontend asks for a Party from P1's PARTY only
//                          while the co-op menus own P1, so P1's PARTY is never both native and a request.
//     The co-op menus count only while their presenter is live (the overlay drew them recently): without it
//     p1_menus, leader_pending and leader_gate are ignored and no menu owns the directions, LB or PARTY, so nothing
//     invisible ever owns Player 1's frontends or input. A native Quickening and the guest walls still apply.
//  2. masks(): the contract as per-device masks and hook flags (a bit per semantic id, bit = id - 0x0B).
//  3. The pulse rules (p1_pulse, guest_pulse): which semantic a player-1 or guest edge turns into this beat.
//
// Nothing here knows about records, threads or time; firewall.cpp composes them every beat.
#pragma once

#include <stdint.h>

#include "firewall/firewall_facts.h"

namespace ilc::firewall {

constexpr uint32_t sem_bit(uint32_t id)
{
    return id >= facts::SEM_FIRST && id <= facts::SEM_LAST ? 1u << (id - facts::SEM_FIRST) : 0u;
}
constexpr uint32_t SEM_ALL = (1u << (facts::SEM_LAST - facts::SEM_FIRST + 1)) - 1u;  // 17 bits
constexpr uint32_t M_COMMAND = sem_bit(facts::SEM_COMMAND);
constexpr uint32_t M_CONFIRM = sem_bit(facts::SEM_CONFIRM);
constexpr uint32_t M_CANCEL = sem_bit(facts::SEM_CANCEL);
constexpr uint32_t M_PARTY = sem_bit(facts::SEM_PARTY);
constexpr uint32_t M_LB = sem_bit(facts::SEM_LB);
constexpr uint32_t M_RB = sem_bit(facts::SEM_RB);
constexpr uint32_t M_PAUSE = sem_bit(facts::SEM_PAUSE);
constexpr uint32_t M_UP = sem_bit(facts::SEM_UP);
constexpr uint32_t M_RIGHT = sem_bit(facts::SEM_RIGHT);
constexpr uint32_t M_DOWN = sem_bit(facts::SEM_DOWN);
constexpr uint32_t M_LEFT = sem_bit(facts::SEM_LEFT);
constexpr uint32_t M_DIRECTIONS = M_UP | M_RIGHT | M_DOWN | M_LEFT;
constexpr uint32_t M_VERTICAL = M_UP | M_DOWN;
constexpr uint32_t M_HORIZONTAL = M_LEFT | M_RIGHT;

// Frontend ids the gate refuses (bit id): the battle menu and the command-tree variant, plus the leader select while
// the co-op leader selector can be shown.
constexpr uint32_t GATE_REFUSED_BATTLE = (1u << facts::FRONTEND_BATTLE) | (1u << facts::FRONTEND_TREE_ACTIVE);
constexpr uint32_t GATE_REFUSED_LEADER = 1u << facts::FRONTEND_LEADER;
constexpr uint32_t GATE_REFUSED = GATE_REFUSED_BATTLE | GATE_REFUSED_LEADER;
constexpr bool gate_refuses(uint32_t refused_mask, uint32_t id)
{
    return id < 32 && (refused_mask & (1u << id)) != 0;
}

// Quickening (policy_inputs.quickening, menu_report.quickening): QB_CHAIN = a native Quickening chain runs (native
// input is authorised); QB_COMMAND / QB_CONFIRM = a guest presses that link, so P1's own button for it is withheld.
enum quickening_block : uint8_t { QB_COMMAND = 0x01, QB_CONFIRM = 0x02, QB_CHAIN = 0x80 };

// Hook flags (record_head.flags of the published record; firewall.h adds the device-identity bits).
enum policy_flag : uint32_t {
    FWF_ACTIVE = 0x0001,             // masks and pulses apply
    FWF_GATE = 0x0002,               // the frontend gate refuses `refused`
    FWF_SUPPRESS_P1_AXES = 0x0040,   // P1's axes read 0 while the stock command tree is the active menu
    FWF_SUPPRESS_WHEEL = 0x0080,     // the desktop wheel is consumed and reads 0
    FWF_SUPPRESS_MOUSE_LOOK = 0x0100 // raw mouse X/Y are cleared before the desktop finalizer
};

struct policy_inputs {
    uint8_t active;           // the firewall runs: subsystem live, co-op switched on, the session running
    uint8_t p1_menus;         // the co-op menus own P1's battle and leader frontends
    uint8_t control;          // P1 has control (no cutscene or scripted lock)
    uint8_t passthrough;      // native input authorised this beat (see derive())
    uint8_t suppressed;       // the co-op menus are suspended (an external frontend was adopted)
    uint8_t p1_open;          // P1's co-op menu is open, in a transition or inside its release guard
    uint8_t any_open;         // the same for any seat
    uint8_t leader_selector;  // P1's Change Leader selector is open
    uint8_t leader_pending;   // a KO-leader recovery is pending
    uint8_t party_request;    // a native Party request is in progress
    uint8_t quickening;       // quickening_block bits (a native Quickening runs and those seats take part)
    uint8_t desktop_guest;    // a guest seat owns the keyboard and mouse
    uint8_t presenter;        // the co-op menus are drawn (presenter live): p1_menus and leader_* count only then
    uint8_t leader_gate;      // the co-op leader selector can be shown (the stock leader select stays shut)
};

struct contract {
    uint8_t p1_replacement;
    uint8_t other_command;
    uint8_t directional_owner;
    uint8_t p1_lb;
    uint8_t party_blocked;
    uint8_t quickening_command;
    uint8_t quickening_confirm;
    uint8_t leader_replacement;
    uint8_t mouse_look;
    uint8_t gate;
    uint8_t gate_leader;
    uint8_t p1_stock;
};

struct policy {
    uint32_t p1_mask;     // P1's device reads 0 for these ids
    uint32_t other_mask;  // every other pad device
    uint32_t flags;       // policy_flag (FWF_ACTIVE and FWF_GATE included)
    uint32_t refused;     // frontend ids the gate refuses while FWF_GATE
};

contract derive(const policy_inputs& in);

// LEADER_ALL_DIRECTIONS rebuilds the leader rule as it was before the player-1 parity change (P1's four directions
// withheld). It exists for the parity oracle in the host tests only; the module always uses LEADER_VERTICAL.
enum leader_rule : uint32_t { LEADER_VERTICAL = 0, LEADER_ALL_DIRECTIONS = 1 };
policy masks(const contract& c, uint32_t rule = LEADER_VERTICAL);

inline policy compose(const policy_inputs& in)
{
    return in.active != 0 ? masks(derive(in)) : policy{};
}

// ---- pulses ---------------------------------------------------------------------------------------------------
constexpr uint64_t PULSE_MS = 65;              // a pulse reads high this long (one ring sample and one frame at least)
constexpr uint64_t PULSE_GAP_MS = 40;          // then low at least this long before the next pulse (a release edge)
constexpr uint64_t PULSE_STALE_MS = 250;       // a queued edge not delivered by then is dropped, never replayed late
constexpr uint64_t TARGET_INFO_CLOSE_MS = 500; // P1's native TARGET INFO is closed at most this often

// Player 1's native pulses (the parity rules). Called with this beat's state; returns the semantic id, or 0.
struct p1_inputs {
    uint8_t replacement;      // the contract's p1_replacement (P1's COMMAND/CONFIRM/CANCEL are withheld)
    uint8_t menu_open;        // P1's co-op menu is open (it consumes CONFIRM and CANCEL itself)
    uint8_t target_page;      // P1's co-op menu is on a target page
    uint8_t leader_selector;  // P1's Change Leader selector is open
    uint8_t driver;           // a guest drives a native menu (P1 waits)
    uint8_t confirm_edge;     // P1's CONFIRM pressed this beat on P1's pad (the keyboard reaches the game itself)
    uint8_t cancel_edge;      // P1's CANCEL pressed this beat on P1's pad
    uint8_t prompt_state;     // P1's world prompt (0 none, 1 highlighted, 2 selected); a live foe reads 0
    uint8_t target_info;      // the native TARGET INFO window is open
    uint8_t target_info_due;  // the 0.5 s close limiter allows a close now
};
enum p1_pulse_kind : uint32_t { P1P_NONE = 0, P1P_CANCEL = 1, P1P_CONFIRM = 2, P1P_TARGET_INFO = 3 };
uint32_t p1_pulse(const p1_inputs& in, uint32_t* kind);

// A guest's edges this beat (ilc_action bits) as one native semantic, or 0. PAUSE always goes (P1's START is
// never masked, a guest's START pauses through a pulse). With a native screen open (a menu, a modal, the pause
// screen), one edge in priority order: CANCEL > PARTY > CONFIRM > LB > RB > up, down, left, right (an opposite
// pair held together cancels out).
uint32_t guest_pulse(uint32_t act_pressed, uint32_t act_held, bool native_screen);

}  // namespace ilc::firewall
