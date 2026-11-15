// Ivalice Legacy co-op module, host tests only: exports for the Wait engine (src/wait) and the co-op battle menus at
// run time (src/frontend).
//
// The pure rules are passed through. The Wait engine runs against doubles of its four game functions (ctypes
// callbacks) or, in unicorn, against the hooked image twin. The frontend layer runs against doubles of its game
// readers: the tables the menu tests parsed, and the shared state, seat states, picker inputs and screen bits the
// test sets here.
#include <string.h>

#include "frontend/frontend.h"
#include "frontend/presenter.h"
#include "frontend/rules.h"
#include "interaction/interaction.h"
#include "menu/catalog.h"
#include "menu/live.h"
#include "menu/tables.h"
#include "session/session.h"
#include "targets/live.h"
#include "targets/targets.h"
#include "wait/wait.h"

using namespace ilc;

extern "C" void ILCT_MenuTablesCopy(menu::catalog_tables* out);  // testsupport/menu_tests.cpp

namespace {

menu::catalog_tables g_tables;
int32_t g_tables_status = menu::TABLES_MISSING;
menu::shared_state g_shared;
int32_t g_shared_status = menu::SAMPLE_NO_STATE;
menu::seat_state g_seat[frontend::SEATS];
int32_t g_seat_status[frontend::SEATS];
targets::picker_input g_picker[frontend::SEATS];
int32_t g_picker_status[frontend::SEATS];
uint32_t g_screen = 0;
uint32_t g_calls[5];  // tables, shared, seat, picker, screen

int32_t d_tables(menu::catalog_tables* out)
{
    ++g_calls[0];
    if (g_tables_status == menu::TABLES_OK) {
        memcpy(out, &g_tables, sizeof(*out));
    }
    return g_tables_status;
}

int32_t d_shared(menu::shared_state* out)
{
    ++g_calls[1];
    if (g_shared_status == menu::SAMPLE_OK) {
        *out = g_shared;
    }
    return g_shared_status;
}

int32_t d_seat(uint32_t seat, const session::seat_entry& e, uint8_t esper_owner, menu::seat_state* out)
{
    (void)e;
    ++g_calls[2];
    if (seat >= frontend::SEATS) {
        return menu::SAMPLE_NO_STATE;
    }
    if (g_seat_status[seat] == menu::SAMPLE_OK) {
        *out = g_seat[seat];
        out->esper_owner = esper_owner;
    }
    return g_seat_status[seat];
}

int32_t d_picker(uint32_t seat, const session::session_record& rec, targets::picker_input* out)
{
    (void)rec;
    ++g_calls[3];
    if (seat >= frontend::SEATS) {
        return targets::PICK_NO_ISSUER;
    }
    if (g_picker_status[seat] == targets::PICK_OK) {
        memcpy(out, &g_picker[seat], sizeof(*out));
    }
    return g_picker_status[seat];
}

uint32_t d_screen(const session::session_record& rec)
{
    (void)rec;
    ++g_calls[4];
    return g_screen;
}

const frontend::sources DOUBLES = {d_tables, d_shared, d_seat, d_picker, d_screen};

wait::game_fns g_wait_fns;

}  // namespace

extern "C" {

// ---- the Wait engine ------------------------------------------------------------------------------------------------
uint32_t ILCT_WaitSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(wait::seat_view), sizeof(wait::engine_in), sizeof(wait::engine_out),
                              sizeof(wait::request_record), sizeof(wait::counters), sizeof(wait::game_fns)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

int32_t ILCT_WaitPauseWanted(const wait::seat_view* seats) { return wait::pause_wanted(seats) ? 1 : 0; }

int32_t ILCT_WaitReasonOf(uint32_t id, uint32_t active) { return wait::reason_of(id, active != 0); }

void ILCT_WaitDecide(const wait::engine_in* in, wait::engine_out* out) { *out = wait::decide(*in); }

void ILCT_WaitPublish(uint32_t epoch, uint64_t generation, uint32_t requested, uint64_t now)
{
    wait::publish(epoch, generation, requested != 0, now);
}

void ILCT_WaitWithdraw() { wait::withdraw(); }

int32_t ILCT_WaitRecord(wait::request_record* out) { return wait::slot().read(*out) ? 1 : 0; }

void ILCT_WaitStep(uint32_t epoch) { wait::step(epoch); }

void ILCT_WaitStepDry() { wait::step_dry(); }

int32_t ILCT_WaitAttach(uint64_t base) { return wait::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }

int32_t ILCT_WaitOwned() { return wait::owned() ? 1 : 0; }

int32_t ILCT_WaitEnabled() { return wait::enabled() ? 1 : 0; }

void ILCT_WaitSetOwned(int32_t owned) { wait::test_set_owned(owned != 0); }

void ILCT_WaitForceEnabled(int32_t forced) { wait::test_force_enabled(forced); }

void ILCT_WaitSetImageBase(uint64_t base) { wait::test_set_image_base(static_cast<uintptr_t>(base)); }

void ILCT_WaitSetFns(const wait::game_fns* fns)
{
    if (fns == nullptr) {
        wait::test_set_game_fns(nullptr);
        return;
    }
    g_wait_fns = *fns;
    wait::test_set_game_fns(&g_wait_fns);
}

void ILCT_WaitReset() { wait::test_reset(); }

void ILCT_WaitLast(wait::engine_out* out) { *out = wait::test_last(); }

void ILCT_WaitStats(wait::counters* out) { memcpy(out, const_cast<const wait::counters*>(wait::stats()), sizeof(*out)); }

uint64_t ILCT_WaitOwnedAddress() { return reinterpret_cast<uint64_t>(wait::test_owned_address()); }

// ---- the frontend's pure rules -----------------------------------------------------------------------------------------
uint32_t ILCT_FeSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(frontend::seat_edges), sizeof(frontend::suspension), sizeof(frontend::screen_in),
                              sizeof(frontend::party_request), sizeof(frontend::party_in),
                              sizeof(frontend::party_out), sizeof(frontend::recovery), sizeof(frontend::seat_view),
                              sizeof(frontend::state_view), sizeof(frontend::counters),
                              sizeof(frontend::selector_member), sizeof(frontend::selector_in),
                              sizeof(frontend::selector)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

uint32_t ILCT_FeCommands(uint32_t player1, uint32_t mode, uint32_t prompt, const frontend::seat_edges* e)
{
    return frontend::commands(player1 != 0, static_cast<uint8_t>(mode), prompt != 0, *e);
}

void ILCT_FeSuspBegin(frontend::suspension* s, uint32_t source, uint32_t roles, uint64_t now)
{
    frontend::suspension_begin(s, static_cast<uint8_t>(source), roles, now);
}

int32_t ILCT_FeSuspStep(frontend::suspension* s, const frontend::screen_in* in, uint64_t now)
{
    return frontend::suspension_step(s, *in, now);
}

int32_t ILCT_FePartyBegin(frontend::party_request* p, uint32_t seat, uint64_t now)
{
    return frontend::party_begin(p, static_cast<uint8_t>(seat), now) ? 1 : 0;
}

void ILCT_FePartyStep(frontend::party_request* p, const frontend::party_in* in, uint64_t now, frontend::party_out* out)
{
    *out = frontend::party_step(p, *in, now);
}

int32_t ILCT_FeRecoveryStep(frontend::recovery* r, uint32_t down, uint32_t native_open, uint64_t now)
{
    return frontend::recovery_step(r, down != 0, native_open != 0, now) ? 1 : 0;
}

uint32_t ILCT_FeRoles(const session::session_record* rec) { return frontend::roles_of(*rec); }

uint32_t ILCT_FeSelectorBuild(frontend::selector* s, uint32_t kind, const frontend::selector_in* in)
{
    return frontend::selector_build(s, static_cast<uint8_t>(kind), *in);
}

int32_t ILCT_FeSelectorCommand(frontend::selector* s, uint32_t cmds, uint32_t* chosen)
{
    uint16_t c = 0;
    const int32_t r = frontend::selector_command(s, cmds, &c);
    *chosen = c;
    return r;
}

// ---- the presenter (the overlay's proof that the co-op menus are drawn) -------------------------------------------------
void ILCT_FePresenter(int32_t mode) { frontend::presenter::test_simulate(mode); }

void ILCT_FeNoteDrawn(uint64_t now) { frontend::presenter::note_drawn(now); }

int32_t ILCT_FePresenterLive(uint64_t now) { return frontend::presenter::live(now) ? 1 : 0; }

void ILCT_FePresenterReset() { frontend::presenter::test_reset(); }

void ILCT_FeNoteDropped(uint32_t mask, uint64_t now) { frontend::presenter::note_dropped(mask, now); }

int32_t ILCT_FeSeatShown(uint32_t seat) { return frontend::presenter::seat_shown(seat) ? 1 : 0; }

int32_t ILCT_FeSeatLost(uint32_t seat, uint64_t now) { return frontend::presenter::seat_lost(seat, now) ? 1 : 0; }

// ---- the frontend layer with doubles ----------------------------------------------------------------------------------
void ILCT_FeReset()
{
    frontend::test_reset();
    memset(&g_shared, 0, sizeof(g_shared));
    g_tables_status = menu::TABLES_MISSING;
    g_shared_status = menu::SAMPLE_NO_STATE;
    for (uint32_t s = 0; s < frontend::SEATS; ++s) {
        memset(&g_seat[s], 0, sizeof(g_seat[s]));
        g_seat_status[s] = menu::SAMPLE_NO_STATE;
        g_picker_status[s] = targets::PICK_NO_ISSUER;
    }
    g_screen = 0;
    memset(g_calls, 0, sizeof(g_calls));
}

void ILCT_FeUseDoubles(uint32_t on) { frontend::test_set_sources(on != 0 ? &DOUBLES : nullptr); }

void ILCT_FeSetImageBase(uint64_t base) { frontend::test_set_image_base(static_cast<uintptr_t>(base)); }

// The tables the menu tests parsed (ILCT_MenuTables*), with the status the double returns.
void ILCT_FeTablesFromMenu(int32_t status)
{
    ILCT_MenuTablesCopy(&g_tables);
    g_tables_status = status;
}

void ILCT_FeSetShared(const menu::shared_state* s, int32_t status)
{
    if (s != nullptr) {
        g_shared = *s;
    }
    g_shared_status = status;
}

void ILCT_FeSetSeatState(uint32_t seat, const menu::seat_state* s, int32_t status)
{
    if (seat < frontend::SEATS) {
        if (s != nullptr) {
            g_seat[seat] = *s;
        }
        g_seat_status[seat] = status;
    }
}

void ILCT_FeSetPicker(uint32_t seat, const targets::picker_input* in, int32_t status)
{
    if (seat < frontend::SEATS) {
        if (in != nullptr) {
            memcpy(&g_picker[seat], in, sizeof(*in));
        }
        g_picker_status[seat] = status;
    }
}

void ILCT_FeSetScreen(uint32_t bits) { g_screen = bits; }

void ILCT_FeCalls(uint32_t* out)
{
    memcpy(out, g_calls, sizeof(g_calls));
}

void ILCT_FeStart(uint64_t base, uint64_t now) { frontend::start(static_cast<uintptr_t>(base), now); }

void ILCT_FeStep(uint64_t now) { frontend::step(now); }

void ILCT_FeStop() { frontend::stop(); }

void ILCT_FeSeat(uint32_t seat, frontend::seat_view* out) { frontend::read_seat(seat, out); }

void ILCT_FeState(frontend::state_view* out) { frontend::read_state(out); }

void ILCT_FeCandidates(uint32_t seat, targets::candidate_list* out)
{
    const targets::candidate_list* l = frontend::candidates(seat);
    if (l != nullptr) {
        memcpy(out, l, sizeof(*out));
    }
}

void ILCT_FeSelector(uint32_t seat, frontend::selector* out)
{
    const frontend::selector* s = frontend::selector_of(seat);
    if (s != nullptr) {
        *out = *s;
    }
}

void ILCT_FeStats(frontend::counters* out)
{
    memcpy(out, const_cast<const frontend::counters*>(frontend::stats()), sizeof(*out));
}

// ---- guest interactions: the co-op menus' CONFIRM owners ----------------------------------------------------------------
void ILCT_IxSetMenuSeats(uint32_t epoch, uint32_t seats) { interaction::set_menu_seats(epoch, seats); }

uint32_t ILCT_IxMenuSeats(uint32_t epoch) { return interaction::menu_seats(epoch); }

}  // extern "C"
