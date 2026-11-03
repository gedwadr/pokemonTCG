#!/usr/bin/env python3
"""Interactive terminal client for a human player.

    python clients/cli_client.py --room r1 --name greg --deck decks/sample.csv
    python clients/cli_client.py --room solo --bot greedy        # practice against a bot

At each selection the board, recent log and the numbered options are printed.
Type the option numbers separated by spaces (or nothing when 0 picks are allowed).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ptcg import describe  # noqa: E402
from ptcg.deck import load_deck  # noqa: E402


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return "quit"


async def play(url: str, room: str, name: str, deck: list[int], bot: str | None) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, max_msg_size=16 * 1024 * 1024) as ws:
            join = {"type": "join", "room": room, "name": name, "deck": deck}
            if bot:
                join["bot"] = bot
            await ws.send_json(join)
            me = None
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    break
                data = json.loads(msg.data)
                t = data.get("type")
                if t == "joined":
                    me = data["playerIndex"]
                    print(f"joined {data['room']} as player {me}")
                elif t == "waiting":
                    print("waiting for an opponent...")
                elif t == "start":
                    print(f"game start: {data['players']}  (you are player {data['yourIndex']})")
                elif t == "state":
                    v = data["view"]
                    for l in v["logs"]:
                        print("  " + describe.log_line(l, me))
                    print(f"(opponent is acting: {describe.SELECT_CONTEXT.get(v.get('pendingContext'), '')})")
                elif t == "observe":
                    obs = data["obs"]
                    print()
                    for l in obs["logs"]:
                        print("  " + describe.log_line(l, me))
                    print(describe.board_summary(obs, me))
                    sel = obs["select"]
                    print(f"\n>>> {data['title']}")
                    for i, label in enumerate(data["labels"]):
                        print(f"  [{i}] {label}")
                    while True:
                        raw = await asyncio.to_thread(ask, f"pick {sel['minCount']}-{sel['maxCount']} > ")
                        if raw.strip() in ("q", "quit"):
                            await ws.send_json({"type": "leave"})
                            return
                        try:
                            select = [int(x) for x in raw.replace(",", " ").split()]
                        except ValueError:
                            print("numbers only")
                            continue
                        if sel["minCount"] <= len(select) <= sel["maxCount"] and all(0 <= i < len(sel["option"]) for i in select) and len(set(select)) == len(select):
                            break
                        print("invalid selection")
                    await ws.send_json({"type": "select", "select": select})
                elif t == "error":
                    print(f"error ({data.get('code')}): {data.get('message')}")
                    if data.get("code") in ("join", "deck"):
                        return
                elif t == "result":
                    who = data["winner"] or "draw"
                    print(f"\n=== {who} ({data['reasonText']}) after {data['steps']} steps ===")
                    return
                elif t == "opponent_disconnected":
                    print(f"opponent disconnected; they have {data.get('grace')}s to reconnect")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="ws://127.0.0.1:8765/ws")
    ap.add_argument("--room", default="default")
    ap.add_argument("--name", default="human")
    ap.add_argument("--deck", default=str(Path(__file__).resolve().parent.parent / "decks" / "sample.csv"))
    ap.add_argument("--bot", default=None, help="ask the server to seat a built-in bot as the opponent")
    args = ap.parse_args()
    asyncio.run(play(args.server, args.room, args.name, load_deck(args.deck), args.bot))


if __name__ == "__main__":
    main()
