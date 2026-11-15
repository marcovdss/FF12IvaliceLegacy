// Ivalice Legacy co-op module: the native TARGET INFO window given back when the co-op Target Info stops, and the
// field target mode's aimed foe for the snapshot.
//
// Why. While ours is live, TI-01 turns every request to open the native window into the set routine's close request.
// The game asks for that window only while one of its own target cursors holds focus (the battle-menu target list,
// the party-selection panel, the field target mode: the "Target" button), and each cursor closes it itself when it
// ends. The only cursor that outlives the co-op menus is the field target mode: when ours stops (element off, the HUD
// not drawing, the co-op thread stalled) during target mode, the request TI-01 swallowed is not repeated until P1 aims at
// another target, so the native window would stay missing. Turning ours back on, a window opened in a gap stays open
// next to ours until the next request (the native one closes only on a request, a cursor end or a stale target).
//
// What (game thread, every UI tick, from runtime/tick.cpp; data writes only, no game function is called):
//  * give-back (open): ours not live for OFF_MS without a break, the target mode holds input focus on a target X
//    that resolves (registry 0, the resolver's generation check, an actor with a battle record), no native window and
//    no pending create, the HUD window present, the UI drawn, no pause, no frontend, and not yet given back for X in
//    this target-mode session and epoch: owner +0x578 = X, then owner +0x57E = (w & ~6) | 1. That is the game's own
//    deferred create: the HUD window's next update (0x2BE490 msg 2 -> 0x2BE290) creates the window for X through
//    0x2BEA40 / 0x2C0F30, exactly as when both window slots were busy. Until the window appears it is cancelled
//    (bit 0 cleared, only while owner +0x578 is still X and no window is open) when ours goes live, the target mode
//    loses focus or aims elsewhere, and after GIVEUP_TICKS ticks without the window.
//  * re-hide (close): ours live for REHIDE_MS without a break and a native window open (handler 0x2C0870, not
//    closing): window +0x280 |= 4, the game's own stale-target close (0x2BE290 fades it out on the next update), and
//    a pending create cleared. This closes any native window open under ours, given back or not.
//  * Both only while every TI-V row (verify-only, INFO in the patch layer) holds its pinned bytes and TI-01 is
//    attached. Any row off: nothing is written (the native window then returns at the next target change, as before).
//  * The gate: F8 / co-op off writes nothing. Both run only while co-op is ON and RENDER is
//    live (LIVE mode, armed, not faulted); with the gate shut nothing is read or written, a create already requested
//    is left for the game to finish, and both clocks start over when the gate opens again.
//
// The aim (co-op thread, the snapshot sampler): aim() returns the target mode's target X while the mode holds focus
// and X resolves as above (same pins), else 0. The snapshot makes it P1's seat target while it is non-zero.
//
// Writes: u32 owner +0x578, u16 owner +0x57E (bits 0..2), u16 window +0x280 (bit 2). Every reader and writer of these
// runs on the game thread (the game takes no lock there); so do we.
#pragma once

#include <stdint.h>

#include "session/reader.h"

namespace ilc::hud::target_info::giveback {

// ---- game facts (FFXII_TZA.exe 1.0.4.0, VAs at the pinned base) ----------------------------------------------------
namespace facts {
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t UI_MANAGER_PTR = 0x209BE80;   // VA: pointer to the UI manager (TI-V9)
constexpr uint32_t UI_HUD = 0x8FA0;              // the HUD window, embedded; [HUD + 0] = its handler
constexpr uint64_t HUD_HANDLER = 0x2BE490;       // VA: the HUD window's handler (TI-V2)
constexpr uint32_t HUD_OWNER = 0xAC0;            // the TARGET INFO owner inside the HUD window (TI-V2)
constexpr uint32_t OWNER_WINDOW = 0x570;         // u64: the current window, or 0 (TI-V1)
constexpr uint32_t OWNER_ID = 0x578;             // u32: the id the creator reads (TI-V3)
constexpr uint32_t OWNER_STATE = 0x57E;          // u16: bit 0 pending create, bit 1 extras, bit 2 handle form
constexpr uint16_t STATE_PENDING = 0x0001;
constexpr uint16_t STATE_EXTRAS = 0x0002;
constexpr uint16_t STATE_HANDLE_FORM = 0x0004;
constexpr uint32_t UI_OWNER = UI_HUD + HUD_OWNER;
constexpr uint64_t WINDOW_HANDLER = 0x2C0870;    // VA: a TARGET INFO window's handler while its slot is in use
constexpr uint32_t WINDOW_TI_FLAGS = 0x280;      // u16: bit 0 closing, bit 2 invalid -> closed (TI-V1, TI-V4, TI-V5)
constexpr uint16_t TI_CLOSING = 0x0001;
constexpr uint16_t TI_INVALID = 0x0004;
constexpr uint64_t TMODE_PTR = 0x2B47730;        // VA: pointer to the field target mode window (TI-V7)
constexpr uint32_t WINDOW_FLAGS = 0x40;          // u32: generic window flags; 0x10 = input focus (TI-V8)
constexpr uint32_t WINDOW_FOCUS = 0x10;
constexpr uint32_t TMODE_TARGET = 0xC4;          // u32: the target mode's target handle (TI-V7)
constexpr uint64_t RENDER_FLAGS = 0x22C7F90;     // VA: u32; bit 3 = the game draws its UI
constexpr uint32_t RENDER_UI = 0x08;
constexpr uint64_t PAUSE_PRIMARY = 0x209AD28;    // VA: pointer, non-null while the native pause is up
constexpr uint64_t PAUSE_SECONDARY = 0x209AD30;  // VA: pointer, same
constexpr uint64_t FRONTEND_ACTIVE = 0x2092734;  // VA: u32, 1 while a frontend is open
constexpr uint32_t ENTRY_ACTOR = 0x30;           // registry entry: the actor
constexpr uint32_t ACTOR_BATTLE = 0x698;         // actor: the battle record
constexpr uint32_t HANDLE_REGISTRY_SHIFT = 16;   // handle: registry in bits 16..19 (TI-V6)
}  // namespace facts

constexpr uint64_t OFF_MS = 500;          // ours not live this long: give back
constexpr uint64_t REHIDE_MS = 250;       // ours live this long: close a native window under it
constexpr uint32_t GIVEUP_TICKS = 10;     // ticks without the window: the pending create is withdrawn
constexpr uint64_t PIN_RECHECK_MS = 5000; // the TI-V rows are compared again this often
constexpr uint32_t PIN_ROWS = 9;

enum pins_state : uint32_t { PINS_UNKNOWN = 0, PINS_OK = 1, PINS_OFF = 2 };

struct counters {
    volatile uint64_t opens;        // pending creates written
    volatile uint64_t opened;       // ... whose window appeared
    volatile uint64_t cancels;      // ... withdrawn before the window appeared (ours live, focus lost, X changed)
    volatile uint64_t giveups;      // ... withdrawn after GIVEUP_TICKS (the HUD did not update)
    volatile uint64_t superseded;   // ... cleared by the game itself (a request, a cursor end) before any window
    volatile uint64_t rehides;      // native windows closed under ours
    volatile uint64_t pending_cleared; // pending creates cleared under ours
    volatile uint64_t rejected;     // target-mode targets that did not resolve (once per target)
    volatile uint64_t pin_checks;   // TI-V comparisons
    volatile uint64_t pin_off;      // ... that found a row off (the give-back and the aim stay off)
    volatile uint64_t aims;         // snapshot samples that used the aimed target
    volatile uint64_t gated;        // ticks that dropped our bookkeeping because the gate shut (F8, RENDER off)
};
const counters* stats();

enum phase : uint8_t { PHASE_IDLE = 0, PHASE_PENDING = 1, PHASE_GIVEN = 2 };

struct status_view {
    uint32_t pins;                  // pins_state
    int32_t pin_row;                // the first TI-V row found off (index in the site table), -1 none
    uint8_t phase;                  // phase
    uint8_t live;                   // ours live at the last tick
    uint8_t reserved[2];
    uint32_t target;                // X of the pending or given-back window
};
status_view status();

// ---- boot / co-op thread ---------------------------------------------------------------------------------------------
void attach(uintptr_t image_base);
// Compares the TI-V rows (first call, then every PIN_RECHECK_MS). Co-op thread.
void sample(uint64_t now_ms);
bool pins_ok();                     // any thread

// The target mode's validated target while it holds focus, else 0 (also 0 while the pins are not ok). Any thread
// that may read game memory through the fault-safe reader (the snapshot sampler).
uint32_t aim(session::game_reader& r);

// ---- game thread (UI tick) -------------------------------------------------------------------------------------------
void tick(uint64_t now_ms, uint32_t epoch);

// ---- host tests ------------------------------------------------------------------------------------------------------
void test_reset();
void test_set_image_base(uintptr_t image_base);
void test_force_pins(int32_t state);   // -1: compare the rows; else a pins_state
void test_force_live(int32_t live);    // -1: TI-01's predicate; 0 / 1 forced
void test_force_gate(int32_t open);    // -1: co-op ON and RENDER live; 0 / 1 forced
bool test_gate_open();                 // the gate as tick() sees it (honours test_force_gate)
int32_t test_pin_row(uint32_t k);      // the site-table index of TI-V(k+1)

}  // namespace ilc::hud::target_info::giveback
