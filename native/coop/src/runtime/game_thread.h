// Ivalice Legacy co-op module: the game-thread job queue drained by the UI-tick detour (UT-01).
//
// Game functions (issue an action, gambit query/set, combat indicator, UI sound, target circles, leader change)
// may only run on the game's own thread, between frames. Producers on any thread (co-op thread, input thread, a
// bridge call) push a job; the UT-01 tick, on the game thread, drains up to DRAIN_BUDGET jobs per frame and posts
// one result per job. Nobody waits for anybody:
//
//  * The queue is a bounded multi-producer/single-consumer ring (per-cell sequence numbers): push is lock-free and
//    returns PUSH_FULL instead of waiting; the drain never spins (a cell whose producer has not finished writing
//    ends this frame's drain).
//  * Each job names an operation from a fixed in-module table. The table is filled before seal() and is immutable
//    afterwards: a job carries an operation number, never a pointer.
//  * Immediately before each job runs, the drain re-samples the liveness record and re-reads the session
//    generation. A job runs only if the operation is registered, its subsystem may act now (LIVE, armed, bit set,
//    not faulted), its epoch equals the current epoch, its generation equals the current generation, and it is
//    younger than its time-to-live. Otherwise it is rejected with the reason, and its operation never runs. An
//    unknown operation is claimed and rejected once; it can never wedge the queue.
//  * Results land in a completion table indexed by ticket. A requester polls result(ticket): PENDING until the
//    game thread has decided, then exactly one terminal result. A request that could not run in time is
//    rejected (EXPIRED) rather than executed late, so a requester never has to guess whether the game might
//    still act on it.
//  * If the liveness sample is torn, stale or disarmed, the drain stops and leaves the rest for a later frame.
//    In DRY mode the tick does not drain at all: flush() rejects queued jobs as DISARMED without running them.
//  * World validity. An epoch match does not mean the world is valid: the session epoch also numbers the invalid
//    stretches between worlds (load, title, cutscene). The session core reports the latest epoch and whether its
//    world is valid (set_world). An operation is world-requiring unless registered with OPF_ANY_WORLD: such a job
//    is refused at push (PUSH_NO_WORLD) and rejected at drain (REJECT_NO_WORLD) unless the session reports a valid
//    world in exactly the job's epoch. Before the session core has started (host tests of the queue alone) there
//    is no restriction.
//  * No job stays PENDING forever. When the game thread is not draining (liveness stale, the module disarmed or
//    faulted, the frame loop stalled), any other thread may reap(): it takes the consumer side for a moment and
//    rejects, from the oldest job on, every job that can no longer run (DISARMED when its subsystem may not act,
//    EXPIRED once older than its ttl), never running one. The co-op thread reaps on every tick and the shutdown
//    path reaps after disarming; a requester can also call await_result(), which reaps by itself once the job is
//    past ttl + EXPIRY_MARGIN_MS. Only one consumer at a time ever touches the queue's read side: drain, flush and
//    reap all take the same consumer flag without waiting (a busy flag means "someone else is consuming").
//
// No allocation, no lock, no I/O anywhere on the drain path.
#pragma once

#include <stdint.h>

namespace ilc::gtq {

constexpr uint32_t CAPACITY = 64;        // jobs in flight (power of two)
constexpr uint32_t DRAIN_BUDGET = 16;    // jobs run per frame at most
constexpr uint32_t COMPLETIONS = 128;    // results kept (power of two, > CAPACITY)
constexpr uint32_t TTL_MAX_MS = 2000;    // a job older than its ttl (at most this) is rejected, never run late
constexpr uint32_t OP_COUNT = 32;        // operation numbers 1..OP_COUNT-1 (0 is never valid)
constexpr uint32_t ARG_COUNT = 4;
constexpr uint32_t EXPIRY_MARGIN_MS = 250; // await_result() reaps a job this long after its ttl ran out

enum push_status : int32_t {
    PUSH_OK = 0,
    PUSH_FULL = 1,       // CAPACITY jobs are waiting; try again next frame
    PUSH_NOT_LIVE = 2,   // not sealed, DRY mode, disarmed, or the operation's subsystem is off or faulted
    PUSH_BAD_OP = 3,     // not a registered operation
    PUSH_BAD_TTL = 4,    // 0 or above TTL_MAX_MS
    PUSH_BAD_EPOCH = 5,  // epoch 0 (no world)
    PUSH_NO_WORLD = 6,   // a world-requiring operation while the session reports no valid world in that epoch
};

enum job_result : int32_t {
    RESULT_PENDING = 0,            // not decided yet (or a ticket this queue never issued)
    RESULT_DONE = 1,               // the operation ran; operations may return any value >= 1
    REJECT_UNKNOWN_OP = -1,
    REJECT_STALE_EPOCH = -2,       // the world changed after the job was made
    REJECT_STALE_GENERATION = -3,  // the session layout changed after the job was made
    REJECT_EXPIRED = -4,           // older than its ttl when the game thread reached it
    REJECT_DISARMED = -5,          // its subsystem may not act (DRY, disarmed, bit clear, faulted)
    RESULT_LOST = -6,              // the completion was overwritten by a newer one before it was read
    REJECT_BAD_RESULT = -7,        // the operation returned 0 or a reserved value (-1..-15)
    REJECT_NO_WORLD = -8,          // world-requiring, and the session's world was not valid in the job's epoch
    // Operations reject with their own codes at -16 and below.
};

struct job {
    uint32_t ticket;
    uint16_t op;
    uint8_t issuer;      // seat that asked (0..3), or 0xFF for the module itself
    uint8_t reserved0;
    uint32_t epoch;
    uint32_t ttl_ms;
    uint64_t generation;
    uint64_t enqueued_ms;
    uint64_t arg[ARG_COUNT];
};
static_assert(sizeof(job) == 64, "job layout");

struct completion {
    uint32_t ticket;
    int32_t result;
    uint16_t op;
    uint8_t issuer;
    uint8_t reserved0;
    uint32_t epoch;
    uint64_t finished_ms;
};
static_assert(sizeof(completion) == 24, "completion layout");

// Runs on the game thread, inside the UT-01 tick: >= 1 = done (operation's own code), <= -16 = rejected.
using op_fn = int32_t (*)(const job& j);

struct counters {
    volatile uint64_t pushed;
    volatile uint64_t push_full;
    volatile uint64_t push_refused;   // NOT_LIVE, BAD_OP, BAD_TTL, BAD_EPOCH
    volatile uint64_t drained;        // jobs taken by the game thread
    volatile uint64_t ran;            // ... whose operation ran
    volatile uint64_t rejected;       // ... rejected without running
    volatile uint64_t flushed;        // ... rejected by flush() (DRY)
    volatile uint64_t drain_stopped;  // drains that stopped on a torn, stale or disarmed liveness sample
    volatile uint64_t reaped;         // jobs rejected by reap() (also counted in rejected)
    volatile uint64_t consumer_busy;  // drain/flush/reap calls that found another consumer active
};

// Boot, before seal(). False if the number is out of range, already taken, or the table is sealed.
enum op_flag : uint32_t {
    OPF_ANY_WORLD = 0x1,  // may run while the world is invalid (housekeeping that touches no world object)
};
bool register_op(uint16_t op, uint32_t subsystem, op_fn fn, uint32_t flags = 0);
void seal();
bool is_sealed();

// Any thread. *ticket_out receives the ticket on PUSH_OK (never 0).
int32_t push(uint16_t op, uint8_t issuer, uint32_t epoch, uint64_t generation, uint32_t ttl_ms,
             const uint64_t args[ARG_COUNT], uint32_t* ticket_out);

// Any thread: RESULT_PENDING, a terminal result (copied to *out when not null), or RESULT_LOST.
int32_t result(uint32_t ticket, completion* out);

// The session generation jobs must match (co-op thread; release store).
void set_generation(uint64_t generation);
// The session core, every step: its epoch and whether that epoch's world is valid (GATE_WORLD). epoch 0 = no
// session core (no world restriction).
void set_world(uint32_t epoch, bool valid);
uint64_t current_generation();

// Game thread only (the UT-01 tick). drain() returns the jobs taken this call; flush() rejects up to
// DRAIN_BUDGET queued jobs with `reason` without running them. Both return 0 while another thread reaps.
uint32_t drain();
uint32_t flush(int32_t reason);

// Any thread except inside an operation. Rejects, oldest first, every queued job that can no longer run:
// REJECT_DISARMED if its operation's subsystem may not act now, REJECT_EXPIRED if older than its ttl; stops at the
// first job that could still run (FIFO). Never runs an operation. Returns the jobs rejected (0 if the consumer
// side was busy: try again later).
uint32_t reap();
// Requester side: result(ticket), but once `now` is past enqueued_ms + ttl_ms + EXPIRY_MARGIN_MS while the job is
// still PENDING, reap() first. reap() is FIFO, so a job behind an older one waits for that one too: a terminal
// result is available once now passes max(its own deadline, the deadlines of the jobs ahead of it) + the margin,
// at most TTL_MAX_MS + EXPIRY_MARGIN_MS (about 2.25 s) after it was queued, given one reap that finds the consumer
// side free.
int32_t await_result(uint32_t ticket, uint64_t enqueued_ms, uint32_t ttl_ms, completion* out);

const counters* stats();

// Host tests only.
void test_reset();
// Enqueues a job exactly as given (no push checks; the ticket is assigned here), to drive the drain's own checks.
int32_t test_push_raw(const job& j, uint32_t* ticket_out);
// Holds (1) or releases (0) the consumer flag, as if another consumer were active.
bool test_hold_consumer(bool hold);
uint64_t test_world();  // the world word set_world() stored (epoch << 32 | valid | active)
// The completion table: COMPLETIONS slots of {u32 seq, u32 reserved, completion}, indexed by ticket & (COMPLETIONS-1)
// (for emulator tests that read results out of a copy of module memory).
const void* test_completion_table();

}  // namespace ilc::gtq
