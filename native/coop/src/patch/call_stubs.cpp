// Ivalice Legacy co-op module: the call-row stubs (see call_island.h).
//
// Per call row k (0..17):
//
//   ilc_call_entry_k   jmp qword [ilc_call_slot + 8k]. No unwind data: a single jump at a call boundary, so an
//                      unwinder treats it as a leaf and finds site+5 at [rsp]. Touches no register, flag or
//                      stack slot: the closed gate is stock bit for bit.
//   ilc_call_slow_k    the open gate. Frame (C = RSP after the prolog, E = RSP at entry, [E] = site+5):
//                        C+0x000  shadow space for the call below
//                        C+0x020  patch::ctx (0x100 bytes)
//                        C+0x120  RFLAGS as pushed by the prolog; later the tail continuation
//                        C+0x128  the return address (site+5); in RETURN mode the resume address
//                      Prolog `pushfq; lea rsp,[rsp-0x120]` is described by two UWOP_ALLOC_SMALL codes, so every
//                      instruction after the prolog unwinds to E with RIP = [E]. It saves RCX and k, calls
//                      ilc_call_common, restores every general register, XMM0-5 and RFLAGS from the ctx and leaves
//                      by `lea rsp,[rsp+0x120]; ret` (TAIL: into the continuation, the original return address
//                      still on the stack) or `lea rsp,[rsp+0x128]; ret` (RETURN: as if the callee had returned).
//                      The only instructions whose RSP differs from the unwind data are the two that restore
//                      RFLAGS (push [ctx.rflags]; popfq); neither can fault.
//   ilc_call_common    one function with its own unwind data (`lea rsp,[rsp-0x78]`): saves the rest of the
//                      machine state, calls ilc_call_dispatch, and either stores the continuation (TAIL/RETURN)
//                      or, for CALL_AROUND, calls the callee from inside its own frame (shadow space and ten
//                      stack-argument qwords copied from the game's frame), saves what it returned and calls
//                      ilc_call_after. A walk from inside the callee then goes callee -> ilc_call_common ->
//                      ilc_call_slow_k -> site+5.
//
// The ctx offsets are patch::ctx's (static_asserts in patch_engine.h); ctx.reserved at 0x68 carries the exit mode
// the dispatcher chose (0 TAIL, 1 RETURN, 2 AROUND).
#include "patch/call_island.h"

static_assert(ilc::patch::island::ROWS == 18, "the .irp lists below name 18 call rows");

#define ILC_CALL_K "0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17"

asm(".text\n"
    // ---- entries (leaf jumps) ----
    ".irp k," ILC_CALL_K "\n"
    "    .p2align 4\n"
    "    .globl ilc_call_entry_\\k\n"
    "ilc_call_entry_\\k:\n"
    "    jmpq *ilc_call_slot+8*\\k(%rip)\n"
    ".endr\n"

    // ---- slow paths (one per row, static unwind data) ----
    ".irp k," ILC_CALL_K "\n"
    "    .p2align 4\n"
    "    .globl ilc_call_slow_\\k\n"
    "    .def ilc_call_slow_\\k; .scl 2; .type 32; .endef\n"
    "    .seh_proc ilc_call_slow_\\k\n"
    "ilc_call_slow_\\k:\n"
    "    pushfq\n"
    "    .seh_stackalloc 8\n"
    "    leaq -0x120(%rsp), %rsp\n"
    "    .seh_stackalloc 0x120\n"
    "    .seh_endprologue\n"
    "    movq %rcx, 0x98(%rsp)\n"          // ctx.rcx
    "    movq $\\k, 0x118(%rsp)\n"         // ctx.index = k (the dispatcher turns it into the row)
    "    leaq 0x20(%rsp), %rcx\n"
    "    call ilc_call_common\n"
    "    movdqu 0x20(%rsp), %xmm0\n"
    "    movdqu 0x30(%rsp), %xmm1\n"
    "    movdqu 0x40(%rsp), %xmm2\n"
    "    movdqu 0x50(%rsp), %xmm3\n"
    "    movdqu 0x60(%rsp), %xmm4\n"
    "    movdqu 0x70(%rsp), %xmm5\n"
    "    movq 0x90(%rsp), %rax\n"
    "    movq 0x98(%rsp), %rcx\n"
    "    movq 0xA0(%rsp), %rdx\n"
    "    movq 0xA8(%rsp), %rbx\n"
    "    movq 0xB8(%rsp), %rbp\n"
    "    movq 0xC0(%rsp), %rsi\n"
    "    movq 0xC8(%rsp), %rdi\n"
    "    movq 0xD0(%rsp), %r8\n"
    "    movq 0xD8(%rsp), %r9\n"
    "    movq 0xE0(%rsp), %r10\n"
    "    movq 0xE8(%rsp), %r11\n"
    "    movq 0xF0(%rsp), %r12\n"
    "    movq 0xF8(%rsp), %r13\n"
    "    movq 0x100(%rsp), %r14\n"
    "    movq 0x108(%rsp), %r15\n"
    "    cmpq $1, 0x88(%rsp)\n"            // ctx.reserved = exit mode
    "    je 1f\n"
    "    pushq 0x110(%rsp)\n"              // ctx.rflags
    "    popfq\n"
    "    leaq 0x120(%rsp), %rsp\n"
    "    ret\n"                            // TAIL: [E-8] = continuation, [E] = site+5 stays
    "1:\n"
    "    pushq 0x110(%rsp)\n"
    "    popfq\n"
    "    leaq 0x128(%rsp), %rsp\n"
    "    ret\n"                            // RETURN: [E] = resume address
    "    .seh_endproc\n"
    ".endr\n"

    // ---- shared body ----
    "    .p2align 4\n"
    "    .globl ilc_call_common\n"
    "    .def ilc_call_common; .scl 2; .type 32; .endef\n"
    "    .seh_proc ilc_call_common\n"
    "ilc_call_common:\n"
    "    leaq -0x78(%rsp), %rsp\n"
    "    .seh_stackalloc 0x78\n"
    "    .seh_endprologue\n"
    "    movq %rax, 0x70(%rcx)\n"
    "    movq %rdx, 0x80(%rcx)\n"
    "    movq %rbx, 0x88(%rcx)\n"
    "    leaq 0x108(%rcx), %rax\n"          // RSP at the callee's entry
    "    movq %rax, 0x90(%rcx)\n"
    "    movq %rbp, 0x98(%rcx)\n"
    "    movq %rsi, 0xA0(%rcx)\n"
    "    movq %rdi, 0xA8(%rcx)\n"
    "    movq %r8, 0xB0(%rcx)\n"
    "    movq %r9, 0xB8(%rcx)\n"
    "    movq %r10, 0xC0(%rcx)\n"
    "    movq %r11, 0xC8(%rcx)\n"
    "    movq %r12, 0xD0(%rcx)\n"
    "    movq %r13, 0xD8(%rcx)\n"
    "    movq %r14, 0xE0(%rcx)\n"
    "    movq %r15, 0xE8(%rcx)\n"
    "    movdqu %xmm0, 0x00(%rcx)\n"
    "    movdqu %xmm1, 0x10(%rcx)\n"
    "    movdqu %xmm2, 0x20(%rcx)\n"
    "    movdqu %xmm3, 0x30(%rcx)\n"
    "    movdqu %xmm4, 0x40(%rcx)\n"
    "    movdqu %xmm5, 0x50(%rcx)\n"
    "    movq 0x100(%rcx), %rax\n"          // the prolog's RFLAGS
    "    movq %rax, 0xF0(%rcx)\n"
    "    movq $0, 0x60(%rcx)\n"
    "    movq $0, 0x68(%rcx)\n"
    "    cld\n"
    "    call ilc_call_dispatch\n"
    "    leaq 0xA0(%rsp), %rcx\n"
    "    cmpq $2, 0x68(%rcx)\n"
    "    je 3f\n"
    "    cmpq $1, 0x68(%rcx)\n"
    "    je 2f\n"
    "    movq %rax, 0x100(%rcx)\n"          // TAIL: continuation where the prolog's RFLAGS were
    "    leaq 0x78(%rsp), %rsp\n"
    "    ret\n"
    "2:\n"
    "    movq %rax, 0x108(%rcx)\n"          // RETURN: resume address over the return address
    "    leaq 0x78(%rsp), %rsp\n"
    "    ret\n"
    "3:\n"                                  // AROUND: rax = callee
    "    movq %rax, 0x60(%rcx)\n"
    ".irp i,0,1,2,3,4,5,6,7,8,9\n"
    "    movq 0x130+8*\\i(%rcx), %rax\n"    // stack arguments 5..14 of the game's call
    "    movq %rax, 0x20+8*\\i(%rsp)\n"
    ".endr\n"
    "    movdqu 0xA0(%rsp), %xmm0\n"
    "    movdqu 0xB0(%rsp), %xmm1\n"
    "    movdqu 0xC0(%rsp), %xmm2\n"
    "    movdqu 0xD0(%rsp), %xmm3\n"
    "    movdqu 0xE0(%rsp), %xmm4\n"
    "    movdqu 0xF0(%rsp), %xmm5\n"
    "    movq 0x110(%rsp), %rax\n"
    "    movq 0x118(%rsp), %rcx\n"
    "    movq 0x120(%rsp), %rdx\n"
    "    movq 0x128(%rsp), %rbx\n"
    "    movq 0x138(%rsp), %rbp\n"
    "    movq 0x140(%rsp), %rsi\n"
    "    movq 0x148(%rsp), %rdi\n"
    "    movq 0x150(%rsp), %r8\n"
    "    movq 0x158(%rsp), %r9\n"
    "    movq 0x160(%rsp), %r10\n"
    "    movq 0x168(%rsp), %r11\n"
    "    movq 0x170(%rsp), %r12\n"
    "    movq 0x178(%rsp), %r13\n"
    "    movq 0x180(%rsp), %r14\n"
    "    movq 0x188(%rsp), %r15\n"
    "    pushq 0x190(%rsp)\n"
    "    popfq\n"
    "    call *0x100(%rsp)\n"
    "    pushfq\n"
    "    popq 0x190(%rsp)\n"
    "    movq %rax, 0x110(%rsp)\n"
    "    movq %rcx, 0x118(%rsp)\n"
    "    movq %rdx, 0x120(%rsp)\n"
    "    movq %rbx, 0x128(%rsp)\n"
    "    movq %rbp, 0x138(%rsp)\n"
    "    movq %rsi, 0x140(%rsp)\n"
    "    movq %rdi, 0x148(%rsp)\n"
    "    movq %r8, 0x150(%rsp)\n"
    "    movq %r9, 0x158(%rsp)\n"
    "    movq %r10, 0x160(%rsp)\n"
    "    movq %r11, 0x168(%rsp)\n"
    "    movq %r12, 0x170(%rsp)\n"
    "    movq %r13, 0x178(%rsp)\n"
    "    movq %r14, 0x180(%rsp)\n"
    "    movq %r15, 0x188(%rsp)\n"
    "    movdqu %xmm0, 0xA0(%rsp)\n"
    "    movdqu %xmm1, 0xB0(%rsp)\n"
    "    movdqu %xmm2, 0xC0(%rsp)\n"
    "    movdqu %xmm3, 0xD0(%rsp)\n"
    "    movdqu %xmm4, 0xE0(%rsp)\n"
    "    movdqu %xmm5, 0xF0(%rsp)\n"
    "    leaq 0xA0(%rsp), %rcx\n"
    "    movq $0, 0x60(%rcx)\n"
    "    cld\n"
    "    call ilc_call_after\n"
    "    leaq 0xA0(%rsp), %rcx\n"
    "    movq $1, 0x68(%rcx)\n"
    "    movq %rax, 0x108(%rcx)\n"
    "    leaq 0x78(%rsp), %rsp\n"
    "    ret\n"
    "    .seh_endproc\n"

    // ---- tables for the C++ side ----
    "    .section .rdata,\"dr\"\n"
    "    .p2align 3\n"
    "    .globl ilc_call_entry_table\n"
    "ilc_call_entry_table:\n"
    ".irp k," ILC_CALL_K "\n"
    "    .quad ilc_call_entry_\\k\n"
    ".endr\n"
    "    .globl ilc_call_slow_table\n"
    "ilc_call_slow_table:\n"
    ".irp k," ILC_CALL_K "\n"
    "    .quad ilc_call_slow_\\k\n"
    ".endr\n"
    "    .text\n");
