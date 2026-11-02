"""Browser check of the queue table in the real dashboard page. Local fixture only; not part of unittest discovery.

Run with an interpreter that has Playwright:
  python tests/verify_queue_browser.py AGENTSMON_RELEASE_DIR CHROME_PATH OUTPUT_DIR
Exit 0 only when every check passes. Writes browser.json and screenshots into a new OUTPUT_DIR.
AGENTSMON_RELEASE_DIR must be agentsmon 0.4.0.dev8 or newer, which understands the workflow presentation.
"""
import asyncio
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agentops.ledger import Ledger  # noqa: E402
from agentops.review_queue import build  # noqa: E402

release, chrome, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(release))
from agentsmon import dashboard, plugins  # noqa: E402

spec = importlib.util.spec_from_file_location('queue_plugin', ROOT / 'monitor/plugin.py')
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)

LONG = '/home/agent/collaboration/velmi-dlouha-slozka-s-podklady-2026-11-02/root-review/required-fixes-s-dlouhym-nazvem.md'
REASON = 'Chybí podklad od dodavatele a bez něj nejde pokračovat, proto je předání stažené do doby, než dorazí'
with tempfile.TemporaryDirectory() as tmp:
    ledger = Ledger(Path(tmp) / 'fixture.sqlite')
    ledger.create('Běžný úkol Michala bez předání', 'michal', 'michal')
    decision = ledger.create('Rozhodnutí o ceně', 'autor', 'autor')['id']
    ledger.change(decision, 'autor', 'start')
    ledger.change(decision, 'autor', 'offer', target='michal', purpose='work', artifact='/podklady/moznosti.md')
    rework = ledger.create('<img src=x onerror="window.PWNED=1">', 'autor', 'autor')['id']
    ledger.change(rework, 'autor', 'start')
    ledger.change(rework, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/vystup.md')
    ledger.change(rework, 'kontrolor', 'accept')
    ledger.change(rework, 'kontrolor', 'offer', target='autor', purpose='work', artifact=LONG)
    blocked = ledger.create('Stažené předání', 'autor', 'autor')['id']
    ledger.change(blocked, 'autor', 'start')
    ledger.change(blocked, 'autor', 'block', note='stary duvod')
    ledger.change(blocked, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/v.md')
    ledger.change(blocked, 'autor', 'withdraw', note=REASON)
    checked = ledger.create('Neúspěšná kontrola výsledku', 'autor', 'autor')['id']
    ledger.change(checked, 'autor', 'start')
    ledger.record_postcheck(checked, 'autor', 'fail', '/dukazy/report-1.json', 'a' * 64)
    ledger.change(checked, 'autor', 'offer', target='kontrolor', purpose='review', artifact='/vystup-2.md')
    ledger.record_run_event({'task_id': checked, 'run_id': '11111111-1111-4111-8111-111111111111', 'parent_id': None, 'at': 5.0, 'source': 'operator',
                             'evidence': 'declared', 'actor': 'autor', 'action': 'start', 'outcome': 'unknown', 'error_code': None, 'usage_tokens': None})
    queue = build(ledger)
    ledger.close()

# The dashboard's own validation of a plugin result; a refused row would hide the whole plugin.
target = plugins.Plugin(id='agentops', version='fixture', target={}, settings={})
validated = plugins.validate_result(target, {'tables': [plugin.queue_table(queue)]})
table = validated['tables'][0]
# An ordinary table beside it must keep its health wording and an error must still show.
ordinary = plugins.validate_result(plugins.Plugin(id='ordinary', version='fixture', target={}, settings={}), {'tables': [
    {'id': 'team', 'title': 'Běžní agenti', 'rows': [{'id': 'one', 'name': 'agent-1', 'alive': True, 'label': 'x', 'status': 'Running'},
                                                     {'id': 'two', 'name': 'agent-2', 'alive': False, 'label': 'x', 'status': 'Stopped'}]}]})['tables'][0]
failed = plugin.queue_table(queue)
failed['id'] = 'failed'
failed['error'] = 'Fixture: snapshot nedostupný'
failed = plugins.validate_result(target, {'tables': [failed]})['tables'][0]
payload = {'time': 0, 'agents': [], 'agent_tables': [table, ordinary, failed], 'columns': [], 'services': [], 'plugins': [], 'plugin_errors': [],
           'core': {'version': 'fixture', 'state': 'clean'}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            body, kind = dashboard.PAGE.replace('POLL', '3600').encode(), 'text/html'
        elif self.path == '/api/state':
            body, kind = json.dumps(payload).encode(), 'application/json'
        elif self.path == '/static/tailwind.js':
            body, kind = dashboard._STATIC_TAILWIND, 'application/javascript'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
VISIBLE = ['Rozhodnutí: michal', 'Oprava po kontrole', 'moznosti.md', 'required-fixes-s-dlouhym-nazvem.md',
           'Stažené předání, důvod:', 'report-1.json', 'fail', 'Kontrola nezávisle, ne autor',
           '4 úkoly · 3 k pozornosti', 'Odkaz ke kopírování', 'Běhy: 1, nejasné: 1, spotřeba neznámá u 1']
TASKS, ATTENTION = 4, 3


async def run():
    from playwright.async_api import async_playwright
    failures, evidence = [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=chrome, headless=True)
        for width in (1280, 390):
            context = await browser.new_context(viewport={'width': width, 'height': 900}, permissions=['clipboard-read', 'clipboard-write'])
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.goto(f'http://127.0.0.1:{server.server_port}/')
            root = page.locator('[data-svc="agentops:queue"]')
            await root.locator('tbody tr').first.wait_for()
            text = await root.inner_text()
            names = await root.locator('tbody tr').count()
            missing = [item for item in VISIBLE if item not in text]
            reason_visible = REASON[:30] in text
            if 'Běžný úkol Michala' in text:
                failures.append(f'{width}: běžný úkol vlastníka je ve frontě')
            if 'stary duvod' in text:
                failures.append(f'{width}: starý důvod')
            # The reference must be reachable by touch: open the details of the long path and copy it.
            row = root.locator('tbody tr', has_text='required-fixes-s-dlouhym-nazvem.md')
            head = (await root.locator('.agents-count').inner_text()).strip()
            states = await root.locator('.agent-rows > tr').evaluate_all('rows => rows.map(tr => tr.dataset.state)')
            green = await root.locator('.agent-rows .bg-emerald-500, .agent-rows .bg-rose-500').count()
            head_classes = await root.locator('.svc-dot').get_attribute('class')
            ordinary_root = page.locator('[data-svc="ordinary:team"]')
            ordinary_text = await ordinary_root.inner_text()
            ordinary_states = await ordinary_root.locator('.agent-rows > tr').evaluate_all('rows => rows.map(tr => tr.dataset.state)')
            ordinary_attach = await ordinary_root.locator('[data-copy^="tmux attach"]').count()
            failed_root = page.locator('[data-svc="agentops:failed"]')
            failed_text = await failed_root.text_content()  # the third block starts collapsed
            failed_dot = await failed_root.locator('.svc-dot').get_attribute('class')
            await row.locator('summary').click()
            sid = row.locator('.sid')
            box = await sid.bounding_box()
            await sid.click()
            copied = await page.evaluate('navigator.clipboard.readText()')
            overflow = await page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
            await page.screenshot(path=str(out / f'queue-{width}.png'), full_page=True)
            links = await root.locator('a[href]').count()
            attach = await root.locator('[data-copy^="tmux attach"]').count()
            buttons = await root.locator('.agent-act').count()
            item = {'width': width, 'rows': names, 'missing': missing, 'reason_visible': reason_visible, 'errors': errors,
                    'copied_full_reference': copied == LONG, 'page_overflow': overflow, 'injected_images': await root.locator('img').count(),
                    'pwned': await page.evaluate('window.PWNED || null'), 'links': links, 'tmux_attach_offers': attach,
                    'action_buttons': buttons, 'sid_box': box, 'tooltip_only': False, 'head': head, 'states': states,
                    'health_coloured_rows': green, 'head_dot': head_classes, 'ordinary_states': ordinary_states,
                    'ordinary_attach': ordinary_attach, 'failed_dot': failed_dot}
            evidence.append(item)
            for name, bad in (('healthy ve frontě', 'healthy' in text), ('agents ve frontě', ' agents' in text.lower()),
                              ('špatný součet úkolů', sum(state != 'workflow-evidence' for state in states) != TASKS),
                              ('špatný počet k pozornosti', states.count('workflow-attention') != ATTENTION),
                              ('zdravotní barvy', green), ('hlava není k pozornosti', 'bg-amber-500' not in head_classes),
                              ('běžná tabulka změněna', '1/2 healthy' not in ordinary_text or ordinary_states != ['ok', 'down'] or ordinary_attach != 2),
                              ('chyba tabulky skryta', 'Fixture: snapshot nedostupný' not in failed_text or 'bg-rose-500' not in failed_dot),
                              ('chybová tabulka tvrdí zdraví', 'healthy' in failed_text)):
                if bad:
                    failures.append(f'{width}: {name}')
            for name, bad in (('chybí viditelné', missing), ('důvod není vidět', not reason_visible), ('chyby stránky', errors),
                              ('kopie není celá', not item['copied_full_reference']), ('přetečení stránky', overflow),
                              ('vložený obrázek', item['injected_images']), ('XSS', item['pwned']), ('tmux attach', attach), ('tlačítka akcí', buttons)):
                if bad:
                    failures.append(f'{width}: {name}: {bad}')
            await context.close()
        await browser.close()
    (out / 'browser.json').write_text(json.dumps({'release': str(release), 'evidence': evidence, 'failures': failures}, ensure_ascii=False, indent=2))
    print(json.dumps({'failures': failures, 'evidence': evidence}, ensure_ascii=False, indent=1))
    return 1 if failures else 0


code = asyncio.run(run())
server.shutdown()
raise SystemExit(code)
