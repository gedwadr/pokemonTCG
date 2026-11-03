import asyncio
import socket

import pytest
from aiohttp import web


class _Running:
    def __init__(self, host, port, runner):
        self.host, self.port, self.runner = host, port, runner


@pytest.fixture
async def aiohttp_server():
    """Minimal stand-in for pytest-aiohttp's fixture: start an app on a free port."""
    runners = []

    async def start(app: web.Application) -> _Running:
        runner = web.AppRunner(app)
        await runner.setup()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        runners.append(runner)
        return _Running("127.0.0.1", port, runner)

    yield start
    for r in runners:
        await r.cleanup()
