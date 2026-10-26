# Ivalice Legacy: Arsenal 0.1.0 (experimental)

Weapon family identities for the English Steam game, version 1.0.4.0. This first
release covers **daggers** only; other weapon families, armour and accessories
follow once daggers are confirmed in play.

Daggers are the fastest weapons in the game:

- Charge time 21 → 15 on all twelve daggers.
- Combo rate rises with the dagger's tier (15 → 20).
- Evade follows the dagger tier (5 → 10). Main Gauche keeps its high parrying
  evade and its native combo rate.
- Attack power and prices are unchanged.

The package replaces one section of `battle_pack.bin` (the equipment table,
section 13) through the FF12 External File Loader. It is built from your own
game files and refuses to build if that table differs from the supported game
version or another mod already replaces it. No Lua scripts, hooks, saves or
co-op files are included. Removing the package restores native daggers.
