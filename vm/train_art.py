"""GPU job: Experiment D at scale - GRPO on Qwen2.5-7B-Instruct as a GameLab "tab model" (writes DSL edits). VM only.

    uv run python vm/train_art.py --steps 60 --max-hours 2           # on the node
    uv run python vm/train_art.py --fake-client                      # Mac: every code path, scripted model, no GPU

Same current-ART calls as ART's examples/mcp-rl (LocalBackend, register, gather_trajectory_groups, backend.train).
Reward = gamelab.rl.tasks.reward (the GameLab checker). Held-out tasks are disjoint from training.
"""
import argparse
import asyncio
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gamelab.rl import tasks as T  # noqa: E402
from gamelab.rl.prompt import SYSTEM  # noqa: E402


def messages(t):
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"Game file:\n{t.game_yaml()}\nRequest: {t.prompt}"}]


def kind(t):
    p = t.prompt.lower()
    return ("swap" if "swap" in p else "remove" if p.startswith("remove") else "rule" if p.startswith("when the boss")
            else "ability" if p.startswith("give the warrior") else "relative" if "%" in p else "set")


async def rollout(model, t, client, art, temperature=1.0):
    traj = art.Trajectory(messages_and_choices=messages(t), reward=0.0, metadata={"kind": kind(t)})
    resp = await client.chat.completions.create(model=model.get_inference_name(), messages=traj.messages(),
                                                temperature=temperature, max_completion_tokens=200)
    choice = resp.choices[0]
    traj.messages_and_choices.append(choice)
    text = (choice.message.content or "").strip().strip("`").strip()
    r, why = T.reward(t, text)
    traj.reward = r
    traj.metrics.update({"correct": float(r == 1.0), "invalid": float(r < 0)})
    return traj


def summarize(trajs, label, step):
    by = collections.defaultdict(list)
    for x in trajs:
        by[x.metadata["kind"]].append(x)
    avg = lambda xs, f: round(sum(f(x) for x in xs) / max(1, len(xs)), 3)
    out = {"label": label, "step": step, "n": len(trajs), "accuracy": avg(trajs, lambda x: x.metrics["correct"]),
           "mean_reward": avg(trajs, lambda x: x.reward), "invalid_rate": avg(trajs, lambda x: x.metrics["invalid"])}
    out.update({f"acc_{k}": avg(v, lambda x: x.metrics["correct"]) for k, v in sorted(by.items())})
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen2.5-7B-Instruct")
    ap.add_argument("--name", default="gamelab-tab-7b")
    ap.add_argument("--steps", type=int, default=60)
    ap.add_argument("--groups-per-step", type=int, default=16)
    ap.add_argument("--rollouts-per-group", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--eval-n", type=int, default=200)
    ap.add_argument("--eval-every", type=int, default=15)
    ap.add_argument("--max-hours", type=float, default=0)
    ap.add_argument("--fake-client", action="store_true")
    a = ap.parse_args()
    out = ROOT / "results" / f"{a.name}_{time.strftime('%Y%m%d-%H%M')}"
    out.mkdir(parents=True, exist_ok=True)
    evals = []

    def save(rec):
        evals.append(rec)
        print(json.dumps(rec), flush=True)
        (out / "evals.json").write_text(json.dumps(evals, indent=1))

    val = T.dataset(a.eval_n, seed=12345)  # same held-out generator seed as the laptop run
    train = T.dataset(a.steps * a.groups_per_step, seed=7)

    if a.fake_client:
        from fake_client import FakeArt, FakeClient, FakeModel
        art, model, client = FakeArt, FakeModel(), FakeClient(val)
        trajs = [await rollout(model, t, client, art) for t in val[:12]]
        save(summarize(trajs, "fake-client check", 0))
        print("FAKE-CLIENT CHECK PASSED: prompt, rollout, edit parsing, reward and summaries all ran")
        return

    import art
    from art.local.backend import LocalBackend
    from art.utils import iterate_dataset
    from openai import AsyncOpenAI

    model = art.TrainableModel(name=a.name, project="gamelab", base_model=a.model)
    backend = LocalBackend()
    await model.register(backend)
    client = AsyncOpenAI(api_key=model.inference_api_key, base_url=model.inference_base_url)

    async def run_eval(label, step):
        groups = await art.gather_trajectory_groups(
            (art.TrajectoryGroup([rollout(model, t, client, art, temperature=0.0)]) for t in val), pbar_desc=label)
        save(summarize([x for g in groups for x in g.trajectories], label, step))

    pre = [await rollout(model, t, client, art) for t in val[:2]]
    if not all(len(x.messages_and_choices) == 3 for x in pre):
        raise SystemExit("PREFLIGHT FAILED: no completion")
    print("PREFLIGHT OK", [x.reward for x in pre], flush=True)

    t0 = time.time()
    await run_eval("base", 0)
    eval_secs = time.time() - t0
    for batch in iterate_dataset(train, groups_per_step=a.groups_per_step, num_epochs=1, initial_step=await model.get_step()):
        if batch.step >= a.steps:
            break
        if a.max_hours and (time.time() - t0) + 1.5 * eval_secs > a.max_hours * 3600:
            print(json.dumps({"stopped_early": True, "at_step": batch.step}), flush=True)
            break
        groups = await art.gather_trajectory_groups(
            (art.TrajectoryGroup(rollout(model, t, client, art) for _ in range(a.rollouts_per_group)) for t in batch.items),
            pbar_desc=f"train step {batch.step}")
        result = await backend.train(model, groups, learning_rate=a.lr)
        await model.log(groups, metrics=result.metrics, step=result.step, split="train")
        rs = [x.reward for g in groups for x in g.trajectories]
        print(json.dumps({"step": batch.step, "mean_reward": round(sum(rs) / len(rs), 3)}), flush=True)
        if (batch.step + 1) % a.eval_every == 0:
            await run_eval(f"step{batch.step + 1}", batch.step + 1)
    await run_eval("final", a.steps)
    print("DONE results in", out, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
