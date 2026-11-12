"""Encode and decode bridge records from schema/bridge-abi-v1.json (generated from include/ilc/bridge_abi.h).

Nothing here hard-codes a layout: offsets, sizes, ranges, field ids and enum values all come from the JSON, so
host tests built on it follow the header automatically. Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / 'bridge-abi-v1.json'
SCALARS = {'uint8_t': 'B', 'uint16_t': 'H', 'uint32_t': 'I', 'int32_t': 'i', 'uint64_t': 'Q'}


class Schema:
    def __init__(self, path: Path = SCHEMA_PATH):
        raw = path.read_bytes().replace(b'\r\n', b'\n')
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self.json = json.loads(raw)
        self.const = self.json['constants']
        self.structs = self.json['structs']
        self.enums = {name: {item['name']: item['value'] for item in enum['items']}
                      for name, enum in self.json['enums'].items()}
        self.by_id = {s['id']: name for name, s in self.structs.items()}

    # ---- names --------------------------------------------------------------------------------------
    def e(self, enum: str, item: str) -> int:
        return self.enums[enum][item]

    def size(self, struct_name: str) -> int:
        return self.structs[struct_name]['size']

    def field(self, struct_name: str, name: str) -> dict:
        for f in self.structs[struct_name]['fields']:
            if f['name'] == name:
                return f
        raise KeyError(f'{struct_name}.{name}')

    def field_id(self, struct_name: str, name: str) -> int:
        return self.field(struct_name, name)['fieldId']

    def field_name(self, field_id: int) -> str:
        struct_name = self.by_id.get(field_id >> 8)
        if struct_name is None:
            return f'?{field_id:#x}'
        return f'{struct_name}.{self.structs[struct_name]["fields"][field_id & 0xFF]["name"]}'

    def defaults(self) -> bytes:
        return bytes.fromhex(self.json['defaults']['hex'])

    def locate(self, struct_name: str, path: list) -> tuple[int, dict, str]:
        """Byte offset, field and owning struct of a path such as ['seats', 1, 'device', 'kind']."""
        offset, current, field = 0, struct_name, None
        items = list(path)
        while items:
            name = items.pop(0)
            field = self.field(current, name)
            offset += field['offset']
            index = items.pop(0) if items and isinstance(items[0], int) else None
            if index is not None:
                if not 0 <= index < field['count']:
                    raise IndexError(f'{name}[{index}]')
                offset += index * field['elemSize']
            if field['kind'] in ('REC', 'RECS'):
                owner = current
                current = field['type']
                if not items:
                    return offset, field, owner
            elif items:
                raise KeyError(f'{name} has no members')
            else:
                return offset, field, current
        raise KeyError('empty path')

    # ---- whole records ------------------------------------------------------------------------------
    def decode(self, struct_name: str, data: bytes, base: int = 0) -> dict:
        result = {}
        for f in self.structs[struct_name]['fields']:
            at = base + f['offset']
            kind, count, elem = f['kind'], f['count'], f['elemSize']
            if kind in ('PAD', 'BYTES'):
                result[f['name']] = bytes(data[at:at + f['size']])
            elif kind in ('REC', 'RECS'):
                items = [self.decode(f['type'], data, at + i * elem) for i in range(count)]
                result[f['name']] = items[0] if kind == 'REC' else items
            else:
                values = list(struct.unpack_from('<' + SCALARS[f['type']] * count, data, at))
                result[f['name']] = values[0] if kind == 'VAL' else values
        return result

    def encode(self, struct_name: str, value: dict, out: bytearray | None = None, base: int = 0) -> bytearray:
        if out is None:
            out = bytearray(self.size(struct_name))
        for f in self.structs[struct_name]['fields']:
            if f['name'] not in value:
                continue
            v, at, kind = value[f['name']], base + f['offset'], f['kind']
            if kind in ('PAD', 'BYTES'):
                out[at:at + f['size']] = bytes(v).ljust(f['size'], b'\0')
            elif kind == 'REC':
                self.encode(f['type'], v, out, at)
            elif kind == 'RECS':
                for i, item in enumerate(v):
                    self.encode(f['type'], item, out, at + i * f['elemSize'])
            else:
                values = [v] if kind == 'VAL' else list(v)
                struct.pack_into('<' + SCALARS[f['type']] * len(values), out, at, *values)
        return out

    def header(self, record_type: str, size: int, generation: int = 0) -> dict:
        return {'magic': self.const['ABI_MAGIC'], 'abi_version': self.const['ABI_VERSION'],
                'record_type': self.e('record_type', record_type), 'size': size, 'generation': generation}

    # ---- helpers ------------------------------------------------------------------------------------
    @staticmethod
    def digest(record: bytes) -> int:
        """FNV-1a 32 over bytes 16..end (the settings body)."""
        h = 0x811C9DC5
        for b in record[16:]:
            h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
        return h

    def inbound_scalar_fields(self, struct_name: str, prefix: list | None = None):
        """Yield (path, field, owner) for every VAL/ARR/PAD element reachable in an inbound record."""
        prefix = prefix or []
        for f in self.structs[struct_name]['fields']:
            if f['kind'] in ('REC', 'RECS'):
                if f['name'] == 'header':
                    continue
                for i in range(f['count']):
                    path = prefix + [f['name']] + ([i] if f['kind'] == 'RECS' else [])
                    yield from self.inbound_scalar_fields(f['type'], path)
            elif f['kind'] in ('VAL', 'ARR', 'PAD'):
                yield prefix + [f['name']], f, struct_name
