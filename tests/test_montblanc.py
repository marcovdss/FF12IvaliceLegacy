"""Native menu extension: append-only data, choices and guarded source pins."""
import hashlib
import json
from pathlib import Path
import struct
import unittest

from ivalice_legacy.montblanc import encode_body, field_strings, patch_script, patch_text


ROOT = Path(__file__).resolve().parents[1]


class MontblancTests(unittest.TestCase):
    def setUp(self):
        self.spec = json.loads((ROOT/'patches/montblanc.json').read_text())

    def test_pages_are_compact_and_encodable(self):
        self.assertEqual(self.spec['pages'], [])           # 0.2.0: the pages are spoken by the Lua topic
        self.assertEqual(len(self.spec['topicPages']), 3)
        for page in self.spec['topicPages']:
            self.assertTrue(encode_body(page))
        with self.assertRaises(ValueError):
            encode_body('a'*45)

    def test_choice_insertion_keeps_existing_text(self):
        prefix = bytes.fromhex('0f3b81810f38a282cd02')
        originals = [prefix+b'\x20\0' for _ in range(254)]
        originals[55] = prefix + bytes.fromhex('0e838080808e20a0218b22')+b'\0'
        raw = bytearray(254*4)
        for i, item in enumerate(originals):
            struct.pack_into('<I', raw, i*4, len(raw))
            raw += item
        spec = dict(self.spec, textSha256=hashlib.sha256(raw).hexdigest(), pages=self.spec['topicPages'], signal=None)
        result = patch_text(bytes(raw), spec)
        pointers, strings = field_strings(result)
        self.assertEqual(len(strings), 257)
        self.assertIn(bytes.fromhex('0e84808080'), strings[55])
        self.assertEqual(strings[55].count(b'\xa3'), 1)
        for i in range(254):
            if i != 55:
                self.assertEqual(strings[i], originals[i])
        self.assertEqual(pointers[0], 257*4)
        self.assertEqual(len(result) % 64, 0)
        with self.assertRaises(ValueError):
            patch_text(result, spec)

    def test_script_append_preserves_native_code_and_metadata(self):
        raw = bytearray(384)
        struct.pack_into('<H', raw, 0, 11)
        struct.pack_into('<I', raw, 4, 384)
        struct.pack_into('<I', raw, 24, 144)
        struct.pack_into('<I', raw, 32, 320)
        struct.pack_into('<I', raw, 144, 1)
        struct.pack_into('<12I', raw, 148, 9, 0, 256, 2, 304, 288, 340, 20, 11, 12, 13, 14)
        raw[256:258] = b'\x2d\x2d'
        struct.pack_into('<3I', raw, 288, 2, 0, 1)
        struct.pack_into('<I', raw, 304, 0)
        struct.pack_into('<2I', raw, 320, 1, 1234)
        spec = dict(self.spec, pages=self.spec['topicPages'], signal=None,
                    scriptSha256=hashlib.sha256(raw).hexdigest(), thread=0,
                    labelCount=2, codeBytes=2, instructionCount=2, dispatchLabel=0,
                    dispatchOffset=0, marksLabel=0, jobsLabel=1, leaveLabel=1, menuOffset=1)
        sizes = [1 if i < 0x47 else 3 for i in range(0x64)]
        result = patch_script(bytes(raw), spec, sizes)
        start, count = struct.unpack_from('<II', result, 156)
        self.assertEqual(result[start:start+2], b'\x2d\x2d')
        self.assertGreater(count, 2)
        self.assertEqual(result[172:196], raw[172:196])  # behavior metadata untouched
        self.assertEqual(result[256:384], raw[256:384])  # all old code/tables untouched
        self.assertEqual(len(result) % 64, 0)
        with self.assertRaises(ValueError):
            patch_script(result, spec, sizes)


if __name__ == '__main__':
    unittest.main()
