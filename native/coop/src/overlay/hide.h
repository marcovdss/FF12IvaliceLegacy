// Ivalice Legacy co-op overlay: the vanilla HUD pieces our overlay replaces, hidden by the window's own draw bit
// (workspace hud-hide study 2026-11-16, verified in game with Marco watching for the party list and the help line).
//
// The mechanism. A UI window draws (and its whole subtree with it) only while (flags & 0x405) == 5 at 0x245e88: bit 0
// "drawn", bit 2 "updated once", bit 10 "being destroyed". Bit 0 is set only by the game's own show (0x2473f0 /
// 0x247490); the per-frame tree update 0x248030 keeps sending message 2 to a window whose bit 0 is clear, so a hidden
// window keeps its values and positions current. Hide = clear bit 0. Give back = set bit 0 again only while the
// window still has the handler it had when we hid it and (flags & 0x402) == 2 (the game's own "shown" request still
// set, not being destroyed): a window the game closed while we held it is never shown again by us.
//
// The pieces (window, its expected handler):
//   PARTY   the party list panel [[0x209be80] + 0x10f78] (0x29be50; 0 while no panel exists: the main menu destroys it
//           and the address is reused, so the pointer and the handler are read again every tick);
//   HELP0/1 the top-left help line, layout group 7: the two cross-fading slots embedded in the HUD window at
//           hud + 0x0c0 and hud + 0x5c0 (0x2bf690 while the slot is in use; hud = [0x209be80] + 0x8fa0, handler
//           0x2be490);
//   TARGET  the native TARGET INFO window [hud + 0x1030] (0x2c0870); Hunter's Insight's DROPS block is its child and
//           hides with it (our Target Info draws the drops). Bit 0 only: TI-01 stays as it is, because closing the
//           window outright would leave no TARGET INFO window for the UI-tick heartbeat (overlay::note_ui_tick) while
//           the native request has faded the combat log out;
//   LOGB    the second combat-log window [hud + 0x1078] (0x2c0b40, layout group 8);
//   LOGA    the first combat-log window [hud + 0x1080] (0x2c0be0, layout group 0) with its backing quad and its child,
//           the log manager (0x2be4f0, HD-01 inside). Hiding it stops HD-01: the 0.1.59 dead end was only that the
//           heartbeat leaned on HD-01; heartbeat v2 (overlay::note_ui_tick) reads the HUD window instead (0.1.60);
//   CHAIN   the native chain counter window: NOT LOCATED (Cheat Engine request CE-4). A seam: no handler is known, so
//           window_of() returns none and the piece is never touched until its window and handler are filled in below.
//
// When each is hidden (want, 0.1.60 item 7: "absolute turn off all vanilla hud"): the hard blocker follows ONE switch,
// overlay::live (overlay.h): co-op on, RENDER live, an overlay frame presented within LIVE_MS, the field gate open, no
// pause. While it is live, or was within LIVE_MS (the fail-safe's hysteresis: everything is given back once it has been
// false for longer, on a fault and at exit), with the HH-V rows holding their pinned bytes:
//   PARTY   while our party list element is switched on, except while P1's vanilla leader select is up (its cursor and
//           glow live on the panel; review 10) or an Esper is summoned (FACT_SUMMON: our list draws no Mist pips, summon
//           row or TIME yet; review 12);
//   HELP0/1 while the field gate is open now (the main menu uses the same help window) and Player 1's menu is ours
//           (frontend::p1_native false): with P1 held on the vanilla command menu its help line is part of that menu;
//   TARGET  while our Target Info element is switched on (Hunter's Insight's DROPS block is its child);
//   LOGA, LOGB, CHAIN  while our battle log element is switched on.
// Menus opened on purpose (Y menu, shops, dialogue, the map) are not HUD pieces and are never touched.
//
// Give-back (reviews 6-9). A held piece is given back only on the window the piece has now (window_of == the held
// window, same handler, still the game's to show). A read that fails keeps the piece held and writes nothing (retried
// next tick); only a readable "gone or replaced" forgets it. Off the game thread (exit, the game thread stopped, or a
// latched CORE fault: watch() below) bit 0 is set with an interlocked OR inside a fault guard, so a game write to the other flag bits is
// never clobbered, and only while no tick is in flight (the pieces are owned by one thread at a time).
//
// Threads. tick() runs on the game thread in the UI tick (runtime/tick.cpp, UT-01), the thread that owns the windows;
// stale() runs there too (UT-01's stale handler: the co-op thread stopped, everything is given back). restore_at_exit()
// runs on the cleanup thread once the hooks are disarmed: the same give-back (a read, then one store of the flags).
// note_presented() is the render thread's stamp. Nothing here calls a game function; reads and writes go through the
// fault-safe probe.
#pragma once

#include <stdint.h>

namespace ilc::overlay::hide {

enum piece : uint32_t {
    PIECE_PARTY = 0,
    PIECE_HELP0 = 1,
    PIECE_HELP1 = 2,
    PIECE_TARGET = 3,
    PIECE_LOGB = 4,
    PIECE_LOGA = 5,
    PIECE_CHAIN = 6,      // seam: the native chain counter, not located yet (never touched)
    PIECE_COUNT = 7,
};

// The overlay elements whose presentation hides a piece (the render thread stamps them).
enum element : uint32_t {
    ELEM_PARTY = 0,      // our party list            -> PARTY
    ELEM_HELP = 1,       // our windows' footer help  -> HELP0, HELP1 (while P1's menu is ours)
    ELEM_TARGET = 2,     // our Target Info           -> TARGET
    ELEM_LOG = 3,        // our battle log            -> LOGB (and HD-01 skips the stock log text)
    ELEM_COUNT = 4,
};

constexpr uint64_t FRESH_MS = 100;          // an element presented this recently (informational stamps)
constexpr uint64_t LIVE_HOLD_MS = 250;      // the fail-safe: not live for longer than this gives every piece back
constexpr uint64_t PIN_RECHECK_MS = 5000;   // the HH-V rows are compared again this often

// ---- game facts (FFXII_TZA.exe 1.0.4.0, VAs at the pinned base 0x120000; HH-V1..HH-V7) -----------------------------
namespace facts {
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t UI_MANAGER_PTR = 0x209BE80;   // VA: pointer to the UI manager
constexpr uint32_t UI_PARTY_PANEL = 0x10F78;     // u64: the party list panel (HH-V2), 0 while none
constexpr uint64_t PARTY_HANDLER = 0x29BE50;     // VA (HH-V3)
constexpr uint32_t UI_HUD = 0x8FA0;              // the HUD window, embedded
constexpr uint64_t HUD_HANDLER = 0x2BE490;       // VA
constexpr uint32_t HUD_HELP0 = 0x0C0;            // embedded help window, slot 0 (HH-V4)
constexpr uint32_t HUD_HELP1 = 0x5C0;            // slot 1
constexpr uint64_t HELP_HANDLER = 0x2BF690;      // VA (HH-V5)
constexpr uint32_t HUD_TARGET = 0x1030;          // u64: the TARGET INFO window (owner +0xac0 +0x570)
constexpr uint64_t TARGET_HANDLER = 0x2C0870;    // VA
constexpr uint32_t HUD_LOG_B = 0x1078;           // u64: the second combat-log window (group 8)
constexpr uint64_t LOG_B_HANDLER = 0x2C0B40;     // VA
constexpr uint32_t HUD_LOG_A = 0x1080;           // u64: the first combat-log window (group 0, the log manager inside)
constexpr uint64_t LOG_A_HANDLER = 0x2C0BE0;     // VA [hud-hide study section 4, static]
constexpr uint64_t CHAIN_HANDLER = 0;            // NOTADDR: the chain counter window is not located (CE-4): never hidden
constexpr uint32_t WINDOW_FLAGS = 0x40;          // u32
constexpr uint32_t FLAG_DRAWN = 0x001;           // NOTADDR
constexpr uint32_t FLAG_SHOWN = 0x002;           // NOTADDR: the game's own show request
constexpr uint32_t FLAG_DYING = 0x400;           // NOTADDR: being destroyed
constexpr uint64_t COMMAND_ACTOR = 0x2092730;    // VA: u32 frontend id (masked 0x7fff)
constexpr uint64_t COMMAND_FLAG = 0x2092734;     // VA: u32, 1 while a frontend is open
constexpr uint64_t PAUSE_PRIMARY = 0x209AD28;    // VA: pointer, non-null while the native pause is up
constexpr uint64_t PAUSE_SECONDARY = 0x209AD30;  // VA: pointer, same
constexpr uint32_t PIN_ROWS = 7;
}  // namespace facts

// ---- the rules (pure; tested directly) --------------------------------------------------------------------------------
// May the window be hidden now: its handler is the expected one, it is not being destroyed and it is drawn.
bool may_hide(uint64_t handler, uint64_t expected, uint32_t flags);
// May bit 0 be given back: the same window (handler as when hidden) and (flags & 0x402) == 2.
bool may_give_back(uint64_t handler, uint64_t held_handler, uint32_t flags);
// The field gate: no frontend, or only Player 1's own battle frontends.
bool field_ui(uint32_t command_flag, uint32_t command_actor);

struct want_in {
    uint64_t now;
    uint64_t live_ms;                 // the last tick overlay::live held (0 never)
    uint8_t coop_on;
    uint8_t render_live;
    uint8_t pins_ok;
    uint8_t field;                    // field_ui now, and no pause
    uint8_t p1_ours;                  // Player 1's menu is the overlay's (frontend::p1_native false)
    uint8_t leader_vanilla;           // P1's vanilla leader select is up (FRONTEND_LEADER while P1 is native)
    uint8_t summon;                   // an Esper is summoned (session FACT_SUMMON)
    uint8_t party_on;                 // our party list element is switched on
    uint8_t target_on;                // our Target Info element is switched on
    uint8_t log_on;                   // our battle log element is switched on
    uint8_t reserved[6];
};
// Bit per piece that should be hidden now.
uint32_t wanted(const want_in& in);

// ---- module -----------------------------------------------------------------------------------------------------------
void attach(uintptr_t image_base);
void sample(uint64_t now_ms);                 // co-op thread: compare the HH-V rows (first call, then every recheck)
bool pins_ok();
void note_presented(uint32_t element, uint64_t now_ms);   // render thread
uint64_t presented(uint32_t element);                    // any thread: the element's last stamp (0 never)
void tick(bool live);                         // game thread, every UI tick (live = LIVE mode)
void stale();                                 // game thread, UT-01's stale handler: give everything back
void restore_at_exit();                       // cleanup thread, hooks disarmed: give everything back
// Co-op thread, every beat, once our UI tick has been silent for TICK_LOST_MS with pieces held (the look and the act
// happen while watch owns the pieces, so no tick runs in between):
//  * a latched CORE fault (patch::faulted_mask() & FAULT_CORE): UT-01 never runs again, neither its handler nor its
//    stale handler (the dispatcher checks the fault before it computes "stale"), while the RENDER rows stay live and
//    keep the frame signal fresh. Every held piece is given back once from here (counted core_fault_restores) with the
//    guarded interlocked bit-0 OR, only on a window that is still the piece's, with the held handler and
//    (flags & 0x402) == 2; nothing hides again, because only the UI tick hides;
//  * otherwise, only when the game thread is really stopped (review 9): the UT-01 handler itself
//    (tick::stats()->last_tick_ms, the CORE row) and the frame hook (overlay::last_frame_ms(), OV-01) silent too; the
//    same give-back (counted orphan_restores). With only our tick silent (a closed gate) the game thread still runs and
//    owns the windows: nothing is written from here (counted watch_deferred); UT-01's stale handler or the next tick
//    gives back (review 8: no check-then-act write racing a live game thread).
void watch(uint64_t now_ms);
constexpr uint64_t TICK_LOST_MS = 1000;

struct counters {
    volatile uint64_t hides[PIECE_COUNT];     // bit 0 cleared
    volatile uint64_t give_backs[PIECE_COUNT];// bit 0 set again
    volatile uint64_t dropped[PIECE_COUNT];   // a held window the game closed or replaced: forgotten, not touched
    volatile uint64_t pin_checks;
    volatile uint64_t pin_off;
    volatile uint64_t exit_restores;
    volatile uint64_t kept_unreadable;        // a held piece kept because a read failed (retried next tick)
    volatile uint64_t orphan_restores;        // give-backs by watch() after the game thread stopped
    volatile uint64_t watch_deferred;         // watch() saw our tick silent but the game still running: no write
    volatile uint64_t core_fault_restores;    // give-back passes by watch() after a latched CORE fault
};
const counters* stats();

struct status_view {
    uint32_t held;                            // bit per piece hidden by us now
    uint32_t wanted;                          // bit per piece wanted at the last tick
    uint8_t pins;                             // 0 unknown, 1 ok, 2 a row off
    uint8_t reserved[3];
    int32_t pin_row;                          // the first HH-V row off (site index), -1 none
};
status_view status();

// ---- host tests ---------------------------------------------------------------------------------------------------------
void test_reset();
void test_set_image_base(uintptr_t image_base);
void test_force_pins(int32_t state);          // -1 compare; 0 off; 1 ok
void test_force_gate(int32_t coop_and_render);// -1 the live gate; 0 shut; 1 open
void test_force_p1_ours(int32_t ours);        // -1 frontend::p1_native; 0 / 1 forced
void test_force_live(int32_t live);           // -1 overlay::live; 0 / 1 forced
void test_force_elements(int32_t mask);       // -1 the elements' own switches; else bit 0 party, 1 target, 2 log
uint64_t test_presented(uint32_t element);
void test_set_tick_ms(uint64_t ms);           // the last UI tick, as watch() sees it
// watch()'s game-progress signals (UT-01's last run, the frame hook's last call) forced to these values (forced 1), or
// the live ones again (forced 0). test_reset() goes back to the live ones.
void test_set_game_signals(int32_t forced, uint64_t core_ms, uint64_t frame_ms);

}  // namespace ilc::overlay::hide
