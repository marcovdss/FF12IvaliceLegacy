# Ivalice Legacy

An overhaul for **FINAL FANTASY XII THE ZODIAC AGE** (PC, Steam), built for co-op play with
[Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) and just as playable solo.

The goal is an Ivalice where preparation wins: jobs that combine instead of overlapping, gear that changes how you
play, enemies that act instead of just hitting harder, rare monsters that rule their zones, a Clan worth climbing,
and progression you can't steamroll.

> **Status: early development (0.0.4).** Only the first modules exist so far. Expect large changes between
> versions; start a new game for each development build.

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

**Kings of the zone (first King).** Ripe Rampager, the rare monster of Dalmasca Estersand, now always appears in
Yardang Labyrinth when you enter, instead of a one-in-three chance. More Kings, each with its own fight and
reward, are planned.

## Planned

- Twelve jobs designed in pairs, so every job combination plays differently, with little overlap.
- Gear with real trade-offs and effects, a second accessory slot for deeper builds.
- Enemy behavior: roles in every group, reactions to what the party does, boss phases.
- Kings of the zone: each area's rare monster is always present, with its own fight and reward.
- A reworked Clan Centurio, hunts and rewards.
- Progression paced so preparation matters more than grinding.

## Requirements

- FINAL FANTASY XII THE ZODIAC AGE, Steam version 1.0.4.0, **English** game language.
- [FF12 External File Loader](https://www.nexusmods.com/finalfantasy12/mods/170).
- Optional: [Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) for local co-op. Ivalice Legacy
  does not change Ivalice Together's files or settings.
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

## Installing

- **Vortex:** install the built zip as a mod and deploy. Using a separate Vortex profile keeps other mod
  collections intact.
- **Manually:** copy the zip's `mods` folder into the game folder.

Save files are shared with your other playthroughs, so start Ivalice Legacy in an empty save slot.

To uninstall, disable or remove the mod in Vortex, or delete the copied files.

## Repository layout

| Path | Contents |
|---|---|
| `patches/` | Readable patch definitions (opening stock, ability chests, Kings): what changes, plus hashes of the expected source and result |
| `ivalice_legacy/` | Archive reader, file formats, patch appliers and the build command |
| `tests/` | Tests for the patch appliers and the full build |

## Credits

File formats build on the FFXII modding community's work, including The Insurgent's Workshop and Toolkit (Xeavin),
the FF12 External File Loader and VM script tools (ffgriever) and VBFTool (topher-au).

FINAL FANTASY XII is a trademark of Square Enix. This is an unofficial fan project; no game files are distributed
here.
