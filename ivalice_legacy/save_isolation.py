"""Preflight the exact native build and early-init loader used for save isolation."""
from pathlib import Path
import hashlib

EXE_SHA256 = '73461d0896204b1c259c52f61aef4efbc95a2ffc388cadb36493d5d1d89721f8'
LOADER_SHA256 = '3ac6685307e52c432b732e5803667bc0c67b471b3081f7d8a80888177a430a9e'
SCRIPT_PATH = 'x64/scripts/init/IvaliceLegacySaves.lua'


def validate_runtime(game):
    """Read only. A mismatched build cannot produce the isolation-enabled package."""
    game = Path(game)
    targets = {'executable': (game/'x64/FFXII_TZA.exe', EXE_SHA256),
               'luaLoader': (game/'x64/modules/ff12-lua-loader.dll', LOADER_SHA256)}
    result = {}
    for name, (path, expected) in targets.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'Unsupported {name} for early save isolation: {path}')
        result[name+'Sha256'] = actual
    result.update(luaLoaderVersion='2.0.1', earlyInitScript=SCRIPT_PATH,
                  savesModified=False)
    return result
