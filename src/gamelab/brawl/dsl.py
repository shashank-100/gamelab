"""Brawler DSL (v2): ring-out arena fights between teams of characters.

Damage is a percentage that never kills by itself; it makes you fly farther when hit.
A unit is knocked out when it ends up off the platform while not airborne. Last team standing wins.
"""
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..dsl import Report, _loc

Button = Literal["a", "b", "a+b"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Platform(Strict):
    radius: float = Field(12, gt=3, le=60)


class Game(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    platform: Platform = Platform()
    tick_rate: int = Field(30, ge=10, le=120)
    max_seconds: float = Field(90, gt=0, le=600)
    friction: float = Field(4.0, gt=0, le=30, description="how fast knockback velocity decays (1/s)")
    seed: int = 0


class _Ability(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    button: Button
    cooldown: float = Field(ge=0, le=60)


class Strike(_Ability):
    kind: Literal["strike"]
    damage: float = Field(ge=0, le=100)
    knockback: float = Field(ge=0, le=100, description="base launch speed; grows with the target's damage %, shrinks with its weight")
    range: float = Field(1.5, gt=0, le=10)
    windup: float = Field(0, ge=0, le=3, description="delay before the hit lands")


class Projectile(_Ability):
    kind: Literal["projectile"]
    damage: float = Field(ge=0, le=100)
    knockback: float = Field(ge=0, le=100)
    speed: float = Field(18, gt=0, le=120)
    range: float = Field(12, gt=0, le=100)
    radius: float = Field(0.4, gt=0, le=5)


class Dash(_Ability):
    kind: Literal["dash"]
    distance: float = Field(gt=0, le=30)
    damage: float = Field(0, ge=0, le=100, description="dealt to enemies you dash through")
    knockback: float = Field(0, ge=0, le=100)


class Jump(_Ability):
    kind: Literal["jump"]
    duration: float = Field(gt=0, le=5, description="seconds airborne: can't fall off, can't be struck")
    air_speed: float = Field(1.0, gt=0, le=4, description="movement speed multiplier while airborne")
    landing_radius: float = Field(0, ge=0, le=15, description="shockwave on landing (0 = none)")
    landing_damage: float = Field(0, ge=0, le=100)
    landing_knockback: float = Field(0, ge=0, le=100)


class Wall(_Ability):
    kind: Literal["wall"]
    length: float = Field(gt=0, le=20)
    duration: float = Field(gt=0, le=30)
    distance: float = Field(1.5, ge=0, le=10, description="how far in front of the caster it rises")


class Rocks(_Ability):
    kind: Literal["rocks"]
    count: int = Field(ge=1, le=12)
    orbit_radius: float = Field(2.0, gt=0, le=10)
    orbit_speed: float = Field(3.0, ge=0, le=20, description="radians per second")
    duration: float = Field(gt=0, le=30)
    damage: float = Field(ge=0, le=100)
    knockback: float = Field(ge=0, le=100)
    rock_radius: float = Field(0.5, gt=0, le=3)


class Channel(_Ability):
    kind: Literal["channel"]
    min_time: float = Field(0.2, ge=0, le=10)
    max_time: float = Field(gt=0, le=10, description="power is full after this long")
    rooted: bool = Field(True, description="true = cannot move while channeling; false = charge while moving slowly")
    move_speed: float = Field(0.4, ge=0, le=1, description="speed multiplier while channeling if not rooted")
    interruptible: bool = Field(True, description="getting hit cancels the channel")
    release: Literal["shockwave", "strike"] = "shockwave"
    radius: float = Field(3, gt=0, le=20, description="shockwave radius, or strike range")
    damage_min: float = Field(ge=0, le=100)
    damage_max: float = Field(ge=0, le=200)
    knockback_min: float = Field(ge=0, le=100)
    knockback_max: float = Field(ge=0, le=200)

    @model_validator(mode="after")
    def _ranges(self):
        if self.min_time >= self.max_time:
            raise ValueError("min_time must be less than max_time")
        if self.damage_min > self.damage_max or self.knockback_min > self.knockback_max:
            raise ValueError("the _min values must not exceed the _max values")
        return self


Ability = Annotated[Union[Strike, Projectile, Dash, Jump, Wall, Rocks, Channel], Field(discriminator="kind")]


class Character(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    weight: float = Field(1.0, gt=0.2, le=5, description="heavier = less knockback, slower to start moving")
    speed: float = Field(gt=0, le=20)
    radius: float = Field(0.6, gt=0.1, le=3)
    abilities: list[Ability] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _buttons(self):
        btn = [a.button for a in self.abilities]
        dup = sorted({b for b in btn if btn.count(b) > 1})
        if dup:
            raise ValueError(f"character `{self.name}` binds more than one ability to button {', '.join(dup)}; each of a, b, a+b holds one ability")
        names = [a.name for a in self.abilities]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"character `{self.name}` has duplicate ability names: {', '.join(dup)}")
        return self


class Member(Strict):
    character: str
    spawn: tuple[float, float] | None = Field(None, description="offset from the platform center")


class BrawlSpec(Strict):
    game: Game
    characters: list[Character] = Field(min_length=1)
    teams: dict[str, list[Member]] = Field(min_length=2)

    @model_validator(mode="after")
    def _semantics(self):
        names = [c.name for c in self.characters]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"character names must be unique; duplicated: {', '.join(dup)}")
        for team, members in self.teams.items():
            if not members:
                raise ValueError(f"team `{team}` has no members")
            for i, m in enumerate(members):
                if m.character not in names:
                    raise ValueError(f"teams.{team}[{i}].character `{m.character}` is not defined; characters: {', '.join(names)}")
                if m.spawn and (m.spawn[0] ** 2 + m.spawn[1] ** 2) ** 0.5 > self.game.platform.radius:
                    raise ValueError(f"teams.{team}[{i}].spawn {list(m.spawn)} is off the platform (radius {self.game.platform.radius})")
        return self


def _warnings(spec: BrawlSpec) -> list[str]:
    w = []
    for c in spec.characters:
        if not any(getattr(a, "knockback", 0) or getattr(a, "knockback_max", 0) or getattr(a, "landing_knockback", 0) for a in c.abilities):
            w.append(f"character `{c.name}` has no knockback, so it can never knock anyone off")
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
        return Report(False, ["top level must be a mapping with keys game, characters, teams"])
    try:
        spec = BrawlSpec.model_validate(data)
    except ValidationError as e:
        errs = []
        for err in e.errors():
            msg = err["msg"].removeprefix("Value error, ")
            loc = _loc([p for p in err["loc"] if p not in ("strike", "projectile", "dash", "jump", "wall", "rocks", "channel")])
            if err["type"] == "extra_forbidden":
                msg = "unknown field (typo, or not supported by the DSL)"
            elif err["type"] == "union_tag_invalid":
                msg = "unknown ability kind; allowed: strike, projectile, dash, jump, wall, rocks, channel"
            elif err["type"] == "literal_error":
                msg = f"{msg}; got `{err.get('input')}`"
            errs.append(f"{loc}: {msg}")
        return Report(False, errs)
    return Report(True, warnings=_warnings(spec), spec=spec)
