# Ivalice Legacy

![Game](https://img.shields.io/badge/FINAL%20FANTASY%20XII-The%20Zodiac%20Age%20(PC)-1f4e79)
![Version](https://img.shields.io/badge/version-0.1.37-2e7d32)
![Status](https://img.shields.io/badge/status-early%20development-c77c02)

**An overhaul for FINAL FANTASY XII THE ZODIAC AGE where preparation wins.**

Jobs combine instead of overlapping, gear changes how you play, and enemies act instead of just hitting harder.
Rare monsters rule their zones, the Clan is worth climbing, and progression can't be steamrolled. Legacy is balanced
as a hard game for a party of four, where choices matter and bosses push the party to the wall.

It plays solo, and alongside [Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) for couch co-op.

> **Early development.** Expect large changes between versions. Updates are meant to continue your existing Legacy
> campaign; any restart a version needs will be stated before you install it. There is no public download yet; see
> [Building from source](#building-from-source).

---

## Highlights

### Twelve jobs, sixty-six hybrids
Knight, Lancer, Warrior, Samurai, Hunter, Gunner, Chemist, Green Mage, Time Mage, Mystic, White Mage and Black Mage,
each with a compact board of its own and job stat trainings that grow the stats its role uses. When a character
takes a second job, both boards fuse into **one board named for the hybrid**: Knight + White Mage is a Paladin.
Every board carries the thirteen legendary weapons, each behind its Esper.

### A Clan worth climbing
**Montblanc** awards each job its Clan **Proficiency** line, which works with whatever weapon the character holds.
Twelve Clan ranks raise three stages of four levels, so every rank is a reward. Writs arrive as key items, and each
stage's current effect is shown on the gear. Lines follow the job, so changing jobs never loses progress. At rank 12,
a master of a legendary weapon's family deals +25% with it.

### Weapons with a style of play
A gentle damage climb replaces the late-game spike. Every weapon family has its own way of fighting, and most pay a
price for it: axes wear Defense down (**Sunder**) but wear your own too (**Reckless**), spears pierce it but never
parry except to counter, katanas chain longer combos, guns never miss (**True aim**) but charge slowly, daggers and
ninja swords glance off heavy armour (**Glancing**), poles counter on their own evade, rods draw Lure when a heal
overflows, crossbows and hammers trade tempo for their status, and fists build up Attack by Attack. Shields choose a
side: the Flame, Diamond and Dragon shields now ward magick instead of blows. No hit can be reduced to nothing by
Defense, for foes or for the party.

### Armour that asks questions
105 of the 140 armour pieces gain one strategic property, such as an element ward or a stat trade, chosen for what
foes cast at that point in the story. Accessories become each character's wildcard edge.

### Magick has a price
Party attack spells are set individually so they keep pace with weapons. A stronger caster pays more MP, and no spell
can pay for itself. Expose and Shear now take a real 30%, and the Technicks that wasted turns became more reliable.

### Enemies that fight back
More than a thousand added behaviours: support foes rescue their allies, foes punish clustered parties, wounded foes
escalate, and bosses change their moves. Monster families fight in their own way. Enemy skills carry new riders, and
everyday items answer them. All 29 story bosses are crowned, scale to your party, and reward you for the fight.

### Kings of the zone
Every rare game is the King of its zone. It is there on every visit until you defeat it once, and only rarely
returns after that.

### A world that grows with you
Rabanastre opens with a wider set of tools. Shops add stock as the story advances. Key ability chests always appear,
and weapons that could be found far too early now wait their turn.

### Your own save files
Legacy saves are `LEGCY_###` files, kept apart from your normal `FFXII_###` saves, with separate settings for several
companion mods.

**[Read the full feature guide &rarr;](docs/FEATURES.md)**

---

## What has been played

| System | State |
|---|---|
| Twelve jobs, boards, trainings | Played (early game) |
| Shops, story stock, loot, separate saves | Played |
| Weapon curve, greatsword sweep | Played |
| Kings of the zone | Played on the Estersand; other zones not yet |
| Clan Proficiency | Stage I played to rank 4; first hybrid lines seen in play; stages II-III offline |
| Hybrid boards | Installed; the Seer line has fired in play |
| Weapon family identities (runtime) | Played since 0.1.24: Spellbreak, Afflict, Marksman (now True aim) and pole counters seen; Sunder, Unseen, Chain, Mend and Cadence's speed ramp not yet seen |
| Guard fatigue, Braced, Open, Long haft | In the campaign since 0.1.26-0.1.28; guard fatigue measured once |
| Weapons pillar 0.1.34: True aim, Reckless, Glancing, spear counters (need the Counter augment, which the Battle Harness carries again from 0.1.35), rod Lure, shield triangle, weapon trims | Offline; built and covered by tests, not yet played |
| Base jobs 0.1.35: Battle Harness Counter; Telekinesis on Warrior, Achilles on Gunner, Protectga and Shellga on White Mage, Lancer without Dispel and Stona; Zanshin I only when not Hasted, Zanshin II-1 and II-2 swapped; Detonate counts Confused foes | Offline; built and covered by tests, not yet played |
| Hybrid job names 0.1.36: 16 renames (Chrono Lancer, Crusader, Monk, Cleric, Chrono Knight, Gunbreaker, Corsair, Riskbreaker, Lanista, Swiftblade, Arithmetician, Ki Gunner, Spellfist, Dragonkin, Spellbow, Despoiler) | Installed; names not yet seen in game |
| Hybrid Proficiency rework 0.1.37: all 66 hybrid lines rebuilt (trigger from one parent, payoff from the other), no Proficiency stage grants Libra, Protect only on Knight, White Mage and Mystic lines, and two more renames (Detonator, Magebreaker) | Offline; built and covered by tests, not yet played |
| Armour and accessories, armour triangle | Text seen; effects and the triangle not yet measured |
| Magicks, MP economy, Technicks | Offline; MP costs confirmed in the game log |
| Enemy behaviours, boss crown, stat pass | In the campaign; not yet checked one by one |
| Weapon Families tips (Traveler's Tips) | Updated for 0.1.35 (Counter augment, bomb status); the 0.1.33 and 0.1.34 texts were never read in game |

*Offline* means built and covered by tests, but not yet confirmed in play.

---

## Requirements

- **FINAL FANTASY XII THE ZODIAC AGE**, Steam version 1.0.4.0, with the game language set to **English**.
- [FF12 External File Loader](https://www.nexusmods.com/finalfantasy12/mods/170) and
  [FF12 LUA Loader](https://www.nexusmods.com/finalfantasy12/mods/171) 2.0.1 with its dialog plugin.
- [The Insurgent's Forge](https://www.nexusmods.com/finalfantasy12/mods/386): Proficiency, weapon identities, damage,
  magicks and MP.
- [The Insurgent's Stat Lores](https://www.nexusmods.com/finalfantasy12/mods/299): job stat trainings.
- [Dynamic Description](https://www.nexusmods.com/finalfantasy12/mods/466) and
  [The Insurgent's Descriptive Inventory](https://www.nexusmods.com/finalfantasy12/mods/319): item and gear text.
- [Dynamic Bosses](https://www.nexusmods.com/finalfantasy12/mods/503) and
  [Instanced Foes](https://www.nexusmods.com/finalfantasy12/mods/528): the enemy foundation.

**Recommended**, and what Legacy is tested with:
[The Insurgent's Companions](https://www.nexusmods.com/finalfantasy12/mods/217) for a party of four,
[Second Accessory](https://www.nexusmods.com/finalfantasy12/mods/436),
[Gambit Rework](https://www.nexusmods.com/finalfantasy12/mods/471) and
[Summons For All](https://www.nexusmods.com/finalfantasy12/mods/381).

**Optional:** [Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) for local co-op. Legacy never
changes Ivalice Together's files or settings.

**Not compatible** with other content overhauls (Struggle for Freedom, The Planetary Age, Horizontal Progression
Overhaul and similar): they replace the same game data. Keep Bountiful Bundle's "Always Spawn Rare Game" **off**, or
defeated Kings return on every visit.

## Installing

- **Vortex (recommended):** install the package as a mod in its own profile, so your other setups stay intact. Let
  Ivalice Legacy win every file it shares with the mods it builds on (Stat Lores, Dynamic Bosses, Dynamic Description,
  Descriptive Inventory, Gambit Rework and the settings files of Bountiful Bundle, Global Gameplay Settings, Vision
  and Orderkeeper), then deploy.
- **Manually:** copy the package's `mods` and `x64` folders into the game folder, merging them with the existing ones.

**Uninstalling:** disable the mod in Vortex, or delete only the files that came from the package, keeping the rest of
the `mods` and `x64` folders. Your normal saves were never touched. Licenses bought on Legacy boards stay bought.

## Building from source

This repository contains only Ivalice Legacy's own code and patch definitions. No game files are included: packages
are built from your own installed game. You need Python 3.10 or newer, with no other packages.

```
python -m ivalice_legacy build --game "<game folder>"              # shops, loot, Kings, magicks, saves
python -m ivalice_legacy build-base-jobs --game "<game folder>"    # the twelve jobs
python -m ivalice_legacy build-arsenal --game "<game folder>"      # weapons and armour
python -m ivalice_legacy build-proficiency --game "<game folder>"  # Clan Proficiency
```

Each command writes a zip plus a manifest of file hashes to `dist/`. A build stops with an error if your game files
differ from the supported version, or if another mod already replaces the same data.

These commands build the systems as separate component packages. The combined 0.1.x release, which also holds the
enemy rework, is not yet reproducible from this repository alone. A single build command for the whole release is
planned before the first public download.

Run the tests with `python -m unittest discover -s tests`. Set `IVALICE_LEGACY_GAME` to your game folder to include
the full build tests. The Lua tests need [Lupa](https://pypi.org/project/lupa/).

<details>
<summary>Repository layout</summary>

| Path | Contents |
|---|---|
| `patches/` | Readable patch definitions: what changes, plus hashes of the expected source and result |
| `ivalice_legacy/` | Archive reader, file formats, writers and the build commands |
| `package/` | Lua runtime and configuration shipped with the game package |
| `package_base_jobs/`, `package_arsenal/` | Documentation and changelogs for jobs and weapons |
| `prototypes/` | Lua for Proficiency, weapon identities, the MP economy and the damage formula |
| `native/hybrid_boards/` | The fused hybrid-board core and its build tools |
| `tests/` | Tests for the writers, the Lua modules and the full builds |
| `docs/` | The feature guide |

</details>

## Planned

- Each King's own fight and reward, and a reworked Clan Centurio with its hunts.
- Job armour lines with their own immunities, and more gear with real trade-offs.
- A signature command for every hybrid.
- Boss phases and group roles beyond the current strategies.
- Espers, exploration and the Bazaar revisited.
- Progression paced so preparation matters more than grinding.

## Credits

File formats build on the FFXII modding community's work, including The Insurgent's Workshop and Toolkit (Xeavin),
the FF12 External File Loader and Lua Loader (ffgriever), FehDead's dialog system, and VBFTool (topher-au). Thanks to
the authors of the mods Legacy builds on, listed under [Requirements](#requirements).

FINAL FANTASY XII is a trademark of Square Enix. This is an unofficial fan project, and no game files are distributed
here.
