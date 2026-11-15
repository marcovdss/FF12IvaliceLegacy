// Ivalice Legacy co-op module: the co-op-thread half of the input firewall (see firewall.h).
#include "firewall/firewall.h"

#include <string.h>

#include "control/control.h"
#include "input/edge_history.h"
#include "input/input_service.h"
#include "interaction/interaction.h"
#include "isolation/isolation.h"
#include "session/reader.h"
#include "session/session.h"

namespace ilc::firewall {

namespace detail {
patch::record_slot<fw_record> g_slot;
counters g_counters;
}  // namespace detail

namespace {

constexpr uint64_t DECIDE_EVERY_MS = 250;
constexpr uint32_t ACTIVE_SEATS = ILC_ACTIVE_SEATS_MAX < ILC_SEATS ? ILC_ACTIVE_SEATS_MAX : ILC_SEATS;
constexpr uint32_t ISO_ROUTED = isolation::ISO_GUARD | isolation::ISO_SUBST | isolation::ISO_DRIVER;

void bump(volatile uint64_t& counter, uint64_t n = 1)
{
    __atomic_add_fetch(&counter, n, __ATOMIC_RELAXED);
}

struct scheduler {
    uint16_t button;           // the current (or last) pulse
    uint8_t source;            // seat (1..) whose edge it carries
    uint8_t rr;                // the guest seat served last
    uint64_t until;            // reads high while now < until
    uint64_t next_free;        // the next pulse may start at or after this
    uint16_t pending[ILC_SEATS];   // index = seat - 1 (index 0 = P1's parity pulse)
    uint64_t pending_at[ILC_SEATS];
    uint8_t requested[ILC_SEATS];  // the pending PARTY edge is a native Party request (kept outside native screens)
    uint64_t target_info_next;
    uint32_t last_p1_kind;
};

// Co-op thread only.
struct core {
    bool started;
    uintptr_t image_base;
    edges::edge_cursor cursor;
    uint32_t cursor_epoch;
    uint8_t kbm_seat;
    uint8_t guest_devices;
    uint8_t prompt_ready;
    uint8_t reserved;
    menu_report menus;
    bool menus_posted;         // a report exists, posted at beat menus_beat in epoch menus_epoch (0: before a beat)
    uint32_t menus_epoch;
    uint32_t seen_epoch;       // the epoch of the last beat
    uint64_t menus_beat;
    uint64_t beats;            // steps since start
    uint8_t last_switched_on;  // the switch at the previous beat (an off/on edge drops the report)
    scheduler sched;
    uint32_t decide;           // DECIDE_CIRCLE / DECIDE_CROSS
    bool decide_known;
    uint64_t decide_next;
};
core g = {};
uintptr_t g_test_image_base = 0;

uintptr_t image_base()
{
    if (g_test_image_base != 0) {
        return g_test_image_base;
    }
    return g.image_base != 0 ? g.image_base : facts::IMAGE_VA;
}

void drop_pending(uint32_t index)
{
    g.sched.requested[index] = 0;
    if (g.sched.pending[index] != 0) {
        g.sched.pending[index] = 0;
        bump(detail::g_counters.pulses_dropped);
    }
}

// ---- game reads (co-op thread, fault-safe) ---------------------------------------------------------------------
bool pointer_set(session::game_reader& r, uintptr_t va)
{
    uintptr_t p = 0;
    return r.ptr(r.global(va), &p) && p != 0;
}

// The stock command tree is the active menu: its slot holds the tree's callback and the active-menu global names
// that slot.
bool tree_active(session::game_reader& r)
{
    uintptr_t ui = 0;
    uint64_t callback = 0;
    uintptr_t active = 0;
    return r.ptr(r.global(facts::UI_MANAGER), &ui) && ui != 0 && r.u64(ui + facts::UI_TREE_SLOT, &callback) &&
           callback == r.global(facts::CB_COMMAND_TREE) && r.ptr(r.global(facts::ACTIVE_MENU), &active) &&
           active == ui + facts::UI_TREE_SLOT;
}

bool target_info_open(session::game_reader& r)
{
    uintptr_t ui = 0;
    uintptr_t window = 0;
    return r.ptr(r.global(facts::UI_MANAGER), &ui) && ui != 0 && r.ptr(ui + facts::UI_TARGET_INFO, &window) &&
           window != 0;
}

// A foe's prompt never becomes P1's CONFIRM (it would open nothing useful and could start an attack): an actor
// (registry 0) whose unit type is a foe's (1, 4, 7), unless it is a mimic asleep as a chest.
bool foe_target(session::game_reader& r, uint32_t handle)
{
    if (((handle >> facts::HANDLE_REGISTRY_SHIFT) & 0x0F) != 0) {
        return false;
    }
    const uintptr_t entry = interaction::resolve_handle(r, handle);
    uint8_t unit = 0;
    if (entry == 0 || !r.u8(entry + facts::ENTRY_UNIT_TYPE, &unit)) {
        return false;
    }
    const uint8_t type = unit & 0x0F;
    if (type != 1 && type != 4 && type != 7) {
        return false;
    }
    return !interaction::sleeping_mimic_handle(r, handle);
}

uint8_t prompt_state(session::game_reader& r)
{
    uint8_t can_use = 0;
    uint32_t d_state = 0;
    uint32_t d_handle = 0;
    uint32_t o_state = 0;
    uint32_t o_handle = 0;
    const uintptr_t desc = r.global(facts::PROMPT_DESCRIPTOR);
    const uintptr_t out = r.global(facts::PROMPT_OUTPUT);
    if (!r.u8(r.global(facts::PROMPT_CAN_USE), &can_use) || !r.u32(desc + facts::DESC_STATE, &d_state) ||
        !r.u32(desc + facts::DESC_HANDLE, &d_handle) || !r.u32(out + facts::OUTPUT_STATE, &o_state) ||
        !r.u32(out + facts::OUTPUT_HANDLE, &o_handle)) {
        return 0;
    }
    uint32_t state = 0;
    uint32_t handle = 0;
    if (can_use != 0 && d_state >= facts::PROMPT_SELECTED && d_handle != 0) {
        state = d_state;
        handle = d_handle;
    } else if (o_state != 0 && o_handle != 0) {
        state = o_state;
        handle = o_handle;
    }
    if (state == 0 || foe_target(r, handle)) {
        return 0;
    }
    return state > 0xFF ? 0xFF : static_cast<uint8_t>(state);
}

void read_decide(session::game_reader& r, uint64_t now)
{
    if (g.decide_known && now < g.decide_next) {
        return;
    }
    g.decide_next = now + DECIDE_EVERY_MS;
    bump(detail::g_counters.decide_reads);
    uintptr_t settings = 0;
    uint32_t value = facts::DECIDE_CROSS;
    uint32_t raw = 0;
    if (r.ptr(r.global(facts::SETTINGS), &settings) && settings != 0 &&
        r.u32(settings + facts::SETTINGS_DECIDE, &raw) && (raw == facts::DECIDE_CIRCLE || raw == facts::DECIDE_CROSS)) {
        value = raw;
    }
    if (!g.decide_known || value != g.decide) {
        g.decide = value;
        g.decide_known = true;
        input::set_decide_swap(value == facts::DECIDE_CIRCLE);  // every seat's pad A/B swap for CONFIRM/CANCEL
    }
}

beat_inputs gather(uint64_t now)
{
    beat_inputs in = {};
    session::session_record s;
    if (session::read(&s)) {
        in.epoch = s.head.epoch;  // the epoch the session published (the liveness epoch of this beat)
        in.generation = s.head.generation;
        in.gates = s.head.flags;
        in.facts = s.facts;
        in.phase = s.phase;
        for (uint32_t k = 0; k < ACTIVE_SEATS; ++k) {
            const uint16_t f = s.seat[k].flags;
            if ((f & session::SEATF_IN_SESSION) != 0 && (f & session::SEATF_MANUAL) != 0) {
                in.seat_playing |= static_cast<uint8_t>(1u << k);
            }
        }
    }
    in.switched_on = control::current_state() == control::STATE_ON ? 1 : 0;
    in.live = patch::subsystem_live(ILC_SUBSYS_FIREWALL_MENUS) ? 1 : 0;
    session::game_reader r(image_base());
    read_decide(r, now);
    if (in.epoch != 0 && in.phase >= session::PHASE_LOADING) {
        in.paused = pointer_set(r, facts::PAUSE_PRIMARY) || pointer_set(r, facts::PAUSE_SECONDARY) ? 1 : 0;
        in.target_info = target_info_open(r) ? 1 : 0;
        in.tree_active = tree_active(r) ? 1 : 0;
        in.prompt_state = prompt_state(r);
    }
    const uint32_t driver = interaction::menu_driver();
    in.driver = driver >= 1 && driver < ACTIVE_SEATS ? static_cast<uint8_t>(driver) : 0;
    in.kbm_seat = g.kbm_seat;
    in.guest_devices = g.guest_devices;
    isolation::iso_record iso;
    in.p1_slot = 0xFF;
    if (isolation::slot().read(iso)) {
        in.iso_flags = iso.head.flags;
        in.p1_slot = iso.p1_slot;
    }
    // Edges since the last beat. A new epoch, a re-seeded or overrun cursor gives none (no press from before is
    // ever replayed into a new world).
    const bool new_epoch = in.epoch != g.cursor_epoch;
    g.cursor_epoch = in.epoch;
    for (uint32_t seat = 1; seat <= ACTIVE_SEATS; ++seat) {
        edges::edge_delta d;
        if (edges::since(g.cursor, seat, &d) && !d.reseeded && !d.overrun && !new_epoch) {
            in.act_pressed[seat - 1] = d.act_pressed;
            in.act_held[seat - 1] = d.act_held;
            in.pad_pressed[seat - 1] = d.pad_pressed;
        }
    }
    // The menu report counts only while it is fresh: posted in this epoch (or before the first beat since start),
    // within the last MENU_REPORT_BEATS beats. A switch edge drops it, so a stalled menu layer never leaves P1
    // without its stock frontends.
    if (g.last_switched_on != 0xFF && in.switched_on != g.last_switched_on) {
        g.menus_posted = false;
    }
    g.last_switched_on = in.switched_on;
    if (g.menus_posted && (g.menus_epoch != 0 && g.menus_epoch != in.epoch)) {
        g.menus_posted = false;  // posted in another epoch
    }
    g.seen_epoch = in.epoch;
    if (g.menus_posted && g.beats - g.menus_beat <= MENU_REPORT_BEATS) {
        in.menus = g.menus;
        if (g.menus_beat + 1 != g.beats) {
            in.menus.party_pulse = 0;  // a request pulse is one beat's decision: an older report never repeats it
        }
    } else {
        g.menus_posted = false;
        memset(&g.menus, 0, sizeof(g.menus));
        in.menus = {};
    }
    return in;
}

}  // namespace

// ---- one beat --------------------------------------------------------------------------------------------------
fw_record beat(const beat_inputs& in, uint64_t now)
{
    using namespace session;
    fw_record rec = {};
    rec.head.epoch = in.epoch;
    rec.head.generation = in.generation;
    rec.p1_slot = 0xFF;
    const menu_report& m = in.menus;
    const bool native_menu = (in.facts & (FACT_MENU | FACT_MODAL)) != 0 || in.paused != 0;

    policy_inputs pi = {};
    pi.active = in.live != 0 && in.switched_on != 0 && in.epoch != 0 && in.phase >= PHASE_LOADING ? 1 : 0;
    pi.p1_menus = m.p1_menus;
    pi.control = (in.facts & FACT_CONTROL) != 0 ? 1 : 0;
    pi.passthrough = native_menu || (in.facts & (FACT_TOWN | FACT_CHOCOBO)) != 0 || m.quickening != 0 ||
                             in.driver != 0 || (in.gates & GATE_PARTY_DOWN) != 0
                         ? 1
                         : 0;
    pi.suppressed = m.suppressed;
    pi.p1_open = m.p1_open;
    pi.any_open = m.p1_open != 0 || (m.guests_open & 0x0E) != 0 ? 1 : 0;
    pi.leader_selector = m.leader_selector;
    pi.leader_pending = m.leader_pending;
    pi.party_request = m.party_request;
    pi.quickening = m.quickening;
    pi.desktop_guest = in.kbm_seat >= 2 && in.kbm_seat <= ACTIVE_SEATS ? 1 : 0;
    pi.presenter = m.presenter_live;
    pi.leader_gate = m.leader_gate;
    const contract c = pi.active != 0 ? derive(pi) : contract{};
    const policy p = pi.active != 0 ? masks(c) : policy{};
    rec.p1_mask = p.p1_mask;
    rec.other_mask = p.other_mask;
    rec.refused = p.refused;
    uint32_t flags = p.flags;

    // Which pad object is P1's (see firewall.h).
    const bool routed = (in.iso_flags & ISO_ROUTED) != 0;
    if (!routed) {
        flags |= FWF_P1_ALL_PADS;
    } else {
        if ((in.iso_flags & isolation::ISO_P1_NATIVE) != 0 && (in.iso_flags & isolation::ISO_DRIVER) == 0 &&
            in.p1_slot < 4) {
            flags |= FWF_P1_NATIVE;
            rec.p1_slot = in.p1_slot;
        }
        if ((in.iso_flags & (isolation::ISO_SUBST | isolation::ISO_DRIVER)) != 0) {
            flags |= FWF_P1_HOST;
        }
        if ((flags & (FWF_P1_NATIVE | FWF_P1_HOST)) == 0) {
            flags |= FWF_PULSE_ANY;
        }
    }
    if (in.tree_active == 0 || in.paused != 0) {
        flags &= ~static_cast<uint32_t>(FWF_SUPPRESS_P1_AXES);  // only while the stock tree is the active menu
    }
    if (in.kbm_seat == 1 || (!routed && in.guest_devices == 0)) {
        flags |= FWF_DESKTOP_P1;
    }
    rec.head.flags = pi.active != 0 ? flags : 0;

    // ---- pulses ----
    scheduler& sc = g.sched;
    const bool pulses_on = pi.active != 0 && in.driver == 0;  // a guest driving a native menu owns it alone
    p1_inputs p1 = {};
    p1.replacement = c.p1_replacement;
    p1.menu_open = m.p1_open;
    p1.target_page = m.p1_target;
    p1.leader_selector = m.leader_selector;
    p1.driver = in.driver != 0 ? 1 : 0;
    const bool p1_pad = in.pad_pressed[0] != 0;
    p1.confirm_edge = p1_pad && (in.act_pressed[0] & (1u << ILC_ACTION_CONFIRM)) != 0 ? 1 : 0;
    p1.cancel_edge = p1_pad && (in.act_pressed[0] & (1u << ILC_ACTION_CANCEL)) != 0 ? 1 : 0;
    p1.prompt_state = in.prompt_state;
    p1.target_info = in.target_info;
    p1.target_info_due = now >= sc.target_info_next ? 1 : 0;
    g.prompt_ready = pulses_on && c.p1_replacement != 0 && m.p1_open == 0 &&
                             in.prompt_state >= facts::PROMPT_HIGHLIGHTED
                         ? 1
                         : 0;
    if (!pulses_on) {
        for (uint32_t k = 0; k < ILC_SEATS; ++k) {
            drop_pending(k);
        }
    } else {
        uint32_t kind = P1P_NONE;
        const uint32_t id = p1_pulse(p1, &kind);
        if (id != 0) {
            sc.pending[0] = static_cast<uint16_t>(id);
            sc.pending_at[0] = now;
            sc.requested[0] = 0;
            sc.last_p1_kind = kind;
            if (kind == P1P_TARGET_INFO) {
                sc.target_info_next = now + TARGET_INFO_CLOSE_MS;
            }
        }
        for (uint32_t seat = 2; seat <= ACTIVE_SEATS; ++seat) {
            const uint32_t k = seat - 1;
            if ((in.seat_playing & (1u << k)) == 0) {
                drop_pending(k);
                continue;
            }
            uint32_t gid = guest_pulse(in.act_pressed[k], in.act_held[k], native_menu);
            if (gid == 0 && (m.quickening & QB_CHAIN) != 0 &&
                (in.act_pressed[k] & (1u << ILC_ACTION_QUICKENING)) != 0) {
                // A native Quickening chain: the guest presses its own link (the slot's button on P1's device).
                if ((m.quickening & QB_COMMAND) != 0 && m.quickening_command_seat == seat) {
                    gid = facts::SEM_COMMAND;
                } else if ((m.quickening & QB_CONFIRM) != 0 && m.quickening_confirm_seat == seat) {
                    gid = facts::SEM_CONFIRM;
                }
            }
            if (gid != 0) {
                sc.pending[k] = static_cast<uint16_t>(gid);
                sc.pending_at[k] = now;
                sc.requested[k] = 0;
            } else if (sc.pending[k] != 0 && sc.pending[k] != facts::SEM_PAUSE && sc.requested[k] == 0 &&
                       !native_menu && (m.quickening & QB_CHAIN) == 0) {
                drop_pending(k);  // the native screen (or chain) it was meant for has closed
            }
        }
        // A native Party request (the co-op menu layer's transaction): one PARTY edge from the requesting seat, which
        // opens P1's stock Party screen outside any native screen. P1's parity pulse keeps its slot if it has one.
        const uint32_t ps = m.party_pulse;
        if (ps >= 1 && ps <= ACTIVE_SEATS && (ps == 1 || (in.seat_playing & (1u << (ps - 1))) != 0) &&
            (ps != 1 || sc.pending[0] == 0)) {
            sc.pending[ps - 1] = static_cast<uint16_t>(facts::SEM_PARTY);
            sc.pending_at[ps - 1] = now;
            sc.requested[ps - 1] = 1;
        }
    }
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        if (sc.pending[k] != 0 && now > sc.pending_at[k] + PULSE_STALE_MS) {
            drop_pending(k);
        }
    }
    if (pulses_on && now >= sc.next_free) {
        int32_t pick = -1;
        if (sc.pending[0] != 0) {
            pick = 0;
        } else {
            for (uint32_t step = 1; step <= ACTIVE_SEATS - 1 && pick < 0; ++step) {
                // guest seats 2..ACTIVE_SEATS, starting after the one served last
                const uint32_t last = sc.rr >= 2 ? sc.rr : ACTIVE_SEATS;
                const uint32_t seat = 2 + ((last - 2 + step) % (ACTIVE_SEATS - 1));
                if (sc.pending[seat - 1] != 0) {
                    pick = static_cast<int32_t>(seat - 1);
                }
            }
        }
        if (pick >= 0) {
            sc.button = sc.pending[pick];
            sc.source = static_cast<uint8_t>(pick + 1);
            sc.until = now + PULSE_MS;
            sc.next_free = sc.until + PULSE_GAP_MS;
            sc.pending[pick] = 0;
            sc.requested[pick] = 0;
            if (pick == 0) {
                bump(detail::g_counters.pulses_p1);
            } else {
                sc.rr = static_cast<uint8_t>(pick + 1);
            }
            bump(detail::g_counters.pulses_started);
        }
    }
    if (!pulses_on) {
        sc.until = 0;  // a pulse never outlives the state that allowed it
    }
    if (sc.until > now) {
        rec.pulse = sc.button;
        rec.pulse_until = sc.until;
    }
    return rec;
}

// ---- co-op thread ----------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    g.started = true;
    g.image_base = image_base;
    memset(&g.cursor, 0, sizeof(g.cursor));
    g.cursor_epoch = 0;
    memset(&g.sched, 0, sizeof(g.sched));
    g.decide_known = false;
    g.menus_posted = false;
    memset(&g.menus, 0, sizeof(g.menus));
    g.beats = 0;
    g.menus_beat = 0;
    g.menus_epoch = 0;
    g.seen_epoch = 0;
    g.last_switched_on = 0xFF;  // unknown until the first beat
}

void configure_from_settings(const ilc_settings& s)
{
    g.kbm_seat = s.kbm_seat <= ACTIVE_SEATS ? s.kbm_seat : 0;
    uint8_t guests = 0;
    for (uint32_t i = 1; i < ACTIVE_SEATS; ++i) {
        if (s.seats[i].device.kind != ILC_DEVICE_NONE || g.kbm_seat == i + 1) {
            guests |= static_cast<uint8_t>(1u << i);
        }
    }
    g.guest_devices = guests;
}

void post_menus(const menu_report& m)
{
    g.menus = m;
    g.menus_posted = true;
    g.menus_epoch = g.seen_epoch;
    g.menus_beat = g.beats;
}

void step(uint64_t now_ms)
{
    ++g.beats;
    const beat_inputs in = gather(now_ms);
    if (in.epoch == 0) {
        detail::g_slot.withdraw();
        return;
    }
    detail::g_slot.publish(beat(in, now_ms));
    bump(detail::g_counters.published);
}

void stop()
{
    detail::g_slot.withdraw();
    g.started = false;
    g.menus_posted = false;
    memset(&g.menus, 0, sizeof(g.menus));
}

bool p1_prompt_ready()
{
    return g.prompt_ready != 0;
}

uint32_t decide_button()
{
    return g.decide_known ? g.decide : facts::DECIDE_CROSS;
}

const counters* stats()
{
    return &detail::g_counters;
}

void test_debug(debug_view* out)
{
    memset(out, 0, sizeof(*out));
    out->pulse_until = g.sched.until;
    out->next_free = g.sched.next_free;
    out->target_info_next = g.sched.target_info_next;
    out->pulse = g.sched.button;
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        out->pending[k] = g.sched.pending[k];
    }
    out->source = g.sched.source;
    out->rr = g.sched.rr;
    out->p1_prompt_ready = g.prompt_ready;
    out->decide = static_cast<uint8_t>(decide_button());
    out->last_p1_kind = g.sched.last_p1_kind;
}

bool menus_own_p1()
{
    if (!patch::subsystem_live(ILC_SUBSYS_FIREWALL_MENUS)) {
        return false;
    }
    fw_record r;
    return detail::g_slot.read(r) && (r.head.flags & (FWF_ACTIVE | FWF_GATE)) == (FWF_ACTIVE | FWF_GATE);
}

patch::record_slot<fw_record>& slot()
{
    return detail::g_slot;
}

void test_reset()
{
    const uintptr_t base = g.image_base;
    const bool started = g.started;
    g = core{};
    g.image_base = base;
    g.started = started;
    memset(const_cast<counters*>(&detail::g_counters), 0, sizeof(detail::g_counters));
}

void test_publish(const fw_record& r)
{
    detail::g_slot.publish(r);
}

void test_set_image_base(uintptr_t base)
{
    g_test_image_base = base;
}

}  // namespace ilc::firewall
