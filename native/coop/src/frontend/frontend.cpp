// Ivalice Legacy co-op module: the co-op battle menus at run time (see frontend.h).
#include "frontend/frontend.h"

#include <string.h>

#include "control/control.h"
#include "firewall/firewall.h"
#include "firewall/firewall_facts.h"
#include "frontend/presenter.h"
#include "ilc/bridge_abi.h"
#include "input/edge_history.h"
#include "interaction/interaction.h"
#include "menu/live.h"
#include "menu/menu_facts.h"
#include "menu/menus.h"
#include "overlay/overlay.h"
#include "party/party.h"
#include "patch/patch_engine.h"
#include "runtime/log.h"
#include "runtime/options.h"
#include "runtime/tick.h"
#include "session/reader.h"
#include "session/world.h"
#include "submit/submit.h"
#include "targets/live.h"
#include "targets/targets_facts.h"
#include "wait/wait.h"

namespace ilc::frontend {
namespace {

using session::seat_entry;
using session::session_record;
namespace ff = firewall::facts;

constexpr uint32_t ACTIVE = ILC_ACTIVE_SEATS_MAX < SEATS ? ILC_ACTIVE_SEATS_MAX : SEATS;
constexpr uint32_t ACTOR_ACTION = 0x714;  // u16: the action an actor is performing (0xFFFF none)

struct seat_rt {
    menu::seat_menu sm;
    selector sel;               // the seat's character selector (Change Leader, Change Character, KO recovery)
    uint64_t sel_next;          // its next candidate refresh
    targets::target_cursor cursor;
    uint64_t next_refresh;
    uint64_t next_pick;
    uint64_t reopen_at;
    uint64_t guard_until;
    uint32_t handle;
    uint16_t member;
    uint8_t bound;
    uint8_t eligible;
    uint32_t last_commands;
    int32_t last_submit;
    uint32_t opens;
    uint32_t submits;
};

struct core {
    bool started;
    uintptr_t image_base;
    edges::edge_cursor cursor;
    uint32_t cursor_epoch;
    uint32_t epoch;
    uint32_t roles;
    bool tables_ready;
    int32_t tables_status;
    uint64_t next_tables;
    bool shared_ok;
    uint64_t next_shared;
    suspension susp;
    party_request party;
    recovery rec;
    bool recovering;
    bool owns_p1;
    bool wait_requested;
    bool presenter_live;
    bool p1_down;               // P1's own seat character is known down (the party not), whoever leads
    bool p1_ko;                 // ... and still the native leader (the KO recovery applies)
    bool ko_live;               // ... and the co-op KO selector has a living character to offer
    uint64_t p1_out_ms;         // P1's window was left out of a presented frame at this time: P1 has the stock
                                // frontends until seat 0 is shown again and P1_GIVEBACK_MS has passed (0 = not)
    uint8_t interaction_mask;
    uint8_t quickening;         // the quickening bits last reported
    firewall::menu_report report;
    uint32_t report_epoch;      // the epoch `report` was posted in
    bool vanilla_prev;          // P1's own vanilla battle frontend was up on the last beat that sampled the screen
};

core g = {};
seat_rt g_seat[SEATS];
targets::candidate_list g_cands[SEATS];
targets::picker_input g_picker;                 // one scratch for every seat's scan
menu::catalog_tables g_tables;                  // the committed tables
menu::catalog_tables g_tables_next;             // where a read lands before it is committed
menu::shared_state g_shared;
counters g_counters;
const sources* g_test_sources = nullptr;
uintptr_t g_test_image_base = 0;
// Player 1's menu on the overlay (rules.h p1_arm_step): on in the module, off after test_reset() (the host tests then
// set the P1-native switch themselves).
volatile uint32_t g_arming = 1;
p1_arm g_arm = {};
uint64_t g_givebacks_seen = 0;
uint64_t g_failed_seen = 0;     // the presenter's last refused frame already looked at
volatile int32_t g_test_live = -1;      // host tests: -1 overlay::live_track; 0 / 1 forced; 2 the presenter's frames
overlay::live_streak g_test_streak = {};
uint32_t g_arm_logged = 0;      // bit per p1_arm_event already written to hook.log (once each per launch)

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uintptr_t image_base()
{
    if (g_test_image_base != 0) {
        return g_test_image_base;
    }
    return g.image_base != 0 ? g.image_base : ff::IMAGE_VA;
}

// ---- the live readers (co-op thread, fault-safe) ------------------------------------------------------------------
int32_t live_tables(menu::catalog_tables* out)
{
    session::game_reader r(image_base());
    return menu::read_tables(r, menu::known_table_globals(), out);
}

int32_t live_shared(menu::shared_state* out)
{
    session::game_reader r(image_base());
    return menu::read_shared(r, out);
}

int32_t live_seat(uint32_t seat, const seat_entry& e, uint8_t esper_owner, menu::seat_state* out)
{
    (void)seat;
    session::game_reader r(image_base());
    return menu::read_seat(r, static_cast<uintptr_t>(e.actor), e.handle, e.member, esper_owner, out);
}

int32_t live_picker(uint32_t seat, const session_record& rec, targets::picker_input* out)
{
    session::game_reader r(image_base());
    return targets::read_picker(r, rec, seat, out);
}

// The pause screen, whether P1's stock command tree is the active menu (its slot holds the tree's callback and the
// active-menu global names that slot), and whether a native Quickening chain runs (an active party member performs
// one of the Quickening actions).
uint32_t live_screen(const session_record& rec)
{
    (void)rec;
    session::game_reader r(image_base());
    uint32_t out = 0;
    session::party_actors pa;
    session::scan_party(r, &pa);
    for (const session::party_actor& a : pa.slot) {
        uint16_t action = 0;
        if (a.actor != 0 && r.u16(a.actor + ACTOR_ACTION, &action) && action >= menu::facts::QUICKENING_FIRST &&
            action <= menu::facts::QUICKENING_LAST) {
            out |= SCREEN_QUICKENING;
        }
    }
    uintptr_t p = 0;
    if ((r.ptr(r.global(ff::PAUSE_PRIMARY), &p) && p != 0) || (r.ptr(r.global(ff::PAUSE_SECONDARY), &p) && p != 0)) {
        out |= SCREEN_PAUSED;
    }
    uintptr_t ui = 0;
    uint64_t callback = 0;
    uintptr_t active = 0;
    if (r.ptr(r.global(ff::UI_MANAGER), &ui) && ui != 0 && r.u64(ui + ff::UI_TREE_SLOT, &callback) &&
        callback == r.global(ff::CB_COMMAND_TREE) && r.ptr(r.global(ff::ACTIVE_MENU), &active) &&
        active == ui + ff::UI_TREE_SLOT) {
        out |= SCREEN_TREE;
    }
    // Player 1's own battle frontend as the current one (id 0x2092730 masked, open flag 0x2092734): the battle menu,
    // the leader select or the command-tree variant.
    uint32_t frontend_id = 0;
    uint32_t frontend_open = 0;
    if (r.u32(r.global(session::facts::COMMAND_ACTOR), &frontend_id) &&
        r.u32(r.global(session::facts::COMMAND_FLAG), &frontend_open) && frontend_open == 1) {
        const uint32_t id = frontend_id & ff::FRONTEND_ID_MASK;
        if (id == ff::FRONTEND_BATTLE || id == ff::FRONTEND_LEADER || id == ff::FRONTEND_TREE_ACTIVE) {
            out |= SCREEN_P1_BATTLE;
        }
        if (id == ff::FRONTEND_LEADER) {
            out |= SCREEN_P1_LEADER;
        }
    }
    return out;
}

sources live_sources()
{
    if (g_test_sources != nullptr) {
        return *g_test_sources;
    }
    sources s;
    s.tables = live_tables;
    s.shared = live_shared;
    s.seat = live_seat;
    s.picker = live_picker;
    s.screen = live_screen;
    return s;
}

// ---- helpers ------------------------------------------------------------------------------------------------------
bool is_open(uint32_t s)
{
    return g_seat[s].sm.model.mode != menu::MODE_CLOSED;
}

bool selecting(uint32_t s)
{
    return g_seat[s].sel.kind != SEL_NONE;
}

// The seat's battle menu or its character selector is up: either owns the seat's buttons.
bool busy(uint32_t s)
{
    return is_open(s) || selecting(s);
}

uint8_t open_mask()
{
    uint8_t m = 0;
    for (uint32_t s = 0; s < ACTIVE; ++s) {
        m = static_cast<uint8_t>(m | (busy(s) ? 1u << s : 0u));
    }
    return m;
}

void hard_close(uint32_t s)
{
    seat_rt& st = g_seat[s];
    if (st.sm.model.mode != menu::MODE_CLOSED) {
        menu::model_close(&st.sm.model);
        bump(g_counters.hard_closes);
    }
    if (st.sel.kind != SEL_NONE) {
        selector_close(&st.sel);
        bump(g_counters.hard_closes);
    }
    targets::cursor_reset(&st.cursor);
}

void close_all()
{
    for (uint32_t s = 0; s < SEATS; ++s) {
        hard_close(s);
    }
}

bool alive_ok(const seat_entry& e)
{
    return (e.flags & session::SEATF_ALIVE) != 0 || (e.flags & session::SEATF_ALIVE_KNOWN) == 0;
}

bool has_character(const seat_entry& e)
{
    return (e.flags & session::SEATF_ACTOR) != 0 && e.member != targets::MEMBER_NONE && e.handle != 0;
}

// The seat's selector choices from the live party (HUD order) and who holds whom. A failed read keeps what the seat
// was shown; the transaction re-checks everything on the game thread anyway.
void fill_selector(uint32_t s, const session_record& rec, uint8_t kind, uint64_t now, bool force)
{
    seat_rt& st = g_seat[s];
    if (!force && now < st.sel_next) {
        return;
    }
    st.sel_next = now + PICK_EVERY_MS;
    const sources src = live_sources();
    if (src.picker(s, rec, &g_picker) != targets::PICK_OK) {
        if (force) {
            selector_build(&st.sel, SEL_NONE, selector_in{});  // nothing to show: not opened
        }
        return;
    }
    selector_in in = {};
    in.seat = static_cast<uint8_t>(s);
    in.own = rec.seat[s].member;
    in.own_able = 0;
    for (uint32_t k = 0; k < SELECTOR_SLOTS; ++k) {
        in.slot[k].member = SEL_NO_MEMBER;
        in.slot[k].holder = SEL_NO_SEAT;
        if (k >= g_picker.party_count) {
            continue;
        }
        const targets::party_member& pm = g_picker.party[k];
        const bool able = pm.present != 0 && pm.hp != 0 &&
                          (pm.status & (targets::facts::ST_KO | targets::facts::ST_STONE)) == 0;
        in.slot[k].member = pm.member;
        in.slot[k].able = able ? 1 : 0;
        for (uint32_t q = 0; q < ACTIVE; ++q) {
            const seat_entry& e = rec.seat[q];
            const bool seated = q == 0 || (e.flags & session::SEATF_IN_SESSION) != 0;
            if (seated && e.member == pm.member) {
                in.slot[k].holder = static_cast<uint8_t>(q);
            }
        }
        if (pm.member == in.own) {
            in.own_able = able ? 1 : 0;
        }
    }
    selector_build(&st.sel, kind, in);
}

// One beat of an open selector: its refresh and its commands. A choice goes to the seat-change transaction.
void run_selector(uint32_t s, const session_record& rec, uint32_t cmds, uint64_t now)
{
    seat_rt& st = g_seat[s];
    fill_selector(s, rec, st.sel.kind, now, false);
    if (st.sel.count == 0) {
        selector_close(&st.sel);  // nobody left to offer (the KO recovery is tried again while P1 is down)
    }
    const uint8_t kind = st.sel.kind;
    uint16_t chosen = SEL_NO_MEMBER;
    const int32_t ev = selector_command(&st.sel, cmds, &chosen);
    if (ev == SELE_MOVED) {
        submit::play(s, submit::sound_for(menu::EV_TARGET_MOVED));
    } else if (ev == SELE_CLOSED) {
        submit::play(s, submit::sound_for(menu::EV_CLOSED));
    } else if (ev == SELE_CHOSEN) {
        submit::play(s, submit::sound_for(menu::EV_SUBMIT));
        bump(g_counters.selector_choices);
        if (chosen != rec.seat[s].member) {
            party::request_change(s, chosen);
            bump(g_counters.selector_requests);
        }
    }
    if (kind != SEL_NONE && st.sel.kind == SEL_NONE) {
        st.reopen_at = now + REOPEN_QUIET_MS;
        st.guard_until = now + RELEASE_GUARD_MS;
    }
}

void read_edges(uint32_t epoch, seat_edges out[SEATS])
{
    memset(out, 0, sizeof(seat_edges) * SEATS);
    const bool new_epoch = epoch != g.cursor_epoch;
    g.cursor_epoch = epoch;
    for (uint32_t s = 0; s < ACTIVE; ++s) {
        edges::edge_delta d;
        if (edges::since(g.cursor, s + 1, &d) && !d.reseeded && !d.overrun && !new_epoch) {
            out[s].pressed = d.act_pressed;
            out[s].repeated = d.act_repeated;
            out[s].held = d.act_held;
        }
    }
}

targets::action_rules rules_for(const menu::entry& pending)
{
    targets::action_rules ar = {};
    ar.action = pending.action;
    ar.groups = pending.groups;
    ar.revive = (pending.flags & menu::EF_REVIVE) != 0 ? 1 : 0;
    if (pending.action < menu::ACTION_CAP && g_tables.actions[pending.action].present != 0) {
        ar.rules = g_tables.actions[pending.action].rules;
    }
    return ar;
}

void forget_candidates(uint32_t s)
{
    targets::candidate_list& l = g_cands[s];
    memset(l.first, 0, sizeof(l.first));
    memset(l.count, 0, sizeof(l.count));
    memset(l.selectable, 0, sizeof(l.selectable));
    l.used = 0;
    l.truncated = 0;
}

void pick(uint32_t s, const session_record& rec, uint64_t now, bool force)
{
    seat_rt& st = g_seat[s];
    if (st.sm.model.mode != menu::MODE_TARGET || (!force && now < st.next_pick)) {
        return;
    }
    st.next_pick = now + PICK_EVERY_MS;
    const sources src = live_sources();
    if (src.picker(s, rec, &g_picker) != targets::PICK_OK) {
        return;  // keep what the seat was shown; a submit re-checks everything on the game thread
    }
    targets::build_candidates(g_picker, rules_for(st.sm.model.pending), &g_cands[s]);
    targets::sync(&st.sm.model, &st.cursor, g_cands[s]);
    bump(g_counters.picks);
}

void refresh_seat(uint32_t s, const session_record& rec, uint64_t now, bool force)
{
    seat_rt& st = g_seat[s];
    if (!force && now < st.next_refresh) {
        return;
    }
    st.next_refresh = now + SEAT_EVERY_MS;
    const seat_entry& e = rec.seat[s];
    const uint8_t owner = submit::summon_owner() == e.member ? 1 : 0;
    menu::seat_state ss;
    const sources src = live_sources();
    if (src.seat(s, e, owner, &ss) != menu::SAMPLE_OK) {
        return;  // the old pages stay
    }
    menu::commit_result cr;
    const uint8_t was = st.sm.model.mode;
    menu::seat_menu_refresh(&st.sm, g_tables, g_shared, ss, &cr);
    bump(g_counters.seat_refreshes);
    if (st.sm.model.mode == menu::MODE_TARGET) {
        targets::sync(&st.sm.model, &st.cursor, g_cands[s]);  // the pending action's data may have changed
    } else if (was == menu::MODE_TARGET) {
        targets::cursor_reset(&st.cursor);
    }
}

// A model event: its sound, and the request it asks for.
void handle(uint32_t s, const menu::menu_event& e, uint64_t now)
{
    if (e.kind == menu::EV_IGNORED) {
        return;
    }
    seat_rt& st = g_seat[s];
    const int32_t r = submit::handle_event(s, &st.sm, e, g_cands[s], now);
    if (e.kind == menu::EV_SUBMIT || e.kind == menu::EV_TOGGLE_GAMBITS) {
        st.last_submit = r;
        ++st.submits;
        bump(g_counters.submits);
    }
}

// ---- why a guest's battle-menu press did not open the menu (verbose log, PLAYTEST-1 M2) ----------------------------
// One line per refused press (presses are edges, so this is press-rate, never per beat), naming the gate.
bool menu_pressed(const seat_edges& e)
{
    return (e.pressed & (1u << ILC_ACTION_BATTLE_MENU)) != 0;
}

void note_refused(uint32_t s, const char* gate)
{
    log::line l;
    l.s("guest battle menu: Player ").u(s + 1).s(" press refused: ").s(gate);
    log::post(log::INFO, l, log::VERBOSE);
}

// The gates before the seats run (frontend-wide): every guest that pressed is told the same gate.
void note_refused_all(const seat_edges* edges, const char* gate)
{
    if (!log::verbose()) {
        return;  // the line would be dropped anyway
    }
    for (uint32_t s = 1; s < ACTIVE; ++s) {
        if (menu_pressed(edges[s])) {
            note_refused(s, gate);
        }
    }
}

// The first seat gate of run_seat that fails, for a guest whose press found the menu closed.
const char* seat_gate(const seat_entry& e, bool hidden, bool control_ok)
{
    if ((e.flags & session::SEATF_IN_SESSION) == 0) {
        return "not in session (no controller and not the keyboard seat, or not joined)";
    }
    if ((e.flags & session::SEATF_MANUAL) == 0) {
        return "not under manual control (parked or AI)";
    }
    if ((e.flags & session::SEATF_BENCHED) != 0) {
        return "its character is benched";
    }
    if ((e.flags & session::SEATF_ACTOR) == 0 || e.member == targets::MEMBER_NONE || e.handle == 0) {
        return "no character actor bound (not in the active party, or the actor handle reads 0)";
    }
    if (hidden) {
        return "hidden (menus suspended, party recovery, or its window not in the presented frame)";
    }
    if ((e.flags & session::SEATF_MENU) == 0) {
        return "menus not allowed now (town, native menu, modal, chocobo or no control)";
    }
    if (!control_ok) {
        return "the game does not give control (FACT_CONTROL clear)";
    }
    return nullptr;
}

// One seat's beat. Returns the seat (1..) when it asks for the native Party screen, else 0.
// confirm_open: a guest's CONFIRM found nothing to use (interaction::take_menu_opens): it opens the menu like the menu
// button, only while the menu is closed and no selector is up (never a close).
uint8_t run_seat(uint32_t s, const session_record& rec, const seat_edges& edges, bool hidden, bool control_ok,
                 uint64_t now, bool confirm_open = false)
{
    seat_rt& st = g_seat[s];
    const seat_entry& e = rec.seat[s];
    const bool in_session = s == 0 || (e.flags & session::SEATF_IN_SESSION) != 0;
    const bool bound = in_session && (s == 0 || (e.flags & session::SEATF_MANUAL) != 0) &&
                       (e.flags & session::SEATF_ACTOR) != 0 && (e.flags & session::SEATF_BENCHED) == 0 &&
                       e.member != targets::MEMBER_NONE && e.handle != 0;
    if (!bound || (st.bound && (st.member != e.member || st.handle != e.handle))) {
        hard_close(s);  // another character, or none: the guest starts again from its new character's root
    }
    st.bound = bound ? 1 : 0;
    st.member = e.member;
    st.handle = e.handle;
    st.eligible = bound && (e.flags & session::SEATF_MENU) != 0 && control_ok ? 1 : 0;
    st.last_commands = 0;
    const bool fallback = s != 0 && confirm_open && st.sm.model.mode == menu::MODE_CLOSED && st.sel.kind == SEL_NONE;
    const bool guest_press = s != 0 && (menu_pressed(edges) || fallback) && st.sm.model.mode == menu::MODE_CLOSED;
    if (guest_press && (!bound || hidden || st.eligible == 0)) {
        const char* gate = seat_gate(e, hidden, control_ok);
        note_refused(s, gate != nullptr ? gate : "not bound");
    }
    if (!bound || hidden) {
        return 0;  // a suspended menu keeps its place and takes no input
    }
    if (st.sel.kind != SEL_NONE) {
        if (guest_press && st.eligible != 0) {
            note_refused(s, "its character selector is open");
        }
        // The selector is a rootless list: up/down, confirm, cancel (not for the KO recovery), the menu button.
        const uint32_t cmds = commands(s == 0, menu::MODE_TARGET, false, edges);
        st.last_commands = cmds;
        if (st.eligible != 0 || st.sel.kind == SEL_KO) {
            run_selector(s, rec, cmds, now);
        }
        return 0;
    }
    const uint8_t mode = st.sm.model.mode;
    const bool prompt = s == 0 && firewall::p1_prompt_ready();
    uint32_t cmds = commands(s == 0, mode, prompt, edges);
    if (fallback && mode == menu::MODE_CLOSED) {
        cmds |= CMD_OPEN;
    }
    st.last_commands = cmds;
    menu::menu_model* m = &st.sm.model;
    if (mode == menu::MODE_CLOSED) {
        const bool may_open = st.eligible != 0 && now >= st.reopen_at && !(s == 0 && g.p1_down);
        if (guest_press && st.eligible != 0 && now < st.reopen_at) {
            note_refused(s, "the reopen delay after a close");
        }
        if ((cmds & CMD_OPEN) != 0 && may_open) {
            refresh_seat(s, rec, now, true);  // the pages of the character as it is now
            const menu::menu_event ev = menu::model_open(m);
            if (ev.kind == menu::EV_OPENED) {
                ++st.opens;
                bump(g_counters.opens);
                handle(s, ev, now);
            }
        } else if ((cmds & CMD_SELECT) != 0 && may_open && alive_ok(e)) {
            // Player 1's Change Leader or a guest's Change Character: the live party, starting on its own character.
            fill_selector(s, rec, s == 0 ? SEL_LEADER : SEL_CHARACTER, now, true);
            if (st.sel.kind != SEL_NONE && st.sel.count > 0) {
                bump(g_counters.selector_opens);
                submit::play(s, submit::sound_for(menu::EV_OPENED));
            } else {
                selector_close(&st.sel);
            }
        }
        // A guest's PARTY opens the native Party menu only while guests may (settings guest_pause); P1's always does.
        const bool party_allowed = s == 0 || options::guest_pause();
        return (cmds & CMD_PARTY) != 0 && party_allowed ? static_cast<uint8_t>(s + 1) : 0;
    }
    if (st.eligible == 0) {
        return 0;  // a native screen is opening or control is gone: the menu waits as it is
    }
    refresh_seat(s, rec, now, false);
    pick(s, rec, now, false);
    if ((cmds & CMD_CLOSE) != 0) {
        handle(s, menu::model_close(m), now);
    } else {
        if (m->mode == menu::MODE_LIST) {
            if ((cmds & CMD_PAGE_UP) != 0) {
                handle(s, menu::model_navigate_page(m, -1, PAGE_SIZE), now);
            } else if ((cmds & CMD_PAGE_DOWN) != 0) {
                handle(s, menu::model_navigate_page(m, 1, PAGE_SIZE), now);
            } else if ((cmds & CMD_UP) != 0) {
                handle(s, menu::model_navigate(m, -1, true), now);
            } else if ((cmds & CMD_DOWN) != 0) {
                handle(s, menu::model_navigate(m, 1, true), now);
            }
        } else if (m->mode == menu::MODE_TARGET) {
            if ((cmds & (CMD_UP | CMD_DOWN)) != 0) {
                handle(s, menu::model_navigate(m, (cmds & CMD_UP) != 0 ? -1 : 1, true), now);
                targets::note_selection(*m, &st.cursor, g_cands[s]);
            }
            if ((cmds & (CMD_GROUP_PREV | CMD_GROUP_NEXT)) != 0) {
                const menu::menu_event ev = menu::model_cycle_group(m, (cmds & CMD_GROUP_PREV) != 0 ? -1 : 1,
                                                                    targets::available_groups(g_cands[s]));
                targets::sync(m, &st.cursor, g_cands[s]);
                targets::note_selection(*m, &st.cursor, g_cands[s]);
                handle(s, ev, now);
            }
        }
        if ((cmds & CMD_CONFIRM) != 0) {
            const menu::menu_event ev = menu::model_confirm(m);
            if (ev.kind == menu::EV_TARGET_OPENED) {
                targets::cursor_reset(&st.cursor);
                forget_candidates(s);     // never a list built for another action
                pick(s, rec, now, true);  // the candidates before the seat sees the page
            }
            handle(s, ev, now);
        }
        if ((cmds & CMD_BACK) != 0 && m->mode != menu::MODE_CLOSED) {
            const uint8_t was = m->mode;
            handle(s, menu::model_back(m), now);
            if (was == menu::MODE_TARGET) {
                targets::cursor_reset(&st.cursor);
            }
        }
    }
    if (m->mode == menu::MODE_CLOSED) {
        bump(g_counters.closes);
        st.reopen_at = now + REOPEN_QUIET_MS;
        st.guard_until = now + RELEASE_GUARD_MS;
        targets::cursor_reset(&st.cursor);
    }
    return 0;
}

bool open_or_guarded(uint32_t s, uint64_t now)
{
    return busy(s) || now < g_seat[s].guard_until;
}

// Open (or guarded) and on screen: a menu whose window the overlay left out of the last presented frame takes no
// input, so it neither owns the directions nor blocks Player 1's PARTY (presenter.h).
bool shown_open(uint32_t s, uint64_t now)
{
    return open_or_guarded(s, now) && (!busy(s) || presenter::seat_shown(s));
}

void post(const firewall::menu_report& m)
{
    g.report = m;
    g.report_epoch = g.epoch;
    firewall::post_menus(m);
}

// Nothing may own the battle UI this beat: every menu closes, the stock menus are P1's, no pause. `base` carries only
// what needs no drawn menu (a native Quickening chain: passthrough and the guests' chain buttons).
void idle(bool rec_ok, const session_record& rec, uint64_t now, const firewall::menu_report& base = {})
{
    (void)now;
    close_all();
    party_cancel(&g.party, PRR_CANCEL_GATE);
    suspension_clear(&g.susp);
    memset(&g.rec, 0, sizeof(g.rec));
    g.recovering = false;
    g.owns_p1 = false;
    g.p1_down = false;
    g.p1_ko = false;
    g.ko_live = false;
    g.p1_out_ms = 0;
    g.wait_requested = false;
    g.interaction_mask = 0;
    g.quickening = base.quickening;
    post(base);
    const uint32_t epoch = rec_ok ? rec.head.epoch : 0;
    interaction::set_menu_seats(epoch, 0);
    if (rec_ok && epoch != 0) {
        wait::publish(epoch, rec.head.generation, false, now);
    } else {
        wait::withdraw();
    }
}

// Player 1's menu on the overlay, once per beat (rules.h p1_arm_step): armed, P1's battle menu, leader select and KO
// selector are co-op menus the overlay draws; disarmed, P1 is on the game's own frontends (the session's P1-native
// switch on). `layer`: the menu layer runs this beat; `native_up`: a stock frontend or the pause screen is up;
// `vanilla_up`: P1's own vanilla battle frontend is up (while armed only a race opens it: vanilla wins, held).
overlay::live_streak live_streak_now(uint64_t now)
{
    const int32_t forced = __atomic_load_n(&g_test_live, __ATOMIC_ACQUIRE);
    if (forced < 0) {
        return overlay::live_track(now);
    }
    // Host tests: the same streak rule on a forced value, or on the presenter's frames (mode 2: a presented frame within
    // overlay::LIVE_MS stands for an overlay frame, as OV-01 stamps both).
    const presenter::run_view r = presenter::run();
    // Mode 2: a frame of an unbroken run within LIVE_MS (a frame reached and not presented, as a menu hides the HUD,
    // breaks the run: overlay_live's field gate is off at once there).
    const bool on = forced == 1 || (forced == 2 && r.frames != 0 && r.last_ms != 0 && now >= r.last_ms &&
                                    now - r.last_ms <= overlay::LIVE_MS);
    if (on) {
        if (g_test_streak.since == 0 || g_test_streak.last == 0 || now < g_test_streak.last ||
            now - g_test_streak.last > overlay::LIVE_MS) {
            g_test_streak.since = now != 0 ? now : 1;
        }
        g_test_streak.last = now != 0 ? now : 1;
    } else if (g_test_streak.last == 0 || now < g_test_streak.last || now - g_test_streak.last > overlay::LIVE_MS) {
        g_test_streak.since = 0;
    }
    return g_test_streak;
}

// race: P1's own vanilla battle frontend came up this beat although the report the firewall applied last beat had the
// gate refusing it (rules.h). seat0_busy: seat 0 has a co-op window up (a refused frame then is a give-back).
void p1_owner(uint64_t now, bool layer, bool native_up, bool race = false, bool seat0_busy = false)
{
    if (__atomic_load_n(&g_arming, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    const presenter::run_view r = presenter::run();
    p1_arm_in in = {};
    in.wanted = 1;   // no settings field yet: Player 1's menu is always wanted on the overlay
    in.live = presenter::live(now) ? 1 : 0;
    in.layer = layer ? 1 : 0;
    in.native_up = native_up ? 1 : 0;
    in.vanilla_race = race ? 1 : 0;
    const uint64_t givebacks = __atomic_load_n(&g_counters.p1_givebacks, __ATOMIC_RELAXED);
    in.gave_back = givebacks != g_givebacks_seen ? 1 : 0;
    g_givebacks_seen = givebacks;
    // A refused frame while P1's co-op window is up is an overlay failure: a give-back (escalating), never a race.
    if (r.failed_ms != 0 && r.failed_ms != g_failed_seen) {
        if (g_arm.armed != 0 && seat0_busy) {
            in.gave_back = 1;
        }
        g_failed_seen = r.failed_ms;
    }
    in.drawn_ok = r.gpu_drawn_ms != 0 && r.gpu_drawn_ms > r.failed_ms ? 1 : 0;
    // overlay_live and its streak (item 7: arming follows the blocker's one switch, with its hysteresis).
    const overlay::live_streak ls = live_streak_now(now);
    in.live_since = ls.since;
    in.live_last = ls.last;
    in.failed_ms = r.failed_ms;
    const uint8_t ev = p1_arm_step(&g_arm, in, now);
    if (ev == PA_DISARMED || ev == PA_HELD || ev == PA_LATCHED) {
        // A re-arm needs a fresh 250 ms of overlay_live, whatever disarmed P1 (the presenter, the layer, a hold).
        overlay::live_restart();
        g_test_streak = overlay::live_streak{};
    }
    session::set_p1_native(g_arm.armed == 0);
    if (ev == PA_NONE || (g_arm_logged & (1u << ev)) != 0) {
        return;
    }
    g_arm_logged |= 1u << ev;
    log::line l;
    switch (ev) {
    case PA_ARMED:
        l.s("P1 menu: on the overlay after ").u(now - ls.since).s(" ms live (first time this launch)");
        log::post(log::INFO, l);
        break;
    case PA_DISARMED:
        l.s("P1 menu: back on the vanilla menu (first time this launch): presenter ").s(in.live != 0 ? "live" : "not live")
            .s(", layer ").s(layer ? "runs" : "idle").s(", overlay live ")
            .s(ls.last != 0 && now >= ls.last && now - ls.last <= P1_LIVE_HOLD_MS ? "yes" : "no").s(", refused frame ")
            .s(r.failed_ms != 0 && now >= r.failed_ms && now - r.failed_ms <= P1_FAILED_MS ? "recent" : "none");
        log::post(log::INFO, l);
        break;
    case PA_LATCHED:
        l.s("P1 menu: Player 1's window was given back ").u(g_arm.holds)
            .s(" times; Player 1 stays on the vanilla menu for the rest of this launch");
        log::post(log::WARNING, l);
        break;
    default:
        l.s("P1 menu: Player 1's window was given back (").s(race ? "the vanilla battle menu won a race" : "left out")
            .s("); vanilla menu for ").u((g_arm.hold_until - now) / 1000).s(" s (first time this launch)");
        log::post(log::WARNING, l);
        break;
    }
}

// The presenter died while the menus are suspended for a native screen (or one is up and may still be adopted): the
// menus keep their models and the suspension machine keeps running, but nothing is owned this beat: no P1 frontend,
// no input, no Party request, no Wait. A suspension that resumes (or a screen that closes unadopted) with nothing
// drawn yet ends like any presenter loss: every menu closes.
void hold(const session_record& rec, uint64_t now, const firewall::menu_report& base, const screen_in& si)
{
    switch (suspension_step(&g.susp, si, now)) {
    case SE_SUSPENDED: bump(g_counters.suspended); break;
    case SE_RESUMED: bump(g_counters.resumed); break;
    case SE_ABORTED:
        bump(g_counters.aborted);
        close_all();
        break;
    default: break;
    }
    if (g.susp.suspended == 0 && si.native_open == 0) {
        idle(true, rec, now, base);
        return;
    }
    g.recovering = false;
    g.owns_p1 = false;
    g.p1_down = false;
    g.p1_ko = false;
    g.ko_live = false;
    g.wait_requested = false;
    g.interaction_mask = 0;
    g.quickening = base.quickening;
    firewall::menu_report m = base;
    m.suppressed = 1;
    post(m);
    interaction::set_menu_seats(rec.head.epoch, 0);
    wait::publish(rec.head.epoch, rec.head.generation, false, now);
}

// A native Quickening chain: P1's buttons pass to the game (P1 presses its own link natively) and each guest whose
// character sits in party slot 1 or 2 presses QUICKENING for that slot's link (the button follows the native
// party slot; slot 1 = COMMAND, slot 2 = CONFIRM; P1's own pad then never fires that link). Slot 3 has no button.
firewall::menu_report quickening_report(const session_record& rec, bool chain)
{
    firewall::menu_report m = {};
    if (!chain) {
        return m;
    }
    m.quickening = firewall::QB_CHAIN;
    for (uint32_t s = 1; s < ACTIVE; ++s) {
        const seat_entry& e = rec.seat[s];
        if ((e.flags & session::SEATF_IN_SESSION) == 0 || (e.flags & session::SEATF_MANUAL) == 0 ||
            (e.flags & session::SEATF_BENCHED) != 0 || !has_character(e)) {
            continue;
        }
        if (e.party_slot == 1 && m.quickening_command_seat == 0) {
            m.quickening |= firewall::QB_COMMAND;
            m.quickening_command_seat = static_cast<uint8_t>(s + 1);
        } else if (e.party_slot == 2 && m.quickening_confirm_seat == 0) {
            m.quickening |= firewall::QB_CONFIRM;
            m.quickening_confirm_seat = static_cast<uint8_t>(s + 1);
        }
    }
    return m;
}

}  // namespace

uint32_t roles_of(const session_record& rec)
{
    uint32_t h = 0x811C9DC5u;  // NOTADDR: FNV-1a 32 offset basis
    for (uint32_t s = 0; s < SEATS; ++s) {
        const uint32_t f = rec.seat[s].flags &
                           (session::SEATF_IN_SESSION | session::SEATF_MANUAL | session::SEATF_BENCHED);
        const uint32_t words[2] = {f, rec.seat[s].member};
        for (uint32_t w : words) {
            for (uint32_t k = 0; k < 4; ++k) {
                h ^= (w >> (8 * k)) & 0xFF;
                h *= 0x01000193u;
            }
        }
    }
    return h;
}

// ---- co-op thread -------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    g = core{};
    g.image_base = image_base;
    for (uint32_t s = 0; s < SEATS; ++s) {
        memset(&g_seat[s], 0, sizeof(g_seat[s]));
        menu::seat_menu_reset(&g_seat[s].sm);
        selector_close(&g_seat[s].sel);
        targets::cursor_reset(&g_seat[s].cursor);
        memset(&g_cands[s], 0, sizeof(g_cands[s]));
    }
    g_arm = p1_arm{};
    g_givebacks_seen = __atomic_load_n(&g_counters.p1_givebacks, __ATOMIC_RELAXED);
    if (__atomic_load_n(&g_arming, __ATOMIC_ACQUIRE) != 0) {
        session::set_p1_native(true);   // P1 starts on the vanilla menu; the overlay earns it (p1_owner)
    }
    g.started = true;
}

void step(uint64_t now)
{
    if (!g.started) {
        return;
    }
    bump(g_counters.steps);
    session_record rec;
    const bool rec_ok = session::read(&rec);
    const uint32_t epoch = rec_ok ? rec.head.epoch : 0;
    seat_edges edges[SEATS];
    read_edges(epoch, edges);
    // Guests' CONFIRM presses that found nothing to use last beat (interaction runs after this layer): taken every beat,
    // used below only if the layer runs, so a press never opens a menu late.
    const uint32_t menu_opens = interaction::take_menu_opens(epoch);
    // The firewall applied LAST beat's report while this beat's edges were pressed. Unless that report already gave
    // P1 to the co-op menus (p1_menus), P1's presses reached the game too (CONFIRM may be opening the vanilla battle
    // menu right now), so seat 0 takes none of them this beat: on the beat P1 arms, and on any beat after the layer
    // gave P1 back, P1's buttons are the game's alone. The PARTY path below reads last beat's owns_p1 the same way.
    const bool p1_owned_last = epoch != 0 && g.report_epoch == epoch && g.report.p1_menus != 0 &&
                               g.report.presenter_live != 0;
    if (epoch != g.epoch) {
        // A new world (or the stretch between worlds): nothing prepared before it carries over.
        close_all();
        party_cancel(&g.party, PRR_CANCEL_GATE);
        suspension_clear(&g.susp);
        g.epoch = epoch;
        g.next_tables = 0;
        g.shared_ok = false;
        g.p1_out_ms = 0;
    }
    const bool live = rec_ok && epoch != 0 && patch::subsystem_live(ILC_SUBSYS_FIREWALL_MENUS) &&
                      control::current_state() == control::STATE_ON && submit::enabled();
    if (!live || (rec.head.flags & session::GATE_WORLD) == 0) {
        note_refused_all(edges, !rec_ok || epoch == 0 ? "no session record"
                                : !live ? "the co-op menus are not live (FIREWALL_MENUS, co-op off, or submit off)"
                                        : "no world");
        p1_owner(now, false, false);
        idle(rec_ok, rec, now);
        return;
    }
    const sources src = live_sources();
    const uint32_t screen = src.screen(rec);
    firewall::menu_report qk = quickening_report(rec, (screen & SCREEN_QUICKENING) != 0);
    // P1's battle menu, leader select or command tree up is P1's native screen, never one the guests' edges drive (the
    // firewall's guest pulses), in every report this beat posts. Whether P1 is on the game's own frontends (p1_native)
    // or not: while the co-op menus own P1 the gate refuses those frontends, so one that is up is P1's own (P1 native,
    // a give-back, or the stock KO path with nobody free to offer).
    qk.p1_battle = (screen & SCREEN_P1_BATTLE) != 0 ? 1 : 0;
    // A race (rules.h): P1's own battle frontend came up on this beat while the report the firewall applied last beat
    // had the gate refusing it (the co-op menus owned P1; for the leader select, the leader gate too). A stock frontend
    // that opened while the layer did not own P1 (the KO path with nobody free, a promotion) is not one.
    const bool vanilla_now = qk.p1_battle != 0;
    const bool vanilla_race = vanilla_now && !g.vanilla_prev && p1_owned_last &&
                              ((screen & SCREEN_P1_LEADER) == 0 || g.report.leader_gate != 0);
    g.vanilla_prev = vanilla_now;
    const bool paused = (screen & SCREEN_PAUSED) != 0;
    const bool tree = (screen & SCREEN_TREE) != 0;
    // ... nor an external screen for the guests' menus (it would suspend them every time P1 picks an action).
    const bool frontend_up = (rec.facts & session::FACT_MENU) != 0 && qk.p1_battle == 0;
    const bool native_open = frontend_up || paused;
    const bool modal = (rec.facts & session::FACT_MODAL) != 0;
    const bool control_ok = (rec.facts & session::FACT_CONTROL) != 0;
    const bool settling = rec.phase == session::PHASE_NO_WORLD || rec.phase == session::PHASE_LOADING ||
                          rec.phase == session::PHASE_CUTSCENE;
    const bool down = (rec.head.flags & session::GATE_PARTY_DOWN) != 0 || rec.phase == session::PHASE_PARTY_DOWN;
    const uint32_t roles = roles_of(rec);
    // The co-op menus may own anything only while they are drawn (presenter.h). Without the overlay every menu stays
    // closed, nothing is requested and P1 keeps every stock frontend; a Quickening chain still passes through. The
    // one exception (rules.h, presenter_mode_of): menus suspended for a native screen that stopped the HUD keep their
    // place, owning nothing, until the screen closes and the layer is drawn again.
    g.presenter_live = presenter::live(now);
    if (!g.presenter_live) {
        presenter_in pin = {};
        pin.suspended = g.susp.suspended;
        pin.source = g.susp.source;
        pin.observed_open = g.susp.observed_open;
        pin.native_open = native_open ? 1 : 0;
        pin.settling = settling ? 1 : 0;
        pin.down = down || g.recovering ? 1 : 0;
        pin.party_busy = g.party.state != PR_IDLE ? 1 : 0;
        note_refused_all(edges, "the co-op HUD layer was not presented in the last 250 ms (presenter not live)");
        p1_owner(now, false, native_open);
        if (presenter_mode_of(pin) == PM_HOLD) {
            screen_in si = {};
            si.native_open = native_open ? 1 : 0;
            si.modal = modal ? 1 : 0;
            si.control = control_ok ? 1 : 0;
            si.settling = settling ? 1 : 0;
            si.roles = roles;
            hold(rec, now, qk, si);
            return;
        }
        idle(rec_ok, rec, now, qk);
        return;
    }
    if (!g.tables_ready || now >= g.next_tables) {
        g.next_tables = now + TABLES_EVERY_MS;
        bump(g_counters.table_reads);
        g.tables_status = src.tables(&g_tables_next);
        if (g.tables_status == menu::TABLES_OK) {
            memcpy(&g_tables, &g_tables_next, sizeof(g_tables));
            g.tables_ready = true;
        } else {
            bump(g_counters.table_failures);  // the committed tables (if any) stay
        }
    }
    if (!g.tables_ready) {
        note_refused_all(edges, "the menu tables could not be read");
        p1_owner(now, false, native_open);
        idle(rec_ok, rec, now, qk);  // no catalog can be built: the stock menus stay P1's
        return;
    }
    // Player 1's menu on the overlay: armed after a run of presented frames, never under an open stock frontend
    // (FACT_MENU, P1's own battle menu included) or the pause screen; disarmed, P1's co-op menu closes at once and
    // P1's buttons are the game's from this beat.
    // P1's own vanilla battle frontend up while armed is a give-back: vanilla wins for seat 0 (the guests keep theirs).
    p1_owner(now, true, (rec.facts & session::FACT_MENU) != 0 || paused, vanilla_race, busy(0));
    if (p1_native() && busy(0)) {
        hard_close(0);
    }
    if (!g.shared_ok || now >= g.next_shared) {
        g.next_shared = now + SHARED_EVERY_MS;
        menu::shared_state next;
        if (src.shared(&next) == menu::SAMPLE_OK) {
            g_shared = next;
            g.shared_ok = true;
        }
    }

    g.roles = roles;
    const uint8_t open_before = open_mask();

    // The party-down recovery: all native.
    const bool recovering = recovery_step(&g.rec, down, native_open, now);
    if (recovering && !g.recovering) {
        bump(g_counters.recoveries);
        close_all();
        party_cancel(&g.party, PRR_CANCEL_GATE);
        suspension_clear(&g.susp);
    }
    g.recovering = recovering;

    uint8_t pulse_seat = 0;
    if (!recovering) {
        if (g.party.state != PR_IDLE) {
            party_in pi = {};
            pi.native_open = frontend_up ? 1 : 0;
            pi.paused = paused ? 1 : 0;
            pi.modal = modal ? 1 : 0;
            pi.gate_ok = control_ok && !settling ? 1 : 0;
            pi.tree_active = tree ? 1 : 0;
            pi.wait_owned = wait::owned() ? 1 : 0;
            const party_out po = party_step(&g.party, pi, now);
            if (po.pulse != 0) {
                pulse_seat = g.party.seat;
                bump(g_counters.party_pulses);
            }
            if (po.result == PRR_OPENED) {
                bump(g_counters.party_opened);
                g.susp.observed_open = 1;  // the native Party screen: the suspension follows it from here
            } else if (po.result != PRR_NONE) {
                bump(g_counters.party_cancelled);
                if (g.susp.suspended != 0 && g.susp.source == SRC_PARTY && g.susp.observed_open == 0) {
                    suspension_clear(&g.susp);  // nothing opened: the menus come back as they were
                }
            }
        }
        screen_in si = {};
        si.native_open = native_open ? 1 : 0;
        si.modal = modal ? 1 : 0;
        si.control = control_ok ? 1 : 0;
        si.settling = settling ? 1 : 0;
        si.party_transfer = g.party.state != PR_IDLE ? 1 : 0;
        si.roles = roles;
        switch (suspension_step(&g.susp, si, now)) {
        case SE_SUSPENDED: bump(g_counters.suspended); break;
        case SE_RESUMED: bump(g_counters.resumed); break;
        case SE_ABORTED:
            bump(g_counters.aborted);
            close_all();
            party_cancel(&g.party, PRR_CANCEL_GATE);
            break;
        default: break;
        }
    }
    const bool hidden = recovering || g.susp.suspended != 0;

    // Player 1's character down (the party not): the KO recovery. The co-op selector offers the living characters
    // nobody holds; while it has one to offer (and can be shown) the stock leader select stays shut, otherwise it runs.
    // P1's own seat character down while someone else already leads (the game promoted a member): no co-op selector,
    // and P1's frontends are the game's (owns_p1 below), so P1 is never gated on a fallen character.
    const seat_entry& p1e = rec.seat[0];
    g.p1_down = !recovering && has_character(p1e) && (p1e.flags & session::SEATF_ALIVE_KNOWN) != 0 &&
                (p1e.flags & session::SEATF_ALIVE) == 0;
    g.p1_ko = g.p1_down && rec.leader == p1e.member;
    seat_rt& p1s = g_seat[0];

    // Player 1's own window (menu, leader or KO selector) left out of a presented frame, or lost: an invisible menu
    // must not hold P1's buttons, so it closes and the stock battle menu and leader select are P1's from this beat.
    // That holds until a presented frame shows seat 0 again and P1_GIVEBACK_MS has passed since the last give-back.
    if (busy(0) && (!presenter::seat_shown(0) || presenter::seat_lost(0, now))) {
        hard_close(0);
        g.p1_out_ms = now != 0 ? now : 1;
        bump(g_counters.p1_givebacks);
    }
    if (g.p1_out_ms != 0 && presenter::seat_shown(0) && now >= g.p1_out_ms && now - g.p1_out_ms >= P1_GIVEBACK_MS) {
        g.p1_out_ms = 0;
    }
    const bool p1_out = g.p1_out_ms != 0 || p1_native();   // P1 on the game's own frontends: never a co-op menu
    if (g.p1_down && is_open(0)) {
        menu::model_close(&p1s.sm.model);  // no battle menu for a fallen character
        bump(g_counters.hard_closes);
    }
    if (g.p1_ko && !p1_out) {
        if (p1s.sel.kind != SEL_KO && now >= p1s.sel_next) {
            // Opened only with a character to offer; tried again every PICK_EVERY_MS until one is free.
            fill_selector(0, rec, SEL_KO, now, true);
            if (p1s.sel.kind == SEL_KO && p1s.sel.count > 0) {
                bump(g_counters.ko_opens);
            } else {
                selector_close(&p1s.sel);
            }
        }
    } else if (p1s.sel.kind == SEL_KO || (g.p1_down && p1s.sel.kind != SEL_NONE)) {
        selector_close(&p1s.sel);  // a living character leads again, or P1's fallen character is not the leader
    }

    uint8_t party_seat = 0;
    for (uint32_t s = 0; s < ACTIVE; ++s) {
        // Per seat (presenter.h): a menu whose window the overlay left out of its frames for over LIVE_MS closes; one
        // left out of the last frame keeps its place but takes no input (and counts as closed for Wait below).
        if (busy(s) && presenter::seat_lost(s, now)) {
            hard_close(s);
        }
        const bool unseen = busy(s) && !presenter::seat_shown(s);
        // P1's buttons are the stock frontends' meanwhile, or were last beat (see p1_owned_last).
        const bool given_back = s == 0 && (p1_out || !p1_owned_last);
        const uint8_t asked = run_seat(s, rec, edges[s], hidden || unseen || given_back, control_ok, now,
                                       s != 0 && (menu_opens & (1u << s)) != 0);
        if (asked != 0 && party_seat == 0) {
            party_seat = asked;
        }
    }
    for (uint32_t s = ACTIVE; s < SEATS; ++s) {
        hard_close(s);  // no seat beyond the cap ever has a menu
    }
    // A Party request: from a seat with a character and its own menu closed, in plain field play. P1's own PARTY
    // reaches the game natively unless a co-op menu is open while the co-op menus own P1 (then the firewall withholds
    // it and it is a request). While P1 is on the stock frontends (owns_p1 clear: the firewall applied last beat's
    // report, so last beat's value is the one that decided this press) it stays native, never a request.
    if (party_seat != 0 && !hidden && g.party.state == PR_IDLE && control_ok && !native_open && !modal && !settling &&
        rec.phase == session::PHASE_FIELD) {
        bool any_open = false;
        for (uint32_t s = 0; s < ACTIVE; ++s) {
            any_open = any_open || shown_open(s, now);
        }
        if ((party_seat != 1 || (any_open && g.owns_p1)) && party_begin(&g.party, party_seat, now)) {
            bump(g_counters.party_requests);
            suspension_begin(&g.susp, SRC_PARTY, roles, now);
        }
    }

    const bool hidden_now = recovering || g.susp.suspended != 0;
    const seat_entry& p1 = rec.seat[0];
    g.ko_live = g.p1_ko && !hidden_now && p1s.sel.kind == SEL_KO && p1s.sel.count > 0;
    // P1's own character down: the co-op layer owns P1 only while its KO selector is live (P1's character still
    // leads and a free living one is offered). Otherwise (nobody free, hidden, or a KO promotion: someone else leads)
    // the report's P1 part is empty: stock id 3, P1's buttons and the battle menu are the game's.
    g.owns_p1 = !recovering && !p1_out && (p1.flags & session::SEATF_ACTOR) != 0 &&
                p1.member != targets::MEMBER_NONE && (!g.p1_down || g.ko_live);
    firewall::menu_report m = qk;
    g.quickening = m.quickening;
    m.p1_menus = g.owns_p1 ? 1 : 0;
    m.presenter_live = 1;
    m.leader_gate = g.owns_p1 ? 1 : 0;   // the co-op leader selector (or the KO recovery) can be shown
    m.leader_pending = g.ko_live ? 1 : 0;
    if (!hidden_now) {
        m.leader_selector = !p1_out && selecting(0) ? 1 : 0;
        m.p1_open = !p1_out && shown_open(0, now) ? 1 : 0;
        m.p1_target = !p1_out && g_seat[0].sm.model.mode == menu::MODE_TARGET ? 1 : 0;
        for (uint32_t s = 1; s < ACTIVE; ++s) {
            m.guests_open = static_cast<uint8_t>(m.guests_open | (shown_open(s, now) ? 1u << s : 0u));
        }
    }
    m.party_request = g.party.state != PR_IDLE ? 1 : 0;
    // During a Party request the menus are hidden but P1's battle buttons stay withheld until the native screen is
    // seen (a held alias must not reach a stock menu during the transfer); after that it is a native screen.
    m.suppressed = recovering || (g.susp.suspended != 0 && !(g.susp.source == SRC_PARTY && m.party_request != 0)) ? 1
                                                                                                                 : 0;
    m.party_pulse = pulse_seat;
    post(m);

    // A guest's CONFIRM inside its open menu is a menu press, never an interaction.
    g.interaction_mask = static_cast<uint8_t>((open_before | open_mask()) & ~1u);
    interaction::set_menu_seats(epoch, g.interaction_mask);

    // The Wait rule.
    wait::seat_view views[wait::SEATS] = {};
    for (uint32_t s = 0; s < ACTIVE && s < wait::SEATS; ++s) {
        if (s == 0 && p1_native()) {
            continue;   // P1's own (stock) battle menu pauses as the game does: the rule counts the guests
        }
        const seat_entry& e = rec.seat[s];
        const bool in = s == 0 || ((e.flags & session::SEATF_IN_SESSION) != 0 && (e.flags & session::SEATF_MANUAL) != 0);
        if (!in) {
            continue;
        }
        if ((e.flags & session::SEATF_BENCHED) != 0) {
            views[s].role = wait::ROLE_BENCHED;
        } else if (g_seat[s].bound != 0 && (e.flags & session::SEATF_MENU) != 0 && alive_ok(e)) {
            views[s].role = wait::ROLE_PARTICIPANT;
        }
        views[s].open = busy(s) && presenter::seat_shown(s) ? 1 : 0;   // a menu nobody sees holds no Wait
    }
    g.wait_requested = (g.owns_p1 || p1_native()) && !hidden_now && g.party.state == PR_IDLE && rec.phase == session::PHASE_FIELD &&
                       control_ok && wait::pause_wanted(views, options::wait_pause());
    wait::publish(epoch, rec.head.generation, g.wait_requested, now);
}

void stop()
{
    if (!g.started) {
        return;
    }
    close_all();
    party_cancel(&g.party, PRR_CANCEL_GATE);
    suspension_clear(&g.susp);
    post(firewall::menu_report{});
    interaction::set_menu_seats(0, 0);
    wait::withdraw();
    if (__atomic_load_n(&g_arming, __ATOMIC_ACQUIRE) != 0) {
        g_arm = p1_arm{};
        session::set_p1_native(true);
    }
    g.started = false;
}

// ---- views --------------------------------------------------------------------------------------------------------
void read_seat(uint32_t seat, seat_view* out)
{
    memset(out, 0, sizeof(*out));
    if (seat >= SEATS) {
        return;
    }
    const seat_rt& st = g_seat[seat];
    const menu::menu_model& m = st.sm.model;
    out->mode = m.mode;
    out->eligible = st.eligible;
    out->member_known = st.sm.member_known ? 1 : 0;
    out->group = m.target_group;
    out->member = st.member;
    out->selector_kind = st.sel.kind;
    out->selector_index = st.sel.index;
    out->target_index = m.target_index;
    out->target_count = m.target_count;
    out->page = m.mode != menu::MODE_CLOSED && m.depth > 0 ? m.stack[m.depth - 1].page : uint16_t{menu::PG_NONE};
    out->cursor = m.mode != menu::MODE_CLOSED && m.depth > 0 ? m.stack[m.depth - 1].index : 0;
    out->last_commands = st.last_commands;
    out->last_submit = st.last_submit;
    out->opens = st.opens;
    out->submits = st.submits;
    out->reopen_at = st.reopen_at;
    out->guard_until = st.guard_until;
}

void read_state(state_view* out)
{
    memset(out, 0, sizeof(*out));
    out->owns_p1 = g.owns_p1 ? 1 : 0;
    out->recovery = g.recovering ? 1 : 0;
    out->tables_ready = g.tables_ready ? 1 : 0;
    out->wait_requested = g.wait_requested ? 1 : 0;
    out->party_state = g.party.state;
    out->party_seat = g.party.seat;
    out->party_last = g.party.last;
    out->interaction_mask = g.interaction_mask;
    out->susp = g.susp;
    out->epoch = g.epoch;
    out->roles = g.roles;
    out->tables_status = g.tables_status;
    out->presenter_live = g.presenter_live ? 1 : 0;
    out->p1_ko = g.p1_ko ? 1 : 0;
    out->ko_live = g.ko_live ? 1 : 0;
    out->quickening = g.quickening;
    static_assert(sizeof(out->report) == sizeof(g.report), "report copy");
    memcpy(out->report, &g.report, sizeof(out->report));
}

const targets::candidate_list* candidates(uint32_t seat)
{
    return seat < SEATS ? &g_cands[seat] : nullptr;
}

const selector* selector_of(uint32_t seat)
{
    return seat < SEATS ? &g_seat[seat].sel : nullptr;
}

const menu::seat_menu* seat_menu_of(uint32_t seat)
{
    return seat < SEATS ? &g_seat[seat].sm : nullptr;
}

void set_p1_native(bool on)
{
    session::set_p1_native(on);   // one switch, owned by the session (it gates the guests' menus there too)
}

bool p1_native()
{
    return session::p1_native();
}

void set_p1_arming(bool on)
{
    __atomic_store_n(&g_arming, on ? 1u : 0u, __ATOMIC_RELEASE);
    g_arm = p1_arm{};
    g_givebacks_seen = __atomic_load_n(&g_counters.p1_givebacks, __ATOMIC_RELAXED);
}

bool p1_arming()
{
    return __atomic_load_n(&g_arming, __ATOMIC_ACQUIRE) != 0;
}

void read_p1_arm(p1_arm* out)
{
    *out = g_arm;
}

const counters* stats()
{
    return &g_counters;
}

// ---- host tests ------------------------------------------------------------------------------------------------
void test_set_sources(const sources* s)
{
    g_test_sources = s;
}

void test_set_image_base(uintptr_t image_base)
{
    g_test_image_base = image_base;
}

void test_reset()
{
    g = core{};
    for (uint32_t s = 0; s < SEATS; ++s) {
        memset(&g_seat[s], 0, sizeof(g_seat[s]));
        menu::seat_menu_reset(&g_seat[s].sm);
        selector_close(&g_seat[s].sel);
        targets::cursor_reset(&g_seat[s].cursor);
        memset(&g_cands[s], 0, sizeof(g_cands[s]));
    }
    presenter::test_reset();
    memset(&g_tables, 0, sizeof(g_tables));
    memset(&g_shared, 0, sizeof(g_shared));
    memset(const_cast<counters*>(&g_counters), 0, sizeof(g_counters));
    g_test_sources = nullptr;
    g_test_image_base = 0;
    __atomic_store_n(&g_arming, 0u, __ATOMIC_RELEASE);   // host tests: the switch is the test's unless it arms
    g_arm = p1_arm{};
    g_givebacks_seen = 0;
    g_failed_seen = 0;
    g_test_streak = overlay::live_streak{};
    __atomic_store_n(&g_test_live, -1, __ATOMIC_RELEASE);
    g_arm_logged = 0;
    session::set_p1_native(false);   // host tests: P1's co-op menu unless a test sets it
}

}  // namespace ilc::frontend

namespace ilc::frontend {
void test_set_overlay_live(int32_t state)
{
    __atomic_store_n(&g_test_live, state, __ATOMIC_RELEASE);
}
}  // namespace ilc::frontend
