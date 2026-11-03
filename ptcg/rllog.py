"""Decision-level logs for reinforcement learning, one JSON Lines file per game.

Line 1 is a ``meta`` record.  Every following line is one decision::

    {"type": "step", "game_id": ..., "step": 12, "turn": 3, "player": 0, "agent": "human",
     "obs": {...},                 # the observation the actor saw (select, current, logs); search blob dropped
     "legal_actions": [...],       # = obs.select.option, repeated for convenience
     "action": [3],                # option indices chosen
     "action_labels": ["Play Gwynn (hand[0])"],
     "events": [...],              # engine log entries caused by this action (seen by the actor before its next decision)
     "events_text": ["P0 played Gwynn", ...],
     "reward": 0.0,                # terminal reward on the actor's last decision: +1 win, -1 loss, 0 draw
     "done": false}

The last line is a ``result`` record.  Rewards are terminal only; shape them
yourself from ``events`` (prizes taken, knockouts) if you want denser signals.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import describe


def strip_obs(obs: dict[str, Any], keep_search_blob: bool = False) -> dict[str, Any]:
    out = {"select": obs.get("select"), "current": obs.get("current"), "logs": obs.get("logs", [])}
    if keep_search_blob:
        out["search_begin_input"] = obs.get("search_begin_input")
    return out


class RLLogger:
    def __init__(self, log_dir: str | Path | None, game_id: str, names: list[str], agents: list[str],
                 decks: list[list[int]], keep_search_blob: bool = False):
        self.dir = Path(log_dir) if log_dir else None
        self.game_id = game_id
        self.names = names
        self.agents = agents
        self.keep_blob = keep_search_blob
        self.records: list[dict[str, Any]] = []
        self.pending: dict[int, dict[str, Any]] = {}  # player -> record awaiting its result events
        self.path: Path | None = None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.path = self.dir / f"{stamp}-{game_id}.jsonl"
            n = 1
            while self.path.exists():
                n += 1
                self.path = self.dir / f"{stamp}-{game_id}-{n}.jsonl"
        self.meta = {"type": "meta", "game_id": game_id, "players": names, "agents": agents, "decks": decks,
                     "started": time.strftime("%Y-%m-%d %H:%M:%S")}

    def observe(self, obs: dict[str, Any]) -> None:
        """Called with every observation before the actor decides; closes the actor's previous record."""
        p = obs["current"]["yourIndex"]
        prev = self.pending.pop(p, None)
        if prev is not None:
            self._attach_events(prev, obs.get("logs", []))

    def act(self, step: int, obs: dict[str, Any], action: list[int]) -> None:
        cur = obs["current"]
        p = cur["yourIndex"]
        labels = describe.option_labels(obs)
        rec = {
            "type": "step", "game_id": self.game_id, "step": step, "turn": cur["turn"], "player": p,
            "agent": self.agents[p], "obs": strip_obs(obs, self.keep_blob),
            "legal_actions": (obs.get("select") or {}).get("option", []),
            "action": list(action),
            "action_labels": [labels[i] if 0 <= i < len(labels) else str(i) for i in action],
            "events": [], "events_text": [], "reward": 0.0, "done": False,
        }
        self.records.append(rec)
        self.pending[p] = rec

    def finish(self, final_obs: dict[str, Any], result: int, reason_text: str) -> Path | None:
        """Attach the final events, assign terminal rewards and write the file."""
        final_logs = final_obs.get("logs", [])
        for p, rec in list(self.pending.items()):
            self._attach_events(rec, final_logs, redact_for=p if p != final_obs["current"]["yourIndex"] else None)
        self.pending.clear()
        last: dict[int, dict[str, Any]] = {}
        for rec in self.records:
            last[rec["player"]] = rec
        for p, rec in last.items():
            rec["done"] = True
            rec["reward"] = 0.0 if result == 2 else (1.0 if result == p else -1.0)
        self.meta["result"] = result
        self.meta["reason"] = reason_text
        tail = {"type": "result", "game_id": self.game_id, "result": result, "reason": reason_text,
                "winner": None if result == 2 else self.names[result], "steps": len(self.records)}
        if self.path:
            with self.path.open("w", encoding="utf-8") as fh:
                fh.write(json.dumps(self.meta) + "\n")
                for rec in self.records:
                    fh.write(json.dumps(rec) + "\n")
                fh.write(json.dumps(tail) + "\n")
        return self.path

    @staticmethod
    def _attach_events(rec: dict[str, Any], logs: list[dict[str, Any]], redact_for: int | None = None) -> None:
        events = []
        for ev in logs:
            if redact_for is not None and ev.get("type") == 4 and ev.get("playerIndex") != redact_for:
                events.append({"type": 5, "playerIndex": ev.get("playerIndex")})
            else:
                events.append(ev)
        rec["events"] = events
        rec["events_text"] = [describe.log_line(ev, None) for ev in events]
