# Results

## Experiment D — RL on a small "tab model" (2026-11-14)

Qwen2.5-0.5B-Instruct + LoRA (rank 16, 16 layers), on an 8 GB Apple M3 with MLX. The model writes edits in a tiny edit
language (`set` / `add` / `remove` lines) instead of rewriting the game file. Reward = the GameLab checker: 1.0 if the edit
validates, does what was asked and touches nothing else; partial credit for some checks; -0.5 for unparseable edits or
invalid games. 60 held-out procedural tasks, disjoint from training.

| Stage | Accuracy | Mean reward | Invalid edits |
|---|---|---|---|
| Base model | 5% | -0.43 | 95% |
| + SFT warm-up (40 steps x 8 reference edits, lr 1e-4) | 65% | 0.58 | 13% |
| + GRPO (30 steps x 4 requests x 6 samples, lr 1e-5), best checkpoint (step 20) | **70%** | **0.69** | **3%** |
| GRPO final step (30) | 62% | 0.55 | 13% |

- SFT teaches the format; GRPO removes most invalid edits (13% -> 3%) and adds 5 points of accuracy.
- GRPO at lr 1e-4 collapsed to 0%. At 1e-5 with held-out checks every 10 steps it improves, then drifts after step 20,
  so the best checkpoint is kept and the last-step number is reported alongside it. Logs: `docs/runs/experiment_d_*`.
- On an 8 GB Mac: project only completion positions onto the 152k vocabulary, evaluate accumulated gradients eagerly,
  and clear MLX's cache after generation, or training swaps.

## v2 brawler — 2026-11-13, Claude Opus 5.5 (medium), headless Claude Code, 18 tasks

| Arm | Success | Regressions | Mean turns | Mean tokens | Cost (equiv.) |
|---|---|---|---|---|---|
| B: DSL only | 12/18 (67%) | 0 | 3.1 | ~13.8k | $0.50 |
| C: DSL + validate/simulate | 17/18 (94%) | 0 | 6.0 | 27.7k | $0.85 |

- v2 is not saturated: without feedback, Opus misses 6/18. All six misses are balance or new-character tasks whose answers
  depend on how numbers interact (friction affects heavy fighters, damage % compounds knockback, a bigger platform shifts
  who wins). B's guesses left one side winning 20/20, 20/20, 1/20, 3/20, 3/20 and 16/20.
- Feedback adds 28 points, at 2x tokens. C iterated up to 20 turns on the hardest one (faster fights while staying fair).
- C's only miss is statistical: on b_balance_wrestler_op it validated with the simulator's default 5 seeds, the grader
  uses 20, and on 20 seeds red won 14/20. Agents that test their own work overfit to small samples; the simulator tool
  could report confidence intervals, or the prompt could ask for 20-seed checks on balance requests.

## v1 re-run after the code-review fixes — same setup, 20 tasks
B 19/20 (miss: balance_swarm_challenge, fight 14.7s vs >= 15s), C 20/20; mean turns 3.1 vs 5.9, tokens 13.0k vs 27.1k.
The original v1 conclusions hold.

## v1 — 2026-11-13, Claude Opus 5.5 (medium effort), headless Claude Code backend, 20 tasks

| Arm | Success | Regressions | Invalid final | Mean turns | Mean tokens | Mean time | Cost (equiv.) |
|---|---|---|---|---|---|---|---|
| B: DSL only | 19/20 (95%) | 0 | 0 | 3.05 | 12.4k | 14.3s | $0.49 |
| C: DSL + validate/simulate | 20/20 (100%) | 0 | 0 | 5.35 | 23.6k | 17.9s | $0.75 |
| Oracle (reference solutions) | 20/20 | 0 | 0 | — | — | — | — |

By category, B vs C: tune 3/3 vs 3/3, behavior 7/7 vs 7/7, content 4/4 vs 4/4, bugfix 3/3 vs 3/3, **balance 2/3 vs 3/3**.

Findings
- Feedback only matters where the answer can't be read off the request. Every miss without feedback is a balance task
  (B's longer-boss-fight ran 90.4s against a 40-90s target); with the simulator, C iterated 10-14 turns on balance tasks and solved all three.
- Feedback roughly doubles cost (1.9x tokens) and is only needed on ~15% of tasks. A router that enables simulation only for
  balance-style requests would get C's accuracy near B's cost.
- v1 is saturated for a frontier model: 95-100% with zero regressions. It cannot separate strong agents.
  Next version needs multi-step requests, ambiguous designer language, and mechanics the DSL can't express (where the right
  answer is to extend the engine or say it's unsupported).
- Not measured on this backend: tool-call counts (Claude Code's JSON output omits them).
