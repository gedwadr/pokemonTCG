#!/usr/bin/env python3
"""Generate self-play decision states with RL records (search blob kept) for rollout labeling.

    python scripts/generate_states.py --target 50000 --workers 16 --out data/selfplay/rl

Random pairings among the entrants (seven decks, mixed pilots, plus rule_based_1 and dhelmise_1 on
the Dhelmise deck).  Each game is one JSONL file (meta, step records, result).  Stops when the number
of *labelable* states (single-pick selections with at least two options) reaches --target.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ENTRANTS = {
    "dragapult": ("agents/dragapult_1/main.py", "decks/dragapult_ex.csv"),
    "basic_box": ("agents/generic_1/main.py", "decks/basic_box.csv"),
    "slowking": ("agents/generic_1/main.py", "decks/slowking.csv"),
    "ns_zoroark": ("agents/generic_1/main.py", "decks/ns_zoroark.csv"),
    "alakazam": ("agents/generic_1/main.py", "decks/alakazam.csv"),
    "crustle": ("agents/generic_1/main.py", "decks/crustle.csv"),
    "festival_lead": ("agents/generic_1/main.py", "decks/festival_lead.csv"),
    "dhelmise_rb": ("agents/rule_based_1/main.py", None),
    "dhelmise_mine": ("agents/dhelmise_1/main.py", None),
    "dragapult_generic": ("agents/generic_1/main.py", "decks/dragapult_ex.csv"),
}


def labelable(obs):
    sel = obs.get("select")
    return bool(sel) and sel["maxCount"] == 1 and len(sel["option"]) >= 2


def worker(args):
    wid, n_games, out_dir, seed, max_steps = args
    from ptcg import bots
    from ptcg.deck import load_deck
    from ptcg.engine import Battle
    from ptcg.rllog import RLLogger

    rng = random.Random(seed)
    out = Path(out_dir)
    cache = {}

    def get(name):
        spec, deck_file = ENTRANTS[name]
        agent, own = bots.load_agent(str(ROOT / spec))
        deck = load_deck(ROOT / deck_file) if deck_file else own
        bots.apply_deck(agent, deck)
        return agent, deck

    counts = {"games": 0, "states": 0, "labelable": 0}
    names = list(ENTRANTS)
    for g in range(n_games):
        a, b = rng.sample(names, 2)
        agent_a, deck_a = get(a)
        agent_b, deck_b = get(b)
        gid = f"w{wid:02d}-g{g:04d}-{a}-vs-{b}"
        battle = Battle(deck_a, deck_b)
        rl = RLLogger(out, gid, [a, b], [f"agent:{ENTRANTS[a][0].split('/')[1]}", f"agent:{ENTRANTS[b][0].split('/')[1]}"],
                      [deck_a, deck_b], keep_search_blob=True)
        agents = [agent_a, agent_b]
        steps = 0
        while not battle.finished and steps < max_steps:
            obs = battle.obs
            rl.observe(obs)
            sel = agents[battle.current_player](obs)
            rl.act(steps + 1, obs, sel)
            counts["states"] += 1
            counts["labelable"] += labelable(obs)
            try:
                battle.select(sel)
            except Exception:
                # illegal move from a pilot: play randomly rather than lose the game's data
                battle.select(bots.RandomBot()(obs))
            steps += 1
        if battle.finished:
            from ptcg import describe
            rl.finish(battle.obs, battle.result, describe.LOG_RESULT_REASON.get(battle.result_reason, "?"))
        else:
            rl.finish(battle.obs, 2, "aborted")
        battle.close()
        counts["games"] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=50000, help="labelable states to produce (approximate)")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--out", default="data/selfplay/rl")
    ap.add_argument("--games-per-batch", type=int, default=8, help="games per worker per batch")
    ap.add_argument("--max-steps", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    totals = {"games": 0, "states": 0, "labelable": 0}
    batch = 0
    t0 = time.time()
    with Pool(args.workers) as pool:
        while totals["labelable"] < args.target:
            jobs = [(w, args.games_per_batch, str(out), args.seed * 100003 + batch * 1009 + w, args.max_steps) for w in range(args.workers)]
            for c in pool.map(worker, jobs):
                for k in totals:
                    totals[k] += c[k]
            batch += 1
            print(f"batch {batch}: games {totals['games']}  states {totals['states']}  labelable {totals['labelable']}  ({time.time() - t0:.0f}s)", flush=True)
    print("done:", totals, "->", out)


if __name__ == "__main__":
    main()
