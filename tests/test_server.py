import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp  # noqa: E402
import pytest  # noqa: E402
from aiohttp import web  # noqa: E402

from ptcg import bots  # noqa: E402
from ptcg.deck import load_deck  # noqa: E402
from ptcg.server import Server  # noqa: E402

SAMPLE = Path(__file__).resolve().parent.parent / "decks" / "sample.csv"


async def _run_client(session, url, name, room, deck, agent, bot=None):
    seen = []
    async with session.ws_connect(url) as ws:
        join = {"type": "join", "room": room, "name": name, "deck": deck}
        if bot:
            join["bot"] = bot
        await ws.send_json(join)
        async for msg in ws:
            data = json.loads(msg.data)
            seen.append(data["type"])
            if data["type"] == "observe":
                await ws.send_json({"type": "select", "select": agent(data["obs"])})
            elif data["type"] == "result":
                return data, seen
            elif data["type"] == "error" and data["code"] in ("join", "deck"):
                return data, seen
    return None, seen


@pytest.fixture
async def server(aiohttp_server, tmp_path):
    srv = Server(move_timeout=0, reconnect_grace=1, replay_dir=None, log_dir=str(tmp_path / "logs"), rl_dir=str(tmp_path / "rl"))
    running = await aiohttp_server(srv.app)
    running.tmp = tmp_path
    return running


async def test_two_clients_play_a_game(server):
    deck = load_deck(SAMPLE)
    url = f"ws://{server.host}:{server.port}/ws"
    async with aiohttp.ClientSession() as session:
        r1, r2 = await asyncio.gather(
            _run_client(session, url, "alice", "t1", deck, bots.GreedyBot(seed=1)),
            _run_client(session, url, "bob", "t1", deck, bots.RandomBot(seed=2)),
        )
    for res, seen in (r1, r2):
        assert res["type"] == "result"
        assert res["result"] in (0, 1, 2)
        assert "joined" in seen and "start" in seen and "observe" in seen
    assert r1[0]["steps"] == r2[0]["steps"]


async def test_play_against_server_bot(server):
    deck = load_deck(SAMPLE)
    url = f"ws://{server.host}:{server.port}/ws"
    async with aiohttp.ClientSession() as session:
        res, seen = await _run_client(session, url, "solo", "t2", deck, bots.RandomBot(seed=3), bot="greedy")
    assert res["type"] == "result"
    assert "greedy-bot" in res["players"]


async def test_illegal_select_is_rejected_then_game_continues(server):
    deck = load_deck(SAMPLE)
    url = f"ws://{server.host}:{server.port}/ws"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.send_json({"type": "join", "room": "t3", "name": "x", "deck": deck, "bot": "random"})
            got_error = False
            rnd = bots.RandomBot(seed=4)
            async for msg in ws:
                data = json.loads(msg.data)
                if data["type"] == "observe":
                    if not got_error:
                        await ws.send_json({"type": "select", "select": [1234]})
                    else:
                        await ws.send_json({"type": "select", "select": rnd(data["obs"])})
                elif data["type"] == "error":
                    assert data["code"] == "select"
                    got_error = True
                elif data["type"] == "result":
                    break
            assert got_error


async def test_bad_deck_rejected_at_join(server):
    url = f"ws://{server.host}:{server.port}/ws"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.send_json({"type": "join", "room": "t4", "name": "x", "deck": [3] * 60})
            while True:
                data = json.loads((await ws.receive()).data)
                if data["type"] == "error":
                    assert "Basic" in data["message"]
                    break


async def test_http_endpoints(server):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://{server.host}:{server.port}/api/cards") as r:
            db = await r.json()
            assert len(db["cards"]) > 1000
        async with session.get(f"http://{server.host}:{server.port}/api/decks/sample.csv") as r:
            d = await r.json()
            assert len(d["deck"]) == 60 and d["problems"] == []
        async with session.get(f"http://{server.host}:{server.port}/api/rooms") as r:
            assert await r.json() == []


async def test_submission_bot_and_rl_log(server, tmp_path):
    deck = load_deck(SAMPLE)
    url = f"ws://{server.host}:{server.port}/ws"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            welcome = json.loads((await ws.receive()).data)
            assert "rule_based_1" in welcome["bots"]
            await ws.send_json({"type": "join", "kind": "human", "room": "t5", "name": "h", "deck": "rule_based_1.csv", "bot": "rule_based_1"})
            rnd = bots.RandomBot(seed=5)
            async for msg in ws:
                data = json.loads(msg.data)
                if data["type"] == "observe":
                    await ws.send_json({"type": "select", "select": rnd(data["obs"])})
                elif data["type"] == "result":
                    assert data["players"][1] == "rule_based_1-bot"
                    rl_path = Path(data["rl"])
                    break
    lines = [json.loads(l) for l in rl_path.read_text().splitlines()]
    assert lines[0]["type"] == "meta" and lines[0]["agents"] == ["human", "bot:rule_based_1"]
    assert lines[-1]["type"] == "result"
    steps = [l for l in lines if l["type"] == "step"]
    assert steps and all("obs" in s and "action" in s and "legal_actions" in s for s in steps)
    assert all("search_begin_input" not in s["obs"] for s in steps)
    human_last = [s for s in steps if s["player"] == 0][-1]
    assert human_last["done"] and human_last["reward"] in (-1.0, 0.0, 1.0)
    assert any(s["events"] for s in steps)
    assert (server.tmp / "logs" / "results.csv").exists()


async def test_images_and_gallery(server):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://{server.host}:{server.port}/api/images") as r:
            m = await r.json()
            assert m["121"].endswith("SV06_EN_130.png")
        async with session.get(f"http://{server.host}:{server.port}/gallery") as r:
            html = await r.text()
            assert r.status == 200 and "dragapult_ex.csv" in html and "SV06_EN_130.png" in html


async def test_submission_bot_keeps_own_deck(server):
    from ptcg.bots import make_bot
    from ptcg.deck import load_deck as ld
    sample = ld(SAMPLE)
    _, deck = make_bot("rule_based_1", None)
    assert deck != sample and len(deck) == 60
    _, deck2 = make_bot("greedy", sample)
    assert deck2 == sample
    url = f"ws://{server.host}:{server.port}/ws"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.receive()
            await ws.send_json({"type": "join", "kind": "human", "room": "t6", "name": "h", "deck": "sample.csv",
                                "bot": "rule_based_1", "botDeck": "sample.csv"})
            rnd = bots.RandomBot(seed=6)
            async for msg in ws:
                data = json.loads(msg.data)
                if data["type"] == "observe":
                    await ws.send_json({"type": "select", "select": rnd(data["obs"])})
                elif data["type"] == "result":
                    meta = json.loads(Path(data["rl"]).read_text().splitlines()[0])
                    assert meta["decks"][0] == sample and meta["decks"][1] == deck  # bot ignored botDeck
                    break
