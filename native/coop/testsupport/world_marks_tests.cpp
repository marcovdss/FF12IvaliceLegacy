// Ivalice Legacy co-op module, host tests only: seat marks in the world (ILCT_Wm*) and the AoE-only ring rule
// (ILCT_Rg*): the rules, the records, the handlers with a synthetic ctx, and doubles of the game functions the diamond
// emitter and the ring operation call.
#include <windows.h>

#include <string.h>

#include "hud/rings.h"
#include "hud/world_marks.h"

using namespace ilc;
namespace wm = ilc::hud::world_marks;
namespace rg = ilc::hud::rings;

namespace {

// ---- world-object doubles: the pool {base, capacity, count}, the frame lookup and the list append ----------------------
// Same contracts as the game's 0x3f0040 / 0x3f12e0 / 0x3f1290 (pool slots of 0x100, null when full; a frame entry whose
// +4 holds w:12 | h:12; append links +0xd0 next / +0xd8 prev and the list's head +0x00 / tail +0x10).
struct frame_call {
    uint64_t obj;
    uint64_t texture;
    uint32_t sheet;
    uint32_t cell;
};
constexpr uint32_t FRAME_LOG = 64;
frame_call g_frames[FRAME_LOG];
uint32_t g_frame_count = 0;
struct frame_entry {
    uint16_t u, v;
    uint32_t size;
};
frame_entry g_gem_frame = {0, 0, 40u | (70u << 12)};
frame_entry g_digit_frame = {0, 0, 28u | (38u << 12)};
uint32_t g_appends = 0;

uint64_t dbl_alloc(uint64_t pool)
{
    int32_t* p = reinterpret_cast<int32_t*>(pool);
    const int32_t count = p[3];
    if (count >= p[2]) {
        return 0;
    }
    p[3] = count + 1;
    uint8_t* obj = reinterpret_cast<uint8_t*>(*reinterpret_cast<uint64_t*>(pool)) + static_cast<uint64_t>(count) * 0x100;
    memset(obj, 0, 0x100);
    return reinterpret_cast<uint64_t>(obj);
}

uint64_t dbl_frame(uint64_t obj, uint64_t texture, uint32_t sheet, uint32_t cell)
{
    if (g_frame_count < FRAME_LOG) {
        g_frames[g_frame_count++] = {obj, texture, sheet, cell};
    }
    *reinterpret_cast<uint64_t*>(obj + 0xE0) = texture;
    return reinterpret_cast<uint64_t>(sheet == wm::GEM_SHEET ? &g_gem_frame : &g_digit_frame);
}

void dbl_append(uint64_t list, uint64_t obj)
{
    uint64_t* l = reinterpret_cast<uint64_t*>(list);
    *reinterpret_cast<uint64_t*>(obj + 0xD0) = 0;
    *reinterpret_cast<uint64_t*>(obj + 0xD8) = 0;
    if (l[0] == 0) {
        l[0] = obj;
        l[2] = obj;
    } else {
        *reinterpret_cast<uint64_t*>(l[2] + 0xD0) = obj;
        *reinterpret_cast<uint64_t*>(obj + 0xD8) = l[2];
        l[2] = obj;
    }
    ++g_appends;
}

const wm::game_fns g_wm_doubles = {dbl_alloc, dbl_frame, dbl_append};

// ---- ring doubles: the area info per action, the live targets and the factory over a synthetic area list -------------
struct info_row {
    uint32_t action;
    int32_t ret;
    uint32_t type;
    uint32_t kind;
    float radius;
};
constexpr uint32_t INFO_ROWS = 16;
info_row g_info[INFO_ROWS];
uint32_t g_info_count = 0;
constexpr uint32_t LIVE_MAX = 16;
uint32_t g_live[LIVE_MAX];
uint32_t g_live_count = 0;
alignas(16) uint8_t g_ui[0x11000];
constexpr uint32_t NODES = 16;
alignas(16) uint8_t g_nodes[NODES][0x80];
uint32_t g_nodes_used = 0;
uint32_t g_creates = 0;

int32_t dbl_area_info(uint32_t caster, uint32_t target, uint32_t action, void* info)
{
    (void)caster;
    (void)target;
    for (uint32_t k = 0; k < g_info_count; ++k) {
        if (g_info[k].action == action) {
            uint8_t* p = static_cast<uint8_t*>(info);
            memcpy(p + rg::INFO_TYPE, &g_info[k].type, 4);
            memcpy(p + rg::INFO_KIND, &g_info[k].kind, 4);
            memcpy(p + rg::INFO_RADIUS, &g_info[k].radius, 4);
            return g_info[k].ret;
        }
    }
    return -1;
}

int32_t dbl_target_live(uint32_t target)
{
    for (uint32_t k = 0; k < g_live_count; ++k) {
        if (g_live[k] == target) {
            return 1;
        }
    }
    return 0;
}

uint32_t& u32_at(uint8_t* p, uint32_t off) { return *reinterpret_cast<uint32_t*>(p + off); }
uint64_t& u64_at(uint8_t* p, uint32_t off) { return *reinterpret_cast<uint64_t*>(p + off); }

// The factory's own behaviour (0x2911d0): a node keyed by the target is updated (radius, CLUT from the type), else a new
// one is linked at the head of the list, live; the CLUT is the native one for the type (3 -> 0, 4 -> 3, 1 -> 1, 2 -> 5).
uint64_t dbl_create(uint32_t target, uint32_t type, float radius)
{
    const uint32_t clut = type == 1 ? 1 : type == 2 ? 5 : type == 4 ? 3 : 0;
    ++g_creates;
    for (uint8_t* n = reinterpret_cast<uint8_t*>(u64_at(g_ui, rg::UI_AREA_LIST)); n != nullptr;
         n = reinterpret_cast<uint8_t*>(u64_at(n, rg::RING_NEXT))) {
        if (u32_at(n, rg::RING_KEY) == target) {
            memcpy(n + 0x64, &radius, 4);
            u32_at(n, rg::RING_CLUT) = clut;
            return reinterpret_cast<uint64_t>(n);
        }
    }
    if (g_nodes_used >= NODES) {
        return 0;
    }
    uint8_t* n = g_nodes[g_nodes_used++];
    memset(n, 0, 0x80);
    u32_at(n, rg::RING_KEY) = target;
    u32_at(n, rg::RING_CLUT) = clut;
    u32_at(n, rg::RING_FLAGS) = 1;
    memcpy(n + 0x64, &radius, 4);
    u64_at(n, rg::RING_NEXT) = u64_at(g_ui, rg::UI_AREA_LIST);
    u64_at(g_ui, rg::UI_AREA_LIST) = reinterpret_cast<uint64_t>(n);
    return reinterpret_cast<uint64_t>(n);
}

const rg::game_fns g_rg_doubles = {dbl_area_info, dbl_target_live, dbl_create};

}  // namespace

extern "C" {

// ---- world marks ---------------------------------------------------------------------------------------------------------
uint32_t ILCT_WmSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(wm::line_entry), sizeof(wm::lines_record), sizeof(wm::seat_input),
                              sizeof(wm::seat_track), sizeof(wm::trigger_state), sizeof(wm::diamond),
                              sizeof(wm::diamonds_record), sizeof(wm::counters), sizeof(wm::status_view),
                              sizeof(rg::seat_ring), sizeof(rg::rings_record), sizeof(rg::seat_aim),
                              sizeof(rg::counters), sizeof(rg::applied_ring), sizeof(rg::status_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

uint32_t ILCT_WmLineClut(const wm::lines_record* r, uint32_t source, uint32_t type, uint32_t native)
{
    return wm::line_clut(*r, source, type, native);
}

uint32_t ILCT_WmArrowClut(const wm::lines_record* r, uint32_t source, uint32_t native)
{
    return wm::arrow_clut(*r, source, native);
}

uint32_t ILCT_WmClutOfColour(uint32_t colour) { return wm::clut_of_colour(static_cast<uint8_t>(colour)); }
uint32_t ILCT_WmGemCell(uint32_t colour) { return wm::gem_cell(static_cast<uint8_t>(colour)); }
uint32_t ILCT_WmAlpha(uint64_t shown_ms, uint64_t now_ms) { return wm::diamond_alpha(shown_ms, now_ms); }

uint32_t ILCT_WmTriggerStep(wm::trigger_state* st, uint32_t epoch, const wm::seat_input* in, uint32_t identify,
                            uint64_t now_ms)
{
    return wm::trigger_step(*st, epoch, in, identify, now_ms);
}

void ILCT_WmReset(void) { wm::test_reset(); }
void ILCT_WmPublishLines(const wm::lines_record* r) { wm::test_publish_lines(*r); }
void ILCT_WmPublishDiamonds(const wm::diamonds_record* r) { wm::test_publish_diamonds(*r); }
int32_t ILCT_WmReadLines(wm::lines_record* out) { return wm::test_read_lines(out) ? 1 : 0; }
int32_t ILCT_WmReadDiamonds(wm::diamonds_record* out) { return wm::test_read_diamonds(out) ? 1 : 0; }
void ILCT_WmSetLinesEnabled(int32_t on) { wm::set_lines_enabled(on != 0); }
void ILCT_WmSetDiamondsEnabled(int32_t on) { wm::set_diamonds_enabled(on != 0); }
void ILCT_WmIdentify(uint32_t seat) { wm::identify(seat); }
void ILCT_WmSetImageBase(uint64_t base) { wm::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_WmSetAttached(uint32_t mask) { wm::test_set_attached(mask); }
int32_t ILCT_WmAttach(uint64_t base) { return wm::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }
void ILCT_WmConfigure(const ilc_settings* s) { wm::configure_from_settings(*s); }
void ILCT_WmStep(uint64_t now_ms) { wm::step(now_ms); }
void ILCT_WmStop(void) { wm::stop(); }
void ILCT_WmStats(wm::counters* out) { memcpy(out, wm::stats(), sizeof(*out)); }

void ILCT_WmStatus(wm::status_view* out)
{
    const wm::status_view v = wm::status();
    memcpy(out, &v, sizeof(v));
}

void ILCT_WmTriggers(wm::trigger_state* out) { memcpy(out, wm::test_triggers(), sizeof(*out)); }
int32_t ILCT_WmLineCall(patch::ctx* c, uint32_t epoch) { return wm::line_call(c, epoch); }
int32_t ILCT_WmArrowCall(patch::ctx* c, uint32_t epoch) { return wm::arrow_call(c, epoch); }
int32_t ILCT_WmOverheadCall(patch::ctx* c, uint32_t epoch) { return wm::overhead_call(c, epoch); }
void ILCT_WmOverheadAfter(patch::ctx* c, uint32_t epoch) { wm::overhead_after(c, epoch); }
int32_t ILCT_WmRow(uint32_t which) { return wm::test_row(which); }

void ILCT_WmUseDoubles(int32_t on)
{
    g_frame_count = 0;
    g_appends = 0;
    memset(g_frames, 0, sizeof(g_frames));
    wm::test_set_fns(on != 0 ? &g_wm_doubles : nullptr);
}

uint32_t ILCT_WmFrameLog(uint64_t* out, uint32_t cap)
{
    for (uint32_t k = 0; k < g_frame_count && k < cap; ++k) {
        out[4 * k + 0] = g_frames[k].obj;
        out[4 * k + 1] = g_frames[k].texture;
        out[4 * k + 2] = g_frames[k].sheet;
        out[4 * k + 3] = g_frames[k].cell;
    }
    return g_frame_count;
}

uint32_t ILCT_WmAppends(void) { return g_appends; }

// ---- rings ---------------------------------------------------------------------------------------------------------------
uint32_t ILCT_RgAreaKind(uint32_t radius, uint32_t flags) { return rg::area_kind(static_cast<uint8_t>(radius), flags); }
uint32_t ILCT_RgFamily(uint32_t page) { return rg::family_of_page(static_cast<uint8_t>(page)); }

int32_t ILCT_RgAllowed(uint32_t action, uint32_t family, uint32_t kind)
{
    return rg::ring_allowed(static_cast<uint16_t>(action), static_cast<uint8_t>(family), static_cast<uint8_t>(kind)) ? 1
                                                                                                                   : 0;
}

int32_t ILCT_RgStockAllowed(uint32_t action) { return rg::stock_ring_allowed(static_cast<uint16_t>(action)) ? 1 : 0; }
uint32_t ILCT_RgWanted(const rg::seat_aim* a) { return rg::wanted_target(*a); }
void ILCT_RgReset(void) { rg::test_reset(); }
void ILCT_RgPublish(const rg::rings_record* r) { rg::test_publish(*r); }
int32_t ILCT_RgRead(rg::rings_record* out) { return rg::test_read(out) ? 1 : 0; }
void ILCT_RgSetEnabled(int32_t on) { rg::set_enabled(on != 0); }
void ILCT_RgSetImageBase(uint64_t base) { rg::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_RgSetAttached(uint32_t mask) { rg::test_set_attached(mask); }
void ILCT_RgForceLive(int32_t live) { rg::test_force_live(live); }
int32_t ILCT_RgAttach(uint64_t base) { return rg::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }
void ILCT_RgConfigure(const ilc_settings* s) { rg::configure_from_settings(*s); }
void ILCT_RgStep(uint64_t now_ms) { rg::step(now_ms); }
void ILCT_RgStop(void) { rg::stop(); }
void ILCT_RgTick(int32_t live) { rg::tick(live != 0); }
int32_t ILCT_RgRunJob(uint64_t ui, uint32_t epoch) { return rg::run_job(ui, epoch); }
void ILCT_RgStats(rg::counters* out) { memcpy(out, rg::stats(), sizeof(*out)); }

void ILCT_RgStatus(rg::status_view* out)
{
    const rg::status_view v = rg::status();
    memcpy(out, &v, sizeof(v));
}

void ILCT_RgApplied(rg::applied_ring* out) { memcpy(out, rg::test_applied(), sizeof(rg::applied_ring) * rg::SEATS); }
uint32_t ILCT_RgRelease(uint64_t ui, uint32_t target) { return rg::test_release(ui, target); }
void ILCT_RgSetAims(const rg::seat_aim* aims) { rg::test_set_aims(aims); }
int32_t ILCT_RgTriggerCall(patch::ctx* c, uint32_t epoch) { return rg::trigger_call(c, epoch); }
int32_t ILCT_RgRow(void) { return rg::test_row(); }

// Doubles: a fresh synthetic UI (area list empty), no info rows, no live targets. Returns the UI manager's address.
uint64_t ILCT_RgUseDoubles(int32_t on)
{
    memset(g_ui, 0, sizeof(g_ui));
    memset(g_nodes, 0, sizeof(g_nodes));
    g_nodes_used = 0;
    g_info_count = 0;
    g_live_count = 0;
    g_creates = 0;
    rg::test_set_fns(on != 0 ? &g_rg_doubles : nullptr);
    return reinterpret_cast<uint64_t>(g_ui);
}

void ILCT_RgDoubleInfo(uint32_t action, int32_t ret, uint32_t type, uint32_t kind, float radius)
{
    if (g_info_count < INFO_ROWS) {
        g_info[g_info_count++] = {action, ret, type, kind, radius};
    }
}

void ILCT_RgDoubleLive(uint32_t handle)
{
    if (g_live_count < LIVE_MAX) {
        g_live[g_live_count++] = handle;
    }
}

uint32_t ILCT_RgDoubleCreates(void) { return g_creates; }

}  // extern "C"
