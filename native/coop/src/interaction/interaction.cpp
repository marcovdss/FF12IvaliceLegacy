// Ivalice Legacy co-op module: the co-op-thread half of guest interactions (see interaction.h).
#include "interaction/interaction.h"

#include <string.h>

#include "input/edge_history.h"
#include "input/pad_mirror.h"
#include "interaction/interaction_facts.h"
#include "interaction/rules.h"
#include "runtime/probe.h"
#include "session/reader.h"
#include "session/session.h"

namespace ilc::interaction {

namespace detail {
patch::record_slot<ix_record> g_slot;
patch::record_slot<ix_private> g_private[SLOTS];
counters g_counters;
request g_request;
}  // namespace detail

namespace hooks_detail {
void reset_game_thread_state();
void set_override(const game_calls* c);
void set_pass(uint32_t active, uint32_t slot, uint64_t entry);
void set_guard(uint32_t active);
void read_state(uint32_t* pass_active, uint32_t* pass_slot, uint64_t* pass_entry, uint32_t* guard, int32_t* result,
                uint32_t* policy);
}  // namespace hooks_detail

namespace {

using session::session_record;

constexpr uint32_t LOCATION_NONE = 0xFFFFFFFFu;  // NOTADDR: no area
constexpr uint64_t PRIVATE_QUIET_MS = 100;        // a guest's private passes stopped this long ago: its find is stale

void bump(volatile uint64_t& counter, uint64_t n = 1)
{
    __atomic_add_fetch(&counter, n, __ATOMIC_RELAXED);
}

// Co-op thread only.
struct core {
    bool started;
    uintptr_t image_base;
    edges::edge_cursor cursor;
    uint32_t input_epoch;
    hold_state hold;
    uint8_t hold_source;      // the owner's pad source when the hold began (ilc_frame_source)
    uint8_t reserved[3];
    uint32_t ticket;
    uint32_t last_end;
    uint32_t tick_seen[SLOTS];
    uint64_t tick_seen_ms[SLOTS];
};
core g = {};
volatile uint32_t g_driver = 0;      // seat driving the native menus (0 = P1)
volatile uint64_t g_hold_word = 0;   // (epoch << 32) | seats
uint32_t (*volatile g_test_presses)(uint32_t epoch) = nullptr;
uintptr_t g_test_image_base = 0;

uintptr_t image_base()
{
    return g_test_image_base != 0 ? g_test_image_base : g.image_base;
}

// The game's handle resolver, read through the fault-safe reader (the same checks as the engine's: registry in
// range and enabled, index below its limit and its table count, generation equal).
uintptr_t resolve(session::game_reader& r, uint32_t handle)
{
    const uint32_t reg = (handle >> facts::HANDLE_REGISTRY_SHIFT) & 0x0F;
    if (handle == 0 || reg >= facts::REGISTRIES) {
        return 0;
    }
    const uint32_t index = handle & facts::HANDLE_INDEX_MASK;
    const uint32_t generation = (handle >> facts::HANDLE_GENERATION_SHIFT) & facts::HANDLE_GENERATION_MASK;
    const uintptr_t registry = r.global(facts::REGISTRY_BASE) + reg * facts::REGISTRY_STRIDE;
    uint64_t head = 0;
    uintptr_t table = 0;
    uint32_t limit = 0;
    uint8_t flags = 0;
    uint32_t count = 0;
    uintptr_t entry = 0;
    uint16_t gen = 0;
    if (!r.u64(registry + facts::REG_HEAD, &head) || head == 0 || !r.ptr(registry + facts::REG_TABLE, &table) ||
        table == 0 || !r.u32(registry + facts::REG_LIMIT, &limit) ||
        static_cast<int32_t>(index) >= static_cast<int32_t>(limit) || !r.u8(registry + facts::REG_FLAGS, &flags) ||
        (flags & 1) == 0 || !r.u32(table + facts::TABLE_COUNT, &count) ||
        static_cast<int32_t>(index) >= static_cast<int32_t>(count) ||
        !r.ptr(table + facts::TABLE_ENTRIES + 8ull * index, &entry) || entry == 0 ||
        !r.u16(entry + facts::E_GENERATION, &gen) || gen != generation) {
        return 0;
    }
    return entry;
}

bool is_treasure(session::game_reader& r, uint32_t handle)
{
    const uintptr_t object = resolve(r, handle);
    uint16_t key = 0;
    return object != 0 && r.u16(object + facts::E_NAME_KEY, &key) &&
           static_cast<int16_t>(key) == facts::TREASURE_NAME_KEY;
}

// A mimic that sleeps as a chest: an actor (registry 0) of the mimic unit type whose extended block says asleep.
bool sleeping_mimic(session::game_reader& r, uint32_t handle)
{
    if (((handle >> facts::HANDLE_REGISTRY_SHIFT) & 0x0F) != 0) {
        return false;
    }
    const uintptr_t entry = resolve(r, handle);
    uintptr_t actor = 0;
    uint32_t actor_handle = 0;
    uint8_t unit = 0;
    uintptr_t extended = 0;
    uint8_t asleep = 0;
    return entry != 0 && r.ptr(entry + facts::E_ACTOR, &actor) && actor != 0 &&
           r.u32(actor + facts::ACTOR_HANDLE, &actor_handle) && actor_handle == handle &&
           r.u8(entry + facts::E_UNIT_TYPE, &unit) && (unit & 0x0F) == facts::MIMIC_UNIT_TYPE &&
           r.ptr(actor + facts::ACTOR_EXTENDED, &extended) && extended != 0 && r.u8(extended, &asleep) &&
           (asleep & facts::MIMIC_ASLEEP) != 0;
}

bool guest_free(session::game_reader& r, uint64_t actor)
{
    uint64_t flags = 0;
    uint32_t state = 0;
    uint32_t action = 0;
    return actor != 0 && r.u64(actor + facts::ACTOR_FLAGS, &flags) && r.u32(actor + facts::ACTOR_STATE, &state) &&
           r.u32(actor + facts::ACTOR_ACTION, &action) && press_allowed(flags, state, action);
}

void count_result(int32_t result)
{
    const int32_t index = result >= 0 ? result : -result - 13;
    if (index >= 0 && index < 12) {
        bump(detail::g_counters.results[index]);
    }
}

// ---- the record ----------------------------------------------------------------------------------------------------
ix_record compose(session::game_reader& r, const session_record& s)
{
    ix_record rec;
    memset(&rec, 0, sizeof(rec));
    rec.head.epoch = s.head.epoch;
    rec.head.generation = s.head.generation;
    const uint32_t gates = s.head.flags;
    uint32_t f = 0;
    if ((gates & session::GATE_WORLD) != 0 && (gates & session::GATE_LOGICAL) != 0 &&
        (gates & session::GATE_PARTY_DOWN) == 0) {
        f |= IXF_GATEWAYS | IXF_LOOT;
    }
    if ((gates & session::GATE_INTERACTION) != 0) {
        f |= IXF_ROUTING;
    }
    rec.head.flags = f;
    for (uint32_t seat = 1; seat < ACTIVE_SEATS; ++seat) {
        const session::seat_entry& e = s.seat[seat];
        const uint16_t need = session::SEATF_STEERS | session::SEATF_ACTOR;
        if ((e.flags & need) != need || e.handle == 0 || e.actor == 0) {
            continue;
        }
        const uintptr_t entry = resolve(r, e.handle);
        uintptr_t actor = 0;
        if (entry == 0 || !r.ptr(entry + facts::E_ACTOR, &actor) || actor != e.actor) {
            continue;  // the handle no longer names the session's actor: no entry this beat
        }
        uintptr_t host = 0;
        uintptr_t skeleton = 0;
        if (r.ptr(entry + facts::E_SKELETON_HOST, &host) && host != 0) {
            r.ptr(host + facts::SKELETON_KEEP, &skeleton);
        }
        ix_entry& x = rec.slot[seat - 1];
        x.entry = entry;
        x.skeleton = skeleton;
        x.handle = e.handle;
        x.member = e.member;
        x.seat = static_cast<uint8_t>(seat);
        x.active = 1;
        rec.active |= 1u << (seat - 1);
    }
    return rec;
}

// ---- presses ---------------------------------------------------------------------------------------------------------
// Bit s: seat s pressed CONFIRM since the last beat. The cursor always moves, so a press made while a request or
// a hold was running is never replayed later; a press from a load or another epoch does nothing.
volatile uint64_t g_menu_seats = 0;  // epoch << 32 | seats: the co-op menus' CONFIRM owners (set_menu_seats)

uint32_t menu_owned(uint32_t epoch)
{
    const uint64_t v = __atomic_load_n(&g_menu_seats, __ATOMIC_ACQUIRE);
    return static_cast<uint32_t>(v >> 32) == epoch ? static_cast<uint32_t>(v) : 0u;
}

uint32_t read_presses(uint32_t epoch)
{
    if (g_test_presses != nullptr) {
        return g_test_presses(epoch) & ~menu_owned(epoch);
    }
    const bool new_epoch = epoch != g.input_epoch;
    g.input_epoch = epoch;
    uint32_t pressed = 0;
    for (uint32_t seat = 1; seat < ACTIVE_SEATS; ++seat) {
        edges::edge_delta d;
        if (edges::since(g.cursor, seat + 1, &d) && !d.reseeded && !d.overrun && !new_epoch &&
            (d.act_pressed & (1u << ILC_ACTION_CONFIRM)) != 0) {
            pressed |= 1u << seat;
        }
    }
    return pressed & ~menu_owned(epoch);  // a CONFIRM that an open co-op menu took is not an interaction
}

// ---- requests --------------------------------------------------------------------------------------------------------
bool request_in_flight()
{
    const uint32_t st = __atomic_load_n(&detail::g_request.state, __ATOMIC_ACQUIRE);
    return st == REQ_ARMED || st == REQ_CLAIMED || st == REQ_DONE;
}

void cancel_request()
{
    uint32_t expected = REQ_ARMED;
    if (__atomic_compare_exchange_n(&detail::g_request.state, &expected, static_cast<uint32_t>(REQ_IDLE), false,
                                    __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        bump(detail::g_counters.requests_expired);
    }
}

// The guest's own scan found an object (fresh, for this character): its handle and published state.
bool private_object(uint32_t slot, uint32_t handle, uint64_t now, uint32_t* object_handle, uint32_t* state)
{
    ix_private p;
    if (!detail::g_private[slot].read(p) || p.source_handle != handle) {
        return false;
    }
    if (p.tick != g.tick_seen[slot]) {
        g.tick_seen[slot] = p.tick;
        g.tick_seen_ms[slot] = now;
    }
    if (now - g.tick_seen_ms[slot] > PRIVATE_QUIET_MS || p.mark == 0 || p.tick - p.mark > PRIVATE_STALE_PASSES) {
        return false;
    }
    const int32_t k = current_slot(p.state);
    if (k < 0) {
        return false;
    }
    *object_handle = p.handle[k];
    *state = p.state[k];
    return *object_handle != 0;
}

void arm_request(session::game_reader& r, const session_record& s, uint32_t seat, uint64_t now)
{
    const session::seat_entry& e = s.seat[seat];
    const uint16_t need = session::SEATF_STEERS | session::SEATF_ACTOR | session::SEATF_ALIVE |
                          session::SEATF_MANUAL | session::SEATF_IN_SESSION;
    if ((e.flags & need) != need || e.handle == 0) {
        return;
    }
    if (!guest_free(r, e.actor)) {
        count_result(WR_OWNER_BUSY);
        return;
    }
    request& q = detail::g_request;
    uint32_t object = 0;
    uint32_t published = 0;
    uint32_t policy = POLICY_NPC;
    uint32_t state = 0;
    if (private_object(seat - 1, e.handle, now, &object, &published)) {
        state = object_state(published, sleeping_mimic(r, object));
        if (state == 0) {
            count_result(WR_NO_TARGET);  // an object the game does not offer for use yet
            return;
        }
        policy = is_treasure(r, object) ? POLICY_TREASURE : POLICY_LEADER;
    }
    q.ticket = ++g.ticket;
    q.epoch = s.head.epoch;
    q.policy = policy;
    q.seat = seat;
    q.source_handle = e.handle;
    q.object_handle = policy == POLICY_NPC ? 0 : object;
    q.object_state = policy == POLICY_NPC ? 0 : state;
    q.deadline_ms = patch::now_ms() + REQUEST_TTL_MS;
    q.result = WR_NONE;
    q.target_handle = 0;
    q.touched = 0;
    q.generation = static_cast<uint32_t>(s.head.generation);
    __atomic_store_n(&q.state, static_cast<uint32_t>(REQ_ARMED), __ATOMIC_RELEASE);
    bump(detail::g_counters.requests);
}

void begin_hold(const session_record& s, uint32_t seat, uint32_t target, uint64_t now)
{
    hold_begin(g.hold, seat, target, s.location, now);
    pads::pad_view v;
    g.hold_source = pads::read(seat + 1, &v) && (v.flags & ILC_FRAMEF_CONNECTED) != 0 ? v.source : 0xFF;
    bump(detail::g_counters.holds);
}

void poll_request(const session_record& s, uint64_t now, bool live)
{
    request& q = detail::g_request;
    const uint32_t st = __atomic_load_n(&q.state, __ATOMIC_ACQUIRE);
    if (st == REQ_DONE) {
        const int32_t result = q.result;
        if (live && (result == WR_CONSUMED || result == WR_TALK) && q.seat >= 1 && q.seat < ACTIVE_SEATS) {
            begin_hold(s, q.seat, q.target_handle, now);
        }
        __atomic_store_n(&q.state, static_cast<uint32_t>(REQ_IDLE), __ATOMIC_RELEASE);
    } else if (st == REQ_ARMED && patch::now_ms() > q.deadline_ms) {
        cancel_request();
    }
}

// ---- the hold --------------------------------------------------------------------------------------------------------
bool owner_ok(const session_record& s, uint32_t seat)
{
    const uint16_t need = session::SEATF_IN_SESSION | session::SEATF_MANUAL;
    if (seat == 0 || seat >= ACTIVE_SEATS || (s.seat[seat].flags & need) != need) {
        return false;
    }
    pads::pad_view v;
    if (g.hold_source != 0xFF && pads::read(seat + 1, &v) && v.source == g.hold_source &&
        (v.flags & ILC_FRAMEF_CONNECTED) == 0) {
        return false;  // the owner's pad went away mid-dialog: the menus go back to P1
    }
    return true;
}

void maintain_hold(session::game_reader& r, const session_record& s, uint64_t now)
{
    if (!g.hold.active) {
        return;
    }
    hold_signals sig;
    memset(&sig, 0, sizeof(sig));
    const uintptr_t target = resolve(r, g.hold.target_handle);
    uint8_t talk = 0;
    sig.talking = (target != 0 && r.u8(target + facts::E_TALK, &talk) && (talk & facts::E_TALK_ACTIVE) != 0) ? 1 : 0;
    uint8_t queued = 0;
    sig.menu_queued = (r.u8(r.global(facts::MENU_QUEUED), &queued) && queued == 1) ? 1 : 0;
    uint32_t active = 0;
    sig.frontend = (r.u32(r.global(facts::FRONTEND_ACTIVE), &active) && active == 1) ? 1 : 0;
    sig.modal = (s.facts & session::FACT_MODAL) != 0 ? 1 : 0;
    sig.owner_ok = owner_ok(s, g.hold.seat) ? 1 : 0;
    sig.world_ok = (s.phase != session::PHASE_STOPPED && s.phase != session::PHASE_NO_WORLD &&
                    s.phase != session::PHASE_LOADING && s.phase != session::PHASE_PARTY_DOWN)
                       ? 1
                       : 0;
    sig.location = s.location;
    const uint32_t end = hold_step(g.hold, sig, now);
    if (end != HOLD_KEEP) {
        g.last_end = end;
        if (end < 5) {
            bump(detail::g_counters.holds_ended[end]);
        }
    }
}

void publish_words(uint32_t epoch)
{
    uint32_t seats = 0;
    const request& q = detail::g_request;
    if (request_in_flight() && q.seat >= 1 && q.seat < ACTIVE_SEATS) {
        seats |= 1u << q.seat;
    }
    if (g.hold.active) {
        seats |= 1u << g.hold.seat;
    }
    __atomic_store_n(&g_hold_word, (static_cast<uint64_t>(epoch) << 32) | seats, __ATOMIC_RELEASE);
    // The menus go back to P1 at once if the subsystem may no longer act (disarmed, bit cleared, faulted).
    const uint32_t driver = patch::subsystem_live(ILC_SITE_SUBSYS_MOVEMENT) ? hold_driver(g.hold) : 0u;
    __atomic_store_n(&g_driver, driver, __ATOMIC_RELEASE);
}

void end_hold(uint32_t why)
{
    if (g.hold.active) {
        memset(&g.hold, 0, sizeof(g.hold));
        g.last_end = why;
        bump(detail::g_counters.holds_ended[why]);
    }
}

void end_everything()
{
    detail::g_slot.withdraw();
    cancel_request();
    end_hold(HOLD_WORLD_CHANGED);
    __atomic_store_n(&g_hold_word, 0ull, __ATOMIC_RELEASE);
    __atomic_store_n(&g_driver, 0u, __ATOMIC_RELEASE);
}

}  // namespace

void start(uintptr_t base, uint64_t now_ms)
{
    (void)now_ms;
    const uint32_t ticket = g.ticket;
    memset(&g, 0, sizeof(g));
    g.ticket = ticket;
    g.image_base = base;
    g.hold_source = 0xFF;
    g.started = true;
}

void step(uint64_t now_ms)
{
    if (!g.started) {
        return;
    }
    bump(detail::g_counters.steps);
    session_record s;
    if (!session::read(&s) || s.head.epoch == 0) {
        end_everything();
        return;
    }
    session::game_reader r(image_base());
    detail::g_slot.publish(compose(r, s));
    bump(detail::g_counters.published);
    // The subsystem may not act (the kill switch, its bit cleared, disarmed, faulted): a waiting request is
    // withdrawn and the hold ends for good, so the menus are P1's and stay P1's when co-op comes back on. A request
    // the window already claimed finishes, but starts no hold.
    const bool live = patch::subsystem_live(ILC_SITE_SUBSYS_MOVEMENT);
    if (!live) {
        cancel_request();
        end_hold(HOLD_OWNER_GONE);
    }
    poll_request(s, now_ms, live);
    maintain_hold(r, s, now_ms);
    const uint32_t presses = read_presses(s.head.epoch);
    if (presses != 0) {
        bump(detail::g_counters.presses);
    }
    // One interaction at a time: the game has one descriptor and one consumer.
    if (presses != 0 && !request_in_flight() && !g.hold.active && (s.head.flags & session::GATE_INTERACTION) != 0 &&
        live) {
        for (uint32_t seat = 1; seat < ACTIVE_SEATS; ++seat) {
            if ((presses & (1u << seat)) != 0) {
                arm_request(r, s, seat, now_ms);
                if (request_in_flight()) {
                    break;
                }
            }
        }
    }
    publish_words(s.head.epoch);
}

void stop()
{
    g.started = false;
    end_everything();
}

patch::record_slot<ix_record>& slot()
{
    return detail::g_slot;
}

uintptr_t resolve_handle(session::game_reader& r, uint32_t handle)
{
    return resolve(r, handle);
}

bool sleeping_mimic_handle(session::game_reader& r, uint32_t handle)
{
    return sleeping_mimic(r, handle);
}

void set_menu_seats(uint32_t epoch, uint32_t seats)
{
    __atomic_store_n(&g_menu_seats, (static_cast<uint64_t>(epoch) << 32) | seats, __ATOMIC_RELEASE);
}

uint32_t menu_seats(uint32_t epoch)
{
    return menu_owned(epoch);
}

uint32_t menu_driver()
{
    return __atomic_load_n(&g_driver, __ATOMIC_ACQUIRE);
}

uint32_t hold_seats(uint32_t epoch)
{
    const uint64_t w = __atomic_load_n(&g_hold_word, __ATOMIC_ACQUIRE);
    return (epoch != 0 && static_cast<uint32_t>(w >> 32) == epoch) ? static_cast<uint32_t>(w) : 0u;
}

bool shop_open_now()
{
    uint32_t id = 0;
    uint32_t active = 0;
    session::game_reader r(image_base() != 0 ? image_base() : facts::IMAGE_VA);
    return r.u32(r.global(facts::FRONTEND_ID), &id) && r.u32(r.global(facts::FRONTEND_ACTIVE), &active) &&
           shop_open(id, active);
}

const counters* stats()
{
    return &detail::g_counters;
}

// ---- host tests ------------------------------------------------------------------------------------------------------
void test_debug(debug_view* out)
{
    memset(out, 0, sizeof(*out));
    hooks_detail::read_state(&out->pass_active, &out->pass_slot, &out->pass_entry, &out->guard_active,
                             &out->last_result, &out->last_policy);
    out->driver = g_driver;
    out->hold_active = g.hold.active;
    out->hold_seat = g.hold.seat;
    out->hold_opened = g.hold.opened;
    out->hold_target = g.hold.target_handle;
    out->last_end = g.last_end;
}

void test_reset()
{
    memset(&g, 0, sizeof(g));
    g.hold_source = 0xFF;
    g_driver = 0;
    g_hold_word = 0;
    g_test_presses = nullptr;
    g_menu_seats = 0;
    g_test_image_base = 0;
    memset(const_cast<counters*>(&detail::g_counters), 0, sizeof(detail::g_counters));
    memset(&detail::g_request, 0, sizeof(detail::g_request));
    detail::g_slot.withdraw();
    for (uint32_t k = 0; k < SLOTS; ++k) {
        detail::g_private[k].withdraw();
    }
    hooks_detail::set_override(nullptr);
    hooks_detail::reset_game_thread_state();
}

void test_set_image_base(uintptr_t base)
{
    g_test_image_base = base;
    g.image_base = base;
}

void test_set_calls(const game_calls* c)
{
    hooks_detail::set_override(c);
}

void test_set_presses(uint32_t (*source)(uint32_t epoch))
{
    g_test_presses = source;
}

void test_set_pass(uint32_t active, uint32_t slot_index, uint64_t entry)
{
    hooks_detail::set_pass(active, slot_index, entry);
}

void test_set_guard(uint32_t active)
{
    hooks_detail::set_guard(active);
}

void test_publish(const ix_record& r)
{
    detail::g_slot.publish(r);
}

void test_withdraw()
{
    detail::g_slot.withdraw();
}

bool test_private(uint32_t slot_index, ix_private* out)
{
    return slot_index < SLOTS && detail::g_private[slot_index].read(*out);
}

void test_publish_private(uint32_t slot_index, const ix_private& p)
{
    if (slot_index < SLOTS) {
        detail::g_private[slot_index].publish(p);
    }
}

request* test_request()
{
    return &detail::g_request;
}

}  // namespace ilc::interaction
