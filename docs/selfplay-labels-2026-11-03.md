# Self-play states with rollout labels (2026-11-03)

Generated with `scripts/generate_states.py --target 50000 --workers 16` and labeled with
`scripts/label_states.py --workers 16 --determinizations 16`. Data lives under `data/selfplay/`
and is not committed.

| | |
|---|---|
| Games | 256 (random pairings of 10 entrants) |
| Decision states recorded | 62,819 (`data/selfplay/rl/`, 390 MB, includes search blobs) |
| Labeled states | 50,830 single-pick decisions with >= 2 options (`data/selfplay/labels/`, 258 MB) |
| Label errors | 0 |
| Labeling wall time | 8,381 s on 16 workers (~2.6 s/state) |
| Rollouts per option | 16 determinizations x 1 rollout, GreedyBot both sides |

## Label format

One JSONL per game. Line 1 is `{"type":"meta", players, agents, decks}`. Each following record:

```
game_id, step, turn, player, agent, context, obs (no search blob),
legal_actions, action (what the pilot played), action_labels,
label: {scores: [mean rollout value per option], best, determinizations, rollouts_per_option},
reward (final game outcome for this player)
```

Scores are win probabilities from the acting player's point of view (1 win, 0 loss, 0.5 draw;
truncated rollouts use 0.5 + prize difference / 12).

## Statistics

- Pilot action == rollout-best option: 27.9% overall (generic_1 28.0%, dragapult_1 29.3%,
  dhelmise_1 26.3%, rule_based_1 23.5%).
- Ties at the top score: 25.9% of states. Scores are multiples of 1/16, so `best` is an
  arbitrary pick among tied options there. Train on the full `scores` vector (soft targets or
  a ranking loss), not on `best` alone.
- Median gap between best and second-best option: 0.047. Only 20.7% of states have a gap >= 0.1,
  and 37.8% have a best-to-worst spread >= 0.2. Most decisions are near-indifferent under this
  teacher; the clear-margin subset is the most valuable for distillation.
- Option counts: 2 options 21%, 3 17%, 4 12%, 5 13%, 6 10%, 7 5%, 8+ 23%.
- Select contexts: main-phase action 34,342 (agree 25%), return-to-deck 6,357 (30%),
  add-to-hand/search 5,043 (36%), damage placement 952, bench promotion 949, switch 606,
  activate-effect 413 (59%), go-first 256 (57%).

## Caveats

- The teacher is weak: GreedyBot rollouts with 16 samples are noisy and biased toward
  greedy play. Raising `--determinizations` or using a stronger rollout policy improves
  labels at linear cost.
- Multi-pick selections (bench placement, multi-discard) are recorded in `rl/` but unlabeled.
- Opponent hidden cards are sampled from the true remaining multiset of each deck, which is
  slightly more informed than a real opponent model would be.
