// Ivalice Legacy co-op module: proof that the co-op menus are on screen.
//
// The co-op battle menus replace Player 1's stock battle menu and leader select. A menu that owns those frontends but
// is not drawn would leave Player 1 without a battle menu while an invisible one takes the buttons (and Wait could
// stop time for a menu nobody sees). So the menu layer may own P1's frontends, open any menu (guests included) or ask
// for a Wait pause only while the presenter is live: the overlay drew the co-op menu layer within LIVE_MS.
//
// The overlay calls note_drawn() on every frame where it actually presented that layer (the HUD frame driver,
// hud/driver.h: the frame hook ran, every pass completed, the menu pass last, and the frame was drawn), whether or not
// a menu was open; nothing else writes the stamp. note_drawn(0) withdraws it (the driver does that on a fault). While
// nothing presents the layer the presenter is not live and the co-op menus stay idle: every stock frontend is
// Player 1's.
//
// Per seat. The menu pass may still leave a seat's open window out of a frame it presents (no room: the frame driver
// keeps the open windows' floor back, so only if the cost model is wrong). The driver reports those seats with
// note_dropped() on every presented frame, before note_drawn(). A seat left out of the last presented frame is not
// shown: its menu keeps its place but takes no input and counts as closed for Wait. Left out for longer than LIVE_MS
// it is lost: the layer closes that seat's menu, as it closes every menu when the whole layer is not presented.
// Player 1's seat is stricter: left out once, its menu closes and P1's stock frontends come back at once
// (frontend::P1_GIVEBACK_MS).
//
// Any thread; 64-bit atomics, wait-free.
#pragma once

#include <stdint.h>

namespace ilc::frontend::presenter {

constexpr uint64_t LIVE_MS = 250;  // the co-op menu layer drawn this recently: the menus may own the battle UI

// The overlay, on a frame where it drew the co-op menu layer (module clock, patch::now_ms()).
void note_drawn(uint64_t now_ms);
// The module clock of the last frame that drew it (0 = never).
uint64_t drawn_ms();
// |now - drawn| <= LIVE_MS (a stamp a little ahead of `now`, another thread's clock read, counts as live).
bool live(uint64_t now_ms);

constexpr uint32_t SEATS = 4;
// The overlay, on a frame where it presented the layer: bit per seat whose open menu window that frame left out.
void note_dropped(uint32_t mask, uint64_t now_ms);
// The seat's window was on the last presented frame (or the seat had none open there).
bool seat_shown(uint32_t seat);
// The seat's window has been left out of every presented frame for more than LIVE_MS.
bool seat_lost(uint32_t seat, uint64_t now_ms);

// The overlay's run of consecutive presented frames: Player 1's menu moves onto the overlay only after a run of them,
// and goes back to the vanilla menu as soon as the run breaks twice in a row or goes stale (frontend.h, rules.h
// p1_arm_step). note_run, break_run and note_failed are written by the overlay alone (the render thread); the run is
// read by the co-op thread. note_drawn has a second, dormant writer: the old HUD frame driver (hud/driver.cpp), whose
// layer is off in the module (driver::set_layer_enabled(false)); its fault path's note_drawn(0) also breaks the run.
//  * note_run(now): this frame presented the layer: one more frame in the run (a gap of more than RUN_GAP_MS since
//    the last one starts a new run: the frame hook stopped being called meanwhile); the missed count goes to zero;
//  * break_run(): the overlay reached a frame and did not present the layer (the HUD hidden, nothing presentable,
//    the guard busy): the run starts again from zero and the missed count (frames in a row not presented) grows;
//  * note_failed(now): the GPU side refused a frame that would have presented the layer (device removed, swap chain
//    or back buffer gone, a creation failure): the run breaks and the failure time is kept (Player 1's buttons go
//    back to the game at once; the guests' menus keep the usual LIVE_MS grace).
// note_drawn(0) (a fault) breaks the run too.
constexpr uint64_t RUN_GAP_MS = 100;
void note_run(uint64_t now_ms);
void break_run();
void note_failed(uint64_t now_ms);
// note_gpu_drawn(now): the GPU side really drew this frame (gpu::DRAWN, not an empty READY frame): proof the back buffer,
// its view and the upload work. Player 1 arms only after such a frame since the last refused one (rules.h).
void note_gpu_drawn(uint64_t now_ms);
// run() is coherent: the writer zeroes the frame count before it moves since/last to a new run and sets the count last;
// the reader takes frames, since and last, reads frames again and retries on a change (a torn view is never returned
// as a run: after three tries it reports a broken run).
struct run_view {
    uint32_t frames;       // presented frames in the current run (0 = broken)
    uint32_t missed;       // frames in a row the overlay reached and did not present (0 after a presented frame)
    uint64_t since_ms;     // the run's first frame (0 = none)
    uint64_t last_ms;      // its last frame
    uint64_t failed_ms;    // the last refused frame (0 = none)
    uint64_t gpu_drawn_ms; // the last frame the GPU side really drew (0 = none)
};
static_assert(sizeof(run_view) == 40, "run_view layout (host tests read it)");
run_view run();

// Host tests: simulate the overlay. 1 = always live, 0 = never, -1 = the stamp decides (the shipped behaviour).
void test_simulate(int32_t mode);
void test_reset();

}  // namespace ilc::frontend::presenter
