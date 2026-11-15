// Ivalice Legacy co-op module, host tests only: the HUD canvas, its frame hook and the guard (ILCT_Hud*).
#include <string.h>

#include "hud/canvas.h"
#include "runtime/guard.h"

using namespace ilc;

namespace {

hud::canvas g_test_canvas;

// Guard drills. Each keeps values in non-volatile registers across the call so a resumed fault that failed to
// restore them would show.
volatile uint64_t g_guard_trace = 0;

void guard_returns(void* arg)
{
    g_guard_trace = reinterpret_cast<uint64_t>(arg);
}

void guard_reads_null(void* arg)
{
    g_guard_trace = reinterpret_cast<uint64_t>(arg);
    asm volatile("movq 8, %%rax" : : : "rax", "memory");
    g_guard_trace = 0;
}

void guard_writes_null(void* arg)
{
    (void)arg;
    asm volatile("movq $1, %%rax\n\tmovq %%rax, 16" : : : "rax", "memory");
}

// Clobbers every non-volatile register (as a game function that saved them would have done before faulting), then
// faults: the guard must hand the caller back its own values.
void guard_clobbers_then_faults(void* arg)
{
    (void)arg;
    asm volatile(
        "movq $0x1111, %%rbx\n"
        "movq $0x2222, %%rbp\n"
        "movq $0x3333, %%rsi\n"
        "movq $0x4444, %%rdi\n"
        "movq $0x5555, %%r12\n"
        "movq $0x6666, %%r13\n"
        "movq $0x7777, %%r14\n"
        "movq $0x8888, %%r15\n"
        "pxor %%xmm6, %%xmm6\n"
        "pxor %%xmm15, %%xmm15\n"
        "subq $0x1000, %%rsp\n"
        "movq 8, %%rax\n"
        :
        :
        : "rax", "memory");
}

void guard_nested(void* arg)
{
    *static_cast<int32_t*>(arg) = guard::call(guard_returns, nullptr);
}

}  // namespace

extern "C" {

uint32_t ILCT_HudSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(hud::frame),       sizeof(hud::cmd),         sizeof(hud::counters),
                              sizeof(hud::budget_view), sizeof(hud::status_view), hud::MAX_COMMANDS,
                              hud::TEXT_POOL,           sizeof(guard::fault_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_HudReset(void) { hud::test_reset(); }

void ILCT_HudSetTestDraw(int32_t on) { hud::test_set_test_draw(on != 0); }

int32_t ILCT_HudTestDraw(void) { return hud::test_draw_on() ? 1 : 0; }

void ILCT_HudConfigure(const ilc_settings* s) { hud::configure_from_settings(*s); }

int32_t ILCT_HudAttach(uint64_t base) { return hud::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }

void ILCT_HudSetImageBase(uint64_t base) { hud::test_set_image_base(static_cast<uintptr_t>(base)); }

void ILCT_HudSetPinsOk(int32_t ok) { hud::test_set_pins_ok(ok != 0); }

void ILCT_HudInjectFault(int32_t on) { hud::test_inject_fault(on != 0); }

void ILCT_HudSetRing(uint64_t start, uint64_t end, uint64_t cursor) { hud::test_set_ring(start, end, cursor); }

int32_t ILCT_HudEncode(const char* ascii, char* out, uint32_t cap, uint32_t* substituted)
{
    return hud::encode(ascii, out, cap, substituted);
}

void ILCT_HudBegin(uint32_t epoch, uint64_t now_ms) { g_test_canvas.begin(epoch, now_ms); }

int32_t ILCT_HudText(int32_t x, int32_t y, const char* ascii, uint32_t rgba, uint32_t scale, uint32_t font)
{
    const hud::rgba c = {static_cast<uint8_t>(rgba), static_cast<uint8_t>(rgba >> 8), static_cast<uint8_t>(rgba >> 16),
                         static_cast<uint8_t>(rgba >> 24)};
    return g_test_canvas.text(static_cast<int16_t>(x), static_cast<int16_t>(y), ascii, c,
                              static_cast<uint16_t>(scale), static_cast<uint8_t>(font)) ? 1 : 0;
}

int32_t ILCT_HudSprite(uint32_t atlas, uint32_t page, uint32_t sheet, uint32_t cell, int32_t x, int32_t y,
                       uint32_t rgba, uint32_t w, uint32_t h)
{
    const hud::rgba c = {static_cast<uint8_t>(rgba), static_cast<uint8_t>(rgba >> 8), static_cast<uint8_t>(rgba >> 16),
                         static_cast<uint8_t>(rgba >> 24)};
    return g_test_canvas.sprite(static_cast<hud::atlas_id>(atlas), static_cast<uint8_t>(page),
                                static_cast<uint8_t>(sheet), static_cast<uint16_t>(cell), static_cast<int16_t>(x),
                                static_cast<int16_t>(y), c, static_cast<uint16_t>(w), static_cast<uint16_t>(h)) ? 1 : 0;
}

int32_t ILCT_HudBar(int32_t x, int32_t y, uint32_t w, uint32_t h, uint32_t rgba)
{
    const hud::rgba c = {static_cast<uint8_t>(rgba), static_cast<uint8_t>(rgba >> 8), static_cast<uint8_t>(rgba >> 16),
                         static_cast<uint8_t>(rgba >> 24)};
    return g_test_canvas.bar(static_cast<int16_t>(x), static_cast<int16_t>(y), static_cast<uint16_t>(w),
                             static_cast<uint16_t>(h), c) ? 1 : 0;
}

uint32_t ILCT_HudRefused(void) { return g_test_canvas.refused(); }

void ILCT_HudFrame(hud::frame* out) { memcpy(out, &g_test_canvas.view(), sizeof(*out)); }

void ILCT_HudPublish(void) { hud::publish(g_test_canvas.view()); }

void ILCT_HudPublishFrame(const hud::frame* f) { hud::publish(*f); }

void ILCT_HudWithdraw(void) { hud::withdraw(); }

int32_t ILCT_HudLatest(hud::frame* out) { return hud::latest(*out) ? 1 : 0; }

uint64_t ILCT_HudRingEstimate(const hud::frame* f) { return hud::ring_estimate(*f); }

void ILCT_HudProduce(uint32_t epoch, uint64_t now_ms) { hud::test_produce(epoch, now_ms); }

void ILCT_HudStep(uint64_t now_ms) { hud::step(now_ms); }

void ILCT_HudStop(void) { hud::stop(); }

int32_t ILCT_HudCall(patch::ctx* c, uint32_t epoch) { return hud::h0(c, epoch); }

void ILCT_HudAfter(patch::ctx* c, uint32_t epoch) { hud::h0_after(c, epoch); }

void ILCT_HudStats(hud::counters* out) { memcpy(out, hud::stats(), sizeof(*out)); }

void ILCT_HudBudget(hud::budget_view* out) { *out = hud::budget(); }

void ILCT_HudStatus(hud::status_view* out) { *out = hud::status(); }

int32_t ILCT_HudReplay(const hud::frame* f, uint64_t context) { return hud::test_replay(*f, context); }

int32_t ILCT_HudRow(void) { return hud::test_row(); }

uint64_t ILCT_HudCountersAddress(void) { return reinterpret_cast<uint64_t>(hud::stats()); }

int32_t ILCT_HudGuardAttach(void) { return guard::attach() ? 1 : 0; }

void ILCT_HudGuardReset(void) { guard::test_reset(); }

void ILCT_HudGuardFault(guard::fault_view* out) { *out = guard::last_fault(); }

uint64_t ILCT_HudGuardTrace(void) { return g_guard_trace; }

// mode 0 returns, 1 reads address 8, 2 writes address 16, 3 clobbers the non-volatile registers then faults,
// 4 nests a second guarded call (it must be refused BUSY: the result goes to *nested).
int32_t ILCT_HudGuardCall(uint32_t mode, uint64_t arg, int32_t* nested)
{
    switch (mode) {
    case 0: return guard::call(guard_returns, reinterpret_cast<void*>(arg));
    case 1: return guard::call(guard_reads_null, reinterpret_cast<void*>(arg));
    case 2: return guard::call(guard_writes_null, reinterpret_cast<void*>(arg));
    case 3: return guard::call(guard_clobbers_then_faults, nullptr);
    case 4: return guard::call(guard_nested, nested);
    default: return -1;
    }
}

// Calls mode 3 with known values in every non-volatile register and reports which came back changed (bit per
// register: rbx, rbp, rsi, rdi, r12..r15, xmm6, xmm15). 0 = all restored. *result receives the guard's result.
uint32_t ILCT_HudGuardPreserves(int32_t* result);

}  // extern "C"

// ilct_guard_preserves(result = RCX) -> EAX mask (see ILCT_HudGuardPreserves). Entry RSP = 8 mod 16; eight pushes
// and 0x58 bytes put the call at RSP = 0 mod 16. Saves and restores its caller's non-volatile registers itself.
asm(".text\n"
    ".p2align 4\n"
    ".globl ILCT_HudGuardPreserves\n"
    "ILCT_HudGuardPreserves:\n"
    "    pushq %rbx\n"
    "    pushq %rbp\n"
    "    pushq %rsi\n"
    "    pushq %rdi\n"
    "    pushq %r12\n"
    "    pushq %r13\n"
    "    pushq %r14\n"
    "    pushq %r15\n"
    "    subq $0x58, %rsp\n"
    "    movq %rcx, 0x20(%rsp)\n"
    "    movdqu %xmm6, 0x30(%rsp)\n"
    "    movdqu %xmm15, 0x40(%rsp)\n"
    "    movq $0xB0B0, %rbx\n"
    "    movq $0xB1B1, %rbp\n"
    "    movq $0xB2B2, %rsi\n"
    "    movq $0xB3B3, %rdi\n"
    "    movq $0xB4B4, %r12\n"
    "    movq $0xB5B5, %r13\n"
    "    movq $0xB6B6, %r14\n"
    "    movq $0xB7B7, %r15\n"
    "    movq $0xC6C6, %rax\n"
    "    movq %rax, %xmm6\n"
    "    movq $0xCFCF, %rax\n"
    "    movq %rax, %xmm15\n"
    "    movl $3, %ecx\n"
    "    xorl %edx, %edx\n"
    "    xorl %r8d, %r8d\n"
    "    call ILCT_HudGuardCall\n"
    "    movl %eax, %r10d\n"
    "    xorl %r11d, %r11d\n"
    "    cmpq $0xB0B0, %rbx\n"
    "    je 1f\n"
    "    orl $0x001, %r11d\n"
    "1:  cmpq $0xB1B1, %rbp\n"
    "    je 1f\n"
    "    orl $0x002, %r11d\n"
    "1:  cmpq $0xB2B2, %rsi\n"
    "    je 1f\n"
    "    orl $0x004, %r11d\n"
    "1:  cmpq $0xB3B3, %rdi\n"
    "    je 1f\n"
    "    orl $0x008, %r11d\n"
    "1:  cmpq $0xB4B4, %r12\n"
    "    je 1f\n"
    "    orl $0x010, %r11d\n"
    "1:  cmpq $0xB5B5, %r13\n"
    "    je 1f\n"
    "    orl $0x020, %r11d\n"
    "1:  cmpq $0xB6B6, %r14\n"
    "    je 1f\n"
    "    orl $0x040, %r11d\n"
    "1:  cmpq $0xB7B7, %r15\n"
    "    je 1f\n"
    "    orl $0x080, %r11d\n"
    "1:  movq %xmm6, %rax\n"
    "    cmpq $0xC6C6, %rax\n"
    "    je 1f\n"
    "    orl $0x100, %r11d\n"
    "1:  movq %xmm15, %rax\n"
    "    cmpq $0xCFCF, %rax\n"
    "    je 1f\n"
    "    orl $0x200, %r11d\n"
    "1:  movq 0x20(%rsp), %rcx\n"
    "    movl %r10d, (%rcx)\n"
    "    movl %r11d, %eax\n"
    "    movdqu 0x30(%rsp), %xmm6\n"
    "    movdqu 0x40(%rsp), %xmm15\n"
    "    addq $0x58, %rsp\n"
    "    popq %r15\n"
    "    popq %r14\n"
    "    popq %r13\n"
    "    popq %r12\n"
    "    popq %rdi\n"
    "    popq %rsi\n"
    "    popq %rbp\n"
    "    popq %rbx\n"
    "    ret\n");
