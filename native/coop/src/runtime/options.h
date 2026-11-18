// Ivalice Legacy co-op module: the player options the subsystems read, and the arm mask a settings record asks for.
//
// The options are copied out of the applied settings record by configure_from_settings() (the boot, then the co-op
// thread whenever the applied generation changes) into one 64-bit word, so any thread (the co-op thread, the game
// thread's detours and the UI tick) reads a consistent set with one atomic load: no lock, no settings copy.
// Before the first configure the word holds the native defaults.
//
// The arm mask: "Co-op On" means every subsystem works. A record asks for every subsystem bit, less
//   * the developer override subsystems_disarm, honoured only while developer = 1 (switching Developer options off
//     re-arms everything, whatever an old file still carries), and
//   * CAMERA while shared_camera is 0 (the game's own camera follows P1: the camera rows run stock).
// The kill switch and the ordered isolation release still apply on top (control.h).
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

namespace ilc::options {

constexpr uint32_t ALL_SUBSYSTEMS = static_cast<uint32_t>(ILC_ENUM_OR(ILC_SUBSYSTEMS));

constexpr uint32_t effective_subsystems(const ilc_settings& s)
{
    uint32_t mask = ALL_SUBSYSTEMS;
    if (s.developer != 0) {
        mask &= ~(s.subsystems_disarm & ALL_SUBSYSTEMS);
    }
    if (s.shared_camera == 0) {
        mask &= ~static_cast<uint32_t>(ILC_SUBSYS_CAMERA);
    }
    return mask;
}

struct view {
    uint8_t shared_camera;       // 1 on
    uint8_t guest_interactions;  // 1 guests use doors, chests, crystals, NPCs and shops
    uint8_t town_view;           // ilc_town_view
    uint8_t wait_pause;          // ilc_wait_pause
    uint8_t guest_pause;         // 1 guests may pause and open the Party menu
    uint8_t log_lines_compact;   // 1..ILC_LOG_LINES_COMPACT_MAX
    uint8_t log_lines_expanded;  // 5..ILC_LOG_LINES_MAX
    uint8_t log_fade_s;          // 3..15
};
static_assert(sizeof(view) == 8, "one 64-bit word");

// The options of a record (out-of-range values fall back to the defaults; validation refuses them anyway).
view from_settings(const ilc_settings& s);

// Co-op thread (and the boot): publish the options of the applied record.
void configure_from_settings(const ilc_settings& s);

// Any thread.
view current();
bool guest_interactions();
bool guest_pause();
uint8_t town_view();
uint8_t wait_pause();

// Host tests: publish a view directly; reset() returns to the native defaults.
void test_set(const view& v);
void test_reset();

}  // namespace ilc::options
