# Play-server protocol

Transport: one WebSocket per client at `ws://HOST:PORT/ws`. Every frame is a JSON object with a `type` field.

## Client → server

| type | fields | notes |
|------|--------|-------|
| `join` | `room` (str), `name` (str), `deck` (list of 60 card IDs **or** a file name from `decks/`), `bot` (optional: `random` / `greedy`) | Take a seat. If `bot` is given and the room is empty, the server seats a built-in bot opposite you. Rejoining a running game with the same `name` reconnects you. |
| `spectate` | `room` | Watch a room; you receive `state` frames only. |
| `select` | `select` (list of option indices) | Answer the pending `observe`. Must satisfy `minCount <= len <= maxCount`, no duplicates, indices `< len(option)`. |
| `rooms` | | Ask for the room list. |
| `ping` | | Answered with `pong`. |
| `leave` | | Close the connection (forfeits a running game after the reconnect grace). |

## Server → client

| type | fields | notes |
|------|--------|-------|
| `welcome` | `version`, `rooms`, `bots` | Sent on connect. |
| `joined` | `room`, `playerIndex`, `name`, `reconnected?` | Your seat. |
| `waiting` | `room` | Waiting for a second player. |
| `start` | `room`, `players` (names by index), `yourIndex` | Game is about to begin. `yourIndex` is `null` for spectators. |
| `observe` | `room`, `step`, `obs`, `title`, `labels` | **Your move.** `obs` is exactly the dict a Kaggle `agent(obs_dict)` receives (`select`, `logs`, `current`, `search_begin_input`). `labels[i]` is a human-readable label for `obs.select.option[i]`; `title` describes the selection. |
| `state` | `room`, `step`, `view` | Board update for the non-acting player and spectators. `view.current` is the engine state with both hands hidden; `view.logs` are the acting player's logs with their draws made face-down; `view.actingPlayer`, `view.pendingContext`. |
| `error` | `code`, `message`, `player?` | `select` (illegal answer; the `observe` is resent), `protocol`, `join` (room full / playing), `deck` (engine rejected a deck; room closed), `timeout` (you forfeited), `server`. |
| `opponent_disconnected` | `player`, `grace` | The opponent dropped; they may reconnect within `grace` seconds. |
| `result` | `room`, `result` (0/1 winner index, 2 draw), `reason`, `reasonText`, `winner` (name or null), `players`, `steps`, `turn`, `replay` (file path or null) | Game over; the room is removed. |
| `rooms` / `pong` / `spectating` | | Replies to the matching requests. |

The player who must act is always `obs.current.yourIndex`; the engine may ask either player to act during the other's turn (choosing a new Active after a knockout, for instance).

## HTTP

- `GET /` browser client, `GET /static/...` its assets
- `GET /api/cards` card and attack database as JSON (`cards`, `attacks`, enum name lists)
- `GET /api/rooms` current rooms
- `GET /api/decks`, `GET /api/decks/{file}` deck files in `decks/`
