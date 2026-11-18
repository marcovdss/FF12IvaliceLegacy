// Ivalice Legacy co-op module: the firewall and frontend-gate detours (see firewall.h).
//
// Every handler runs on a game thread (the input tick thread, the main thread or the UI thread: get-button has
// several callers), never blocks, allocates or calls a game function. The pad get-button and get-axis handlers
// read no game memory at all: P1's device is a pointer comparison against what input isolation's pad visits
// found (P1's own XInput object, the host object). Game memory is read or written only through the fault-safe
// probe (FW-04's wheel consume, FW-05's mouse look clear).
//
// A handler that answers instead of the game sets rax (or xmm0) and resumes at a bare `ret` of the same function
// (or, for a refused frontend, at a `xor eax, eax; ret`): the stack is exactly as the game's call left it.
#include <string.h>

#include "firewall/firewall.h"
#include "firewall/firewall_facts.h"
#include "isolation/isolation.h"
#include "patch/call_island.h"
#include "patch/detour.h"
#include "runtime/probe.h"

namespace ilc::firewall {

namespace detail {
extern patch::record_slot<fw_record> g_slot;
extern counters g_counters;
}  // namespace detail

namespace {

using patch::ctx;
using patch::RESUME;
using patch::RUN_ORIGINAL;

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

constexpr bool firewall_row(int32_t row)
{
    return row >= 0 && patch::SITES[row].policy == ILC_SITE_POLICY_PATCH &&
           patch::SITES[row].subsystem == ILC_SITE_SUBSYS_FIREWALL_MENUS && patch::SITES[row].hook_len >= 5 &&
           (patch::SITES[row].flags & (ILC_SITE_FLAG_INTERIOR | ILC_SITE_FLAG_DROPPED)) == 0;
}

constexpr int32_t FW01 = find_row("FW-01");
constexpr int32_t FW02 = find_row("FW-02");
constexpr int32_t FW03 = find_row("FW-03");
constexpr int32_t FW04 = find_row("FW-04");
constexpr int32_t FW05 = find_row("FW-05");
constexpr int32_t FG01 = find_row("FG-01");
constexpr int32_t FG02 = find_row("FG-02");
constexpr int32_t FG03 = find_row("FG-03");
constexpr int32_t FG04 = find_row("FG-04");
constexpr int32_t FG05 = find_row("FG-05");
constexpr int32_t FG06 = find_row("FG-06");
constexpr int32_t FG07 = find_row("FG-07");
static_assert(firewall_row(FW01) && firewall_row(FW02) && firewall_row(FW03) && firewall_row(FW04) &&
                  firewall_row(FW05) && firewall_row(FG01) && firewall_row(FG02) && firewall_row(FG03) &&
                  firewall_row(FG04) && firewall_row(FG05) && firewall_row(FG06) && firewall_row(FG07),
              "FW-01..05 and FG-01..07 are FIREWALL_MENUS port rows");
static_assert(patch::island::is_call_row(static_cast<uint32_t>(FW05)), "FW-05 is the mouse-look finalize call");
static_assert(!patch::island::is_call_row(static_cast<uint32_t>(FW01)) &&
                  !patch::island::is_call_row(static_cast<uint32_t>(FW02)) &&
                  !patch::island::is_call_row(static_cast<uint32_t>(FW03)) &&
                  !patch::island::is_call_row(static_cast<uint32_t>(FW04)) &&
                  !patch::island::is_call_row(static_cast<uint32_t>(FG01)) &&
                  !patch::island::is_call_row(static_cast<uint32_t>(FG07)),
              "the other rows are MinHook rows (function entries and loads)");
static_assert(patch::SITES[FW01].rva == 0x79D670 - facts::IMAGE_VA && patch::SITES[FW02].rva == 0x79D730 - facts::IMAGE_VA &&
                  patch::SITES[FG01].rva == 0x254B50 - facts::IMAGE_VA && patch::SITES[FG07].rva == 0x2465F0 - facts::IMAGE_VA,
              "the rows the continuations below belong to");

static_assert(patch::IMAGE_BASE == facts::IMAGE_VA, "the pinned image base");
constexpr uint64_t va(uintptr_t v)
{
    return static_cast<uint64_t>(patch::IMAGE_BASE + (v - facts::IMAGE_VA));
}

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

uint64_t (*volatile g_host_source)() = isolation::host_object;
uint64_t (*volatile g_p1_source)() = isolation::p1_object;
volatile uint64_t g_fg07_last = 0;  // the last object FG-07 saw created with a forbidden callback (diagnostics)

int32_t resume_at(ctx* c, uintptr_t target_va)
{
    c->resume = va(target_va);
    return RESUME;
}

bool p1_device(uint64_t dev, const fw_record& r)
{
    const uint32_t f = r.head.flags;
    if ((f & FWF_P1_ALL_PADS) != 0) {
        return true;
    }
    if (dev == 0) {
        return false;
    }
    if ((f & FWF_P1_HOST) != 0 && dev == g_host_source()) {
        return true;
    }
    return (f & FWF_P1_NATIVE) != 0 && dev == g_p1_source();
}

bool gate_armed(const fw_record& r)
{
    return (r.head.flags & (FWF_ACTIVE | FWF_GATE)) == (FWF_ACTIVE | FWF_GATE);
}

// ---- the fail-open check (isolation's rule: the dispatcher's epoch or one either side) --------------------------
bool epoch_near(uint32_t record, uint32_t dispatcher)
{
    return record != 0 && dispatcher != 0 &&
           (record == dispatcher || record + 1u == dispatcher || dispatcher + 1u == record);
}

template <int32_t (*Body)(ctx*, const fw_record&)>
int32_t fw_open(ctx* c, uint32_t epoch)
{
    const uint32_t row = static_cast<uint32_t>(c->index);
    if (row >= patch::SITE_COUNT || !patch::subsystem_live(patch::SITES[row].subsystem)) {
        patch::note_declined(row);
        return RUN_ORIGINAL;
    }
    fw_record copy;
    if (!detail::g_slot.read(copy) || !epoch_near(copy.head.epoch, epoch) ||
        (copy.head.flags & FWF_ACTIVE) == 0) {
        patch::note_declined(row);
        return RUN_ORIGINAL;
    }
    return Body(c, copy);
}

// ---- FW-01: pad get-button 0x79D670 (entry: rcx = device, edx = semantic id; returns the level in al) ------------
int32_t body_fw01(ctx* c, const fw_record& r)
{
    const uint32_t id = static_cast<uint32_t>(c->rdx);
    const uint32_t bit = sem_bit(id);
    if (bit == 0) {
        return RUN_ORIGINAL;  // outside 0x0B..0x1B: the game answers 0 itself
    }
    const bool p1 = p1_device(c->rcx, r);
    if (r.pulse == id && (p1 || (r.head.flags & FWF_PULSE_ANY) != 0) && patch::now_ms() < r.pulse_until) {
        c->rax = 1;  // a pulse precedes every mask: the co-op thread already decided it may go
        bump(detail::g_counters.pulsed);
        return resume_at(c, facts::GET_BUTTON_RET);
    }
    if (((p1 ? r.p1_mask : r.other_mask) & bit) != 0) {
        c->rax = 0;
        bump(detail::g_counters.masked);
        return resume_at(c, facts::GET_BUTTON_RET);
    }
    return RUN_ORIGINAL;
}

// ---- FW-02: pad get-axis 0x79D730 (entry: rcx = device, edx = axis id; returns xmm0) ----------------------------
// P1's stick reads 0 while the record says so: P1's replacement is on, no pause is up and the stock command tree
// is the active menu (the co-op thread reads that every beat; with the frontend gate armed the tree never opens,
// so this is the second wall).
int32_t body_fw02(ctx* c, const fw_record& r)
{
    if ((r.head.flags & FWF_SUPPRESS_P1_AXES) == 0 || !p1_device(c->rcx, r)) {
        return RUN_ORIGINAL;
    }
    c->xmm[0][0] = 0;
    c->xmm[0][1] = 0;
    bump(detail::g_counters.axes_zeroed);
    return resume_at(c, facts::GET_AXIS_RET);
}

// ---- FW-03: desktop button load 0x79D65E (rcx = device, rdx = id - 0x0B in 0..2; then `ret`) ---------------------
int32_t body_fw03(ctx* c, const fw_record& r)
{
    const uint32_t index = static_cast<uint32_t>(c->rdx);
    if (index > 2) {
        return RUN_ORIGINAL;
    }
    const uint32_t mask = (r.head.flags & FWF_DESKTOP_P1) != 0 ? r.p1_mask : r.other_mask;
    if ((mask & sem_bit(facts::SEM_COMMAND + index)) == 0) {
        return RUN_ORIGINAL;
    }
    c->rax = 0;
    bump(detail::g_counters.masked);
    return resume_at(c, facts::DESKTOP_BUTTON_RET);
}

// ---- FW-04: desktop wheel load 0x79D6E6 (rcx = device; returns xmm0) ---------------------------------------------
// Suppressed: the delta is consumed (so it never arrives late, after the suppression ends) and reads 0.
int32_t body_fw04(ctx* c, const fw_record& r)
{
    if ((r.head.flags & FWF_SUPPRESS_WHEEL) == 0) {
        return RUN_ORIGINAL;
    }
    const int32_t zero = 0;
    store_game(reinterpret_cast<void*>(c->rcx + facts::DEV_DESKTOP_WHEEL), &zero, sizeof(zero));
    c->xmm[0][0] = 0;
    c->xmm[0][1] = 0;
    bump(detail::g_counters.wheel_consumed);
    return resume_at(c, facts::DESKTOP_WHEEL_RET);
}

// ---- FW-05: mouse look finalize call 0x7A1A1E (call row: rcx = the desktop device at the finalizer's entry) ------
int32_t body_fw05(ctx* c, const fw_record& r)
{
    if ((r.head.flags & FWF_SUPPRESS_MOUSE_LOOK) != 0) {
        const uint64_t zero = 0;
        if (store_game(reinterpret_cast<void*>(c->rcx + facts::DEV_DESKTOP_LOOK), &zero, sizeof(zero))) {
            bump(detail::g_counters.look_cleared);
        } else {
            bump(detail::g_counters.probe_failed);
        }
    }
    return RUN_ORIGINAL;  // the finalizer always runs (edges and the rest of the desktop state stay the game's)
}

// ---- the frontend gate (entries: ecx = frontend id) --------------------------------------------------------------
int32_t refuse(ctx* c, uint32_t counter)
{
    c->rax = 0;
    bump(detail::g_counters.refused[counter]);
    return resume_at(c, facts::RETURN_ZERO);
}

template <uint32_t Counter>
int32_t by_id(ctx* c, const fw_record& r)
{
    const uint32_t id = static_cast<uint32_t>(c->rcx) & facts::FRONTEND_ID_MASK;
    if (gate_armed(r) && gate_refuses(r.refused, id)) {
        return refuse(c, Counter);
    }
    bump(detail::g_counters.passed);
    return RUN_ORIGINAL;
}

// FG-02/03/04 serve only refused ids (battle request: 1 and 4; leader request: 3; the queue: the battle request's
// ids), so while the gate is armed they refuse whatever reaches them.
template <uint32_t Counter>
int32_t behind(ctx* c, const fw_record& r)
{
    return gate_armed(r) ? refuse(c, Counter) : RUN_ORIGINAL;
}

int32_t body_fg07(ctx* c, const fw_record& r)
{
    if (gate_armed(r) && (c->r8 == va(facts::CB_COMMAND_TREE) || c->r8 == va(facts::CB_LEADER_WINDOW))) {
        __atomic_store_n(&g_fg07_last, c->rcx, __ATOMIC_RELAXED);
        bump(detail::g_counters.refused[6]);  // seen, never refused: a refused object would block the tree slot
    }
    return RUN_ORIGINAL;
}

struct expected_bytes {
    uintptr_t va;
    uint8_t n;
    uint8_t bytes[3];
};
constexpr expected_bytes CONTINUATIONS[] = {
    {facts::GET_BUTTON_RET, 1, {0xC3, 0, 0}},
    {facts::GET_AXIS_RET, 1, {0xC3, 0, 0}},
    {facts::DESKTOP_BUTTON_RET, 1, {0xC3, 0, 0}},
    {facts::DESKTOP_WHEEL_RET, 1, {0xC3, 0, 0}},
    {facts::RETURN_ZERO, 3, {0x33, 0xC0, 0xC3}},
};

}  // namespace

int32_t fw01(ctx* c, uint32_t epoch) { return fw_open<body_fw01>(c, epoch); }
int32_t fw02(ctx* c, uint32_t epoch) { return fw_open<body_fw02>(c, epoch); }
int32_t fw03(ctx* c, uint32_t epoch) { return fw_open<body_fw03>(c, epoch); }
int32_t fw04(ctx* c, uint32_t epoch) { return fw_open<body_fw04>(c, epoch); }
int32_t fw05(ctx* c, uint32_t epoch) { return fw_open<body_fw05>(c, epoch); }
int32_t fg01(ctx* c, uint32_t epoch) { return fw_open<by_id<0>>(c, epoch); }
int32_t fg02(ctx* c, uint32_t epoch) { return fw_open<behind<1>>(c, epoch); }
int32_t fg03(ctx* c, uint32_t epoch) { return fw_open<behind<2>>(c, epoch); }
int32_t fg04(ctx* c, uint32_t epoch) { return fw_open<behind<3>>(c, epoch); }
int32_t fg05(ctx* c, uint32_t epoch) { return fw_open<by_id<4>>(c, epoch); }
int32_t fg06(ctx* c, uint32_t epoch) { return fw_open<by_id<5>>(c, epoch); }
int32_t fg07(ctx* c, uint32_t epoch) { return fw_open<body_fg07>(c, epoch); }

bool attach()
{
    // Every continuation a handler resumes at must hold the bytes it relies on (they are outside the hook windows,
    // so the patch layer's preflight does not pin them).
    for (const expected_bytes& e : CONTINUATIONS) {
        uint8_t got[3] = {};
        if (!copy_from_caller(got, reinterpret_cast<const void*>(va(e.va)), e.n) || memcmp(got, e.bytes, e.n) != 0) {
            return false;
        }
    }
    const struct {
        int32_t row;
        patch::handler_fn fn;
    } hooks[] = {{FW01, fw01}, {FW02, fw02}, {FW03, fw03}, {FW04, fw04}, {FW05, fw05}, {FG01, fg01},
                 {FG02, fg02}, {FG03, fg03}, {FG04, fg04}, {FG05, fg05}, {FG06, fg06}, {FG07, fg07}};
    bool ok = true;
    for (const auto& h : hooks) {
        ok = ok && patch::set_handler(static_cast<uint32_t>(h.row), h.fn);
    }
    for (const auto& h : hooks) {
        ok = ok && patch::set_site_armed(static_cast<uint32_t>(h.row), true);
    }
    if (!ok) {
        for (const auto& h : hooks) {
            patch::set_site_armed(static_cast<uint32_t>(h.row), false);
        }
    }
    return ok;
}

void test_host_source(uint64_t (*source)())
{
    g_host_source = source != nullptr ? source : isolation::host_object;
}

void test_p1_source(uint64_t (*source)())
{
    g_p1_source = source != nullptr ? source : isolation::p1_object;
}

uint64_t test_fg07_last()
{
    return g_fg07_last;
}

}  // namespace ilc::firewall
