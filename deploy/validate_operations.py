"""Read-only live status and isolated restore rehearsal of a registered archive."""
import shlex
from pathlib import Path
from remote import connect
password=(Path(__file__).resolve().parents[1]/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
code=r'''
import httpx,sys
with httpx.Client(base_url='http://pkm-web',headers={'Origin':'http://8.133.220.164:8091'},timeout=90) as c:
 c.post('/api/auth/login',json={'password':sys.stdin.read()}).raise_for_status()
 before=c.get('/api/notes/').json()['total']
 r=c.get('/api/operations/status');r.raise_for_status();assert r.json()['notes_count']==before
 backups=c.get('/api/backups/').json();assert backups
 r=c.post('/api/operations/backups/%s/rehearse'%backups[0]['id']);r.raise_for_status();data=r.json()
 assert data['verified'] and data['sqlite_restore_verified'] and not data['live_data_modified']
 assert c.get('/api/notes/').json()['total']==before
 print('Live isolated restore rehearsal passed:',data)
'''
c=connect()
try:
 i,o,e=c.exec_command('docker exec -i pkm-api python -c '+shlex.quote(code),timeout=120)
 i.write(password);i.flush();i.channel.shutdown_write();out=o.read().decode();e.read();status=o.channel.recv_exit_status()
 if status:raise RuntimeError('Operations validation failed; inspect privately')
 print(out)
finally:c.close()
