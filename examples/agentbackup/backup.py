#!/usr/bin/python3
"""Encrypted application recovery capsules. No live credentials or full HOME export."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import zipfile

EXCLUDED = {'.git', '.env', 'node_modules', 'dist', '__pycache__', '.venv', 'venv', '.cache', '.pytest_cache'}
MAX_BYTES = 512 * 1024**2

def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1024**2), b''):
            h.update(chunk)
    return h.hexdigest()

def private_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2); f.write('\n')
        f.flush(); os.fsync(f.fileno())
    os.replace(name, path)

def sqlite_copy(source, target):
    if source.is_symlink() or not source.is_file():
        raise ValueError('Required database unavailable')
    from urllib.parse import quote
    with closing(sqlite3.connect('file:'+quote(str(source))+'?mode=ro',uri=True)) as live:
        with closing(sqlite3.connect(target)) as copied:
            deadline=time.monotonic()+45
            def progress(*_):
                if time.monotonic()>deadline: raise TimeoutError('SQLite snapshot deadline')
            live.backup(copied,pages=256,progress=progress)
            if copied.execute('PRAGMA integrity_check').fetchone()!=('ok',):
                raise ValueError('SQLite integrity failed')

def capsule(roots, dbs, extras, output):
    """Reviewed source trees + consistent DB snapshots; never follows source links."""
    entries=[]; excluded=[]; total=0; seen=set()
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as z:
        def add(path, name, database=False):
            nonlocal total
            if name in seen: raise ValueError('Duplicate archive name')
            seen.add(name)
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as src:
                info=os.fstat(src.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size>128*1024**2:
                    raise ValueError('Unsupported source type/size')
                digest=hashlib.sha256();size=0
                zi=zipfile.ZipInfo(name);zi.external_attr=(stat.S_IFREG|0o600)<<16
                zi.compress_type=zipfile.ZIP_DEFLATED
                with z.open(zi,'w') as dst:
                    for chunk in iter(lambda:src.read(1024**2),b''):
                        size+=len(chunk);total+=len(chunk)
                        if total>MAX_BYTES or len(entries)>=10000: raise ValueError('Capsule size limit')
                        digest.update(chunk);dst.write(chunk)
                after=os.fstat(src.fileno())
                if (after.st_size,after.st_mtime_ns)!=(info.st_size,info.st_mtime_ns):
                    raise ValueError('Source changed; retry')
            entries.append({'path':name,'size':size,'sha256':digest.hexdigest(),'mode':stat.S_IMODE(info.st_mode),'sqlite':database})
        for label, raw in roots.items():
            root=Path(raw)
            if root.is_symlink() or not root.is_dir(): raise ValueError('Invalid reviewed root')
            def walk_error(error):
                raise error  # An unreadable subtree must not become a successful incomplete backup.
            for directory,dirs,files in os.walk(root,followlinks=False,onerror=walk_error):
                for child in list(dirs):
                    p=Path(directory)/child
                    if child in EXCLUDED or p.is_symlink():
                        dirs.remove(child);excluded.append(f'{label}/{p.relative_to(root)}')
                for child in sorted(files):
                    p=Path(directory)/child;rel=p.relative_to(root)
                    if (p.is_symlink() or child=='.env' or child.startswith('.env.')
                        or child.endswith(('.pem','.key','.sqlite','.sqlite3','.db'))
                        or child.endswith(('-wal','-shm'))):
                        excluded.append(f'{label}/{rel}');continue
                    add(p,f'files/{label}/{rel.as_posix()}')
        for label,path in dbs.items(): add(path,f'files/{label}/{label}.sqlite3',True)
        for name,path in extras.items(): add(path,'files/recovery/'+name)
        manifest={'schema':1,'includes_secrets':False,'files':entries,'excluded':excluded,'missing':[]}
        z.writestr('manifest.json',json.dumps(manifest,sort_keys=True))
    os.chmod(output,0o600)
    return manifest

def run(config, local_only=False):
    cfg=json.loads(config.read_text());h=Path.home()
    state=Path(cfg['state']);state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if shutil.disk_usage(state).free < 5*1024**3: raise RuntimeError('Less than 5GiB disk free')
    if sum(p.stat().st_size for p in state.glob('agent-recovery-*.zip.age')) > 4*1024**3:
        raise RuntimeError('Local encrypted backlog exceeds 4GiB; review offsite upload before continuing')
    lock=open(state/'backup.lock','a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    output=state/('agent-recovery-'+stamp+'.zip.age')
    status={'attempt_at':datetime.now(timezone.utc).isoformat(),'local_ok':False,'offsite_ok':False}
    private_json(state/'last-attempt.json',status)
    try:
        roots={k:str(Path(v).expanduser().resolve(strict=True)) for k,v in cfg['roots'].items()}
        with tempfile.TemporaryDirectory(prefix='.build-',dir=state) as tmp:
            tmp=Path(tmp);dbs={}
            for label,value in cfg['sqlite'].items():
                dst=tmp/(label+'.sqlite3');sqlite_copy(Path(value).expanduser(),dst);dbs[label]=dst
            extras={}
            if cfg.get('postgres'):
                pg=cfg['postgres'];dump=tmp/'wiki-postgres.dump'
                subprocess.run([pg['pg_dump'],'-h',pg['socket'],'-U',pg['user'],'-d',pg['database'],'-Fc','-f',str(dump)],
                               check=True,timeout=120,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                subprocess.run([pg['pg_restore'],'--list',str(dump)],check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                extras['wiki-postgres.dump']=dump
            note=tmp/'RECOVERY.md';note.write_text(Path(cfg['runbook']).read_text());extras['RECOVERY.md']=note
            versions=tmp/'versions.json'
            versions.write_text(json.dumps({'created_at':stamp,'roots':roots,'credentials_included':False},indent=2));extras['versions.json']=versions
            plain=tmp/'capsule.zip';manifest=capsule(roots,dbs,extras,plain)
            plain_sha=sha(plain)
            cipher=tmp/'capsule.zip.age'
            subprocess.run([cfg['age'],'-r',cfg['recipient'],'-o',str(cipher),str(plain)],check=True,timeout=120,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            os.chmod(cipher,0o600);os.link(cipher,output)
        status.update({'local_ok':True,'archive':str(output),'sha256':sha(output),'plaintext_sha256':plain_sha,
                       'files':len(manifest['files']),'bytes':output.stat().st_size,'credentials_included':False})
        private_json(output.with_suffix(output.suffix+'.json'),status)
        private_json(state/'last-local-success.json',status)
        if not local_only:
            rc=Path(cfg['rclone_config'])
            if not rc.is_file(): raise RuntimeError('OneDrive server authorization missing')
            target=cfg['remote']+'/'+output.name
            common=[cfg['rclone'],'--config',str(rc),'--contimeout','20s','--timeout','90s','--retries','3']
            subprocess.run(common+['copyto',str(output),target,'--immutable'],check=True,timeout=300,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            downloaded=state/('.verify-'+output.name)
            try:
                subprocess.run(common+['copyto',target,str(downloaded),'--immutable'],check=True,timeout=300,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                os.chmod(downloaded,0o600)
                if sha(downloaded)!=status['sha256']: raise RuntimeError('Offsite readback hash mismatch')
            finally:
                downloaded.unlink(missing_ok=True)
            side=output.with_suffix(output.suffix+'.json')
            verified={**status,'offsite_ok':True,'offsite_verified_at':datetime.now(timezone.utc).isoformat()}
            private_json(side,verified)
            subprocess.run(common+['copyto',str(side),cfg['remote']+'/'+side.name,'--immutable'],check=True,timeout=120,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            status.update(verified)
            private_json(state/'last-offsite-success.json',status)
        # Local retention only, after a verified offsite copy; remote files are never deleted.
        if status['offsite_ok']:
            keep=sorted(state.glob('agent-recovery-*.zip.age'),reverse=True)
            for old in keep[int(cfg.get('local_keep',48)):]:
                receipt=old.with_suffix(old.suffix+'.json')
                if receipt.is_file():
                    record=json.loads(receipt.read_text())
                    if record.get('offsite_ok'):
                        old.unlink();receipt.unlink()
        private_json(output.with_suffix(output.suffix+'.json'),status)
        private_json(state/'last-attempt.json',status)
        print(json.dumps(status))
    except Exception as e:
        status['error_type']=type(e).__name__
        private_json(state/'last-attempt.json',status)
        print(json.dumps(status));return 1
    finally:
        lock.close()
    return 0

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,required=True);p.add_argument('--local-only',action='store_true')
    a=p.parse_args();os.umask(0o077)
    sys.exit(run(a.config,a.local_only))
