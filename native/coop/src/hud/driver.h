// Ivalice Legacy co-op module: the HUD frame driver and the presenter stamp.
//
// Once per frame the frame hook (H0, canvas.h) asks the driver whether the co-op HUD layer may be drawn, and if so
// the driver builds that frame from the latest HudSnapshot (snapshot.h) by running the element passes in a fixed
// order, each into the same canvas:
//
//     1. party extras (seat edge and numeral, low-HP glow, damage ghost, action line, guest cursors, timers)
//     2. Target Info
//     3. battle log
//     4. menus (the co-op battle menus, selectors and target pages): LAST, so they draw over everything else
//
// The passes read only the snapshot and the frame's own inputs, never game memory. They are stubs in this build
// (later tasks fill them in); a pass that has nothing to show emits nothing.
//
// The presenter stamp (frontend/presenter.h). The co-op menus may own Player 1's battle UI, open for anyone or ask
// for a Wait pause only while the menu layer is being presented. The driver stamps presenter::note_drawn(now) on
// every frame where the menu layer was actually presented, whether or not a menu was open:
//   * the hook ran this frame (the HUD tree drew), with a live draw context (readable, the window's, not faded out,
//     the UI-ready flag set);
//   * RENDER and FIREWALL_MENUS live, co-op on (not after the kill switch), the game's UI shown (render flags bit 3);
//   * a snapshot of this epoch at most SNAPSHOT_STALE_MS old, laid out in a known mode;
//   * every pass, the menu pass last, completed without a fault, and the frame was replayed to its end (or had
//     nothing to draw);
//   * the menu pass drew from a usable menu view (published, this epoch, at most VIEW_STALE_MS old): a stalled co-op
//     thread leaves none, so nothing of the menus is known to be on screen and nothing is stamped.
// Per seat (presenter::note_dropped): a seat whose open window the menu pass left out of the frame is not presented
// on it, so that seat's menu takes no input and holds no Wait, and closes if it stays out (presenter.h). The open
// windows' floor (menus_draw::floor_cost) is kept back while the passes before the menus draw (canvas::reserve), so
// that happens only if the cost model is wrong. The battle log's floor (battle_log::LOG_FLOOR / LOG_TEXT_FLOOR) is
// kept back the same way while party extras and Target Info draw, on every frame where ours replaces the native log.
// A stamp only on frames with an open menu would deadlock (menus may open only while the presenter is live), so a
// frame that presents an empty menu layer stamps too. Anything else stamps nothing, so P1 gets the stock menus back
// once the stamp is older than LIVE_MS (250 ms). A fault in a pass or in the replay latches RENDER and withdraws the
// stamp at once: the next co-op beat already idles the menus.
//
// Threads. The game thread runs everything here (from the hook). Counters are read by any thread.
#pragma once

#include <stdint.h>

#include "hud/canvas.h"
#include "hud/snapshot.h"

namespace ilc::hud::driver {

enum pass_id : uint8_t {
    PASS_PARTY = 0,        // party extras
    PASS_TARGET_INFO = 1,
    PASS_LOG = 2,
    PASS_MENUS = 3,        // always last
    PASS_COUNT
};

// What a pass draws from: the snapshot, the frame time and the layout mode (couch; online is a seam).
struct pass_input {
    const hud_snapshot* snap;
    uint64_t now_ms;
    uint8_t layout;
    uint8_t reserved[7];
};
using pass_fn = void (*)(canvas& cv, const pass_input& in);

// Why the layer was not drawn (or not presented) on a frame.
enum gate_reason : uint8_t {
    GATE_OK = 0,
    GATE_NO_SNAPSHOT = 1,  // none, withdrawn, torn, another epoch, stale, or an unknown layout mode
    GATE_COOP_OFF = 2,     // the kill switch (or co-op off)
    GATE_UI_HIDDEN = 3,    // render flags bit 3 clear, or unreadable
    GATE_COUNT
};

// ---- the co-op layer switch (0.1.57 interim) --------------------------------------------------------------------
// Off by default: the frame hook draws no co-op layer and stamps no presenter, so Player 1 keeps the game's own command
// menu and the native battle log stays (nothing replaces it), the native Target Info is never closed, the native party
// list is left as the game lays it out and the guests' co-op menus do not open (their model and input logic are kept for
// the own overlay that will draw them). On only for experiments: Developer options and the HUD test draw both on
// (battle_hud::configure_from_settings). Any thread.
void set_layer_enabled(bool on);
bool layer_enabled();

// ---- the frame hook (game thread) --------------------------------------------------------------------------------
void attach(uintptr_t image_base);
// May the co-op layer be drawn this frame? Cheap (no snapshot copy); counts the reason when it may not.
bool frame_gate(uint32_t epoch, uint64_t now_ms);

// Builds the layer's frame: the snapshot copied out, then the passes in order into the driver's canvas, then (when
// given) the Diagnostics test frame's commands appended underneath them. Runs the passes inside the draw guard.
enum compose_result : int32_t {
    COMPOSE_OK = 0,
    COMPOSE_NO_SNAPSHOT = 1,  // torn twice, withdrawn, another epoch, stale or unknown layout: nothing composed
    COMPOSE_FAULTED = 2,      // a pass faulted (RENDER is latched by the caller, the stamp withdrawn here)
    COMPOSE_BUSY = 3,         // the guard was busy or not ready
};
int32_t compose(uint32_t epoch, uint64_t now_ms, const frame* test_frame, const frame** out);

// The menu layer was presented on this frame (the caller replayed the composed frame to its end, or it was empty):
// stamps the presenter when FIREWALL_MENUS is live and the menu pass completed (the seats whose window it left out
// first: presenter::note_dropped).
void presented(uint64_t now_ms);
// The layer could not be presented because of a fault: the stamp is withdrawn so the menus idle at once.
void faulted();

// ---- views -------------------------------------------------------------------------------------------------------
struct counters {
    volatile uint64_t frames;          // frame_gate said yes
    volatile uint64_t declined[GATE_COUNT];
    volatile uint64_t composed;        // compose returned OK
    volatile uint64_t torn;            // compose found no coherent snapshot copy
    volatile uint64_t pass_faults;     // a pass faulted
    volatile uint64_t stamped;         // presenter stamps written
    volatile uint64_t not_stamped;     // presented, but FIREWALL_MENUS off or the menu pass did not run
    volatile uint64_t appended;        // test-frame commands appended
    volatile uint64_t last_stamp_ms;
    volatile uint64_t last_passes;     // bit per pass completed on the last composed frame
    uint8_t trace[PASS_COUNT];         // the order the passes ran in on the last composed frame
    uint8_t traced;                    // how many ran
    uint8_t layout;                    // the layout mode of the last composed frame
    uint8_t reserved[2];
    volatile uint64_t kept_commands;   // commands kept back for the menu pass on the last composed frame
    volatile uint64_t dropped_frames;  // presented frames that left a seat's open window out (that seat not presented)
    uint16_t pass_commands[PASS_COUNT];// commands each pass added on the last composed frame
    volatile uint64_t kept_log;        // commands kept back for the log pass on the last composed frame (0 or LOG_FLOOR)
    volatile uint64_t menus_no_view;   // composed frames whose menu pass had no usable menu view (nothing stamped)
    volatile uint64_t layer_off;       // frames the layer switch refused (set_layer_enabled)
};
const counters* stats();

// ---- host tests --------------------------------------------------------------------------------------------------
void test_reset();
// Bit per pass: that stub emits a marker text (its name) at a fixed spot, so the composed frame shows the order.
void test_set_probe(uint32_t mask);
// The pass with this id faults (dereferences address 8) before drawing; PASS_COUNT = none.
void test_inject_fault(uint32_t pass);
const char* pass_name(uint32_t pass);

}  // namespace ilc::hud::driver
