// Ivalice Legacy co-op module: one detour stub per site row, and the shared slow path (the fail-open template).
//
// MinHook's relay jumps to stub N with the exact machine state the game had at site N: every register, the
// flags and the stack are live, and there is no return address (the site may be mid-function). So:
//
//  * Fast path (gate N closed): pushfq; cmpb $0, gate[N]; jne; popfq; jmp *tramp[N]. Nothing but the 8 bytes
//    below RSP changes (Windows x64 has no red zone: that memory is never live), and the trampoline runs the
//    original instructions. A disarmed site or subsystem therefore falls through at once.
//  * Slow path (gate open): push N and go to ilc_patch_common, which builds a patch::ctx below the stack
//    (0x100 bytes: XMM0-5, resume, the 16 general registers with RSP as it was at the site, RFLAGS, N), aligns
//    the stack, calls ilc_patch_dispatch(ctx) with 32 bytes of shadow space and DF clear, puts the returned
//    continuation (the trampoline, or ctx->resume) in the slot that held N, restores every register (any change
//    the handler made, except RSP) and the flags, and RETs into the continuation with RSP back at its value on
//    entry.
//
// Stubs are 32-byte aligned and indexed by row: stub N = ilc_patch_stub_base + 32 * N. They have no unwind data,
// like MinHook's trampolines; nothing inside them may raise an exception.
#include "patch/site_table.h"

#define ILC_PATCH_STR2(x) #x
#define ILC_PATCH_STR(x) ILC_PATCH_STR2(x)

asm(".text\n"
    ".p2align 6\n"
    ".globl ilc_patch_stub_base\n"
    "ilc_patch_stub_base:\n"
    "    .set ilc_patch_i, 0\n"
    "    .rept " ILC_PATCH_STR(ILC_SITE_COUNT) "\n"
    "    .p2align 5\n"
    "    pushfq\n"
    "    cmpb $0, ilc_patch_gate+ilc_patch_i(%rip)\n"
    "    jne 1f\n"
    "    popfq\n"
    "    jmpq *ilc_patch_tramp+8*ilc_patch_i(%rip)\n"
    "1:\n"
    "    popfq\n"
    "    pushq $ilc_patch_i\n"
    "    jmp ilc_patch_common\n"
    "    .set ilc_patch_i, ilc_patch_i+1\n"
    "    .endr\n"
    ".globl ilc_patch_stub_end\n"
    "ilc_patch_stub_end:\n"
    "\n"
    ".p2align 4\n"
    ".globl ilc_patch_common\n"
    "ilc_patch_common:\n"
    "    pushfq\n"
    "    leaq -0xF0(%rsp), %rsp\n"
    "    movq %rax, 0x70(%rsp)\n"
    "    movq %rcx, 0x78(%rsp)\n"
    "    movq %rdx, 0x80(%rsp)\n"
    "    movq %rbx, 0x88(%rsp)\n"
    "    leaq 0x100(%rsp), %rax\n"
    "    movq %rax, 0x90(%rsp)\n"
    "    movq %rbp, 0x98(%rsp)\n"
    "    movq %rsi, 0xA0(%rsp)\n"
    "    movq %rdi, 0xA8(%rsp)\n"
    "    movq %r8, 0xB0(%rsp)\n"
    "    movq %r9, 0xB8(%rsp)\n"
    "    movq %r10, 0xC0(%rsp)\n"
    "    movq %r11, 0xC8(%rsp)\n"
    "    movq %r12, 0xD0(%rsp)\n"
    "    movq %r13, 0xD8(%rsp)\n"
    "    movq %r14, 0xE0(%rsp)\n"
    "    movq %r15, 0xE8(%rsp)\n"
    "    movdqu %xmm0, 0x00(%rsp)\n"
    "    movdqu %xmm1, 0x10(%rsp)\n"
    "    movdqu %xmm2, 0x20(%rsp)\n"
    "    movdqu %xmm3, 0x30(%rsp)\n"
    "    movdqu %xmm4, 0x40(%rsp)\n"
    "    movdqu %xmm5, 0x50(%rsp)\n"
    "    movq $0, 0x60(%rsp)\n"
    "    movq $0, 0x68(%rsp)\n"
    "    movq %rsp, %rbx\n"
    "    andq $-16, %rsp\n"
    "    subq $32, %rsp\n"
    "    movq %rbx, %rcx\n"
    "    cld\n"
    "    call ilc_patch_dispatch\n"
    "    movq %rbx, %rsp\n"
    "    movq %rax, 0xF8(%rsp)\n"
    "    movdqu 0x00(%rsp), %xmm0\n"
    "    movdqu 0x10(%rsp), %xmm1\n"
    "    movdqu 0x20(%rsp), %xmm2\n"
    "    movdqu 0x30(%rsp), %xmm3\n"
    "    movdqu 0x40(%rsp), %xmm4\n"
    "    movdqu 0x50(%rsp), %xmm5\n"
    "    movq 0x70(%rsp), %rax\n"
    "    movq 0x78(%rsp), %rcx\n"
    "    movq 0x80(%rsp), %rdx\n"
    "    movq 0x98(%rsp), %rbp\n"
    "    movq 0xA0(%rsp), %rsi\n"
    "    movq 0xA8(%rsp), %rdi\n"
    "    movq 0xB0(%rsp), %r8\n"
    "    movq 0xB8(%rsp), %r9\n"
    "    movq 0xC0(%rsp), %r10\n"
    "    movq 0xC8(%rsp), %r11\n"
    "    movq 0xD0(%rsp), %r12\n"
    "    movq 0xD8(%rsp), %r13\n"
    "    movq 0xE0(%rsp), %r14\n"
    "    movq 0xE8(%rsp), %r15\n"
    "    movq 0x88(%rsp), %rbx\n"
    "    leaq 0xF0(%rsp), %rsp\n"
    "    popfq\n"
    "    ret\n");
