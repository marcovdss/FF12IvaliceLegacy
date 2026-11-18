// Ivalice Legacy co-op module: our own overlay (see overlay.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "overlay/overlay.h"

#include "control/control.h"
#include "firewall/firewall.h"
#include "frontend/frontend.h"
#include "frontend/presenter.h"
#include "hud/canvas.h"
#include "hud/hud_facts.h"
#include "hud/menus_draw.h"
#include "hud/snapshot.h"
#include "hud/snapshot_facts.h"
#include "hud/target_info_native.h"
#include "session/game_facts.h"
#include "hud/battle_log.h"
#include "hud/party_rows.h"
#include "hud/target_info.h"
#include "overlay/batch.h"
#include "overlay/feed.h"
#include "overlay/gpu.h"
#include "overlay/hide.h"
#include "overlay/hud_scene.h"
#include "overlay/scene.h"
#include "overlay/states.h"
#include "overlay/font.h"
#include "overlay/theme.h"
#include "runtime/guard.h"
#include "runtime/log.h"
#include "runtime/probe.h"

namespace ilc::overlay {
namespace {

namespace md = hud::menus_draw;
namespace hf = hud::facts;
namespace sf = hud::snapfacts;

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

constexpr int32_t ROW_OV = find_row("OV-01");
static_assert(ROW_OV >= 0 && patch::SITES[ROW_OV].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_OV].kind == ILC_SITE_KIND_CALL &&
                  patch::SITES[ROW_OV].subsystem == ILC_SUBSYS_RENDER &&
                  (patch::SITES[ROW_OV].flags & ILC_SITE_FLAG_OPTIONAL) != 0,
              "the overlay frame hook is an optional RENDER call row");
constexpr const char* OV_GATES[] = {"OV-V1", "OV-V2", "OV-V3"};
constexpr bool gates_of_ov()
{
    uint32_t n = 0;
    for (const char* id : OV_GATES) {
        const int32_t row = find_row(id);
        if (row < 0 || patch::SITES[row].policy != ILC_SITE_POLICY_GATE ||
            patch::SITES[row].gate != static_cast<uint32_t>(ROW_OV)) {
            return false;
        }
        ++n;
    }
    return n == 3;
}
static_assert(gates_of_ov(), "OV-V1..OV-V3 gate OV-01: the wrapper, its Present call and the renderer's fields");

// A UI window's flags (+0x40, u32): the draw at 0x245e88 runs only while (flags & 0x405) == 5 (bit 0 drawn, bit 2
// updated once, bit 10 being destroyed) [verified live and static, workspace hud-hide study 2026-11-16].
constexpr uint32_t WINDOW_FLAGS = 0x40;
constexpr uint32_t WINDOW_DRAW_MASK = 0x405;
constexpr uint32_t WINDOW_DRAWN = 0x005;
constexpr uint32_t WINDOW_ALPHA = 0xA0;        // u8: the window's own alpha (0x80 = 1.0) [hud-hide study, window layout]

uintptr_t g_base = 0x120000;   // NOTADDR: the pinned image base until attach
volatile uint32_t g_attached = 0;
volatile uint32_t g_dead = 0;
volatile uint64_t g_frame_ms = 0;   // module clock of the last frame-hook call (hide::watch: the game still presents)
volatile uint64_t g_present_ms = 0; // module clock of the last frame that presented (overlay_live)
volatile int32_t g_force_live = -1; // host tests
live_streak g_streak = {};          // co-op thread (live_track)
volatile uint32_t g_test_card = 0;
volatile uint32_t g_developer = 0;
volatile uint32_t g_inject = 0;
counters g_counters = {};
int64_t g_qpc_hz = 0;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }
void store(volatile uint64_t& c, uint64_t v) { __atomic_store_n(&c, v, __ATOMIC_RELAXED); }
uint64_t load(const volatile uint64_t& c) { return __atomic_load_n(&c, __ATOMIC_RELAXED); }
void event(uint32_t bit) { __atomic_or_fetch(&g_counters.events, static_cast<uint64_t>(bit), __ATOMIC_RELAXED); }

uint64_t game(uint64_t va) { return g_base + (va - hf::PINNED_BASE); }

uint64_t qpc()
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return static_cast<uint64_t>(t.QuadPart);
}

uint64_t to_us(uint64_t ticks)
{
    return g_qpc_hz > 0 ? ticks * 1000000ull / static_cast<uint64_t>(g_qpc_hz) : 0;
}

bool fresh(uint64_t stamp, uint64_t now, uint64_t limit)
{
    return stamp != 0 && (now >= stamp ? now - stamp <= limit : stamp - now <= limit);
}

// ---- render-thread state (one render thread presents; the handler is not re-entered) ---------------------------------
batch g_batch;
md::menu_view g_view;
hud::hud_snapshot g_snap;
// The HUD elements' inputs, copied once per frame (hud_presentable).
hud::party_rows::rows_record g_rows;
feed::record g_feed;
hud::target_info::view g_ti;
hud::battle_log::view g_log;
states::record g_states;
// The carousel's restarts: when each seat's cursor arrived on its row, when Target Info's target changed.
struct focus_key {
    uint8_t kind, page, group, cursor;
    uint16_t first;
    uint16_t reserved;
};
focus_key g_focus_key[md::SEATS] = {};
uint64_t g_focus_ms[md::SEATS] = {};
uint32_t g_ti_handle = 0;
uint64_t g_ti_since = 0;

void track_focus(uint64_t now)
{
    for (uint32_t s = 0; s < md::SEATS; ++s) {
        const md::seat_view& sv = g_view.seats[s];
        const focus_key k = {sv.kind, sv.page, sv.group, sv.cursor, sv.first, 0};
        const focus_key& o = g_focus_key[s];
        if (k.kind != o.kind || k.page != o.page || k.group != o.group || k.cursor != o.cursor || k.first != o.first ||
            g_focus_ms[s] == 0) {
            g_focus_key[s] = k;
            g_focus_ms[s] = now != 0 ? now : 1;
        }
    }
}
uint64_t g_last_call = 0;      // qpc of the previous handler call
uint32_t g_last_dropped = 0;

struct job {
    uint64_t renderer;
    uint64_t now;
    uint32_t test_card;
    uint32_t menus;            // the seats' menus are presentable this frame
    uint32_t p1;               // Player 1's window is ours (P1 not on the game's own frontends)
    uint32_t hud;              // the HUD elements' inputs this frame (hud_have bits)
    uint8_t boss_gauge;
    uint8_t party_on;          // the party list element is switched on
    uint8_t reserved[2];
    float alpha;
    int32_t result;            // gpu::result
    uint32_t finished;
    scene::menus_out menus_out;
    hud_scene::output hud_out;
    gpu::frame_info info;
};

// Which HUD inputs this frame has (hud_presentable).
enum hud_have : uint32_t {
    HAVE_SNAP = 0x01, HAVE_ROWS = 0x02, HAVE_FEED = 0x04, HAVE_TI = 0x08, HAVE_LOG = 0x10, HAVE_STATES = 0x20,
    HAVE_MENUS = 0x40,
};

void run_job(void* arg)
{
    job& j = *static_cast<job*>(arg);
    // A theme the co-op thread read is applied here, at the frame boundary (gpu re-creates the atlas texture from it);
    // the small-size tracking follows the last back buffer's scale.
    theme::take_pending();
    {
        const gpu::status_view gs = gpu::status();
        const space sp = make_space(gs.last_w, gs.last_h);
        font::set_pixel_scale(sp.scale > 0.0f ? sp.scale : 1.0f);
    }
    g_batch.clear();
    g_batch.alpha = j.alpha;
    // The seat windows claim the batch first (a full batch then leaves out the card, never a window, P1's least of
    // all); the card is composed after them and moved underneath.
    if (j.menus != 0) {
        track_focus(j.now);
        j.menus_out = scene::seat_windows(g_batch, g_view, g_snap, j.now, j.p1 != 0, g_focus_ms);
    }
    // The HUD elements next (each within its own budget, rolled back whole when it does not fit).
    if (j.hud != 0) {
        hud_scene::input in = {};
        in.snap = (j.hud & HAVE_SNAP) != 0 ? &g_snap : nullptr;
        in.rows = (j.hud & HAVE_ROWS) != 0 ? &g_rows : nullptr;
        in.feed = (j.hud & HAVE_FEED) != 0 ? &g_feed : nullptr;
        in.menus = (j.hud & HAVE_MENUS) != 0 ? &g_view : nullptr;
        in.ti = (j.hud & HAVE_TI) != 0 ? &g_ti : nullptr;
        in.log = (j.hud & HAVE_LOG) != 0 ? &g_log : nullptr;
        in.states = (j.hud & HAVE_STATES) != 0 ? &g_states : nullptr;
        in.now = j.now;
        in.boss_gauge = j.boss_gauge;
        in.party_on = j.party_on;
        if ((j.hud & HAVE_TI) != 0 && g_ti.handle != g_ti_handle) {
            g_ti_handle = g_ti.handle;
            g_ti_since = j.now;
        }
        in.ti_since = g_ti_since;
        g_batch.alpha = j.alpha;
        j.hud_out = hud_scene::compose(g_batch, in);
    }
    const uint32_t card_from = g_batch.count();
    if (j.test_card != 0) {
        scene::card_info ci = {};
        const gpu::status_view gs = gpu::status();
        ci.bb_w = gs.last_w;
        ci.bb_h = gs.last_h;
        ci.format = gs.last_format;
        ci.frame_us = static_cast<uint32_t>(load(g_counters.frame_us));
        ci.draw_us = static_cast<uint32_t>(load(g_counters.cost_us_last));
        ci.frames = load(g_counters.drawn);
        ci.hud_alpha_pct = static_cast<uint32_t>(j.alpha * 100.0f + 0.5f);
        ci.menus_live = frontend::presenter::live(j.now) ? 1 : 0;
        if (j.menus != 0) {
            for (uint32_t s = 0; s < 4; ++s) {
                ci.seat_colour[s] = g_view.seats[s].kind != md::WK_NONE ? from_ps2(g_view.seats[s].colour) : 0u;
            }
        }
        g_batch.alpha = j.alpha;
        scene::test_card(g_batch, ci);
        g_batch.to_front(card_from);
    }
    if (__atomic_load_n(&g_inject, __ATOMIC_RELAXED) != 0) {
        gpu::test_set_fault(__atomic_load_n(&g_inject, __ATOMIC_RELAXED));
    }
    j.result = gpu::frame(j.renderer, g_batch, &j.info);
    j.finished = 1;
}

void run_restore(void*)
{
    gpu::restore_after_fault();
}

// The guests' menus may be presented this frame: co-op on, FIREWALL_MENUS live, a fresh view and snapshot of this
// epoch (copied into g_view / g_snap; a torn copy is tried once more, never waited for).
bool menus_presentable(uint32_t epoch, uint64_t now)
{
    if (!patch::subsystem_live(ILC_SUBSYS_FIREWALL_MENUS) || control::current_state() != control::STATE_ON) {
        return false;
    }
    bool got = false;
    for (uint32_t k = 0; k < 2 && !got; ++k) {
        got = md::read(g_view);
    }
    if (!got || g_view.head.epoch != epoch || epoch == 0 || !fresh(g_view.built_ms, now, md::VIEW_STALE_MS)) {
        bump(g_counters.no_view);
        return false;
    }
    got = false;
    for (uint32_t k = 0; k < 2 && !got; ++k) {
        got = hud::snapshot::read(g_snap);
    }
    if (!got || g_snap.head.epoch != epoch || !fresh(g_snap.sampled_ms, now, hud::SNAPSHOT_STALE_MS) ||
        (g_snap.head.flags & hud::SNAP_WORLD) == 0 || g_snap.layout >= hud::LAYOUT_COUNT) {
        bump(g_counters.no_snapshot);
        return false;
    }
    return true;
}

bool fresh_pair(uint32_t got_epoch, uint32_t epoch, uint64_t built, uint64_t now, uint64_t limit)
{
    return got_epoch == epoch && epoch != 0 && fresh(built, now, limit);
}

// The HUD elements' inputs this frame: co-op on, RENDER live, a fresh HUD snapshot of this epoch (read here unless the
// menus already did), then whatever else is fresh. Each element draws only from what it has (hud_scene.h).
uint32_t hud_presentable(uint32_t epoch, uint64_t now, bool snap_read, bool view_read, uint8_t* boss_gauge)
{
    *boss_gauge = 0;
    if (control::current_state() != control::STATE_ON || !patch::subsystem_live(ILC_SUBSYS_RENDER) || epoch == 0) {
        return 0;
    }
    if (!snap_read) {
        bool got = false;
        for (uint32_t k = 0; k < 2 && !got; ++k) {
            got = hud::snapshot::read(g_snap);
        }
        if (!got || g_snap.head.epoch != epoch || !fresh(g_snap.sampled_ms, now, hud::SNAPSHOT_STALE_MS) ||
            (g_snap.head.flags & hud::SNAP_WORLD) == 0) {
            return 0;
        }
    }
    *boss_gauge = (g_snap.head.flags & hud::SNAP_BOSS_GAUGE) != 0 ? 1 : 0;
    uint32_t have = HAVE_SNAP;
    if (view_read) {
        have |= HAVE_MENUS;
    }
    if (hud::party_rows::read(&g_rows) &&
        fresh_pair(g_rows.head.epoch, epoch, g_rows.sampled_ms, now, hud::party_rows::RECORD_STALE_MS)) {
        have |= HAVE_ROWS;
    }
    if (feed::read(g_feed) && fresh_pair(g_feed.head.epoch, epoch, g_feed.built_ms, now, feed::STALE_MS)) {
        have |= HAVE_FEED;
    }
    if (hud::target_info::enabled() && hud::target_info::latest(g_ti) &&
        fresh_pair(g_ti.head.epoch, epoch, g_ti.built_ms, now, hud::target_info::LIVE_MS)) {
        have |= HAVE_TI;
    }
    if (hud::battle_log::current().enabled != 0 && hud::battle_log::latest(g_log) &&
        fresh_pair(g_log.head.epoch, epoch, g_log.built_ms, now, hud::battle_log::LIVE_MS)) {
        if (g_log.fade_s == 0) {
            g_log.fade_s = static_cast<uint8_t>(hud_scene::LOG_LIFE_MS / 1000);   // the overlay's faster fade
        }
        have |= HAVE_LOG;
    }
    if (states::read(g_states) && fresh_pair(g_states.head.epoch, epoch, g_states.built_ms, now, states::STALE_MS)) {
        have |= HAVE_STATES;
    }
    if (!hud::party_rows::enabled()) {
        have &= ~(HAVE_ROWS | HAVE_FEED);   // the party list off: no rows (and party_on 0: nothing drawn)
    }
    return have;
}

void note_result(int32_t r, const gpu::frame_info& info)
{
    store(g_counters.last_result, static_cast<uint64_t>(static_cast<uint32_t>(r)));
    switch (r) {
    case gpu::DRAWN:
        if (load(g_counters.drawn) == 0) {
            store(g_counters.bb_w, info.w);
            store(g_counters.bb_h, info.h);
            store(g_counters.bb_format, info.format);
            event(EV_FIRST_FRAME);
        }
        bump(g_counters.drawn);
        store(g_counters.quads_last, info.quads);
        return;
    case gpu::READY: bump(g_counters.ready); return;
    case gpu::NO_RENDERER: event(EV_NO_RENDERER); break;
    case gpu::MISMATCH: event(EV_MISMATCH); break;
    case gpu::CREATE_FAILED: event(EV_CREATE_FAILED); break;
    case gpu::DEVICE_REMOVED: event(EV_DEVICE_REMOVED); break;
    case gpu::BAD_BACK_BUFFER:
        store(g_counters.bb_w, info.w);
        store(g_counters.bb_h, info.h);
        store(g_counters.bb_format, info.format);
        event(EV_BAD_BACK_BUFFER);
        break;
    default: event(EV_GPU_OTHER); break;
    }
    bump(g_counters.failures);
}

void note_cost(uint64_t started)
{
    const uint64_t us = to_us(qpc() - started);
    store(g_counters.cost_us_last, us);
    if (us > load(g_counters.cost_us_max)) {
        store(g_counters.cost_us_max, us);   // one writer: the render thread
    }
    __atomic_add_fetch(&g_counters.cost_us_total, us, __ATOMIC_RELAXED);
}

void note_interval(uint64_t now_ticks)
{
    if (g_last_call != 0 && now_ticks > g_last_call) {
        const uint64_t dt = to_us(now_ticks - g_last_call);
        if (dt < 1000000) {
            const uint64_t prev = load(g_counters.frame_us);
            store(g_counters.frame_us, prev == 0 ? dt : prev + (static_cast<int64_t>(dt - prev) / 8));
        }
    }
    g_last_call = now_ticks;
}

// ---- co-op thread: what gets logged ---------------------------------------------------------------------------------------
uint64_t g_logged = 0;        // event bits already written
uint64_t g_report_ms = 0;
uint32_t g_hook_logged = 0;   // the not-hooked lines

const char* format_name(uint64_t f)
{
    switch (f) {
    case 28: return "R8G8B8A8_UNORM";
    case 29: return "R8G8B8A8_UNORM_SRGB";
    case 87: return "B8G8R8A8_UNORM";
    case 91: return "B8G8R8A8_UNORM_SRGB";
    case 24: return "R10G10B10A2_UNORM";
    case 10: return "R16G16B16A16_FLOAT";
    default: return "other";
    }
}

void log_events()
{
    const uint64_t ev = load(g_counters.events);
    const uint64_t added = ev & ~g_logged;
    if (added == 0) {
        return;
    }
    g_logged |= added;
    const gpu::status_view gs = gpu::status();
    if ((added & EV_FIRST_FRAME) != 0) {
        log::line l;
        l.s("overlay: first frame drawn, back buffer ").u(load(g_counters.bb_w)).s("x").u(load(g_counters.bb_h)).s(" ")
            .s(format_name(load(g_counters.bb_format))).s(" (format ").u(load(g_counters.bb_format)).s(")");
        log::post(log::INFO, l);
    }
    if ((added & EV_CREATE_FAILED) != 0) {
        log::line l;
        l.s("overlay: could not create its ").s(gpu::object_name(gs.failed_object)).s(" (hr ")
            .x(static_cast<uint32_t>(gs.failed_hr)).s("): nothing is drawn on this device and the guests' menus stay idle");
        log::post(log::ERR, l);
    }
    if ((added & EV_MISMATCH) != 0) {
        log::post(log::WARNING, "overlay: the swap chain's device is not the renderer's: nothing is drawn");
    }
    if ((added & EV_BAD_BACK_BUFFER) != 0) {
        log::line l;
        l.s("overlay: back buffer ").u(load(g_counters.bb_w)).s("x").u(load(g_counters.bb_h)).s(" format ")
            .u(load(g_counters.bb_format)).s(" is smaller than 640x360 or not drawable: nothing is drawn");
        log::post(log::WARNING, l);
    }
    if ((added & EV_NO_RENDERER) != 0) {
        log::post(log::WARNING, "overlay: the renderer's device, context or swap chain field is empty: nothing drawn");
    }
    if ((added & EV_DEVICE_REMOVED) != 0) {
        log::post(log::WARNING, "overlay: the D3D11 device was removed: nothing is drawn");
    }
    if ((added & EV_GPU_OTHER) != 0) {
        log::line l;
        l.s("overlay: a frame was not drawn (").s(gpu::result_name(static_cast<int32_t>(load(g_counters.last_result))))
            .s(")");
        log::post(log::WARNING, l);
    }
    if ((added & EV_FAULT) != 0) {
        const guard::fault_view f = guard::last_fault();
        log::line l;
        l.s("overlay: fault while drawing (code ").x(f.code).s(" at ").x(f.rip)
            .s("): pipeline state put back, RENDER latched off, the guests' menus stay idle");
        log::post(log::ERR, l);
    }
}

void report(uint64_t now)
{
    // One topic per line, each well under log::LINE_MAX (200 bytes with the 22-byte prefix): 0.1.59's two lines were
    // cut in hook.log and hid the pieces, P1's menu state and the quads (0.1.60 first play).
    const uint64_t calls = load(g_counters.drawn);
    const uint64_t avg = calls != 0 ? load(g_counters.cost_us_total) / calls : 0;
    const gpu::status_view gs = gpu::status();
    const status_view st = status();
    log::line a;
    a.s("overlay: ").s(st.row_hooked != 0 ? "hooked" : "not hooked").s(", device ")
        .s(gs.failed_object != 0 ? "failed" : (gs.ready != 0 ? "ready" : "none yet")).s(", back buffer ")
        .u(gs.last_w).s("x").u(gs.last_h).s(" fmt ").u(gs.last_format).s("; test card ")
        .s(st.test_card != 0 ? "on" : "off");
    log::post(log::INFO, a);
    log::line a2;
    a2.s("overlay frames: drawn ").u(calls).s(", empty ").u(load(g_counters.ready)).s(", HUD hidden ")
        .u(load(g_counters.hidden)).s(", idle ").u(load(g_counters.idle)).s(", busy ").u(load(g_counters.busy))
        .s(", refused ").u(load(g_counters.failures)).s(", faults ").u(load(g_counters.faults));
    log::post(log::INFO, a2);
    log::line b;
    b.s("overlay cost: last ").u(load(g_counters.cost_us_last)).s(" us, avg ").u(avg).s(" us, max ")
        .u(load(g_counters.cost_us_max)).s(" us; frame ").u(load(g_counters.frame_us)).s(" us; quads ")
        .u(load(g_counters.quads_last));
    log::post(log::INFO, b);
    const uint64_t hud = load(g_counters.hud_ms);
    const uint64_t present = presented_ms();
    log::line c;
    c.s("overlay live: ").s(live(now) ? "yes" : "no").s(", field ").s(field_open_now() ? "open" : "closed")
        .s(", heartbeat age ").u(hud != 0 && now >= hud ? now - hud : 0).s(" ms, presented age ")
        .u(present != 0 && now >= present ? now - present : 0).s(" ms; beats HD-01 ").u(load(g_counters.heartbeats))
        .s(", UI tick ").u(load(g_counters.tick_heartbeats));
    log::post(log::INFO, c);
    log::line c2;
    c2.s("overlay menus: stamps ").u(load(g_counters.stamps)).s(", windows ").u(load(g_counters.windows))
        .s(", left out ").u(load(g_counters.dropped_frames)).s("; no view ").u(load(g_counters.no_view))
        .s(", no snapshot ").u(load(g_counters.no_snapshot));
    log::post(log::INFO, c2);
    log::line d;
    d.s("overlay HUD quads: party ").u(load(g_counters.hud_quads_party)).s(", target ")
        .u(load(g_counters.hud_quads_target)).s(", log ").u(load(g_counters.hud_quads_log)).s("; refused ")
        .u(load(g_counters.hud_refused));
    log::post(log::INFO, d);
    const hide::status_view hs = hide::status();
    log::line e;
    e.s("overlay vanilla pieces: hidden ").x(hs.held).s(", wanted ").x(hs.wanted)
        .s(" (party 1, help 2|4, target 8, log B 0x10, log A 0x20, chain 0x40); pins ")
        .s(hs.pins == 1 ? "ok" : (hs.pins == 2 ? "OFF" : "unknown"));
    log::post(log::INFO, e);
    const frontend::presenter::run_view r = frontend::presenter::run();
    log::line f;
    f.s("overlay P1: menu ").s(frontend::p1_native() ? "vanilla" : "overlay").s(", run ").u(r.frames).s(", missed ")
        .u(r.missed).s("; prompt ").s(firewall::p1_prompt_ready() ? "ready" : "not ready").s(", why ")
        .x(firewall::p1_prompt_why()).s(" (1 off, 2 driver, 4 vanilla, 8 menu, 0x10 none, 0x20 foe)");
    log::post(log::INFO, f);
}

}  // namespace

// ---- boot -------------------------------------------------------------------------------------------------------------
bool test_attach(uintptr_t image_base)
{
    g_base = image_base;
    LARGE_INTEGER hz;
    g_qpc_hz = QueryPerformanceFrequency(&hz) ? hz.QuadPart : 0;
    // The theme's default atlas: made by theme::attach at the boot (host tests: by prepare_atlas, without a themes folder).
    const bool ok = gpu::prepare_atlas() && guard::attach();
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    return ok;
}

bool attach(uintptr_t image_base)
{
    const uint32_t row = static_cast<uint32_t>(ROW_OV);
    bool ok = test_attach(image_base) && patch::set_handler(row, frame_hook) && patch::set_site_armed(row, true);
    if (!ok) {
        patch::set_site_armed(row, false);
        __atomic_store_n(&g_attached, 0u, __ATOMIC_RELEASE);
    }
    return ok;
}

void configure_from_settings(const ilc_settings& s)
{
    theme::configure_from_settings(s);   // the HUD theme (hud_theme): read on the co-op thread, applied at a frame boundary
    // Generation 0 is the native defaults record: the test card stays off until the player's settings say otherwise.
    const bool dev = s.header.generation != 0 && s.developer != 0;
    __atomic_store_n(&g_developer, dev ? 1u : 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_test_card, dev && s.hud_test_draw != 0 ? 1u : 0u, __ATOMIC_RELEASE);
}

// ---- overlay_live (any thread) ---------------------------------------------------------------------------------------------
bool field_open_now()
{
    namespace tf = hud::target_info::giveback::facts;
    namespace gf = session::facts;
    uint32_t command_actor = 0, command_flag = 0;
    uint64_t pause_a = 1, pause_b = 1;
    return load_game(&command_flag, reinterpret_cast<const void*>(game(gf::COMMAND_FLAG)), sizeof(command_flag)) &&
           load_game(&command_actor, reinterpret_cast<const void*>(game(gf::COMMAND_ACTOR)), sizeof(command_actor)) &&
           hide::field_ui(command_flag, command_actor) &&
           load_game(&pause_a, reinterpret_cast<const void*>(game(tf::PAUSE_PRIMARY)), sizeof(pause_a)) && pause_a == 0 &&
           load_game(&pause_b, reinterpret_cast<const void*>(game(tf::PAUSE_SECONDARY)), sizeof(pause_b)) && pause_b == 0;
}

bool live(uint64_t now_ms)
{
    const int32_t forced = __atomic_load_n(&g_force_live, __ATOMIC_ACQUIRE);
    if (forced >= 0) {
        return forced != 0;
    }
    return __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 && __atomic_load_n(&g_dead, __ATOMIC_ACQUIRE) == 0 &&
           control::current_state() == control::STATE_ON && patch::subsystem_live(ILC_SUBSYS_RENDER) &&
           fresh(__atomic_load_n(&g_present_ms, __ATOMIC_ACQUIRE), now_ms, LIVE_MS) &&
           fresh(__atomic_load_n(&g_counters.hud_ms, __ATOMIC_ACQUIRE), now_ms, LIVE_MS) && field_open_now();
}

live_streak live_track(uint64_t now_ms)
{
    if (live(now_ms)) {
        if (g_streak.since == 0 || g_streak.last == 0 || now_ms < g_streak.last || now_ms - g_streak.last > LIVE_MS) {
            g_streak.since = now_ms != 0 ? now_ms : 1;
        }
        g_streak.last = now_ms != 0 ? now_ms : 1;
    } else if (g_streak.last == 0 || now_ms < g_streak.last || now_ms - g_streak.last > LIVE_MS) {
        g_streak.since = 0;   // not live for longer than the hysteresis: the streak ends
    }
    return g_streak;
}

void live_restart()
{
    g_streak = live_streak{};
}

uint64_t presented_ms()
{
    return __atomic_load_n(&g_present_ms, __ATOMIC_ACQUIRE);
}

// ---- the HUD heartbeat (game thread, HD-01) -------------------------------------------------------------------------------
void note_hud(uint64_t manager, uint64_t context)
{
    if (__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) == 0 || manager == 0 || context == 0 || !field_open_now()) {
        return;   // item 1: a menu or the pause screen is up while the HUD fades out under it
    }
    uint32_t ready = 0;
    uint32_t flags = 0;
    if (!load_game(&ready, reinterpret_cast<const void*>(game(hf::UI_READY)), sizeof(ready)) || ready == 0 ||
        !load_game(&flags, reinterpret_cast<const void*>(game(sf::RENDER_FLAGS)), sizeof(flags)) ||
        (flags & sf::RENDER_UI) == 0) {
        return;
    }
    // The HUD root's context (a window's context stays set while its children draw): its alpha is the HUD's fade.
    uint64_t window = manager;
    uint64_t root_context = context;
    for (uint32_t depth = 0; depth < hf::ANCESTORS_MAX; ++depth) {
        uint64_t parent = 0;
        uint64_t parent_context = 0;
        if (!load_game(&parent, reinterpret_cast<const void*>(window + hf::WINDOW_PARENT), sizeof(parent)) ||
            parent == 0) {
            break;
        }
        if (!load_game(&parent_context, reinterpret_cast<const void*>(parent + hf::WINDOW_CONTEXT),
                       sizeof(parent_context)) ||
            parent_context == 0) {
            break;
        }
        window = parent;
        root_context = parent_context;
    }
    int16_t alpha = 0;
    if (!load_game(&alpha, reinterpret_cast<const void*>(root_context + hf::CONTEXT_ALPHA), sizeof(alpha)) ||
        alpha <= 0) {
        return;
    }
    store(g_counters.hud_alpha, static_cast<uint64_t>(alpha));
    __atomic_store_n(&g_counters.hud_ms, patch::now_ms(), __ATOMIC_RELEASE);
    bump(g_counters.heartbeats);
}

bool log_replaces_native(uint64_t now_ms)
{
    return hud::battle_log::current().enabled != 0 && live(now_ms);
}

// ---- the heartbeat's second source (game thread, UI tick; see overlay.h) ------------------------------------------------------
void note_ui_tick()
{
    namespace tf = hud::target_info::giveback::facts;
    namespace gf = session::facts;
    if (__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    const auto u32_at = [](uint64_t va, uint32_t* out) {
        return load_game(out, reinterpret_cast<const void*>(game(va)), sizeof(*out));
    };
    const auto u64_at = [](uint64_t address, uint64_t* out) {
        return load_game(out, reinterpret_cast<const void*>(address), sizeof(*out));
    };
    uint32_t ready = 0, flags = 0, command_actor = 0, command_flag = 0;
    uint64_t pause_a = 1, pause_b = 1, manager = 0;
    if (!u32_at(hf::UI_READY, &ready) || ready == 0 || !u32_at(sf::RENDER_FLAGS, &flags) ||
        (flags & sf::RENDER_UI) == 0 || !u64_at(game(tf::PAUSE_PRIMARY), &pause_a) || pause_a != 0 ||
        !u64_at(game(tf::PAUSE_SECONDARY), &pause_b) || pause_b != 0 || !u32_at(gf::COMMAND_ACTOR, &command_actor) ||
        !u32_at(gf::COMMAND_FLAG, &command_flag)) {
        return;
    }
    if (!hide::field_ui(command_flag, command_actor)) {
        return;   // the main menu, the map, a shop, dialogue: not the HUD
    }
    uint64_t hud_handler = 0;
    uint32_t hud_flags = 0;
    uint8_t hud_alpha = 0;
    if (!u64_at(game(tf::UI_MANAGER_PTR), &manager) || manager == 0 ||
        !u64_at(manager + tf::UI_HUD, &hud_handler) || hud_handler != game(tf::HUD_HANDLER) ||
        !load_game(&hud_flags, reinterpret_cast<const void*>(manager + tf::UI_HUD + WINDOW_FLAGS), sizeof(hud_flags)) ||
        (hud_flags & WINDOW_DRAW_MASK) != WINDOW_DRAWN ||
        !load_game(&hud_alpha, reinterpret_cast<const void*>(manager + tf::UI_HUD + WINDOW_ALPHA), sizeof(hud_alpha)) ||
        hud_alpha == 0) {
        return;
    }
    // The HUD window's own alpha (the HUD's fade as this tick sees it), not HD-01's last reading.
    store(g_counters.hud_alpha, hud_alpha);
    __atomic_store_n(&g_counters.hud_ms, patch::now_ms(), __ATOMIC_RELEASE);
    bump(g_counters.tick_heartbeats);
}

// ---- the frame hook (render thread) ----------------------------------------------------------------------------------------
int32_t frame_hook(patch::ctx* c, uint32_t epoch)
{
    const uint64_t started = qpc();
    note_interval(started);
    bump(g_counters.reached);
    {
        const uint64_t ms = patch::now_ms();
        __atomic_store_n(&g_frame_ms, ms != 0 ? ms : uint64_t{1}, __ATOMIC_RELEASE);
    }
    const uint32_t row = static_cast<uint32_t>(ROW_OV);
    // Every way out that presents nothing breaks the presenter's run of presented frames (Player 1's arming needs an
    // unbroken run; frontend/presenter.h).
    if (__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) == 0 || __atomic_load_n(&g_dead, __ATOMIC_ACQUIRE) != 0 ||
        !guard::ready() || c == nullptr) {
        bump(g_counters.declined);
        patch::note_declined(row);
        frontend::presenter::break_run();
        return patch::RUN_ORIGINAL;
    }
    const uint64_t now = patch::now_ms();
    const uint64_t hud = __atomic_load_n(&g_counters.hud_ms, __ATOMIC_ACQUIRE);
    if (!fresh(hud, now, HUD_FRESH_MS) || !field_open_now()) {
        // The game hides its HUD (menu, map, cutscene, fade), or a menu or the pause screen is up while the HUD still
        // fades under it (item 1: read here, so the overlay leaves the very frame the game's flag says so).
        bump(g_counters.hidden);
        frontend::presenter::break_run();
        return patch::RUN_ORIGINAL;
    }
    float alpha = static_cast<float>(load(g_counters.hud_alpha)) / 128.0f;
    alpha = alpha > 1.0f ? 1.0f : alpha;
    job j = {};
    j.renderer = c->rcx;
    j.now = now;
    j.alpha = alpha;
    j.test_card = __atomic_load_n(&g_test_card, __ATOMIC_ACQUIRE);
    j.menus = menus_presentable(epoch, now) ? 1u : 0u;
    j.p1 = frontend::p1_native() ? 0u : 1u;   // read once: the co-op thread may flip it between frames
    j.hud = hud_presentable(epoch, now, j.menus != 0, j.menus != 0, &j.boss_gauge);
    // The party list element's switch (review 2): off, the list draws nothing, so the vanilla panel is never hidden.
    j.party_on = hud::party_rows::enabled() ? 1 : 0;
    if (j.test_card == 0 && j.menus == 0 && j.hud == 0) {
        bump(g_counters.idle);
        frontend::presenter::break_run();
        return patch::RUN_ORIGINAL;
    }
    const int32_t r = guard::call(run_job, &j);
    if (r == guard::FAULTED) {
        guard::call(run_restore, nullptr);   // the game's pipeline state back, if the fault left ours bound
        bump(g_counters.faults);
        event(EV_FAULT);
        __atomic_store_n(&g_dead, 1u, __ATOMIC_RELEASE);
        patch::fault_subsystem(ILC_SUBSYS_RENDER);
        frontend::presenter::note_drawn(0);  // every seat's menu idles (P1's back to vanilla) from the next co-op beat
        return patch::RUN_ORIGINAL;
    }
    if (r != guard::RETURNED || j.finished == 0) {
        bump(g_counters.busy);
        frontend::presenter::break_run();
        return patch::RUN_ORIGINAL;
    }
    note_result(j.result, j.info);
    if (j.result == gpu::DRAWN) {
        note_cost(started);
        frontend::presenter::note_gpu_drawn(now);   // the back buffer really took a frame (arming needs one)
    }
    const bool shown = j.result == gpu::DRAWN || j.result == gpu::READY;
    if (shown && (j.hud != 0 || j.menus != 0)) {
        __atomic_store_n(&g_present_ms, now != 0 ? now : uint64_t{1}, __ATOMIC_RELEASE);   // overlay_live's frame
    }
    if (shown && j.hud != 0) {
        // The vanilla pieces our elements replace stay hidden while ours are presented (hide.h).
        const uint32_t drawn = j.hud_out.drawn;
        if ((drawn & (1u << hud_scene::EL_PARTY)) != 0) {
            hide::note_presented(hide::ELEM_PARTY, now);
        }
        if ((drawn & (1u << hud_scene::EL_TARGET)) != 0) {
            hide::note_presented(hide::ELEM_TARGET, now);
        }
        if ((drawn & (1u << hud_scene::EL_LOG)) != 0) {
            hide::note_presented(hide::ELEM_LOG, now);
        }
        store(g_counters.hud_quads_party, j.hud_out.quads[hud_scene::EL_PARTY]);
        store(g_counters.hud_quads_target, j.hud_out.quads[hud_scene::EL_TARGET]);
        store(g_counters.hud_quads_log, j.hud_out.quads[hud_scene::EL_LOG]);
        if (j.hud_out.refused != 0) {
            bump(g_counters.hud_refused);
        }
        bump(g_counters.hud_frames);
    }
    if (shown && j.menus != 0 && j.menus_out.drawn != 0) {
        hide::note_presented(hide::ELEM_HELP, now);   // the windows' footer help is on screen
    }
    if (shown && j.menus != 0) {
        const uint64_t stamp = now != 0 ? now : 1;
        g_last_dropped = j.menus_out.dropped;
        const uint32_t expected = j.p1 != 0 ? 0u : 1u;   // P1 on the game's own menu: its window is never ours
        if ((j.menus_out.dropped & ~expected) != 0) {
            bump(g_counters.dropped_frames);
        }
        __atomic_add_fetch(&g_counters.windows, static_cast<uint64_t>(j.menus_out.drawn), __ATOMIC_RELAXED);
        frontend::presenter::note_dropped(j.menus_out.dropped, stamp);
        frontend::presenter::note_drawn(stamp);
        frontend::presenter::note_run(stamp);
        bump(g_counters.stamps);
    } else if (j.menus != 0) {
        frontend::presenter::note_failed(now);   // nothing of the menus on screen: P1 goes back to vanilla at once
    } else {
        frontend::presenter::break_run();        // the test card or the HUD elements alone present no menus
    }
    return patch::RUN_ORIGINAL;
}

// ---- co-op thread ------------------------------------------------------------------------------------------------------------
void step(uint64_t now_ms)
{
    theme::step(now_ms);   // the themes folder scan, the selected theme read and handed over, retired themes freed
    log_events();
    if (g_hook_logged == 0 && patch::current_mode() == patch::MODE_LIVE &&
        control::current_state() == control::STATE_ON) {
        g_hook_logged = 1;
        const status_view st = status();
        if (st.attached == 0) {
            log::post(log::ERR, "overlay: not attached (atlas, guard or handler): no overlay, the guests' menus stay idle");
        } else if (st.row_hooked == 0) {
            log::post(log::WARNING, "overlay: OV-01 is not hooked (another mod changed the present call, or a gate row "
                                    "differs): no overlay, the guests' menus stay idle");
        } else if (hud::status().row_hooked == 0) {
            log::post(log::WARNING, "overlay: the HUD heartbeat (HD-01) is not hooked: the overlay never draws");
        }
    }
    if (__atomic_load_n(&g_developer, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    if (g_report_ms == 0 || now_ms - g_report_ms >= REPORT_MS) {
        g_report_ms = now_ms != 0 ? now_ms : 1;
        report(now_ms);
    }
}

void stop()
{
    // The D3D objects belong to the render thread and are left to the process exit: releasing them from here could
    // race a frame in flight.
    __atomic_store_n(&g_test_card, 0u, __ATOMIC_RELEASE);
}

const counters* stats()
{
    return &g_counters;
}

uint64_t last_frame_ms()
{
    return __atomic_load_n(&g_frame_ms, __ATOMIC_ACQUIRE);
}

status_view status()
{
    status_view v = {};
    v.attached = __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    v.row_hooked = patch::trampoline(static_cast<uint32_t>(ROW_OV)) != nullptr ? 1 : 0;
    v.test_card = __atomic_load_n(&g_test_card, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    v.dead = __atomic_load_n(&g_dead, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    return v;
}

// ---- host tests ------------------------------------------------------------------------------------------------------------
void test_reset()
{
    static_assert(sizeof(counters) % sizeof(uint64_t) == 0, "counters are 64-bit fields");
    volatile uint64_t* field = &g_counters.reached;
    for (uint32_t k = 0; k < sizeof(counters) / sizeof(uint64_t); ++k) {
        field[k] = 0;
    }
    __atomic_store_n(&g_dead, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_frame_ms, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_present_ms, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_force_live, -1, __ATOMIC_RELEASE);
    g_streak = live_streak{};
    __atomic_store_n(&g_test_card, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_developer, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_inject, 0u, __ATOMIC_RELEASE);
    gpu::test_set_fault(0);
    g_last_call = 0;
    g_last_dropped = 0;
    g_logged = 0;
    g_report_ms = 0;
    g_hook_logged = 0;
    g_batch.clear();
}

void test_set_test_card(bool on)
{
    __atomic_store_n(&g_test_card, on ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_set_heartbeat(uint64_t ms, uint32_t alpha)
{
    store(g_counters.hud_alpha, alpha);
    // As if HD-01 had run (alpha > 0) or never run this launch (alpha 0): note_ui_tick needs one HD-01 heartbeat.
    store(g_counters.heartbeats, alpha != 0 ? (load(g_counters.heartbeats) != 0 ? load(g_counters.heartbeats) : 1) : 0);
    __atomic_store_n(&g_counters.hud_ms, ms, __ATOMIC_RELEASE);
}

void test_force_live(int32_t state)
{
    __atomic_store_n(&g_force_live, state, __ATOMIC_RELEASE);
}

void test_set_presented(uint64_t ms)
{
    __atomic_store_n(&g_present_ms, ms, __ATOMIC_RELEASE);
}

void test_inject_fault(uint32_t where)
{
    __atomic_store_n(&g_inject, where, __ATOMIC_RELEASE);
    if (where == 0) {
        gpu::test_set_fault(0);
    }
}

const void* test_last_batch()
{
    return &g_batch;
}

uint32_t test_last_dropped()
{
    return g_last_dropped;
}

}  // namespace ilc::overlay
