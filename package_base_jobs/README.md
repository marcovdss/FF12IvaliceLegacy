# Ivalice Legacy: twelve base jobs candidate 0.3.0

Experimental offline build for the English Steam game, version 1.0.4.0.
Twelve custom boards, English job names and descriptions, and eight exclusive
weapon-license LP price increases are included. No Lua scripts,
hooks, save redirection, equipment-stat changes or Together files are included.
This candidate has not been validated in the game UI or in combat.

Boards are compact regions around Essentials: gear above, accessories to the
left, passive augments to the right and magicks/Technicks below, cheaper licenses
nearest the centre. Espers and Quickenings sit on the outer edge, each next to one
ordinary license. Ordinary boards cost 4,506–5,491 LP. Every board has two Swiftness licenses;
both are reachable before half the ordinary total. Connected opening license
kits cost 70–145 LP. Acquisition still matters: licenses do not supply a weapon
or command. Measures need later shop placement; Hornito costs 5,000 gil.
Several signature commands are not opening shop purchases.

Gunner uses light armor and guns. Chemist has eight Battle Lores and crossbows.
Kumbha belongs to Knight; Excalibur belongs to Warrior. Native command bundles
and item/action records are unchanged. The license-table override changes only
eight LP bytes: Guns 2–6 and Mithuna, plus Crossbows 3–4. The manifest lists each
before/after price. Previously owned licenses are never charged again.

## Saved slot identities

Existing job slot IDs remain unchanged. Their proposed new curricula are:

| Original slot | Candidate job |
|---|---|
| White Mage | White Mage |
| Uhlan | Lancer |
| Machinist | Gunner |
| Red Battlemage | Green Mage |
| Knight | Knight |
| Monk | Mystic |
| Time Battlemage | Time Mage |
| Foebreaker | Warrior |
| Archer | Hunter |
| Black Mage | Black Mage |
| Bushi | Samurai |
| Shikari | Chemist |

Already owned licenses remain owned, including licenses outside the new board.
No automatic respec, job reassignment or save migration is performed. Spell and
Technick inventory acquisition still applies. Native Quickening, Esper and
second-job restrictions remain in place; ordinary licenses are never gated by them.
Base boards retain the native two-board interface. No hybrid class is enabled.

## Installation and rollback

This is a review candidate, not an instruction to deploy into an ongoing campaign.
Before an approved installation, inventory the exact deployed winners and back up
every existing target with SHA-256 hashes. Use the mod manager's supported
installation/deployment path. Do not stack this with another board or English
menu-command/menu-message or license-table replacement, or with the experimental
Paladin pilot. Other mods replacing battle-pack section 12 require review;
separate section overrides such as shop section 39 remain independent.

For rollback, disable/remove this candidate through the same manager and restore
the previous winners from that installation's backups. Delete a candidate-owned
file only if its current hash still matches this package; preserve changed or
unknown files. The included manifest lists payload hashes and excludes its own
hash. Removing these files restores prior layouts/labels; it does not undo LP
spending or licenses acquired in a save while the candidate was active. Rollback
must restore the prior section 12 provider as well as board and text resources.

Ivalice Together's files and settings must remain unchanged. Native labels are
patched for English only; compatibility of any separate co-op job-name display
must be checked independently.
