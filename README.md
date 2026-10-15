# GameLab

Can agents reliably create and modify game mechanics through a constrained, human-readable DSL instead of editing engine source?

Three layers: the **engine** (`src/gamelab/engine.py`) is agent-owned; the **DSL** (`docs/DSL.md`) is shared by human and agent; **design** (what feels fun) stays with the human.

## Use
```bash
uv run gamelab validate games/arena.yaml          # typed validation with fixable error messages
uv run gamelab sim games/arena.yaml --seeds 10     # headless, deterministic simulation
uv run gamelab sim games/arena.yaml --events       # full event log
uv run gamelab play examples/dungeon_arena.yaml    # WASD move, 1-4 abilities, mouse aims
uv run gamelab shots games/arena.yaml              # headless screenshots
```

## Benchmark
`tasks/<id>/` holds `task.yaml` (designer request, allowed changes, checks), `start.yaml` and a reference `solution.yaml`.
Checks are static (DSL values), behavioral (event-log assertions over 5 seeds of the auto player) and regression
(any change outside `allowed_changes`). `tools/make_tasks.py` regenerates the tasks and proves every start fails and every solution passes.

20 tasks: tune (3), behavior (7), content (4), bugfix (3), balance (3).

## Experiments
```bash
uv run gamelab-agent --arm oracle                                   # harness check, no API: 20/20
uv run gamelab-agent --arm dsl          --model claude-opus-5-5     # B: edit DSL, no feedback
uv run gamelab-agent --arm dsl_feedback --model claude-opus-5-5     # C: + validate_game, simulate_game
```
Metrics per run: success, regression, invalid final file, turns, tool calls, tokens, wall time (`results/<run>/summary.json`).
Arm A (agent edits engine source directly) is GameDevBench (`gamedevbench/`, 333 Godot tasks).
Credentials: `ANTHROPIC_API_KEY`, `.env` in this folder, or `ant auth login`.

## Results (details in docs/RESULTS.md)
- v1 arena (20 tasks): DSL only 19/20, DSL + validate/simulate 20/20 (Claude Opus 5.5).
- v2 brawler (18 tasks): DSL only 12/18, DSL + validate/simulate 17/18. Every miss without feedback is a balance task.
- Experiment D, RL "tab model" (Qwen 0.5B on a laptop): 5% base -> 65% after SFT -> 70% after GRPO.

## Brawler (v2)
Ring-out arena fighter: damage %, weight, knockback, walls, orbiting rocks, channeled abilities, jump, J/K/J+K buttons.
`uv run gamelab play games/pro_bending.yaml` (WASD + J/K). Reference: docs/BRAWL.md.

## RL (Experiment D)
`uv run --extra rl python -m gamelab.rl.grpo --sft-steps 40 --steps 30 --out runs/grpo` (Apple silicon, MLX). Claude Code login works as the agent backend: `--backend claude-code`.
