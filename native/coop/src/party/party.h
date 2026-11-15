// Ivalice Legacy co-op module: seat assignment, benching, preferences, swaps and leader changes (PARTY subsystem).
//
// Who controls which character is the session core's identity (session.h): it assigns characters to playing
// seats in seat order (a seat's preferred character first, then the first free member in party order), keeps
// every seat on its character through party reorders, benches a playing seat for which no regular is free
// (SEATF_BENCHED: it keeps its seat and input and claims the next free member, Player 1 > Player 2 > Player 3),
// and adopts a party or leader change the game or another mod makes once it has settled. This subsystem adds the
// changes a player asks for, and makes every change of hands atomic:
//
//  * Co-op thread, step() once per heartbeat after the other subsystems: takes "change character" requests (the
//    Mod Menu's move-seat command through the input service, or request_change()), plans each one with the rules
//    in assign.h against the published session record and a world read, and queues one OP_PARTY_CHANGE job. A
//    request that cannot run yet (a load, a cutscene, a menu, the world gate closed, a stale record) waits and is
//    planned again, up to REQUEST_WAIT_MS; a refusal that waiting cannot fix ends it. It also retires the
//    steering remap (remap.h) once the session has adopted the change and the steering record names the new
//    characters.
//
//  * Game thread, OP_PARTY_CHANGE in the UI-tick drain (world-requiring): re-reads the party, the leader, the seat
//    actors and the frame state (no area change pending or in progress, no script roster edit, field ready, no
//    frontend menu, no controlled-actor change already pending this frame, no summon, chocobo or scripted mode,
//    the same location as when the job was planned), plans again and refuses unless it is the same change. Then,
//    in this one callback: the leader setter 0x327850 for a leader change (the game applies the new controlled
//    actor at the end of this frame), the steering remap for every guest that changed hands (its pad drives the
//    new character from the next frame on), and the change posted to the session, which adopts it without the
//    identity settle.
//
//  * Towns (the engine's town mode with Companions' "Party In Towns" arm): only controlled members have a body, so an
//    unseated member of the roster has empty current and HUD slots. Player 1's leader change and a guest's take-free
//    onto such a member are planned against the roster (default layout) instead: the member keeps its roster slot,
//    its member record says whether it can lead, and it is never busy. The operation then writes the leader (P1),
//    lets TW-01 keep the member (towns::allow_pending, a guest), runs the town rebuild and steers the body it bound,
//    all in the same callback; if the rebuild does not run, the leader write is undone and the request retries
//    after TOWN_RETRY_MS, at most TOWN_TRIES times.
//    A member that is not in the roster at all is still refused (REFUSE_NOT_IN_PARTY).
//
//  * Game thread, the leader watcher in the UI tick (before the drain, and after the stock UI tick through the
//    call row's after-handler): every writer of the leader ends in the same field, so comparing it with the
//    value last seen catches a change by the game, a script or another mod (The Insurgent's Companions leader
//    cycle in towns). When the new leader is a guest's character and Player 1's character could still lead (not
//    a KO promotion), Player 1 and that guest exchange characters in the same callback: remap and session post,
//    exactly as for a requested swap. Any other external change is left to the session's settled adoption. In a
//    town, a cycle onto a member who has no body yet runs the town rebuild in the same callback, so the game's
//    fallback never hands Player 1's pad to a guest's character.
//
// Arm bit: ILC_SUBSYS_PARTY. With it clear (or the module DRY, disarmed or faulted) nothing is queued, the job is
// refused, the watcher only follows the leader without acting, and the detours ignore the remap.
#pragma once

#include <stdint.h>

#include "party/assign.h"
#include "party/remap.h"
#include "patch/detour.h"
#include "runtime/game_thread.h"

namespace ilc::party {

constexpr uint16_t OP_PARTY_CHANGE = 16;   // game-thread operation number (runtime/game_thread.h table)
constexpr uint32_t CHANGE_TTL_MS = 200;
constexpr uint64_t REQUEST_WAIT_MS = 5000; // a request that could not run by then ends (TIMED_OUT)
// A town take whose rebuild did not run (OP_TOWN_REBUILD) is planned again only after TOWN_RETRY_MS, and the
// TOWN_TRIES-th such result ends the request (REFUSED). Each try runs the leader setter and its undo, and both
// re-select the controlled actor (0x326500), so a failing rebuild must not be retried on every beat. The rebuild
// cannot go first: its synchronous bind (0x233CF0 -> TW-01) and tail (0x326500) read the leader already.
constexpr uint64_t TOWN_RETRY_MS = 1000;
constexpr uint8_t TOWN_TRIES = 3;

// OP_PARTY_CHANGE results: >= 1 done, the refusals of assign.h (-16..-23), and these.
enum op_result : int32_t {
    CHANGE_APPLIED = 1,
    CHANGE_ALREADY = 2,
    OP_NO_WORLD = -32,          // the session record is missing, of another epoch, or not a valid world
    OP_AREA_PENDING = -33,      // an area change is requested or in progress
    OP_ROSTER_EDIT = -34,       // a script is editing the party
    OP_NOT_FIELD = -35,         // field not ready, or a frontend menu is open
    OP_APPLY_PENDING = -36,     // a controlled-actor change already waits for this frame's end
    OP_MODE = -37,              // summon, chocobo or a scripted mode
    OP_LOCATION_MOVED = -38,    // the location changed (or is being reloaded) since the job was planned
    OP_STALE = -39,             // the party, leader or seats are not what the job was planned on
    OP_UNREADABLE = -40,        // a read failed
    OP_BAD_ARGS = -41,
    OP_SETTER_FAILED = -42,     // the leader did not read back as written
    OP_TOWN_BUSY = -43,         // a town take: a re-materialize task is in flight, or no player control right now
    OP_TOWN_REBUILD = -44,      // a town take: the rebuild did not run (the leader write, if any, was undone);
                                // retried after TOWN_RETRY_MS, at most TOWN_TRIES times
};

// Request states (debug view).
enum request_state : uint8_t {
    REQ_IDLE = 0,
    REQ_WAITING = 1,            // planned again each step until it can be queued
    REQ_QUEUED = 2,             // a job is in flight
    REQ_DONE = 3,               // applied (or already so)
    REQ_REFUSED = 4,            // a refusal waiting cannot fix
    REQ_TIMED_OUT = 5,
};

struct counters {
    volatile uint64_t steps;
    volatile uint64_t requests;          // change requests taken (menu command or request_change)
    volatile uint64_t pushed;
    volatile uint64_t push_refused;
    volatile uint64_t applied;           // game thread: OP_PARTY_CHANGE changed hands
    volatile uint64_t already;
    volatile uint64_t refused;           // game thread: the operation refused
    volatile uint64_t requests_refused;  // co-op thread: a request ended refused
    volatile uint64_t requests_timed_out;
    volatile uint64_t leader_writes;     // game thread: the leader setter ran
    volatile uint64_t remaps;            // game thread: remaps published
    volatile uint64_t remaps_retired;    // co-op thread: retired once confirmed
    volatile uint64_t remaps_expired;    // co-op thread: retired after REMAP_MAX_MS without confirmation
    volatile uint64_t watcher_changes;   // game thread: the leader changed outside our jobs
    volatile uint64_t external_swaps;    // ... onto a guest's character: an atomic Player 1 / guest exchange
    volatile uint64_t external_left;     // ... onto anyone else: left to the session's settled adoption
    volatile uint64_t ko_promotions;     // ... onto a guest's character while Player 1's could not lead
    volatile uint64_t watcher_after;     // the watcher's second look, after the stock UI tick
    volatile uint64_t town_rebuilds;     // the watcher ran the town rebuild for a member without a body
    volatile uint64_t town_takes;        // OP_PARTY_CHANGE gave a member without a body (town) one, then the change
};

// ---- boot (before gtq::seal) ----------------------------------------------------------------------------------
// Registers OP_PARTY_CHANGE (PARTY, world-requiring) and the watcher's after-handler on the UI-tick row. False if
// either cannot be registered (the boot then latches PARTY as faulted).
bool attach();

// ---- co-op thread ---------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void step(uint64_t now_ms);
void stop();

// ---- any thread -----------------------------------------------------------------------------------------------
// Ask for `member` on `seat` (0 = Player 1: a leader change; a guest: change character). The newest request per
// seat wins. Taken at the next step.
void request_change(uint32_t seat, uint32_t member);
const counters* stats();

// ---- game thread (the UI tick) --------------------------------------------------------------------------------
void tick_before();            // before the drain, LIVE only
bool after_wanted();           // the UI-tick handler asks for the after-handler (LIVE and PARTY may act)
void tick_after(patch::ctx* c, uint32_t epoch);
int32_t op_change(const gtq::job& j);

// ---- host tests -----------------------------------------------------------------------------------------------
struct request_view {
    uint8_t state;             // request_state
    uint8_t target;
    uint8_t attempts;
    uint8_t town_fails;        // OP_TOWN_REBUILD results so far (TOWN_TRIES ends the request)
    int32_t last_result;       // the last plan refusal, push status or job result
    uint32_t ticket;
    uint32_t reserved2;
};
struct debug_view {
    request_view seat[SEATS];
    uint32_t watcher_leader;   // the leader the watcher saw last (0xFFFF unknown)
    uint32_t watcher_epoch;
    uint32_t remap_seq;        // the last remap published
    uint32_t remap_retired;
    uint32_t adopted_seq;      // session::adopted_seq()
    uint32_t posted_seq;       // the last change posted to the session
};
using setter_fn = void (*)(uint8_t member);
using ready_fn = int32_t (*)();
void test_set_game_fns(setter_fn setter, ready_fn field_ready);  // nullptr = the game's functions
void test_set_image_base(uintptr_t image_base);
void test_force_live(int32_t forced);  // -1 = the patch layer decides whether PARTY may act; 0/1 forced
void test_debug(debug_view* out);
void test_reset();
const volatile uint32_t* test_posted_seq();  // the last change posted to the session (emulator tests read it)
// The newest request_change() for a seat that the next step will take: its sequence (0 = never asked) and member.
void test_api_request(uint32_t seat, uint32_t* seq, uint32_t* member);

}  // namespace ilc::party
