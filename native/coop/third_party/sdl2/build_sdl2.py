"""Build SDL 2.32.10 as a static library for the co-op module, from the pinned official release tarball.

The library is not committed. This recipe, the configuration in SDL_config.h and the pins in sdl2.lock.json
are: the tarball's SHA-256 (checked before anything is extracted), the expected source tree digest, and the
expected SHA-256 of the finished libSDL2-ilc.a for the pinned toolchain. Any mismatch stops the build.

Where the tarball comes from: download it yourself from the URL in sdl2.lock.json (signature, signing key and
checksum are listed there) and pass its path with --tarball, or set ILC_SDL2_TARBALL. The recipe never downloads
anything and never runs a script from the tarball; it only compiles the C sources listed below with the pinned
gcc.

What is built (the equivalent of SDL's CMake with SDL_AUDIO, SDL_VIDEO, SDL_RENDER, SDL_HAPTIC, SDL_SENSOR,
SDL_POWER, SDL_FILESYSTEM, SDL_LOCALE and SDL_MISC off): the generic core, events, joystick + game controller with
the DirectInput, XInput and HIDAPI drivers, HIDAPI, Windows threads/timers/loadso, and SDL's dummy drivers for
the switched-off subsystems. SDL_DYNAMIC_API is off (see SDL_config.h); the build proves it twice: a probe
compile that fails if SDL_dynapi.h still selects the dynamic API, and a scan of the library for the strings
"SDL_DYNAMIC_API" and "SDL_DYNAPI_entry".

  python build_sdl2.py --tarball PATH [--out DIR]     build (default out: native/coop/build/sdl2)
  python build_sdl2.py --tarball PATH --update-lock   record the library hash and tree digest in the lock
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
LOCK = HERE / 'sdl2.lock.json'
CONFIG = HERE / 'SDL_config.h'
LIB_NAME = 'libSDL2-ilc.a'
TOP = 'SDL2-2.32.10'

# Source groups, relative to the SDL tree. Globs are expanded and sorted, so the object order is stable.
SOURCE_GLOBS = [
    # SDL's CMake core list (CMakeLists.txt, "file(GLOB SOURCE_FILES ..."), unchanged.
    'src/*.c', 'src/atomic/*.c', 'src/audio/*.c', 'src/cpuinfo/*.c', 'src/dynapi/*.c', 'src/events/*.c',
    'src/file/*.c', 'src/joystick/*.c', 'src/haptic/*.c', 'src/hidapi/*.c', 'src/libm/*.c', 'src/locale/*.c',
    'src/misc/*.c', 'src/power/*.c', 'src/render/*.c', 'src/render/*/*.c', 'src/sensor/*.c', 'src/stdlib/*.c',
    'src/thread/*.c', 'src/timer/*.c', 'src/video/*.c', 'src/video/yuv2rgb/*.c',
    # Windows platform parts that stay on.
    'src/core/windows/*.c', 'src/timer/windows/*.c', 'src/loadso/windows/*.c', 'src/joystick/windows/*.c',
    'src/joystick/hidapi/*.c',
    # Dummy drivers for the subsystems that are off (as CMake adds them).
    'src/audio/dummy/*.c', 'src/video/dummy/*.c', 'src/haptic/dummy/*.c', 'src/sensor/dummy/*.c',
    'src/filesystem/dummy/*.c', 'src/locale/dummy/*.c', 'src/misc/dummy/*.c',
]
SOURCE_FILES = [
    # CMake's Windows thread list.
    'src/thread/generic/SDL_syscond.c', 'src/thread/windows/SDL_syscond_cv.c', 'src/thread/windows/SDL_sysmutex.c',
    'src/thread/windows/SDL_syssem.c', 'src/thread/windows/SDL_systhread.c', 'src/thread/windows/SDL_systls.c',
]
CFLAGS = ['-std=gnu11', '-O2', '-w', '-fno-strict-aliasing', '-fno-ident', '-ffunction-sections', '-fdata-sections',
          '-fno-stack-protector', '-U_FORTIFY_SOURCE', '-DNDEBUG', '-DDYNAPI_NEEDS_DLOPEN=1',
          '-DUSING_GENERATED_CONFIG_H']
DYNAPI_PROBE = (b'#include "SDL_internal.h"\n'
                b'#if SDL_DYNAMIC_API\n#error SDL_DYNAMIC_API is still on\n#endif\n'
                b'int ilc_sdl_dynapi_off;\n')
FORBIDDEN_STRINGS = [b'SDL_DYNAMIC_API', b'SDL_DYNAPI_entry']


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def load_lock() -> dict:
    return json.loads(LOCK.read_text(encoding='utf-8'))


def safe_extract(tarball: Path, dest: Path) -> Path:
    """Extract the regular files and directories of include/, src/ and the licence; refuse escaping paths.
    Everything else (project files, test programs, the Android symlinks) is never written to disk."""
    wanted = []
    with tarfile.open(tarball, 'r:gz') as archive:
        for member in archive.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or not name.parts or name.parts[0] != TOP:
                raise RuntimeError(f'unexpected path in tarball: {member.name}')
            inside = len(name.parts) > 1 and (name.parts[1] in ('include', 'src') or name.parts[1] == 'LICENSE.txt')
            if not inside:
                continue
            if not (member.isfile() or member.isdir()):
                raise RuntimeError(f'refusing non-regular tar member: {member.name}')
            wanted.append(member)
        archive.extractall(dest, members=wanted, filter='data')
    return dest / TOP


def tree_digest(root: Path, files: list[str]) -> str:
    """SHA-256 over (path, size, content) of every compiled source and every header the build can include."""
    digest = hashlib.sha256()
    headers = sorted(p.relative_to(root).as_posix() for p in (root / 'include').glob('*.h'))
    headers += sorted(p.relative_to(root).as_posix() for p in (root / 'src').rglob('*.h'))
    for rel in sorted(set(files) | set(headers)):
        data = (root / rel).read_bytes()
        digest.update(rel.encode() + b'\0' + len(data).to_bytes(8, 'little') + data)
    return digest.hexdigest()


def source_list(root: Path) -> list[str]:
    found = set(SOURCE_FILES)
    for pattern in SOURCE_GLOBS:
        found.update(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
    missing = [rel for rel in found if not (root / rel).is_file()]
    if missing:
        raise RuntimeError(f'sources missing from the tree: {missing}')
    return sorted(found)


def stage_headers(root: Path, include: Path) -> None:
    """SDL's public headers, unchanged, plus our SDL_config.h in place of SDL's (as SDL's CMake build does)."""
    include.mkdir(parents=True)
    for header in sorted((root / 'include').glob('*.h')):
        if header.name != 'SDL_config.h':
            shutil.copyfile(header, include / header.name)
    shutil.copyfile(CONFIG, include / 'SDL_config.h')


def object_name(rel: str) -> str:
    return rel.removeprefix('src/').replace('/', '__').removesuffix('.c') + '.o'


def environment(gcc: Path) -> dict:
    env = dict(os.environ)
    env['PATH'] = str(gcc.parent) + os.pathsep + env.get('PATH', '')
    return env


def compile_tree(gcc: Path, root: Path, include: Path, objects: Path, files: list[str]) -> list[str]:
    flags = [*CFLAGS, f'-ffile-prefix-map={root.as_posix()}={TOP}', f'-I{include.as_posix()}',
             f'-I{(root / "src").as_posix()}']
    env = environment(gcc)
    probe = objects / 'dynapi_probe.c'
    probe.write_bytes(DYNAPI_PROBE)
    subprocess.run([str(gcc), *flags, '-c', str(probe), '-o', str(objects / 'dynapi_probe.o')],
                   check=True, cwd=root, env=env)
    names = []
    for rel in files:
        name = object_name(rel)
        subprocess.run([str(gcc), *flags, '-c', rel, '-o', str(objects / name)], check=True, cwd=root, env=env)
        names.append(name)
    if len(set(names)) != len(names):
        raise RuntimeError('object name collision')
    return names


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
    with tempfile.TemporaryDirectory(prefix='ilc-sdl2-') as temp:
        temp = Path(temp)
        root = safe_extract(tarball, temp / 'src')
        files = source_list(root)
        digest = tree_digest(root, files)
        if not update_lock and digest != lock['build']['treeSha256']:
            raise RuntimeError(f'source tree digest {digest} differs from the lock')
        include = temp / 'include'
        stage_headers(root, include)
        objects = temp / 'obj'
        objects.mkdir()
        names = compile_tree(gcc, root, include, objects, files)
        out.mkdir(parents=True, exist_ok=True)
        lib = out / LIB_NAME
        if lib.exists():
            lib.unlink()
        # D: deterministic archive (zero timestamps, uids and modes).
        subprocess.run([str(ar), 'rcsD', str(lib), *names], check=True, cwd=objects, env=environment(gcc))
        public = out / 'include'
        if public.exists():
            shutil.rmtree(public)
        shutil.copytree(include, public)
    data = lib.read_bytes()
    found = [s.decode() for s in FORBIDDEN_STRINGS if s in data]
    if found:
        raise RuntimeError(f'{LIB_NAME} contains {found}: the dynamic API is not off')
    lib_sha = hashlib.sha256(data).hexdigest()
    manifest = {
        'library': LIB_NAME, 'sha256': lib_sha, 'size': len(data), 'sdlVersion': lock['source']['version'],
        'tarballSha256': actual, 'treeSha256': digest, 'objects': len(names), 'gcc': gcc_version,
        'cflags': CFLAGS, 'recipeSha256': {name: sha256_file(HERE / name)
                                           for name in ('build_sdl2.py', 'SDL_config.h')},
        'dynamicApi': 'off (probe compile passed; no SDL_DYNAMIC_API / SDL_DYNAPI_entry strings)',
    }
    if update_lock:
        lock['build']['treeSha256'] = digest
        lock['build']['libSha256'] = lib_sha
        lock['build']['recipeSha256'] = manifest['recipeSha256']
        LOCK.write_bytes((json.dumps(lock, indent=2) + '\n').encode('utf-8'))  # LF on every platform
    elif lib_sha != lock['build']['libSha256']:
        raise RuntimeError(f'{LIB_NAME} SHA-256 {lib_sha} differs from the lock {lock["build"]["libSha256"]}')
    (out / 'sdl2-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def cached(out: Path) -> dict | None:
    """The manifest of an already-built library in `out` if it matches the lock and the recipe, else None."""
    lock = load_lock()
    manifest_path, lib = out / 'sdl2-manifest.json', out / LIB_NAME
    if not (manifest_path.is_file() and lib.is_file() and (out / 'include' / 'SDL.h').is_file()):
        return None
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    recipe = {name: sha256_file(HERE / name) for name in ('build_sdl2.py', 'SDL_config.h')}
    if (manifest.get('sha256') != lock['build']['libSha256'] or sha256_file(lib) != lock['build']['libSha256']
            or recipe != lock['build'].get('recipeSha256')):
        return None
    return manifest


def ensure(gcc: Path, out: Path, tarball: Path | None) -> dict:
    """Use the cached library when it matches the lock; otherwise build it from the tarball."""
    manifest = cached(out)
    if manifest is not None:
        return manifest
    if tarball is None:
        env = os.environ.get('ILC_SDL2_TARBALL')
        tarball = Path(env) if env else None
    if tarball is None or not tarball.is_file():
        raise RuntimeError('SDL2 static library not built yet: pass the pinned SDL2-2.32.10.tar.gz with --sdl2-tarball '
                           'or set ILC_SDL2_TARBALL (see native/coop/third_party/sdl2/sdl2.lock.json)')
    return build(gcc, tarball, out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--gcc', type=Path, default=Path(os.environ.get('IVALICE_LEGACY_CC',
                                                                        'C:/msys64/ucrt64/bin/gcc.exe')))
    parser.add_argument('--tarball', type=Path, default=None)
    parser.add_argument('--out', type=Path, default=COOP / 'build' / 'sdl2')
    parser.add_argument('--update-lock', action='store_true')
    args = parser.parse_args(argv)
    tarball = args.tarball or (Path(os.environ['ILC_SDL2_TARBALL']) if os.environ.get('ILC_SDL2_TARBALL') else None)
    if tarball is None:
        parser.error('--tarball (or ILC_SDL2_TARBALL) is required')
    manifest = build(args.gcc.resolve(), tarball.resolve(), args.out.resolve(), update_lock=args.update_lock)
    print(json.dumps({k: manifest[k] for k in ('library', 'sha256', 'size', 'objects', 'treeSha256')}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
