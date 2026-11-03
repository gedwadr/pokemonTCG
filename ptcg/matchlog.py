"""Human-readable match logs and a results ledger.

One file per game in ``logs/<timestamp>-<room>.log`` listing every engine
event and every selection, plus one line per game appended to
``logs/results.csv``.  Used by the server and by ``scripts/local_match.py``.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any

from . import describe

RESULTS_FIELDS = ["time", "room", "player0", "player1", "winner", "result", "reason", "steps", "turn", "log", "replay", "rl"]


class MatchLogger:
    def __init__(self, log_dir: str | Path | None, room: str, names: list[str], echo=None):
        """``echo`` is an optional callable (e.g. ``logging.getLogger().debug``) that also receives every line."""
        self.dir = Path(log_dir) if log_dir else None
        self.room = room
        self.names = names
        self.echo = echo
        self.path: Path | None = None
        self._fh = None
        self._turn = -1
        self.started = time.time()
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.path = self.dir / f"{stamp}-{room}.log"
            n = 1
            while self.path.exists():
                n += 1
                self.path = self.dir / f"{stamp}-{room}-{n}.log"
            self._fh = self.path.open("w", encoding="utf-8")
        self.line(f"# room {room}: P0={names[0]} vs P1={names[1]}  ({time.strftime('%Y-%m-%d %H:%M:%S')})")

    def line(self, text: str) -> None:
        if self._fh:
            self._fh.write(text + "\n")
        if self.echo:
            self.echo("%s", text)

    def move(self, step: int, obs: dict[str, Any], select: list[int]) -> None:
        """Log the events that led to this selection and the selection itself."""
        cur = obs["current"]
        p = cur["yourIndex"]
        for ev in obs.get("logs", []):
            self.line(f"    {describe.log_line(ev, None)}")
        if cur["turn"] != self._turn:
            self._turn = cur["turn"]
            self.line("")
            for row in describe.board_summary(obs, p).splitlines():
                self.line(f"  | {row}")
        labels = describe.option_labels(obs)
        chosen = ", ".join(labels[i] if 0 <= i < len(labels) else str(i) for i in select) or "(none)"
        self.line(f"[{step:4d}] T{cur['turn']} P{p} {self.names[p]} | {describe.select_title(obs)} -> {chosen}")
        if self._fh:
            self._fh.flush()

    def events(self, obs: dict[str, Any]) -> None:
        """Log trailing events (e.g. the final observation after the game ended)."""
        for ev in obs.get("logs", []):
            self.line(f"    {describe.log_line(ev, None)}")

    def result(self, result: int, reason_text: str, steps: int, turn: int, replay: str | None = None,
               rl: str | None = None) -> None:
        winner = "draw" if result == 2 else f"P{result} {self.names[result]}"
        elapsed = time.time() - self.started
        self.line("")
        self.line(f"# RESULT: {winner} ({reason_text}) after {steps} selections, turn {turn}, {elapsed:.1f}s")
        if self._fh:
            self._fh.close()
            self._fh = None
        if self.dir:
            append_result(self.dir / "results.csv", {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"), "room": self.room,
                "player0": self.names[0], "player1": self.names[1],
                "winner": "draw" if result == 2 else self.names[result], "result": result,
                "reason": reason_text, "steps": steps, "turn": turn,
                "log": str(self.path) if self.path else "", "replay": replay or "", "rl": rl or "",
            })

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def append_result(path: Path, row: dict[str, Any]) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RESULTS_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
