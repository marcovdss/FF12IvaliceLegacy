// Ivalice Legacy co-op module: the game-thread job queue (see game_thread.h).
#include "runtime/game_thread.h"

#include <string.h>

#include "patch/patch_engine.h"

namespace ilc::gtq {
namespace {

constexpr uint64_t MASK = CAPACITY - 1;
constexpr uint32_t COMPLETION_MASK = COMPLETIONS - 1;
static_assert((CAPACITY & MASK) == 0 && (COMPLETIONS & COMPLETION_MASK) == 0, "powers of two");
static_assert(COMPLETIONS > CAPACITY, "a result must outlive the jobs queued behind it");

// One ring cell. `seq` == position: free for the producer that claims that position; position + 1: holds that
// position's job for the consumer; position + CAPACITY: free again for the next lap.
struct cell {
    volatile uint64_t seq;
    job data;
};

struct op_entry {
    op_fn fn;
    uint32_t subsystem;
    uint32_t flags;
};

// One completion record per ticket slot, single writer (the game thread), seqlock for readers.
struct completion_slot {
    volatile uint32_t seq;  // even = stable
    uint32_t reserved;
    completion value;
};

cell g_cells[CAPACITY];
volatile uint64_t g_enqueue_pos = 0;  // producers (CAS)
uint64_t g_dequeue_pos = 0;           // the game thread only
volatile uint32_t g_next_ticket = 1;
volatile uint64_t g_generation = 0;
// The session's world: epoch << 32 | VALID | ACTIVE. 0 = no session core: no restriction.
constexpr uint64_t WORLD_ACTIVE = 0x2;
constexpr uint64_t WORLD_VALID = 0x1;
volatile uint64_t g_world = 0;
op_entry g_ops[OP_COUNT];
volatile uint32_t g_sealed = 0;
completion_slot g_done[COMPLETIONS];
counters g_counters;
volatile uint32_t g_consumer = 0;  // 1 while a drain, flush or reap owns the read side
bool g_initialized = false;  // cells' seq start at their index; done once (and again by test_reset)

void initialize_cells()
{
    for (uint64_t k = 0; k < CAPACITY; ++k) {
        g_cells[k].seq = k;
    }
    g_initialized = true;
}

// Static initialization: the ring needs seq[k] = k before the first push.
struct ring_init {
    ring_init() { initialize_cells(); }
} g_ring_init;

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

uint32_t take_ticket()
{
    for (;;) {
        const uint32_t ticket = __atomic_fetch_add(&g_next_ticket, 1u, __ATOMIC_RELAXED);
        if (ticket != 0) {
            return ticket;
        }
    }
}

// Lock-free multi-producer enqueue. The ticket is assigned only after a cell is claimed.
int32_t enqueue(job j, uint32_t* ticket_out)
{
    uint64_t pos = __atomic_load_n(&g_enqueue_pos, __ATOMIC_RELAXED);
    cell* c = nullptr;
    for (;;) {
        c = &g_cells[pos & MASK];
        const uint64_t seq = __atomic_load_n(&c->seq, __ATOMIC_ACQUIRE);
        const int64_t diff = static_cast<int64_t>(seq - pos);
        if (diff == 0) {
            if (__atomic_compare_exchange_n(&g_enqueue_pos, &pos, pos + 1, true, __ATOMIC_RELAXED,
                                            __ATOMIC_RELAXED)) {
                break;
            }
        } else if (diff < 0) {
            bump(g_counters.push_full);
            return PUSH_FULL;
        } else {
            pos = __atomic_load_n(&g_enqueue_pos, __ATOMIC_RELAXED);
        }
    }
    j.ticket = take_ticket();
    c->data = j;
    __atomic_store_n(&c->seq, pos + 1, __ATOMIC_RELEASE);
    bump(g_counters.pushed);
    if (ticket_out != nullptr) {
        *ticket_out = j.ticket;
    }
    return PUSH_OK;
}

// Single consumer: copy out the oldest ready job. False if empty or its producer is still writing.
bool dequeue(job& out)
{
    cell& c = g_cells[g_dequeue_pos & MASK];
    const uint64_t seq = __atomic_load_n(&c.seq, __ATOMIC_ACQUIRE);
    if (seq != g_dequeue_pos + 1) {
        return false;
    }
    out = c.data;
    __atomic_store_n(&c.seq, g_dequeue_pos + CAPACITY, __ATOMIC_RELEASE);
    ++g_dequeue_pos;
    bump(g_counters.drained);
    return true;
}

bool ready()
{
    const cell& c = g_cells[g_dequeue_pos & MASK];
    return __atomic_load_n(&c.seq, __ATOMIC_ACQUIRE) == g_dequeue_pos + 1;
}

void post(const job& j, int32_t result, uint64_t now)
{
    completion_slot& slot = g_done[j.ticket & COMPLETION_MASK];
    const uint32_t odd = (slot.seq + 1u) | 1u;
    __atomic_store_n(&slot.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    slot.value.ticket = j.ticket;
    slot.value.result = result;
    slot.value.op = j.op;
    slot.value.issuer = j.issuer;
    slot.value.reserved0 = 0;
    slot.value.epoch = j.epoch;
    slot.value.finished_ms = now;
    __atomic_store_n(&slot.seq, odd + 1u, __ATOMIC_RELEASE);
}

// The read side has one owner at a time; nobody ever waits for it.
bool take_consumer()
{
    if (__atomic_exchange_n(&g_consumer, 1u, __ATOMIC_ACQUIRE) != 0) {
        bump(g_counters.consumer_busy);
        return false;
    }
    return true;
}

void give_consumer()
{
    __atomic_store_n(&g_consumer, 0u, __ATOMIC_RELEASE);
}

// May this job's operation run in its epoch, as far as the world goes?
bool world_allows(uint16_t op, uint32_t epoch)
{
    if ((g_ops[op].flags & OPF_ANY_WORLD) != 0) {
        return true;
    }
    const uint64_t w = __atomic_load_n(&g_world, __ATOMIC_ACQUIRE);
    if ((w & WORLD_ACTIVE) == 0) {
        return true;
    }
    return (w & WORLD_VALID) != 0 && static_cast<uint32_t>(w >> 32) == epoch;
}

int32_t normalize(int32_t r)
{
    if (r >= 1 || r <= -16) {
        return r;
    }
    return REJECT_BAD_RESULT;
}

}  // namespace

bool register_op(uint16_t op, uint32_t subsystem, op_fn fn, uint32_t flags)
{
    if (__atomic_load_n(&g_sealed, __ATOMIC_ACQUIRE) || op == 0 || op >= OP_COUNT || fn == nullptr ||
        g_ops[op].fn != nullptr) {
        return false;
    }
    g_ops[op].fn = fn;
    g_ops[op].subsystem = subsystem;
    g_ops[op].flags = flags;
    return true;
}

void seal()
{
    __atomic_store_n(&g_sealed, 1u, __ATOMIC_RELEASE);
}

bool is_sealed()
{
    return __atomic_load_n(&g_sealed, __ATOMIC_ACQUIRE) != 0;
}

int32_t push(uint16_t op, uint8_t issuer, uint32_t epoch, uint64_t generation, uint32_t ttl_ms,
             const uint64_t args[ARG_COUNT], uint32_t* ticket_out)
{
    int32_t refused = PUSH_OK;
    if (!is_sealed()) {
        refused = PUSH_NOT_LIVE;
    } else if (op == 0 || op >= OP_COUNT || g_ops[op].fn == nullptr) {
        refused = PUSH_BAD_OP;
    } else if (ttl_ms == 0 || ttl_ms > TTL_MAX_MS) {
        refused = PUSH_BAD_TTL;
    } else if (epoch == 0) {
        refused = PUSH_BAD_EPOCH;
    } else if (!patch::subsystem_live(g_ops[op].subsystem)) {
        refused = PUSH_NOT_LIVE;
    } else if (!world_allows(op, epoch)) {
        refused = PUSH_NO_WORLD;
    }
    if (refused != PUSH_OK) {
        bump(g_counters.push_refused);
        return refused;
    }
    job j;
    memset(&j, 0, sizeof(j));
    j.op = op;
    j.issuer = issuer;
    j.epoch = epoch;
    j.ttl_ms = ttl_ms;
    j.generation = generation;
    j.enqueued_ms = patch::now_ms();
    if (args != nullptr) {
        for (uint32_t k = 0; k < ARG_COUNT; ++k) {
            j.arg[k] = args[k];
        }
    }
    return enqueue(j, ticket_out);
}

int32_t result(uint32_t ticket, completion* out)
{
    if (ticket == 0) {
        return RESULT_PENDING;
    }
    const completion_slot& slot = g_done[ticket & COMPLETION_MASK];
    for (int attempt = 0; attempt < 4; ++attempt) {  // a reader may retry; it is never a game thread
        const uint32_t first = __atomic_load_n(&slot.seq, __ATOMIC_ACQUIRE);
        if (first & 1u) {
            continue;
        }
        const completion copy = slot.value;
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if (__atomic_load_n(&slot.seq, __ATOMIC_RELAXED) != first) {
            continue;
        }
        if (first == 0) {
            return RESULT_PENDING;
        }
        const int32_t age = static_cast<int32_t>(copy.ticket - ticket);
        if (age == 0) {
            if (out != nullptr) {
                *out = copy;
            }
            return copy.result;
        }
        return age > 0 ? RESULT_LOST : RESULT_PENDING;
    }
    return RESULT_PENDING;
}

void set_generation(uint64_t generation)
{
    __atomic_store_n(&g_generation, generation, __ATOMIC_RELEASE);
}

void set_world(uint32_t epoch, bool valid)
{
    const uint64_t w = epoch == 0 ? 0 : (static_cast<uint64_t>(epoch) << 32) | WORLD_ACTIVE | (valid ? WORLD_VALID : 0);
    __atomic_store_n(&g_world, w, __ATOMIC_RELEASE);
}

uint64_t current_generation()
{
    return __atomic_load_n(&g_generation, __ATOMIC_ACQUIRE);
}

uint32_t drain()
{
    if (!take_consumer()) {
        return 0;
    }
    uint32_t taken = 0;
    while (taken < DRAIN_BUDGET && ready()) {
        // Re-sample the world before every job, not once per frame: a job must see the epoch and generation that
        // hold at the moment its operation would run.
        const patch::liveness_view live = patch::sample_liveness();
        if (!live.fresh) {
            bump(g_counters.drain_stopped);
            break;  // leave the job queued; a later frame (or its ttl) decides
        }
        job j;
        if (!dequeue(j)) {
            break;
        }
        ++taken;
        const uint64_t generation = current_generation();
        int32_t verdict = RESULT_PENDING;
        if (j.op == 0 || j.op >= OP_COUNT || g_ops[j.op].fn == nullptr) {
            verdict = REJECT_UNKNOWN_OP;
        } else if (!patch::subsystem_live(g_ops[j.op].subsystem)) {
            verdict = REJECT_DISARMED;
        } else if (j.epoch != live.epoch) {
            verdict = REJECT_STALE_EPOCH;
        } else if (!world_allows(j.op, j.epoch)) {
            verdict = REJECT_NO_WORLD;
        } else if (j.generation != generation) {
            verdict = REJECT_STALE_GENERATION;
        } else if (live.now_ms < j.enqueued_ms || live.now_ms - j.enqueued_ms > j.ttl_ms) {
            verdict = REJECT_EXPIRED;
        }
        if (verdict != RESULT_PENDING) {
            bump(g_counters.rejected);
            post(j, verdict, live.now_ms);
            continue;
        }
        const int32_t r = normalize(g_ops[j.op].fn(j));
        bump(g_counters.ran);
        post(j, r, patch::now_ms());
    }
    give_consumer();
    return taken;
}

uint32_t flush(int32_t reason)
{
    if (!take_consumer()) {
        return 0;
    }
    uint32_t taken = 0;
    const uint64_t now = patch::now_ms();
    job j;
    while (taken < DRAIN_BUDGET && dequeue(j)) {
        ++taken;
        bump(g_counters.flushed);
        bump(g_counters.rejected);
        post(j, reason, now);
    }
    give_consumer();
    return taken;
}

uint32_t reap()
{
    if (!take_consumer()) {
        return 0;
    }
    uint32_t taken = 0;
    const uint64_t now = patch::now_ms();
    while (taken < CAPACITY && ready()) {
        // Peek: only a job that can never run is taken out.
        const job& head = g_cells[g_dequeue_pos & MASK].data;
        int32_t verdict = RESULT_PENDING;
        if (head.op == 0 || head.op >= OP_COUNT || g_ops[head.op].fn == nullptr) {
            verdict = REJECT_UNKNOWN_OP;
        } else if (!patch::subsystem_live(g_ops[head.op].subsystem)) {
            verdict = REJECT_DISARMED;
        } else if (now < head.enqueued_ms || now - head.enqueued_ms > head.ttl_ms) {
            verdict = REJECT_EXPIRED;
        } else {
            // The session moved on (a job of an older epoch can never run), or the job's own epoch is an invalid
            // stretch (the world becomes valid only in a later epoch).
            const uint64_t w = __atomic_load_n(&g_world, __ATOMIC_ACQUIRE);
            if ((w & WORLD_ACTIVE) != 0 && static_cast<uint32_t>(w >> 32) != head.epoch) {
                verdict = REJECT_STALE_EPOCH;
            } else if (!world_allows(head.op, head.epoch)) {
                verdict = REJECT_NO_WORLD;
            }
        }
        if (verdict == RESULT_PENDING) {
            break;  // FIFO: this one may still run; the ones behind it wait for it
        }
        job j;
        if (!dequeue(j)) {
            break;
        }
        ++taken;
        bump(g_counters.reaped);
        bump(g_counters.rejected);
        post(j, verdict, now);
    }
    give_consumer();
    return taken;
}

int32_t await_result(uint32_t ticket, uint64_t enqueued_ms, uint32_t ttl_ms, completion* out)
{
    int32_t r = result(ticket, out);
    if (r != RESULT_PENDING) {
        return r;
    }
    const uint64_t now = patch::now_ms();
    if (now > enqueued_ms + ttl_ms + EXPIRY_MARGIN_MS) {
        reap();
        r = result(ticket, out);
    }
    return r;
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    memset(g_cells, 0, sizeof(g_cells));
    initialize_cells();
    g_enqueue_pos = 0;
    g_dequeue_pos = 0;
    g_next_ticket = 1;
    g_generation = 0;
    g_world = 0;
    memset(g_ops, 0, sizeof(g_ops));
    g_sealed = 0;
    memset(g_done, 0, sizeof(g_done));
    memset(&g_counters, 0, sizeof(g_counters));
    g_consumer = 0;
}

bool test_hold_consumer(bool hold)
{
    if (hold) {
        return __atomic_exchange_n(&g_consumer, 1u, __ATOMIC_ACQUIRE) == 0;
    }
    __atomic_store_n(&g_consumer, 0u, __ATOMIC_RELEASE);
    return true;
}

uint64_t test_world()
{
    return __atomic_load_n(&g_world, __ATOMIC_ACQUIRE);
}

int32_t test_push_raw(const job& j, uint32_t* ticket_out)
{
    return enqueue(j, ticket_out);
}

const void* test_completion_table()
{
    static_assert(sizeof(completion_slot) == 32, "test layout");
    return g_done;
}

}  // namespace ilc::gtq
