// Ivalice Legacy co-op module: "Keep running in background" (the player's own setting; no co-op session needed).
//
// FF12 pauses itself when another window takes the focus: the screen-state machine clears its state's active byte,
// pushes it into the application's active byte (app+0x27) once per loop, and the field tick opens the "PauseMenu",
// which suspends the main update (no field tick at all) until a button is pressed back in the game. With the setting
// on, the game keeps running instead, in windowed and borderless mode:
//
//   FP-01  screen-state ApplyActive 0x7A4150 (entry; app thread)    for the Window and Borderless states: dl is forced
//                                                                  to 1, so the application stays active and the
//                                                                  pause never starts; the state's active byte (+0x18)
//                                                                  is set to 1 only while the window has the focus.
//                                                                  Exclusive fullscreen (and its lose-focus and change
//                                                                  states) runs stock: the game leaves and re-enters
//                                                                  exclusive mode through its own path
//   IN-06  pad focus gate 0x7A2236 (mid-function; tick thread)     unfocused: player 1's XInput state reads neutral (the
//                                                                  game's own code interprets a zero gamepad under a
//                                                                  fresh packet number and runs the finalizer, so held
//                                                                  buttons release); on refocus the real packet is new
//   FP-03  DirectInput pad update 0x7A1AAA (tick thread)           unfocused: the neutral block and the finalizer
//   FP-04  DirectInput mouse update 0x7A17E9 (tick thread)         unfocused: deltas and buttons zero, the finalizer
//   FP-V1  the only writer of app+0x27 (verify-only GATE)          another mod rewrote it: FP-01 is skipped
//
// So while the game runs in the background nothing from another application reaches it: the keyboard is already
// cleared by the game, the cursor delta is zeroed by the game, and the pads above read neutral. Isolation's host pad
// (player 1 fed from SDL, or a guest driving the native menus) reads neutral too (running_unfocused). Guests on SDL
// pads keep playing their own characters (SDL reads them in the background on purpose). Steam Input pads have no game
// focus gate.
//
// The rows have no subsystem bit (CORE): they act with no guest seated, with the co-op switch off and while the module
// is dry-armed, and only on this setting. Off = stock: the handlers return at once. FP-01 also refuses to act unless
// IN-06 is hooked, so the game never runs in the background with a stale stick (fail closed: the stock pause instead).
// The state's active byte is never held at 1 while unfocused, so whenever FP-01 stops acting there (the setting turned
// off, a stale heartbeat, a closed gate) the next loop passes the game's own 0 and the stock pause starts. Turning it
// off while focused is clean, because the byte was held at 1 while focused.
//
// From boot the setting is off until the player's saved settings have been read: the native defaults record
// (generation 0) never turns it on; the first record from Lua does (its saved value, or the schema default "on" for a
// new store). A saved "Off" therefore holds from the first loop.
//
// Handlers read and write game memory only through the fault-safe probe, call no game function, allocate nothing and
// never block. The setting is one atomic word written by the co-op thread from the applied settings.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/detour.h"

namespace ilc::focus {

// ---- co-op thread ----------------------------------------------------------------------------------------------
void configure_from_settings(const ilc_settings& s);   // keep_running

// ---- any thread --------------------------------------------------------------------------------------------------
bool keep_running();            // the setting as last applied (off until a settings record from Lua was applied)
bool neutral_ready();           // IN-06 is hooked with our handler: FP-01 may act
bool window_focused();          // the game window has the foreground (unreadable or no window yet = focused)
bool running_unfocused();       // keep_running() and another window has the foreground (asks Windows)

// Boot (once, before arming): registers the handlers of IN-06, FP-01, FP-03 and FP-04 and arms the sites that were
// hooked. A row that was not hooked (its gate did not match, another mod holds it) stays stock; FP-01 needs IN-06.
// Returns false if a handler could not be registered (nothing of this feature acts then).
bool attach();

struct counters {
    volatile uint64_t forced;        // FP-01: Window/Borderless held active
    volatile uint64_t passed;        // FP-01: another state (exclusive fullscreen and its transitions): stock
    volatile uint64_t declined;      // FP-01: setting off or IN-06 not hooked: stock
    volatile uint64_t xinput;        // IN-06: neutral XInput state while unfocused
    volatile uint64_t dinput_pad;    // FP-03: neutral DirectInput pad while unfocused
    volatile uint64_t dinput_mouse;  // FP-04: neutral mouse while unfocused
    volatile uint64_t probe_failed;  // a probe read or write failed: stock
};
const counters* stats();

struct status_view {
    uint8_t keep_running;
    uint8_t fp01_armed;             // FP-01 hooked (FP-V1 matched) and its handler registered
    uint8_t in06_armed;             // IN-06 hooked (IN-06a matched)
    uint8_t fp03_armed;
    uint8_t fp04_armed;
    uint8_t reserved[3];
};
static_assert(sizeof(status_view) == 8, "status_view layout (host tests read it)");
status_view status();

// ---- host tests --------------------------------------------------------------------------------------------------
void test_set_keep_running(bool on);
void test_set_foreground(int32_t forced);   // -1 = ask Windows; 0 = another window has the foreground; 1 = the game
void test_set_neutral_ready(bool ready);     // as attach() would decide (direct handler calls without an install)
void test_reset();
int32_t fp01(patch::ctx* c, uint32_t epoch);
int32_t fp03(patch::ctx* c, uint32_t epoch);
int32_t fp04(patch::ctx* c, uint32_t epoch);
int32_t in06(patch::ctx* c, uint32_t epoch);

}  // namespace ilc::focus
