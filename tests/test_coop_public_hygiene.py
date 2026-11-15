"""The co-op sources are public: no internal planning identifiers in them.

Comments, strings and documentation in native/coop, the co-op Lua, the co-op tests and the repository's top-level
documents describe things in product terms. Planning, change, review and session identifiers (P0, C1.., S01.., S13b,
B01.., review findings such as S-1, N-3, N-a or SF-2, session ids such as CS-E1, decision ids such as D-14) belong to the developers' own notes,
not to the repository.
Vendored third-party text (licences, the build cache) is excluded; the generated files (sites.inc, the bridge schema)
are scanned, because they are built from our own sources.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
LUA_PACKAGE = ROOT / 'package' / 'x64' / 'scripts' / 'IvaliceLegacy' / 'coop'
PATTERN = re.compile(r'\b(P0|C[0-9]+|S[0-9]{2}b?|B[0-9]{2}|[SN]-(?:[0-9]+|[a-z])|SF-[0-9]+|CS-[A-Z0-9]+|D-[0-9]+)\b')
SUFFIXES = {'.py', '.cpp', '.h', '.inc', '.md', '.json', '.def', '.S', '.lua', '.txt'}
EXCLUDED_DIRS = {'build', '__pycache__'}
EXCLUDED_FILES = {'THIRD_PARTY_NOTICES.txt'}  # licence texts, verbatim


def scanned() -> list[Path]:
    files = []
    for path in sorted(COOP.rglob('*')):
        rel = path.relative_to(COOP)
        if not path.is_file() or path.suffix not in SUFFIXES or EXCLUDED_DIRS & set(rel.parts):
            continue
        if path.name in EXCLUDED_FILES or path.name.startswith('LICENSE'):
            continue
        files.append(path)
    files += sorted(p for p in LUA_PACKAGE.rglob('*.lua') if p.is_file())
    files.append(ROOT / 'package' / 'x64' / 'scripts' / 'IvaliceLegacyCoop.lua')
    files += sorted((ROOT / 'tests').glob('test_coop_*.py'))
    files += sorted((ROOT / 'tests').glob('coop_*.py'))
    files += [ROOT / 'README.md', ROOT / 'docs' / 'FEATURES.md']
    return [p for p in files if p.is_file()]


def hits(text: str) -> list[tuple[int, str]]:
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in PATTERN.finditer(line):
            found.append((number, match.group(0)))
    return found


class PublicHygieneTest(unittest.TestCase):
    def test_no_planning_identifiers(self):
        files = scanned()
        self.assertGreater(len(files), 40, 'instrument: too few files scanned')
        problems = []
        for path in files:
            if path.name == Path(__file__).name:
                continue  # this file names the pattern
            for number, token in hits(path.read_text(encoding='utf-8', errors='replace')):
                problems.append(f'{path.relative_to(ROOT).as_posix()}:{number}: {token}')
        self.assertEqual(problems, [])

    def test_instrument_catches_each_form(self):
        for sample in ('the P0 table', 'decision C7', 'row S13b', 'task B09', '(S01-S17)', 'S-1: the hold',
                       'N-3 and the flag', 'N-a: read back', 'fix SF-2', '(CS-E1)', 'session CS-12', 'decision D-14',
                       '(D-4)'):
            self.assertTrue(hits(sample), sample)
        for sample in ('CM-01', '0xC3', 'XINPUT', 'P1 seat', 'SDL2', 'B 09', 'c11', 'MV-07', 'IX-20', 'TW-01',
                       'N-way', 'S-curve', 'non-zero', 'CS-', 'HD-01', 'WD-3', 'D-pad'):
            self.assertFalse(hits(sample), sample)


if __name__ == '__main__':
    unittest.main()
