import hashlib,json,subprocess,tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from retention import prune_local,prune_remote

class RetentionTests(unittest.TestCase):
 def test_local_keeps_seven_verified_and_preserves_unverified_and_tools(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);data=b'verified'
   for day in range(1,10):
    name=f'agent-recovery-202610{day:02d}-040000.zip.age';(root/name).write_bytes(data)
    (root/(name+'.json')).write_text(json.dumps(dict(archive=name,bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),offsite_ok=True)))
   pending=root/'agent-recovery-20261010-040000.zip.age';pending.write_bytes(b'no verified receipt')
   tool=root/'recovery-kit.zip';tool.write_bytes(b'tool')
   removed=prune_local(root,7,'agent-recovery-20261009-040000.zip.age')
   self.assertEqual(len(removed),2);self.assertTrue(pending.exists());self.assertTrue(tool.exists())
   self.assertEqual(len(list(root.glob('*.zip.age.json'))),7)
 def test_remote_cutoff_receipt_checks_and_exact_paths(self):
  current='agent-recovery-20261101-040000.zip.age';old='agent-recovery-20260901-040000.zip.age';boundary='agent-recovery-20261002-040000.zip.age';unknown='agent-recovery-20260902-040000.zip.age';other='recovery-kit.zip'
  entries=[dict(Name=n,Size=8,IsDir=False) for n in [current,old,boundary,unknown,other]]+[dict(Name=old+'.json',Size=200,IsDir=False)]
  record=dict(archive=old,bytes=8,sha256='a'*64,offsite_ok=True);deletes=[]
  def transport(args,**kwargs):
   if 'lsjson' in args:return subprocess.CompletedProcess(args,0,stdout=json.dumps(entries))
   if 'cat' in args:return subprocess.CompletedProcess(args,0,stdout=json.dumps(record))
   if 'deletefile' in args:deletes.append(args[-1]);return subprocess.CompletedProcess(args,0)
   self.fail(str(args))
  with patch('retention.subprocess.run',side_effect=transport):
   removed=prune_remote(['fixture'],'fixture:AgentOps-Backups',30,current,datetime(2026,11,1,4,tzinfo=timezone.utc))
  self.assertEqual(removed,[old]);self.assertEqual(deletes,['fixture:AgentOps-Backups/'+old,'fixture:AgentOps-Backups/'+old+'.json'])
  self.assertNotIn(other,removed);self.assertNotIn(unknown,removed);self.assertNotIn(boundary,removed)
 def test_missing_current_and_root_remote_fail_before_deletion(self):
  for remote in ['fixture:','fixture:.','fixture:folder/..','fixture:folder/../other','fixture:/','fixture:folder//child']:
   with patch('retention.subprocess.run') as untouched:
    with self.assertRaises(ValueError):prune_remote(['fixture'],remote,30,'anything')
    untouched.assert_not_called()
  with patch('retention.subprocess.run',return_value=subprocess.CompletedProcess([],0,stdout='[]')) as call:
   with self.assertRaises(ValueError):prune_remote(['fixture'],'fixture:AgentOps-Backups',30,'agent-recovery-20261101-040000.zip.age')
   self.assertEqual(call.call_count,1)
if __name__=='__main__':unittest.main()
