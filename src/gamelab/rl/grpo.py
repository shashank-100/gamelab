"""Experiment D: GRPO on a small model that writes GameLab edits (the "tab model"), on Apple silicon with MLX.

    uv run --extra rl python -m gamelab.rl.grpo --steps 60 --out runs/grpo1

Every step: sample G edits for each of B requests, score them with the checker (rl/tasks.reward), normalize rewards
within each request's group (GRPO), and push up the log-probability of above-average edits. Groups where every sample
got the same reward carry no signal and are skipped. Held-out accuracy is measured before and after training.
"""
import argparse
import json
import random
import time
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
from mlx.utils import tree_flatten, tree_map
from mlx_lm import load, stream_generate
from mlx_lm.sample_utils import make_sampler
from mlx_lm.tuner.utils import linear_to_lora_layers

from . import tasks

SYSTEM = """You edit game files written in a YAML game DSL. Reply ONLY with edit lines, no prose, no code fences:
  set <path> = <value>     replace or create a field
  add <path> = <value>     append to a list (value in JSON/YAML flow style)
  remove <path>            delete a field or a named list item
Paths use dots for fields and [name] for list items, e.g. characters[warrior].abilities[fireball].damage or enemies[boss].behavior
Behavior rules look like {"when": "health < 30%", "action": "teleport", "cooldown": 5}; actions: chase, flee, idle, teleport, enrage, heal, summon.
Arena abilities: {"name": ..., "type": heal|aoe|dash|projectile|melee, "cooldown": ..., plus amount / damage+radius / distance}."""


def prompt_ids(tok, t: tasks.EditTask):
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Game file:\n{t.game_yaml()}\nRequest: {t.prompt}"}]
    return tok.apply_chat_template(msgs, add_generation_prompt=True)


def sample(model, tok, ids, temp, max_tokens=120):
    out_ids, text = [], ""
    for r in stream_generate(model, tok, ids, max_tokens=max_tokens, sampler=make_sampler(temp=temp)):
        out_ids.append(r.token)
        text += r.text
    eos = set(tok.eos_token_ids) if hasattr(tok, "eos_token_ids") else {tok.eos_token_id}
    mx.clear_cache()  # generation leaves KV-cache buffers in MLX's cache; on 8 GB they push training into swap
    return [i for i in out_ids if i not in eos], text.strip().strip("`").strip()


def seq_logprob(model, ids, comp):
    """Mean log-prob of the completion. The vocabulary projection is applied only at completion positions:
    projecting all ~800 prompt positions onto a 152k vocab costs ~0.5 GB per sample and swaps an 8 GB Mac."""
    x = mx.array([ids + comp])
    h = model.model(x[:, :-1])[:, len(ids) - 1:]
    head = model.lm_head if hasattr(model, "lm_head") else model.model.embed_tokens.as_linear
    lp = nn.log_softmax(head(h).astype(mx.float32), axis=-1)
    tgt = mx.array([comp])
    return mx.take_along_axis(lp, tgt[..., None], axis=-1)[..., 0].mean()


def evaluate(model, tok, ds, label):
    rs, exact = [], 0
    for t in ds:
        _, text = sample(model, tok, prompt_ids(tok, t), temp=0.0)
        r, _ = tasks.reward(t, text)
        rs.append(r)
        exact += r == 1.0
    out = {"label": label, "n": len(ds), "accuracy": round(exact / len(ds), 3), "mean_reward": round(sum(rs) / len(rs), 3),
           "invalid_rate": round(sum(r < 0 for r in rs) / len(rs), 3)}
    print(json.dumps(out), flush=True)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    p.add_argument("--steps", type=int, default=60)
    p.add_argument("--batch", type=int, default=4, help="requests per step")
    p.add_argument("--group", type=int, default=8, help="samples per request")
    p.add_argument("--lr", type=float, default=1e-4, help="SFT learning rate")
    p.add_argument("--rl-lr", type=float, default=1e-5, help="GRPO learning rate (10x lower: 1e-4 made GRPO diverge)")
    p.add_argument("--eval-every", type=int, default=10)
    p.add_argument("--temp", type=float, default=1.0)
    p.add_argument("--lora-layers", type=int, default=16)
    p.add_argument("--rank", type=int, default=16)
    p.add_argument("--eval-n", type=int, default=100)
    p.add_argument("--out", default="runs/grpo")
    p.add_argument("--sft-steps", type=int, default=0, help="supervised warm-up steps on reference edits before RL")
    p.add_argument("--sft-batch", type=int, default=8)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    mx.set_cache_limit(512 * 1024 * 1024)
    mx.random.seed(0)
    random.seed(0)

    model, tok = load(a.model)
    model.freeze()
    linear_to_lora_layers(model, a.lora_layers, {"rank": a.rank, "scale": 20.0, "dropout": 0.0})
    n_train = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
    print(f"model {a.model}, trainable LoRA params {n_train:,}", flush=True)

    heldout = tasks.dataset(a.eval_n, seed=12345)
    train = tasks.dataset(a.steps * a.batch, seed=7)
    log = open(out / "log.jsonl", "w")
    before = evaluate(model, tok, heldout, "before")
    log.write(json.dumps(before) + "\n")

    if a.sft_steps:
        sft_data = tasks.dataset(a.sft_steps * a.sft_batch, seed=99)  # disjoint seeds from train (7) and held-out (12345)
        sft_opt = optim.Adam(learning_rate=a.lr)
        eos = tok.eos_token_id

        def sft_loss(model, ids, comp):
            return -seq_logprob(model, ids, comp)

        sft_grad = nn.value_and_grad(model, sft_loss)
        for step in range(a.sft_steps):
            acc, tot = None, 0.0
            for t in sft_data[step * a.sft_batch:(step + 1) * a.sft_batch]:
                comp = tok.encode(t.solution, add_special_tokens=False) + [eos]
                l, g = sft_grad(model, prompt_ids(tok, t), comp)
                tot += l.item()
                acc = g if acc is None else tree_map(lambda x, y: x + y, acc, g)
                mx.eval(acc)  # evaluate now: a lazy sum would keep every example's activations alive
            sft_opt.update(model, tree_map(lambda x: x / a.sft_batch, acc))
            mx.eval(model.parameters(), sft_opt.state)
            if True:
                rec = {"sft_step": step, "nll": round(tot / a.sft_batch, 4)}
                print(json.dumps(rec), flush=True)
                log.write(json.dumps(rec) + "\n")
        mid = evaluate(model, tok, heldout, "after_sft")
        log.write(json.dumps(mid) + "\n")

    opt = optim.Adam(learning_rate=a.rl_lr)
    best = mid if a.sft_steps else before
    best_weights = dict(tree_flatten(model.trainable_parameters()))
    best_step = -1

    def loss_fn(model, ids, comp, adv):
        return -adv * seq_logprob(model, ids, comp)

    grad_fn = nn.value_and_grad(model, loss_fn)
    for step in range(a.steps):
        t0 = time.time()
        batch = train[step * a.batch:(step + 1) * a.batch]
        grads_acc, n_used, all_r = None, 0, []
        for t in batch:
            ids = prompt_ids(tok, t)
            samples = [sample(model, tok, ids, a.temp) for _ in range(a.group)]
            rs = [tasks.reward(t, text)[0] for _, text in samples]
            all_r += rs
            mean = sum(rs) / len(rs)
            std = (sum((r - mean) ** 2 for r in rs) / len(rs)) ** 0.5
            if std < 1e-6:
                continue  # every sample scored the same: no signal
            for (comp, _), r in zip(samples, rs):
                if not comp:
                    continue
                adv = (r - mean) / (std + 1e-4)
                _, g = grad_fn(model, ids, comp, mx.array(adv / a.group))
                grads_acc = g if grads_acc is None else tree_map(lambda x, y: x + y, grads_acc, g)
                mx.eval(grads_acc)
                n_used += 1
        if grads_acc is not None:
            grads_acc = tree_map(lambda x: x / len(batch), grads_acc)
            opt.update(model, grads_acc)
            mx.eval(model.parameters(), opt.state)
        rec = {"step": step, "mean_reward": round(sum(all_r) / len(all_r), 3), "correct": round(sum(r == 1 for r in all_r) / len(all_r), 3),
               "samples_with_signal": n_used, "sec": round(time.time() - t0, 1)}
        print(json.dumps(rec), flush=True)
        log.write(json.dumps(rec) + "\n")
        log.flush()
        if (step + 1) % a.eval_every == 0 and step + 1 < a.steps:
            ck = evaluate(model, tok, heldout, f"step{step + 1}")
            log.write(json.dumps(ck) + "\n")
            if ck["mean_reward"] > best["mean_reward"]:
                best, best_step = ck, step + 1
                best_weights = dict(tree_flatten(model.trainable_parameters()))
    after = evaluate(model, tok, heldout, "after")
    if after["mean_reward"] < best["mean_reward"]:  # keep the best checkpoint, report both honestly
        model.load_weights(list(best_weights.items()), strict=False)
        after = {**best, "label": f"best (step {best_step})", "final_step_eval": after}
    log.write(json.dumps(after) + "\n")
    model.save_weights(str(out / "adapters.safetensors"))
    (out / "summary.json").write_text(json.dumps({"before": before, "after_sft": mid if a.sft_steps else None, "after": after, "args": vars(a)}, indent=2))


if __name__ == "__main__":
    main()
