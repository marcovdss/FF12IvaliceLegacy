// Ivalice Legacy co-op overlay: the HUD feed (see feed.h).
#include "overlay/feed.h"

#include "control/control.h"
#include "frontend/frontend.h"
#include "hud/battle_log.h"
#include "hud/menus_draw.h"
#include "hud/party_rows.h"
#include "hud/snapshot_facts.h"
#include "menu/menu_facts.h"
#include "menu/model.h"
#include "patch/patch_engine.h"
#include "patch/site_table.h"
#include "session/reader.h"
#include "session/session.h"
#include "targets/live.h"

namespace ilc::overlay::feed {
namespace {

namespace sf = hud::snapfacts;

static_assert(menu::facts::REC_MP == 0x4C, "MP is the record's s16 at +0x4C");

constexpr uint32_t NK_ACTION = 1;   // menus_draw::live_name kinds (menus_draw_view.cpp name_kind)
constexpr uint32_t NK_MEMBER = 2;

// The party-HUD record filler's reads (static study 2026-11-16, rows PH-V1..PH-V4): MP max and the action charge.
constexpr uint32_t REC_MP_MAX = 0x28;          // s16 battle record +0x28 (PH-V1)
constexpr uint32_t ACTOR_ACTION_LIVE = 0x708;  // u64: non-zero while an action is set (the filler's test)
constexpr uint32_t ACTOR_ACTION_ID = 0x714;    // u16 (PH-V3/PH-V4)
constexpr uint32_t ACTOR_ACTION_STATE = 0x6B4; // u8 (PH-V3/PH-V4)
constexpr uint32_t ACTOR_CHARGE = 0x768;       // s32 current (PH-V2)
constexpr uint32_t ACTOR_CHARGE_NEED = 0x76C;  // s32 required (PH-V2)
constexpr uint16_t ACTION_BOUND = 0x21E;       // NOTADDR: battle actions (cmp ..., 0x21e; ja)
constexpr uint32_t PH_PINS = 4;
constexpr uint64_t PIN_RECHECK_MS = 5000;

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != 0 && *a == *b) {
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
constexpr int32_t PH_PIN[PH_PINS] = {find_row("PH-V1"), find_row("PH-V2"), find_row("PH-V3"), find_row("PH-V4")};
constexpr bool ph_rows_ok()
{
    for (int32_t r : PH_PIN) {
        if (r < 0 || patch::SITES[r].kind != ILC_SITE_KIND_VERIFY || patch::SITES[r].policy != ILC_SITE_POLICY_INFO ||
            patch::SITES[r].length > 32) {
            return false;
        }
    }
    return true;
}
static_assert(ph_rows_ok(), "PH-V1..PH-V4: verify-only INFO rows");

uintptr_t g_base = 0x120000;   // NOTADDR: the pinned image base until start
int32_t g_ph_pins = -1;            // -1 unknown, 0 a row off, 1 ok (co-op thread)
volatile int32_t g_force_ph = -1;  // host tests
uint64_t g_ph_ms = 0;
patch::record_slot<record> g_slot = {};
counters g_counters = {};
uint32_t g_generation = 0;
bool g_published = false;
hud::party_rows::ghost g_ghosts[ROWS];

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

bool ph_pins(uint64_t now)
{
    const int32_t forced = __atomic_load_n(&g_force_ph, __ATOMIC_ACQUIRE);
    if (forced >= 0) {
        return forced != 0;
    }
    if (g_ph_pins < 0 || now - g_ph_ms >= PIN_RECHECK_MS) {
        g_ph_ms = now;
        session::game_reader r(g_base);
        bool ok = true;
        for (uint32_t k = 0; k < PH_PINS && ok; ++k) {
            const patch::site& site = patch::SITES[PH_PIN[k]];
            uint8_t bytes[32];
            ok = r.read(g_base + site.rva, bytes, site.length);
            for (uint32_t i = 0; ok && i < site.length; ++i) {
                ok = bytes[i] == static_cast<uint8_t>(site.expected[i]);
            }
        }
        g_ph_pins = ok ? 1 : 0;
    }
    return g_ph_pins == 1;
}

void zero(void* p, uint32_t n)
{
    auto* b = static_cast<volatile uint8_t*>(p);
    for (uint32_t k = 0; k < n; ++k) {
        b[k] = 0;
    }
}

bool name_of(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap)
{
    out[0] = '\0';
    if (!hud::menus_draw::live_name(kind, id, pointer, out, cap) || out[0] == '\0') {
        out[0] = '\0';
        bump(g_counters.name_misses);
        return false;
    }
    return true;
}

const hud::party_rows::row_extra* extra_for(const hud::party_rows::rows_record& rec, uint16_t member)
{
    for (const auto& e : rec.rows) {
        if (e.member == member && member != sf::MEMBER_NONE) {
            return &e;
        }
    }
    return nullptr;
}

bool build(const hud::hud_snapshot& snap, uint64_t now, record* out)
{
    zero(out, sizeof(*out));
    out->head.epoch = snap.head.epoch;
    out->head.generation = ++g_generation;
    out->built_ms = now;
    out->row_count = snap.member_count < ROWS ? snap.member_count : static_cast<uint8_t>(ROWS);
    hud::party_rows::rows_record extras;
    const bool have_extras = hud::party_rows::read(&extras) && extras.head.epoch == snap.head.epoch;
    session::game_reader r(g_base);
    uintptr_t state = 0;
    const bool have_state = r.ptr(r.global(sf::BATTLE_STATE_PTR), &state) && state != 0;
    for (uint32_t k = 0; k < ROWS; ++k) {
        row& o = out->rows[k];
        o.member = sf::MEMBER_NONE;
        o.action = 0xFFFF;
        o.mp = MP_UNKNOWN;
        o.mp_max = -1;
        o.gauge = GAUGE_UNKNOWN;
        if (k >= out->row_count) {
            g_ghosts[k] = hud::party_rows::ghost{0xFFFF, 0, 0, 0, 0, 0};
            continue;
        }
        const hud::hud_unit& u = snap.members[k];
        if (u.member == sf::MEMBER_NONE || u.member >= sf::MEMBER_IDS) {
            continue;
        }
        o.member = u.member;
        o.ghost = hud::party_rows::ghost_step(g_ghosts[k], u.member, u.hp, u.hp_max, now);
        name_of(NK_MEMBER, u.member, 0, o.name, sizeof(o.name));
        int16_t mp = 0;
        const uintptr_t record = have_state ? state + sf::MEMBER_RECORDS +
                                                  static_cast<uintptr_t>(u.member) * sf::MEMBER_RECORD_SIZE
                                            : 0;
        if (record != 0 && r.u16(record + menu::facts::REC_MP, reinterpret_cast<uint16_t*>(&mp))) {
            o.mp = mp;
            o.flags |= RF_MP;
        }
        const bool pins = ph_pins(now);
        int16_t mp_max = 0;
        if (pins && record != 0 && r.u16(record + REC_MP_MAX, reinterpret_cast<uint16_t*>(&mp_max)) && mp_max > 0) {
            o.mp_max = mp_max;
        }
        // The action gauge, as the native row fills it (states 1-2 empty, 3-8 charge / need, 9+ full).
        if (pins && (u.flags & hud::UNIT_ACTOR) != 0) {
            const uintptr_t actor = targets::live_actor(r, u.handle, false);
            uint64_t set = 0;
            uint16_t id = 0xFFFF;
            uint8_t st = 0;
            uint32_t cur = 0;
            uint32_t need = 0;
            if (actor != 0 && r.u64(actor + ACTOR_ACTION_LIVE, &set) && r.u16(actor + ACTOR_ACTION_ID, &id) &&
                r.u8(actor + ACTOR_ACTION_STATE, &st) && r.u32(actor + ACTOR_CHARGE, &cur) &&
                r.u32(actor + ACTOR_CHARGE_NEED, &need)) {
                o.gauge = gauge_percent(set != 0, id, st, static_cast<int32_t>(cur), static_cast<int32_t>(need));
            }
        }
        const hud::party_rows::row_extra* ex = have_extras ? extra_for(extras, u.member) : nullptr;
        if (ex != nullptr) {
            if ((ex->flags & hud::party_rows::EXTRA_READ) != 0) {
                o.flags |= RF_GAMBITS_KNOWN;
                if ((ex->flags & hud::party_rows::EXTRA_GAMBITS_ON) != 0) {
                    o.flags |= RF_GAMBITS_ON;
                }
            }
            if ((ex->flags & hud::party_rows::EXTRA_CHOOSING) != 0) {
                o.flags |= RF_CHOOSING;
            }
            if ((ex->flags & hud::party_rows::EXTRA_ACTING) != 0) {
                o.flags |= RF_ACTING;
                o.action = ex->action;
                if (ex->action != menu::facts::ACTION_ATTACK) {
                    name_of(NK_ACTION, ex->action, 0, o.action_name, sizeof(o.action_name));
                }
            }
        }
    }
    uint16_t chain = 0;
    uint8_t level = 0;
    out->chain_known = hud::battle_log::chain_now(&chain, &level) ? 1 : 0;
    out->chain = chain;
    out->chain_level = level;
    return true;
}

}  // namespace

uint8_t gauge_percent(bool action_set, uint16_t action, uint8_t state, int32_t charge, int32_t need)
{
    if (!action_set || action > ACTION_BOUND || state == 0) {
        return GAUGE_UNKNOWN;   // no action: no gauge (the native one is hidden too)
    }
    if (state >= 9) {
        return 100;
    }
    if (state < 3) {
        return 0;
    }
    const int32_t total = need > 0 ? need : 100;
    if (charge >= total) {
        return 100;
    }
    if (charge <= 0) {
        return 0;
    }
    return static_cast<uint8_t>((static_cast<int64_t>(charge) * 100) / total);
}

void start(uintptr_t image_base)
{
    g_base = image_base;
}

void step(uint64_t now_ms)
{
    session::session_record srec;
    hud::hud_snapshot snap;
    if (!session::read(&srec) || srec.head.epoch == 0 || (srec.head.flags & session::GATE_WORLD) == 0 ||
        control::current_state() != control::STATE_ON || !patch::subsystem_live(ILC_SUBSYS_RENDER) ||
        !hud::snapshot::read(snap) || snap.head.epoch != srec.head.epoch) {
        if (g_published) {
            g_slot.withdraw();
            g_published = false;
            bump(g_counters.withdrawn);
        }
        return;
    }
    static record next;
    build(snap, now_ms, &next);
    g_slot.publish(next);
    g_published = true;
    bump(g_counters.built);
}

void stop()
{
    g_slot.withdraw();
    g_published = false;
}

bool read(record& out)
{
    return g_slot.read(out);
}

const counters* stats()
{
    return &g_counters;
}

void test_force_pins(int32_t ok)
{
    __atomic_store_n(&g_force_ph, ok, __ATOMIC_RELEASE);
}

void test_reset()
{
    __atomic_store_n(&g_force_ph, -1, __ATOMIC_RELEASE);
    g_ph_pins = -1;
    g_slot.withdraw();
    g_published = false;
    g_generation = 0;
    zero(&g_counters, sizeof(g_counters));
    for (auto& g : g_ghosts) {
        g = hud::party_rows::ghost{0xFFFF, 0, 0, 0, 0, 0};
    }
}

void test_publish(const record& r)
{
    g_slot.publish(r);
    g_published = true;
}

bool test_build(const hud::hud_snapshot& snap, uint64_t now_ms, record* out)
{
    return build(snap, now_ms, out);
}

}  // namespace ilc::overlay::feed
