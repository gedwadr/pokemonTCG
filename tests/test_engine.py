import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from ptcg import bots, describe  # noqa: E402
from ptcg.deck import load_deck, validate_deck  # noqa: E402
from ptcg.engine import Battle, DeckError, SelectError  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "decks" / "sample.csv"


@pytest.fixture(scope="module")
def deck():
    d = load_deck(SAMPLE)
    assert len(d) == 60
    assert validate_deck(d) == []
    return d


def test_random_game_finishes(deck):
    b = Battle(deck, deck)
    bot = bots.RandomBot(seed=0)
    steps = 0
    while not b.finished and steps < 20000:
        b.select(bot(b.obs))
        steps += 1
    assert b.finished
    assert b.result in (0, 1, 2)
    assert b.visualize().startswith("[")
    b.close()


def test_greedy_beats_random_mostly(deck):
    wins = 0
    for g in range(6):
        b = Battle(deck, deck)
        agents = [bots.GreedyBot(seed=g), bots.RandomBot(seed=g)]
        while not b.finished:
            b.select(agents[b.current_player](b.obs))
        wins += b.result == 0
        b.close()
    assert wins >= 3


def test_select_validation(deck):
    b = Battle(deck, deck)
    with pytest.raises(SelectError):
        b.select([99])
    with pytest.raises(SelectError):
        b.select([0, 0])
    with pytest.raises(SelectError):
        b.select("0")  # type: ignore[arg-type]
    b.close()


def test_bad_deck_rejected(deck):
    with pytest.raises(DeckError) as ei:
        Battle(deck, deck[:59] + [999999])
    assert ei.value.player == 1
    with pytest.raises(ValueError):
        Battle(deck, deck[:10])


def test_public_view_hides_hands(deck):
    b = Battle(deck, deck)
    v = b.public_view()
    assert all(p["hand"] is None for p in v["current"]["players"])
    assert "search_begin_input" not in v
    assert "select" not in v
    b.close()


def test_labels_and_summary(deck):
    b = Battle(deck, deck)
    labels = describe.option_labels(b.obs)
    assert labels == ["Yes", "No"]
    assert "Go first?" in describe.select_title(b.obs)
    assert "Turn 0" in describe.board_summary(b.obs)
    b.close()


def test_two_battles_are_independent(deck):
    a, b = Battle(deck, deck), Battle(deck, deck)
    a.select([0])
    assert a.step_count == 1 and b.step_count == 0
    a.close()
    b.close()
