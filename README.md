# pokemonTCG — local play server for the PTCG AI Battle Challenge engine

A code base around the battle engine shipped with the Kaggle competition
[The Pokémon Company PTCG AI Battle Challenge (playground)](https://www.kaggle.com/competitions/the-pokemon-company-ptcg-ai-battle-challenge-playground).
It lets two players (humans, Kaggle-style agents, or built-in bots) connect to a
server and play a full match refereed by the official engine, and it gives you
tooling to evaluate agents locally.

> **License.** The engine (`engine/`), card data (`data/`) and card texts are
> provided by The Pokémon Company for competition use only. They may not be
> redistributed or published, and must be deleted when the competition ends
> (see `engine/LICENSE-ENGINE.txt`). Keep this repository private.

## Layout

```
engine/            the competition engine: prebuilt libcg (Linux x86-64 / arm64, macOS, Windows),
                   the `cg` Python wrapper from the sample submission, and the C++ source (ptcgProgram/)
ptcg/              this project's package
  engine.py        Battle: one match per object, thread-safe, Kaggle-identical observation dicts
  cards.py         card / attack database read from the engine (AllCard / AllAttack)
  deck.py          deck file loading and legality checks
  describe.py      human-readable labels for selections, options, logs and the board
  bots.py          built-in agents (random, greedy), a loader for Kaggle-style main.py agents,
                   and the bot registry (every agents/<name>/ folder is selectable as an opponent)
  matchlog.py      readable per-game move logs + results.csv
  rllog.py         per-decision JSON Lines logs for RL training
  images.py        official card image URLs (pokemon.com CDN), cached in data/card_images.json
  server.py        the WebSocket + HTTP play server          →  python -m ptcg.server
clients/
  web/index.html   browser client (served at http://localhost:8765/)
  cli_client.py    terminal client for a human
  agent_client.py  connects a Kaggle-style agent or built-in bot to a room
agents/sample/     the competition's sample submission (main.py + deck.csv), unchanged
agents/rule_based_1/  Dhelmise / Hide 'n' Sneak rule-based agent extracted from the
                   rule-based-agent-for-dhelmise notebook
agents/dragapult_1/   rule-based agent for the Dragapult ex deck (decks/dragapult_ex.csv); env vars
                   DRAGAPULT_GO_FIRST=0/1 and DRAGAPULT_RUINS=0/1 switch its opening choices
agents/dhelmise_1/    my own agent for the Dhelmise / Hide 'n' Sneak deck (DHELMISE_GO_FIRST=0/1).
                   Drop more submissions beside these, one folder each
scripts/local_match.py   agents vs agents in-process, win-rate report
decks/             predetermined decks offered in the UI (sample.csv, rule_based_1.csv, add your own)
data/              card lists (CSV, PDF) from the competition
tests/             pytest suite (engine wrapper + server protocol)
PROTOCOL.md        the WebSocket message protocol
```

## Setup

Python 3.11+ and `aiohttp` (tests also need `pytest` and `pytest-asyncio`):

```
pip install -r requirements.txt kaggle
export KAGGLE_API_TOKEN=...        # or ~/.kaggle/kaggle.json; you must have joined the competition
python scripts/fetch_engine.py     # downloads engine/ and data/ from Kaggle (not committed, see below)
python -m pytest -q
```

The engine's shared library is loaded from `engine/cg/` automatically; nothing to compile.
`engine/` and `data/` are gitignored because the competition license forbids redistributing
them; every clone must fetch its own copy with the script above. Keep this repository private.

## Run the server

```
python -m ptcg.server                      # http://0.0.0.0:8765  ws://…/ws
python -m ptcg.server --move-timeout 60    # forfeit a player who takes > 60 s per selection
python -m ptcg.server --port 9000 --replays my_replays
```

Every game is logged twice:

- `logs/<timestamp>-<room>.log` is a readable move log: each engine event
  (draws, plays, attacks, HP changes, knockouts) and each selection with the
  acting player, turn, the question asked and the option chosen. A board
  summary is printed at the start of every turn.
- `logs/results.csv` gets one row per game: players, winner, reason, number of
  selections, final turn, and the paths of the log and replay files.
- `replays/<timestamp>-<room>.json` holds both decks, the raw selection
  history and the engine's visualizer data.

`scripts/local_match.py` writes the same logs (`--logs DIR`, `--tag NAME`).
Pass `--logs ''` to either to disable; `python -m ptcg.server -v` also echoes
every move to the console.

### Training data (`rl_logs/`)

The server also writes one JSON Lines file per game to `rl_logs/<timestamp>-<room>.jsonl`
(`--rl DIR`, `--rl ''` to disable), designed for reinforcement learning:

- line 1: `{"type":"meta", players, agents (e.g. "human", "bot:rule_based_1"), decks, result}`
- one line per decision, for both players:
  `{"type":"step", step, turn, player, agent, obs, legal_actions, action, action_labels, events, events_text, reward, done}`
  - `obs` is the observation the actor saw (`select`, `current`, `logs`); the search blob is dropped
  - `legal_actions` is `obs.select.option`; `action` is the list of chosen indices
  - `events` are the engine log entries caused by that action (Pokémon evolved, card
    discarded, energy attached, damage, knockouts, prizes), i.e. what the actor saw before its next decision;
    `events_text` is the same in words
  - `reward` is terminal: +1 / -1 / 0 on each player's last decision, 0 elsewhere; `done` marks it
- last line: `{"type":"result", result, reason, winner, steps}`

Filter on `"agent": "human"` for your own trajectories, or keep both sides for
self-play data. Dense rewards (prizes taken, HP dealt) can be derived from `events`.

## Play

**You vs a bot in the browser**: open `http://<server>:8765/`, pick your deck
from the predetermined list (every `.csv`/`.txt` in `decks/`; drop more files
there to extend the list), pick the opponent (`random`, `greedy`, or any
submission folder under `agents/`, e.g. `rule_based_1`), click Play. You are
shown exactly the option list the engine gives the bot; click one option when a
single pick is required, otherwise toggle and Confirm. Every card in the deck preview, your hand, both boards and the option list
is shown with its official card image (hover to enlarge); `/gallery` shows
all registered decks as image grids. Hover a card or option for its text. No animations.

**Human vs bot in a terminal**:

```
python clients/cli_client.py --room solo --bot greedy --deck decks/sample.csv
```

**Agent vs agent over the network** (two terminals, same room):

```
python clients/agent_client.py --agent agents/sample/main.py --room r1 --name sample
python clients/agent_client.py --agent greedy --room r1 --name greedy --deck decks/sample.csv
```

`--agent` takes a built-in bot name (`random`, `greedy`) or the path to a
Kaggle-style `main.py` exposing `agent(obs_dict) -> list[int]`. A submission is
driven exactly as on Kaggle: first call with `select=None` must return its
60-card deck, every later call receives the observation dict and returns option
indices. Your competition `main.py` + `deck.csv` work without changes.

**Spectate**: `Spectate room` in the browser, or `{"type":"spectate","room":"r1"}` over the socket.

## Evaluate agents without the server

```
python scripts/local_match.py --a agents/sample/main.py --b greedy --games 100
```

Players alternate going first; the report shows wins, draws and result reasons.

## Writing your own client

See `PROTOCOL.md`. In short: send `join`, wait for `observe` frames, answer each
with `select`. The `obs` inside `observe` is byte-for-byte what the Kaggle
environment passes to `agent()`, so the same code runs in both places.

## Deck files

One card ID per line (the competition's `deck.csv` format), or `id,count` /
`id x count` lines; `#` starts a comment. Card IDs are in `data/Card_ID_List_EN.pdf`
and `data/EN_Card_Data_R2_full.csv`, or query the server: `GET /api/cards`.
Legality (60 cards, max 4 per name except basic Energy, 1 ACE SPEC, at least one
Basic Pokémon) is checked both by `ptcg.deck.validate_deck` and by the engine.
