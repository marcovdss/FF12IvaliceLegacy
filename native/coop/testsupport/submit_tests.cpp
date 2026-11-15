// Ivalice Legacy co-op module, host tests only: exports for target candidates (src/targets) and battle-menu requests
// (src/submit).
//
// The candidate builder and the cursor are pure and passed through. The live picker reads a synthetic image through
// the fault-safe reader. The request slots run against the real game-thread queue; the game functions are replaced
// by the test's doubles (ctypes callbacks) unless a test runs the operations on the hooked twin in unicorn.
#include <string.h>

#include "ilc/bridge_abi.h"
#include "menu/menus.h"
#include "session/reader.h"
#include "session/session.h"
#include "submit/submit.h"
#include "targets/live.h"
#include "targets/targets.h"

using namespace ilc;

ilc::menu::seat_menu* menu_test_seat(uint32_t seat);  // testsupport/menu_tests.cpp

namespace {

targets::target_cursor g_cursor[4];
submit::game_fns g_fns;
bool g_fns_set = false;

}  // namespace

extern "C" {

uint32_t ILCT_TgtSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(targets::actor_snapshot), sizeof(targets::party_member),
                              sizeof(targets::picker_input), sizeof(targets::action_rules),
                              sizeof(targets::candidate), sizeof(targets::candidate_list),
                              sizeof(targets::target_cursor), sizeof(submit::action_request),
                              sizeof(submit::slot_view), sizeof(submit::indicator_view),
                              sizeof(submit::summon_view), sizeof(submit::counters), sizeof(submit::game_fns),
                              sizeof(gtq::job)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_TgtBuild(const targets::picker_input* in, const targets::action_rules* act, targets::candidate_list* out)
{
    targets::build_candidates(*in, *act, out);
}

uint32_t ILCT_TgtRemedy(uint32_t action) { return targets::remedy_mask(action); }

uint32_t ILCT_TgtAvailable(const targets::candidate_list* l) { return targets::available_groups(*l); }

int32_t ILCT_TgtReserve(const uint16_t* current, const uint16_t* def, const uint16_t* menu_layout, uint16_t* out,
                        uint32_t* count)
{
    return targets::reserve_from_layouts(current, def, menu_layout, out, count) ? 1 : 0;
}

int32_t ILCT_TgtReadPicker(uint64_t image_base, const session::session_record* rec, uint32_t seat,
                           targets::picker_input* out)
{
    session::game_reader r(static_cast<uintptr_t>(image_base));
    return targets::read_picker(r, *rec, seat, out);
}

uint64_t ILCT_TgtLiveActor(uint64_t image_base, uint32_t handle, uint32_t alive)
{
    session::game_reader r(static_cast<uintptr_t>(image_base));
    return targets::live_actor(r, handle, alive != 0);
}

void ILCT_TgtCursorReset(uint32_t seat)
{
    if (seat < 4) {
        targets::cursor_reset(&g_cursor[seat]);
    }
}

// Sync the menu test seat's model with candidates; the cursor is the seat's own.
int32_t ILCT_TgtSync(uint32_t seat, const targets::candidate_list* l)
{
    menu::seat_menu* s = menu_test_seat(seat);
    return s ? targets::sync(&s->model, &g_cursor[seat], *l) : -100;
}

void ILCT_TgtNote(uint32_t seat, const targets::candidate_list* l)
{
    if (menu::seat_menu* s = menu_test_seat(seat)) {
        targets::note_selection(s->model, &g_cursor[seat], *l);
    }
}

void ILCT_TgtCursor(uint32_t seat, targets::target_cursor* out)
{
    if (seat < 4) {
        *out = g_cursor[seat];
    }
}

// ---- requests ----------------------------------------------------------------------------------------------------
int32_t ILCT_SubAttach(uint64_t image_base) { return submit::attach(static_cast<uintptr_t>(image_base)) ? 1 : 0; }
int32_t ILCT_SubEnabled() { return submit::enabled() ? 1 : 0; }
void ILCT_SubReset()
{
    submit::test_reset();
    g_fns_set = false;
    for (uint32_t k = 0; k < 4; ++k) {
        targets::cursor_reset(&g_cursor[k]);
    }
}
void ILCT_SubForceEnabled(int32_t forced) { submit::test_force_enabled(forced); }
void ILCT_SubSetImageBase(uint64_t base) { submit::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_SubSetFns(const submit::game_fns* fns)
{
    if (fns == nullptr) {
        g_fns_set = false;
        submit::test_set_game_fns(nullptr);
        return;
    }
    g_fns = *fns;
    g_fns_set = true;
    submit::test_set_game_fns(&g_fns);
}
void ILCT_SubStart(uint64_t base, uint64_t now) { submit::start(static_cast<uintptr_t>(base), now); }
void ILCT_SubStep(uint64_t now) { submit::step(now); }
void ILCT_SubStop() { submit::stop(); }

int32_t ILCT_SubRequest(uint32_t seat, const submit::action_request* rq, uint64_t now)
{
    return submit::request(seat, *rq, now);
}

int32_t ILCT_SubDismiss(uint32_t seat, uint64_t now) { return submit::request_dismiss(seat, now); }

int32_t ILCT_SubGambits(uint32_t seat, uint64_t now)
{
    return submit::request_gambits(seat, menu_test_seat(seat), now);
}

int32_t ILCT_SubEvent(uint32_t seat, const menu::menu_event* e, const targets::candidate_list* l, uint64_t now)
{
    return submit::handle_event(seat, menu_test_seat(seat), *e, *l, now);
}

void ILCT_SubSlot(uint32_t seat, submit::slot_view* out) { submit::read_slot(seat, out); }
void ILCT_SubIndicator(uint32_t seat, submit::indicator_view* out) { submit::read_indicator(seat, out); }
void ILCT_SubSummon(submit::summon_view* out) { submit::read_summon(out); }
uint32_t ILCT_SubOwner() { return submit::summon_owner(); }

void ILCT_SubSummonUpdate(submit::summon_view* s, uint32_t active, const uint16_t* current, uint32_t leader,
                          uint64_t now)
{
    submit::summon_update(s, active != 0, current, leader, now);
}

void ILCT_SubGambitOutcome(int32_t result, int32_t* code, uint32_t* final_enabled)
{
    submit::gambit_outcome(result, code, final_enabled);
}

uint32_t ILCT_SubSoundFor(uint32_t kind) { return submit::sound_for(static_cast<uint8_t>(kind)); }
void ILCT_SubPlay(uint32_t seat, uint32_t id) { submit::play(seat, id); }

// Seat sounds as the applied settings would carry them: bit k = seat k.
void ILCT_SubSetSounds(uint32_t mask)
{
    static ilc_settings s;
    memset(&s, 0, sizeof(s));
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        s.seats[k].seat_ui_sounds = (mask >> k) & 1u;
    }
    submit::configure_from_settings(s);
}

void ILCT_SubStats(submit::counters* out) { memcpy(out, submit::stats(), sizeof(*out)); }

// Run one operation directly (as the drain would, without the queue's own checks).
int32_t ILCT_SubOp(uint32_t op, const gtq::job* j)
{
    switch (op) {
    case submit::OP_MENU_ACTION: return submit::op_action(*j);
    case submit::OP_MENU_GAMBITS: return submit::op_gambits(*j);
    case submit::OP_MENU_DISMISS: return submit::op_dismiss(*j);
    case submit::OP_MENU_INDICATOR: return submit::op_indicator(*j);
    case submit::OP_MENU_SOUND: return submit::op_sound(*j);
    default: return -100;
    }
}

void ILCT_SubEncode(uint32_t seat, const submit::action_request* rq, uint32_t member, uint32_t handle, uint64_t actor,
                    uint64_t* args)
{
    submit::test_encode(seat, *rq, member, handle, actor, args);
}

}  // extern "C"
