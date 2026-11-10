"""Equipment description templates for Dynamic Description: status and element groups join the stats.

Every equipment template (DD keys equipment_0..36, the item's icon) drops its statusEffect and elementalAffinity rows
and lists Legacy's custom attribute `legacyExtras` at the end of the stats row; scripts/IvaliceLegacy/gear_layout.lua
fills it at startup with the same groups, packed by width.
"""
import re

TEMPLATE_ICONS = range(0, 37)
TEMPLATE_DIR = 'x64/scripts/config/DynamicDescription/templates/equipment/'

CUSTOM = '  {dt:equipment:customAttributes content:(legacyExtras) args:(showLabel)}\n'
STATS_ROW = re.compile(r'(\{dt:row [^}]*maxitems:\(4\)\}\n  \{dt:equipment:attributes [^\n]*\n)(\{/row\}\n)')
STATUS_ROW = ('{dt:row itemspace:(1) groupspace:(1) maxitems:(10)}\n'
              '  {dt:equipment:statusEffect content:(hit, equip, immune) args:(showLabel, showIcon)}\n{/row}\n\n')
ELEMENT_ROW = ('{dt:row itemspace:(1) groupspace:(1) maxitems:(10)}\n'
               '  {dt:equipment:elementalAffinity content:(weak, immune, half, absorb, potency) args:(showLabel, showIcon)}\n'
               '{/row}\n\n')


WEAPON_ICONS = range(0, 18)
NOTES_ROW = '{dt:row itemspace:(1) groupspace:(1)}\n  {dt:equipment:common content:(notes)}\n{/row}\n\n'
NOTES = '  {dt:equipment:common content:(notes)}\n'
NOTES_LIMIT = 40  # a weapon note shares a stats line, so it must stay short (gear_layout measures it)


def equipment_template(text, weapon=False):
    """Return the Legacy template for DD's stock equipment template `text` (each change must apply once).
    Weapons (icons 0-17) also move the note into the stats row, before the extras. The template's line endings
    (DD ships CRLF) are kept."""
    crlf = '\r\n' in text
    text = text.replace('\r\n', '\n')
    if 'legacyExtras' in text:
        raise ValueError('template already has the Legacy custom attribute')
    for row in (STATUS_ROW, ELEMENT_ROW) + ((NOTES_ROW,) if weapon else ()):
        if text.count(row) != 1:
            raise ValueError(f'expected exactly one {row.split(chr(10))[1].strip()[:40]!r} row')
        text = text.replace(row, '')
    insert = (NOTES if weapon else '') + CUSTOM
    text, n = STATS_ROW.subn(lambda m: m.group(1) + insert + m.group(2), text)
    if n != 1:
        raise ValueError('expected exactly one stats row (maxitems 4, one attributes block)')
    if 'statusEffect' in text or 'elementalAffinity' in text:
        raise ValueError('template has other status or element rows')
    return text.replace('\n', '\r\n') if crlf else text
