import copy
import hashlib
import struct
import unittest

from ivalice_legacy.job_labels import apply_text_patch, encode_label, parse_text


def fixture(alias=False):
    count = 3
    text = 32 + count*4
    start = 4 + 4*(count+1)
    label = encode_label('Uhlan')
    other = b'\xff\x0a\x04\0'  # Opaque, unedited native-control-code fixture.
    strings = label + other if alias else label + encode_label('Steal') + other
    pointers = ([start, start, start+len(label), start+len(strings)] if alias else
                [start, start+len(label), start+len(label)+len(encode_label('Steal')), start+len(strings)])
    header = bytearray(32)
    header[:4] = b'st2e'
    struct.pack_into('<III', header, 4, count, 4, 32)
    struct.pack_into('<I', header, 20, text)
    group = struct.pack('<I4I', count, *pointers) + strings
    raw = bytes(header) + struct.pack('<3I', 10101, 5000, 5001)
    raw += struct.pack('<III', 1, 12, 12+len(group)) + group
    return raw + b'\0'*(-len(raw) % 4)


def patch(raw, replacement='Lancer'):
    return {'sourceSha256': hashlib.sha256(raw).hexdigest(),
            'changes': [{'stringId': 10101, 'expected': 'Uhlan', 'replacement': replacement}]}


def string_bytes(raw, value):
    data = parse_text(raw)
    start = data['group'] + data['pointers'][data['ids'].index(value)]
    return raw[start:raw.index(0, start)+1]


class JobLabelsTest(unittest.TestCase):
    def test_growing_and_shrinking_labels_preserve_other_encoded_records(self):
        raw = fixture()
        for name in ('Lancer', 'Time Mage', 'Mage'):
            with self.subTest(name=name):
                result = apply_text_patch(raw, patch(raw, name))
                self.assertEqual(string_bytes(result, 10101), encode_label(name))
                for value in (5000, 5001):
                    self.assertEqual(string_bytes(result, value), string_bytes(raw, value))
                self.assertEqual(result[:20], raw[:20])
                self.assertEqual(len(result) % 4, 0)

    def test_noop_is_byte_identical(self):
        raw = fixture()
        self.assertEqual(apply_text_patch(raw, patch(raw, 'Uhlan')), raw)

    def test_conflicting_source_or_expected_name_rejected(self):
        raw = fixture()
        wrong = patch(raw)
        wrong['sourceSha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'source hash'):
            apply_text_patch(raw, wrong)
        wrong = patch(raw)
        wrong['changes'][0]['expected'] = 'Knight'
        with self.assertRaisesRegex(ValueError, 'Unexpected source label'):
            apply_text_patch(raw, wrong)

    def test_alias_cannot_change_an_unrequested_string(self):
        raw = fixture(alias=True)
        with self.assertRaisesRegex(ValueError, 'aliased label'):
            apply_text_patch(raw, patch(raw))

    def test_truncated_group_and_duplicate_patch_ids_rejected(self):
        raw = fixture()
        data = bytearray(raw)
        struct.pack_into('<I', data, parse_text(raw)['text']+8, 12)
        with self.assertRaisesRegex(ValueError, 'Truncated text group'):
            parse_text(bytes(data))
        recipe = patch(raw)
        recipe['changes'].append(copy.deepcopy(recipe['changes'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate label patch ID'):
            apply_text_patch(raw, recipe)
