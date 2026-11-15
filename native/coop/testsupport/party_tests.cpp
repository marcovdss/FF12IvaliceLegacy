// Ivalice Legacy co-op module, host tests only: exports for seat changes (src/party) and the session's posted
// adoption.
//
// The rules are pure and passed through unchanged. The operation, the watcher and the co-op-thread step run
// natively on synthetic memory the test builds. The game's leader setter and field-ready function are never
// executed natively: the operation's calls go to stubs chosen here (the setter stub writes the leader word where
// the test says, as the game's setter does, and records the stack alignment it was entered with).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "movement/movement.h"
#include "party/assign.h"
#include "party/party.h"
#include "party/remap.h"
#include "session/session.h"

using namespace ilc;
using namespace ilc::party;

namespace {

volatile uint32_t g_setter_calls = 0;
volatile uint32_t g_ready_calls = 0;
uint32_t g_last_member = 0xFFFFFFFFu;  // NOTADDR: none yet
uintptr_t g_leader_address = 0;
int32_t g_ready_value = 1;
uint32_t g_setter_rsp_mod16 = 0xFF;
uint32_t g_setter_override = 0xFFFFFFFFu;  // NOTADDR: write this instead of the member (a setter that "fails")
uint32_t g_setter_override_from = 0;       // the override applies from this call on (1-based; 0 = every call)

__attribute__((noinline)) void setter_stub(uint8_t member)
{
    g_setter_rsp_mod16 = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(__builtin_frame_address(0)) & 0xF);
    const uint32_t call = __atomic_add_fetch(&g_setter_calls, 1u, __ATOMIC_RELAXED);
    g_last_member = member;
    if (g_leader_address != 0) {
        const bool fails = g_setter_override != 0xFFFFFFFFu && call >= g_setter_override_from;  // NOTADDR
        const uint32_t value = fails ? g_setter_override : member;
        memcpy(reinterpret_cast<void*>(g_leader_address), &value, sizeof(value));
    }
}

int32_t ready_stub()
{
    __atomic_add_fetch(&g_ready_calls, 1u, __ATOMIC_RELAXED);
    return g_ready_value;
}

}  // namespace

extern "C" {

uint32_t ILCT_PartySizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(seats_view), sizeof(party_view), sizeof(change_plan), sizeof(remap_record),
                              sizeof(counters), sizeof(debug_view), sizeof(session::adoption)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

int32_t ILCT_PartyPlan(const seats_view* s, const party_view* p, uint32_t seat, uint32_t target, change_plan* out)
{
    return plan_change(*s, *p, seat, target, out);
}

int32_t ILCT_PartyPicker(uint32_t seat, uint32_t member, uint32_t p1)
{
    return picker_allows(seat, member, p1) ? 1 : 0;
}

int32_t ILCT_PartyBenched(const seats_view* s, uint32_t seat)
{
    return benched(*s, seat) ? 1 : 0;
}

int32_t ILCT_PartyAttach(void)
{
    return attach() ? 1 : 0;
}

void ILCT_PartyStart(uint64_t base, uint64_t now)
{
    start(static_cast<uintptr_t>(base), now);
}

void ILCT_PartyStep(uint64_t now)
{
    step(now);
}

void ILCT_PartyStop(void)
{
    stop();
}

void ILCT_PartyReset(void)
{
    test_reset();
    g_setter_calls = 0;
    g_ready_calls = 0;
    g_last_member = 0xFFFFFFFFu;  // NOTADDR
    g_leader_address = 0;
    g_ready_value = 1;
    g_setter_rsp_mod16 = 0xFF;
    g_setter_override = 0xFFFFFFFFu;  // NOTADDR
    g_setter_override_from = 0;
}

void ILCT_PartySetImageBase(uint64_t base)
{
    test_set_image_base(static_cast<uintptr_t>(base));
}

void ILCT_PartyForceLive(int32_t forced)
{
    test_force_live(forced);
}

// stubs 1: the setter stub writes the leader at leader_address, field ready answers `ready`; 0: the game's own.
void ILCT_PartySetStubs(uint32_t stubs, uint64_t leader_address, int32_t ready)
{
    g_leader_address = static_cast<uintptr_t>(leader_address);
    g_ready_value = ready;
    test_set_game_fns(stubs ? setter_stub : nullptr, stubs ? ready_stub : nullptr);
}

void ILCT_PartySetterOverride(uint32_t value)
{
    g_setter_override = value;
    g_setter_override_from = 0;
}

// The setter writes `value` from its `from_call`-th call on (counted since the reset); the earlier calls work.
void ILCT_PartySetterOverrideFrom(uint32_t value, uint32_t from_call)
{
    g_setter_override = value;
    g_setter_override_from = from_call;
}

// out: setter calls, last member, field-ready calls, the setter's frame address mod 16.
void ILCT_PartyCalls(uint32_t* out)
{
    out[0] = g_setter_calls;
    out[1] = g_last_member;
    out[2] = g_ready_calls;
    out[3] = g_setter_rsp_mod16;
}

void ILCT_PartyPeekRequest(uint32_t seat, uint32_t* seq, uint32_t* member)
{
    party::test_api_request(seat, seq, member);
}

void ILCT_PartyRequest(uint32_t seat, uint32_t member)
{
    request_change(seat, member);
}

int32_t ILCT_PartyOp(uint32_t epoch, uint64_t generation, const uint64_t* args)
{
    gtq::job j;
    memset(&j, 0, sizeof(j));
    j.op = OP_PARTY_CHANGE;
    j.epoch = epoch;
    j.generation = generation;
    j.ttl_ms = CHANGE_TTL_MS;
    memcpy(j.arg, args, sizeof(j.arg));
    return op_change(j);
}

void ILCT_PartyTickBefore(void)
{
    tick_before();
}

void ILCT_PartyTickAfter(void)
{
    patch::ctx c;
    memset(&c, 0, sizeof(c));
    tick_after(&c, 0);
}

int32_t ILCT_PartyAfterWanted(void)
{
    return after_wanted() ? 1 : 0;
}

void ILCT_PartyRemapPublish(uint32_t seq, uint32_t epoch, uint64_t generation, uint32_t mask,
                            const movement::steer_entry* slots, uint64_t now)
{
    remap_publish(seq, epoch, generation, mask, slots, now);
}

int32_t ILCT_PartyRemapApply(movement::steer_record* r)
{
    return remap_apply(*r) ? 1 : 0;
}

int32_t ILCT_PartyRemapPending(void)
{
    return remap_pending() ? 1 : 0;
}

int32_t ILCT_PartyRemapRead(remap_record* out)
{
    return remap_read(out) ? 1 : 0;
}

void ILCT_PartyRemapRetire(uint32_t seq)
{
    remap_retire(seq);
}

void ILCT_PartyDebug(debug_view* out)
{
    test_debug(out);
}

void ILCT_PartyStats(counters* out)
{
    memcpy(out, const_cast<const counters*>(stats()), sizeof(*out));
}

// Where the counters and the last posted change sequence live (the emulator tests read them from its memory).
uint64_t ILCT_PartyAddress(uint32_t which)
{
    return which == 0 ? reinterpret_cast<uint64_t>(stats()) : reinterpret_cast<uint64_t>(test_posted_seq());
}

void ILCT_SesPostAdoption(const session::adoption* a)
{
    session::post_adoption(*a);
}

uint32_t ILCT_SesAdoptedSeq(void)
{
    return session::adopted_seq();
}

uint32_t ILCT_SesAdoptionsDropped(void)
{
    return session::adoptions_dropped();
}

int32_t ILCT_SesResolvePrefer(const session::identity* configured, const session::identity* prefer,
                              const uint16_t* current, const uint16_t* ui, uint32_t leader, session::identity* out)
{
    return session::resolve_identity_prefer(*configured, prefer, current, ui, leader, out) ? 1 : 0;
}

}  // extern "C"
