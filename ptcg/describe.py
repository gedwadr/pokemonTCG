"""Human-readable labels for observations, selections and options."""
from __future__ import annotations

from typing import Any

from . import cards
from .cards import AREA_NAMES, ENERGY_LETTERS

SELECT_CONTEXT = {
    0: "Main phase: choose an action", 1: "Choose your Active Pokémon (setup)", 2: "Choose Bench Pokémon (setup)",
    3: "Choose the Pokémon to switch with your Active", 4: "Choose a Pokémon to put into the Active Spot",
    5: "Choose a Pokémon to put on the Bench", 6: "Choose a Pokémon to put into play", 7: "Choose a card to add to your hand",
    8: "Choose a card to discard", 9: "Choose a card to return to the deck", 10: "Choose a card for the bottom of the deck",
    11: "Choose a card for the prizes", 12: "Choose the card that stays", 13: "Choose a Pokémon to place damage counters on",
    14: "Place damage counters as you like", 15: "Choose a Pokémon to damage", 16: "Choose a Pokémon to remove damage counters from",
    17: "Choose a Pokémon to heal", 18: "Choose the Pokémon to evolve", 19: "Choose the card to evolve into",
    20: "Choose a Pokémon to devolve", 21: "Choose the Pokémon to attach to", 22: "Choose the card to attach",
    23: "Choose the Pokémon to remove a card from", 24: "Choose a card to look at", 25: "Choose the effect target",
    26: "Choose the Energy card to discard", 27: "Choose the Pokémon Tool to discard", 28: "Choose the Energy card to replace",
    29: "Choose the card to discard", 30: "Choose the Energy to discard", 31: "Choose the Energy to return to hand",
    32: "Choose the Energy to return to deck", 33: "Choose the Energy to switch", 34: "Choose the order of effects",
    35: "Choose an attack", 36: "Choose the attack to disable", 37: "Choose evolution source and target",
    38: "How many cards to draw?", 39: "How many damage counters to place?", 40: "How many damage counters to remove?",
    41: "Go first?", 42: "Mulligan (redraw)?", 43: "Activate the effect?", 44: "Use the first effect?",
    45: "Devolve further?", 46: "Choose heads?", 47: "Choose the Special Condition to apply", 48: "Choose the Special Condition to recover",
}
SPECIAL_CONDITIONS = ["Poisoned", "Burned", "Asleep", "Paralyzed", "Confused"]
LOG_RESULT_REASON = {1: "all Prize cards taken", 2: "deck out", 3: "no Active Pokémon", 4: "card effect"}


def _card_at(obs: dict[str, Any], player: int | None, area: int | None, index: int | None) -> dict[str, Any] | None:
    cur = obs.get("current") or {}
    sel = obs.get("select") or {}
    try:
        if area == 1:   # deck (only when the engine exposes it)
            return (sel.get("deck") or [])[index]
        if area == 12:  # looking
            return (cur.get("looking") or [])[index]
        if area == 7:
            return cur["stadium"][index]
        p = cur["players"][player]
        if area == 2:
            return (p.get("hand") or [])[index]
        if area == 3:
            return p["discard"][index]
        if area == 4:
            return p["active"][index]
        if area == 5:
            return p["bench"][index]
        if area == 6:
            return p["prize"][index]
    except (IndexError, KeyError, TypeError):
        return None
    return None


def _pokemon_label(obs: dict[str, Any], player: int | None, area: int | None, index: int | None) -> str:
    me = (obs.get("current") or {}).get("yourIndex")
    who = "your" if player == me else "opp"
    c = _card_at(obs, player, area, index)
    where = AREA_NAMES.get(area, str(area))
    if c is None:
        return f"{who} {where}[{index}] (face down)"
    name = cards.card_name(c.get("id"))
    hp = f" {c['hp']}/{c['maxHp']}hp" if "hp" in c else ""
    return f"{name}{hp} ({who} {where}[{index}])"


def option_label(obs: dict[str, Any], opt: dict[str, Any]) -> str:
    t = opt.get("type")
    me = (obs.get("current") or {}).get("yourIndex")
    if t == 0:
        return f"{opt.get('number')}"
    if t == 1:
        return "Yes"
    if t == 2:
        return "No"
    if t == 3:
        return _pokemon_label(obs, opt.get("playerIndex"), opt.get("area"), opt.get("index"))
    if t == 4:
        host = _pokemon_label(obs, opt.get("playerIndex"), opt.get("area"), opt.get("index"))
        c = _card_at(obs, opt.get("playerIndex"), opt.get("area"), opt.get("index"))
        tool = None
        if c and c.get("tools"):
            try:
                tool = c["tools"][opt.get("toolIndex")]
            except (IndexError, TypeError):
                tool = None
        return f"Tool {cards.card_name(tool['id']) if tool else '?'} on {host}"
    if t in (5, 6):
        host = _pokemon_label(obs, opt.get("playerIndex"), opt.get("area"), opt.get("index"))
        c = _card_at(obs, opt.get("playerIndex"), opt.get("area"), opt.get("index"))
        en = None
        if c and c.get("energyCards"):
            try:
                en = c["energyCards"][opt.get("energyIndex")]
            except (IndexError, TypeError):
                en = None
        cnt = f" x{opt['count']}" if opt.get("count") else ""
        return f"Energy {cards.card_name(en['id']) if en else '?'}{cnt} on {host}"
    if t == 7:
        c = _card_at(obs, me, 2, opt.get("index"))
        return f"Play {cards.card_name(c['id']) if c else '?'} (hand[{opt.get('index')}])"
    if t == 8:
        c = _card_at(obs, me, opt.get("area"), opt.get("index"))
        host = _pokemon_label(obs, me, opt.get("inPlayArea"), opt.get("inPlayIndex"))
        return f"Attach {cards.card_name(c['id']) if c else '?'} to {host}"
    if t == 9:
        c = _card_at(obs, me, opt.get("area"), opt.get("index"))
        host = _pokemon_label(obs, me, opt.get("inPlayArea"), opt.get("inPlayIndex"))
        return f"Evolve {host} into {cards.card_name(c['id']) if c else '?'}"
    if t == 10:
        return f"Ability of {_pokemon_label(obs, me, opt.get('area'), opt.get('index'))}"
    if t == 11:
        return f"Discard {_pokemon_label(obs, me, opt.get('area'), opt.get('index'))}"
    if t == 12:
        return "Retreat"
    if t == 13:
        a = cards.attack(opt.get("attackId"))
        if a:
            cost = "".join(ENERGY_LETTERS[e] for e in a["energies"]) or "-"
            return f"Attack {a['name']} [{cost}] {a['damage']}dmg"
        return f"Attack #{opt.get('attackId')}"
    if t == 14:
        return "End turn"
    if t == 15:
        return f"Effect of {cards.card_name(opt.get('cardId')) if opt.get('cardId') else 'Special Condition'}"
    if t == 16:
        sc = opt.get("specialConditionType")
        return SPECIAL_CONDITIONS[sc] if sc is not None and sc < len(SPECIAL_CONDITIONS) else f"condition {sc}"
    return str(opt)


def select_title(obs: dict[str, Any]) -> str:
    sel = obs.get("select")
    if not sel:
        return "no selection pending"
    title = SELECT_CONTEXT.get(sel.get("context"), f"context {sel.get('context')}")
    lo, hi = sel.get("minCount"), sel.get("maxCount")
    rng = f"pick {lo}" if lo == hi else f"pick {lo}-{hi}"
    extra = ""
    if sel.get("effect"):
        extra += f" | effect of {cards.card_name(sel['effect'].get('id'))}"
    if sel.get("contextCard"):
        extra += f" | about {cards.card_name(sel['contextCard'].get('id'))}"
    return f"{title} ({rng}){extra}"


def option_labels(obs: dict[str, Any]) -> list[str]:
    sel = obs.get("select")
    if not sel:
        return []
    return [option_label(obs, o) for o in sel["option"]]


def log_line(log: dict[str, Any], me: int | None = None) -> str:
    t = log.get("type")
    p = log.get("playerIndex")
    who = "You" if p == me else ("Opp" if me is not None else f"P{p}")
    n = cards.card_name
    if t == 0:
        return f"{who} shuffled the deck"
    if t == 1:
        return f"{who} {'has' if log.get('hasBasicPokemon') else 'has no'} Basic Pokémon"
    if t == 2:
        return f"--- {who}: turn start ---"
    if t == 3:
        return f"--- {who}: turn end ---"
    if t == 4:
        return f"{who} drew {n(log.get('cardId'))}"
    if t == 5:
        return f"{who} drew a card"
    if t == 6:
        return f"{who} moved {n(log.get('cardId'))}: {AREA_NAMES.get(log.get('fromArea'))} -> {AREA_NAMES.get(log.get('toArea'))}"
    if t == 7:
        return f"{who} moved a card: {AREA_NAMES.get(log.get('fromArea'))} -> {AREA_NAMES.get(log.get('toArea'))}"
    if t == 8:
        return f"{who} switched {n(log.get('cardIdActive'))} with {n(log.get('cardIdBench'))}"
    if t == 9:
        return f"{who}: {n(log.get('cardIdBefore'))} became {n(log.get('cardIdAfter'))}"
    if t == 10:
        return f"{who} played {n(log.get('cardId'))}"
    if t == 11:
        return f"{who} attached {n(log.get('cardId'))} to {n(log.get('cardIdTarget'))}"
    if t == 12:
        return f"{who} evolved {n(log.get('cardIdTarget'))} into {n(log.get('cardId'))}"
    if t == 13:
        return f"{who} devolved {n(log.get('cardIdTarget'))} ({n(log.get('cardId'))} removed)"
    if t == 14:
        return f"{who} moved {n(log.get('cardId'))} from {n(log.get('cardIdBefore'))} to {n(log.get('cardIdAfter'))}"
    if t == 15:
        return f"{who}: {n(log.get('cardId'))} used {cards.attack_name(log.get('attackId'))}"
    if t == 16:
        v = log.get("value", 0)
        kind = "damage counters" if log.get("putDamageCounter") else "HP"
        return f"{who}: {n(log.get('cardId'))} {'+' if v > 0 else ''}{v} {kind}"
    if t in (17, 18, 19, 20, 21):
        cond = SPECIAL_CONDITIONS[t - 17]
        return f"{who}: {n(log.get('cardId'))} {'recovered from' if log.get('isRecover') else 'is now'} {cond}"
    if t == 22:
        return f"{who} flipped {'heads' if log.get('head') else 'tails'}"
    if t == 23:
        r = log.get("result")
        res = "draw" if r == 2 else f"player {r} wins"
        return f"=== Result: {res} ({LOG_RESULT_REASON.get(log.get('reason'), log.get('reason'))}) ==="
    return str(log)


def board_summary(obs: dict[str, Any], me: int | None = None) -> str:
    """Multi-line text rendering of the board from ``me``'s point of view."""
    cur = obs.get("current")
    if not cur:
        return "(no state)"
    if me is None:
        me = cur["yourIndex"]
    lines = [f"Turn {cur['turn']}  first player: {cur['firstPlayer']}  acting: P{cur['yourIndex']}"
             f"  [supporter={'x' if cur['supporterPlayed'] else '-'} stadium={'x' if cur['stadiumPlayed'] else '-'}"
             f" energy={'x' if cur['energyAttached'] else '-'} retreat={'x' if cur['retreated'] else '-'}]"]
    if cur.get("stadium"):
        lines.append(f"Stadium: {cards.card_name(cur['stadium'][0]['id'])}")
    for idx in (1 - me, me):
        p = cur["players"][idx]
        tag = "YOU" if idx == me else "OPP"
        conds = [c for c, k in (("PSN", "poisoned"), ("BRN", "burned"), ("SLP", "asleep"), ("PAR", "paralyzed"), ("CNF", "confused")) if p.get(k)]
        lines.append(f"[{tag} P{idx}] deck {p['deckCount']}  hand {p['handCount']}  prizes {len(p['prize'])}  discard {len(p['discard'])}")
        if p["active"]:
            a = p["active"][0]
            lines.append("  Active: " + (_poke(a) if a else "(face down)") + (f"  {' '.join(conds)}" if conds else ""))
        else:
            lines.append("  Active: (none)")
        for i, b in enumerate(p["bench"]):
            lines.append(f"  Bench[{i}]: {_poke(b)}")
        if p.get("hand") is not None:
            lines.append("  Hand: " + ", ".join(f"[{i}] {cards.card_name(c['id'])}" for i, c in enumerate(p["hand"])))
    return "\n".join(lines)


def _poke(pk: dict[str, Any]) -> str:
    en = "".join(ENERGY_LETTERS[e] for e in pk.get("energies", []))
    tools = ",".join(cards.card_name(t["id"]) for t in pk.get("tools", []))
    s = f"{cards.card_name(pk['id'])} {pk['hp']}/{pk['maxHp']}hp"
    if en:
        s += f" E[{en}]"
    if tools:
        s += f" T[{tools}]"
    if pk.get("appearThisTurn"):
        s += " (new)"
    return s
