"""Offline fixture browser checks for Czech hints, links, touch and keyboard."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agentsmon import dashboard,__version__
from verify_compact_browser import crew

async def verify(out):
 from playwright.async_api import async_playwright
 out.mkdir(parents=True,exist_ok=True)
 payload={'agents':crew(12),'services':[],'columns':[],'agent_tables':[],
          'links':[{'title':'Mozek','url':'https://wiki.example.test/','note':'Team wiki','host':'wiki.example.test'}],
          'core':{'version':__version__,'state':'clean'}}
 payload['agents'][0].update(label='codex hook',status='No fresh bound hook',status_detail='No current exact-session observation',actions=False)
 requested=[];errors=[]
 async def route(r):
  path=r.request.url.split('hints.test')[-1];requested.append(path)
  if path=='/api/state':return await r.fulfill(content_type='application/json',body=json.dumps(payload))
  if path=='/api/ecosystem':return await r.fulfill(content_type='application/json',body='{}')
  if path=='/static/tailwind.js':return await r.fulfill(content_type='application/javascript',body=dashboard._STATIC_TAILWIND)
  if path=='/':return await r.fulfill(content_type='text/html',body=dashboard.PAGE.replace('POLL','3600'))
  return await r.abort()
 async with async_playwright() as pw:
  browser=await pw.chromium.launch()
  try:
   for touch,width in [(False,1440),(True,390),(True,320)]:
    context=await browser.new_context(viewport={'width':width,'height':900},has_touch=touch,is_mobile=touch)
    page=await context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
    await page.route('**/*',route);await page.goto('http://hints.test/')
    await page.wait_for_function('document.querySelector("#metric-agents").textContent==="12"')
    row=page.locator('[data-svc="agents"] tr[data-agent]').first
    hint=row.locator('.hint-i[data-key="no fresh bound hook"]');pop=page.locator('#hint-pop')
    if touch:
     assert await hint.is_visible()
     await hint.tap()
    else:
     await row.locator('[data-hint="No fresh bound hook"]').hover()
     await pop.wait_for(state='visible')
     await page.keyboard.press('Escape');await pop.wait_for(state='hidden')
     # Focus from keyboard exposes the desktop info button and its explanation.
     await hint.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab')
     assert await hint.is_visible()
    await pop.wait_for(state='visible')
    assert 'Neznamená to, že je agent vypnutý' in await pop.inner_text()
    assert 'No current exact-session observation' in await pop.inner_text()
    await pop.evaluate('(el)=>Promise.all(el.getAnimations().map(a=>a.finished))')
    assert await pop.evaluate('(el)=>getComputedStyle(el).opacity')=='1'
    assert await pop.evaluate('(el)=>getComputedStyle(el).backgroundColor')!='rgba(0, 0, 0, 0)'
    await page.screenshot(path=str(out/f'hints-{width}-popover.png'))
    if touch:
     assert (await hint.bounding_box())['width']<=18
     metric=page.locator('#metric-agents').locator('..').locator('.hint-target')
     label=await metric.bounding_box();button=await metric.locator('.hint-i').bounding_box()
     assert abs(label['y']+label['height']/2-button['y']-button['height']/2)<4
    # Pin via click to verify preservation across refresh.
    if not touch:await hint.click()
    assert await hint.get_attribute('aria-expanded')=='true'
    await page.evaluate('refresh()');await pop.wait_for(state='visible')
    assert 'Neznamená to' in await pop.inner_text()
    rect=await pop.bounding_box();assert rect['x']>=0 and rect['x']+rect['width']<=width+1
    await page.keyboard.press('Escape');await pop.wait_for(state='hidden')
    # Info does not activate the existing details/copy/row actions.
    details=row.locator('details');assert not await details.get_attribute('open')
    info=row.locator('.hint-i[data-key="connection details"]')
    if touch:await info.tap()
    else:await info.focus();await page.keyboard.press('Enter')
    assert await details.get_attribute('open') is None
    await page.keyboard.press('Escape')
    await details.locator('summary').click();assert await details.get_attribute('open') is not None
    link=page.locator('#links .link-chip');assert await link.get_attribute('href')=='https://wiki.example.test/'
    assert await link.get_attribute('rel')=='noopener noreferrer'
    # Bigger roster remains within the page on touch; no AI request is made.
    payload['agents']=crew(24);await page.evaluate('refresh()')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.screenshot(path=str(out/f'hints-{width}-dark.png'))
    await page.evaluate('document.documentElement.dataset.theme="light"')
    await page.screenshot(path=str(out/f'hints-{width}-light.png'))
    payload['agents']=crew(12);payload['agents'][0].update(label='codex hook',status='No fresh bound hook',status_detail='No current exact-session observation',actions=False)
    await context.close()
   assert not errors,errors
   assert all(p in ('/','/api/state','/api/ecosystem','/static/tailwind.js') for p in requested),requested
   print('PASS: hover, keyboard, touch390/320, Czech detail, refresh, Escape, details/link behavior, 24agents, no AI requests')
  finally:await browser.close()

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
 asyncio.run(verify(p.parse_args().out))
