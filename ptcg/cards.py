"""Card and attack database, read from the engine itself (``AllCard`` / ``AllAttack``)."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .engine import lib, _LOCK

CARD_TYPES = ["Pokemon", "Item", "Tool", "Supporter", "Stadium", "BasicEnergy", "SpecialEnergy"]
ENERGY_TYPES = ["Colorless", "Grass", "Fire", "Water", "Lightning", "Psychic", "Fighting",
                "Darkness", "Metal", "Dragon", "Rainbow", "TeamRocket"]
AREA_NAMES = {0: "all", 1: "deck", 2: "hand", 3: "discard", 4: "active", 5: "bench", 6: "prize", 7: "stadium",
              8: "attached energy", 9: "attached tool", 10: "pre-evolution", 11: "player", 12: "looking",
              13: "playing", 14: "deck bottom"}
ENERGY_LETTERS = ["C", "G", "R", "W", "L", "P", "F", "D", "M", "N", "*", "TR"]  # PTCG symbols: R=Fire, F=Fighting


@lru_cache(maxsize=1)
def all_cards() -> dict[int, dict[str, Any]]:
    with _LOCK:
        raw = json.loads(lib.AllCard().decode())
    return {c["cardId"]: c for c in raw}


@lru_cache(maxsize=1)
def all_attacks() -> dict[int, dict[str, Any]]:
    with _LOCK:
        raw = json.loads(lib.AllAttack().decode())
    return {a["attackId"]: a for a in raw}


def card(card_id: int) -> dict[str, Any] | None:
    return all_cards().get(card_id)


def card_name(card_id: int | None) -> str:
    if card_id is None:
        return "?"
    c = all_cards().get(card_id)
    return c["name"] if c else f"#{card_id}"


def attack(attack_id: int) -> dict[str, Any] | None:
    return all_attacks().get(attack_id)


def attack_name(attack_id: int | None) -> str:
    a = all_attacks().get(attack_id) if attack_id is not None else None
    return a["name"] if a else f"attack#{attack_id}"


def database_json() -> str:
    """Everything a UI needs, as one JSON document (cached)."""
    return _database_json()


@lru_cache(maxsize=1)
def _database_json() -> str:
    return json.dumps({
        "cards": list(all_cards().values()),
        "attacks": list(all_attacks().values()),
        "cardTypes": CARD_TYPES,
        "energyTypes": ENERGY_TYPES,
    }, ensure_ascii=False)
