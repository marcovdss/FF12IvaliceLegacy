// Ivalice Legacy co-op module: chests to the combat log, no pause (see treasure_log.h).
#include "interaction/treasure_log.h"

#include "control/control.h"

#include "patch/site_table.h"
#include "runtime/log.h"
#include "runtime/probe.h"
#include "session/session.h"
#include "submit/submit.h"
#include "submit/submit_facts.h"

namespace ilc::interaction::treasure_log {
namespace {

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

constexpr int32_t ROW_TL01 = find_row("TL-01");
constexpr int32_t ROW_TL02 = find_row("TL-02");
constexpr bool optional_patch(int32_t row, uint8_t kind)
{
    return row >= 0 && patch::SITES[row].policy == ILC_SITE_POLICY_PATCH && patch::SITES[row].kind == kind &&
           patch::SITES[row].subsystem == ILC_SITE_SUBSYS_MOVEMENT &&
           (patch::SITES[row].flags & ILC_SITE_FLAG_OPTIONAL) != 0;
}
static_assert(optional_patch(ROW_TL01, ILC_SITE_KIND_IMM) && optional_patch(ROW_TL02, ILC_SITE_KIND_CALL),
              "TL-01 (the presentation store) and TL-02 (the begin call) are optional MOVEMENT rows");
static_assert(patch::SITES[ROW_TL01].rva == SITE_PRESENTATION - PINNED_BASE &&
                  patch::SITES[ROW_TL01].length == PRESENTATION_LEN && patch::SITES[ROW_TL01].hook_len == PRESENTATION_LEN,
              "TL-01 is the 6-byte store, hooked whole");
static_assert(patch::SITES[ROW_TL02].rva == SITE_BEGIN_CALL - PINNED_BASE, "TL-02 is the begin call");
constexpr int32_t ROW_TL03 = find_row("TL-03");
static_assert(ROW_TL03 >= 0 && patch::SITES[ROW_TL03].policy == ILC_SITE_POLICY_INFO,
              "TL-03 (the notice's sound call) is never written: Together's sound 0x27 is queued our own way (step)");

volatile uint32_t g_attached = 0;
volatile uint32_t g_hooked = 0;   // bit 0 TL-01, bit 1 TL-02 (as the install left them)
volatile uint32_t g_tried = 0;    // attach ran (it does not when the interaction rows failed to attach)
constexpr uint32_t SOUNDS_PENDING_MAX = 2;
uint32_t g_sound_epoch = 0;      // co-op thread: the epoch the pending sounds belong to
volatile uint32_t g_sound_due = 0;// chests TL-02 took whose sound is not queued yet (game thread adds, co-op takes)
uint32_t g_logged = 0;
counters g_counters = {};

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uint64_t va(uint64_t pinned)
{
    return static_cast<uint64_t>(patch::IMAGE_BASE) + (pinned - PINNED_BASE);
}

bool live()
{
    return __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 && patch::subsystem_live(ILC_SITE_SUBSYS_MOVEMENT);
}

}  // namespace

record_image record_for(uint64_t actor, uint64_t chest, uint64_t data)
{
    record_image r = {};
    r.active = 1;
    r.actor = actor;
    r.chest = chest;
    r.data = data;
    r.pending = 0;
    return r;
}

// TL-01, mid-function (MinHook): the notice the grant is filling gets the combat-log presentation. It writes only the
// field of the object its interrupted code is writing (rsi), so it needs no world record; no C runtime call.
int32_t tl01(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    const uint32_t row = static_cast<uint32_t>(ROW_TL01);
    if (!live() || c->rsi == 0) {
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;
    }
    const uint32_t mode = MODE_COMBAT_LOG;
    if (!store_game(reinterpret_cast<void*>(c->rsi), &mode, sizeof(mode))) {
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;   // the stock store runs: the modal notice
    }
    bump(g_counters.log_notices);
    c->resume = va(SITE_PRESENTATION + PRESENTATION_LEN);
    return patch::RESUME;
}

// TL-02, call row (the begin call; registers at the callee's entry): the record as 0x328640 writes it, pending 0, and
// the call skipped (its result is not used by the caller).
int32_t tl02(patch::ctx* c, uint32_t epoch)
{
    const uint32_t row = static_cast<uint32_t>(ROW_TL02);
    if (!live() || c->rcx == 0) {
        bump(g_counters.stock_other);
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;
    }
    // 0x328640 acts only on an actor whose entity resolves ([actor + 0x30], 0x263e30): the end step dereferences it.
    uint64_t entity = 0;
    session::session_record s;
    if (!load_game(&entity, reinterpret_cast<const void*>(c->rcx + 0x30), sizeof(entity)) || entity == 0 ||
        !session::read(&s) || s.head.epoch != epoch || epoch == 0 || (s.head.flags & session::GATE_WORLD) == 0) {
        bump(g_counters.stock_other);
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;
    }
    uint32_t pending = 1;
    if (!load_game(&pending, reinterpret_cast<const void*>(va(RECORD + RECORD_PENDING)), sizeof(pending)) ||
        pending != 0) {
        bump(g_counters.stock_in_flight);   // a stock open is in flight (or unreadable): it finishes as stock
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;
    }
    const record_image r = record_for(c->rcx, c->rdx, c->r8);
    if (!store_game(reinterpret_cast<void*>(va(RECORD)), &r, RECORD_WRITE)) {   // never the result word after it
        bump(g_counters.stock_other);
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;
    }
    bump(g_counters.no_pause_opens);
    // Together's chest sound, queued by the co-op thread: at most SOUNDS_PENDING_MAX waiting (review 16: a burst of
    // chests never becomes a long tail of sounds).
    uint32_t due = __atomic_load_n(&g_sound_due, __ATOMIC_ACQUIRE);
    while (due < SOUNDS_PENDING_MAX &&
           !__atomic_compare_exchange_n(&g_sound_due, &due, due + 1u, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
    }
    c->resume = va(SITE_BEGIN_CALL + 5);
    return patch::RESUME;
}

bool attach()
{
    __atomic_store_n(&g_tried, 1u, __ATOMIC_RELEASE);
    const uint32_t r1 = static_cast<uint32_t>(ROW_TL01);
    const uint32_t r2 = static_cast<uint32_t>(ROW_TL02);
    const uint32_t hooked = (patch::trampoline(r1) != nullptr ? 1u : 0u) | (patch::trampoline(r2) != nullptr ? 2u : 0u);
    __atomic_store_n(&g_hooked, hooked, __ATOMIC_RELEASE);
    bool ok = hooked == 3u && patch::set_handler(r1, tl01) && patch::set_handler(r2, tl02) &&
              patch::set_site_armed(r1, true) && patch::set_site_armed(r2, true);
    if (!ok) {
        patch::set_site_armed(r1, false);   // all or nothing: never a pause-free open with a modal notice or the reverse
        patch::set_site_armed(r2, false);
    }
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    return ok;
}

void step(uint64_t now_ms)
{
    (void)now_ms;
    // Pending chest sounds belong to their epoch and to co-op on (review 16): a map change, a load, or co-op off and on
    // again drops them.
    const uint32_t epoch = session::epoch();
    if ((g_sound_epoch != 0 && epoch != g_sound_epoch) || control::current_state() != control::STATE_ON) {
        __atomic_store_n(&g_sound_due, 0u, __ATOMIC_RELEASE);
    }
    g_sound_epoch = epoch;
    // The chest sound: one per beat at most (the sound slot holds one), the rest on the next beats. The slot is taken
    // only when empty: a guest's menu sound (queued by frontend::step earlier in the beat, flushed by submit::step at
    // its end) is never replaced; the chest sound waits for the next beat instead.
    if (__atomic_load_n(&g_sound_due, __ATOMIC_ACQUIRE) != 0) {
        if (submit::play_system(submit::facts::SOUND_CHEST)) {
            __atomic_sub_fetch(&g_sound_due, 1u, __ATOMIC_ACQ_REL);
            bump(g_counters.sounds);
        } else {
            bump(g_counters.sounds_retried);
        }
    }
    if (g_logged != 0 || patch::current_mode() != patch::MODE_LIVE) {
        return;
    }
    g_logged = 1;
    const uint32_t hooked = __atomic_load_n(&g_hooked, __ATOMIC_ACQUIRE);
    if (__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0) {
        log::post(log::INFO, "treasure log: on (chests open without a pause; the reward goes to the combat log, the "
                             "chest sound plays)");
        return;
    }
    if (__atomic_load_n(&g_tried, __ATOMIC_ACQUIRE) == 0) {
        log::post(log::WARNING, "treasure log: off, chests stay stock (modal notice): the interaction rows did not "
                                "attach, so the treasure rows were never armed");
        return;
    }
    log::line l;
    l.s("treasure log: off, chests stay stock (modal notice): ")
        .s((hooked & 1u) == 0 ? "TL-01 not hooked" : "TL-01 hooked")
        .s(", ")
        .s((hooked & 2u) == 0 ? "TL-02 not hooked" : "TL-02 hooked")
        .s(hooked != 3u ? " (another mod rewrote the site)" : " (handlers could not be set)");
    log::post(log::WARNING, l);
}

const counters* stats()
{
    return &g_counters;
}

status_view status()
{
    status_view v = {};
    v.attached = __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    const uint32_t hooked = __atomic_load_n(&g_hooked, __ATOMIC_ACQUIRE);
    v.presentation = (hooked & 1u) != 0 ? 1 : 0;
    v.begin = (hooked & 2u) != 0 ? 1 : 0;
    v.logged = g_logged != 0 ? 1 : 0;
    return v;
}

void test_reset()
{
    __atomic_store_n(&g_attached, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_hooked, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_tried, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_sound_due, 0u, __ATOMIC_RELEASE);
    g_sound_epoch = 0;
    g_logged = 0;
    volatile uint64_t* field = &g_counters.log_notices;
    for (uint32_t k = 0; k < sizeof(counters) / sizeof(uint64_t); ++k) {
        field[k] = 0;
    }
}

uint32_t test_sound_due()
{
    return __atomic_load_n(&g_sound_due, __ATOMIC_ACQUIRE);
}

}  // namespace ilc::interaction::treasure_log
