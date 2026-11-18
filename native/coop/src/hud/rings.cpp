// Ivalice Legacy co-op module: the AoE-only ring rule (see rings.h).
#include "hud/rings.h"

#include "control/control.h"
#include "frontend/frontend.h"
#include "hud/hud_internal.h"
#include "hud/party_rows.h"
#include "hud/snapshot.h"
#include "hud/snapshot_facts.h"
#include "menu/menu_facts.h"
#include "menu/menus.h"
#include "menu/model.h"
#include "patch/site_table.h"
#include "runtime/probe.h"
#include "session/reader.h"
#include "session/session.h"

namespace ilc::hud::rings {
namespace {

namespace sf = snapfacts;
namespace mf = menu::facts;
using detail::copy_bytes;
using detail::zero_bytes;

constexpr uint32_t REGULARS = 6;

// The first bytes of every game function the operation reaches (pinned build); any other bytes -> no operation.
struct prologue {
    uint64_t va;
    uint8_t n;
    uint8_t bytes[17];
};
constexpr prologue PROLOGUES[] = {
    {FN_AREA_INFO, 10, {0x48, 0x89, 0x5C, 0x24, 0x08, 0x57, 0x48, 0x83, 0xEC, 0x20}},
    {FN_TARGET_LIVE, 8, {0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0x8B, 0xD9}},
    // the 4-instruction thunk (mov r9d, edx; mov edx, ecx; mov rcx, [UI]; jmp 0x2911d0)
    {FN_RING_CREATE, 17, {0x44, 0x8B, 0xCA, 0x8B, 0xD1, 0x48, 0x8B, 0x0D, 0x54, 0xD4, 0xE0, 0x01, 0xE9, 0x9F, 0x27, 0x00,
                          0x00}},
    // the factory it jumps to
    {0x2911D0, 16, {0x48, 0x89, 0x5C, 0x24, 0x10, 0x48, 0x89, 0x6C, 0x24, 0x18, 0x48, 0x89, 0x74, 0x24, 0x20, 0x57}},
};
// What RG-01 relies on: the call's arguments (edx = r14d target, r8d = owner +0xdc action, ecx = page +0x2fe0
// caster, r9d = row flags & 2) and 0x27dc10's entry (no ring without a target; the flag in r9d).
struct pin {
    uint64_t va;
    uint8_t n;
    uint8_t bytes[30];
};
constexpr pin TRIGGER_PINS[] = {
    {SITE_RING_TRIGGER - 30, 30, {0x41, 0x8B, 0xD6, 0x44, 0x0F, 0xB7, 0x86, 0xDC, 0x00, 0x00, 0x00, 0x41, 0x8B, 0x8F, 0xE0,
                                  0x2F, 0x00, 0x00, 0x44, 0x8B, 0x8C, 0xC3, 0x14, 0x05, 0x00, 0x00, 0x41, 0x83, 0xE1, 0x02}},
    {FN_RING_TRIGGER, 16, {0x85, 0xD2, 0x0F, 0x84, 0x87, 0x00, 0x00, 0x00, 0x48, 0x89, 0x5C, 0x24, 0x20, 0x55, 0x56, 0x57}},
};

template <typename P, uint32_t N>
bool pinned(session::game_reader& r, const P (&table)[N])
{
    for (const P& p : table) {
        uint8_t now[sizeof(p.bytes)];
        if (!r.read(r.global(p.va), now, p.n)) {
            return false;
        }
        for (uint32_t k = 0; k < p.n; ++k) {
            if (now[k] != p.bytes[k]) {
                return false;
            }
        }
    }
    return true;
}

counters g_counters;
volatile uint32_t g_attached = 0;      // bit 0 the operation, bit 1 RG-01
volatile uint32_t g_enabled = 1;
volatile int32_t g_force_live = -1;
volatile int32_t g_last_job = 0;
volatile uintptr_t g_base = PINNED_BASE;
const game_fns* volatile g_test_fns = nullptr;
const seat_aim* volatile g_test_aims = nullptr;
patch::record_slot<rings_record> g_slot = {};

// Co-op thread.
uint32_t g_published = 0;
uint64_t g_generation = 0;
bool g_in_flight = false;
uint32_t g_ticket = 0;
uint64_t g_pushed_ms = 0;
seat_ring g_pushed[SEATS] = {};
uint8_t g_colour_mode = ILC_COLOUR_MODE_CHARACTER;
uint8_t g_seat_colours[ILC_SEATS] = {ILC_COLOUR_GOLD, ILC_COLOUR_SAPPHIRE, ILC_COLOUR_EMERALD, ILC_COLOUR_AMETHYST};
uint8_t g_character_colours[REGULARS] = {ILC_COLOUR_SAPPHIRE, ILC_COLOUR_PINK, ILC_COLOUR_AMETHYST,
                                         ILC_COLOUR_BRONZE,   ILC_COLOUR_EMERALD, ILC_COLOUR_GOLD};

// Game thread: the rings this module made (by target; the node itself is found again by its key).
applied_ring g_applied[SEATS] = {};
volatile uint32_t g_applied_count = 0;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

constexpr int32_t ROW_TRIGGER = find_row("RG-01");
static_assert(ROW_TRIGGER >= 0 && patch::SITES[ROW_TRIGGER].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_TRIGGER].kind == ILC_SITE_KIND_CALL &&
                  patch::SITES[ROW_TRIGGER].subsystem == ILC_SUBSYS_TARGETS_CARDS &&
                  patch::SITES[ROW_TRIGGER].rva == SITE_RING_TRIGGER - PINNED_BASE &&
                  (patch::SITES[ROW_TRIGGER].flags & ILC_SITE_FLAG_OPTIONAL) != 0,
              "RG-01 is an optional TARGETS_CARDS call row on the stock target list's ring call");

template <typename T>
bool load(uint64_t address, T* out)
{
    return load_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

bool store_u32(uint64_t address, uint32_t value)
{
    return store_game(reinterpret_cast<void*>(address), &value, sizeof(value));
}

bool targets_live()
{
    const int32_t forced = g_force_live;
    if (forced >= 0) {
        return forced != 0;
    }
    return patch::subsystem_live(ILC_SUBSYS_TARGETS_CARDS);
}

bool wanted_now()
{
    return __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0 && targets_live();
}

game_fns live_fns()
{
    const game_fns* test = g_test_fns;
    if (test != nullptr) {
        return *test;
    }
    const uintptr_t base = g_base;
    game_fns f;
    f.area_info = reinterpret_cast<decltype(f.area_info)>(base + (FN_AREA_INFO - PINNED_BASE));
    f.target_live = reinterpret_cast<decltype(f.target_live)>(base + (FN_TARGET_LIVE - PINNED_BASE));
    f.create = reinterpret_cast<decltype(f.create)>(base + (FN_RING_CREATE - PINNED_BASE));
    return f;
}

// Release every live node of the area list keyed by `target`, as the game's own release does (bit 0 and the key
// cleared; the per-frame tick fades and frees it). Game thread.
uint32_t release_target(uint64_t ui, uint32_t target)
{
    if (ui == 0 || target == 0) {
        return 0;
    }
    uint64_t node = 0;
    if (!load(ui + UI_AREA_LIST, &node)) {
        return 0;
    }
    uint32_t released = 0;
    for (uint32_t n = 0; node != 0 && n < RING_WALK_MAX; ++n) {
        uint32_t key = 0;
        uint32_t flags = 0;
        if (!load(node + RING_KEY, &key) || !load(node + RING_FLAGS, &flags)) {
            break;
        }
        if (key == target && store_u32(node + RING_FLAGS, flags & ~1u) && store_u32(node + RING_KEY, 0)) {
            ++released;
            bump(g_counters.released);
        }
        if (!load(node + RING_NEXT, &node)) {
            break;
        }
    }
    return released;
}

uint32_t count_applied()
{
    uint32_t n = 0;
    for (const applied_ring& a : g_applied) {
        n += a.target != 0 ? 1u : 0u;
    }
    return n;
}

void give_back(uint64_t ui)
{
    for (applied_ring& a : g_applied) {
        if (a.target != 0) {
            release_target(ui, a.target);
        }
        a.target = 0;
        a.clut = 0;
    }
    __atomic_store_n(&g_applied_count, 0u, __ATOMIC_RELEASE);
}

uint64_t ui_manager()
{
    uint64_t ui = 0;
    const uintptr_t base = g_base;
    return load(base + (UI_PTR - PINNED_BASE), &ui) ? ui : 0;
}

int32_t op_rings(const gtq::job& j)
{
    g_last_job = run_job(ui_manager(), j.epoch);
    return gtq::RESULT_DONE;
}

int32_t trigger_body(patch::ctx* c, const rings_record& r)
{
    if (r.enabled == 0 || __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) == 0) {
        patch::note_declined(static_cast<uint32_t>(ROW_TRIGGER));
        return patch::RUN_ORIGINAL;
    }
    if (!stock_ring_allowed(static_cast<uint16_t>(c->r8))) {
        c->r9 = STOCK_NO_RING;  // 0x27dc10 still releases the other rings, then makes none (as for any non-area action)
        bump(g_counters.cleave_gated);
    }
    return patch::RUN_ORIGINAL;
}

// The action row's radius and flags (the action table the game loaded: rows below 4 GiB, resource base 0).
bool read_area(session::game_reader& r, uint16_t action, uint8_t* kind)
{
    uintptr_t resource = 1;
    uintptr_t header = 0;
    uint32_t count = 0;
    uint16_t stride = 0;
    uint32_t rows = 0;
    if (!r.ptr(r.global(mf::RESOURCE_BASE_PTR), &resource) || resource != 0 ||
        !r.ptr(r.global(mf::ACTION_TABLE_PTR), &header) || header == 0 || !r.u32(header + mf::TABLE_COUNT, &count) ||
        !r.u16(header + mf::TABLE_STRIDE, &stride) || !r.u32(header + mf::TABLE_ROWS, &rows) || action >= count ||
        stride < ROW_FLAGS + 4 || rows == 0) {
        return false;
    }
    const uint64_t row = uint64_t{rows} + uint64_t{action} * stride;
    if (row + stride > 0xFFFFFFFFull) {  // NOTADDR: 4 GiB, the rows' own 32-bit arithmetic
        return false;
    }
    uint8_t radius = 0;
    uint32_t flags = 0;
    if (!r.u8(static_cast<uintptr_t>(row) + ROW_AREA, &radius) || !r.u32(static_cast<uintptr_t>(row) + ROW_FLAGS, &flags)) {
        return false;
    }
    *kind = area_kind(radius, flags);
    return true;
}

bool held(const session::seat_entry& e)
{
    return (e.flags & session::SEATF_IN_SESSION) != 0 && e.member != sf::MEMBER_NONE && e.handle != 0;
}

void withdraw_record()
{
    if (g_published != 0) {
        g_slot.withdraw();
        g_published = 0;
    }
}

void forget_flight()
{
    g_in_flight = false;
    g_ticket = 0;
}

bool same_rings(const seat_ring* a, const seat_ring* b)
{
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (a[s].caster != b[s].caster || a[s].target != b[s].target || a[s].action != b[s].action ||
            a[s].clut != b[s].clut || a[s].order != b[s].order) {
            return false;
        }
    }
    return true;
}

}  // namespace

// ---- the rule ----------------------------------------------------------------------------------------------------------------
uint8_t area_kind(uint8_t radius, uint32_t flags)
{
    if (radius == 0 || (flags & (1u << 12)) != 0) {
        return (flags & 0x2u) != 0 ? AREA_KIND_5 : AREA_NONE;
    }
    if ((flags & (1u << 24)) != 0) {
        return AREA_CONE;
    }
    if ((flags & (1u << 25)) != 0) {
        return AREA_KIND_4;
    }
    return (flags & (1u << 21)) != 0 ? AREA_CIRCLE_ALT : AREA_CIRCLE;
}

uint8_t family_of_page(uint8_t page)
{
    if (page >= menu::PG_WHITE && page < menu::PG_WHITE + menu::SCHOOLS) {
        return FAMILY_MAGICK;
    }
    if (page == menu::PG_TECHNICKS) {
        return FAMILY_TECHNICK;
    }
    if (page == menu::PG_ROOT) {
        return FAMILY_ATTACK;   // the root's only action row is Attack
    }
    return FAMILY_OTHER;
}

bool ring_allowed(uint16_t action, uint8_t family, uint8_t kind)
{
    return action != ACTION_ATTACK && (family == FAMILY_MAGICK || family == FAMILY_TECHNICK) && kind == AREA_CIRCLE;
}

bool stock_ring_allowed(uint16_t action)
{
    return action != ACTION_ATTACK;
}

uint32_t wanted_target(const seat_aim& a)
{
    if (a.held == 0 || a.mode != menu::MODE_TARGET || a.pending_kind != menu::EK_ACTION || a.on_page == 0 ||
        a.target == 0 || a.caster == 0) {
        return 0;
    }
    return ring_allowed(a.action, family_of_page(a.page), a.area_kind) ? a.target : 0;
}

// ---- the operation (game thread) -------------------------------------------------------------------------------------------
int32_t run_job(uint64_t ui, uint32_t epoch)
{
    bump(g_counters.jobs);
    rings_record rec;
    const bool have = g_slot.read(rec) && rec.head.epoch == epoch && rec.head.epoch != 0 && rec.enabled != 0 &&
                      wanted_now();
    applied_ring next[SEATS];
    zero_bytes(next, sizeof(next));
    if (have) {
        const game_fns f = live_fns();
        // Oldest aim first: on a shared target the most recent seat's colour is written last.
        for (uint32_t order = 0; order < SEATS; ++order) {
            for (uint32_t s = 0; s < SEATS; ++s) {
                const seat_ring& w = rec.seats[s];
                if (w.order != order || w.target == 0 || w.caster == 0 || w.clut >= 7) {
                    continue;
                }
                alignas(16) uint8_t info[INFO_BYTES];
                zero_bytes(info, sizeof(info));
                if (f.area_info(w.caster, w.target, w.action, info) < 0) {
                    bump(g_counters.refused);
                    continue;
                }
                uint32_t type = 0;
                uint32_t kind = 0;
                float radius = 0.0f;
                copy_bytes(&type, info + INFO_TYPE, sizeof(type));
                copy_bytes(&kind, info + INFO_KIND, sizeof(kind));
                copy_bytes(&radius, info + INFO_RADIUS, sizeof(radius));
                if (kind != AREA_CIRCLE || f.target_live(w.target) == 0) {
                    bump(g_counters.refused);
                    continue;
                }
                const uint64_t node = f.create(w.target, type, radius);
                if (node == 0 || !store_u32(node + RING_CLUT, w.clut)) {
                    bump(g_counters.refused);
                    continue;
                }
                next[s].target = w.target;
                next[s].clut = w.clut;
                bump(g_counters.created);
            }
        }
    }
    // Rings we made that nothing wants now (page closed, action or target changed, refused, switched off).
    for (const applied_ring& old : g_applied) {
        if (old.target == 0) {
            continue;
        }
        bool kept = false;
        for (const applied_ring& n : next) {
            kept = kept || n.target == old.target;
        }
        if (!kept) {
            release_target(ui, old.target);
        }
    }
    for (uint32_t s = 0; s < SEATS; ++s) {
        g_applied[s] = next[s];
    }
    __atomic_store_n(&g_applied_count, count_applied(), __ATOMIC_RELEASE);
    return have ? 1 : 2;
}

// ---- module ---------------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base)
{
    g_base = image_base;
    uint32_t mask = 0;
    session::game_reader r(image_base);
    // Fail closed: without the pinned functions no ring is ever made (the stock rings stay as they are).
    if (pinned(r, PROLOGUES) && gtq::register_op(OP_HUD_RINGS, ILC_SITE_SUBSYS_TARGETS_CARDS, op_rings)) {
        mask |= 1u;
    }
    // RG-01 on its own: without its pins the stock target list keeps its native rings (Attack included).
    const uint32_t row = static_cast<uint32_t>(ROW_TRIGGER);
    if (pinned(r, TRIGGER_PINS) && patch::set_handler(row, trigger_call) && patch::set_site_armed(row, true)) {
        mask |= 2u;
    } else {
        patch::set_site_armed(row, false);
    }
    __atomic_store_n(&g_attached, mask, __ATOMIC_RELEASE);
    return mask == 3u;
}

void configure_from_settings(const ilc_settings& s)
{
    g_colour_mode = s.colour_mode;
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        g_seat_colours[k] = s.seats[k].colour;
    }
    for (uint32_t k = 0; k < REGULARS; ++k) {
        g_character_colours[k] = s.character_colour[k];
    }
}

void step(uint64_t now_ms)
{
    if ((__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) & 1u) == 0) {
        return;
    }
    if (g_in_flight) {
        const int32_t res = gtq::await_result(g_ticket, g_pushed_ms, JOB_TTL_MS, nullptr);
        if (res != gtq::RESULT_PENDING) {
            if (res != gtq::RESULT_DONE) {
                zero_bytes(g_pushed, sizeof(g_pushed));  // not applied: push again
            }
            forget_flight();
        }
    }
    session::session_record srec;
    if (!wanted_now() || !session::read(&srec) || srec.head.epoch == 0 ||
        (srec.head.flags & session::GATE_WORLD) == 0 || control::current_state() != control::STATE_ON) {
        withdraw_record();   // the tick gives the applied rings back
        zero_bytes(g_pushed, sizeof(g_pushed));
        return;
    }
    rings_record rec;
    zero_bytes(&rec, sizeof(rec));
    rec.head.epoch = srec.head.epoch;
    rec.head.generation = ++g_generation;
    rec.enabled = 1;
    hud_snapshot snap;
    const bool have_snap = snapshot::read(snap) && snap.head.epoch == srec.head.epoch;
    session::game_reader r(g_base);
    uint64_t since[SEATS] = {};
    for (uint32_t s = 0; s < SEATS && s < session::SEATS; ++s) {
        const session::seat_entry& e = srec.seat[s];
        seat_aim a;
        zero_bytes(&a, sizeof(a));
        const seat_aim* test = g_test_aims;
        if (test != nullptr) {
            a = test[s];
        } else if (held(e)) {
            a.held = 1;
            a.caster = e.handle;
            const menu::seat_menu* sm = frontend::seat_menu_of(s);
            if (sm != nullptr && sm->model.mode == menu::MODE_TARGET && sm->model.depth > 0) {
                a.mode = sm->model.mode;
                a.page = sm->model.stack[sm->model.depth - 1].page;
                a.pending_kind = sm->model.pending.kind;
                a.action = sm->model.pending.action;
                if (!read_area(r, a.action, &a.area_kind)) {
                    a.area_kind = AREA_NONE;
                }
            }
            if (have_snap && snap.seats[s].on_page != 0) {
                a.on_page = 1;
                a.target = snap.seats[s].handle;
                since[s] = snap.seats[s].since_ms;
            }
        }
        const uint32_t target = wanted_target(a);
        if (target == 0) {
            continue;
        }
        const uint8_t colour = party_rows::row_colour(g_colour_mode, g_seat_colours, g_character_colours,
                                                      static_cast<uint8_t>(s + 1), e.member);
        rec.seats[s] = {a.caster, target, a.action, static_cast<uint8_t>(colour < 7 ? colour : 0), 0};
    }
    // Order: by when each seat aimed (ties by seat number).
    for (uint32_t s = 0; s < SEATS; ++s) {
        uint8_t order = 0;
        for (uint32_t o = 0; o < SEATS; ++o) {
            if (o != s && (since[o] < since[s] || (since[o] == since[s] && o < s))) {
                ++order;
            }
        }
        rec.seats[s].order = order;
    }
    g_slot.publish(rec);
    g_published = 1;
    bump(g_counters.samples);

    bool any = __atomic_load_n(&g_applied_count, __ATOMIC_ACQUIRE) != 0;
    for (const seat_ring& w : rec.seats) {
        any = any || w.target != 0;
    }
    const bool changed = !same_rings(rec.seats, g_pushed);
    if (!any || g_in_flight || (!changed && now_ms - g_pushed_ms < REFRESH_MS)) {
        return;
    }
    const uint64_t args[gtq::ARG_COUNT] = {0, 0, 0, 0};
    uint32_t ticket = 0;
    const int32_t status = gtq::push(OP_HUD_RINGS, 0xFF, srec.head.epoch, srec.head.generation, JOB_TTL_MS, args, &ticket);
    g_pushed_ms = now_ms;
    if (status == gtq::PUSH_OK) {
        g_in_flight = true;
        g_ticket = ticket;
        for (uint32_t s = 0; s < SEATS; ++s) {
            g_pushed[s] = rec.seats[s];
        }
        bump(g_counters.pushed);
    } else {
        bump(g_counters.push_refused);
    }
}

void stop()
{
    withdraw_record();
    forget_flight();
    zero_bytes(g_pushed, sizeof(g_pushed));
}

void tick(bool live)
{
    if ((__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) & 1u) == 0 ||
        __atomic_load_n(&g_applied_count, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    rings_record rec;
    const bool want = live && wanted_now() && g_slot.read(rec) && rec.enabled != 0;
    if (want) {
        return;
    }
    give_back(ui_manager());
    bump(g_counters.give_backs);
}

void set_enabled(bool on) { __atomic_store_n(&g_enabled, on ? 1u : 0u, __ATOMIC_RELEASE); }
bool enabled() { return __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0; }

int32_t trigger_call(patch::ctx* c, uint32_t epoch)
{
    return patch::fail_open<rings_record, g_slot, trigger_body>(c, epoch);
}

const counters* stats() { return &g_counters; }

status_view status()
{
    status_view v;
    zero_bytes(&v, sizeof(v));
    v.attached = static_cast<uint8_t>(g_attached);
    v.enabled = enabled() ? 1 : 0;
    v.applied = static_cast<uint8_t>(g_applied_count);
    v.last_job = g_last_job;
    return v;
}

// ---- host tests -------------------------------------------------------------------------------------------------------------
void test_reset()
{
    zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    g_slot.withdraw();
    g_published = 0;
    g_generation = 0;
    forget_flight();
    g_pushed_ms = 0;
    zero_bytes(g_pushed, sizeof(g_pushed));
    zero_bytes(g_applied, sizeof(g_applied));
    g_applied_count = 0;
    g_enabled = 1;
    g_force_live = -1;
    g_last_job = 0;
    g_test_fns = nullptr;
    g_test_aims = nullptr;
    g_colour_mode = ILC_COLOUR_MODE_CHARACTER;
}

void test_publish(const rings_record& r) { g_slot.publish(r); }
bool test_read(rings_record* out) { return g_slot.read(*out); }
void test_set_image_base(uintptr_t base) { g_base = base; }
void test_set_fns(const game_fns* fns) { g_test_fns = fns; }
void test_set_attached(uint32_t mask) { __atomic_store_n(&g_attached, mask, __ATOMIC_RELEASE); }
void test_force_live(int32_t live) { g_force_live = live; }
const applied_ring* test_applied() { return g_applied; }
uint32_t test_release(uint64_t ui, uint32_t target) { return release_target(ui, target); }
void test_set_aims(const seat_aim* aims) { g_test_aims = aims; }
int32_t test_row() { return ROW_TRIGGER; }

}  // namespace ilc::hud::rings
