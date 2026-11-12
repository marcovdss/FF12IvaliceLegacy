"""Audit a built co-op DLL's exports and imports against pe-allowlist.json.

Checks, each one a failure on its own:
  * machine is AMD64 and the image is a DLL with an entry point;
  * the export names are exactly the allow-list's, none forwarded, each in an executable section;
  * every imported DLL and function is on the allow-list;
  * no denied DLL (network, platform services) is imported, delay-loaded, or named anywhere in the
    image as an ASCII or UTF-16 string (which would hint at a LoadLibrary at run time);
  * no denied DLL is named without its extension either (word-bounded, ASCII or UTF-16);
  * no denied function name or prefix is imported, or stored as a NUL-terminated string (GetProcAddress);
  * no denied marker string (emutls, pthread) appears, and no code reads the TLS array (gs:[0x58]);
  * no export is ordinal-only (every exported function has a name);
  * the required DllCharacteristics are set; no delay imports unless allowed; a TLS directory only when allowed,
    and then within the allow-list's template-size and callback-count limits (the CRT's tlssup, nothing else).

Standard library only. Exit code 0 = PASS, 1 = FAIL, 2 = the file could not be parsed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

MACHINES = {0x8664: 'AMD64', 0x14C: 'I386', 0xAA64: 'ARM64'}
DLL_CHARACTERISTICS = {
    'HIGH_ENTROPY_VA': 0x0020, 'DYNAMIC_BASE': 0x0040, 'FORCE_INTEGRITY': 0x0080, 'NX_COMPAT': 0x0100,
    'NO_ISOLATION': 0x0200, 'NO_SEH': 0x0400, 'NO_BIND': 0x0800, 'APPCONTAINER': 0x1000,
    'WDM_DRIVER': 0x2000, 'GUARD_CF': 0x4000, 'TERMINAL_SERVER_AWARE': 0x8000,
}
IMAGE_FILE_DLL = 0x2000
SCN_MEM_EXECUTE = 0x20000000
DIR_EXPORT, DIR_IMPORT, DIR_TLS, DIR_DELAY_IMPORT = 0, 1, 9, 13


class PEError(ValueError):
    pass


class PE:
    """Just enough of a PE32+ reader for the audit and for mapping sections in tests."""

    def __init__(self, data: bytes):
        self.data = data
        if data[:2] != b'MZ':
            raise PEError('no MZ header')
        lfanew = self._u32(0x3C)
        if data[lfanew:lfanew + 4] != b'PE\0\0':
            raise PEError('no PE signature')
        coff = lfanew + 4
        (self.machine, nsections, self.timestamp, _, _, opt_size,
         self.characteristics) = struct.unpack_from('<HHIIIHH', data, coff)
        opt = coff + 20
        if self._u16(opt) != 0x20B:
            raise PEError('not a PE32+ image')
        self.entry_rva = self._u32(opt + 16)
        self.image_base = struct.unpack_from('<Q', data, opt + 24)[0]
        self.size_of_image = self._u32(opt + 56)
        self.size_of_headers = self._u32(opt + 60)
        self.dll_characteristics = self._u16(opt + 70)
        ndirs = self._u32(opt + 108)
        self.dirs = [struct.unpack_from('<II', data, opt + 112 + 8 * i) for i in range(min(ndirs, 16))]
        self.dirs += [(0, 0)] * (16 - len(self.dirs))
        self.sections = []
        sec = opt + opt_size
        for i in range(nsections):
            name, vsize, va, raw_size, raw_ptr = struct.unpack_from('<8sIIII', data, sec + 40 * i)
            flags = self._u32(sec + 40 * i + 36)
            self.sections.append({'name': name.rstrip(b'\0').decode('ascii', 'replace'), 'va': va,
                                  'vsize': vsize, 'raw_size': raw_size, 'raw_ptr': raw_ptr, 'flags': flags})

    def _u16(self, off: int) -> int:
        return struct.unpack_from('<H', self.data, off)[0]

    def _u32(self, off: int) -> int:
        return struct.unpack_from('<I', self.data, off)[0]

    def section_of(self, rva: int):
        for s in self.sections:
            if s['va'] <= rva < s['va'] + max(s['vsize'], s['raw_size']):
                return s
        return None

    def offset(self, rva: int) -> int:
        if rva < self.size_of_headers:
            return rva
        s = self.section_of(rva)
        if s is None or rva - s['va'] >= s['raw_size']:
            raise PEError(f'RVA 0x{rva:X} is not backed by file data')
        return s['raw_ptr'] + rva - s['va']

    def u32_at(self, rva: int) -> int:
        return self._u32(self.offset(rva))

    def u64_at(self, rva: int) -> int:
        return struct.unpack_from('<Q', self.data, self.offset(rva))[0]

    def cstr_at(self, rva: int) -> str:
        off = self.offset(rva)
        end = self.data.index(b'\0', off)
        return self.data[off:end].decode('ascii', 'replace')

    def exports(self) -> list[dict]:
        rva, size = self.dirs[DIR_EXPORT]
        if not rva:
            return []
        off = self.offset(rva)
        base, nfuncs, nnames, funcs, names, ords = struct.unpack_from('<IIIIII', self.data, off + 16)
        result = []
        for i in range(nnames):
            name = self.cstr_at(self.u32_at(names + 4 * i))
            index = struct.unpack_from('<H', self.data, self.offset(ords + 2 * i))[0]
            func = self.u32_at(funcs + 4 * index)
            result.append({'name': name, 'ordinal': base + index, 'rva': func,
                           'forwarded': rva <= func < rva + size})
        return result

    def export_counts(self) -> tuple[int, int, list[int]]:
        """(number of functions, number of names, RVAs of functions that have no name)."""
        rva, _ = self.dirs[DIR_EXPORT]
        if not rva:
            return 0, 0, []
        off = self.offset(rva)
        base, nfuncs, nnames, funcs, names, ords = struct.unpack_from('<IIIIII', self.data, off + 16)
        named = {struct.unpack_from('<H', self.data, self.offset(ords + 2 * i))[0] for i in range(nnames)}
        unnamed = [self.u32_at(funcs + 4 * i) for i in range(nfuncs) if i not in named]
        return nfuncs, nnames, [f for f in unnamed if f]

    def tls(self) -> dict | None:
        """IMAGE_TLS_DIRECTORY64: template size and callback VAs, or None without a TLS directory."""
        rva, _ = self.dirs[DIR_TLS]
        if not rva:
            return None
        start, end, index, callbacks = struct.unpack_from('<QQQQ', self.data, self.offset(rva))
        zero_fill = self._u32(self.offset(rva) + 32)
        found = []
        if callbacks:
            at = callbacks - self.image_base
            while True:
                value = self.u64_at(at)
                if value == 0 or len(found) > 64:
                    break
                found.append(value)
                at += 8
        return {'templateBytes': (end - start) + zero_fill, 'callbacks': found}

    def _thunk_names(self, thunk_rva: int) -> list[str]:
        names = []
        while thunk_rva:
            value = self.u64_at(thunk_rva)
            if value == 0:
                break
            if value & (1 << 63):
                names.append(f'#ordinal{value & 0xFFFF}')
            else:
                names.append(self.cstr_at((value & 0x7FFFFFFF) + 2))
            thunk_rva += 8
        return names

    def imports(self) -> dict[str, list[str]]:
        rva, _ = self.dirs[DIR_IMPORT]
        result: dict[str, list[str]] = {}
        while rva:
            original, _, _, name, first = struct.unpack_from('<IIIII', self.data, self.offset(rva))
            if not (original or name or first):
                break
            result.setdefault(self.cstr_at(name), []).extend(self._thunk_names(original or first))
            rva += 20
        return result

    def delay_imports(self) -> dict[str, list[str]]:
        rva, _ = self.dirs[DIR_DELAY_IMPORT]
        result: dict[str, list[str]] = {}
        while rva:
            fields = struct.unpack_from('<IIIIIIII', self.data, self.offset(rva))
            if not any(fields):
                break
            result.setdefault(self.cstr_at(fields[1]), []).extend(self._thunk_names(fields[4]))
            rva += 32
        return result


def _strings_mentioning(data: bytes, needles: list[str]) -> list[str]:
    lowered = data.lower()
    found = []
    for needle in needles:
        n = needle.lower().encode('ascii')
        if n in lowered or n.decode().encode('utf-16-le') in lowered:
            found.append(needle)
    return found


def _bare_dll_names_mentioned(data: bytes, dlls: list[str]) -> list[str]:
    """Denied DLL names written without ".dll" (e.g. LoadLibraryA("ws2_32")), word-bounded, ASCII or UTF-16."""
    found = []
    for dll in dlls:
        stem = dll.lower().removesuffix('.dll')
        ascii_pattern = re.compile(rb'(?<![a-z0-9_.])' + re.escape(stem.encode()) + rb'(?![a-z0-9_])', re.I)
        wide = b''.join(bytes([b, 0]) for b in stem.encode())
        wide_pattern = re.compile(rb'(?<![a-z0-9_.]\x00)' + re.escape(wide) + rb'(?![a-z0-9_]\x00)', re.I)
        if ascii_pattern.search(data) or wide_pattern.search(data):
            found.append(stem)
    return found


def _function_strings(data: bytes, names: set[str], prefixes: tuple[str, ...], imported: set[str]) -> list[str]:
    """Denied function names stored as NUL-terminated ASCII strings (what GetProcAddress takes)."""
    found = set()
    for match in re.finditer(rb'(?<![!-~])([A-Za-z_][A-Za-z0-9_]{2,})\x00', data):
        text = match.group(1).decode('ascii')
        if text in imported:
            continue  # already judged by the import checks
        if text in names or text.startswith(prefixes):
            found.add(text)
    return sorted(found)


# mov r64, gs:[0x58] (TEB.ThreadLocalStoragePointer): what native thread_local compiles to on x64 Windows.
# Any REX.W prefix (0x48-0x4F covers every R/X/B combination) and any destination register.
_TLS_ARRAY_READ = re.compile(rb'\x65[\x48-\x4f]\x8b[\x04\x0c\x14\x1c\x24\x2c\x34\x3c]\x25\x58\x00\x00\x00')


def _tls_array_reads(pe: 'PE') -> list[int]:
    found = []
    for s in pe.sections:
        if not s['flags'] & SCN_MEM_EXECUTE:
            continue
        body = pe.data[s['raw_ptr']:s['raw_ptr'] + min(s['raw_size'], s['vsize'] or s['raw_size'])]
        found += [s['va'] + m.start() for m in _TLS_ARRAY_READ.finditer(body)]
    return found


def audit(data: bytes, allow: dict) -> dict:
    """Return {'ok': bool, 'failures': [...], 'exports': [...], 'imports': {...}, ...}."""
    failures: list[str] = []
    pe = PE(data)
    machine = MACHINES.get(pe.machine, f'0x{pe.machine:X}')
    if machine != allow['machine']:
        failures.append(f'machine {machine}, expected {allow["machine"]}')
    if not pe.characteristics & IMAGE_FILE_DLL:
        failures.append('image is not a DLL')
    if not pe.entry_rva:
        failures.append('no entry point')

    exports = pe.exports()
    names = sorted(e['name'] for e in exports)
    expected = sorted(allow['exports'])
    if names != expected:
        extra = sorted(set(names) - set(expected))
        missing = sorted(set(expected) - set(names))
        failures.append(f'exports differ: extra={extra} missing={missing}')
    nfuncs, nnames, unnamed = pe.export_counts()
    if unnamed:
        failures.append(f'ordinal-only exports at RVAs {[hex(r) for r in unnamed]} '
                        f'({nfuncs} functions, {nnames} names)')
    for e in exports:
        if e['forwarded']:
            failures.append(f'export {e["name"]} is forwarded')
            continue
        s = pe.section_of(e['rva'])
        if s is None or not s['flags'] & SCN_MEM_EXECUTE:
            failures.append(f'export {e["name"]} RVA 0x{e["rva"]:X} is not in an executable section')

    denied_dlls = {d.lower() for d in allow['deniedDlls']}
    denied_names = set(allow['deniedImportNames'])
    denied_prefixes = tuple(allow['deniedImportPrefixes'])
    allowed = {dll.lower(): set(funcs) for dll, funcs in allow['imports'].items()}

    def check_table(table: dict[str, list[str]], kind: str):
        for dll, funcs in table.items():
            key = dll.lower()
            if key in denied_dlls:
                failures.append(f'{kind} of denied DLL {dll}')
            elif key not in allowed:
                failures.append(f'{kind} of DLL {dll} is not on the allow-list')
            for func in funcs:
                if func in denied_names or func.startswith(denied_prefixes):
                    failures.append(f'{kind} of denied function {dll}!{func}')
                elif key in allowed and func not in allowed[key]:
                    failures.append(f'{kind} of {dll}!{func} is not on the allow-list')

    imports = pe.imports()
    delay = pe.delay_imports()
    check_table(imports, 'import')
    check_table(delay, 'delay import')
    if delay and not allow['allowDelayImports']:
        failures.append(f'delay imports present: {sorted(delay)}')
    tls = pe.tls()
    if tls is not None:
        if not allow['allowTls']:
            failures.append('TLS directory present (TLS callbacks run under the loader lock)')
        else:
            limits = allow.get('tls', {})
            if tls['templateBytes'] > limits.get('maxTemplateBytes', 0):
                failures.append(f'TLS template is {tls["templateBytes"]} bytes, more than the CRT tlssup allows '
                                f'({limits.get("maxTemplateBytes", 0)}): thread_local data?')
            if len(tls['callbacks']) > limits.get('maxCallbacks', 0):
                failures.append(f'{len(tls["callbacks"])} TLS callbacks, more than allowed '
                                f'({limits.get("maxCallbacks", 0)})')
    mentioned = _strings_mentioning(data, allow['deniedDlls'])
    if mentioned:
        failures.append(f'image names denied DLLs as strings: {mentioned}')
    bare = [stem for stem in _bare_dll_names_mentioned(data, allow['deniedDlls'])
            if stem + '.dll' not in [m.lower() for m in mentioned]]
    if bare:
        failures.append(f'image names denied DLLs without extension: {bare}')
    imported_names = {f for funcs in list(imports.values()) + list(delay.values()) for f in funcs}
    strings = _function_strings(data, denied_names, denied_prefixes, imported_names)
    if strings:
        failures.append(f'image stores denied function names as strings: {strings}')
    markers = [m for m in allow.get('deniedStrings', []) if m.encode() in data]
    if markers:
        failures.append(f'image contains denied marker strings: {markers}')
    tls_reads = _tls_array_reads(pe)
    if tls_reads:
        failures.append(f'code reads the TLS array (gs:[0x58], thread_local) at RVAs {[hex(r) for r in tls_reads]}')

    flags = [n for n, bit in DLL_CHARACTERISTICS.items() if pe.dll_characteristics & bit]
    for required in allow['requiredDllCharacteristics']:
        if required not in flags:
            failures.append(f'DllCharacteristics lacks {required}')

    return {
        'ok': not failures, 'failures': failures, 'sha256': hashlib.sha256(data).hexdigest(),
        'machine': machine, 'imageBase': f'0x{pe.image_base:X}', 'timestamp': f'0x{pe.timestamp:08X}',
        'dllCharacteristics': flags, 'entryRva': f'0x{pe.entry_rva:X}',
        'exports': [{'name': e['name'], 'ordinal': e['ordinal'], 'rva': f'0x{e["rva"]:X}'} for e in exports],
        'imports': imports, 'delayImports': delay, 'tls': tls,
        'sections': [{'name': s['name'], 'va': f'0x{s["va"]:X}', 'vsize': s['vsize'],
                      'flags': f'0x{s["flags"]:08X}'} for s in pe.sections],
    }


def format_report(path: Path, result: dict) -> str:
    lines = [f'file      {path.name}', f'sha256    {result["sha256"]}',
             f'machine   {result["machine"]}  base {result["imageBase"]}  timestamp {result["timestamp"]}'
             f'  entry {result["entryRva"]}',
             f'flags     {" ".join(result["dllCharacteristics"])}',
             'sections  ' + ' '.join(f'{s["name"]}({s["flags"]})' for s in result['sections']),
             'exports   ' + ', '.join(f'{e["name"]} @{e["ordinal"]} {e["rva"]}' for e in result['exports'])]
    for dll, funcs in result['imports'].items():
        lines.append(f'import    {dll}: {", ".join(funcs)}')
    for dll, funcs in result['delayImports'].items():
        lines.append(f'delay     {dll}: {", ".join(funcs)}')
    tls = result['tls']
    lines.append('tls       ' + ('none' if tls is None else
                                 f'template {tls["templateBytes"]} bytes, {len(tls["callbacks"])} callbacks'))
    lines += [f'FAIL      {f}' for f in result['failures']]
    lines.append('RESULT    ' + ('PASS' if result['ok'] else f'FAIL ({len(result["failures"])})'))
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('dll', nargs='?', type=Path, default=HERE / 'build' / 'ivalice-legacy-coop.dll')
    parser.add_argument('--allowlist', type=Path, default=HERE / 'pe-allowlist.json')
    parser.add_argument('--json', action='store_true', help='print the result as JSON')
    args = parser.parse_args(argv)
    try:
        result = audit(args.dll.read_bytes(), json.loads(args.allowlist.read_text(encoding='utf-8')))
    except (OSError, PEError, struct.error, ValueError) as error:
        print(f'cannot audit {args.dll.name}: {error}', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else format_report(args.dll, result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
