"""Deck files and deck-legality checks.

Deck file format (``.csv`` or ``.txt``): one entry per line, either a bare card
ID (``721``) or ``<id>,<count>`` / ``<id> x<count>``.  Blank lines and lines
starting with ``#`` are ignored.  The sample deck shipped with the competition
(60 bare IDs) loads unchanged.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import cards
from .engine import DECK_SIZE

_LINE = re.compile(r"^\s*(\d+)\s*(?:[,x\s]\s*(\d+))?\s*$", re.I)


def load_deck(path: str | Path) -> list[int]:
    deck: list[int] = []
    for raw in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _LINE.match(line)
        if not m:
            raise ValueError(f"{path}: cannot parse line {raw!r}")
        card_id, count = int(m.group(1)), int(m.group(2) or 1)
        deck.extend([card_id] * count)
    return deck


def validate_deck(deck: list[int]) -> list[str]:
    """Return a list of rule violations (empty means legal as far as we can tell)."""
    problems: list[str] = []
    if len(deck) != DECK_SIZE:
        problems.append(f"deck has {len(deck)} cards, needs {DECK_SIZE}")
    db = cards.all_cards()
    unknown = sorted({c for c in deck if c not in db})
    if unknown:
        problems.append(f"unknown card IDs: {unknown}")
        return problems
    names = Counter(db[c]["name"] for c in deck)
    for name, n in names.items():
        c = next(db[x] for x in deck if db[x]["name"] == name)
        if n > 4 and c["cardType"] != 5:
            problems.append(f"{n} copies of {name!r} (max 4)")
    if sum(1 for c in deck if db[c]["aceSpec"]) > 1:
        problems.append("more than one ACE SPEC card")
    if not any(db[c]["cardType"] == 0 and db[c]["basic"] for c in deck):
        problems.append("no Basic Pokémon")
    return problems


def describe_deck(deck: list[int]) -> str:
    db = cards.all_cards()
    counts = Counter(deck)
    lines = []
    for card_id, n in sorted(counts.items(), key=lambda kv: (db.get(kv[0], {}).get("cardType", 99), kv[0])):
        c = db.get(card_id)
        lines.append(f"{n:2d} x {c['name'] if c else '?'} (#{card_id})")
    return "\n".join(lines)
