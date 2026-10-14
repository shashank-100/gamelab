# Results

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
