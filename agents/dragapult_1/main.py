"""dragapult_1 - a rule-based agent for the Dragapult ex deck (Hedrick Worlds 2026 list).

Plan: go first, bench Dreepy early, evolve into Drakloak (Recon Directive every turn) and
Dragapult ex, power one attacker with Fire + Psychic, Phantom Dive for 200 and spread the
6 damage counters to set up or take bench knockouts.  Budew's Itchy Pollen item-locks the
opponent while we set up; Munkidori moves damage counters; Boss's Orders pulls kills.
Works on the raw observation dict so it runs unchanged on Kaggle and on the local server.
"""
from __future__ import annotations

import os
from collections import Counter

from cg.api import all_card_data

# card IDs
DREEPY, DRAKLOAK, DRAGAPULT = 119, 120, 121
MUNKIDORI, BUDEW, DUNSPARCE, DUDUNSPARCE, MEOWTH, FEZANDIPITI = 112, 235, 305, 66, 1071, 140
LILLIE, POKEPAD, HAMMER, POFFIN, BOSS, STRETCHER, ULTRABALL, CRISPIN, RUINS, ROSA, STAMP, JUDGE = \
    1227, 1152, 1120, 1086, 1182, 1097, 1121, 1198, 1260, 1240, 1080, 1213
FIRE, PSY, DARK = 2, 5, 7
ENERGY_IDS = {FIRE, PSY, DARK}
DRAGON_LINE = {DREEPY, DRAKLOAK, DRAGAPULT}
# attack ids
JET_HEADBUTT, PHANTOM_DIVE, DRAGON_HEADBUTT, ITCHY_POLLEN, TRADING_PLACES, RAM = 153, 154, 152, 323, 423, 424
MIND_BEND, CRUEL_ARROW, LAND_CRUSH, TUCK_TAIL, PETTY_GRUDGE, BITE = 141, 183, 76, 1546, 150, 151
# energy type enums
E_FIRE, E_PSY, E_DARK = 2, 5, 7
# areas / option types / contexts (cg.api enums)
A_DECK, A_HAND, A_DISCARD, A_ACTIVE, A_BENCH, A_PRIZE, A_STADIUM, A_LOOKING = 1, 2, 3, 4, 5, 6, 7, 12
O_NUMBER, O_YES, O_NO, O_CARD, O_TOOL, O_ECARD, O_ENERGY, O_PLAY, O_ATTACH, O_EVOLVE, O_ABILITY, O_DISCARD, O_RETREAT, O_ATTACK, O_END, O_SKILL, O_SC = range(17)
C_MAIN, C_SETUP_ACTIVE, C_SETUP_BENCH, C_SWITCH, C_TO_ACTIVE, C_TO_BENCH, C_TO_FIELD, C_TO_HAND, C_DISCARD = range(9)
C_DAMAGE_COUNTER, C_DAMAGE_COUNTER_ANY, C_DAMAGE, C_REMOVE_DC, C_HEAL = 13, 14, 15, 16, 17
C_ATTACH_FROM, C_ATTACH_TO, C_DETACH_FROM, C_LOOK, C_EFFECT_TARGET = 21, 22, 23, 24, 25
C_IS_FIRST, C_MULLIGAN = 41, 42

_path = "deck.csv"
if not os.path.exists(_path):
    _path = "/kaggle_simulations/agent/deck.csv"
MY_DECK = [int(x) for x in open(_path).read().split()][:60]
GO_FIRST = os.environ.get("DRAGAPULT_GO_FIRST", "1") == "1"
PLAY_RUINS = os.environ.get("DRAGAPULT_RUINS", "0") == "1"
CLEFAIRY, DHELMISE = 272, 1380  # the Dragon-counter cards in the Dhelmise deck

CARDS = {c.cardId: c for c in all_card_data()}


def is_supporter(cid): return CARDS[cid].cardType == 3
def is_pokemon(cid): return CARDS[cid].cardType == 0


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


def energy_counts(pk):
    en = pk.get("energies", [])
    return en.count(E_FIRE), en.count(E_PSY), en.count(E_DARK), len(en)


def best_attack(pk, opp_bench_count=0):
    """(damage, attack id) the Pokémon could use with its current energy, ignoring special conditions."""
    r, p, d, n = energy_counts(pk)
    cid = pk["id"]
    if cid == DRAGAPULT:
        if r and p:
            return 200 + 10 * min(6, opp_bench_count), PHANTOM_DIVE
        if n:
            return 70, JET_HEADBUTT
    if cid == DRAKLOAK and r and p:
        return 70, DRAGON_HEADBUTT
    if cid == FEZANDIPITI and n >= 3:
        return 100, CRUEL_ARROW
    if cid == DUDUNSPARCE and n >= 3:
        return 90, LAND_CRUSH
    if cid == MUNKIDORI and p and n >= 2:
        return 60, MIND_BEND
    if cid == DREEPY and r and p:
        return 40, BITE
    if cid == DUNSPARCE and n >= 2:
        return 20, RAM
    if cid == BUDEW:
        return 10, ITCHY_POLLEN
    if cid == DREEPY and p:
        return 10, PETTY_GRUDGE
    return 0, None


def needs_energy(pk):
    """Which basic energy IDs would move this Pokémon toward attacking (ordered by value)."""
    r, p, d, n = energy_counts(pk)
    cid = pk["id"]
    if cid in (DRAGAPULT, DRAKLOAK):
        want = []
        if not r:
            want.append(FIRE)
        if not p:
            want.append(PSY)
        return want
    if cid == MUNKIDORI and not d:
        return [DARK]
    if cid == DREEPY:
        return [PSY] if not p else ([FIRE] if not r else [])
    return []


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
    op_field = ([op_active] if op_active else []) + [b for b in op["bench"] if b]
    bench_room = my["benchMax"] - len(my["bench"])
    dragon_lines = fc[DREEPY] + fc[DRAKLOAK] + fc[DRAGAPULT]
    energy_in_hand = sum(hc[e] for e in ENERGY_IDS)
    discard_ids = [c["id"] for c in my["discard"]]
    stadium = cur["stadium"][0]["id"] if cur["stadium"] else 0
    turn = cur["turn"]
    opp_bench_n = len(op["bench"])

    # what the active can do right now
    active_dmg, active_atk = best_attack(my_active, opp_bench_n) if my_active else (0, None)
    bench_best = max(((best_attack(b, opp_bench_n)[0], i) for i, b in enumerate(my["bench"])), default=(0, -1))
    basics_in_hand = [c for c in hand_ids if is_pokemon(c) and CARDS[c].basic]

    scores = []
    for o in opts:
        t = o["type"]
        s = 0
        if t == O_NUMBER:
            s = o.get("number") or 0
        elif t == O_YES:
            s = (1 if GO_FIRST else 0) if ctx == C_IS_FIRST else (0 if ctx == C_MULLIGAN else 1)
        elif t == O_NO:
            s = (0 if GO_FIRST else 1) if ctx == C_IS_FIRST else (1 if ctx == C_MULLIGAN else 0)

        elif t == O_PLAY:
            c = card_at(obs, A_HAND, o["index"], me)
            cid = c["id"] if c else 0
            if is_pokemon(cid):
                if cid == DREEPY:
                    s = 8000 if dragon_lines < 3 else (3000 if bench_room >= 2 else -1)
                elif cid == MUNKIDORI:
                    s = 7000 if fc[MUNKIDORI] == 0 and bench_room >= 1 else -1
                elif cid == BUDEW:
                    s = 6000 if fc[BUDEW] == 0 and turn <= 8 and bench_room >= 2 else -1
                elif cid == DUNSPARCE:
                    s = 5000 if fc[DUNSPARCE] == 0 and bench_room >= 2 else -1
                elif cid == MEOWTH:
                    s = 6500 if len(hand) <= 2 and bench_room >= 3 else -1
                elif cid == FEZANDIPITI:
                    s = 5500 if fc[FEZANDIPITI] == 0 and bench_room >= 2 and len(hand) <= 3 else -1
                else:
                    s = -1
            elif cid == POFFIN:
                s = 8500 if bench_room >= 1 and my["deckCount"] > 0 else -1
            elif cid == POKEPAD:
                s = 8400 if my["deckCount"] > 0 else -1
            elif cid == ULTRABALL:
                junk = sum(1 for x in hand_ids if discard_value(x, hc, fc, bench_room) >= 50)
                s = 8300 if junk >= 2 and my["deckCount"] > 0 else -1
            elif cid == HAMMER:
                s = 8600 if any(pk.get("energies") for pk in op_field) else -1
            elif cid == STAMP:
                s = 8700 if op["handCount"] >= 4 else -1
            elif cid == STRETCHER:
                want = {DRAGAPULT, DRAKLOAK, DREEPY} | (ENERGY_IDS if energy_in_hand == 0 else set())
                s = 7500 if any(x in want for x in discard_ids) else -1
            elif cid == RUINS:
                s = 6800 if PLAY_RUINS and stadium != RUINS and not basics_in_hand and len(my["bench"]) >= 2 else -1
            elif cid == LILLIE:
                keep = hc[DRAGAPULT] + hc[DRAKLOAK] + hc[BOSS] + hc[ROSA] + min(2, energy_in_hand) + hc[POFFIN] + hc[POKEPAD]
                s = -1 if keep >= 2 else (7000 if len(hand) <= 3 else 5200 if len(hand) <= 5 else -1)
            elif cid == CRISPIN:
                need = any(needs_energy(pk) for pk in my_field)
                s = 7600 if need and energy_in_hand == 0 else (6900 if need else -1)
            elif cid == BOSS:
                s = boss_score(my_active, active_dmg, op, op_active)
            elif cid == ROSA:
                s = 7700 if any(e in discard_ids for e in (FIRE, PSY)) and any(pk["id"] == DRAGAPULT and needs_energy(pk) for pk in my_field) else -1
            elif cid == JUDGE:
                s = 6000 if len(hand) <= 3 and hc[LILLIE] == 0 else -1
            else:
                s = -1

        elif t == O_ATTACH:
            c = card_at(obs, o["area"], o["index"], me)
            pk = card_at(obs, o["inPlayArea"], o["inPlayIndex"], me)
            if c and pk:
                s = attach_score(c["id"], pk, o["inPlayArea"] == A_ACTIVE, energy_in_hand)

        elif t == O_EVOLVE:
            ev = card_at(obs, o["area"], o["index"], me)
            pk = card_at(obs, o["inPlayArea"], o["inPlayIndex"], me)
            if ev and pk:
                r, p, d, n = energy_counts(pk)
                if ev["id"] == DRAGAPULT:
                    can_attach = not cur["energyAttached"]
                    rosa_ok = hc[ROSA] >= 1 and not cur["supporterPlayed"] and len(my["prize"]) > len(op["prize"]) \
                        and discard_ids.count(FIRE) + discard_ids.count(PSY) >= 1
                    ready = (r and p) or (r and can_attach and hc[PSY]) or (p and can_attach and hc[FIRE]) \
                        or (rosa_ok and (r or p or discard_ids.count(FIRE) >= 1 and discard_ids.count(PSY) >= 1))
                    if ready:
                        s = 9000 + (500 if r and p else 0) + (100 if o["inPlayArea"] == A_ACTIVE else 0)
                    else:
                        s = -1
                elif ev["id"] == DRAKLOAK:
                    s = 8800 + (100 if o["inPlayArea"] == A_ACTIVE else 0)
                elif ev["id"] == DUDUNSPARCE:
                    s = 4000 if len(hand) <= 4 else -1
                else:
                    s = -1

        elif t == O_ABILITY:
            c = card_at(obs, o["area"], o["index"], me)
            cid = c["id"] if c else 0
            if cid == DRAKLOAK:
                s = 9600
            elif cid == FEZANDIPITI:
                s = 9700
            elif cid == DUDUNSPARCE:
                s = 9400 if len(hand) <= 4 else -1
            elif cid == MUNKIDORI:
                s = 9300 if any(pk["hp"] < pk["maxHp"] for pk in my_field) and op_field else -1
            elif cid == MEOWTH:
                s = 9500
            else:
                s = 2000

        elif t == O_RETREAT:
            if my_active and bench_best[0] > active_dmg and bench_best[0] >= 70:
                s = 8100
            elif my_active and my_active["id"] in (BUDEW, DUNSPARCE, DREEPY) and bench_best[0] >= 70:
                s = 8100
            else:
                s = -1

        elif t == O_ATTACK:
            aid = o.get("attackId")
            base = {PHANTOM_DIVE: 1500, DRAGON_HEADBUTT: 1300, CRUEL_ARROW: 1250, LAND_CRUSH: 1250, JET_HEADBUTT: 1200,
                    MIND_BEND: 1150, ITCHY_POLLEN: 1100, BITE: 1100, RAM: 1050, PETTY_GRUDGE: 1050, TRADING_PLACES: 1020, TUCK_TAIL: 900}
            s = base.get(aid, 1000)
            dmg = {PHANTOM_DIVE: 200, DRAGON_HEADBUTT: 70, JET_HEADBUTT: 70, CRUEL_ARROW: 100, LAND_CRUSH: 90, MIND_BEND: 60, BITE: 40}.get(aid, 0)
            if op_active and dmg >= op_active["hp"]:
                s += 400
            if aid == TRADING_PLACES and bench_best[0] < 70:
                s = 900

        elif t == O_END:
            s = 0

        elif t == O_CARD:
            s = card_option_score(obs, o, ctx, me, my, op, hc, fc, bench_room, dragon_lines, energy_in_hand, active_dmg, sel)

        elif t in (O_ENERGY, O_ECARD):
            pk = card_at(obs, o["area"], o["index"], o.get("playerIndex", me))
            ec = None
            if pk and o.get("energyIndex") is not None and o["energyIndex"] < len(pk.get("energyCards", [])):
                ec = pk["energyCards"][o["energyIndex"]]
            eid = ec["id"] if ec else 0
            s = {DARK: 100, PSY: 40, FIRE: 30}.get(eid, 50)
            if pk and pk["id"] == MUNKIDORI and eid == DARK:
                s = 10
        else:
            s = 1
        scores.append(s)

    order = sorted(range(len(opts)), key=lambda i: scores[i], reverse=True)
    lo, hi = sel["minCount"], sel["maxCount"]
    if ctx in (C_SETUP_BENCH, C_TO_BENCH, C_DISCARD, C_TO_FIELD):
        chosen = [i for i in order if scores[i] >= 0][:hi]
        if len(chosen) < lo:
            chosen = order[:lo]
        return chosen
    if ctx == C_MAIN:
        return [order[0]]
    return order[:max(lo, min(hi, 1))] if hi == 1 or ctx not in (C_TO_HAND,) else order[:hi]


def attach_score(eid, pk, is_active, energy_in_hand):
    cid = pk["id"]
    r, p, d, n = energy_counts(pk)
    want = needs_energy(pk)
    s = -1
    if cid in (DRAGAPULT, DRAKLOAK):
        if eid in want:
            s = 8900 if cid == DRAGAPULT else 8200
        elif eid == DARK and n == 0 and cid == DRAGAPULT:
            s = 5000
        else:
            s = 2500
    elif cid == MUNKIDORI:
        s = 7900 if eid == DARK and not d else 1000
    elif cid == DREEPY:
        s = 6000 if eid in (PSY, FIRE) else 1500
    elif cid == FEZANDIPITI:
        s = 3000 if n < 3 else 500
    elif cid == DUDUNSPARCE:
        s = 2800
    else:
        s = 1200
    if is_active and s > 0:
        s += 100
    return s


def boss_score(my_active, active_dmg, op, op_active):
    if not my_active or active_dmg < 70:
        return -1
    best = -1
    for b in op["bench"]:
        if not b:
            continue
        prizes = 3 if CARDS[b["id"]].megaEx else 2 if CARDS[b["id"]].ex else 1
        if b["hp"] <= active_dmg:
            val = 7800 + prizes * 200 + (600 if b["id"] == CLEFAIRY else 0)
            if prizes == 1 and b["id"] != DHELMISE:
                val = -1  # save Boss's Orders for Clefairy / Latias / Dhelmise
            if op_active and op_active["hp"] <= active_dmg and prizes <= (2 if CARDS[op_active["id"]].ex else 1):
                val = -1  # active is already a kill of equal value
            best = max(best, val)
    return best


def discard_value(cid, hc, fc, bench_room):
    """Higher = happier to discard from hand."""
    if cid == JUDGE: return 100
    if cid == HAMMER: return 90
    if cid == RUINS: return 95
    if cid == ROSA: return 80
    if cid == DUDUNSPARCE: return 75 if fc[DUNSPARCE] == 0 else 30
    if cid == STRETCHER: return 70
    if cid == LILLIE: return 65 if hc[LILLIE] >= 2 else 15
    if cid == STAMP: return 60
    if cid == DUNSPARCE: return 55 if fc[DUNSPARCE] else 35
    if cid == MEOWTH: return 50
    if cid == FEZANDIPITI: return 45 if fc[FEZANDIPITI] else 25
    if cid == BUDEW: return 40 if fc[BUDEW] else 20
    if cid == POFFIN: return 80 if bench_room == 0 else 20
    if cid == BOSS: return 30 if hc[BOSS] >= 2 else 10
    if cid == POKEPAD: return 25
    if cid == CRISPIN: return 28
    if cid == DREEPY: return 10 if fc[DREEPY] + fc[DRAKLOAK] + fc[DRAGAPULT] < 3 else 45
    if cid in ENERGY_IDS: return 5
    if cid == DRAKLOAK: return 0 if hc[DRAKLOAK] <= 1 else 20
    if cid == DRAGAPULT: return -10 if hc[DRAGAPULT] <= 1 else 15
    return 20


def card_option_score(obs, o, ctx, me, my, op, hc, fc, bench_room, dragon_lines, energy_in_hand, active_dmg, sel):
    c = card_at(obs, o["area"], o["index"], o.get("playerIndex", me))
    if c is None:
        return 0
    cid = c["id"]
    mine = o.get("playerIndex", me) == me
    effect = (sel.get("effect") or {}).get("id", 0)

    if ctx == C_SETUP_ACTIVE:
        return {DREEPY: 100, BUDEW: 80, DUNSPARCE: 60, MUNKIDORI: 50, FEZANDIPITI: 30, MEOWTH: 20}.get(cid, 10)
    if ctx in (C_SETUP_BENCH, C_TO_BENCH, C_TO_FIELD):
        if cid == DREEPY:
            return 100 if dragon_lines + hc[DREEPY] < 3 else 40
        return {MUNKIDORI: 70, BUDEW: 60, DUNSPARCE: 50, FEZANDIPITI: 40 if ctx == C_SETUP_BENCH else -1, MEOWTH: -1}.get(cid, -1)

    if ctx in (C_SWITCH, C_TO_ACTIVE):
        if mine:
            dmg, _ = best_attack(c, len(op["bench"]))
            base = {DRAGAPULT: 80, DRAKLOAK: 60, FEZANDIPITI: 40, BUDEW: 45 if obs["current"]["turn"] <= 8 else 20,
                    MUNKIDORI: 35, DUNSPARCE: 30, DREEPY: 25, MEOWTH: 20, DUDUNSPARCE: 30}.get(cid, 10)
            return base + dmg + c["hp"] // 10
        # choosing the opponent's new Active (Boss's Orders): a kill we can take, by prize value
        prizes = 3 if CARDS[cid].megaEx else 2 if CARDS[cid].ex else 1
        s = prizes * 100 + len(c.get("energies", [])) * 10 - c["hp"] // 10 + (300 if cid == CLEFAIRY else 0)
        if c["hp"] <= active_dmg:
            s += 1000
        return s

    if ctx == C_TO_HAND:
        unevolved_dreepy = fc[DREEPY] > 0
        unevolved_drak = fc[DRAKLOAK] > 0
        need_fire = any(FIRE in needs_energy(pk) for pk in ([my["active"][0]] if my["active"] and my["active"][0] else []) + my["bench"])
        need_psy = any(PSY in needs_energy(pk) for pk in ([my["active"][0]] if my["active"] and my["active"][0] else []) + my["bench"])
        if o["area"] == A_PRIZE:
            return 1
        lonely = len(my["bench"]) == 0  # no bench: one knockout loses the game, get bodies first
        if (dragon_lines == 0 or lonely) and cid == DREEPY:
            return 130
        if (dragon_lines == 0 or lonely) and cid == POFFIN:
            return 125
        if lonely and cid in (BUDEW, DUNSPARCE, MUNKIDORI):
            return 118
        if cid == DRAGAPULT:
            return 110 if unevolved_drak and hc[DRAGAPULT] == 0 else 60
        if cid == DRAKLOAK:
            return 100 if unevolved_dreepy and hc[DRAKLOAK] == 0 else 55
        if cid == FIRE:
            return 95 if need_fire and energy_in_hand == 0 else (70 if need_fire else 30)
        if cid == PSY:
            return 92 if need_psy and energy_in_hand == 0 else (68 if need_psy else 30)
        if cid == DARK:
            return 75 if fc[MUNKIDORI] and not any(DARK in needs_energy(pk) for pk in []) else 35
        if cid == LILLIE:
            return 85 if len(my.get("hand") or []) <= 4 and hc[LILLIE] == 0 else 50
        if cid == BOSS:
            return 72 if hc[BOSS] == 0 else 40
        if cid == POFFIN:
            return 78 if bench_room >= 1 and dragon_lines < 3 else 25
        if cid == POKEPAD:
            return 74
        if cid == ULTRABALL:
            return 62
        if cid == CRISPIN:
            return 70 if energy_in_hand == 0 else 40
        if cid == DREEPY:
            return 58 if dragon_lines < 3 else 15
        if cid == MUNKIDORI:
            return 52 if fc[MUNKIDORI] == 0 else 10
        if cid == FEZANDIPITI:
            return 48 if fc[FEZANDIPITI] == 0 else 10
        if cid == HAMMER:
            return 45
        if cid == STRETCHER:
            return 44
        if cid == RUINS:
            return 42
        if cid == BUDEW:
            return 40 if fc[BUDEW] == 0 else 10
        if cid == STAMP:
            return 38
        if cid == MEOWTH:
            return 36
        if cid == DUNSPARCE:
            return 30
        if cid == ROSA:
            return 28
        if cid == JUDGE:
            return 20
        if cid == DUDUNSPARCE:
            return 22
        return 25

    if ctx == C_DISCARD:
        return discard_value(cid, hc, fc, bench_room) if mine else 0

    if ctx in (C_DAMAGE_COUNTER_ANY, C_DAMAGE_COUNTER, C_DAMAGE, C_EFFECT_TARGET):
        if mine:
            return (c["maxHp"] - c["hp"]) if "maxHp" in c else 0  # Adrena-Brain source: most damaged
        remain = max(1, sel.get("remainDamageCounter") or 1) * 10
        prizes = 3 if CARDS[cid].megaEx else 2 if CARDS[cid].ex else 1
        s = prizes * 50 - c["hp"] // 10 + len(c.get("energies", [])) * 15 + (120 if cid == CLEFAIRY else 0)
        if c["hp"] <= remain:
            s += 1000 + prizes * 300 + c["hp"]  # finish it, and prefer using the counters fully
        elif c["hp"] <= remain + 60:
            s += 200  # set up for next turn
        return s

    if ctx == C_REMOVE_DC:
        return (c["maxHp"] - c["hp"]) if mine and "maxHp" in c else 0

    if ctx in (C_ATTACH_FROM, C_ATTACH_TO):
        if "hp" in c:  # a Pokémon target
            return attach_score(FIRE if FIRE in needs_energy(c) else PSY, c, o["area"] == A_ACTIVE, energy_in_hand)
        return {FIRE: 100, PSY: 95, DARK: 60}.get(cid, 30)

    if ctx == C_DETACH_FROM:
        return 100 if cid in (DUNSPARCE, BUDEW, DREEPY) else 10

    if ctx == C_LOOK:
        return 50
    return 10
