// Ivalice Legacy co-op module: the player options word (see options.h).
#include "runtime/options.h"

#include "ilc/bridge_defaults.h"

namespace ilc::options {

namespace {

constexpr uint8_t pick(uint8_t value, uint8_t lo, uint8_t hi, uint8_t fallback)
{
    return value >= lo && value <= hi ? value : fallback;
}

constexpr view build(const ilc_settings& s)
{
    view v{};
    v.shared_camera = s.shared_camera != 0 ? 1 : 0;
    v.guest_interactions = s.guest_interactions != 0 ? 1 : 0;
    v.town_view = pick(s.town_view, 0, static_cast<uint8_t>(ILC_ENUM_MAX(ILC_TOWN_VIEWS)), ILC_TOWN_VIEW_PLAYERS);
    v.wait_pause = pick(s.wait_pause, 0, static_cast<uint8_t>(ILC_ENUM_MAX(ILC_WAIT_PAUSES)), ILC_WAIT_PAUSE_ALL);
    v.guest_pause = s.guest_pause != 0 ? 1 : 0;
    v.log_lines_expanded = pick(s.log_lines_expanded, 5, ILC_LOG_LINES_MAX, 20);
    v.log_lines_compact = pick(s.log_lines_compact, 1, ILC_LOG_LINES_COMPACT_MAX, 5);
    if (v.log_lines_compact > v.log_lines_expanded) {
        v.log_lines_compact = v.log_lines_expanded;
    }
    v.log_fade_s = pick(s.log_fade_s, 3, 15, 6);
    return v;
}

constexpr uint64_t pack(const view& v)
{
    return static_cast<uint64_t>(v.shared_camera) | static_cast<uint64_t>(v.guest_interactions) << 8 |
           static_cast<uint64_t>(v.town_view) << 16 | static_cast<uint64_t>(v.wait_pause) << 24 |
           static_cast<uint64_t>(v.guest_pause) << 32 | static_cast<uint64_t>(v.log_lines_compact) << 40 |
           static_cast<uint64_t>(v.log_lines_expanded) << 48 | static_cast<uint64_t>(v.log_fade_s) << 56;
}

constexpr view unpack(uint64_t w)
{
    view v{};
    v.shared_camera = static_cast<uint8_t>(w);
    v.guest_interactions = static_cast<uint8_t>(w >> 8);
    v.town_view = static_cast<uint8_t>(w >> 16);
    v.wait_pause = static_cast<uint8_t>(w >> 24);
    v.guest_pause = static_cast<uint8_t>(w >> 32);
    v.log_lines_compact = static_cast<uint8_t>(w >> 40);
    v.log_lines_expanded = static_cast<uint8_t>(w >> 48);
    v.log_fade_s = static_cast<uint8_t>(w >> 56);
    return v;
}

constexpr uint64_t DEFAULT_WORD = pack(build(default_settings()));
static_assert(unpack(DEFAULT_WORD).log_lines_compact == 5 && unpack(DEFAULT_WORD).log_lines_expanded == 20 &&
                  unpack(DEFAULT_WORD).log_fade_s == 6 && unpack(DEFAULT_WORD).shared_camera == 1 &&
                  unpack(DEFAULT_WORD).guest_pause == 1,
              "the options word starts as the native defaults");
static_assert(effective_subsystems(default_settings()) == ALL_SUBSYSTEMS, "the defaults arm every subsystem");

volatile uint64_t g_word = DEFAULT_WORD;

}  // namespace

view from_settings(const ilc_settings& s)
{
    return build(s);
}

void configure_from_settings(const ilc_settings& s)
{
    __atomic_store_n(&g_word, pack(build(s)), __ATOMIC_RELEASE);
}

view current()
{
    return unpack(__atomic_load_n(&g_word, __ATOMIC_ACQUIRE));
}

bool guest_interactions()
{
    return current().guest_interactions != 0;
}

bool guest_pause()
{
    return current().guest_pause != 0;
}

uint8_t town_view()
{
    return current().town_view;
}

uint8_t wait_pause()
{
    return current().wait_pause;
}

void test_set(const view& v)
{
    __atomic_store_n(&g_word, pack(v), __ATOMIC_RELEASE);
}

void test_reset()
{
    __atomic_store_n(&g_word, DEFAULT_WORD, __ATOMIC_RELEASE);
}

}  // namespace ilc::options
