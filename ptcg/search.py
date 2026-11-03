"""Determinized rollouts on the engine's search API (SearchBegin / SearchStep).

The engine lets an agent clone the current battle from the serialized observation blob
(``search_begin_input``), fill in the hidden cards (decks, prizes, opponent hand) with a
guess, and play the clone forward.  Here the hidden cards are sampled from the *true*
remaining multiset of each deck (we know both 60-card lists), so each sample is a fair
determinization of the information the acting player cannot see.
"""
from __future__ import annotations

import ctypes
import json
import random
from collections import Counter
from typing import Any

from .engine import lib, _LOCK

A_LOOKING = 12
_agent_ptr = None


def _ptr():
    global _agent_ptr
    if _agent_ptr is None:
        _agent_ptr = lib.AgentStart()
    return _agent_ptr


class SearchError(RuntimeError):
    pass


def _result(bs: bytes) -> tuple[dict[str, Any], int]:
    d = json.loads(bs.decode())
    if d.get("error"):
        raise SearchError(f"search error {d['error']}")
    st = d["state"]
    return st["observation"], st["searchId"]


def _known_cards(player: dict[str, Any], hand_visible: bool) -> list[int]:
    ids: list[int] = []
    if hand_visible and player.get("hand"):
        ids += [c["id"] for c in player["hand"]]
    for pk in (player.get("active") or []) + (player.get("bench") or []):
        if not pk:
            continue
        ids.append(pk["id"])
        for key in ("energyCards", "tools", "preEvolution"):
            ids += [c["id"] for c in pk.get(key, []) if c]
    ids += [c["id"] for c in player.get("discard", []) if c]
    ids += [c["id"] for c in player.get("prize", []) if c]  # face-up prizes, if any
    return ids


def determinize(obs: dict[str, Any], decks: list[list[int]], rng: random.Random) -> dict[str, list[int]]:
    """Sample the hidden cards for both players consistent with what the actor can see."""
    cur = obs["current"]
    me = cur["yourIndex"]
    out: dict[str, list[int]] = {}
    for idx, key_deck, key_prize, hand_key in ((me, "your_deck", "your_prize", None), (1 - me, "opponent_deck", "opponent_prize", "opponent_hand")):
        p = cur["players"][idx]
        unknown = Counter(decks[idx])
        known = _known_cards(p, hand_visible=(idx == me))
        if cur.get("stadium") and cur["stadium"][0].get("playerIndex") == idx:
            known.append(cur["stadium"][0]["id"])
        if idx == me and cur.get("looking"):
            known += [c["id"] for c in cur["looking"] if c]
        for cid in known:
            if unknown[cid] > 0:
                unknown[cid] -= 1
        pool = list(unknown.elements())
        rng.shuffle(pool)
        need_deck = p["deckCount"]
        need_prize = sum(1 for c in p.get("prize", []) if not c)
        need_hand = p["handCount"] if hand_key else 0
        need = need_deck + need_prize + need_hand
        if len(pool) < need:  # cards in transit (e.g. the Supporter being resolved): pad with random deck cards
            pool += [rng.choice(decks[idx]) for _ in range(need - len(pool))]
        if idx == me and obs.get("select", {}) and obs["select"].get("deck") is not None:
            out[key_deck] = []  # engine ignores it when the deck is being looked at
            deck_part, pool = pool[:need_deck], pool[need_deck:]
        else:
            out[key_deck], pool = pool[:need_deck], pool[need_deck:]
        prizes = pool[:need_prize]
        pool = pool[need_prize:]
        # face-down prizes only; face-up ones are already known
        out[key_prize] = prizes + [c["id"] for c in p.get("prize", []) if c]
        if hand_key:
            out[hand_key] = pool[:need_hand]
    act = cur["players"][1 - me].get("active") or []
    out["opponent_active"] = [rng.choice([c for c in decks[1 - me]]) ] if act and act[0] is None else []
    if out["opponent_active"]:
        from . import cards
        basics = [c for c in decks[1 - me] if (cards.card(c) or {}).get("basic")]
        out["opponent_active"] = [rng.choice(basics)] if basics else []
    return out


def search_begin(obs: dict[str, Any], hidden: dict[str, list[int]], manual_coin: bool = False) -> tuple[dict[str, Any], int]:
    sbi = obs["search_begin_input"].encode("ascii")
    arrs = {}
    for k in ("your_deck", "your_prize", "opponent_deck", "opponent_prize", "opponent_hand", "opponent_active"):
        v = hidden.get(k, [])
        arrs[k] = (ctypes.c_int * max(1, len(v)))(*v) if v else (ctypes.c_int * 1)(0)
    with _LOCK:
        bs = lib.SearchBegin(_ptr(), sbi, len(sbi), arrs["your_deck"], arrs["your_prize"], arrs["opponent_deck"],
                             arrs["opponent_prize"], arrs["opponent_hand"], arrs["opponent_active"], int(manual_coin))
        return _result(bs)


def search_step(search_id: int, select: list[int]) -> tuple[dict[str, Any], int]:
    arr = (ctypes.c_int * max(1, len(select)))(*select) if select else (ctypes.c_int * 1)(0)
    with _LOCK:
        return _result(lib.SearchStep(_ptr(), search_id, arr, len(select)))


def search_release(search_id: int) -> None:
    with _LOCK:
        lib.SearchRelease(_ptr(), search_id)


def search_end() -> None:
    with _LOCK:
        lib.SearchEnd(_ptr())


def rollout(obs: dict[str, Any], search_id: int, policy, me: int, max_steps: int = 200) -> float:
    """Play a cloned state forward with ``policy`` for both sides. Returns the value for player ``me`` in [0, 1]."""
    steps = 0
    while obs["current"]["result"] == -1 and steps < max_steps:
        sel = obs["select"]
        if sel is None:
            break
        obs, search_id = search_step(search_id, policy(obs))
        steps += 1
    r = obs["current"]["result"]
    if r == me:
        return 1.0
    if r == 1 - me:
        return 0.0
    if r == 2:
        return 0.5
    mine = len(obs["current"]["players"][me]["prize"])
    theirs = len(obs["current"]["players"][1 - me]["prize"])
    return 0.5 + (theirs - mine) / 12.0  # truncated: prize differential


def label_state(obs: dict[str, Any], decks: list[list[int]], policy, determinizations: int = 8,
                rng: random.Random | None = None, max_steps: int = 200) -> dict[str, Any] | None:
    """Rollout value for every single-pick option. Returns None for multi-pick selections."""
    sel = obs.get("select")
    if not sel or sel["maxCount"] != 1 or len(sel["option"]) < 2:
        return None
    rng = rng or random.Random()
    me = obs["current"]["yourIndex"]
    n = len(sel["option"])
    totals = [0.0] * n
    for _ in range(determinizations):
        hidden = determinize(obs, decks, rng)
        root_obs, root_id = search_begin(obs, hidden)
        for i in range(n):
            child_obs, child_id = search_step(root_id, [i])
            totals[i] += rollout(child_obs, child_id, policy, me, max_steps)
        search_end()
    scores = [t / determinizations for t in totals]
    best = max(range(n), key=lambda i: scores[i])
    return {"scores": scores, "best": best, "determinizations": determinizations, "rollouts_per_option": determinizations}
