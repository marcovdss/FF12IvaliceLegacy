// Ivalice Legacy co-op overlay: the HUD theme module (see theme.h): the active theme, the hand-over to the render
// thread, the themes folder scan, the theme list and the hook.log lines.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "overlay/theme_builtin.h"
#include "overlay/theme_internal.h"
#include "runtime/log.h"

namespace ilc::overlay::theme {
namespace {

constexpr uint32_t SCAN_FOLDERS_MAX = 64;      // folders looked at per scan (more are counted as refused)
constexpr uint64_t RESCAN_AFTER_MS = 5000;     // the theme list asks for a new scan when the last one is older
constexpr uint32_t LOGGED_MAX = 32;            // refusals already named in hook.log this launch

tokens g_default;
volatile uint32_t g_default_ready = 0;

// Render thread (and attach / host tests before any frame).
slot* g_active_slot = nullptr;
const tokens* g_active_tokens = nullptr;
slot* volatile g_pending = nullptr;
slot* volatile g_retired = nullptr;
volatile uint32_t g_atlas_gen = 0;
volatile uint32_t g_atlas_failed = 0;
volatile uint32_t g_applied = 0;

// Co-op thread (configure_from_settings runs there and on the boot's init thread, which becomes the co-op thread).
wchar_t g_root[1024];
bool g_have_root = false;
char g_wanted[ID_MAX + 1];
char g_current[ID_MAX + 1];        // the theme last handed to the render thread
char g_refused[ID_MAX + 1];        // the wanted theme that could not be read (not retried until it changes or a scan finds it)
bool g_fallback = false;           // the active texture failed: the default only
uint32_t g_logged[LOGGED_MAX];
uint32_t g_logged_n = 0;
char g_builtin_names[builtin::COUNT][NAME_MAX + 1];

// The scan (co-op thread) and the list it publishes (read by the bridge under the lock).
struct entry {
    char id[ID_MAX + 1];
    char name[NAME_MAX + 1];
};
SRWLOCK g_lock = SRWLOCK_INIT;
entry g_entries[PLAYER_THEMES_MAX];
uint32_t g_entry_count = 0;
uint32_t g_refused_count = 0;
uint32_t g_ignored_count = 0;   // valid player themes left out of the last scan's list for lack of slots
uint32_t g_scans = 0;
char g_listed_current[ID_MAX + 1];
char g_listed_wanted[ID_MAX + 1];
volatile uint32_t g_rescan_wanted = 0;
volatile uint64_t g_last_scan_ms = 0;
bool g_scanning = false;
char g_scan_names[SCAN_FOLDERS_MAX][ID_MAX + 1];
uint32_t g_scan_n = 0;
uint32_t g_scan_i = 0;
uint32_t g_scan_skipped = 0;
entry g_scan_ok[PLAYER_THEMES_MAX];
uint32_t g_scan_ok_n = 0;
uint32_t g_scan_refused = 0;
uint32_t g_scan_ignored = 0;

void copy_id(char* dst, const char* src, uint32_t cap)
{
    uint32_t k = 0;
    while (src != nullptr && k + 1 < cap && src[k] != '\0') {
        dst[k] = src[k];
        ++k;
    }
    while (k < cap) {
        dst[k++] = '\0';
    }
}

bool same_id(const char* a, const char* b)
{
    uint32_t k = 0;
    while (k <= ID_MAX && a[k] == b[k]) {
        if (a[k] == '\0') {
            return true;
        }
        ++k;
    }
    return false;
}

// The wanted id, with the empty id standing for the default.
const char* effective(const char* id)
{
    return id[0] != '\0' ? id : builtin::get(builtin::DEFAULT).id;
}

uint32_t hash_of(const char* id, uint32_t code)
{
    uint32_t h = 2166136261u ^ code;
    for (uint32_t k = 0; k < ID_MAX && id[k] != '\0'; ++k) {
        h = (h ^ static_cast<uint8_t>(id[k])) * 16777619u;
    }
    return h;
}

// One hook.log line per theme and reason per launch.
void log_refusal(const char* id, const report& r, bool selected)
{
    const uint32_t h = hash_of(id, r.code + (selected ? 0x100u : 0u));
    for (uint32_t k = 0; k < g_logged_n; ++k) {
        if (g_logged[k] == h) {
            return;
        }
    }
    if (g_logged_n < LOGGED_MAX) {
        g_logged[g_logged_n++] = h;
    }
    log::line l;
    l.s("HUD theme \"").s(id).s("\" refused: ").s(refusal_text(r.code));
    if (r.what[0] != '\0') {
        l.s(" (").s(r.what).s(")");
    }
    l.s(selected ? "; drawing the built-in default (legacy-bold)" : "; not offered in the Mod Menu");
    log::post(log::WARNING, l);
}

// A valid player theme past the list's room: one line per theme per launch, worded as what it is.
void log_ignored(const char* id)
{
    const uint32_t h = hash_of(id, 0x200u);
    for (uint32_t k = 0; k < g_logged_n; ++k) {
        if (g_logged[k] == h) {
            return;
        }
    }
    if (g_logged_n < LOGGED_MAX) {
        g_logged[g_logged_n++] = h;
    }
    log::line l;
    l.s("HUD theme \"").s(id).s("\" ignored: too many themes (the Mod Menu lists the built-ins and ")
        .u(PLAYER_THEMES_MAX)
        .s(" player themes); not offered in the Mod Menu");
    log::post(log::WARNING, l);
}

void activate(slot* s)
{
    g_active_slot = s;
    g_active_tokens = s != nullptr ? &s->t : nullptr;
    font::set_active(s != nullptr ? &s->f : nullptr);
    __atomic_add_fetch(&g_atlas_gen, 1u, __ATOMIC_RELEASE);
}

void publish(slot* s, const char* id)
{
    slot* old = __atomic_exchange_n(&g_pending, s, __ATOMIC_ACQ_REL);
    free_slot(old);   // never taken by the render thread: ours to free
    AcquireSRWLockExclusive(&g_lock);
    copy_id(g_current, id, sizeof(g_current));
    copy_id(g_listed_current, id, sizeof(g_listed_current));
    ReleaseSRWLockExclusive(&g_lock);
}

bool valid_folder_name(const wchar_t* w, char* out)
{
    uint32_t k = 0;
    for (; w[k] != L'\0'; ++k) {
        const wchar_t ch = w[k];
        if (k >= ID_MAX || !((ch >= L'a' && ch <= L'z') || (ch >= L'0' && ch <= L'9') || ch == L'-' || ch == L'_')) {
            return false;
        }
        out[k] = static_cast<char>(ch);
    }
    out[k] = '\0';
    return k != 0;
}

bool folder_of(const char* id, wchar_t* out, uint32_t cap)
{
    uint32_t n = 0;
    while (g_root[n] != L'\0') {
        if (n + 1 >= cap) {
            return false;
        }
        out[n] = g_root[n];
        ++n;
    }
    if (n + 2 + ID_MAX >= cap) {
        return false;
    }
    out[n++] = L'\\';
    for (uint32_t k = 0; id[k] != '\0' && k < ID_MAX; ++k) {
        out[n++] = static_cast<wchar_t>(id[k]);
    }
    out[n] = L'\0';
    return true;
}

// The folder list for a new scan (names only; each is read in its own beat).
void scan_begin()
{
    g_scan_n = 0;
    g_scan_i = 0;
    g_scan_skipped = 0;
    g_scan_ok_n = 0;
    g_scan_refused = 0;
    g_scan_ignored = 0;
    g_scanning = true;
    wchar_t pattern[1100];
    uint32_t n = 0;
    while (g_root[n] != L'\0' && n + 4 < 1100) {
        pattern[n] = g_root[n];
        ++n;
    }
    pattern[n++] = L'\\';
    pattern[n++] = L'*';
    pattern[n] = L'\0';
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileExW(pattern, FindExInfoBasic, &fd, FindExSearchLimitToDirectories, nullptr, 0);
    if (h == INVALID_HANDLE_VALUE) {
        return;   // no themes folder: an empty list
    }
    do {
        if ((fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0 || fd.cFileName[0] == L'.') {
            continue;
        }
        char id[ID_MAX + 1];
        if (!valid_folder_name(fd.cFileName, id)) {
            ++g_scan_skipped;   // not a theme id (README files, other folders): counted as refused, named below
            report r{};
            r.code = BAD_ID;
            copy_id(r.what, "the folder name must be a theme id (a-z, 0-9, '-', '_')", sizeof(r.what));
            log_refusal("(folder)", r, false);
            continue;
        }
        if (g_scan_n >= SCAN_FOLDERS_MAX) {
            ++g_scan_skipped;
            continue;
        }
        copy_id(g_scan_names[g_scan_n++], id, ID_MAX + 1);
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

// One folder per beat; publishes the list when done.
void scan_step()
{
    if (g_scan_i < g_scan_n) {
        const char* id = g_scan_names[g_scan_i++];
        wchar_t dir[1100];
        report r{};
        slot* s = folder_of(id, dir, 1100) ? make_player(dir, id, &r) : nullptr;
        if (s != nullptr && g_scan_ok_n < PLAYER_THEMES_MAX) {
            copy_id(g_scan_ok[g_scan_ok_n].id, id, ID_MAX + 1);
            copy_id(g_scan_ok[g_scan_ok_n].name, s->t.name, NAME_MAX + 1);
            ++g_scan_ok_n;
        } else if (s != nullptr) {
            // Valid, but the list is full (the built-ins and PLAYER_THEMES_MAX player themes): not a refusal, so it
            // is counted on its own (g_scan_ignored), never in the refused count.
            ++g_scan_ignored;
            log_ignored(id);
        } else {
            ++g_scan_refused;
            log_refusal(id, r, false);
        }
        free_slot(s);
        return;
    }
    AcquireSRWLockExclusive(&g_lock);
    for (uint32_t k = 0; k < g_scan_ok_n; ++k) {
        g_entries[k] = g_scan_ok[k];
    }
    g_entry_count = g_scan_ok_n;
    g_refused_count = g_scan_refused + g_scan_skipped;
    g_ignored_count = g_scan_ignored;
    ++g_scans;
    ReleaseSRWLockExclusive(&g_lock);
    g_scanning = false;
    __atomic_store_n(&g_last_scan_ms, GetTickCount64(), __ATOMIC_RELEASE);
    // A wanted theme that could not be read is tried again once a scan finds it valid.
    for (uint32_t k = 0; k < g_scan_ok_n; ++k) {
        if (g_refused[0] != '\0' && same_id(g_scan_ok[k].id, g_refused)) {
            g_refused[0] = '\0';
        }
    }
}

// Reads the wanted theme (or the default) and hands it to the render thread when it is not already there.
void select_step()
{
    const char* want = g_fallback ? builtin::get(builtin::DEFAULT).id : effective(g_wanted);
    if (same_id(want, g_current) || (g_refused[0] != '\0' && same_id(want, g_refused))) {
        return;
    }
    const uint32_t k = builtin::find(want, font::length(want, ID_MAX + 1));
    report r{};
    slot* s = nullptr;
    if (k < builtin::COUNT) {
        s = make_builtin(k, &r);
    } else if (g_have_root) {
        wchar_t dir[1100];
        if (folder_of(want, dir, 1100)) {
            s = make_player(dir, want, &r);
        } else {
            r.code = READ_FAILED;
        }
    } else {
        r.code = NO_FILE;
    }
    if (s != nullptr) {
        publish(s, want);
        if (log::verbose()) {
            log::line l;
            l.s("HUD theme \"").s(want).s("\" read; drawn from the next frame");
            log::post(log::INFO, l, log::VERBOSE);
        }
        return;
    }
    log_refusal(want, r, true);
    copy_id(g_refused, want, sizeof(g_refused));
    const char* def = builtin::get(builtin::DEFAULT).id;
    if (!same_id(g_current, def)) {
        slot* d = make_builtin(builtin::DEFAULT, &r);
        if (d != nullptr) {
            publish(d, def);
        }
    }
}

}  // namespace

// ---- tokens ---------------------------------------------------------------------------------------------------------------
const tokens& default_tokens()
{
    if (__atomic_load_n(&g_default_ready, __ATOMIC_ACQUIRE) == 0) {
        // The built-in default's own theme.json, on the engine's base values; if it ever failed (a broken build: the host
        // tests read it), the base values stand.
        base_tokens(&g_default);
        resolve(&g_default);
        report r{};
        slot* s = make_builtin(builtin::DEFAULT, &r);
        if (s != nullptr) {
            g_default = s->t;
            free_slot(s);
        }
        __atomic_store_n(&g_default_ready, 1u, __ATOMIC_RELEASE);
    }
    return g_default;
}

const tokens& active()
{
    return g_active_tokens != nullptr ? *g_active_tokens : default_tokens();
}

// ---- the module ---------------------------------------------------------------------------------------------------------
bool attach(const wchar_t* x64_dir)
{
    default_tokens();
    for (uint32_t k = 0; k < builtin::COUNT; ++k) {
        report r{};
        slot* s = make_builtin(k, &r);
        copy_id(g_builtin_names[k], s != nullptr ? s->t.name : builtin::get(k).id, NAME_MAX + 1);
        if (k == builtin::DEFAULT && s != nullptr && g_active_slot == nullptr) {
            activate(s);
            copy_id(g_current, s->t.id, sizeof(g_current));
            copy_id(g_listed_current, s->t.id, sizeof(g_listed_current));
            continue;
        }
        free_slot(s);
    }
    g_have_root = false;
    if (x64_dir != nullptr && x64_dir[0] != L'\0') {
        static const wchar_t relative[] = L"\\scripts\\config\\IvaliceLegacy\\themes";
        uint32_t n = 0;
        while (x64_dir[n] != L'\0' && n < 900) {
            g_root[n] = x64_dir[n];
            ++n;
        }
        if (x64_dir[n] == L'\0') {
            for (uint32_t k = 0; k < sizeof(relative) / sizeof(wchar_t); ++k) {
                g_root[n + k] = relative[k];
            }
            g_have_root = true;
            __atomic_store_n(&g_rescan_wanted, 1u, __ATOMIC_RELEASE);
        }
    }
    return g_active_slot != nullptr;
}

void configure_from_settings(const ilc_settings& s)
{
    char want[ID_MAX + 1];
    uint32_t k = 0;
    for (; k < ID_MAX && s.hud_theme[k] != 0; ++k) {
        want[k] = static_cast<char>(s.hud_theme[k]);   // the bridge allowed only id characters
    }
    want[k] = '\0';
    if (!same_id(want, g_wanted)) {
        copy_id(g_wanted, want, sizeof(g_wanted));
        g_refused[0] = '\0';
        AcquireSRWLockExclusive(&g_lock);
        copy_id(g_listed_wanted, effective(g_wanted), sizeof(g_listed_wanted));
        ReleaseSRWLockExclusive(&g_lock);
    }
}

void step(uint64_t now_ms)
{
    (void)now_ms;
    slot* old = __atomic_exchange_n(&g_retired, static_cast<slot*>(nullptr), __ATOMIC_ACQ_REL);
    free_slot(old);
    if (__atomic_exchange_n(&g_atlas_failed, 0u, __ATOMIC_ACQ_REL) != 0 && !g_fallback) {
        g_fallback = true;
        const char* def = builtin::get(builtin::DEFAULT).id;
        log::line l;
        l.s("HUD theme \"").s(g_current).s("\": its atlas texture could not be created; drawing ").s(def)
            .s(" for the rest of the launch");
        log::post(log::WARNING, l);
        if (same_id(g_current, def)) {
            g_current[0] = '\0';   // the default itself failed: hand it over once more (a new texture is tried)
        }
    }
    if (g_have_root) {
        if (g_scanning) {
            scan_step();
        } else if (__atomic_exchange_n(&g_rescan_wanted, 0u, __ATOMIC_ACQ_REL) != 0) {
            scan_begin();
        }
    }
    select_step();
}

bool take_pending()
{
    if (__atomic_load_n(&g_retired, __ATOMIC_ACQUIRE) != nullptr) {
        return false;   // the co-op thread has not freed the last one yet: next frame
    }
    slot* p = __atomic_exchange_n(&g_pending, static_cast<slot*>(nullptr), __ATOMIC_ACQ_REL);
    if (p == nullptr) {
        return false;
    }
    slot* old = g_active_slot;
    activate(p);
    __atomic_add_fetch(&g_applied, 1u, __ATOMIC_RELAXED);
    __atomic_store_n(&g_retired, old, __ATOMIC_RELEASE);
    return true;
}

const uint8_t* atlas_bytes()
{
    return font::active().atlas;   // the active font set's atlas (the active slot's; host tests may activate their own)
}

uint32_t atlas_generation()
{
    return __atomic_load_n(&g_atlas_gen, __ATOMIC_ACQUIRE);
}

void note_atlas_failed()
{
    __atomic_store_n(&g_atlas_failed, 1u, __ATOMIC_RELEASE);
}

void list(ilc_themes_reply* out)
{
    const auto put = [](ilc_theme_entry& e, const char* id, const char* name) {
        for (uint32_t k = 0; k < ID_MAX; ++k) {
            e.id[k] = 0;
        }
        for (uint32_t k = 0; k < NAME_MAX; ++k) {
            e.name[k] = 0;
        }
        for (uint32_t k = 0; k < ID_MAX && id[k] != '\0'; ++k) {
            e.id[k] = static_cast<uint8_t>(id[k]);
        }
        for (uint32_t k = 0; k < NAME_MAX && name[k] != '\0'; ++k) {
            e.name[k] = static_cast<uint8_t>(name[k]);
        }
    };
    AcquireSRWLockShared(&g_lock);
    uint32_t n = 0;
    for (uint32_t k = 0; k < builtin::COUNT; ++k) {
        ilc_theme_entry& e = out->entries[n++];
        const char* id = builtin::get(k).id;
        put(e, id, g_builtin_names[k][0] != '\0' ? g_builtin_names[k] : id);
        e.flags = ILC_THEME_BUILTIN | (k == builtin::DEFAULT ? ILC_THEME_DEFAULT : 0);
    }
    for (uint32_t k = 0; k < g_entry_count && n < ILC_THEMES_MAX; ++k) {
        ilc_theme_entry& e = out->entries[n++];
        put(e, g_entries[k].id, g_entries[k].name);
        e.flags = 0;
    }
    const char* selected = g_listed_wanted[0] != '\0' ? g_listed_wanted : builtin::get(builtin::DEFAULT).id;
    for (uint32_t k = 0; k < n; ++k) {
        ilc_theme_entry& e = out->entries[k];
        char id[ID_MAX + 1];
        for (uint32_t q = 0; q < ID_MAX; ++q) {
            id[q] = static_cast<char>(e.id[q]);
        }
        id[ID_MAX] = '\0';
        if (same_id(id, g_listed_current)) {
            e.flags |= ILC_THEME_ACTIVE;
        }
        if (same_id(id, selected)) {
            e.flags |= ILC_THEME_SELECTED;
        }
    }
    out->count = n;
    out->refused = g_refused_count;
    out->scans = g_scans;
    ReleaseSRWLockShared(&g_lock);
    // A Mod Menu that keeps asking sees new folders: a fresh scan when the last one is older than RESCAN_AFTER_MS.
    const uint64_t last = __atomic_load_n(&g_last_scan_ms, __ATOMIC_ACQUIRE);
    if (last != 0 && GetTickCount64() - last >= RESCAN_AFTER_MS) {
        __atomic_store_n(&g_rescan_wanted, 1u, __ATOMIC_RELEASE);
    }
}

status_view status()
{
    status_view v = {};
    AcquireSRWLockShared(&g_lock);
    copy_id(v.active_id, g_listed_current, sizeof(v.active_id));
    copy_id(v.wanted_id, g_listed_wanted[0] != '\0' ? g_listed_wanted : builtin::get(builtin::DEFAULT).id,
            sizeof(v.wanted_id));
    v.players_valid = g_entry_count;
    v.players_refused = g_refused_count;
    v.players_ignored = g_ignored_count;
    v.scans = g_scans;
    ReleaseSRWLockShared(&g_lock);
    v.applied = __atomic_load_n(&g_applied, __ATOMIC_RELAXED);
    return v;
}

// ---- host tests -------------------------------------------------------------------------------------------------------------
void test_reset()
{
    free_slot(__atomic_exchange_n(&g_pending, static_cast<slot*>(nullptr), __ATOMIC_ACQ_REL));
    free_slot(__atomic_exchange_n(&g_retired, static_cast<slot*>(nullptr), __ATOMIC_ACQ_REL));
    g_wanted[0] = '\0';
    g_refused[0] = '\0';
    g_fallback = false;
    g_logged_n = 0;
    g_scanning = false;
    g_atlas_failed = 0;
    AcquireSRWLockExclusive(&g_lock);
    g_entry_count = 0;
    g_refused_count = 0;
    g_ignored_count = 0;
    g_scans = 0;
    g_listed_wanted[0] = '\0';
    ReleaseSRWLockExclusive(&g_lock);
    if (g_active_slot == nullptr) {
        attach(nullptr);
    } else {
        copy_id(g_current, g_active_slot->t.id, sizeof(g_current));
        AcquireSRWLockExclusive(&g_lock);
        copy_id(g_listed_current, g_current, sizeof(g_listed_current));
        ReleaseSRWLockExclusive(&g_lock);
    }
}

void test_activate(const slot* s)
{
    g_active_tokens = s != nullptr ? &s->t : (g_active_slot != nullptr ? &g_active_slot->t : nullptr);
    font::set_active(s != nullptr ? &s->f : (g_active_slot != nullptr ? &g_active_slot->f : nullptr));
    __atomic_add_fetch(&g_atlas_gen, 1u, __ATOMIC_RELEASE);   // gpu re-creates the texture from the new atlas
}

void test_set_root(const wchar_t* dir)
{
    g_have_root = dir != nullptr && dir[0] != L'\0';
    uint32_t n = 0;
    while (dir != nullptr && dir[n] != L'\0' && n + 1 < 1024) {
        g_root[n] = dir[n];
        ++n;
    }
    g_root[n] = L'\0';
    if (g_have_root) {
        scan_begin();
        while (g_scanning) {
            scan_step();
        }
    }
}

}  // namespace ilc::overlay::theme
