"""Opt-in file import check; remove ONLY the new unique test note in finally."""
import shlex
from pathlib import Path
from remote import connect
password=(Path(__file__).resolve().parents[1]/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
code=r'''
import httpx,sys,uuid
with httpx.Client(base_url='http://pkm-web',headers={'Origin':'http://8.133.220.164:8091'},timeout=30) as c:
 c.post('/api/auth/login',json={'password':sys.stdin.read()}).raise_for_status()
 before=c.get('/api/notes/').json()['total'];text='# Temporary validation\n'+uuid.uuid4().hex
 file=('validation.md',text.encode(),'text/markdown');note_id=None
 try:
  r=c.post('/api/file-import/preview',files={'file':file});r.raise_for_status();assert r.json()['chars']==len(text)
  r=c.post('/api/file-import/commit',files={'file':file});r.raise_for_status();assert r.json()['status']=='imported';note_id=r.json()['note_id']
  r=c.post('/api/file-import/commit',files={'file':file});r.raise_for_status();assert r.json()['status']=='duplicate' and r.json()['note_id']==note_id
  print('Live file preview/import/dedup passed; no AI call')
 finally:
  if note_id:c.delete('/api/notes/%s'%note_id).raise_for_status()
 assert c.get('/api/notes/').json()['total']==before
 print('Temporary note removed; original count preserved')
'''
c=connect()
try:
 i,o,e=c.exec_command('docker exec -i pkm-api python -c '+shlex.quote(code),timeout=60)
 i.write(password);i.flush();i.channel.shutdown_write();out=o.read().decode();e.read();status=o.channel.recv_exit_status()
 if status:raise RuntimeError('File validation failed; inspect privately')
 print(out)
finally:c.close()
