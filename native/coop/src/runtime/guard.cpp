// Ivalice Legacy co-op module: guarded call (see guard.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "runtime/guard.h"
#include "runtime/probe.h"

extern "C" {
// Written by ilc_guard_call_raw after its prolog, read by the handler: RSP of the guard frame.
volatile uint64_t ilc_guard_frame_rsp = 0;
int ilc_guard_call_raw(void (*f)(void*), void* arg);
extern const char ilc_guard_recover[];
}

// ilc_guard_call_raw(f = RCX, arg = RDX) -> EAX 0 (f returned) or 1 (resumed at ilc_guard_recover by the handler).
// Frame (entry RSP = E, E = 8 mod 16): eight pushes, then 0xC8 bytes, so the call below is made at RSP = 0 mod 16
// with 0x20 bytes of shadow space; XMM6-15 at +0x20..+0xBF. Static unwind data (.seh_*), so a walk from inside f
// reaches our caller. The handler sets RSP = ilc_guard_frame_rsp and RIP = ilc_guard_recover: the epilogue then
// restores exactly what the prolog saved.
asm(".text\n"
    ".p2align 4\n"
    ".globl ilc_guard_call_raw\n"
    ".def ilc_guard_call_raw; .scl 2; .type 32; .endef\n"
    ".seh_proc ilc_guard_call_raw\n"
    "ilc_guard_call_raw:\n"
    "    pushq %rbp\n"
    "    .seh_pushreg %rbp\n"
    "    pushq %rbx\n"
    "    .seh_pushreg %rbx\n"
    "    pushq %rsi\n"
    "    .seh_pushreg %rsi\n"
    "    pushq %rdi\n"
    "    .seh_pushreg %rdi\n"
    "    pushq %r12\n"
    "    .seh_pushreg %r12\n"
    "    pushq %r13\n"
    "    .seh_pushreg %r13\n"
    "    pushq %r14\n"
    "    .seh_pushreg %r14\n"
    "    pushq %r15\n"
    "    .seh_pushreg %r15\n"
    "    subq $0xC8, %rsp\n"
    "    .seh_stackalloc 0xC8\n"
    "    movaps %xmm6, 0x20(%rsp)\n"
    "    .seh_savexmm %xmm6, 0x20\n"
    "    movaps %xmm7, 0x30(%rsp)\n"
    "    .seh_savexmm %xmm7, 0x30\n"
    "    movaps %xmm8, 0x40(%rsp)\n"
    "    .seh_savexmm %xmm8, 0x40\n"
    "    movaps %xmm9, 0x50(%rsp)\n"
    "    .seh_savexmm %xmm9, 0x50\n"
    "    movaps %xmm10, 0x60(%rsp)\n"
    "    .seh_savexmm %xmm10, 0x60\n"
    "    movaps %xmm11, 0x70(%rsp)\n"
    "    .seh_savexmm %xmm11, 0x70\n"
    "    movaps %xmm12, 0x80(%rsp)\n"
    "    .seh_savexmm %xmm12, 0x80\n"
    "    movaps %xmm13, 0x90(%rsp)\n"
    "    .seh_savexmm %xmm13, 0x90\n"
    "    movaps %xmm14, 0xA0(%rsp)\n"
    "    .seh_savexmm %xmm14, 0xA0\n"
    "    movaps %xmm15, 0xB0(%rsp)\n"
    "    .seh_savexmm %xmm15, 0xB0\n"
    "    .seh_endprologue\n"
    "    movq %rsp, ilc_guard_frame_rsp(%rip)\n"
    "    movq %rcx, %rax\n"
    "    movq %rdx, %rcx\n"
    "    callq *%rax\n"
    "    xorl %eax, %eax\n"
    ".Lilc_guard_exit:\n"
    "    movaps 0x20(%rsp), %xmm6\n"
    "    movaps 0x30(%rsp), %xmm7\n"
    "    movaps 0x40(%rsp), %xmm8\n"
    "    movaps 0x50(%rsp), %xmm9\n"
    "    movaps 0x60(%rsp), %xmm10\n"
    "    movaps 0x70(%rsp), %xmm11\n"
    "    movaps 0x80(%rsp), %xmm12\n"
    "    movaps 0x90(%rsp), %xmm13\n"
    "    movaps 0xA0(%rsp), %xmm14\n"
    "    movaps 0xB0(%rsp), %xmm15\n"
    "    addq $0xC8, %rsp\n"
    "    popq %r15\n"
    "    popq %r14\n"
    "    popq %r13\n"
    "    popq %r12\n"
    "    popq %rdi\n"
    "    popq %rsi\n"
    "    popq %rbx\n"
    "    popq %rbp\n"
    "    ret\n"
    ".globl ilc_guard_recover\n"
    "ilc_guard_recover:\n"
    "    movl $1, %eax\n"
    "    jmp .Lilc_guard_exit\n"
    ".seh_endproc\n");

namespace ilc::guard {
namespace {

enum : long { NOT_INSTALLED = 0, INSTALLING = 1, INSTALLED = 2, FAILED = 3 };

long g_state = NOT_INSTALLED;
void* g_handler = nullptr;
volatile uint32_t g_active = 0;  // a guarded call runs
volatile uint64_t g_owner = 0;   // its thread id (TEB ClientId.UniqueThread)
volatile uint64_t g_faults = 0;
volatile uint32_t g_last_code = 0;
volatile uint64_t g_last_rip = 0;

// The current thread's id from the TEB (gs:[0x48]), as GetCurrentThreadId() returns it, without an import call.
uint64_t thread_id()
{
    uint64_t id;
    asm volatile("movq %%gs:0x48, %0" : "=r"(id));
    return id;
}

bool ours(DWORD code)
{
    switch (code) {
    case EXCEPTION_ACCESS_VIOLATION:
    case EXCEPTION_IN_PAGE_ERROR:
    case EXCEPTION_ILLEGAL_INSTRUCTION:
    case EXCEPTION_PRIV_INSTRUCTION:
    case EXCEPTION_INT_DIVIDE_BY_ZERO:
    case EXCEPTION_INT_OVERFLOW:
    case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:
    case EXCEPTION_DATATYPE_MISALIGNMENT:
        return true;
    default:
        return false;
    }
}

LONG CALLBACK guard_handler(EXCEPTION_POINTERS* info)
{
    if (__atomic_load_n(&g_active, __ATOMIC_ACQUIRE) == 0 || !ours(info->ExceptionRecord->ExceptionCode)) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    CONTEXT* c = info->ContextRecord;
    if (__atomic_load_n(&g_owner, __ATOMIC_RELAXED) != thread_id() || probe_fault_site(c->Rip)) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    const uint64_t frame = __atomic_load_n(&ilc_guard_frame_rsp, __ATOMIC_RELAXED);
    if (frame == 0) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    g_last_code = info->ExceptionRecord->ExceptionCode;
    g_last_rip = c->Rip;
    __atomic_add_fetch(&g_faults, 1, __ATOMIC_RELAXED);
    __atomic_store_n(&g_active, 0u, __ATOMIC_RELEASE);  // one shot: a fault in the recovery path is not ours
    c->Rsp = frame;
    c->Rip = reinterpret_cast<DWORD64>(ilc_guard_recover);
    c->EFlags &= ~static_cast<DWORD>(0x400);  // DF clear, as the ABI requires at a function boundary
    return EXCEPTION_CONTINUE_EXECUTION;
}

struct handler_teardown {
    ~handler_teardown()
    {
        if (__atomic_load_n(&g_state, __ATOMIC_ACQUIRE) == INSTALLED && g_handler != nullptr) {
            RemoveVectoredExceptionHandler(g_handler);
            g_handler = nullptr;
            __atomic_store_n(&g_state, NOT_INSTALLED, __ATOMIC_RELEASE);
        }
    }
};

handler_teardown g_teardown;

}  // namespace

bool attach()
{
    long expected = NOT_INSTALLED;
    if (__atomic_compare_exchange_n(&g_state, &expected, INSTALLING, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        void* handler = AddVectoredExceptionHandler(1, guard_handler);
        g_handler = handler;
        __atomic_store_n(&g_state, handler != nullptr ? INSTALLED : FAILED, __ATOMIC_RELEASE);
    }
    long state;
    while ((state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE)) == INSTALLING) {
        __builtin_ia32_pause();
    }
    return state == INSTALLED;
}

bool ready()
{
    return __atomic_load_n(&g_state, __ATOMIC_ACQUIRE) == INSTALLED;
}

int32_t call(fn f, void* arg)
{
    if (!ready() || f == nullptr) {
        return NOT_READY;
    }
    uint32_t idle = 0;
    if (!__atomic_compare_exchange_n(&g_active, &idle, 1u, false, __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE)) {
        return BUSY;
    }
    __atomic_store_n(&g_owner, thread_id(), __ATOMIC_RELEASE);
    const int raw = ilc_guard_call_raw(f, arg);
    __atomic_store_n(&ilc_guard_frame_rsp, 0ull, __ATOMIC_RELAXED);
    __atomic_store_n(&g_owner, 0ull, __ATOMIC_RELAXED);
    __atomic_store_n(&g_active, 0u, __ATOMIC_RELEASE);
    return raw == 0 ? RETURNED : FAULTED;
}

fault_view last_fault()
{
    fault_view v = {};
    v.faults = __atomic_load_n(&g_faults, __ATOMIC_RELAXED);
    v.code = g_last_code;
    v.rip = g_last_rip;
    return v;
}

void test_reset()
{
    __atomic_store_n(&g_faults, 0ull, __ATOMIC_RELAXED);
    g_last_code = 0;
    g_last_rip = 0;
}

}  // namespace ilc::guard
