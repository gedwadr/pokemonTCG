"""Built-in agents.  Every agent is a callable ``(obs_dict) -> list[int]``.

``load_agent`` turns a Kaggle-style submission (a module exposing
``agent(obs_dict) -> list[int]``) into the same callable, so the server and
the local match runner can use submissions and built-ins interchangeably.
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import cards

Agent = Callable[[dict[str, Any]], list[int]]
_load_counter = 0


class RandomBot:
    name = "random"

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)

    def __call__(self, obs: dict[str, Any]) -> list[int]:
        sel = obs["select"]
        k = self.rng.randint(sel["minCount"], sel["maxCount"])
        return self.rng.sample(range(len(sel["option"])), k)


class GreedyBot:
    """A simple heuristic: evolve, attach, play, use abilities, then attack with the biggest hit."""

    name = "greedy"
    ORDER = {9: 0, 8: 1, 7: 2, 10: 3, 13: 5, 12: 6, 14: 9, 11: 8}

    def __init__(self, seed: int | None = None):
        self.rng = random.Random(seed)
        self._abilities_used: set[tuple[int, int]] = set()

    def __call__(self, obs: dict[str, Any]) -> list[int]:
        sel = obs["select"]
        opts = sel["option"]
        if sel["type"] == 0:  # main phase
            turn = obs["current"]["turn"]
            scored = []
            for i, o in enumerate(opts):
                t = o["type"]
                score = self.ORDER.get(t, 7)
                if t == 13:
                    a = cards.attack(o.get("attackId"))
                    score = 5 - (a["damage"] / 1000 if a else 0)
                if t == 10:
                    key = (turn, o.get("index", -1))
                    if key in self._abilities_used:
                        score = 8.5
                    else:
                        self._abilities_used.add(key)
                if t == 7:  # don't loop forever on cards we keep playing: small random jitter
                    score += self.rng.random() * 0.1
                scored.append((score, i))
            scored.sort()
            return [scored[0][1]]
        if sel["type"] == 9:  # yes/no: say yes to going first / activating, no to mulligan
            ctx = sel["context"]
            want_yes = ctx != 42
            for i, o in enumerate(opts):
                if (o["type"] == 1) == want_yes:
                    return [i]
        if sel["type"] == 8:  # count: take the max
            best = max(range(len(opts)), key=lambda i: opts[i].get("number", 0))
            return [best]
        if sel["type"] == 6:  # attack choice during an effect
            best = max(range(len(opts)), key=lambda i: (cards.attack(opts[i].get("attackId")) or {}).get("damage", 0))
            return [best]
        k = sel["maxCount"] if sel["context"] in (2, 19, 22) else sel["minCount"]
        k = max(sel["minCount"], min(k, sel["maxCount"]))
        return self.rng.sample(range(len(opts)), k)


BUILTIN: dict[str, Callable[[], Agent]] = {
    "random": RandomBot,
    "greedy": GreedyBot,
}

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


def available_bots() -> list[str]:
    """Built-in bots plus every ``agents/<name>/main.py`` submission."""
    names = list(BUILTIN)
    if AGENTS_DIR.exists():
        names += sorted(p.parent.name for p in AGENTS_DIR.glob("*/main.py"))
    return names


def make_bot(name: str, deck: list[int] | None = None) -> tuple[Agent, list[int] | None]:
    """Instantiate a bot by name: a built-in (uses ``deck``) or a submission folder (uses its own deck.csv
    unless ``deck`` is given; the server never passes one for submissions).

    A submission is loaded fresh every call so each game gets its own module state.
    """
    if name in BUILTIN:
        return BUILTIN[name](), deck
    path = AGENTS_DIR / name / "main.py"
    if not path.exists():
        raise ValueError(f"unknown bot {name!r}; choose from {', '.join(available_bots())}")
    agent, own_deck = load_agent(str(path))
    return agent, (own_deck if deck is None else deck)


def load_agent(spec: str) -> tuple[Agent, list[int] | None]:
    """Resolve ``spec`` to ``(agent, deck_or_None)``.

    ``spec`` is either a built-in name (``random``, ``greedy``) or a path to a
    Kaggle-style ``main.py``.  For a submission the deck is obtained the Kaggle
    way: the agent is called once with ``select=None`` and must return 60 IDs.
    """
    if spec in BUILTIN:
        return BUILTIN[spec](), None
    path = Path(spec)
    if not path.exists():
        raise FileNotFoundError(f"agent {spec!r} is neither a built-in ({', '.join(BUILTIN)}) nor a file")
    mod_dir = str(path.parent.resolve())
    if mod_dir not in sys.path:
        sys.path.insert(0, mod_dir)
    global _load_counter
    _load_counter += 1
    spec_obj = importlib.util.spec_from_file_location(f"agent_{path.stem}_{_load_counter}", path)
    module = importlib.util.module_from_spec(spec_obj)
    cwd = os.getcwd()
    os.chdir(mod_dir)  # submissions read deck.csv relative to themselves
    try:
        spec_obj.loader.exec_module(module)
        deck = module.agent({"select": None, "logs": [], "current": None, "search_begin_input": None})
    finally:
        os.chdir(cwd)
    if not isinstance(deck, list) or len(deck) != 60:
        raise ValueError(f"{spec}: agent must return a 60-card deck on the initial call")
    return module.agent, [int(c) for c in deck]
