"""Generic brawler bot: works for any character by reading its abilities' kinds and numbers."""
import math
import random

from .engine import Command


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bot(obs) -> Command:
    rng = random.Random(obs["seed"] * 1_000_003 + obs["tick"] * 7919 + sum(map(ord, obs["self"]["name"])) % 997)
    me, R, ab = obs["self"], obs["R"], obs["abilities"]
    pos = me["pos"]
    if not obs["enemies"]:
        return Command()
    tgt = min(obs["enemies"], key=lambda e: (e["dist"], e["name"]))
    d = tgt["dist"]
    ready = {n: a for n, a in ab.items() if a["ready"]}
    by_kind = lambda k: [a for a in ready.values() if a["kind"] == k]
    r_center = math.hypot(*pos)
    to_center = (-pos[0], -pos[1])

    # 1. channeling: release when someone is in the blast and power is decent, or at full power
    if me["channel"]:
        ch = me["channel"]
        in_blast = any(e["dist"] <= ch["radius"] + e["radius"] for e in obs["enemies"])
        frac = ch["held"] / ch["max_time"]
        return Command(release=(in_blast and frac >= 0.5) or frac >= 1.0, move=to_center)

    # 2. edge danger: flying outward near the edge -> jump if we can, else run to the center
    speed_out = (me["vel"][0] * pos[0] + me["vel"][1] * pos[1]) / max(r_center, 1e-6)
    if r_center > 0.7 * R and speed_out > 2 and by_kind("jump"):
        return Command(press=by_kind("jump")[0]["name"], move=to_center)
    if r_center > 0.8 * R:
        return Command(move=to_center)

    noise = rng.random()
    # 3. block incoming projectiles with a wall
    for s in obs["incoming"]:
        rel = (pos[0] - s["pos"][0], pos[1] - s["pos"][1])
        if _d(s["pos"], pos) < 6 and rel[0] * s["vel"][0] + rel[1] * s["vel"][1] > 0 and by_kind("wall") and noise < 0.7:
            return Command(press=by_kind("wall")[0]["name"], aim=s["pos"])
    # 4. rocks when an enemy is getting close
    if by_kind("rocks") and d < 6:
        return Command(press=by_kind("rocks")[0]["name"])
    # 5. strike in range
    for a in by_kind("strike"):
        if d <= a["range"] + me["radius"] + tgt["radius"] and not tgt["airborne"]:
            return Command(press=a["name"], aim=tgt["pos"])
    # 6. channel when the enemy is near but not on top of us
    for a in by_kind("channel"):
        if a["radius"] * 0.8 < d < a["radius"] * 2.5 and noise < 0.5:
            return Command(press=a["name"], aim=tgt["pos"])
    # 7. jump onto a close enemy if the landing hurts
    for a in by_kind("jump"):
        if a["landing_radius"] > 0 and d < a["landing_radius"] * 2 and noise < 0.3:
            return Command(press=a["name"], move=(tgt["pos"][0] - pos[0], tgt["pos"][1] - pos[1]))
    # 8. shoot
    for a in by_kind("projectile"):
        if d <= a["range"] * 0.9:
            wob = rng.gauss(0, 0.5)
            return Command(press=a["name"], aim=(tgt["pos"][0] + wob, tgt["pos"][1] + rng.gauss(0, 0.5)),
                           move=(pos[0] - tgt["pos"][0], pos[1] - tgt["pos"][1]) if d < 3 else (0.0, 0.0))
    # 9. dash in (never off the edge)
    for a in by_kind("dash"):
        if d > a["distance"] * 0.6:
            u = ((tgt["pos"][0] - pos[0]) / d, (tgt["pos"][1] - pos[1]) / d)
            end = (pos[0] + u[0] * a["distance"], pos[1] + u[1] * a["distance"])
            if math.hypot(*end) < 0.8 * R:
                return Command(press=a["name"], aim=tgt["pos"], move=u)
    # 10. move: get between the enemy and the center so hits push them outward
    tr = math.hypot(*tgt["pos"]) or 1.0
    spot = (tgt["pos"][0] - tgt["pos"][0] / tr * 1.2, tgt["pos"][1] - tgt["pos"][1] / tr * 1.2)
    mv = (spot[0] - pos[0], spot[1] - pos[1])
    if rng.random() < 0.15:
        mv = (rng.uniform(-1, 1), rng.uniform(-1, 1))
    return Command(move=mv)
