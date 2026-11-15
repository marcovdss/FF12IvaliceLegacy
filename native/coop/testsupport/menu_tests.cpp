// Ivalice Legacy co-op module, host tests only: exports for the guest battle-menu model (src/menu).
//
// The catalog builder, the model and the Gambits state are pure and passed through unchanged. The tables come
// either from section files the test passes (as the External File Loader would load them) or from synthetic game
// memory read through the live reader. Four seat menus live here so a test can drive them like the co-op thread.
#include <string.h>

#include "menu/catalog.h"
#include "menu/cost.h"
#include "menu/gambits.h"
#include "menu/live.h"
#include "menu/menus.h"
#include "menu/model.h"
#include "menu/tables.h"
#include "session/reader.h"

using namespace ilc;
using namespace ilc::menu;

namespace {

catalog_tables g_tables;
uint32_t g_parsed = 0;
seat_menu g_seats[4];

seat_menu* seat_at(uint32_t seat) { return seat < 4 ? &g_seats[seat] : nullptr; }

}  // namespace

// The same four seat menus, for the target and request tests (testsupport/submit_tests.cpp).
seat_menu* menu_test_seat(uint32_t seat) { return seat_at(seat); }
// The tables the menu exports use, for the cost and help tests (testsupport/cost_help_tests.cpp).
catalog_tables* menu_test_tables() { return &g_tables; }

extern "C" {

uint32_t ILCT_MenuSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(action_rule), sizeof(content_row), sizeof(licence_node), sizeof(catalog_tables),
                              sizeof(shared_state), sizeof(seat_state), sizeof(entry), sizeof(page_slot),
                              sizeof(catalog), sizeof(menu_event), sizeof(commit_result), sizeof(menu_model),
                              sizeof(gambit_identity), sizeof(gambit_state)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_MenuTablesReset()
{
    memset(&g_tables, 0, sizeof(g_tables));
    g_parsed = 0;
}

int32_t ILCT_MenuTablesSection(uint32_t which, const uint8_t* section, uint64_t size)
{
    table_view v{};
    if (which >= T_COUNT || !view_section(section, static_cast<size_t>(size), &v)) {
        return 0;
    }
    if (!parse_table(&g_tables, static_cast<table_id>(which), v)) {
        return 0;
    }
    g_parsed |= 1u << which;
    return 1;
}

int32_t ILCT_MenuTablesFinish() { return finish_tables(&g_tables, g_parsed); }

void ILCT_MenuTablesCopy(catalog_tables* out) { memcpy(out, &g_tables, sizeof(g_tables)); }

void ILCT_MenuKnownGlobals(uint64_t* out)
{
    const table_globals g = known_table_globals();
    for (uint32_t k = 0; k < T_COUNT; ++k) {
        out[k] = g.va[k];
    }
}

int32_t ILCT_MenuTablesRead(uint64_t image_base, const uint64_t* globals)
{
    table_globals g{};
    for (uint32_t k = 0; k < T_COUNT; ++k) {
        g.va[k] = static_cast<uintptr_t>(globals[k]);
    }
    session::game_reader r(static_cast<uintptr_t>(image_base));
    const int32_t status = read_tables(r, g, &g_tables);
    g_parsed = 0;
    return status;
}

int32_t ILCT_MenuBuild(const shared_state* sh, const seat_state* st, catalog* out)
{
    return build_catalog(g_tables, *sh, *st, out);
}

int32_t ILCT_MenuCost(uint32_t action, const seat_state* st) { return mp_cost(g_tables, action, *st); }

uint64_t ILCT_MenuSignature(const shared_state* sh, const seat_state* st)
{
    return catalog_signature(g_tables, *sh, *st);
}

void ILCT_MenuSeatReset(uint32_t seat)
{
    if (seat_menu* s = seat_at(seat)) {
        seat_menu_reset(s);
    }
}

int32_t ILCT_MenuSeatRefresh(uint32_t seat, const shared_state* sh, const seat_state* st, commit_result* result)
{
    seat_menu* s = seat_at(seat);
    return s ? seat_menu_refresh(s, g_tables, *sh, *st, result) : -100;
}

int32_t ILCT_MenuSeatCommit(uint32_t seat, const catalog* c, commit_result* result)
{
    seat_menu* s = seat_at(seat);
    return s ? model_commit(&s->model, *c, result) : -100;
}

// op: 0 open, 1 close, 2 toggle, 3 navigate(a, wrap b), 4 navigate page(a, size b), 5 confirm, 6 back,
// 7 set target count(a), 8 cycle group(a, available b)
int32_t ILCT_MenuSeatOp(uint32_t seat, uint32_t op, int32_t a, uint32_t b, menu_event* out)
{
    seat_menu* s = seat_at(seat);
    if (s == nullptr) {
        return -100;
    }
    menu_event e{};
    menu_model* m = &s->model;
    switch (op) {
    case 0: e = model_open(m); break;
    case 1: e = model_close(m); break;
    case 2: e = model_toggle(m); break;
    case 3: e = model_navigate(m, a, b != 0); break;
    case 4: e = model_navigate_page(m, a, b); break;
    case 5: e = model_confirm(m); break;
    case 6: e = model_back(m); break;
    case 7: model_set_target_count(m, static_cast<uint16_t>(a)); break;
    case 8: e = model_cycle_group(m, a, static_cast<uint8_t>(b)); break;
    default: return -101;
    }
    if (out) {
        *out = e;
    }
    return 0;
}

void ILCT_MenuSeatModel(uint32_t seat, menu_model* out)
{
    if (seat_menu* s = seat_at(seat)) {
        memcpy(out, &s->model, sizeof(s->model));
    }
}

// signature, last status, builds, unchanged, kept, identity closes
void ILCT_MenuSeatCounters(uint32_t seat, uint64_t* out)
{
    if (seat_menu* s = seat_at(seat)) {
        out[0] = s->signature;
        out[1] = static_cast<uint64_t>(static_cast<int64_t>(s->last_status));
        out[2] = s->builds;
        out[3] = s->unchanged;
        out[4] = s->kept;
        out[5] = s->identity_closes;
    }
}

int32_t ILCT_MenuGambitCheck(const gambit_identity* id) { return gambit_check_identity(*id); }

int32_t ILCT_MenuGambitRequest(uint32_t seat, const gambit_identity* id, uint32_t status)
{
    seat_menu* s = seat_at(seat);
    return s ? gambit_request(&s->gambits, *id, status) : -100;
}

void ILCT_MenuGambitComplete(uint32_t seat, int32_t result, uint32_t final_enabled)
{
    if (seat_menu* s = seat_at(seat)) {
        gambit_complete(&s->gambits, result, final_enabled);
    }
}

void ILCT_MenuGambitObserve(uint32_t seat, uint32_t record_state)
{
    if (seat_menu* s = seat_at(seat)) {
        gambit_observe(&s->gambits, static_cast<uint8_t>(record_state));
    }
}

void ILCT_MenuGambitState(uint32_t seat, gambit_state* out)
{
    if (seat_menu* s = seat_at(seat)) {
        *out = s->gambits;
    }
}

int32_t ILCT_MenuReadShared(uint64_t image_base, shared_state* out)
{
    session::game_reader r(static_cast<uintptr_t>(image_base));
    return read_shared(r, out);
}

int32_t ILCT_MenuReadSeat(uint64_t image_base, uint64_t actor, uint32_t handle, uint32_t member, uint32_t esper_owner,
                          seat_state* out)
{
    session::game_reader r(static_cast<uintptr_t>(image_base));
    return read_seat(r, static_cast<uintptr_t>(actor), handle, member, static_cast<uint8_t>(esper_owner), out);
}

}  // extern "C"
