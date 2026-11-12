// Ivalice Legacy co-op module, host tests only: exports for the movement subsystem.
//
// The steering maths and the planner are pure functions of plain structs, passed through unchanged so the tests
// can state each rule as a table. The handlers and the follow operation run natively on synthetic memory the test
// builds (and, in the emulator tests, on the image twin).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "movement/movement.h"
#include "movement/planner.h"
#include "movement/steering.h"
#include "patch/patch_engine.h"

using namespace ilc;
using namespace ilc::movement;

namespace {

seat_input g_inputs[ILC_SEATS];
uint32_t g_input_mask = 0;  // bit s: seat s has an input this tick

bool test_input(uint32_t seat, seat_input* out)
{
    if (seat >= ILC_SEATS || (g_input_mask & (1u << seat)) == 0) {
        return false;
    }
    *out = g_inputs[seat];
    return true;
}

}  // namespace

extern "C" {

uint32_t ILCT_MoveSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(steer_entry),  sizeof(steer_record),  sizeof(seat_input),
                              sizeof(actor_facts),  sizeof(plan_inputs),   sizeof(planner_state),
                              sizeof(plan_outputs), sizeof(counters),      sizeof(basis),
                              sizeof(steer_result), sizeof(camera_filter), sizeof(slot_state)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_MoveConstants(double* out)
{
    const double v[] = {RUN_STEP,         ARMED_RUN_STEP,  WALK_STEP,        STICK_WALK_START,
                        STICK_WALK_FULL,  STICK_RUN_START, STICK_WALK_SHARE, TURN_RATE,
                        ACTION_TURN_RATE, DT_MAX,          CAMERA_YAW_MAX_STEP,
                        static_cast<double>(CAMERA_CONFIRM), TURN_DEGREES_PER_FRAME};
    memcpy(out, v, sizeof(v));
}

double ILCT_MoveWrap(double a)
{
    return wrap(a);
}

double ILCT_MoveShare(double strength)
{
    return stick_share(strength);
}

void ILCT_MoveSteer(double forward, double right, const basis* b, int32_t walk, int32_t armed, int32_t allowed,
                    steer_result* out)
{
    *out = steer(forward, right, *b, walk != 0, armed != 0, allowed != 0);
}

double ILCT_MoveTurn(double visual, double target, double rate, double dt)
{
    return turn_toward(visual, target, rate, dt);
}

int32_t ILCT_MoveBasis(const float* view, const basis* previous, camera_filter* filter, basis* out)
{
    bool accepted = false;
    *out = basis_from_view(view, *previous, *filter, &accepted);
    return accepted ? 1 : 0;
}

void ILCT_MoveDefaultBasis(basis* out)
{
    *out = default_basis();
}

void ILCT_MovePlanReset(planner_state* st)
{
    planner_reset(*st);
}

void ILCT_MovePlan(planner_state* st, const plan_inputs* in, plan_outputs* out)
{
    plan(*st, *in, *out);
}

void ILCT_MovePublish(const steer_record* r)
{
    slot().publish(*r);
}

void ILCT_MoveWithdraw(void)
{
    slot().withdraw();
}

int32_t ILCT_MoveRecord(steer_record* out)
{
    return movement::read(out) ? 1 : 0;
}

int32_t ILCT_MoveAttach(void)
{
    return attach() ? 1 : 0;
}

int32_t ILCT_MoveRow(const char* id)
{
    return row_of(id);
}

// Runs a handler on a saved machine state: which = 1..6 (MV-01..MV-06). MV-04 may call the game's facing setter:
// only states where it must not (the image twin is never executed natively).
int32_t ILCT_MoveInvoke(uint32_t which, patch::ctx* c, uint32_t epoch)
{
    char id[6] = {'M', 'V', '-', '0', static_cast<char>('0' + which), 0};
    const int32_t row = row_of(id);
    if (row < 0) {
        return -100;
    }
    c->index = static_cast<uint64_t>(row);
    switch (which) {
    case 1: return mv01(c, epoch);
    case 2: return mv02(c, epoch);
    case 3: return mv03(c, epoch);
    case 4: return mv04(c, epoch);
    case 5: return mv05(c, epoch);
    case 6: return mv06(c, epoch);
    default: return -100;
    }
}

int32_t ILCT_MoveFollow(uint64_t actor, uint64_t phys, uint32_t handle, uint32_t epoch)
{
    gtq::job j;
    memset(&j, 0, sizeof(j));
    j.op = OP_FOLLOW;
    j.epoch = epoch;
    j.arg[0] = actor;
    j.arg[1] = phys;
    j.arg[2] = handle;
    return op_follow(j);
}

void ILCT_MoveSetImageBase(uint64_t base)
{
    test_set_image_base(static_cast<uintptr_t>(base));
}

void ILCT_MoveStats(counters* out)
{
    memcpy(out, stats(), sizeof(*out));
}

void ILCT_MoveReset(void)
{
    test_reset();
    memset(g_inputs, 0, sizeof(g_inputs));
    g_input_mask = 0;
}

void ILCT_MoveStart(uint64_t base, uint64_t now)
{
    start(static_cast<uintptr_t>(base), now);
}

void ILCT_MoveStep(uint64_t now)
{
    step(now);
}

void ILCT_MoveStop(void)
{
    stop();
}

// Replaces the live input service for the next steps: mask bit s = seat s has inputs[s].
void ILCT_MoveSetInputs(const seat_input* inputs, uint32_t mask)
{
    memcpy(g_inputs, inputs, sizeof(g_inputs));
    g_input_mask = mask;
    test_set_input_source(test_input);
}

void ILCT_MoveLast(plan_outputs* out)
{
    test_last(out);
}

uint32_t ILCT_MoveFlee(uint32_t epoch)
{
    return flee_seats(epoch);
}

// -1: the patch layer decides whether MOVEMENT may act (flee word); 0/1 force it.
void ILCT_MoveForceFleeLive(int32_t forced)
{
    test_force_flee_live(forced);
}

int32_t ILCT_MoveBerserkGate(void)
{
    return berserk_gate_live() ? 1 : 0;
}

}  // extern "C"

#include "session/session.h"

extern "C" {

// Publishes a session record exactly as given (the movement step and the follow operation read it).
void ILCT_MoveSessionPublish(const session::session_record* r)
{
    session::slot().publish(*r);
}

void ILCT_MoveSessionWithdraw(void)
{
    session::slot().withdraw();
}

}  // extern "C"
