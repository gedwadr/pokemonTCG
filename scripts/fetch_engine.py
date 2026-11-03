#!/usr/bin/env python3
"""Download the competition engine and card data from Kaggle into this checkout.

The engine (engine/), the card CSVs and PDFs (data/) are competition-use-only and
are deliberately not committed.  Run this once after cloning:

    export KAGGLE_API_TOKEN=...      # or ~/.kaggle/kaggle.json
    python scripts/fetch_engine.py

It uses the `kaggle` CLI (pip install kaggle) and needs you to have joined the
competition so the files are downloadable with your account.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SLUG = "the-pokemon-company-ptcg-ai-battle-challenge-playground"


def main() -> None:
    if shutil.which("kaggle") is None:
        sys.exit("kaggle CLI not found: pip install kaggle")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        subprocess.run(["kaggle", "competitions", "download", "-c", SLUG, "-p", str(tmp)], check=True)
        zips = list(tmp.glob("*.zip"))
        if not zips:
            sys.exit("download produced no zip file")
        src = tmp / "x"
        with zipfile.ZipFile(zips[0]) as z:
            z.extractall(src)
        cg = next(src.rglob("cg/sim.py")).parent
        prog = next(p for p in src.rglob("README.md") if "ptcgProgram" in str(p)).parent
        (ROOT / "engine").mkdir(exist_ok=True)
        (ROOT / "data").mkdir(exist_ok=True)
        shutil.copytree(cg, ROOT / "engine" / "cg", dirs_exist_ok=True)
        shutil.copytree(prog, ROOT / "engine" / "ptcgProgram", dirs_exist_ok=True)
        shutil.copy(prog / "LICENSES" / "LicenseRef-PTCG-ABC-Competition-Use-Only.txt", ROOT / "engine" / "LICENSE-ENGINE.txt")
        for name in ("EN_Card_Data_R2_full.csv", "JP_Card_Data_R2_full.csv", "Card_ID_List_EN.pdf", "Card_ID_List_JP.pdf"):
            shutil.copy(next(src.rglob(name)), ROOT / "data" / name)
        sample = next(src.rglob("sample_submission/**/deck.csv"))
        (ROOT / "decks").mkdir(exist_ok=True)
        if not (ROOT / "decks" / "sample.csv").exists():
            shutil.copy(sample, ROOT / "decks" / "sample.csv")
    print("engine and data installed under", ROOT)
    print("verify with: python -m pytest -q")


if __name__ == "__main__":
    main()
