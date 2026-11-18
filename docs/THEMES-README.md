# HUD themes

The co-op HUD (the command windows, the party list, Target Info and the battle log) can be drawn in
different themes. A theme changes how the HUD looks: the letters, the colours, the window shape, the glove, the marks,
the gauges and the status icons. It never changes what the HUD does or where things are.

## Choosing a theme

In game: **Mod Menu > Ivalice Legacy > Co-op > HUD > HUD theme**. The row shows the theme in use; press it to switch to
the next one. The new look is built in the background and swapped in between two frames, normally within a second of
the press [U: not yet confirmed in game]. If the chosen theme cannot be used, the HUD draws Bold Modern and the row
says so: a theme that was already broken when the folder was read is not in the list, so the row shows its folder
name followed by "(not found; using default)"; a theme that breaks after it was listed, or whose textures cannot be
created, shows its name followed by "(using default)". hook.log names the reason.

Ivalice Legacy ships three themes:

| Theme | Look |
|---|---|
| Bold Modern (the default) | heavy modern letters, windows with two cut corners, slanted gauges |
| Refined Vanilla | softer letters close to the original game, rounded windows |
| Crisp Vanilla | clean sans letters, crisp windows, square gauges with ticks |

Valid player themes follow them in the list.

## Adding your own theme

Player themes go in this folder of the game (next to the co-op settings file):

```
FINAL FANTASY XII THE ZODIAC AGE\x64\scripts\config\IvaliceLegacy\themes\<theme id>\
    theme.json
    atlas\theme.atlas
    icons\icons.json        (only if theme.json names an icon file)
```

- `<theme id>` is the folder name and must equal the `"id"` in `theme.json`: lower-case letters, digits, `-` and `_`,
  at most 32 characters (for example `my-theme`). The ids of the built-in themes (`legacy-bold`, `legacy-humanist`,
  `legacy-crisp`) cannot be used.
- `theme.atlas` is made by the theme converter (`theme_convert.py`) from your `theme.json` and your font files. The font
  files themselves are not read by the game and do not need to be in the folder.
- Nothing else in the folder is read. A theme is data only: it cannot run anything.

How to make one: see the theme converter's guide (`PLAYER-THEMES.md`) and the field list (`THEME-FORMAT.md`) that come
with the converter. In short: start from a built-in theme's `theme.json`, change the colours and sizes, point the fonts
at fonts you have the right to use, and run the converter. It checks everything and tells you in plain words what is
wrong.

**Fonts and licences.** The converted atlas contains the outlines of every character in it, so converting a font is the
same as embedding it. Only use fonts whose licence allows that, and only share a theme if its fonts allow sharing.

## What the game checks

The game reads a theme with strict rules, the same for the built-in themes:

- `theme.json`: at most 64 KB, valid JSON (no comments, no trailing commas, no repeated keys, at most 512 keys in one
  object), `"format": "ilc-theme"`, `"version": 1`, every required section, every value in its range. Text must reach
  7:1 contrast against the outline colour it is drawn with (`text.ink` when you give one, else `colors.ink`), the HP
  warning, HP critical and rare-drop colours 4.5:1. Help text is at least 17 units, rows 18 to 30, the row pitch at
  least 1.15 times the row size, exactly four seats numbered 1 to 4, 2 to 4 window gradient stops in order, gauge ticks
  (`gauge.ticks_every`) either 0 (none) or 0.02 to 0.5.
- `theme.atlas`: made by the converter (format version 2), at most 5 text faces, 4096 glyphs, 20000 kerning pairs and
  1 MB of atlas; every glyph inside the atlas; its digest is recomputed and must match. The body and title fonts must
  have every printable ASCII character.
- `icons.json` (optional): at most 256 KB, one name per icon in the atlas, no name twice, the required icons present.
  The icon file must be a `.json` file inside the theme's folder (a plain relative path such as `icons/icons.json`;
  Windows device names such as `nul` or `com1` are refused).

A theme that breaks a rule is not offered in the Mod Menu. If the theme you chose cannot be read when the game starts
(a file changed or went missing), the HUD draws Bold Modern instead. Either way the game writes **one line** to
`x64\hook.log` naming the theme and the reason, for example:

```
Ivalice Legacy Co-op: HUD theme "my-theme" refused: a value is out of range (sizes.row 31.0: 18.0..30.0); not offered in the Mod Menu
```

A theme is never applied halfway: it passes every rule or the built-in theme stays. Files are read on the co-op
thread, never on the drawing thread, and the new look is swapped in between two frames. Values that would make drawing
slow are refused (for example gauge ticks finer than 0.02, which once could hang the drawing thread) [U: the frame time
with each theme has not been measured in game].

## Optional extras

These `theme.json` sections are optional. A key you leave out takes the default shown here (whatever theme you
started from); a key the game does not know inside them is refused, with its name in `hook.log`:

- `"motion"`: the carousel for names too long for their column:
  `{"marquee_speed": 40, "marquee_hold_ms": 1000, "marquee_fade": 12, "marquee_on": true}`: the speed in units per
  second (10 to 120), the pause at each end in milliseconds (0 to 5000), the soft edge in units (0 to 40), and `false`
  to cut long names with "..." instead of moving them.
- `"text": {"tracking": {"page_face": "body", "title_em": 0, "header_em": 0.06}}`: the font window page titles use
  (`"body"` or `"title"`), the extra letter-spacing of window page titles (`title_em`) and of panel headers and labels
  such as Target Info, Battle Log, Drops and Weak/Half/Null (`header_em`), in ems, 0 to 0.3. Caps title fonts look
  better with 0.05 to 0.09.

## Limits

- The window size and every position on screen are fixed: a theme cannot move or resize anything.
- The chamfered window shape cuts the top-left and bottom-right corners only.
- Text outline softness (`text.soft`) is read but not used.
