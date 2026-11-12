"""Build MinHook 1.3.4 as a static library for the co-op module, from the pinned official source tarball.

MinHook (BSD-2, Tsuda Kageyu; bundled Hacker Disassembler Engine 64, BSD, Vyacheslav Patkov) does the hook
mechanics of the patch layer: the near-target buffer, the trampoline (its own instruction decoder relocates the
displaced instructions), the 5-byte jump, thread suspension and instruction-pointer fix-up. Our layer
(src/patch/) owns everything around it: the pinned build, the byte checks, all-or-nothing install, rollback and
the arm flags.

The library is not committed. This recipe and the pins in minhook.lock.json are: the tarball's SHA-256 (checked
before anything is extracted), the tag's commit, the expected source tree digest, and the expected SHA-256 of
the finished libMinHook-ilc.a for the pinned toolchain. Any mismatch stops the build.

Where the tarball comes from: download it yourself from the URL in minhook.lock.json (GitHub's archive of the
release tag) and pass its path with --tarball, or set ILC_MINHOOK_TARBALL. The recipe never downloads anything and
never runs a script or makefile from the tarball; it only compiles the four C files below with the pinned gcc.

What is built: src/buffer.c, src/hook.c, src/trampoline.c and src/hde/hde64.c (x64 only), as the upstream MinGW
makefile does (its CFLAGS -masm=intel -Wall -Werror -std=c11), plus our determinism and section flags. The DLL
build, the resources and the x86 decoder are not built. No source file is modified.

  python build_minhook.py --tarball PATH [--out DIR]     build (default out: native/coop/build/minhook)
  python build_minhook.py --tarball PATH --update-lock   record the library hash and tree digest in the lock
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
COOP = HERE.parents[1]
LOCK = HERE / 'minhook.lock.json'
LIB_NAME = 'libMinHook-ilc.a'
TOP = 'minhook-1.3.4'

SOURCES = ['src/buffer.c', 'src/hook.c', 'src/trampoline.c', 'src/hde/hde64.c']
HEADERS = ['include/MinHook.h', 'src/buffer.h', 'src/trampoline.h', 'src/hde/hde64.h', 'src/hde/pstdint.h',
           'src/hde/table64.h']
CFLAGS = ['-std=c11', '-O2', '-masm=intel', '-Wall', '-Werror', '-fno-ident', '-ffunction-sections',
          '-fdata-sections', '-fno-stack-protector', '-U_FORTIFY_SOURCE', '-DNDEBUG']
# What the library may import (checked with nm on every build): the hook mechanics and nothing else.
EXPECTED_UNDEFINED = {
    'CloseHandle', 'CreateToolhelp32Snapshot', 'FlushInstructionCache', 'GetCurrentProcess',
    'GetCurrentProcessId', 'GetCurrentThreadId', 'GetModuleHandleW', 'GetProcAddress', 'GetSystemInfo',
    'GetThreadContext', 'HeapAlloc', 'HeapCreate', 'HeapDestroy', 'HeapFree', 'HeapReAlloc', 'OpenThread',
    'ResumeThread', 'SetThreadContext', 'Sleep', 'SuspendThread', 'Thread32First', 'Thread32Next',
    'VirtualAlloc', 'VirtualFree', 'VirtualProtect', 'VirtualQuery', 'GetLastError', 'memcpy',
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding='utf-8'))


def safe_extract(tarball: Path, dest: Path) -> Path:
    """Extract only the regular files of include/, src/ and LICENSE.txt; refuse escaping paths and links.
    Project files, makefiles and resources are never written to disk."""
    wanted = []
    with tarfile.open(tarball, 'r:gz') as archive:
        for member in archive.getmembers():
            if member.type == tarfile.XGLTYPE or member.name == 'pax_global_header':
                continue
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != TOP:
                raise RuntimeError(f'unexpected path in tarball: {member.name}')
            inside = len(name.parts) > 1 and name.parts[1] in ('include', 'src', 'LICENSE.txt')
            if not inside:
                continue
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f'refusing non-regular tar member: {member.name}')
            wanted.append(member)
        archive.extractall(dest, members=wanted, filter='data')
    return dest / TOP


def tree_digest(root: Path) -> str:
    """SHA-256 over (path, size, content) of every compiled source, every header and the licence."""
    digest = hashlib.sha256()
    for rel in sorted({*SOURCES, *HEADERS, 'LICENSE.txt'}):
        data = (root / rel).read_bytes()
        digest.update(rel.encode() + b'\0' + len(data).to_bytes(8, 'little') + data)
    return digest.hexdigest()


def environment(gcc: Path) -> dict:
    env = dict(os.environ)
    env['PATH'] = str(gcc.parent) + os.pathsep + env.get('PATH', '')
    return env


def object_name(rel: str) -> str:
    return 'mh__' + rel.removeprefix('src/').replace('/', '__').removesuffix('.c') + '.o'


def undefined_symbols(gcc: Path, lib: Path) -> set[str]:
    """External symbols the archive needs, without the __imp_ prefix (nm -u, minus what the archive defines itself)."""
    nm = gcc.parent / 'nm.exe'
    out = subprocess.run([str(nm), '-u', str(lib)], capture_output=True, text=True, check=True,
                         env=environment(gcc)).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == 'U':
            names.add(parts[1].removeprefix('__imp_'))
    defined = subprocess.run([str(nm), '--defined-only', str(lib)], capture_output=True, text=True, check=True,
                             env=environment(gcc)).stdout
    own = {line.split()[-1] for line in defined.splitlines() if len(line.split()) == 3}
    return names - own


def build(gcc: Path, tarball: Path, out: Path, update_lock: bool = False) -> dict:
    lock = load_lock()
    pinned = lock['source']['sha256']
    actual = sha256_file(tarball)
    if actual != pinned:
        raise RuntimeError(f'{tarball.name}: SHA-256 {actual}, pinned {pinned}')
    gcc_version = subprocess.check_output([str(gcc), '--version'], text=True).splitlines()[0].strip()
    if gcc_version != lock['toolchain']['gcc']:
        raise RuntimeError(f'gcc is "{gcc_version}", the lock pins "{lock["toolchain"]["gcc"]}"')
    ar = gcc.parent / 'ar.exe'
    with tempfile.TemporaryDirectory(prefix='ilc-minhook-') as temp:
        temp = Path(temp)
        root = safe_extract(tarball, temp / 'src')
        digest = tree_digest(root)
        if not update_lock and digest != lock['build']['treeSha256']:
            raise RuntimeError(f'source tree digest {digest} differs from the lock')
        objects = temp / 'obj'
        objects.mkdir()
        flags = [*CFLAGS, f'-ffile-prefix-map={root.as_posix()}={TOP}', '-Isrc', '-Iinclude']
        names = []
        for rel in SOURCES:
            name = object_name(rel)
            subprocess.run([str(gcc), *flags, '-c', rel, '-o', str(objects / name)], check=True, cwd=root,
                           env=environment(gcc))
            names.append(name)
        out.mkdir(parents=True, exist_ok=True)
        lib = out / LIB_NAME
        if lib.exists():
            lib.unlink()
        # D: deterministic archive (zero timestamps, uids and modes).
        subprocess.run([str(ar), 'rcsD', str(lib), *names], check=True, cwd=objects, env=environment(gcc))
        include = out / 'include'
        if include.exists():
            shutil.rmtree(include)
        include.mkdir()
        shutil.copyfile(root / 'include' / 'MinHook.h', include / 'MinHook.h')
        licence = (root / 'LICENSE.txt').read_bytes()
    undefined = undefined_symbols(gcc, lib)
    if undefined != EXPECTED_UNDEFINED:
        raise RuntimeError(f'{LIB_NAME} needs {sorted(undefined - EXPECTED_UNDEFINED)} beyond the expected set, '
                           f'or no longer needs {sorted(EXPECTED_UNDEFINED - undefined)}')
    data = lib.read_bytes()
    lib_sha = hashlib.sha256(data).hexdigest()
    manifest = {
        'library': LIB_NAME, 'sha256': lib_sha, 'size': len(data), 'minhookVersion': lock['source']['version'],
        'tag': lock['source']['tag'], 'commit': lock['source']['commit'], 'tarballSha256': actual,
        'treeSha256': digest, 'licenceSha256': hashlib.sha256(licence).hexdigest(), 'objects': names,
        'gcc': gcc_version, 'cflags': CFLAGS, 'undefinedSymbols': sorted(undefined),
        'recipeSha256': {'build_minhook.py': sha256_file(HERE / 'build_minhook.py')},
        'headerSha256': sha256_file(out / 'include' / 'MinHook.h'),
    }
    if update_lock:
        lock['build']['treeSha256'] = digest
        lock['build']['libSha256'] = lib_sha
        lock['build']['licenceSha256'] = manifest['licenceSha256']
        lock['build']['recipeSha256'] = manifest['recipeSha256']
        lock['build']['headerSha256'] = manifest['headerSha256']
        LOCK.write_bytes((json.dumps(lock, indent=2) + '\n').encode('utf-8'))  # LF on every platform
    elif lib_sha != lock['build']['libSha256']:
        raise RuntimeError(f'{LIB_NAME} SHA-256 {lib_sha} differs from the lock {lock["build"]["libSha256"]}')
    elif manifest['headerSha256'] != lock['build'].get('headerSha256'):
        raise RuntimeError(f'MinHook.h SHA-256 {manifest["headerSha256"]} differs from the lock')
    (out / 'minhook-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def cached(out: Path) -> dict | None:
    """The manifest of an already-built library in `out` if it matches the lock and the recipe, else None."""
    lock = load_lock()
    manifest_path, lib = out / 'minhook-manifest.json', out / LIB_NAME
    if not (manifest_path.is_file() and lib.is_file() and (out / 'include' / 'MinHook.h').is_file()):
        return None
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    recipe = {'build_minhook.py': sha256_file(HERE / 'build_minhook.py')}
    # The header is hashed too: the build compiles against out/include/MinHook.h, so a changed copy is a rebuild.
    if (manifest.get('sha256') != lock['build']['libSha256'] or sha256_file(lib) != lock['build']['libSha256']
            or recipe != lock['build'].get('recipeSha256')
            or sha256_file(out / 'include' / 'MinHook.h') != lock['build'].get('headerSha256')):
        return None
    return manifest


def ensure(gcc: Path, out: Path, tarball: Path | None) -> dict:
    """Use the cached library when it matches the lock; otherwise build it from the tarball."""
    manifest = cached(out)
    if manifest is not None:
        return manifest
    if tarball is None:
        env = os.environ.get('ILC_MINHOOK_TARBALL')
        tarball = Path(env) if env else None
    if tarball is None or not tarball.is_file():
        raise RuntimeError('MinHook static library not built yet: pass the pinned minhook-1.3.4.tar.gz with '
                           '--minhook-tarball or set ILC_MINHOOK_TARBALL (see '
                           'native/coop/third_party/minhook/minhook.lock.json)')
    return build(gcc, tarball, out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gcc', type=Path, default=Path(os.environ.get('IVALICE_LEGACY_CC',
                                                                        'C:/msys64/ucrt64/bin/gcc.exe')))
    parser.add_argument('--tarball', type=Path, default=None)
    parser.add_argument('--out', type=Path, default=COOP / 'build' / 'minhook')
    parser.add_argument('--update-lock', action='store_true')
    args = parser.parse_args(argv)
    tarball = args.tarball or (Path(os.environ['ILC_MINHOOK_TARBALL'])
                               if os.environ.get('ILC_MINHOOK_TARBALL') else None)
    if tarball is None:
        parser.error('--tarball (or ILC_MINHOOK_TARBALL) is required')
    manifest = build(args.gcc.resolve(), tarball.resolve(), args.out.resolve(), update_lock=args.update_lock)
    print(json.dumps({k: manifest[k] for k in ('library', 'sha256', 'size', 'treeSha256', 'undefinedSymbols')},
                     indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
