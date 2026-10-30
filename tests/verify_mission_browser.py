"""Browser regression checks for long identities, themes and snapshot failures.

Optional: python tests/verify_mission_browser.py --out /tmp/mission-evidence
Requires Playwright and its Chromium browser. All requests use local fixtures.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentsmon import dashboard, __version__


async def verify(out):
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    agent = {'name': 'agent_vsehomira_Claude', 'kind': 'claude-code', 'vendor': 'anthropic',
             'label': 'Opus 5.5', 'alive': True, 'age': 7200,
             'session_id': '321d6959-991b-4c9b-bc95-4ee8b853c076',
             'telegram_bot': 'agent_vsehomira_claude_bot',
             'telegram_url': 'https://t.me/agent_vsehomira_claude_bot'}
    payload = {'agents': [{**agent, 'name': 'CEO_Master', 'vendor': 'openai',
                           'kind': 'codex', 'label': 'GPT-6.1 Sol'}, agent],
               'services': [], 'columns': [], 'agent_tables': [],
               'core': {'version': __version__, 'state': 'clean'}}
    mode = {'status': 200, 'delay': 2}
    posts = []
    async def route(request):
        path = request.request.url.split('mission.test')[-1]
        if request.request.method == 'POST':
            posts.append(request.request.post_data)
            return await request.fulfill(status=200, json={'ok': True})
        if path == '/api/state':
            await asyncio.sleep(mode['delay'])
            return await request.fulfill(status=mode['status'], content_type='application/json',
                                         body=json.dumps(payload))
        if path == '/static/tailwind.js':
            return await request.fulfill(content_type='application/javascript', body=dashboard._STATIC_TAILWIND)
        if path == '/':
            return await request.fulfill(content_type='text/html', body=dashboard.PAGE.replace('POLL', '15'))
        return await request.abort()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 1280, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/*', route)
            await page.goto('http://mission.test/', wait_until='domcontentloaded')
            await page.wait_for_timeout(1100)
            assert await page.locator('#connection-announcement').inner_text() == ''
            await page.wait_for_function('document.querySelector("#metric-agents").textContent === "2"')
            await page.locator('#connection-state[data-state=fresh]').wait_for()
            assert await page.locator('#gauge-value').text_content() == '2/2'
            assert await page.locator('#gauge').get_attribute('data-level') == 'full'
            assert float(await page.locator('#gauge-arc').get_attribute('stroke-dashoffset')) == 0
            mode['delay'] = 0
            assert 'columnheader' in await page.locator('[data-svc=agents] table').aria_snapshot()
            cards = page.locator('[data-svc="agents"] .agent-rows > tr')
            await page.screenshot(path=str(out / 'mission-desktop.png'), full_page=True)
            for width in (1280, 768, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 900})
                for card in await cards.all():
                    name = card.locator('.agent-title')
                    link = card.locator('.telegram-link')
                    a, b = await name.bounding_box(), await link.bounding_box()
                    assert a and b and a['x'] + a['width'] <= b['x'] + 1, (width, a, b)
                    assert b['x'] + b['width'] <= width, (width, b)
                    assert await name.evaluate('(el)=>el.scrollWidth <= el.clientWidth + 1')
                    assert await link.get_attribute('aria-label')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
            await page.screenshot(path=str(out / 'mission-mobile.png'), full_page=True)
            for color in ('red', 'gold', 'green', 'blue'):
                agent['name_color'] = color
                await page.evaluate('refresh()')
                assert await cards.nth(1).locator('.agent-title .inline-block').evaluate('(el)=>getComputedStyle(el).color') == 'rgb(22, 37, 61)'
            agent.pop('name_color')
            agent['status'] = 'Waiting for authentication: please sign in to continue'
            await page.evaluate('refresh()')
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            agent.pop('status')
            daemon = {**agent, 'name': 'Health fixture', 'kind': 'daemon', 'health_url': 'http://fixture.invalid/health', 'alive': False}
            payload['agents'].append(daemon)
            await page.evaluate('refresh()')
            assert 'Endpoint unavailable' in await cards.nth(2).inner_text()
            assert await page.locator('#gauge-value').text_content() == '2/3'
            assert await page.locator('#gauge').get_attribute('data-level') == 'partial'
            daemon['alive'] = True
            await page.evaluate('refresh()')
            assert 'Endpoint responding' in await cards.nth(2).inner_text()
            assert await page.locator('#metric-running').inner_text() == '2'
            payload['agents'].pop()
            agent['alive'] = False
            await page.evaluate('refresh()')
            assert await page.locator('#gauge-value').text_content() == '1/2'
            assert await page.locator('#gauge').get_attribute('data-level') == 'partial'
            assert 144 < float(await page.locator('#gauge-arc').get_attribute('stroke-dashoffset')) < 145
            assert await cards.nth(1).evaluate('(el)=>el.classList.contains("agent-changed")')
            await page.evaluate('refresh()')
            assert not await cards.nth(1).evaluate('(el)=>el.classList.contains("agent-changed")')
            agent['alive'] = True
            await page.evaluate('refresh()')
            await cards.nth(1).locator('.agent-name').focus()
            await page.evaluate('refresh()')
            assert await cards.nth(1).locator('.agent-name').evaluate('(el)=>el===document.activeElement')
            await page.keyboard.press('Enter')
            assert await cards.nth(1).locator('.agent-name').evaluate('(el)=>el===document.activeElement')
            agent['name'] = 'unbroken_' + 'x' * 180 + '<img src=x onerror="window.PWNED=1">'
            await page.evaluate('refresh()')
            assert await page.locator('[data-svc="agents"] img').count() == 0
            assert await page.evaluate('window.PWNED || null') is None
            assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            detail = cards.nth(1).locator('details')
            await detail.locator('summary').click()
            await page.evaluate('refresh()')
            assert await detail.get_attribute('open') is not None
            await page.get_by_role('button', name='Switch to light theme').click()
            await page.reload()
            await page.wait_for_function('document.documentElement.dataset.theme === "light"')
            await page.wait_for_function('document.querySelector("#metric-agents").textContent === "2"')
            await page.screenshot(path=str(out / 'mission-light.png'), full_page=True)
            # A failed request remains stale between ticks, even just after a successful fetch.
            mode['status'] = 503
            await page.evaluate('refresh()')
            await page.wait_for_timeout(1200)
            assert await page.locator('#app').get_attribute('data-stale') == 'true'
            assert 'connection failed' in await page.locator('#connection-state').inner_text()
            await page.screenshot(path=str(out / 'mission-stale.png'), full_page=True)
            mode['status'] = 200
            await page.evaluate('refresh()')
            assert await page.locator('#app').get_attribute('data-stale') == 'false'
            await page.evaluate('lastSnapshot=Date.now()-staleAfter-1000;updateConnection()')
            assert await page.locator('#app').get_attribute('data-stale') == 'true'
            await page.evaluate('nextPoll=Date.now()+5000;updateConnection()')
            assert 'Next scheduled snapshot in 5s' in await page.locator('#poll-caption').inner_text()
            assert await page.locator('#deck-clock').inner_text() != '--:--:--'
            original_agents=payload['agents']
            payload['agents']=[]
            await page.evaluate('refresh()')
            assert await page.locator('#gauge-value').text_content() == '0/0'
            assert await page.locator('#gauge').get_attribute('data-level') == 'none'
            payload['agents']=original_agents
            await page.evaluate('refresh()')
            await page.emulate_media(reduced_motion='reduce')
            assert await page.locator('#theme-toggle').evaluate('(el)=>getComputedStyle(el).transitionDuration') == '0s'
            head = page.locator('[data-svc="agents"] .svc-head')
            await head.focus()
            await page.keyboard.press('Enter')
            assert await head.get_attribute('aria-expanded') == 'false'
            await page.keyboard.press('Space')
            assert await head.get_attribute('aria-expanded') == 'true'
            assert not errors, errors
            assert not posts, posts
            print('PASS: desktop/mobile identity, Telegram, escaping, details, themes, stale/error/recovery, keyboard, reduced motion, gauge states and poll timing')
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(verify(parser.parse_args().out))
