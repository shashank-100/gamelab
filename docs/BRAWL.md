# GameLab brawler DSL reference (v2)

A ring-out arena fighter. A game is one YAML file with keys `game`, `characters`, `teams`.
Unknown fields are rejected. Names are lowercase `snake_case`.

**How fights work**
- The platform is a circle of `radius` centered at (0, 0). A unit that is outside it and not airborne is knocked out (`ko`).
- Damage is a percentage that never kills by itself. Each hit adds `damage` to the target's damage % and launches it with
  speed `knockback × (1 + damage% / 100) / weight`, which then decays with `friction`. So damaged, light characters fly far.
- Last team with units on the platform wins. At `max_seconds` the team with more units left wins; a tie is a `draw`.
- Each character has up to three abilities, bound to buttons `a`, `b` and `a+b` (one ability per button).
- Every unit is played by the same bot, which uses whatever abilities the character has.

## game
| field | default | meaning |
|---|---|---|
| name | required | snake_case id |
| platform | {radius: 12} | arena size |
| tick_rate | 30 | steps per second |
| max_seconds | 90 | time limit |
| friction | 4 | how fast knockback speed decays (per second); lower = slides farther |
| seed | 0 | randomness seed |

## characters
| field | default | meaning |
|---|---|---|
| name, speed | required | speed in units/second |
| weight | 1.0 | divides knockback taken (0.2–5) |
| radius | 0.6 | body size |
| abilities | required | 1–3 abilities, each with `name`, `kind`, `button`, `cooldown` |

### ability kinds
| kind | fields | behavior |
|---|---|---|
| strike | damage, knockback, range (1.5), windup (0) | hits enemies in front within range, after `windup` seconds |
| projectile | damage, knockback, speed (18), range (12), radius (0.4) | flies toward the target; stopped by walls and enemy rocks |
| dash | distance, damage (0), knockback (0) | instant move; hits enemies it passes through; can carry you off the edge |
| jump | duration, air_speed (1), landing_radius (0), landing_damage (0), landing_knockback (0) | airborne: can't fall off, can't be hit except by projectiles; optional landing shockwave |
| wall | length, duration, distance (1.5) | a barrier in front: blocks movement and projectiles, and stops a unit being knocked into it |
| rocks | count, duration, damage, knockback, orbit_radius (2), orbit_speed (3), rock_radius (0.5) | rocks orbit you; each hits the first enemy it touches, then breaks; they also block enemy projectiles |
| channel | max_time, damage_min, damage_max, knockback_min, knockback_max, min_time (0.2), rooted (true), move_speed (0.4), interruptible (true), release (shockwave), radius (3) | hold to build power; released early = weaker, at `max_time` = full; released before `min_time` it fizzles; `rooted` = can't move (else moves at `move_speed`); `interruptible` = any hit cancels it; `release: shockwave` hits all around within radius, `strike` hits in front within radius |

Cooldowns start when the ability is used (for channels, when released or interrupted).

## teams
```yaml
teams:
  red:  [{character: wrestler}]
  blue: [{character: ninja}, {character: ninja, spawn: [3, 2]}]   # spawn = offset from center (optional)
```
At least two teams. Units are named `<team>_<character>` (plus `_<n>` when a team has duplicates).

## events (what simulations report)
`hit` (src, dst, ability, damage, knockback, dmg_after), `ko` (src = who fell, by = last hitter within 3s or null),
`ability`, `wall`, `wall_block` (a wall stopped a knocked-back unit), `rocks`, `blocked` (projectile stopped by wall/rock),
`jump`, `land`, `channel_start`, `channel_release` (held, power 0–1), `channel_interrupt`, `channel_fizzle`, `end` (outcome).
