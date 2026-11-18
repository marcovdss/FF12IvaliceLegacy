// Ivalice Legacy co-op overlay: the vanilla HUD pieces hidden by their draw bit (see hide.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "overlay/hide.h"

#include "control/control.h"
#include "frontend/frontend.h"
#include "hud/battle_log.h"
#include "hud/party_rows.h"
#include "hud/target_info.h"
#include "hud/target_info_native.h"
#include "overlay/overlay.h"
#include "patch/patch_engine.h"
#include "runtime/guard.h"
#include "runtime/probe.h"
#include "runtime/tick.h"
#include "session/game_facts.h"
#include "session/session.h"

namespace ilc::overlay::hide {
namespace {

namespace hf = facts;

static_assert(hf::UI_MANAGER_PTR == hud::target_info::giveback::facts::UI_MANAGER_PTR &&
                  hf::UI_HUD == hud::target_info::giveback::facts::UI_HUD &&
                  hf::HUD_HANDLER == hud::target_info::giveback::facts::HUD_HANDLER &&
                  hf::HUD_TARGET == hud::target_info::giveback::facts::UI_OWNER +
                                        hud::target_info::giveback::facts::OWNER_WINDOW -
                                        hud::target_info::giveback::facts::UI_HUD &&
                  hf::TARGET_HANDLER == hud::target_info::giveback::facts::WINDOW_HANDLER,
              "the HUD window and the TARGET INFO window are the give-back module's");
static_assert(hf::COMMAND_ACTOR == session::facts::COMMAND_ACTOR && hf::COMMAND_FLAG == session::facts::COMMAND_FLAG,
              "the frontend flag and id are the session's");

// ---- the HH-V rows -----------------------------------------------------------------------------------------------------
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

constexpr uint32_t PIN_COUNT = 7;
constexpr int32_t PIN[PIN_COUNT] = {find_row("HH-V1"), find_row("HH-V2"), find_row("HH-V3"), find_row("HH-V4"),
                                    find_row("HH-V5"), find_row("HH-V6"), find_row("HH-V7")};
constexpr uint32_t PIN_RVA[PIN_COUNT] = {0x125E88, 0x16F800, 0x1764F6, 0x19F5C1, 0x19F604, 0x1280DB, 0x19DD9B};
constexpr uint32_t PIN_BYTES_MAX = 40;

constexpr bool pins_are_info_rows()
{
    for (uint32_t k = 0; k < PIN_COUNT; ++k) {
        if (PIN[k] < 0) {
            return false;
        }
        const patch::site& s = patch::SITES[PIN[k]];
        if (s.kind != ILC_SITE_KIND_VERIFY || s.policy != ILC_SITE_POLICY_INFO || s.rva != PIN_RVA[k] ||
            s.length > PIN_BYTES_MAX || s.subsystem != ILC_SUBSYS_RENDER) {
            return false;
        }
    }
    return true;
}
static_assert(pins_are_info_rows(), "HH-V1..HH-V7: verify-only INFO rows (a mismatch refuses nothing)");
static_assert(hf::PIN_ROWS <= PIN_COUNT, "the header's count is a floor");

// The pieces' expected handlers (CHAIN: none known, never touched).
constexpr uint64_t HANDLER_OF[PIECE_COUNT] = {hf::PARTY_HANDLER, hf::HELP_HANDLER,   hf::HELP_HANDLER, hf::TARGET_HANDLER,
                                              hf::LOG_B_HANDLER, hf::LOG_A_HANDLER, hf::CHAIN_HANDLER};
static_assert(hf::CHAIN_HANDLER == 0, "fill window_of() for PIECE_CHAIN together with its handler");

// ---- state -------------------------------------------------------------------------------------------------------------
struct held_window {
    uint64_t window;      // the window whose bit 0 we cleared (0 = none held)
    uint64_t handler;     // its handler then
};

uintptr_t g_base = 0x120000;   // NOTADDR: the pinned image base until attach
counters g_counters = {};
volatile uint64_t g_presented[ELEM_COUNT] = {};
volatile uint32_t g_pins = 0;          // 0 unknown, 1 ok, 2 off
volatile int32_t g_pin_row = -1;
volatile uint32_t g_held_mask = 0;
volatile uint32_t g_wanted_mask = 0;
volatile int32_t g_force_pins = -1;
volatile int32_t g_force_gate = -1;
volatile int32_t g_force_p1 = -1;
volatile int32_t g_force_live = -1;
volatile int32_t g_force_elements = -1;
uint64_t g_live_ms = 0;                // the last tick overlay::live held (game thread, under g_owner)
bool g_pins_once = false;
uint64_t g_pins_ms = 0;
held_window g_held[PIECE_COUNT] = {};  // owned by whoever holds g_owner (tick, stale, watch, the exit restore)
volatile uint32_t g_owner = 0;         // 1 while a thread works on the pieces (a tick never waits: it skips)
volatile uint32_t g_exiting = 0;       // the exit restore ran: ticks do nothing any more
volatile uint64_t g_tick_ms = 0;       // the last UI tick that ran (watch: the tick stopped)
volatile uint32_t g_force_signals = 0; // host tests: the game-progress signals below instead of the live ones
volatile uint64_t g_forced_core_ms = 0;
volatile uint64_t g_forced_frame_ms = 0;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uint64_t game(uint64_t va) { return g_base + (va - hf::PINNED_BASE); }

template <typename T>
bool load(uint64_t address, T* out)
{
    *out = 0;
    return address != 0 && load_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

bool store_flags(uint64_t window, uint32_t flags)
{
    return window != 0 && store_game(reinterpret_cast<void*>(window + hf::WINDOW_FLAGS), &flags, sizeof(flags));
}

bool rows_stock(int32_t* bad)
{
    for (uint32_t k = 0; k < PIN_COUNT; ++k) {
        const patch::site& s = patch::SITES[PIN[k]];
        uint8_t bytes[PIN_BYTES_MAX];
        bool same = read_game(bytes, reinterpret_cast<const void*>(g_base + s.rva), s.length);
        for (uint32_t i = 0; same && i < s.length; ++i) {
            same = bytes[i] == static_cast<uint8_t>(s.expected[i]);
        }
        if (!same) {
            *bad = PIN[k];
            return false;
        }
    }
    *bad = -1;
    return true;
}

bool pins_now()
{
    const int32_t forced = __atomic_load_n(&g_force_pins, __ATOMIC_ACQUIRE);
    if (forced >= 0) {
        return forced != 0;
    }
    return __atomic_load_n(&g_pins, __ATOMIC_ACQUIRE) == 1;
}

bool gate_now()
{
    const int32_t forced = __atomic_load_n(&g_force_gate, __ATOMIC_ACQUIRE);
    if (forced >= 0) {
        return forced != 0;
    }
    return control::current_state() == control::STATE_ON && patch::subsystem_live(ILC_SUBSYS_RENDER);
}

bool live_now(uint64_t now_ms)
{
    const int32_t forced = __atomic_load_n(&g_force_live, __ATOMIC_ACQUIRE);
    if (forced >= 0) {
        return forced != 0;
    }
    return overlay::live(now_ms);
}

// Our elements' own switches: bit 0 party list, bit 1 Target Info, bit 2 battle log.
uint32_t elements_now()
{
    const int32_t forced = __atomic_load_n(&g_force_elements, __ATOMIC_ACQUIRE);
    if (forced >= 0) {
        return static_cast<uint32_t>(forced);
    }
    return (hud::party_rows::enabled() ? 1u : 0u) | (hud::target_info::enabled() ? 2u : 0u) |
           (hud::battle_log::current().enabled != 0 ? 4u : 0u);
}

bool p1_ours_now()
{
    const int32_t forced = __atomic_load_n(&g_force_p1, __ATOMIC_ACQUIRE);
    if (forced >= 0) {
        return forced != 0;
    }
    return !frontend::p1_native();
}

// The window of a piece now. *readable false: a read failed (nothing is known); readable with 0: no such window.
uint64_t window_of(uint32_t piece, bool* readable)
{
    *readable = false;
    uint64_t ui = 0;
    if (!load(game(hf::UI_MANAGER_PTR), &ui)) {
        return 0;
    }
    *readable = true;
    if (ui == 0) {
        return 0;
    }
    if (piece == PIECE_PARTY) {
        uint64_t panel = 0;
        *readable = load(ui + hf::UI_PARTY_PANEL, &panel);
        return panel;
    }
    const uint64_t hud = ui + hf::UI_HUD;
    uint64_t hud_handler = 0;
    if (!load(hud, &hud_handler)) {
        *readable = false;
        return 0;
    }
    if (hud_handler != game(hf::HUD_HANDLER)) {
        return 0;
    }
    switch (piece) {
    case PIECE_HELP0: return hud + hf::HUD_HELP0;
    case PIECE_HELP1: return hud + hf::HUD_HELP1;
    case PIECE_TARGET: {
        uint64_t w = 0;
        *readable = load(hud + hf::HUD_TARGET, &w);
        return w;
    }
    case PIECE_LOGB: {
        uint64_t w = 0;
        *readable = load(hud + hf::HUD_LOG_B, &w);
        return w;
    }
    case PIECE_LOGA: {
        uint64_t w = 0;
        *readable = load(hud + hf::HUD_LOG_A, &w);
        return w;
    }
    default: return 0;   // PIECE_CHAIN: not located (CE-4)
    }
}

// Sets bit 0 off the game thread: an interlocked OR (game writes to the other bits survive) inside the fault guard
// (the window's memory may go while we look). The guard serves one call at a time: a few tries if it is busy.
struct or_job {
    uint32_t* flags;
};
void or_drawn(void* arg)
{
    __atomic_fetch_or(static_cast<or_job*>(arg)->flags, hf::FLAG_DRAWN, __ATOMIC_SEQ_CST);
}
bool interlocked_drawn(uint64_t window)
{
    uintptr_t begin = 0;
    uintptr_t end = 0;
    const uint64_t at = window + hf::WINDOW_FLAGS;
    if (window == 0 || !game_region(reinterpret_cast<const void*>(at), &begin, &end) || at + 4 > end || (at & 3u) != 0) {
        return false;
    }
    or_job job{reinterpret_cast<uint32_t*>(at)};
    for (uint32_t k = 0; k < 20; ++k) {
        const int32_t r = guard::call(or_drawn, &job);
        if (r == guard::RETURNED) {
            return true;
        }
        if (r != guard::BUSY) {
            return false;
        }
        Sleep(1);
    }
    return false;
}

// Gives back one held piece: bit 0 set again only on the window the piece has now, still the same window (handler)
// and the game's to show. A failed read keeps the piece held, nothing written (next tick tries again; review 7).
// `interlocked`: off the game thread (review 8).
void give_back(uint32_t piece, bool interlocked = false)
{
    held_window& h = g_held[piece];
    if (h.window == 0) {
        return;
    }
    bool readable = false;
    const uint64_t now_window = window_of(piece, &readable);
    uint64_t handler = 0;
    uint32_t flags = 0;
    if (!readable || (now_window == h.window && (!load(h.window, &handler) || !load(h.window + hf::WINDOW_FLAGS, &flags)))) {
        bump(g_counters.kept_unreadable);
        return;
    }
    // The piece's window now is the held one (review 6), the same window and still the game's to show.
    const bool ok = now_window == h.window && may_give_back(handler, h.handler, flags) &&
                    (interlocked ? interlocked_drawn(h.window) : store_flags(h.window, flags | hf::FLAG_DRAWN));
    if (ok) {
        bump(g_counters.give_backs[piece]);
    } else if (interlocked && now_window == h.window && may_give_back(handler, h.handler, flags)) {
        bump(g_counters.kept_unreadable);   // the guarded write did not happen: keep it, try again
        return;
    } else {
        bump(g_counters.dropped[piece]);   // closed, destroyed or replaced while held: left as the game has it
    }
    h = held_window{};
    __atomic_and_fetch(&g_held_mask, ~(1u << piece), __ATOMIC_RELEASE);
}

void give_back_all(bool interlocked = false)
{
    for (uint32_t p = 0; p < PIECE_COUNT; ++p) {
        give_back(p, interlocked);
    }
}

bool own_try()
{
    uint32_t expected = 0;
    return __atomic_compare_exchange_n(&g_owner, &expected, 1u, false, __ATOMIC_ACQUIRE, __ATOMIC_RELAXED);
}
bool own_wait(uint32_t tries)
{
    for (uint32_t k = 0; k < tries; ++k) {
        if (own_try()) {
            return true;
        }
        Sleep(1);
    }
    return false;
}
void own_release() { __atomic_store_n(&g_owner, 0u, __ATOMIC_RELEASE); }

// Hides one piece (or keeps it hidden): the current window, if it is the expected one and drawn. A held window that
// is no longer the piece's window (destroyed, address reused) is forgotten without a write; a failed read changes
// nothing (the piece stays held).
void hold(uint32_t piece)
{
    held_window& h = g_held[piece];
    bool readable = false;
    const uint64_t w = window_of(piece, &readable);
    if (!readable) {
        if (h.window != 0) {
            bump(g_counters.kept_unreadable);
        }
        return;
    }
    if (h.window != 0 && h.window != w) {
        bump(g_counters.dropped[piece]);
        h = held_window{};
        __atomic_and_fetch(&g_held_mask, ~(1u << piece), __ATOMIC_RELEASE);
    }
    if (w == 0) {
        return;
    }
    uint64_t handler = 0;
    uint32_t flags = 0;
    if (!load(w, &handler) || !load(w + hf::WINDOW_FLAGS, &flags)) {
        if (h.window != 0) {
            bump(g_counters.kept_unreadable);
        }
        return;
    }
    const uint64_t expected = game(HANDLER_OF[piece]);
    if (h.window == w && handler != h.handler) {
        // The same address now holds another window: ours is gone, never touch the new one's bit for it.
        bump(g_counters.dropped[piece]);
        h = held_window{};
        __atomic_and_fetch(&g_held_mask, ~(1u << piece), __ATOMIC_RELEASE);
    }
    if (!may_hide(handler, expected, flags)) {
        return;   // not the expected window, being destroyed, or already hidden (by us, or the game's own hide)
    }
    if (store_flags(w, flags & ~hf::FLAG_DRAWN)) {
        if (h.window == 0) {
            bump(g_counters.hides[piece]);
        }
        h = held_window{w, handler};
        __atomic_or_fetch(&g_held_mask, 1u << piece, __ATOMIC_RELEASE);
    }
}

}  // namespace

// ---- the rules ----------------------------------------------------------------------------------------------------------
bool may_hide(uint64_t handler, uint64_t expected, uint32_t flags)
{
    return handler != 0 && handler == expected && (flags & hf::FLAG_DYING) == 0 && (flags & hf::FLAG_DRAWN) != 0;
}

bool may_give_back(uint64_t handler, uint64_t held_handler, uint32_t flags)
{
    return handler != 0 && handler == held_handler && (flags & (hf::FLAG_DYING | hf::FLAG_SHOWN)) == hf::FLAG_SHOWN;
}

bool field_ui(uint32_t command_flag, uint32_t command_actor)
{
    if (command_flag != 1) {
        return true;
    }
    const uint32_t id = command_actor & session::facts::FRONTEND_ID_MASK;
    return command_actor != 0 && (id == session::facts::FRONTEND_BATTLE || id == session::facts::FRONTEND_LEADER ||
                                  id == session::facts::FRONTEND_TREE_ACTIVE);
}

uint32_t wanted(const want_in& in)
{
    const uint64_t t = in.live_ms;
    const bool live = t != 0 && (in.now >= t ? in.now - t <= LIVE_HOLD_MS : t - in.now <= LIVE_HOLD_MS);
    if (in.coop_on == 0 || in.render_live == 0 || in.pins_ok == 0 || !live) {
        return 0;   // the fail-safe: not live for longer than LIVE_HOLD_MS gives everything back
    }
    uint32_t mask = 0;
    if (in.party_on != 0 && in.leader_vanilla == 0 && in.summon == 0) {
        mask |= 1u << PIECE_PARTY;   // not under P1's vanilla leader select (review 10) or an Esper (review 12)
    }
    if (in.field != 0 && in.p1_ours != 0) {
        mask |= (1u << PIECE_HELP0) | (1u << PIECE_HELP1);   // the main menu's help is the same window
    }
    if (in.target_on != 0) {
        mask |= 1u << PIECE_TARGET;
    }
    if (in.log_on != 0) {
        mask |= (1u << PIECE_LOGA) | (1u << PIECE_LOGB) | (1u << PIECE_CHAIN);
    }
    return mask;
}

// ---- module ---------------------------------------------------------------------------------------------------------------
void attach(uintptr_t image_base)
{
    g_base = image_base;
}

void sample(uint64_t now_ms)
{
    if (g_pins_once && now_ms - g_pins_ms < PIN_RECHECK_MS) {
        return;
    }
    g_pins_once = true;
    g_pins_ms = now_ms;
    int32_t bad = -1;
    const bool ok = rows_stock(&bad);
    bump(g_counters.pin_checks);
    if (!ok) {
        bump(g_counters.pin_off);
    }
    __atomic_store_n(&g_pin_row, bad, __ATOMIC_RELAXED);
    __atomic_store_n(&g_pins, ok ? 1u : 2u, __ATOMIC_RELEASE);
}

bool pins_ok()
{
    return pins_now();
}

void note_presented(uint32_t element, uint64_t now_ms)
{
    if (element < ELEM_COUNT) {
        __atomic_store_n(&g_presented[element], now_ms != 0 ? now_ms : uint64_t{1}, __ATOMIC_RELEASE);
    }
}

uint64_t presented(uint32_t element)
{
    return element < ELEM_COUNT ? __atomic_load_n(&g_presented[element], __ATOMIC_ACQUIRE) : 0;
}

void tick(bool live)
{
    if (__atomic_load_n(&g_exiting, __ATOMIC_ACQUIRE) != 0 || !own_try()) {
        return;   // the exit restore ran, or watch() / the restore holds the pieces this moment: next tick
    }
    want_in in = {};
    in.now = patch::now_ms();
    __atomic_store_n(&g_tick_ms, in.now != 0 ? in.now : uint64_t{1}, __ATOMIC_RELEASE);
    const bool gate = live && gate_now();
    if (gate && live_now(in.now)) {
        g_live_ms = in.now != 0 ? in.now : 1;
    }
    in.live_ms = g_live_ms;
    const bool held_live = g_live_ms != 0 && in.now >= g_live_ms && in.now - g_live_ms <= LIVE_HOLD_MS;
    // Not live past the hysteresis, or the gate shut: give everything back, no other game read (the DRY tick, co-op off).
    if (!gate || !held_live) {
        __atomic_store_n(&g_wanted_mask, 0u, __ATOMIC_RELEASE);
        if (__atomic_load_n(&g_held_mask, __ATOMIC_ACQUIRE) != 0) {
            give_back_all();
        }
        own_release();
        return;
    }
    in.coop_on = gate ? 1 : 0;
    in.render_live = gate ? 1 : 0;
    // The Target Info piece leans on the TI-V rows too (the window pointer and its handler are theirs).
    in.pins_ok = pins_now() ? 1 : 0;
    uint32_t flag = 0;
    uint32_t actor = 0;
    uint64_t pause_a = 1;
    uint64_t pause_b = 1;
    in.field = load(game(hf::COMMAND_FLAG), &flag) && load(game(hf::COMMAND_ACTOR), &actor) && field_ui(flag, actor) &&
                       load(game(hf::PAUSE_PRIMARY), &pause_a) && pause_a == 0 &&
                       load(game(hf::PAUSE_SECONDARY), &pause_b) && pause_b == 0
                   ? 1
                   : 0;
    const uint32_t elements = elements_now();
    in.party_on = (elements & 1u) != 0 ? 1 : 0;
    in.target_on = (elements & 2u) != 0 ? 1 : 0;
    in.log_on = (elements & 4u) != 0 ? 1 : 0;
    in.p1_ours = p1_ours_now() ? 1 : 0;
    in.leader_vanilla = flag == 1 && (actor & session::facts::FRONTEND_ID_MASK) == session::facts::FRONTEND_LEADER &&
                                in.p1_ours == 0
                            ? 1
                            : 0;
    session::session_record rec;
    in.summon = session::read(&rec) && (rec.facts & session::FACT_SUMMON) != 0 ? 1 : 0;
    uint32_t want = gate ? wanted(in) : 0u;
    if (!hud::target_info::giveback::pins_ok() && __atomic_load_n(&g_force_pins, __ATOMIC_ACQUIRE) < 0) {
        want &= ~(1u << PIECE_TARGET);
    }
    __atomic_store_n(&g_wanted_mask, want, __ATOMIC_RELEASE);
    for (uint32_t p = 0; p < PIECE_COUNT; ++p) {
        if ((want & (1u << p)) != 0) {
            hold(p);
        } else {
            give_back(p);
        }
    }
    own_release();
}

void stale()
{
    __atomic_store_n(&g_wanted_mask, 0u, __ATOMIC_RELEASE);
    if (!own_try()) {
        return;   // another thread gives the pieces back this moment
    }
    give_back_all();
    own_release();
}

void restore_at_exit()
{
    __atomic_store_n(&g_exiting, 1u, __ATOMIC_RELEASE);   // no tick starts from here on
    // A tick in flight finishes first (it owns the pieces and may hide one more): own them, then look (review 7: the
    // mask read before the wait could miss a piece that tick was hiding).
    if (!own_wait(200)) {
        return;
    }
    if (__atomic_load_n(&g_held_mask, __ATOMIC_ACQUIRE) == 0) {
        own_release();
        return;
    }
    give_back_all(true);
    own_release();
    bump(g_counters.exit_restores);
}

bool stale_for(uint64_t stamp, uint64_t now_ms, uint64_t limit)
{
    return stamp == 0 || (now_ms >= stamp && now_ms - stamp >= limit);
}

// Is the game thread really stopped? Our UI tick not running is not enough on its own (a closed gate stops it while the
// game runs on, and a write from here could then race the game thread between our look and our store; review 8; a
// latched CORE fault is handled before this in watch()). It counts only when the other game-progress signals are stale
// too: the UT-01 handler itself (the CORE row: tick::stats()->last_tick_ms) and the frame hook (OV-01 on the render
// thread, which the game thread feeds: overlay::last_frame_ms(), stamped once the dispatcher's gate let the handler
// run). A signal that never fired (0) is no sign of life.
bool game_thread_stopped(uint64_t now_ms)
{
    uint64_t core = 0;
    uint64_t frame = 0;
    if (__atomic_load_n(&g_force_signals, __ATOMIC_ACQUIRE) != 0) {
        core = __atomic_load_n(&g_forced_core_ms, __ATOMIC_ACQUIRE);
        frame = __atomic_load_n(&g_forced_frame_ms, __ATOMIC_ACQUIRE);
    } else {
        core = __atomic_load_n(&tick::stats()->last_tick_ms, __ATOMIC_ACQUIRE);
        frame = overlay::last_frame_ms();
    }
    return stale_for(core, now_ms, TICK_LOST_MS) && stale_for(frame, now_ms, TICK_LOST_MS);
}

bool tick_lost(uint64_t now_ms)
{
    const uint64_t last = __atomic_load_n(&g_tick_ms, __ATOMIC_ACQUIRE);
    return last != 0 && now_ms >= last && now_ms - last >= TICK_LOST_MS;
}

void watch(uint64_t now_ms)
{
    // The common case (our tick runs) takes no ownership, so a tick never has to skip a frame for the watch.
    if (__atomic_load_n(&g_held_mask, __ATOMIC_ACQUIRE) == 0 || !tick_lost(now_ms) || !own_try()) {
        return;
    }
    // Owned: no tick can run on the pieces (or stamp g_tick_ms) until we release, so the look below holds for the act.
    if (!tick_lost(now_ms)) {
        own_release();
        return;
    }
    if ((patch::faulted_mask() & patch::FAULT_CORE) != 0) {
        // A latched CORE fault: UT-01 never runs again, neither its handler nor its stale handler (the dispatcher's
        // fault check comes before the stale test), so no tick and no stale() will ever give back; the RENDER rows stay
        // live, so the frame signal says "running" for good. Give back from here, once: the interlocked bit-0 OR inside
        // the fault guard, only on a window that is still the piece's, with the held handler and (flags & 0x402) == 2.
        // Nothing re-hides afterwards (only the UI tick hides). A piece kept by a failed read is retried next beat.
        give_back_all(true);
        own_release();
        bump(g_counters.core_fault_restores);
        return;
    }
    if (!game_thread_stopped(now_ms)) {
        // The game runs on without our tick (a closed gate): UT-01's stale handler or the next tick gives back.
        own_release();
        bump(g_counters.watch_deferred);
        return;
    }
    give_back_all(true);
    own_release();
    bump(g_counters.orphan_restores);
}

const counters* stats()
{
    return &g_counters;
}

status_view status()
{
    status_view v = {};
    v.held = __atomic_load_n(&g_held_mask, __ATOMIC_ACQUIRE);
    v.wanted = __atomic_load_n(&g_wanted_mask, __ATOMIC_ACQUIRE);
    v.pins = static_cast<uint8_t>(__atomic_load_n(&g_pins, __ATOMIC_ACQUIRE));
    v.pin_row = __atomic_load_n(&g_pin_row, __ATOMIC_RELAXED);
    return v;
}

// ---- host tests -------------------------------------------------------------------------------------------------------------
void test_reset()
{
    volatile uint64_t* field = &g_counters.hides[0];
    for (uint32_t k = 0; k < sizeof(counters) / sizeof(uint64_t); ++k) {
        field[k] = 0;
    }
    for (uint32_t e = 0; e < ELEM_COUNT; ++e) {
        g_presented[e] = 0;
    }
    for (uint32_t p = 0; p < PIECE_COUNT; ++p) {
        g_held[p] = held_window{};
    }
    g_pins = 0;
    g_pin_row = -1;
    g_held_mask = 0;
    g_wanted_mask = 0;
    g_force_pins = -1;
    g_force_gate = -1;
    g_force_p1 = -1;
    g_force_live = -1;
    g_force_elements = -1;
    g_live_ms = 0;
    g_pins_once = false;
    g_pins_ms = 0;
    g_owner = 0;
    g_exiting = 0;
    g_tick_ms = 0;
    g_force_signals = 0;
    g_forced_core_ms = 0;
    g_forced_frame_ms = 0;
}

void test_set_tick_ms(uint64_t ms)
{
    __atomic_store_n(&g_tick_ms, ms, __ATOMIC_RELEASE);
}

void test_set_game_signals(int32_t forced, uint64_t core_ms, uint64_t frame_ms)
{
    __atomic_store_n(&g_forced_core_ms, core_ms, __ATOMIC_RELEASE);
    __atomic_store_n(&g_forced_frame_ms, frame_ms, __ATOMIC_RELEASE);
    __atomic_store_n(&g_force_signals, forced != 0 ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_set_image_base(uintptr_t image_base)
{
    g_base = image_base;
}

void test_force_pins(int32_t state)
{
    __atomic_store_n(&g_force_pins, state, __ATOMIC_RELEASE);
}

void test_force_gate(int32_t coop_and_render)
{
    __atomic_store_n(&g_force_gate, coop_and_render, __ATOMIC_RELEASE);
}

void test_force_p1_ours(int32_t ours)
{
    __atomic_store_n(&g_force_p1, ours, __ATOMIC_RELEASE);
}

void test_force_live(int32_t live)
{
    __atomic_store_n(&g_force_live, live, __ATOMIC_RELEASE);
}

void test_force_elements(int32_t mask)
{
    __atomic_store_n(&g_force_elements, mask, __ATOMIC_RELEASE);
}

uint64_t test_presented(uint32_t element)
{
    return element < ELEM_COUNT ? __atomic_load_n(&g_presented[element], __ATOMIC_ACQUIRE) : 0;
}

}  // namespace ilc::overlay::hide
