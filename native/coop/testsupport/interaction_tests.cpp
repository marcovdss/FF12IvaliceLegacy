// Ivalice Legacy co-op module, host tests only: exports for guest interactions.
//
// The rules are pure functions of plain structs. The handlers run natively on saved machine states and synthetic
// objects; the window's calls into the game can be replaced by fakes recorded here (the emulator tests use the
// game's own functions on the image twin instead).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "interaction/interaction.h"
#include "interaction/interaction_facts.h"
#include "interaction/rules.h"
#include "isolation/isolation.h"
#include "patch/patch_engine.h"
#include "runtime/probe.h"
#include "session/session.h"

using namespace ilc;
using namespace ilc::interaction;

namespace {

// ---- the fake game ---------------------------------------------------------------------------------------------
// One event per call into the "game", with what the window had published at that moment.
struct fake_event {
    uint32_t kind;            // 1 resolve, 2 generic scan, 3 exact scan, 4 build handle, 5 consumer, 6 gate,
                              // 7 auxiliary, 8 gateway
    int32_t arg;              // registry / mode / handle (low 32 bits)
    uint32_t controlled;      // the controlled handle at the call
    uint32_t guard;           // the exact-scan guard flag at the call
    uint64_t source;          // the interaction source at the call
    uint32_t descriptor[6];
    uint8_t can_use;
    uint8_t target_range;     // consumer: the target's +0xC byte at the call
    uint8_t reserved[6];
    uint64_t target;          // consumer: the entry the descriptor names at the call; generic scan: the pass entry
};
static_assert(sizeof(fake_event) == 64, "fake_event layout (the tests read it)");

constexpr uint32_t LOG_CAP = 64;
fake_event g_log[LOG_CAP];
volatile uint32_t g_log_n = 0;

struct handle_entry {
    uint32_t handle;
    uint32_t reserved;
    uint64_t entry;
};
handle_entry g_handles[16];
uint32_t g_handles_n = 0;
int32_t g_gate = 1;
uint64_t g_aux = 1;
// What the fake auxiliary resolver changes as a side effect (0 = nothing): the qword at g_aux_swap_at becomes
// g_aux_swap_value (a table slot naming another entry, or a sub-object pointer cleared).
uint64_t g_aux_swap_at = 0;
uint64_t g_aux_swap_value = 0;
// What the fake exact scan publishes (once, on registry g_scan_registry): descriptor fields and can-use.
int32_t g_scan_registry = -1;
uint32_t g_scan_index = 0;
uint32_t g_scan_handle = 0;
uint32_t g_scan_state = 2;
uint8_t g_scan_can_use = 0;
// What the fake consumer does: bit 0 = set the target's talk flag, bit 1 = remove the target from its table,
// bit 2 = clear the target's range byte.
uint32_t g_consumer_effect = 0;

template <typename T>
T peek(uintptr_t va)
{
    T v{};
    copy_from_caller(&v, reinterpret_cast<const void*>(va), sizeof(T));
    return v;
}

template <typename T>
void poke(uintptr_t va, const T& v)
{
    copy_to_caller(reinterpret_cast<void*>(va), &v, sizeof(T));
}

uint64_t descriptor_entry(const uint32_t d[6], uint64_t* slot_address)
{
    const int32_t reg = static_cast<int32_t>(d[1]);
    const int32_t index = static_cast<int32_t>(d[2]);
    *slot_address = 0;
    if (reg < 0 || reg >= 5 || index < 0) {
        return 0;
    }
    const uintptr_t registry = facts::REGISTRY_BASE + static_cast<uint32_t>(reg) * facts::REGISTRY_STRIDE;
    const uint64_t table = peek<uint64_t>(registry + facts::REG_TABLE);
    if (table == 0 || index >= peek<int32_t>(table)) {
        return 0;
    }
    *slot_address = table + facts::TABLE_ENTRIES + 8ull * static_cast<uint32_t>(index);
    return peek<uint64_t>(*slot_address);
}

void log(uint32_t kind, int32_t arg)
{
    const uint32_t n = g_log_n;
    if (n >= LOG_CAP) {
        return;
    }
    fake_event& e = g_log[n];
    memset(&e, 0, sizeof(e));
    e.kind = kind;
    e.arg = arg;
    e.controlled = peek<uint32_t>(facts::CONTROLLED_HANDLE);
    debug_view v;
    test_debug(&v);
    e.guard = v.guard_active;
    e.source = peek<uint64_t>(facts::SOURCE);
    copy_from_caller(e.descriptor, reinterpret_cast<const void*>(facts::DESCRIPTOR), sizeof(e.descriptor));
    e.can_use = peek<uint8_t>(facts::CAN_USE);
    if (kind == 2) {
        e.target = v.pass_entry;
        e.guard = v.pass_active;
    }
    if (kind == 5) {
        uint64_t slot = 0;
        e.target = descriptor_entry(e.descriptor, &slot);
        e.target_range = e.target != 0 ? peek<uint8_t>(e.target + facts::E_RANGE) : 0;
    }
    g_log_n = n + 1;
}

uint64_t fake_resolve(uint32_t handle)
{
    log(1, static_cast<int32_t>(handle));
    for (uint32_t k = 0; k < g_handles_n; ++k) {
        if (g_handles[k].handle == handle) {
            return g_handles[k].entry;
        }
    }
    return 0;
}

// What the fake generic scan publishes through the real IX-02 handler (as the game's scan would call the publisher).
struct fake_publication {
    uint32_t slot;
    uint32_t handle;
    uint32_t state;
    uint32_t reserved;
    uint64_t resource;
    uint64_t text;
};
fake_publication g_publish = {};
uint32_t g_publish_on = 0;
int32_t g_publish_result = -1;
uint64_t g_publish_regs[4] = {};  // rax, rdx, r10, resume after the handler

void fake_generic_scan(int32_t registry)
{
    log(2, registry);
    if (g_publish_on != 0) {
        uint64_t stack[8] = {};
        stack[5] = g_publish.text;  // [rsp+0x28] at the publisher's entry
        patch::ctx c;
        memset(&c, 0, sizeof(c));
        c.index = static_cast<uint64_t>(row_of("IX-02"));
        c.rcx = g_publish.handle;
        c.rdx = g_publish.slot;
        c.r8 = g_publish.state;
        c.r9 = g_publish.resource;
        c.rsp = reinterpret_cast<uint64_t>(stack);
        g_publish_result = ix02(&c, 0);
        g_publish_regs[0] = c.rax;
        g_publish_regs[1] = c.rdx;
        g_publish_regs[2] = c.r10;
        g_publish_regs[3] = c.resume;
    }
}

void fake_exact_scan(int32_t registry)
{
    log(3, registry);
    if (registry == g_scan_registry) {
        uint32_t d[6];
        copy_from_caller(d, reinterpret_cast<const void*>(facts::DESCRIPTOR), sizeof(d));
        d[1] = static_cast<uint32_t>(registry);
        d[2] = g_scan_index;
        d[3] = g_scan_state;
        copy_to_caller(reinterpret_cast<void*>(facts::DESCRIPTOR), d, sizeof(d));
        poke<uint8_t>(facts::CAN_USE, g_scan_can_use);
    }
}

void fake_build_handle()
{
    log(4, 0);
    poke<uint32_t>(facts::DESCRIPTOR + facts::D_HANDLE, g_scan_handle);
}

void fake_consumer()
{
    log(5, 0);
    uint32_t d[6];
    copy_from_caller(d, reinterpret_cast<const void*>(facts::DESCRIPTOR), sizeof(d));
    uint64_t slot = 0;
    const uint64_t target = descriptor_entry(d, &slot);
    if (target == 0) {
        return;
    }
    if ((g_consumer_effect & 1) != 0) {
        poke<uint8_t>(target + facts::E_TALK, static_cast<uint8_t>(peek<uint8_t>(target + facts::E_TALK) | 8));
    }
    if ((g_consumer_effect & 4) != 0) {
        poke<uint8_t>(target + facts::E_RANGE, static_cast<uint8_t>(0));
    }
    if ((g_consumer_effect & 2) != 0) {
        poke<uint64_t>(slot, 0);
    }
    // The real consumer clears the trigger byte.
    poke<uint8_t>(facts::TRIGGER, static_cast<uint8_t>(0));
}

int32_t fake_gate(int32_t mode)
{
    log(6, mode);
    return g_gate;
}

uint64_t fake_auxiliary(uint64_t entry)
{
    log(7, static_cast<int32_t>(entry));
    if (g_aux_swap_at != 0) {
        poke<uint64_t>(g_aux_swap_at, g_aux_swap_value);
    }
    return g_aux;
}

void fake_gateway()
{
    log(8, 0);
}

const game_calls FAKES = {fake_resolve, fake_generic_scan, fake_exact_scan, fake_build_handle,
                          fake_consumer, fake_gate,         fake_auxiliary,  fake_gateway};

volatile uint32_t g_presses = 0;
uint32_t take_presses(uint32_t epoch)
{
    (void)epoch;
    return __atomic_exchange_n(&g_presses, 0u, __ATOMIC_ACQ_REL);
}

volatile int32_t g_driver_fake = 0;
uint32_t fake_driver()
{
    return static_cast<uint32_t>(g_driver_fake);
}

using handler = int32_t (*)(patch::ctx*, uint32_t);
const handler HANDLERS[] = {mv07, mv08, ix01, ix02, ix03, ix04, ix05, ix06, ix07, ix08, ix09,
                            ix10, ix17, ix18, ix19, ix20, lt01};

}  // namespace

extern "C" {

uint32_t ILCT_IxSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(ix_entry),    sizeof(ix_record),   sizeof(ix_private), sizeof(request),
                              sizeof(counters),    sizeof(debug_view),  sizeof(target_view), sizeof(hold_state),
                              sizeof(hold_signals), sizeof(fake_event)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

// ---- rules -------------------------------------------------------------------------------------------------------
int32_t ILCT_IxPressAllowed(uint64_t flags, uint32_t state, uint32_t action)
{
    return press_allowed(flags, state, action) ? 1 : 0;
}

int32_t ILCT_IxLeaderAllows(uint64_t flags)
{
    return leader_allows(flags) ? 1 : 0;
}

int32_t ILCT_IxShopOpen(uint32_t id, uint32_t active)
{
    return shop_open(id, active) ? 1 : 0;
}

void ILCT_IxForced(uint32_t handle, uint32_t state, uint32_t live_ready, uint32_t* out)
{
    forced_descriptor(handle, state, live_ready, out);
}

void ILCT_IxResetDescriptor(uint32_t* out)
{
    reset_descriptor(out);
}

uint32_t ILCT_IxObjectState(uint32_t published, int32_t mimic)
{
    return object_state(published, mimic != 0);
}

int32_t ILCT_IxCurrentSlot(const uint32_t* state)
{
    return current_slot(state);
}

int32_t ILCT_IxTargetReady(const target_view* t, uint32_t state)
{
    return target_ready(*t, state);
}

void ILCT_IxHoldBegin(hold_state* h, uint32_t seat, uint32_t target, uint32_t location, uint64_t now)
{
    hold_begin(*h, seat, target, location, now);
}

uint32_t ILCT_IxHoldStep(hold_state* h, const hold_signals* s, uint64_t now)
{
    return hold_step(*h, *s, now);
}

uint32_t ILCT_IxHoldDriver(const hold_state* h)
{
    return hold_driver(*h);
}

// ---- subsystem ---------------------------------------------------------------------------------------------------
void ILCT_IxReset()
{
    test_reset();
    g_log_n = 0;
    g_handles_n = 0;
    g_gate = 1;
    g_aux = 1;
    g_aux_swap_at = 0;
    g_aux_swap_value = 0;
    g_scan_registry = -1;
    g_scan_index = 0;
    g_scan_handle = 0;
    g_scan_state = 2;
    g_scan_can_use = 0;
    g_consumer_effect = 0;
    g_presses = 0;
    g_driver_fake = 0;
    g_publish_on = 0;
    g_publish_result = -1;
    isolation::test_driver_source(nullptr);
}

int32_t ILCT_IxAttach()
{
    return attach() ? 1 : 0;
}

// 1: the next attach() (the boot's included) arms its rows, then fails and disarms them.
void ILCT_IxFailAttach(uint32_t fail)
{
    test_fail_attach(fail != 0);
}

void ILCT_IxStart(uint64_t base, uint64_t now)
{
    start(static_cast<uintptr_t>(base), now);
    test_set_presses(take_presses);
}

void ILCT_IxStep(uint64_t now)
{
    step(now);
}

void ILCT_IxStop()
{
    stop();
}

void ILCT_IxSetImageBase(uint64_t base)
{
    test_set_image_base(static_cast<uintptr_t>(base));
}

void ILCT_IxPress(uint32_t seats)
{
    __atomic_or_fetch(&g_presses, seats, __ATOMIC_ACQ_REL);
}

void ILCT_IxPublish(const ix_record* r)
{
    test_publish(*r);
}

void ILCT_IxWithdraw()
{
    test_withdraw();
}

int32_t ILCT_IxRecord(ix_record* out)
{
    return slot().read(*out) ? 1 : 0;
}

int32_t ILCT_IxPrivate(uint32_t k, ix_private* out)
{
    return test_private(k, out) ? 1 : 0;
}

void ILCT_IxPublishPrivate(uint32_t k, const ix_private* p)
{
    test_publish_private(k, *p);
}

uint64_t ILCT_IxRequest()
{
    return reinterpret_cast<uint64_t>(test_request());
}

void ILCT_IxDebug(debug_view* out)
{
    test_debug(out);
}

void ILCT_IxStats(counters* out)
{
    memcpy(out, const_cast<const counters*>(stats()), sizeof(*out));
}

uint32_t ILCT_IxDriver()
{
    return menu_driver();
}

uint32_t ILCT_IxHoldSeats(uint32_t epoch)
{
    return hold_seats(epoch);
}

int32_t ILCT_IxShopOpenNow()
{
    return shop_open_now() ? 1 : 0;
}

int32_t ILCT_IxRow(const char* id)
{
    return row_of(id);
}

int32_t ILCT_IxInvoke(uint32_t which, patch::ctx* c, uint32_t epoch)
{
    if (which >= sizeof(HANDLERS) / sizeof(HANDLERS[0])) {
        return -1;
    }
    return HANDLERS[which](c, epoch);
}

void ILCT_IxAfter(patch::ctx* c, uint32_t epoch)
{
    mv07_after(c, epoch);
}

void ILCT_IxSetPass(uint32_t active, uint32_t k, uint64_t entry)
{
    test_set_pass(active, k, entry);
}

void ILCT_IxSetGuard(uint32_t active)
{
    test_set_guard(active);
}

// ---- the fake game -----------------------------------------------------------------------------------------------
void ILCT_IxUseFakes(int32_t on)
{
    test_set_calls(on != 0 ? &FAKES : nullptr);
    g_log_n = 0;
}

void ILCT_IxFakeResolve(uint32_t handle, uint64_t entry)
{
    for (uint32_t k = 0; k < g_handles_n; ++k) {
        if (g_handles[k].handle == handle) {
            g_handles[k].entry = entry;
            return;
        }
    }
    if (g_handles_n < 16) {
        g_handles[g_handles_n++] = {handle, 0, entry};
    }
}

void ILCT_IxFakeGate(int32_t value)
{
    g_gate = value;
}

void ILCT_IxFakeAuxiliary(uint64_t value)
{
    g_aux = value;
}

// The fake auxiliary resolver writes `value` at `at` when it runs (at = 0: no side effect).
void ILCT_IxFakeAuxiliarySwap(uint64_t at, uint64_t value)
{
    g_aux_swap_at = at;
    g_aux_swap_value = value;
}

void ILCT_IxFakeScan(int32_t registry, uint32_t index, uint32_t handle, uint32_t state, uint32_t can_use)
{
    g_scan_registry = registry;
    g_scan_index = index;
    g_scan_handle = handle;
    g_scan_state = state;
    g_scan_can_use = static_cast<uint8_t>(can_use);
}

void ILCT_IxFakeConsumer(uint32_t effect)
{
    g_consumer_effect = effect;
}

uint32_t ILCT_IxFakeLog(fake_event* out, uint32_t cap)
{
    const uint32_t n = g_log_n;
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = g_log[k];
    }
    return n;
}

void ILCT_IxFakeLogClear()
{
    g_log_n = 0;
}

// The fake generic scan calls the IX-02 handler with this publication (on = 0: it publishes nothing).
void ILCT_IxFakePublish(uint32_t on, uint32_t slot_index, uint32_t handle, uint32_t state, uint64_t resource,
                        uint64_t text)
{
    g_publish_on = on;
    g_publish = {slot_index, handle, state, 0, resource, text};
    g_publish_result = -1;
}

int32_t ILCT_IxFakePublishResult(uint64_t* regs)
{
    memcpy(regs, g_publish_regs, sizeof(g_publish_regs));
    return g_publish_result;
}

// Publish a session record directly (the session core is not stepped in these tests).
void ILCT_IxSessionPublish(const session::session_record* r)
{
    session::slot().publish(*r);
}

void ILCT_IxSessionWithdraw()
{
    session::slot().withdraw();
}

// Replace the menu driver input isolation reads (seat; -1 restores the interaction subsystem's).
void ILCT_IsoFakeDriver(int32_t seat)
{
    g_driver_fake = seat;
    isolation::test_driver_source(seat >= 0 ? fake_driver : nullptr);
}

}  // extern "C"
