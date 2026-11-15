// Ivalice Legacy co-op module: the Wait-mode participant rule (see wait.h).
#include "wait/wait.h"

#include <string.h>

#include "ilc/bridge_abi.h"
#include "patch/patch_engine.h"
#include "session/reader.h"
#include "wait/wait_facts.h"

namespace ilc::wait {
namespace {

patch::record_slot<request_record> g_slot;
counters g_counters;
volatile uint32_t g_owned = 0;        // game thread writes, any thread reads
volatile uintptr_t g_image_base = facts::IMAGE_VA;
volatile int32_t g_enabled = 0;
volatile int32_t g_forced = -1;
const game_fns* g_test_fns = nullptr;
engine_out g_last = {};               // game thread only

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uintptr_t base() { return __atomic_load_n(&g_image_base, __ATOMIC_ACQUIRE); }

uintptr_t at(uintptr_t va) { return base() + (va - facts::IMAGE_VA); }

bool is_enabled()
{
    const int32_t forced = __atomic_load_n(&g_forced, __ATOMIC_RELAXED);
    return forced >= 0 ? forced != 0 : __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0;
}

game_fns live_fns()
{
    if (g_test_fns != nullptr) {
        return *g_test_fns;
    }
    game_fns f;
    f.acquire = reinterpret_cast<decltype(f.acquire)>(at(facts::FN_ACQUIRE));
    f.release = reinterpret_cast<decltype(f.release)>(at(facts::FN_RELEASE));
    f.aggregate = reinterpret_cast<decltype(f.aggregate)>(at(facts::FN_AGGREGATE));
    f.clear_lock = reinterpret_cast<decltype(f.clear_lock)>(at(facts::FN_CLEAR_WAIT_LOCK));
    return f;
}

}  // namespace

// ---- the rule ---------------------------------------------------------------------------------------------------
bool pause_wanted(const seat_view seats[SEATS])
{
    bool any = false;
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (seats[s].role != ROLE_PARTICIPANT) {
            continue;  // absent and benched seats never count
        }
        if (seats[s].open == 0) {
            return false;
        }
        any = true;
    }
    return any;
}

int32_t reason_of(uint32_t id, bool active_mode)
{
    switch (id) {
    case 0x01: return active_mode ? -1 : 0;   // P1's battle command menu: reason 0 in Wait
    case 0x04: return active_mode ? 0 : -1;   // the command-tree variant: reason 0 in Active
    case 0x14: return 0;
    case 0x03:
    case 0x18: return 2;
    case 0x02:
    case 0x06:
    case 0x0F:
    case 0x11:
    case 0x12:
    case 0x13:
    case 0x16: return 0x0D;
    default: return -1;                       // the rest of 1..0x18 and every id outside it: no reason
    }
}

engine_out decide(const engine_in& in)
{
    engine_out out = {};
    bool owned = in.owned != 0;
    if (in.readable == 0) {
        out.owned = owned ? 1 : 0;  // nothing is known this frame: change nothing
        return out;
    }
    const bool other_reason = in.frontend_on != 0 && in.frontend_reason >= 0 && in.frontend_reason != 0;
    const bool wanted = in.requested != 0 && in.live != 0 && in.active_mode == 0 && in.in_play != 0 && !other_reason;
    out.wanted = wanted ? 1 : 0;
    if (owned && in.held == 0) {
        owned = false;  // someone released our reason (a stock reason-0 close, the area reset)
        out.revalidated = 1;
    }
    if (wanted && in.held == 0) {
        out.action = ACT_ACQUIRE;  // also after a revalidation: re-acquire on this call while still wanted
        owned = true;
    } else if (!wanted && owned) {
        if (in.frontend_on != 0 && in.frontend_reason == 0) {
            out.action = ACT_HANDOVER;  // a stock reason-0 frontend holds the pause now: never steal it
        } else {
            out.action = ACT_RELEASE;
        }
        owned = false;
    } else if (wanted && in.held != 0 && !owned) {
        out.borrowed = 1;
    }
    out.owned = owned ? 1 : 0;
    return out;
}

// ---- co-op thread ---------------------------------------------------------------------------------------------
void publish(uint32_t epoch, uint64_t generation, bool requested, uint64_t now_ms)
{
    request_record r = {};
    r.head.epoch = epoch;
    r.head.flags = requested ? 1u : 0u;
    r.head.generation = generation;
    r.published_ms = now_ms;
    g_slot.publish(r);
    bump(g_counters.published);
}

void withdraw()
{
    g_slot.withdraw();
}

// ---- any thread ---------------------------------------------------------------------------------------------------
bool owned()
{
    return __atomic_load_n(&g_owned, __ATOMIC_ACQUIRE) != 0;
}

bool enabled()
{
    return is_enabled();
}

// ---- game thread --------------------------------------------------------------------------------------------------
void step(uint32_t epoch)
{
    bump(g_counters.steps);
    if (!is_enabled()) {
        return;  // attach refused the functions: nothing was ever taken, nothing is called
    }
    session::game_reader r(base());
    engine_in in = {};
    in.owned = owned() ? 1 : 0;
    uint32_t held = 0;
    uint32_t frontend_flag = 0;
    uint32_t frontend_id = 0;
    uint8_t mode = 0;
    in.readable = r.u32(r.global(facts::REASONS_HELD), &held) && r.u32(r.global(facts::FRONTEND_FLAG), &frontend_flag) &&
                          r.u32(r.global(facts::FRONTEND_ID), &frontend_id) && r.u8(r.global(facts::GAME_MODE), &mode)
                      ? 1
                      : 0;
    // The battle setting: a missing or unreadable settings object reads Active, so it never pauses.
    uintptr_t settings = 0;
    uint32_t battle = 0;
    in.active_mode = r.ptr(r.global(facts::SETTINGS_PTR), &settings) && settings != 0 && r.u32(settings, &battle) &&
                             (battle & facts::SETTINGS_ACTIVE_BIT) == 0
                         ? 0
                         : 1;
    in.held = (held & (1u << facts::REASON_WAIT)) != 0 ? 1 : 0;
    in.in_play = mode == facts::GAME_MODE_PLAY ? 1 : 0;
    in.frontend_on = frontend_flag == 1 ? 1 : 0;
    in.frontend_reason = static_cast<int8_t>(reason_of(frontend_id & facts::FRONTEND_ID_MASK, in.active_mode != 0));
    in.live = patch::subsystem_live(ILC_SUBSYS_WAIT) ? 1 : 0;
    request_record rec;
    if (g_slot.read(rec) && (rec.head.flags & 1u) != 0) {
        const uint64_t now = patch::now_ms();
        const bool fresh = rec.head.epoch == epoch && epoch != 0 && now >= rec.published_ms &&
                           now - rec.published_ms <= FRESH_MS;
        in.requested = fresh ? 1 : 0;
        if (!fresh) {
            bump(g_counters.stale);
        }
    }
    if (in.readable == 0) {
        bump(g_counters.unreadable);
    }
    const engine_out out = decide(in);
    g_last = out;
    if (out.revalidated) {
        bump(g_counters.revalidated);
    }
    if (out.borrowed) {
        bump(g_counters.borrowed);
    }
    const game_fns f = live_fns();
    switch (out.action) {
    case ACT_ACQUIRE:
        f.acquire(facts::REASON_WAIT, 0, 0, 0, -1, -1);
        f.aggregate();
        bump(g_counters.acquired);
        break;
    case ACT_RELEASE:
        f.release(facts::REASON_WAIT, -1);
        f.clear_lock();
        f.aggregate();
        bump(g_counters.released);
        break;
    case ACT_HANDOVER:
        bump(g_counters.handed_over);
        break;
    default:
        break;
    }
    __atomic_store_n(&g_owned, uint32_t{out.owned}, __ATOMIC_RELEASE);
}

bool release_if_owned()
{
    if (!owned()) {
        return false;
    }
    if (!is_enabled()) {
        __atomic_store_n(&g_owned, 0u, __ATOMIC_RELEASE);
        return true;
    }
    session::game_reader r(base());
    uint32_t held = 0;
    uint32_t frontend_flag = 0;
    uint32_t frontend_id = 0;
    if (!r.u32(r.global(facts::REASONS_HELD), &held) || !r.u32(r.global(facts::FRONTEND_FLAG), &frontend_flag) ||
        !r.u32(r.global(facts::FRONTEND_ID), &frontend_id)) {
        bump(g_counters.unreadable);
        return true;  // nothing known this frame: keep ownership, try at the next UI tick
    }
    if ((held & (1u << facts::REASON_WAIT)) != 0) {
        uintptr_t settings = 0;
        uint32_t battle = 0;
        const bool active = !(r.ptr(r.global(facts::SETTINGS_PTR), &settings) && settings != 0 &&
                              r.u32(settings, &battle) && (battle & facts::SETTINGS_ACTIVE_BIT) == 0);
        const bool stock_reason0 =
            frontend_flag == 1 && reason_of(frontend_id & facts::FRONTEND_ID_MASK, active) == 0;
        if (!stock_reason0) {
            const game_fns f = live_fns();
            f.release(facts::REASON_WAIT, -1);
            f.clear_lock();
            f.aggregate();
            bump(g_counters.released);
        } else {
            bump(g_counters.handed_over);  // that frontend holds the pause now: never steal it
        }
    } else {
        bump(g_counters.revalidated);
    }
    bump(g_counters.stale_released);
    __atomic_store_n(&g_owned, 0u, __ATOMIC_RELEASE);
    return true;
}

void step_dry()
{
    if (owned()) {
        bump(g_counters.dry_owned);
    }
}

// ---- boot ---------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base)
{
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
    __atomic_store_n(&g_enabled, 0, __ATOMIC_RELEASE);
    session::game_reader r(image_base);
    for (const facts::prologue& p : facts::PROLOGUES) {
        uint8_t now[facts::PROLOGUE_MAX];
        if (!r.read(r.global(p.va), now, p.length) || memcmp(now, p.bytes, p.length) != 0) {
            bump(g_counters.prologue_mismatch);
            return false;  // fail closed: the engine never calls the game
        }
    }
    __atomic_store_n(&g_enabled, 1, __ATOMIC_RELEASE);
    return true;
}

const counters* stats()
{
    return &g_counters;
}

// ---- host tests ------------------------------------------------------------------------------------------------
void test_set_game_fns(const game_fns* fns)
{
    g_test_fns = fns;
}

void test_set_image_base(uintptr_t image_base)
{
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
}

void test_set_owned(bool value)
{
    __atomic_store_n(&g_owned, value ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_force_enabled(int32_t forced)
{
    __atomic_store_n(&g_forced, forced, __ATOMIC_RELAXED);
}

void test_reset()
{
    memset(const_cast<counters*>(&g_counters), 0, sizeof(g_counters));
    __atomic_store_n(&g_owned, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_enabled, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&g_forced, -1, __ATOMIC_RELAXED);
    __atomic_store_n(&g_image_base, facts::IMAGE_VA, __ATOMIC_RELEASE);
    g_test_fns = nullptr;
    g_last = engine_out{};
    g_slot.withdraw();
}

engine_out test_last()
{
    return g_last;
}

const volatile uint32_t* test_owned_address()
{
    return &g_owned;
}

patch::record_slot<request_record>& slot()
{
    return g_slot;
}

}  // namespace ilc::wait
