// Ivalice Legacy co-op module: the HUD frame driver and the presenter stamp (see driver.h).
#include "hud/driver.h"

#include "control/control.h"
#include "frontend/presenter.h"
#include "hud/battle_log.h"
#include "hud/hud_internal.h"
#include "hud/menus_draw.h"
#include "hud/party_rows.h"
#include "hud/snapshot_facts.h"
#include "hud/target_info.h"
#include "ilc/bridge_abi.h"
#include "patch/patch_engine.h"
#include "runtime/guard.h"
#include "runtime/probe.h"

namespace ilc::hud::driver {
namespace {

namespace sf = snapfacts;
using detail::copy_bytes;
using detail::zero_bytes;

uintptr_t g_base = 0x120000;  // NOTADDR: the pinned image base until attach
counters g_counters = {};
volatile uint32_t g_probe = 0;
volatile uint32_t g_inject = PASS_COUNT;

// ---- game-thread state (one game thread draws the UI; the hook is not re-entered) ---------------------------------
hud_snapshot g_snap;       // the copy the passes read
canvas g_cv;               // the passes draw here
frame g_out;               // the composed frame when the Diagnostics test frame is appended
uint32_t g_menus_done = 0; // the menu pass completed on the frame being presented
uint32_t g_menus_dropped = 0; // ... and left these seats' open windows out (bit per seat)

const char* const NAMES[PASS_COUNT] = {"party", "target info", "log", "menus"};

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uint64_t game(uint64_t va) { return g_base + (va - sf::PINNED_BASE); }

bool stale(uint64_t stamp, uint64_t now)
{
    return now > stamp + SNAPSHOT_STALE_MS || stamp > now + SNAPSHOT_STALE_MS;
}

// ---- the element passes (stubs: later tasks draw the elements here) ------------------------------------------------
void mark(canvas& cv, uint32_t id)
{
    if ((__atomic_load_n(&g_probe, __ATOMIC_RELAXED) >> id & 1u) != 0) {
        cv.text(60, static_cast<int16_t>(500 + 40 * id), NAMES[id], WHITE);
    }
}

void pass_party_extras(canvas& cv, const pass_input& in)
{
    mark(cv, PASS_PARTY);
    party_rows::draw(cv, in);   // seat edge and numeral, damage ghost, statuses, action line, party-target cursors
}

void pass_target_info(canvas& cv, const pass_input& in)
{
    mark(cv, PASS_TARGET_INFO);
    target_info::draw(cv, in);   // the expanded Target Info, top-left
}

void pass_battle_log(canvas& cv, const pass_input& in)
{
    mark(cv, PASS_LOG);
    battle_log::draw(cv, in);   // the battle log, top-right
}

// The menu pass reports whether it drew from a usable view (published, this epoch, fresh): without one nothing of the
// menus is known to be on screen, so the frame does not count as presenting the menu layer (no stamp).
bool g_menus_view = false;

void pass_menus(canvas& cv, const pass_input& in)
{
    mark(cv, PASS_MENUS);
    g_menus_view = menus_draw::pass(cv, in);   // the co-op battle menus, selectors and target pages
}

struct pass_entry {
    uint8_t id;
    pass_fn fn;
};
constexpr pass_entry PASSES[PASS_COUNT] = {{PASS_PARTY, pass_party_extras},
                                           {PASS_TARGET_INFO, pass_target_info},
                                           {PASS_LOG, pass_battle_log},
                                           {PASS_MENUS, pass_menus}};
constexpr bool passes_in_order()
{
    for (uint32_t k = 0; k < PASS_COUNT; ++k) {
        if (PASSES[k].id != k) {
            return false;
        }
    }
    return PASSES[PASS_COUNT - 1].id == PASS_MENUS;
}
static_assert(passes_in_order(), "party extras, Target Info, log, then the menus last (drawn over the rest)");

// The floors kept back fit one frame together: four floor windows and the log's floor.
static_assert(menus_draw::FLOOR_COMMANDS_MAX + battle_log::LOG_FLOOR <= MAX_COMMANDS,
              "the menu floor and the log floor fit one frame's commands");
static_assert(menus_draw::FLOOR_TEXT_MAX + battle_log::LOG_TEXT_FLOOR <= TEXT_POOL,
              "the menu floor and the log floor fit one frame's text");

struct compose_job {
    const pass_input* in;
    uint32_t menus;      // FIREWALL_MENUS live: the menu pass runs
    uint32_t done;       // bit per pass completed (the menu pass: only when it drew from a usable view)
    uint32_t finished;   // stored after the last pass
    uint32_t kept;       // commands kept back for the menu pass
    uint32_t kept_log;   // commands kept back for the log pass while party extras and Target Info draw
    uint16_t added[PASS_COUNT];   // commands each pass added
    uint8_t trace[PASS_COUNT];
    uint8_t traced;
    uint8_t reserved[3];
};

void run_passes(void* arg)
{
    compose_job& job = *static_cast<compose_job*>(arg);
    const uint32_t inject = __atomic_load_n(&g_inject, __ATOMIC_RELAXED);
    // Floors kept back, each lifted when its own pass comes (every pass yields: the canvas refuses what would eat into
    // what is kept): the open menu windows' floor until the menu pass, so it never has to leave a window out for
    // room; the battle log's floor until the log pass, so a frame that hides the native log always shows ours.
    menus_draw::footprint menus = {0, 0};
    if (job.menus != 0) {
        menus = menus_draw::prepare(*job.in);
    }
    menus_draw::footprint log = {0, 0};
    if (battle_log::log_floor_due(job.in->snap->head.epoch, job.in->now_ms)) {
        log = {battle_log::LOG_FLOOR, battle_log::LOG_TEXT_FLOOR};
    }
    g_cv.reserve(menus.commands + log.commands, menus.text + log.text);
    job.kept = menus.commands;
    job.kept_log = log.commands;
    g_menus_view = false;
    for (const pass_entry& p : PASSES) {
        if (p.id == PASS_MENUS && job.menus == 0) {
            continue;  // the co-op menus cannot act: nothing of theirs is drawn, and nothing is stamped
        }
        if (p.id == PASS_LOG) {
            g_cv.reserve(menus.commands, menus.text);  // the log draws into its floor and whatever the others left
        } else if (p.id == PASS_MENUS) {
            g_cv.reserve(0, 0);  // the menus draw into everything that is left
        }
        if (inject == p.id) {
            asm volatile("movq 8, %%rax" : : : "rax", "memory");  // a load from address 8: the fault drill
        }
        const uint32_t before = g_cv.view().count;
        p.fn(g_cv, *job.in);
        job.added[p.id] = static_cast<uint16_t>(g_cv.view().count - before);
        job.trace[job.traced++] = p.id;
        if (p.id != PASS_MENUS || g_menus_view) {
            job.done |= 1u << p.id;   // a menu pass without a usable view presented nothing of the menus
        }
    }
    job.finished = 1;
}

// Appends `src`'s commands to `dst` (strings copied into dst's pool) while dst holds at most `cmd_limit` commands and
// `text_limit` text bytes; a command that does not fit is dropped alone.
uint32_t append(frame& dst, const frame& src, uint32_t cmd_limit, uint32_t text_limit)
{
    uint32_t added = 0;
    for (uint32_t k = 0; k < src.count && k < MAX_COMMANDS && dst.count < cmd_limit; ++k) {
        cmd c;
        copy_bytes(&c, &src.cmds[k], sizeof(cmd));
        if (c.kind == CMD_TEXT) {
            uint32_t len = 0;
            while (c.text + len < src.text_used && src.text[c.text + len] != '\0') {
                ++len;
            }
            if (c.text + len >= src.text_used || dst.text_used + len + 1 > text_limit) {
                continue;
            }
            copy_bytes(dst.text + dst.text_used, src.text + c.text, len + 1);
            c.text = static_cast<uint16_t>(dst.text_used);
            dst.text_used += len + 1;
        }
        copy_bytes(&dst.cmds[dst.count++], &c, sizeof(cmd));
        ++added;
    }
    return added;
}

}  // namespace

const char* pass_name(uint32_t pass)
{
    return pass < PASS_COUNT ? NAMES[pass] : nullptr;
}

// ---- the frame hook ---------------------------------------------------------------------------------------------------
void attach(uintptr_t image_base)
{
    g_base = image_base;
}

bool frame_gate(uint32_t epoch, uint64_t now_ms)
{
    uint8_t reason = GATE_OK;
    uint32_t snap_epoch = 0;
    uint64_t sampled = 0;
    uint32_t flags = 0;
    if (!snapshot::meta(&snap_epoch, &sampled) || snap_epoch != epoch || stale(sampled, now_ms)) {
        reason = GATE_NO_SNAPSHOT;
    } else if (control::current_state() != control::STATE_ON) {
        reason = GATE_COOP_OFF;
    } else if (!load_from_caller(&flags, reinterpret_cast<const void*>(game(sf::RENDER_FLAGS)), sizeof(flags)) ||
               (flags & sf::RENDER_UI) == 0) {
        reason = GATE_UI_HIDDEN;
    }
    if (reason != GATE_OK) {
        bump(g_counters.declined[reason]);
        return false;
    }
    bump(g_counters.frames);
    return true;
}

int32_t compose(uint32_t epoch, uint64_t now_ms, const frame* test_frame, const frame** out)
{
    g_menus_done = 0;
    g_menus_dropped = 0;
    bool got = false;
    for (uint32_t attempt = 0; attempt < 2 && !got; ++attempt) {
        got = snapshot::read(g_snap);  // a torn copy is tried once more, never waited for
    }
    if (!got || g_snap.head.epoch != epoch || stale(g_snap.sampled_ms, now_ms) ||
        (g_snap.head.flags & SNAP_WORLD) == 0 || g_snap.layout >= LAYOUT_COUNT) {
        bump(g_counters.torn);
        return COMPOSE_NO_SNAPSHOT;
    }
    const pass_input in = {&g_snap, now_ms, g_snap.layout, {}};
    g_cv.begin(epoch, now_ms);
    compose_job job = {};
    job.in = &in;
    job.menus = patch::subsystem_live(ILC_SUBSYS_FIREWALL_MENUS) ? 1u : 0u;
    const int32_t r = guard::call(run_passes, &job);
    if (r == guard::FAULTED) {
        bump(g_counters.pass_faults);
        faulted();
        return COMPOSE_FAULTED;
    }
    if (r != guard::RETURNED || job.finished == 0) {
        return COMPOSE_BUSY;
    }
    for (uint32_t k = 0; k < PASS_COUNT; ++k) {
        g_counters.trace[k] = k < job.traced ? job.trace[k] : static_cast<uint8_t>(PASS_COUNT);
    }
    g_counters.traced = job.traced;
    g_counters.layout = g_snap.layout;
    for (uint32_t k = 0; k < PASS_COUNT; ++k) {
        g_counters.pass_commands[k] = job.added[k];
    }
    g_counters.kept_log = job.kept_log;
    if (job.menus != 0 && ((job.done >> PASS_MENUS) & 1u) == 0) {
        bump(g_counters.menus_no_view);
    }
    __atomic_store_n(&g_counters.last_passes, static_cast<uint64_t>(job.done), __ATOMIC_RELAXED);
    g_menus_done = (job.done >> PASS_MENUS) & 1u;
    g_menus_dropped = g_menus_done != 0 ? menus_draw::last_dropped() : 0u;
    g_counters.kept_commands = job.kept;
    if (test_frame != nullptr && test_frame->count != 0) {
        // The Diagnostics test pattern first (underneath), then the layer, the menus still last. The test pattern
        // gets only the room the layer leaves, so it never pushes a menu command out of the frame.
        const frame& layer = g_cv.view();
        g_out.head = layer.head;
        g_out.built_ms = now_ms;
        g_out.count = 0;
        g_out.text_used = 0;
        const uint32_t added = append(g_out, *test_frame, MAX_COMMANDS - layer.count, TEXT_POOL - layer.text_used);
        __atomic_add_fetch(&g_counters.appended, static_cast<uint64_t>(added), __ATOMIC_RELAXED);
        append(g_out, layer, MAX_COMMANDS, TEXT_POOL);
        *out = &g_out;
    } else {
        *out = &g_cv.view();
    }
    bump(g_counters.composed);
    return COMPOSE_OK;
}

void presented(uint64_t now_ms)
{
    if (g_menus_done == 0 || !patch::subsystem_live(ILC_SUBSYS_FIREWALL_MENUS)) {
        bump(g_counters.not_stamped);
        return;
    }
    g_menus_done = 0;
    // Per seat first: a seat whose open window this frame left out is not presented (presenter.h), whatever the rest.
    if (g_menus_dropped != 0) {
        bump(g_counters.dropped_frames);
    }
    frontend::presenter::note_dropped(g_menus_dropped, now_ms != 0 ? now_ms : 1);
    g_menus_dropped = 0;
    frontend::presenter::note_drawn(now_ms != 0 ? now_ms : 1);
    __atomic_store_n(&g_counters.last_stamp_ms, now_ms, __ATOMIC_RELAXED);
    bump(g_counters.stamped);
}

void faulted()
{
    g_menus_done = 0;
    g_menus_dropped = 0;
    frontend::presenter::note_drawn(0);  // 0 = never drawn: the presenter is not live from the next co-op beat
}

const counters* stats()
{
    return &g_counters;
}

// ---- host tests ---------------------------------------------------------------------------------------------------
void test_reset()
{
    zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    __atomic_store_n(&g_probe, 0u, __ATOMIC_RELAXED);
    __atomic_store_n(&g_inject, static_cast<uint32_t>(PASS_COUNT), __ATOMIC_RELAXED);
    g_menus_done = 0;
    g_menus_dropped = 0;
    g_menus_view = false;
}

void test_set_probe(uint32_t mask)
{
    __atomic_store_n(&g_probe, mask, __ATOMIC_RELAXED);
}

void test_inject_fault(uint32_t pass)
{
    __atomic_store_n(&g_inject, pass, __ATOMIC_RELAXED);
}

}  // namespace ilc::hud::driver
