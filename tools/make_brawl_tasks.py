"""Brawler (v2) tasks: tasks/b_*/. Same verification as v1: start must FAIL, reference solution must PASS.

Run: uv run python tools/make_brawl_tasks.py
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_tasks import ROOT, find, sim, spec, write  # noqa: E402

BASE = {g: yaml.safe_load(open(ROOT / "games" / f"{g}.yaml")) for g in ("pro_bending", "squad")}
TASKS = []
N = 20  # seeds for rate checks


def task(id, category, base, prompt, allowed, checks, solve, start=None):
    TASKS.append(dict(id=id, category=category, base=base, prompt=prompt, allowed=allowed, checks=checks, solve=solve, start=start))


def ch(d, name):
    return find(d["characters"], name)


def ab(d, char, name):
    return find(ch(d, char)["abilities"], name)


def fair(lo=0.35, hi=0.65, team="red", seeds=N, extra=()):
    return sim({"rate": {"outcome": [team]}, "min": lo, "max": hi}, *extra, seeds=seeds)


W, NJ = "characters[wrestler]", "characters[ninja]"

# ---------------------------------------------------------------- feel / tuning (designer language)
def _heavy(d):
    ch(d, "wrestler")["weight"] = 4.0
    ch(d, "wrestler")["speed"] = 3.0
task("b_heavier_wrestler", "feel", "pro_bending",
     "The wrestler should feel twice as heavy and a little slower, but he still needs to win at least 35% of 1v1s against the ninja.",
     [f"{W}.weight", f"{W}.speed"],
     [spec(f"{W}.weight", "==", 4.0), spec(f"{W}.speed", "<", 3.5), spec(f"{W}.speed", ">=", 2.5),
      fair(0.35, 1.0)],
     _heavy)


def _floaty(d):
    ab(d, "ninja", "leap")["duration"] = 1.4
    ab(d, "ninja", "leap")["air_speed"] = 2.0
task("b_floaty_leap", "feel", "pro_bending",
     "Make the ninja's leap last twice as long, and let her move faster while she's in the air.",
     [f"{NJ}.abilities[leap]"],
     [spec(f"{NJ}.abilities[leap].duration", "==", 1.4), spec(f"{NJ}.abilities[leap].air_speed", ">", 1.5)],
     _floaty)


def _icy(d):
    d["game"]["friction"] = 2.0
task("b_icy_floor", "feel", "pro_bending",
     "Make the floor feel icy: fighters who get hit should slide much farther than they do now.",
     ["game.friction"],
     [spec("game.friction", "<=", 2.5), spec("game.friction", ">", 0)],
     _icy)


# ---------------------------------------------------------------- abilities
def _rocks(d):
    w = ch(d, "wrestler")
    w["abilities"] = [a for a in w["abilities"] if a["name"] != "earthquake"] + [
        {"name": "boulder_ring", "kind": "rocks", "button": "a+b", "cooldown": 7, "count": 4, "duration": 5, "damage": 6, "knockback": 8}]
task("b_wrestler_rocks", "ability", "pro_bending",
     "Replace the wrestler's earthquake with a rock summon on a+b: 4 rocks that orbit him for 5 seconds, each doing 6 damage and 8 knockback.",
     [f"{W}.abilities"],
     [spec(f"{W}.abilities[earthquake]", "missing"),
      sim({"count": "rocks", "where": {"src": "red_wrestler", "count": 4}, "min": 1}, seeds=5),
      sim({"value": "hit", "where": {"src": "red_wrestler"}, "field": "damage", "agg": "max", "min": 6}, seeds=5)],
     _rocks)


def _unint(d):
    q = ab(d, "wrestler", "earthquake")
    q["interruptible"] = False
    q["knockback_max"] = 11
task("b_quake_uninterruptible", "ability", "pro_bending",
     "Earthquake gets interrupted too easily. Make it impossible to interrupt, but halve its maximum knockback to compensate.",
     [f"{W}.abilities[earthquake]"],
     [spec(f"{W}.abilities[earthquake].knockback_max", "==", 11), spec(f"{W}.abilities[earthquake].knockback_min", "==", 6),
      sim({"count": "channel_interrupt", "where": {"src": "red_wrestler"}, "max": 0}, {"count": "channel_start", "where": {"src": "red_wrestler"}, "min": 1}, seeds=5)],
     _unint)


def _shock(d):
    l = ab(d, "ninja", "leap")
    l.update({"landing_radius": 2.5, "landing_damage": 5, "landing_knockback": 9})
task("b_leap_shockwave", "ability", "pro_bending",
     "Give the ninja's leap a landing shockwave: radius 2.5, 5 damage, 9 knockback.",
     [f"{NJ}.abilities[leap]"],
     [spec(f"{NJ}.abilities[leap].landing_radius", "==", 2.5), spec(f"{NJ}.abilities[leap].duration", "==", 0.7),
      sim({"rate": {"count": "hit", "where": {"src": "blue_ninja", "ability": "leap"}, "min": 1}, "min": 0.2}, seeds=N)],
     _shock)


def _swap(d):
    ab(d, "wrestler", "earthquake")["button"] = "a"
    ab(d, "wrestler", "grapple_slam")["button"] = "a+b"
task("b_swap_buttons", "ability", "pro_bending",
     "Swap the wrestler's controls: earthquake goes on a, grapple slam goes on a+b.",
     [f"{W}.abilities"],
     [spec(f"{W}.abilities[earthquake].button", "==", "a"), spec(f"{W}.abilities[grapple_slam].button", "==", "a+b"),
      spec(f"{W}.abilities[rock_wall].button", "==", "b")],
     _swap)


def _wall(d):
    ab(d, "wrestler", "rock_wall").update({"length": 7, "duration": 5})
task("b_bigger_wall", "ability", "pro_bending",
     "The ninja spams shuriken. Make the wrestler's wall at least 7 units long and last 5 seconds so it blocks more of them.",
     [f"{W}.abilities[rock_wall]"],
     [spec(f"{W}.abilities[rock_wall].length", ">=", 7), spec(f"{W}.abilities[rock_wall].duration", "==", 5),
      sim({"rate": {"count": "blocked", "where": {"by": "wall"}, "min": 1}, "min": 0.5}, seeds=10)],
     _wall)


# ---------------------------------------------------------------- bug fixes
def _clash_start(d):
    ab(d, "ninja", "leap")["button"] = "a"
def _clash_fix(d):
    ab(d, "ninja", "leap")["button"] = "a+b"
task("b_fix_button_clash", "bugfix", "pro_bending",
     "The game won't load since the last edit. Fix it. Leap is supposed to be the ninja's a+b move.",
     [f"{NJ}.abilities[leap]"],
     [spec(f"{NJ}.abilities[leap].button", "==", "a+b"), spec(f"{NJ}.abilities[shuriken].button", "==", "a")],
     _clash_fix, start=_clash_start)


def _fizzle_start(d):
    ab(d, "wrestler", "earthquake")["min_time"] = 1.9
def _fizzle_fix(d):
    ab(d, "wrestler", "earthquake")["min_time"] = 0.3
task("b_fix_fizzle", "bugfix", "pro_bending",
     "Players say the wrestler's earthquake never does anything. Fix it without changing its damage or knockback.",
     [f"{W}.abilities[earthquake]"],
     [spec(f"{W}.abilities[earthquake].damage_max", "==", 20), spec(f"{W}.abilities[earthquake].knockback_max", "==", 22),
      sim({"rate": {"count": "channel_release", "where": {"src": "red_wrestler"}, "min": 1}, "min": 0.5}, seeds=10)],
     _fizzle_fix, start=_fizzle_start)


def _slip_start(d):
    d["game"]["friction"] = 0.5
def _slip_fix(d):
    d["game"]["friction"] = 4.0
task("b_fix_slippery", "bugfix", "pro_bending",
     "Since the last patch, fights end in a few seconds and tiny hits fling people off the map. Fix it.",
     ["game"],
     [sim({"rate": {"time": {"min": 15}}, "min": 0.8}, seeds=10)],
     _slip_fix, start=_slip_start)


# ---------------------------------------------------------------- content
def _3v3(d):
    d["teams"] = {"red": [{"character": "wrestler"}, {"character": "wrestler"}, {"character": "ninja"}],
                  "blue": [{"character": "ninja"}, {"character": "ninja"}, {"character": "ninja"}]}
task("b_make_3v3", "content", "pro_bending",
     "Make this a 3v3: red is two wrestlers and a ninja, blue is three ninjas.",
     ["teams"],
     [spec("teams.red[0].character", "==", "wrestler"), spec("teams.red[1].character", "==", "wrestler"),
      spec("teams.red[2].character", "==", "ninja"), spec("teams.blue[2].character", "==", "ninja"),
      sim({"count": "spawn", "min": 6, "max": 6}, seeds=2)],
     _3v3)

EARTH = {"name": "earthbender", "weight": 1.5, "speed": 3.2, "radius": 0.7, "abilities": [
    {"name": "stone_orbit", "kind": "rocks", "button": "a", "cooldown": 6, "count": 4, "duration": 5, "damage": 4, "knockback": 8},
    {"name": "boulder", "kind": "projectile", "button": "b", "cooldown": 1.4, "damage": 6, "knockback": 9, "speed": 12, "range": 10, "radius": 0.6},
    {"name": "quake_leap", "kind": "jump", "button": "a+b", "cooldown": 5, "duration": 0.8, "landing_radius": 2.5, "landing_damage": 6, "landing_knockback": 10}]}


def _earth(d):
    d["characters"].append(dict(EARTH))
    d["teams"]["red"] = [{"character": "earthbender"}]
E = "characters[earthbender]"
task("b_new_earthbender", "content", "pro_bending",
     "Add a new character, `earthbender`, and have it fight the ninja instead of the wrestler (put it on red). "
     "Earthbender: weight 1.5, speed 3.2, radius 0.7. On a: `stone_orbit`, 4 orbiting rocks for 5s, knockback 8, 6s cooldown. "
     "On b: `boulder`, a projectile with knockback 9, speed 12, range 10, radius 0.6, 1.4s cooldown. "
     "On a+b: `quake_leap`, a 0.8s jump with a landing shockwave (radius 2.5, damage 6, knockback 10), 5s cooldown. "
     "You choose the rock and boulder damage: pick values so earthbender wins between 30% and 70% of fights against the ninja.",
     ["characters", "teams.red"],
     [spec(f"{E}.weight", "==", 1.5), spec(f"{E}.abilities[boulder].knockback", "==", 9), spec(f"{E}.abilities[stone_orbit].count", "==", 4),
      spec(f"{E}.abilities[quake_leap].landing_knockback", "==", 10), spec("teams.red[0].character", "==", "earthbender"),
      spec("characters[wrestler].weight", "==", 2.0), fair(0.30, 0.70)],
     _earth)


# ---------------------------------------------------------------- balance (needs simulation)
def _wop_start(d):
    ab(d, "wrestler", "grapple_slam")["knockback"] = 13
def _wop_fix(d):
    ab(d, "wrestler", "grapple_slam")["knockback"] = 7
task("b_balance_wrestler_op", "balance", "pro_bending",
     "The wrestler wins way too often in 1v1. Rebalance it so each side wins between 35% and 65% of fights, only touching the wrestler's abilities.",
     [f"{W}.abilities"],
     [fair()],
     _wop_fix, start=_wop_start)


def _nop_start(d):
    ab(d, "ninja", "shadow_step")["knockback"] = 18
def _nop_fix(d):
    ab(d, "ninja", "shadow_step")["knockback"] = 11
task("b_balance_ninja_op", "balance", "pro_bending",
     "The ninja is overpowered in 1v1. Bring the matchup back so each side wins 35-65% of fights. Only change the ninja.",
     [NJ],
     [fair()],
     _nop_fix, start=_nop_start)


def _fast(d):
    d["game"]["friction"] = 3
    ab(d, "wrestler", "grapple_slam")["knockback"] = 13
task("b_faster_fights", "balance", "pro_bending",
     "Fights drag on. At least 60% of 1v1s should be over in under 33 seconds, and the matchup must stay fair: each side wins 30-70%.",
     ["game", "characters"],
     [sim({"rate": {"time": {"max": 33}}, "min": 0.6}, {"rate": {"outcome": ["red"]}, "min": 0.3, "max": 0.7}, seeds=N)],
     _fast)


def _big(d):
    d["game"]["platform"]["radius"] = 18
    ab(d, "wrestler", "grapple_slam")["knockback"] = 9
    ab(d, "wrestler", "earthquake")["knockback_max"] = 28
task("b_bigger_arena", "balance", "pro_bending",
     "Make the platform 50% bigger, and keep the 1v1 fair afterwards: each side should win 35-65% of fights.",
     ["game.platform", "characters"],
     [spec("game.platform.radius", "==", 18), fair()],
     _big)


def _squad_start(d):
    ab(d, "ninja", "shadow_step")["knockback"] = 14


def _squad(d):
    ab(d, "ninja", "shadow_step")["knockback"] = 11
task("b_balance_3v3", "balance", "squad",
     "Three ninjas crush the mixed red team in 3v3. Make it fair (red wins 35-65%) by changing only the ninja.",
     [NJ],
     [fair()],
     _squad, start=_squad_start)


if __name__ == "__main__":
    sys.exit(0 if write(TASKS, BASE) else 1)
