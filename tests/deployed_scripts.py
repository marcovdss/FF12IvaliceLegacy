"""Locate the script files a game folder actually runs, so a test can replay the installed code instead of the
repository prototype. IVALICE_LEGACY_GAME names the game folder; without it (or without an installed copy) the
dependent tests skip.

Line endings are normalised to LF: an installed file may carry CRLF where the repository file does not.
"""
from pathlib import Path
import os

SCRIPTS = 'x64/scripts'
IVALICE = SCRIPTS + '/IvaliceLegacy'
FORGE_CONFIG = SCRIPTS + '/config/TheInsurgentsForgeConfig/functions'


def game():
    value = os.environ.get('IVALICE_LEGACY_GAME')
    return Path(value) if value else None


def path(rel):
    root = game()
    return root / rel if root else None


def installed(rel):
    p = path(rel)
    return p is not None and p.is_file()


def read(rel):
    p = path(rel)
    return p.read_text(encoding='utf-8').replace('\r\n', '\n')


def module(name):
    return read(f'{IVALICE}/{name}.lua')


def forge(number):
    return read(f'{FORGE_CONFIG}/{number}.lua')


def have(*names):
    return all(installed(f'{IVALICE}/{n}.lua') for n in names)


SKIP_REASON = 'set IVALICE_LEGACY_GAME to a game folder with Ivalice Legacy installed'
