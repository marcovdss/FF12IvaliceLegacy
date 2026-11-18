// Ivalice Legacy co-op overlay: the HUD theme (Phase 8, E5; Marco 2026-11-16: "a moderne look but that still feels
// vanilla/natural but improved", direction C "Bold Modern" the default, A and B built in, player-made themes allowed).
//
// A theme is data (THEME-FORMAT.md): theme.json's tokens (colours, sizes, window shape, tab, focused row, gauge, status
// icons, the layered mark recipes, the carousel) and its font (font.h: the atlas, glyph and kerning rows). Every look
// the overlay draws comes from the active theme's `tokens`; the geometry (the seat slots, the HUD element positions)
// stays in the engine (scene.h, hud_scene.h).
//
// Themes. The built-ins (theme_builtin.h: legacy-bold = C, the default; legacy-humanist = A; legacy-crisp = B) and the
// player themes found under x64/scripts/config/IvaliceLegacy/themes/<id>/ (theme.json, atlas/theme.atlas and an optional
// icons/icons.json; nothing else is read: no fonts, no code). Every theme, built-in or not, is read by the same rules:
//   * theme.json <= 64 KiB, strict JSON (theme_json.h), format "ilc-theme" version 1, the required sections, every value
//     in its range (THEME-FORMAT.md 2), the converter's error rules (help >= 17, row 18..30, pitch >= 1.15 x row, text
//     outline 0.8..3, core vs the drawn outline colour >= 7:1 and hp_warn / hp_critical / drop_rare vs it >= 4.5:1 (the
//     outline is text.ink when given, else colors.ink), four seats numbered 1..4, 2..4 ordered window stops, known
//     shapes and kinds, gauge.ticks_every 0 or 0.02..0.5, motion.* in the spec's ranges, text.tracking and motion with
//     no unknown keys), the id lower-case letters, digits, '-' or '_' and equal to the folder's name, no built-in id;
//   * theme.atlas: magic "ILCA", version 2, <= 5 text faces, <= 4096 glyphs, <= 20000 kerning pairs, <= 1 MiB decoded,
//     every row and cell checked (font::tables_valid), the run-length data decoding to exactly width x height, and the
//     SHA-256 digest recomputed over the decoded atlas and the tables as the converter wrote it;
//   * icons.json (or the converter's built-in icon order when theme.json names none): <= 256 KiB, one name per atlas
//     icon, the eight required names, no duplicates; recipes may name only icons that exist;
//   * any refusal: the built-in default stays (or comes back), one hook.log line names the theme and the reason. A
//     theme is applied whole or not at all.
//
// Threads.
//   * attach (boot thread): the default theme is read from its embedded theme.json and made active before any frame.
//   * The co-op thread (step) scans the themes folder (one folder per beat), reads and checks the selected theme's files,
//     decodes its atlas and builds its tokens into a slot it owns, then publishes the slot (pending). It frees retired
//     slots. Files are never read on the render thread.
//   * The render thread takes a pending slot at the start of a frame (take_pending): from then on the composition reads
//     its tokens and font, and gpu.cpp re-creates the atlas texture from it (one immutable R8 texture, no stall). The
//     slot it replaced is retired for the co-op thread to free (one retired slot at a time: a pending slot waits a frame
//     while one is still retired).
//   * The bridge (script threads) reads the theme list (list) under a lock the scan holds only to copy entries.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "overlay/batch.h"
#include "overlay/font.h"

namespace ilc::overlay::theme {

constexpr uint32_t ID_MAX = ILC_THEME_ID_MAX;       // bytes of a theme id
constexpr uint32_t NAME_MAX = ILC_THEME_NAME_MAX;   // bytes of a shown name (printable ASCII)
constexpr uint32_t STOPS_MAX = 4;
constexpr uint32_t RECIPE_MAX = 6;                  // layers in one mark
constexpr uint32_t STATUS_ICONS_MAX = 8;
constexpr uint32_t JSON_BYTES_MAX = 64u * 1024u;
constexpr uint32_t ICONS_JSON_BYTES_MAX = 256u * 1024u;
constexpr uint32_t PLAYER_THEMES_MAX = ILC_THEMES_MAX - 3;   // the list holds the three built-ins first
// gauge.ticks_every: 0 (none) or at least this fraction; the drawing caps the ticks at GAUGE_TICKS_DRAWN (review ★1:
// a tiny fraction hung the render thread in the tick loop).
constexpr float GAUGE_TICKS_MIN = 0.02f;
constexpr uint32_t GAUGE_TICKS_DRAWN = 50;
// motion.* (THEME-FORMAT.md 2): the carousel's ranges.
constexpr float MARQUEE_SPEED_MIN = 10.0f, MARQUEE_SPEED_MAX = 120.0f;   // units per second
constexpr uint32_t MARQUEE_HOLD_MAX_MS = 5000;
constexpr float MARQUEE_FADE_MAX = 40.0f;                                // units

enum shape : uint8_t { SHAPE_ROUND = 0, SHAPE_CRISP = 1, SHAPE_CHAMFER = 2 };
enum shine_kind : uint8_t { SHINE_NONE = 0, SHINE_SOFT = 1, SHINE_LINE = 2, SHINE_DIAG = 3 };
enum tab_kind : uint8_t { TAB_A = 0, TAB_B = 1, TAB_C = 2 };   // A accent + "P1 - Name", B diamond + underline, C block
enum gauge_kind : uint8_t { GAUGE_A = 0, GAUGE_B = 1, GAUGE_C = 2 };   // rounded, square with ticks, slanted
enum status_style : uint8_t { STATUS_MEDALLION = 0, STATUS_CHIP = 1, STATUS_HEX = 2 };

// A recipe colour: a fixed rgba or a reference the caller resolves (THEME-FORMAT.md 3).
enum colour_ref : uint8_t {
    CR_NONE = 0, CR_RGBA, CR_SEAT, CR_SEAT_LIGHT, CR_SEAT_DARK, CR_INK, CR_WHITE, CR_WHITE_SOFT, CR_INK_SOFT, CR_CHEVRON,
    CR_DOT, CR_STATUS, CR_STATUS_LIGHT, CR_STATUS_MID, CR_STATUS_DARK,
};
struct colour {
    uint8_t ref;           // colour_ref
    uint8_t reserved[3];
    uint32_t rgba;         // CR_RGBA
};
enum glyph_template : uint8_t { GT_PLAIN = 0, GT_DIR = 1, GT_NAME = 2 };
constexpr uint32_t GLYPH_NAME_MAX = 40;             // bytes of a recipe glyph name (template included)
constexpr uint32_t GLYPH_PREFIX_MAX = GLYPH_NAME_MAX - 6;   // the part before "{name}"
struct layer {
    uint8_t tmpl;          // glyph_template
    uint8_t reserved[3];
    uint16_t glyph[STATUS_ICONS_MAX];   // icon index: [0] plain; [0..3] up/down/left/right; [k] status k (ICON_NONE: skip)
    char prefix[GLYPH_PREFIX_MAX + 1];  // GT_NAME: the glyph name before "{name}" (re-resolved by status name when a
                                        // theme inherits the recipe with its own status list)
    colour core_top, core_bottom, edge, outline;
    float edge_px;         // units of the quad's size
    float grow;            // units added on every side
    float glow_px;
    float glow_alpha;      // 0..1
};
struct recipe {
    uint32_t n;
    layer l[RECIPE_MAX];
};

struct tokens {
    char id[ID_MAX + 1];
    char name[NAME_MAX + 1];

    // ---- text ----
    float outline;                 // text.outline (units)
    uint32_t ink;                  // text.ink: the outline colour (alpha 235)
    uint32_t shadow;               // text.shadow: the ink at its alpha
    float shadow_dx, shadow_dy, shadow_soft;
    float small_below_px, small_tracking_em;
    uint8_t page_face;             // text.tracking.page_face: font::FACE_BODY (the spec default) or FACE_TITLE
    uint8_t reserved_t[3];
    float title_track;             // text.tracking.title_em: window page titles (em; default 0)
    float header_track;            // text.tracking.header_em: panel headers and labels (em; default 0.06)

    // ---- sizes (em sizes in units) ----
    float row_em, value_em, header_em, tab_em, help_em, glove, glove_out, panel_head, target_em, party_name_em, hp_em,
        max_em, mpv_em, act_em, log_em;

    // ---- colours ----
    uint32_t core, dim, header, help, gold, foe_dot, heal, dmg, low, mpl, chev, rule, rule2;
    uint32_t hp_warn, hp_critical, hp_critical_glow, drop_rare;
    uint32_t seat[4];              // the default seat palette (players' own colours win)
    uint32_t gambit_ally;

    // ---- the command window ----
    uint8_t win_shape;             // shape
    uint8_t win_stops;
    uint8_t shine;                 // shine_kind
    uint8_t reserved_w;
    float win_radius, win_cut;
    float stop_t[STOPS_MAX];
    uint32_t stop_c[STOPS_MAX];
    uint32_t win_rim;
    float win_rim_px, win_rim_top_bias;
    uint32_t inner_line;           // alpha 0: none
    float shine_amount, shine_extent;
    uint32_t shine_rgba;
    uint32_t outer_line;           // alpha 0: none
    float outer_px;
    uint32_t halo;                 // alpha 0: none
    float halo_sigma;

    // ---- the seat tab ----
    uint8_t tab_kind;
    uint8_t tab_shape;
    uint8_t reserved_tab[2];
    float tab_h, tab_dx, tab_radius;
    uint32_t tab_top, tab_bottom;

    // ---- the focused row ----
    uint8_t focus_bar;
    uint8_t focus_alpha_top, focus_alpha_bottom, focus_edge_alpha;
    float focus_darken;

    // ---- gauges ----
    uint8_t gauge_kind;
    uint8_t trail_hatch;
    uint8_t reserved_g[2];
    float gauge_h, gauge_slant, gauge_ticks;
    uint32_t g_hp[2], g_low[2], g_mp[2], g_act[2];   // top, bottom
    uint32_t g_trail;
    uint32_t g_track;
    float warn_at, critical_at, critical_glow_px;

    // ---- status icons (statuses with an icon of their own; the rest keep the stand-in token) ----
    uint8_t status_style;
    uint8_t status_count;
    uint8_t status_bit[STATUS_ICONS_MAX];   // the game's status bit of each named icon
    uint8_t reserved_s[2];
    uint32_t status_rgb[STATUS_ICONS_MAX];

    // ---- marks ----
    recipe glove_r, chevron_r, diamond_r, dot_r, status_r;

    // ---- the carousel (motion.marquee_*; engine defaults 40 / 1000 / 12 / true) ----
    float marquee_speed;           // units per second, MARQUEE_SPEED_MIN..MAX
    uint32_t marquee_hold_ms;      // 0..MARQUEE_HOLD_MAX_MS
    float marquee_fade;            // units, 0..MARQUEE_FADE_MAX
    uint8_t marquee_on;
    uint8_t reserved_m[3];

    // ---- derived text styles (resolve(): from the tokens above) ----
    text_style row, grey, help_st, header_st, value, on, hdr, tag, label, numeral, low_hp, word, word_cost, party_max,
        mp_label, gambit_g, danger, anti_libra, state_hot, state, drop_rare_st, status_mark, icon_st, card_label;

    // ---- engine constants (no theme field; kept here so every look is in one place) ----
    uint32_t leader_band, log_mp, log_important, log_miss, log_holding, log_dot_system, status_token_rim, ti_fill_left,
        ti_fill_right, card_top, card_bottom, card_rim, card_shine;
    float glove_bob;               // the glove's bob (units)
};

// The active theme's tokens (render thread; host tests). Never null: the built-in default until a theme is applied.
const tokens& active();

// ---- reading themes (any thread with its own buffers; the module uses the co-op and boot threads) --------------------
enum refusal : uint8_t {
    OK = 0, NO_FILE, TOO_LARGE, READ_FAILED, BAD_JSON, BAD_FORMAT, BAD_VERSION, MISSING, BAD_VALUE, BAD_ID, BUILTIN_ID,
    BAD_ATLAS, BAD_DIGEST, BAD_ICONS, NO_MEMORY,
};
const char* refusal_text(uint32_t r);

struct report {
    uint32_t code;                 // refusal
    char what[96];                 // the field or file it concerns ("sizes.row 14: 18..30")
};

// A theme read into memory: tokens, its font set and the buffers both point into.
struct slot;

// Reads built-in `k` into a new slot (allocates). nullptr with *r set on failure (only a broken build could fail).
slot* make_builtin(uint32_t k, report* r);
// Reads the player theme in folder `dir` (UTF-16, no trailing slash) whose folder name is `id`. nullptr with *r set.
slot* make_player(const wchar_t* dir, const char* id, report* r);
void free_slot(slot* s);
const tokens* tokens_of(const slot* s);
const font::set* font_of(const slot* s);

// Checks one theme.json text against the rules and fills `out` (from the default's tokens for the optional sections).
// `icon_names` maps the atlas's icon order to names (count = icons); `id` the folder name (nullptr: not checked).
bool read_json(const char* text, uint32_t len, const char* id, const char* const* icon_names, uint32_t icons,
               font::set* font, tokens* out, report* r);

// ---- the module (theme.cpp) ------------------------------------------------------------------------------------------
// Boot thread: the default theme made active; the themes folder under x64_dir (scanned by step). False only if the
// default theme cannot be read (then the font's constant tables stand in and nothing is drawn: gpu has no atlas).
bool attach(const wchar_t* x64_dir);
// Co-op thread and the boot: the theme the settings ask for (hud_theme; all zero = the default).
void configure_from_settings(const ilc_settings& s);
// Co-op thread, once per beat: scan, load, publish, free, and the hook.log lines.
void step(uint64_t now_ms);
// Render thread, at the start of a frame: takes a pending theme. True when the active theme changed this frame (the
// caller re-creates the atlas texture). The atlas of the active theme: atlas_bytes().
bool take_pending();
const uint8_t* atlas_bytes();
uint32_t atlas_generation();       // changes with every applied theme (gpu re-creates its texture when it differs)
// Render thread: the texture for the active theme could not be created. The co-op thread falls back to the default.
void note_atlas_failed();

// Bridge (any thread): the theme list for the Mod Menu: the built-ins, then the valid player themes found.
void list(ilc_themes_reply* out);

struct status_view {
    char active_id[ID_MAX + 1];
    char wanted_id[ID_MAX + 1];
    uint32_t players_valid;        // player themes that passed
    uint32_t players_refused;      // folders refused (broken packages, names that are not an id); never the ignored
    uint32_t scans;                // completed scans
    uint32_t applied;              // themes applied (render-thread swaps)
    uint32_t players_ignored;      // valid player themes left out for lack of slots (each named in hook.log)
};
status_view status();

// ---- host tests -------------------------------------------------------------------------------------------------------
void test_reset();
// Makes theme `s` active at once (no pending/retire; the caller keeps ownership). nullptr: the built-in default.
void test_activate(const slot* s);
// Points the folder scan at `dir` (UTF-16) and runs scans and loads synchronously.
void test_set_root(const wchar_t* dir);

}  // namespace ilc::overlay::theme
