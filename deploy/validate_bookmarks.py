"""Opt-in unique temporary bookmark validation, cleanup only created IDs."""
import shlex
from pathlib import Path
from remote import connect
password=(Path(__file__).resolve().parents[1]/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
code=r'''
import httpx,sys,uuid
with httpx.Client(base_url='http://pkm-web',headers={'Origin':'http://8.133.220.164:8091'},timeout=30) as c:
 c.post('/api/auth/login',json={'password':sys.stdin.read()}).raise_for_status()
 before=c.get('/api/notes/').json()['total']
 url='https://example.org/pkm-validation/'+uuid.uuid4().hex
 body=('<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><A HREF="'+url+'">Test</A></DL>').encode()
 file=('bookmarks.html',body,'text/html');created=[]
 try:
  r=c.post('/api/bookmark-import/preview',files={'file':file});r.raise_for_status();preview=r.json();assert preview['valid']==1
  r=c.post('/api/bookmark-import/commit',files={'file':file},data={'content_hash':preview['content_hash']});r.raise_for_status();created=r.json()['note_ids'];assert r.json()['imported']==1
  r=c.post('/api/bookmark-import/commit',files={'file':file});r.raise_for_status();assert r.json()['duplicates']==1 and r.json()['imported']==0
  print('Live bookmarks preview/commit/dedup verified without upstream fetch')
 finally:
  for id in created:c.delete('/api/notes/%s'%id).raise_for_status()
 assert c.get('/api/notes/').json()['total']==before
 print('Temporary bookmark deleted; original notes preserved')
'''
c=connect()
try:
 i,o,e=c.exec_command('docker exec -i pkm-api python -c '+shlex.quote(code),timeout=60)
 i.write(password);i.flush();i.channel.shutdown_write();out=o.read().decode();e.read();status=o.channel.recv_exit_status()
 if status:raise RuntimeError('Bookmark validation failed; inspect privately')
 print(out)
finally:c.close()
