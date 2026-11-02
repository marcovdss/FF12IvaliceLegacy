"""Dynamic Description equipment templates with a Proficiency row (weapons, shields, helms, armour, accessories).

The Proficiency runtime writes the selected character's Proficiency as DD custom attributes (legacyProficiency,
legacyProficiencyI-III) on weapons, shields, helms and armour only; DD shows custom attributes only where a template
asks for them. Templates are chosen by the item's icon: 0-17 weapons, 18 shields, 19-32 helms, armour and accessories
(one shared template; accessories never get the attributes, so nothing shows on them), 33-36 ammunition (untouched).

The templates are read from the user's own installed Dynamic Description configuration and checked against the
known versions; only one row is added before the notes row. No Dynamic Description file is part of this repository.
"""
import hashlib

TEMPLATES = 'x64/scripts/config/DynamicDescription/templates/equipment/{}.lua'
ICONS = range(0, 33)
KNOWN = {  # sha256 of the installed template -> icons using it
    '9c28086fd95063d7': range(0, 18), '42d03f444e1edffb': range(18, 19), 'a6d68789ef64e82c': range(19, 33)}
NOTES = '{dt:row itemspace:(1) groupspace:(1)}\r\n  {dt:equipment:common content:(notes)}\r\n{/row}'
ROW = ('{dt:row itemspace:(1) groupspace:(1) maxitems:(1)}\r\n'
       '  {dt:equipment:customAttributes content:(legacyProficiency, legacyProficiencyI, legacyProficiencyII, '
       'legacyProficiencyIII) args:(showLabel)}\r\n'
       '{/row}\r\n\r\n')


def require(value, message):
    if not value:
        raise ValueError(message)


def add_row(raw, icon, rebuild=False):
    if rebuild and raw.count(ROW.encode()) == 1:  # our own installed template: rebuild from the original under it
        raw = raw.replace(ROW.encode(), b'')
    digest = hashlib.sha256(raw).hexdigest()[:16]
    require(digest in KNOWN and icon in KNOWN[digest], f'Unknown Dynamic Description template for icon {icon}')
    text = raw.decode('utf-8')
    require(text.count(NOTES) == 1 and 'legacyProficiency' not in text, f'Unexpected template layout for icon {icon}')
    return text.replace(NOTES, ROW + NOTES).encode('utf-8')


def build_files(game):
    """{deploy path: bytes} for the 33 templates, or {} when Dynamic Description is not installed."""
    from pathlib import Path
    files = {}
    for icon in ICONS:
        path = Path(game) / TEMPLATES.format(icon)
        if not path.is_file():
            require(not files and icon == 0, 'Dynamic Description templates are incomplete')
            return {}
        files[TEMPLATES.format(icon)] = add_row(path.read_bytes(), icon, rebuild=True)
    return files
