import hashlib,json,os,socket,sqlite3,threading,sys
from pathlib import Path
from urllib.request import urlopen
from agentops.continuity import restore_backup
w=Path('/workspace')
expected=json.loads((w/'expected.json').read_text())
assert not Path('/home/agent_vsehomira').exists()
assert not Path('/run/user/1000').exists()
assert not any(k in os.environ for k in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','DBUS_SESSION_BUS_ADDRESS'))
s=socket.socket();s.settimeout(1)
try:
    s.connect(('1.1.1.1',443))
except OSError:
    network_blocked=True
else:
    raise AssertionError('External networking unexpectedly available')
finally:
    s.close()
(w/'restored').mkdir(mode=0o700)
r=restore_backup(w/'input.zip',w/'restored',expected_sha256=expected['sha256'])
assert len(r['restored'])==expected['files']
with sqlite3.connect(w/'restored/files/ledger/ledger.sqlite3') as db:
    tables=[x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
# Full runtime code is staged separately: filename exclusions omit some source modules.
sys.path.insert(0,'/workspace/runtime')
from agentsmon import config,dashboard
cfg=config.load()
assert not cfg['agents'] and not cfg['daemons'] and not cfg['services']
server=dashboard.make_server('127.0.0.1',0,cfg)
t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
try:
    with urlopen('http://127.0.0.1:%d/'%server.server_port,timeout=10) as response:
        assert response.status==200
        assert b'<html' in response.read().lower()
finally:
    server.shutdown();server.server_close();t.join(timeout=5)
evidence={'restored_files':len(r['restored']),'sqlite_integrity':r['sqlite_integrity'],'archive_sha256_verified':True,'network_blocked':network_blocked,'host_home_hidden':True,'production_sockets_hidden':True,'dashboard_fresh_config_http':200,'production_agents_started':False,'same_host_only':True,'full_server_recovery':False,'runtime_staged_separately':True,'database_tables':tables}
(w/'result.json').write_text(json.dumps(evidence,indent=2)+'\n')
print(json.dumps(evidence))
