"""Read single files from the game's FFXII_TZA.vbf archive (read-only)."""
from pathlib import Path
import hashlib
import struct
import zlib


class VBF:
    """Minimal reader: header table, MD5 path hashes, 64 KiB zlib blocks."""

    def __init__(self, path):
        self.path = Path(path)
        with self.path.open('rb') as f:
            magic, size, count = struct.unpack('<4sIQ', f.read(16))
            if magic != b'SRYK' or not 16 <= size < 100_000_000 or count >= 1_000_000:
                raise ValueError(f'{self.path} is not an FF12 VBF archive')
            f.seek(0)
            self.header = f.read(size)
            f.seek(-16, 2)
            if hashlib.md5(self.header).digest() != f.read(16):
                raise ValueError('VBF header checksum mismatch')
        h = self.header
        record_base = 16 + 16 * count
        name_base = record_base + 32 * count
        name_size = struct.unpack_from('<I', h, name_base)[0]
        self.block_base = name_base + name_size
        self.records = [struct.unpack_from('<IIQQQ', h, record_base + i * 32) for i in range(count)]
        self.by_hash = {h[16 + i * 16:32 + i * 16]: i for i in range(count)}
        self.names = h[name_base + 4:name_base + name_size].rstrip(b'\0').decode('utf-8').split('\0')

    def read(self, name):
        key = hashlib.md5(name.lower().replace('\\', '/').encode()).digest()
        if key not in self.by_hash:
            raise KeyError(name)
        first, _, size, offset, _ = self.records[self.by_hash[key]]
        out = bytearray()
        with self.path.open('rb') as f:
            f.seek(offset)
            for i in range((size + 65535) // 65536):
                length = struct.unpack_from('<H', self.header, self.block_base + 2 * (first + i))[0] or 65536
                expected = min(65536, size - len(out))
                block = f.read(length)
                if length != expected:
                    block = zlib.decompress(block)
                if len(block) != expected:
                    raise ValueError(f'{name}: corrupt block {i}')
                out += block
        return bytes(out)
