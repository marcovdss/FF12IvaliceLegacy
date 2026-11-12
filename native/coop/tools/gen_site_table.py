"""Generate src/patch/sites.inc, the compiled-in patch site table, from the reverse-engineered site table JSON.

Inputs (never written):
  --site-table PATH   the reverse-engineered site table JSON (env ILC_SITE_TABLE)
  --game PATH         the game folder; the exe is x64/FFXII_TZA.exe (env IVALICE_LEGACY_GAME)
  tools/sites_legacy.json  Legacy-only sites, appended after the table's rows (checked the same way)

The generator uses the JSON's `rva` column (the specs' "RVAs" are VAs; RVA = VA - 0x120000), checks every
expected byte string against the exe on disk, and scans the exe for references into the middle of each displaced
range (branch targets from decoded code, rel32/rel8 branch candidates, and 32/64-bit values in every section that
equal an interior RVA or VA). A site with such a reference gets the INTERIOR flag, and the patch layer refuses
it. For a PATCH site the scanned range is at least the 5 bytes MinHook overwrites.

Hook window (generator version 2): for every PATCH site, `hook_len` is the length of the whole instructions that
overlap MinHook's 5-byte jump (what MinHook relocates into its trampoline), decoded with capstone from the exe.
When it is longer than the site's recorded length, the extra bytes are emitted as `hook_tail` and the patch layer checks them
too, so every byte MinHook reads or writes is pinned. Other policies get hook_len 0 and an empty tail.

  python gen_site_table.py            rewrite src/patch/sites.inc
  python gen_site_table.py --check    exit 1 if the committed file differs from a fresh generation (drift)

Policy per site (what the patch engine does with it):
  PATCH    port / port-optional sites: must match, may be installed
  REQUIRE  verify-only and retired sites that must be stock: a mismatch refuses the whole install
  GATE     verify-only site whose mismatch skips the one site it gates (IN-06a -> IN-06)
  INFO     everything else (spec-gap, do-not-port, dropped, proposed, read-only discovery sites, and port sites dropped by a
           design decision): read and reported, never written, never a refusal
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COOP = HERE.parent
OUTPUT = COOP / 'src' / 'patch' / 'sites.inc'
# Legacy-only sites (not in the reverse-engineered table), appended after its rows so row numbers never move.
LEGACY_SITES = HERE / 'sites_legacy.json'
GENERATOR_VERSION = 2
# MinHook writes a 5-byte jmp rel32 at the site and relocates every instruction that overlaps those 5 bytes.
HOOK_JUMP = 5

EXPECT_BASE, EXPECT_TIMESTAMP, EXPECT_SIZE = 0x120000, 0x5DCA7FD7, 0x2F76000

# Subsystem per site group: the bit names of ILC_SUBSYSTEMS in include/ilc/bridge_abi.h. CORE = no bit (always
# armed while installed); NONE = never written.
GROUP_SUBSYSTEM = {
    'input-isolation': 'INPUT_ISOLATION',
    'movement': 'MOVEMENT', 'berserk': 'MOVEMENT', 'interaction': 'MOVEMENT',
    'leader': 'PARTY', 'party': 'PARTY', 'transition': 'PARTY', 'loot': 'PARTY',
    'camera': 'CAMERA',
    'firewall': 'FIREWALL_MENUS', 'frontend-gate': 'FIREWALL_MENUS', 'menu': 'FIREWALL_MENUS',
    'wait': 'WAIT',
    'combat-log': 'RENDER', 'treasure-log': 'RENDER', 'hud-colour': 'RENDER', 'damage-number': 'RENDER',
    'targeting': 'TARGETS_CARDS', 'cards': 'TARGETS_CARDS',
    'game-thread': 'CORE',
    'retired': 'NONE',
}
KIND = {'detour': 'DETOUR', 'call-redirect': 'CALL', 'imm-patch': 'IMM', 'byte-patch': 'BYTES',
        'verify-only': 'VERIFY', 'discovery-only': 'VERIFY', 'retired-must-be-stock': 'VERIFY'}
# Port sites dropped by design decisions, with the reason.
DROPPED_BY_DECISION = {
    'IN-04': 'player 1 input is written into the device fields at the finalize sites instead',
    'IN-09': 'player 1 input is written into the device fields at the finalize sites instead',
    'LD-01': 'blocking the stock leader setter would break mods that change the leader; adopt the change instead',
}
GATES = {'IN-06a': 'IN-06'}           # a mismatch here (another mod rewrote the approach) skips the gated site
VERIFY_INFO = {'CD-01', 'CD-02'}      # read-only discovery: a mismatch is information
# Interior references reviewed and found harmless, with the reason. Empty: nothing has been waived.
INTERIOR_WAIVED: dict[str, str] = {}

FLAG_INTERIOR = 0x01
FLAG_DROPPED = 0x02
FLAG_RIPREL = 0x04


class Image:
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        b = self.data
        p = struct.unpack_from('<I', b, 60)[0]
        if b[:2] != b'MZ' or b[p:p + 4] != b'PE\0\0':
            raise ValueError(f'{path} is not a PE file')
        self.timestamp = struct.unpack_from('<I', b, p + 8)[0]
        count = struct.unpack_from('<H', b, p + 6)[0]
        optional = struct.unpack_from('<H', b, p + 20)[0]
        o = p + 24
        if struct.unpack_from('<H', b, o)[0] != 0x20B:
            raise ValueError('not PE32+')
        self.base = struct.unpack_from('<Q', b, o + 24)[0]
        self.size_of_image = struct.unpack_from('<I', b, o + 56)[0]
        self.pdata_dir = struct.unpack_from('<II', b, o + 112 + 8 * 3)
        self.sections = []
        for i in range(count):
            s = o + optional + 40 * i
            name = b[s:s + 8].rstrip(b'\0').decode('ascii', 'replace')
            vsize, rva, rsize, offset = struct.unpack_from('<4I', b, s + 8)
            self.sections.append((name, rva, vsize, offset, rsize))

    def read(self, rva: int, size: int) -> bytes:
        for _, srva, vsize, offset, rsize in self.sections:
            if srva <= rva and rva + size <= srva + max(vsize, rsize):
                start = rva - srva
                chunk = self.data[offset + start: offset + min(start + size, rsize)]
                return chunk + bytes(size - len(chunk))
        raise ValueError(f'rva {rva:#x} (+{size}) is outside every section')

    def section(self, name: str):
        for sec in self.sections:
            if sec[0] == name:
                return sec
        raise ValueError(f'no {name} section')

    def functions(self) -> list[tuple[int, int]]:
        rva, size = self.pdata_dir
        raw = self.read(rva, size)
        out = [struct.unpack_from('<II', raw, i)[0:2] for i in range(0, size - size % 12, 12)]
        return sorted((b, e) for b, e in out if b and e > b)


def parse_hex(text: str) -> bytes:
    return bytes(int(t, 16) for t in text.split())


def interior_references(image: Image, sites: list[dict]) -> dict[str, list[str]]:
    """References that land strictly inside a displaced range: (va, va + length)."""
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs, CS_GRP_JUMP, CS_GRP_CALL
    import numpy as np

    cs = Cs(CS_ARCH_X86, CS_MODE_64)
    cs.detail = True
    interior: dict[int, str] = {}
    for site in sites:
        rva, length = int(site['rva'], 16), site['length']
        for k in range(1, length):
            interior[rva + k] = site['id']
    found: dict[str, set[str]] = {site['id']: set() for site in sites}
    functions = image.functions()
    starts = [b for b, _ in functions]

    def function_of(rva: int):
        i = bisect.bisect_right(starts, rva) - 1
        if i >= 0 and functions[i][0] <= rva < functions[i][1]:
            return functions[i]
        return None

    boundary_cache: dict[tuple[int, int], tuple[set[int], list[tuple[int, int]]]] = {}

    def decode_function(fn):
        """Linear decode of one .pdata function: instruction starts and (insn rva, branch target rva)."""
        if fn not in boundary_cache:
            begin, end = fn
            code = image.read(begin, end - begin)
            boundaries, branches = set(), []
            for insn in cs.disasm(code, begin):
                boundaries.add(insn.address)
                if (insn.group(CS_GRP_JUMP) or insn.group(CS_GRP_CALL)) and insn.operands and \
                        insn.operands[0].type == 2:  # X86_OP_IMM
                    branches.append((insn.address, insn.operands[0].imm))
            boundary_cache[fn] = (boundaries, branches)
        return boundary_cache[fn]

    # 1. Every branch inside the functions that contain a site.
    for site in sites:
        fn = function_of(int(site['rva'], 16))
        if fn is None:
            continue
        _, branches = decode_function(fn)
        for at, target in branches:
            if target in interior:
                found[interior[target]].add(f'branch at rva {at:#x} -> {target:#x}')

    # 2. rel32 / rel8 branch candidates anywhere in .text, confirmed by decoding the containing function.
    _, text_rva, text_vsize, text_off, text_rsize = image.section('.text')
    text = np.frombuffer(image.data[text_off:text_off + text_rsize], dtype=np.uint8)
    keys = np.array(sorted(interior), dtype=np.int64)
    n = len(text)

    def rel32_at(k: int):
        """For every position p, the signed 32-bit value stored at p + k."""
        m = n - k - 3
        value = (text[k:k + m].astype(np.int64) | (text[k + 1:k + 1 + m].astype(np.int64) << 8) |
                 (text[k + 2:k + 2 + m].astype(np.int64) << 16) | (text[k + 3:k + 3 + m].astype(np.int64) << 24))
        return np.where(value >= 1 << 31, value - (1 << 32), value)

    candidates = []
    rel_after_1 = rel32_at(1)   # E8/E9 rel32 at +1, length 5
    pos = np.arange(len(rel_after_1), dtype=np.int64)
    target = text_rva + pos + 5 + rel_after_1
    mask = np.isin(target, keys) & ((text[:len(pos)] == 0xE8) | (text[:len(pos)] == 0xE9))
    candidates += [(int(p), int(t)) for p, t in zip(pos[mask], target[mask])]
    rel_after_2 = rel32_at(2)   # 0F 8x rel32 at +2, length 6
    pos2 = np.arange(len(rel_after_2), dtype=np.int64)
    target2 = text_rva + pos2 + 6 + rel_after_2
    first, second = text[:len(pos2)], text[1:len(pos2) + 1]
    mask2 = np.isin(target2, keys) & (first == 0x0F) & (second >= 0x80) & (second <= 0x8F)
    candidates += [(int(p), int(t)) for p, t in zip(pos2[mask2], target2[mask2])]
    rel8 = text[1:].astype(np.int64)
    rel8 = np.where(rel8 >= 128, rel8 - 256, rel8)
    pos8 = np.arange(len(rel8), dtype=np.int64)
    target8 = text_rva + pos8 + 2 + rel8
    op = text[:len(pos8)]
    mask8 = np.isin(target8, keys) & ((op == 0xEB) | ((op >= 0x70) & (op <= 0x7F)) | ((op >= 0xE0) & (op <= 0xE3)))
    candidates += [(int(p), int(t)) for p, t in zip(pos8[mask8], target8[mask8])]
    own_boundaries = {}
    for site in sites:
        rva = int(site['rva'], 16)
        for insn in cs.disasm(parse_hex(site['expected']), rva):
            own_boundaries[insn.address] = site['id']
    for offset, tgt in candidates:
        at = text_rva + offset
        if at in interior and at not in own_boundaries:
            continue  # bytes inside a displaced instruction: not an instruction start
        fn = function_of(at)
        if fn is None:
            found[interior[tgt]].add(f'possible branch at rva {at:#x} (outside .pdata) -> {tgt:#x}')
            continue
        boundaries, _ = decode_function(fn)
        if at in boundaries:
            found[interior[tgt]].add(f'branch at rva {at:#x} -> {tgt:#x}')

    operands: dict[str, set[str]] = {site['id']: set() for site in sites}
    cs_detail = Cs(CS_ARCH_X86, CS_MODE_64)
    cs_detail.detail = True

    def operand_field(rva: int) -> bool:
        """True if rva lies inside an instruction found by decoding the containing .pdata function linearly, and
        is either the first byte of its rel32/disp32 field or not 4-byte aligned (MSVC jump tables are aligned
        arrays of RVAs). Such values are operand bytes, not a table entry."""
        fn = function_of(rva)
        if fn is None:
            return False
        begin, end = fn
        for insn in cs_detail.disasm(image.read(begin, end - begin), begin):
            if insn.address <= rva < insn.address + insn.size:
                off = rva - insn.address
                if off >= 1 and rva % 4:
                    return True
                return (insn.disp_size == 4 and insn.disp_offset == off) or                     (insn.imm_size == 4 and insn.imm_offset == off and
                     (insn.group(CS_GRP_JUMP) or insn.group(CS_GRP_CALL)))
        return False

    # 3. 32-bit RVA and 64-bit VA values anywhere in the mapped sections (jump tables, scope tables, pointers).
    for name, srva, vsize, offset, rsize in image.sections:
        raw = image.data[offset:offset + rsize]
        arr = np.frombuffer(raw, dtype=np.uint8).astype(np.int64)
        if len(arr) < 8:
            continue
        v32 = arr[:-3] | (arr[1:-2] << 8) | (arr[2:-1] << 16) | (arr[3:] << 24)
        for p in np.nonzero(np.isin(v32, keys))[0]:
            value = int(v32[p])
            if name == '.text' and operand_field(srva + int(p)):
                operands[interior[value]].add(f'dword {value:#x} at {name}+{int(p):#x} lies in the operand '
                                              'bytes of a decoded instruction')
                continue
            found[interior[value]].add(f'dword {value:#x} at {name}+{int(p):#x}')
        vas = keys + image.base
        hits = np.nonzero(np.isin(v32, vas))[0]
        for p in hits:
            if p + 8 <= len(arr) and int(arr[p + 4]) == 0 and int(arr[p + 5]) == 0 and \
                    int(arr[p + 6]) == 0 and int(arr[p + 7]) == 0:
                va = int(v32[p])
                found[interior[va - image.base]].add(f'qword VA {va:#x} at {name}+{int(p):#x}')
    return {k: sorted(v) for k, v in found.items()}, {k: sorted(v) for k, v in operands.items()}


def hook_window(image: Image, rva: int) -> int:
    """Bytes of the whole instructions starting at rva that cover MinHook's 5-byte jump."""
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs

    total = 0
    for insn in Cs(CS_ARCH_X86, CS_MODE_64).disasm(image.read(rva, 32), rva):
        total += insn.size
        if total >= HOOK_JUMP:
            return total
    raise ValueError(f'rva {rva:#x}: cannot decode {HOOK_JUMP} bytes of whole instructions')


def short_purpose(text: str) -> str:
    text = re.split(r'[.(:;]', text or '', maxsplit=1)[0].strip()
    words = text.split()
    return ' '.join(words[:7])


def build_rows(table: dict, image: Image) -> list[dict]:
    ids = [s['id'] for s in table['sites']]
    rows = []
    for site in table['sites']:
        sid = site['id']
        rva = int(site['rva'], 16)
        va = int(site['va'], 16)
        if va - EXPECT_BASE != rva:
            raise ValueError(f'{sid}: rva {rva:#x} != va {va:#x} - image base')
        if not 0 < rva < EXPECT_SIZE:
            raise ValueError(f'{sid}: rva {rva:#x} outside the image')
        expected = parse_hex(site['expected'])
        if len(expected) != site['length']:
            raise ValueError(f'{sid}: expected bytes {len(expected)} != length {site["length"]}')
        disk = image.read(rva, len(expected))
        if disk != expected:
            raise ValueError(f'{sid}: exe bytes {disk.hex(" ")} != expected {expected.hex(" ")}')
        legacy, kind = site['legacy'], site['kind']
        flags = 0
        gate = ''
        if legacy in ('port', 'port-optional'):
            policy = 'PATCH'
            if sid in DROPPED_BY_DECISION:
                policy, flags = 'INFO', flags | FLAG_DROPPED
        elif sid in GATES:
            policy, gate = 'GATE', GATES[sid]
            if gate not in ids:
                raise ValueError(f'{sid}: gated site {gate} is not in the table')
        elif legacy == 'verify-only' and sid not in VERIFY_INFO:
            policy = 'REQUIRE'
        elif kind == 'retired-must-be-stock':
            policy = 'REQUIRE'
        else:
            policy = 'INFO'
        if site.get('rip_relative_in_displaced'):
            flags |= FLAG_RIPREL
        if site['group'] not in GROUP_SUBSYSTEM:
            raise ValueError(f'{sid}: group {site["group"]} has no subsystem mapping')
        if kind not in KIND:
            raise ValueError(f'{sid}: kind {kind} unknown')
        hook_len, tail = 0, b''
        if policy == 'PATCH':
            hook_len = hook_window(image, rva)
            tail = image.read(rva + len(expected), hook_len - len(expected)) if hook_len > len(expected) else b''
        rows.append({'id': sid, 'rva': rva, 'length': len(expected), 'expected': expected,
                     'hook_len': hook_len, 'hook_tail': tail,
                     'kind': KIND[kind], 'policy': policy, 'subsystem': GROUP_SUBSYSTEM[site['group']],
                     'flags': flags, 'gate': gate, 'group': site['group'], 'legacy': legacy,
                     'purpose': short_purpose(site.get('purpose', ''))})
    return rows


def source_digest(table: dict) -> str:
    """Digest of exactly the JSON fields the generator reads, so unrelated edits are not drift."""
    keep = ['id', 'group', 'kind', 'legacy', 'va', 'rva', 'length', 'expected', 'rip_relative_in_displaced',
            'purpose']
    projection = {'build': {k: table['build'][k] for k in ('sha256', 'image_base', 'pe_timestamp',
                                                             'size_of_image')},
                  'sites': [{k: s.get(k) for k in keep} for s in table['sites']]}
    return hashlib.sha256(json.dumps(projection, sort_keys=True).encode()).hexdigest()


def c_bytes(data: bytes) -> str:
    return '"' + ''.join(f'\\x{b:02X}' for b in data) + '"'


def render(table: dict, image: Image) -> str:
    build = table['build']
    if (int(build['image_base'], 16), int(build['pe_timestamp'], 16), int(build['size_of_image'], 16)) != \
            (EXPECT_BASE, EXPECT_TIMESTAMP, EXPECT_SIZE):
        raise ValueError('site table build identity is not the pinned 1.0.4.0 build')
    if (image.base, image.timestamp, image.size_of_image) != (EXPECT_BASE, EXPECT_TIMESTAMP, EXPECT_SIZE):
        raise ValueError('exe identity is not the pinned build')
    sha = hashlib.sha256(image.data).hexdigest()
    if sha != build['sha256']:
        raise ValueError(f'exe SHA-256 {sha} != site table {build["sha256"]}')
    rows = build_rows(table, image)
    patch_ids = {r['id'] for r in rows if r['policy'] == 'PATCH'}
    scan = [dict(s, length=max(s['length'], HOOK_JUMP)) if s['id'] in patch_ids else s for s in table['sites']]
    refs, operand_notes = interior_references(image, scan)
    index = {r['id']: i for i, r in enumerate(rows)}
    lines = [
        '// GENERATED by native/coop/tools/gen_site_table.py from the site table. Do not edit by hand.',
        f'// generator-version: {GENERATOR_VERSION}',
        f'// source-digest: {source_digest(table)}',
        f'// exe-sha256: {sha}',
        '// Columns: id, rva (added to the module base; never a VA), length, kind, policy, subsystem, flags,',
        '//          gated site index (or ILC_SITE_NO_GATE), expected original bytes, hook_len (PATCH: whole',
        '//          instructions under the 5-byte hook jump; else 0), hook_tail (pinned bytes past length up to',
        '//          hook_len), purpose (short).',
        '// Flags: 0x01 INTERIOR (a reference lands inside the displaced range: never relocated),',
        '//        0x02 DROPPED by a design decision, 0x04 RIP-relative operand in the displaced bytes.',
        '',
        f'#define ILC_SITE_COUNT {len(rows)}',
        f'#define ILC_SITE_BUILD_IMAGE_BASE 0x{EXPECT_BASE:X}ull',
        f'#define ILC_SITE_BUILD_TIMESTAMP 0x{EXPECT_TIMESTAMP:X}u  // NOTADDR: PE header TimeDateStamp',
        f'#define ILC_SITE_BUILD_SIZE_OF_IMAGE 0x{EXPECT_SIZE:X}u  // NOTADDR: SizeOfImage',
        '',
        '#define ILC_SITE_LIST(X) \\',
    ]
    interior_notes = []
    for r in rows:
        flags = r['flags']
        hits = refs.get(r['id'], [])
        if hits and r['id'] not in INTERIOR_WAIVED:
            flags |= FLAG_INTERIOR
            interior_notes.append((r['id'], hits))
        gate = str(index[r['gate']]) if r['gate'] else 'ILC_SITE_NO_GATE'
        purpose = r['purpose'].replace('\\', '/').replace('"', "'")
        lines.append(f'    X("{r["id"]}", 0x{r["rva"]:06X}u, {r["length"]}, ILC_SITE_KIND_{r["kind"]}, '
                     f'ILC_SITE_POLICY_{r["policy"]}, ILC_SITE_SUBSYS_{r["subsystem"]}, 0x{flags:02X}, {gate}, '
                     f'{c_bytes(r["expected"])}, {r["hook_len"]}, {c_bytes(r["hook_tail"])}, "{purpose}") \\')
    lines.append('')
    lines.append('// Interior references found by the generator (sites flagged INTERIOR):')
    if not interior_notes:
        lines.append('//   none')
    for sid, hits in interior_notes:
        for h in hits[:6]:
            lines.append(f'//   {sid}: {h}')
        if len(hits) > 6:
            lines.append(f'//   {sid}: ... {len(hits) - 6} more')
    lines.append('// Interior values inside the operand bytes of decoded instructions (not flagged):')
    noted = [(sid, note) for sid, notes in operand_notes.items() for note in notes]
    if not noted:
        lines.append('//   none')
    for sid, note in noted:
        lines.append(f'//   {sid}: {note}')
    return '\n'.join(lines) + '\n'


def resolve_inputs(args) -> tuple[Path, Path]:
    table = args.site_table or os.environ.get('ILC_SITE_TABLE')
    game = args.game or os.environ.get('IVALICE_LEGACY_GAME')
    if not table:
        raise SystemExit('no site table: pass --site-table or set ILC_SITE_TABLE')
    if not game:
        raise SystemExit('no game folder: pass --game or set IVALICE_LEGACY_GAME')
    return Path(table), Path(game) / 'x64' / 'FFXII_TZA.exe'


def generate(table_path: Path, exe_path: Path) -> str:
    table = json.loads(table_path.read_text(encoding='utf-8'))
    return render(with_legacy_sites(table), Image(exe_path))


def with_legacy_sites(table: dict) -> dict:
    """The table plus LEGACY_SITES appended after its rows (ids must be new; the rows are checked like any other)."""
    extra = json.loads(LEGACY_SITES.read_text(encoding='utf-8'))['sites']
    known = {s['id'] for s in table['sites']}
    for site in extra:
        if site['id'] in known:
            raise ValueError(f'{LEGACY_SITES.name}: {site["id"]} is already in the site table')
        known.add(site['id'])
    return dict(table, sites=list(table['sites']) + extra)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--site-table', type=Path)
    parser.add_argument('--game', type=Path)
    parser.add_argument('--check', action='store_true', help='fail if the committed sites.inc is stale')
    parser.add_argument('--out', type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    table_path, exe_path = resolve_inputs(args)
    text = generate(table_path, exe_path)
    if args.check:
        current = args.out.read_bytes().decode('utf-8').replace('\r\n', '\n') if args.out.is_file() else None
        if current != text:
            print(f'site table drift: {args.out.name} differs from a fresh generation; '
                  'run tools/gen_site_table.py after an intended change', file=sys.stderr)
            return 1
        print('site table: current')
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(text.encode('utf-8'))
    print(f'wrote {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
