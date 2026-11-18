// Ivalice Legacy co-op overlay: the carousel for text wider than its column (Marco, 2026-11-16: "the text moves from
// one side to the other so the full name is readable").
//
// A text that fits its column is drawn as it is and never moves. A wider one is clipped to the column (batch::clip, so
// it never draws under the value column or outside the window) with a soft fade at each clipped edge, no "...":
//  * unfocused: it shows its start, faded at the right edge;
//  * focused (the row under a seat's cursor, the Target Info name, an overflowing party name): ping-pong. It holds
//    the theme's motion.marquee_hold_ms at the start, glides left at motion.marquee_speed units per second until its end meets the
//    column's right edge, holds there, glides back, and repeats; `since` (the cursor's arrival) restarts it at the start.
// motion.marquee_on false (the reduced-motion token): focused text behaves like unfocused text.
// Pure: no state, no game memory; any thread.
#pragma once

#include <stdint.h>

#include "overlay/batch.h"

namespace ilc::overlay::marquee {

// How far (authoring units, 0..text_w - col_w) the text is shifted left at `now` for a focus that began at `since`.
// 0 whenever the text fits, the speed is not positive (or not a number), now is before since, or one glide would take
// a minute or more; a hold above theme::MARQUEE_HOLD_MAX_MS counts as that maximum (the cycle is never 0 or huge).
float offset(uint64_t now, uint64_t since, float text_w, float col_w, float speed, uint32_t hold_ms);

// Draws `s` in the column [x, x + col_w): as text() when it fits; else clipped with soft edges, shifted by offset()
// when focused. Returns the width used (at most col_w). The batch's own clip is restored afterwards.
float text(batch& b, uint32_t face, float x, float y, float em, const char* s, uint32_t n, const text_style& st,
           float col_w, uint64_t now, uint64_t since, bool focused);

}  // namespace ilc::overlay::marquee
