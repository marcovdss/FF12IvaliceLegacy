// Ivalice Legacy co-op module: fault-safe byte copy (see probe.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "runtime/probe.h"

// ilc_probe_copy_raw(dst = RCX, src = RDX, count = R8) -> EAX 0 = copied, 1 = faulted.
// A leaf routine: it touches no stack and no non-volatile register, so it needs no unwind data. Only the two
// instructions between ilc_probe_guard_begin and ilc_probe_guard_end may fault; the handler sends such a
// fault to ilc_probe_fault.
asm(".text\n"
    ".p2align 4\n"
    ".globl ilc_probe_copy_raw\n"
    ".def ilc_probe_copy_raw; .scl 2; .type 32; .endef\n"
    "ilc_probe_copy_raw:\n"
    "    xorl %eax, %eax\n"
    "    testq %r8, %r8\n"
    "    jz 2f\n"
    "1:\n"
    ".globl ilc_probe_guard_begin\n"
    "ilc_probe_guard_begin:\n"
    "    movzbl (%rdx), %r9d\n"
    "    movb %r9b, (%rcx)\n"
    ".globl ilc_probe_guard_end\n"
    "ilc_probe_guard_end:\n"
    "    incq %rdx\n"
    "    incq %rcx\n"
    "    decq %r8\n"
    "    jnz 1b\n"
    "2:\n"
    "    ret\n"
    ".globl ilc_probe_fault\n"
    "ilc_probe_fault:\n"
    "    movl $1, %eax\n"
    "    ret\n");

// ilc_probe_load_raw(dst = RCX, src = RDX, width = R8: 1, 2, 4 or 8) -> EAX 0 = loaded, 1 = faulted.
// One load of exactly that width (a naturally aligned load is a single access the game's own writes cannot tear),
// then one store to the module's buffer. Only the instructions between ilc_probe_load_begin and ilc_probe_load_end
// may fault; the handler sends such a fault to ilc_probe_fault. A leaf like the copy loop: no stack, no
// non-volatile register.
asm(".text\n"
    ".p2align 4\n"
    ".globl ilc_probe_load_raw\n"
    ".def ilc_probe_load_raw; .scl 2; .type 32; .endef\n"
    "ilc_probe_load_raw:\n"
    "    cmpq $8, %r8\n"
    "    je .Lilc_load8\n"
    "    cmpq $4, %r8\n"
    "    je .Lilc_load4\n"
    "    cmpq $2, %r8\n"
    "    je .Lilc_load2\n"
    "    cmpq $1, %r8\n"
    "    jne ilc_probe_fault\n"
    ".globl ilc_probe_load_begin\n"
    "ilc_probe_load_begin:\n"
    "    movzbl (%rdx), %eax\n"
    "    movb %al, (%rcx)\n"
    "    jmp .Lilc_load_done\n"
    ".Lilc_load2:\n"
    "    movzwl (%rdx), %eax\n"
    "    movw %ax, (%rcx)\n"
    "    jmp .Lilc_load_done\n"
    ".Lilc_load4:\n"
    "    movl (%rdx), %eax\n"
    "    movl %eax, (%rcx)\n"
    "    jmp .Lilc_load_done\n"
    ".Lilc_load8:\n"
    "    movq (%rdx), %rax\n"
    "    movq %rax, (%rcx)\n"
    ".globl ilc_probe_load_end\n"
    "ilc_probe_load_end:\n"
    ".Lilc_load_done:\n"
    "    xorl %eax, %eax\n"
    "    ret\n");

extern "C" {
int ilc_probe_copy_raw(void* dst, const void* src, size_t count);
int ilc_probe_load_raw(void* dst, const void* src, size_t width);
extern const char ilc_probe_guard_begin[];
extern const char ilc_probe_guard_end[];
extern const char ilc_probe_load_begin[];
extern const char ilc_probe_load_end[];
extern const char ilc_probe_fault[];
}

namespace {

enum : long { NOT_INSTALLED = 0, INSTALLING = 1, READY = 2, FAILED = 3 };

long g_state = NOT_INSTALLED;
void* g_handler = nullptr;

LONG CALLBACK probe_handler(EXCEPTION_POINTERS* info)
{
    // Guard-page hits are not ours to handle: the guard belongs to its owner. caller_range_usable() keeps the
    // probe away from guard pages in the first place.
    const DWORD code = info->ExceptionRecord->ExceptionCode;
    if (code != EXCEPTION_ACCESS_VIOLATION && code != EXCEPTION_IN_PAGE_ERROR) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    const DWORD64 rip = info->ContextRecord->Rip;
    const bool in_copy = rip >= reinterpret_cast<DWORD64>(ilc_probe_guard_begin) &&
                         rip < reinterpret_cast<DWORD64>(ilc_probe_guard_end);
    const bool in_load = rip >= reinterpret_cast<DWORD64>(ilc_probe_load_begin) &&
                         rip < reinterpret_cast<DWORD64>(ilc_probe_load_end);
    if (!in_copy && !in_load) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    info->ContextRecord->Rip = reinterpret_cast<DWORD64>(ilc_probe_fault);
    return EXCEPTION_CONTINUE_EXECUTION;
}

bool ensure_handler()
{
    long state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE);
    if (state == READY) {
        return true;
    }
    long expected = NOT_INSTALLED;
    if (__atomic_compare_exchange_n(&g_state, &expected, INSTALLING, false, __ATOMIC_ACQ_REL,
                                    __ATOMIC_ACQUIRE)) {
        void* handler = AddVectoredExceptionHandler(1, probe_handler);
        g_handler = handler;
        __atomic_store_n(&g_state, handler != nullptr ? READY : FAILED, __ATOMIC_RELEASE);
    }
    // Another thread is installing: AddVectoredExceptionHandler takes microseconds.
    while ((state = __atomic_load_n(&g_state, __ATOMIC_ACQUIRE)) == INSTALLING) {
        __builtin_ia32_pause();
    }
    return state == READY;
}

// Removes the handler when the module unloads, so no handler ever points into unmapped code.
struct handler_teardown {
    ~handler_teardown()
    {
        if (__atomic_load_n(&g_state, __ATOMIC_ACQUIRE) == READY && g_handler != nullptr) {
            RemoveVectoredExceptionHandler(g_handler);
            g_handler = nullptr;
            __atomic_store_n(&g_state, NOT_INSTALLED, __ATOMIC_RELEASE);
        }
    }
};

handler_teardown g_teardown;

// Every page of [address, address + count) is committed and neither PAGE_GUARD nor PAGE_NOACCESS. Read or
// write permission is left to the probe: a fault there is an ordinary access violation and is caught.
bool caller_range_usable(const void* address, size_t count)
{
    const uintptr_t start = reinterpret_cast<uintptr_t>(address);
    const uintptr_t end = start + count;
    if (end < start) {
        return false;  // wraps around the address space
    }
    uintptr_t at = start;
    while (at < end) {
        MEMORY_BASIC_INFORMATION info;
        if (VirtualQuery(reinterpret_cast<const void*>(at), &info, sizeof(info)) != sizeof(info)) {
            return false;
        }
        if (info.State != MEM_COMMIT || (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) != 0) {
            return false;
        }
        const uintptr_t region_end = reinterpret_cast<uintptr_t>(info.BaseAddress) + info.RegionSize;
        if (region_end <= at) {
            return false;
        }
        at = region_end;
    }
    return true;
}

bool probe_copy(void* dst, const void* src, size_t count, const void* caller_side)
{
    if (count == 0) {
        return true;
    }
    if (!caller_range_usable(caller_side, count) || !ensure_handler()) {
        return false;
    }
    return ilc_probe_copy_raw(dst, src, count) == 0;
}

}  // namespace

namespace ilc {

bool copy_from_caller(void* dst, const void* src, size_t count)
{
    return probe_copy(dst, src, count, src);
}

bool copy_to_caller(void* dst, const void* src, size_t count)
{
    return probe_copy(dst, src, count, dst);
}

bool probe_region(const void* address, uintptr_t* begin, uintptr_t* end)
{
    MEMORY_BASIC_INFORMATION info;
    if (VirtualQuery(address, &info, sizeof(info)) != sizeof(info)) {
        return false;
    }
    if (info.State != MEM_COMMIT || (info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) != 0) {
        return false;
    }
    *begin = reinterpret_cast<uintptr_t>(info.BaseAddress);
    *end = *begin + info.RegionSize;
    return *end > *begin;
}

bool load_from_caller(void* dst, const void* src, size_t width)
{
    if ((width != 1 && width != 2 && width != 4 && width != 8) ||
        (reinterpret_cast<uintptr_t>(src) & (width - 1)) != 0) {
        return false;
    }
    if (!caller_range_usable(src, width) || !ensure_handler()) {
        return false;
    }
    return ilc_probe_load_raw(dst, src, width) == 0;
}

bool load_from_checked(void* dst, const void* src, size_t width)
{
    if ((width != 1 && width != 2 && width != 4 && width != 8) ||
        (reinterpret_cast<uintptr_t>(src) & (width - 1)) != 0 || !ensure_handler()) {
        return false;
    }
    return ilc_probe_load_raw(dst, src, width) == 0;
}

bool copy_from_checked(void* dst, const void* src, size_t count)
{
    if (count == 0) {
        return true;
    }
    if (!ensure_handler()) {
        return false;
    }
    return ilc_probe_copy_raw(dst, src, count) == 0;
}

}  // namespace ilc
