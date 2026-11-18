// Ivalice Legacy co-op module, host tests only: the battle log (ILCT_Bl*).
#include <stddef.h>
#include <string.h>

#include "hud/battle_log.h"
#include "hud/battle_log_internal.h"
#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/snapshot.h"
#include "patch/patch_engine.h"

using namespace ilc;
namespace bl = ilc::hud::battle_log;

namespace {

hud::canvas g_cv;

// A stand-in for an element reader: action 0x40 is fire (bit 0), 0x41 ice (bit 2); everything else unknown.
uint8_t fake_element(void* ctx, uint16_t action)
{
    (void)ctx;
    if (action == 0x40) {
        return 0x01;
    }
    if (action == 0x41) {
        return 0x04;
    }
    return bl::ELEMENT_UNKNOWN;
}

}  // namespace

extern "C" {

uint32_t ILCT_BlSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(bl::event),     sizeof(bl::who),        sizeof(bl::message),
                              sizeof(bl::unit_obs),  sizeof(bl::line),       sizeof(bl::view),
                              sizeof(bl::counters),  sizeof(bl::status_view), sizeof(bl::settings),
                              sizeof(bl::ring_view), offsetof(bl::view, lines), offsetof(bl::line, actor_name),
                              sizeof(bl::model),     sizeof(hud::frame),       sizeof(bl::hook_payload),
                              sizeof(bl::hit),       sizeof(bl::kill)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_BlReset(void) { bl::test_reset(); }
void ILCT_BlSetImageBase(uint64_t base) { bl::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_BlSetAttached(int32_t on) { bl::test_set_attached(on != 0); }
void ILCT_BlSetNamesOnly(int32_t on) { bl::test_set_names_only(on != 0); }
int32_t ILCT_BlFeedRow(void) { return bl::feed_row(); }

// ---- the ring ----------------------------------------------------------------------------------------------------------
void ILCT_BlRingPush(uint32_t msg, uint64_t key, uint64_t args, uint64_t now) { bl::ring_push(msg, key, args, now); }
uint32_t ILCT_BlRingDrain(bl::event* out, uint32_t cap, uint32_t* lost) { return bl::ring_drain(out, cap, lost); }
void ILCT_BlRingStats(bl::ring_view* out) { *out = bl::ring_stats(); }
uint64_t ILCT_BlRingBytes(void) { return sizeof(bl::event) * bl::RING; }

// The soak: `total` messages pushed in bursts of `burst`, the ring drained after each burst into a scratch buffer.
// Returns the events drained; *max_behind = the largest backlog seen right before a drain (never above RING).
uint64_t ILCT_BlRingSoak(uint64_t total, uint32_t burst, uint32_t* max_behind)
{
    static bl::event scratch[bl::RING];
    static uint8_t args[bl::EVENT_ARGS];
    uint64_t drained = 0;
    uint32_t worst = 0;
    for (uint64_t k = 0; k < total;) {
        for (uint32_t b = 0; b < burst && k < total; ++b, ++k) {
            args[0] = static_cast<uint8_t>(k);
            bl::ring_push(15, k, reinterpret_cast<uint64_t>(args), k);
        }
        const bl::ring_view v = bl::ring_stats();
        const uint32_t behind = v.written - v.read;
        if (behind > worst) {
            worst = behind;
        }
        uint32_t lost = 0;
        uint32_t got = 0;
        while ((got = bl::ring_drain(scratch, bl::RING, &lost)) != 0) {
            drained += got;
        }
    }
    *max_behind = worst;
    return drained;
}

// ---- the model ---------------------------------------------------------------------------------------------------------
void ILCT_BlModelReset(void) { bl::model_reset(bl::test_model()); }
uint32_t ILCT_BlModelMessage(const bl::message* m) { return bl::model_message(bl::test_model(), *m); }
void ILCT_BlModelUnits(uint64_t now, const bl::unit_obs* units, uint32_t n, int32_t element)
{
    bl::model_units(bl::test_model(), now, units, n, element != 0 ? fake_element : nullptr, nullptr);
}
void ILCT_BlModelHint(uint32_t victim, uint32_t tags, uint64_t now)
{
    bl::model_hint(bl::test_model(), victim, static_cast<uint8_t>(tags), now);
}
uint32_t ILCT_BlModelLines(bl::line* out, uint32_t cap) { return bl::model_lines(*bl::test_model(), out, cap); }

// ---- presentation ------------------------------------------------------------------------------------------------------
uint32_t ILCT_BlLineText(const bl::line* l, char* out, uint32_t cap) { return bl::line_text(*l, out, cap); }
uint32_t ILCT_BlTimeAlpha(const bl::line* l, uint64_t now) { return bl::time_alpha(*l, now); }
int32_t ILCT_BlPasses(const bl::line* l, uint32_t filter, uint32_t seat)
{
    return bl::passes(*l, static_cast<uint8_t>(filter), static_cast<uint8_t>(seat)) ? 1 : 0;
}
uint32_t ILCT_BlSelect(const bl::view* v, uint64_t now, uint8_t* index, uint8_t* alpha, uint32_t cap)
{
    return bl::select(*v, now, index, alpha, cap);
}
int32_t ILCT_BlHeaderShown(const bl::view* v, uint64_t now) { return bl::header_shown(*v, now) ? 1 : 0; }

// Composes the view into a fresh canvas that already holds `prefill` commands (and `prefill_text` text bytes) from
// earlier passes; the frame is copied out. Returns the commands the log added.
uint32_t ILCT_BlCompose(const bl::view* v, uint64_t now, hud::frame* out, uint32_t prefill, uint32_t prefill_text)
{
    g_cv.begin(v->head.epoch, now);
    for (uint32_t k = 0; k < prefill; ++k) {
        g_cv.bar(0, 0, 1, 1, hud::WHITE);
    }
    if (prefill_text != 0) {
        char filler[hud::TEXT_MAX + 1];
        uint32_t left = prefill_text;
        while (left > 1 && g_cv.view().count < hud::MAX_COMMANDS) {
            const uint32_t n = left - 1 > hud::TEXT_MAX ? hud::TEXT_MAX : left - 1;
            memset(filler, 'a', n);
            filler[n] = '\0';
            g_cv.text(0, 0, filler, hud::WHITE);
            left -= n + 1;
        }
    }
    const uint32_t added = bl::compose(g_cv, *v, now);
    memcpy(out, &g_cv.view(), sizeof(*out));
    return added;
}

// The test model's lines as a view (what the co-op thread would publish).
void ILCT_BlBuildView(bl::view* out, uint32_t epoch, uint64_t now, uint32_t flags, uint32_t filter, uint32_t seat,
                      const uint32_t* seat_colour)
{
    memset(out, 0, sizeof(*out));
    out->head.epoch = epoch;
    out->head.flags = flags;
    out->built_ms = now;
    out->filter = static_cast<uint8_t>(filter);
    out->filter_seat = static_cast<uint8_t>(seat);
    for (uint32_t k = 0; k < 4; ++k) {
        out->seat_colour[k] = seat_colour != nullptr ? seat_colour[k] : 0;
    }
    out->line_count = static_cast<uint8_t>(bl::model_lines(*bl::test_model(), out->lines, bl::HISTORY));
}

// ---- settings, module, hook --------------------------------------------------------------------------------------------
void ILCT_BlConfigure(uint32_t enabled, uint32_t expanded, uint32_t control, uint32_t filter, uint32_t seat)
{
    bl::settings s = {};
    s.enabled = static_cast<uint8_t>(enabled);
    s.expanded = static_cast<uint8_t>(expanded);
    s.expand_control = static_cast<uint8_t>(control);
    s.filter = static_cast<uint8_t>(filter);
    s.filter_seat = static_cast<uint8_t>(seat);
    bl::configure(s);
}
// The line counts and the hold time (settings log_lines_compact / log_lines_expanded / log_fade_s), the rest kept.
void ILCT_BlConfigureLines(uint32_t compact, uint32_t expanded, uint32_t fade_s)
{
    bl::settings s = bl::current();
    s.compact_lines = static_cast<uint8_t>(compact);
    s.expanded_lines = static_cast<uint8_t>(expanded);
    s.fade_s = static_cast<uint8_t>(fade_s);
    bl::configure(s);
}
void ILCT_BlCurrent(bl::settings* out) { *out = bl::current(); }
void ILCT_BlDefaults(bl::settings* out) { *out = bl::defaults(); }
void ILCT_BlToggle(void) { bl::toggle_expanded(); }
int32_t ILCT_BlHandler(patch::ctx* c, uint32_t epoch) { return bl::lg01(c, epoch); }

// ---- the per-hit feed and the kill reward -------------------------------------------------------------------------------
// which: 1 HR-01, 2 HR-02, 3 HR-03, 5 HR-05, 6 KR-01 (anything else: -1).
int32_t ILCT_BlHitHandler(uint32_t which, patch::ctx* c, uint32_t epoch)
{
    switch (which) {
    case 1: return bl::hr01(c, epoch);
    case 2: return bl::hr02(c, epoch);
    case 3: return bl::hr03(c, epoch);
    case 5: return bl::hr05(c, epoch);
    case 6: return bl::kr01(c, epoch);
    default: return -1;
    }
}
void ILCT_BlSetHitRows(uint32_t rows) { bl::test_set_hit_rows(static_cast<uint8_t>(rows)); }
int32_t ILCT_BlHitRowIndex(uint32_t bit) { return bl::hit_row_index(bit); }
void ILCT_BlSetBracket(uint32_t serial, uint64_t thread, uint64_t ms) { bl::test_set_bracket(serial, thread, ms); }
uint64_t ILCT_BlThreadId(void) { return bl::current_thread(); }
int32_t ILCT_BlAttach(uint64_t base) { return bl::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }
void ILCT_BlRingAddresses(uint64_t* ring, uint64_t* write)
{
    *ring = bl::test_ring_address();
    *write = bl::test_ring_write_address();
}
uint32_t ILCT_BlOutcomeTags(uint32_t outcome) { return bl::outcome_tags(static_cast<uint8_t>(outcome)); }
uint32_t ILCT_BlModelHit(const bl::hit* h) { return bl::model_hit(bl::test_model(), *h); }
uint32_t ILCT_BlModelKill(const bl::kill* k) { return bl::model_kill(bl::test_model(), *k); }
int32_t ILCT_BlChain(uint16_t* shown, uint8_t* level) { return bl::test_chain(shown, level) ? 1 : 0; }
int32_t ILCT_BlStep(const hud::hud_snapshot* snap, uint64_t now, bl::view* out)
{
    return bl::test_step(snap, now, out) ? 1 : 0;
}
void ILCT_BlPublish(const bl::view* v) { bl::test_publish(*v); }
int32_t ILCT_BlLatest(bl::view* out) { return bl::latest(*out) ? 1 : 0; }
int32_t ILCT_BlReplaces(uint32_t epoch, uint64_t now) { return bl::replaces_native(epoch, now) ? 1 : 0; }
uint32_t ILCT_BlDraw(const hud::hud_snapshot* snap, uint64_t now, hud::frame* out)
{
    g_cv.begin(snap->head.epoch, now);
    const hud::driver::pass_input in = {snap, now, snap->layout, {}};
    bl::draw(g_cv, in);
    memcpy(out, &g_cv.view(), sizeof(*out));
    return g_cv.view().count;
}
void ILCT_BlStats(bl::counters* out) { memcpy(out, bl::stats(), sizeof(*out)); }
void ILCT_BlStatus(bl::status_view* out) { *out = bl::status(); }
int32_t ILCT_BlResolve(uint64_t record, bl::who* out) { return bl::test_resolve(record, out) ? 1 : 0; }

}  // extern "C"
