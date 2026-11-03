#!/usr/bin/env python3
"""Connect a Kaggle-style agent (``main.py`` with ``agent(obs_dict)``) or a built-in bot to the server.

    python clients/agent_client.py --agent agents/sample/main.py --room r1 --name alice
    python clients/agent_client.py --agent greedy --room r1 --name bob
    python clients/agent_client.py --agent greedy --room solo --bot random      # play against a server-side bot

The agent is called exactly as on Kaggle: once with ``select=None`` to obtain
the deck, then once per selection with the observation dict.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ptcg import bots  # noqa: E402
from ptcg.deck import load_deck  # noqa: E402


async def play(url: str, room: str, name: str, agent: bots.Agent, deck: list[int], bot: str | None, quiet: bool) -> int | None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, max_msg_size=16 * 1024 * 1024) as ws:
            join = {"type": "join", "room": room, "name": name, "deck": deck}
            if bot:
                join["bot"] = bot
            await ws.send_json(join)
            my_index = None
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = json.loads(msg.data)
                t = data.get("type")
                if t == "joined":
                    my_index = data["playerIndex"]
                    print(f"[{name}] joined room {data['room']} as player {my_index}")
                elif t == "waiting":
                    print(f"[{name}] waiting for an opponent...")
                elif t == "start":
                    print(f"[{name}] game start: {data['players']}")
                elif t == "observe":
                    select = await asyncio.to_thread(agent, data["obs"])
                    if not quiet:
                        labels = data.get("labels", [])
                        chosen = ", ".join(labels[i] if 0 <= i < len(labels) else str(i) for i in select)
                        print(f"[{name}] step {data['step']} | {data['title']} -> {chosen or '(none)'}")
                    await ws.send_json({"type": "select", "select": [int(i) for i in select]})
                elif t == "state":
                    pass
                elif t == "error":
                    print(f"[{name}] error ({data.get('code')}): {data.get('message')}")
                    if data.get("code") in ("join", "deck"):
                        return None
                elif t == "result":
                    who = data["winner"] or "draw"
                    print(f"[{name}] result: {who} ({data['reasonText']}) after {data['steps']} steps, turn {data['turn']}")
                    return data["result"]
                elif t in ("opponent_disconnected",):
                    print(f"[{name}] opponent disconnected; waiting {data.get('grace')}s for reconnect")
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="ws://127.0.0.1:8765/ws")
    ap.add_argument("--room", default="default")
    ap.add_argument("--name", default=None)
    ap.add_argument("--agent", default="greedy", help="built-in bot name or path to a Kaggle-style main.py")
    ap.add_argument("--deck", default=None, help="deck file (required for built-in bots; overrides a submission's deck)")
    ap.add_argument("--bot", default=None, help="ask the server to seat a built-in bot as the opponent")
    ap.add_argument("--games", type=int, default=1, help="play this many games in a row (rooms get a numeric suffix)")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    agent, deck = bots.load_agent(args.agent)
    if args.deck:
        deck = load_deck(args.deck)
    if deck is None:
        deck = load_deck(Path(__file__).resolve().parent.parent / "decks" / "sample.csv")
    bots.apply_deck(agent, deck)
    name = args.name or (args.agent if args.agent in bots.BUILTIN else Path(args.agent).parent.name or "agent")

    wins = 0
    for g in range(args.games):
        room = args.room if args.games == 1 else f"{args.room}-{g}"
        res = asyncio.run(play(args.server, room, name, agent, deck, args.bot, args.quiet))
        if res is None:
            break
    if args.games > 1:
        print(f"[{name}] finished {args.games} games")


if __name__ == "__main__":
    main()
