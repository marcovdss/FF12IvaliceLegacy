// Ivalice Legacy co-op overlay: the carousel (see marquee.h).
#include "overlay/marquee.h"

#include "overlay/font.h"
#include "overlay/theme.h"

namespace ilc::overlay::marquee {

float offset(uint64_t now, uint64_t since, float text_w, float col_w, float speed, uint32_t hold_ms)
{
    const float travel = text_w - col_w;
    // Negated tests: a NaN travel or speed is "no motion" too.
    if (!(travel > 0.0f) || !(speed > 0.0f) || now < since) {
        return 0.0f;
    }
    // A bounded cycle whatever the inputs (the reader keeps speed 10..120 and hold 0..5000, the column keeps travel
    // small): a glide is 1 ms .. GLIDE_MAX_MS and a hold at most theme::MARQUEE_HOLD_MAX_MS, so the cycle is never 0 and
    // never overflows; an absurd glide (or an infinite one) shows the start instead of a frozen crawl.
    constexpr float GLIDE_MAX_MS = 60000.0f;
    const float glide_ms = travel / speed * 1000.0f;
    if (!(glide_ms < GLIDE_MAX_MS)) {
        return 0.0f;
    }
    if (hold_ms > theme::MARQUEE_HOLD_MAX_MS) {
        hold_ms = theme::MARQUEE_HOLD_MAX_MS;
    }
    const uint64_t glide = static_cast<uint64_t>(glide_ms + 0.5f);
    if (glide == 0) {
        return 0.0f;
    }
    const uint64_t cycle = 2 * static_cast<uint64_t>(hold_ms) + 2 * glide;
    const uint64_t t = (now - since) % cycle;
    if (t < hold_ms) {
        return 0.0f;                                                                // hold at the start
    }
    if (t < hold_ms + glide) {
        return travel * static_cast<float>(t - hold_ms) / static_cast<float>(glide);  // glide left
    }
    if (t < 2 * static_cast<uint64_t>(hold_ms) + glide) {
        return travel;                                                              // hold at the end
    }
    const uint64_t back = t - 2 * static_cast<uint64_t>(hold_ms) - glide;
    return travel * (1.0f - static_cast<float>(back) / static_cast<float>(glide));  // glide back
}

float text(batch& b, uint32_t face, float x, float y, float em, const char* s, uint32_t n, const text_style& st,
           float col_w, uint64_t now, uint64_t since, bool focused)
{
    const theme::tokens& T = theme::active();
    const float w = font::measure(face, em, s, n, extra_of(st, em));   // as text() measures it
    if (w <= col_w || col_w <= 0.0f) {
        return ilc::overlay::text(b, face, x, y, em, s, n, st, col_w > 0.0f ? col_w : 0.0f);
    }
    const float travel = w - col_w;
    const float off = focused && T.marquee_on != 0
                          ? offset(now, since, w, col_w, T.marquee_speed, T.marquee_hold_ms)
                          : 0.0f;
    const clip_rect saved = b.clip;
    b.clip.x0 = x;
    b.clip.x1 = x + col_w;
    b.clip.fade_l = off > 0.5f ? T.marquee_fade : 0.0f;
    b.clip.fade_r = off < travel - 0.5f ? T.marquee_fade : 0.0f;
    b.clip.on = 1;
    ilc::overlay::text(b, face, x - off, y, em, s, n, st);   // whole, no "...": the clip cuts it
    b.clip = saved;
    return col_w;
}

}  // namespace ilc::overlay::marquee
