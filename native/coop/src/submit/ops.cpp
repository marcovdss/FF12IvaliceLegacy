// Ivalice Legacy co-op module: the game-thread operations of battle-menu requests and their attach (see submit.h).
//
// Every operation runs inside the UT-01 callback, after the queue has matched the job's epoch and generation. Each
// proves the rest again from the published session record and fault-safe reads, then calls the game at most once.
// No allocation, no lock, no I/O.
#include <string.h>

#include "menu/menu_facts.h"
#include "patch/patch_engine.h"
#include "patch/site_table.h"
#include "session/reader.h"
#include "session/session.h"
#include "session/world.h"
#include "submit/submit.h"
#include "submit/submit_detail.h"
#include "submit/submit_facts.h"
#include "targets/live.h"

namespace ilc::submit {

using namespace detail;

namespace {

using session::game_reader;
using session::session_record;

uintptr_t at(uintptr_t va) { return base() + (va - facts::IMAGE_VA); }

// The game's functions (relocated), or the host test's doubles.
game_fns live_fns()
{
    if (g_test_fns != nullptr) {
        return *g_test_fns;
    }
    game_fns f;
    f.submit = reinterpret_cast<decltype(f.submit)>(at(facts::FN_SUBMIT_ACTION));
    f.reserve = reinterpret_cast<decltype(f.reserve)>(at(facts::FN_SUBMIT_RESERVE));
    f.dismiss = reinterpret_cast<decltype(f.dismiss)>(at(facts::FN_DISMISS));
    f.admission = reinterpret_cast<decltype(f.admission)>(at(facts::FN_ADMISSION));
    f.available = reinterpret_cast<decltype(f.available)>(at(facts::FN_AVAILABLE));
    f.query = reinterpret_cast<decltype(f.query)>(at(facts::FN_GAMBITS_QUERY));
    f.set = reinterpret_cast<decltype(f.set)>(at(facts::FN_GAMBITS_SET));
    f.record = reinterpret_cast<decltype(f.record)>(at(facts::FN_RECORD));
    f.indicator = reinterpret_cast<decltype(f.indicator)>(at(facts::FN_INDICATOR));
    f.sound = reinterpret_cast<decltype(f.sound)>(at(facts::FN_UI_SOUND));
    return f;
}

struct issuer_args {
    uint32_t handle;
    uint16_t member;
    uint16_t action;
    uint64_t actor;
};

issuer_args decode_issuer(const gtq::job& j)
{
    issuer_args a;
    a.handle = static_cast<uint32_t>(j.arg[0]);
    a.member = static_cast<uint16_t>(j.arg[0] >> 32);
    a.action = static_cast<uint16_t>(j.arg[0] >> 48);
    a.actor = j.arg[1];
    return a;
}

// The record of the job's own epoch and generation, with a world.
bool record_of_job(const gtq::job& j, session_record* rec)
{
    return session::read(rec) && rec->head.epoch == j.epoch && rec->head.generation == j.generation &&
           (rec->head.flags & session::GATE_WORLD) != 0;
}

// The seat still names the job's character, handle and actor; the actor still carries the handle and its record is
// the party block's record for that party id. *record_out = the record.
bool issuer_holds(game_reader& r, const session_record& rec, uint32_t seat, const issuer_args& a,
                  uintptr_t* record_out)
{
    if (seat >= session::ACTIVE_SEATS || (seat != 0 && (rec.seat[seat].flags & session::SEATF_IN_SESSION) == 0)) {
        return false;
    }
    const session::seat_entry& e = rec.seat[seat];
    if ((e.flags & session::SEATF_ACTOR) == 0 || e.member != a.member || e.handle != a.handle || e.actor != a.actor ||
        a.actor == 0 || a.handle == 0 || a.member >= facts::MEMBER_LIMIT) {
        return false;
    }
    uint32_t handle_now = 0;
    uintptr_t record = 0;
    uintptr_t block = 0;
    if (!r.u32(a.actor + facts::ACTOR_HANDLE, &handle_now) || handle_now != a.handle ||
        !r.ptr(a.actor + facts::ACTOR_BATTLE, &record) || record == 0 ||
        !r.ptr(r.global(facts::BATTLE_STATE_PTR), &block) || block == 0 ||
        record != block + facts::MEMBER_RECORDS + uintptr_t{a.member} * facts::MEMBER_RECORD_SIZE) {
        return false;
    }
    *record_out = record;
    return true;
}

int32_t reject(int32_t code)
{
    bump(g_counters.op_rejected);
    return code;
}

}  // namespace

// ---- attach ---------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base)
{
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
    __atomic_store_n(&g_enabled, 0, __ATOMIC_RELEASE);
    game_reader r(image_base);
    for (const facts::prologue& p : facts::PROLOGUES) {
        uint8_t now[facts::PROLOGUE];
        if (!r.read(r.global(p.va), now, sizeof(now)) || memcmp(now, p.bytes, sizeof(now)) != 0) {
            bump(g_counters.prologue_mismatch);
            return false;  // fail closed: no operation is registered, every request is refused
        }
    }
    const uint32_t sub = ILC_SITE_SUBSYS_FIREWALL_MENUS;
    const bool ok = gtq::register_op(OP_MENU_ACTION, sub, op_action) &&
                    gtq::register_op(OP_MENU_GAMBITS, sub, op_gambits) &&
                    gtq::register_op(OP_MENU_DISMISS, sub, op_dismiss) &&
                    gtq::register_op(OP_MENU_INDICATOR, sub, op_indicator) &&
                    gtq::register_op(OP_MENU_SOUND, sub, op_sound);
    __atomic_store_n(&g_enabled, ok ? 1 : 0, __ATOMIC_RELEASE);
    return ok;
}

bool enabled()
{
    return is_enabled();
}

// ---- operations -----------------------------------------------------------------------------------------------------
int32_t op_action(const gtq::job& j)
{
    if (!is_enabled()) {
        return reject(REJ_DISABLED);
    }
    const issuer_args a = decode_issuer(j);
    const uint32_t target = static_cast<uint32_t>(j.arg[2]);
    const uint8_t kind = static_cast<uint8_t>(j.arg[2] >> 32);
    const uint8_t flags = static_cast<uint8_t>(j.arg[2] >> 40);
    const uint16_t reserve = static_cast<uint16_t>(j.arg[2] >> 48);
    const uint64_t target_actor = j.arg[3];
    if ((kind != RK_ACTION && kind != RK_RESERVE) || (kind == RK_ACTION && target == 0) ||
        (kind == RK_RESERVE && reserve >= facts::MEMBER_LIMIT) || a.action == facts::ACTION_DISMISS) {
        return reject(REJ_BAD_ARGS);
    }
    session_record rec;
    if (!record_of_job(j, &rec)) {
        return reject(REJ_NO_WORLD);
    }
    game_reader r(base());
    uintptr_t record = 0;
    if (!issuer_holds(r, rec, j.issuer, a, &record)) {
        return reject(REJ_STALE_ISSUER);
    }
    uint32_t status = 0;
    if (!session::battle_status(r, record, &status) || (status & facts::ST_NO_COMMAND) ||
        ((status & facts::ST_BERSERK) && a.action != facts::ACTION_ATTACK)) {
        return reject(REJ_STATUS);
    }
    if (kind == RK_ACTION && target != a.handle) {
        // A foe or neutral must still be alive at the call (a revival excepted): one that died since the guest
        // chose it would otherwise take the action, or the game would turn it on whoever stands nearest.
        const bool alive_required = (flags & RF_WORLD) != 0 && (flags & RF_REVIVE) == 0;
        const uintptr_t now = targets::live_actor(r, target, alive_required);
        if (now == 0 || now != target_actor) {
            return reject(REJ_STALE_TARGET);
        }
    } else if (kind == RK_RESERVE) {
        uintptr_t block = 0;
        uint16_t current[4];
        if (!r.ptr(r.global(facts::BATTLE_STATE_PTR), &block) || block == 0 ||
            !r.read(block + facts::LAYOUT_TABLE + facts::LAYOUT_CURRENT * facts::LAYOUT_STRIDE, current,
                    sizeof(current))) {
            return reject(REJ_STALE_TARGET);
        }
        for (uint16_t m : current) {
            if (m == reserve) {
                return reject(REJ_STALE_TARGET);  // joined the formation since: a reserve submit would misfire
            }
        }
    }
    const game_fns f = live_fns();
    if ((flags & RF_LICENSED) && f.available(static_cast<uint8_t>(a.member), a.action, 0) != 1) {
        return reject(REJ_UNAVAILABLE);
    }
    const int32_t admission = f.admission(static_cast<uintptr_t>(a.actor), a.action);
    if (admission != 0) {
        return reject(REJ_ADMISSION - (admission & 0xFF));
    }
    bump(g_counters.op_ran);
    const int32_t result = kind == RK_RESERVE
                               ? f.reserve(a.handle, 0, static_cast<uint8_t>(reserve), a.action, 0)
                               : f.submit(a.handle, target, a.action, 0);
    return result < 0 ? REJ_GAME_REFUSED : OPR_DONE;
}

int32_t op_dismiss(const gtq::job& j)
{
    if (!is_enabled()) {
        return reject(REJ_DISABLED);
    }
    const issuer_args a = decode_issuer(j);
    session_record rec;
    if (!record_of_job(j, &rec)) {
        return reject(REJ_NO_WORLD);
    }
    game_reader r(base());
    uintptr_t record = 0;
    if (!issuer_holds(r, rec, j.issuer, a, &record)) {
        return reject(REJ_STALE_ISSUER);
    }
    if (__atomic_load_n(&g_owner, __ATOMIC_ACQUIRE) != a.member) {
        return reject(REJ_NOT_OWNER);
    }
    if ((rec.facts & session::FACT_SUMMON) == 0) {
        return reject(REJ_NO_SUMMON);
    }
    bump(g_counters.op_ran);
    live_fns().dismiss(0);
    return OPR_DONE;
}

namespace {

// The Gambits identity proof (repeated before the setter): handle -> the same actor, the same physics object, the
// actor's record = the party block's record for the id = what the game's own getter returns; KO and Stone refuse.
int32_t gambit_proof(game_reader& r, const game_fns& f, const issuer_args& a, uint64_t physics, uint64_t record)
{
    uint32_t handle_now = 0;
    if (a.handle == 0 || a.actor == 0 || !r.u32(a.actor + facts::ACTOR_HANDLE, &handle_now) ||
        handle_now != a.handle || targets::live_actor(r, a.handle, false) != a.actor) {
        return menu::GB_STALE_IDENTITY;
    }
    if (physics == 0 || session::actor_physics(r, a.actor) != physics) {
        return menu::GB_STALE_PHYSICS;
    }
    uintptr_t bound = 0;
    uintptr_t block = 0;
    if (record == 0 || !r.ptr(a.actor + facts::ACTOR_BATTLE, &bound) || bound != record ||
        !r.ptr(r.global(facts::BATTLE_STATE_PTR), &block) || block == 0 ||
        record != block + facts::MEMBER_RECORDS + uint64_t{a.member} * facts::MEMBER_RECORD_SIZE ||
        f.record(static_cast<uint8_t>(a.member)) != record) {
        return menu::GB_STALE_RECORD;
    }
    uint32_t status = 0;
    if (!session::battle_status(r, record, &status)) {
        return menu::GB_STALE_RECORD;
    }
    if (status & (facts::ST_KO | facts::ST_STONE)) {
        return menu::GB_BLOCKED_STATUS;
    }
    return menu::GB_APPLIED;
}

}  // namespace

int32_t op_gambits(const gtq::job& j)
{
    if (!is_enabled()) {
        return reject(REJ_DISABLED);
    }
    const issuer_args a = decode_issuer(j);
    const uint64_t physics = j.arg[2];
    const uint64_t record = j.arg[3];
    if (a.member > 5) {
        return reject(REJ_GAMBIT - menu::GB_INVALID_MEMBER);
    }
    session_record rec;
    if (!record_of_job(j, &rec)) {
        return reject(REJ_GAMBIT - menu::GB_STALE_SEQUENCE);
    }
    const session::seat_entry& e = rec.seat[j.issuer < session::SEATS ? j.issuer : 0];
    if (j.issuer >= session::ACTIVE_SEATS || e.member != a.member || e.handle != a.handle || e.actor != a.actor) {
        return reject(REJ_GAMBIT - menu::GB_STALE_IDENTITY);
    }
    game_reader r(base());
    const game_fns f = live_fns();
    int32_t proof = gambit_proof(r, f, a, physics, record);
    if (proof != menu::GB_APPLIED) {
        return reject(REJ_GAMBIT - proof);
    }
    const uint32_t desired = f.query(static_cast<uint8_t>(a.member)) == 0 ? 1u : 0u;
    // The query is a game call: prove the whole identity again right before the setter.
    proof = gambit_proof(r, f, a, physics, record);
    if (proof != menu::GB_APPLIED) {
        return reject(REJ_GAMBIT - proof);
    }
    uint32_t before = 0;
    if (!r.u32(record, &before)) {
        return reject(REJ_GAMBIT - menu::GB_STALE_RECORD);
    }
    bump(g_counters.op_ran);
    f.set(static_cast<uint8_t>(a.member), static_cast<int32_t>(desired));
    // Verify, never retry: the same record, bit 2 as asked, every other bit kept.
    uintptr_t block = 0;
    uint32_t after = 0;
    const uint32_t expected = (before & ~uint32_t{menu::facts::REC_GAMBITS_ON}) |
                              (desired ? uint32_t{menu::facts::REC_GAMBITS_ON} : 0u);
    if (!r.ptr(r.global(facts::BATTLE_STATE_PTR), &block) ||
        record != block + facts::MEMBER_RECORDS + uint64_t{a.member} * facts::MEMBER_RECORD_SIZE ||
        f.record(static_cast<uint8_t>(a.member)) != record || !r.u32(record, &after) || after != expected) {
        return REJ_GAMBIT - menu::GB_VERIFY_FAILED;
    }
    return desired ? OPR_GAMBITS_ON : OPR_GAMBITS_OFF;
}

int32_t op_indicator(const gtq::job& j)
{
    if (!is_enabled()) {
        return reject(REJ_DISABLED);
    }
    const uint32_t source = static_cast<uint32_t>(j.arg[0]);
    const uint32_t target = static_cast<uint32_t>(j.arg[0] >> 32);
    const uint64_t seat = j.arg[1];
    if (seat >= SEATS || source == 0 || target == 0) {
        return reject(REJ_BAD_ARGS);
    }
    session_record rec;
    if (!record_of_job(j, &rec)) {
        return reject(REJ_NO_WORLD);
    }
    game_reader r(base());
    uintptr_t ui = 0;
    if (!r.ptr(r.global(facts::UI_MANAGER_PTR), &ui) || ui == 0) {
        return reject(REJ_NO_WORLD);
    }
    if (targets::live_actor(r, source, false) == 0 || targets::live_actor(r, target, true) == 0) {
        return reject(REJ_STALE_TARGET);
    }
    bump(g_counters.op_ran);
    const uintptr_t node = live_fns().indicator(ui, source, target);
    __atomic_store_n(&g_node[seat], uint64_t{node}, __ATOMIC_RELEASE);
    return node != 0 || source == target ? OPR_DONE : OPR_NO_NODE;
}

int32_t op_sound(const gtq::job& j)
{
    if (!is_enabled()) {
        return reject(REJ_DISABLED);
    }
    const uint32_t id = static_cast<uint32_t>(j.arg[0]);
    bool allowed = false;
    for (uint32_t k : facts::SOUND_IDS) {
        allowed = allowed || k == id;
    }
    if (!allowed) {
        return reject(REJ_BAD_ARGS);
    }
    bump(g_counters.op_ran);
    live_fns().sound(id);
    return OPR_DONE;
}

}  // namespace ilc::submit
