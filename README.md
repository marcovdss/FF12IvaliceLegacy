# Ivalice Legacy

An overhaul for **FINAL FANTASY XII THE ZODIAC AGE** (PC, Steam), built for co-op play with
[Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) and just as playable solo.

The goal is an Ivalice where preparation wins: jobs that combine instead of overlapping, gear that changes how you
play, enemies that act instead of just hitting harder, rare monsters that rule their zones, a Clan worth climbing,
and progression you can't steamroll.

> **Status: early development (0.0.6).** Only the first modules exist so far. Expect large changes between
> versions. Incremental updates are intended to continue your existing Legacy campaign. Save compatibility will
> be checked for each change; any migration or restart requirement will be stated before installation.

## What's in this version

**Opening stock.** Rabanastre's merchants sell a wider set of opening tools from the very start. Items they
already stocked later in the story move to the start instead of appearing twice. Prices are unchanged.

| Merchant | New from the start |
|---|---|
| Amal's Weaponry | Bowgun, Iron Hammer, Rod, Oak Staff, Mace, Hornito |
| Panamis's Protectives | Bangle, Orrachea Armlet |
| Migelo's Sundries | Phoenix Down, Antidote, Onion Bolts, Onion Bombs |
| Yugri's Magicks | Thunder, Blizzard, Blind, Silence |
| Batahn's Technicks | First Aid, Steal |

**Reliable ability chests.** Three chests holding abilities that are easy to miss now always appear: Cleanse
(Cerobi Steppe, The Terraced Bank), Addle (Henne Mines, Special Charter Shaft) and Wither (Pharos, Subterra -
Abyssal - North). Contents and one-time collection are unchanged.

**Kings of the zone.** Each area's rare monster becomes the ruler of its zone and is always there:

- **Ripe Rampager** always appears in Yardang Labyrinth (Dalmasca Estersand), instead of a one-in-three chance.
- **Rain Dancer** always appears in Warrior's Wash, Toam Hills and Giza's South Bank during Giza Plains' rainy
  season, and only then.

More Kings, each with its own fight and reward, are planned.

**Separate save files.** When the save redirect activates successfully, the game saves and loads its own files
(`LEGCY_000`, `LEGCY_001`, ...) in the usual save folder, so Legacy and your other playthroughs never share or
overwrite slots. Without the mod, the game shows your normal saves (`FFXII_000`, ...) as before. This needs the
[FF12 LUA Loader](https://www.nexusmods.com/finalfantasy12/mods/171). If the game version differs from the
supported one, the script leaves save names unchanged and says so in `x64/hook.log`. Before saving for the first
time, check that the log says `Ivalice Legacy: using separate save files (LEGCY_###).` (or `separate save files
already active`) and that the load menu shows your Legacy saves. If activation fails, ordinary `FFXII_###` saves
remain accessible; stop before saving to protect other playthroughs. Native save isolation is still awaiting
in-game validation in this development build.

## Planned

- Twelve jobs designed in pairs, so every job combination plays differently, with little overlap.
- Gear with real trade-offs and effects, a second accessory slot for deeper builds.
- Enemy behavior: roles in every group, reactions to what the party does, boss phases.
- Kings of the zone: each area's rare monster is always present, with its own fight and reward.
- A reworked Clan Centurio, hunts and rewards.
- Progression paced so preparation matters more than grinding.

## Requirements

- FINAL FANTASY XII THE ZODIAC AGE, Steam version 1.0.4.0, **English** game language.
- [FF12 External File Loader](https://www.nexusmods.com/finalfantasy12/mods/170) and
  [FF12 LUA Loader](https://www.nexusmods.com/finalfantasy12/mods/171).
- Optional: [Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) for local co-op. Ivalice Legacy
  does not change Ivalice Together's files or settings.
- Recommended: [The Insurgent's Companions](https://www.nexusmods.com/finalfantasy12/mods/217) for a party of four,
  which Ivalice Legacy is being balanced around, plus [Second Accessory](https://www.nexusmods.com/finalfantasy12/mods/436)
  and [Gambit Rework](https://www.nexusmods.com/finalfantasy12/mods/471).
- Do not combine with other content overhauls (Struggle for Freedom, The Planetary Age, Horizontal Progression
  Overhaul and similar). They replace the same game data.

## Building

This repository contains only the overhaul's own code and patch definitions. No game files are included: the mod
is built from your own installed game.

```
python -m ivalice_legacy build --game "C:\Program Files (x86)\Steam\steamapps\common\FINAL FANTASY XII THE ZODIAC AGE"
```

Python 3.10 or newer, no other packages. The result is `dist/IvaliceLegacy-<version>.zip` plus a manifest of file
hashes. The build stops with an error if your game files differ from the supported version, for example if another
mod has replaced them inside the archive.

Run the tests with `python -m unittest discover -s tests` (set `IVALICE_LEGACY_GAME` to your game folder to include
the full build test).

## Optional Rampager pilot

Build the separate recovery-phase experiment with:

```
python -m ivalice_legacy build-rampager-pilot --game "<game folder>"
```

This produces `IvaliceLegacy-RampagerPilot-0.0.1.zip`: one enemy AI section, without a save redirect or other
modules. The normal build remains unchanged. This is an offline experimental addon awaiting in-game validation.

| Ripe Rampager condition | Original | Pilot |
|---|---|---|
| Enter existing recovery phase | HP < 50% | HP < 60% |
| Leave recovery (two rules) | HP >= 60% | HP >= 70% |

All other behavior, stats, rewards and spawns are preserved. Earlier recovery may lengthen the fight;
its effect on difficulty remains untested. The addon replaces Estersand's complete enemy AI section and must not
be combined with another mod editing that section. It does not include the Kings spawn changes.

## Installing

- **Vortex:** install the built zip as a mod and deploy. Using a separate Vortex profile keeps other mod
  collections intact.
- **Manually:** copy both the zip's `mods` and `x64` folders into the game folder, merging them with the existing
  folders. The save-isolation script must be at `x64/scripts/IvaliceLegacySaves.lua`.

Ivalice Legacy keeps its own save files (see above). To continue a Legacy game started before 0.0.6, copy its
`FFXII_###` file (and its `FFXII_###.dir` folder, if present) to `LEGCY_###` in the same save folder.

To uninstall, disable or remove the mod in Vortex, or delete only the files copied from this package, including
`x64/scripts/IvaliceLegacySaves.lua`. Preserve the existing `mods` and `x64` folders and their other contents.

## Repository layout

| Path | Contents |
|---|---|
| `patches/` | Readable patch definitions (opening stock, ability chests, Kings): what changes, plus hashes of the expected source and result |
| `ivalice_legacy/` | Archive reader, file formats, patch appliers and the build command |
| `package/` | Files shipped as they are, such as the separate-saves script |
| `tests/` | Tests for the patch appliers and the full build |
| `native/hybrid_boards/` | Experimental C++ hybrid-board core and build tools, excluded from installable packages |

## Credits

File formats build on the FFXII modding community's work, including The Insurgent's Workshop and Toolkit (Xeavin),
the FF12 External File Loader and VM script tools (ffgriever) and VBFTool (topher-au).

FINAL FANTASY XII is a trademark of Square Enix. This is an unofficial fan project; no game files are distributed
here.
