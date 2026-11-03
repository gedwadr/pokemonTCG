#!/usr/bin/env python3
"""Run agents head to head in-process (no server).  Handy for evaluating a submission.

    python scripts/local_match.py --a agents/sample/main.py --b greedy --games 50
    python scripts/local_match.py --a greedy --b random --games 200 --deck-a decks/sample.csv --deck-b decks/sample.csv
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ptcg import bots  # noqa: E402
from ptcg.deck import load_deck, validate_deck  # noqa: E402
from ptcg.engine import Battle  # noqa: E402
from ptcg.matchlog import MatchLogger  # noqa: E402
from ptcg.rllog import RLLogger  # noqa: E402
from ptcg import describe  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "decks" / "sample.csv"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", default="greedy", help="built-in bot or Kaggle-style main.py for player A")
    ap.add_argument("--b", default="random", help="built-in bot or Kaggle-style main.py for player B")
    ap.add_argument("--deck-a", default=None)
    ap.add_argument("--deck-b", default=None)
    ap.add_argument("--games", type=int, default=10)
    ap.add_argument("--max-steps", type=int, default=20000, help="abort a game after this many selections")
    ap.add_argument("--no-swap", action="store_true", help="keep A as player 0 in every game instead of alternating")
    ap.add_argument("--logs", default="logs", help="directory for per-game move logs and results.csv ('' to disable)")
    ap.add_argument("--tag", default="local", help="room name used in log file names")
    ap.add_argument("--rl", default="rl_logs", help="directory for per-decision JSONL logs for RL training ('' to disable)")
    args = ap.parse_args()

    agent_a, deck_a = bots.load_agent(args.a)
    agent_b, deck_b = bots.load_agent(args.b)
    deck_a = load_deck(args.deck_a) if args.deck_a else deck_a or load_deck(SAMPLE)
    deck_b = load_deck(args.deck_b) if args.deck_b else deck_b or load_deck(SAMPLE)
    for label, d in (("A", deck_a), ("B", deck_b)):
        problems = validate_deck(d)
        if problems:
            sys.exit(f"deck {label} illegal: {'; '.join(problems)}")

    name_a = args.a if args.a in bots.BUILTIN else Path(args.a).parent.name or "A"
    name_b = args.b if args.b in bots.BUILTIN else Path(args.b).parent.name or "B"
    if name_a == name_b:
        name_a, name_b = name_a + "-A", name_b + "-B"
    wins = {"A": 0, "B": 0, "draw": 0, "aborted": 0}
    reasons: dict[str, int] = {}
    t0 = time.time()
    total_steps = 0
    for g in range(args.games):
        swap = (g % 2 == 1) and not args.no_swap
        agents = [agent_b, agent_a] if swap else [agent_a, agent_b]
        decks = [deck_b, deck_a] if swap else [deck_a, deck_b]
        b = Battle(decks[0], decks[1])
        names = [name_b, name_a] if swap else [name_a, name_b]
        mlog = MatchLogger(args.logs or None, f"{args.tag}-{g + 1}", names)
        kinds = [f"agent:{n}" for n in names]
        rl = RLLogger(args.rl or None, f"{args.tag}-{g + 1}", names, kinds, [decks[0], decks[1]])
        steps = 0
        while not b.finished and steps < args.max_steps:
            rl.observe(b.obs)
            select = agents[b.current_player](b.obs)
            mlog.move(steps + 1, b.obs, select)
            rl.act(steps + 1, b.obs, select)
            b.select(select)
            steps += 1
        mlog.events(b.obs)
        total_steps += steps
        if not b.finished:
            wins["aborted"] += 1
            outcome = "aborted"
        elif b.result == 2:
            wins["draw"] += 1
            outcome = "draw"
        else:
            winner_is_a = (b.result == 1) if swap else (b.result == 0)
            outcome = "A" if winner_is_a else "B"
            wins[outcome] += 1
        reason = str(b.result_reason)
        reasons[reason] = reasons.get(reason, 0) + 1
        if b.finished:
            reason_text = describe.LOG_RESULT_REASON.get(b.result_reason, reason)
            rl_path = rl.finish(b.obs, b.result, reason_text)
            mlog.result(b.result, reason_text, steps, b.turn, None, str(rl_path) if rl_path else None)
        else:
            mlog.line(f"# ABORTED after {steps} selections (max-steps)")
            mlog.close()
        print(f"game {g + 1:3d}: {outcome:7s} turn {b.turn:3d} steps {steps:4d} reason {reason}")
        b.close()
    dt = time.time() - t0
    print(f"\nA={args.a}  B={args.b}  games={args.games}")
    print(f"A wins {wins['A']}  B wins {wins['B']}  draws {wins['draw']}  aborted {wins['aborted']}")
    print(f"reasons {reasons}  avg steps {total_steps / max(1, args.games):.0f}  {dt:.1f}s total")
    if args.logs:
        print(f"move logs in {args.logs}/, results appended to {args.logs}/results.csv")
    if args.rl:
        print(f"RL decision records in {args.rl}/")


if __name__ == "__main__":
    main()
