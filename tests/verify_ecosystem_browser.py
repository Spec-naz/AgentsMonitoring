"""Browser checks for the Security & ecosystem section (FIXTURE data, no host scan).

Optional: python tests/verify_ecosystem_browser.py --out /tmp/eco-evidence
Requires Playwright and its Chromium browser.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentsmon import dashboard, __version__

XSS = '<img src=x onerror="window.PWNED=1">'


def eco_fixture():
    return {
        "generated_at": 1793400000, "duration_ms": 12, "host": "fixture-host", "user": "fixture",
        "security": {"counts": {"risk": 1, "warn": 1, "ok": 1, "unknown": 1}, "checks": [
            {"id": "ok1", "title": "Firewall (ufw)", "status": "ok", "detail": "enabled", "advice": ""},
            {"id": "w1", "title": "Package updates", "status": "warn", "detail": "8 security", "advice": "Apply updates."},
            {"id": "r1", "title": "SSH password login " + XSS, "status": "risk", "detail": XSS, "advice": XSS},
            {"id": "u1", "title": "Auth log", "status": "unknown", "detail": "needs root", "advice": ""}]},
        "access": [
            {"owner": "Hermes", "name": "Provider keys", "kind": "environment file", "path": "~/.hermes/.env",
             "exists": True, "mode": "600", "age_days": 3, "exposed": False,
             "keys": ["GEMINI_API_KEY", "GITHUB_TOKEN", "A", "B", "C", "D", "E", XSS]},
            {"owner": "Unclassified", "name": "api-token", "kind": "possible credential",
             "path": "~/.config/thing/" + "very_long_unbroken_name_" * 6, "exists": True, "mode": "644",
             "age_days": 0, "exposed": True}],
        "ecosystem": {
            "sessions": [{"name": "CEO_Master", "command": "codex", "cwd": "~"}],
            "services": [{"name": "hermes-gateway.service", "active": "active", "sub": "running", "description": XSS},
                         {"name": "health.service", "active": "inactive", "sub": "dead", "description": "check", "timer": True}],
            "cron": ["* * * * * /home/u/launch.sh"],
            "listeners": [{"proto": "tcp", "address": "0.0.0.0", "port": "22", "process": "", "public": True},
                          {"proto": "tcp", "address": "0.0.0.0", "port": "9000", "process": "node", "public": True}],
            "repositories": [{"path": "~/AgentsMonitoring", "remote": "https://github.com/x/y.git",
                              "branch": "main", "changes": 2, "last_commit": 1793300000}]},
        "limits": ["Runs without root."]}


async def verify(out):
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    state = {'agents': [{'name': 'CEO_Master', 'kind': 'codex', 'vendor': 'openai', 'label': 'GPT', 'alive': True}],
             'services': [], 'columns': [], 'agent_tables': [], 'core': {'version': __version__, 'state': 'clean'}}
    mode = {'eco_status': 200, 'eco_calls': [], 'eco': eco_fixture()}

    async def route(request):
        url = request.request.url.split('eco.test')[-1]
        if url.startswith('/api/ecosystem'):
            mode['eco_calls'].append(url)
            return await request.fulfill(status=mode['eco_status'], content_type='application/json', body=json.dumps(mode['eco']))
        if url == '/api/state':
            return await request.fulfill(content_type='application/json', body=json.dumps(state))
        if url == '/static/tailwind.js':
            return await request.fulfill(content_type='application/javascript', body=dashboard._STATIC_TAILWIND)
        if url == '/':
            return await request.fulfill(content_type='text/html', body=dashboard.PAGE.replace('POLL', '3600'))
        return await request.abort()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            for theme in ('dark', 'light'):
                page = await browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.add_init_script(f'try{{localStorage.setItem("agentsmon.theme","{theme}");'
                                           'localStorage.setItem("agentsmon.collapsed:ecosystem","0")}catch(e){}')
                await page.route('**/*', route)
                await page.goto('http://eco.test/')
                eco = page.locator('#eco')
                await page.wait_for_function('document.querySelector("#eco .eco-summary").textContent.includes("risk")')
                summary = await eco.locator('.eco-summary').inner_text()
                assert summary == '1 risk · 1 warning · 1 not verified', summary
                rows = eco.locator('.eco-panel .eco-row')
                assert await rows.first.get_attribute('data-status') == 'risk'      # risks sorted first
                assert await page.evaluate('window.PWNED || null') is None
                assert await eco.locator('img').count() == 0
                await eco.screenshot(path=str(out / f'eco-security-{theme}.png'))
                await page.get_by_role('tab', name='Access').click()
                assert '+2 more' in await eco.locator('.eco-panel').inner_text()
                assert await eco.locator('.eco-row[data-status=risk]').count() == 1    # 0o644 file
                assert 'excluded from this response' in await eco.locator('.eco-note').inner_text()
                await eco.screenshot(path=str(out / f'eco-access-{theme}.png'))
                tab = page.get_by_role('tab', name='Access')
                await tab.focus()
                await page.keyboard.press('ArrowRight')
                assert await page.get_by_role('tab', name='Ecosystem').get_attribute('aria-selected') == 'true'
                text = await eco.locator('.eco-panel').inner_text()
                for needle in ('CEO_Master', 'hermes-gateway.service', 'launch.sh', '9000', '2 changed', 'on timer'):
                    assert needle in text, needle
                assert await eco.locator('.eco-row[data-status=warn]').count() >= 2   # public :9000, dirty repo
                await eco.screenshot(path=str(out / f'eco-ecosystem-{theme}.png'))
                assert await page.evaluate('window.PWNED || null') is None
                for width in (1280, 768, 390, 320):
                    await page.set_viewport_size({'width': width, 'height': 900})
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (theme, width)
                await page.screenshot(path=str(out / f'eco-mobile-{theme}.png'), full_page=True)
                await page.set_viewport_size({'width': 1280, 'height': 900})
                # Rescan asks the server for a refresh; a failed scan keeps the last data and says so.
                before = len(mode['eco_calls'])
                mode['eco_status'] = 503
                await eco.locator('.eco-rescan').click()
                await page.wait_for_function('document.querySelector("#eco .eco-summary").textContent === "scan unavailable"')
                assert mode['eco_calls'][before:] == ['/api/ecosystem?refresh=1']
                assert 'CEO_Master' in await eco.locator('.eco-panel').inner_text()
                mode['eco_status'] = 200
                assert not errors, errors
                await page.close()
            # Default: collapsed so the agent roster stays first; the summary is visible in the head.
            page = await browser.new_page(viewport={'width': 1280, 'height': 900})
            await page.route('**/*', route)
            await page.goto('http://eco.test/')
            await page.wait_for_function('document.querySelector("#eco .eco-summary").textContent.includes("risk")')
            assert await page.locator('#eco .svc-head').get_attribute('aria-expanded') == 'false'
            assert await page.locator('#eco .eco-summary').is_visible()
            print('PASS: summary/sorting, escaping, access metadata, tabs+keyboard, ecosystem groups, '
                  'themes, 1280-320 no overflow, rescan + failure, collapsed by default')
        finally:
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    asyncio.run(verify(parser.parse_args().out))
