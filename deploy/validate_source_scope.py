"""Explicit opt-in: sync existing source once to establish trusted memberships.
No new source, deletion or settings change. May run AI only if user enabled it.
"""
import shlex
from pathlib import Path
from remote import connect
password=(Path(__file__).resolve().parents[1]/'PKM-login-private.txt').read_text(encoding='utf-8').split('登录密码：',1)[1].splitlines()[0]
code=r'''
import httpx,sys,time
with httpx.Client(base_url='http://pkm-web',headers={'Origin':'http://8.133.220.164:8091'},timeout=30) as c:
 c.post('/api/auth/login',json={'password':sys.stdin.read()}).raise_for_status()
 source=next(x for x in c.get('/api/sources/').json() if not x['active_task_id'])
 r=c.post('/api/sources/%s/sync'%source['id']);r.raise_for_status();task_id=r.json()['id']
 for _ in range(90):
  task=c.get('/api/import/%s'%task_id).json()
  if task['status'] not in ['pending','running']:break
  time.sleep(2)
 assert task['status'] in ['completed','partial'],task['status']
 allm=c.get('/api/sync/manifest').json()
 scoped=c.get('/api/sync/manifest',params={'scope':'sources','source_ids':source['id']});scoped.raise_for_status();scoped=scoped.json()
 empty=c.get('/api/sync/manifest',params={'scope':'sources'}).json()
 assert not empty['notes'] and not empty['attachments']
 assert scoped['notes'] and len(scoped['notes'])<=len(allm['notes'])
 ids=[n['id'] for n in scoped['notes'][:50]]
 batch=c.post('/api/sync/batch',json={'snapshot':scoped['snapshot'],'ids':ids,'scope':'sources','source_ids':[source['id']]});batch.raise_for_status()
 assert c.post('/api/sync/batch',json={'snapshot':scoped['snapshot'],'ids':ids}).status_code==409
 print('Trusted source membership/scope isolation verified:',len(scoped['notes']),'source notes;',len(allm['notes']),'total')
'''
c=connect()
try:
 i,o,e=c.exec_command('docker exec -i pkm-api python -c '+shlex.quote(code),timeout=220)
 i.write(password);i.flush();i.channel.shutdown_write();output=o.read().decode();e.read();status=o.channel.recv_exit_status()
 if status:raise RuntimeError('Source scope validation failed; inspect privately')
 print(output)
finally:c.close()
