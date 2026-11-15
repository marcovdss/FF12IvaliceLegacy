// Ivalice Legacy co-op module, host tests only: the couch layout table (ILCT_Ly*): the corners, the boss-gauge drop,
// the log's line cap, the text floor, the boss gauge read on synthetic memory, and the Target Info and battle log passes
// as the driver runs them (so the integration test sees every element where it really draws).
#include <string.h>

#include "hud/battle_log.h"
#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/layout.h"
#include "hud/snapshot.h"
#include "hud/target_info.h"

using namespace ilc;
namespace ly = ilc::hud::layout;

namespace {
hud::canvas g_ly_cv;
}  // namespace

extern "C" {

void ILCT_LyCorners(uint32_t mode, int32_t boss, uint32_t rows, int32_t reduced, ly::zone* out4)
{
    ly::corners(static_cast<uint8_t>(mode), boss != 0, rows, reduced != 0, out4);
}

int32_t ILCT_LyHeaderDy(uint32_t mode, int32_t boss) { return ly::header_dy(static_cast<uint8_t>(mode), boss != 0); }

uint32_t ILCT_LyLogLines(int32_t dy, uint32_t pitch, int32_t lines_dy, uint32_t want)
{
    return ly::log_lines(static_cast<int16_t>(dy), pitch, static_cast<int16_t>(lines_dy), want);
}

int32_t ILCT_LyTextPx(uint32_t scale, uint32_t w, uint32_t h) { return ly::text_px(static_cast<uint16_t>(scale), w, h); }

int32_t ILCT_LyBossShown(uint64_t image_base) { return hud::snapshot::boss_gauge_shown(image_base) ? 1 : 0; }

void ILCT_LyConstants(int32_t* out)
{
    out[0] = ly::BOSS_SHIFT;
    out[1] = ly::TEXT_FLOOR_PX;
    out[2] = ly::FONT_HEIGHT;
    out[3] = hud::target_info::corner::HEADER_Y;
    out[4] = hud::battle_log::LINE_PITCH;
    out[5] = hud::battle_log::LINES_DY;
}

// The Target Info pass on a snapshot of any layout mode (its view published beforehand), into a fresh canvas. The log's
// pass is ILCT_BlDraw (it already takes the snapshot).
uint32_t ILCT_LyTiDraw(const hud::hud_snapshot* snap, uint64_t now, hud::frame* out)
{
    g_ly_cv.begin(snap->head.epoch, now);
    const hud::driver::pass_input in = {snap, now, snap->layout, {}};
    hud::target_info::draw(g_ly_cv, in);
    memcpy(out, &g_ly_cv.view(), sizeof(*out));
    return g_ly_cv.view().count;
}

}  // extern "C"
