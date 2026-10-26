import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from ivalice_legacy.base_jobs import BOARD, apply_boards, encode_board, safe_path


class BaseJobsTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.recipe = json.loads((root/'patches/base_jobs.json').read_text())
        self.licenses = b'synthetic unchanged native-license source'
        self.recipe['licenseTableSha256'] = hashlib.sha256(self.licenses).hexdigest()
        self.sources = {}
        for job in self.recipe['jobs']:
            path = BOARD.format(job['nativeSlot']+1)
            raw = f'synthetic original slot {job["nativeSlot"]}'.encode()
            self.sources[path] = raw
            job['sourceSha256'] = hashlib.sha256(raw).hexdigest()

    def test_all_authored_boards_encode_from_owned_recipe(self):
        result = apply_boards(self.sources.__getitem__, self.recipe, self.licenses)
        self.assertEqual(len(result), 12)
        self.assertTrue(all(len(raw) == 1160 for raw in result.values()))
        for job in self.recipe['jobs']:
            self.assertEqual(hashlib.sha256(result[BOARD.format(job['nativeSlot']+1)]).hexdigest(),
                             job['candidateSha256'])

    def test_wrong_sources_reject_whole_candidate(self):
        self.sources[BOARD.format(12)] = b'conflicting board replacement'
        with self.assertRaisesRegex(ValueError, 'Unsupported source board'):
            apply_boards(self.sources.__getitem__, self.recipe, self.licenses)
        with self.assertRaisesRegex(ValueError, 'license table'):
            apply_boards(self.sources.__getitem__, self.recipe, b'changed grants')

    def test_slot_aliases_and_incomplete_roster_rejected(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['jobs'][-1]['nativeSlot'] = 0
        with self.assertRaisesRegex(ValueError, 'native slots'):
            apply_boards(self.sources.__getitem__, recipe, self.licenses)

    def test_corrupt_geometry_and_special_inventory_rejected(self):
        for mutate in [lambda j: j['cells'].append(copy.deepcopy(j['cells'][0])),
                       lambda j: j['cells'][0].update(x=24),
                       lambda j: j['cells'][0].update(x=True),
                       lambda j: j.update(cells=[c for c in j['cells'] if c['licenseId'] != 18])]:
            with self.subTest(mutate=mutate):
                job = copy.deepcopy(self.recipe['jobs'][0])
                mutate(job)
                with self.assertRaises(ValueError):
                    encode_board(job)

    def test_detached_special_rejected_before_hash(self):
        job = copy.deepcopy(self.recipe['jobs'][0])
        occupied = {(c['x'], c['y']) for c in job['cells']}
        empty = next((x,y) for y in range(24) for x in range(24)
                     if all(p not in occupied for p in [(x,y),(x-1,y),(x+1,y),(x,y-1),(x,y+1)]))
        next(c for c in job['cells'] if c['licenseId'] == 18).update(x=empty[0], y=empty[1])
        with self.assertRaisesRegex(ValueError, 'pendant leaf'):
            encode_board(job)

    def test_resource_paths_cannot_escape(self):
        for value in ('../save.bin', '/absolute.bin', 'C:/save.bin', 'a\\b', 'a/../b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_path(value)


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source integration')
class FullBaseJobsBuildTest(unittest.TestCase):
    def test_source_build_has_exact_payload_and_valid_embedded_manifest(self):
        from ivalice_legacy.base_jobs import build
        from ivalice_legacy.job_labels import encode_label, parse_text
        with tempfile.TemporaryDirectory() as directory:
            archive, manifest = build(os.environ['IVALICE_LEGACY_GAME'], directory)
            with zipfile.ZipFile(archive) as packed:
                self.assertEqual(len(packed.namelist()), 18)
                self.assertEqual(set(packed.namelist()), set(manifest['files']))
                for relative, digest in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(packed.read(relative)).hexdigest(), digest)
                embedded = json.loads(packed.read('IvaliceLegacy-BaseJobs/manifest.json'))
                self.assertTrue(embedded['manifestExcludesOwnHash'])
                self.assertEqual(len(embedded['files']), 17)
                for relative, digest in embedded['files'].items():
                    self.assertEqual(digest, manifest['files'][relative])
                root = Path(__file__).resolve().parents[1]
                recipe = json.loads((root/'patches/base_jobs.json').read_text())
                for job in recipe['jobs']:
                    relative = 'mods/deploy/ff12data/'+BOARD.format(job['nativeSlot']+1)
                    self.assertEqual(manifest['files'][relative], job['candidateSha256'])
                labels = recipe['labels'][0]
                raw = packed.read('mods/deploy/ff12data/'+labels['archivePath'])
                parsed = parse_text(raw)
                for row in labels['changes']:
                    index = parsed['ids'].index(row['stringId'])
                    start = parsed['group']+parsed['pointers'][index]
                    self.assertEqual(raw[start:raw.index(0,start)+1], encode_label(row['replacement']))
