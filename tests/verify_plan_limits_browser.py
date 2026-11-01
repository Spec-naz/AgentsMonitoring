"""Independent local-only panel fixture; no real telemetry or accounts."""
import argparse
import asyncio
import copy
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentsmon import dashboard
async def main(out):
 out.mkdir(parents=True, exist_ok=True)
 from playwright.async_api import async_playwright
 now=time.time()
 def row(i,title,pct=25,text='',label='5h'):
  return {'id':i,'name':i,'kind':'codex','alive':True,'actions_locked':True,'cells':{'usage:limits':{'text':text,'tone':'muted' if text else 'ok','title':title,'bars':[{'label':label,'percent':pct,'resets_at':now+3600,'title':'Synthetic reset'}]},'other:load':{'bars':[{'label':'NOT A QUOTA','percent':99}]}}}
 rows=[row('shared-one','Explicit shared · claude-statusline · now'),row('shared-two','Explicit shared · claude-statusline · now'),row('consumer-a','consumer · Pool A · Antigravity statusLine · now',label='weekly used'),row('consumer-b','consumer · Pool B · Antigravity statusLine · now',label='weekly used'),row('stale','Separate · codex-rollout · old',text='Cached · synthetic amount'),row('empty','Unavailable',text='Unavailable')]
 rows[-1]['cells']['usage:limits'].pop('bars')
 for r in rows[:2]: r['cells']['usage:limits']['quota_group']='source-'+'a'*64
 old=copy.deepcopy(rows[0]); old.update(id='shared-stale',name='shared-stale'); old['cells']['usage:limits'].update(text='Cached',tone='muted'); rows.append(old)
 payload={'agents':rows,'services':[],'columns':[{'id':'usage:limits','title':'Usage'},{'id':'other:load','title':'Load'}],'agent_tables':[],'plugin_errors':[],'core':{'version':'fixture','state':'clean'}}
 async def route(r):
  u=r.request.url
  if u.endswith('/api/state'): return await r.fulfill(json=payload)
  if u.endswith('/static/tailwind.js'): return await r.fulfill(content_type='application/javascript',body=dashboard._STATIC_TAILWIND)
  if u.endswith('/'): return await r.fulfill(content_type='text/html',body=dashboard.PAGE.replace('POLL','3600').replace('REQUEST_TIMEOUT','10'))
  return await r.abort()
 async with async_playwright() as p:
  browser=await p.chromium.launch()
  try:
   for theme in ['dark','light']:
    for width in [1280,390,320]:
     page=await browser.new_page(viewport={'width':width,'height':1000}); errors=[]
     page.on('pageerror',lambda e:errors.append(str(e)))
     await page.add_init_script('localStorage.setItem("agentsmon.theme",'+json.dumps(theme)+')')
     await page.route('**/*',route); await page.goto('http://quota.test/')
     await page.locator('#quota .quota-card').first.wait_for()
     content=await page.locator('#quota').inner_text()
     assert 'NOT A QUOTA' not in content,content
     assert await page.locator('#quota .quota-card').count()==5,content
     shared=page.locator('#quota .quota-card').filter(has_text='shared-one')
     assert await shared.count()==1 and 'shared-two' in await shared.inner_text(),content
     assert await page.locator('#quota .quota-card.is-stale').count()==2,content
     assert '75%' in content,content
     assert 'weekly used' not in content,content
     assert 'Pool A' in content and 'Pool B' in content,content
     stale=page.locator('#quota .quota-card').filter(has_text='staleSeparate')
     assert 'is-stale' in (await stale.get_attribute('class')),content
     assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(theme,width)
     assert not errors,errors
     await page.screenshot(path=str(out/f'quota-{theme}-{width}.png'),full_page=True)
     if theme == 'dark' and width == 1280:
      original = [copy.deepcopy(r['cells']['usage:limits']) for r in rows]
      for r in rows:
       r['cells']['usage:limits'] = {'text': 'Unavailable', 'tone': 'muted'}
      await page.evaluate('refresh()')
      assert await page.locator('#quota').is_hidden()
      for r, cell in zip(rows, original):
       r['cells']['usage:limits'] = cell
     await page.close()
  finally: await browser.close()
 print('PASS: six desktop/mobile dark/light fixtures; remaining arithmetic, explicit pools, stale prefix, unrelated bars excluded; no JS errors/overflow')
if __name__ == '__main__':
 parser = argparse.ArgumentParser()
 parser.add_argument('--out', type=Path, default=Path('browser-evidence/plan-limits'))
 asyncio.run(main(parser.parse_args().out))
