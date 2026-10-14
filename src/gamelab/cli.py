"""gamelab validate|sim|play|watch|shots <game.yaml>"""
import argparse
import json
import sys

from . import dsl
from . import engines


def main(argv=None):
    p = argparse.ArgumentParser(prog="gamelab")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("validate", "sim", "play", "watch", "shots", "agent-sim"):
        s = sub.add_parser(name)
        s.add_argument("file")
        if name == "sim":
            s.add_argument("--seeds", type=int, default=1, help="run seeds game.seed .. game.seed+N-1")
            s.add_argument("--events", action="store_true", help="print the full event log")
            s.add_argument("--trace", help="write frames + events JSON here")
        if name == "agent-sim":
            s.add_argument("--seeds", type=int, default=5)
            s.add_argument("--events", default="", help="comma-separated event types to list from seed 1")
        if name == "shots":
            s.add_argument("--out", default="shots")
            s.add_argument("--every", type=float, default=1.0)
    a = p.parse_args(argv)

    src = open(a.file).read()
    rep = engines.validate(src)
    if a.cmd == "validate" or not rep.ok:
        print(rep.text())
        return 0 if rep.ok else 1
    for w in rep.warnings:
        print(f"warning: {w}", file=sys.stderr)
    spec = rep.spec

    if a.cmd == "agent-sim":
        from .agent import Workspace

        ws = Workspace(src)
        print(ws.call("simulate_game", {"seeds": max(1, min(a.seeds, 10)), "event_types": [e for e in a.events.split(",") if e]}))
        return 0
    if a.cmd == "sim":
        outs = []
        for i in range(a.seeds):
            res = engines.run(spec, i, record_frames=bool(a.trace))
            outs.append(res.outcome)
            if a.seeds == 1:
                print(json.dumps(res.summary(), indent=2))
                if a.events:
                    for e in res.events:
                        print(json.dumps(e))
                if a.trace:
                    json.dump({"spec": spec.model_dump(), "events": res.events, "frames": res.frames}, open(a.trace, "w"))
        if a.seeds > 1:
            print(json.dumps({o: outs.count(o) for o in sorted(set(outs))}))
        return 0
    from . import render

    if a.cmd == "shots":
        print(render.run(spec, mode="watch", screenshot_dir=a.out, every=a.every))
    else:
        print(render.run(spec, mode=a.cmd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
