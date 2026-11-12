// Host tool: print the bridge ABI as JSON, expanded from the same tables the module is compiled from.
//
// build.py compiles this with the pinned g++ and compares its output with schema/bridge-abi-v1.json. Offsets
// and sizes come from the compiler (offsetof/sizeof), so the JSON describes the layout the module really uses.
// Exit code 1 if any record has implicit padding (field sizes not summing to the struct size, or a gap).
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "ilc/bridge_abi.h"
#include "ilc/bridge_defaults.h"

namespace {

int g_failures = 0;

void json_string(const char* s)
{
    putchar('"');
    for (; *s; ++s) {
        const unsigned char c = static_cast<unsigned char>(*s);
        if (c == '"' || c == '\\') {
            printf("\\%c", c);
        } else if (c < 0x20) {
            printf("\\u%04x", c);
        } else {
            putchar(c);
        }
    }
    putchar('"');
}

struct field_cursor {
    const char* struct_name;
    unsigned struct_id;
    unsigned index;
    size_t expected_offset;
    bool first;
};

void emit_field(field_cursor& cur, const char* kind, const char* type, const char* name, unsigned long count,
                size_t offset, size_t size, unsigned long long lo, unsigned long long hi, const char* doc)
{
    if (offset != cur.expected_offset) {
        fprintf(stderr, "%s.%s: offset %zu, expected %zu (implicit padding)\n", cur.struct_name, name, offset,
                cur.expected_offset);
        ++g_failures;
    }
    cur.expected_offset = offset + size;
    printf("%s\n        {\"name\": ", cur.first ? "" : ",");
    cur.first = false;
    json_string(name);
    printf(", \"kind\": \"%s\", \"type\": ", kind);  // VAL, ARR, REC, RECS, PAD, BYTES
    json_string(type);
    printf(", \"count\": %lu, \"offset\": %zu, \"size\": %zu, \"elemSize\": %zu, \"min\": %llu, \"max\": %llu, "
           "\"fieldId\": %u, \"doc\": ",
           count, offset, size, size / count, lo, hi, (cur.struct_id << 8) | cur.index);
    json_string(doc);
    putchar('}');
    ++cur.index;
}

void emit_hex(const void* data, size_t size)
{
    const unsigned char* p = static_cast<const unsigned char*>(data);
    putchar('"');
    for (size_t i = 0; i < size; ++i) {
        printf("%02x", p[i]);
    }
    putchar('"');
}

}  // namespace

int main()
{
    printf("{\n  \"abi\": \"ivalice-legacy-coop bridge\",\n  \"abiVersion\": %u,\n", ILC_ABI_VERSION);
    printf("  \"source\": \"native/coop/include/ilc/bridge_abi.h\",\n  \"byteOrder\": \"little\",\n");
    printf("  \"entry\": \"int32_t ILC_Bridge(uint64_t op, const void* in, uint64_t inLen, void* out, uint64_t "
           "outCap)\",\n");
    printf("  \"rules\": {\n"
           "    \"arguments\": \"op, inLen and outCap must be < 2^32, else BAD_ARGS and nothing written\",\n"
           "    \"return\": \">= 0 bytes written (<= outCap, <= INT32_MAX); < 0 ilc_error, nothing written\",\n"
           "    \"fault\": \"on FAULT, state may have changed and out may hold a partial reply; re-sync with "
           "GET_STATUS. Every other error writes nothing\",\n"
           "    \"fieldId\": \"(struct id << 8) | field index\",\n"
           "    \"location\": \"(outer << 8) | inner; outer = ilc_location, inner = element index\",\n"
           "    \"digest\": \"FNV-1a 32 (offset basis 0x811c9dc5, prime 0x01000193) over settings bytes 16..size\",\n"
           "    \"schemaId\": \"first 16 bytes of SHA-256 of this file with LF line endings, as two little-endian "
           "u64 in HELLO\",\n"
           "    \"generation\": \"settings: >= 1; repeat with same bytes = DUPLICATE; same generation, other bytes = "
           "GENERATION_CONFLICT; older = STALE_GENERATION\"\n  },\n");

    printf("  \"constants\": {");
    bool first = true;
#define ILC_EMIT_CONST(name, value, doc)                                                                    \
    printf("%s\n    \"%s\": %u", first ? "" : ",", #name, static_cast<unsigned>(ILC_##name));              \
    first = false;
    ILC_CONSTANTS(ILC_EMIT_CONST)
#undef ILC_EMIT_CONST
    printf("\n  },\n");

    printf("  \"enums\": {");
    bool first_enum = true;
#define ILC_EMIT_ITEM(prefix, name, value, doc)                                                             \
    printf("%s\n        {\"name\": \"%s\", \"value\": %lld, \"doc\": ", first_item ? "" : ",", #name,      \
           static_cast<long long>(prefix##name));                                                           \
    json_string(doc);                                                                                       \
    putchar('}');                                                                                           \
    first_item = false;
#define ILC_EMIT_ENUM(json, type, underlying, LIST)                                                         \
    {                                                                                                       \
        printf("%s\n    \"%s\": {\"type\": \"%s\", \"underlying\": \"%s\", \"items\": [", first_enum ? "" : ",", \
               #json, #type, #underlying);                                                                  \
        first_enum = false;                                                                                 \
        bool first_item = true;                                                                             \
        LIST(ILC_EMIT_ITEM)                                                                                 \
        printf("\n      ]}");                                                                               \
    }
    ILC_ENUMS(ILC_EMIT_ENUM)
#undef ILC_EMIT_ENUM
#undef ILC_EMIT_ITEM
    printf("\n  },\n");

    printf("  \"structs\": {");
    bool first_struct = true;
#define ILC_EMIT_FIELD(kind, type, name, count, lo, hi, doc)                                                \
    emit_field(cur, #kind, #type, #name, static_cast<unsigned long>(count), offsetof(S, name), sizeof(S::name), \
               static_cast<unsigned long long>(lo), static_cast<unsigned long long>(hi), doc);
#define ILC_EMIT_STRUCT(type, id, LIST, doc)                                                                \
    {                                                                                                       \
        using S = type;                                                                                     \
        printf("%s\n    \"%s\": {\"id\": %u, \"size\": %zu, \"align\": %zu, \"doc\": ", first_struct ? "" : ",", \
               #type, static_cast<unsigned>(id), sizeof(S), alignof(S));                                    \
        json_string(doc);                                                                                   \
        printf(", \"fields\": [");                                                                          \
        first_struct = false;                                                                               \
        field_cursor cur = {#type, static_cast<unsigned>(id), 0, 0, true};                                  \
        LIST(ILC_EMIT_FIELD)                                                                                \
        if (cur.expected_offset != sizeof(S)) {                                                             \
            fprintf(stderr, "%s: fields end at %zu, size %zu (tail padding)\n", #type, cur.expected_offset, \
                    sizeof(S));                                                                             \
            ++g_failures;                                                                                   \
        }                                                                                                   \
        printf("\n      ]}");                                                                               \
    }
    ILC_STRUCTS(ILC_EMIT_STRUCT)
#undef ILC_EMIT_STRUCT
#undef ILC_EMIT_FIELD
    printf("\n  },\n");

    // Which record each op takes and returns (documentation for the Lua side; the tests check behaviour).
    printf("  \"ops\": {\n"
           "    \"HELLO\": {\"in\": [0, 16], \"inRecord\": \"ilc_header\", \"out\": \"ilc_hello_reply\"},\n"
           "    \"SET_SETTINGS\": {\"in\": [%zu], \"inRecord\": \"ilc_settings\", \"out\": \"ilc_settings_ack\"},\n"
           "    \"COMMAND\": {\"in\": [%zu], \"inRecord\": \"ilc_command\", \"out\": null},\n"
           "    \"GET_STATUS\": {\"in\": [0], \"out\": \"ilc_status\"},\n"
           "    \"GET_SEATS\": {\"in\": [0], \"out\": \"ilc_seats_reply\"},\n"
           "    \"GET_DEVICES\": {\"in\": [0], \"out\": \"ilc_devices_reply\", \"variable\": \"entries\"},\n"
           "    \"POP_EVENTS\": {\"in\": [0], \"out\": \"ilc_events_reply\", \"variable\": \"events\"},\n"
           "    \"GET_SETTINGS\": {\"in\": [0], \"out\": \"ilc_settings\"},\n"
           "    \"TEST_SAFE_POINT\": {\"in\": [0], \"out\": \"ilc_safe_point_result\", \"debugOnly\": true}\n"
           "  },\n",
           sizeof(ilc_settings), sizeof(ilc_command));

    constexpr ilc_settings defaults = ilc::default_settings();
    printf("  \"defaults\": {\"record\": \"ilc_settings\", \"hex\": ");
    emit_hex(&defaults, sizeof(defaults));
    printf("}\n}\n");
    return g_failures == 0 ? 0 : 1;
}
