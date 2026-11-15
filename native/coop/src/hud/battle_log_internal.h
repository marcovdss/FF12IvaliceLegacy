// Ivalice Legacy co-op module: state shared by the battle log's game-thread side (battle_log.cpp) and its co-op thread
// side (battle_log_sample.cpp).
#pragma once

#include <stdint.h>

#include "hud/battle_log.h"

namespace ilc::hud::battle_log::detail {

// The published view: one writer (the co-op thread); readers copy and discard a torn copy (seq only grows).
struct view_slot {
    volatile uint32_t seq;
    volatile uint32_t valid;
    view value;
};
extern view_slot g_slot;
extern counters g_counters;
extern volatile uint32_t g_attached;     // LG-01's handler is set (the feed may run)
extern volatile uint32_t g_enabled;      // the element switch
extern volatile uint32_t g_epoch;        // epoch of the last published view (0 none)
extern volatile uint64_t g_published_ms; // when it was published

void publish(const view& v);
void withdraw();

}  // namespace ilc::hud::battle_log::detail

namespace ilc::hud::battle_log {

void ring_test_reset();   // host tests: the ring (and the hit bracket) back to empty
int32_t feed_row();       // LG-01's row in the site table
int32_t hit_row_index(uint32_t bit);   // the row of one hit_row bit (HR-01, HR-02, HR-03, HR-05, KR-01), -1 none
void set_hit_rows(uint8_t rows);       // which hit rows act (attach); drops the bracket rows unless both ends act
uint8_t hit_rows();
void chain_rows(int32_t* reset, int32_t* shown);   // CH-V1 and CH-V2

}  // namespace ilc::hud::battle_log
