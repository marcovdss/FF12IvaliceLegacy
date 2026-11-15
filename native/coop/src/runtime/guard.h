// Ivalice Legacy co-op module: a guarded call for code that calls game functions (the HUD canvas replay).
//
// guard::call(fn, arg) runs fn(arg) on the calling thread. If anything inside it faults (our code or a game function
// it called: access violation, in-page error, illegal or privileged instruction, integer divide or overflow), a
// vectored handler resumes execution at the guard's own exit with every non-volatile register (RBX, RBP, RSI, RDI,
// R12-R15, XMM6-15) and RSP as they were when guard::call was entered, and call() returns FAULTED. The work fn left
// half-done is not undone (a game draw batch may stay half-written for that frame); the caller disarms whatever
// made the call (the HUD latches its subsystem fault), so the fault cannot repeat every frame.
//
// One guarded call at a time, process-wide (the HUD replay runs on one game thread): a second call while one is
// active returns BUSY without running fn. The handler acts only on the thread that owns the active call (thread
// id from the TEB, no import call), never on a fault the fault-safe probe resumes itself (probe.h), and never on a
// guard-page hit, a breakpoint or a C++/SEH exception code. It is installed by attach() on a normal thread, never
// lazily from a game thread; without it call() refuses (NOT_READY) and the caller stays stock.
#pragma once

#include <stdint.h>

namespace ilc::guard {

enum result : int32_t { RETURNED = 0, FAULTED = 1, BUSY = 2, NOT_READY = 3 };

using fn = void (*)(void* arg);

bool attach();     // install the vectored handler (idempotent); false if it cannot be installed
bool ready();
int32_t call(fn f, void* arg);

struct fault_view {
    uint64_t faults;   // faults caught since start
    uint32_t code;     // exception code of the last one
    uint32_t reserved;
    uint64_t rip;      // where it happened
};
fault_view last_fault();

// Host tests only: run fn with the guard and force the owner check to pass/fail. Never reached in the shipped DLL.
void test_reset();

}  // namespace ilc::guard
