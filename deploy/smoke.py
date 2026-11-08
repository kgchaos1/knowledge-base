"""Read-only live API smoke check. No import, deletion, AI call or settings changes."""
import json
import shlex
from pathlib import Path
from remote import connect

ROOT = Path(__file__).resolve().parents[1]
password = (ROOT/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
code = r'''
import sys,httpx
with httpx.Client(base_url='http://pkm-web',headers={'Origin':'http://8.133.220.164:8091'},timeout=30) as c:
 assert c.get('/api/notes/').status_code==401
 c.post('/api/auth/login',json={'password':sys.stdin.read()}).raise_for_status()
 notes=c.get('/api/notes/');notes.raise_for_status()
 manifest=c.get('/api/sync/manifest');manifest.raise_for_status()
 m=manifest.json()
 if m['notes']:
  c.post('/api/sync/batch',json={'snapshot':m['snapshot'],'ids':[n['id'] for n in m['notes'][:50]]}).raise_for_status()
 for path in ['/api/sources/','/api/devices/','/api/backups/']:
  c.get(path).raise_for_status()
 print('Authenticated read-only smoke passed:',notes.json()['total'],'notes;',len(m['attachments']),'attachments')
'''
client = connect()
try:
    stdin,out,err = client.exec_command('docker exec -i pkm-api python -c '+shlex.quote(code),timeout=60)
    stdin.write(password);stdin.flush();stdin.channel.shutdown_write()
    output=out.read().decode('utf-8');err.read();status=out.channel.recv_exit_status()
    if status:raise RuntimeError('Live smoke failed; inspect privately')
    print(output)
finally:client.close()
