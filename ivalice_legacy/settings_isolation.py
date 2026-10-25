"""Read personal settings for inclusion in the MAIN Legacy package.

The mod manager selects the provider; mod scripts keep their canonical paths.
No game, source configuration or manager state is written here.
"""
from pathlib import Path
import hashlib
import json


CONFIG_PATHS = (
    'x64/scripts/config/TheInsurgentsBountifulBundleConfig.json',
    'x64/scripts/config/GlobalGameplaySettingsConfig.lua',
    'x64/scripts/config/TheInsurgentsVisionConfig.json',
    'x64/scripts/config/Orderkeeper/modMenu.json',
)


def read_settings(root, protected_output=None):
    """Capture all four current files byte-for-byte, or fail without output.

    Root may be the game directory or a prepared directory with the same x64
    structure. Following deployed symlinks is intentional. Prefer the existing
    Legacy-owned files on later upgrades so personal changes are carried over.
    """
    root = Path(root)
    output = Path(protected_output).resolve() if protected_output is not None else None
    files = {}
    for relative in CONFIG_PATHS:
        source = root / relative
        resolved = source.resolve(strict=True)
        if output is not None and resolved.is_relative_to(output):
            raise ValueError('Settings source would be overwritten by the output package')
        if not source.is_file():
            raise ValueError(f'Expected a settings file: {relative}')
        raw = source.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
            if not text.strip():
                raise ValueError('Empty settings file')
            if relative.endswith('.json') and type(json.loads(text)) is not dict:
                raise ValueError('Expected a JSON settings object')
        except (UnicodeError, ValueError) as error:
            raise ValueError(f'Invalid settings file {relative}: {error}') from error
        files[relative] = raw
    return files


def add_settings(payload, settings):
    """Return a new payload, rejecting missing, extra or colliding config paths."""
    if set(settings) != set(CONFIG_PATHS):
        raise ValueError('Expected exactly the four personal settings files')
    if set(payload) & set(settings):
        raise ValueError('Settings paths collide with another package payload')
    if not all(type(raw) is bytes and raw for raw in settings.values()):
        raise ValueError('Expected nonempty settings bytes')
    return {**payload, **settings}


def metadata(settings):
    return {'owner': 'main Legacy package',
            'switching': 'Enable/disable MAIN, redeploy through the mod manager, then restart the game',
            'initialFileSha256': {p: hashlib.sha256(raw).hexdigest()
                                  for p, raw in sorted(settings.items())}}
