"""Condition mini-language for behavior rules: `health < 30% and distance_to_player <= 8`.

Parsed into a tree once at validation time; never eval'd.
"""
import operator
import re
from dataclasses import dataclass

VARIABLES = {
    "health": "own health; `%` means fraction of max health",
    "player_health": "player health; `%` means fraction of the player's max health",
    "distance_to_player": "distance in arena units",
    "time": "seconds since the game started",
    "enemies_alive": "number of enemies still alive",
}
PERCENT_VARS = {"health", "player_health"}
OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge, "==": operator.eq, "!=": operator.ne}
CLAUSE = re.compile(r"^\s*([a-z_]+)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)\s*(%?)\s*$")


class ConditionError(ValueError):
    pass


@dataclass(frozen=True)
class Clause:
    var: str
    op: str
    value: float
    percent: bool

    def holds(self, ctx: dict) -> bool:
        lhs = ctx[self.var]
        if self.percent:
            lhs = ctx[self.var] / ctx[f"{self.var}_max"] * 100
        return OPS[self.op](lhs, self.value)


@dataclass(frozen=True)
class Condition:
    source: str
    any_of: tuple  # OR of AND-groups: ((Clause, ...), ...)

    def holds(self, ctx: dict) -> bool:
        return any(all(c.holds(ctx) for c in group) for group in self.any_of)


def parse(source: str) -> Condition:
    if not isinstance(source, str) or not source.strip():
        raise ConditionError("condition is empty")
    groups = []
    for group_src in re.split(r"\s+or\s+", source.strip()):
        clauses = []
        for clause_src in re.split(r"\s+and\s+", group_src):
            m = CLAUSE.match(clause_src)
            if not m:
                raise ConditionError(f"cannot parse `{clause_src.strip()}`; expected `<variable> <op> <number>[%]`, e.g. `health < 30%`")
            var, op, num, pct = m.groups()
            if var not in VARIABLES:
                raise ConditionError(f"unknown variable `{var}`; known: {', '.join(VARIABLES)}")
            if pct and var not in PERCENT_VARS:
                raise ConditionError(f"`%` only applies to {', '.join(sorted(PERCENT_VARS))}, not `{var}`")
            clauses.append(Clause(var, op, float(num), bool(pct)))
        groups.append(tuple(clauses))
    return Condition(source.strip(), tuple(groups))
