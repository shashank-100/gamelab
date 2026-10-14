"""Generate tasks/<id>/{task.yaml,start.yaml,solution.yaml} and verify each one:
the start game must FAIL its checks and the reference solution must PASS.

Run: uv run python tools/make_tasks.py
"""
import copy
import shutil
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gamelab import bench  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BASE = {g: yaml.safe_load(open(ROOT / "games" / f"{g}.yaml")) for g in ("arena", "swarm")}


def find(items, name):
    return next(x for x in items if x["name"] == name)


def boss(d):
    return find(d["enemies"], "boss")


def ab(d, char, name):
    return find(find(d["characters"], char)["abilities"], name)


def sim(*asserts, seeds=5, policy="auto"):
    return {"kind": "sim", "seeds": seeds, "policy": policy, "assert": list(asserts)}


def spec(path, op, value=None):
    return {"kind": "spec", "path": path, "op": op, **({} if value is None else {"value": value})}


TASKS = []


def task(id, category, base, prompt, allowed, checks, solve, start=None):
    TASKS.append(dict(id=id, category=category, base=base, prompt=prompt, allowed=allowed, checks=checks, solve=solve, start=start))


# ---------------------------------------------------------------- tuning
def _fireball40(d):
    ab(d, "warrior", "fireball")["damage"] = 40
task("tune_fireball_damage", "tune", "arena",
     "Make the warrior's fireball deal 40 damage per hit.",
     ["characters[warrior].abilities[fireball].damage"],
     [sim({"damage": {"src": "warrior", "ability": "fireball"}, "each": 40}, {"outcome": ["win"]})],
     _fireball40)


def _faster(d):
    find(d["characters"], "warrior")["speed"] = 7.5
    ab(d, "warrior", "dash")["distance"] = 15
task("faster_warrior", "tune", "arena",
     "The warrior feels sluggish. Make it move 50% faster and dash 50% farther.",
     ["characters[warrior].speed", "characters[warrior].abilities[dash].distance"],
     [spec("characters[warrior].speed", "==", 7.5), spec("characters[warrior].abilities[dash].distance", "==", 15),
      sim({"outcome": ["win"]})],
     _faster)


def _gravity(d):
    d["game"]["gravity"] = 9.8
task("arcing_projectiles", "tune", "arena",
     "Turn on gravity of 9.8 so projectiles arc downward.",
     ["game.gravity"],
     [spec("game.gravity", "==", 9.8)],
     _gravity)


# ---------------------------------------------------------------- new behavior
def _teleport(d):
    boss(d)["behavior"] = [{"when": "health < 30%", "action": "teleport", "cooldown": 5}]
task("boss_teleport_low_hp", "behavior", "arena",
     "When the boss drops below 30% health it should teleport to a random spot, at most once every 5 seconds.",
     ["enemies[boss].behavior"],
     [sim({"count": "teleport", "where": {"src": "boss"}, "min": 1}, {"hp_pct": "teleport", "where": {"src": "boss"}, "max": 30},
          {"gap": "teleport", "where": {"src": "boss"}, "min": 5})],
     _teleport)


def _enrage(d):
    boss(d)["behavior"] = [{"when": "health < 50%", "action": "enrage", "amount": 1.5, "once": True}]
    find(d["characters"], "warrior")  # unchanged
task("boss_enrage_half", "behavior", "arena",
     "Once the boss falls below half health it should become enraged and hit 50% harder for the rest of the fight. This happens only once.",
     ["enemies[boss].behavior"],
     [spec("enemies[boss].behavior[0].amount", "==", 1.5),
      sim({"count": "enrage", "where": {"src": "boss"}, "min": 1, "max": 1}, {"hp_pct": "enrage", "where": {"src": "boss"}, "max": 50})],
     _enrage)


def _summon(d):
    b = boss(d)
    b["behavior"] = [{"when": "health < 50%", "action": "summon", "unit": "skeleton", "count": 2, "once": True}]
    d["enemies"].append({"name": "skeleton", "minion": True, "health": 40, "speed": 4, "attack": {"damage": 5, "range": 1.2, "cooldown": 1}})
    ab(d, "warrior", "fireball")["damage"] = 25
    find(d["characters"], "warrior")["health"] = 100
task("boss_summons_skeletons", "behavior", "arena",
     "Below 50% health the boss should summon 2 skeletons, one time only. Skeletons have 40 health, speed 4, and a melee attack doing 5 damage.",
     ["enemies"],
     [spec("enemies[skeleton].health", "==", 40), spec("enemies[skeleton].speed", "==", 4), spec("enemies[skeleton].attack.damage", "==", 5),
      sim({"count": "summon", "where": {"src": "boss"}, "min": 2, "max": 2}, {"hp_pct": "summon", "where": {"src": "boss"}, "max": 50})],
     _summon)


def _flee(d):
    boss(d)["behavior"] = [{"when": "health < 20%", "action": "flee"}]
task("boss_flees_when_low", "behavior", "arena",
     "Under 20% health the boss should run away from the player instead of chasing.",
     ["enemies[boss].behavior"],
     [sim({"count": "mode", "where": {"src": "boss", "mode": "flee"}, "min": 1}, {"hp_pct": "mode", "where": {"src": "boss", "mode": "flee"}, "max": 20})],
     _flee)


def _ambush(d):
    boss(d)["behavior"] = [{"when": "distance_to_player > 12", "action": "idle"}]
task("boss_waits_in_ambush", "behavior", "arena",
     "The boss should stand still while the player is more than 12 units away, and chase as usual once the player is within 12 units.",
     ["enemies[boss].behavior"],
     [sim({"before": ["mode", "mode"], "where": {"src": "boss"}, "where_a": {"mode": "idle"}, "where_b": {"mode": "chase"}},
          {"count": "mode", "where": {"src": "boss", "mode": "idle"}, "min": 1},
          {"count": "mode", "where": {"src": "boss", "mode": "chase"}, "min": 1})],
     _ambush)


def _phase2(d):
    boss(d)["behavior"] = [
        {"when": "health < 40%", "action": "heal", "amount": 100, "once": True},
        {"when": "health < 40%", "action": "enrage", "amount": 2, "once": True},
    ]
    find(d["characters"], "warrior")["health"] = 250
task("boss_second_phase", "behavior", "arena",
     "Give the boss a second phase: the first time it drops below 40% health it heals 100 health and becomes enraged, dealing double damage. "
     "The warrior should still be able to win, so give the warrior more health if needed (only health).",
     ["enemies[boss].behavior", "characters[warrior].health"],
     [sim({"count": "heal", "where": {"src": "boss"}, "min": 1, "max": 1}, {"hp_pct": "heal", "where": {"src": "boss"}, "max": 40},
          {"count": "enrage", "where": {"src": "boss"}, "min": 1, "max": 1}, {"outcome": ["win"]})],
     _phase2)


def _imps(d):
    boss(d)["behavior"] = [{"when": "time >= 10", "action": "summon", "unit": "imp", "count": 1, "cooldown": 10}]
    d["enemies"].append({"name": "imp", "minion": True, "health": 20, "speed": 6, "attack": {"damage": 3, "range": 1.2, "cooldown": 1}})
task("boss_summons_on_timer", "behavior", "arena",
     "Starting 10 seconds into the fight, the boss summons one imp every 10 seconds. Imps have 20 health, speed 6 and a 3 damage melee attack.",
     ["enemies"],
     [spec("enemies[imp].health", "==", 20),
      sim({"count": "summon", "where": {"src": "boss"}, "min": 1}, {"gap": "summon", "where": {"src": "boss"}, "min": 10})],
     _imps)


# ---------------------------------------------------------------- new content
def _mend(d):
    find(d["characters"], "warrior")["abilities"].append({"name": "mend", "type": "heal", "cooldown": 15, "amount": 30})
task("add_heal_ability", "content", "arena",
     "Give the warrior a new heal ability called `mend` that restores 30 health with a 15 second cooldown.",
     ["characters[warrior].abilities"],
     [spec("characters[warrior].abilities[mend].type", "==", "heal"), spec("characters[warrior].abilities[mend].amount", "==", 30),
      spec("characters[warrior].abilities[mend].cooldown", "==", 15), sim({"outcome": ["win"]})],
     _mend)


def _slam_start(d):
    for e in d["enemies"]:  # tougher goblins so slams land non-lethal hits and per-hit damage is observable
        e["health"] = 70


def _slam(d):
    find(d["characters"], "mage")["abilities"].append({"name": "ground_slam", "type": "aoe", "cooldown": 5, "damage": 20, "radius": 4})
task("add_aoe_ability", "content", "swarm",
     "Give the mage a `ground_slam` that deals 20 damage to every enemy within 4 units, with a 5 second cooldown.",
     ["characters[mage].abilities"],
     [spec("characters[mage].abilities[ground_slam].radius", "==", 4),
      sim({"damage": {"src": "mage", "ability": "ground_slam"}, "each": 20}, {"outcome": ["win"]})],
     _slam, start=_slam_start)


def _cleric(d):
    d["characters"].append({"name": "cleric", "health": 80, "speed": 4,
                            "abilities": [{"name": "smite", "type": "projectile", "cooldown": 1.2, "damage": 10, "range": 12}]})
task("add_ally", "content", "arena",
     "Add an ally character `cleric` with 80 health and speed 4 that fights alongside the warrior using a projectile `smite`: 10 damage, range 12, 1.2s cooldown.",
     ["characters[cleric]"],
     [spec("characters[cleric].health", "==", 80), sim({"damage": {"src": "cleric", "ability": "smite"}, "each": 10}, {"outcome": ["win"]})],
     _cleric)


def _rename(d):
    ab(d, "warrior", "slash")["name"] = "cleave"
task("rename_ability", "content", "arena",
     "Rename the warrior's `slash` ability to `cleave`. Nothing else about it should change.",
     ["characters[warrior].abilities[slash]", "characters[warrior].abilities[cleave]"],
     [spec("characters[warrior].abilities[slash]", "missing"), spec("characters[warrior].abilities[cleave].damage", "==", 12),
      spec("characters[warrior].abilities[cleave].cooldown", "==", 0.8), spec("characters[warrior].abilities[cleave].type", "==", "melee")],
     _rename)


# ---------------------------------------------------------------- bug fixes (start game is broken)
def _bug_pct_start(d):
    boss(d)["behavior"] = [{"when": "health < 30", "action": "teleport", "cooldown": 5}]
def _bug_pct_fix(d):
    boss(d)["behavior"] = [{"when": "health < 30%", "action": "teleport", "cooldown": 5}]
task("fix_teleport_threshold", "bugfix", "arena",
     "The boss is supposed to teleport (at most every 5s) once it is below 30% health, but it basically never does. Fix it.",
     ["enemies[boss].behavior"],
     [sim({"count": "teleport", "where": {"src": "boss"}, "min": 1}, {"hp_pct": "teleport", "where": {"src": "boss"}, "first": True, "min": 20, "max": 30})],
     _bug_pct_fix, start=_bug_pct_start)


def _bug_flood_start(d):
    boss(d)["behavior"] = [{"when": "health < 60%", "action": "summon", "unit": "bat", "count": 3}]
    d["enemies"].append({"name": "bat", "minion": True, "health": 10, "speed": 7, "attack": {"damage": 1, "range": 1, "cooldown": 1}})
def _bug_flood_fix(d):
    boss(d)["behavior"][0]["once"] = True
task("fix_summon_flood", "bugfix", "arena",
     "Once the boss is hurt it summons an endless flood of bats. It should summon 3 bats one time only.",
     ["enemies[boss].behavior"],
     [sim({"count": "summon", "where": {"src": "boss"}, "min": 3, "max": 3})],
     _bug_flood_fix, start=_bug_flood_start)


def _bug_order_start(d):
    boss(d)["behavior"] = [{"when": "distance_to_player < 100", "action": "chase"}, {"when": "health < 25%", "action": "flee"}]
def _bug_order_fix(d):
    boss(d)["behavior"] = [{"when": "health < 25%", "action": "flee"}, {"when": "distance_to_player < 100", "action": "chase"}]
task("fix_rule_order", "bugfix", "arena",
     "The boss should flee below 25% health but it keeps chasing. Fix the behavior without removing either rule.",
     ["enemies[boss].behavior"],
     [sim({"count": "mode", "where": {"src": "boss", "mode": "flee"}, "min": 1}, {"hp_pct": "mode", "where": {"src": "boss", "mode": "flee"}, "max": 25}),
      spec("enemies[boss].behavior[1]", "exists")],
     _bug_order_fix, start=_bug_order_start)


# ---------------------------------------------------------------- balance (need simulation to get right)
def _longer(d):
    b = boss(d)
    b["health"] = 1250
    b["attack"]["damage"] = 1
task("balance_longer_boss_fight", "balance", "arena",
     "The boss fight is over too fast. Make it last between 40 and 90 seconds with the auto player, and the warrior must still win every time. Only change the boss.",
     ["enemies[boss]"],
     [sim({"time": {"min": 40, "max": 90}}, {"outcome": ["win"]}, seeds=5)],
     _longer)


def _hard(d):
    boss(d)["speed"] = 5
    boss(d)["attack"]["damage"] = 20
task("balance_unbeatable_boss", "balance", "arena",
     "Make the boss strong enough that the auto player loses every time, by changing only the boss's attack and speed.",
     ["enemies[boss].attack", "enemies[boss].speed"],
     [sim({"outcome": ["lose"]}, seeds=5)],
     _hard)


def _goblins(d):
    for e in d["enemies"]:
        e["health"] = 90
        e["attack"]["damage"] = 1.5
task("balance_swarm_challenge", "balance", "swarm",
     "The goblins are too weak. Make the fight last at least 15 seconds and make the mage take at least 41 damage, but the mage must still win every time. Only change the goblins.",
     ["enemies"],
     [sim({"time": {"min": 15}}, {"outcome": ["win"]}, {"damage": {"dst": "mage"}, "min_total": 41}, seeds=5)],
     _goblins)


def write():
    out = ROOT / "tasks"
    if out.exists():
        shutil.rmtree(out)
    ok = True
    for t in TASKS:
        d = out / t["id"]
        d.mkdir(parents=True)
        start = copy.deepcopy(BASE[t["base"]])
        if t["start"]:
            t["start"](start)
        sol = copy.deepcopy(start)
        t["solve"](sol)
        dump = lambda x: yaml.safe_dump(x, sort_keys=False, default_flow_style=None, width=140)
        (d / "start.yaml").write_text(dump(start))
        (d / "solution.yaml").write_text(dump(sol))
        (d / "task.yaml").write_text(dump({"id": t["id"], "category": t["category"], "prompt": t["prompt"], "start": "start.yaml",
                                           "allowed_changes": t["allowed"], "checks": t["checks"]}))
        tk = bench.load_task(d)
        v_start, v_sol = bench.evaluate(tk, tk["_start_src"]), bench.evaluate(tk, (d / "solution.yaml").read_text())
        good = (not v_start.passed) and v_sol.passed
        ok &= good
        print(f"{'ok ' if good else 'BAD'} {t['id']:28s} start={'pass' if v_start.passed else 'fail'} solution={'pass' if v_sol.passed else 'fail'}")
        if not good:
            print(v_sol.text() if not v_sol.passed else v_start.text())
    print(f"{len(TASKS)} tasks, all verified" if ok else "SOME TASKS BROKEN")
    return ok


if __name__ == "__main__":
    sys.exit(0 if write() else 1)
