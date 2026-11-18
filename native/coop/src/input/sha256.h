// Ivalice Legacy co-op module: SHA-256 (FIPS 180-4), used to turn a device path into a stable device uid and to
// check a HUD theme's atlas digest.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ilc {

void sha256(const void* data, size_t size, uint8_t digest[32]);

// The same digest over data given in pieces (the HUD theme loader hashes a decoded atlas and its tables as text).
struct sha256_ctx {
    uint32_t h[8];
    uint8_t buf[64];
    uint64_t total;
    uint32_t used;
};
void sha256_init(sha256_ctx& c);
void sha256_update(sha256_ctx& c, const void* data, size_t size);
void sha256_final(sha256_ctx& c, uint8_t digest[32]);

}  // namespace ilc
