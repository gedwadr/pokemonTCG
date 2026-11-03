"""WebSocket play server: two players connect to a room and the engine referees.

Run:  python -m ptcg.server [--host 0.0.0.0] [--port 8765] [--move-timeout 0] [--replays replays]

HTTP:
  GET  /                      browser client
  GET  /api/cards             card + attack database (JSON)
  GET  /api/images            card id -> official card image URL (JSON)
  GET  /gallery               every deck in decks/ as a grid of card images
  GET  /api/rooms             rooms and their status
  GET  /api/decks             deck files in the decks/ directory
  GET  /api/decks/{name}      one deck as a list of card IDs
  WS   /ws                    game protocol (see PROTOCOL.md)

Every game writes logs/<timestamp>-<room>.log (all events and selections) and
appends a row to logs/results.csv; replays/<timestamp>-<room>.json holds the
raw selection history and visualizer data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from . import __version__, bots, cards, describe, images
from .matchlog import MatchLogger
from .rllog import RLLogger
from .deck import load_deck, validate_deck
from .engine import Battle, DeckError, SelectError, validate_select

log = logging.getLogger("ptcg.server")
ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "clients" / "web"
DECK_DIR = ROOT / "decks"

FORFEIT = object()


class Seat:
    def __init__(self, index: int, name: str, deck: list[int], ws: web.WebSocketResponse | None = None,
                 agent: bots.Agent | None = None, kind: str = "client"):
        self.index = index
        self.kind = kind
        self.name = name
        self.deck = deck
        self.ws = ws
        self.agent = agent
        self.queue: asyncio.Queue = asyncio.Queue()
        self.forfeit_task: asyncio.Task | None = None

    @property
    def is_bot(self) -> bool:
        return self.agent is not None

    @property
    def connected(self) -> bool:
        return self.is_bot or (self.ws is not None and not self.ws.closed)


class Room:
    def __init__(self, server: "Server", name: str):
        self.server = server
        self.name = name
        self.seats: list[Seat | None] = [None, None]
        self.spectators: set[web.WebSocketResponse] = set()
        self.battle: Battle | None = None
        self.status = "waiting"
        self.task: asyncio.Task | None = None
        self.created = time.time()
        self.step = 0

    # -- helpers -----------------------------------------------------------
    def info(self) -> dict[str, Any]:
        return {
            "room": self.name,
            "status": self.status,
            "players": [s.name if s else None for s in self.seats],
            "spectators": len(self.spectators),
            "turn": self.battle.turn if self.battle else 0,
            "step": self.step,
        }

    def seat_of(self, ws: web.WebSocketResponse) -> Seat | None:
        for s in self.seats:
            if s is not None and s.ws is ws:
                return s
        return None

    async def _send(self, ws: web.WebSocketResponse | None, msg: dict[str, Any]) -> None:
        if ws is None or ws.closed:
            return
        try:
            await ws.send_str(json.dumps(msg))
        except Exception as exc:  # pragma: no cover - network errors
            log.warning("send to %s failed: %s", self.name, exc)

    async def broadcast(self, msg: dict[str, Any], exclude: Seat | None = None) -> None:
        for s in self.seats:
            if s is not None and not s.is_bot and s is not exclude:
                await self._send(s.ws, msg)
        for ws in list(self.spectators):
            await self._send(ws, msg)

    # -- seating -----------------------------------------------------------
    def free_index(self) -> int | None:
        for i, s in enumerate(self.seats):
            if s is None:
                return i
        return None

    async def add_player(self, ws: web.WebSocketResponse, name: str, deck: list[int], bot: str | None,
                         kind: str = "client", bot_deck: list[int] | None = None) -> Seat:
        # reconnect into a running game
        if self.status == "playing":
            for s in self.seats:
                if s is not None and not s.is_bot and s.name == name and not s.connected:
                    s.ws = ws
                    if s.forfeit_task:
                        s.forfeit_task.cancel()
                        s.forfeit_task = None
                    await self._send(ws, {"type": "joined", "room": self.name, "playerIndex": s.index, "name": name, "reconnected": True})
                    await self._send(ws, {"type": "start", "room": self.name, "players": [x.name for x in self.seats], "yourIndex": s.index})
                    await self.push_views(only=s)
                    log.info("room %s: %s reconnected as player %d", self.name, name, s.index)
                    return s
            raise web.HTTPConflict(reason="room is already playing")
        if self.status == "finished":
            raise web.HTTPConflict(reason="room has finished")
        idx = self.free_index()
        if idx is None:
            raise web.HTTPConflict(reason="room is full")
        if bot and bot not in bots.available_bots():
            raise web.HTTPBadRequest(reason=f"unknown bot {bot!r}; choose from {', '.join(bots.available_bots())}")
        seat = Seat(idx, name, deck, ws=ws, kind=kind)
        self.seats[idx] = seat
        await self._send(ws, {"type": "joined", "room": self.name, "playerIndex": idx, "name": name})
        if bot:
            other = self.free_index()
            if other is not None:
                agent, agent_deck = await asyncio.to_thread(bots.make_bot, bot, bot_deck if bot in bots.BUILTIN else None)
                if agent_deck is None:
                    agent_deck = self.server.default_deck()
                self.seats[other] = Seat(other, f"{bot}-bot", agent_deck, agent=agent, kind=f"bot:{bot}")
        if self.free_index() is None:
            self.task = asyncio.create_task(self.run())
        else:
            await self._send(ws, {"type": "waiting", "room": self.name})
        return seat

    async def add_spectator(self, ws: web.WebSocketResponse) -> None:
        self.spectators.add(ws)
        await self._send(ws, {"type": "spectating", "room": self.name, **self.info()})
        if self.battle is not None:
            await self._send(ws, {"type": "state", "view": self.battle.public_view(), "step": self.step, "room": self.name})

    async def on_disconnect(self, ws: web.WebSocketResponse) -> None:
        self.spectators.discard(ws)
        seat = self.seat_of(ws)
        if seat is None:
            return
        seat.ws = None
        if self.status == "waiting":
            self.seats[seat.index] = None
            log.info("room %s: %s left while waiting", self.name, seat.name)
            if all(s is None or s.is_bot for s in self.seats):
                self.server.remove_room(self)
            return
        if self.status == "playing":
            grace = self.server.reconnect_grace
            log.info("room %s: %s disconnected; forfeits in %ss unless they reconnect", self.name, seat.name, grace)
            await self.broadcast({"type": "opponent_disconnected", "player": seat.index, "grace": grace}, exclude=seat)

            async def forfeit_later() -> None:
                await asyncio.sleep(grace)
                if not seat.connected:
                    seat.queue.put_nowait(FORFEIT)

            seat.forfeit_task = asyncio.create_task(forfeit_later())

    # -- game loop ---------------------------------------------------------
    async def push_views(self, only: Seat | None = None) -> None:
        b = self.battle
        assert b is not None
        acting = self.seats[b.current_player]
        assert acting is not None
        observe = {
            "type": "observe", "room": self.name, "step": self.step, "obs": b.obs,
            "title": describe.select_title(b.obs), "labels": describe.option_labels(b.obs),
        }
        view = {"type": "state", "room": self.name, "step": self.step, "view": b.public_view()}
        if only is not None:
            await self._send(only.ws, observe if only is acting else view)
            return
        if not acting.is_bot:
            await self._send(acting.ws, observe)
        await self.broadcast(view, exclude=acting)

    async def run(self) -> None:
        names = [s.name for s in self.seats]  # type: ignore[union-attr]
        try:
            self.battle = Battle(self.seats[0].deck, self.seats[1].deck)  # type: ignore[union-attr]
        except (DeckError, ValueError) as exc:
            player = getattr(exc, "player", None)
            log.info("room %s: battle start failed: %s", self.name, exc)
            await self.broadcast({"type": "error", "code": "deck", "message": str(exc), "player": player})
            self.server.remove_room(self)
            return
        self.status = "playing"
        log.info("room %s: start %s vs %s", self.name, *names)
        mlog = MatchLogger(self.server.log_dir, self.name, names, echo=log.debug)
        rl = RLLogger(self.server.rl_dir, self.name, names, [s.kind for s in self.seats],  # type: ignore[union-attr]
                      [s.deck for s in self.seats])  # type: ignore[union-attr]
        for s in self.seats:
            if s and not s.is_bot:
                await self._send(s.ws, {"type": "start", "room": self.name, "players": names, "yourIndex": s.index})
        for ws in list(self.spectators):
            await self._send(ws, {"type": "start", "room": self.name, "players": names, "yourIndex": None})

        b = self.battle
        forfeit_by: int | None = None
        try:
            while not b.finished:
                seat = self.seats[b.current_player]
                assert seat is not None
                rl.observe(b.obs)
                if seat.is_bot:
                    select = await asyncio.to_thread(seat.agent, b.obs)  # type: ignore[arg-type]
                    err = validate_select(b.obs, select)
                    if err:
                        log.warning("room %s: bot %s illegal move (%s); playing randomly", self.name, seat.name, err)
                        select = bots.RandomBot()(b.obs)
                else:
                    await self.push_views()
                    select = await self._await_select(seat)
                    if select is FORFEIT:
                        forfeit_by = seat.index
                        break
                mlog.move(self.step + 1, b.obs, select)
                rl.act(self.step + 1, b.obs, select)
                b.select(select)
                self.step += 1
            mlog.events(b.obs)
        except Exception:  # pragma: no cover
            log.exception("room %s: game loop crashed", self.name)
            mlog.close()
            await self.broadcast({"type": "error", "code": "server", "message": "internal error, room closed"})
            self.server.remove_room(self)
            return

        self.status = "finished"
        if forfeit_by is not None:
            result, reason, reason_text = 1 - forfeit_by, 0, "forfeit (disconnect or timeout)"
        else:
            result = b.result
            reason = b.result_reason
            reason_text = describe.LOG_RESULT_REASON.get(reason, str(reason))
        winner = None if result == 2 else names[result]
        replay = self.server.save_replay(self, names, result, reason_text)
        rl_path = rl.finish(b.obs, result, reason_text)
        mlog.result(result, reason_text, self.step, b.turn, replay, str(rl_path) if rl_path else None)
        msg = {"type": "result", "room": self.name, "result": result, "reason": reason, "reasonText": reason_text,
               "winner": winner, "players": names, "steps": self.step, "turn": b.turn, "replay": replay,
               "log": str(mlog.path) if mlog.path else None, "rl": str(rl_path) if rl_path else None}
        log.info("room %s: result %s (%s) after %d steps", self.name, winner or "draw", reason_text, self.step)
        await self.broadcast(msg)
        self.server.remove_room(self)

    async def _await_select(self, seat: Seat) -> Any:
        timeout = self.server.move_timeout or None
        while True:
            try:
                item = await asyncio.wait_for(seat.queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                log.info("room %s: %s timed out", self.name, seat.name)
                await self._send(seat.ws, {"type": "error", "code": "timeout", "message": "move timeout; you forfeit"})
                return FORFEIT
            if item is FORFEIT:
                return FORFEIT
            err = validate_select(self.battle.obs, item)  # type: ignore[union-attr]
            if err is None:
                return item
            await self._send(seat.ws, {"type": "error", "code": "select", "message": err})
            await self.push_views(only=seat)

    def close(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        if self.battle:
            self.battle.close()
            self.battle = None


class Server:
    def __init__(self, move_timeout: float = 0, reconnect_grace: float = 30, replay_dir: str | None = "replays",
                 default_deck_path: str | None = None, log_dir: str | None = "logs", rl_dir: str | None = "rl_logs"):
        self.rooms: dict[str, Room] = {}
        self.log_dir = log_dir
        self.rl_dir = rl_dir
        self.move_timeout = move_timeout
        self.reconnect_grace = reconnect_grace
        self.replay_dir = Path(replay_dir) if replay_dir else None
        self._default_deck_path = default_deck_path or str(DECK_DIR / "sample.csv")
        self.app = web.Application()
        self.app.add_routes([
            web.get("/", self.index),
            web.get("/api/cards", self.api_cards),
            web.get("/api/images", self.api_images),
            web.get("/gallery", self.gallery),
            web.get("/api/rooms", self.api_rooms),
            web.get("/api/decks", self.api_decks),
            web.get("/api/decks/{name}", self.api_deck),
            web.get("/ws", self.websocket),
        ])
        if WEB_DIR.exists():
            self.app.router.add_static("/static", WEB_DIR)

    # -- misc --------------------------------------------------------------
    def default_deck(self) -> list[int]:
        return load_deck(self._default_deck_path)

    def remove_room(self, room: Room) -> None:
        room.close()
        if self.rooms.get(room.name) is room:
            del self.rooms[room.name]

    def save_replay(self, room: Room, names: list[str], result: int, reason: str) -> str | None:
        if not self.replay_dir or room.battle is None:
            return None
        try:
            self.replay_dir.mkdir(parents=True, exist_ok=True)
            path = self.replay_dir / f"{time.strftime('%Y%m%d-%H%M%S')}-{room.name}.json"
            data = {
                "room": room.name, "players": names, "decks": [s.deck for s in room.seats],  # type: ignore[union-attr]
                "result": result, "reason": reason, "steps": room.step, "history": room.battle.history,
                "visualize": json.loads(room.battle.visualize()),
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            return str(path)
        except Exception as exc:  # pragma: no cover
            log.warning("could not save replay: %s", exc)
            return None

    # -- http --------------------------------------------------------------
    async def index(self, request: web.Request) -> web.StreamResponse:
        page = WEB_DIR / "index.html"
        if not page.exists():
            return web.Response(text="ptcg server running; web client missing", content_type="text/plain")
        return web.FileResponse(page)

    async def api_cards(self, request: web.Request) -> web.Response:
        return web.Response(text=cards.database_json(), content_type="application/json")

    async def api_images(self, request: web.Request) -> web.Response:
        return web.json_response({str(k): v for k, v in images.image_map().items()})

    async def gallery(self, request: web.Request) -> web.Response:
        """Every registered deck as a grid of official card images."""
        from collections import Counter
        from html import escape

        parts = ["<!doctype html><meta charset='utf-8'><title>Deck gallery</title>",
                 "<style>body{font:14px system-ui;margin:16px;background:#f4f5f7;color:#1c1e21}h2{margin:24px 0 8px}"
                 ".g{display:flex;flex-wrap:wrap;gap:10px}.c{width:150px;text-align:center;font-size:12px}"
                 ".c img{width:150px;border-radius:6px;display:block}.c b{display:block;margin-top:2px}</style>",
                 "<h1>Registered decks</h1><p><a href='/'>back to the game</a></p>"]
        names = sorted(p.name for p in DECK_DIR.glob("*") if p.suffix in (".csv", ".txt")) if DECK_DIR.exists() else []
        for name in names:
            deck = load_deck(DECK_DIR / name)
            counts = Counter(deck)
            db = cards.all_cards()
            order = sorted(counts, key=lambda c: (db.get(c, {}).get("cardType", 99), c))
            parts.append(f"<h2>{escape(name)} <small>({len(deck)} cards)</small></h2><div class='g'>")
            for cid in order:
                url = images.image_url(cid)
                label = f"{counts[cid]} x {escape(cards.card_name(cid))} (#{cid})"
                img = f"<img src='{escape(url)}' loading='lazy' alt=''>" if url else "<div style='height:210px;background:#ddd'></div>"
                parts.append(f"<div class='c'>{img}<b>{label}</b></div>")
            parts.append("</div>")
        return web.Response(text="".join(parts), content_type="text/html")

    async def api_rooms(self, request: web.Request) -> web.Response:
        return web.json_response([r.info() for r in self.rooms.values()])

    async def api_decks(self, request: web.Request) -> web.Response:
        names = sorted(p.name for p in DECK_DIR.glob("*") if p.suffix in (".csv", ".txt")) if DECK_DIR.exists() else []
        return web.json_response(names)

    async def api_deck(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        path = DECK_DIR / name
        if "/" in name or not path.exists():
            raise web.HTTPNotFound()
        deck = load_deck(path)
        return web.json_response({"name": name, "deck": deck, "problems": validate_deck(deck)})

    # -- websocket ---------------------------------------------------------
    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=4 * 1024 * 1024)
        await ws.prepare(request)
        room: Room | None = None
        await ws.send_str(json.dumps({"type": "welcome", "version": __version__, "rooms": [r.info() for r in self.rooms.values()],
                                      "bots": bots.available_bots()}))
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    if msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                        break
                    continue
                try:
                    data = json.loads(msg.data)
                    if not isinstance(data, dict):
                        raise ValueError
                except ValueError:
                    await ws.send_str(json.dumps({"type": "error", "code": "protocol", "message": "messages must be JSON objects"}))
                    continue
                kind = data.get("type")
                try:
                    if kind == "ping":
                        await ws.send_str(json.dumps({"type": "pong"}))
                    elif kind == "rooms":
                        await ws.send_str(json.dumps({"type": "rooms", "rooms": [r.info() for r in self.rooms.values()]}))
                    elif kind == "join":
                        if room is not None:
                            raise ValueError("already in a room")
                        room = await self._join(ws, data)
                    elif kind == "spectate":
                        if room is not None:
                            raise ValueError("already in a room")
                        name = str(data.get("room") or "default")
                        room = self.rooms.get(name)
                        if room is None:
                            raise ValueError(f"no such room {name!r}")
                        await room.add_spectator(ws)
                    elif kind == "select":
                        if room is None:
                            raise ValueError("join a room first")
                        seat = room.seat_of(ws)
                        if seat is None:
                            raise ValueError("spectators cannot select")
                        if room.battle is None or room.battle.current_player != seat.index:
                            raise ValueError("it is not your turn")
                        seat.queue.put_nowait(data.get("select"))
                    elif kind == "leave":
                        break
                    else:
                        raise ValueError(f"unknown message type {kind!r}")
                except web.HTTPException as exc:
                    await ws.send_str(json.dumps({"type": "error", "code": "join", "message": exc.reason}))
                except ValueError as exc:
                    await ws.send_str(json.dumps({"type": "error", "code": "protocol", "message": str(exc)}))
        finally:
            if room is not None:
                await room.on_disconnect(ws)
            if not ws.closed:
                await ws.close()
        return ws

    async def _join(self, ws: web.WebSocketResponse, data: dict[str, Any]) -> Room:
        name = str(data.get("name") or "anon")[:40]
        room_name = str(data.get("room") or "default")[:40]
        deck = data.get("deck")
        if isinstance(deck, str):
            path = DECK_DIR / deck
            if "/" in deck or not path.exists():
                raise ValueError(f"unknown deck file {deck!r}")
            deck = load_deck(path)
        if deck is None:
            deck = self.default_deck()
        if not isinstance(deck, list) or not all(isinstance(c, int) for c in deck):
            raise ValueError("deck must be a list of 60 card IDs or a deck file name")
        problems = validate_deck(deck)
        if problems:
            raise ValueError("illegal deck: " + "; ".join(problems))
        room = self.rooms.get(room_name)
        if room is None:
            room = Room(self, room_name)
            self.rooms[room_name] = room
        bot_deck = data.get("botDeck")
        if isinstance(bot_deck, str) and bot_deck:
            path = DECK_DIR / bot_deck
            if "/" in bot_deck or not path.exists():
                raise ValueError(f"unknown deck file {bot_deck!r}")
            bot_deck = load_deck(path)
        elif not isinstance(bot_deck, list):
            bot_deck = None
        kind = str(data.get("kind") or "client")[:20]
        await room.add_player(ws, name, deck, data.get("bot"), kind=kind, bot_deck=bot_deck)
        return room

    # -- lifecycle ---------------------------------------------------------
    def run(self, host: str = "0.0.0.0", port: int = 8765) -> None:
        log.info("ptcg server %s listening on http://%s:%d  (ws://%s:%d/ws)", __version__, host, port, host, port)
        web.run_app(self.app, host=host, port=port, print=None)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Pokémon TCG play server")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--move-timeout", type=float, default=0, help="seconds a player may take per selection (0 = unlimited)")
    ap.add_argument("--reconnect-grace", type=float, default=30, help="seconds a disconnected player has to reconnect")
    ap.add_argument("--replays", default="replays", help="directory for replay files ('' to disable)")
    ap.add_argument("--logs", default="logs", help="directory for move logs and results.csv ('' to disable)")
    ap.add_argument("--rl", default="rl_logs", help="directory for per-decision JSONL logs for RL training ('' to disable)")
    ap.add_argument("--default-deck", default=None, help="deck file used for bots and for players who send no deck")
    ap.add_argument("-v", "--verbose", action="store_true", help="also print every move to the console")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Server(move_timeout=args.move_timeout, reconnect_grace=args.reconnect_grace, replay_dir=args.replays or None,
           default_deck_path=args.default_deck, log_dir=args.logs or None, rl_dir=args.rl or None).run(args.host, args.port)


if __name__ == "__main__":
    main()
