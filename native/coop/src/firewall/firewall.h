// Ivalice Legacy co-op module: the semantic input firewall and the stock-frontend gate (subsystem FIREWALL_MENUS).
//
// The game turns every pad into "normalized buttons" (semantic ids 0x0B..0x1B: COMMAND, CONFIRM, CANCEL, PARTY,
// LB, RB, PAUSE, the d-pad, ...) through one get-button function shared by the DirectInput, XInput and Steam pad
// classes. The firewall sits there and at the desktop (mouse) device:
//
//   FW-01  pad get-button 0x79D670 (entry; any thread)   a masked id reads 0 on that device; a pulse reads 1
//   FW-02  pad get-axis 0x79D730 (entry)                 P1's axes read 0 while the stock command tree is the
//                                                        active menu and no pause is up (read by the co-op thread)
//   FW-03  desktop button load 0x79D65E                  M1/M2/M3 (COMMAND/CONFIRM/CANCEL) masked like a pad
//   FW-04  desktop wheel load 0x79D6E6                   consumed and read as 0 while the wheel is suppressed
//   FW-05  mouse look finalize call 0x7A1A1E (call row)  raw X/Y cleared while a guest owns keyboard and mouse
//
// and the stock-frontend gate refuses, at every numeric boundary, the frontends the co-op menus replace:
//
//   FG-01  request dispatcher 0x254B50   ids 1 (P1's battle menu) and 4 (command-tree variant) return 0 = not
//   FG-05  state setter 0x252B40          opened, and 3 (leader select, the automatic KO path included) while the
//   FG-06  dispatch 0x254740              co-op leader selector can be shown; every other id passes untouched
//   FG-02  battle request 0x35D660       behind FG-01 (only ids 1/4 reach it): refused and counted
//   FG-03  leader request 0x35D980       behind FG-01 (only id 3): refused and counted
//   FG-04  command-tree queue 0x28FD50   behind FG-02: refused and counted
//   FG-07  object initializer 0x2465F0   a guard that should never fire: counts an object created with the
//                                        command-tree or leader-window callback while the gate is armed, and lets
//                                        it be created (swapping in the empty callback would leave a live object
//                                        in the tree slot that blocks every later open)
//
// Which device is P1's (a guest's pad never is): the isolation record says how P1 reaches the game this beat.
//   * no guest device configured and P1 not substituted: every pad object is P1's (only P1 plays);
//   * P1 on its own XInput pad: the XInput object whose polled user index (+0x458) is P1's slot, as isolation's
//     pad visit found it (isolation::p1_object);
//   * P1 fed through substitution, or a guest driving a native menu: isolation's host object.
// The hooks compare pointers only; they never read device memory on the get-button path.
// Isolation already neutralizes every other pad's platform block; the other-slots mask is the second wall.
// A pulse reads high on P1's device only, or on every pad object when P1 has none (P1 on keyboard); a guest's
// pulse is P1's input on purpose: guests drive P1's native screens through it.
//
// Policy, composed by the co-op thread every beat (policy.h) from the session record, the isolation record, the
// co-op menus' report (post_menus) and the players' edges, published in one record (wait-free for the hooks):
// masks, hook flags, the gate's refused ids and the current pulse (a semantic id that reads high until a module
// clock deadline). Pulses: one at a time, PULSE_MS high, then PULSE_GAP_MS low; player 1's parity pulses first,
// then the guests round-robin (one queued edge per seat, dropped after PULSE_STALE_MS).
//
// Player 1 parity (built into the policy): P1's LEFT/RIGHT stay native while every menu is
// closed (only UP/DOWN, which open co-op's Change Leader selector, are withheld); with P1's co-op menu closed, B
// becomes a native CANCEL and A on a highlighted world prompt (state >= 1, never a live foe) a native CONFIRM; on
// P1's target page the native TARGET INFO window is closed with CANCEL at most every 0.5 s. The decide setting
// (Circle or Cross confirms, read every 0.25 s) swaps the pad A/B bindings of CONFIRM and CANCEL for every seat
// (input::set_decide_swap); pulses then carry the semantic the player meant.
//
// The keyboard has no firewall row: its semantic ids reach the game only through the key map that isolation's
// keyboard filter rebuilds (a guest's keyboard never reaches the game), and P1's keyboard is stopped by the gate.
//
// Hooks run behind a fail-open check like isolation's: the subsystem live and a record for the dispatcher's epoch
// or one either side (the firewall reads no world object through its record, so an epoch change only makes the
// record one beat older; without this, the KO path, which requests the leader select every frame, could slip
// through at a map change). Anything else is stock. The hooks read game memory only through the fault-safe probe.
#pragma once

#include <stdint.h>

#include "firewall/policy.h"
#include "ilc/bridge_abi.h"
#include "patch/detour.h"

namespace ilc::firewall {

// Device-identity bits of the record (next to policy.h's policy_flag bits).
enum identity_flag : uint32_t {
    FWF_P1_ALL_PADS = 0x0004,  // no guest device configured and P1 not substituted: every pad object is P1's
    FWF_P1_NATIVE = 0x0008,    // p1_slot names P1's own XInput object
    FWF_P1_HOST = 0x0010,      // isolation's host object is P1's device (substitution, or a guest driving a menu)
    FWF_PULSE_ANY = 0x0020,    // P1 has no pad object: the pulse reads high on every pad object
    FWF_DESKTOP_P1 = 0x0200,   // the desktop belongs to P1 (FW-03 uses P1's mask, else the other mask)
};

struct fw_record {
    patch::record_head head;  // epoch = session epoch, flags = policy_flag | identity_flag, generation
    uint32_t p1_mask;         // bit (id - 0x0B)
    uint32_t other_mask;
    uint32_t refused;         // frontend ids (bit id) the gate refuses while FWF_GATE
    uint16_t pulse;           // semantic id that reads high on P1's device until pulse_until, 0 = none
    uint8_t p1_slot;          // XInput user index of P1's own pad, 0xFF none
    uint8_t reserved0;
    uint64_t pulse_until;     // module clock (patch::now_ms)
};
static_assert(sizeof(fw_record) == 40, "fw_record layout (host tests read it)");

// What the co-op menus report every beat they run (the menu layer posts it on the co-op thread, before the
// firewall step). Until a menu layer posts anything, p1_menus is 0: P1's stock frontends stay open and only the
// guest-side walls (other-slots COMMAND, the stock leader menu for guests, isolation) and the guest pulses apply.
struct menu_report {
    uint8_t p1_menus;         // the co-op menus own P1's battle and leader frontends
    uint8_t p1_open;          // P1's co-op menu is open, in a transition or inside its release guard
    uint8_t p1_target;        // P1's co-op menu is on a target page
    uint8_t leader_selector;  // P1's Change Leader selector is open
    uint8_t guests_open;      // bit s: seat s+1's co-op menu is open, in a transition or a release guard (s >= 1)
    uint8_t party_request;    // a native Party request is in progress
    uint8_t quickening;       // quickening_block bits: a native Quickening runs and those seats take part
    uint8_t suppressed;       // the co-op menus are suspended for an external frontend
    uint8_t leader_pending;   // a KO-leader recovery is pending
    uint8_t party_pulse;      // seat (1..) whose native Party request wants a PARTY pulse now, 0 none: queued like
                              // that seat's own edge (P1's parity slot for seat 1), outside any native screen. Taken
                              // only from a report posted for this very beat, never from a stale one
    uint8_t presenter_live;   // the overlay drew the co-op menu layer within the presenter window (presenter.h):
                              // without it p1_menus, leader_pending and leader_gate count for nothing
    uint8_t leader_gate;      // the co-op leader selector can be shown: the stock leader select (id 3, the KO path
                              // included) is refused with the battle menu. Clear: id 3 passes to stock
    uint8_t quickening_command_seat;  // with quickening: the seat (1..) whose QUICKENING press pulses COMMAND
    uint8_t quickening_confirm_seat;  // ... and CONFIRM (the button follows the native party slot, 1 and 2)
    uint8_t reserved[2];
};
static_assert(sizeof(menu_report) == 16, "menu_report layout");

// ---- co-op thread -------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void configure_from_settings(const ilc_settings& s);  // keyboard/mouse owner, configured guest devices
// The menu layer, every beat it runs (latest wins). A report counts only for the epoch it was posted in and for
// MENU_REPORT_BEATS steps after it; a new epoch, a co-op switch edge, start() and stop() drop it. A menu layer that
// stops posting therefore hands P1's stock frontends back within two beats.
constexpr uint64_t MENU_REPORT_BEATS = 2;
void post_menus(const menu_report& m);
void step(uint64_t now_ms);                           // after isolation::publish: compose and publish the record
void stop();                                          // withdraws the record: every hook is stock
// For the menu layer: at the last step P1's world prompt was highlighted (a live foe excluded) while P1's
// replacement was on, so P1's A becomes a native CONFIRM (the interaction) and must not also open P1's co-op menu.
bool p1_prompt_ready();
// Any thread: the published record arms the frontend gate (the co-op menus own P1's battle frontends and the stock
// battle menu is refused) and FIREWALL_MENUS is live. False: P1 may drive the stock battle menu, so its target list
// keeps the native TARGET INFO (hud/target_info.cpp, TI-01). No epoch check: a stalled co-op thread leaves the last
// record, and ours then stops drawing anyway.
bool menus_own_p1();
// The decide setting the last read found (DECIDE_CIRCLE / DECIDE_CROSS; Cross when unreadable).
uint32_t decide_button();

// Boot (once, before arming): registers the handlers and arms FW-01..05 and FG-01..07. All or none: false (and
// nothing armed) if a row is missing or a continuation does not hold the bytes the handlers resume at.
bool attach();

// ---- what one beat is made of (gathered by step(); host tests drive beat() with their own) -------------------
struct beat_inputs {
    uint32_t epoch;
    uint64_t generation;
    uint32_t gates;           // session gate bits
    uint32_t facts;           // session fact bits
    uint8_t phase;            // session phase
    uint8_t switched_on;      // the co-op switch is ON (not releasing or off)
    uint8_t live;             // FIREWALL_MENUS is live
    uint8_t paused;           // the native pause screen is up
    uint8_t target_info;      // the native TARGET INFO window is open
    uint8_t tree_active;      // the stock command tree is the active menu (UI tree slot, active-menu global)
    uint8_t prompt_state;     // P1's world prompt state (0 none; a live foe reads 0)
    uint8_t driver;           // a guest (session seat 1..) drives a native dialog or shop, 0 none
    uint8_t kbm_seat;         // settings: 0 none, 1 = P1, 2.. guests
    uint32_t iso_flags;       // the isolation record's flags (0 = none published)
    uint8_t p1_slot;          // from the isolation record, 0xFF none
    uint8_t guest_devices;    // bit s: seat s+1 has a configured guest device or the keyboard
    uint8_t seat_playing;     // bit s: seat s+1 is in session and under manual control
    uint32_t act_pressed[ILC_SEATS];  // ilc_action edges since the last beat (index = seat - 1)
    uint32_t act_held[ILC_SEATS];
    uint32_t pad_pressed[ILC_SEATS];  // ilc_pad_control edges (a P1 edge counts as a pad edge only with one)
    menu_report menus;
};
fw_record beat(const beat_inputs& in, uint64_t now_ms);  // composes; moves the pulse scheduler; does not publish

// ---- counters --------------------------------------------------------------------------------------------------
struct counters {
    volatile uint64_t published;
    volatile uint64_t masked;          // FW-01/03 reads answered 0
    volatile uint64_t pulsed;          // FW-01 reads answered 1 by a pulse
    volatile uint64_t axes_zeroed;     // FW-02
    volatile uint64_t wheel_consumed;  // FW-04
    volatile uint64_t look_cleared;    // FW-05
    volatile uint64_t refused[7];      // FG-01..07 refusals (FG-07: forbidden objects seen; never refused)
    volatile uint64_t passed;          // FG-01/05/06 ids that went through
    volatile uint64_t pulses_started;
    volatile uint64_t pulses_p1;       // of those, player 1's parity pulses
    volatile uint64_t pulses_dropped;  // queued edges older than PULSE_STALE_MS, or dropped by a context change
    volatile uint64_t decide_reads;
    volatile uint64_t probe_failed;
};
const counters* stats();

struct debug_view {
    uint64_t pulse_until;
    uint64_t next_free;
    uint64_t target_info_next;
    uint16_t pulse;
    uint16_t pending[ILC_SEATS];  // per seat (index = seat - 1): the queued semantic, 0 none
    uint8_t source;               // seat (1..) whose edge the current pulse carries, 0 none
    uint8_t rr;                   // the guest seat served last
    uint8_t p1_prompt_ready;
    uint8_t decide;
    uint32_t last_p1_kind;        // p1_pulse_kind of the last P1 pulse
};
void test_debug(debug_view* out);

// ---- host tests ------------------------------------------------------------------------------------------------
patch::record_slot<fw_record>& slot();
void test_reset();                           // scheduler, counters, menus, decide cache, cursor
void test_publish(const fw_record& r);
void test_set_image_base(uintptr_t base);    // the reader's image base for gather (0 = the started one)
void test_host_source(uint64_t (*source)()); // nullptr = isolation's host object
void test_p1_source(uint64_t (*source)());   // nullptr = isolation's P1 object
uint64_t test_fg07_last();                   // the last object FG-07 saw with a forbidden callback
int32_t fw01(patch::ctx* c, uint32_t epoch);
int32_t fw02(patch::ctx* c, uint32_t epoch);
int32_t fw03(patch::ctx* c, uint32_t epoch);
int32_t fw04(patch::ctx* c, uint32_t epoch);
int32_t fw05(patch::ctx* c, uint32_t epoch);
int32_t fg01(patch::ctx* c, uint32_t epoch);
int32_t fg02(patch::ctx* c, uint32_t epoch);
int32_t fg03(patch::ctx* c, uint32_t epoch);
int32_t fg04(patch::ctx* c, uint32_t epoch);
int32_t fg05(patch::ctx* c, uint32_t epoch);
int32_t fg06(patch::ctx* c, uint32_t epoch);
int32_t fg07(patch::ctx* c, uint32_t epoch);

}  // namespace ilc::firewall
