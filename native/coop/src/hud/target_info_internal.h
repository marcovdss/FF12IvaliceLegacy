// Ivalice Legacy co-op module: state shared by the Target Info's two halves (target_info.cpp: rules, drawing, the
// pass and TI-01; target_info_sample.cpp: the co-op thread's view).
#pragma once

#include <stdint.h>

#include "hud/target_info.h"

namespace ilc::hud::target_info {

namespace detail_ti {

// The published view: one writer (the co-op thread); readers copy and discard a torn copy (seq only grows).
struct view_slot {
    volatile uint32_t seq;
    volatile uint32_t valid;
    view value;
};
extern view_slot g_view;
extern volatile uint32_t g_enabled;
extern volatile uint32_t g_attached;
extern volatile uint64_t g_last_drawn;
extern volatile uint64_t g_test_stamp;
extern counters g_counters;

void bump(volatile uint64_t& c);
void publish(const view& v);
void withdraw();

}  // namespace detail_ti

// The sampler's attach (image base; nothing is read yet).
void sample_attach(uintptr_t image_base);

}  // namespace ilc::hud::target_info
