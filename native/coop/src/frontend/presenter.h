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

// Host tests: simulate the overlay. 1 = always live, 0 = never, -1 = the stamp decides (the shipped behaviour).
void test_simulate(int32_t mode);
void test_reset();

}  // namespace ilc::frontend::presenter
