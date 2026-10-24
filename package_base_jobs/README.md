# Ivalice Legacy: twelve base jobs candidate 0.1.0

Experimental offline build for the English Steam game, version 1.0.4.0.
Twelve custom boards and native English job labels are included. No Lua scripts,
hooks, save redirection, equipment-stat changes or Together files are included.
This candidate has not been validated in the game UI or in combat.

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
second-job restrictions remain in place; ordinary nodes are never gated by them.
Base boards retain the native two-board interface. No hybrid class is enabled.

## Installation and rollback

This is a review candidate, not an instruction to deploy into an ongoing campaign.
Before an approved installation, inventory the exact deployed winners and back up
every existing target with SHA-256 hashes. Use the mod manager's supported
installation/deployment path. Do not stack this with another board or English
menu-command replacement, or with the experimental Paladin pilot.

For rollback, disable/remove this candidate through the same manager and restore
the previous winners from that installation's backups. Delete a candidate-owned
file only if its current hash still matches this package; preserve changed or
unknown files. The included manifest lists payload hashes and excludes its own
hash. Removing these files restores prior layouts/labels; it does not undo LP
spending or licenses acquired in a save while the candidate was active.

Ivalice Together's files and settings must remain unchanged. Native labels are
patched for English only; compatibility of any separate co-op job-name display
must be checked independently.
