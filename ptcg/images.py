"""Official card images from pokemon.com's card asset CDN.

The competition card CSV gives each printing's expansion code and collection
number; the CDN serves ``.../cards/web/<SET>/<SET>_EN_<NUMBER>.png``.  The
expansion -> SET code table below was verified by probing the CDN.  The
resolved map is cached in ``data/card_images.json`` (built once by
``python -m ptcg.images``) so the server never touches the network.
"""
from __future__ import annotations

import csv
import json
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "EN_Card_Data_R2_full.csv"
CACHE_PATH = ROOT / "data" / "card_images.json"
CDN = "https://assets.pokemon.com/static-assets/content-assets/cms2/img/cards/web/{set}/{set}_EN_{num}.png"

SET_CODES = {
    "SVI": "SV01", "PAL": "SV02", "TEF": "SV05", "TWM": "SV06", "SFA": "SV6PT5", "SCR": "SV07",
    "SSP": "SV08", "PRE": "SV8PT5", "JTG": "SV09", "DRI": "SV10", "BLK": "ZSV10PT5", "WHT": "RSV10PT5",
    "MEG": "ME01", "PFL": "ME02", "ASC": "ME2PT5", "POR": "ME03", "CRI": "ME04", "PBL": "ME05",
    "SVE": "SVE", "SVP": "SVP", "PROMO": "SVP",
}


def printings() -> dict[int, list[tuple[str, str]]]:
    """card id -> [(expansion, number), ...] in CSV order."""
    out: dict[int, list[tuple[str, str]]] = {}
    with CSV_PATH.open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                cid = int(row["Card ID"])
            except ValueError:
                continue
            key = (row["Expansion"].strip(), row["Collection No."].strip())
            lst = out.setdefault(cid, [])
            if key not in lst:
                lst.append(key)
    return out


def candidate_urls(card_id: int, table: dict[int, list[tuple[str, str]]] | None = None) -> list[str]:
    table = table or printings()
    urls = []
    for exp, num in table.get(card_id, []):
        code = SET_CODES.get(exp)
        if code and num:
            urls.append(CDN.format(set=code, num=num))
    return urls


@lru_cache(maxsize=1)
def image_map() -> dict[int, str]:
    """card id -> verified image URL (from the cache file; falls back to unverified first candidates)."""
    if CACHE_PATH.exists():
        return {int(k): v for k, v in json.loads(CACHE_PATH.read_text()).items()}
    table = printings()
    return {cid: urls[0] for cid in table if (urls := candidate_urls(cid, table))}


def image_url(card_id: int) -> str | None:
    return image_map().get(card_id)


def build_cache(workers: int = 16) -> dict[int, str]:
    """Probe the CDN for every card and write data/card_images.json."""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    table = printings()

    def ok(url: str) -> bool:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status == 200
        except Exception:
            return False

    def resolve(cid: int) -> tuple[int, str | None]:
        for url in candidate_urls(cid, table):
            if ok(url):
                return cid, url
        return cid, None

    with ThreadPoolExecutor(workers) as ex:
        results = dict(ex.map(resolve, sorted(table)))
    found = {cid: url for cid, url in results.items() if url}
    CACHE_PATH.write_text(json.dumps({str(k): v for k, v in sorted(found.items())}, indent=0))
    missing = sorted(cid for cid, url in results.items() if not url)
    print(f"{len(found)} of {len(table)} cards resolved; written to {CACHE_PATH}")
    if missing:
        print("no image for card IDs:", missing)
    return found


if __name__ == "__main__":
    build_cache(int(sys.argv[1]) if len(sys.argv) > 1 else 16)
