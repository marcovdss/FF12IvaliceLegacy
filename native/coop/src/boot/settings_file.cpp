// Ivalice Legacy co-op module: the start-up switch in the settings file (see settings_file.h).
//
// A small, strict JSON reader (RFC 8259): the whole document must parse, nesting is capped, and only two values are
// looked at. Keys are compared after unescaping; a repeated key takes its last value, as the Lua store reads it.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "boot/settings_file.h"

namespace ilc::boot {
namespace {

constexpr uint32_t MAX_DEPTH = 32;
constexpr uint32_t KEY_CAP = 32;

enum value_type : uint32_t { T_NONE = 0, T_OBJECT, T_ARRAY, T_STRING, T_NUMBER, T_TRUE, T_FALSE, T_NULL };

struct reader {
    const char* p;
    const char* end;
};

void skip_ws(reader& r)
{
    while (r.p < r.end && (*r.p == ' ' || *r.p == '\t' || *r.p == '\n' || *r.p == '\r')) {
        ++r.p;
    }
}

bool literal(reader& r, const char* word)
{
    const size_t n = strlen(word);
    if (static_cast<size_t>(r.end - r.p) < n || memcmp(r.p, word, n) != 0) {
        return false;
    }
    r.p += n;
    return true;
}

int hex_digit(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

// Parses a string at r.p (which points at the opening quote). The unescaped text is kept in `key` when it is plain
// ASCII and fits; `key_ok` says whether it did (a key that does not fit or is not ASCII matches nothing).
bool parse_string(reader& r, char* key, bool* key_ok)
{
    uint32_t n = 0;
    bool fits = key != nullptr;
    ++r.p;  // opening quote
    while (r.p < r.end) {
        unsigned char c = static_cast<unsigned char>(*r.p++);
        if (c == '"') {
            if (key != nullptr) {
                key[fits ? n : 0] = '\0';
            }
            if (key_ok != nullptr) {
                *key_ok = fits;
            }
            return true;
        }
        if (c < 0x20) {
            return false;  // control characters must be escaped
        }
        uint32_t ch = c;
        if (c == '\\') {
            if (r.p >= r.end) {
                return false;
            }
            const char e = *r.p++;
            switch (e) {
            case '"': ch = '"'; break;
            case '\\': ch = '\\'; break;
            case '/': ch = '/'; break;
            case 'b': ch = '\b'; break;
            case 'f': ch = '\f'; break;
            case 'n': ch = '\n'; break;
            case 'r': ch = '\r'; break;
            case 't': ch = '\t'; break;
            case 'u': {
                if (r.end - r.p < 4) {
                    return false;
                }
                uint32_t v = 0;
                for (int i = 0; i < 4; ++i) {
                    const int d = hex_digit(r.p[i]);
                    if (d < 0) {
                        return false;
                    }
                    v = v * 16 + static_cast<uint32_t>(d);
                }
                r.p += 4;
                ch = v;
                break;
            }
            default:
                return false;
            }
        }
        if (fits) {
            if (ch >= 0x80 || n + 1 >= KEY_CAP) {
                fits = false;
            } else {
                key[n++] = static_cast<char>(ch);
            }
        }
    }
    return false;  // unterminated
}

// A JSON number. *integer_one: the number is exactly the integer 1 (no fraction or exponent); *above_one: an
// integer above 1.
bool parse_number(reader& r, bool* integer_one, bool* above_one)
{
    const char* start = r.p;
    bool negative = false;
    if (r.p < r.end && *r.p == '-') {
        negative = true;
        ++r.p;
    }
    if (r.p >= r.end) {
        return false;
    }
    if (*r.p == '0') {
        ++r.p;
    } else if (*r.p >= '1' && *r.p <= '9') {
        while (r.p < r.end && *r.p >= '0' && *r.p <= '9') {
            ++r.p;
        }
    } else {
        return false;
    }
    const char* int_end = r.p;
    bool plain = true;
    if (r.p < r.end && *r.p == '.') {
        plain = false;
        ++r.p;
        if (r.p >= r.end || *r.p < '0' || *r.p > '9') {
            return false;
        }
        while (r.p < r.end && *r.p >= '0' && *r.p <= '9') {
            ++r.p;
        }
    }
    if (r.p < r.end && (*r.p == 'e' || *r.p == 'E')) {
        plain = false;
        ++r.p;
        if (r.p < r.end && (*r.p == '+' || *r.p == '-')) {
            ++r.p;
        }
        if (r.p >= r.end || *r.p < '0' || *r.p > '9') {
            return false;
        }
        while (r.p < r.end && *r.p >= '0' && *r.p <= '9') {
            ++r.p;
        }
    }
    const char* digits = start + (negative ? 1 : 0);
    const size_t len = static_cast<size_t>(int_end - digits);
    if (integer_one != nullptr) {
        *integer_one = plain && !negative && len == 1 && digits[0] == '1';
    }
    if (above_one != nullptr) {
        *above_one = plain && !negative && (len > 1 || (len == 1 && digits[0] > '1'));
    }
    return true;
}

// What the caller wants to learn about the value being parsed.
struct probe_out {
    value_type type;
    bool integer_one;
    bool above_one;
};

bool parse_value(reader& r, uint32_t depth, probe_out* out, const char* want_key, probe_out* want_out);

// Parses an object; when `want_key` is not null, the last member with that key is described in *want_out.
bool parse_object(reader& r, uint32_t depth, const char* want_key, probe_out* want_out, const char* want_key2,
                  probe_out* want_out2, const char* nested_key, probe_out* nested_out)
{
    ++r.p;  // '{'
    skip_ws(r);
    if (r.p < r.end && *r.p == '}') {
        ++r.p;
        return true;
    }
    for (;;) {
        skip_ws(r);
        if (r.p >= r.end || *r.p != '"') {
            return false;
        }
        char key[KEY_CAP];
        bool key_ok = false;
        if (!parse_string(r, key, &key_ok)) {
            return false;
        }
        skip_ws(r);
        if (r.p >= r.end || *r.p != ':') {
            return false;
        }
        ++r.p;
        skip_ws(r);
        probe_out here = {T_NONE, false, false};
        const bool is_first = key_ok && want_key != nullptr && strcmp(key, want_key) == 0;
        const bool is_second = key_ok && want_key2 != nullptr && strcmp(key, want_key2) == 0;
        probe_out nested = {T_NONE, false, false};
        // Only the second wanted key (the "module" object) is searched for the nested key.
        if (!parse_value(r, depth + 1, &here, is_second ? nested_key : nullptr, is_second ? &nested : nullptr)) {
            return false;
        }
        if (is_first && want_out != nullptr) {
            *want_out = here;
        }
        if (is_second) {
            if (want_out2 != nullptr) {
                *want_out2 = here;
            }
            if (nested_out != nullptr) {
                *nested_out = nested;  // last "module" wins, nested value included
            }
        }
        skip_ws(r);
        if (r.p < r.end && *r.p == ',') {
            ++r.p;
            continue;
        }
        if (r.p < r.end && *r.p == '}') {
            ++r.p;
            return true;
        }
        return false;
    }
}

bool parse_value(reader& r, uint32_t depth, probe_out* out, const char* want_key, probe_out* want_out)
{
    if (depth > MAX_DEPTH) {
        return false;
    }
    skip_ws(r);
    if (r.p >= r.end) {
        return false;
    }
    probe_out local = {T_NONE, false, false};
    probe_out* o = out != nullptr ? out : &local;
    const char c = *r.p;
    if (c == '{') {
        o->type = T_OBJECT;
        return parse_object(r, depth, want_key, want_out, nullptr, nullptr, nullptr, nullptr);
    }
    if (c == '[') {
        o->type = T_ARRAY;
        ++r.p;
        skip_ws(r);
        if (r.p < r.end && *r.p == ']') {
            ++r.p;
            return true;
        }
        for (;;) {
            if (!parse_value(r, depth + 1, nullptr, nullptr, nullptr)) {
                return false;
            }
            skip_ws(r);
            if (r.p < r.end && *r.p == ',') {
                ++r.p;
                continue;
            }
            if (r.p < r.end && *r.p == ']') {
                ++r.p;
                return true;
            }
            return false;
        }
    }
    if (c == '"') {
        o->type = T_STRING;
        return parse_string(r, nullptr, nullptr);
    }
    if (c == 't') {
        o->type = T_TRUE;
        return literal(r, "true");
    }
    if (c == 'f') {
        o->type = T_FALSE;
        return literal(r, "false");
    }
    if (c == 'n') {
        o->type = T_NULL;
        return literal(r, "null");
    }
    o->type = T_NUMBER;
    return parse_number(r, &o->integer_one, &o->above_one);
}

}  // namespace

settings_switch parse_module_switch(const char* text, size_t size, bool default_on)
{
    settings_switch s = {default_on ? SETTINGS_ON : SETTINGS_OFF, 0, false};
    reader r = {text, text + size};
    if (size >= 3 && memcmp(text, "\xEF\xBB\xBF", 3) == 0) {
        r.p += 3;  // a UTF-8 byte-order mark
    }
    skip_ws(r);
    if (r.p >= r.end || *r.p != '{') {
        return {SETTINGS_INVALID, INVALID_SYNTAX, false};
    }
    probe_out schema = {T_NONE, false, false};
    probe_out module = {T_NONE, false, false};
    probe_out enabled = {T_NONE, false, false};
    if (!parse_object(r, 1, "schema", &schema, "module", &module, "enabledAtStart", &enabled)) {
        return {SETTINGS_INVALID, INVALID_SYNTAX, false};
    }
    skip_ws(r);
    if (r.p != r.end) {
        return {SETTINGS_INVALID, INVALID_SYNTAX, false};  // trailing bytes after the document
    }
    if (schema.type != T_NUMBER || !schema.integer_one) {
        return {SETTINGS_INVALID, schema.type == T_NUMBER && schema.above_one ? INVALID_NEWER_SCHEMA : INVALID_SCHEMA,
                false};
    }
    if (module.type == T_NONE) {
        s.defaulted = true;
        return s;
    }
    if (module.type != T_OBJECT) {
        return {SETTINGS_INVALID, INVALID_VALUE, false};
    }
    switch (enabled.type) {
    case T_NONE:
        s.defaulted = true;
        return s;
    case T_TRUE:
        return {SETTINGS_ON, 0, false};
    case T_FALSE:
        return {SETTINGS_OFF, 0, false};
    default:
        return {SETTINGS_INVALID, INVALID_VALUE, false};
    }
}

settings_switch read_module_switch(const wchar_t* x64_dir, bool default_on)
{
    static const wchar_t relative[] = L"\\scripts\\config\\IvaliceLegacy\\coop.json";
    wchar_t path[1024];
    size_t dir_len = 0;
    while (x64_dir != nullptr && dir_len < 1024 && x64_dir[dir_len] != L'\0') {
        ++dir_len;
    }
    if (x64_dir == nullptr || dir_len + sizeof(relative) / sizeof(wchar_t) > sizeof(path) / sizeof(wchar_t)) {
        return {SETTINGS_UNREADABLE, ERROR_FILENAME_EXCED_RANGE, false};
    }
    memcpy(path, x64_dir, dir_len * sizeof(wchar_t));
    memcpy(path + dir_len, relative, sizeof(relative));

    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        const DWORD error = GetLastError();
        if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) {
            return {default_on ? SETTINGS_ON : SETTINGS_OFF, 0, true};
        }
        return {SETTINGS_UNREADABLE, error, false};
    }
    settings_switch result = {SETTINGS_UNREADABLE, 0, false};
    LARGE_INTEGER size;
    if (!GetFileSizeEx(file, &size)) {
        result.detail = GetLastError();
    } else if (size.QuadPart < 0 || size.QuadPart > static_cast<LONGLONG>(SETTINGS_MAX_BYTES)) {
        result.detail = 0;  // too large: not read
    } else {
        // VirtualAlloc, not the heap: the main thread may run this before the CRT heap is warm, and it is freed
        // before returning.
        const DWORD want = static_cast<DWORD>(size.QuadPart);
        char* buffer = static_cast<char*>(VirtualAlloc(nullptr, want + 1, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE));
        if (buffer == nullptr) {
            result.detail = GetLastError();
        } else {
            DWORD got = 0;
            DWORD total = 0;
            bool ok = true;
            while (total < want) {
                if (!ReadFile(file, buffer + total, want - total, &got, nullptr)) {
                    result.detail = GetLastError();
                    ok = false;
                    break;
                }
                if (got == 0) {
                    break;  // shorter than its size said (the store rewrote it): parse what was read
                }
                total += got;
            }
            if (ok) {
                result = parse_module_switch(buffer, total, default_on);
            }
            VirtualFree(buffer, 0, MEM_RELEASE);
        }
    }
    CloseHandle(file);
    return result;
}

}  // namespace ilc::boot
