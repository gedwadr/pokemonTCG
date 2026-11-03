#!/usr/bin/env python3
"""Convert a Limitless-style decklist ("4 Dreepy TWM 128" per line) into a deck file of engine card IDs.

    python scripts/import_decklist.py decks/sources/alakazam.txt decks/alakazam.csv

Cards are matched by set code + collection number first, then by name (any printing of the same
name is the same engine card).  Unmapped lines are reported and written as comments so you can
substitute by hand.  Exit status is 1 if the result is not a legal 60-card deck.
"""
from __future__ import annotations

import csv
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ptcg.deck import validate_deck  # noqa: E402

CSV_PATH = ROOT / "data" / "EN_Card_Data_R2_full.csv"
ENERGY = {"grass": 1, "fire": 2, "water": 3, "lightning": 4, "psychic": 5, "fighting": 6, "darkness": 7, "metal": 8}
ALIASES = {"telepathic psychic energy": "telepath psychic energy"}
LINE = re.compile(r"^\s*(\d+)\s+(.+?)\s+([A-Z0-9]{2,5})\s+(\d+[a-z]?)\s*$")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace("’", "'")
    s = ALIASES.get(s.strip(), s)
    return re.sub(r"[^a-z0-9]", "", s)


def load_tables():
    by_setnum, by_name = {}, {}
    with CSV_PATH.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                cid = int(r["Card ID"])
            except ValueError:
                continue
            by_setnum.setdefault((r["Expansion"], r["Collection No."]), cid)
            by_name.setdefault(norm(r["Card Name"]), cid)
    return by_setnum, by_name


def convert(src: Path) -> tuple[list[tuple[int, int, str]], list[str]]:
    by_setnum, by_name = load_tables()
    out, unmapped = [], []
    for raw in src.read_text(encoding="utf-8").splitlines():
        line = raw.strip().lstrip("-• ").strip()
        if not line or line.startswith("#") or not line[0].isdigit():
            continue
        m = LINE.match(line)
        if not m:
            unmapped.append(f"unparsed: {line}")
            continue
        count, name, setc, num = int(m.group(1)), m.group(2).strip(), m.group(3), m.group(4)
        em = re.match(r"^(?:basic )?(\w+) energy$", name.lower())
        cid = ENERGY.get(em.group(1)) if em else None
        if cid is None:
            cid = by_setnum.get((setc, num)) or by_name.get(norm(name)) or by_name.get(norm("Basic " + name))
        if cid is None:
            unmapped.append(f"{count} {name} {setc} {num}")
        else:
            out.append((cid, count, f"{name} ({setc} {num})"))
    return out, unmapped


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    out, unmapped = convert(src)
    deck = [cid for cid, n, _ in out for _ in range(n)]
    problems = validate_deck(deck) if len(deck) == 60 else [f"{len(deck)} cards mapped"]
    lines = [f"# imported from {src.name}; format: <engine card id>,<count>"]
    lines += [f"{cid},{n}    # {label}" for cid, n, label in out]
    lines += [f"# UNMAPPED: {u}" for u in unmapped]
    dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    status = "OK" if not problems else "PROBLEMS: " + "; ".join(problems)
    print(f"{src.name}: {len(deck)} cards -> {dst}  [{status}]" + (f"  unmapped: {unmapped}" if unmapped else ""))
    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    main()
