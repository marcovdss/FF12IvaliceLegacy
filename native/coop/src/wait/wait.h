// Ivalice Legacy co-op module: the Wait-mode participant rule (subsystem WAIT).
//
// In the game's Wait battle mode, battle time stands still while Player 1's battle menu is open. With co-op the
// rule becomes: time stops only while EVERY participating player has their co-op battle menu open, so one player
// browsing never freezes the others. Active mode never pauses for the co-op menus. A participant is a seat in the
// session, under manual control, with a living character whose battle menu may open; a benched seat (no character
// free for it), a parked seat and a seat whose character is down never count. No participant, no pause.
//
// Two halves, one record between them:
//  * Co-op thread (the frontend layer, every beat): pause_wanted() over the seats, then publish() one request
//    record (epoch, generation, requested, publish time). Not requested while the co-op menus are suspended for a
//    native screen, a native Party request is in flight, the party is down, the world is not in plain field play,
//    co-op is switched off or the menus' subsystem may not act.
//  * Game thread, first thing in the UI-tick callback (UT-01), before the job drain: step() takes or gives back
//    the game's own pause reason 0, the reason stock Wait mode holds while P1's battle menu is open. Acquire and
//    release are synchronous, so the next field tick after this callback sees the new state (latency at most one
//    field tick at any frame rate and speed), and the sub-steps keep consuming time while entities are frozen, so
//    a release never replays a backlog.
//
// The reason bit has no reference count and no owner, and stock frontends hold reason 0 too. So the engine owns
// the reason only when it took it itself, and every UT-01 call applies these rules (re-reading everything on the
// game thread; the co-op thread's view is never trusted for these):
//   wanted   = the request is fresh (this liveness epoch, published within FRESH_MS), WAIT may act, the game is in
//              play (mode 2), the battle setting is Wait, and no stock frontend with another pause reason is up;
//   revalidate: owned but the bit is clear (a stock close or the area reset released it) -> not owned any more;
//   borrow:     wanted and the bit is already held by someone else -> do nothing, own nothing;
//   acquire:    wanted and the bit is clear -> acquire(0, 0, 0, 0, -1, -1), aggregate, owned;
//   hand over:  not wanted, owned, and a stock frontend whose reason is 0 is up -> drop ownership, release nothing
//               (that frontend now holds the pause; stealing it would unfreeze its world);
//   release:    not wanted and owned -> release(0, -1), clear the Wait input lock, aggregate, not owned. The lock
//               clear runs on every release, even in Active mode: a reason taken in Wait and released after a switch
//               to Active would otherwise leave the lock set.
// A stock frontend with another reason (the main menu's 0xD, the leader select's 2) makes "wanted" false at once:
// that frontend's own reason keeps the world paused and stock never combines reason 0 with them.
//
// "Owned implies release when not wanted" outlives the WAIT bit: it runs on every UT-01 call in LIVE mode, so the
// kill switch (which clears every subsystem bit), a stale epoch, a drop-out, an area load and a co-op thread that
// stops publishing all release a held pause within one UI tick. A co-op thread stalled past the liveness window (the
// UT-01 handler itself no longer runs) is covered by release_if_owned(), which the UI-tick row runs as its stale
// handler. The kill switch's ordered disarm also waits for
// owned() to read false (control.cpp). Nothing here allocates, locks or blocks; a game read that fails changes
// nothing that frame.
#pragma once

#include <stdint.h>

#include "patch/detour.h"

namespace ilc::wait {

constexpr uint32_t SEATS = 4;
constexpr uint64_t FRESH_MS = 100;  // a request older than this is no request (about five co-op beats)

// ---- the rule (pure) --------------------------------------------------------------------------------------------
enum seat_role : uint8_t {
    ROLE_ABSENT = 0,       // not in the session, parked, toggled off, no character, or its character is down
    ROLE_PARTICIPANT = 1,
    ROLE_BENCHED = 2,      // playing, but no character was free for it: never counts
};
struct seat_view {
    uint8_t role;          // seat_role
    uint8_t open;          // the seat's co-op battle menu is not closed (a list page or a target page)
};
static_assert(sizeof(seat_view) == 2, "seat_view layout (host tests build it)");

// True when at least one seat participates and every participating seat's menu is open.
bool pause_wanted(const seat_view seats[SEATS]);

// The stock frontend id -> pause reason table (0x255290 restated): -1 = no reason. Ids 1 and 4 depend on the mode.
int32_t reason_of(uint32_t id, bool active_mode);

// The game-thread decision (pure; step() feeds it the game's state).
struct engine_in {
    uint8_t owned;         // the engine took the reason earlier and has not given it back
    uint8_t readable;      // every game read below succeeded
    uint8_t held;          // reason 0's bit is set
    uint8_t requested;     // the co-op request is set and fresh
    uint8_t live;          // WAIT may act
    uint8_t active_mode;   // the battle setting is Active (an unreadable setting reads Active)
    uint8_t in_play;       // game mode 2
    uint8_t frontend_on;   // a stock frontend is up
    int8_t frontend_reason;// its pause reason (-1 none)
    uint8_t reserved[3];
};
static_assert(sizeof(engine_in) == 12, "engine_in layout (host tests build it)");

enum action : uint8_t {
    ACT_NONE = 0,
    ACT_ACQUIRE = 1,
    ACT_RELEASE = 2,
    ACT_HANDOVER = 3,      // ownership dropped, nothing released
};
struct engine_out {
    uint8_t action;        // action
    uint8_t owned;         // after this call
    uint8_t wanted;
    uint8_t revalidated;   // ownership was dropped because the bit had been cleared by someone else
    uint8_t borrowed;      // wanted, and the bit is held by someone else: nothing done
    uint8_t reserved[3];
};
static_assert(sizeof(engine_out) == 8, "engine_out layout");

engine_out decide(const engine_in& in);

// ---- co-op thread ---------------------------------------------------------------------------------------------
struct request_record {
    patch::record_head head;   // epoch = session epoch, flags = 1 when requested, generation = session generation
    uint64_t published_ms;     // module clock
};
static_assert(sizeof(request_record) == 24, "request_record layout");

void publish(uint32_t epoch, uint64_t generation, bool requested, uint64_t now_ms);
void withdraw();               // no request at all (the engine releases what it owns at its next call)

// ---- any thread -----------------------------------------------------------------------------------------------
bool owned();                  // the engine holds reason 0 (game thread writes, others read)
bool enabled();                // attach found the game's functions as pinned

// ---- game thread (the UI tick, LIVE mode) ------------------------------------------------------------------------
// The engine's whole turn: read, decide, call. `epoch` = the liveness epoch the dispatcher sampled.
void step(uint32_t epoch);
// DRY mode calls no game function; it only counts a reason still owned (never expected: DRY is a boot state).
void step_dry();
// The UI tick while the liveness record is stale (the co-op thread stopped beating for over 2 s: the normal handler
// does not run). A pause the engine owns is given back the way step() would (release, lock clear, aggregate), or
// handed over to a stock reason-0 frontend; one already cleared by someone else is dropped. Nothing else is done:
// no request is trusted, nothing is acquired. True when it owned the reason on entry.
bool release_if_owned();

// ---- boot (before arming) -----------------------------------------------------------------------------------------
// Checks the five functions' first bytes at `image_base`. False: the engine never calls anything (owned() stays
// false; co-op menus still work, Wait mode just pauses whenever stock would, i.e. never for co-op menus).
bool attach(uintptr_t image_base);

struct counters {
    volatile uint64_t steps;
    volatile uint64_t acquired;
    volatile uint64_t released;
    volatile uint64_t handed_over;
    volatile uint64_t borrowed;     // UI ticks spent borrowing someone else's reason 0
    volatile uint64_t revalidated;
    volatile uint64_t unreadable;   // UI ticks that could not read the game's state (nothing done)
    volatile uint64_t stale;        // UI ticks whose request was set but stale (another epoch, or too old)
    volatile uint64_t dry_owned;
    volatile uint64_t prologue_mismatch;
    volatile uint64_t published;
    volatile uint64_t stale_released;  // pauses given back (or handed over) by release_if_owned()
};
const counters* stats();

// ---- host tests ------------------------------------------------------------------------------------------------
struct game_fns {
    void (*acquire)(int32_t reason, int32_t a, int32_t b, int32_t c, int32_t d, int32_t e);
    void (*release)(int32_t reason, int32_t all);
    void (*aggregate)();
    void (*clear_lock)();
};
void test_set_game_fns(const game_fns* fns);   // nullptr: the game's own (relocated from the VAs)
void test_set_image_base(uintptr_t image_base);
void test_set_owned(bool owned);
void test_force_enabled(int32_t forced);       // -1 = attach decides
void test_reset();
engine_out test_last();                        // the last step's decision
const volatile uint32_t* test_owned_address(); // emulator tests read the flag out of module memory
patch::record_slot<request_record>& slot();

}  // namespace ilc::wait
