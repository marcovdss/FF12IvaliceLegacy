// Ivalice Legacy co-op module: our own overlay, drawn inside the game's frame (RENDER).
//
// The game presents through one wrapper, 0x6234a0(renderer), called from exactly one place: the Present case of the
// render-command dispatcher (0x607588, row OV-01, a call row; OV-V1..OV-V3 pin the wrapper, its Present call and the
// renderer's device fields). The handler runs there, on the render-ring consumer thread that owns the immediate
// context, draws the overlay into the back buffer with our own D3D11 objects (gpu.h) and lets the wrapper run
// (RUN_ORIGINAL), which flushes and presents. No DXGI vtable patch, no dummy device, no window of our own.
//
// What it draws (scene.h): the test card (Developer options + the Diagnostics test switch) and every seat's command
// window from the published menu view, one uniform window per seat in a fixed slot along the bottom. Player 1's
// window is ours only while P1 is not on the game's own frontends (frontend::p1_native: off after a run of presented
// frames, frontend/rules.h p1_arm_step); otherwise P1 keeps the game's own command menu.
//
// Visibility follows the game's HUD. The UI tick (note_ui_tick, heartbeat v2, primary) and HD-01 (note_hud, the HUD
// tree's combat-log draw call, secondary) refresh the heartbeat while the field/battle HUD is drawn, the field gate is
// open and no pause is up. The overlay draws only while that heartbeat is at most HUD_FRESH_MS old and the field gate
// read in the frame hook itself is open, at the HUD's own alpha, so nothing shows over the main menu, the map, a
// cutscene, an FMV or a fade. overlay_live (live()) is the vanilla HUD blocker's one switch (0.1.60 item 7).
//
// The presenter (frontend/presenter.h). The seats' menus may open, take input and hold Wait only while the overlay
// presents them. On every frame where the HUD heartbeat is fresh, co-op is on, FIREWALL_MENUS is live, a fresh menu
// view and HUD snapshot of this epoch exist and the GPU side is ready (drawn, or nothing to draw), the handler calls
// presenter::note_dropped(mask) (seats whose open window is not on this frame: Player 1's while P1 is native, any
// window the batch had no room for), then note_drawn(now) and note_run(now) (one more frame in the run). Anything
// else stamps nothing and breaks the run (break_run); a frame the GPU side refused while the menus were presentable
// is reported with note_failed(now), which puts Player 1 back on the vanilla menu on the next co-op beat. Without
// stamps the menus go idle 250 ms later.
//
// Fail closed. Not attached, a creation failure, a mismatch, a removed device, a small or odd back buffer: nothing is
// drawn and nothing is stamped (the game is stock; the guests' menus idle). The whole frame's work runs inside
// guard::call; a fault puts the saved pipeline state back (guarded again), latches RENDER faulted (every RENDER row is
// stock from then on) and withdraws the presenter stamp. The render thread never logs: it records counters and event
// bits, and step() (co-op thread) writes them to hook.log once each, plus a report every 30 s with Developer options.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/patch_engine.h"

namespace ilc::overlay {

constexpr uint64_t HUD_FRESH_MS = 100;    // the HUD drew this recently: the overlay may draw
constexpr uint64_t REPORT_MS = 30000;     // the Developer options report
constexpr uint64_t LIVE_MS = 250;         // overlay_live: an overlay frame presented this recently (and its hysteresis)

// ---- overlay_live: the one switch of the vanilla HUD blocker (0.1.60 item 7) ---------------------------------------------
// The overlay is live while co-op is on, RENDER is live and not faulted, the overlay attached, an overlay frame was
// presented within LIVE_MS (the frame hook stamps it when the GPU side took a frame that composed the HUD elements or the
// seats' menus), the HUD heartbeat is that fresh too, and the field gate is open with no pause (field_open_now). While it
// is live every vanilla battle HUD piece stays hidden (hide.h), HD-01 skips the stock log text, and P1's menu may arm
// (frontend rules.h p1_arm_step, with LIVE_MS of hysteresis). Not live for more than LIVE_MS gives everything back.
// Any thread: the field gate read now: hide::field_ui on COMMAND_FLAG / COMMAND_ACTOR (no frontend, or only Player 1's
// own battle frontends 1/3/4) and both pause pointers (0x209ad28 / 0x209ad30) null. A failed read is a closed gate.
bool field_open_now();
// Any thread.
bool live(uint64_t now_ms);
// Co-op thread, once per beat (the frontend's arming): the current live streak. since = the first beat of the streak
// (gaps of at most LIVE_MS do not break it), 0 while not live; last = the last beat live held (0 never).
struct live_streak {
    uint64_t since;
    uint64_t last;
};
live_streak live_track(uint64_t now_ms);
// Co-op thread: a new streak from the next live beat (the frontend calls it when P1 disarms, so a re-arm always needs
// LIVE_MS of overlay_live again, whatever the gap that ended the last one).
void live_restart();
uint64_t presented_ms();                  // any thread: the last frame that presented (0 never)

// Boot thread: the atlas decoded, the guard installed, OV-01's handler set and armed. False: no overlay (logged).
bool attach(uintptr_t image_base);
// Co-op thread (and the boot): the test card switch (Developer options and the Diagnostics test switch).
void configure_from_settings(const ilc_settings& s);
// Game thread, from HD-01's handler: the HUD tree is drawing (rcx = the log manager, rdx = its draw context). Counts
// only while the field gate is open and no pause is up (item 1, 0.1.60: the game's HUD keeps drawing while it fades out
// under the Y menu, and the overlay drew over that menu for about a second). The secondary heartbeat source: while the
// blocker hides the first log window (hide.h PIECE_LOGA) HD-01 does not run at all.
void note_hud(uint64_t manager, uint64_t context);
// Game thread, every UI tick in LIVE mode (runtime/tick.cpp): heartbeat v2, the primary source. While the UI is ready
// and drawn (UI_READY, RENDER_FLAGS bit 3), the field gate is open with no pause (field_open_now's rule) and the HUD
// window [0x209be80] + 0x8fa0 has its handler 0x2be490, is drawn ((flags & 0x405) == 5) and has its own alpha (+0xa0)
// above 0, the heartbeat is refreshed at that alpha. It no longer leans on the TARGET INFO window or on HD-01 having run
// (0.1.59 did both: a native TARGET INFO request fades the combat log out and silences HD-01, and the blocker now hides
// the first log window on purpose).
void note_ui_tick();
// Co-op thread, once per beat: hook.log lines for what the render thread recorded.
void step(uint64_t now_ms);
void stop();

// Game thread (HD-01): the overlay is live (overlay_live) and our battle log element is switched on: the stock log text
// draw is skipped (the heartbeat is noted first, so skipping it never hides the overlay).
bool log_replaces_native(uint64_t now_ms);

// The OV-01 handler (render thread; host tests call it with a synthetic ctx).
int32_t frame_hook(patch::ctx* c, uint32_t epoch);

enum event_bit : uint32_t {
    EV_FIRST_FRAME = 0x0001,
    EV_CREATE_FAILED = 0x0002,
    EV_MISMATCH = 0x0004,
    EV_BAD_BACK_BUFFER = 0x0008,
    EV_FAULT = 0x0010,
    EV_NO_RENDERER = 0x0020,
    EV_DEVICE_REMOVED = 0x0040,
    EV_GPU_OTHER = 0x0080,      // no back buffer, render target view or map failed
};

struct counters {
    volatile uint64_t reached;        // the handler ran
    volatile uint64_t declined;       // not attached or dead
    volatile uint64_t hidden;         // the HUD heartbeat was stale: nothing drawn
    volatile uint64_t idle;           // nothing to draw (no test card, guests' menus not presentable)
    volatile uint64_t drawn;          // frames drawn
    volatile uint64_t ready;          // frames with nothing to draw but a ready GPU side
    volatile uint64_t failures;       // frames the GPU side refused (gpu::result other than DRAWN/READY)
    volatile uint64_t busy;           // the guard was busy or not ready
    volatile uint64_t faults;
    volatile uint64_t stamps;         // presenter stamps
    volatile uint64_t windows;        // guest windows drawn (sum over frames)
    volatile uint64_t dropped_frames; // stamped frames that left a guest's open window out
    volatile uint64_t no_view;        // menus not presentable: no fresh menu view of this epoch
    volatile uint64_t no_snapshot;    // ... no fresh HUD snapshot of this epoch
    volatile uint64_t quads_last;     // quads on the last drawn frame
    volatile uint64_t cost_us_last;   // the handler's own time on the last drawn frame
    volatile uint64_t cost_us_max;
    volatile uint64_t cost_us_total;
    volatile uint64_t frame_us;       // smoothed time between handler calls
    volatile uint64_t hud_ms;         // module clock of the last HUD heartbeat
    volatile uint64_t hud_alpha;      // its alpha (0x80 = 1.0)
    volatile uint64_t last_result;    // the last gpu::result
    volatile uint64_t heartbeats;     // note_hud calls that counted
    volatile uint64_t bb_w, bb_h, bb_format;
    volatile uint64_t events;         // event_bit, OR-ed by the render thread
    volatile uint64_t tick_heartbeats;// note_ui_tick calls that refreshed the heartbeat (Target Info up, log faded)
    volatile uint64_t hud_frames;     // frames that composed the HUD elements
    volatile uint64_t hud_refused;    // ... with an element left out (its budget or the batch ran out)
    volatile uint64_t hud_quads_party;// quads of each element on the last such frame (the timing report)
    volatile uint64_t hud_quads_target;
    volatile uint64_t hud_quads_log;
};
const counters* stats();
// Any thread: the module clock of the frame hook's last call (0 = never this launch). Stamped first thing in the
// handler, so after the dispatcher's gate (OV-01's gate byte, LIVE, RENDER not faulted, a fresh beat) and before the
// handler's own checks: it says only that the game still presents frames while OV-01 may run (hide::watch's evidence
// of a running game).
uint64_t last_frame_ms();

struct status_view {
    uint8_t attached;
    uint8_t row_hooked;     // OV-01 holds our call island
    uint8_t test_card;
    uint8_t dead;           // a fault latched the overlay off
};
status_view status();

// ---- host tests -------------------------------------------------------------------------------------------------------
void test_reset();
bool test_attach(uintptr_t image_base);    // attach without the patch layer (handler not registered)
void test_set_test_card(bool on);
void test_set_heartbeat(uint64_t ms, uint32_t alpha);
void test_force_live(int32_t state);       // -1 the real overlay_live; 0 / 1 forced
void test_set_presented(uint64_t ms);      // the last presented frame, as if the frame hook had stamped it
// 1: the frame's job faults right after the pipeline state was changed (the restore drill); 0: off.
void test_inject_fault(uint32_t where);
const void* test_last_batch();             // overlay::batch* of the last composed frame
uint32_t test_last_dropped();

}  // namespace ilc::overlay
