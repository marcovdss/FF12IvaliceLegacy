// Ivalice Legacy co-op module: state shared by the canvas builder (canvas.cpp) and the frame hook (replay.cpp).
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "hud/canvas.h"
#include "hud/hud_facts.h"

namespace ilc::hud::detail {

// The published frame: one writer (the co-op thread), readers copy and discard a torn copy (seq only grows).
struct frame_slot {
    volatile uint32_t seq;
    volatile uint32_t valid;
    frame value;
};
extern frame_slot g_slot;
extern volatile uint32_t g_test_draw;
extern counters g_counters;

// Byte copy as one `rep movsb`: no library call (the replay also runs inside the host tests' emulator, which has
// no C runtime), and the compiler cannot turn it into a memcpy call.
inline void copy_bytes(void* dst, const void* src, size_t n)
{
    asm volatile("rep movsb" : "+D"(dst), "+S"(src), "+c"(n) : : "memory");
}

inline void zero_bytes(void* dst, size_t n)
{
    asm volatile("rep stosb" : "+D"(dst), "+c"(n) : "a"(0) : "memory");
}

}  // namespace ilc::hud::detail
