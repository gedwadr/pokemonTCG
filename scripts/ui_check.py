"""Drive the web UI with Playwright and save screenshots (lobby, gallery, a game vs rule_based_1).

    python -m ptcg.server --port 8771 --logs "" --rl "" --replays ""   # in another terminal
    python scripts/ui_check.py 8771 screenshots

Needs: pip install playwright && python -m playwright install chromium
"""
import asyncio, sys, json
from playwright.async_api import async_playwright

import os
PORT = sys.argv[1] if len(sys.argv) > 1 else "8765"; OUT = sys.argv[2] if len(sys.argv) > 2 else "screenshots"; os.makedirs(OUT, exist_ok=True)
BASE = f"http://127.0.0.1:{PORT}"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1400, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        await page.goto(BASE + "/", wait_until="networkidle")
        await page.wait_for_function("document.querySelectorAll('#deckPreview .pc').length > 0")
        await page.select_option("#deckfile", "dragapult_ex.csv")
        await page.wait_for_function("document.querySelectorAll('#deckPreview .pc').length >= 20")
        await page.wait_for_timeout(1500)
        n_pc = await page.evaluate("document.querySelectorAll('#deckPreview .pc').length")
        n_img = await page.evaluate("document.querySelectorAll('#deckPreview .pc img').length")
        n_vis = await page.evaluate("[...document.querySelectorAll('#deckPreview .pc img')].filter(i => i.offsetParent !== null).length")
        print(f"lobby: {n_pc} deck entries, {n_img} img elements, {n_vis} visible (expect visible == entries)")
        await page.screenshot(path=f"{OUT}/1_lobby.png", full_page=True)
        await page.hover("#deckPreview .pc >> nth=2")
        await page.wait_for_timeout(300)
        await page.screenshot(path=f"{OUT}/2_lobby_hover.png", full_page=False)

        # gallery
        g = await browser.new_page(viewport={"width": 1400, "height": 1000})
        await g.goto(BASE + "/gallery", wait_until="networkidle")
        await g.wait_for_timeout(1500)
        await g.screenshot(path=f"{OUT}/3_gallery.png", full_page=False)
        await g.close()

        # play: join vs rule_based_1 and make a few random moves
        await page.select_option("#bot", "rule_based_1")
        await page.fill("#room", "pw")
        await page.click("#join")
        for step in range(8):
            await page.wait_for_function("document.querySelectorAll('#opts button').length > 0", timeout=20000)
            await page.wait_for_timeout(400)
            if step == 4:
                vis = await page.evaluate("[...document.querySelectorAll('#hand .card, #youBoard .card, #oppBoard .card')].map(c => [...c.querySelectorAll('img')].filter(i => i.offsetParent !== null).length)")
                print("game: visible imgs per card element (expect all 1):", vis)
                await page.screenshot(path=f"{OUT}/4_game.png", full_page=True)
                await page.hover("#hand .card >> nth=0"); await page.wait_for_timeout(300)
                await page.screenshot(path=f"{OUT}/5_game_hover.png", full_page=False)
            n = await page.evaluate("document.querySelectorAll('#opts button').length")
            pick = await page.evaluate("document.getElementById('pick').textContent")
            if "click an option" in pick:
                await page.click("#opts button >> nth=0")
            else:
                await page.click("#opts button >> nth=0")
                await page.click("#confirm")
            await page.wait_for_timeout(300)
        await page.screenshot(path=f"{OUT}/6_game_later.png", full_page=True)
        print("page errors:", errors or "none")
        await browser.close()

asyncio.run(main())
