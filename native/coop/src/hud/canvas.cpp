// Ivalice Legacy co-op module: the HUD canvas builder, its publication and the Diagnostics test draw (see canvas.h).
#include "hud/canvas.h"

#include "hud/hud_internal.h"
#include "session/session.h"

namespace ilc::hud {

namespace detail {

frame_slot g_slot = {};
volatile uint32_t g_test_draw = 0;  // co-op thread writes, game thread reads

}  // namespace detail

namespace {

using detail::copy_bytes;

// The st2e banks' single-byte character table (game fact): ASCII -> code.
constexpr uint8_t QUESTION = 154;  // '?'

uint8_t code_of(char ch)
{
    const unsigned char c = static_cast<unsigned char>(ch);
    if (c >= 'A' && c <= 'Z') {
        return static_cast<uint8_t>(32 + (c - 'A'));
    }
    if (c >= 'a' && c <= 'z') {
        return static_cast<uint8_t>(58 + (c - 'a'));
    }
    if (c >= '0' && c <= '9') {
        return static_cast<uint8_t>(0x85 + (c - '0'));
    }
    switch (c) {
    case ' ': return 4;
    case '!': return 171;
    case '"': return 173;
    case '%': return 158;
    case '&': return 160;
    case '\'': return 172;
    case '(': return 174;
    case ')': return 175;
    case '+': return 166;
    case ',': return 167;
    case '-': return 165;
    case '.': return 168;
    case '/': return 162;
    case ':': return 169;
    case ';': return 170;
    case '?': return QUESTION;
    default: return 0;
    }
}

const char* const ATLAS_NAMES[ATLAS_COUNT] = {"battle_4_c", "partytop_4_c", "partytop_8_c", "battle_4_p",
                                              "icons_c",    "cursol_4_p",   "bt_win_0_c",   "buttonicon",
                                              "targetline_p"};

// Seat colours of the test draw (ilc_colour order), 0x80 = full: our own RGB choice, halved to the PS2 scale.
constexpr rgba scaled(uint8_t r, uint8_t g, uint8_t b)
{
    return {static_cast<uint8_t>((r + 1) / 2), static_cast<uint8_t>((g + 1) / 2), static_cast<uint8_t>((b + 1) / 2),
            0x80};
}
const rgba SEAT_RGB[ILC_ENUM_COUNT(ILC_COLOURS)] = {
    scaled(70, 130, 240),   // sapphire
    scaled(230, 60, 70),    // ruby
    scaled(245, 120, 190),  // pink
    scaled(60, 210, 110),   // emerald
    scaled(240, 190, 50),   // gold
    scaled(170, 100, 235),  // amethyst
    scaled(205, 127, 50),   // bronze
};

uint8_t g_seat_colour[ILC_SEATS] = {ILC_COLOUR_GOLD, ILC_COLOUR_SAPPHIRE, ILC_COLOUR_EMERALD, ILC_COLOUR_AMETHYST};
uint64_t g_last_build = 0;
uint32_t g_published = 0;  // the producer has a frame out
uint64_t g_generation = 0;
canvas g_canvas;

void bump(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

}  // namespace

const char* atlas_name(uint32_t atlas)
{
    return atlas < ATLAS_COUNT ? ATLAS_NAMES[atlas] : nullptr;
}

int32_t encode(const char* ascii, char* out, uint32_t cap, uint32_t* substituted)
{
    uint32_t n = 0;
    uint32_t subs = 0;
    if (ascii == nullptr || cap == 0) {
        return -1;
    }
    for (; ascii[n] != '\0'; ++n) {
        if (n + 1 >= cap || n >= TEXT_MAX) {
            return -1;
        }
        uint8_t code = code_of(ascii[n]);
        if (code == 0) {
            code = QUESTION;
            ++subs;
        }
        out[n] = static_cast<char>(code);
    }
    out[n] = '\0';
    if (substituted != nullptr) {
        *substituted = subs;
    }
    return static_cast<int32_t>(n);
}

// ---- builder ---------------------------------------------------------------------------------------------------
void canvas::begin(uint32_t epoch, uint64_t now_ms)
{
    f_.head.epoch = epoch;
    f_.head.flags = 0;
    f_.head.generation = ++g_generation;
    f_.built_ms = now_ms;
    f_.count = 0;
    f_.text_used = 0;
    refused_ = 0;
    cmd_limit_ = MAX_COMMANDS;
    text_limit_ = TEXT_POOL;
}

void canvas::reserve(uint32_t commands, uint32_t text)
{
    cmd_limit_ = commands < MAX_COMMANDS ? MAX_COMMANDS - commands : 0;
    text_limit_ = text < TEXT_POOL ? TEXT_POOL - text : 0;
}

bool canvas::text(int16_t x, int16_t y, const char* ascii, rgba colour, uint16_t scale, uint8_t font)
{
    if (f_.count >= cmd_limit_ || f_.text_used >= text_limit_ || scale == 0 || scale > 400) {
        ++refused_;
        return false;
    }
    const int32_t n = encode(ascii, f_.text + f_.text_used, text_limit_ - f_.text_used, nullptr);
    if (n <= 0) {
        ++refused_;
        return false;
    }
    cmd& c = f_.cmds[f_.count++];
    c = cmd{};
    c.kind = CMD_TEXT;
    c.font = font;
    c.x = x;
    c.y = y;
    c.scale = scale;
    c.text = static_cast<uint16_t>(f_.text_used);
    c.colour = pack(colour);
    f_.text_used += static_cast<uint32_t>(n) + 1;
    return true;
}

bool canvas::sprite(atlas_id atlas, uint8_t page, uint8_t sheet, uint16_t cell, int16_t x, int16_t y, rgba tint,
                    uint16_t w, uint16_t h, uint8_t palette)
{
    if (f_.count >= cmd_limit_ || atlas >= ATLAS_COUNT || w > 2048 || h > 2048 ||
        (palette != PALETTE_OWN && palette > 15)) {
        ++refused_;
        return false;
    }
    cmd& c = f_.cmds[f_.count++];
    c = cmd{};
    c.kind = CMD_SPRITE;
    c.atlas = atlas;
    c.page = page;
    c.sheet = sheet;
    c.palette = palette == PALETTE_OWN ? 0 : static_cast<uint8_t>(palette + 1);
    c.cell = cell;
    c.x = x;
    c.y = y;
    c.w = w;
    c.h = h;
    c.colour = pack(tint);
    return true;
}

bool canvas::bar(int16_t x, int16_t y, uint16_t w, uint16_t h, rgba colour)
{
    if (f_.count >= cmd_limit_ || w == 0 || h == 0 || w > 2048 || h > 512) {
        ++refused_;
        return false;
    }
    cmd& c = f_.cmds[f_.count++];
    c = cmd{};
    c.kind = CMD_BAR;
    c.x = x;
    c.y = y;
    c.w = w;
    c.h = h;
    c.colour = pack(colour);
    return true;
}

// ---- publication -----------------------------------------------------------------------------------------------
void publish(const frame& f)
{
    detail::frame_slot& s = detail::g_slot;
    const uint32_t odd = (s.seq + 1u) | 1u;
    __atomic_store_n(&s.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    copy_bytes(const_cast<frame*>(&s.value), &f, sizeof(frame));
    s.valid = 1;
    __atomic_store_n(&s.seq, odd + 1u, __ATOMIC_RELEASE);
    bump(detail::g_counters.published);
}

void withdraw()
{
    detail::frame_slot& s = detail::g_slot;
    const uint32_t odd = (s.seq + 1u) | 1u;
    __atomic_store_n(&s.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    s.valid = 0;
    __atomic_store_n(&s.seq, odd + 1u, __ATOMIC_RELEASE);
}

bool latest(frame& out)
{
    const detail::frame_slot& s = detail::g_slot;
    const uint32_t first = __atomic_load_n(&s.seq, __ATOMIC_ACQUIRE);
    if ((first & 1u) != 0) {
        return false;
    }
    const uint32_t ok = s.valid;
    copy_bytes(&out, const_cast<const frame*>(&s.value), sizeof(frame));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&s.seq, __ATOMIC_RELAXED) == first && ok == 1 && out.count <= MAX_COMMANDS &&
           out.text_used <= TEXT_POOL;
}

uint64_t ring_estimate(const frame& f)
{
    uint64_t bytes = 0;
    uint32_t run = 0;  // sprites in the open batch
    const uint64_t vertex4 = 4ull * facts::ARENA_VERTEX_BYTES;
    for (uint32_t k = 0; k < f.count && k < MAX_COMMANDS; ++k) {
        const cmd& c = f.cmds[k];
        if (c.kind == CMD_SPRITE) {
            if (run == 0 || run == facts::BATCH_SPRITES) {
                bytes += facts::ARENA_BATCH_BYTES;
                run = 0;
            }
            ++run;
            bytes += vertex4;
            continue;
        }
        run = 0;
        if (c.kind == CMD_BAR) {
            bytes += 3ull * (facts::ARENA_BATCH_BYTES + vertex4);  // cap, stretch, cap
        }
    }
    return bytes;
}

// ---- co-op thread: settings and the test draw ----------------------------------------------------------------------
void configure_from_settings(const ilc_settings& s)
{
    // Generation 0 is the native defaults record: the test draw stays off until the player's settings say otherwise.
    const bool on = s.header.generation != 0 && s.hud_test_draw != 0;
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        const uint8_t c = s.seats[k].colour;
        g_seat_colour[k] = c < ILC_ENUM_COUNT(ILC_COLOURS) ? c : static_cast<uint8_t>(ILC_COLOUR_GOLD);
    }
    __atomic_store_n(&detail::g_test_draw, on ? 1u : 0u, __ATOMIC_RELEASE);
}

bool test_draw_on()
{
    return __atomic_load_n(&detail::g_test_draw, __ATOMIC_ACQUIRE) != 0;
}

namespace {

// The test draw: one string per seat in its colour (the four lines of a full couch frame), the Protect status icon
// (partytop_4_c 3.0 cell 18) and a thin bar in P2's colour. Top centre, clear of the native windows.
void build_test_frame(canvas& cv, uint32_t epoch, uint64_t now_ms)
{
    static const char* const LINES[ILC_SEATS] = {"P1  Co-op canvas test", "P2  Native text, seat colour",
                                                 "P3  Font, outline and size", "P4  Four seats on one screen"};
    cv.begin(epoch, now_ms);
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        cv.text(760, static_cast<int16_t>(150 + 40 * k), LINES[k], SEAT_RGB[g_seat_colour[k]]);
    }
    cv.sprite(ATLAS_PARTYTOP_4_C, 3, 0, 18, 700, 140);
    cv.bar(760, 320, 400, 12, SEAT_RGB[g_seat_colour[1]]);
}

}  // namespace

void step(uint64_t now_ms)
{
    session::session_record rec;
    const bool world = session::read(&rec) && (rec.head.flags & session::GATE_WORLD) != 0 && rec.head.epoch != 0;
    if (!test_draw_on() || !world || !patch::subsystem_live(ILC_SUBSYS_RENDER)) {
        if (g_published != 0) {
            withdraw();
            g_published = 0;
        }
        return;
    }
    if (g_published != 0 && now_ms < g_last_build + 33 && now_ms >= g_last_build) {
        return;  // about 30 frames a second is plenty for a test pattern
    }
    build_test_frame(g_canvas, rec.head.epoch, now_ms);
    publish(g_canvas.view());
    g_last_build = now_ms;
    g_published = 1;
}

void stop()
{
    withdraw();
    g_published = 0;
}

void test_produce(uint32_t epoch, uint64_t now_ms)
{
    build_test_frame(g_canvas, epoch, now_ms);
    publish(g_canvas.view());
}

void test_set_test_draw(bool on)
{
    __atomic_store_n(&detail::g_test_draw, on ? 1u : 0u, __ATOMIC_RELEASE);
}

}  // namespace ilc::hud
