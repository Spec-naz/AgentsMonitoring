#!/usr/bin/python3
"""Decrypt then validate selected application capsule into an empty private directory."""
import argparse,json,os,subprocess,tempfile,hashlib
from pathlib import Path
from continuity_restore import restore_backup
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--archive',type=Path,required=True);p.add_argument('--sha256',required=True)
p.add_argument('--identity',type=Path,required=True);p.add_argument('--age',required=True)
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();os.umask(0o077)
if a.identity.is_symlink() or a.identity.stat().st_mode & 0o077:
    p.error('Recovery identity must be an ordinary private file')
if a.archive.is_symlink() or not a.archive.is_file(): p.error('Ordinary archive file required')
with tempfile.TemporaryDirectory(prefix='agent-recovery-') as scratch:
    scratch=Path(scratch);cipher=scratch/'input.age'
    h=hashlib.sha256()
    with a.archive.open('rb') as src,cipher.open('xb') as dst:
        for b in iter(lambda:src.read(1024**2),b''):
            h.update(b);dst.write(b)
    if h.hexdigest()!=a.sha256: p.error('Ciphertext SHA256 mismatch')
    plain=scratch/'capsule.zip'
    subprocess.run([a.age,'-d','-i',str(a.identity),'-o',str(plain),str(cipher)],check=True)
    a.output.mkdir(mode=0o700,exist_ok=True)
    result=restore_backup(plain,a.output)
    print(json.dumps({'restored_files':len(result['restored']),'sqlite_integrity':result['sqlite_integrity'],
                      'staging':str(a.output),'services_started':False}))
