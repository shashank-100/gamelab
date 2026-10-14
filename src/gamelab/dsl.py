"""Typed game DSL. A game file is YAML that must validate against `GameSpec`.

`validate()` never raises: it returns a report whose messages are written for an
agent to act on (path to the bad field + what is allowed).
"""
from dataclasses import dataclass, field
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from . import conditions

Pos = tuple[float, float]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Arena(Strict):
    width: float = Field(40, gt=4, le=500)
    height: float = Field(30, gt=4, le=500)


class Game(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    arena: Arena = Arena()
    gravity: float = Field(0, ge=0, le=50, description="downward pull on projectiles, units/s^2; 0 = top-down")
    tick_rate: int = Field(20, ge=5, le=120)
    max_seconds: float = Field(60, gt=0, le=600)
    seed: int = 0


class Dash(Strict):
    name: str
    type: Literal["dash"]
    cooldown: float = Field(ge=0)
    distance: float = Field(gt=0, le=100)


class Projectile(Strict):
    name: str
    type: Literal["projectile"]
    cooldown: float = Field(ge=0)
    damage: float = Field(gt=0)
    range: float = Field(gt=0, le=200)
    projectile_speed: float = Field(20, gt=0, le=200)
    radius: float = Field(0.5, gt=0, le=10)


class Melee(Strict):
    name: str
    type: Literal["melee"]
    cooldown: float = Field(ge=0)
    damage: float = Field(gt=0)
    range: float = Field(1.5, gt=0, le=10)


class Heal(Strict):
    name: str
    type: Literal["heal"]
    cooldown: float = Field(ge=0)
    amount: float = Field(gt=0)


class Aoe(Strict):
    name: str
    type: Literal["aoe"]
    cooldown: float = Field(ge=0)
    damage: float = Field(gt=0)
    radius: float = Field(gt=0, le=30)


Ability = Annotated[Union[Dash, Projectile, Melee, Heal, Aoe], Field(discriminator="type")]


class Character(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    health: float = Field(gt=0, le=100000)
    speed: float = Field(gt=0, le=50)
    spawn: Pos | None = None
    radius: float = Field(0.5, gt=0, le=5)
    abilities: list[Ability] = []


class Attack(Strict):
    damage: float = Field(gt=0)
    range: float = Field(1.5, gt=0, le=20)
    cooldown: float = Field(1, ge=0)


Action = Literal["chase", "flee", "idle", "teleport", "enrage", "heal", "summon"]
MOVE_ACTIONS = {"chase", "flee", "idle"}


class Rule(Strict):
    when: str
    action: Action
    once: bool = False
    cooldown: float = Field(0, ge=0)
    amount: float | None = Field(None, gt=0, description="heal: health restored; enrage: damage multiplier")
    unit: str | None = Field(None, description="summon: name of a minion enemy to spawn")
    count: int = Field(1, ge=1, le=20, description="summon: how many")

    @field_validator("when")
    @classmethod
    def _parse_when(cls, v):
        conditions.parse(v)
        return v


class Enemy(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    health: float = Field(gt=0, le=100000)
    speed: float = Field(gt=0, le=50)
    spawn: Pos | None = None
    radius: float = Field(0.6, gt=0, le=5)
    attack: Attack | None = None
    behavior: list[Rule] = []
    minion: bool = Field(False, description="true = not present at start; only appears via a summon rule")


class GameSpec(Strict):
    game: Game
    characters: list[Character] = Field(min_length=1)
    enemies: list[Enemy] = Field(min_length=1)

    @model_validator(mode="after")
    def _semantics(self):
        names = [c.name for c in self.characters] + [e.name for e in self.enemies]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"names must be unique across characters and enemies; duplicated: {', '.join(dup)}")
        a = self.game.arena
        for kind, items in (("characters", self.characters), ("enemies", self.enemies)):
            for i, it in enumerate(items):
                if it.spawn and not (0 <= it.spawn[0] <= a.width and 0 <= it.spawn[1] <= a.height):
                    raise ValueError(f"{kind}[{i}] ({it.name}).spawn {list(it.spawn)} is outside the arena 0..{a.width} x 0..{a.height}")
        minions = {e.name for e in self.enemies if e.minion}
        if len(minions) == len(self.enemies):
            raise ValueError("at least one enemy must have minion: false (otherwise nothing spawns at start)")
        for i, e in enumerate(self.enemies):
            for j, r in enumerate(e.behavior):
                if r.action == "summon" and r.unit not in minions:
                    raise ValueError(f"enemies[{i}].behavior[{j}]: summon needs `unit:` naming an enemy with `minion: true`; minions defined: {sorted(minions) or 'none'}")
                if r.action != "summon" and r.unit is not None:
                    raise ValueError(f"enemies[{i}].behavior[{j}]: `unit` only applies to action summon")
        for i, c in enumerate(self.characters):
            an = [ab.name for ab in c.abilities]
            dup = sorted({n for n in an if an.count(n) > 1})
            if dup:
                raise ValueError(f"characters[{i}] ({c.name}) has duplicate ability names: {', '.join(dup)}")
        return self


@dataclass
class Report:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    spec: GameSpec | None = None

    def text(self) -> str:
        if self.ok:
            lines = ["OK"] + [f"warning: {w}" for w in self.warnings]
        else:
            lines = [f"INVALID ({len(self.errors)} error{'s' * (len(self.errors) != 1)})"] + [f"error: {e}" for e in self.errors]
        return "\n".join(lines)


def _loc(loc) -> str:
    out = ""
    for p in loc:
        if isinstance(p, int):
            out += f"[{p}]"
        elif p in ("dash", "projectile", "melee", "heal", "aoe", "function-after[_parse_when(), str]"):
            continue
        else:
            out += f".{p}" if out else str(p)
    return out or "(root)"


def _warnings(spec: GameSpec) -> list[str]:
    w = []
    for e in spec.enemies:
        if e.attack is None:
            w.append(f"enemy `{e.name}` has no attack, so it can never damage the player")
        for r in e.behavior:
            if r.action in ("heal", "enrage") and r.amount is None:
                w.append(f"enemy `{e.name}` rule `{r.when} -> {r.action}` has no amount; default {'20' if r.action == 'heal' else '1.5x'} is used")
            if not r.once and r.cooldown == 0 and r.action in ("teleport", "heal", "enrage", "summon"):
                w.append(f"enemy `{e.name}` rule `{r.when} -> {r.action}` can fire every tick; add `once: true` or a cooldown")
    for c in spec.characters:
        if not any(a.type in ("projectile", "melee", "aoe") for a in c.abilities):
            w.append(f"character `{c.name}` has no damaging ability, so it cannot win")
    return w


def validate(source: str | dict) -> Report:
    if isinstance(source, str):
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as e:
            return Report(False, [f"YAML syntax: {e}"])
    else:
        data = source
    if not isinstance(data, dict):
        return Report(False, ["top level must be a mapping with keys game, characters, enemies"])
    try:
        spec = GameSpec.model_validate(data)
    except ValidationError as e:
        errs = []
        for err in e.errors():
            msg = err["msg"].removeprefix("Value error, ")
            if err["type"] == "extra_forbidden":
                msg = "unknown field (typo, or not supported by the DSL)"
            elif err["type"] == "union_tag_invalid":
                msg = f"unknown ability type; allowed: dash, projectile, melee, heal, aoe"
            elif err["type"] == "literal_error":
                msg = f"{msg}; got `{err.get('input')}`"
            errs.append(f"{_loc(err['loc'])}: {msg}")
        return Report(False, errs)
    return Report(True, warnings=_warnings(spec), spec=spec)


def load(path: str) -> GameSpec:
    rep = validate(open(path).read())
    if not rep.ok:
        raise SystemExit(f"{path}: {rep.text()}")
    return rep.spec
