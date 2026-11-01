"""Actual automatic quota adapter and existing renderer, local synthetic metadata only."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentsmon import antigravity_usage, dashboard, plugins, usage_plugin


async def verify(out):
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        now = time.time()
        rows = []
        for index, quota in enumerate(({'weekly': {'remaining_fraction': .75, 'reset_in_seconds': 3600}},
                                      {'credits': {'remaining_amount': 25, 'reset_in_seconds': 3600}},
                                      {'weekly': {'remaining_fraction': .5, 'reset_time': now - 10}}, {})):
            sid = f'12345678-abcd-ef01-2345-{index:012d}'
            rows.append({'id': f'new-agent-{index}', 'name': f'Antigravity {index + 1}', 'kind': 'antigravity',
                         'session_id': sid, 'label': 'Gemini fixture', 'vendor': 'google', 'alive': True, 'actions_locked': True})
            if index != 3:
                snapshot = antigravity_usage.sanitize({'conversation_id': sid, 'model': {'id': 'gemini-fixture'}, 'quota': quota},
                                                       'gcp' if index == 1 else 'consumer',
                                                       'GCP trial unverified' if index == 1 else 'Google AI Pro', now)
                antigravity_usage.store_snapshot(snapshot, directory)
        result = usage_plugin.collect({'agents': rows}, {'antigravity_directory': str(directory)})
        for patch in result['patches']:
            target = next(r for r in rows if r['id'] == patch['row_id'])
            target['cells'] = {'usage:limits': plugins.cell(patch['cells']['limits'])}
        payload = {'agents': rows, 'services': [], 'columns': [{'id': 'usage:limits', 'title': 'Usage'}],
                   'agent_tables': [], 'plugin_errors': [], 'core': {'version': 'fixture', 'state': 'clean'}}
        async def route(request):
            url = request.request.url
            if url.endswith('/api/state'):
                return await request.fulfill(json=payload)
            if url.endswith('/static/tailwind.js'):
                return await request.fulfill(content_type='application/javascript', body=dashboard._STATIC_TAILWIND)
            if url.endswith('/'):
                return await request.fulfill(content_type='text/html', body=dashboard.PAGE.replace('POLL', '3600').replace('REQUEST_TIMEOUT', '10'))
            return await request.abort()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for theme in ('dark', 'light'):
                    for width in (1280, 390, 320):
                        page = await browser.new_page(viewport={'width': width, 'height': 1000})
                        errors = []
                        page.on('pageerror', lambda e: errors.append(str(e)))
                        await page.add_init_script('localStorage.setItem("agentsmon.theme",' + json.dumps(theme) + ')')
                        await page.route('**/*', route)
                        await page.goto('http://antigravity.test/')
                        await page.get_by_text('weekly used', exact=False).first.wait_for()
                        cells = page.locator('td[data-label="Usage"]')
                        self_text = await cells.nth(0).inner_text()
                        assert '25%' in self_text and 'used' in self_text, self_text
                        assert '25 remaining' in await cells.nth(1).inner_text()
                        assert '$' not in await cells.nth(1).inner_text()
                        assert 'No current reading' in await cells.nth(2).inner_text()
                        assert 'Unavailable' in await cells.nth(3).inner_text()
                        assert 'not dollars' in await cells.nth(1).locator('span[title]').first.get_attribute('title')
                        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert not errors, errors
                        await page.screenshot(path=str(out / f'antigravity-{theme}-{width}.png'), full_page=True)
                        await page.close()
            finally:
                await browser.close()
    print('PASS: automatic UUID quota adapter, used percentage, unitless amount, expired/missing, dark/light desktop/mobile')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--out', type=Path, required=True)
    asyncio.run(verify(parser.parse_args().out))
