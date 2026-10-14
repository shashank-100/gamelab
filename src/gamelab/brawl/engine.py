"""Deterministic ring-out brawler simulation.

Platform is a circle of radius R centered at (0, 0). A unit that is off the platform and not airborne is knocked out.
Knockback speed = base * (1 + damage%/100) / weight, decaying with friction.
"""
import math
import random
from dataclasses import dataclass, field

from .dsl import BrawlSpec


@dataclass
class Command:
    move: tuple[float, float] = (0.0, 0.0)
    press: str | None = None  # ability name to start
    release: bool = False  # release an active channel
    aim: tuple[float, float] | None = None  # world point; default = nearest enemy


@dataclass
class Unit:
    name: str
    team: str
    spec: object
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    dmg: float = 0.0
    alive: bool = True
    air_until: float = -1.0
    jump_ab: object = None
    stun_until: float = -1.0
    facing: tuple[float, float] = (1.0, 0.0)
    ready_at: dict = field(default_factory=dict)
    channel: dict | None = None
    last_hitter: str | None = None
    last_hit_t: float = -99.0
    kos: int = 0

    @property
    def pos(self):
        return (self.x, self.y)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _norm(v):
    n = math.hypot(*v)
    return (v[0] / n, v[1] / n) if n > 1e-9 else (0.0, 0.0)


def _seg_dist(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2))
    return _dist((a[0] + t * dx, a[1] + t * dy), p), t


def _seg_cross(p1, p2, q1, q2):
    """Fraction along p1->p2 where it crosses segment q1-q2, or None."""
    r = (p2[0] - p1[0], p2[1] - p1[1])
    s = (q2[0] - q1[0], q2[1] - q1[1])
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-12:
        return None
    qp = (q1[0] - p1[0], q1[1] - p1[1])
    t = (qp[0] * s[1] - qp[1] * s[0]) / den
    u = (qp[0] * r[1] - qp[1] * r[0]) / den
    return t if 0 <= t <= 1 and 0 <= u <= 1 else None


def with_seed(spec: BrawlSpec, offset: int) -> BrawlSpec:
    return spec.model_copy(update={"game": spec.game.model_copy(update={"seed": spec.game.seed + offset})})


@dataclass
class Result:
    outcome: str  # winning team name, or "draw"
    time: float
    ticks: int
    events: list
    frames: list
    units: dict

    def summary(self) -> dict:
        hits, used = {}, {}
        for e in self.events:
            if e["type"] == "hit":
                hits.setdefault(e["src"], {}).setdefault(e["ability"], 0)
                hits[e["src"]][e["ability"]] += 1
            if e["type"] == "ability":
                used.setdefault(e["src"], {}).setdefault(e["ability"], 0)
                used[e["src"]][e["ability"]] += 1
        return {
            "outcome": self.outcome,
            "time": round(self.time, 2),
            "knockouts": [f"{e['src']} by {e.get('by') or 'self'} at {e['t']}s ({e['dmg']:.0f}%)" for e in self.events if e["type"] == "ko"],
            "final_damage_pct": {n: round(u["dmg"], 1) for n, u in self.units.items()},
            "abilities_used": used,
            "hits_landed": hits,
            "events": {t: sum(1 for e in self.events if e["type"] == t) for t in sorted({e["type"] for e in self.events})},
        }


class Sim:
    def __init__(self, spec: BrawlSpec, policies=None, record_frames=True):
        from .bots import bot

        self.spec, self.g = spec, spec.game
        self.R = self.g.platform.radius
        self.dt = 1.0 / self.g.tick_rate
        self.rng = random.Random(self.g.seed)
        self.t, self.tick = 0.0, 0
        self.events, self.frames = [], []
        self.record = record_frames
        self.policies = policies or {}
        self.default_policy = bot
        self.walls, self.rocks, self.shots, self.pending = [], [], [], []
        chars = {c.name: c for c in spec.characters}
        self.units: list[Unit] = []
        teams = list(spec.teams)
        for ti, team in enumerate(teams):
            ang = math.pi + 2 * math.pi * ti / len(teams)
            members = spec.teams[team]
            for mi, m in enumerate(members):
                c = chars[m.character]
                if m.spawn:
                    x, y = m.spawn
                else:
                    spread = (mi - (len(members) - 1) / 2) * 2.0
                    r = self.R * 0.55
                    x = r * math.cos(ang) - spread * math.sin(ang)
                    y = r * math.sin(ang) + spread * math.cos(ang)
                same = sum(1 for mm in members if mm.character == m.character)
                name = f"{team}_{c.name}" + (f"_{mi + 1}" if same > 1 else "")
                self.units.append(Unit(name, team, c, x, y, facing=_norm((-x, -y)) or (1.0, 0.0)))
        for u in self.units:
            self.emit("spawn", src=u.name, team=u.team, x=round(u.x, 2), y=round(u.y, 2))

    # -- helpers ---------------------------------------------------------------
    def emit(self, type_, **kw):
        self.events.append({"t": round(self.t, 3), "type": type_, **kw})

    def airborne(self, u):
        return self.t < u.air_until

    def enemies(self, u):
        return [v for v in self.units if v.alive and v.team != u.team]

    def ready(self, u, ab):
        return self.t + 1e-9 >= u.ready_at.get(ab.name, 0.0)

    def nearest_enemy(self, u):
        es = self.enemies(u)
        return min(es, key=lambda v: (_dist(u.pos, v.pos), v.name)) if es else None

    def hit(self, src: Unit, dst: Unit, ability: str, damage: float, kb: float, direction, kind="strike"):
        if not dst.alive or (self.airborne(dst) and kind != "projectile"):
            return False
        if dst.channel and dst.channel["ab"].interruptible:
            self.emit("channel_interrupt", src=dst.name, ability=dst.channel["ab"].name, by=src.name)
            dst.ready_at[dst.channel["ab"].name] = self.t + dst.channel["ab"].cooldown
            dst.channel = None
        dst.dmg += damage
        mag = kb * (1 + dst.dmg / 100) / dst.spec.weight
        d = _norm(direction) if direction != (0, 0) else _norm((dst.x - src.x, dst.y - src.y))
        dst.vx += d[0] * mag
        dst.vy += d[1] * mag
        if mag > 0:
            dst.stun_until = max(dst.stun_until, self.t + 0.1 + 0.012 * mag)
        dst.last_hitter, dst.last_hit_t = src.name, self.t
        self.emit("hit", src=src.name, dst=dst.name, ability=ability, damage=round(damage, 3), knockback=round(mag, 3), dmg_after=round(dst.dmg, 2))
        return True

    def area(self, src: Unit, center, radius, ability, damage, kb, front=None, kind="strike"):
        n = 0
        for v in sorted(self.enemies(src), key=lambda v: v.name):
            dvec = (v.x - center[0], v.y - center[1])
            if math.hypot(*dvec) > radius + v.spec.radius:
                continue
            if front is not None and (dvec[0] * front[0] + dvec[1] * front[1]) < -v.spec.radius:
                continue
            n += self.hit(src, v, ability, damage, kb, dvec if dvec != (0, 0) else (front or (1, 0)), kind)
        return n

    # -- abilities -------------------------------------------------------------
    def start(self, u: Unit, ab, aim):
        d = _norm((aim[0] - u.x, aim[1] - u.y)) if aim else u.facing
        if d == (0.0, 0.0):
            d = u.facing
        u.facing = d
        self.emit("ability", src=u.name, ability=ab.name, kind=ab.kind, button=ab.button)
        if ab.kind == "channel":
            u.channel = {"ab": ab, "start": self.t, "dir": d}
            self.emit("channel_start", src=u.name, ability=ab.name)
            return  # cooldown starts on release
        u.ready_at[ab.name] = self.t + ab.cooldown
        if ab.kind == "strike":
            if ab.windup > 0:
                self.pending.append((self.t + ab.windup, u.name, ab, d))
            else:
                self.area(u, (u.x + d[0] * u.spec.radius, u.y + d[1] * u.spec.radius), ab.range, ab.name, ab.damage, ab.knockback, front=d)
        elif ab.kind == "projectile":
            self.shots.append({"owner": u.name, "team": u.team, "ab": ab, "x": u.x, "y": u.y, "vx": d[0] * ab.speed, "vy": d[1] * ab.speed, "left": ab.range})
        elif ab.kind == "dash":
            a0 = u.pos
            u.x += d[0] * ab.distance
            u.y += d[1] * ab.distance
            self.block_by_walls(u, a0)
            if ab.damage or ab.knockback:
                for v in sorted(self.enemies(u), key=lambda v: v.name):
                    if _seg_dist(a0, u.pos, v.pos)[0] <= u.spec.radius + v.spec.radius:
                        self.hit(u, v, ab.name, ab.damage, ab.knockback, d)
        elif ab.kind == "jump":
            u.air_until = self.t + ab.duration
            u.jump_ab = ab
            self.emit("jump", src=u.name, ability=ab.name)
        elif ab.kind == "wall":
            c = (u.x + d[0] * ab.distance, u.y + d[1] * ab.distance)
            p = (-d[1], d[0])
            a = (c[0] + p[0] * ab.length / 2, c[1] + p[1] * ab.length / 2)
            b = (c[0] - p[0] * ab.length / 2, c[1] - p[1] * ab.length / 2)
            self.walls.append({"owner": u.name, "a": a, "b": b, "until": self.t + ab.duration})
            self.emit("wall", src=u.name, ability=ab.name, a=[round(a[0], 2), round(a[1], 2)], b=[round(b[0], 2), round(b[1], 2)])
        elif ab.kind == "rocks":
            for k in range(ab.count):
                self.rocks.append({"owner": u.name, "team": u.team, "ab": ab, "angle": 2 * math.pi * k / ab.count, "until": self.t + ab.duration, "x": u.x, "y": u.y})
            self.emit("rocks", src=u.name, ability=ab.name, count=ab.count)

    def release(self, u: Unit):
        ch = u.channel
        ab, held = ch["ab"], self.t - ch["start"]
        u.channel = None
        u.ready_at[ab.name] = self.t + ab.cooldown
        if held < ab.min_time:
            self.emit("channel_fizzle", src=u.name, ability=ab.name, held=round(held, 3))
            return
        f = max(0.0, min(1.0, (held - ab.min_time) / (ab.max_time - ab.min_time)))
        dmg = ab.damage_min + f * (ab.damage_max - ab.damage_min)
        kb = ab.knockback_min + f * (ab.knockback_max - ab.knockback_min)
        self.emit("channel_release", src=u.name, ability=ab.name, held=round(held, 3), power=round(f, 3))
        if ab.release == "shockwave":
            self.area(u, u.pos, ab.radius, ab.name, dmg, kb)
        else:
            d = ch["dir"]
            self.area(u, (u.x + d[0] * u.spec.radius, u.y + d[1] * u.spec.radius), ab.radius, ab.name, dmg, kb, front=d)

    # -- physics ---------------------------------------------------------------
    def block_by_walls(self, u: Unit, prev):
        for w in self.walls:
            if _seg_cross(prev, u.pos, w["a"], w["b"]) is not None or _seg_dist(w["a"], w["b"], u.pos)[0] < u.spec.radius:
                d, t = _seg_dist(w["a"], w["b"], prev)
                cp = (w["a"][0] + t * (w["b"][0] - w["a"][0]), w["a"][1] + t * (w["b"][1] - w["a"][1]))
                n = _norm((prev[0] - cp[0], prev[1] - cp[1])) or (1.0, 0.0)
                u.x, u.y = cp[0] + n[0] * (u.spec.radius + 0.01), cp[1] + n[1] * (u.spec.radius + 0.01)
                into = u.vx * n[0] + u.vy * n[1]
                if into < 0:  # a wall stops knockback that drives into it
                    if math.hypot(u.vx, u.vy) > 3:
                        self.emit("wall_block", src=u.name, wall_owner=w["owner"], speed=round(math.hypot(u.vx, u.vy), 2))
                    u.vx -= into * n[0]
                    u.vy -= into * n[1]

    def step_unit(self, u: Unit):
        pol = self.policies.get(u.team, self.default_policy)
        cmd = pol(self.observe(u))
        stunned = self.t < u.stun_until
        if u.channel:
            ab = u.channel["ab"]
            if cmd.release or self.t - u.channel["start"] >= ab.max_time + 1.0:
                self.release(u)
        elif cmd.press and not stunned:
            ab = next((a for a in u.spec.abilities if a.name == cmd.press), None)
            if ab and self.ready(u, ab) and not (ab.kind == "jump" and self.airborne(u)):
                tgt = self.nearest_enemy(u)
                self.start(u, ab, cmd.aim or (tgt.pos if tgt else None))
        mv = _norm(cmd.move)
        sp = u.spec.speed
        if stunned:
            sp = 0.0
        elif u.channel:
            sp *= 0.0 if u.channel["ab"].rooted else u.channel["ab"].move_speed
        elif self.airborne(u):
            sp *= u.jump_ab.air_speed
        if mv != (0.0, 0.0) and not u.channel:
            u.facing = mv
        prev = u.pos
        u.x += (mv[0] * sp + u.vx) * self.dt
        u.y += (mv[1] * sp + u.vy) * self.dt
        decay = math.exp(-self.g.friction * self.dt)
        u.vx *= decay
        u.vy *= decay
        self.block_by_walls(u, prev)

    def step_rocks(self):
        keep = []
        for r in self.rocks:
            o = next(u for u in self.units if u.name == r["owner"])
            if self.t >= r["until"] or not o.alive:
                continue
            r["angle"] += r["ab"].orbit_speed * self.dt
            r["x"] = o.x + math.cos(r["angle"]) * r["ab"].orbit_radius
            r["y"] = o.y + math.sin(r["angle"]) * r["ab"].orbit_radius
            hit = False
            for v in sorted(self.enemies(o), key=lambda v: v.name):
                if not self.airborne(v) and _dist((r["x"], r["y"]), v.pos) <= r["ab"].rock_radius + v.spec.radius:
                    hit = self.hit(o, v, r["ab"].name, r["ab"].damage, r["ab"].knockback, (v.x - o.x, v.y - o.y))
                    break
            if not hit:
                keep.append(r)
        self.rocks = keep

    def step_shots(self):
        keep = []
        for s in self.shots:
            p0 = (s["x"], s["y"])
            s["x"] += s["vx"] * self.dt
            s["y"] += s["vy"] * self.dt
            s["left"] -= math.hypot(s["vx"], s["vy"]) * self.dt
            p1 = (s["x"], s["y"])
            owner = next(u for u in self.units if u.name == s["owner"])
            cands = []
            for w in self.walls:
                t = _seg_cross(p0, p1, w["a"], w["b"])
                if t is not None:
                    cands.append((t, 0, "wall", w))
            for i, r in enumerate(self.rocks):
                if r["team"] != s["team"]:
                    d, t = _seg_dist(p0, p1, (r["x"], r["y"]))
                    if d <= s["ab"].radius + r["ab"].rock_radius:
                        cands.append((t, 1, "rock", i))
            for v in self.units:
                if v.alive and v.team != s["team"]:
                    d, t = _seg_dist(p0, p1, v.pos)
                    if d <= s["ab"].radius + v.spec.radius:
                        cands.append((t, 2, "unit", v))
            if cands:
                _, _, what, obj = min(cands, key=lambda c: (c[0], c[1]))
                if what == "wall":
                    self.emit("blocked", src=s["owner"], ability=s["ab"].name, by="wall", wall_owner=obj["owner"])
                elif what == "rock":
                    self.emit("blocked", src=s["owner"], ability=s["ab"].name, by="rock", rock_owner=self.rocks[obj]["owner"])
                    self.rocks.pop(obj)
                else:
                    self.hit(owner, obj, s["ab"].name, s["ab"].damage, s["ab"].knockback, (s["vx"], s["vy"]), kind="projectile")
            elif s["left"] > 0 and math.hypot(*p1) < self.R + 15:
                keep.append(s)
        self.shots = keep

    # -- observation -------------------------------------------------------------
    def observe(self, u: Unit) -> dict:
        return {
            "t": self.t, "tick": self.tick, "seed": self.g.seed, "R": self.R,
            "self": {"name": u.name, "team": u.team, "pos": u.pos, "vel": (u.vx, u.vy), "dmg": u.dmg, "weight": u.spec.weight,
                     "radius": u.spec.radius, "airborne": self.airborne(u), "stunned": self.t < u.stun_until,
                     "channel": None if not u.channel else {"ability": u.channel["ab"].name, "held": self.t - u.channel["start"],
                                                            "max_time": u.channel["ab"].max_time, "radius": u.channel["ab"].radius}},
            "abilities": {a.name: {**a.model_dump(), "ready": self.ready(u, a)} for a in u.spec.abilities},
            "enemies": [{"name": v.name, "pos": v.pos, "dist": _dist(u.pos, v.pos), "dmg": v.dmg, "airborne": self.airborne(v),
                         "channeling": v.channel is not None, "radius": v.spec.radius} for v in self.enemies(u)],
            "incoming": [{"pos": (s["x"], s["y"]), "vel": (s["vx"], s["vy"])} for s in self.shots if s["team"] != u.team],
        }

    # -- main loop ---------------------------------------------------------------
    def frame(self):
        return {"t": round(self.t, 3),
                "units": [[u.name, u.team, round(u.x, 2), round(u.y, 2), round(u.dmg, 1), u.alive, self.airborne(u), u.channel is not None, u.spec.radius] for u in self.units],
                "walls": [[w["a"], w["b"]] for w in self.walls],
                "rocks": [[round(r["x"], 2), round(r["y"], 2), r["ab"].rock_radius] for r in self.rocks],
                "shots": [[round(s["x"], 2), round(s["y"], 2), s["ab"].radius] for s in self.shots]}

    def step(self):
        for u in [u for u in self.units if u.alive]:
            was_air = self.airborne(u)
            self.step_unit(u)
            if was_air and not self.airborne(u):
                ab = u.jump_ab
                self.emit("land", src=u.name)
                if ab and ab.landing_radius > 0:
                    self.area(u, u.pos, ab.landing_radius, ab.name, ab.landing_damage, ab.landing_knockback)
        due = [p for p in self.pending if p[0] <= self.t + 1e-9]
        self.pending = [p for p in self.pending if p[0] > self.t + 1e-9]
        for _, name, ab, d in due:
            u = next(x for x in self.units if x.name == name)
            if u.alive:
                self.area(u, (u.x + d[0] * u.spec.radius, u.y + d[1] * u.spec.radius), ab.range, ab.name, ab.damage, ab.knockback, front=d)
        self.step_rocks()
        self.step_shots()
        self.walls = [w for w in self.walls if self.t < w["until"]]
        for u in self.units:
            if u.alive and not self.airborne(u) and math.hypot(u.x, u.y) > self.R:
                u.alive = False
                by = u.last_hitter if self.t - u.last_hit_t < 3.0 else None
                if by:
                    next(v for v in self.units if v.name == by).kos += 1
                self.emit("ko", src=u.name, team=u.team, by=by, dmg=round(u.dmg, 2), x=round(u.x, 2), y=round(u.y, 2))
                u.channel = None
        self.t += self.dt
        self.tick += 1
        if self.record:
            self.frames.append(self.frame())
        alive_teams = sorted({u.team for u in self.units if u.alive})
        if len(alive_teams) == 1:
            return alive_teams[0]
        if not alive_teams:
            return "draw"
        if self.t >= self.g.max_seconds - 1e-9:
            counts = {t: sum(u.alive for u in self.units if u.team == t) for t in alive_teams}
            best = max(counts.values())
            leaders = [t for t, c in counts.items() if c == best]
            return leaders[0] if len(leaders) == 1 else "draw"
        return None

    def run(self) -> Result:
        if self.record:
            self.frames.append(self.frame())
        out = None
        while out is None:
            out = self.step()
        self.emit("end", outcome=out)
        return Result(out, self.t, self.tick, self.events, self.frames,
                      {u.name: {"team": u.team, "dmg": u.dmg, "alive": u.alive, "kos": u.kos} for u in self.units})


def simulate(spec: BrawlSpec, **kw) -> Result:
    return Sim(spec, **kw).run()
