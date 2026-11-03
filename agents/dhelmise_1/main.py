"""dhelmise_1 - my own rule-based agent for the Dhelmise / Hide 'n' Sneak deck.

Plan: go second; dump Hide 'n' Sneak Pokémon (Shuppet, Banette, Poltchageist, Sinistcha) into the
discard with Gwynn, Explorer's Guidance, Ultra Ball, Naveen and Prism Tower; Dhelmise then hits for
170 off a single Psychic energy.  Latias ex gives free retreat, Lillie's Clefairy ex is benched only
against Dragon decks (it makes them Psychic-weak: 340 from Dhelmise), Sinistcha's Matcha Spin spreads
40 to every opposing Pokémon once 6 are discarded, Spiritomb and Ursaluna close games.
"""
from __future__ import annotations

import os
from collections import Counter

from cg.api import all_card_data

SHUPPET, BANETTE, POLT, SINISTCHA, DHELMISE, CLEFAIRY, SPIRITOMB, LATIAS, URSALUNA = 1374, 1375, 1348, 1349, 1380, 272, 1376, 184, 44
EXPLORER, LILLIE, GWYNN, BOSS, NAVEEN, ULTRABALL, POKEPAD, STRETCHER, PRISM = 1185, 1227, 1428, 1182, 1239, 1121, 1152, 1097, 1430
TELEPATH, PSY, LEGACY = 19, 5, 12
HNS = {SHUPPET, BANETTE, POLT, SINISTCHA}
ENERGY_IDS = {TELEPATH, PSY, LEGACY}
VENGEFUL_ANCHOR, MATCHA_SPIN, PUPPET_PULL, EON_BLADE, BLOOD_MOON, FULL_MOON, SPIRITUAL_END, FURTIVE_DROP, HANG_DOWN = 1708, 1664, 1703, 243, 41, 371, 1704, 1663, 1702
E_PSY, E_RAINBOW, E_TR = 5, 10, 11
T_DRAGON = 9
A_DECK, A_HAND, A_DISCARD, A_ACTIVE, A_BENCH, A_PRIZE, A_STADIUM, A_LOOKING = 1, 2, 3, 4, 5, 6, 7, 12
O_NUMBER, O_YES, O_NO, O_CARD, O_TOOL, O_ECARD, O_ENERGY, O_PLAY, O_ATTACH, O_EVOLVE, O_ABILITY, O_DISCARD, O_RETREAT, O_ATTACK, O_END, O_SKILL, O_SC = range(17)
C_MAIN, C_SETUP_ACTIVE, C_SETUP_BENCH, C_SWITCH, C_TO_ACTIVE, C_TO_BENCH, C_TO_FIELD, C_TO_HAND, C_DISCARD = range(9)
C_DAMAGE_COUNTER, C_DAMAGE_COUNTER_ANY, C_DAMAGE, C_EFFECT_TARGET, C_ATTACH_FROM, C_ATTACH_TO = 13, 14, 15, 25, 21, 22
C_IS_FIRST, C_MULLIGAN = 41, 42

_path = "deck.csv" if os.path.exists("deck.csv") else "/kaggle_simulations/agent/deck.csv"
MY_DECK = [int(x) for x in open(_path).read().split()][:60]
CARDS = {c.cardId: c for c in all_card_data()}
GO_FIRST = os.environ.get("DHELMISE_GO_FIRST", "0") == "1"


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


def psy(pk):
    return sum(1 for e in pk.get("energies", []) if e in (E_PSY, E_RAINBOW, E_TR))


def prizes_of(cid):
    c = CARDS[cid]
    return 3 if c.megaEx else 2 if c.ex else 1


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
    discard_ids = [c["id"] for c in my["discard"]]
    hns_discard = sum(1 for c in discard_ids if c in HNS)
    hns_hand = sum(hc[c] for c in HNS)
    energy_in_hand = sum(hc[e] for e in ENERGY_IDS)
    stadium = cur["stadium"][0]["id"] if cur["stadium"] else 0
    op_dragon = any(CARDS[pk["id"]].energyType == T_DRAGON for pk in op_field)
    op_prizes_taken = 6 - len(op["prize"])
    clefairy_up = fc[CLEFAIRY] > 0
    outlets = hc[GWYNN] + hc[NAVEEN] + hc[ULTRABALL] + (1 if stadium == PRISM or hc[PRISM] else 0)

    def damage_vs(pk, target):
        """Damage pk's best attack does to target right now."""
        cid = pk["id"]
        n, p = len(pk.get("energies", [])), psy(pk)
        if cid == DHELMISE and p >= 1:
            d = 170 if hns_discard >= 4 else 30
        elif cid == LATIAS and p >= 2 and n >= 3:
            d = 200
        elif cid == URSALUNA and n >= max(0, 5 - op_prizes_taken):
            d = 240
        elif cid == BANETTE and p >= 1:
            d = 80
        elif cid == CLEFAIRY and p >= 1 and n >= 2:
            d = 20 + 20 * (len(my["bench"]) + len(op["bench"]))
        elif cid == SINISTCHA and n >= 1 and hns_discard >= 6:
            d = 40
        else:
            return 0
        if target is None:
            return d
        weak = CARDS[target["id"]].weakness
        if clefairy_up and CARDS[target["id"]].energyType == T_DRAGON:
            weak = E_PSY
        atype = CARDS[cid].energyType
        if weak is not None and weak == atype:
            d *= 2
        if CARDS[target["id"]].resistance == atype:
            d = max(0, d - 30)
        return d

    active_dmg = damage_vs(my_active, op_active) if my_active else 0
    bench_ready = max((damage_vs(b, op_active) for b in my["bench"]), default=0)

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
            if cid == DHELMISE:
                s = 8500 if fc[DHELMISE] < 2 and bench_room >= 1 else (6000 if fc[DHELMISE] < 3 and bench_room >= 2 else -1)
            elif cid == POLT:
                s = 8000 if fc[POLT] + fc[SINISTCHA] == 0 and bench_room >= 1 else -1
            elif cid == LATIAS:
                s = 4000 if len(my_field) <= 1 and fc[LATIAS] == 0 else -1  # 2-prize liability: only as an emergency body
            elif cid == CLEFAIRY:
                s = 8300 if op_dragon and fc[CLEFAIRY] == 0 and bench_room >= 1 else -1
            elif cid == SPIRITOMB:
                s = 7000 if hns_discard >= 12 and fc[SPIRITOMB] == 0 else -1
            elif cid == URSALUNA:
                s = 7000 if op_prizes_taken >= 4 and fc[URSALUNA] == 0 else -1
            elif cid == SHUPPET:
                s = 3000 if len(my["bench"]) == 0 and fc[DHELMISE] == 0 else -1
            elif cid == ULTRABALL:
                need = fc[DHELMISE] + hc[DHELMISE] < 2 or (fc[LATIAS] + hc[LATIAS] == 0) or (op_dragon and fc[CLEFAIRY] + hc[CLEFAIRY] == 0)
                s = 8600 if hns_hand >= 2 else (8200 if hns_hand >= 1 and need else (7000 if need and len(hand) >= 4 else -1))
                if my["deckCount"] == 0:
                    s = -1
            elif cid == POKEPAD:
                s = (8200 if fc[DHELMISE] + hc[DHELMISE] < 2 or fc[POLT] + fc[SINISTCHA] == 0 else 6500) if my["deckCount"] else -1
            elif cid == STRETCHER:
                want = (energy_in_hand == 0 and PSY in discard_ids) or (fc[DHELMISE] + hc[DHELMISE] == 0 and DHELMISE in discard_ids) \
                    or (fc[LATIAS] + hc[LATIAS] == 0 and LATIAS in discard_ids)
                s = 7500 if want else -1
            elif cid == PRISM:
                s = 8900 if stadium != PRISM else -1
            elif cid == GWYNN:
                s = 8700 if hns_hand >= 2 else (8100 if hns_hand == 1 else -1)
                if my["deckCount"] <= 3:
                    s = -1
            elif cid == EXPLORER:
                s = (8650 if hns_discard < 6 else 7900) if my["deckCount"] > 6 else -1
            elif cid == NAVEEN:
                s = 8000 if hns_hand >= 1 and len(hand) - hns_hand <= 4 else (7000 if len(hand) <= 3 else -1)
                if my["deckCount"] <= 4:
                    s = -1
            elif cid == LILLIE:
                s = 7600 if len(hand) <= 5 and hns_hand == 0 and energy_in_hand + hc[DHELMISE] <= 1 else -1
            elif cid == BOSS:
                s = -1
                for b in op["bench"]:
                    if b and active_dmg >= b["hp"] and (prizes_of(b["id"]) >= 2 or not (op_active and active_dmg >= op_active["hp"])):
                        s = max(s, 8800 + prizes_of(b["id"]) * 100)
            else:
                s = -1

        elif t == O_ATTACH:
            c = card_at(obs, o["area"], o["index"], me)
            pk = card_at(obs, o["inPlayArea"], o["inPlayIndex"], me)
            if c and pk:
                s = attach_score(c["id"], pk, o["inPlayArea"] == A_ACTIVE, hns_discard, op_dragon, op_prizes_taken, my["benchMax"] - len(my["bench"]))

        elif t == O_EVOLVE:
            ev = card_at(obs, o["area"], o["index"], me)
            pk = card_at(obs, o["inPlayArea"], o["inPlayIndex"], me)
            if ev and pk:
                if ev["id"] == SINISTCHA:
                    s = 7800 if hns_discard >= 6 and (hc[SINISTCHA] >= 2 or hns_discard >= 8) else -1
                elif ev["id"] == BANETTE:
                    s = 7800 if hns_discard < 4 and o["inPlayArea"] == A_ACTIVE and psy(pk) >= 1 and hc[BANETTE] >= 2 else -1
                else:
                    s = -1

        elif t == O_ABILITY:
            c = card_at(obs, o["area"], o["index"], me)
            cid = c["id"] if c else 0
            if cid == PRISM:
                fodder = sum(1 for x in hand_ids if discard_value(x, hc, fc, hns_discard, op_dragon, stadium) >= 60)
                s = 9000 if fodder >= 2 and my["deckCount"] > 2 else -1
            else:
                s = 2000

        elif t == O_RETREAT:
            s = 8100 if my_active and bench_ready > active_dmg and bench_ready >= 80 else -1

        elif t == O_ATTACK:
            aid = o.get("attackId")
            base = {VENGEFUL_ANCHOR: 1500, SPIRITUAL_END: 1480 if hns_discard >= 13 else 100, MATCHA_SPIN: 1450 if hns_discard >= 6 else 200,
                    BLOOD_MOON: 1400, EON_BLADE: 1350, PUPPET_PULL: 1300, FULL_MOON: 1200, FURTIVE_DROP: 1000, HANG_DOWN: 950}
            s = base.get(aid, 900)
            if op_active and active_dmg >= op_active["hp"] and aid in (VENGEFUL_ANCHOR, BLOOD_MOON, EON_BLADE, PUPPET_PULL, FULL_MOON):
                s += 400
            if aid == MATCHA_SPIN and hns_discard >= 6:
                s += 100 * sum(1 for pk in op_field if pk["hp"] <= 40)

        elif t == O_END:
            s = 0

        elif t == O_CARD:
            s = card_score(obs, o, ctx, me, my, op, hc, fc, hns_discard, hns_hand, energy_in_hand, op_dragon, op_prizes_taken, outlets, stadium, active_dmg, sel, damage_vs, my_active)

        elif t in (O_ENERGY, O_ECARD):
            pk = card_at(obs, o["area"], o["index"], o.get("playerIndex", me))
            ec = None
            if pk and o.get("energyIndex") is not None and o["energyIndex"] < len(pk.get("energyCards", [])):
                ec = pk["energyCards"][o["energyIndex"]]
            s = {PSY: 100, TELEPATH: 80, LEGACY: 10}.get(ec["id"] if ec else 0, 50)
        else:
            s = 1
        scores.append(s)

    order = sorted(range(len(opts)), key=lambda i: scores[i], reverse=True)
    lo, hi = sel["minCount"], sel["maxCount"]
    if ctx in (C_SETUP_BENCH, C_TO_BENCH, C_DISCARD, C_TO_FIELD):
        chosen = [i for i in order if scores[i] >= 0][:hi]
        return chosen if len(chosen) >= lo else order[:lo]
    if ctx == C_MAIN:
        return [order[0]]
    return order[:hi] if ctx == C_TO_HAND else order[:max(lo, min(hi, 1))]


def attach_score(eid, pk, is_active, hns_discard, op_dragon, op_prizes_taken, bench_room):
    cid, p, n = pk["id"], psy(pk), len(pk.get("energies", []))
    if cid == DHELMISE:
        s = 8950 if p == 0 else 1500
        if eid == TELEPATH and p == 0 and bench_room >= 1:
            s += 60  # Telepath benches two Basic Psychic Pokémon on the way in
    elif cid == SPIRITOMB:
        s = 8900 if hns_discard >= 12 and p == 0 else 500
    elif cid == URSALUNA:
        s = 6500 if 1 <= 5 - op_prizes_taken <= 2 and n < 5 - op_prizes_taken else 400
    elif cid == CLEFAIRY:
        s = 7000 if op_dragon and n < 2 else 800
    elif cid == LATIAS:
        s = 6000 if n < 3 else 300
    elif cid == SINISTCHA:
        s = 6800 if hns_discard >= 6 and n == 0 else 600
    elif cid == BANETTE:
        s = 5000 if hns_discard < 4 and p == 0 else 300
    else:
        s = 1000
    if eid == LEGACY and cid not in (DHELMISE, CLEFAIRY, URSALUNA, LATIAS):
        s -= 300
    return s + (100 if is_active else 0)


def discard_value(cid, hc, fc, hns_discard, op_dragon, stadium):
    if cid == BANETTE: return 100
    if cid == SHUPPET: return 95
    if cid == SINISTCHA: return 90 if not (fc[POLT] and hns_discard >= 6 and hc[SINISTCHA] == 1) else 40
    if cid == POLT: return 85 if fc[POLT] + fc[SINISTCHA] or hc[POLT] >= 2 else 45
    if cid == LATIAS: return 72
    if cid == PRISM: return 65 if stadium == PRISM or hc[PRISM] >= 2 else 20
    if cid == SPIRITOMB: return 60 if hns_discard < 9 else -30
    if cid == CLEFAIRY: return 55 if (not op_dragon or fc[CLEFAIRY]) else -40
    if cid == URSALUNA: return 35
    if cid == LILLIE: return 30
    if cid == NAVEEN: return 25
    if cid == EXPLORER: return 22
    if cid == ULTRABALL: return 20
    if cid == POKEPAD: return 18
    if cid == STRETCHER: return 15
    if cid == BOSS: return 12 if hc[BOSS] >= 2 else -10
    if cid == GWYNN: return 10
    if cid == DHELMISE: return 25 if fc[DHELMISE] + hc[DHELMISE] >= 3 else -50
    if cid == PSY: return -60
    if cid == TELEPATH: return -70
    if cid == LEGACY: return -80
    return 0


def card_score(obs, o, ctx, me, my, op, hc, fc, hns_discard, hns_hand, energy_in_hand, op_dragon, op_prizes_taken, outlets, stadium, active_dmg, sel, damage_vs, my_active):
    c = card_at(obs, o["area"], o["index"], o.get("playerIndex", me))
    if c is None:
        return 0
    cid = c["id"]
    mine = o.get("playerIndex", me) == me
    if ctx == C_SETUP_ACTIVE:
        return {DHELMISE: 100, POLT: 90, LATIAS: 70, SHUPPET: 50, CLEFAIRY: 40, SPIRITOMB: 30, URSALUNA: 10}.get(cid, 5)
    if ctx in (C_SETUP_BENCH, C_TO_BENCH, C_TO_FIELD):
        if cid == DHELMISE:
            return 100 if fc[DHELMISE] < 2 else 20
        if cid == LATIAS:
            return -1
        if cid == POLT:
            return 40 if fc[POLT] + fc[SINISTCHA] == 0 else -1
        if cid == CLEFAIRY:
            return 80 if op_dragon and fc[CLEFAIRY] == 0 else -1
        if cid == SPIRITOMB:
            return 60 if hns_discard >= 11 and fc[SPIRITOMB] == 0 else -1
        return -1
    if ctx in (C_SWITCH, C_TO_ACTIVE):
        if mine:
            d = damage_vs(c, op["active"][0] if op["active"] and op["active"][0] else None)
            base = {DHELMISE: 120, SINISTCHA: 80, POLT: 100, LATIAS: 90, SPIRITOMB: 70, URSALUNA: 60, CLEFAIRY: 65, BANETTE: 50, SHUPPET: 40}.get(cid, 10)
            return base + d + c["hp"] // 20
        s = prizes_of(cid) * 100 + len(c.get("energies", [])) * 10 - c["hp"] // 20
        if active_dmg >= c["hp"]:
            s += 1000
        return s
    if ctx == C_TO_HAND:
        if o["area"] == A_PRIZE:
            return 1
        if o["area"] == A_LOOKING:  # Explorer's Guidance: what we leave is discarded, so leave Hide 'n' Sneak
            if cid in HNS:
                return 260 if (cid == SINISTCHA and fc[POLT] and hns_discard >= 6 and hc[SINISTCHA] == 0) else -500
            if cid in ENERGY_IDS:
                return 450 if energy_in_hand == 0 else 240
            if cid == DHELMISE:
                return 420 if fc[DHELMISE] + hc[DHELMISE] < 2 else 180
            if cid == LATIAS:
                return 440 if fc[LATIAS] + hc[LATIAS] == 0 else 50
            if cid == CLEFAIRY:
                return 430 if op_dragon and fc[CLEFAIRY] + hc[CLEFAIRY] == 0 else 100
            return {BOSS: 350, GWYNN: 340, ULTRABALL: 320, PRISM: 300 if stadium != PRISM else 120, STRETCHER: 310, POKEPAD: 290,
                    EXPLORER: 280, NAVEEN: 265, LILLIE: 250, SPIRITOMB: 300 if hns_discard >= 11 else 80, URSALUNA: 300 if op_prizes_taken >= 4 else 90}.get(cid, 100)
        if o["area"] == A_DISCARD:  # Night Stretcher
            if cid == PSY:
                return 100 if energy_in_hand == 0 else 40
            if cid == DHELMISE:
                return 95 if fc[DHELMISE] + hc[DHELMISE] == 0 else 50
            if cid == LATIAS:
                return 90 if fc[LATIAS] + hc[LATIAS] == 0 else 20
            return -100 if cid in HNS else 30
        # deck search (Ultra Ball / Poké Pad / Puppet Pull)
        if cid == DHELMISE:
            return 100 if fc[DHELMISE] + hc[DHELMISE] < 2 else 45
        if cid == LATIAS:
            return 8
        if cid == CLEFAIRY:
            return 93 if op_dragon and fc[CLEFAIRY] + hc[CLEFAIRY] == 0 else 5
        if cid == POLT:
            return 80 if fc[POLT] + fc[SINISTCHA] == 0 and hc[POLT] == 0 else 50
        if cid in ENERGY_IDS:
            return 85 if energy_in_hand == 0 else 30
        if cid in HNS:
            return (70 if cid == BANETTE else 68 if cid == SHUPPET else 60) if outlets and hns_discard < 6 else 40
        if cid == SPIRITOMB:
            return 50 if hns_discard >= 11 else 5
        if cid == URSALUNA:
            return 50 if op_prizes_taken >= 4 else 5
        if cid == BOSS:
            return 75 if hc[BOSS] == 0 else 30
        return 20
    if ctx == C_DISCARD:
        return discard_value(cid, hc, fc, hns_discard, op_dragon, stadium) if mine else 0
    if ctx in (C_DAMAGE_COUNTER, C_DAMAGE_COUNTER_ANY, C_DAMAGE, C_EFFECT_TARGET):
        if mine:
            return 0
        dmg = c["maxHp"] - c["hp"] if "maxHp" in c else 0
        return prizes_of(cid) * 100 + dmg + (1000 if 3 * dmg >= c["hp"] else 0)
    if ctx in (C_ATTACH_FROM, C_ATTACH_TO):
        if "hp" in c:
            return attach_score(PSY, c, o["area"] == A_ACTIVE, hns_discard, op_dragon, op_prizes_taken, my["benchMax"] - len(my["bench"]))
        return {PSY: 100, TELEPATH: 90, LEGACY: 50}.get(cid, 30)
    return 10
