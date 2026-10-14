"""Procedural edit tasks + reward for RL on the edit language (Experiment D).

Each task: a designer request, a starting game, the paths it may touch, and checks on the edited game.
Reward: 1.0 = valid, all checks pass, no out-of-scope change; 0.25 * fraction of checks for valid-but-wrong;
0 for a valid edit that also changed something it shouldn't; -0.5 for unparseable edits or invalid games.
"""
import copy
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .. import engines
from ..bench import _allowed, diff, get_path
from . import edits

GAMES = Path(__file__).resolve().parents[3] / "games"
BASES = {g: yaml.safe_load(open(GAMES / f"{g}.yaml")) for g in ("arena", "swarm", "pro_bending")}
FULL = {g: engines.validate(d).spec.model_dump() for g, d in BASES.items()}  # with defaults filled in


@dataclass
class EditTask:
    prompt: str
    base: str
    allowed: list
    checks: list  # (path, expected) or (path, "missing") or ("any", list_path, {field: value})
    start: dict = field(repr=False, default=None)
    solution: str = ""  # a reference edit that earns reward 1.0

    def game_yaml(self):
        return yaml.safe_dump(self.start, sort_keys=False, default_flow_style=None, width=120)


def reward(task: EditTask, completion: str) -> tuple[float, str]:
    try:
        new = edits.apply(task.start, completion)
    except edits.EditError as e:
        return -0.5, f"edit error: {e}"
    rep = engines.validate(new)
    if not rep.ok:
        return -0.5, f"invalid game: {rep.errors[0]}"
    now, start = rep.spec.model_dump(), engines.validate(task.start).spec.model_dump()
    ok = 0
    for c in task.checks:
        try:
            if c[0] == "any":
                items = get_path(now, c[1])
                ok += any(all(_eq(it.get(k), v) for k, v in c[2].items()) for it in items)
            elif c[1] == "missing":
                try:
                    get_path(now, c[0])
                except KeyError:
                    ok += 1
            else:
                ok += _eq(get_path(now, c[0]), c[1])
        except KeyError:
            pass
    oos = [p for p in diff(start, now) if not _allowed(p, task.allowed)]
    if ok == len(task.checks):
        return (1.0, "correct") if not oos else (0.0, f"correct but out of scope: {oos[:3]}")
    return 0.25 * ok / len(task.checks), f"{ok}/{len(task.checks)} checks"


def _eq(a, b):
    if isinstance(b, float) or isinstance(a, float):
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return False
    return a == b


# ------------------------------------------------------------------ generators
NUMERIC = {
    "arena": [("the warrior's health", "characters[warrior].health", 50, 300, int),
              ("the warrior's speed", "characters[warrior].speed", 2, 10, int),
              ("the fireball damage", "characters[warrior].abilities[fireball].damage", 5, 80, int),
              ("the fireball range", "characters[warrior].abilities[fireball].range", 6, 30, int),
              ("the dash cooldown", "characters[warrior].abilities[dash].cooldown", 1, 8, int),
              ("the dash distance", "characters[warrior].abilities[dash].distance", 3, 20, int),
              ("the slash damage", "characters[warrior].abilities[slash].damage", 4, 40, int),
              ("the boss's health", "enemies[boss].health", 100, 2000, int),
              ("the boss's speed", "enemies[boss].speed", 1, 8, int),
              ("the boss's attack damage", "enemies[boss].attack.damage", 2, 40, int),
              ("the fight time limit", "game.max_seconds", 30, 300, int)],
    "pro_bending": [("the wrestler's weight", "characters[wrestler].weight", 1, 5, float),
                    ("the ninja's speed", "characters[ninja].speed", 3, 12, int),
                    ("the grapple slam knockback", "characters[wrestler].abilities[grapple_slam].knockback", 3, 25, int),
                    ("the shuriken damage", "characters[ninja].abilities[shuriken].damage", 1, 15, int),
                    ("the rock wall length", "characters[wrestler].abilities[rock_wall].length", 2, 12, int),
                    ("the leap duration", "characters[ninja].abilities[leap].duration", 0.3, 2.0, float),
                    ("the platform radius", "game.platform.radius", 8, 25, int),
                    ("the floor friction", "game.friction", 1, 8, int)],
}
SET_PHRASES = ["Set {what} to {v}.", "Change {what} to {v}.", "Make {what} {v}.", "I want {what} at {v}.", "{What} should be {v}."]
REL_PHRASES = [("Make {what} {p}% higher.", 1), ("Increase {what} by {p}%.", 1), ("Lower {what} by {p}%.", -1), ("Cut {what} by {p}%.", -1)]


def _num(rng, lo, hi, kind):
    if kind is int:
        return rng.randint(int(lo), int(hi))
    return round(rng.uniform(lo, hi), 1)


def gen(rng: random.Random) -> EditTask:
    r = rng.random()
    if r < 0.35:  # absolute numeric
        base = rng.choice(list(NUMERIC))
        what, path, lo, hi, kind = rng.choice(NUMERIC[base])
        start = copy.deepcopy(BASES[base])
        cur = get_path(FULL[base], path)
        v = _num(rng, lo, hi, kind)
        while v == cur:
            v = _num(rng, lo, hi, kind)
        ph = rng.choice(SET_PHRASES)
        return EditTask(ph.format(what=what, What=what[0].upper() + what[1:], v=v), base, [path], [(path, v)], start, f"set {path} = {v}")
    if r < 0.55:  # relative numeric
        base = rng.choice(list(NUMERIC))
        what, path, lo, hi, kind = rng.choice(NUMERIC[base])
        start = copy.deepcopy(BASES[base])
        cur = get_path(FULL[base], path)
        p = rng.choice([10, 20, 25, 50, 100]) if True else 0
        ph, sign = rng.choice(REL_PHRASES)
        if sign < 0 and p >= 100:
            p = 50
        v = round(cur * (1 + sign * p / 100), 4)
        return EditTask(ph.format(what=what, p=p), base, [path], [(path, v)], start, f"set {path} = {v}")
    if r < 0.75:  # add a boss rule
        start = copy.deepcopy(BASES["arena"])
        pct = rng.choice([10, 20, 25, 30, 40, 50, 60, 75])
        act = rng.choice(["teleport", "enrage", "heal", "flee", "idle"])
        rule = {"when": f"health < {pct}%", "action": act}
        extra = ""
        if act in ("teleport", "heal", "enrage"):
            if rng.random() < 0.5:
                rule["once"] = True
                extra = ", only once"
            else:
                cd = rng.choice([2, 3, 5, 8, 10])
                rule["cooldown"] = cd
                extra = f", at most every {cd} seconds"
        if act == "enrage":
            m = rng.choice([1.25, 1.5, 2])
            rule["amount"] = m
            extra = f" (damage x{m})" + extra
        if act == "heal":
            h = rng.choice([20, 50, 100])
            rule["amount"] = h
            extra = f" for {h} health" + extra
        verb = {"teleport": "teleport away", "enrage": "enrage", "heal": "heal itself", "flee": "run away from the player", "idle": "stand still"}[act]
        prompt = f"When the boss is below {pct}% health it should {verb}{extra}."
        return EditTask(prompt, "arena", ["enemies[boss].behavior"], [("any", "enemies[boss].behavior", rule)], start,
                        f"add enemies[boss].behavior = {json.dumps(rule)}")
    if r < 0.88:  # add an ability
        start = copy.deepcopy(BASES["arena"])
        kind = rng.choice(["heal", "aoe", "dash"])
        name = rng.choice({"heal": ["mend", "second_wind", "bandage"], "aoe": ["ground_slam", "nova", "whirlwind"], "dash": ["blink", "lunge", "roll"]}[kind])
        cd = rng.choice([2, 4, 6, 10, 15])
        ab = {"name": name, "type": kind, "cooldown": cd}
        if kind == "heal":
            ab["amount"] = rng.choice([10, 25, 40])
            desc = f"a heal called `{name}` that restores {ab['amount']} health with a {cd}s cooldown"
        elif kind == "aoe":
            ab["damage"], ab["radius"] = rng.choice([10, 20, 30]), rng.choice([2, 3, 4, 5])
            desc = f"an area attack `{name}` hitting everything within {ab['radius']} units for {ab['damage']} damage, {cd}s cooldown"
        else:
            ab["distance"] = rng.choice([4, 6, 8])
            desc = f"a dash called `{name}` that moves {ab['distance']} units, {cd}s cooldown"
        checks = [(f"characters[warrior].abilities[{name}].{k}", v) for k, v in ab.items() if k != "name"]
        return EditTask(f"Give the warrior {desc}.", "arena", ["characters[warrior].abilities"], checks, start,
                        f"add characters[warrior].abilities = {json.dumps(ab)}")
    if r < 0.94:  # remove
        start = copy.deepcopy(BASES["arena"])
        n = rng.choice(["dash", "fireball", "slash"])
        return EditTask(f"Remove the warrior's {n} ability.", "arena", ["characters[warrior].abilities"],
                        [(f"characters[warrior].abilities[{n}]", "missing")], start, f"remove characters[warrior].abilities[{n}]")
    start = copy.deepcopy(BASES["pro_bending"])  # rebind a button
    abil = {"wrestler": ["grapple_slam", "rock_wall", "earthquake"], "ninja": ["shuriken", "shadow_step", "leap"]}
    who = rng.choice(list(abil))
    a1, a2 = rng.sample(abil[who], 2)
    b1 = get_path(FULL["pro_bending"], f"characters[{who}].abilities[{a1}].button")
    b2 = get_path(FULL["pro_bending"], f"characters[{who}].abilities[{a2}].button")
    return EditTask(f"Swap the {who}'s {a1} and {a2} buttons.", "pro_bending", [f"characters[{who}].abilities"],
                    [(f"characters[{who}].abilities[{a1}].button", b2), (f"characters[{who}].abilities[{a2}].button", b1)], start,
                    f"set characters[{who}].abilities[{a1}].button = {b2}\nset characters[{who}].abilities[{a2}].button = {b1}")


def dataset(n, seed):
    rng = random.Random(seed)
    return [gen(rng) for _ in range(n)]
