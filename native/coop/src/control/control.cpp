// Ivalice Legacy co-op module: the co-op switches (see control.h).
#include "control/control.h"

#include <string.h>

#include "bridge/bridge_internal.h"
#include "input/edge_history.h"
#include "input/input_service.h"
#include "isolation/isolation.h"
#include "patch/patch_engine.h"
#include "session/session.h"

namespace ilc::control {
namespace {

constexpr uint32_t ISOLATION = static_cast<uint32_t>(ILC_SUBSYS_INPUT_ISOLATION);
constexpr uint32_t GUESTS = ILC_ACTIVE_SEATS_MAX;  // seats 2..GUESTS can toggle (P1 never)
constexpr uint32_t TOGGLE_BIT = 1u << ILC_ACTION_TOGGLE_CONTROL;
static_assert(ILC_ACTIVE_SEATS_MAX <= ILC_SEATS, "seat cap");

// ---- shared with the UI tick and the bridge (atomics) ----------------------------------------------------------
volatile uint32_t g_state = STATE_ON;
volatile uint32_t g_kill_request = 0;     // 0 none, 1 off, 2 on (bridge command)
volatile uint32_t g_settings_mask = 0;    // the last mask a settings record asked for
volatile uint32_t g_settings_known = 0;
volatile uint32_t g_drop_pending = 0;     // a settings record turned INPUT_ISOLATION off: release first
volatile uint32_t g_reapply = 0;
volatile uint32_t g_reapply_mask = 0;
volatile uint32_t g_parked = 0;
counters g_counters;

// ---- co-op thread only ---------------------------------------------------------------------------------------------
struct keys {
    uint8_t kill[ILC_KILL_SWITCH_BINDINGS];             // VK (keyboard or mouse button), 0 = none
    uint8_t toggle[ILC_SEATS][ILC_BINDINGS_PER_ACTION]; // guest toggle keys, 0 = none or read as actions
};
keys g_keys = {};
bool g_configured = false;
input::keyboard_api* g_test_keyboard = nullptr;
bool g_prev_focused = false;
uint8_t g_prev_down[256];
edges::edge_cursor g_cursor = {};
bool g_releasing_for_drop = false;
uint64_t g_release_t0 = 0;
uint32_t g_override_mask = 0;   // seats whose park bit a toggle set and the input service has not yet reported
uint32_t g_override_value = 0;
uint64_t g_override_until[ILC_SEATS] = {};
bool g_override_sticky[ILC_SEATS] = {};  // the input service is not running: the toggle's bit holds
input::snapshot g_snapshot;     // too large for the stack
uint32_t g_log[16];
uint32_t g_log_count = 0;

void bump(volatile uint64_t& c, uint64_t by = 1)
{
    __atomic_add_fetch(&c, by, __ATOMIC_RELAXED);
}

bool apply_mask(uint32_t mask)
{
    if (!patch::try_apply_subsystems(mask)) {
        bump(g_counters.mask_busy);
        return false;
    }
    if (g_log_count < sizeof(g_log) / sizeof(g_log[0])) {
        g_log[g_log_count++] = mask;
    }
    return true;
}

uint32_t settings_mask()
{
    if (__atomic_load_n(&g_settings_known, __ATOMIC_ACQUIRE) != 0) {
        return __atomic_load_n(&g_settings_mask, __ATOMIC_ACQUIRE);
    }
    static ilc_settings applied;  // co-op thread only
    bridge_copy_applied(&applied);
    return applied.subsystems_armed;
}

// A press edge needs this sample and the previous one in the foreground (no edge out of an alt-tab).
uint32_t read_keys(bool* kill)
{
    *kill = false;
    uint32_t toggles = 0;
    input::keyboard_api* kb = g_test_keyboard != nullptr ? g_test_keyboard : input::live_keyboard();
    const bool focused = kb->focused();
    uint8_t down[256];
    memset(down, 0, sizeof(down));
    if (focused) {
        for (const uint8_t vk : g_keys.kill) {
            if (vk != 0) {
                down[vk] = kb->down(vk) ? 1 : 0;
            }
        }
        for (uint32_t s = 1; s < GUESTS; ++s) {
            for (const uint8_t vk : g_keys.toggle[s]) {
                if (vk != 0) {
                    down[vk] = kb->down(vk) ? 1 : 0;
                }
            }
        }
    }
    const bool trusted = focused && g_prev_focused;
    if (trusted) {
        for (const uint8_t vk : g_keys.kill) {
            *kill = *kill || (vk != 0 && down[vk] && !g_prev_down[vk]);
        }
        for (uint32_t s = 1; s < GUESTS; ++s) {
            for (const uint8_t vk : g_keys.toggle[s]) {
                if (vk != 0 && down[vk] && !g_prev_down[vk]) {
                    toggles |= 1u << s;
                }
            }
        }
    }
    memcpy(g_prev_down, down, sizeof(down));
    g_prev_focused = focused;
    return toggles;
}

// Pad bindings (and the keys of the seat that owns the keyboard) arrive as TOGGLE_CONTROL press edges.
uint32_t read_action_toggles()
{
    uint32_t toggles = 0;
    for (uint32_t s = 1; s < GUESTS; ++s) {
        edges::edge_delta d;
        if (edges::since(g_cursor, s + 1, &d) && !d.overrun && !d.reseeded && (d.act_pressed & TOGGLE_BIT) != 0) {
            toggles |= 1u << s;
        }
    }
    return toggles;
}

// The live park bits: the input service's (its PARK command, the Mod Menu's), with a toggle's own bit on top until
// the service reports it. P1's bit is always 0.
uint32_t park_bits(uint64_t now)
{
    uint32_t service = 0;
    const bool running = input::snapshot_read(&g_snapshot) && g_snapshot.state == input::RUN_RUNNING;
    if (running) {
        service = g_snapshot.control.parked;
    }
    for (uint32_t s = 1; s < GUESTS; ++s) {
        const uint32_t bit = 1u << s;
        if ((g_override_mask & bit) == 0) {
            continue;
        }
        const bool reported = running && (service & bit) == (g_override_value & bit);
        if (reported || (!g_override_sticky[s] && now >= g_override_until[s])) {
            g_override_mask &= ~bit;
        }
    }
    const uint32_t bits = (service & ~g_override_mask) | (g_override_value & g_override_mask);
    return bits & ~1u & ((1u << GUESTS) - 1u);
}

void toggle_seat(uint32_t s, uint64_t now, uint32_t current)
{
    const uint32_t bit = 1u << s;
    const bool park = (current & bit) == 0;
    const input::command_msg m = {static_cast<uint16_t>(ILC_CMD_PARK), static_cast<uint8_t>(s + 1), 0, 0,
                                  park ? 1u : 0u, 0};
    const int32_t posted = input::post_command(m);
    g_override_mask |= bit;
    g_override_value = park ? (g_override_value | bit) : (g_override_value & ~bit);
    g_override_until[s] = now + OVERRIDE_HOLD_MS;
    g_override_sticky[s] = posted == ILC_E_NOT_READY;  // no service: the toggle is the only park source
    bump(g_counters.toggles);
}

void begin_release(uint64_t now)
{
    isolation::begin_release();
    g_release_t0 = now;
}

bool release_done(uint64_t now)
{
    if (isolation::release_settled()) {
        bump(g_counters.released_by_settle);
    } else if (now - g_release_t0 >= RELEASE_CAP_MS) {
        bump(g_counters.released_by_cap);
    } else {
        return false;
    }
    __atomic_store_n(&g_counters.last_release_ms, now - g_release_t0, __ATOMIC_RELAXED);
    return true;
}

void kill_on(uint64_t now)
{
    session::set_live_enabled(false);
    if (!g_releasing_for_drop) {
        begin_release(now);  // an ordered drop already in progress keeps its start time
    }
    g_releasing_for_drop = false;
    __atomic_store_n(&g_state, STATE_RELEASING, __ATOMIC_RELEASE);
}

void finish_kill(uint64_t now)
{
    if (!release_done(now)) {
        return;
    }
    const uint32_t current = patch::subsystems_mask();
    if (!apply_mask(current & ~ISOLATION) || !apply_mask(0)) {
        return;  // retried next beat; the release stays published meanwhile
    }
    isolation::end_release();
    __atomic_store_n(&g_state, STATE_OFF, __ATOMIC_RELEASE);
    bump(g_counters.kills);
}

void revive()
{
    session::set_live_enabled(true);
    __atomic_store_n(&g_reapply_mask, settings_mask(), __ATOMIC_RELEASE);
    __atomic_store_n(&g_reapply, 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_state, STATE_ON, __ATOMIC_RELEASE);
    bump(g_counters.revives);
}

}  // namespace

void configure_from_settings(const ilc_settings& s)
{
    keys k = {};
    for (uint32_t i = 0; i < ILC_KILL_SWITCH_BINDINGS; ++i) {
        const ilc_binding& b = s.kill_switch[i];
        if (b.kind == ILC_BIND_KEY) {
            k.kill[i] = static_cast<uint8_t>(b.code);
        } else if (b.kind == ILC_BIND_MOUSE && (b.code == 1 || b.code == 2)) {
            k.kill[i] = static_cast<uint8_t>(b.code);  // VK_LBUTTON / VK_RBUTTON
        }
    }
    for (uint32_t seat = 1; seat < GUESTS; ++seat) {
        if (s.kbm_seat == seat + 1) {
            continue;  // that seat's keys are its own bindings: they arrive as action edges
        }
        for (uint32_t slot = 0; slot < ILC_BINDINGS_PER_ACTION; ++slot) {
            const ilc_binding& b = s.seats[seat].bindings[ILC_ACTION_TOGGLE_CONTROL * ILC_BINDINGS_PER_ACTION + slot];
            if (b.kind == ILC_BIND_KEY) {
                k.toggle[seat][slot] = static_cast<uint8_t>(b.code);
            }
        }
    }
    g_keys = k;
    g_configured = true;
}

void step(uint64_t now_ms)
{
    bool kill_key = false;
    const uint32_t key_toggles = g_configured ? read_keys(&kill_key) : 0u;
    const uint32_t pad_toggles = read_action_toggles();
    const uint32_t request = __atomic_exchange_n(&g_kill_request, 0u, __ATOMIC_ACQ_REL);
    if (kill_key) {
        bump(g_counters.kill_presses);
    }
    const uint32_t state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE);
    const bool want_off = (kill_key && state == STATE_ON) || (request == 1 && state == STATE_ON);
    const bool want_on = (kill_key && state == STATE_OFF) || (request == 2 && state == STATE_OFF);

    if (state == STATE_ON) {
        if (want_off) {
            kill_on(now_ms);
        } else if (__atomic_load_n(&g_drop_pending, __ATOMIC_ACQUIRE) != 0) {
            // A settings record turned INPUT_ISOLATION off: release first, then apply the record's mask.
            if (!g_releasing_for_drop) {
                g_releasing_for_drop = true;
                begin_release(now_ms);
            } else if (release_done(now_ms) && apply_mask(settings_mask())) {
                isolation::end_release();
                g_releasing_for_drop = false;
                __atomic_store_n(&g_drop_pending, 0u, __ATOMIC_RELEASE);
                bump(g_counters.isolation_drops);
            }
        }
    } else if (state == STATE_RELEASING) {
        finish_kill(now_ms);
    } else if (want_on) {
        revive();
    }

    uint32_t current = park_bits(now_ms);
    const uint32_t toggles = key_toggles | pad_toggles;
    if (toggles != 0) {
        if (__atomic_load_n(&g_state, __ATOMIC_ACQUIRE) != STATE_ON) {
            bump(g_counters.toggles_ignored);
        } else {
            for (uint32_t s = 1; s < GUESTS; ++s) {
                if ((toggles & (1u << s)) != 0) {
                    toggle_seat(s, now_ms, current);
                }
            }
            current = park_bits(now_ms);
        }
    }
    __atomic_store_n(&g_parked, current, __ATOMIC_RELEASE);
    session::set_live_parked(current);  // the session step that follows sees it: stock on the same beat
}

void post_kill(bool off)
{
    __atomic_store_n(&g_kill_request, off ? 1u : 2u, __ATOMIC_RELEASE);
}

uint32_t current_state()
{
    return __atomic_load_n(&g_state, __ATOMIC_ACQUIRE);
}

uint32_t parked_seats()
{
    return __atomic_load_n(&g_parked, __ATOMIC_ACQUIRE);
}

uint32_t mask_for_safe_point(uint32_t requested)
{
    __atomic_store_n(&g_settings_mask, requested, __ATOMIC_RELEASE);
    __atomic_store_n(&g_settings_known, 1u, __ATOMIC_RELEASE);
    const uint32_t state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE);
    if (state == STATE_OFF) {
        return 0;  // co-op is off: the record is remembered for when it comes back
    }
    const uint32_t current = patch::subsystems_mask();
    if (state == STATE_RELEASING) {
        return current;  // the kill switch's sequence owns the mask until it has finished
    }
    if ((current & ISOLATION) != 0 && (requested & ISOLATION) == 0) {
        __atomic_store_n(&g_drop_pending, 1u, __ATOMIC_RELEASE);
        return requested | ISOLATION;  // isolation stays until its release has run (the co-op thread drops it)
    }
    if ((requested & ISOLATION) != 0) {
        __atomic_store_n(&g_drop_pending, 0u, __ATOMIC_RELEASE);  // a later record wants it back: nothing to drop
    }
    return requested;
}

bool take_reapply(uint32_t* mask)
{
    if (__atomic_exchange_n(&g_reapply, 0u, __ATOMIC_ACQ_REL) == 0) {
        return false;
    }
    *mask = __atomic_load_n(&g_reapply_mask, __ATOMIC_ACQUIRE);
    return true;
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    g_state = STATE_ON;
    g_kill_request = 0;
    g_settings_mask = 0;
    g_settings_known = 0;
    g_drop_pending = 0;
    g_reapply = 0;
    g_reapply_mask = 0;
    g_parked = 0;
    memset(&g_counters, 0, sizeof(g_counters));
    g_keys = {};
    g_configured = false;
    g_test_keyboard = nullptr;
    g_prev_focused = false;
    memset(g_prev_down, 0, sizeof(g_prev_down));
    memset(&g_cursor, 0, sizeof(g_cursor));
    g_releasing_for_drop = false;
    g_release_t0 = 0;
    g_override_mask = 0;
    g_override_value = 0;
    memset(g_override_until, 0, sizeof(g_override_until));
    memset(g_override_sticky, 0, sizeof(g_override_sticky));
    g_log_count = 0;
}

void test_keyboard(input::keyboard_api* keyboard)
{
    g_test_keyboard = keyboard;
}

uint32_t test_mask_log(uint32_t* out, uint32_t max)
{
    uint32_t n = 0;
    for (; n < g_log_count && n < max; ++n) {
        out[n] = g_log[n];
    }
    return n;
}

}  // namespace ilc::control
