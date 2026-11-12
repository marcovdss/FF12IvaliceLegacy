"""Build the co-op module ivalice-legacy-coop.dll. No game files are read or written.

The output goes to native/coop/build/ (or --out). Nothing is copied into package/ or into a game folder.

Toolchain pin: MSYS2 UCRT64 g++ 16.2.0 (Rev3) with GNU ld 2.47.20260726, and the exact pacman package versions
in EXPECTED_PACKAGES (gcc, binutils, mingw-w64 CRT and headers); any other version is refused. The DLL uses the
normal mingw-w64 DLL start-up (DllMainCRTStartup) on the Universal CRT, with libgcc and libstdc++ linked
statically, and no exceptions or RTTI. After linking, build.py runs audit_pe.py against pe-allowlist.json and
fails the build on any finding.

The bridge schema: tools/bridge_schema.cpp is compiled with the same g++ and prints the wire format expanded
from include/ilc/bridge_abi.h. The result must equal schema/bridge-abi-v1.json byte for byte (LF), or the
build fails (run with --update-schema after an intended ABI change). The JSON's SHA-256 goes into the DLL and
comes back in HELLO.

  python build.py                  build into native/coop/build/
  python build.py --out DIR        build into DIR
  python build.py --repro          build twice in fresh temporary folders and require identical bytes
  python build.py --update-schema  rewrite schema/bridge-abi-v1.json from the header
  python build.py --update-golden  record the DLL hash in golden.json (the golden test compares against it)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import audit_pe  # noqa: E402

sys.path.insert(0, str(HERE / 'third_party' / 'sdl2'))
import build_sdl2  # noqa: E402

sys.path.insert(0, str(HERE / 'third_party' / 'minhook'))
import build_minhook  # noqa: E402

DLL_NAME = 'ivalice-legacy-coop.dll'
MAP_NAME = 'ivalice-legacy-coop.map'
SCHEMA = HERE / 'schema' / 'bridge-abi-v1.json'
GOLDEN = HERE / 'golden.json'
EXPECTED_COMPILER = 'g++.exe (Rev3, Built by MSYS2 project) 16.2.0'
EXPECTED_LINKER = 'GNU ld (GNU Binutils) 2.47.20260726'
EXPECTED_PACKAGES = {
    'mingw-w64-ucrt-x86_64-gcc': '16.2.0-3',
    'mingw-w64-ucrt-x86_64-binutils': '2.47-3',
    'mingw-w64-ucrt-x86_64-crt': '14.0.0.r353.g6df76fa52-4',
    'mingw-w64-ucrt-x86_64-headers': '14.0.0.r353.g6df76fa52-2',
}
SOURCES = ['src/boot/dllmain.cpp', 'src/boot/efl_exports.cpp', 'src/bridge/bridge.cpp',
           'src/bridge/validate.cpp', 'src/runtime/probe.cpp',
           'src/input/input_core.cpp', 'src/input/input_live.cpp', 'src/input/input_service.cpp',
           'src/input/sha256.cpp']
# Action layer: bindings, edges, repeat, absorption; seat commands (pairing, capture); keyboard reader.
SOURCES += ['src/input/actions.cpp', 'src/input/seat_control.cpp', 'src/input/keyboard_live.cpp']
# Patch layer: MinHook 1.3.4, static, built by third_party/minhook/build_minhook.py (pinned in its lock).
SOURCES += ['src/patch/patch_engine.cpp', 'src/patch/patch_stubs.cpp']
# Call-type sites: rel32 rewrite to a jump-only island, stubs with static unwind data (no MinHook).
SOURCES += ['src/patch/call_island.cpp', 'src/patch/call_stubs.cpp']
# Input isolation: guest pads and keyboard never drive the game; P1 on SDL fed through the finalize hooks.
SOURCES += ['src/input/pad_mirror.cpp', 'src/isolation/isolation.cpp']
# Co-op switches: kill switch (ordered disarm), seat drop-in/out toggles, P1 lock.
SOURCES += ['src/control/control.cpp']
# Game-thread queue and the UI-tick handler (UT-01): linked, not called until the boot wires them.
SOURCES += ['src/runtime/game_thread.cpp', 'src/runtime/tick.cpp']
# Boot: host probe, instance guard, Ivalice Together detection, the start-up switch, the init thread.
SOURCES += ['src/boot/boot.cpp', 'src/boot/together_detect.cpp', 'src/boot/settings_file.cpp']
# Session core: world reads through the fault-safe probe, epochs and gates, the seat table, ownership hand-off,
# the published session record. Per-consumer edge cursors at the input seam.
SOURCES += ['src/session/reader.cpp', 'src/session/world.cpp', 'src/session/policy.cpp', 'src/session/handoff.cpp',
            'src/session/session.cpp', 'src/input/edge_history.cpp']
# Guest movement: steering maths, the per-seat planner, the published steering record, the movement detours and
# the follow-the-leader game-thread operation.
SOURCES += ['src/movement/steering.cpp', 'src/movement/planner.cpp', 'src/movement/movement.cpp',
            'src/movement/hooks.cpp']
# Town visibility (PARTY): only player-controlled characters keep a body in towns (TW-01 filter, rebuild job).
SOURCES += ['src/towns/filter.cpp', 'src/towns/towns.cpp', 'src/towns/hooks.cpp']
MINHOOK_DIR = Path(os.environ.get('ILC_MINHOOK_DIR', str(HERE / 'build' / 'minhook')))
MINHOOK_LOCK = 'third_party/minhook/minhook.lock.json'
# Input service: SDL 2.32.10, static, built by third_party/sdl2/build_sdl2.py into SDL2_DIR (pinned in its lock).
SDL2_DIR = Path(os.environ.get('ILC_SDL2_DIR', str(HERE / 'build' / 'sdl2')))
SDL2_LOCK = 'third_party/sdl2/sdl2.lock.json'
# What SDL's own CMake links on Windows; --gc-sections keeps only the imports SDL actually reaches.
SDL2_SYSTEM_LIBS = ['-lsetupapi', '-lole32', '-loleaut32', '-luuid', '-lversion', '-limm32', '-lwinmm', '-lgdi32',
                    '-luser32', '-ladvapi32', '-lshell32', '-ldinput8']
# Host-test build only (never shipped): the same sources plus testsupport/, with extra ILCT_* exports.
TEST_DLL_NAME = 'ilc-coop-test.dll'
TEST_SOURCES = ['testsupport/input_replay.cpp', 'testsupport/test_exports.cpp']
TEST_SOURCES += ['testsupport/patch_tests.cpp', 'testsupport/island_tests.cpp', 'testsupport/isolation_tests.cpp']
TEST_SOURCES += ['testsupport/control_tests.cpp']
TEST_SOURCES += ['testsupport/twin_drills.cpp']
TEST_SOURCES += ['testsupport/action_tests.cpp']
TEST_SOURCES += ['testsupport/boot_tests.cpp']
TEST_SOURCES += ['testsupport/session_tests.cpp']
TEST_SOURCES += ['testsupport/movement_tests.cpp']
TEST_SOURCES += ['testsupport/towns_tests.cpp']
SCHEMA_TOOL = 'tools/bridge_schema.cpp'
DEF_FILE = 'coop.def'
CXXFLAGS = ['-std=c++20', '-O2', '-Wall', '-Wextra', '-Werror', '-fno-exceptions', '-fno-rtti',
            '-fno-stack-protector', '-fno-ident', '-ffunction-sections', '-fdata-sections', '-U_FORTIFY_SOURCE',
            '-Iinclude', '-Isrc']
LDFLAGS = ['-shared', '-s', '-static-libgcc', '-static-libstdc++', '-Wl,--entry,DllMainCRTStartup',
           '-Wl,--subsystem,windows', '-Wl,--no-insert-timestamp', '-Wl,--image-base,0x180000000',
           '-Wl,--dynamicbase', '-Wl,--nxcompat', '-Wl,--high-entropy-va', '-Wl,--gc-sections',
           '-Wl,-Map,{out}/' + MAP_NAME]


def source_files() -> list[str]:
    """Every file that goes into the image, as sorted paths relative to native/coop."""
    files = {DEF_FILE, SDL2_LOCK, MINHOOK_LOCK, *SOURCES}
    for folder in ('include', 'src'):
        files.update(p.relative_to(HERE).as_posix() for p in (HERE / folder).rglob('*') if p.is_file())
    return sorted(files)


def source_id(files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in files:
        data = (HERE / rel).read_bytes().replace(b'\r\n', b'\n')
        digest.update(rel.encode() + b'\0' + len(data).to_bytes(8, 'little') + data)
    return digest.hexdigest()


def tool_version(executable: Path, *args: str) -> str:
    return subprocess.check_output([str(executable), *args], text=True).splitlines()[0].strip()


def installed_packages(compiler: Path) -> dict[str, str]:
    """pacman -Q for the pinned packages; the MSYS2 root is two levels above ucrt64/bin."""
    pacman = compiler.parents[2] / 'usr' / 'bin' / 'pacman.exe'
    if not pacman.is_file():
        raise RuntimeError(f'pacman not found at {pacman}; cannot verify the pinned packages')
    output = subprocess.run([str(pacman), '-Q', *EXPECTED_PACKAGES], capture_output=True, text=True)
    found = {}
    for line in output.stdout.splitlines():
        name, _, version = line.strip().partition(' ')
        found[name] = version
    return found


def check_toolchain(compiler: Path) -> dict:
    compiler_version = tool_version(compiler, '--version')
    if compiler_version != EXPECTED_COMPILER:
        raise RuntimeError(f'compiler is "{compiler_version}", expected "{EXPECTED_COMPILER}"')
    linker_version = tool_version(compiler.parent / 'ld.exe', '--version')
    if linker_version != EXPECTED_LINKER:
        raise RuntimeError(f'linker is "{linker_version}", expected "{EXPECTED_LINKER}"')
    packages = installed_packages(compiler)
    wrong = {name: packages.get(name) for name, version in EXPECTED_PACKAGES.items() if packages.get(name) != version}
    if wrong:
        raise RuntimeError(f'pacman packages differ from the pin {EXPECTED_PACKAGES}: found {wrong}')
    return {'compiler': compiler_version, 'linker': linker_version,
            'packages': {name: f'{name} {version}' for name, version in packages.items()}}


def tool_environment(compiler: Path) -> dict:
    environment = dict(os.environ)
    environment['PATH'] = str(compiler.parent) + os.pathsep + environment.get('PATH', '')
    return environment


def generate_schema(compiler: Path) -> str:
    """Compile and run tools/bridge_schema.cpp; return the canonical JSON text (LF, trailing newline)."""
    with tempfile.TemporaryDirectory(prefix='ilc-schema-') as temp:
        exe = Path(temp) / 'bridge_schema.exe'
        subprocess.run([str(compiler), '-std=c++20', '-O1', '-Wall', '-Wextra', '-Werror', '-Iinclude', '-Isrc',
                        SCHEMA_TOOL, '-static', '-o', str(exe)], check=True, cwd=HERE, env=tool_environment(compiler))
        result = subprocess.run([str(exe)], capture_output=True, text=True, env=tool_environment(compiler))
    if result.returncode != 0:
        raise RuntimeError(f'bridge_schema failed:\n{result.stderr}')
    return json.dumps(json.loads(result.stdout), indent=2, ensure_ascii=True) + '\n'


def schema_digest(text: str) -> str:
    return hashlib.sha256(text.replace('\r\n', '\n').encode('utf-8')).hexdigest()


def committed_schema() -> str | None:
    return SCHEMA.read_bytes().decode('utf-8').replace('\r\n', '\n') if SCHEMA.is_file() else None


def sdl2_inputs(compiler: Path, tarball: Path | None) -> tuple[dict, list[str], list[str]]:
    """The pinned SDL2 static library (built from the tarball when the cache does not match the lock).
    Returns its manifest, the include flags and the link inputs, with paths relative to native/coop if possible."""
    manifest = build_sdl2.ensure(compiler.parent / 'gcc.exe', SDL2_DIR, tarball)

    def rel(path: Path) -> str:
        try:
            return path.resolve().relative_to(HERE).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    include = ['-isystem', rel(SDL2_DIR / 'include')]
    link = [rel(SDL2_DIR / build_sdl2.LIB_NAME), *SDL2_SYSTEM_LIBS]
    return manifest, include, link


def minhook_inputs(compiler: Path, tarball: Path | None) -> tuple[dict, list[str], list[str]]:
    """The pinned MinHook static library (built from the tarball when the cache does not match the lock)."""
    manifest = build_minhook.ensure(compiler.parent / 'gcc.exe', MINHOOK_DIR, tarball)

    def rel(path: Path) -> str:
        try:
            return path.resolve().relative_to(HERE).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    return manifest, ['-isystem', rel(MINHOOK_DIR / 'include')], [rel(MINHOOK_DIR / build_minhook.LIB_NAME)]


def test_def_file(out: Path) -> Path:
    """coop.def plus every ILCT_* function of the testsupport sources."""
    names = [name for source in TEST_SOURCES if source.endswith('.cpp')
             for name in re.findall(r'^\w[\w\s\*]*?\b(ILCT_\w+)\(', (HERE / source).read_text(encoding='utf-8'),
                                    flags=re.M)]
    lines = (HERE / DEF_FILE).read_text(encoding='utf-8').replace(DLL_NAME, TEST_DLL_NAME).rstrip().splitlines()
    lines += [f'    {name}' for name in sorted(set(names))]
    path = out / 'ilc-coop-test.def'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def build_test_dll(compiler: Path, out: Path, sdl2_tarball: Path | None = None,
                   minhook_tarball: Path | None = None) -> Path:
    """The host-test DLL: shipped sources + testsupport/, exports from coop.def plus ILCT_*. Not audited for
    shipping and never packaged."""
    check_toolchain(compiler)
    _, sdl_include, sdl_link = sdl2_inputs(compiler, sdl2_tarball)
    _, mh_include, mh_link = minhook_inputs(compiler, minhook_tarball)
    sdl_include, sdl_link = sdl_include + mh_include, mh_link + sdl_link
    out.mkdir(parents=True, exist_ok=True)
    def_file = test_def_file(out)
    flags = [f for f in LDFLAGS if not f.startswith('-Wl,-Map')]
    command = [str(compiler), *CXXFLAGS, *sdl_include, '-Itestsupport', *SOURCES, *TEST_SOURCES, str(def_file),
               *flags, '-o', str(out / TEST_DLL_NAME), *sdl_link]
    subprocess.run(command, check=True, cwd=HERE, env=tool_environment(compiler))
    return out / TEST_DLL_NAME


def build(compiler: Path, out: Path, update_schema: bool = False, sdl2_tarball: Path | None = None,
          minhook_tarball: Path | None = None) -> dict:
    toolchain = check_toolchain(compiler)
    sdl_manifest, sdl_include, sdl_link = sdl2_inputs(compiler, sdl2_tarball)
    mh_manifest, mh_include, mh_link = minhook_inputs(compiler, minhook_tarball)
    sdl_include, sdl_link = sdl_include + mh_include, mh_link + sdl_link

    schema = generate_schema(compiler)
    if update_schema:
        SCHEMA.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA.write_bytes(schema.encode('utf-8'))
    elif committed_schema() != schema:
        raise RuntimeError(f'schema drift: {SCHEMA.relative_to(HERE).as_posix()} differs from the header. '
                           'Run build.py --update-schema after an intended ABI change.')
    schema_sha = schema_digest(schema)

    files = source_files()
    sid = source_id(files)
    # HELLO carries both ids as two little-endian u64 each; the hex is split so the bytes read back in order.
    defines = [f'-DILC_SOURCE_ID0=0x{sid[0:16]}ull', f'-DILC_SOURCE_ID1=0x{sid[16:32]}ull',
               f'-DILC_SCHEMA_ID0=0x{schema_sha[0:16]}ull', f'-DILC_SCHEMA_ID1=0x{schema_sha[16:32]}ull']
    out.mkdir(parents=True, exist_ok=True)
    dll = out / DLL_NAME
    # Relative inputs and cwd = native/coop keep machine paths out of the command and the image.
    arguments = [*CXXFLAGS, *sdl_include, *defines, *SOURCES, DEF_FILE, *LDFLAGS, '-o', '{out}/' + DLL_NAME,
                 *sdl_link]
    command = [str(compiler)] + [a.replace('{out}', str(out)) for a in arguments]
    subprocess.run(command, check=True, cwd=HERE, env=tool_environment(compiler))

    data = dll.read_bytes()
    allow = json.loads((HERE / 'pe-allowlist.json').read_text(encoding='utf-8'))
    result = audit_pe.audit(data, allow)
    golden = json.loads(GOLDEN.read_text(encoding='utf-8')) if GOLDEN.is_file() else {}
    manifest = {
        'module': DLL_NAME,
        'purpose': 'Co-op module: EFL identity exports, bridge ABI v1 (settings staging, status, events, device '
                   'inventory), the input service, the patch layer (MinHook) and the boot: FF12Hinit checks the host build, '
                   'the instance guard, Ivalice Together and the Co-op module switch, then an init thread starts the '
                   'input service and installs the hooks dry-armed (every detour runs stock; only the settings safe '
                   'point runs); the session core reads the world each beat and publishes epochs, gates and the seat '
                   'table',
        'toolchain': {**toolchain, 'msys2Environment': 'UCRT64',
                      'cRuntime': 'UCRT (api-ms-win-crt), mingw-w64 DllMainCRTStartup; libgcc and libstdc++ static',
                      'sdl2': {'version': sdl_manifest['sdlVersion'], 'library': build_sdl2.LIB_NAME,
                               'sha256': sdl_manifest['sha256'], 'tarballSha256': sdl_manifest['tarballSha256'],
                               'dynamicApi': 'off', 'linkage': 'static'},
                      'minhook': {'version': mh_manifest['minhookVersion'], 'tag': mh_manifest['tag'],
                                  'commit': mh_manifest['commit'], 'library': build_minhook.LIB_NAME,
                                  'sha256': mh_manifest['sha256'], 'tarballSha256': mh_manifest['tarballSha256'],
                                  'licence': 'BSD-2-Clause (+ HDE64 BSD)', 'linkage': 'static'}},
        'command': ['g++'] + arguments,
        'sourceId': sid,
        'sources': {rel: hashlib.sha256((HERE / rel).read_bytes()).hexdigest() for rel in files},
        'schema': {'path': SCHEMA.relative_to(HERE).as_posix(), 'sha256': schema_sha},
        'buildScripts': {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                         for name in ('build.py', 'audit_pe.py', 'pe-allowlist.json', SCHEMA_TOOL)},
        'dllSha256': hashlib.sha256(data).hexdigest(),
        'dllSize': len(data),
        'goldenMatch': golden.get('dllSha256') == hashlib.sha256(data).hexdigest(),
        'audit': {'ok': result['ok'], 'failures': result['failures'], 'exports': result['exports'],
                  'imports': result['imports'], 'tls': result['tls'],
                  'dllCharacteristics': result['dllCharacteristics']},
    }
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (out / 'audit.txt').write_text(audit_pe.format_report(dll, result) + '\n', encoding='utf-8')
    if not result['ok']:
        raise RuntimeError('PE audit failed:\n  ' + '\n  '.join(result['failures']))
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--compiler', type=Path,
                        default=Path(os.environ.get('IVALICE_LEGACY_CXX', 'C:/msys64/ucrt64/bin/g++.exe')))
    parser.add_argument('--out', type=Path, default=HERE / 'build')
    parser.add_argument('--repro', action='store_true',
                        help='build twice in fresh temporary folders and compare the DLL bytes')
    parser.add_argument('--update-schema', action='store_true', help='rewrite schema/bridge-abi-v1.json')
    parser.add_argument('--update-golden', action='store_true', help='record this build in golden.json')
    parser.add_argument('--sdl2-tarball', type=Path, default=None,
                        help='the pinned SDL2-2.32.10.tar.gz, needed once to build the static SDL2 library')
    parser.add_argument('--minhook-tarball', type=Path, default=None,
                        help='the pinned minhook-1.3.4.tar.gz, needed once to build the static MinHook library')
    parser.add_argument('--test-dll', action='store_true', help='also build the host-test DLL (never shipped)')
    args = parser.parse_args(argv)
    compiler = args.compiler.resolve()
    if args.repro:
        hashes = []
        for _ in range(2):
            with tempfile.TemporaryDirectory(prefix='ilc-repro-') as temp:
                hashes.append(build(compiler, Path(temp), sdl2_tarball=args.sdl2_tarball,
                                    minhook_tarball=args.minhook_tarball)['dllSha256'])
        same = hashes[0] == hashes[1]
        print(json.dumps({'reproducible': same, 'dllSha256': hashes}, indent=2))
        return 0 if same else 1
    manifest = build(compiler, args.out.resolve(), update_schema=args.update_schema, sdl2_tarball=args.sdl2_tarball,
                     minhook_tarball=args.minhook_tarball)
    if args.test_dll:
        print(f'test DLL: {build_test_dll(compiler, args.out.resolve(), args.sdl2_tarball, args.minhook_tarball)}')
    if args.update_golden:
        golden = {'dllSha256': manifest['dllSha256'], 'dllSize': manifest['dllSize'],
                  'sourceId': manifest['sourceId'], 'schemaSha256': manifest['schema']['sha256'],
                  'packages': manifest['toolchain']['packages']}
        GOLDEN.write_text(json.dumps(golden, indent=2) + '\n', encoding='utf-8')
        manifest['goldenMatch'] = True
    print(json.dumps({k: manifest[k] for k in ('module', 'dllSha256', 'dllSize', 'sourceId', 'goldenMatch')},
                     indent=2))
    print('PE audit: PASS')
    return 0


if __name__ == '__main__':
    sys.exit(main())
