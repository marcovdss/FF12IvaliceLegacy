# Ivalice Legacy

An overhaul for **FINAL FANTASY XII THE ZODIAC AGE** (PC, Steam), built for co-op play with
[Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) and just as playable solo.

The goal is an Ivalice where preparation wins: jobs that combine instead of overlapping, gear that changes how you
play, enemies that act instead of just hitting harder, rare monsters that rule their zones, a Clan worth climbing,
and progression you can't steamroll.

> **Status: early development.** Ivalice Legacy ships as several packages that are developed and tested together.
> Expect large changes between versions. Updates are intended to continue your existing Legacy campaign; any
> migration or restart requirement will be stated before installation.

| Package | Version | What it is | Tested in game |
|---|---|---|---|
| Ivalice Legacy (main) | 0.0.10 | Shops, story stock, ability chests, Kings, separate save files | Yes (earlier versions; 0.0.10 adds a test shop slot) |
| Base Jobs | 0.5.0 | Twelve job boards, legendary-weapon islands, job stat trainings | Boards up to 0.4.x; 0.5.0 not yet |
| Arsenal | 0.5.0 | Weapon attack curve and family identities, accessory identities | Earlier versions; 0.5.0 not yet |
| Proficiency | 0.2.0 | Clan-rank job lines handed out by Montblanc | Not yet |
| Attack Area Experiment | 0.0.2 | Greatswords sweep foes next to the target | Yes |

## Main package (0.0.10)

**Opening stock.** Rabanastre's merchants sell a wider set of opening tools from the very start. Items they
already stocked later in the story move to the start instead of appearing twice. Prices are unchanged.

| Merchant | New from the start |
|---|---|
| Amal's Weaponry | Bowgun, Iron Hammer, Rod, Oak Staff, Mace, Hornito, Claymore (test build, for the Attack area experiment) |
| Panamis's Protectives | Bangle, Orrachea Armlet |
| Migelo's Sundries | Phoenix Down, Antidote, Onion Bolts, Onion Bombs |
| Yugri's Magicks | Thunder, Blizzard, Blind, Silence |
| Batahn's Technicks | First Aid, Steal |

**Story stock.** As the story advances, shops add the gear, ammunition, magicks and Technicks of that stage, so
the shop shelf grows with the journey instead of all at once. Timing follows the item progression of established
overhauls, and items that replace a consumable come after it (Phoenix Down before Raise, Gold Needle before Stona).
Prices are unchanged.

**Reliable ability chests.** Cleanse (Cerobi Steppe), Addle (Henne Mines) and Wither (Pharos) always appear.

**Kings of the zone.** Ripe Rampager always appears in Yardang Labyrinth; Rain Dancer always appears in Giza
Plains' rainy season, and only then. More Kings, each with its own fight and reward, are planned.

**Separate save files.** An early-init script (`x64/scripts/init/IvaliceLegacySaves.lua`, FF12 LUA Loader 2.0.1)
makes the game list and write `LEGCY_000`, `LEGCY_001`, ... instead of `FFXII_###`. Normal saves are left alone.
Disabling the main package and restarting restores the normal namespace. The script never copies, renames or
converts saves. If activation fails it logs **FATAL** and restores its own changes: stop before loading or saving.

## Base Jobs (0.5.0)

Twelve standalone jobs, each with a compact board: Knight, Lancer, Warrior, Samurai, Hunter, Gunner, Chemist,
Green Mage, Time Mage, Mystic, White Mage and Black Mage.

- **Boards** are compact regions around Essentials: gear above, accessories to the left, passive augments to the
  right, magicks and Technicks below, cheaper licenses nearest the centre.
- **Legendary islands.** Every board carries the same thirteen islands: each Esper gates the zodiac weapon of its
  sign, and Zodiark gates Masamune. An island opens only for a character who owns that Esper (use
  [Summons For All](https://www.nexusmods.com/finalfantasy12/mods/381) so anyone can license any Esper).
- **Shared basic tools** where another role needs them (Decoy and Oil for Knight and Warrior, Libra for Gunner,
  Green Mage and Mystic, and so on). Schools and signature Technicks stay with their job.
- **Job stat trainings.** The deep, repeated Battle and Magick Lores become two licenses of each job's own
  (I at 50 LP, II at 100 LP) that raise the stats its weapons and role use. Totals stay close to the base game's;
  what changes is which stats grow. The board help states each training's effect. Requires
  [The Insurgent's Stat Lores](https://www.nexusmods.com/finalfantasy12/mods/299); the package's Stat Lores config
  must win over Stat Lores' own.

| Job | Training | I | II |
|---|---|---|---|
| Knight | Bulwark | Defense +3, shield evasion +5 | Defense +4, magick shield evasion +5 |
| Lancer | Dragoon | Strength +3, Magick Resist +3 | Strength +4, Magick Resist +4 |
| Warrior | Juggernaut | HP +435, Strength +2, Vitality +4 | HP +500, Strength +3, Vitality +6 |
| Samurai | Bushido | Strength +2, MP +40 | Strength +2, Magick +1, MP +60 |
| Hunter | Fleetfoot | Strength +2, Speed +2 | Strength +3, Speed +3 |
| Gunner | Deadeye | Speed +2, Attack +1 | Speed +2, Attack +1 |
| Chemist | Field Medic | Vitality +3, Magick Resist +3, Strength +2 | Vitality +4, Magick Resist +4, Strength +2 |
| Green Mage | Hexcraft | Magick +5, MP +30 | Magick +7, MP +50 |
| Time Mage | Chronomancy | Magick +2, Speed +2 | Magick +4, Speed +2 |
| Mystic | Mysticism | Strength +2, Magick +2 | Strength +2, Magick +2 |
| White Mage | Devotion | Magick +4, Magick Resist +3 | Magick +5, Magick Resist +4 |
| Black Mage | Sorcery | Magick +6 | Magick +8 |

Existing saved slot IDs and acquired licenses persist; there is no automatic respec. Hybrid jobs (66 combinations)
are being designed and are not part of this package.

## Arsenal (0.5.0)

- **A gentle damage climb.** The opening weapons keep their attack; every later weapon is lower than native, so a
  typical hit grows from about 47 at the start to about 830 by the last shops (native: about 1,375). Each weapon
  keeps its place in its stage of the story; rewards are set for where they're found; the legendary weapons still
  lead their families; story and guest weapons are unchanged.
- **Family identities** change how weapons play, not their damage over time: daggers fastest, katana longest
  chains, bows the highest critical rate, greatswords slow and heavy, swords the best evade, hammers knockback and
  Disable, hand-bombs Slow or Oil, ninja swords Blind, spears reach. Speed, combo and critical identities are offset
  in attack.
- **Accessory identities** borrowed from established overhauls where they add a new answer, with renamed items.
- **Test build:** the Claymore costs 800 gil so it can be bought early for the Attack area experiment.

See [package_arsenal/README.md](package_arsenal/README.md) for every weapon's numbers.

## Proficiency (0.2.0)

Each job's Clan line, earned from **Montblanc** and applied to whatever the character wields.

- **12 Clan ranks = 3 stages x 4 levels.** Ranks 1-4 raise stage I, 5-8 stage II, 9-12 stage III, so every new
  rank is a reward. Montblanc gives an Apprentice's, Journeyman's and Master's Writ at ranks 1, 5 and 9, and stamps
  it at every other rank.
- **It follows the job.** A character uses the line of the job they have now, at the levels the Clan has earned;
  changing jobs never loses progress.
- **Montblanc speaks.** When the party leaves the Clan hall with a rank he hasn't rewarded yet, he speaks in the
  game's own dialogue windows. Saves that were already ranked catch up once. The rewarded rank is stored beside
  each Legacy save.

| Job | Line | Stage I in short |
|---|---|---|
| Knight | Bulwark | A parry or block draws the attacker to the Knight |
| Lancer | Grounding | Hits strip Float, then Haste, Faith and Regen |
| Warrior | Cleave | A kill grants Bravery |
| Samurai | Zanshin | Hits in a row on one target grant Haste |
| Hunter | Pursuit | More damage to targets hindered by an ailment |
| Gunner | Suppression | Hits push back the target's next action |
| Chemist | Contagion | Statuses it lands spread to another foe |
| Green Mage | Mana Leech | Hits drain MP to her |
| Time Mage | Detonate | Hits can briefly Stop a Slowed target |
| Mystic | Ki Flow | Counterattacks heal the Mystic |
| White Mage | Overflow | Healing past full HP becomes Regen |
| Black Mage | Resonance | Casting the same element twice charges faster |

Stages II and III add new facets of the same idea. A few levels whose mechanism is not yet proven are inert for now
(listed in `prototypes/proficiency.lua`). Requires [The Insurgent's Forge](https://www.nexusmods.com/finalfantasy12/mods/386)
and FF12 LUA Loader 2.0.1 with its dialog plugin. The package is six Forge `config/functions` adapters (110, 165,
170, 182, 22, 240), a shared runtime, the rules and a Clan rank reader; each adapter runs Forge's original function
first.

## Attack Area Experiment (0.0.2)

The basic Attack gets an area (a half circle around the target). A Forge filter keeps the sweep for a party member's
greatsword only; every other Attack, foes' included, hits just the unit it was aimed at. Tested in game: the
Claymore hits the foes next to its target and other weapons don't. Disable the package to restore the native
single-target Attack. Ivalice Together's co-op menus still list Attack as single-target.

## Planned

- Hybrid jobs for the 66 job combinations, stronger than base jobs, with their own Proficiency and armour lines.
- Armour lines, gear with real trade-offs, and legendary weapons at full power for a Clan master of their job.
- Enemy behavior: roles in every group, reactions to what the party does, boss phases.
- More Kings of the zone, a reworked Clan Centurio, hunts and rewards.
- Progression paced so preparation matters more than grinding.

## Requirements

- FINAL FANTASY XII THE ZODIAC AGE, Steam version 1.0.4.0, **English** game language.
- [FF12 External File Loader](https://www.nexusmods.com/finalfantasy12/mods/170) and
  [FF12 LUA Loader](https://www.nexusmods.com/finalfantasy12/mods/171) (2.0.1).
- Base Jobs: [The Insurgent's Stat Lores](https://www.nexusmods.com/finalfantasy12/mods/299). Proficiency and the
  Attack Area Experiment: [The Insurgent's Forge](https://www.nexusmods.com/finalfantasy12/mods/386).
- Optional: [Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) for local co-op. Ivalice Legacy
  does not change Ivalice Together's files or settings.
- Recommended: [The Insurgent's Companions](https://www.nexusmods.com/finalfantasy12/mods/217) for a party of four,
  which Ivalice Legacy is being balanced around, plus [Second Accessory](https://www.nexusmods.com/finalfantasy12/mods/436),
  [Gambit Rework](https://www.nexusmods.com/finalfantasy12/mods/471) and
  [Summons For All](https://www.nexusmods.com/finalfantasy12/mods/381).
- Do not combine with other content overhauls (Struggle for Freedom, The Planetary Age, Horizontal Progression
  Overhaul and similar). They replace the same game data.

## Building

This repository contains only the overhaul's own code and patch definitions. No game files are included: the
packages are built from your own installed game. Python 3.10 or newer, no other packages.

```
python -m ivalice_legacy build --game "<game folder>"                        # main package
python -m ivalice_legacy build-base-jobs --game "<game folder>"              # Base Jobs
python -m ivalice_legacy build-arsenal --game "<game folder>"                # Arsenal
python -m ivalice_legacy build-attack-area-experiment --game "<game folder>" # Attack Area Experiment
```

Each command writes a zip plus a manifest of file hashes to `dist/`. A build stops with an error if your game files
differ from the supported version, or if another mod already replaces the same data. Proficiency is plain Lua: copy
`prototypes/forge_proficiency_<n>.lua` to `x64/scripts/config/TheInsurgentsForgeConfig/functions/<n>.lua` and
`proficiency.lua`, `proficiency_runtime.lua` and `clan_rank.lua` to `x64/scripts/IvaliceLegacy/`.

Run the tests with `python -m unittest discover -s tests`. Set `IVALICE_LEGACY_GAME` to your game folder to include
the full build tests; the Lua tests need [Lupa](https://pypi.org/project/lupa/).

## Installing

- **Vortex:** install each built zip as a mod and deploy. A separate Vortex profile keeps other mod collections
  intact. Let Base Jobs win `TheInsurgentsStatLoresConfig.lua` over The Insurgent's Stat Lores.
- **Manually:** copy the zip's `mods` and `x64` folders into the game folder, merging them with the existing ones.

Ivalice Legacy keeps its own save files (see above). To continue a Legacy game started before 0.0.6, copy its
`FFXII_###` file (and its `FFXII_###.dir` folder, if present) to `LEGCY_###` in the same save folder.

To uninstall, disable or remove the mods in Vortex, or delete only the files copied from these packages. Preserve
the existing `mods` and `x64` folders and their other contents.

### Separate personal settings while Legacy is enabled

`python -m ivalice_legacy build --game "<game folder>" --settings-from "<settings source folder>"` adds exact copies
of Bountiful Bundle's JSON, Global Gameplay Settings' Lua config, Vision's JSON and `Orderkeeper/modMenu.json` to the
main package, so those mods keep separate settings while Legacy is enabled. Install it with Legacy winning those
four paths, and carry the Legacy-owned copies forward before any later upgrade.

## Experimental addons

These build separately and are not part of the normal packages:

- **Rampager pilot** (`build-rampager-pilot`): Ripe Rampager enters its recovery phase at 60% HP instead of 50%.
- **Estersand numbers pilot** (`build-estersand-pilot`): Dalmasca Estersand's five early foes take three to five
  hits instead of one to three.
- **Paladin pilot** (`python -m ivalice_legacy.hybrid_pilot`): native White Mage + Knight shown as one authored
  board, with a pinned native adapter in `native/hybrid_boards/`. A design candidate awaiting in-game validation.
- **Clan progression and status prototypes** (`ivalice_legacy/clan_progression.py`, `prototypes/`): offline
  contracts, excluded from installable packages unless a package section above says otherwise.

## Repository layout

| Path | Contents |
|---|---|
| `patches/` | Readable patch definitions (shops, chests, Kings, jobs, weapons, trainings): what changes, plus hashes of the expected source and result |
| `ivalice_legacy/` | Archive reader, file formats, writers and the build commands |
| `package/`, `package_base_jobs/`, `package_arsenal/` | Files and documentation shipped with each package |
| `prototypes/` | Lua for Proficiency, the Attack area filter and diagnostics, plus offline prototypes |
| `tests/` | Tests for the writers, the Lua modules and the full builds |
| `native/hybrid_boards/` | Experimental C++ hybrid-board core and build tools, excluded from installable packages |

## Credits

File formats build on the FFXII modding community's work, including The Insurgent's Workshop and Toolkit (Xeavin),
the FF12 External File Loader and Lua Loader (ffgriever), FehDead's dialog system, and VBFTool (topher-au).

FINAL FANTASY XII is a trademark of Square Enix. This is an unofficial fan project; no game files are distributed
here.
