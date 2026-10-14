"""Run an LLM agent on GameLab tasks and score it.

Arms (the experiment conditions):
  dsl           B: the agent can read/edit game.yaml, but gets no validation or simulation feedback
  dsl_feedback  C: B + validate_game and simulate_game tools
  oracle        writes the reference solution through the same tool interface (no API; harness check)

Usage: gamelab-agent --arm dsl_feedback --tasks all --model claude-opus-5-5 --effort medium
"""
import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import bench, dsl
from .engine import simulate

ROOT = Path(__file__).resolve().parents[2]
DSL_DOC = (ROOT / "docs" / "DSL.md").read_text()
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM = f"""You modify games written in the GameLab DSL. The game is a single file, game.yaml.
A designer asks for a change; make exactly that change by editing game.yaml with your tools.

Rules:
- Change only what the request needs. Anything else you change counts as a regression.
- The file must stay valid DSL.
- When you are done, reply with one or two sentences saying what you changed, and make no further tool calls.

{DSL_DOC}"""


def _schema(props, required):
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


TOOLS = {
    "read_game": {
        "name": "read_game",
        "description": "Return the current contents of game.yaml. Call this before editing.",
        "input_schema": _schema({}, []),
    },
    "write_game": {
        "name": "write_game",
        "description": "Replace game.yaml with the full new contents.",
        "input_schema": _schema({"content": {"type": "string", "description": "complete YAML file"}}, ["content"]),
    },
    "edit_game": {
        "name": "edit_game",
        "description": "Replace one exact occurrence of old_text in game.yaml with new_text. old_text must match exactly once.",
        "input_schema": _schema({"old_text": {"type": "string"}, "new_text": {"type": "string"}}, ["old_text", "new_text"]),
    },
    "validate_game": {
        "name": "validate_game",
        "description": "Check game.yaml against the DSL. Returns OK plus warnings, or the exact errors to fix. Call after every edit.",
        "input_schema": _schema({}, []),
    },
    "simulate_game": {
        "name": "simulate_game",
        "description": "Run the current game.yaml with the auto player for several seeds and report outcomes, fight length, "
                       "damage, and event counts. Pass event_types to also get those events from the first seed "
                       "(e.g. [\"summon\", \"teleport\", \"mode\", \"enrage\"]). Use it to confirm a behavior actually happens.",
        "input_schema": _schema({
            "seeds": {"type": "integer", "minimum": 1, "maximum": 10, "description": "number of seeds, default 5"},
            "event_types": {"type": "array", "items": {"type": "string"}, "description": "event types to list from seed 1"},
        }, []),
    },
}
ARM_TOOLS = {"dsl": ["read_game", "write_game", "edit_game"],
             "dsl_feedback": ["read_game", "write_game", "edit_game", "validate_game", "simulate_game"]}


class Workspace:
    def __init__(self, src):
        self.src = src
        self.writes = 0
        self.invalid_writes = 0

    def _wrote(self):
        self.writes += 1
        if not dsl.validate(self.src).ok:
            self.invalid_writes += 1

    def call(self, name, args):
        if name == "read_game":
            return self.src
        if name == "write_game":
            self.src = args["content"]
            self._wrote()
            return "game.yaml written"
        if name == "edit_game":
            n = self.src.count(args["old_text"])
            if n != 1:
                raise ValueError(f"old_text matches {n} times; it must match exactly once")
            self.src = self.src.replace(args["old_text"], args["new_text"])
            self._wrote()
            return "edit applied"
        if name == "validate_game":
            return dsl.validate(self.src).text()
        if name == "simulate_game":
            rep = dsl.validate(self.src)
            if not rep.ok:
                return "cannot simulate, the game is invalid:\n" + rep.text()
            spec, n = rep.spec, args.get("seeds") or 5
            runs = [simulate(spec.model_copy(update={"game": spec.game.model_copy(update={"seed": spec.game.seed + i})}), record_frames=False) for i in range(n)]
            out = {"outcomes": [r.outcome for r in runs], "fight_seconds": [round(r.time, 1) for r in runs], "seed1_summary": runs[0].summary()}
            if args.get("event_types"):
                out["seed1_events"] = [e for e in runs[0].events if e["type"] in set(args["event_types"])][:60]
            return json.dumps(out)
        raise ValueError(f"unknown tool {name}")


@dataclass
class RunResult:
    task: str
    arm: str
    model: str
    passed: bool = False
    valid: bool = False
    regression: bool = False
    checks_passed: int = 0
    checks_total: int = 0
    turns: int = 0
    tool_calls: int = 0
    writes: int = 0
    invalid_writes: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0
    stop: str = ""
    error: str = ""
    final_message: str = ""
    verdict: str = ""
    failed_checks: list = field(default_factory=list)


def _client():
    import anthropic

    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return anthropic.Anthropic()


def run_task(task, arm, model, effort, max_turns=30, client=None) -> tuple[RunResult, str]:
    res = RunResult(task["id"], arm, model)
    ws = Workspace(task["_start_src"])
    t0 = time.time()
    if arm == "oracle":
        ws.call("write_game", {"content": (task["_dir"] / "solution.yaml").read_text()})
        res.tool_calls, res.turns, res.stop = 1, 1, "oracle"
    else:
        import anthropic

        tools = [{**TOOLS[n], "strict": True} for n in ARM_TOOLS[arm]]
        messages = [{"role": "user", "content": f"Request from the designer:\n\n{task['prompt']}"}]
        try:
            while res.turns < max_turns:
                resp = client.beta.messages.create(
                    model=model, max_tokens=16000,
                    system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                    tools=tools, messages=messages,
                    output_config={"effort": effort},
                    betas=[FALLBACK_BETA], extra_body={"fallbacks": "default"},
                )
                res.turns += 1
                u = resp.usage
                res.input_tokens += u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
                res.cache_read_tokens += u.cache_read_input_tokens or 0
                res.output_tokens += u.output_tokens
                res.stop = resp.stop_reason
                messages.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason in ("refusal", "max_tokens"):
                    break
                uses = [b for b in resp.content if b.type == "tool_use"]
                if not uses:
                    res.final_message = " ".join(b.text for b in resp.content if b.type == "text").strip()
                    break
                results = []
                for b in uses:
                    res.tool_calls += 1
                    try:
                        out = ws.call(b.name, b.input)
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
                    except Exception as e:  # tool errors go back to the model
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"error: {e}", "is_error": True})
                messages.append({"role": "user", "content": results})
            else:
                res.stop = "max_turns"
        except anthropic.APIStatusError as e:
            res.error = f"{type(e).__name__} {e.status_code}: {e.message}"
        except anthropic.APIConnectionError as e:
            res.error = f"connection: {e}"
    res.seconds = round(time.time() - t0, 2)
    res.writes, res.invalid_writes = ws.writes, ws.invalid_writes
    v = bench.evaluate(task, ws.src)
    res.passed, res.valid, res.regression = v.passed, v.valid, v.regression
    res.checks_total, res.checks_passed = len(v.checks), sum(ok for ok, _ in v.checks)
    res.failed_checks = [d for ok, d in v.checks if not ok] + [f"out of scope: {p}" for p in v.out_of_scope] + v.errors
    res.verdict = v.text()
    return res, ws.src


CC_SYSTEM = {
    "dsl": """You modify games written in the GameLab DSL. The game is the file game.yaml in the current directory.
A designer asks for a change; make exactly that change by editing game.yaml with the Read, Edit and Write tools.

Rules:
- Change only what the request needs. Anything else you change counts as a regression.
- The file must stay valid DSL.
- When you are done, reply with one or two sentences saying what you changed.
""",
}
CC_SYSTEM["dsl_feedback"] = CC_SYSTEM["dsl"] + """
You can check your work with two commands (run them with Bash, from the current directory):
- ./validate                         checks game.yaml against the DSL; prints OK or the exact errors. Run it after every edit.
- ./simulate [seeds] [event_types]   runs the game with the auto player, e.g. `./simulate 5 summon,teleport`; prints outcomes,
                                     fight length, damage, event counts, and the listed event types from seed 1.
"""


def run_task_cc(task, arm, model, effort, root, budget_usd=2.0):
    """Same task, run through headless Claude Code (`claude -p`), using the machine's Claude Code login."""
    import subprocess
    import tempfile

    res = RunResult(task["id"], arm, model)
    d = Path(tempfile.mkdtemp(prefix=f"gamelab-{task['id']}-", dir=root))
    (d / "game.yaml").write_text(task["_start_src"])
    py = ROOT / ".venv" / "bin" / "python"
    tools, allowed = "Read,Edit,Write", ["Read", "Edit", "Write"]
    if arm == "dsl_feedback":
        for name, body in (("validate", f'exec "{py}" -m gamelab.cli validate game.yaml'),
                           ("simulate", f'exec "{py}" -m gamelab.cli agent-sim game.yaml --seeds "${{1:-5}}" --events "${{2:-}}"')):
            f = d / name
            f.write_text(f"#!/bin/sh\n{body}\n")
            f.chmod(0o755)
        tools += ",Bash"
        allowed += ["Bash(./validate)", "Bash(./simulate:*)", "Bash(./simulate)"]
    cmd = ["claude", "-p", f"Request from the designer:\n\n{task['prompt']}",
           "--output-format", "json", "--model", model, "--effort", effort,
           "--system-prompt", CC_SYSTEM[arm] + "\n" + DSL_DOC,
           "--tools", tools, "--allowedTools", ",".join(allowed),
           "--restricted", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
           "--max-budget-usd", str(budget_usd)]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE")}
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=1200, env=env)
        out = json.loads(p.stdout) if p.stdout.strip().startswith("{") else {}
        if not out:
            res.error = (p.stderr or p.stdout).strip()[:300]
        u = out.get("usage") or {}
        res.turns = out.get("num_turns", 0)
        res.input_tokens = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
        res.cache_read_tokens = u.get("cache_read_input_tokens", 0)
        res.output_tokens = u.get("output_tokens", 0)
        res.stop = out.get("subtype", "")
        res.final_message = (out.get("result") or "")[:500]
        if out.get("is_error"):
            res.error = res.error or res.stop
        res.cost_usd = out.get("total_cost_usd", 0.0)
    except subprocess.TimeoutExpired:
        res.error = "timeout"
    res.seconds = round(time.time() - t0, 2)
    final = (d / "game.yaml").read_text()
    res.writes = int(final != task["_start_src"])
    v = bench.evaluate(task, final)
    res.passed, res.valid, res.regression = v.passed, v.valid, v.regression
    res.checks_total, res.checks_passed = len(v.checks), sum(ok for ok, _ in v.checks)
    res.failed_checks = [d_ for ok, d_ in v.checks if not ok] + [f"out of scope: {p_}" for p_ in v.out_of_scope] + v.errors
    res.verdict = v.text()
    return res, final


def main(argv=None):
    p = argparse.ArgumentParser(prog="gamelab-agent")
    p.add_argument("--arm", choices=["dsl", "dsl_feedback", "oracle"], required=True)
    p.add_argument("--tasks", default="all", help="'all', a category, or comma-separated task ids")
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--max-turns", type=int, default=30)
    p.add_argument("--out", default="results")
    p.add_argument("--backend", default="api", choices=["api", "claude-code"],
                   help="api: Anthropic SDK (needs an API key); claude-code: headless `claude -p` using this machine's Claude Code login")
    a = p.parse_args(argv)

    tasks = bench.all_tasks(ROOT / "tasks")
    if a.tasks != "all":
        want = set(a.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in want or t["category"] in want]
    client = None if a.arm == "oracle" or a.backend == "claude-code" else _client()
    if client is not None:
        import anthropic

        try:  # free preflight so a missing key fails once, clearly
            client.models.retrieve(a.model)
        except (TypeError, anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            raise SystemExit(f"No working Claude API credentials ({type(e).__name__}).\n"
                             f"Set ANTHROPIC_API_KEY (or put it in {ROOT / '.env'}), or run `ant auth login`.")
    tag = a.arm if a.arm == "oracle" else f"{a.arm}__{a.model}__{a.effort}__{a.backend}"
    import tempfile
    ws_root = tempfile.mkdtemp(prefix="gamelab-runs-")
    out = ROOT / a.out / f"{time.strftime('%Y%m%d-%H%M%S')}__{tag}"
    out.mkdir(parents=True)
    rows = []
    for t in tasks:
        if a.backend == "claude-code" and a.arm != "oracle":
            r, final = run_task_cc(t, a.arm, a.model, a.effort, ws_root)
        else:
            r, final = run_task(t, a.arm, a.model, a.effort, a.max_turns, client)
        rows.append(r)
        (out / f"{t['id']}.yaml").write_text(final)
        (out / f"{t['id']}.json").write_text(json.dumps(asdict(r), indent=2))
        print(f"{'PASS' if r.passed else 'FAIL'} {t['id']:28s} turns={r.turns:2d} tools={r.tool_calls:2d} "
              f"tok={r.input_tokens + r.output_tokens:6d} {r.seconds:6.1f}s" + (f"  [{r.error or r.stop}]" if r.error or r.stop not in ("end_turn", "oracle") else "")
              + ("" if r.passed else f"  -> {(r.failed_checks or ['?'])[0][:90]}"), flush=True)
    n = len(rows)
    summary = {
        "arm": a.arm, "model": a.model, "effort": a.effort, "backend": a.backend, "tasks": n,
        "success_rate": round(sum(r.passed for r in rows) / n, 3),
        "regression_rate": round(sum(r.regression for r in rows) / n, 3),
        "invalid_final_rate": round(sum(not r.valid for r in rows) / n, 3),
        "mean_turns": round(sum(r.turns for r in rows) / n, 2),
        "mean_tool_calls": round(sum(r.tool_calls for r in rows) / n, 2),
        "mean_tokens": round(sum(r.input_tokens + r.output_tokens for r in rows) / n),
        "mean_seconds": round(sum(r.seconds for r in rows) / n, 2),
        "total_cost_usd": round(sum(r.cost_usd for r in rows), 3),
        "by_category": {c: f"{sum(r.passed for r in rows if r.task in ids)}/{len(ids)}"
                        for c, ids in {c: {t['id'] for t in tasks if t['category'] == c} for c in sorted({t['category'] for t in tasks})}.items()},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    print(f"results: {out}")


if __name__ == "__main__":
    main()
