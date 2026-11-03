"""generic_1 - a deck-agnostic rule-based pilot.

Reads the engine's card and attack tables, so it can play any legal deck without card-specific
rules: evolve, use abilities, bench basics, attach energy toward the nearest affordable attack,
play draw/search Trainers, Boss for knockouts, retreat when the Active cannot attack, and attack
for the most damage (weakness-adjusted, knockout bonus).  Used as the common pilot for meta decks
in scripts/tournament.py.  The deck is whatever deck.csv sits beside it (the runner overrides it).
"""
from __future__ import annotations

import os
import re
from collections import Counter

from cg.api import all_attack, all_card_data

_path = "deck.csv" if os.path.exists("deck.csv") else "/kaggle_simulations/agent/deck.csv"
MY_DECK = [int(x) for x in open(_path).read().split()][:60]
CARDS = {c.cardId: c for c in all_card_data()}
ATTACKS = {a.attackId: a for a in all_attack()}
NAME_TO_IDS: dict[str, list[int]] = {}
for _c in CARDS.values():
    NAME_TO_IDS.setdefault(_c.name, []).append(_c.cardId)
EVOLVES_TO: dict[str, list[int]] = {}
for _c in CARDS.values():
    if _c.evolvesFrom:
        EVOLVES_TO.setdefault(_c.evolvesFrom, []).append(_c.cardId)

E_COLORLESS, E_RAINBOW, E_TEAM_ROCKET, E_DARK, E_PSY = 0, 10, 11, 7, 5
A_DECK, A_HAND, A_DISCARD, A_ACTIVE, A_BENCH, A_PRIZE, A_STADIUM, A_LOOKING = 1, 2, 3, 4, 5, 6, 7, 12
O_NUMBER, O_YES, O_NO, O_CARD, O_TOOL, O_ECARD, O_ENERGY, O_PLAY, O_ATTACH, O_EVOLVE, O_ABILITY, O_DISCARD, O_RETREAT, O_ATTACK, O_END, O_SKILL, O_SC = range(17)
C_MAIN, C_SETUP_ACTIVE, C_SETUP_BENCH, C_SWITCH, C_TO_ACTIVE, C_TO_BENCH, C_TO_FIELD, C_TO_HAND, C_DISCARD = range(9)
C_TO_DECK, C_TO_DECK_BOTTOM, C_TO_PRIZE, C_NOT_MOVE, C_DAMAGE_COUNTER, C_DAMAGE_COUNTER_ANY, C_DAMAGE, C_REMOVE_DC, C_HEAL = range(9, 18)
C_EVOLVES_FROM, C_EVOLVES_TO, C_DEVOLVE, C_ATTACH_FROM, C_ATTACH_TO, C_DETACH_FROM, C_LOOK, C_EFFECT_TARGET = range(18, 26)
C_IS_FIRST, C_MULLIGAN, C_ACTIVATE = 41, 42, 43
TYPE_POKEMON, TYPE_ITEM, TYPE_TOOL, TYPE_SUPPORTER, TYPE_STADIUM, TYPE_BASIC_ENERGY, TYPE_SPECIAL_ENERGY = range(7)

MY_NAMES = Counter(CARDS[c].name for c in MY_DECK if c in CARDS)


def set_deck(deck: list[int]) -> None:
    """Called by the local runner / client when the deck is overridden on the command line."""
    global MY_DECK, MY_NAMES
    MY_DECK = list(deck)
    MY_NAMES = Counter(CARDS[c].name for c in MY_DECK if c in CARDS)
_used_abilities: dict[tuple[int, int], int] = {}
_draws_this_turn: dict[int, int] = {}


def card_at(obs, area, index, player):
    cur, sel = obs["current"], obs["select"]
    try:
        if area == A_DECK:
            return sel["deck"][index]
        if area == A_LOOKING:
            return cur["looking"][index]
        if area == A_STADIUM:
            return cur["stadium"][index]
        p = cur["players"][player]
        return {A_HAND: p["hand"], A_DISCARD: p["discard"], A_ACTIVE: p["active"], A_BENCH: p["bench"], A_PRIZE: p["prize"]}[area][index]
    except (KeyError, IndexError, TypeError):
        return None


def prizes_of(cid):
    c = CARDS.get(cid)
    return 3 if c and c.megaEx else 2 if c and c.ex else 1


def has_line(cid):
    """True if the deck contains an evolution for this card."""
    c = CARDS.get(cid)
    return bool(c) and any(CARDS[e].name in MY_NAMES for e in EVOLVES_TO.get(c.name, []))


def cost_gap(attack_id, energies):
    a = ATTACKS.get(attack_id)
    if a is None:
        return 99
    pool = list(energies)
    missing = 0
    typed = [t for t in a.energies if t != E_COLORLESS]
    colorless = len(a.energies) - len(typed)
    for t in typed:
        if t in pool:
            pool.remove(t)
        elif E_RAINBOW in pool:
            pool.remove(E_RAINBOW)
        elif t in (E_DARK, E_PSY) and E_TEAM_ROCKET in pool:
            pool.remove(E_TEAM_ROCKET)
        else:
            missing += 1
    missing += max(0, colorless - len(pool))
    return missing


def attack_damage(attack_id, attacker_cid, target):
    a = ATTACKS.get(attack_id)
    if a is None:
        return 0
    d = a.damage or 0
    if target is not None and d > 0:
        tc = CARDS.get(target["id"])
        at = CARDS[attacker_cid].energyType
        if tc and tc.weakness is not None and tc.weakness == at:
            d *= 2
        if tc and tc.resistance is not None and tc.resistance == at:
            d = max(0, d - 30)
    return d


def best_attack(pk, target):
    """(damage, attack_id, gap) for the best attack this Pokémon could use now (gap==0) or soonest."""
    best = (0, None, 99)
    for aid in CARDS[pk["id"]].attacks:
        gap = cost_gap(aid, pk.get("energies", []))
        dmg = attack_damage(aid, pk["id"], target)
        a = ATTACKS[aid]
        value = dmg + (25 if a.text else 0)
        if gap == 0 and (best[2] > 0 or value > best[0]):
            best = (value, aid, 0)
        elif gap > 0 and best[2] > 0 and (gap < best[2] or (gap == best[2] and value > best[0])):
            best = (value, aid, gap)
    return best


def agent(obs_dict: dict) -> list[int]:
    obs = obs_dict
    sel = obs.get("select")
    if sel is None:
        return MY_DECK
    cur = obs["current"]
    ctx, opts = sel["context"], sel["option"]
    me = cur["yourIndex"]
    my, op = cur["players"][me], cur["players"][1 - me]
    hand = my.get("hand") or []
    hand_ids = [c["id"] for c in hand]
    hc = Counter(hand_ids)
    my_active = my["active"][0] if my["active"] and my["active"][0] else None
    my_field = ([my_active] if my_active else []) + list(my["bench"])
    fc = Counter(pk["id"] for pk in my_field)
    op_active = op["active"][0] if op["active"] and op["active"][0] else None
    bench_room = my["benchMax"] - len(my["bench"])
    turn = cur["turn"]
    energy_in_hand = sum(1 for c in hand_ids if CARDS[c].cardType in (TYPE_BASIC_ENERGY, TYPE_SPECIAL_ENERGY))
    supporters_in_hand = [c for c in hand_ids if CARDS[c].cardType == TYPE_SUPPORTER]
    stadium = cur["stadium"][0]["id"] if cur["stadium"] else 0
    active_best = best_attack(my_active, op_active) if my_active else (0, None, 99)
    bench_best = max(((best_attack(b, op_active), i) for i, b in enumerate(my["bench"])), default=((0, None, 99), -1))

    def text_of(cid):
        c = CARDS[cid]
        return " ".join(s["text"] if isinstance(s, dict) else s.text for s in c.skills).lower()

    scores = []
    for o in opts:
        t = o["type"]
        s = 0
        if t == O_NUMBER:
            s = o.get("number") or 0
        elif t == O_YES:
            s = 0 if ctx == C_MULLIGAN else 1
        elif t == O_NO:
            s = 1 if ctx == C_MULLIGAN else 0

        elif t == O_PLAY:
            c = card_at(obs, A_HAND, o["index"], me)
            cid = c["id"] if c else 0
            cd = CARDS.get(cid)
            if cd is None:
                s = -1
            elif cd.cardType == TYPE_POKEMON:
                if bench_room <= 0:
                    s = -1
                else:
                    s = 7000 + cd.hp // 10 + (800 if has_line(cid) else 0)
                    if cd.ex or cd.megaEx:
                        s = s - 1500 if len(my_field) >= 2 else s + 200
                    if len(my["bench"]) >= 4 and not has_line(cid):
                        s = 2500
            elif cd.cardType == TYPE_ITEM:
                tx = text_of(cid)
                s = 8500
                if my["deckCount"] <= 2 and "deck" in tx:
                    s = -1
                if ("move" in tx and "energy" in tx and "from 1 of your pokémon to" in tx) or ("energy" in tx and "switch" in tx and "attach" in tx):
                    s = -1
                if "draw" in tx and my["deckCount"] <= 6:
                    s = -1
                if "discard 2 other cards" in tx or "discard 2 cards" in tx:
                    s = 8300 if len(hand) >= 4 else -1
                if "switch" in tx and "active" in tx and "opponent" not in tx:
                    s = 8100 if active_best[2] > 0 and bench_best[0][2] == 0 else -1
                if "knocked out during your opponent" in tx:
                    s = 8600
                if "prize" in tx and "more prize" in tx and len(my["prize"]) <= len(op["prize"]):
                    s = -1
                if "shuffle your hand" in tx and len(hand) > 5:
                    s = -1
            elif cd.cardType == TYPE_SUPPORTER:
                tx = text_of(cid)
                if "benched pokémon" in tx and "active spot" in tx and "opponent" in tx:  # gust
                    s = -1
                    if active_best[2] == 0:
                        for b in op["bench"]:
                            if b and attack_damage(active_best[1], my_active["id"], b) >= b["hp"]:
                                s = max(s, 8700 + prizes_of(b["id"]) * 100)
                elif "draw" in tx or "shuffle your hand" in tx:
                    s = 7500 if len(hand) <= 4 else (6000 if len(hand) <= 6 else -1)
                    if my["deckCount"] <= 8:
                        s = -1
                    if "shuffle your hand" in tx and energy_in_hand + sum(1 for x in hand_ids if CARDS[x].cardType == TYPE_POKEMON and (has_line(x) or CARDS[x].evolvesFrom)) >= 3:
                        s = -1
                elif "energy" in tx and "search" in tx:
                    s = 7600 if energy_in_hand == 0 else 6200
                elif "search" in tx:
                    s = 7300
                else:
                    s = 6000
            elif cd.cardType == TYPE_STADIUM:
                s = 6500 if stadium != cid else -1
            elif cd.cardType == TYPE_TOOL:
                s = 6800
            else:
                s = 3000

        elif t == O_ATTACH:
            c = card_at(obs, o["area"], o["index"], me)
            pk = card_at(obs, o["inPlayArea"], o["inPlayIndex"], me)
            if c and pk:
                cd = CARDS[c["id"]]
                if cd.cardType == TYPE_TOOL:
                    s = 6800 + (200 if o["inPlayArea"] == A_ACTIVE else 0) + pk["hp"] // 10
                else:
                    provides = cd.energyType
                    before = best_attack(pk, op_active)
                    after = best_attack({**pk, "energies": pk.get("energies", []) + [provides]}, op_active)
                    s = 5000 + (after[0] - before[0]) * 5 + (before[2] - after[2]) * 1200 + pk["hp"] // 20
                    if after[2] == 0 and before[2] > 0:
                        s += 2500
                    if o["inPlayArea"] == A_ACTIVE:
                        s += 400
                    if after[2] == before[2] and after[0] == before[0]:
                        s = 4000 + pk["hp"] // 20  # no progress toward any attack: low priority target

        elif t == O_EVOLVE:
            ev = card_at(obs, o["area"], o["index"], me)
            pk = card_at(obs, o["inPlayArea"], o["inPlayIndex"], me)
            if ev and pk:
                s = 8800 + CARDS[ev["id"]].hp // 10 + (300 if o["inPlayArea"] == A_ACTIVE else 0)

        elif t == O_ABILITY:
            c = card_at(obs, o["area"], o["index"], me)
            if c:
                key = (turn, c.get("serial", -1))
                uses = _used_abilities.get(key, 0)
                tx = text_of(c["id"]) if c["id"] in CARDS else ""
                if "end your turn" in tx or "your turn ends" in tx:
                    s = -1
                elif uses >= 2:
                    s = -1
                elif "draw" in tx and (len(hand) >= 7 or my["deckCount"] <= 8 or _draws_this_turn.get(turn, 0) >= 2):
                    s = -1
                elif "discard" in tx and "draw" in tx and len(hand) <= 2:
                    s = -1
                else:
                    s = 9000 - uses * 1000

        elif t == O_RETREAT:
            s = 8100 if active_best[2] > 0 and bench_best[0][2] == 0 else (7900 if my_active and bench_best[0][2] == 0 and bench_best[0][0] > active_best[0] + 60 else -1)

        elif t == O_ATTACK:
            aid = o.get("attackId")
            dmg = attack_damage(aid, my_active["id"], op_active) if my_active else 0
            a = ATTACKS.get(aid)
            s = 1000 + dmg + (30 if a and a.text else 0)
            if op_active and dmg >= op_active["hp"]:
                s += 400 + prizes_of(op_active["id"]) * 100
            if a and "can't attack" in (a.text or "").lower() and not (op_active and dmg >= op_active["hp"]):
                s -= 80
            if a and "switch this pokémon" in (a.text or "").lower() and dmg == 0:
                s = 950 if bench_best[0][2] == 0 else 900

        elif t == O_END:
            s = 0

        elif t == O_CARD:
            s = card_score(obs, o, ctx, me, my, op, hc, fc, bench_room, energy_in_hand, supporters_in_hand, sel, active_best, my_active, op_active)

        elif t in (O_ENERGY, O_ECARD):
            pk = card_at(obs, o["area"], o["index"], o.get("playerIndex", me))
            ec = None
            if pk and o.get("energyIndex") is not None and o["energyIndex"] < len(pk.get("energyCards", [])):
                ec = pk["energyCards"][o["energyIndex"]]
            s = 50 if ec and CARDS[ec["id"]].cardType == TYPE_BASIC_ENERGY else 20
        else:
            s = 1
        scores.append(s)

    order = sorted(range(len(opts)), key=lambda i: scores[i], reverse=True)
    lo, hi = sel["minCount"], sel["maxCount"]
    if ctx == C_MAIN:
        pick = order[0]
        if opts[pick]["type"] == O_ABILITY:
            c = card_at(obs, opts[pick]["area"], opts[pick]["index"], me)
            if c:
                key = (turn, c.get("serial", -1))
                _used_abilities[key] = _used_abilities.get(key, 0) + 1
                if "draw" in text_of(c["id"]):
                    _draws_this_turn[turn] = _draws_this_turn.get(turn, 0) + 1
        return [pick]
    if ctx in (C_SETUP_BENCH, C_TO_BENCH, C_DISCARD, C_TO_FIELD, C_TO_DECK, C_TO_DECK_BOTTOM):
        chosen = [i for i in order if scores[i] >= 0][:hi]
        return chosen if len(chosen) >= lo else order[:lo]
    return order[:hi] if ctx == C_TO_HAND else order[:max(lo, min(hi, 1))]


def card_score(obs, o, ctx, me, my, op, hc, fc, bench_room, energy_in_hand, supporters_in_hand, sel, active_best, my_active, op_active):
    c = card_at(obs, o["area"], o["index"], o.get("playerIndex", me))
    if c is None:
        return 0
    cid = c["id"]
    cd = CARDS.get(cid)
    mine = o.get("playerIndex", me) == me
    if cd is None:
        return 0
    field_names = Counter(CARDS[pk["id"]].name for pk in ([my["active"][0]] if my["active"] and my["active"][0] else []) + my["bench"])
    hand_names = Counter(CARDS[x].name for x in (k for k in hc.elements()))

    if ctx == C_SETUP_ACTIVE:
        return cd.hp + (150 if has_line(cid) else 0) - (120 if (cd.ex or cd.megaEx) else 0)
    if ctx in (C_SETUP_BENCH, C_TO_BENCH, C_TO_FIELD):
        if cd.cardType != TYPE_POKEMON:
            return -1
        s = 50 + cd.hp // 10 + (100 if has_line(cid) else 0)
        if cd.ex or cd.megaEx:
            s -= 90
        if field_names[cd.name] + hand_names[cd.name] >= 2 and not has_line(cid):
            s -= 60
        return s
    if ctx in (C_SWITCH, C_TO_ACTIVE):
        if mine:
            dmg, _, gap = best_attack(c, op_active)
            return 100 + (dmg if gap == 0 else dmg // 4) + c["hp"] // 10 - (40 if (cd.ex or cd.megaEx) else 0)
        s = prizes_of(cid) * 100 + len(c.get("energies", [])) * 10 - c["hp"] // 20
        if my_active and active_best[2] == 0 and attack_damage(active_best[1], my_active["id"], c) >= c["hp"]:
            s += 1000
        return s
    if ctx == C_TO_HAND:
        if o["area"] == A_PRIZE:
            return 1
        s = 20
        if cd.cardType == TYPE_POKEMON:
            if cd.evolvesFrom and field_names[cd.evolvesFrom] > hand_names[cd.name]:
                s = 120  # evolution for something in play
            elif cd.basic and len(my["bench"]) <= 2:
                s = 90 + (20 if has_line(cid) else 0)
            elif has_line(cid):
                s = 60
            else:
                s = 35
            if cd.ex or cd.megaEx:
                s -= 15
        elif cd.cardType in (TYPE_BASIC_ENERGY, TYPE_SPECIAL_ENERGY):
            s = 100 if energy_in_hand == 0 else 45
        elif cd.cardType == TYPE_SUPPORTER:
            s = 85 if not supporters_in_hand else 40
        elif cd.cardType == TYPE_ITEM:
            s = 55
        elif cd.cardType == TYPE_STADIUM:
            s = 30
        elif cd.cardType == TYPE_TOOL:
            s = 40
        return s
    if ctx == C_DISCARD or ctx in (C_TO_DECK, C_TO_DECK_BOTTOM):
        if not mine:
            return 0
        if cd.cardType == TYPE_POKEMON:
            if cd.evolvesFrom and field_names[cd.evolvesFrom]:
                return 2
            if cd.evolvesFrom and not hand_names[cd.evolvesFrom]:
                return 60
            if hand_names[cd.name] >= 2:
                return 40
            return 12 if has_line(cid) or cd.basic else 30
        if cd.cardType in (TYPE_BASIC_ENERGY, TYPE_SPECIAL_ENERGY):
            return 70 if energy_in_hand >= 3 else 5
        if cd.cardType == TYPE_SUPPORTER:
            return 65 if hand_names[cd.name] >= 2 else 25
        if cd.cardType == TYPE_STADIUM:
            return 75
        if cd.cardType == TYPE_TOOL:
            return 55
        return 45
    if ctx in (C_DAMAGE_COUNTER, C_DAMAGE_COUNTER_ANY, C_DAMAGE, C_EFFECT_TARGET):
        if mine:
            return c.get("maxHp", 0) - c.get("hp", 0)
        remain = max(1, sel.get("remainDamageCounter") or 1) * 10
        s = prizes_of(cid) * 50 - c["hp"] // 10
        if c["hp"] <= remain:
            s += 1000 + prizes_of(cid) * 300
        return s
    if ctx in (C_REMOVE_DC, C_HEAL):
        return c.get("maxHp", 0) - c.get("hp", 0) if mine else 0
    if ctx in (C_ATTACH_FROM, C_ATTACH_TO):
        if "hp" in c:
            dmg, _, gap = best_attack(c, op_active)
            return 100 - gap * 20 + dmg // 10 + (30 if o["area"] == A_ACTIVE else 0)
        return 50 + (20 if cd.cardType == TYPE_BASIC_ENERGY else 0)
    if ctx == C_EVOLVES_FROM:
        return c["hp"] // 10 + (50 if o["area"] == A_ACTIVE else 0)
    if ctx == C_EVOLVES_TO:
        return cd.hp
    return 10
