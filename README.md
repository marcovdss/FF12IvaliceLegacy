# Ivalice Legacy

An overhaul for **FINAL FANTASY XII THE ZODIAC AGE** (PC, Steam), built for co-op play with
[Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548) and just as playable solo.

The goal is an Ivalice where preparation wins: jobs that combine instead of overlapping, gear that changes how you
play, enemies that act instead of just hitting harder, rare monsters that rule their zones, a Clan worth climbing,
and progression you can't steamroll.

> **Status: early development (0.0.8).** Only the first modules exist so far. Expect large changes between
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

**Separate save files.** The main package includes an early-init save redirect at
`x64/scripts/init/IvaliceLegacySaves.lua`, requiring FF12 LUA Loader **2.0.1**. On the supported build it patches
the native listing prefix and filename format to `LEGCY_`, before game startup continues. Legacy therefore lists
and addresses its own `LEGCY_000`, `LEGCY_001`, ... namespace. Normal `FFXII_000`, `FFXII_001`, ... files are
left in place. Disabling the main package and restarting the game restores the normal namespace. A restart is
required when switching; this is not an in-session toggle.

The script does not copy, rename or convert saves. An intentionally imported `LEGCY_` campaign stays available;
without Legacy files, an empty Legacy load list is expected. Filenames separate namespaces, not creation provenance.

The build checks the exact supported executable and Lua-loader bytes. Startup verifies both patched strings
and logs `Ivalice Legacy: using separate save files (LEGCY_###); early-init readback verified.` (or
`separate save files already active`). On activation failure it reports **FATAL** and attempts to restore only
its own changes to their exact initial bytes. This does **not** block the whole game: stop before loading or saving
if activation fails. Native filename/wildcard instructions and synthetic-memory transaction tests are checked;
the save-list switch has also been checked in-game with Legacy enabled and disabled. Actual save writing,
sidecars and Steam Cloud still require in-game validation.

## Planned

- Twelve standalone base jobs, followed by derived hybrid classes for their combinations.
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

## Experimental twelve-base-job candidate

```
python -m ivalice_legacy build-base-jobs --game "<game folder>"
```

This builds a separate `IvaliceLegacy-BaseJobs-0.1.0.zip` containing twelve authored
boards, English native job names, package hashes and rollback documentation. It
does not include the normal modules or the Paladin pilot. The regular `build`
command is unchanged.

The bases are Knight, Lancer, Warrior, Samurai, Hunter, Gunner, Chemist, Green Mage,
Time Mage, Mystic, White Mage and Black Mage. Existing saved slot IDs and acquired
licenses persist; each slot receives the curriculum listed in the candidate's
README. No automatic respec or job reassignment occurs. Native special-license
restrictions remain in place, with no ordinary node gated behind a special choice.

These are offline candidates. Source hashes, geometry, LP routes and native text
records were checked; game rendering, saved-campaign behavior and combat balance
remain unverified. Do not combine with other board/English menu-command replacements
or the experimental Paladin pilot. Together's files and settings are untouched.

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

## Paladin pilot (separate review candidate)

The experimental Paladin pilot presents native **White Mage + Knight** as one
authored board, in either job order. It retains all198 unique licenses from the
source pair, native ownership, LP prices, Espers and Quickenings. Other pairs keep
their normal boards. This is one pilot, not66 completed hybrid jobs.

The new paths change progression: unchanged node prices do not mean identical
LP investment to reach each ability. The layout remains a design candidate;
actual UI, installed-loader behavior and campaign play await in-game validation.
It is excluded from the normal build and introduces no save redirect.

Build the pinned Windows x64 adapter and the separate candidate:

```powershell
python native/hybrid_boards/adapterbuild.py
python -m ivalice_legacy.hybrid_pilot --game "<game folder>" --recipe patches/paladin_pilot.json --gate native/hybrid_boards/pilot-gate.json --out dist
```

The build refuses changed source boards, license definitions, incomplete hook
plans or a gate that no longer matches the image/layout/loader bytes. Install
only after reviewing the candidate on the intended Legacy profile, with the
game closed. Do not hot-reload or hot-unload its process-lifetime native hooks.

## Clan progression prototype

`ivalice_legacy/clan_progression.py` defines an offline contract for character-specific
equipment training. Each campaign supplies its own schema-1 ledger, keyed by stable
character ID and either one job or a canonical unordered pair. Switching class or
equipment selects only matching owned rules; base-job training is not added to a
hybrid's line. Weapons, actual shields, head armor and body armor are eligible.
Both accessory slots are excluded.

Rank and completed quests determine availability separately from ownership. Missing
ledger data starts empty; malformed data is rejected; unknown owned rule IDs are
preserved but grant nothing. The module does not read saves, charge costs, apply
combat effects or hook the game, and contains no production upgrade catalogue.
Synthetic tests cover all 66 pairs and isolation between characters and classes.
This prototype is excluded from installable game payloads.

The separate [status prototype](prototypes/README.md) explores different utility
for two owners of the same Dagger through caller-supplied class rules. It remains
offline and excluded from builds; no production status chance or Clan effect is
enabled by its presence in this repository.

## QoL integration

The selected QoL foundation uses separately obtained Manifesto **1.5.2** for
Smart Stealing Gambits, Dynamic Description **1.1.14**, and Descriptive Inventory
**1.2.2**. The checked loader versions are External File Loader **1.6.1** and
Lua Loader **2.0.1**. These dependencies and their assets are not included in the
main Legacy ZIP. Use Manifesto as the sole Smart Steal provider.

Keep the integration's writable configuration in a separate Legacy-only provider:
DD's `customAttributes.json` and all nine generated TIDI language files must
belong to that provider. English-only isolation would still allow another game
language to overwrite a shared preset. Preserve autosaves, automatic pause and
crash diagnostics; the selected preset leaves optional changes other than
Smart Stealing disabled.

Offline checks cover the twelve boards' ordinary licensed gear/command selection
and the generated-description buffer path. Native text rendering and gameplay
acceptance require an existing-save observation. Esper descriptions remain outside
the selected DD action range. Native DD/TIDI descriptions do not update Together's
private overlay catalogue.

## Installing

### Separate personal settings while Legacy is enabled

For the four selected mods, build a personalized MAIN package from your current
settings. The source directory must contain the normal `x64/scripts/config/`
structure:

```powershell
python -m ivalice_legacy build --game "<game folder>" --settings-from "<settings source folder>" --out dist
```

This adds exact copies of Bountiful Bundle's JSON, Global Gameplay Settings' Lua
config, Vision's JSON and `Orderkeeper/modMenu.json` to the main Legacy package.
Install that package with Legacy winning those four paths over their original
providers. With MAIN enabled, reads and writes follow the deployed links into
Legacy's private staging files. Disable MAIN and redeploy to restore the normal
providers; enable and redeploy to resume the retained Legacy copies. Restart the
game after switching. This isolates settings by enabled mod, not by individual save.

The builder preserves bytes and chosen values, and requires all four files. It
does not edit the source files or include these personal configs in this repository.
Before a later upgrade, back up and carry forward the existing Legacy-owned
copies, including changes saved through mod menus. A replacement package must
not reset them to the other profile's settings. Without `--settings-from`, the
build contains only the nine core Legacy files and does not provide this settings
isolation. The four external mods remain separate dependencies.

Use mod-manager deployment for this personalized package, with regular config
files in Legacy's own staging directory. Manually copying over existing deployed
links can overwrite another provider's settings and does not establish separate
storage. The manual procedure below applies to the core-only build.

- **Vortex:** install the built zip as a mod and deploy. Using a separate Vortex profile keeps other mod
  collections intact.
- **Manually:** copy both the zip's `mods` and `x64` folders into the game folder, merging them with the existing
  folders. The save-isolation script must be at `x64/scripts/init/IvaliceLegacySaves.lua`.

Ivalice Legacy keeps its own save files (see above). To continue a Legacy game started before 0.0.6, copy its
`FFXII_###` file (and its `FFXII_###.dir` folder, if present) to `LEGCY_###` in the same save folder.

To uninstall, disable or remove the mod in Vortex, or delete only the files copied from this package, including
`x64/scripts/init/IvaliceLegacySaves.lua`. Preserve the existing `mods` and `x64` folders and their other contents.

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
