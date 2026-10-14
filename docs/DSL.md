# GameLab DSL reference

A game is one YAML file with three top-level keys: `game`, `characters`, `enemies`.
Unknown fields are rejected. Names are lowercase `snake_case` and unique across characters and enemies.
Coordinates: arena units, x right, y down. Times in seconds.

## game
| field | default | meaning |
|---|---|---|
| name | required | snake_case id |
| arena | {width: 40, height: 30} | playfield size |
| gravity | 0 | downward pull on projectiles (units/s²); 0 = top-down |
| tick_rate | 20 | simulation steps per second |
| max_seconds | 60 | fight ends as `timeout` after this |
| seed | 0 | randomness seed |

## characters
The first character is the player; any others are allies driven by the same auto player.
| field | meaning |
|---|---|
| name, health, speed | required |
| spawn | [x, y] (default: left side) |
| radius | body size, default 0.5 |
| abilities | list, see below |

### abilities (`type` selects the kind)
| type | fields |
|---|---|
| dash | cooldown, distance — instant move toward the movement direction (or the nearest enemy) |
| projectile | cooldown, damage, range, projectile_speed (20), radius (0.5) — flies toward the aim point |
| melee | cooldown, damage, range (1.5) — hits the nearest enemy if in reach |
| aoe | cooldown, damage, radius — hits every enemy within radius |
| heal | cooldown, amount — restores own health |

## enemies
| field | meaning |
|---|---|
| name, health, speed | required |
| spawn | [x, y] (default: right side) |
| radius | default 0.6 |
| attack | {damage, range (1.5), cooldown (1)} — automatic melee when the player is in reach |
| behavior | ordered list of rules, see below |
| minion | true = not present at start; only appears via a `summon` rule |

Enemies with no attack can never hurt the player.

### behavior rules
```yaml
- when: health < 30%          # condition
  action: teleport            # what to do
  once: true                  # optional: fire at most once per fight
  cooldown: 5                 # optional: seconds between firings
  amount: 1.5                 # heal: health restored; enrage: damage multiplier
  unit: skeleton              # summon: which minion
  count: 2                    # summon: how many
```
Rules are checked top to bottom every tick.
- Movement actions `chase`, `flee`, `idle`: the **first** matching movement rule wins; with none matching the enemy chases.
- Instant actions `teleport`, `enrage`, `heal`, `summon` fire whenever their condition holds and they are off cooldown,
  so without `once` or `cooldown` they fire every tick.
- `once` and `cooldown` only apply to instant actions; a movement rule holds for as long as its condition is true.

### conditions
`<variable> <op> <number>[%]`, joined with `and` / `or`. Ops: `< <= > >= == !=`.
| variable | meaning |
|---|---|
| health | own health; with `%` it is a fraction of max health (`health < 30%`). Without `%` it is absolute hit points. |
| player_health | the player's health; `%` allowed |
| distance_to_player | arena units to the player (the first character), not to allies |
| time | seconds since start |
| enemies_alive | number of living enemies |

## Outcomes
`win` when every enemy (including summoned minions) is dead, `lose` when the player (the first character) dies, even if allies are still alive, `timeout` at `max_seconds`.
Enemies attack and chase the nearest character, which can be an ally.
The auto player heals when low, uses aoe/melee up close, shoots in range, dashes to close distance and kites; it has small random variation per seed.
