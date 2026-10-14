"""Deterministic headless simulation of a GameSpec.

Same spec + seed + policy => identical event log, so agents and tests can rely
on exact outcomes. Coordinates: x right, y down; gravity pulls projectiles +y.
"""
import math
import random
from dataclasses import dataclass, field

from . import conditions
from .dsl import MOVE_ACTIONS, GameSpec


@dataclass
class Command:
    move: tuple[float, float] = (0.0, 0.0)
    use: str | None = None
    aim: tuple[float, float] | None = None  # world point; default = nearest enemy


@dataclass
class Unit:
    name: str
    team: str  # "player" | "ally" | "enemy"
    x: float
    y: float
    max_hp: float
    hp: float
    speed: float
    radius: float
    spec: object
    ready_at: dict = field(default_factory=dict)  # ability/rule key -> time it is usable again
    fired: set = field(default_factory=set)  # indices of `once` rules already used
    dmg_mult: float = 1.0
    alive: bool = True

    @property
    def pos(self):
        return (self.x, self.y)


@dataclass
class Shot:
    owner: str
    team: str
    ability: str
    x: float
    y: float
    vx: float
    vy: float
    damage: float
    radius: float
    range_left: float


@dataclass
class Result:
    outcome: str  # "win" | "lose" | "timeout"
    time: float
    ticks: int
    events: list
    frames: list
    units: dict

    def summary(self) -> dict:
        dealt, taken, used = {}, {}, {}
        for e in self.events:
            if e["type"] == "damage":
                dealt[e["src"]] = dealt.get(e["src"], 0) + e["amount"]
                taken[e["dst"]] = taken.get(e["dst"], 0) + e["amount"]
            if e["type"] == "ability":
                used.setdefault(e["src"], {}).setdefault(e["ability"], 0)
                used[e["src"]][e["ability"]] += 1
        return {
            "outcome": self.outcome,
            "time": round(self.time, 2),
            "survivors": {n: round(u["hp"], 1) for n, u in self.units.items() if u["alive"]},
            "damage_dealt": {k: round(v, 1) for k, v in dealt.items()},
            "damage_taken": {k: round(v, 1) for k, v in taken.items()},
            "abilities_used": used,
            "events": {t: sum(1 for e in self.events if e["type"] == t) for t in sorted({e["type"] for e in self.events})},
        }


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _seg_dist(a, b, p):
    """Distance from point p to segment a-b, and how far along the segment (0..1) the closest point is."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return _dist((a[0] + t * dx, a[1] + t * dy), p), t


def with_seed(spec: GameSpec, offset: int) -> GameSpec:
    """Same game with the seed shifted by `offset` (seed i of a multi-seed run)."""
    return spec.model_copy(update={"game": spec.game.model_copy(update={"seed": spec.game.seed + offset})})


def _unit(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-9 else (0.0, 0.0)


class Sim:
    def __init__(self, spec: GameSpec, policy=None, ally_policy=None, record_frames=True):
        from .policies import auto_policy

        self.spec = spec
        self.g = spec.game
        self.dt = 1.0 / self.g.tick_rate
        self.rng = random.Random(self.g.seed)
        self.t = 0.0
        self.tick = 0
        self.events = []
        self.frames = []
        self.shots: list[Shot] = []
        self.record = record_frames
        self.policy = policy or auto_policy
        self.ally_policy = ally_policy or auto_policy
        W, H = self.g.arena.width, self.g.arena.height
        self.units: list[Unit] = []
        for i, c in enumerate(spec.characters):
            x, y = c.spawn or (W * 0.15, H * (i + 1) / (len(spec.characters) + 1))
            self.units.append(Unit(c.name, "player" if i == 0 else "ally", x, y, c.health, c.health, c.speed, c.radius, c))
        starters = [e for e in spec.enemies if not e.minion]
        self.templates = {e.name: e for e in spec.enemies if e.minion}
        self.summoned = {}
        for i, e in enumerate(starters):
            x, y = e.spawn or (W * 0.85, H * (i + 1) / (len(starters) + 1))
            self.units.append(Unit(e.name, "enemy", x, y, e.health, e.health, e.speed, e.radius, e))
        self.player = next(u for u in self.units if u.team == "player")
        self.cond = {id(r): conditions.parse(r.when) for e in spec.enemies for r in e.behavior}
        for u in self.units:
            self.emit("spawn", src=u.name, x=round(u.x, 2), y=round(u.y, 2), hp=u.hp)

    # -- helpers -------------------------------------------------------------
    def emit(self, type_, **kw):
        self.events.append({"t": round(self.t, 3), "type": type_, **kw})

    def alive(self, team=None):
        return [u for u in self.units if u.alive and (team is None or (u.team == "enemy") == (team == "enemy"))]

    def nearest(self, u, team):
        opts = self.alive(team)
        return min(opts, key=lambda o: (_dist(u.pos, o.pos), o.name)) if opts else None

    def clamp(self, u):
        u.x = min(max(u.x, u.radius), self.g.arena.width - u.radius)
        u.y = min(max(u.y, u.radius), self.g.arena.height - u.radius)

    def ready(self, u, key):
        return self.t + 1e-9 >= u.ready_at.get(key, 0.0)

    def damage(self, src: Unit, dst: Unit, amount: float, via: str):
        if not dst.alive:
            return
        amount = min(amount, dst.hp)
        dst.hp -= amount
        self.emit("damage", src=src.name, dst=dst.name, ability=via, amount=round(amount, 3), hp_after=round(dst.hp, 3))
        if dst.hp <= 1e-9:
            dst.hp, dst.alive = 0.0, False
            self.emit("death", src=dst.name, killer=src.name)

    def observe(self, u: Unit) -> dict:
        abilities = {a.name: {"type": a.type, "ready": self.ready(u, a.name), **a.model_dump(exclude={"name", "type"})} for a in u.spec.abilities}
        return {
            "t": self.t,
            "tick": self.tick,
            "seed": self.g.seed,
            "self": {"name": u.name, "pos": u.pos, "hp": u.hp, "max_hp": u.max_hp, "speed": u.speed},
            "abilities": abilities,
            "enemies": [{"name": e.name, "pos": e.pos, "hp": e.hp, "max_hp": e.max_hp, "dist": _dist(u.pos, e.pos)} for e in self.alive("enemy")],
            "arena": (self.g.arena.width, self.g.arena.height),
        }

    # -- characters ----------------------------------------------------------
    def use_ability(self, u: Unit, cmd: Command):
        ab = next((a for a in u.spec.abilities if a.name == cmd.use), None)
        if ab is None or not self.ready(u, ab.name):
            return
        tgt = self.nearest(u, "enemy")
        aim = cmd.aim or (tgt.pos if tgt else (u.x + 1, u.y))
        if ab.type == "dash":
            d = _unit(cmd.move) if cmd.move != (0, 0) else _unit((aim[0] - u.x, aim[1] - u.y))
            if d == (0.0, 0.0):
                return
            x0, y0 = u.pos
            u.x += d[0] * ab.distance
            u.y += d[1] * ab.distance
            self.clamp(u)
            self.emit("ability", src=u.name, ability=ab.name, kind="dash", moved=round(_dist((x0, y0), u.pos), 3))
        elif ab.type == "projectile":
            d = _unit((aim[0] - u.x, aim[1] - u.y))
            if d == (0.0, 0.0):
                return
            self.shots.append(Shot(u.name, u.team, ab.name, u.x, u.y, d[0] * ab.projectile_speed, d[1] * ab.projectile_speed, ab.damage, ab.radius, ab.range))
            self.emit("ability", src=u.name, ability=ab.name, kind="projectile")
        elif ab.type == "melee":
            self.emit("ability", src=u.name, ability=ab.name, kind="melee")
            if tgt and _dist(u.pos, tgt.pos) <= ab.range + u.radius + tgt.radius:
                self.damage(u, tgt, ab.damage, ab.name)
        elif ab.type == "aoe":
            self.emit("ability", src=u.name, ability=ab.name, kind="aoe")
            for v in sorted(self.alive("enemy"), key=lambda v: v.name):
                if _dist(u.pos, v.pos) <= ab.radius + v.radius:
                    self.damage(u, v, ab.damage, ab.name)
        elif ab.type == "heal":
            before = u.hp
            u.hp = min(u.max_hp, u.hp + ab.amount)
            self.emit("ability", src=u.name, ability=ab.name, kind="heal")
            self.emit("heal", src=u.name, dst=u.name, amount=round(u.hp - before, 3), hp_after=round(u.hp, 3))
        u.ready_at[ab.name] = self.t + ab.cooldown

    def step_character(self, u: Unit):
        cmd = (self.policy if u.team == "player" else self.ally_policy)(self.observe(u))
        if cmd.use:
            self.use_ability(u, cmd)
        d = _unit(cmd.move)
        u.x += d[0] * u.speed * self.dt
        u.y += d[1] * u.speed * self.dt
        self.clamp(u)

    # -- enemies -------------------------------------------------------------
    def step_enemy(self, u: Unit):
        tgt = self.nearest(u, "player")  # attacks and movement go for the nearest character (player or ally)
        player = self.player
        ctx = {
            "health": u.hp, "health_max": u.max_hp,
            "player_health": player.hp, "player_health_max": player.max_hp,
            "distance_to_player": _dist(u.pos, player.pos) if player.alive else 1e9,
            "time": self.t, "enemies_alive": len(self.alive("enemy")),
        }
        move = None
        for i, r in enumerate(u.spec.behavior):
            if r.once and i in u.fired:
                continue
            if not self.cond[id(r)].holds(ctx):
                continue
            if r.action in MOVE_ACTIONS:
                move = move or r.action
                continue
            key = f"rule{i}"
            if not self.ready(u, key):
                continue
            pct = round(100 * u.hp / u.max_hp, 2)
            if r.action == "teleport":
                W, H = self.g.arena.width, self.g.arena.height
                x0, y0 = u.pos
                u.x, u.y = self.rng.uniform(u.radius, W - u.radius), self.rng.uniform(u.radius, H - u.radius)
                self.emit("teleport", src=u.name, rule=i, hp_pct=pct, when=r.when, frm=[round(x0, 2), round(y0, 2)], to=[round(u.x, 2), round(u.y, 2)])
            elif r.action == "enrage":
                u.dmg_mult *= r.amount or 1.5
                self.emit("enrage", src=u.name, rule=i, hp_pct=pct, when=r.when, multiplier=round(u.dmg_mult, 3))
            elif r.action == "summon":
                t = self.templates[r.unit]
                for _ in range(r.count):
                    taken = {x.name for x in self.units}
                    k = self.summoned.get(t.name, 0) + 1
                    while f"{t.name}_{k}" in taken:
                        k += 1
                    self.summoned[t.name] = k
                    ang = self.rng.uniform(0, 2 * math.pi)
                    m = Unit(f"{t.name}_{k}", "enemy", u.x + math.cos(ang) * 2, u.y + math.sin(ang) * 2, t.health, t.health, t.speed, t.radius, t)
                    self.clamp(m)
                    self.units.append(m)
                    self.emit("summon", src=u.name, rule=i, hp_pct=pct, when=r.when, unit=m.name, template=t.name, x=round(m.x, 2), y=round(m.y, 2))
            elif r.action == "heal":
                before = u.hp
                u.hp = min(u.max_hp, u.hp + (r.amount or 20))
                self.emit("heal", src=u.name, dst=u.name, rule=i, hp_pct=pct, when=r.when, amount=round(u.hp - before, 3), hp_after=round(u.hp, 3))
            if r.once:
                u.fired.add(i)
            u.ready_at[key] = self.t + r.cooldown
        move = move or "chase"
        if move != getattr(u, "mode", None):
            u.mode = move
            self.emit("mode", src=u.name, mode=move, hp_pct=round(100 * u.hp / u.max_hp, 2))
        if tgt and move != "idle":
            d = _unit((tgt.x - u.x, tgt.y - u.y))
            if move == "flee":
                d = (-d[0], -d[1])
            if move == "chase" and _dist(u.pos, tgt.pos) <= (u.spec.attack.range if u.spec.attack else 0) + u.radius + tgt.radius:
                d = (0.0, 0.0)  # in range: stop and hit
            u.x += d[0] * u.speed * self.dt
            u.y += d[1] * u.speed * self.dt
            self.clamp(u)
        a = u.spec.attack
        if a and tgt and self.ready(u, "attack") and _dist(u.pos, tgt.pos) <= a.range + u.radius + tgt.radius:
            self.emit("ability", src=u.name, ability="attack", kind="melee")
            self.damage(u, tgt, a.damage * u.dmg_mult, "attack")
            u.ready_at["attack"] = self.t + a.cooldown

    # -- projectiles ---------------------------------------------------------
    def step_shots(self):
        keep = []
        W, H = self.g.arena.width, self.g.arena.height
        for s in self.shots:
            s.vy += self.g.gravity * self.dt
            step = math.hypot(s.vx, s.vy) * self.dt
            x0, y0 = s.x, s.y
            s.x += s.vx * self.dt
            s.y += s.vy * self.dt
            s.range_left -= step
            owner = next(u for u in self.units if u.name == s.owner)
            # swept test: the shot hits the first target its path touched this tick, however fast it moves
            hits = []
            for v in self.alive("enemy" if s.team != "enemy" else "player"):
                d, along = _seg_dist((x0, y0), (s.x, s.y), v.pos)
                if d <= s.radius + v.radius:
                    hits.append((along, v.name, v))
            hit = min(hits)[2] if hits else None
            if hit:  # stop where it hit, not where it would have been
                s.x, s.y = hit.x, hit.y
            if hit:
                self.emit("hit", src=s.owner, dst=hit.name, ability=s.ability)
                self.damage(owner, hit, s.damage, s.ability)
            elif s.range_left > 0 and 0 <= s.x <= W and 0 <= s.y <= H:
                keep.append(s)
            else:
                self.emit("miss", src=s.owner, ability=s.ability, reason="range" if s.range_left <= 0 else "wall")
        self.shots = keep

    # -- main loop -----------------------------------------------------------
    def frame(self):
        return {
            "t": round(self.t, 3),
            "units": [[u.name, u.team, round(u.x, 2), round(u.y, 2), round(u.hp, 1), u.max_hp, u.radius, u.alive] for u in self.units],
            "shots": [[round(s.x, 2), round(s.y, 2), s.radius] for s in self.shots],
        }

    def step(self) -> str | None:
        for u in [u for u in self.units if u.alive and u.team != "enemy"]:
            self.step_character(u)
        for u in [u for u in self.units if u.alive and u.team == "enemy"]:
            self.step_enemy(u)
        self.step_shots()
        self.t += self.dt
        self.tick += 1
        if self.record:
            self.frames.append(self.frame())
        if not self.alive("enemy"):
            return "win"
        if not self.player.alive:
            return "lose"
        if self.t >= self.g.max_seconds - 1e-9:
            return "timeout"
        return None

    def run(self) -> Result:
        if self.record:
            self.frames.append(self.frame())
        outcome = None
        while outcome is None:
            outcome = self.step()
        self.emit("end", outcome=outcome)
        return Result(outcome, self.t, self.tick, self.events, self.frames,
                      {u.name: {"team": u.team, "hp": u.hp, "max_hp": u.max_hp, "alive": u.alive} for u in self.units})


def simulate(spec: GameSpec, **kw) -> Result:
    return Sim(spec, **kw).run()
