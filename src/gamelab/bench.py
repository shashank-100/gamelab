"""Benchmark tasks: a request in plain English + a starting game + automatic checks.

Task file (tasks/<id>/task.yaml):
    id, category, prompt, start (path to starting game, relative to the task dir)
    allowed_changes: spec paths the agent may modify; any other change is a regression
    checks: list of
      {kind: spec, path: "enemies[boss].health", op: "<=", value: 300}
      {kind: sim, seeds: 5, policy: auto, assert: [ ... ]}
    sim assertions (every seed must satisfy each one):
      {outcome: [win]}
      {count: summon, where: {src: boss}, min: 2, max: 2}
      {hp_pct: summon, where: {src: boss}, max: 50}        # unit's health % when the event fired (first: true = only the first one)
      {damage: {src: warrior, ability: fireball}, each: 25}  # every hit deals exactly this
      {time: {min: 10, max: 40}}
      {before: [enrage, teleport], where: {src: boss}}       # first A happens before first B (where_a / where_b filter each side)
      {gap: teleport, where: {src: boss}, min: 5}            # consecutive events at least this far apart
      {value: hit, where: {src: red_wrestler}, field: knockback, agg: max, min: 20}   # aggregate an event field
      {rate: {outcome: [red]}, min: 0.35, max: 0.65}         # fraction of seeds where the inner assertion holds
    events also include {type: mode, src, mode: chase|flee|idle, hp_pct} whenever an enemy's movement changes

Paths: dotted keys, list items selected by name (`characters[warrior]`) or index (`[0]`).
"""
import json
import operator
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import dsl
from . import engines
from .policies import auto_policy, idle_policy

POLICIES = {"auto": auto_policy, "idle": idle_policy}
OPS = {"==": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}


# -- spec paths ----------------------------------------------------------------
def _tokens(path):
    for key, sel in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[([^\]]+)\]", path):
        yield ("key", key) if key else ("sel", sel)


def get_path(data, path):
    cur = data
    for kind, tok in _tokens(path):
        if kind == "key":
            if not isinstance(cur, dict) or tok not in cur:
                raise KeyError(path)
            cur = cur[tok]
        else:
            if not isinstance(cur, list):
                raise KeyError(path)
            if tok.isdigit():
                if int(tok) >= len(cur):
                    raise KeyError(path)
                cur = cur[int(tok)]
            else:
                hit = [x for x in cur if isinstance(x, dict) and x.get("name") == tok]
                if not hit:
                    raise KeyError(path)
                cur = hit[0]
    return cur


def diff(a, b, prefix=""):
    """Paths that differ between two spec dumps; named list items are matched by name."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            p = f"{prefix}.{k}" if prefix else k
            if k not in a or k not in b:
                out.append(p)
            else:
                out += diff(a[k], b[k], p)
        return out
    if isinstance(a, list) and isinstance(b, list):
        named = all(isinstance(x, dict) and "name" in x for x in a + b)
        if named:
            an, bn = {x["name"]: x for x in a}, {x["name"]: x for x in b}
            out = []
            for n in sorted(set(an) | set(bn)):
                p = f"{prefix}[{n}]"
                out += [p] if n not in an or n not in bn else diff(an[n], bn[n], p)
            if not out and [x["name"] for x in a] != [x["name"] for x in b]:
                out.append(prefix + "(order)")
            return out
        return [] if a == b else [prefix]
    return [] if a == b else [prefix]


def _glob(pattern, path):
    """`*` is the only wildcard; `[name]` selectors are literal (fnmatch would treat them as character classes)."""
    return re.fullmatch(re.escape(pattern).replace(r"\*", ".*"), path) is not None


def _allowed(path, patterns):
    return any(path == p or path.startswith(p + ".") or path.startswith(p + "[") or ("*" in p and _glob(p, path)) for p in patterns)


# -- sim assertions -------------------------------------------------------------
def _match(e, where):
    for k, v in (where or {}).items():
        ev = e.get(k)
        if isinstance(v, str) and v.endswith("*"):
            if not (isinstance(ev, str) and ev.startswith(v[:-1])):
                return False
        elif ev != v:
            return False
    return True


def _check_run(res, a):
    ev = res.events
    if "outcome" in a:
        want = a["outcome"] if isinstance(a["outcome"], list) else [a["outcome"]]
        return res.outcome in want, f"outcome {res.outcome}, want {want}"
    if "count" in a:
        n = sum(1 for e in ev if e["type"] == a["count"] and _match(e, a.get("where")))
        lo, hi = a.get("min", 0), a.get("max", float("inf"))
        return lo <= n <= hi, f"{a['count']} {a.get('where', {})} count {n}, want [{lo}, {hi}]"
    if "hp_pct" in a:
        xs = [e["hp_pct"] for e in ev if e["type"] == a["hp_pct"] and _match(e, a.get("where"))]
        if a.get("first"):
            xs = xs[:1]
        if not xs:
            return False, f"no {a['hp_pct']} {a.get('where', {})} events"
        lo, hi = a.get("min", 0), a.get("max", 100)
        return all(lo <= x <= hi for x in xs), f"{a['hp_pct']} fired at hp% {xs}, want in [{lo}, {hi}]"
    if "damage" in a:
        xs = [e["amount"] for e in ev if e["type"] == "damage" and _match(e, a["damage"])]
        if not xs:
            return False, f"no damage events matching {a['damage']}"
        if "each" in a:
            # a killing blow is capped by the target's remaining hp, so only non-lethal hits show the real per-hit damage
            full = [e["amount"] for e in ev if e["type"] == "damage" and _match(e, a["damage"]) and e["hp_after"] > 0]
            if not full:
                return False, f"every matching hit was lethal (amounts {sorted(set(round(x, 2) for x in xs))}), so per-hit damage {a['each']} can't be confirmed"
            return all(abs(x - a["each"]) < 1e-6 for x in full), f"hits {sorted(set(round(x, 2) for x in full))}, want each {a['each']}"
        tot = sum(xs)
        return a.get("min_total", 0) <= tot <= a.get("max_total", 1e12), f"total {tot:.1f}, want [{a.get('min_total', 0)}, {a.get('max_total', 'inf')}]"
    if "time" in a:
        t = a["time"]
        return t.get("min", 0) <= res.time <= t.get("max", 1e9), f"fight lasted {res.time:.1f}s, want [{t.get('min', 0)}, {t.get('max', 'inf')}]"
    if "value" in a:
        xs = [e[a["field"]] for e in ev if e["type"] == a["value"] and _match(e, a.get("where")) and a["field"] in e]
        if not xs:
            return False, f"no {a['value']} {a.get('where', {})} events with field {a['field']}"
        agg = a.get("agg", "max")
        v = {"max": max, "min": min, "sum": sum, "mean": lambda x: sum(x) / len(x)}[agg](xs)
        lo, hi = a.get("min", float("-inf")), a.get("max", float("inf"))
        return lo <= v <= hi, f"{agg} {a['value']}.{a['field']} {a.get('where', {})} = {v:.2f}, want [{lo}, {hi}]"
    if "gap" in a:
        ts = [e["t"] for e in ev if e["type"] == a["gap"] and _match(e, a.get("where"))]
        gaps = [round(b - a_, 3) for a_, b in zip(ts, ts[1:])]
        return all(g >= a["min"] - 1e-6 for g in gaps), f"{a['gap']} gaps {gaps[:6]}, want each >= {a['min']}"
    if "before" in a:
        A, B = a["before"]
        wa, wb = {**(a.get("where") or {}), **(a.get("where_a") or {})}, {**(a.get("where") or {}), **(a.get("where_b") or {})}
        ta = next((e["t"] for e in ev if e["type"] == A and _match(e, wa)), None)
        tb = next((e["t"] for e in ev if e["type"] == B and _match(e, wb)), None)
        return ta is not None and tb is not None and ta < tb, f"first {A} at {ta}, first {B} at {tb}"
    raise ValueError(f"unknown assertion {a}")


# -- task evaluation -------------------------------------------------------------
@dataclass
class Verdict:
    task: str
    valid: bool
    passed: bool
    regression: bool
    checks: list = field(default_factory=list)  # (ok, description)
    changed: list = field(default_factory=list)
    out_of_scope: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def text(self):
        lines = [f"{self.task}: {'PASS' if self.passed else 'FAIL'}" + (" (regression)" if self.regression else "")]
        lines += [f"  invalid DSL: {e}" for e in self.errors]
        lines += [f"  {'ok ' if ok else 'BAD'} {d}" for ok, d in self.checks]
        if self.out_of_scope:
            lines.append(f"  out-of-scope changes: {', '.join(self.out_of_scope)}")
        return "\n".join(lines)


def load_task(task_dir):
    task_dir = Path(task_dir)
    t = yaml.safe_load(open(task_dir / "task.yaml"))
    t["_dir"] = task_dir
    t["_start_src"] = open(task_dir / t["start"]).read()
    return t


def evaluate(task, game_src: str) -> Verdict:
    rep = engines.validate(game_src)
    if not rep.ok:
        return Verdict(task["id"], False, False, False, errors=rep.errors)
    spec = rep.spec
    start_rep = engines.validate(task["_start_src"])
    now = spec.model_dump()
    if start_rep.ok:
        changed = diff(start_rep.spec.model_dump(), now)
        oos = [p for p in changed if not _allowed(p, task.get("allowed_changes", []))]
    else:  # the task starts from a broken file: there is no valid baseline to diff against, so only the checks apply
        changed, oos = [], []
    checks = []
    for c in task["checks"]:
        if c["kind"] == "spec":
            try:
                v = get_path(now, c["path"])
                if c["op"] == "exists":
                    ok, d = True, f"{c['path']} exists"
                elif c["op"] == "missing":
                    ok, d = False, f"{c['path']} should not exist"
                else:
                    ok, d = OPS[c["op"]](v, c["value"]), f"{c['path']} = {v!r}, want {c['op']} {c['value']!r}"
            except KeyError:
                ok, d = c["op"] == "missing", f"{c['path']} {'missing (ok)' if c['op'] == 'missing' else 'not found'}"
            checks.append((ok, d))
        elif c["kind"] == "sim":
            n = c.get("seeds", 5)
            pol = POLICIES[c.get("policy", "auto")]
            runs = []
            for i in range(n):
                runs.append(engines.run(spec, i, policy=pol))
            for a in c["assert"]:
                if "rate" in a:  # fraction of seeds on which the inner assertion holds
                    ok_n = sum(_check_run(r, a["rate"])[0] for r in runs)
                    lo, hi = a.get("min", 0), a.get("max", 1)
                    checks.append((lo <= ok_n / n <= hi, f"[{n} seeds] {json.dumps(a['rate'])} held on {ok_n}/{n} seeds, want rate in [{lo}, {hi}]"))
                    continue
                res = [_check_run(r, a) for r in runs]
                bad = [d for ok, d in res if not ok]
                checks.append((not bad, f"[{n} seeds, {c.get('policy', 'auto')}] " + (bad[0] + (f" (+{len(bad) - 1} more seeds)" if len(bad) > 1 else "") if bad else res[0][1])))
    passed = all(ok for ok, _ in checks) and not oos
    return Verdict(task["id"], True, passed, bool(oos), checks, changed, oos)


def all_tasks(root="tasks"):
    return [load_task(p.parent) for p in sorted(Path(root).glob("*/task.yaml"))]
