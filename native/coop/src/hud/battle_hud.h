// Ivalice Legacy co-op module: the Battle HUD settings (the Mod Menu's Battle HUD page) and what the page is told back.
//
// Settings (ilc_settings): hud_enabled is the page's Battle HUD on/off; hud_elements has one bit per element
// (ilc_hud_element_bit); log_expand_control, log_filter and log_filter_seat are the battle log's. configure() turns
// them into each element's own switch, so an element that is off runs exactly as it does with its switch off:
//
//   PARTY_SHRINK   native::set_parts (the reduced party list; given back on the next UI tick)
//   CIRCLES_OFF    native::set_parts (target circles off; given back on the next UI tick)
//   SEAT_EDGE      party_rows part EDGE (edge and numeral)
//   LOW_HP_GLOW    party_rows part GLOW (the HP digit colour rows run stock)
//   DAMAGE_GHOST   party_rows part GHOST
//   ACTION_LINE    party_rows part ACTION
//   TARGET_INFO    target_info::set_enabled (off: TI-01 stock, the native window opens again)
//   BATTLE_LOG     battle_log::configure (off: the native combat log draws again)
//   SEAT_LINES     world_marks::set_lines_enabled (off: TG-05/TG-06 stock colours)
//   SEAT_DIAMONDS  world_marks::set_diamonds_enabled (off: WM-01 stock, Identify does nothing)
//   AOE_RING       rings::set_enabled (off: every ring we applied is released; RG-01 stock)
//
// hud_enabled 0 = every element off and the party rows' remaining pieces (status timers, party-target hands) too. The
// co-op battle menus are not part of it: they are how guests act. The Diagnostics test draw has its own field.
//
// Identify (ilc_action IDENTIFY): a seat's press flashes its diamond (world_marks::identify). The co-op thread reads
// the presses through its own edge cursor, so a press is seen once however slowly it runs.
//
// The report (status hud_report, ilc_hud_report_bit), refreshed by the co-op thread every REPORT_MS:
//   FRAME_HOOK_OFF        HD-01 is not hooked, or its call no longer jumps to our island (no co-op HUD, no menus);
//   RENDER_OFF            the frame hook is hooked but draws nothing: the RENDER subsystem is not live (the Mod Menu's
//                         switch, F8, a fault latch, DRY mode) or the hook did not attach (pins, guard, handlers);
//   VISION_NO_COMBAT_LOG  the bytes at HD-01's call are a five-byte NOP run (Insurgent's Vision "No Combat Log");
//   PARTY_LAYOUT_FOREIGN  relayout found a layout another mod changed;
//   CIRCLES_BY_VISION / CIRCLES_FOREIGN  the target-circle site's class (circles.h).
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

namespace ilc::hud::battle_hud {

constexpr uint32_t ALL_ELEMENTS = static_cast<uint32_t>(ILC_ENUM_OR(ILC_HUD_ELEMENTS));
constexpr uint64_t REPORT_MS = 1000;   // how often the co-op thread looks at the frame hook's call bytes

// The elements a record turns on: hud_elements & every defined bit, or none while hud_enabled is 0.
uint32_t effective_elements(const ilc_settings& s);

// The frame hook's five call bytes, classified (pure).
enum call_class : uint8_t {
    CALL_UNREADABLE = 0,
    CALL_STOCK = 1,     // E8 to the log draw: nothing hooked it
    CALL_OTHER = 2,     // E8 elsewhere (with HD-01 hooked: our island)
    CALL_NOPS = 3,      // a run of NOP forms filling the five bytes (Vision's "No Combat Log")
    CALL_FOREIGN = 4,   // anything else
};
uint8_t classify_call(const uint8_t bytes[5], uint64_t site, uint64_t log_draw);
// The report bits from the facts (pure). drawing: the frame hook attached and RENDER live.
uint8_t report_from(uint8_t call, bool row_hooked, uint32_t layout_state, uint32_t circles_site, bool drawing);

// ---- the module side --------------------------------------------------------------------------------------------
void attach(uintptr_t image_base);                    // where the frame hook's call is
void configure_from_settings(const ilc_settings& s);  // every element switch and the log settings
void step(uint64_t now_ms);                           // co-op thread: Identify presses, the report
void stop();
uint32_t elements();                                  // as last configured
uint8_t report();                                     // any thread: the last report (0 before attach)

struct counters {
    volatile uint64_t configured;
    volatile uint64_t identifies;     // Identify presses passed on
    volatile uint64_t identify_off;   // presses dropped: diamonds off
    volatile uint64_t reports;        // report refreshes
};
const counters* stats();

// ---- host tests ---------------------------------------------------------------------------------------------------
void test_reset();
void test_set_call_bytes(const uint8_t* bytes);       // the five bytes the report reads (nullptr = the live ones)
void test_report_now();                               // refresh the report at once
uint32_t test_identify_pending();                     // world_marks' pending Identify bits

}  // namespace ilc::hud::battle_hud
