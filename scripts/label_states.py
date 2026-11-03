#!/usr/bin/env python3
"""Label self-play states with determinized rollout values (the "teacher" for a distilled policy).

    python scripts/label_states.py --in data/selfplay/rl --out data/selfplay/labels --workers 16 --determinizations 16

For every single-pick decision with >= 2 options, each option is played in D determinizations of
the hidden cards and rolled out to the end of the game (or a step cap) with the greedy pilot on
both sides.  Output: one JSONL per game, records like
  {"game_id", "step", "turn", "player", "agent", "context", "obs" (no blob), "legal_actions",
   "action" (what the pilot actually played), "label": {"scores": [...], "best": i, ...},
   "reward" (final game outcome for the actor)}
Resumable: games with an existing output file are skipped.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def label_file(args):
    src, dst, determinizations, max_steps, seed = args
    from ptcg import bots, search
    rng = random.Random(seed)
    policy = bots.GreedyBot(seed)
    lines = [json.loads(l) for l in open(src, encoding="utf-8")]
    meta = lines[0]
    decks = meta["decks"]
    # final reward per player from the last step records
    rewards = {}
    for rec in lines:
        if rec.get("type") == "step" and rec.get("done"):
            rewards[rec["player"]] = rec["reward"]
    out_records = []
    n_labeled = 0
    t0 = time.time()
    for rec in lines:
        if rec.get("type") != "step":
            continue
        obs = rec["obs"]
        sel = obs.get("select")
        if not sel or sel["maxCount"] != 1 or len(sel["option"]) < 2 or not obs.get("search_begin_input"):
            continue
        try:
            label = search.label_state(obs, decks, policy, determinizations=determinizations, rng=rng, max_steps=max_steps)
        except search.SearchError as exc:
            label = {"error": str(exc)}
        obs_out = {k: v for k, v in obs.items() if k != "search_begin_input"}
        out_records.append({
            "game_id": rec["game_id"], "step": rec["step"], "turn": rec["turn"], "player": rec["player"], "agent": rec["agent"],
            "context": sel["context"], "obs": obs_out, "legal_actions": sel["option"], "action": rec["action"],
            "action_labels": rec.get("action_labels"), "label": label, "reward": rewards.get(rec["player"], 0.0),
        })
        n_labeled += label is not None and "error" not in label
    tmp = Path(str(dst) + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", **{k: v for k, v in meta.items() if k != "type"}, "determinizations": determinizations}) + "\n")
        for r in out_records:
            fh.write(json.dumps(r) + "\n")
    tmp.rename(dst)
    return str(dst), n_labeled, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", default="data/selfplay/rl")
    ap.add_argument("--out", default="data/selfplay/labels")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--determinizations", type=int, default=16)
    ap.add_argument("--max-steps", type=int, default=200, help="rollout step cap (then prize differential)")
    ap.add_argument("--limit", type=int, default=0, help="only process this many game files (0 = all)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    inp, out = ROOT / args.inp, ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    files = sorted(inp.glob("*.jsonl"))
    todo = [f for f in files if not (out / f.name).exists()]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(files)} game files, {len(todo)} to label, {args.workers} workers, D={args.determinizations}", flush=True)
    jobs = [(str(f), str(out / f.name), args.determinizations, args.max_steps, args.seed + i) for i, f in enumerate(todo)]
    t0 = time.time()
    done = labeled = 0
    with Pool(args.workers) as pool:
        for dst, n, dt in pool.imap_unordered(label_file, jobs):
            done += 1
            labeled += n
            if done % 10 == 0 or done == len(jobs):
                el = time.time() - t0
                print(f"{done}/{len(jobs)} files, {labeled} states labeled, {el:.0f}s elapsed, eta {el / done * (len(jobs) - done):.0f}s", flush=True)
    print(f"done: {labeled} labeled states in {out}")


if __name__ == "__main__":
    main()
