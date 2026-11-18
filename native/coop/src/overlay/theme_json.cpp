// Ivalice Legacy co-op overlay: the bounded JSON reader (see theme_json.h). No C runtime calls.
#include "overlay/theme_json.h"

namespace ilc::overlay::json {
namespace {

struct reader {
    doc& d;
    const char* s;
    uint32_t n;
    uint32_t i;
    uint32_t max_depth;

    bool fail(const char* why)
    {
        if (d.error == nullptr) {
            d.error = why;
            d.error_at = i;
        }
        return false;
    }

    void space()
    {
        while (i < n && (s[i] == ' ' || s[i] == '\t' || s[i] == '\n' || s[i] == '\r')) {
            ++i;
        }
    }

    uint32_t new_node(uint8_t t)
    {
        if (d.node_count >= d.node_cap) {
            fail("too many values");
            return NONE;
        }
        node& x = d.nodes[d.node_count];
        x = node{};
        x.type = t;
        x.next = NONE;
        x.child = NONE;
        x.key = NONE;
        x.str = NONE;
        return d.node_count++;
    }

    bool put(uint8_t b)
    {
        if (d.string_len + 1 >= d.string_cap) {
            return fail("strings too long");
        }
        d.strings[d.string_len++] = static_cast<char>(b);
        return true;
    }

    bool put_code(uint32_t c)
    {
        if (c < 0x80) {
            return put(static_cast<uint8_t>(c));
        }
        if (c < 0x800) {
            return put(static_cast<uint8_t>(0xC0 | c >> 6)) && put(static_cast<uint8_t>(0x80 | (c & 0x3F)));
        }
        if (c < 0x10000) {
            return put(static_cast<uint8_t>(0xE0 | c >> 12)) && put(static_cast<uint8_t>(0x80 | (c >> 6 & 0x3F))) &&
                   put(static_cast<uint8_t>(0x80 | (c & 0x3F)));
        }
        return put(static_cast<uint8_t>(0xF0 | c >> 18)) && put(static_cast<uint8_t>(0x80 | (c >> 12 & 0x3F))) &&
               put(static_cast<uint8_t>(0x80 | (c >> 6 & 0x3F))) && put(static_cast<uint8_t>(0x80 | (c & 0x3F)));
    }

    bool hex4(uint32_t* out)
    {
        if (i + 4 > n) {
            return fail("short \\u escape");
        }
        uint32_t v = 0;
        for (uint32_t k = 0; k < 4; ++k) {
            const char c = s[i + k];
            v <<= 4;
            if (c >= '0' && c <= '9') {
                v |= static_cast<uint32_t>(c - '0');
            } else if (c >= 'a' && c <= 'f') {
                v |= static_cast<uint32_t>(c - 'a' + 10);
            } else if (c >= 'A' && c <= 'F') {
                v |= static_cast<uint32_t>(c - 'A' + 10);
            } else {
                return fail("bad \\u escape");
            }
        }
        i += 4;
        *out = v;
        return true;
    }

    // A string at s[i] == '"'; its unescaped bytes go to the string buffer. *off / *len receive where.
    bool string(uint32_t* off, uint32_t* len)
    {
        ++i;   // the opening quote
        const uint32_t start = d.string_len;
        while (true) {
            if (i >= n) {
                return fail("unterminated string");
            }
            const uint8_t c = static_cast<uint8_t>(s[i]);
            if (c == '"') {
                ++i;
                break;
            }
            if (c < 0x20) {
                return fail("control character in a string");
            }
            if (c == '\\') {
                if (++i >= n) {
                    return fail("unterminated escape");
                }
                const char e = s[i++];
                uint32_t code = 0;
                switch (e) {
                case '"': code = '"'; break;
                case '\\': code = '\\'; break;
                case '/': code = '/'; break;
                case 'b': code = 8; break;
                case 'f': code = 12; break;
                case 'n': code = 10; break;
                case 'r': code = 13; break;
                case 't': code = 9; break;
                case 'u':
                    if (!hex4(&code)) {
                        return false;
                    }
                    if (code >= 0xD800 && code <= 0xDBFF) {
                        uint32_t low = 0;
                        if (i + 2 > n || s[i] != '\\' || s[i + 1] != 'u') {
                            return fail("lone surrogate");
                        }
                        i += 2;
                        if (!hex4(&low) || low < 0xDC00 || low > 0xDFFF) {
                            return fail("bad surrogate pair");
                        }
                        code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00);
                    } else if (code >= 0xDC00 && code <= 0xDFFF) {
                        return fail("lone surrogate");
                    }
                    if (code == 0) {
                        return fail("NUL in a string");
                    }
                    break;
                default:
                    return fail("unknown escape");
                }
                if (!put_code(code)) {
                    return false;
                }
                continue;
            }
            // Raw UTF-8: validate the sequence, copy it as is.
            uint32_t need = 0;
            uint32_t code = 0;
            uint32_t min = 0;
            if (c < 0x80) {
                need = 0;
            } else if ((c & 0xE0) == 0xC0) {
                need = 1;
                code = c & 0x1F;
                min = 0x80;
            } else if ((c & 0xF0) == 0xE0) {
                need = 2;
                code = c & 0x0F;
                min = 0x800;
            } else if ((c & 0xF8) == 0xF0) {
                need = 3;
                code = c & 0x07;
                min = 0x10000;
            } else {
                return fail("bad UTF-8");
            }
            if (i + need >= n) {
                return fail("bad UTF-8");
            }
            for (uint32_t k = 1; k <= need; ++k) {
                const uint8_t b = static_cast<uint8_t>(s[i + k]);
                if ((b & 0xC0) != 0x80) {
                    return fail("bad UTF-8");
                }
                code = code << 6 | (b & 0x3F);
            }
            if (need != 0 && (code < min || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF))) {
                return fail("bad UTF-8");
            }
            for (uint32_t k = 0; k <= need; ++k) {
                if (!put(static_cast<uint8_t>(s[i + k]))) {
                    return false;
                }
            }
            i += need + 1;
        }
        *off = start;
        *len = d.string_len - start;
        return put(0);
    }

    bool number(double* out)
    {
        bool neg = false;
        if (s[i] == '-') {
            neg = true;
            ++i;
        }
        if (i >= n || s[i] < '0' || s[i] > '9') {
            return fail("bad number");
        }
        uint64_t mant = 0;
        int32_t exp10 = 0;
        uint32_t digits = 0;
        const auto digit = [&](char c, bool fraction) {
            if (digits < 18) {
                mant = mant * 10 + static_cast<uint64_t>(c - '0');
                if (mant != 0) {
                    ++digits;
                }
                if (fraction) {
                    --exp10;
                }
            } else if (!fraction) {
                ++exp10;   // past 18 significant digits: the rest only scales
            }
        };
        if (s[i] == '0') {
            ++i;
        } else {
            while (i < n && s[i] >= '0' && s[i] <= '9') {
                digit(s[i++], false);
            }
        }
        if (i < n && s[i] == '.') {
            ++i;
            if (i >= n || s[i] < '0' || s[i] > '9') {
                return fail("bad number");
            }
            while (i < n && s[i] >= '0' && s[i] <= '9') {
                digit(s[i++], true);
            }
        }
        if (i < n && (s[i] == 'e' || s[i] == 'E')) {
            ++i;
            bool eneg = false;
            if (i < n && (s[i] == '+' || s[i] == '-')) {
                eneg = s[i] == '-';
                ++i;
            }
            if (i >= n || s[i] < '0' || s[i] > '9') {
                return fail("bad number");
            }
            int32_t e = 0;
            while (i < n && s[i] >= '0' && s[i] <= '9') {
                if (e < 10000) {
                    e = e * 10 + (s[i] - '0');
                }
                ++i;
            }
            exp10 += eneg ? -e : e;
        }
        if (exp10 > 300 || exp10 < -300) {
            return fail("number out of range");
        }
        double v = static_cast<double>(mant);
        while (exp10 > 0) {
            v *= 10.0;
            --exp10;
        }
        while (exp10 < 0) {
            v /= 10.0;
            ++exp10;
        }
        *out = neg ? -v : v;
        return true;
    }

    bool literal(const char* word)
    {
        uint32_t k = 0;
        while (word[k] != '\0') {
            if (i + k >= n || s[i + k] != word[k]) {
                return fail("bad literal");
            }
            ++k;
        }
        i += k;
        return true;
    }

    bool same_key(uint32_t a, uint32_t alen, uint32_t b, uint32_t blen) const
    {
        if (alen != blen) {
            return false;
        }
        for (uint32_t k = 0; k < alen; ++k) {
            if (d.strings[a + k] != d.strings[b + k]) {
                return false;
            }
        }
        return true;
    }

    // A value at s[i]; returns its node index (NONE on failure).
    uint32_t value(uint32_t depth)
    {
        space();
        if (i >= n) {
            fail("value expected");
            return NONE;
        }
        const char c = s[i];
        if (c == '{' || c == '[') {
            if (depth >= max_depth) {
                fail("nested too deep");
                return NONE;
            }
            const bool obj = c == '{';
            const uint32_t self = new_node(obj ? T_OBJ : T_ARR);
            if (self == NONE) {
                return NONE;
            }
            ++i;
            space();
            const char close = obj ? '}' : ']';
            if (i < n && s[i] == close) {
                ++i;
                return self;
            }
            uint32_t last = NONE;
            while (true) {
                uint32_t key = NONE;
                uint32_t key_len = 0;
                if (obj) {
                    space();
                    if (i >= n || s[i] != '"') {
                        fail("key expected");
                        return NONE;
                    }
                    if (!string(&key, &key_len)) {
                        return NONE;
                    }
                    // The duplicate check below is quadratic in the members: cap them first (review 6).
                    if (d.nodes[self].count >= OBJECT_MEMBERS_MAX) {
                        fail("too many members in one object");
                        return NONE;
                    }
                    // Duplicate keys are refused: a hand-edited file must not mean two things.
                    for (uint32_t e = d.nodes[self].child; e != NONE; e = d.nodes[e].next) {
                        if (same_key(d.nodes[e].key, d.nodes[e].key_len, key, key_len)) {
                            fail("duplicate key");
                            return NONE;
                        }
                    }
                    space();
                    if (i >= n || s[i] != ':') {
                        fail("':' expected");
                        return NONE;
                    }
                    ++i;
                }
                const uint32_t v = value(depth + 1);
                if (v == NONE) {
                    return NONE;
                }
                d.nodes[v].key = key;
                d.nodes[v].key_len = key_len;
                if (last == NONE) {
                    d.nodes[self].child = v;
                } else {
                    d.nodes[last].next = v;
                }
                last = v;
                ++d.nodes[self].count;
                space();
                if (i < n && s[i] == ',') {
                    ++i;
                    continue;
                }
                if (i < n && s[i] == close) {
                    ++i;
                    return self;
                }
                fail(obj ? "',' or '}' expected" : "',' or ']' expected");
                return NONE;
            }
        }
        if (c == '"') {
            const uint32_t self = new_node(T_STR);
            if (self == NONE) {
                return NONE;
            }
            uint32_t off = 0;
            uint32_t len = 0;
            if (!string(&off, &len)) {
                return NONE;
            }
            d.nodes[self].str = off;
            d.nodes[self].str_len = len;
            return self;
        }
        if (c == '-' || (c >= '0' && c <= '9')) {
            const uint32_t self = new_node(T_NUM);
            if (self == NONE) {
                return NONE;
            }
            double v = 0.0;
            if (!number(&v)) {
                return NONE;
            }
            d.nodes[self].num = v;
            return self;
        }
        if (c == 't' || c == 'f' || c == 'n') {
            const uint8_t t = c == 't' ? T_TRUE : (c == 'f' ? T_FALSE : T_NULL);
            const uint32_t self = new_node(t);
            if (self == NONE || !literal(c == 't' ? "true" : (c == 'f' ? "false" : "null"))) {
                return NONE;
            }
            return self;
        }
        fail("unexpected character");
        return NONE;
    }
};

}  // namespace

bool parse(doc& d, const char* src, uint32_t len, uint32_t max_depth)
{
    d.node_count = 0;
    d.string_len = 0;
    d.error = nullptr;
    d.error_at = 0;
    if (d.nodes == nullptr || d.strings == nullptr || d.node_cap == 0 || d.string_cap == 0 || src == nullptr) {
        d.error = "no buffer";
        return false;
    }
    reader r{d, src, len, 0, max_depth};
    // A UTF-8 byte order mark is tolerated (editors write one).
    if (len >= 3 && static_cast<uint8_t>(src[0]) == 0xEF && static_cast<uint8_t>(src[1]) == 0xBB &&
        static_cast<uint8_t>(src[2]) == 0xBF) {
        r.i = 3;
    }
    const uint32_t top = r.value(0);
    if (top == NONE) {
        return false;
    }
    r.space();
    if (r.i != len) {
        return r.fail("text after the value");
    }
    return true;
}

const node* root(const doc& d)
{
    return d.error == nullptr && d.node_count != 0 ? &d.nodes[0] : nullptr;
}

const node* member(const doc& d, const node* obj, const char* key)
{
    if (obj == nullptr || obj->type != T_OBJ) {
        return nullptr;
    }
    for (uint32_t e = obj->child; e != NONE; e = d.nodes[e].next) {
        const char* k = d.strings + d.nodes[e].key;
        uint32_t j = 0;
        while (key[j] != '\0' && k[j] == key[j]) {
            ++j;
        }
        if (key[j] == '\0' && j == d.nodes[e].key_len) {
            return &d.nodes[e];
        }
    }
    return nullptr;
}

const node* element(const doc& d, const node* parent, uint32_t k)
{
    if (parent == nullptr || (parent->type != T_ARR && parent->type != T_OBJ)) {
        return nullptr;
    }
    uint32_t e = parent->child;
    while (e != NONE && k > 0) {
        e = d.nodes[e].next;
        --k;
    }
    return e != NONE ? &d.nodes[e] : nullptr;
}

const node* next(const doc& d, const node* n)
{
    return n != nullptr && n->next != NONE ? &d.nodes[n->next] : nullptr;
}

const char* key_of(const doc& d, const node* n)
{
    return n != nullptr && n->key != NONE ? d.strings + n->key : "";
}

const char* str_of(const doc& d, const node* n)
{
    return n != nullptr && n->type == T_STR ? d.strings + n->str : nullptr;
}

bool str_is(const doc& d, const node* n, const char* text)
{
    const char* s = str_of(d, n);
    if (s == nullptr) {
        return false;
    }
    uint32_t j = 0;
    while (text[j] != '\0' && s[j] == text[j]) {
        ++j;
    }
    return text[j] == '\0' && j == n->str_len;
}

}  // namespace ilc::overlay::json
