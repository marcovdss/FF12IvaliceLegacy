// Ivalice Legacy co-op overlay: what a frame shows, composed into the quad batch (pure functions of their arguments).
//
//  * test_card (Developer options): a small labelled glass card and a line of text in each corner of the
//    1920x1080 authoring space, tick marks along the screen edges every 160 / 90 units, the icon set in the seat
//    colours and a line with the frame time, the overlay's own cost and the back buffer. It proves the layer is drawn,
//    where the authoring space lands on Marco's screen (pillarbox or not) and how the baked font and glass look.
//  * seat_windows: every seat's open command window, target page or character selector from the published menu view
//    (hud/menus_draw.h), one uniform window per seat in that seat's fixed slot along the bottom of the screen, P1
//    leftmost (Marco, 2026-11-16, on the HUD mockup: "P1 command menu is too big. make it uniform."). A closed menu
//    leaves its slot empty: no window moves or resizes with who opened it. Player 1's window is drawn only when the
//    caller says P1's menu is ours (P1 not on the game's own frontends); otherwise a P1 window in the view is reported
//    left out (the frontend then gives P1's buttons back at once).
//
// Window anatomy (towards the vanilla FF12 window; procedural, no game art yet): blue gradient glass with a thin light
// rim and a faint top shine; a tab above its left end with the seat number in the seat colour and the character's
// name ("P2 - Fran") under a thin seat-colour accent; the page title in pale blue (the target group with chevrons on
// the right of a target page); rows of white outlined text, greyed entries dimmed, MP costs and item counts
// right-aligned, the Gambits word ON in pale gold; the white glove cursor outlined in the seat colour poking out of
// the window's left edge; scroll chevrons in the right margin; the help line in a footer under a thin rule. The seat
// shows in the tab and the glove only (no edge bar, no coloured selection bar). Opening: a 150 ms fade while the
// window rises 12 units into its slot.
//
// Slots (slot_layout, authoring units): four windows of SLOT_W x SLOT_H, SLOT_GAP apart, from SLOT_LEFT, bottoms on
// SLOT_BOTTOM, tabs SLOT_TAB_H above. They end left of the stock party list (x 1184, rows from y 790) and far left of
// the reduced one (x 1381); Target Info (y <= 430 with the boss gauge) and the log zone (x >= 1360, y <= 696) are
// clear of them in every layout (host tests: SlotTest).
#pragma once

#include <stdint.h>

#include "hud/menus_draw.h"
#include "hud/snapshot.h"
#include "overlay/batch.h"

namespace ilc::overlay::scene {

struct card_info {
    uint32_t bb_w, bb_h;      // back buffer
    uint32_t format;          // DXGI format
    uint32_t frame_us;        // smoothed time between presented frames
    uint32_t draw_us;         // the overlay's own cost on the last drawn frame
    uint64_t frames;          // frames the overlay drew
    uint32_t hud_alpha_pct;   // the HUD's fade (100 = fully shown)
    uint8_t menus_live;       // the guests' menus are being presented (they may open)
    uint8_t reserved[3];
    uint32_t seat_colour[4];  // rgba per seat; 0 = the default palette
};
// Returns false when the batch ran out of room (the card is then partly drawn; harmless).
bool test_card(batch& b, const card_info& info);

struct menus_out {
    uint32_t drawn;           // windows drawn
    uint32_t dropped;         // bit per seat whose open window is not on this frame (P1's when not ours; a full batch)
    uint32_t open;            // bit per seat with a window in the view
};
// p1_ours: Player 1's window is drawn (P1 not on the game's own frontends); false: it is reported left out.
// focus_ms (optional, four entries): when each seat's cursor arrived on its row (the carousel restarts there); null =
// the window's opening time.
menus_out seat_windows(batch& b, const hud::menus_draw::menu_view& v, const hud::hud_snapshot& snap, uint64_t now_ms,
                       bool p1_ours, const uint64_t* focus_ms = nullptr);

// The opening at `now` for a window opened at `opened` (0..1, smoothstep over menus_draw::SLIDE_MS).
float ease(uint64_t opened, uint64_t now);

// ---- the seats' fixed slots (pure) ----------------------------------------------------------------------------------
constexpr float SLOT_LEFT = 24.0f;
constexpr float SLOT_W = 275.0f;
constexpr float SLOT_GAP = 16.0f;          // the next window's glove pokes into it, never into the previous window
constexpr float SLOT_H = 230.0f;
constexpr float SLOT_BOTTOM = 1056.0f;     // 24 units above the screen's bottom edge
constexpr float SLOT_TAB_H = 32.0f;        // the tab above the window (2 of it tucked under the window's rim)
constexpr float SLOT_PITCH = 30.0f;        // row pitch
constexpr uint32_t SLOT_ROWS = 5;          // rows shown: P1's root page (Attack .. Items); longer lists scroll
constexpr float SLOT_RISE = 12.0f;         // the opening: rises this far into the slot while it fades in

struct rect {
    float x, y, w, h;
};
struct slot {
    rect frame;          // the glass
    rect tab;            // the tab's box (its drawn width follows its text, at most this)
    rect header;         // the page title line
    rect rows;           // the row area: `visible` rows of SLOT_PITCH from its top
    rect footer;         // the help line, under a thin rule at its top
    uint32_t visible;    // rows shown
    uint32_t start;      // the first shown row (index into the seat view's rows): the cursor kept in view
};
// The slot of `seat` (0..3) for the window in `sv`; false (and *out zeroed) for a seat beyond the four slots.
bool slot_layout(uint32_t seat, const hud::menus_draw::seat_view& sv, slot* out);

}  // namespace ilc::overlay::scene
