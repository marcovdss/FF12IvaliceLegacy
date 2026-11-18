// Ivalice Legacy co-op overlay: a strict, bounded JSON reader for theme files (theme.json, icons.json).
//
// RFC 8259 grammar only: no comments, no trailing commas, no NaN/Infinity, UTF-8 text (validated), strings unescaped
// (\uXXXX with surrogate pairs), duplicate keys in one object refused. Everything is bounded by the caller's arena:
// a node cap, a string-bytes cap and a nesting depth, and by OBJECT_MEMBERS_MAX members per object (the duplicate-key
// check is quadratic in them); past any of them the document is refused (never truncated).
// Numbers are read into a double by our own conversion (no C runtime); theme values need no more than six digits.
//
// The tree is an array of nodes: an array's or object's `child` is its first element, every element's `next` its
// sibling (NONE ends the list), an object's elements carry their key. Pure: no state, any thread.
#pragma once

#include <stdint.h>

namespace ilc::overlay::json {

enum type : uint8_t { T_NULL = 0, T_FALSE, T_TRUE, T_NUM, T_STR, T_ARR, T_OBJ };
constexpr uint32_t NONE = 0xFFFFFFFFu;
constexpr uint32_t OBJECT_MEMBERS_MAX = 512;   // members in one object (theme files use a few dozen at most)

struct node {
    uint8_t type;
    uint8_t reserved[3];
    uint32_t next;       // the next element of the enclosing array/object (NONE: last)
    uint32_t child;      // T_ARR / T_OBJ: the first element (NONE: empty)
    uint32_t count;      // T_ARR / T_OBJ: elements
    uint32_t key;        // offset of the key in the string bytes (object members only)
    uint32_t key_len;
    uint32_t str;        // T_STR: offset of the value in the string bytes
    uint32_t str_len;
    double num;          // T_NUM
};

struct doc {
    node* nodes;         // the caller's arena
    uint32_t node_cap;
    char* strings;       // the caller's buffer for unescaped keys and strings (NUL after each)
    uint32_t string_cap;
    uint32_t node_count;
    uint32_t string_len;
    uint32_t error_at;   // byte offset of the first problem
    const char* error;   // what it was (nullptr: parsed)
};

// Parses `src` (len bytes) into d's arena. False with d.error / d.error_at set on any problem.
bool parse(doc& d, const char* src, uint32_t len, uint32_t max_depth);

const node* root(const doc& d);
// An object's member by key (nullptr: none, or `obj` is not an object).
const node* member(const doc& d, const node* obj, const char* key);
// The k-th element of an array or object (nullptr past the end).
const node* element(const doc& d, const node* parent, uint32_t k);
const node* next(const doc& d, const node* n);
const char* key_of(const doc& d, const node* n);     // NUL-terminated
const char* str_of(const doc& d, const node* n);     // NUL-terminated; nullptr unless T_STR
bool str_is(const doc& d, const node* n, const char* text);

}  // namespace ilc::overlay::json
