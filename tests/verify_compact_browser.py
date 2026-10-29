"""Browser checks for the compact flight deck: density, roster filter, keyed provider tables
and bounded horizontal overflow.

Optional: python tests/verify_compact_browser.py --out /tmp/compact-evidence
Requires Playwright and its Chromium browser. All data are local FIXTURES, not measurements.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentsmon import dashboard, __version__


def crew(n, prefix='agent'):
    rows = []
    for i in range(n):
        name = f'{prefix}_{i + 1:02d}'
        row = {'id': name, 'name': name, 'kind': 'claude-code', 'vendor': 'anthropic', 'label': 'Opus 5.5',
               'alive': i % 5 != 3, 'age': 600 * (i + 1),
               'session_id': f'{i:08d}-1111-2222-3333-444455556666'}
        if i % 3 != 2:
            row.update(telegram_bot=f'{name}_bot', telegram_url=f'https://t.me/{name}_bot')
        rows.append(row)
    rows[1]['name'] = rows[1]['id'] = 'research_long_running_agent_with_a_very_descriptive_name'
    return rows


DECK_OVERLAP = '''()=>{const deck=document.querySelector('.deck').getBoundingClientRect();
  const parts=['.deck-ident','.deck-instruments .gauge-module','.deck-instruments .mission-metrics','.deck-link','.deck-switches']
    .map(s=>[s,document.querySelector(s)]).filter(([,el])=>el&&getComputedStyle(el).display!=='none')
    .map(([s,el])=>{const r=el.getBoundingClientRect();return [s,r]}).filter(([,r])=>r.width>0);
  const out=[];
  for(const [s,r] of parts){ if(r.left<deck.left-0.5||r.right>deck.right+0.5) out.push(['outside',s,r.left,r.right]); }
  for(let i=0;i<parts.length;i++)for(let j=i+1;j<parts.length;j++){const [a,ra]=parts[i],[b,rb]=parts[j];
    const x=Math.min(ra.right,rb.right)-Math.max(ra.left,rb.left), y=Math.min(ra.bottom,rb.bottom)-Math.max(ra.top,rb.top);
    if(x>0.5&&y>0.5) out.push(['overlap',a,b,Math.round(x),Math.round(y)]);}
  return out}'''


VISIBLE = '''(sel)=>{const rows=[...document.querySelectorAll(sel)];
  return rows.filter(r=>{const b=r.getBoundingClientRect();return b.height>0&&b.top>=0&&b.bottom<=innerHeight}).length}'''


async def verify(out):
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    payload = {'agents': crew(12), 'services': [], 'columns': [], 'agent_tables': [],
               'core': {'version': __version__, 'state': 'clean'}}

    async def route(request):
        path = request.request.url.split('compact.test')[-1]
        if path == '/api/state':
            return await request.fulfill(content_type='application/json', body=json.dumps(payload))
        if path == '/static/tailwind.js':
            return await request.fulfill(content_type='application/javascript', body=dashboard._STATIC_TAILWIND)
        if path == '/':
            return await request.fulfill(content_type='text/html', body=dashboard.PAGE.replace('POLL', '3600'))
        return await request.abort()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.route('**/*', route)
            await page.goto('http://compact.test/')
            await page.wait_for_function('document.querySelector("#metric-agents").textContent === "12"')
            rows = '[data-svc="agents"] .agent-rows > tr[data-agent]'

            # Density: at least 8 complete stations in the first 1440x900 viewport.
            visible = await page.evaluate(VISIBLE, rows)
            assert visible >= 8, visible
            await page.screenshot(path=str(out / 'compact-1440.png'))
            for width in (1440, 1280, 900, 861, 768, 390, 320):
                await page.set_viewport_size({'width': width, 'height': 900})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                for card in await page.locator(rows).all():
                    link = card.locator('.telegram-link')
                    if await link.count():
                        a, b = await card.locator('.agent-title').bounding_box(), await link.bounding_box()
                        assert a['x'] + a['width'] <= b['x'] + 1 and b['x'] + b['width'] <= width, (width, a, b)
            # Deck: no instrument overlaps another or leaves the console, at any width (real rects;
            # the console clips overflow, so scrollWidth alone cannot catch this).
            for width in [1440, 1320, 1281, 1280, 1260, 1241, 1240, 1220, 1200, 1181, 1180, 1100, 1024,
                          960, 900, 861, 860, 821, 820, 800, 768, 700, 641, 640, 600, 480, 414, 390, 375, 360, 340, 320]:
                await page.set_viewport_size({'width': width, 'height': 900})
                bad = await page.evaluate(DECK_OVERLAP)
                assert not bad, (width, bad)
            await page.set_viewport_size({'width': 1440, 'height': 900})

            # Roster filter: offered for large crews, hides rows, survives refresh with focus.
            tools = page.locator('[data-svc="agents"] .roster-tools')
            assert await tools.is_visible()
            box = page.locator('[data-svc="agents"] .roster-filter')
            await box.fill('agent_1')
            assert await page.locator(rows + ':not([hidden])').count() == 3   # agent_10..12
            assert '3 of 12 shown' in await page.locator('[data-svc="agents"] .roster-count').inner_text()
            await page.evaluate('refresh()')
            assert await box.evaluate('(el)=>el===document.activeElement')
            assert await page.locator(rows + ':not([hidden])').count() == 3
            # Empty roster while filtering: count must not keep the previous total.
            saved, payload['agents'] = payload['agents'], []
            await page.evaluate('refresh()')
            assert '0 of 0 shown' in await page.locator('[data-svc="agents"] .roster-count').inner_text()
            payload['agents'] = saved
            await page.evaluate('refresh()')
            await box.fill('')
            payload['agents'] = crew(4)
            await page.evaluate('refresh()')
            assert not await tools.is_visible()          # small crews get no filter chrome
            payload['agents'] = crew(12)
            await page.evaluate('refresh()')

            # Provider tables are keyed by id: adding/removing another table keeps filter + focus.
            team = {'id': 'fixture:team', 'title': 'Team', 'rows': crew(12, 'worker'), 'up': True}
            other = {'id': 'fixture:other', 'title': 'Other', 'rows': crew(2, 'other'), 'up': True}
            payload['agent_tables'] = [team]
            await page.evaluate('refresh()')
            team_box = page.locator('[data-svc="fixture:team"] .roster-filter')
            await team_box.fill('worker_01')
            await team_box.focus()
            payload['agent_tables'] = [other, team]
            await page.evaluate('refresh()')
            assert await team_box.input_value() == 'worker_01'
            assert await team_box.evaluate('(el)=>el===document.activeElement')
            assert await page.locator('[data-svc="fixture:team"] .agent-rows > tr[data-agent]:not([hidden])').count() == 1
            payload['agent_tables'] = [team]
            await page.evaluate('refresh()')
            assert await team_box.input_value() == 'worker_01'
            assert await team_box.evaluate('(el)=>el===document.activeElement')
            payload['agent_tables'] = []

            # Five provider columns: the roster scrolls inside its frame, the page never does.
            payload['columns'] = [{'id': f'c{i}', 'title': f'Column {i}'} for i in range(5)]
            for row in payload['agents']:
                row['cells'] = {f'c{i}': {'text': f'value {i}'} for i in range(5)}
            await page.evaluate('refresh()')
            for width in (1280, 900, 861):
                await page.set_viewport_size({'width': width, 'height': 900})
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                name = page.locator(rows).first.locator('.agent-title')
                assert (await name.bounding_box())['width'] >= 140, width
            await page.screenshot(path=str(out / 'compact-columns-861.png'))
            assert not errors, errors
            print('PASS: density >=8 at 1440x900, Telegram beside names 1440-320, deck rects 1440-320, filter/focus/empty count, '
                  'keyed provider tables, bounded overflow with 5 provider columns')
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(verify(parser.parse_args().out))
