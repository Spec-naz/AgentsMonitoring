import tempfile,unittest,zipfile,json,sqlite3
import contextlib,io,subprocess
from unittest.mock import patch
from pathlib import Path
from backup import capsule,sqlite_copy,run
from continuity_restore import restore_backup,ContinuityError
class BackupTests(unittest.TestCase):
 def test_remote_receipt_and_failure_truthfulness(self):
  for failure in (None,'archive','receipt'):
   with self.subTest(failure=failure),tempfile.TemporaryDirectory() as t:
    w=Path(t);src=w/'source';src.mkdir();(src/'a.py').write_text('x=1')
    note=w/'note';note.write_text('recovery');rc=w/'rclone.conf';rc.write_text('fixture')
    state=w/'state';cfg=w/'settings.json'
    cfg.write_text(json.dumps({'state':str(state),'roots':{'code':str(src)},'sqlite':{},
      'runbook':str(note),'age':'fixture-age','recipient':'fixture-public-recipient',
      'rclone':'fixture-rclone','rclone_config':str(rc),'remote':'fixture:backups'}))
    remote={};receipts=[]
    def transport(args,**kwargs):
     if args[0]=='fixture-age':Path(args[args.index('-o')+1]).write_bytes(b'fixture encrypted');return
     i=args.index('copyto');source,target=args[i+1:i+3]
     if source.startswith('fixture:'):Path(target).write_bytes(remote[source]);return
     data=Path(source).read_bytes()
     if source.endswith('.json'):
      receipts.append(json.loads(data))
      if failure=='receipt':raise subprocess.CalledProcessError(1,args)
     elif failure=='archive':raise subprocess.CalledProcessError(1,args)
     remote[target]=data
    with patch('backup.subprocess.run',side_effect=transport),patch('backup.shutil.disk_usage') as disk,contextlib.redirect_stdout(io.StringIO()):
     disk.return_value.free=10*1024**3;result=run(cfg)
    attempt=json.loads((state/'last-attempt.json').read_text())
    self.assertTrue(attempt['local_ok'])
    self.assertEqual(attempt['offsite_ok'],failure is None)
    self.assertEqual(result,0 if failure is None else 1)
    self.assertEqual((state/'last-offsite-success.json').exists(),failure is None)
    if failure!='archive':
     self.assertEqual(len(receipts),1);self.assertTrue(receipts[0]['offsite_ok'])
     self.assertIn('offsite_verified_at',receipts[0])
 def test_unreadable_subtree_fails_closed(self):
  with tempfile.TemporaryDirectory() as t:
   w=Path(t);src=w/'source';src.mkdir();(src/'required.txt').write_text('required')
   def failed_walk(root, **kwargs):
    kwargs['onerror'](PermissionError('fixture unreadable subtree'))
    yield  # Mirror os.walk's lazy traversal and error callback.
   with patch('backup.os.walk',failed_walk):
    with self.assertRaises(PermissionError):capsule({'code':src},{},{},w/'capsule.zip')
 def test_complete_code_excludes_credentials_and_roundtrips_sqlite(self):
  with tempfile.TemporaryDirectory() as t:
   w=Path(t);src=w/'code';src.mkdir();(src/'config.py').write_text('x=1');(src/'identity.py').write_text('y=2');(src/'.env').write_text('SECRET');(src/'link').symlink_to('/etc/passwd')
   live=w/'live.sqlite';db=sqlite3.connect(live);db.execute('PRAGMA journal_mode=WAL');db.execute('create table t(x)');db.execute('insert into t values(42)');db.commit()
   snap=w/'snap';sqlite_copy(live,snap);db.close()
   archive=w/'capsule.zip';m=capsule({'code':src},{'tasks':snap},{},archive)
   names={f['path'] for f in m['files']};self.assertIn('files/code/config.py',names);self.assertIn('files/code/identity.py',names);self.assertNotIn('files/code/.env',names);self.assertNotIn('files/code/link',names)
   stage=w/'stage';stage.mkdir();r=restore_backup(archive,stage);self.assertEqual(r['sqlite_integrity'],'passed')
   with sqlite3.connect(stage/'files/tasks/tasks.sqlite3') as d:self.assertEqual(d.execute('select x from t').fetchone()[0],42)
 def test_tamper_refused_before_extract(self):
  with tempfile.TemporaryDirectory() as t:
   w=Path(t);src=w/'source';src.mkdir();(src/'a').write_text('ok');a=w/'a.zip';capsule({'code':src},{},{},a)
   with zipfile.ZipFile(a) as z:m=json.loads(z.read('manifest.json'))
   m['files'][0]['sha256']='0'*64;b=w/'b.zip'
   with zipfile.ZipFile(b,'w') as z:z.writestr('manifest.json',json.dumps(m));z.writestr('files/code/a','ok')
   stage=w/'stage';stage.mkdir()
   with self.assertRaises(ContinuityError):restore_backup(b,stage)
   self.assertEqual(list(stage.iterdir()),[])
if __name__=='__main__':unittest.main()
