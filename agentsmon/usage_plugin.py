"""Opt-in, account-scoped usage column. Inspired by TomasErnest's PR #8.

Read local usage/status-line snapshots only. No OAuth token reads or network
requests; never infer that all agents from one vendor share an account.
"""
from datetime import datetime
import json
import math
from pathlib import Path
import time


def epoch(value):
    if type(value) in (int,float) and math.isfinite(value): return int(value)
    if isinstance(value,str): return int(datetime.fromisoformat(value.replace('Z','+00:00')).timestamp())
    return None


def window(label, pct, reset, now):
    if type(pct) not in (int,float) or not math.isfinite(pct) or not 0<=pct<=100: return None
    reset=epoch(reset)
    if reset and reset<=now: return None
    return {'label':label,'percent':round(pct,1),
            'title':('Resets '+datetime.fromtimestamp(reset).astimezone().isoformat()) if reset else 'Reset time unavailable'}


def read_source(spec, now):
    path=Path(spec['path']).expanduser()
    kind=spec['format']
    with path.open('rb') as stream:
        if kind=='codex-rollout':
            stream.seek(max(0,stream.seek(0,2)-2_000_000))
        raw=stream.read(2_000_001)
    if len(raw)>2_000_000: raise ValueError('Oversized snapshot')
    stamp=path.stat().st_mtime
    if kind=='codex-rollout':
        data=None
        for line in reversed(raw.splitlines()):
            try: record=json.loads(line)
            except ValueError: continue
            limits=(record.get('payload') or {}).get('rate_limits')
            if limits:
                data=limits; stamp=epoch(record.get('timestamp')) or stamp; break
        if data is None: raise ValueError('No usage snapshot')
        pairs=[('5h','primary','used_percent','resets_at'),('7d','secondary','used_percent','resets_at')]
    elif kind=='claude-statusline':
        data=json.loads(raw).get('rate_limits') or {}
        pairs=[('5h','five_hour','used_percentage','resets_at'),('7d','seven_day','used_percentage','resets_at')]
    elif kind=='chatgpt-snapshot':
        data=json.loads(raw).get('rate_limit') or {}
        pairs=[('5h','primary_window','used_percent','reset_at'),('7d','secondary_window','used_percent','reset_at')]
    else: raise ValueError('Unknown usage source')
    bars=[]
    for label,key,pct,reset in pairs:
        w=data.get(key) or {}
        # Prefer reported window length, not a guessed five-hour/week label.
        minutes=w.get('window_minutes') or (w.get('limit_window_seconds') or 0)/60
        if minutes: label=(f'{minutes/1440:g}d' if minutes%1440==0 else f'{minutes/60:g}h')
        b=window(label,w.get(pct),w.get(reset),now)
        if b: bars.append(b)
    if not bars: return {'text':'No current reading','tone':'muted','title':'No unexpired usage window'}
    stale=now-stamp>600 or stamp>now+30
    return {'text':'Cached' if stale else '', 'tone':'muted' if stale else 'ok', 'bars':bars,
            'title':f"{spec.get('account','Explicit source')} · {kind} · {datetime.fromtimestamp(stamp).astimezone().isoformat()}"}


def antigravity_reading(sid, directory, now):
    from .antigravity_usage import UUID, label, number, read_json
    if not isinstance(sid, str) or not UUID.fullmatch(sid):
        raise ValueError('Conversation UUID unavailable')
    data = read_json(Path(directory).expanduser() / (sid.lower() + '.json'))
    if data.get('schema_version') != 1 or data.get('conversation_id') != sid.lower():
        raise ValueError('Conversation identity mismatch')
    account = label(data['profile']) + ' · ' + label(data['pool'])
    stamp = data['observed_at']
    if not number(stamp) or stamp > now + 30:
        raise ValueError('Invalid observation time')
    quota = data.get('quota')
    if not isinstance(quota, dict) or len(quota) > 8:
        raise ValueError('Invalid quota metadata')
    bars, amounts, disabled = [], [], False
    for bucket, item in quota.items():
        label(bucket)
        if not isinstance(item, dict):
            continue
        if item.get('disabled') is True:
            disabled = True
            continue
        reset = item.get('reset_at')
        if reset is not None and (not number(reset) or reset <= now):
            continue
        fraction = item.get('remaining_fraction')
        amount = item.get('remaining_amount')
        if number(fraction) and 0 <= fraction <= 1 and len(bars) < 4:
            bar = window(bucket[:25] + ' used', (1 - fraction) * 100, reset, now)
            if bar:
                bars.append(bar)
        elif number(amount) and amount >= 0 and len(amounts) < 4:
            amounts.append(bucket[:30] + ': ' + format(amount, '.6g') + ' remaining')
    stale = now - stamp > 600
    if not bars and not amounts:
        return {'text': 'Disabled' if disabled else 'No current reading', 'tone': 'muted',
                'title': (account + ' · No unexpired quota reading; context tokens are not quota')[:500]}
    title = account + ' · Antigravity statusLine · ' + datetime.fromtimestamp(stamp).astimezone().isoformat()
    if amounts:
        title += ' · Remaining amount units unspecified; not dollars or verified billed spend'
    value = {'text': ('Cached · ' if stale else '') + '; '.join(amounts),
             'tone': 'muted' if stale else 'ok', 'title': title[:500]}
    if bars:
        value['bars'] = bars
    return value


def collect(context,settings):
    sources=settings.get('sources',{})
    if not isinstance(sources,dict) or len(sources)>200: raise ValueError('Invalid usage mapping')
    patches=[]
    for identity,source in sources.items():
        try: value=read_source(source,time.time())
        except Exception: value={'text':'Unavailable','tone':'muted','title':'Usage source unavailable; account not inferred'}
        patches.append({'row_id':identity,'cells':{'limits':value}})
    directory = settings.get('antigravity_directory', '~/.local/state/agentsmon/usage/antigravity')
    for agent in context.get('agents', []):
        if not isinstance(agent, dict) or agent.get('kind') != 'antigravity':
            continue
        identity = agent.get('id') or agent.get('name')
        if not identity or identity in sources:
            continue
        sid, conversation = agent.get('session_id'), agent.get('conversation_id')
        try:
            if sid and conversation and sid != conversation:
                raise ValueError('Conflicting conversation identity')
            value = antigravity_reading(sid or conversation, directory, time.time())
        except Exception:
            value = {'text': 'Unavailable', 'tone': 'muted',
                     'title': 'Antigravity quota unavailable; configure the profile collector and wait for its statusLine event'}
        patches.append({'row_id': identity, 'cells': {'limits': value}})
    return {'columns':[{'id':'limits','title':'Usage'}],'patches':patches}
