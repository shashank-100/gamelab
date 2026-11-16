"""Export simulated brawler fights as compact replay JSON for the web viewer (docs/demo/).

    uv run python tools/export_replay.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from gamelab import engines  # noqa: E402

OUT = ROOT / "docs" / "demo" / "replays.js"
FIGHTS = [("pro_bending", 0, "1v1: wrestler vs ninja"), ("pro_bending", 2, "1v1: rematch"), ("squad", 1, "3v3 squad")]
EVENTS = {"hit", "ko", "wall", "rocks", "jump", "land", "channel_start", "channel_release", "channel_interrupt", "blocked", "wall_block"}


def export(game, seed, title):
    rep = engines.validate((ROOT / "games" / f"{game}.yaml").read_text())
    spec = rep.spec
    res = engines.run(spec, seed, record_frames=True)
    chars = {c.name: {"weight": c.weight, "radius": c.radius, "abilities": [f"{a.button}: {a.name}" for a in c.abilities]}
             for c in spec.characters}
    frames = []
    for f in res.frames[::2]:  # 15 fps of a 30 Hz sim is smooth enough once interpolated
        frames.append({"t": f["t"],
                       "u": [[n, tm, round(x, 2), round(y, 2), round(d), int(al), int(air), int(ch)] for n, tm, x, y, d, al, air, ch, r in f["units"]],
                       "w": [[round(a[0], 2), round(a[1], 2), round(b[0], 2), round(b[1], 2)] for a, b in f["walls"]],
                       "r": [[round(x, 2), round(y, 2)] for x, y, _ in f["rocks"]],
                       "s": [[round(x, 2), round(y, 2)] for x, y, _ in f["shots"]]})
    events = [{k: v for k, v in e.items() if k in ("t", "type", "src", "dst", "ability", "by", "knockback", "power")}
              for e in res.events if e["type"] in EVENTS]
    units = {u.name: {"team": u.team, "character": u.spec.name} for u in Sim_units(spec)}
    return {"title": title, "game": game, "seed": seed, "radius": spec.game.platform.radius, "winner": res.outcome,
            "duration": round(res.time, 2), "characters": chars, "units": units, "frames": frames, "events": events}


def Sim_units(spec):
    from gamelab.brawl.engine import Sim
    return Sim(spec, record_frames=False).units


def main():
    data = [export(*f) for f in FIGHTS]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.REPLAYS = " + json.dumps(data, separators=(",", ":")) + ";\n")
    for d in data:
        print(f"{d['title']}: winner {d['winner']} in {d['duration']}s, {len(d['frames'])} frames, {len(d['events'])} events")
    print(f"{OUT} ({OUT.stat().st_size / 1e3:.0f} KB)")


if __name__ == "__main__":
    main()
