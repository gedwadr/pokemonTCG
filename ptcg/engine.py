"""Thin, battle-scoped wrapper around the competition engine (libcg).

The sample submission's ``cg.game`` module keeps a single global battle pointer,
which makes it unusable for a server that hosts several matches at once.  This
module talks to the shared library directly and keeps one pointer per Battle.

All calls into the library are serialised through a process-wide lock: the
engine's thread-safety is undocumented and the calls are cheap (milliseconds).
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
from typing import Any

ENGINE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "engine"))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

from cg.sim import lib  # noqa: E402  (loads the shared library and calls GameInitialize)

DECK_SIZE = 60
_LOCK = threading.RLock()

START_ERRORS = {
    1: "deck contains an invalid card ID",
    2: "deck has more than 4 copies of a card (basic Energy excepted)",
    3: "deck has no Basic Pokémon",
    4: "deck has more than one ACE SPEC card",
}

RESULT_REASONS = {
    1: "took all Prize cards",
    2: "opponent started a turn with no cards in deck",
    3: "opponent had no Pokémon in the Active Spot",
    4: "card effect",
}


class DeckError(ValueError):
    def __init__(self, player: int, code: int):
        self.player = player
        self.code = code
        super().__init__(f"player {player}: {START_ERRORS.get(code, f'engine error {code}')}")


class SelectError(ValueError):
    pass


def validate_select(obs: dict[str, Any], select: Any) -> str | None:
    """Return an error message if ``select`` is not a legal answer to ``obs``."""
    sel = obs.get("select")
    if sel is None:
        return "no selection is pending"
    if not isinstance(select, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in select):
        return "select must be a list of integers"
    n = len(sel["option"])
    if not (sel["minCount"] <= len(select) <= sel["maxCount"]):
        return f"select must contain between {sel['minCount']} and {sel['maxCount']} indices (got {len(select)})"
    if any(i < 0 or i >= n for i in select):
        return f"select indices must be in [0, {n})"
    if len(set(select)) != len(select):
        return "select contains duplicate indices"
    return None


class Battle:
    """One match between two 60-card decks.

    ``obs`` always holds the observation for the player who must act next
    (``obs['current']['yourIndex']``), in exactly the dict shape the Kaggle
    environment hands to ``agent(obs_dict)``.
    """

    def __init__(self, deck0: list[int], deck1: list[int], reverse_player: bool = False):
        for i, d in enumerate((deck0, deck1)):
            if len(d) != DECK_SIZE:
                raise ValueError(f"player {i}: deck must contain {DECK_SIZE} cards (got {len(d)})")
        cards = list(deck0) + list(deck1)
        arg = (ctypes.c_int * len(cards))(*cards)
        with _LOCK:
            sd = lib.BattleStartReverse(arg) if reverse_player else lib.BattleStart(arg)
            if not sd.battlePtr:
                raise DeckError(sd.errorPlayer, sd.errorType)
            self._ptr = sd.battlePtr
            self.step_count = 0
            self.history: list[list[int]] = []
            self.obs: dict[str, Any] = self._fetch()

    # -- raw access -----------------------------------------------------
    def _fetch(self) -> dict[str, Any]:
        sd = lib.GetBattleData(self._ptr)
        obs = json.loads(sd.json.decode())
        obs["search_begin_input"] = ctypes.string_at(sd.data, sd.count).decode("ascii")
        return obs

    # -- properties -----------------------------------------------------
    @property
    def current_player(self) -> int:
        return self.obs["current"]["yourIndex"]

    @property
    def finished(self) -> bool:
        return self.obs["current"]["result"] != -1

    @property
    def result(self) -> int:
        """0/1 = winning player index, 2 = draw, -1 = still running."""
        return self.obs["current"]["result"]

    @property
    def result_reason(self) -> int | None:
        for log in reversed(self.obs.get("logs", [])):
            if log.get("type") == 23:
                return log.get("reason")
        return None

    @property
    def turn(self) -> int:
        return self.obs["current"]["turn"]

    # -- actions --------------------------------------------------------
    def select(self, select: list[int]) -> dict[str, Any]:
        if self._ptr is None:
            raise SelectError("battle is closed")
        if self.finished:
            raise SelectError("battle has finished")
        err = validate_select(self.obs, select)
        if err:
            raise SelectError(err)
        arg = (ctypes.c_int * len(select))(*select)
        with _LOCK:
            code = lib.Select(self._ptr, arg, len(select))
            if code != 0:
                raise SelectError(f"engine rejected selection (code {code})")
            self.obs = self._fetch()
        self.step_count += 1
        self.history.append(list(select))
        return self.obs

    def visualize(self) -> str:
        """JSON string consumed by the competition visualizer."""
        with _LOCK:
            return lib.VisualizeData(self._ptr).decode()

    def close(self) -> None:
        with _LOCK:
            if self._ptr is not None:
                lib.BattleFinish(self._ptr)
                self._ptr = None

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass

    # -- views ----------------------------------------------------------
    def public_view(self) -> dict[str, Any]:
        """A redacted copy of ``obs`` for the player who is *not* acting and for spectators.

        Hands are hidden, the pending selection is removed, draws by the acting
        player are rewritten as face-down draws, and the engine's search blob is
        dropped.
        """
        obs = self.obs
        cur = json.loads(json.dumps(obs["current"]))
        acting = cur["yourIndex"]
        for p in cur["players"]:
            p["hand"] = None
        cur["looking"] = None
        logs = []
        for log in obs.get("logs", []):
            if log.get("type") == 4 and log.get("playerIndex") == acting:  # DRAW -> DRAW_REVERSE
                logs.append({"type": 5, "playerIndex": acting})
            else:
                logs.append(log)
        return {"current": cur, "logs": logs, "actingPlayer": acting, "pendingContext": (obs["select"] or {}).get("context")}
