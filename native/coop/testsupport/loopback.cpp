// Ivalice Legacy co-op module, host tests only: the loopback codec (see src/runtime/loopback.h). Never linked into
// the shipped DLL.
//
// Wire format of one record: a 16-byte header (magic, family, object size, value byte count) and the object's
// value bytes in order. The decoder checks the header, poisons every padding byte of the object and copies the
// value bytes back. A second encode of the decoded object must give the same wire bytes (counted otherwise).
#include <stdlib.h>
#include <string.h>

#include "runtime/loopback.h"

namespace ilc::loopback {
namespace {

constexpr uint32_t WIRE_MAGIC = 0x4C4F4F50u;  // "POOL"
constexpr uint8_t POISON = 0xA5;

volatile uint32_t g_on = 0;       // 0 off, 1 on, 2 lossy (tests of the instrument only)
volatile uint64_t g_lossy_count = 0;

struct family_stats {
    volatile uint64_t records;
    volatile uint64_t value_bytes;
    volatile uint64_t padding_bytes;  // bytes poisoned on decode
    volatile uint64_t wire_errors;    // header or re-encode mismatches
};

family_stats g_stats[FAM_COUNT];

void bump(volatile uint64_t& c, uint64_t by)
{
    __atomic_add_fetch(&c, by, __ATOMIC_RELAXED);
}

uint32_t encode(uint8_t* wire, uint32_t fam, const uint8_t* object, const uint8_t* mask, uint32_t size)
{
    uint32_t n = 0;
    uint8_t* body = wire + 16;
    for (uint32_t i = 0; i < size; ++i) {
        if (mask[i] != 0) {
            body[n++] = object[i];
        }
    }
    const uint32_t header[4] = {WIRE_MAGIC, fam, size, n};
    memcpy(wire, header, sizeof(header));
    return 16 + n;
}

bool decode(const uint8_t* wire, uint32_t length, uint32_t fam, uint8_t* object, const uint8_t* mask, uint32_t size)
{
    uint32_t header[4];
    memcpy(header, wire, sizeof(header));
    if (header[0] != WIRE_MAGIC || header[1] != fam || header[2] != size || 16 + header[3] != length) {
        return false;
    }
    const uint8_t* body = wire + 16;
    uint32_t n = 0;
    for (uint32_t i = 0; i < size; ++i) {
        if (mask[i] != 0) {
            if (n >= header[3]) {
                return false;
            }
            object[i] = body[n++];
        } else {
            object[i] = POISON;
        }
    }
    return n == header[3];
}

}  // namespace

bool on()
{
    return __atomic_load_n(&g_on, __ATOMIC_ACQUIRE) != 0;
}

void build_mask(uint8_t* mask, uint8_t* probe, uint32_t size)
{
    for (uint32_t i = 0; i < size; ++i) {
        mask[i] = probe[i] != 0 ? 1 : 0;  // a byte with any value bit is carried whole
    }
}

void roundtrip_bytes(uint32_t fam, void* object, const uint8_t* mask, uint32_t size)
{
    if (fam >= FAM_COUNT) {
        return;
    }
    uint8_t* wire = static_cast<uint8_t*>(malloc(2u * (16u + size)));
    if (wire == nullptr) {
        bump(g_stats[fam].wire_errors, 1);
        return;
    }
    uint8_t* again = wire + 16 + size;
    uint8_t* o = static_cast<uint8_t*>(object);
    const uint32_t length = encode(wire, fam, o, mask, size);
    if (!decode(wire, length, fam, o, mask, size)) {
        bump(g_stats[fam].wire_errors, 1);
    } else if (encode(again, fam, o, mask, size) != length || memcmp(wire, again, length) != 0) {
        bump(g_stats[fam].wire_errors, 1);
    }
    free(wire);
    const uint64_t record = __atomic_add_fetch(&g_lossy_count, 1, __ATOMIC_RELAXED);
    if (__atomic_load_n(&g_on, __ATOMIC_ACQUIRE) == 2 && record % 997 == 0) {
        // The instrument's falsifier: a deliberately lossy codec (the first value byte of every 997th record
        // flipped) must change the replay's digests.
        for (uint32_t i = 0; i < size; ++i) {
            if (mask[i] != 0) {
                o[i] ^= 1;
                break;
            }
        }
    }
    bump(g_stats[fam].records, 1);
    bump(g_stats[fam].value_bytes, length - 16);
    bump(g_stats[fam].padding_bytes, size - (length - 16));
}

}  // namespace ilc::loopback

extern "C" {

// The codec's switch (0 = off, the default; 1 = on; 2 = lossy, to test the replay itself) and its counters: per
// family records, value bytes, poisoned padding bytes, wire errors (FAM_COUNT * 4 values).
void ILCT_LoopbackSet(int32_t on)
{
    __atomic_store_n(&ilc::loopback::g_on, on == 2 ? 2u : (on != 0 ? 1u : 0u), __ATOMIC_RELEASE);
    __atomic_store_n(&ilc::loopback::g_lossy_count, 0u, __ATOMIC_RELAXED);
}

uint32_t ILCT_LoopbackStats(uint64_t* out, uint32_t cap)
{
    uint32_t n = 0;
    for (uint32_t f = 0; f < ilc::loopback::FAM_COUNT; ++f) {
        const ilc::loopback::family_stats& s = ilc::loopback::g_stats[f];
        const uint64_t v[4] = {s.records, s.value_bytes, s.padding_bytes, s.wire_errors};
        for (uint32_t k = 0; k < 4 && n < cap; ++k) {
            out[n++] = v[k];
        }
    }
    return n;
}

void ILCT_LoopbackResetStats(void)
{
    memset(static_cast<void*>(ilc::loopback::g_stats), 0, sizeof(ilc::loopback::g_stats));
}

}  // extern "C"
