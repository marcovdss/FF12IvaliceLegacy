import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import unittest

from ivalice_legacy.base_jobs import encode_board
from ivalice_legacy.license_lp import apply_license_lp
from ivalice_legacy.job_labels import apply_text_patch, encode_description_line, encode_label, text_records

ROOT = Path(__file__).resolve().parents[1]
RECIPE = json.loads((ROOT/'patches/base_jobs.json').read_text())
def sha(raw): return hashlib.sha256(raw).hexdigest()


class LicensePricesTest(unittest.TestCase):
    def setUp(self):
        self.patch = copy.deepcopy(RECIPE['licenseLpPatch'])
        raw = bytearray(32+361*24)
        raw[:4] = b'st2e'
        struct.pack_into('<III',raw,4,361,24,32)
        for i in range(361): raw[32+24*i+4] = 1
        for c in self.patch['changes']: raw[32+24*c['licenseId']+4] = c['beforeLp']
        self.raw = bytes(raw)
        self.patch['sourceSha256'] = sha(self.raw)
        result = bytearray(raw)
        for c in self.patch['changes']: result[32+24*c['licenseId']+4] = c['afterLp']
        self.patch['resultSha256'] = sha(result)

    def test_only_declared_lp_bytes_change(self):
        result = apply_license_lp(self.raw,self.patch,RECIPE['jobs'])
        self.assertEqual([i for i,(a,b) in enumerate(zip(self.raw,result)) if a!=b],
                         sorted(32+24*c['licenseId']+4 for c in self.patch['changes']))
        self.assertEqual(len(result),len(self.raw))

    def test_wrong_source_and_result_hash_rejected(self):
        with self.assertRaisesRegex(ValueError,'source hash'):
            apply_license_lp(self.raw+b'x',self.patch,RECIPE['jobs'])
        self.patch['resultSha256'] = '0'*64
        with self.assertRaisesRegex(ValueError,'result hash'):
            apply_license_lp(self.raw,self.patch,RECIPE['jobs'])

    def test_before_value_is_checked_even_with_matching_source_pin(self):
        self.patch['changes'][0]['beforeLp'] += 1
        with self.assertRaisesRegex(ValueError,'before value'):
            apply_license_lp(self.raw,self.patch,RECIPE['jobs'])

    def test_shared_license_or_wrong_owner_rejected(self):
        jobs = copy.deepcopy(RECIPE['jobs'])
        lid = self.patch['changes'][0]['licenseId']
        jobs[0]['cells'].append({'licenseId':lid})
        with self.assertRaisesRegex(ValueError,'sole declared owner'):
            apply_license_lp(self.raw,self.patch,jobs)
        self.patch['changes'][0]['owner'] = 'Knight'
        with self.assertRaisesRegex(ValueError,'sole declared owner'):
            apply_license_lp(self.raw,self.patch,RECIPE['jobs'])

    def test_duplicate_missing_and_out_of_byte_range_changes_rejected(self):
        for mutate in [lambda p:p['changes'].pop(),
                       lambda p:p['changes'].__setitem__(1,p['changes'][0]),
                       lambda p:p['changes'][0].update(afterLp=256)]:
            p = copy.deepcopy(self.patch); mutate(p)
            with self.assertRaises(ValueError): apply_license_lp(self.raw,p,RECIPE['jobs'])


def description_fixture():
    ids = list(range(22000,22012))+[77777]
    prompt = b'\x0f\x48\x80\x04'+encode_label('Preview License Board')
    entries = [b'\x20'*60+b'\x02'+b'\x21'*100+b'\x02'+prompt for _ in range(12)]+[b'\xff\x0a\x04\0']
    count = len(ids); offset = 4+4*(count+1); pointers = []
    for entry in entries: pointers.append(offset); offset += len(entry)
    pointers.append(offset)
    header = bytearray(32); header[:4] = b'st2e'
    struct.pack_into('<III',header,4,count,4,32)
    struct.pack_into('<I',header,20,32+4*count)
    group = struct.pack('<I',count)+struct.pack(f'<{count+1}I',*pointers)+b''.join(entries)
    raw = bytes(header)+struct.pack(f'<{count}I',*ids)+struct.pack('<III',1,12,12+len(group))+group
    raw += b'\0'*(-len(raw)%4)
    patch = copy.deepcopy(RECIPE['descriptions']); patch['sourceSha256'] = sha(raw)
    records = text_records(raw)
    for r in patch['changes']: r['sourceEntrySha256'] = sha(records[r['stringId']])
    return raw,patch


class DescriptionsTest(unittest.TestCase):
    def test_roundtrip_and_opaque_unrequested_string_preserved(self):
        raw,patch = description_fixture(); result = apply_text_patch(raw,patch)
        self.assertEqual(text_records(result)[77777],text_records(raw)[77777])
        noop = copy.deepcopy(patch); noop['sourceSha256'] = sha(raw)
        for r in noop['changes']: r['lines'] = ['A'*60,'B'*100]
        self.assertEqual(apply_text_patch(raw,noop),raw)
        equal_size = copy.deepcopy(patch)
        for r in equal_size['changes']: r['lines'] = ['C'*60,'D'*100]
        forward = apply_text_patch(raw,equal_size)
        reverse = copy.deepcopy(noop); reverse['sourceSha256'] = sha(forward)
        for r in reverse['changes']: r['sourceEntrySha256'] = sha(text_records(forward)[r['stringId']])
        self.assertEqual(apply_text_patch(forward,reverse),raw)
        for r in patch['changes']:
            parts = text_records(result)[r['stringId']].split(b'\x02')
            self.assertEqual(parts[:2],[encode_description_line(s) for s in r['lines']])
            self.assertEqual(parts[2],text_records(raw)[r['stringId']].split(b'\x02')[2])
        self.assertEqual(len(result)%4,0)

    def test_wrong_source_entry_and_line_overflow_rejected(self):
        raw,patch = description_fixture()
        for mutate in [lambda p:p.update(sourceSha256='0'*64),
                       lambda p:p['changes'][0].update(sourceEntrySha256='0'*64),
                       lambda p:p['changes'][0].update(lines=['A'*61,'B']),
                       lambda p:p['changes'][0].update(lines=['unsupported\nline','B'])]:
            p = copy.deepcopy(patch); mutate(p)
            with self.assertRaises(ValueError): apply_text_patch(raw,p)

    def test_menu_punctuation_and_number_glyphs(self):
        self.assertEqual(encode_description_line('0-9,.: '),bytes([133,165,142,167,168,170,4]))


class BoardPortTest(unittest.TestCase):
    def test_round_two_role_constraints(self):
        ids = {j['name']:{c['licenseId'] for c in j['cells']} for j in RECIPE['jobs']}
        self.assertFalse(ids['Gunner'] & (set(range(126,136))|{349,350}))
        self.assertEqual(ids['Chemist'] & {251,252,253,254,255,*range(307,318)},
                         {251,252,253,254,255,307,308,309})
        self.assertTrue(all({335,49,52,95,76,330} <= values for values in ids.values()))
        self.assertTrue(all({236,237} <= values and 238 not in values for values in ids.values()))
        self.assertEqual(len([encode_board(j) for j in RECIPE['jobs']]),12)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'),'set IVALICE_LEGACY_GAME for source integration')
class NativeResourcesTest(unittest.TestCase):
    def test_native_descriptions_unchanged_strings_and_lp_bytes(self):
        from ivalice_legacy.vbf import VBF
        from ivalice_legacy.formats import battle_pack_section
        archive = VBF(Path(os.environ['IVALICE_LEGACY_GAME'])/'FFXII_TZA.vbf')
        patch = RECIPE['descriptions']; source = archive.read(patch['archivePath'])
        result = apply_text_patch(source,patch)
        before,after = text_records(source),text_records(result)
        changed = {i for i in before if before[i]!=after[i]}
        self.assertEqual(changed,set(range(22000,22012)))
        for r in patch['changes']:
            parts = after[r['stringId']].split(b'\x02')
            self.assertEqual(parts[:2],[encode_description_line(s) for s in r['lines']])
            self.assertEqual(parts[2],before[r['stringId']].split(b'\x02')[2])
            self.assertTrue(all(len(a)<=len(b) for a,b in zip(parts,before[r['stringId']].split(b'\x02'))))
        native = battle_pack_section(archive.read('ps2data/image/ff12/test_battle/us/binaryfile/battle_pack.bin'),12)
        priced = apply_license_lp(native,RECIPE['licenseLpPatch'],RECIPE['jobs'])
        self.assertEqual(sum(a!=b for a,b in zip(native,priced)),6)
