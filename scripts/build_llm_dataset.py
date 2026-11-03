#!/usr/bin/env python3
"""Build an LLM training corpus from the competition card pool.

Outputs (under data/llm/, which is gitignored because card text is competition-use-only):

  cards.txt       one plain-text document per card, separated by a line of ---  (raw / continued pretraining)
  cards.jsonl     one JSON object per card: {"id", "name", "text", "fields": {...structured...}}
  sft.jsonl       chat-format supervised pairs {"messages":[{"role":"user"},{"role":"assistant"}]},
                  several per card plus glossary questions
  glossary.txt    Pokémon TCG terms and rules in plain language (also folded into sft.jsonl)

Sources: data/EN_Card_Data_R2_full.csv (card text, costs, printings) merged with the engine's own
card table (engine/cg via ptcg.cards) for stage/ex/Tera flags and attack IDs.

    python scripts/build_llm_dataset.py [--out data/llm]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ptcg import cards  # noqa: E402

CSV_PATH = ROOT / "data" / "EN_Card_Data_R2_full.csv"

ENERGY_WORDS = {"G": "Grass", "R": "Fire", "W": "Water", "L": "Lightning", "P": "Psychic", "F": "Fighting",
                "D": "Darkness", "M": "Metal", "N": "Dragon", "C": "Colorless", "A": "Stellar"}
EXPANSIONS = {
    "SVI": "Scarlet & Violet", "PAL": "Paldea Evolved", "TEF": "Temporal Forces", "TWM": "Twilight Masquerade",
    "SFA": "Shrouded Fable", "SCR": "Stellar Crown", "SSP": "Surging Sparks", "PRE": "Prismatic Evolutions",
    "JTG": "Journey Together", "DRI": "Destined Rivals", "BLK": "Black Bolt", "WHT": "White Flare",
    "MEG": "Mega Evolution", "PFL": "Phantasmal Flames", "ASC": "Ascended Heroes", "POR": "Perfect Order",
    "CRI": "Chaos Rising", "PBL": "Pitch Black", "SVE": "Scarlet & Violet Basic Energy", "SVP": "Scarlet & Violet Black Star Promos",
    "PROMO": "Promo",
}
RULE_TEXT = {
    "Pokémon ex": "Pokémon ex rule: When your Pokémon ex is Knocked Out, your opponent takes 2 Prize cards.",
    "Mega Pokémon ex": "Mega Evolution Pokémon ex rule: When your Mega Evolution Pokémon ex is Knocked Out, your opponent takes 3 Prize cards.",
    "ACE SPEC": "ACE SPEC rule: You can't have more than 1 ACE SPEC card in your deck.",
}
TERA_RULE = "Tera rule: As long as this Pokémon is on your Bench, prevent all damage done to this Pokémon by attacks (both yours and your opponent's)."


def words(symbols: str) -> str:
    """'{R}{P}' -> 'Fire, Psychic'; '●●' -> 'Colorless, Colorless'; '' -> 'none'."""
    if not symbols or symbols in ("n/a", "No cost"):
        return "none"
    out = []
    for tok in re.findall(r"\{([^}]+)\}|(●)", symbols):
        sym = tok[0] or "C"
        out.append(ENERGY_WORDS.get(sym, sym))
    return ", ".join(out) if out else "none"


def expand(text: str) -> str:
    """Replace in-text symbols with words: '{P} Energy' -> 'Psychic Energy', '{ex}' -> 'ex'."""
    def sub(m):
        sym = m.group(1)
        if sym in ENERGY_WORDS:
            return ENERGY_WORDS[sym]
        return sym  # ex, V, ACE SPEC, Team Rocket
    text = text.strip()
    if text in ("", "n/a"):
        return ""
    return re.sub(r"\{([^}]+)\}", sub, text).replace("’", "'").strip()


def load_csv() -> dict[int, list[dict]]:
    by: dict[int, list[dict]] = OrderedDict()
    with CSV_PATH.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                cid = int(r["Card ID"])
            except ValueError:
                continue
            by.setdefault(cid, []).append(r)
    return by


def build_record(cid: int, rows: list[dict]) -> dict:
    eng = cards.card(cid) or {}
    r0 = rows[0]
    kind = r0["Stage (Pokémon)/Type (Energy and Trainer)"]
    name = expand(r0["Card Name"])
    printings = []
    for r in rows:
        key = (r["Expansion"], r["Collection No."])
        if key not in printings and r["Expansion"]:
            printings.append(key)
    rec = {
        "id": cid, "name": name, "kind": kind,
        "category": "Energy" if "Energy" in kind else ("Trainer" if kind in ("Item", "Supporter", "Stadium", "Pokémon Tool") else "Pokémon"),
        "printings": [{"expansion": e, "expansionName": EXPANSIONS.get(e, e), "number": n} for e, n in printings],
        "rule": r0["Rule"] if r0["Rule"] != "n/a" else None,
        "tags": [t for t in [r0["Category"]] if t and t != "n/a"],
        "abilities": [], "attacks": [], "effect": None,
    }
    if rec["category"] == "Pokémon":
        rec.update({
            "hp": int(r0["HP"]) if r0["HP"].isdigit() else None,
            "type": words(r0["Type"]),
            "evolvesFrom": r0["Previous stage"] if r0["Previous stage"] not in ("", "n/a") else None,
            "weakness": words(r0["Weakness"]) if r0["Weakness"] not in ("", "n/a") else "none",
            "resistance": words(r0["Resistance (Type)"]) if r0["Resistance (Type)"] not in ("", "n/a") else "none",
            "retreat": int(r0["Retreat"]) if r0["Retreat"].isdigit() else 0,
            "species": r0["Species"] if r0["Species"] not in ("", "n/a") else None,
            "tera": bool(eng.get("tera")),
        })
        for r in rows:
            mv, eff = r["Move Name"].strip(), expand(r["Effect Explanation"])
            if not mv:
                continue
            if mv.startswith("[Ability]"):
                rec["abilities"].append({"name": mv[len("[Ability]"):].strip(), "text": eff})
            elif mv.startswith("[Tera]"):
                rec["tera"] = True
            else:
                dmg = r["Damage"].strip()
                rec["attacks"].append({"name": mv, "cost": words(r["Cost"]), "damage": dmg if dmg and dmg != "n/a" else "",
                                       "text": eff})
    else:
        if rec["category"] == "Energy":
            rec["type"] = words(r0["Type"]) if r0["Type"] not in ("", "n/a") else None
        texts = [t for t in (expand(r["Effect Explanation"]) for r in rows) if t]
        rec["effect"] = "\n".join(OrderedDict.fromkeys(texts)) or None
    return rec


def render(rec: dict) -> str:
    p = rec["printings"][0] if rec["printings"] else None
    where = f"{p['expansionName']} ({p['expansion']} {p['number']})" if p else "promo"
    lines = [f"Card: {rec['name']} (card ID {rec['id']}, {where})"]
    if len(rec["printings"]) > 1:
        lines.append("Also printed as: " + "; ".join(f"{q['expansionName']} ({q['expansion']} {q['number']})" for q in rec["printings"][1:]))
    if rec["category"] == "Pokémon":
        stage = rec["kind"].replace(" Pokémon", "")
        head = f"Category: Pokémon, {stage}"
        if rec.get("evolvesFrom"):
            head += f", evolves from {rec['evolvesFrom']}"
        head += f". Type: {rec['type']}. HP: {rec['hp']}."
        lines.append(head)
        if rec["rule"]:
            lines.append(RULE_TEXT.get(rec["rule"], rec["rule"]))
        for t in rec["tags"]:
            lines.append(f"Tag: {t}." + (" This is a Tera Pokémon." if t.startswith("Tera") else ""))
        if rec.get("tera"):
            lines.append(TERA_RULE)
        for a in rec["abilities"]:
            lines.append(f"Ability: {a['name']}. {a['text']}")
        for a in rec["attacks"]:
            s = f"Attack: {a['name']}. Cost: {a['cost']}."
            if a["damage"]:
                s += f" Damage: {a['damage']}."
            if a["text"]:
                s += f" Effect: {a['text']}"
            lines.append(s)
        lines.append(f"Weakness: {rec['weakness']}. Resistance: {rec['resistance']}. Retreat cost: {rec['retreat']}.")
        if rec.get("species"):
            lines.append(f"Species: {rec['species']}.")
    else:
        kind = rec["kind"]
        lines.append(f"Category: {rec['category']}, {kind}." + (f" Provides {rec['type']} Energy." if rec.get("type") and kind == "Basic Energy" else ""))
        if rec["rule"]:
            lines.append(RULE_TEXT.get(rec["rule"], rec["rule"]))
        for t in rec["tags"]:
            lines.append(f"Tag: {t}.")
        if rec["effect"]:
            lines.append(f"Text: {rec['effect']}")
        lines.append(TRAINER_RULES.get(kind, ""))
    return "\n".join(l for l in lines if l)


TRAINER_RULES = {
    "Item": "Item rule: You may play any number of Item cards during your turn.",
    "Supporter": "Supporter rule: You may play only 1 Supporter card during your turn.",
    "Stadium": "Stadium rule: You may play only 1 Stadium card during your turn. Put it next to the Active Spot, and discard it if another Stadium comes into play. A Stadium with the same name can't be played.",
    "Pokémon Tool": "Pokémon Tool rule: Attach a Pokémon Tool to 1 of your Pokémon that doesn't already have a Pokémon Tool attached.",
    "Special Energy": "Special Energy rule: Special Energy cards provide Energy as described on the card and may have extra effects.",
    "Basic Energy": "Basic Energy rule: Attach 1 Energy card from your hand to 1 of your Pokémon once during your turn.",
}

GLOSSARY = [
    ("Active Spot", "The single position where a player's attacking Pokémon stands. Only the Active Pokémon can attack or be attacked directly; each player must always have one Active Pokémon, and a player with no Pokémon to put there loses."),
    ("Bench", "Up to 5 Pokémon a player has in play behind the Active Pokémon. Benched Pokémon can be attached energy, evolved, retreated into, and targeted by some effects, but they do not attack."),
    ("Prize cards", "6 face-down cards each player sets aside at the start. Whenever you Knock Out an opponent's Pokémon you take 1 Prize card (2 for a Pokémon ex, 3 for a Mega Evolution Pokémon ex). Taking your last Prize card wins the game."),
    ("Knocked Out (KO)", "A Pokémon whose damage equals or exceeds its HP is Knocked Out: it and everything attached go to the discard pile and the opponent takes Prize cards."),
    ("HP", "Hit Points. Damage accumulates on a Pokémon as damage counters; when damage reaches HP the Pokémon is Knocked Out."),
    ("Damage counter", "A token worth 10 damage. Effects that 'place damage counters' add damage without using an attack, so Weakness and Resistance do not apply."),
    ("Energy", "Cards attached to Pokémon to pay attack and retreat costs. You may attach 1 Energy card from hand per turn (the manual attachment); card effects can attach more. Types: Grass, Fire, Water, Lightning, Psychic, Fighting, Darkness, Metal, Dragon (no basic Dragon Energy exists), Colorless (any energy counts)."),
    ("Basic Energy", "The eight plain Energy cards (Grass, Fire, Water, Lightning, Psychic, Fighting, Darkness, Metal). Unlimited copies are allowed in a deck."),
    ("Special Energy", "Energy cards with extra rules or effects, limited to 4 copies per name like other cards."),
    ("Attack cost", "The energy a Pokémon needs attached to use an attack, shown as energy symbols. A Colorless symbol can be paid with any Energy."),
    ("Weakness", "If the attacking Pokémon's type matches the defender's Weakness, attack damage is doubled (×2)."),
    ("Resistance", "If the attacking Pokémon's type matches the defender's Resistance, attack damage is reduced by 30."),
    ("Retreat cost", "The number of Energy you must discard from your Active Pokémon to move it to the Bench and promote a Benched Pokémon. You may retreat once per turn. Some Pokémon have a retreat cost of 0 (free retreat)."),
    ("Basic Pokémon", "A Pokémon that can be played directly from hand onto the Bench. Every deck needs at least one."),
    ("Stage 1 / Stage 2", "Evolution Pokémon. A Stage 1 is played on top of the Basic it evolves from; a Stage 2 on top of the matching Stage 1. You cannot evolve a Pokémon the turn it was played or the first turn of the game."),
    ("Evolution", "Playing an evolution card onto the Pokémon named in its 'evolves from'. The Pokémon keeps its damage and attachments; its HP, attacks and abilities become the new card's."),
    ("Pokémon ex", "Powerful Pokémon marked 'ex'. They have a Rule Box: when Knocked Out the opponent takes 2 Prize cards."),
    ("Mega Evolution Pokémon ex", "Mega ex Pokémon give up 3 Prize cards when Knocked Out."),
    ("Rule Box", "The rule text printed on Pokémon ex, Mega ex and similar cards. Many effects say 'Pokémon that doesn't have a Rule Box', meaning ordinary single-prize Pokémon."),
    ("Tera Pokémon", "Pokémon marked Tera take no damage from attacks while on the Bench."),
    ("ACE SPEC", "Very strong Trainer or Energy cards; a deck may contain only 1 ACE SPEC card in total."),
    ("Ability", "A passive or once-per-turn effect a Pokémon has that is not an attack. Abilities can be used during your turn in addition to attacking."),
    ("Item", "Trainer card you can play any number of times per turn, resolved immediately then discarded."),
    ("Supporter", "Trainer card limited to 1 per turn, usually a strong draw, search or disruption effect. The player going first cannot play a Supporter on their first turn."),
    ("Stadium", "Trainer card that stays in play and affects both players until replaced by another Stadium. Only 1 Stadium per turn can be played."),
    ("Pokémon Tool", "Trainer card attached to a Pokémon; each Pokémon can hold 1 Tool."),
    ("Trainer's Pokémon", "Pokémon whose name includes a character, like Lillie's Clefairy ex. They evolve only from and into Pokémon with the same owner name and are counted as different cards from the plain version."),
    ("Special Conditions", "Asleep (can't attack or retreat; flip a coin between turns to wake), Paralyzed (can't attack or retreat for a turn), Confused (flip a coin to attack; tails puts 3 damage counters on itself), Poisoned (1 damage counter between turns), Burned (2 damage counters between turns, flip to recover). Retreating or evolving removes Special Conditions."),
    ("Discard pile", "Face-up pile of used, discarded and Knocked Out cards. Many cards count or recover cards from it."),
    ("Deck out", "A player who must draw at the start of their turn but has no cards in their deck loses."),
    ("Mulligan", "If your opening hand has no Basic Pokémon you reveal it, shuffle, and draw again; the opponent may draw an extra card."),
    ("Going first", "The coin-flip winner chooses who goes first. The first player draws but cannot attack or play a Supporter on their first turn."),
    ("Turn structure", "Draw a card; then in any order: play Basic Pokémon to the Bench, evolve, attach 1 Energy, play Trainers (Items freely, 1 Supporter, 1 Stadium), use Abilities, retreat once; then attack (ends the turn) or end the turn."),
    ("Boss's Orders / gust", "Effects that switch an opponent's Benched Pokémon into their Active Spot, letting you attack a Pokémon the opponent was protecting."),
    ("Spread damage", "Placing damage counters on several of the opponent's Pokémon at once, usually on the Bench, to set up multiple Knock Outs."),
    ("Prize trade", "Comparing how many Prize cards each side gives up per Knock Out. Single-prize attackers that Knock Out Pokémon ex win the trade."),
    ("Deck rules", "Exactly 60 cards; at most 4 copies of any card name except Basic Energy; at most 1 ACE SPEC; at least 1 Basic Pokémon."),
]


def sft_pairs(rec: dict, text: str, rng: random.Random) -> list[dict]:
    n = rec["name"]
    qs: list[tuple[str, str]] = [(f"Describe the Pokémon TCG card {n}.", text),
                                 (f"What does the card {n} do?", text)]
    if rec["category"] == "Pokémon":
        qs.append((f"How much HP does {n} have, and what type is it?", f"{n} is a {rec['type']}-type {rec['kind']} with {rec['hp']} HP."))
        qs.append((f"What is {n}'s Weakness, Resistance and Retreat cost?",
                   f"{n} has Weakness {rec['weakness']}, Resistance {rec['resistance']}, and a Retreat cost of {rec['retreat']}."))
        if rec.get("evolvesFrom"):
            qs.append((f"What does {n} evolve from?", f"{n} is a {rec['kind']} that evolves from {rec['evolvesFrom']}."))
        else:
            qs.append((f"Is {n} a Basic Pokémon?", f"Yes, {n} is a Basic Pokémon and can be played directly from your hand onto the Bench."
                       if rec["kind"] == "Basic Pokémon" else f"No, {n} is a {rec['kind']}."))
        for a in rec["abilities"]:
            qs.append((f"What does the Ability {a['name']} on {n} do?", f"{a['name']} (Ability of {n}): {a['text']}"))
        for a in rec["attacks"]:
            ans = f"{n}'s attack {a['name']} costs {a['cost']}"
            ans += f" and does {a['damage']} damage" if a["damage"] else " and does no base damage"
            ans += f". Effect: {a['text']}" if a["text"] else "."
            qs.append((f"What does {n}'s attack {a['name']} do and what does it cost?", ans))
        if rec["rule"]:
            qs.append((f"How many Prize cards does the opponent take when {n} is Knocked Out?",
                       "3 Prize cards, because it is a Mega Evolution Pokémon ex." if rec["rule"] == "Mega Pokémon ex" else
                       "2 Prize cards, because it is a Pokémon ex." if rec["rule"] == "Pokémon ex" else "1 Prize card."))
        elif rng.random() < 0.3:
            qs.append((f"How many Prize cards does the opponent take when {n} is Knocked Out?", "1 Prize card. It is not a Pokémon ex."))
    else:
        qs.append((f"What kind of card is {n}?", f"{n} is a {rec['kind']} card." + (f" {rec['effect']}" if rec["effect"] else "")))
        if rec["kind"] == "Supporter":
            qs.append((f"How many copies of {n} can I play in one turn?", f"Only one Supporter per turn, so {n} can be played once per turn. Its effect: {rec['effect']}"))
        if rec["rule"] == "ACE SPEC":
            qs.append((f"Can I run two copies of {n} in a deck?", f"No. {n} is an ACE SPEC card, and a deck may contain only one ACE SPEC card in total."))
    return [{"messages": [{"role": "user", "content": q}, {"role": "assistant", "content": a}], "card_id": rec["id"]} for q, a in qs]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "llm"))
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    by = load_csv()
    records = [build_record(cid, rows) for cid, rows in by.items()]
    docs = [(rec, render(rec)) for rec in records]

    (out / "cards.txt").write_text("\n\n---\n\n".join(t for _, t in docs) + "\n", encoding="utf-8")
    with (out / "cards.jsonl").open("w", encoding="utf-8") as fh:
        for rec, t in docs:
            fh.write(json.dumps({"id": rec["id"], "name": rec["name"], "text": t, "fields": rec}, ensure_ascii=False) + "\n")

    glossary_lines = [f"{term}: {desc}" for term, desc in GLOSSARY]
    (out / "glossary.txt").write_text("Pokémon Trading Card Game glossary\n\n" + "\n\n".join(glossary_lines) + "\n", encoding="utf-8")

    pairs = []
    for rec, t in docs:
        pairs.extend(sft_pairs(rec, t, rng))
    for term, desc in GLOSSARY:
        pairs.append({"messages": [{"role": "user", "content": f"In the Pokémon TCG, what does '{term}' mean?"}, {"role": "assistant", "content": desc}], "card_id": None})
        pairs.append({"messages": [{"role": "user", "content": f"Explain the Pokémon TCG term {term}."}, {"role": "assistant", "content": f"{term}: {desc}"}], "card_id": None})
    rng.shuffle(pairs)
    with (out / "sft.jsonl").open("w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    n_pk = sum(1 for r in records if r["category"] == "Pokémon")
    print(f"{len(records)} cards ({n_pk} Pokémon, {len(records) - n_pk} Trainer/Energy) -> {out}/")
    print(f"cards.txt {sum(len(t) for _, t in docs):,} chars; sft.jsonl {len(pairs):,} pairs; glossary {len(GLOSSARY)} terms")


if __name__ == "__main__":
    main()
