"""Player policies: observation dict -> Command."""
import math
import random

from .engine import Command


def _toward(a, b, sign=1.0):
    return (sign * (b[0] - a[0]), sign * (b[1] - a[1]))


def _kite(me, tgt, arena):
    """Back away from tgt; near a wall, strafe along it toward the roomier side instead."""
    away = _toward(me, tgt, -1)
    W, H = arena
    nx = away[0] if 2 < me[0] + away[0] * 0.2 < W - 2 else 0.0
    ny = away[1] if 2 < me[1] + away[1] * 0.2 < H - 2 else 0.0
    if (nx, ny) == (0.0, 0.0) or abs(nx) + abs(ny) < 0.3 * (abs(away[0]) + abs(away[1])):
        if abs(away[0]) >= abs(away[1]):
            ny = 1.0 if me[1] < H / 2 else -1.0
        else:
            nx = 1.0 if me[0] < W / 2 else -1.0
    return (nx, ny)


def auto_policy(obs: dict) -> Command:
    """Scripted baseline with seeded human-like noise, so different seeds give different fights."""
    rng = random.Random(obs["seed"] * 1_000_003 + obs["tick"] * 7919 + sum(map(ord, obs["self"]["name"])) % 997)
    if rng.random() < 0.12:  # reaction delay: occasionally just keep drifting
        return Command(move=(rng.uniform(-1, 1), rng.uniform(-1, 1)))
    cmd = _auto(obs)
    if cmd.move != (0.0, 0.0) or cmd.use is None:
        ang = rng.uniform(-0.6, 0.6)  # strafe jitter
        mx, my = cmd.move
        cmd.move = (mx * math.cos(ang) - my * math.sin(ang), mx * math.sin(ang) + my * math.cos(ang))
    if cmd.use and obs["abilities"].get(cmd.use, {}).get("type") == "projectile" and obs["enemies"]:
        tgt = min(obs["enemies"], key=lambda e: (e["dist"], e["name"]))
        wob = rng.gauss(0, 0.6)  # aim wobble, in arena units
        cmd.aim = (tgt["pos"][0] + wob, tgt["pos"][1] + rng.gauss(0, 0.6))
    return cmd


def _auto(obs: dict) -> Command:
    """Deterministic core: heal when low, aoe/melee up close, shoot in range, dash to close gaps, kite at range."""
    me, abil = obs["self"], obs["abilities"]
    if not obs["enemies"]:
        return Command()
    tgt = min(obs["enemies"], key=lambda e: (e["dist"], e["name"]))
    d = tgt["dist"]
    ready = lambda kind: [n for n, a in abil.items() if a["type"] == kind and a["ready"]]
    shots = {n: a for n, a in abil.items() if a["type"] == "projectile"}
    melee = {n: a for n, a in abil.items() if a["type"] == "melee"}
    best_range = max([a["range"] for a in shots.values()], default=0)

    if me["hp"] < 0.4 * me["max_hp"] and ready("heal"):
        return Command(use=ready("heal")[0])
    for n in ready("aoe"):
        r = abil[n]["radius"]
        if any(e["dist"] <= r + 0.5 for e in obs["enemies"]):
            return Command(move=_toward(me["pos"], tgt["pos"]), use=n)
    for n in ready("melee"):
        if d <= melee[n]["range"] + 1.1:
            return Command(move=_toward(me["pos"], tgt["pos"]), use=n)
    for n in sorted(ready("projectile"), key=lambda n: -shots[n]["damage"]):
        if d <= shots[n]["range"] * 0.95:
            return Command(move=_kite(me["pos"], tgt["pos"], obs["arena"]) if d < best_range * 0.5 else (0.0, 0.0), use=n)
    want = best_range * 0.7 if shots else 1.0
    if d > want + 2 and ready("dash"):
        return Command(move=_toward(me["pos"], tgt["pos"]), use=ready("dash")[0])
    if shots and d < best_range * 0.5:
        return Command(move=_kite(me["pos"], tgt["pos"], obs["arena"]))
    return Command(move=_toward(me["pos"], tgt["pos"]) if d > want else (0.0, 0.0))


def idle_policy(obs: dict) -> Command:
    return Command()
