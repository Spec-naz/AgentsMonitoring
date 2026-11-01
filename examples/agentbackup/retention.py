"""Narrow retention of verified recovery capsules; never sync/purge directories."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess

ARCHIVE = re.compile(r'agent-recovery-(\d{8}-\d{6})\.zip\.age\Z')

def created(name):
    match=ARCHIVE.fullmatch(name)
    if not match:return None
    try:return datetime.strptime(match[1],'%Y%m%d-%H%M%S').replace(tzinfo=timezone.utc)
    except ValueError:return None

def verified(record,name,size):
    return (isinstance(record,dict) and isinstance(record.get('archive'),str)
            and record.get('offsite_ok') is True
            and Path(record.get('archive','')).name==name
            and type(record.get('bytes')) is int and record['bytes']==size
            and isinstance(record.get('sha256'),str)
            and re.fullmatch(r'[a-f0-9]{64}',record['sha256']) is not None)

def prune_local(state,keep,current):
    if type(keep) is not int or keep<1:raise ValueError('Local retention must keep at least one copy')
    candidates=[]
    for archive in sorted(Path(state).glob('agent-recovery-*.zip.age'),reverse=True):
        receipt=archive.with_suffix(archive.suffix+'.json')
        if (not created(archive.name) or archive.is_symlink() or receipt.is_symlink()
                or not receipt.is_file()):continue
        try:record=json.loads(receipt.read_text())
        except (OSError,ValueError):continue
        if not verified(record,archive.name,archive.stat().st_size):continue
        digest=hashlib.sha256()
        with archive.open('rb') as source:
            for block in iter(lambda:source.read(1024**2),b''):digest.update(block)
        if digest.hexdigest()!=record['sha256']:continue
        candidates.append((archive,receipt))
    removed=[]
    for archive,receipt in candidates[keep:]:
        if archive.name==current:continue
        archive.unlink();receipt.unlink();removed.append(archive.name)
    return removed

def prune_remote(common,remote,days,current,now=None):
    if type(days) is not int or not 1<=days<=3650:raise ValueError('Invalid remote retention days')
    # Root-level cleanup is deliberately unsupported, even if a backend has a virtual root.
    if not isinstance(remote,str) or ':' not in remote:
        raise ValueError('Remote retention requires an explicit folder path')
    folder=remote.split(':',1)[1].rstrip('/')
    if (not folder or folder.startswith('/') or '\\' in folder
            or any(part in ('', '.', '..') for part in folder.split('/'))):
        raise ValueError('Remote retention requires a non-root canonical folder path')
    remote=remote.rstrip('/')
    now=now or datetime.now(timezone.utc);cutoff=now-timedelta(days=days)
    result=subprocess.run(common+['lsjson',remote,'--files-only','--max-depth','1'],
                          capture_output=True,text=True,check=True,timeout=120)
    entries=json.loads(result.stdout)
    by_name={item['Name']:item for item in entries if isinstance(item,dict)
             and isinstance(item.get('Name'),str) and not item.get('IsDir')}
    valid=[name for name in by_name if created(name)]
    newest=max(valid) if valid else None
    # Current verified upload must be visible before any deletion.
    if current not in valid:raise ValueError('Current verified backup missing from retention listing')
    removed=[]
    for name in sorted(valid):
        if name in (current,newest) or created(name)>=cutoff:continue
        side=by_name.get(name+'.json')
        if (not side or type(side.get('Size')) is not int
                or not 0<side['Size']<=16384):continue
        result=subprocess.run(common+['cat',remote+'/'+name+'.json'],capture_output=True,
                              text=True,check=True,timeout=120)
        if len(result.stdout.encode())>16384:continue
        try:record=json.loads(result.stdout)
        except ValueError:continue
        if not verified(record,name,by_name[name].get('Size')):continue
        # Remove only this recognized archive, then its exact receipt. No wildcards/recursion.
        subprocess.run(common+['deletefile',remote+'/'+name],check=True,timeout=120,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        subprocess.run(common+['deletefile',remote+'/'+name+'.json'],check=True,timeout=120,
                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        removed.append(name)
    return removed
