#!/usr/bin/env python3
"""Round-robin between decks (each piloted by an agent) plus every deck against rule_based_1.

    python scripts/tournament.py --games 500 --jobs 8 --out logs/tournament

Pairings are run as parallel `scripts/local_match.py` processes, one directory per pairing under
--out (text move logs + results.csv; RL records off by default, --rl to enable).  A summary with a
win matrix is written to <out>/summary.md and printed.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# deck -> (agent spec, deck file or None for the agent's own deck)
DEFAULT_ENTRANTS = {
    "dragapult": ("agents/dragapult_1/main.py", "decks/dragapult_ex.csv"),
    "basic_box": ("agents/generic_1/main.py", "decks/basic_box.csv"),
    "slowking": ("agents/generic_1/main.py", "decks/slowking.csv"),
    "ns_zoroark": ("agents/generic_1/main.py", "decks/ns_zoroark.csv"),
    "alakazam": ("agents/generic_1/main.py", "decks/alakazam.csv"),
    "crustle": ("agents/generic_1/main.py", "decks/crustle.csv"),
    "festival_lead": ("agents/generic_1/main.py", "decks/festival_lead.csv"),
}
BOT = ("rule_based", "agents/rule_based_1/main.py", None)


def run_pair(a, b, games, out, rl, max_steps):
    name_a, agent_a, deck_a = a
    name_b, agent_b, deck_b = b
    tag = f"{name_a}__vs__{name_b}"
    d = out / tag
    d.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(ROOT / "scripts" / "local_match.py"), "--a", agent_a, "--b", agent_b, "--games", str(games),
           "--tag", tag, "--logs", str(d), "--rl", str(d / "rl") if rl else "", "--max-steps", str(max_steps)]
    if deck_a:
        cmd += ["--deck-a", deck_a]
    if deck_b:
        cmd += ["--deck-b", deck_b]
    t0 = time.time()
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    (d / "stdout.txt").write_text(res.stdout + ("\n--- stderr ---\n" + res.stderr if res.stderr else ""))
    wins = {"A": 0, "B": 0, "draw": 0, "aborted": 0}
    rows = list(csv.DictReader((d / "results.csv").open())) if (d / "results.csv").exists() else []
    label_a, label_b = player_labels(agent_a, agent_b)
    for r in rows:
        if r["winner"] == "draw":
            wins["draw"] += 1
        elif r["winner"] == label_a:
            wins["A"] += 1
        elif r["winner"] == label_b:
            wins["B"] += 1
    return tag, name_a, name_b, wins, len(rows), time.time() - t0, res.returncode


def player_labels(agent_a, agent_b):
    """The player names local_match.py uses: the agent folder, suffixed -A/-B when both sides share one agent."""
    la, lb = Path(agent_a).parent.name, Path(agent_b).parent.name
    if la == lb:
        la, lb = la + "-A", lb + "-B"
    return la, lb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=500)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--out", default="logs/tournament")
    ap.add_argument("--rl", action="store_true", help="also write RL JSONL records (large)")
    ap.add_argument("--max-steps", type=int, default=4000)
    ap.add_argument("--decks", default=",".join(DEFAULT_ENTRANTS), help="comma-separated subset of entrants")
    ap.add_argument("--no-bot", action="store_true", help="skip the rule_based_1 matches")
    args = ap.parse_args()
    out = ROOT / args.out
    entrants = [(n, *DEFAULT_ENTRANTS[n]) for n in args.decks.split(",") if n]
    pairs = list(itertools.combinations(entrants, 2))
    if not args.no_bot:
        pairs += [(e, BOT) for e in entrants]
    print(f"{len(pairs)} pairings x {args.games} games, {args.jobs} parallel jobs -> {out}")
    results = []
    with ThreadPoolExecutor(args.jobs) as ex:
        futs = [ex.submit(run_pair, a, b, args.games, out, args.rl, args.max_steps) for a, b in pairs]
        for f in futs:
            tag, na, nb, wins, n, dt, rc = f.result()
            results.append((na, nb, wins, n))
            print(f"{tag:40s} {na} {wins['A']:4d} - {wins['B']:4d} {nb}   draws {wins['draw']}  ({n} games, {dt:.0f}s{', rc=' + str(rc) if rc else ''})")
    # summary
    names = [e[0] for e in entrants] + ([] if args.no_bot else [BOT[0]])
    wr = {}
    for na, nb, w, n in results:
        if n:
            wr[(na, nb)] = w["A"] / n
            wr[(nb, na)] = w["B"] / n
    lines = [f"# Tournament: {args.games} games per pairing, {len(pairs)} pairings\n",
             "Pilots: dragapult = dragapult_1 (deck-specific agent); other decks = generic_1 (deck-agnostic); rule_based = rule_based_1 with its own Dhelmise deck.\n",
             "Win rate of the row deck against the column deck:\n",
             "| deck | " + " | ".join(names) + " | avg |", "|---|" + "---|" * (len(names) + 1)]
    for a in names:
        cells, vals = [], []
        for b in names:
            if a == b:
                cells.append("-")
            elif (a, b) in wr:
                cells.append(f"{wr[(a, b)] * 100:.0f}%")
                vals.append(wr[(a, b)])
            else:
                cells.append("")
        lines.append(f"| {a} | " + " | ".join(cells) + f" | {sum(vals) / len(vals) * 100:.0f}% |" if vals else f"| {a} | " + " | ".join(cells) + " | |")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
