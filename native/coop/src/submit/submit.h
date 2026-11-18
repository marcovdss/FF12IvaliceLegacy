// Ivalice Legacy co-op module: a seat's battle-menu requests reach the game on the game thread.
//
// A confirmed action, a Gambits toggle, the summon owner's Dismiss, the combat indicator and the menu sounds are
// game-thread operations (runtime/game_thread.h), drained in the UI-tick callback (UT-01). Nothing here waits for
// the game thread: a request is queued and the co-op thread collects its one terminal result on a later beat.
//
// Per seat there is one request slot, a small state machine:
//   IDLE --request--> QUEUED --terminal result--> IDLE (the result is delivered: sound, Gambits state, summon owner,
//                                                       combat indicator)
//                     QUEUED --no result by its deadline--> QUARANTINED
//   QUARANTINED --the same ticket's terminal result--> IDLE (the late result is recorded, never applied)
//   QUARANTINED --a lost result (overwritten)--> IDLE once the request's deadline has passed (or at a new epoch):
//                                                 the FIFO queue had already finished the job, so nothing can still act
// While a slot is QUEUED or QUARANTINED the seat's next request is refused (busy / quarantined), so a guest can never
// have two requests in flight or act on a stale answer.
//
// Right before each game call, in the same callback, the operation proves again what the request was built from:
// the job's epoch and generation (the queue, then the published session record), the issuer seat still bound to the
// same party id, handle and actor (the actor still carries the handle; its record is the party block's for that id),
// a status that allows a command, the target still alive under the same handle (or the reserve member still outside
// the formation), the character still able to use the action (the game's own availability check for magicks and
// technicks) and the game's admission check (0x303BD0: MP, charges, gil). Any difference rejects without calling.
//
// Gambits toggle through the game's own query and setter with the identity proof repeated before the setter and the
// result verified (record bit 2 changed, every other bit kept); result codes and sounds (0x12 on, 0x13 off) as the
// menu model defines them. Dismiss goes through the stock Dismiss (0x3138D0) and only for the seat whose character
// summoned the Esper: a summon queued by a seat is remembered for 20 s; when the Esper appears within that window the
// seat's character owns it, otherwise the leader (or the first regular in the party) does.
//
// The combat indicator after a submitted action follows the game's own timing: first try 75 ms after the submit
// (at once when the target is the issuer), a retry 350 ms after an attempt that did not take (a node that is
// starting gets 400 ms), and it gives up after 30 s or as soon as the issuer or the target is gone. Menu sounds play
// per seat when that seat's sounds are on, at most one per frame (the first one wins).
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "menu/menus.h"
#include "menu/model.h"
#include "runtime/game_thread.h"
#include "targets/targets.h"

namespace ilc::submit {

constexpr uint32_t SEATS = 4;
constexpr uint16_t OP_MENU_ACTION = 17;      // game-thread operation numbers (runtime/game_thread.h table)
constexpr uint16_t OP_MENU_GAMBITS = 18;
constexpr uint16_t OP_MENU_DISMISS = 19;
constexpr uint16_t OP_MENU_INDICATOR = 20;
constexpr uint16_t OP_MENU_SOUND = 21;

constexpr uint32_t ACTION_TTL_MS = 400;      // a job older than this is rejected, never run late
constexpr uint32_t WAIT_MS = ACTION_TTL_MS + gtq::EXPIRY_MARGIN_MS;  // no terminal result by then: quarantine
constexpr uint32_t INDICATOR_TTL_MS = 200;
constexpr uint32_t SOUND_TTL_MS = 100;
constexpr uint32_t INDICATOR_FIRST_MS = 75;
constexpr uint32_t INDICATOR_RETRY_MS = 350;
constexpr uint32_t INDICATOR_GRACE_MS = 400;
constexpr uint32_t INDICATOR_GIVE_UP_MS = 30000;
constexpr uint32_t SUMMON_OWNER_WINDOW_MS = 20000;
constexpr uint8_t OWNER_NONE = 0xFF;

// ---- what operations return (>= 1 done, <= -16 rejected; runtime/game_thread.h) ----------------------------------
enum op_result : int32_t {
    OPR_DONE = 1,                // submitted / dismissed / sound played / indicator node returned
    OPR_GAMBITS_OFF = 1,
    OPR_GAMBITS_ON = 2,
    OPR_NO_NODE = 2,             // the indicator call returned no node
    REJ_BAD_ARGS = -16,
    REJ_NO_WORLD = -17,          // the session record is not the job's epoch/generation, or no world
    REJ_STALE_ISSUER = -18,      // the seat's character, handle or actor changed
    REJ_STALE_TARGET = -19,      // the target is gone or is another actor now (or the reserve member joined)
    REJ_STATUS = -20,            // a status refuses the command (Berserk: anything but Attack)
    REJ_UNAVAILABLE = -21,       // the game's availability check says the character cannot use it
    REJ_GAME_REFUSED = -22,      // the game's submit function returned an error
    REJ_NOT_OWNER = -23,         // Dismiss from a seat whose character did not summon the Esper
    REJ_NO_SUMMON = -24,
    REJ_DISABLED = -25,          // the game functions failed their check at attach
};
constexpr int32_t REJ_ADMISSION = -0x100;  // minus the admission code (0x12..0x15): -0x112..-0x115
constexpr int32_t REJ_GAMBIT = -0x200;     // minus a menu::gambit_result code: -0x210..-0x216

// ---- requests --------------------------------------------------------------------------------------------------------
enum request_kind : uint8_t {
    RK_ACTION = 1,               // target: a handle (the issuer's own for self-only actions)
    RK_RESERVE = 2,              // target: a party member outside the formation (no world handle)
    RK_DISMISS = 3,
    RK_GAMBITS = 4,
};

enum request_flag : uint8_t {
    RF_LICENSED = 0x01,          // a magick or technick: the game's availability check runs before the call
    RF_INDICATOR = 0x02,         // show the combat indicator once it is submitted
    RF_WORLD = 0x04,             // the target is a world actor (Foes / Neutrals): it must still be alive at the call
    RF_REVIVE = 0x08,            // a revival: a world target is not required to be alive
};

struct action_request {
    uint16_t action;
    uint8_t kind;                // RK_ACTION or RK_RESERVE
    uint8_t flags;               // request_flag
    uint32_t target;             // handle (RK_ACTION)
    uint64_t target_actor;       // compare only (RK_ACTION, not self)
    uint16_t reserve_member;     // party id (RK_RESERVE)
    uint16_t reserved[3];
};
static_assert(sizeof(action_request) == 24, "action_request layout (host tests build it)");

enum submit_status : int32_t {
    SUB_QUEUED = 1,
    SUB_BUSY = -1,               // the seat's previous request has no result yet
    SUB_QUARANTINED = -2,        // the seat's previous request timed out and its result has not come back
    SUB_NOT_SEAT = -3,
    SUB_NO_ISSUER = -4,          // the seat has no bound character this beat
    SUB_NOT_LIVE = -5,           // the menus' subsystem may not act, or the game functions failed their check
    SUB_FULL = -6,               // the game-thread queue is full
    SUB_NO_WORLD = -7,
    SUB_BAD_REQUEST = -8,
    SUB_NOT_OWNER = -9,          // Dismiss, and this seat's character does not own the Esper
    SUB_REFUSED = -10,           // another push refusal
    SUB_GAMBITS_REFUSED = -11,   // the Gambits model refused (its code is in the seat's gambit state)
};

enum slot_state : uint8_t {
    SLOT_IDLE = 0,
    SLOT_QUEUED = 1,
    SLOT_QUARANTINED = 2,
};

struct slot_view {
    uint8_t state;               // slot_state
    uint8_t kind;                // request_kind of the request in flight (or the last one)
    uint16_t action;
    uint32_t ticket;
    uint32_t epoch;
    int32_t last_result;         // the last terminal result (operation code or queue rejection)
    uint64_t generation;
    uint64_t enqueued_ms;
    uint64_t deadline_ms;
    uint32_t completed;          // results delivered
    uint32_t quarantined;        // times the slot entered quarantine
    uint32_t released;           // quarantines released by their late result
    uint32_t late_result;        // the late result that released the last quarantine
};
static_assert(sizeof(slot_view) == 56, "slot_view layout (host tests read it)");

struct indicator_view {
    uint8_t active;
    uint8_t in_flight;
    uint16_t attempts;
    uint32_t source;
    uint32_t target;
    uint32_t ticket;
    uint64_t node;
    uint64_t next_try_ms;
    uint64_t attempt_ms;
    uint64_t deadline_ms;
    int32_t last;                // last outcome: 1 shown, 2 expired, 3 cancelled, 0 none yet
    uint32_t reserved;
};
static_assert(sizeof(indicator_view) == 56, "indicator_view layout");

struct summon_view {
    uint8_t active;              // an Esper is in the active party
    uint8_t owner;               // party id of the owner, OWNER_NONE
    uint8_t pending;             // party id remembered from a summon request, OWNER_NONE
    uint8_t source;              // 0 none, 1 the request, 2 the fallback (leader or first regular)
    uint32_t reserved;
    uint64_t pending_until_ms;
};
static_assert(sizeof(summon_view) == 16, "summon_view layout");

struct counters {
    volatile uint64_t requests;
    volatile uint64_t queued;
    volatile uint64_t refused;        // refused before the queue (busy, quarantined, no issuer, push refusals)
    volatile uint64_t completed;
    volatile uint64_t quarantined;
    volatile uint64_t released;
    volatile uint64_t lost;           // results lost (the slot stays quarantined until the next epoch)
    volatile uint64_t op_ran;         // operations that reached a game call
    volatile uint64_t op_rejected;    // operations that rejected before any game call
    volatile uint64_t indicators_shown;
    volatile uint64_t indicators_expired;
    volatile uint64_t indicators_cancelled;
    volatile uint64_t sounds;
    volatile uint64_t sounds_dropped;
    volatile uint64_t prologue_mismatch;
};

// ---- boot ----------------------------------------------------------------------------------------------------------
// Before gtq::seal(): checks the game functions' first bytes at `image_base`, then registers the operations
// (FIREWALL_MENUS). False (nothing registered, every request refused) on a mismatch or a taken operation number.
bool attach(uintptr_t image_base);
bool enabled();

// ---- co-op thread ----------------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void configure_from_settings(const ilc_settings& s);   // per-seat menu sounds
void step(uint64_t now_ms);
void stop();

// A confirmed action for the seat's character (RK_ACTION / RK_RESERVE).
int32_t request(uint32_t seat, const action_request& rq, uint64_t now_ms);
// The owner's Dismiss.
int32_t request_dismiss(uint32_t seat, uint64_t now_ms);
// Toggle the seat's Gambits: the model's checks (menu::gambit_request on sm->gambits), then the operation. The result
// lands in sm->gambits (menu::gambit_complete) and its sound plays.
int32_t request_gambits(uint32_t seat, menu::seat_menu* sm, uint64_t now_ms);
// A model event: EV_SUBMIT (the target resolved from the candidates the seat was shown) or EV_TOGGLE_GAMBITS. Other
// events only play their sound. Returns a submit_status, or 0 for an event that asks for nothing.
int32_t handle_event(uint32_t seat, menu::seat_menu* sm, const menu::menu_event& e,
                     const targets::candidate_list& shown, uint64_t now_ms);

// The sound a menu event plays (0 = none).
uint32_t sound_for(uint8_t event_kind);
// Queue a menu sound for the seat (dropped when its sounds are off, the id is not allowed, or one is pending).
void play(uint32_t seat, uint32_t sound_id);
// A sound that is no seat's menu sound (the chest sound of the treasure log): queued for the game thread like a menu
// sound (at most one per beat, the UI sound function behind the pinned prologue), whatever the seats' sound settings.
// False when the slot is taken this beat or the id is not one of SOUND_IDS (the caller may try again next beat).
bool play_system(uint32_t sound_id);

// The party id that owns the Esper out now (OWNER_NONE): its menu shows Dismiss instead of Mist.
uint32_t summon_owner();

const counters* stats();
void read_slot(uint32_t seat, slot_view* out);
void read_indicator(uint32_t seat, indicator_view* out);
void read_summon(summon_view* out);

// ---- game thread -----------------------------------------------------------------------------------------------------
int32_t op_action(const gtq::job& j);
int32_t op_gambits(const gtq::job& j);
int32_t op_dismiss(const gtq::job& j);
int32_t op_indicator(const gtq::job& j);
int32_t op_sound(const gtq::job& j);

// ---- pure, exposed for host tests --------------------------------------------------------------------------------------
// The summon owner rule: active = an Esper in the current party; current = slots 0..3; leader = the native leader.
void summon_update(summon_view* s, bool active, const uint16_t current[4], uint32_t leader, uint64_t now_ms);
// Map an operation / queue result of a Gambits job to the model's code and the final state (0/1, 0xFF unknown).
void gambit_outcome(int32_t result, int32_t* code, uint32_t* final_enabled);

// ---- host tests ------------------------------------------------------------------------------------------------------
struct game_fns {
    int32_t (*submit)(uint32_t issuer, uint32_t target, uint16_t action, uint32_t flag);
    int32_t (*reserve)(uint32_t issuer, uint32_t target, uint8_t member, uint16_t action, uint32_t flag);
    void (*dismiss)(uint32_t flag);
    int32_t (*admission)(uintptr_t actor, uint16_t action);
    int32_t (*available)(uint8_t member, uint16_t action, int32_t unused);
    int32_t (*query)(uint8_t member);
    void (*set)(uint8_t member, int32_t on);
    uintptr_t (*record)(uint8_t member);
    uintptr_t (*indicator)(uintptr_t ui, uint32_t source, uint32_t target);
    int32_t (*sound)(uint32_t id);
};
void test_set_game_fns(const game_fns* fns);   // nullptr: the game's own (relocated from the VAs)
void test_set_image_base(uintptr_t image_base);
void test_force_enabled(int32_t forced);       // -1 = attach decides
void test_reset();
void test_encode(uint32_t seat, const action_request& rq, uint32_t member, uint32_t handle, uint64_t actor,
                 uint64_t args[gtq::ARG_COUNT]);

}  // namespace ilc::submit
