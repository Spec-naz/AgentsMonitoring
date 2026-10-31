"""Synthetic plugin acceptance against an existing AgentsMonitoring checkout."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import time


async def verify(source, out):
    sys.path.insert(0, str(source))
    from agentsmon import dashboard, plugins
    from playwright.async_api import async_playwright
    spec = importlib.util.spec_from_file_location('agentops_plugin', Path(__file__).parents[1] / 'monitor/plugin.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'snapshot.json'
        path.write_text(json.dumps({'schema_version': 1, 'generated_at': time.time(), 'costs': [],
            'signals': {'klodak': {'signal': 'waiting_input_hint', 'samples': 2},
                        'kodak': {'signal': 'unclassified', 'provider_observation': {
                            'state': 'idle', 'provider': 'codex', 'last_event': 'Stop', 'age_seconds': 3,
                            'confidence': 'confirmed', 'proves_task_progress': False}}}, 'tasks': [
            {'id': 'uuid-one', 'title': 'Review <img src=x onerror="window.PWNED=1">', 'owner': 'kodak',
             'state': 'handoff_pending', 'target': 'klodak', 'version': 3, 'created': time.time() - 700,
             'updated': time.time(), 'progress_age_seconds': 650, 'attention': 'handoff_unacknowledged'}]}))
        raw = module.collect({}, {'snapshot_path': str(path)})
        provider = plugins.Plugin('agentops', '0.1.0', {}, {}, 2)
        # Normalize through the same contract as a production worker.
        tables = [{**table, 'id': 'agentops:' + table['id'],
                   'rows': [plugins.row(row, provider.id) for row in table['rows']]} for table in raw['tables']]
    state = {'time': time.time(), 'agents': [], 'services': [], 'columns': [], 'plugin_errors': [],
             'agent_tables': tables, 'core': {'version': 'fixture', 'state': 'clean'}}
    async def route(request):
        url = request.request.url
        if '/api/state' in url:
            return await request.fulfill(content_type='application/json', body=json.dumps(state))
        if '/api/ecosystem' in url:
            return await request.fulfill(status=503, body='fixture unavailable')
        if '/static/tailwind.js' in url:
            return await request.fulfill(content_type='application/javascript', body=dashboard._STATIC_TAILWIND)
        if url.endswith('/'):
            return await request.fulfill(content_type='text/html', body=dashboard.PAGE.replace('POLL', '3600').replace('REQUEST_TIMEOUT', '10'))
        return await request.abort()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for theme in ('dark', 'light'):
                for width in (1280, 390, 320):
                    page = await browser.new_page(viewport={'width': width, 'height': 900})
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    await page.add_init_script(f'localStorage.setItem("agentsmon.theme","{theme}")')
                    await page.route('**/*', route)
                    await page.goto('http://fixture.test/')
                    await page.get_by_text('Handoff not accepted', exact=True).wait_for()
                    await page.get_by_text('Possible input dialog', exact=True).wait_for()
                    await page.get_by_text('Turn ended (hook)', exact=True).wait_for()
                    assert await page.locator('img').count() == 0
                    assert await page.evaluate('window.PWNED || null') is None
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    assert await page.get_by_text('Work queue (declared state)', exact=True).count() == 1
                    assert not errors, errors
                    await page.screenshot(path=str(out / f'agentops-{theme}-{width}.png'), full_page=True)
                    await page.close()
        finally:
            await browser.close()
    print('PASS: real plugin contract, existing renderer, declared handoff state, escaping, themes, 1280/390/320 widths')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--agentsmon', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(verify(args.agentsmon, args.out))
