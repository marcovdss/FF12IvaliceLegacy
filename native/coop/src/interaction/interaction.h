// Ivalice Legacy co-op module: guests and the world (gateways, doors, chests, crystals, NPC talk, shops, loot).
//
// The game has one interaction source (the controlled actor), one interaction target (the descriptor) and one
// consumer per frame. Everything here lets a guest's character use them without taking P1's away. Three parts:
//
//  * Co-op thread, step() once per beat after the session step:
//      - publishes the interaction record: per guest that steers its character, the actor manager entry and the
//        skeleton keep (compared as pointers by the detours) and the stable handle;
//      - composes the guests' prompt record (prompt.h): what each manual guest's own scan found, as the native
//        marker's level, drawn over that guest's character by IX-21;
//      - turns a guest's CONFIRM press into one request for the consumer window: the
//        object the guest's own scan found (door, chest, crystal), or else an NPC near the guest (talk, shops);
//      - after a request ran, holds the guest's interaction while its dialog, menu or shop is up. While held, the
//        guest's character stands still and, once a dialog or menu has opened, that guest drives the native menus
//        with their own pad (input isolation feeds it through the game's own pad path; P1 waits);
//      - Player 1 is never the owner here: P1 uses the stock path untouched.
//
//  * Game thread, the detours (hooks.cpp). Each one compares the game's registers with the record first, runs
//    only for a seat-controlled actor or inside our own nested call, and otherwise runs the original instructions:
//      MV-07 gateway trigger call (call row): the stock pass for the controlled actor, then the same native pass
//            once per steering guest with the controlled handle named as that guest's, restored after each pass.
//      MV-08 gateway collision class store: a guest's skeleton gets the leader's class (0) instead of follower
//            class 1, so trigger volumes treat it like the leader.
//      IX-01 dual scan call (call row): before the stock scan for P1, the same native scan once per steering guest
//            ("pass"), with IX-02..IX-10 redirecting its source, gates and publication to that guest.
//      IX-02 publisher entry: during a pass, the publication goes to the guest's private slots, not the prompt's.
//      IX-03 / IX-04 scan sources: during a pass, the guest's entry.
//      IX-05..IX-10 enable, range, broadphase, proximity and secondary filters: during a pass, measured for the
//            guest's entry instead of the controlled actor's.
//      IX-17..IX-19 NPC candidate guards: armed only while the window runs the exact NPC scan for a guest; the
//            NPCs' proximity state, timers and proximity scripts stay P1's.
//      IX-20 consumer call (call row): the window. With a request claimed: save the source, can-use byte and
//            descriptor; resolve the guest's entry from its handle now; publish the guest's object descriptor or
//            run the exact NPC scan from the guest; re-check the target entry immediately before the call (a stale
//            entry would be dereferenced as null by the consumer); call the consumer; restore everything. P1's own
//            consumer runs untouched in every other frame.
//      LT-01 loot ownership query call (call row): a seat-controlled actor's handle answers "party" (walk-over
//            loot), everything else asks the game.
//      IX-21 prompt drawer call (call row, optional; prompt.h): after P1's own prompt, the game's marker once more
//            over each guest whose private pass found something (the "!", or the usable marker).
//
//  * Nothing is written to the game outside those detours, and nothing at all while the subsystem may not act.
//
// Guests interact in towns too. A pending area change (0x1EED064 != -1) or an area transition in progress stops
// the gateway passes and refuses the window.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/detour.h"

namespace ilc::session {
class game_reader;
}

namespace ilc::interaction {

constexpr uint32_t SEATS = ILC_SEATS;
constexpr uint32_t ACTIVE_SEATS = ILC_ACTIVE_SEATS_MAX;
constexpr uint32_t SLOTS = ACTIVE_SEATS - 1;  // guest seats 1..ACTIVE_SEATS-1 -> slot seat-1

// Record head flags.
enum record_flag : uint32_t {
    IXF_GATEWAYS = 0x01,  // guests take trigger volumes (gateway passes and the collision class)
    IXF_ROUTING = 0x02,   // guests' own scans run (private passes)
    IXF_LOOT = 0x04,      // guests' characters pick up walk-over loot
};

struct ix_entry {
    uint64_t entry;       // actor manager entry (compare only)
    uint64_t skeleton;    // the entry's skeleton keep (compare only)
    uint32_t handle;      // the actor's stable handle
    uint16_t member;
    uint8_t seat;         // 1..ACTIVE_SEATS-1
    uint8_t active;
};
static_assert(sizeof(ix_entry) == 24, "ix_entry layout (host tests read it)");

struct ix_record {
    patch::record_head head;  // epoch = session epoch, flags = record_flag, generation = session generation
    uint32_t active;          // bit k: slot k is active
    uint32_t reserved;
    ix_entry slot[SLOTS];
};
static_assert(sizeof(ix_record) == 24 + 24 * SLOTS && sizeof(ix_record) <= 256,
              "small: copied at mid-function sites (inline copy limit: movement/planner.h)");

// What a guest's private pass found (the publication slots of its last pass that found anything).
struct ix_private {
    uint64_t resource[8];
    uint64_t text[8];
    uint32_t state[8];
    uint32_t handle[8];
    uint32_t tick;            // passes run (every registry pass counts)
    uint32_t mark;            // tick of the last pass that found something (0 = never)
    uint32_t source_handle;   // the guest's handle the pass ran for
    uint32_t epoch;           // the session epoch of the record the pass ran under
};
static_assert(sizeof(ix_private) == 208, "ix_private layout (host tests read it)");
constexpr uint32_t PRIVATE_STALE_PASSES = 4;  // a find older than this many registry passes is gone

// ---- the request the window runs ----------------------------------------------------------------------------------
enum policy : uint32_t {
    POLICY_LEADER = 1,    // an object (door, crystal, switch): P1's leader must be free (the game animates it)
    POLICY_TREASURE = 2,  // a chest: only the guest's own entry is needed
    POLICY_NPC = 3,       // talk (and the shops a talk opens): the exact NPC scan from the guest
};

enum request_state : uint32_t { REQ_IDLE = 0, REQ_ARMED = 1, REQ_CLAIMED = 2, REQ_DONE = 3 };

enum window_result : int32_t {
    WR_NONE = 0,
    WR_CONSUMED = 1,       // the consumer ran on the guest's target
    WR_TALK = 2,           // ... and the NPC is now in conversation
    WR_STALE = -16,        // made for another world or seat layout (a swap), or older than its deadline
    WR_BLOCKED = -17,      // area change pending or in progress, UI off, interactions suppressed, event busy
    WR_P1_BUSY = -18,      // the consumer's gate refuses P1's controlled actor (it acts, a menu is up)
    WR_LEADER_BUSY = -19,  // P1's leader handles a weapon or plays an execution motion
    WR_OWNER_BUSY = -20,   // the guest's own character is not free
    WR_SOURCE_GONE = -21,  // the guest's handle no longer resolves (or has no physics object)
    WR_NO_TARGET = -22,    // nothing usable near the guest
    WR_TARGET_STALE = -23, // the target entry vanished or is torn down: the consumer was not called
    WR_GUARDS_OFF = -24,   // the NPC guards are not armed: no exact scan from a guest
};

struct request {
    volatile uint32_t state;  // request_state
    uint32_t ticket;
    uint32_t epoch;
    uint32_t policy;
    uint32_t seat;
    uint32_t source_handle;   // the guest's actor handle
    uint32_t object_handle;   // object policies
    uint32_t object_state;
    uint64_t deadline_ms;     // module clock
    volatile int32_t result;  // window_result, written before state = DONE
    uint32_t target_handle;   // the descriptor handle the consumer ran on
    uint32_t touched;         // the window wrote the game's interaction state (and restored it)
    uint32_t generation;      // low 32 bits of the session generation the request was armed under
};
static_assert(sizeof(request) == 56, "request layout (host tests read it)");
constexpr uint32_t REQUEST_TTL_MS = 250;

// The window's calls into the game, replaceable by host tests (the default is the game's functions).
struct game_calls {
    uint64_t (*resolve)(uint32_t handle);
    void (*generic_scan)(int32_t registry);
    void (*exact_scan)(int32_t registry);
    void (*build_handle)();
    void (*consumer)();
    int32_t (*gate)(int32_t mode);
    uint64_t (*auxiliary)(uint64_t entry);
    void (*gateway)();
};

struct counters {
    volatile uint64_t steps;
    volatile uint64_t published;
    volatile uint64_t presses;            // guest CONFIRM presses seen
    volatile uint64_t requests;           // requests armed
    volatile uint64_t requests_expired;   // never claimed before the deadline
    volatile uint64_t results[12];        // by window_result: index = result >= 0 ? result : -result - 13
    volatile uint64_t holds;              // holds started
    volatile uint64_t holds_ended[5];     // by hold_end
    volatile uint64_t gateway_passes;     // MV-07: guest passes run
    volatile uint64_t gateway_skipped;    // MV-07: guest passes not run (P1's handle, area pending, no handle)
    volatile uint64_t class_rewritten;    // MV-08
    volatile uint64_t private_passes;     // IX-01: guest scans run
    volatile uint64_t private_finds;      // ... that published something
    volatile uint64_t windows;            // IX-20: requests claimed
    volatile uint64_t guard_hits;         // IX-17..19 armed decisions
    volatile uint64_t loot_owned;         // LT-01 answers for a guest
    volatile uint64_t menu_opens;         // CONFIRM presses that found nothing and asked for the guest's menu
};

// ---- boot (before gtq::seal) ----------------------------------------------------------------------------------
// Registers every handler and arms the sites, all or nothing: on failure nothing stays armed and false is returned.
bool attach();

// ---- co-op thread ---------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void step(uint64_t now_ms);
void stop();  // withdraws the record, cancels a waiting request, ends the hold

// ---- any thread ----------------------------------------------------------------------------------------------
patch::record_slot<ix_record>& slot();
// The seat whose guest drives the native menus now (0 = Player 1 / nobody): input isolation reads it.
uint32_t menu_driver();
// The co-op battle menus, every beat before step(): guest seats (bit s = session seat s) whose menu owns their
// CONFIRM this beat (open before or after the menus handled this beat's edges). Their CONFIRM presses are menu
// presses, never interaction requests. Counts only for `epoch`.
void set_menu_seats(uint32_t epoch, uint32_t seats);
uint32_t menu_seats(uint32_t epoch);  // what set_menu_seats left for `epoch` (0 for any other)
// A guest's CONFIRM that found nothing to use opens that guest's battle menu (Together's order: an interaction first,
// then NPC talk, then the menu). Bit s = seat s pressed CONFIRM with its menu closed and: the window answered
// WR_NO_TARGET; or the guest's scan found an object the game does not offer yet; or no object was found and the NPC
// scan could not run (WR_P1_BUSY, WR_GUARDS_OFF); or guests may not interact (settings guest_interactions 0, or the
// session's interaction gate is off). The frontend takes it on its next beat (co-op thread) and opens the menu through
// its usual gates (eligible, reopen delay, not hidden, menu still closed). Counts only for `epoch`; taking clears it.
uint32_t take_menu_opens(uint32_t epoch);
// Seats (bit s) whose character an interaction holds still (a request in flight or a hold): movement reads it.
uint32_t hold_seats(uint32_t epoch);
// The shop is open now (frontend id 10, active), read through the fault-safe probe.
bool shop_open_now();
// Co-op thread readers for other subsystems (the firewall's P1 prompt check): the registry entry a handle names
// (the engine's checks, 0 = none), and whether a handle names a mimic asleep as a chest.
uintptr_t resolve_handle(session::game_reader& r, uint32_t handle);
bool sleeping_mimic_handle(session::game_reader& r, uint32_t handle);
const counters* stats();

// ---- the detour handlers (exposed for host tests) -------------------------------------------------------------
int32_t mv07(patch::ctx* c, uint32_t epoch);
void mv07_after(patch::ctx* c, uint32_t epoch);
int32_t mv08(patch::ctx* c, uint32_t epoch);
int32_t ix01(patch::ctx* c, uint32_t epoch);
int32_t ix02(patch::ctx* c, uint32_t epoch);
int32_t ix03(patch::ctx* c, uint32_t epoch);
int32_t ix04(patch::ctx* c, uint32_t epoch);
int32_t ix05(patch::ctx* c, uint32_t epoch);
int32_t ix06(patch::ctx* c, uint32_t epoch);
int32_t ix07(patch::ctx* c, uint32_t epoch);
int32_t ix08(patch::ctx* c, uint32_t epoch);
int32_t ix09(patch::ctx* c, uint32_t epoch);
int32_t ix10(patch::ctx* c, uint32_t epoch);
int32_t ix17(patch::ctx* c, uint32_t epoch);
int32_t ix18(patch::ctx* c, uint32_t epoch);
int32_t ix19(patch::ctx* c, uint32_t epoch);
int32_t ix20(patch::ctx* c, uint32_t epoch);
int32_t lt01(patch::ctx* c, uint32_t epoch);
int32_t row_of(const char* id);

// ---- host tests ----------------------------------------------------------------------------------------------
struct debug_view {
    uint32_t pass_active;     // a private pass is running (game thread)
    uint32_t pass_slot;
    uint64_t pass_entry;
    uint32_t guard_active;    // the window's exact scan is running
    uint32_t driver;
    uint32_t hold_active;
    uint32_t hold_seat;
    uint32_t hold_opened;
    uint32_t hold_target;
    uint32_t last_end;        // hold_end of the last hold
    int32_t last_result;      // window_result of the last request
    uint32_t last_policy;
};
void test_debug(debug_view* out);
void test_reset();
void test_set_image_base(uintptr_t image_base);
void test_set_calls(const game_calls* calls);           // nullptr = the game's functions
void test_set_presses(uint32_t (*source)(uint32_t epoch)); // bit s = seat s pressed CONFIRM; nullptr = live input
void test_set_pass(uint32_t active, uint32_t slot, uint64_t entry);  // game-thread pass state, for handler tests
void test_set_guard(uint32_t active);
void test_publish(const ix_record& r);
void test_withdraw();
bool test_private(uint32_t slot, ix_private* out);
void test_publish_private(uint32_t slot, const ix_private& p);
request* test_request();
void test_fail_attach(bool fail);  // attach() arms its rows, then fails and disarms them all (the boot's fallback)
void test_note_menu_open(uint32_t epoch, uint32_t seats);  // as a CONFIRM that found nothing (take_menu_opens)

}  // namespace ilc::interaction
